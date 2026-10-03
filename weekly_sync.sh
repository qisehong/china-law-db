#!/bin/bash
# ============================================================
# 中国法律法规数据库 — 周度同步脚本（本地手动入口）
# 数据源: 国家法律法规数据库 (flk.npc.gov.cn) 直连
# ============================================================
# 定时自动化首选 GitHub Actions（.github/workflows/weekly_sync.yml，
# 每周日北京 22:00）；本脚本用于本地手动补跑或无 CI 环境。
# 用法（crontab 本地方案，可选）:
#   0 6 * * 1 /path/to/china-law-db/weekly_sync.sh >> /path/to/china-law-db/sync.log 2>&1
# ============================================================

set -e

# 脚本所在目录（不再硬编码路径）
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="$REPO_DIR/sync.log"

# Python 解释器：Linux cron 通常是 python3，Windows Git Bash 通常只有 python
PY="$(command -v python3 || command -v python)"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

cd "$REPO_DIR"

log "=========================================="
log "开始周度法律法规定期同步（flk 直连）"

# 0. 拉取本仓库远端更新（多机部署时保持一致；单人使用可注释）
if git remote | grep -q origin; then
    log "0/3 拉取本仓库远端更新..."
    git pull --ff-only origin "$(git rev-parse --abbrev-ref HEAD)" 2>&1 | while read line; do log "  $line"; done || log "  ⚠️ pull 失败（跳过）"
fi

# 1. 增量同步官方数据库（五大国家层面分类 + 地方法规仅沪苏浙）
# 与 .github/workflows/weekly_sync.yml 调用同一条命令，避免双份逻辑漂移
log "1/3 直连 flk.npc.gov.cn 增量同步..."
"$PY" cli.py sync --include-local 2>&1 | while read line; do log "  $line"; done

# 2. 官方最新立法速报（写入日志便于审计）
log "2/3 检查官方最新立法..."
"$PY" cli.py check --limit 10 2>&1 | while read line; do log "  $line"; done

# 3. 条件提交（逻辑单一归属 scripts/commit_if_changed.sh，与 workflow 共用）
log "3/3 提交变更..."
bash scripts/commit_if_changed.sh 2>&1 | while read line; do log "  $line"; done

log "同步完成"
log "=========================================="
