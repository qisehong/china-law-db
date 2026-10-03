#!/usr/bin/env python3
"""一次性迁移（2026-10-03）：status 标签修正 + 状态文件跨平台化 + 旧命名副本清理

背景：
  1. STATUS_MAP 曾与官方定义不符（官方前端筛选项实测：1=已废止/2=已修改/3=有效/4=尚未生效），
     旧指纹不含映射标签，导致改表后旧文件不会重生成。本脚本按官方检索元数据
     就地重写 frontmatter 的 status 行——status 取自检索接口而非 docx 正文，无需重新下载。
  2. 状态文件 path 为 Windows 反斜杠，Linux CI 上增量判断全部失效 → 统一改 POSIX。
  3. 状态文件指纹改用新算法（纳入映射标签），本脚本按新算法重算，避免下次同步全量重下。
  4. 清理 2026-06-08 旧版导入的旧命名副本（判定标准：标题核心与库内新命名文件相同，
     仅相差"中华人民共和国"前缀/公布日期标签）。无官方对应条目的孤儿文件保留，
     清单写入 .cache/orphan_unmatched_2026-10-03.txt 供人工复核。

用法：
  python scripts/migrate_status_2026-10.py --apply   # 实际执行
  python scripts/migrate_status_2026-10.py           # 仅预览（拉取官方列表并统计，不写任何文件）
"""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.flk import FlkClient, TOP_CATEGORIES, LOCAL_REGIONS, STATUS_MAP
from src.sync_flk import meta_hash, DEFAULT_STATE, DEFAULT_OUTPUT

DATE_TAG_RE = re.compile(r"\((\d{4}-\d{2}-\d{2})\)")
NOISE_RE = re.compile(r"[\s（）()《》、，,．.·\-—]")


def core_name(s: str) -> str:
    """文件名 → 标题核心：去掉日期标签与标点空白，用于同法判定"""
    s = DATE_TAG_RE.sub("", s)
    s = s.rsplit(".", 1)[0] if s.endswith(".md") else s
    return NOISE_RE.sub("", s)


def strip_prefix(s: str) -> str:
    return re.sub(r"^中华人民共和国", "", s)


def rewrite_status_line(text: str, label: str):
    """重写 frontmatter 中的 status 行。返回 (new_text, old_label)；无 frontmatter/status 返回原文本"""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return text, None
    try:
        end = lines.index("---", 1)
    except ValueError:
        return text, None
    for i in range(1, end):
        if lines[i].startswith("status:"):
            old = lines[i]
            lines[i] = f'status: "{label}"'
            return "\n".join(lines), old
    return text, None


def fetch_all(client: FlkClient, code_ids, zdjg_ids=None):
    """遍历分类全部条目，返回 (rows, total)"""
    out, page, total = [], 1, None
    while True:
        rows, total = client.search_category(code_ids, page=page, zdjg_ids=zdjg_ids)
        if not rows:
            break
        out.extend(rows)
        if page * client.page_size >= total:
            break
        page += 1
    return out, total


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际执行（默认仅预览）")
    args = ap.parse_args()
    apply = args.apply

    output_root = DEFAULT_OUTPUT
    state_file = DEFAULT_STATE
    state = json.loads(state_file.read_text(encoding="utf-8"))
    old_laws = state.get("laws", {})

    client = FlkClient(delay=1.0)

    # ---- 1. 拉取官方全量列表（国家五大分类 + 沪苏浙） ----
    rows: dict = {}          # bbbs -> row
    official_totals: dict = {}  # 展示名 -> 官方 total
    plan = [(name, cfg["code"], None) for name, cfg in TOP_CATEGORIES.items()
            if not cfg.get("local")]
    plan += [(f"地方法规·{region}", cfg["code"], cfg["zdjg"])
             for region, cfg in LOCAL_REGIONS.items()]
    for name, code, zdjg in plan:
        fetched, total = fetch_all(client, code, zdjg)
        for r in fetched:
            if r.get("bbbs"):
                rows[r["bbbs"]] = r
        official_totals[name] = total
        print(f"  📄 {name}: 官方 {total} 条")

    # ---- 2. 重建 state：新指纹 + POSIX 路径 + 就地重写 status ----
    new_laws: dict = {}
    status_changes: Counter = Counter()
    rewritten = 0
    stale_state = []   # state 有、官方列表已无
    missing_state = [] # 官方有、state 无（下次同步自补）

    for bbbs, row in rows.items():
        rec = old_laws.get(bbbs)
        if rec is None:
            missing_state.append(row)
            continue
        path = rec["path"].replace("\\", "/")
        new_laws[bbbs] = {
            "hash": meta_hash(row),
            "path": path,
            "title": row.get("title", rec.get("title", "")),
            "synced": rec.get("synced", ""),
        }
        label = STATUS_MAP.get(row.get("sxx"))
        f = output_root / path
        if not (apply and label and f.exists()):
            continue
        text = f.read_text(encoding="utf-8")
        new_text, old_status = rewrite_status_line(text, label)
        if old_status is not None and old_status != f'status: "{label}"':
            f.write_text(new_text, encoding="utf-8")
            rewritten += 1
            status_changes[f"{old_status} -> status: \"{label}\""] += 1

    for bbbs, rec in old_laws.items():
        if bbbs not in rows:
            stale_state.append(bbbs)
            new_laws.setdefault(bbbs, dict(rec, path=rec["path"].replace("\\", "/")))

    # ---- 3. 孤儿文件：旧命名副本删除，无官方对应者列清单 ----
    disk = {p.relative_to(output_root).as_posix() for p in output_root.rglob("*.md")}
    new_paths = {rec["path"] for rec in new_laws.values()}
    orphans = sorted(disk - new_paths)

    by_core: dict = {}
    for p in new_paths:
        c = core_name(Path(p).name)
        by_core.setdefault(c, []).append(p)
        by_core.setdefault(strip_prefix(c), []).append(p)

    deleted, unmatched = [], []
    for op in orphans:
        oc = core_name(Path(op).name)
        twins = [p for c, ps in by_core.items() if c == oc for p in ps]
        if twins:
            deleted.append((op, twins[0]))
        else:
            unmatched.append(op)

    if apply:
        for op, twin in deleted:
            (output_root / op).unlink()
        # 清空目录顺带移除（如 民法典/ 整目录删除后）
        for d in sorted((p for p in output_root.rglob("*") if p.is_dir()),
                        key=lambda p: len(p.parts), reverse=True):
            try:
                d.rmdir()
            except OSError:
                pass
        state["laws"] = new_laws
        import datetime as _dt
        state["last_sync"] = _dt.datetime.now().isoformat()
        state_file.write_text(
            json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        cache = PROJECT_ROOT / ".cache"
        cache.mkdir(exist_ok=True)
        (cache / "orphan_unmatched_2026-10-03.txt").write_text(
            "\n".join(unmatched), encoding="utf-8"
        )
        (cache / "deleted_orphans_2026-10-03.txt").write_text(
            "\n".join(f"{op}  <=  {tw}" for op, tw in deleted), encoding="utf-8"
        )

    # ---- 4. 汇总 ----
    print(f"\n=== 迁移{'已执行' if apply else '预览'} ===")
    print(f"官方条目 {len(rows)} | state 重建 {len(new_laws)}"
          f"（官方有本地无 {len(missing_state)}，本地有官方无 {len(stale_state)}）")
    print(f"\nstatus 重写 {rewritten} 个文件：")
    for ch, n in status_changes.most_common():
        print(f"  {n:5d} × {ch}")
    print(f"\n孤儿文件 {len(orphans)}：删除旧命名副本 {len(deleted)}，无官方对应保留 {len(unmatched)}"
          + ("（清单: .cache/orphan_unmatched_2026-10-03.txt）" if unmatched else ""))
    print("\n各分类数量比对（本地 state vs 官方检索 total）:")
    for name, total in official_totals.items():
        if name.startswith("地方法规"):
            region = name.split("·")[1]
            local_n = sum(1 for rec in new_laws.values()
                          if rec["path"].startswith(f"地方法规/{region}/"))
        else:
            local_n = sum(1 for rec in new_laws.values()
                          if rec["path"].split("/")[0] == name)
        gap = total - local_n
        mark = "✅" if gap == 0 else f"⚠️ 差 {gap}"
        print(f"  {name:12s} 本地 {local_n:5d} | 官方 {total:5d} | {mark}")


if __name__ == "__main__":
    main()
