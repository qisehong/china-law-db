"""flk.npc.gov.cn 直连同步引擎

工作流:
  1. 按分类分页检索全部条目（bbbs + 元数据）
  2. 与 .flk_sync_state.json 对比，跳过未变更条目（增量）
  3. 待同步条目分批走官方 download/batch 接口取预签名 URL
  4. 下载 docx → 转 Markdown + YAML → 按分类写入 laws/
  5. 更新同步状态

默认同步: 宪法 / 法律 / 行政法规 / 监察法规 / 司法解释
地方法规按省同步（默认仅沪苏浙，见 src/flk.py LOCAL_REGIONS），--include-local 开启。
"""

import hashlib
import json
import re
import warnings
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.flk import (
    FlkClient,
    LEAF_MAP,
    LEAF_NAMES,
    LOCAL_REGIONS,
    STATUS_MAP,
    TOP_CATEGORIES,
)
from src.docx2md import convert_docx_to_law_md, safe_filename

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = PROJECT_ROOT / "laws"
DEFAULT_STATE = PROJECT_ROOT / ".flk_sync_state.json"
DOCX_CACHE = PROJECT_ROOT / ".cache" / "flk_docx"

DEFAULT_CATEGORIES = ["宪法", "法律", "行政法规", "监察法规", "司法解释"]

BATCH_SIZE = 10  # 每批官方下载接口条数


def meta_hash(row: dict) -> str:
    """条目版本指纹：标题/公布日期/时效性任一变化即视为需更新

    指纹纳入 STATUS_MAP 映射出的标签文本而非仅原始码值：
    否则映射规则变更后旧文件不会被重新生成，留下混杂的旧标签。"""
    sxx = row.get("sxx")
    raw = f"{row.get('title')}|{row.get('gbrq')}|{sxx}|{STATUS_MAP.get(sxx)}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


class FlkSyncEngine:
    def __init__(
        self,
        output_root: Path = DEFAULT_OUTPUT,
        state_file: Path = DEFAULT_STATE,
        client: Optional[FlkClient] = None,
        dry_run: bool = False,
    ):
        self.output_root = Path(output_root)
        self.state_file = Path(state_file)
        self.client = client or FlkClient()
        self.dry_run = dry_run
        self.state: Dict = self.load_state()

    # ----------------------------------------------------------------
    # 状态
    # ----------------------------------------------------------------

    def load_state(self) -> dict:
        if self.state_file.exists():
            try:
                return json.loads(self.state_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, IOError):
                pass
        return {"last_sync": None, "laws": {}}

    def save_state(self) -> None:
        if self.dry_run:
            return
        # 比较式写入：laws 指纹无变化时不写文件（含时间戳字段），
        # 保证无新法时 git 工作区干净，CI 条件提交才不会产生空提交
        current: Optional[dict] = None
        if self.state_file.exists():
            try:
                current = json.loads(self.state_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, IOError):
                current = None
        self.state["last_sync"] = datetime.now().isoformat()
        if current is not None and current.get("laws") == self.state.get("laws"):
            return
        self.state_file.write_text(
            json.dumps(self.state, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    # ----------------------------------------------------------------
    # 单分类同步
    # ----------------------------------------------------------------

    def sync_category(
        self,
        name: str,
        code_ids: List[int],
        force: bool = False,
        limit: Optional[int] = None,
        zdjg_ids: Optional[List[int]] = None,
        region: Optional[str] = None,
    ) -> Tuple[int, int, int]:
        """同步一个顶层分类（地方法规传 zdjg_ids+region 按省检索与路由）。
        返回 (updated, skipped, total)"""
        label = f"{name}·{region}" if region else name
        print(f"\n📂 分类: {label} (codeId={code_ids})")
        rows = list(self.client.search_all(code_ids, zdjg_ids=zdjg_ids))
        if limit:
            rows = rows[:limit]
        total = len(rows)
        print(f"  官方共 {total} 部，开始比对本地状态…")

        todo: List[dict] = []
        skipped = 0
        for row in rows:
            bbbs = row.get("bbbs")
            if not bbbs:
                continue
            h = meta_hash(row)
            rec = self.state["laws"].get(bbbs)
            if not force and rec and rec.get("hash") == h and (self.output_root / rec.get("path", "!")).exists():
                skipped += 1
            else:
                todo.append(row)

        print(f"  需更新 {len(todo)} 部，跳过 {skipped} 部")
        if self.dry_run:
            for row in todo[:20]:
                print(f"    [dry-run] {row.get('title')} ({row.get('gbrq')})")
            if len(todo) > 20:
                print(f"    ... 共 {len(todo)} 部")
            return len(todo), skipped, total

        updated, failed = 0, 0
        for i in range(0, len(todo), BATCH_SIZE):
            chunk = todo[i:i + BATCH_SIZE]
            try:
                dls = self.client.batch_download_urls([r["bbbs"] for r in chunk])
            except RuntimeError as e:
                print(f"  ❌ 批量下载接口失败: {e}")
                failed += len(chunk)
                continue
            for row, dl in zip(chunk, dls):
                url = dl.get("url") or dl.get("urlIn")
                if not url:
                    failed += 1
                    continue
                try:
                    if self._process_one(row, url, name, region=region):
                        updated += 1
                    else:
                        failed += 1
                except Exception as e:
                    print(f"\n  ❌ {row.get('title')}: {e}")
                    failed += 1
            if len(dls) < len(chunk):
                # 官方批量下载偶发返回条数少于请求，zip 会静默截断，缺失部分显式记失败
                failed += len(chunk) - len(dls)
            self.save_state()  # 每批落盘，中断可续
            done = min(i + BATCH_SIZE, len(todo))
            print(f"\r  ⬇️  下载转换进度: {done}/{len(todo)}", end="", flush=True)
        print()
        if failed:
            print(f"  ⚠️ 失败 {failed} 部（可重跑本命令续传）")
        return updated, skipped, total

    def _process_one(self, row: dict, url: str, top_name: str,
                     region: Optional[str] = None) -> bool:
        """下载单个 docx → 转换 → 写入 laws/。成功返回 True

        region 非空时（地方法规按省）：输出 laws/地方法规/<省>/<子类>/，
        frontmatter subcategory 为 "<省>/<子类>"。"""
        bbbs = row["bbbs"]
        docx_path = DOCX_CACHE / f"{bbbs}.docx"
        try:
            sxx = row.get("sxx")
            npc_status = STATUS_MAP.get(sxx)
            if sxx is not None and npc_status is None:
                warnings.warn(f"sxx={sxx} 未在 STATUS_MAP 中定义（{row.get('title')}），status 将记为未知")
            self.client.download_file(url, docx_path)
            leaf = self.client.leaf_of(row)
            category, sub = LEAF_MAP.get(leaf, (top_name, None))
            if region:
                category = top_name
                leaf_name = sub or LEAF_NAMES.get(leaf, "")
                sub = f"{region}/{leaf_name}" if leaf_name else region

            result = convert_docx_to_law_md(
                docx_path,
                category,
                sub,
                npc_status=npc_status,
                issuing_authority=row.get("zdjgName"),
                api_publish_date=row.get("gbrq"),
            )
            # 标题优先取官方 API（docx 首段偶尔含书名号/空格差异）；
            # 折叠标题中的软换行/回车（会破坏 YAML 与文件名）
            title = re.sub(r"[\r\n]+", "", (row.get("title") or result["title"])).strip()
            pub_date = row.get("gbrq") or result.get("publish_date") or ""
            date_tag = f"({pub_date})" if pub_date else ""

            rel_dir = Path(category) / sub if sub else Path(category)
            out_path = self.output_root / rel_dir / f"{safe_filename(title)}{date_tag}.md"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            # 不同公布日期的版本共存（与仓库现有惯例一致，如 专利法实施细则(2010)/(2023).md）

            # 标题/日期变化会改变文件名，清理 state 中登记的旧文件，避免残留副本
            rec = self.state["laws"].get(bbbs)
            if rec and rec.get("path"):
                old_path = self.output_root / rec["path"]
                if old_path != out_path and old_path.exists():
                    old_path.unlink()

            out_path.write_text(result["content"], encoding="utf-8")
            self.state["laws"][bbbs] = {
                "hash": meta_hash(row),
                # POSIX 分隔符入库：状态文件跨 Windows/Linux（CI）使用，
                # 反斜杠在 Linux 上不是路径分隔符，会令增量判断全部失效
                "path": out_path.relative_to(self.output_root).as_posix(),
                "title": title,
                "synced": datetime.now().strftime("%Y-%m-%d"),
            }
            return True
        finally:
            if docx_path.exists():
                docx_path.unlink()

    # ----------------------------------------------------------------
    # 全量入口
    # ----------------------------------------------------------------

    def sync_all(
        self,
        categories: Optional[List[str]] = None,
        include_local: bool = False,
        force: bool = False,
        limit: Optional[int] = None,
    ) -> dict:
        categories = list(categories or DEFAULT_CATEGORIES)
        # 地方法规：按省同步（仅 LOCAL_REGIONS 中配置的省份）
        want_local = include_local or "地方法规" in categories
        categories = [c for c in categories if c != "地方法规"]
        results = {}
        for name in categories:
            cfg = TOP_CATEGORIES.get(name)
            if not cfg:
                print(f"⚠️ 未知分类: {name}，可选: {list(TOP_CATEGORIES)}")
                continue
            results[name] = self.sync_category(name, cfg["code"], force=force, limit=limit)
        if want_local:
            for region, cfg in LOCAL_REGIONS.items():
                results[f"地方法规·{region}"] = self.sync_category(
                    "地方法规", cfg["code"], force=force, limit=limit,
                    zdjg_ids=cfg["zdjg"], region=region,
                )
        self.save_state()
        return results

    # ----------------------------------------------------------------
    # 统计
    # ----------------------------------------------------------------

    def get_stats(self) -> dict:
        categories: Dict[str, int] = {}
        total = 0
        if self.output_root.exists():
            for p in self.output_root.rglob("*.md"):
                rel = p.relative_to(self.output_root)
                if len(rel.parts) > 1:
                    cat = rel.parts[0]
                    # 地方法规按省细分统计（地方法规/上海）
                    if cat == "地方法规" and len(rel.parts) > 2:
                        cat = f"地方法规/{rel.parts[1]}"
                else:
                    cat = "未分类"
                categories[cat] = categories.get(cat, 0) + 1
                total += 1
        return {"total": total, "categories": dict(sorted(categories.items(), key=lambda x: -x[1]))}

    def verify_against_official(self) -> List[Tuple[str, int, int]]:
        """本地各分类数量 vs 官方检索总数（含全部时效性版本，与本地口径一致）

        官方首页 aggregate 计数是"现行有效"口径，而本地保留历史版本，
        两者直接相比必然"本地多出"，无校验意义，故统一改用检索 total 比对。"""
        local = self.get_stats()["categories"]
        rows = []
        for name, cfg in TOP_CATEGORIES.items():
            if cfg.get("local"):
                # 地方法规官方全量 1.5 万+，本地仅同步 LOCAL_REGIONS 配置的省份（沪苏浙），逐省比对
                for region, rcfg in LOCAL_REGIONS.items():
                    _, r_total = self.client.search_category(
                        rcfg["code"], page=1, page_size=1, zdjg_ids=rcfg["zdjg"]
                    )
                    rows.append((f"地方法规·{region}", local.get(f"地方法规/{region}", 0), r_total))
                continue
            _, total = self.client.search_category(cfg["code"], page=1, page_size=1)
            rows.append((name, local.get(name, 0), total))
        return rows
