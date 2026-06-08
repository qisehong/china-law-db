"""LawRefBook 格式 → YAML frontmatter + Markdown 转换器

LawRefBook 原始格式：
    # 标题
    日期行（纯文本）
    <!-- INFO END -->
    ## 编 / ### 章 / #### 节
    第X条 内容

输出格式：
    ---
    title: "..."
    publish_date: "..."
    status: "..."
    ...
    ---
    （原始内容保留）
"""

import re
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict


# ------------------------------------------------------------------
# 元数据提取
# ------------------------------------------------------------------

def parse_lawrefbook_metadata(filepath: Path) -> Dict:
    """从 LawRefBook 格式的法律文件中提取元数据

    返回:
        title, subtitle, publish_date, implement_date,
        dates (所有日期行), status, category
    """
    meta: Dict = {
        "title": None,
        "subtitle": None,
        "publish_date": None,
        "implement_date": None,
        "dates": [],
        "status": "现行有效",
        "category": None,
    }

    try:
        with open(filepath, encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return meta

    lines = content.split("\n")
    if not lines:
        return meta

    # --- 提取标题 (# 开头的行，仅前 20 行) ---
    title_lines = []
    for line in lines[:20]:
        stripped = line.strip()
        if stripped.startswith("# ") and not stripped.startswith("## "):
            title_lines.append(stripped[2:].strip())

    if title_lines:
        meta["title"] = title_lines[0]
    if len(title_lines) > 1:
        meta["subtitle"] = title_lines[1]

    # --- 提取日期行 ---
    date_pattern = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")

    for line in lines[:50]:
        stripped = line.strip()
        if "<!-- INFO END -->" in stripped:
            break
        match = date_pattern.search(stripped)
        if match:
            date_str = (
                f"{int(match.group(1)):04d}-"
                f"{int(match.group(2)):02d}-"
                f"{int(match.group(3)):02d}"
            )
            # 施行/生效 日期优先级最高
            if "施行" in stripped or "生效" in stripped:
                meta["implement_date"] = date_str
            elif meta["publish_date"] is None:
                meta["publish_date"] = date_str

            meta["dates"].append(stripped)

    # --- 状态判定 ---
    first_text = "\n".join(lines[:50])
    if any(kw in first_text for kw in ["废止", "失效"]):
        meta["status"] = "废止或失效"
    elif "已被修改" in first_text:
        meta["status"] = "已被修改"
    elif "试行" in first_text:
        meta["status"] = "试行"
    else:
        meta["status"] = "现行有效"

    # --- 分类（从父目录名推断）---
    meta["category"] = filepath.parent.name if filepath.parent else None

    return meta


# ------------------------------------------------------------------
# YAML frontmatter 生成
# ------------------------------------------------------------------

def generate_frontmatter(meta: Dict) -> str:
    """根据元数据生成 Obsidian-compatible YAML frontmatter"""
    title = meta.get("title") or "未知法律法规"
    tags = ["法律法规"]

    fm = ["---"]
    fm.append(f'title: "{title}"')

    if meta.get("subtitle"):
        fm.append(f'subtitle: "{meta["subtitle"]}"')

    if meta.get("publish_date"):
        fm.append(f'publish_date: "{meta["publish_date"]}"')

    if meta.get("implement_date"):
        fm.append(f'implement_date: "{meta["implement_date"]}"')

    fm.append(f'status: "{meta.get("status", "现行有效")}"')

    category = meta.get("category")
    if category:
        fm.append(f'category: "{category}"')
        tags.append(category)

    fm.append('source: "国家法律法规数据库"')
    fm.append(f'synced: "{datetime.now().strftime("%Y-%m-%d")}"')

    fm.append("tags:")
    for t in tags:
        fm.append(f"  - {t}")

    fm.append("---")
    fm.append("")

    return "\n".join(fm)


# ------------------------------------------------------------------
# 主转换函数
# ------------------------------------------------------------------

def convert_file(src: Path, dst: Path) -> bool:
    """转换单个 LawRefBook 文件为 YAML + Markdown

    返回 True 表示成功
    """
    meta = parse_lawrefbook_metadata(src)

    with open(src, encoding="utf-8") as f:
        content = f.read()

    # 移除可能存在的 Hugo frontmatter（_index.md 等）
    if content.startswith("---"):
        idx = content.find("---", 3)
        if idx != -1:
            content = content[idx + 3:].lstrip()

    # 确保目标目录存在
    dst.parent.mkdir(parents=True, exist_ok=True)

    frontmatter = generate_frontmatter(meta)

    with open(dst, "w", encoding="utf-8") as f:
        f.write(frontmatter + content)

    return True
