"""Wikisource 法律抓取与解析模块

从 zh.wikisource.org 搜索和抓取中国法律法规全文，
将 wikitext 转换为 Markdown + YAML frontmatter。

用法:
  from src.wikisource import WikisourceClient
  client = WikisourceClient()
  results = client.search("中华人民共和国监狱法")
  content, meta = client.parse_wikitext(raw_wikitext)
"""

import re
import json
import urllib.parse
import subprocess
from pathlib import Path
from typing import Optional, Dict, List, Tuple

WIKISOURCE_API = "https://zh.wikisource.org/w/api.php"
USER_AGENT = "Mozilla/5.0 (compatible; ChinaLawDB/1.0; +https://github.com)"


def _curl_get(url: str, timeout: int = 20) -> Optional[str]:
    """使用 curl 发送 GET 请求（避免 Python SSL 问题）"""
    try:
        result = subprocess.run(
            ["curl", "-s", "--connect-timeout", str(timeout),
             "-H", f"User-Agent: {USER_AGENT}", url],
            capture_output=True, text=True, timeout=timeout + 5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
        return None
    except Exception:
        return None


class WikisourceClient:
    """Wikisource 法律客户端"""

    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = cache_dir or Path(".cache/wikisource")
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def search(self, keyword: str, limit: int = 10) -> List[dict]:
        """搜索 Wikisource 中的法律页面"""
        params = urllib.parse.urlencode({
            "action": "query", "format": "json",
            "list": "search", "srsearch": keyword, "srlimit": limit,
        })
        url = f"{WIKISOURCE_API}?{params}"
        raw = _curl_get(url, timeout=20)
        if raw is None:
            return []
        try:
            data = json.loads(raw)
            return [
                {"title": r["title"], "size": r["size"], "pageid": r["pageid"]}
                for r in data.get("query", {}).get("search", [])
            ]
        except json.JSONDecodeError:
            return []

    def fetch_content(self, title: str) -> Optional[str]:
        """获取 Wikisource 页面的原始 wikitext"""
        encoded = urllib.parse.quote(title)
        params = urllib.parse.urlencode({
            "action": "query", "format": "json",
            "prop": "revisions", "titles": title,
            "rvprop": "content", "rvslots": "main",
        })
        url = f"{WIKISOURCE_API}?{params}"
        raw = _curl_get(url, timeout=30)
        if raw is None:
            return None
        try:
            data = json.loads(raw)
            pages = data.get("query", {}).get("pages", {})
            for _pid, page in pages.items():
                if "missing" in page:
                    return None
                revs = page.get("revisions", [])
                if revs:
                    return revs[0].get("slots", {}).get("main", {}).get("*")
            return None
        except json.JSONDecodeError:
            return None

    def parse_wikitext(self, content: str) -> Tuple[str, dict]:
        """将 wikitext 转换为 Markdown 正文 + 元数据

        返回: (markdown_body, metadata_dict)
        """
        meta: Dict = {
            "title": None, "publish_date": None, "implement_date": None,
            "status": "现行有效", "lawmaker": None, "notes": "",
        }

        # --- 提取 header 元数据 ---
        header_match = re.search(
            r'\{\{[Hh]eader\s*\n?(.*?)\}\}', content, re.DOTALL
        )
        if header_match:
            header = header_match.group(1)
            # 日期: y=2026|m=4|d=30
            ym = re.search(
                r'y\s*=\s*(\d{4}).*?m\s*=\s*(\d{1,2}).*?d\s*=\s*(\d{1,2})',
                header
            )
            if ym:
                meta["publish_date"] = (
                    f"{ym.group(1)}-{ym.group(2).zfill(2)}-{ym.group(3).zfill(2)}"
                )
            # 施行日期
            eff = re.search(
                r'生效日期\s*=\s*(\d{4})年(\d{1,2})月(\d{1,2})日', header
            )
            if eff:
                meta["implement_date"] = (
                    f"{eff.group(1)}-{eff.group(2).zfill(2)}-{eff.group(3).zfill(2)}"
                )
            # lawmaker
            lm = re.search(r'lawmaker\s*=\s*(.+?)(?:\n|$)', header)
            if lm:
                meta["lawmaker"] = lm.group(1).strip()
            # notes
            notes_match = re.search(
                r'notes\s*=\s*(.+?)(?:\n\||\n\})', header, re.DOTALL
            )
            if notes_match:
                meta["notes"] = notes_match.group(1).strip()

        # --- 提取标题 ---
        title_match = re.search(
            r'\|\s*title\s*=\s*(.+?)(?:\n|$)', content[:1000]
        )
        if title_match:
            meta["title"] = title_match.group(1).strip()

        # --- 移除模板 ---
        body = content
        # 移除 header
        body = re.sub(
            r'\{\{[Hh]eader\s*\n?.*?\}\}', '', body, flags=re.DOTALL
        )
        # 移除 onlyinclude 标签
        body = body.replace('<onlyinclude>', '').replace('</onlyinclude>', '')
        # 移除 {{Gap}}
        body = body.replace('{{Gap}}', '').replace('{{gap}}', '')
        # 移除 PD 和 nav 模板
        body = re.sub(r'\{\{PD-PRC-exempt\}\}', '', body)
        body = re.sub(r'\{\{PRC-\w+-\w+\|.*?\}\}', '', body)
        body = re.sub(r'\{\{中华人民共和国法律.*?\}\}', '', body)
        body = re.sub(
            r'\{\{中华人民共和国.*?\}\}', '', body, flags=re.DOTALL
        )
        body = re.sub(r'\{\{[Cc]enter\|(.*?)\}\}', r'\1', body)

        # --- 转换标题 ---
        body = re.sub(
            r'^==([^=]+)==[ \t]*$', r'## \1', body, flags=re.MULTILINE
        )
        body = re.sub(
            r'^===([^=]+)===[ \t]*$', r'### \1', body, flags=re.MULTILINE
        )
        body = re.sub(
            r'^====([^=]+)====[ \t]*$', r'#### \1', body, flags=re.MULTILINE
        )

        # --- 转换粗体条目 ---
        # '''第X条''' → 直接作为条目行
        body = re.sub(r"'''(.+?)'''[\s　]*", r'\1 ', body)

        # --- 清理 ---
        body = re.sub(r'\n{3,}', '\n\n', body)
        body = body.strip()

        return body, meta
