#!/usr/bin/env python3
"""Import 2026 national laws from Wikisource."""

import sys, time
sys.path.insert(0, "/home/daniel/Documents/china-law-db")
from pathlib import Path
from src.wikisource import WikisourceClient
from src.updater import Updater

ws = WikisourceClient()
updater = Updater(Path("/home/daniel/Documents/china-law-db/laws"))

LAWS = [
    ("中华人民共和国监狱法 (2026年)", "法律/行政法"),
    ("中华人民共和国国家发展规划法", "法律/宪法相关法"),
    ("中华人民共和国民族团结进步促进法", "法律/宪法相关法"),
    ("中华人民共和国社会救助法", "法律/社会法"),
    ("中华人民共和国生态环境法典", "法律/行政法"),
]

for title, cat in LAWS:
    print(f"Processing: {title}")
    content = ws.fetch_content(title)
    if not content:
        print(f"  SKIP: no content")
        continue
    body, meta = ws.parse_wikitext(content)
    law_title = meta.get("title", title).strip("[[]]")
    outpath = updater.save_law(law_title, body, meta, cat)
    if outpath:
        print(f"  OK: {outpath.relative_to(updater.laws_dir)}")
    else:
        print(f"  FAILED")
    time.sleep(2)

print("Done!")
