"""法律法规定期同步引擎

从 LawRefBook/Laws 源仓库检测新增/变更的法律文件，
注入 YAML frontmatter 并组织到分类目录。

工作流:
  1. 扫描源仓库所有 .md 文件
  2. 计算 MD5 hash，与上次同步状态对比
  3. 确定目标输出路径（按分类映射）
  4. 调用 convert.py 转换格式
  5. 保存同步状态
"""

import json
import hashlib
import shutil
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from src.convert import convert_file


# ====================================================================
# 分类映射：LawRefBook 源目录 → 输出目录
# ====================================================================
CATEGORY_MAP = {
    "宪法":           "法律/宪法相关法",
    "宪法相关法":      "法律/宪法相关法",
    "民法典":          "民法典",
    "民法商法":        "法律/民法商法",
    "行政法":          "法律/行政法",
    "经济法":          "法律/经济法",
    "社会法":          "法律/社会法",
    "刑法":            "法律/刑法",
    "诉讼与非诉讼程序法": "法律/诉讼与非诉讼程序法",
    "行政法规":        "行政法规",
    "司法解释":        "司法解释",
    "部门规章":        "部门规章",
    "监察法规":        "监察法规",
    "其他":            "其他",
}

# 排除的目录（地方法规、脚本、案例等）
EXCLUDE_DIRS = {
    "DLC", ".git", ".github", "scripts", "__pycache__",
    "案例", ".cache",
}

# 排除的文件
EXCLUDE_FILES = {
    "_index.md", "README.md", "法律法规模版.md",
}

# 输出目录基础结构（预创建以确保空目录也存在）
OUTPUT_CATEGORIES = [
    "法律/宪法相关法",
    "法律/民法商法",
    "法律/行政法",
    "法律/经济法",
    "法律/社会法",
    "法律/刑法",
    "法律/诉讼与非诉讼程序法",
    "民法典",
    "行政法规",
    "司法解释",
    "部门规章",
    "监察法规",
    "其他",
]


class SyncEngine:
    """同步引擎：将 LawRefBook 源文件转换为带 YAML frontmatter 的法律文件"""

    def __init__(
        self,
        source_root: Path,
        output_root: Path,
        state_file: Path,
        dry_run: bool = False,
    ):
        self.source_root = Path(source_root)
        self.output_root = Path(output_root)
        self.state_file = Path(state_file)
        self.dry_run = dry_run

    # ----------------------------------------------------------------
    # 状态管理
    # ----------------------------------------------------------------

    def load_state(self) -> dict:
        """加载上次同步状态"""
        if self.state_file.exists():
            try:
                with open(self.state_file, encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        return {"last_sync": None, "file_hashes": {}}

    def save_state(self, state: dict) -> None:
        """保存同步状态"""
        state["last_sync"] = datetime.now().isoformat()
        if not self.dry_run:
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)

    # ----------------------------------------------------------------
    # 文件工具
    # ----------------------------------------------------------------

    @staticmethod
    def hash_file(path: Path) -> str:
        """计算文件 MD5 哈希"""
        with open(path, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()

    def get_output_path(self, src_rel: Path) -> Optional[Path]:
        """根据源文件路径确定输出路径

        例: "刑法/刑法.md" → laws/法律/刑法/刑法.md
            "行政法规/xxx(2020-01-01).md" → laws/行政法规/xxx(2020-01-01).md
        """
        parts = src_rel.parts
        if not parts:
            return None

        cat = parts[0]
        if cat in EXCLUDE_DIRS:
            return None

        mapped = CATEGORY_MAP.get(cat, cat)
        filename = parts[-1]
        return self.output_root / mapped / filename

    # ----------------------------------------------------------------
    # 源文件扫描
    # ----------------------------------------------------------------

    def scan_source_files(self) -> List[Path]:
        """扫描源仓库中所有需要同步的 .md 文件"""
        files = []
        for p in self.source_root.rglob("*.md"):
            # 跳过排除的文件名
            if p.name in EXCLUDE_FILES:
                continue

            # 跳过排除的目录
            rel = p.relative_to(self.source_root)
            if any(part in EXCLUDE_DIRS for part in rel.parts):
                continue

            files.append(p)

        return sorted(files)

    # ----------------------------------------------------------------
    # 同步
    # ----------------------------------------------------------------

    def ensure_output_dirs(self) -> None:
        """确保所有输出分类目录存在"""
        for cat in OUTPUT_CATEGORIES:
            (self.output_root / cat).mkdir(parents=True, exist_ok=True)

    def sync_incremental(self) -> Tuple[int, int, int]:
        """增量同步：仅处理新增或变更的文件

        返回: (added_or_updated, removed, total)
        """
        state = self.load_state()
        old_hashes = state.get("file_hashes", {})
        new_hashes = {}
        synced = 0
        errors = 0

        self.ensure_output_dirs()

        all_files = self.scan_source_files()
        total = len(all_files)

        for src in all_files:
            rel = str(src.relative_to(self.source_root))
            h = self.hash_file(src)
            new_hashes[rel] = h

            dst = self.get_output_path(src.relative_to(self.source_root))
            if dst is None:
                continue

            # 跳过未变更的文件
            if rel in old_hashes and old_hashes[rel] == h and dst.exists():
                continue

            if self.dry_run:
                print(f"  [dry-run] {rel} → {dst.relative_to(self.output_root)}")
                synced += 1
                continue

            try:
                convert_file(src, dst)
                synced += 1
            except Exception as e:
                print(f"  ❌ 转换失败 {rel}: {e}")
                errors += 1

        # 清理：移除源仓库中已删除的文件
        removed = 0
        for old_rel, old_hash in old_hashes.items():
            if old_rel not in new_hashes:
                dst = self.get_output_path(Path(old_rel))
                if dst and dst.exists() and not self.dry_run:
                    dst.unlink()
                    removed += 1

        # 保存新状态
        state["file_hashes"] = new_hashes
        self.save_state(state)

        return synced, removed, total

    def sync_full(self) -> Tuple[int, int, int]:
        """全量同步：清除缓存，重新处理所有文件

        返回: (processed, removed, total)
        """
        state = self.load_state()
        state["file_hashes"] = {}
        self.save_state(state)
        return self.sync_incremental()

    # ----------------------------------------------------------------
    # 统计
    # ----------------------------------------------------------------

    def get_stats(self) -> dict:
        """获取输出目录的统计信息"""
        categories: Dict[str, int] = {}
        total = 0

        if not self.output_root.exists():
            return {"total": 0, "categories": {}}

        for p in self.output_root.rglob("*.md"):
            try:
                cat = str(p.relative_to(self.output_root).parts[0])
            except (ValueError, IndexError):
                cat = "未分类"
            categories[cat] = categories.get(cat, 0) + 1
            total += 1

        # 按数量降序排列
        sorted_cats = dict(
            sorted(categories.items(), key=lambda x: (-x[1], x[0]))
        )

        return {"total": total, "categories": sorted_cats}
