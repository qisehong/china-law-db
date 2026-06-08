#!/bin/bash
# ============================================================
# 中国法律法规数据库 — 周度同步脚本
# ============================================================
# 用法: 配置 crontab 每周执行
#   0 6 * * 1 /home/daniel/Documents/china-law-db/weekly_sync.sh >> /home/daniel/Documents/china-law-db/sync.log 2>&1
# ============================================================

set -e

REPO_DIR="/home/daniel/Documents/china-law-db"
LOG_FILE="$REPO_DIR/sync.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

cd "$REPO_DIR"

log "=========================================="
log "开始周度法律法规定期同步"

# 1. 拉取上游 LawRefBook 最新数据
log "1/4 拉取 LawRefBook 上游更新..."
git pull origin master 2>&1 | while read line; do log "  $line"; done

# 2. 增量同步（检测变更并转换）
log "2/4 增量同步法律法规..."
python3 cli.py sync 2>&1 | while read line; do log "  $line"; done

# 3. 检查 NPC 官方数据库是否有新法
log "3/4 检查 NPC 官方最新立法..."
python3 cli.py check --limit 5 2>&1 | while read line; do log "  $line"; done

# 4. 提交变更
log "4/4 提交变更..."
if git diff --quiet && git diff --cached --quiet && [ -z "$(git ls-files --others --exclude-standard)" ]; then
    log "  无变更，跳过提交"
else
    git add laws/ .sync_state.json
    git commit -m "weekly sync $(date '+%Y-%m-%d')" 2>&1 | while read line; do log "  $line"; done
    git push 2>&1 | while read line; do log "  $line"; done
    log "  提交并推送完成"
fi

log "同步完成"
log "=========================================="
