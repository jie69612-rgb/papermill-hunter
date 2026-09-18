# PaperMill Hunter · 论文工厂猎手

> 用公开学术元数据侦测"论文工厂"的统计指纹，并量化撤稿的真实代价。
>
> 一个「采集 → 数仓 → 分析 → 可视化」全链路的数据分析项目。

<p align="left">
  <img alt="python" src="https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white">
  <img alt="duckdb" src="https://img.shields.io/badge/DuckDB-OLAP-FFF000?logo=duckdb&logoColor=black">
  <img alt="tests" src="https://img.shields.io/badge/tests-pytest-0A9EDC?logo=pytest&logoColor=white">
  <img alt="license" src="https://img.shields.io/badge/license-MIT-green">
</p>

---

## 一、这个项目在解决什么问题

学术出版已经产业化，造假也随之**工业化**。

过去，一篇假论文背后是一个走投无路的研究者；现在，它背后可能是一条流水线：
同一天投稿十几篇结构雷同的论文，标题只是换了几个词，作者互相挂名，
引用彼此堆叠，专挑审稿周期极短的期刊特刊下手。
学术界把这种流水线叫作 **论文工厂（Paper Mill）**。

问题在于：**这些论文从表面上看完全正常**。它们有 DOI、有正规期刊、有完整的参考文献。
等你发现时，污染已经进入综述、进入 meta 分析、进入临床指南。

本项目尝试回答三个问题：

| # | 问题 | 方法 | 产出 |
|---|------|------|------|
| 1 | 全球撤稿的规模、结构和演变是什么样的？ | 描述性统计、时间序列分解 | 撤稿趋势、原因图谱、撤稿时滞 |
| 2 | 能否在"被撤稿之前"识别出论文工厂的指纹？ | 无监督异常检测 + 文本相似度 + 图分析 | 期刊/作者风险评分榜 |
| 3 | 造假被揭穿后，代价究竟有多大？ | 事件研究法（Event Study）+ 双重差分（DiD） | 撤稿的引用惩罚因果估计 |

> **为什么这个选题值得写进简历**
>
> 表面看它属于"学术圈的事"，但内核是**黑产团伙识别**：
> 产量突增、模板化生产、抱团互挂、闭环刷量——
> 这套指纹与电商刷单、虚假评论、金融欺诈是**同一套方法论**。
> 换句话说：我在一个数据干净、有标准答案的领域里，
> 演练了一套可以直接迁移到风控业务的技术栈。

---

## 二、数据来源

全部使用**公开、免密钥、允许自动化访问**的数据源，不涉及任何爬虫对抗。

| 数据源 | 内容 | 用途 | 获取方式 |
|--------|------|------|----------|
| [Retraction Watch DB](https://www.crossref.org/documentation/retrieve-metadata/retraction-watch/) | 全球撤稿案底 **72,602 条**：撤稿原因、日期、机构、期刊、出版商 | 现况刻画、原因分类、撤稿时滞 | [官方 GitLab 仓库](https://gitlab.com/crossref/retraction-watch-data)（`git clone`，每工作日更新） |
| [OpenAlex](https://docs.openalex.org/) | 2.5 亿+ 学术作品的完整图谱：作者、机构、国家、主题、**逐年引用轨迹** | 论文工厂指纹侦测、因果推断 | REST API（cursor 分页） |
| [Crossref](https://api.crossref.org/) | 撤稿声明的关系链（`update-to`）、期刊元数据 | 交叉验证、撤稿标注缺口分析 | REST API |

**一个从数据里冒出来的问题**：OpenAlex 标记了约 13.5 万篇被撤稿作品，
而 Retraction Watch 记录了约 7.3 万条撤稿事件。这个**缺口本身就是一个可分析的对象**——
有多少撤稿从未被正式标注？这对依赖文献的后续研究意味着什么？

> 数据源之间的不一致，往往藏着最有价值的发现。

### 关于数据源选择的一次真实踩坑

最初我们调用的是 Crossref Labs 的 HTTP 端点 `api.labs.crossref.org/data/retractionwatch`。
它的表现很差：平均速率约 50 KB/s，下载 63 MB 要几个小时，且返回
`Transfer-Encoding: chunked`、**无 Content-Length**、**不支持 Range 断点续传**，
中断后只能从零重下。

查阅官方文档才发现根因：**该端点已被 Crossref 官方弃用**
（2026 年 5 月公告，不再推送新数据，建议迁移到 GitLab）。
改用 `git clone` 后，**15 秒完成**，快约 1000 倍，还天然具备增量更新与版本追溯。

> 教训：遇到又慢又难用的接口时，先花五分钟查官方文档。
> 很可能不是你的代码有问题，而是这个接口正在被淘汰。

---

## 三、技术架构

```
                  ┌─────────────────────────────────────────────┐
                  │              ingest  采集层                  │
                  │  限流(令牌桶) · 重试(指数退避) · 磁盘缓存      │
                  └───────────────────┬─────────────────────────┘
                                      │  原始快照 (Parquet/CSV)
                                      ▼
                  ┌─────────────────────────────────────────────┐
                  │            warehouse  数仓层                 │
                  │   DuckDB · 分层建模 staging → intermediate → marts │
                  └───────────────────┬─────────────────────────┘
                                      │  分析就绪宽表
                                      ▼
                  ┌─────────────────────────────────────────────┐
                  │             analysis  分析层                 │
                  │  描述统计 · 异常检测 · 文本指纹 · 图分析 · 因果推断 │
                  └───────────────────┬─────────────────────────┘
                                      │  指标与结论
                                      ▼
                  ┌─────────────────────────────────────────────┐
                  │          visualization  可视化层             │
                  │      Streamlit 交互看板 · 静态报告图表        │
                  └─────────────────────────────────────────────┘
```

### 目录结构

```
papermill-hunter/
├── src/papermill_hunter/
│   ├── config.py              # 配置中心：一处定义，全局引用
│   ├── logging_conf.py        # 统一日志
│   ├── ingest/                # 采集层：只负责"取回来 + 存下来"
│   │   ├── http.py            #   带限流/重试/缓存的 HTTP 客户端
│   │   ├── retraction_watch.py
│   │   └── openalex.py
│   ├── warehouse/             # 数仓层：清洗与建模（SQL 分层）
│   │   └── sql/{staging,intermediate,marts}/
│   ├── analysis/              # 分析层：业务逻辑与统计方法
│   └── viz/                   # 可视化层
├── scripts/                   # 按序号排列的可执行入口
├── tests/                     # pytest 测试
├── data/                      # 数据落盘（不进 Git，可一键重跑）
└── reports/                   # 分析报告与图表
```

**分层原则**：`ingest` 只做"取回来 + 存下来"，不做业务清洗；清洗与建模交给 `warehouse`。
职责单一，出问题时能立刻定位是"数据没取到"还是"清洗逻辑写错了"。

---

## 四、快速开始

```bash
# 1. 克隆
git clone https://github.com/jie69612-rgb/papermill-hunter.git
cd papermill-hunter

# 2. 创建虚拟环境
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux

# 3. 安装依赖
pip install -r requirements.txt
pip install -e .

# 4. 配置联系邮箱（进入 OpenAlex/Crossref 礼貌池，限流更宽松）
copy .env.example .env          # Windows
# cp .env.example .env          # macOS / Linux
# 然后编辑 .env，填入你的邮箱

# 5. 跑起来
python scripts/01_fetch_retraction_watch.py
```

### 跑测试

```bash
pytest
```

---

## 五、项目状态

- [x] 项目骨架与配置中心
- [x] 采集层：限流 / 重试 / 磁盘缓存（含 14 项单元测试）
- [ ] 采集层：OpenAlex 全量撤稿作品抓取
- [ ] 数仓层：DuckDB 分层建模
- [ ] 分析一：撤稿现况刻画
- [ ] 分析二：论文工厂异常侦测
- [ ] 分析三：撤稿引用代价的因果推断
- [ ] 可视化看板
- [ ] GitHub Actions 每日增量更新

---

## 六、许可

MIT License
