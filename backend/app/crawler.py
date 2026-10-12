"""微信公众号文章抓取器。

只认 mp.weixin.qq.com 域名的文章页，只收 mmbiz.qpic.cn 域名的图片，
从源头保证索引库的素材 100% 来自微信公众号。
"""

from __future__ import annotations

import hashlib
import os
import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from .db import IMAGE_DIR

ARTICLE_HOSTS = ("mp.weixin.qq.com",)
IMAGE_HOSTS = ("mmbiz.qpic.cn", "mmbiz.qlogo.cn")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 MicroMessenger/7.0")
HEADERS = {
    "User-Agent": UA,
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://mp.weixin.qq.com/",
}

TIMEOUT = httpx.Timeout(20.0, connect=8.0)


class CrawlError(Exception):
    pass


def is_article_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(host.endswith(h) for h in ARTICLE_HOSTS)


def _first_match(patterns: list, text: str, default: str = "") -> str:
    for pat in patterns:
        m = re.search(pat, text, re.S)
        if m:
            value = m.group(1).strip()
            if value:
                return value
    return default


def parse_article(html: str, url: str) -> dict:
    """解析文章页，提取标题、公众号、时间与图片列表。"""
    soup = BeautifulSoup(html, "html.parser")

    title = _first_match([
        r'var\s+msg_title\s*=\s*[\'"](.*?)[\'"]',
        r'<meta\s+property="og:title"\s+content="(.*?)"',
        r'<h1[^>]*class="rich_media_title"[^>]*>(.*?)</h1>',
    ], html)
    if not title:
        tag = soup.select_one("#activity-name")
        title = tag.get_text(strip=True) if tag else ""
    title = re.sub(r"\s+", " ", BeautifulSoup(title, "html.parser").get_text()).strip()

    account = _first_match([
        r'var\s+nickname\s*=\s*[\'"](.*?)[\'"]',
        r'var\s+user_name\s*=\s*[\'"](.*?)[\'"]',
    ], html)
    if not account:
        tag = soup.select_one("#js_name, .profile_nickname, .rich_media_meta_nickname")
        account = tag.get_text(strip=True) if tag else ""

    publish_time = _first_match([
        r'var\s+ct\s*=\s*["\']?(\d{9,10})',
        r'var\s+createTime\s*=\s*["\'](\d{4}-\d{2}-\d{2})',
    ], html)
    if not publish_time:
        tag = soup.select_one("#publish_time, em#publish_time")
        publish_time = tag.get_text(strip=True) if tag else ""
    if publish_time.isdigit() and len(publish_time) == 10:
        import datetime as _dt
        publish_time = _dt.datetime.fromtimestamp(int(publish_time)).strftime("%Y-%m-%d")

    # ---- 图片：正文 img 的 data-src（微信懒加载）+ 封面 + 背景图 ----
    found: list[tuple] = []
    seen = set()

    def add(src: str, alt: str = "", is_cover: bool = False):
        if not src:
            return
        src = src.strip().replace("&amp;", "&")
        if src.startswith("//"):
            src = "https:" + src
        full = urljoin(url, src)
        host = urlparse(full).netloc.lower()
        if not any(host.endswith(h) for h in IMAGE_HOSTS):
            return                                  # 非微信 CDN 一律丢弃
        if "/mmbiz_png/" in full or "wx_fmt=png" in full or "wx_fmt=gif" in full:
            pass                                    # 格式不限，统一保留
        key = full.split("?")[0]
        if key in seen:
            return
        seen.add(key)
        found.append((full, alt, is_cover))

    meta_cover = _first_match([r'var\s+msg_cdn_url\s*=\s*[\'"](.*?)[\'"]',
                               r'<meta\s+property="og:image"\s+content="(.*?)"'], html)
    add(meta_cover, is_cover=True)

    for idx, img in enumerate(soup.select("#js_content img, .rich_media_content img")):
        src = img.get("data-src") or img.get("data-original") or img.get("src") or ""
        add(src, alt=img.get("alt", ""))

    # 兜底：正文里用 background-image 塞的图
    for el in soup.select("#js_content [style*='background-image']"):
        m = re.search(r'url\(["\']?(.*?)["\']?\)', el.get("style", ""))
        if m:
            add(m.group(1))

    if not found:
        raise CrawlError("未在页面中发现微信 CDN 图片（可能已被删除或需要验证）")

    # 搜索渠道给的是带 signature 的临时链接，还原成 __biz/mid/idx/sn 永久链接，
    # 否则「打开原文」过几天就失效了。
    canonical = url
    biz = _first_match([r'var\s+biz\s*=\s*["\']([^"\']*)'], html)
    mid = _first_match([r'var\s+mid\s*=\s*["\']([^"\']*)'], html)
    idx = _first_match([r'var\s+idx\s*=\s*["\']([^"\']*)'], html)
    sn = _first_match([r'var\s+sn\s*=\s*["\']([^"\']*)'], html)
    if biz and mid and idx and sn:
        canonical = (f"https://mp.weixin.qq.com/s?__biz={biz}&mid={mid}"
                     f"&idx={idx}&sn={sn}")

    return {
        "url": canonical,
        "source_url": url,
        "title": title or "(无标题)",
        "account": account,
        "publish_time": publish_time,
        "cover": found[0][0] if found[0][2] else (found[0][0] if found else ""),
        "images": [{"src": s, "alt": a, "cover": 1 if c else 0,
                    "position": i} for i, (s, a, c) in enumerate(found)],
    }


def fetch_article(url: str) -> dict:
    """抓取单篇文章页面并解析。"""
    if not is_article_url(url):
        raise CrawlError("不是微信公众号文章链接（需为 mp.weixin.qq.com）")
    with httpx.Client(headers=HEADERS, timeout=TIMEOUT, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        text = resp.text
    if "环境异常" in text or "去验证" in text or len(text) < 2000:
        raise CrawlError("微信返回了验证页，该链接暂时无法直接抓取")
    return parse_article(text, url)


def download_image(url: str) -> bytes:
    """下载微信 CDN 图片，必须带 Referer 否则会被拒绝。"""
    headers = dict(HEADERS)
    headers["Referer"] = "https://mp.weixin.qq.com/"
    with httpx.Client(headers=headers, timeout=TIMEOUT, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.content


def cache_path_for(url: str) -> str:
    name = hashlib.md5(url.split("?")[0].encode()).hexdigest() + ".img"
    return os.path.join(IMAGE_DIR, name)
