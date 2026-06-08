# 🇨🇳 中国法律法规数据库

> 基于国家法律法规数据库 (flk.npc.gov.cn)，面向程序员和法律从业者的结构化中国法律法规知识库。
> 每周自动更新，Markdown + YAML 格式，版本可追溯。

## 项目统计

| 指标 | 数值 |
|------|------|
| **法律法规总数** | 1,539 部 |
| **数据源** | 国家法律法规数据库 → LawRefBook/Laws |
| **覆盖范围** | 宪法 → 法律 → 行政法规 → 司法解释 → 部门规章 |
| **格式** | Markdown + YAML frontmatter |
| **更新频率** | 每周一自动同步 |

## 分类明细

| 分类 | 数量 |
|------|------|
| 行政法规 | 710 |
| 司法解释 | 439 |
| 法律（含7个子类）| 351 |
| 部门规章 | 29 |
| 民法典（8编）| 8 |
| 其他 | 2 |

## 快速开始

```bash
# 克隆仓库
git clone <this-repo-url>
cd china-law-db

# 安装依赖
pip install -r requirements.txt

# 全量同步（首次使用）
python cli.py sync --full

# 增量同步（周度更新）
python cli.py sync --incremental

# 查看统计
python cli.py stats

# 搜索法律
python cli.py search "公司法"

# 检查 NPC 官方最新立法
python cli.py check

# 对比本地与官方数据库
python cli.py verify
```

## 数据格式

每部法律文件包含 YAML frontmatter 元数据 + Markdown 正文：

```markdown
---
title: "中华人民共和国刑法"
publish_date: "1979-07-01"
status: "现行有效"
category: "刑法"
source: "国家法律法规数据库"
synced: "2026-06-08"
tags:
  - 法律法规
  - 刑法
---
# 中华人民共和国刑法

1979年7月1日 第五届全国人民代表大会第二次会议通过
...
```

## 架构

```
LawRefBook/Laws (GitHub)   ←── 爬取自 国家法律法规数据库
        │
        ▼  git pull (每周)
  格式转换 (convert.py)
        │
        ▼  YAML frontmatter + Markdown
  分类存储 (laws/)
        │
        ▼  git commit + push
 china-law-db 仓库
```

## 周度更新

```bash
# crontab 定时任务（每周一早 6:00）
0 6 * * 1 cd /path/to/china-law-db && \
  git pull origin master && \
  python cli.py sync && \
  git add laws/ .sync_state.json && \
  git commit -m "weekly sync $(date +\%Y-\%m-\%d)" && \
  git push
```

## 许可证

- 法律法规文本属于公有领域
- 工具脚本 MIT 许可
- 数据源: [LawRefBook/Laws](https://github.com/LawRefBook/Laws) (国家法律法规数据库爬取)
