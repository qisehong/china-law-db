#!/usr/bin/env python3
"""中国法律法规数据库 — CLI 命令行工具

用法:
  python cli.py sync --full            # 首次全量同步
  python cli.py sync --incremental     # 增量同步（默认）
  python cli.py sync --dry-run         # 预览变更
  python cli.py check [--limit 20]     # 查看 NPC 最新立法
  python cli.py stats                  # 分类统计
  python cli.py search <关键词>        # 全文搜索
"""

import argparse
import subprocess
import sys
from pathlib import Path

# 确保项目根在 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

# 配置路径
LAWS_SRC = PROJECT_ROOT  # LawRefBook 源文件（即本仓库根目录）
LAWS_OUT = PROJECT_ROOT / "laws"
STATE_FILE = PROJECT_ROOT / ".sync_state.json"


# ====================================================================
# 子命令
# ====================================================================

def cmd_sync(args: argparse.Namespace) -> None:
    """同步法律法规：从源仓库转换到输出目录"""
    from src.sync import SyncEngine

    engine = SyncEngine(LAWS_SRC, LAWS_OUT, STATE_FILE, dry_run=args.dry_run)

    if args.full:
        print("🔄 模式: 全量同步")
        synced, removed, total = engine.sync_full()
    else:
        print("🔄 模式: 增量同步")
        synced, removed, total = engine.sync_incremental()

    if not args.dry_run:
        print(f"✅ 同步完成: {synced} 更新, {removed} 移除, {total} 总计")


def cmd_check(args: argparse.Namespace) -> None:
    """检查 NPC 官方数据库最新立法"""
    from src.client import NPCClient, STATUS_MAP

    client = NPCClient()

    print("🔍 正在查询国家法律法规数据库...")
    try:
        counts = client.get_category_counts()
        print(f"\n📊 官方数据库总量统计:")
        for name, cnt in counts.items():
            print(f"    {name}: {cnt} 部")
        print(f"    总计: {sum(counts.values())} 部")
    except Exception as e:
        print(f"  ⚠️ 获取统计失败: {e}")

    print(f"\n📋 最新立法（前 {args.limit} 条）:")
    try:
        latest = client.get_latest_laws(limit=args.limit)
        for i, law in enumerate(latest, 1):
            title = law.get("title", "未知")
            date = law.get("gbrq", "?")
            law_type = law.get("flxz", "?")
            law_id = law.get("bbbs", "")
            print(f"  {i:2d}. [{law_type}] {title}")
            print(f"      公布: {date}  |  ID: {law_id}")
    except Exception as e:
        print(f"  ⚠️ 获取最新立法失败: {e}")


def cmd_stats(args: argparse.Namespace) -> None:
    """显示本地法律法规统计"""
    from src.sync import SyncEngine

    engine = SyncEngine(LAWS_SRC, LAWS_OUT, STATE_FILE)
    stats = engine.get_stats()

    print(f"\n📊 本地法律法规统计")
    print(f"   总数: {stats['total']} 部\n")

    for cat, count in stats["categories"].items():
        bar = "█" * (count // 10)
        print(f"  {cat:20s}  {count:5d}  {bar}")


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
            capture_output=True,
            text=True,
            timeout=30,
        )
        lines = [l.strip() for l in result.stdout.strip().split("\n") if l]

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

    except subprocess.TimeoutExpired:
        print("  ⚠️ 搜索超时")


def cmd_verify(args: argparse.Namespace) -> None:
    """用 NPC 官方 API 验证本地法律状态"""
    from src.client import NPCClient, STATUS_MAP
    from src.sync import SyncEngine

    engine = SyncEngine(LAWS_SRC, LAWS_OUT, STATE_FILE)
    client = NPCClient()

    print("🔍 正在获取 NPC 最新立法清单...")
    try:
        latest = client.get_latest_laws(limit=args.limit)
    except Exception as e:
        print(f"❌ 获取失败: {e}")
        return

    local_files = set()
    for p in LAWS_OUT.rglob("*.md"):
        local_files.add(p.stem)

    print(f"\n📋 NPC 最新立法 vs 本地库比对:\n")
    for law in latest:
        title = law.get("title", "")
        date = law.get("gbrq", "?")
        status_code = law.get("sxx", "?")
        status = STATUS_MAP.get(status_code, f"未知({status_code})")

        # 检查本地是否有
        found = any(title in f for f in local_files)
        marker = "✅" if found else "⚠️ 缺失"

        print(f"  {marker} [{status}] {title} ({date})")


# ====================================================================
# 主入口
# ====================================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="🏛️  中国法律法规数据库 CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python cli.py sync --full           # 首次全量同步
  python cli.py sync --dry-run        # 预览即将变更的文件
  python cli.py check                 # 查看 NPC 最新 30 部立法
  python cli.py stats                 # 查看本地分类统计
  python cli.py search "公司法"       # 搜索包含关键词的法律
  python cli.py verify --limit 10     # 比对前 10 部最新法律
        """,
    )
    sub = parser.add_subparsers(dest="command")

    # sync
    p = sub.add_parser("sync", help="同步法律法规（从源仓库转换到输出目录）")
    p.add_argument("--full", action="store_true", help="全量同步（清除缓存重新处理）")
    p.add_argument("--dry-run", action="store_true", help="仅显示变更，不实际写入")
    p.set_defaults(func=cmd_sync)

    # check
    p = sub.add_parser("check", help="查看 NPC 官方数据库最新立法")
    p.add_argument("--limit", "-n", type=int, default=30, help="显示条数（默认 30）")
    p.set_defaults(func=cmd_check)

    # stats
    p = sub.add_parser("stats", help="显示本地法律法规分类统计")
    p.set_defaults(func=cmd_stats)

    # search
    p = sub.add_parser("search", help="全文搜索法律法规")
    p.add_argument("keyword", help="搜索关键词")
    p.add_argument("--limit", "-n", type=int, default=50, help="显示条数（默认 50）")
    p.set_defaults(func=cmd_search)

    # verify
    p = sub.add_parser("verify", help="用 NPC 官方 API 验证本地法律是否有缺失")
    p.add_argument("--limit", "-n", type=int, default=30, help="检查条数（默认 30）")
    p.set_defaults(func=cmd_verify)

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
