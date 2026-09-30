#!/bin/bash
# 条件提交：仅当 laws/ 或 .flk_sync_state.json 出现真实变更时才 commit+push
# 单一归属：weekly_sync.yml 与 weekly_sync.sh 均调用本脚本，勿在两处复制这段逻辑
set -e

if git status --porcelain -- laws/ .flk_sync_state.json | grep -q .; then
    NEW_COUNT=$(git -c core.quotepath=false status --porcelain -- laws/ | grep -c '^??' || true)
    git config user.name "china-law-db bot"
    git config user.email "actions@users.noreply.github.com"
    git add laws/
    # 状态文件首次提交前可能尚不存在，按存在性守卫添加，避免 pathspec 报错中断
    if [ -f .flk_sync_state.json ]; then git add .flk_sync_state.json; fi
    git commit -m "weekly sync from flk.npc.gov.cn $(date -u +'%Y-%m-%d')：新增 ${NEW_COUNT} 部法律法规"
    if git remote | grep -q origin; then
        git push origin HEAD
    else
        echo "（无 origin 远端，跳过 push）"
    fi
    echo "已提交：新增 ${NEW_COUNT} 部法律法规"
else
    echo "No changes since last sync — nothing to commit."
fi
