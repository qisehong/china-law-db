"""增量更新编排器

编排三层更新策略：
  1. NPC 官方 API → 检测最新立法、比对本地缺失
  2. Wikisource → 搜索并抓取缺失法律的全文
  3. 格式转换 → wikitext → Markdown + YAML
"""

import time
import re
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional

from src.client import NPCClient, STATUS_MAP
from src.wikisource import WikisourceClient


TYPE_TO_CATEGORY = {
    "法律":      "法律",
    "行政法规":   "行政法规",
    "司法解释":   "司法解释",
    "监察法规":   "监察法规",
    "部门规章":   "部门规章",
    "宪法":       "法律/宪法相关法",
}


class Updater:
    """增量更新编排器"""

    def __init__(self, laws_dir: Path):
        self.laws_dir = Path(laws_dir)
        self.npc = NPCClient()
        self.ws = WikisourceClient()

    def _build_local_index(self) -> Dict[str, Path]:
        """构建本地法律标题 → 文件路径的索引"""
        index = {}
        for md_file in self.laws_dir.rglob("*.md"):
            name = md_file.stem
            name_clean = re.sub(r'\(?\d{4}(-\d{2}-\d{2})?\)?$', '', name).strip()
            index[name_clean] = md_file
            index[name] = md_file
        return index

    def detect_missing(self, check_limit: int = 50) -> List[dict]:
        """检测 NPC 最新立法中本地缺失的国家层面法律"""
        print(f"[detect] Checking NPC database latest {check_limit} laws...")
        local = self._build_local_index()
        local_titles = set(local.keys())
        missing = self.npc.compare_with_local(local_titles, limit=check_limit)
        print(f"[detect] Found {len(missing)} potentially missing laws")

        # Also check Wikisource for known 2026 laws
        known_2026 = self._check_wikisource_2026(local_titles)
        for law in known_2026:
            if law["title"] not in {m["title"] for m in missing}:
                missing.append(law)

        return missing

    def _check_wikisource_2026(self, local_titles: set) -> List[dict]:
        """直接检查 Wikisource 上的 2026 年新法"""
        known_2026_pages = [
            ("中华人民共和国监狱法 (2026年)", "法律"),
            ("中华人民共和国国家发展规划法", "法律"),
            ("中华人民共和国民族团结进步促进法", "法律"),
            ("中华人民共和国社会救助法", "法律"),
            ("中华人民共和国生态环境法典", "法律"),
        ]
        new_laws = []
        for title, law_type in known_2026_pages:
            clean = title.replace("中华人民共和国", "").replace(" (2026年)", "")
            found = any(clean in t for t in local_titles)
            if not found:
                new_laws.append({
                    "title": title,
                    "flxz": law_type,
                    "bbbs": f"ws-{clean}",
                    "source": "wikisource_known",
                })
        if new_laws:
            print(f"[ws2026] Found {len(new_laws)} known 2026 laws not in local")
        return new_laws

    def fetch_from_wikisource(self, title: str) -> Optional[tuple]:
        """从 Wikisource 抓取并解析法律，返回 (body, meta) 或 None"""
        print(f"  [ws] Searching: {title}")
        results = self.ws.search(title, limit=5)
        if not results:
            print(f"  [ws] No results")
            return None

        for r in results[:3]:
            page_title = r["title"]
            print(f"  [ws] Trying: {page_title}...")
            content = self.ws.fetch_content(page_title)
            if content and len(content) > 500:
                body, meta = self.ws.parse_wikitext(content)
                meta["source_page"] = page_title
                return body, meta

        print(f"  [ws] All pages too short")
        return None

    def save_law(self, title: str, body: str, meta: dict,
                 category: str = None) -> Optional[Path]:
        """保存法律为 Markdown + YAML 文件"""
        cat = category or "其他"
        cat_dir = self.laws_dir / cat
        cat_dir.mkdir(parents=True, exist_ok=True)

        pub_date = meta.get("publish_date", "")
        date_tag = pub_date[:4] if pub_date else ""
        safe_name = title.replace("中华人民共和国", "").replace(" ", "")
        if not safe_name:
            safe_name = title
        filename = f"{safe_name}({date_tag}).md" if date_tag else f"{safe_name}.md"
        outpath = cat_dir / filename

        status_str = meta.get("status", "现行有效")
        fm = ["---"]
        fm.append(f'title: "{title}"')
        if meta.get("publish_date"):
            fm.append(f'publish_date: "{meta["publish_date"]}"')
        if meta.get("implement_date"):
            fm.append(f'implement_date: "{meta["implement_date"]}"')
        fm.append(f'status: "{status_str}"')
        fm.append(f'category: "{cat}"')
        fm.append(f'source: "国家法律法规数据库 (via Wikisource)"')
        if meta.get("lawmaker"):
            fm.append(f'issuing_authority: "{meta["lawmaker"]}"')
        fm.append(f'synced: "{datetime.now().strftime("%Y-%m-%d")}"')
        fm.append("tags:")
        fm.append("  - 法律法规")
        fm.append(f"  - {cat}")
        fm.append("---")
        fm.append("")
        fm.append(f"# {title}")
        fm.append("")
        if meta.get("publish_date"):
            fm.append(f"{meta['publish_date']} 公布")
        if meta.get("implement_date"):
            fm.append(f"{meta['implement_date']} 施行")
        fm.append("")
        fm.append(body)

        with open(outpath, "w", encoding="utf-8") as f:
            f.write("\n".join(fm))

        return outpath

    def run(self, check_limit: int = 50, dry_run: bool = False) -> dict:
        """执行增量更新"""
        results = {"detected": 0, "fetched": 0, "saved": 0, "failed": []}

        missing = self.detect_missing(check_limit=check_limit)
        results["detected"] = len(missing)

        if not missing:
            print("Local database is up to date")
            return results

        for i, law in enumerate(missing, 1):
            title = law.get("title", "")
            law_type = law.get("flxz", "")
            law_id = law.get("bbbs", "")

            print(f"\n[{i}/{len(missing)}] [{law_type}] {title}")

            if dry_run:
                print(f"  [dry-run] Would fetch from Wikisource")
                continue

            # For wikisource_known, use the title directly as page name
            if law.get("source") == "wikisource_known":
                print(f"  [ws] Known page: {title}")
                content = self.ws.fetch_content(title)
                if content and len(content) > 500:
                    body, meta = self.ws.parse_wikitext(content)
                    results["fetched"] += 1
                else:
                    results["failed"].append(title)
                    continue
            else:
                result = self.fetch_from_wikisource(title)
                if result is None:
                    results["failed"].append(title)
                    continue
                body, meta = result
                results["fetched"] += 1

            cat = TYPE_TO_CATEGORY.get(law_type, "其他")
            for kw, c in {"刑法": "法律/刑法", "民法典": "民法典", "诉讼": "法律/诉讼与非诉讼程序法"}.items():
                if kw in title and law_type == "法律":
                    cat = c
                    break

            outpath = self.save_law(title, body, meta, cat)
            if outpath:
                results["saved"] += 1
                print(f"  Saved: {outpath.relative_to(self.laws_dir)}")
            else:
                results["failed"].append(title)

            if i < len(missing):
                time.sleep(2)

        print(f"\nUpdate complete: detected={results['detected']} fetched={results['fetched']} saved={results['saved']}")
        if results["failed"]:
            print(f"Failed: {', '.join(results['failed'])}")

        return results
