"""生成演示数据集，用于验证「以图搜文」链路。

沙箱环境受微信访问限制时无法抓取真实文章，这份数据用程序生成的图片
模拟「公众号文章 + 正文配图」，并额外产出一张经过压缩、缩放、加水印的
「二次传播版」查询图 —— 用来验证检索算法在图片被改动后是否仍能命中。

用法：
    python3 scripts/seed_demo.py          # 生成并入库
    python3 scripts/seed_demo.py --clean  # 仅清空演示数据
"""

from __future__ import annotations

import argparse
import io
import os
import random
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "backend"))

from app import crawler, features          # noqa: E402
from app.db import Article, SessionLocal  # noqa: E402
from app.db import Image as ImageRow  # noqa: E402

DEMO_ACCOUNT = "演示数据集"

ARTICLES = [
    ("旅行摄影构图指南", 4),
    ("家常菜一周不重样", 4),
    ("新手健身入门计划", 3),
    ("客厅改造实录", 4),
    ("读懂财报三张表", 3),
    ("大模型入门读书笔记", 4),
]

PALETTE = [
    (32, 84, 140), (168, 62, 50), (44, 120, 96), (150, 110, 40),
    (96, 60, 140), (40, 90, 110), (140, 70, 100), (60, 130, 70),
]


def make_image(seed: int, w=960, h=640) -> bytes:
    """程序化生成一张有辨识度的图：渐变底 + 几何块 + 文字 + 噪点。"""
    rng = random.Random(seed)
    c1 = np.array(PALETTE[seed % len(PALETTE)], dtype=np.float32)
    c2 = np.array(PALETTE[(seed * 3 + 1) % len(PALETTE)], dtype=np.float32)

    # 斜向渐变
    xs = np.linspace(0, 1, w)[None, :]
    ys = np.linspace(0, 1, h)[:, None]
    t = (xs * 0.6 + ys * 0.4) * np.ones((h, w))
    arr = c1[None, None, :] * (1 - t[..., None]) + c2[None, None, :] * t[..., None]

    # 叠两块半透明几何图形，增加局部特征点
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    dr = ImageDraw.Draw(overlay)
    for _ in range(rng.randint(3, 6)):
        x, y = rng.randint(0, w), rng.randint(0, h)
        r = rng.randint(40, 180)
        color = (rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255), 150)
        kind = rng.choice(["ellipse", "rect", "poly"])
        if kind == "ellipse":
            dr.ellipse([x - r, y - r, x + r, y + r], fill=color)
        elif kind == "rect":
            dr.rectangle([x - r, y - r // 2, x + r, y + r // 2], fill=color)
        else:
            n = rng.randint(3, 6)
            pts = [(rng.randint(0, w), rng.randint(0, h)) for _ in range(n)]
            dr.polygon(pts, fill=color)

    base = Image.fromarray(arr.astype(np.uint8)).convert("RGBA")
    base.alpha_composite(overlay)

    # 文字块（模拟配图上的标题）
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 44)
    except OSError:
        font = ImageFont.load_default()
    dr = ImageDraw.Draw(base)
    label = f"DEMO-{seed:03d}"
    dr.text((24, 20), label, fill=(255, 255, 255, 230), font=font)
    dr.text((24, 78), "WeChat Image Trace", fill=(255, 255, 255, 180), font=font)

    img = base.convert("RGB").filter(ImageFilter.GaussianBlur(0.4))

    # 轻微噪点
    noise = np.random.default_rng(seed).normal(0, 4, (h, w, 3))
    out = np.clip(np.asarray(img, dtype=np.float32) + noise, 0, 255).astype(np.uint8)

    buf = io.BytesIO()
    Image.fromarray(out).save(buf, format="JPEG", quality=88)
    return buf.getvalue()


def make_query_variant(raw: bytes, seed: int) -> bytes:
    """把原图做成「传播版」：缩放 + 压缩 + 水印 + 裁边。"""
    img = Image.open(io.BytesIO(raw))
    w, h = img.size
    # 裁掉边缘 4%
    img = img.crop((int(w * .04), int(h * .04), int(w * .96), int(h * .96)))
    # 缩到 62%
    img = img.resize((int(img.width * .62), int(img.height * .62)), Image.BILINEAR)
    dr = ImageDraw.Draw(img)
    dr.rectangle([10, img.height - 70, 330, img.height - 14], fill=(0, 0, 0))
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 30)
    except OSError:
        font = ImageFont.load_default()
    dr.text((22, img.height - 62), "@ 某公众号", fill=(255, 255, 255), font=font)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=52)
    return buf.getvalue()


def clean_demo(session):
    rows = session.query(Article).filter(Article.account == DEMO_ACCOUNT).all()
    for a in rows:
        session.delete(a)
    session.commit()
    return len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clean", action="store_true")
    args = ap.parse_args()

    session = SessionLocal()
    removed = clean_demo(session)
    if args.clean:
        print(f"已清除 {removed} 篇演示数据")
        session.close()
        return
    if removed:
        print(f"已清除旧演示数据 {removed} 篇")

    total_imgs = 0
    query_raw = None
    seed = 1

    for idx, (title, n_images) in enumerate(ARTICLES, start=1):
        article = Article(
            url=f"https://mp.weixin.qq.com/s/demo-{idx:04d}",
            title=f"[演示] {title}",
            account=DEMO_ACCOUNT,
            publish_time="2025-01-01",
            status="ok",
        )
        session.add(article)
        session.commit()

        for k in range(n_images):
            raw = make_image(seed)
            feats = features.extract(raw)
            src = (f"https://mmbiz.qpic.cn/mmbiz_jpg/demo{idx:02d}{k:02d}"
                   f"/640?wx_fmt=jpeg")
            path = crawler.cache_path_for(src)
            with open(path, "wb") as fh:
                fh.write(raw)
            session.add(ImageRow(
                article_id=article.id, src_url=src, local_path=path,
                width=feats["width"], height=feats["height"],
                dhash=features.pack_hash(feats["dhash"]),
                phash=features.pack_hash(feats["phash"]),
                hist=features.pack_hist(feats["hist"]),
                orb=features.pack_orb(feats["orb"]),
                kp=features.pack_kp(feats["kp"]),
                position=k, is_cover=1 if k == 0 else 0, status="ok",
            ))
            total_imgs += 1

            if idx == 2 and k == 1:          # 挑一张做成待查询的「传播版」
                query_raw = raw
            seed += 1
        session.commit()

    # 保存示例查询图，供前端「试试示例图片」使用
    if query_raw is not None:
        demo_dir = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "backend", "data")
        os.makedirs(demo_dir, exist_ok=True)
        with open(os.path.join(demo_dir, "demo_query.jpg"), "wb") as fh:
            fh.write(make_query_variant(query_raw, 999))

    print(f"演示数据已生成：{len(ARTICLES)} 篇文章 / {total_imgs} 张图片")
    print("示例查询图：backend/data/demo_query.jpg（压缩+缩放+水印后的版本）")
    session.close()


if __name__ == "__main__":
    main()
