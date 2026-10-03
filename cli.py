#!/usr/bin/env python3
"""中国法律法规数据库 — CLI 命令行工具

数据源：国家法律法规数据库 (flk.npc.gov.cn) 直连

用法:
  python cli.py sync --full                # 全量同步（官方分类计数校验）
  python cli.py sync --incremental         # 增量同步（默认）
  python cli.py sync --dry-run             # 预览变更
  python cli.py sync --include-local       # 地方法规（仅沪苏浙，约 3100 部）
  python cli.py stats                      # 本地分类统计
  python cli.py check [--limit 20]         # 官方最新立法（新法速递）
  python cli.py verify                     # 本地 vs 官方分类数量比对
  python cli.py search <关键词>            # 全文搜索
"""

import argparse
import io
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Windows GBK 控制台下强制 UTF-8 输出，避免 emoji/中文崩溃
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# 确保项目根在 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

LAWS_OUT = PROJECT_ROOT / "laws"


# ====================================================================
# 子命令
# ====================================================================

def cmd_sync(args: argparse.Namespace) -> None:
    """直连 flk.npc.gov.cn 同步法律法规"""
    from src.flk import FlkClient
    from src.sync_flk import FlkSyncEngine, DEFAULT_CATEGORIES

    engine = FlkSyncEngine(dry_run=args.dry_run)
    if args.dry_run:
        print("🔍 模式: dry-run（预览变更，不写入）")
    else:
        print("🔄 模式: " + ("全量同步" if args.full else "增量同步"))

    if args.categories:
        cats = [c.strip() for c in args.categories.split(",")]
        if "地方法规" in cats and not args.include_local:
            args.include_local = True  # 显式点名地方法规即视为开启
    else:
        cats = DEFAULT_CATEGORIES

    print(f"📂 同步分类: {cats}"
          + ("（地方法规仅限沪苏浙）" if args.include_local else ""))

    results = engine.sync_all(
        categories=cats,
        include_local=args.include_local,
        force=args.full,
        limit=args.limit,
    )

    print("\n" + "=" * 40)
    for name, (updated, skipped, total) in results.items():
        print(f"  {name}: 更新 {updated} / 跳过 {skipped} / 官方 {total}")
    if not args.dry_run:
        print(f"\n✅ 同步完成 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print("💡 提交变更: git add laws/ .flk_sync_state.json && git commit")


def cmd_check(args: argparse.Namespace) -> None:
    """查看官方数据库最新立法"""
    from src.flk import FlkClient, national_latest_filtered

    client = FlkClient()
    print("🔍 正在查询国家法律法规数据库…")
    try:
        counts = client.get_category_counts()
        print("\n📊 官方数据库总量统计:")
        for name, cnt in counts.items():
            print(f"    {name}: {cnt} 部")
        print(f"    总计: {sum(counts.values())} 部")
    except Exception as e:
        print(f"  ⚠️ 获取统计失败: {e}")

    print(f"\n📋 国家层面最新立法（前 {args.limit} 条）:")
    try:
        latest = national_latest_filtered(client, limit=args.limit)
        for i, law in enumerate(latest, 1):
            title = law.get("title", "未知")
            date = law.get("gbrq", "?")
            law_type = law.get("flxz", "?")
            print(f"  {i:2d}. [{law_type}] {title}  公布: {date}")
    except Exception as e:
        print(f"  ⚠️ 获取最新立法失败: {e}")


def cmd_stats(args: argparse.Namespace) -> None:
    """显示本地法律法规统计"""
    from src.flk import FlkClient
    from src.sync_flk import FlkSyncEngine

    engine = FlkSyncEngine()
    stats = engine.get_stats()
    print(f"\n📊 本地法律法规统计")
    print(f"   总数: {stats['total']} 部\n")
    for cat, count in stats["categories"].items():
        bar = "█" * (count // 10)
        print(f"  {cat:20s}  {count:5d}  {bar}")

    if args.compare:
        # 复用 verify 的比对逻辑（官方检索 total，含历史版本，与本地口径一致；
        # 官方首页 aggregate 是"现行有效"口径，与本地全量直接相比必然"多出"）
        print("\n🔍 与官方数据库比对（检索 total 口径）:")
        try:
            for name, local, official in engine.verify_against_official():
                gap = official - local
                mark = "✅" if gap == 0 else (f"⚠️ 少 {gap} 部" if gap > 0 else f"ℹ️ 多 {-gap} 部")
                print(f"    {name:14s} 官方 {official:6d} | 本地 {local:6d} | {mark}")
        except Exception as e:
            print(f"    ⚠️ 官方比对失败: {e}")


def cmd_verify(args: argparse.Namespace) -> None:
    """本地 vs 官方 分类数量比对"""
    from src.flk import FlkClient
    from src.sync_flk import FlkSyncEngine

    engine = FlkSyncEngine()
    print("🔍 本地 laws/ 与官方分类数量比对:\n")
    for name, local, official in engine.verify_against_official():
        gap = official - local
        mark = "✅" if gap == 0 else (f"⚠️ 缺 {gap} 部" if gap > 0 else f"ℹ️ 多 {-gap} 部")
        print(f"  {name:10s} 官方 {official:6d} | 本地 {local:6d} | {mark}")


def cmd_search(args: argparse.Namespace) -> None:
    """全文搜索法律文件"""
    if not LAWS_OUT.exists():
        print("❌ laws/ 目录不存在，请先运行 sync")
        return

    keyword = args.keyword
    print(f"🔍 搜索: \"{keyword}\"\n")
    try:
        result = subprocess.run(
            ["grep", "-rli", keyword, str(LAWS_OUT)],
            capture_output=True, text=True, timeout=60,
        )
        lines = [l.strip() for l in result.stdout.strip().split("\n") if l]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        # 无 grep（如 Windows 非 Git Bash 环境）或超时：回退 Python 全文扫描
        print("  （grep 不可用，改用内置扫描）")
        lines = [
            str(p) for p in LAWS_OUT.rglob("*.md")
            if keyword in p.read_text(encoding="utf-8", errors="ignore")
        ]
    if not lines:
        print("  未找到匹配结果")
        return
    for line in lines[:args.limit]:
        p = Path(line)
        try:
            rel = p.relative_to(LAWS_OUT)
        except ValueError:
            rel = p
        print(f"  📄 {rel}")
    if len(lines) > args.limit:
        print(f"\n  ... 共 {len(lines)} 条结果，显示前 {args.limit} 条")


# ====================================================================
# 主入口
# ====================================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="🏛️  中国法律法规数据库 CLI（flk.npc.gov.cn 直连）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python cli.py sync --full           # 全量同步（首次使用）
  python cli.py sync --dry-run        # 预览将更新的文件
  python cli.py sync --categories 监察法规  # 只同步指定分类
  python cli.py check                 # 官方最新立法
  python cli.py stats --compare       # 本地 vs 官方
  python cli.py search "公司法"       # 全文搜索
        """,
    )
    sub = parser.add_subparsers(dest="command")

    # sync
    p = sub.add_parser("sync", help="同步法律法规（flk.npc.gov.cn 直连）")
    p.add_argument("--full", action="store_true", help="全量同步（忽略缓存，强制重新下载）")
    p.add_argument("--incremental", action="store_true", help="增量同步（默认行为）")
    p.add_argument("--dry-run", action="store_true", help="仅显示变更，不实际写入")
    p.add_argument("--include-local", action="store_true", help="包含地方法规（仅沪苏浙，约 3100 部）")
    p.add_argument("--categories", type=str, help="逗号分隔的分类，如: 监察法规,宪法,地方法规")
    p.add_argument("--limit", type=int, help="每分类最多同步 N 部（调试用）")
    p.set_defaults(func=cmd_sync)

    # check
    p = sub.add_parser("check", help="查看官方数据库最新立法")
    p.add_argument("--limit", "-n", type=int, default=30, help="显示条数（默认 30）")
    p.set_defaults(func=cmd_check)

    # stats
    p = sub.add_parser("stats", help="显示本地法律法规分类统计")
    p.add_argument("--compare", action="store_true", help="同时与官方数量比对")
    p.set_defaults(func=cmd_stats)

    # verify
    p = sub.add_parser("verify", help="本地与官方分类数量比对")
    p.set_defaults(func=cmd_verify)

    # search
    p = sub.add_parser("search", help="全文搜索法律法规")
    p.add_argument("keyword", help="搜索关键词")
    p.add_argument("--limit", "-n", type=int, default=50, help="显示条数（默认 50）")
    p.set_defaults(func=cmd_search)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)
    args.func(args)


if __name__ == "__main__":
    main()
