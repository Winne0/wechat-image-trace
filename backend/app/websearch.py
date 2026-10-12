"""全网以图搜图引擎适配层。

本地指纹库只能覆盖"收录过的"文章；本模块负责调用第三方以图搜图引擎
检索全网，并把命中结果按「与微信公众号的关联度」重新排序。

引擎通过环境变量启用（未配置 key 的引擎自动禁用）：

    SERPAPI_KEY       SerpAPI 的 Google Lens 引擎
    BING_VISUAL_KEY   Azure Bing Visual Search
    TINEYE_KEY        TinEye 反向图片搜索（需配合 TINEYE_SECRET）
    TINEYE_SECRET
    SAUCENAO_KEY      SauceNAO（二次元图片溯源，免费额度）
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import os
import socket
from urllib.parse import urlparse

import httpx

# 微信公众号域名：命中这些域名的结果优先展示
WECHAT_HOSTS = ("mp.weixin.qq.com", "weixin.qq.com", "mp.weixin.qq.com/s")
# 常见图文平台（公众号内容常被搬运到这里），给次级加权
REPOST_HOSTS = (
    "zhihu.com", "jianshu.com", "csdn.net", "juejin.cn", "toutiao.com",
    "sina.com.cn", "163.com", "qq.com", "sohu.com", "baijiahao.baidu.com",
    "xiaohongshu.com", "weibo.com", "douban.com", "bilibili.com",
)

TIMEOUT = 45.0


# --------------------------------------------------------------- SSRF 防护
def is_safe_url(url: str) -> bool:
    """仅允许公网 http(s) 地址，屏蔽内网 / 回环 / 链路本地地址。"""
    try:
        p = urlparse(url)
    except Exception:
        return False
    if p.scheme not in ("http", "https") or not p.hostname:
        return False
    host = p.hostname.strip("[]").lower()
    if host in ("localhost", "localhost.localdomain"):
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        try:
            ip = ipaddress.ip_address(socket.gethostbyname(host))
        except Exception:
            return False
    return not (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


def _host_rank(url: str) -> float:
    """按域名给出加权分：公众号 > 转载平台 > 其他。"""
    host = (urlparse(url).hostname or "").lower()
    if any(h in host for h in ("mp.weixin.qq.com", "weixin.qq.com")):
        return 0.28
    if any(host.endswith(h) for h in REPOST_HOSTS):
        return 0.12
    return 0.0


# --------------------------------------------------------------- 引擎基类
class BaseProvider:
    name = ""
    key_env: tuple = ()

    def __init__(self):
        self.key = (os.getenv(self.key_env[0], "") or "").strip() if self.key_env else ""
        self.secret = (os.getenv(self.key_env[1], "") or "").strip() if len(self.key_env) > 1 else ""

    @property
    def enabled(self) -> bool:
        return bool(self.key)

    async def search(self, image: bytes) -> list[dict]:
        raise NotImplementedError

    def _pack(self, title, url, snippet="", image_url="", rank_score=0.0) -> dict | None:
        if not url or not url.startswith(("http://", "https://")):
            return None
        return {
            "title": (title or url)[:180],
            "url": url,
            "snippet": (snippet or "")[:300],
            "image_url": image_url,
            "provider": self.name,
            "score": max(0.0, min(1.0, rank_score)),
        }


# --------------------------------------------------------------- SerpAPI
class SerpApiLens(BaseProvider):
    """SerpAPI 的 Google Lens 引擎（覆盖最常用的全网图片检索）。"""

    name = "Google Lens"
    key_env = ("SERPAPI_KEY",)

    async def search(self, image: bytes) -> list[dict]:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            resp = await c.post(
                "https://serpapi.com/search.json",
                params={"engine": "google_lens", "api_key": self.key},
                files={"file": ("query.jpg", image, "image/jpeg")},
            )
            resp.raise_for_status()
            data = resp.json()

        out = []
        # 视觉匹配（最主要的结果集）
        for i, m in enumerate(data.get("visual_matches", [])[:20]):
            item = self._pack(
                m.get("title"), m.get("link"),
                m.get("source") or "", m.get("thumbnail"),
                rank_score=0.95 - i * 0.035,
            )
            if item:
                out.append(item)
        # 网页上出现同一张图的其他位置
        for i, m in enumerate(data.get("image_results", [])[:10]):
            item = self._pack(
                m.get("title"), m.get("link"),
                m.get("snippet") or "", m.get("thumbnail"),
                rank_score=0.62 - i * 0.03,
            )
            if item:
                out.append(item)
        return out


# --------------------------------------------------------------- Bing
class BingVisual(BaseProvider):
    """Azure Bing Visual Search（上传图片二进制，返回相似图片与来源页）。"""

    name = "Bing 视觉搜索"
    key_env = ("BING_VISUAL_KEY",)

    async def search(self, image: bytes) -> list[dict]:
        endpoint = os.getenv(
            "BING_VISUAL_ENDPOINT",
            "https://api.bing.microsoft.com/v7.0/images/visualsearch",
        )
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            resp = await c.post(
                endpoint,
                headers={"Ocp-Apim-Subscription-Key": self.key},
                files={"image": ("query.jpg", image, "image/jpeg")},
            )
            resp.raise_for_status()
            data = resp.json()

        out, seen = [], set()
        for tag in data.get("tags", []):
            for action in tag.get("actions", []):
                for i, v in enumerate((action.get("data") or {}).get("value", [])[:15]):
                    url = v.get("hostPageUrl") or v.get("webSearchUrl")
                    if not url or url in seen:
                        continue
                    seen.add(url)
                    item = self._pack(
                        v.get("name"), url,
                        v.get("description") or "", v.get("thumbnailUrl"),
                        rank_score=0.85 - i * 0.03,
                    )
                    if item:
                        out.append(item)
        return out


# --------------------------------------------------------------- TinEye
class TinEye(BaseProvider):
    """TinEye 反向图片搜索：擅长找图片的原始出处与修改版本。"""

    name = "TinEye"
    key_env = ("TINEYE_KEY", "TINEYE_SECRET")

    @property
    def enabled(self) -> bool:
        return bool(self.key and self.secret)

    async def search(self, image: bytes) -> list[dict]:
        auth = base64.b64encode(f"{self.key}:{self.secret}".encode()).decode()
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            resp = await c.post(
                "https://api.tineye.com/rest/search/",
                headers={"Authorization": f"Basic {auth}"},
                files={"image": ("query.jpg", image, "image/jpeg")},
                data={"sort": "score", "order": "desc", "limit": "20"},
            )
            resp.raise_for_status()
            data = resp.json()

        out = []
        for i, r in enumerate((data.get("results") or [])[:20]):
            links = r.get("links") or []
            url = links[0] if isinstance(links[0], str) else (links[0].get("url") if links else "")
            item = self._pack(
                (r.get("domain") or url), url,
                f"{r.get('domain', '')} · {r.get('format', '')}",
                r.get("image_url"),
                rank_score=float(r.get("score", 0.8)) - i * 0.01,
            )
            if item:
                out.append(item)
        return out


# --------------------------------------------------------------- SauceNAO
class SauceNAO(BaseProvider):
    """SauceNAO：二次元插画/漫画的溯源强者，免费额度即可用。"""

    name = "SauceNAO"
    key_env = ("SAUCENAO_KEY",)

    async def search(self, image: bytes) -> list[dict]:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            resp = await c.post(
                "https://saucenao.com/search.php",
                data={"api_key": self.key, "output_type": "2", "db": "999"},
                files={"file": ("query.jpg", image, "image/jpeg")},
            )
            resp.raise_for_status()
            data = resp.json()

        out = []
        for i, r in enumerate((data.get("results") or [])[:12]):
            urls = r.get("data", {}).get("ext_urls") or []
            item = self._pack(
                r.get("data", {}).get("title") or urls[0] if urls else "",
                urls[0] if urls else "",
                r.get("data", {}).get("source") or "",
                r.get("header", {}).get("thumbnail") or "",
                rank_score=float(r.get("header", {}).get("similarity", 70)) / 100,
            )
            if item:
                out.append(item)
        return out


PROVIDERS = [SerpApiLens, BingVisual, TinEye, SauceNAO]


def active_providers() -> list:
    return [p() for p in PROVIDERS if p().enabled]


def provider_status() -> dict:
    """给前端用：哪些引擎已启用、哪些缺 key。"""
    enabled, missing = [], []
    for cls in PROVIDERS:
        inst = cls()
        (enabled if inst.enabled else missing).append(inst.name)
    return {"enabled": enabled, "missing": missing}


async def search_web(image: bytes, top_k: int = 12) -> dict:
    """并发调用所有已启用引擎，合并去重并按公众号关联度排序。"""
    providers = active_providers()
    if not providers:
        raise RuntimeError(
            "没有配置任何以图搜图引擎。请设置环境变量 SERPAPI_KEY / "
            "BING_VISUAL_KEY / TINEYE_KEY+TINEYE_SECRET / SAUCENAO_KEY 之一后重启服务。"
        )

    async def run(p):
        try:
            return p, await p.search(image)
        except Exception as exc:                 # 单个引擎失败不影响其他
            return p, {"__error__": str(exc)[:200]}

    results = await asyncio.gather(*[run(p) for p in providers])

    merged, errors = [], []
    for p, res in results:
        if isinstance(res, dict):
            errors.append({"provider": p.name, "error": res["__error__"]})
            continue
        merged.extend(res)

    # 按 URL 去重，同一 URL 保留最高分
    best: dict[str, dict] = {}
    for item in merged:
        key = item["url"].split("#")[0].rstrip("/")
        if key not in best or item["score"] > best[key]["score"]:
            item["score"] = min(1.0, item["score"] + _host_rank(item["url"]))
            best[key] = item

    hits = sorted(best.values(), key=lambda x: x["score"], reverse=True)[:top_k]
    return {
        "hits": hits,
        "engines": [p.name for p in providers],
        "errors": errors,
        "total_matches": len(best),
    }
