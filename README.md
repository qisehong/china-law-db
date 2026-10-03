# 🇨🇳 中国法律法规数据库

> 直连国家法律法规数据库 ([flk.npc.gov.cn](https://flk.npc.gov.cn))，同步官方最新法律法规，
> 面向程序员和法律从业者的结构化中文法律知识库。
> Markdown + YAML 格式，版本可追溯。

## 项目统计

| 指标 | 数值 |
|------|------|
| **数据源** | 国家法律法规数据库 (flk.npc.gov.cn) 官方 API 直连 |
| **默认覆盖** | 宪法 → 法律 → 行政法规 → 监察法规 → 司法解释 |
| **可选覆盖** | 地方法规（上海/江苏/浙江，3,084 部，`--include-local` 开启） |
| **格式** | Markdown + YAML frontmatter |
| **更新方式** | 官方 API 增量同步，每周定时 |

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 全量同步（首次使用）
python cli.py sync --full

# 增量同步（日常更新）
python cli.py sync

# 预览变更（不写入）
python cli.py sync --dry-run

# 只同步指定分类
python cli.py sync --categories 监察法规,宪法

# 地方法规（仅沪苏浙，3,084 部，配置见「地方法规」章节）
python cli.py sync --include-local

# 查看统计并与官方比对
python cli.py stats --compare

# 查看官方最新立法
python cli.py check

# 全文搜索
python cli.py search "公司法"
```

## 架构

```
flk.npc.gov.cn (国家法律法规数据库)
        │  POST /law-search/search/list     分类分页检索
        │  POST /law-search/download/batch  官方批量下载 → OSS 预签名 URL
        ▼
  docx 下载 + 解析 (src/flk.py → src/docx2md.py)
        │  python-docx 提取条文 → Markdown
        ▼
  YAML frontmatter + Markdown
        │
        ▼
  分类存储 laws/  ←  增量状态 .flk_sync_state.json
```

### 分类体系（官方 codeId）

| 分类 | codeId | 同步策略 |
|------|--------|---------|
| 宪法 | 100 | 默认同步 |
| 法律（含 8 个子类） | 101 | 默认同步 |
| 行政法规 | 201 | 默认同步 |
| 监察法规 | 220 | 默认同步 |
| 司法解释 | 311 | 默认同步 |
| 地方法规 | 221 | `--include-local` 开启，按省过滤（详见下节） |

## 地方法规（上海 / 江苏 / 浙江）

官方地方法规全量超过 1.5 万部，本仓库仅保留**上海、江苏、浙江**三个省市。
过滤在服务端完成：检索时传入官方“制定机关”分类码（`zdjgCodeId`），只拉取三省人大及其常委会（含设区的市人大）制定的法规，其余省份的数据完全不会下载。

### 目录结构

地方法规按省存储；普通地方性法规直接放在省目录下，特殊子类（浦东新区法规、自治条例等）再分子目录：

```
laws/地方法规/
├── 上海/                        529 部
│   ├── *.md                     地方性法规 443 部
│   ├── 浦东新区法规/             30 部
│   ├── 法规性决定/               18 部
│   └── 修改、废止的决定/          38 部
├── 江苏/                      1,511 部
│   ├── *.md                     地方性法规 1,354 部
│   ├── 法规性决定/               44 部
│   └── 修改、废止的决定/         113 部
└── 浙江/                      1,044 部
    ├── *.md                     地方性法规 967 部
    ├── 自治条例/                  3 部
    ├── 单行条例/                  7 部
    ├── 法规性决定/               12 部
    └── 修改、废止的决定/          55 部
```

### 省份配置

同步省份在 [src/flk.py](src/flk.py) 的 `LOCAL_REGIONS` 字典中配置，每个省一项：

```python
LOCAL_REGIONS: Dict[str, dict] = {
    "上海": {"zdjg": [250], "code": [221, 222, 230, 260, 270, 290, 295, 300, 305, 310]},
    "江苏": {"zdjg": [260], "code": [221, 222, 230, 260, 270, 290, 295, 300, 305, 310]},
    "浙江": {"zdjg": [270], "code": [221, 222, 230, 260, 270, 290, 295, 300, 305, 310]},
}
```

- `zdjg`：该省的制定机关分类码（`code` 为地方法规分类树，各省通用，无需修改）
- 增加新省份时，先从官方制定机关树（`/law-search/search/enumData` 返回的 `zdjgfl` 字段）查对应码值：

```python
from src.flk import FlkClient
tree = FlkClient()._request("GET", "/law-search/search/enumData")
# 在 tree['data']['zdjgfl'] 的「地方人大及其常委会」子树中查找省份 codeId
```

修改后运行 `python cli.py sync --include-local` 即可全量拉取新增省份。

### 数据概览（与官方实时比对可用 `python cli.py verify`）

| 省份 | 制定机关码 | 官方数量 | 本地数量 | 备注 |
|------|-----------|---------|---------|------|
| 上海 | 250 | 530 | 529 | ✅ 全覆盖（官方 530 条中 2 条为同一决定的重复记录，对应同一文件；重复条目官方 docx 源 404，已登记增量状态停止重试） |
| 江苏 | 260 | 1,511 | 1,511 | ✅ 与官方一致 |
| 浙江 | 270 | 1,044 | 1,044 | ✅ 与官方一致 |

地方法规的 frontmatter 中 `subcategory` 带省份信息（特殊子类为 `<省>/<子类>`），`issuing_authority` 为官方制定机关名称：

```markdown
---
title: "上海市促进浦东新区运用区块链赋能电子单证应用若干规定"
publish_date: "2026-05-27"
status: "现行有效"
category: "地方法规"
subcategory: "上海/浦东新区法规"
issuing_authority: "上海市人民代表大会常务委员会"
source: "国家法律法规数据库 (flk.npc.gov.cn)"
synced: "2026-09-30"
tags:
  - 法律法规
  - 地方法规
  - 上海
---
```

## 数据格式

每部法律文件包含 YAML frontmatter 元数据 + Markdown 正文：

```markdown
---
title: "中华人民共和国刑法"
publish_date: "2020-12-26"
implement_date: "2021-03-01"
status: "现行有效"
category: "法律"
subcategory: "刑法"
issuing_authority: "全国人民代表大会常务委员会"
source: "国家法律法规数据库 (flk.npc.gov.cn)"
synced: "2026-09-30"
tags:
  - 法律法规
  - 法律
  - 刑法
---
# 中华人民共和国刑法

第一编　总则

### 第一章　刑法的任务、基本原则和适用范围

第一条　为了惩罚犯罪，保护人民，……
```

## 自动化更新

### GitHub Actions（推荐）

仓库自带定时工作流 [.github/workflows/weekly_sync.yml](.github/workflows/weekly_sync.yml)：
**每周日北京时间 22:00**（UTC 周日 14:00）自动同步官方数据库，发现新法时自动 commit + push。

- commit message 带日期与新增部数，如 `weekly sync from flk.npc.gov.cn 2026-09-27：新增 3 部法律法规`
- 无新法时不产生空提交（同步状态文件无变化则跳过提交）
- 可在 Actions 页面手动触发（`workflow_dispatch`）用于首次验证或补跑
- 需要在仓库 Settings → Actions → General → Workflow permissions 中允许 Read and write，或保持默认的 `permissions: contents: write` 声明
- GitHub 定时调度不保证准点，高峰期可能有数分钟到半小时延迟
- 个别上游数据问题（1 部上海决定在官方库存在重复记录，重复条目的 docx 源 404）不会使工作流标红或阻断其他新法提交；两条记录均已登记增量状态，同步不再触发下载，仅日志可能留有历史失败记录

### 本地手动补跑

```bash
./weekly_sync.sh
```

`weekly_sync.sh` 与 Actions 工作流调用**同一条同步命令**、使用**相同的变更判据**，仅多出立法动态日志；也可自行配 crontab（每周一早 6:00）：

```bash
0 6 * * 1 /path/to/china-law-db/weekly_sync.sh >> /path/to/china-law-db/sync.log 2>&1
```

## 增量机制

`.flk_sync_state.json` 记录每部法律的版本指纹（标题 | 公布日期 | 时效性）。
每次同步先拉取官方全量列表（每分类一次分页遍历），指纹未变的条目直接跳过，
仅对新增或变更的条目调用官方下载接口，因此日常增量同步通常只需数分钟。

该状态文件**随仓库提交**（勿加入 .gitignore），CI 环境依靠它实现真正的增量同步；
同步代码在无新法时不会重写该文件，保证 GitHub Actions 的条件提交不产生空提交。

## 许可证

- 法律法规文本属于公有领域（全国人大常委会办公厅公开数据）
- 工具脚本 MIT 许可
