"""通过搜狗微信搜索采集真实公众号文章链接，用于索引库冷启动。

搜狗对纯 HTTP 请求有反爬拦截（/link 跳转会落到 antispider 页），
这里改用真实浏览器会话访问，成功率更高。

用法：
    python3 scripts/seed_collect.py --keywords 人工智能,美食 --per-keyword 4
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time

from playwright.sync_api import sync_playwright

SOGOU = "https://weixin.sogou.com/weixin?type=2&query={}"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

DEFAULT_KEYWORDS = [
    "人工智能", "旅游攻略", "美食推荐", "摄影技巧", "健身入门",
    "投资理财", "考研经验", "家居装修", "新能源汽车", "读书笔记",
]


def collect(keywords: list, per_keyword: int, out_file: str, headless: bool = True):
    found: list = []
    seen = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless,
                                    args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(user_agent=UA, locale="zh-CN",
                                  viewport={"width": 1280, "height": 900})
        page = ctx.new_page()

        for kw in keywords:
            print(f"[搜索] {kw}", file=sys.stderr)
            try:
                page.goto(SOGOU.format(kw), wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(1500)
            except Exception as exc:
                print(f"  搜索页失败: {exc}", file=sys.stderr)
                continue

            if "antispider" in page.url or page.locator("text=请输入验证码").count():
                print("  遇到验证码，等待后重试…", file=sys.stderr)
                page.wait_for_timeout(3000)
                try:
                    page.reload(wait_until="domcontentloaded")
                except Exception:
                    pass

            hrefs = page.eval_on_selector_all(
                "h3 a", "els => els.map(e => e.getAttribute('href'))",
            ) or []
            hrefs = [h for h in hrefs if h and "/link?url=" in h][:per_keyword]

            for h in hrefs:
                link = "https://weixin.sogou.com" + h if h.startswith("/") else h
                try:
                    page.goto(link, wait_until="domcontentloaded", timeout=25000)
                    page.wait_for_timeout(800)
                    final = page.url
                except Exception:
                    continue
                if "mp.weixin.qq.com" in final:
                    clean = final.split("#")[0]
                    if clean in seen:
                        continue
                    seen.add(clean)
                    found.append(clean)
                    print(f"  + {clean[:78]}", file=sys.stderr)
                time.sleep(random.uniform(0.8, 1.8))

            time.sleep(random.uniform(1.5, 3.0))

        browser.close()

    os.makedirs(os.path.dirname(out_file) or ".", exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as fh:
        fh.write("\n".join(found))
    print(f"\n共采集 {len(found)} 篇，已写入 {out_file}", file=sys.stderr)
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keywords", default=",".join(DEFAULT_KEYWORDS))
    ap.add_argument("--per-keyword", type=int, default=4)
    ap.add_argument("--out", default="scripts/seed_urls.txt")
    ap.add_argument("--headful", action="store_true")
    args = ap.parse_args()

    kws = [k.strip() for k in args.keywords.split(",") if k.strip()]
    collect(kws, args.per_keyword, args.out, headless=not args.headful)


if __name__ == "__main__":
    main()
