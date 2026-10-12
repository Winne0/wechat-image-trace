"""把一批文章链接灌进索引库（命令行版，适合批量）。

用法：
    python3 scripts/import_urls.py scripts/seed_urls.txt
"""

from __future__ import annotations

import sys
import time

import httpx

BASE = "http://localhost:3000"


def main():
    if len(sys.argv) < 2:
        print("用法: python3 scripts/import_urls.py <链接文件>")
        return 1

    with open(sys.argv[1], encoding="utf-8") as fh:
        urls = [ln.strip() for ln in fh if ln.strip().startswith("http")]

    if not urls:
        print("文件里没有有效链接")
        return 1

    print(f"共 {len(urls)} 篇待导入")
    for batch_start in range(0, len(urls), 20):
        batch = urls[batch_start:batch_start + 20]
        job_id = httpx.post(f"{BASE}/api/import", json={"urls": batch},
                            timeout=30).json()["job_id"]
        while True:
            time.sleep(1.5)
            st = httpx.get(f"{BASE}/api/import/{job_id}", timeout=15).json()
            print(f"\r  进度 {st['done']}/{st['total']}", end="", flush=True)
            if st["status"] == "done":
                break
        print()
        for r in st.get("results", []):
            mark = {"ok": "已收录", "skipped": "已存在", "failed": "失败"}[r["status"]]
            title = r.get("title") or r.get("error") or r.get("url", "")
            print(f"    {mark}  {title[:60]}")

    stats = httpx.get(f"{BASE}/api/stats", timeout=15).json()
    print(f"\n索引库现状：文章 {stats['articles_ok']} 篇 / 图片 {stats['images']} 张")
    return 0


if __name__ == "__main__":
    sys.exit(main())
