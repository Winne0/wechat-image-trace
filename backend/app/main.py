"""微信图片溯源 · 后端服务。

上传一张图，在本地微信公众号图片指纹库里找出它出自哪篇文章。
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import func

from . import crawler, features
from .db import Article, Image, SessionLocal, engine
from .search import index

MAX_UPLOAD = 16 * 1024 * 1024
ALLOWED_MIME = ("image/jpeg", "image/png", "image/webp", "image/bmp", "image/gif")
MIN_SCORE = 0.22                    # 低于此分视为「未命中」

app = FastAPI(title="微信图片溯源", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

# ---------------------------------------------------------------- 导入任务
JOBS: dict = {}
_executor = ThreadPoolExecutor(max_workers=4)


def _import_one(url: str) -> dict:
    """抓取一篇文章 -> 下载图片 -> 计算指纹 -> 入库。"""
    session = SessionLocal()
    try:
        try:
            data = crawler.fetch_article(url)
        except Exception as exc:
            return {"url": url, "status": "failed", "error": str(exc)[:300]}

        # 以规范化后的永久链接为准，避免同一篇文章被重复收录
        canonical = data.get("url") or url
        existing = session.query(Article).filter(Article.url == canonical).first()
        if existing and existing.status == "ok":
            return {"url": canonical, "status": "skipped",
                    "title": existing.title, "images": len(existing.images)}
        if existing:
            session.delete(existing)
            session.commit()

        article = Article(url=canonical, status="pending")
        session.add(article)
        session.commit()

        article.title = data["title"]
        article.account = data["account"]
        article.publish_time = data["publish_time"]
        article.cover = data["cover"]
        article.status = "ok"
        session.commit()

        added = 0
        for item in data["images"][:80]:          # 单篇最多 80 张，避免超大长图帖
            src = item["src"]
            try:
                raw = crawler.download_image(src)
                if len(raw) > 20 * 1024 * 1024:
                    continue
                feats = features.extract(raw)
            except Exception:
                continue                          # 单张失败不影响整篇
            path = crawler.cache_path_for(src)
            try:
                with open(path, "wb") as fh:
                    fh.write(raw)
            except OSError:
                path = ""
            img = Image(
                article_id=article.id, src_url=src, local_path=path,
                width=feats["width"], height=feats["height"],
                dhash=features.pack_hash(feats["dhash"]),
                phash=features.pack_hash(feats["phash"]),
                hist=features.pack_hist(feats["hist"]),
                orb=features.pack_orb(feats["orb"]),
                kp=features.pack_kp(feats["kp"]),
                alt=item.get("alt", ""), position=item.get("position", 0),
                is_cover=item.get("cover", 0), status="ok",
            )
            session.add(img)
            added += 1
        session.commit()
        return {"url": url, "status": "ok", "title": article.title,
                "account": article.account, "images": added}
    except Exception as exc:
        try:
            art = session.query(Article).filter(Article.url == url).first()
            if art:
                art.status = "failed"
                art.error = str(exc)[:400]
                session.commit()
        except Exception:
            pass
        return {"url": url, "status": "failed", "error": str(exc)[:300]}
    finally:
        session.close()


def _run_job(job_id: str, urls: list):
    job = JOBS[job_id]
    job["status"] = "running"
    futures = [_executor.submit(_import_one, u) for u in urls]
    for i, fut in enumerate(futures):
        try:
            res = fut.result()
        except Exception as exc:
            res = {"url": urls[i], "status": "failed", "error": str(exc)[:300]}
        job["results"].append(res)
        job["done"] = i + 1
    job["status"] = "done"
    job["finished_at"] = time.time()
    index.refresh()


class ImportRequest(BaseModel):
    urls: list[str]


@app.post("/api/import")
async def api_import(req: ImportRequest):
    urls = [u.strip() for u in req.urls if u.strip()]
    urls = list(dict.fromkeys(urls))[:30]
    if not urls:
        raise HTTPException(400, "请提供至少一个文章链接")
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {"id": job_id, "total": len(urls), "done": 0,
                    "status": "queued", "results": [], "created_at": time.time()}
    threading.Thread(target=_run_job, args=(job_id, urls), daemon=True).start()
    return {"job_id": job_id, "total": len(urls)}


@app.get("/api/import/{job_id}")
async def api_import_status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    return {k: v for k, v in job.items() if k != "results"} | {
        "results": job["results"][-20:]
    }


# ---------------------------------------------------------------- 检索
def _do_search(feats: dict, top_k: int) -> dict:
    hits = index.search(feats, top_k=top_k)
    hits = [h for h in hits if h["score"] >= MIN_SCORE]

    session = SessionLocal()
    try:
        articles = {a.id: a for a in session.query(Article).all()}
        out = []
        for h in hits:
            art = articles.get(h["article_id"])
            if not art:
                continue
            out.append({
                "score": round(h["score"], 4),
                "confidence": _confidence(h),
                "image": {
                    "url": h["src_url"],
                    "width": h["width"], "height": h["height"],
                    "alt": h["alt"], "position": h["position"],
                    "is_cover": h["is_cover"],
                    "preview": f"/api/preview/{h['image_id']}",
                },
                "article": {
                    "id": art.id, "title": art.title, "account": art.account,
                    "url": art.url, "publish_time": art.publish_time,
                    "cover": art.cover,
                },
                "evidence": {
                    "hash_distance": h["hash_distance"],
                    "hist_similarity": round(h["hist_similarity"], 3),
                    "orb_matches": h["orb_matches"],
                    "orb_inliers": h.get("orb_inliers", 0),
                },
            })
    finally:
        session.close()

    # 同一篇文章可能命中多张图，按文章去重并保留最佳命中
    best_by_article = {}
    for item in out:
        aid = item["article"]["id"]
        if aid not in best_by_article or item["score"] > best_by_article[aid]["score"]:
            best_by_article[aid] = item
    merged = sorted(best_by_article.values(), key=lambda x: x["score"], reverse=True)
    return {"total_indexed": index.size, "hits": merged}


def _confidence(h: dict) -> str:
    if h["score"] >= 0.62 and h.get("orb_inliers", 0) >= 15:
        return "high"
    if h["score"] >= 0.42:
        return "medium"
    return "low"


@app.post("/api/search")
async def api_search(file: UploadFile | None = File(default=None),
                     image_url: str | None = Form(default=None),
                     top_k: int = Form(default=10)):
    raw = None
    if file is not None and file.filename:
        raw = await file.read()
        if len(raw) > MAX_UPLOAD:
            raise HTTPException(413, "图片过大（上限 16MB）")
    elif image_url:
        if not any(image_url.startswith(p) for p in ("http://", "https://")):
            raise HTTPException(400, "图片地址需为 http(s) 链接")
        try:
            headers = {"User-Agent": crawler.UA, "Referer": "https://mp.weixin.qq.com/"}
            async with httpx.AsyncClient(timeout=20, follow_redirects=True,
                                         headers=headers) as client:
                resp = await client.get(image_url)
                resp.raise_for_status()
                raw = resp.content
        except Exception as exc:
            raise HTTPException(400, f"图片下载失败：{exc}")
    else:
        raise HTTPException(400, "请上传图片或提供图片地址")

    try:
        feats = features.extract(raw)
    except Exception as exc:
        raise HTTPException(400, f"无法解析这张图片：{exc}")

    return _do_search(feats, min(top_k, 30))


# ---------------------------------------------------------------- 数据接口
@app.get("/api/stats")
async def api_stats():
    session = SessionLocal()
    try:
        articles = session.query(func.count(Article.id)).scalar() or 0
        ok_articles = (session.query(func.count(Article.id))
                       .filter(Article.status == "ok").scalar() or 0)
        images = session.query(func.count(Image.id)).scalar() or 0
        return {"articles": articles, "articles_ok": ok_articles,
                "images": images, "indexed": index.size}
    finally:
        session.close()


@app.get("/api/articles")
async def api_articles(q: str = "", limit: int = 50):
    session = SessionLocal()
    try:
        query = session.query(Article)
        if q:
            like = f"%{q}%"
            query = query.filter(Article.title.like(like) | Article.account.like(like))
        rows = query.order_by(Article.id.desc()).limit(limit).all()
        return {"items": [{
            "id": a.id, "title": a.title, "account": a.account, "url": a.url,
            "publish_time": a.publish_time, "status": a.status,
            "error": a.error, "images": len(a.images),
        } for a in rows]}
    finally:
        session.close()


@app.delete("/api/articles/{article_id}")
async def api_delete_article(article_id: int):
    session = SessionLocal()
    try:
        art = session.get(Article, article_id)
        if not art:
            raise HTTPException(404, "文章不存在")
        session.delete(art)
        session.commit()
    finally:
        session.close()
    index.refresh()
    return {"ok": True}


@app.get("/api/preview/{image_id}")
async def api_preview(image_id: int):
    """回显索引库里的图片（本地缓存优先，否则回源微信 CDN）。"""
    session = SessionLocal()
    try:
        img = session.get(Image, image_id)
        if not img:
            raise HTTPException(404, "图片不存在")
        if img.local_path and os.path.exists(img.local_path):
            return FileResponse(img.local_path, media_type="image/jpeg")
        url = img.src_url
    finally:
        session.close()

    try:
        data = crawler.download_image(url)
    except Exception as exc:
        raise HTTPException(502, f"图片拉取失败：{exc}")
    path = crawler.cache_path_for(url)
    try:
        with open(path, "wb") as fh:
            fh.write(data)
    except OSError:
        pass
    from fastapi import Response
    return Response(content=data, media_type="image/jpeg")


@app.get("/api/proxy-image")
async def api_proxy_image(url: str):
    """代理外部图片，规避浏览器直连微信 CDN 的防盗链限制。"""
    from urllib.parse import urlparse
    host = urlparse(url).netloc.lower()
    if not any(host.endswith(h) for h in crawler.IMAGE_HOSTS + crawler.ARTICLE_HOSTS):
        raise HTTPException(400, "只允许代理微信公众号域名的图片")
    try:
        data = crawler.download_image(url)
    except Exception as exc:
        raise HTTPException(502, f"图片拉取失败：{exc}")
    from fastapi import Response
    return Response(content=data, media_type="image/jpeg")


@app.post("/api/reindex")
async def api_reindex(background: BackgroundTasks):
    background.add_task(index.refresh)
    return {"ok": True}


@app.get("/api/demo-query")
async def api_demo_query():
    """返回一张「传播版」示例图（压缩+缩放+水印），便于一键体验检索。"""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "demo_query.jpg")
    if not os.path.exists(path):
        raise HTTPException(404, "示例图未生成，先运行 scripts/seed_demo.py")
    return FileResponse(path, media_type="image/jpeg")


@app.get("/api/health")
async def health():
    return {"ok": True, "indexed": index.size}


# ---------------------------------------------------------------- 静态资源
DIST = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "frontend", "dist")
if os.path.isdir(DIST):
    app.mount("/", StaticFiles(directory=DIST, html=True), name="static")


@app.on_event("startup")
async def on_startup():
    try:
        index.refresh()
    except Exception:
        pass
