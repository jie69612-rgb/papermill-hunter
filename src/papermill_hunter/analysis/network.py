"""合作网络分析：社群发现与"挂名团伙"识别。

【本模块要解决的问题】
    前几个分析都是**横截面**的：看期刊、看国家、看单个作者。
    但论文工厂的本质是**关系**，不是个体 ——
    它需要一批人反复互相挂名，才能把论文"凑够作者"并让每篇都看起来可信。
    这种结构只有在**图**上才看得见。

    打个比方：
      - 看单个作者：每个人都只发了 5 篇，毫不起眼
      - 看合作网络：这 20 个人彼此之间两两都合作过，构成一个近乎完全的图 ——
        而真实科研团队是树状的（导师带学生），不会是"人人都和人人合作"的团块

    **这就是图算法的价值：它能把"个体层面完全正常"的数据，
      在关系层面暴露出异常结构。**

【方法】
    1. 用作者 ID 作为节点（不用姓名 —— 姓名有重名和拼写变体，ID 是消歧过的）
    2. 边的权重 = 两人共同署名的被撤稿论文数
    3. 用 **Louvain 算法**做社群发现：它通过最大化模块度（modularity）
       把网络切成"内部连接紧密、外部连接稀疏"的社群。
       选它的理由：无需预设社群数量、在百万级边上依然很快、对权重天然支持。
    4. 给每个社群算特征，再用一个可解释的复合分排序
    5. **用官方 Paper Mill 标签验证这个分数** —— 和期刊评分一样的流程

【必须说清的局限】
    1. 图上只有**被撤稿**论文的合作关系。这意味着我们看不到这些人的正常产出，
       无法区分"这是个专做假论文的团伙"和"这是个正常团队但有几篇被撤"。
    2. 社群发现是**无监督**的，算法给出的社群不等于"犯罪团伙"。
       高密度社群同样可能是一个高产的正规课题组。
    3. 因此本模块的输出是**筛查线索（screening lead）**，不是结论。
       任何针对具体个人的指控都必须经过人工核查 —— 这是学术诚信领域的基本伦理。
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb
import networkx as nx
import pandas as pd

from papermill_hunter.config import Settings, get_settings
from papermill_hunter.logging_conf import get_logger

logger = get_logger(__name__)

# 只保留至少合作过 N 篇的边。
# 为什么？因为"合作 1 篇"是极弱的关系 —— 一次会议论文、一次大合作项目
# 都会产生大量这种边。它们会把网络连成一大团，
# 让社群发现失去分辨力（就像用"同城"当好友关系去分析社交圈）。
# 实测：106 万条边中，合作 ≥2 篇的只有 27.6 万条（26%），
# 剔除后网络结构立刻清晰起来。
MIN_SHARED_WORKS = 2

# Louvain 的随机种子。必须固定 ——
# 否则同一份数据跑两次会得到不同的社群划分，
# 结论无法复现，这在分析项目里是不可接受的。
RANDOM_SEED = 42


@dataclass
class NetworkAnalysisResult:
    """分析结果摘要，便于在脚本里打印和写报告。"""

    n_nodes: int
    n_edges: int
    n_communities: int
    n_clusters_scored: int
    top_cluster_id: int | None
    aucs: dict[str, float]


# ----------------------------------------------------------------------
# 数据加载
# ----------------------------------------------------------------------
def load_edges(con: duckdb.DuckDBPyConnection, min_shared_works: int = MIN_SHARED_WORKS) -> pd.DataFrame:
    """从数仓读出合作网络边表。"""
    logger.info("读取合作网络边表（筛选合作 ≥ %d 篇）……", min_shared_works)
    df = con.execute(
        """
        SELECT
            author_a,
            author_b,
            n_shared_works,
            n_shared_paper_mill,
            shared_paper_mill_share,
            n_journals,
            n_fields,
            first_year,
            last_year
        FROM intermediate.coauthorship_edges
        WHERE n_shared_works >= ?
        """,
        [min_shared_works],
    ).df()
    logger.info(
        "载入 %s 条边，涉及 %s 位作者", f"{len(df):,}", f"{df[['author_a', 'author_b']].stack().nunique():,}"
    )
    return df


def build_graph(edges: pd.DataFrame) -> nx.Graph:
    """把边表构造成 networkx 无向加权图。

    节点属性里带上"该作者参与的论文工厂论文数" ——
    这样后面做社群画像时不必再回查数据库。
    """
    graph = nx.Graph()

    for row in edges.itertuples(index=False):
        graph.add_edge(
            row.author_a,
            row.author_b,
            weight=int(row.n_shared_works),
            paper_mill=int(row.n_shared_paper_mill),
            journals=int(row.n_journals or 0),
            fields=int(row.n_fields or 0),
            first_year=row.first_year,
            last_year=row.last_year,
        )

    logger.info(
        "构图完成：%s 个节点，%s 条边，%s 个连通分量",
        f"{graph.number_of_nodes():,}",
        f"{graph.number_of_edges():,}",
        f"{nx.number_connected_components(graph):,}",
    )
    return graph


# ----------------------------------------------------------------------
# 社群发现
# ----------------------------------------------------------------------
def detect_communities(graph: nx.Graph) -> dict[str, int]:
    """用 Louvain 算法做社群发现，返回 {作者ID: 社群编号}。"""
    logger.info("运行 Louvain 社群发现（weight=weight, seed=%d）……", RANDOM_SEED)

    # 只对规模 ≥ 3 的连通分量做社群发现。
    # 为什么？孤立的两三个节点谈不上"社群"，它们只会制造大量噪声社群，
    # 把真正的大团块淹没掉。这是图分析里很实用的一步预处理。
    components = [c for c in nx.connected_components(graph) if len(c) >= 3]
    logger.info("其中规模 ≥3 的连通分量有 %s 个", f"{len(components):,}")

    membership: dict[str, int] = {}
    next_id = 0

    for comp in components:
        subgraph = graph.subgraph(comp)
        communities = nx.community.louvain_communities(
            subgraph,
            weight="weight",
            seed=RANDOM_SEED,
        )
        for community in communities:
            for node in community:
                membership[node] = next_id
            next_id += 1

    logger.info("共发现 %s 个社群", f"{len(set(membership.values())):,}")
    return membership


# ----------------------------------------------------------------------
# 社群画像
# ----------------------------------------------------------------------
def profile_communities(
    graph: nx.Graph,
    membership: dict[str, int],
) -> pd.DataFrame:
    """为每个社群计算特征指标。

    指标设计围绕一个核心问题：**这个团块像不像"流水线"？**

      density          内部密度。真实课题组是树状结构（导师—学生），
                       密度低；挂名团伙要求"人人都能挂"，密度高。
      n_paper_mill_edges 含论文工厂记录的边数 —— 最直接的信号，但覆盖不全。
      journals_per_field 期刊数 ÷ 学科数。正常团队的期刊与学科是匹配的
                       （做医学的在医学期刊发），流水线则"什么期刊都投"。
      works_per_year   人均年产。异常高说明是批量生产而非真实研究。
      median_span      合作关系跨越的年数。真实合作通常持续多年。
    """
    # 按社群把节点分组
    groups: dict[int, list[str]] = {}
    for node, cid in membership.items():
        groups.setdefault(cid, []).append(node)

    records: list[dict[str, object]] = []

    for cid, nodes in groups.items():
        subgraph = graph.subgraph(nodes)
        n_nodes = subgraph.number_of_nodes()
        n_edges = subgraph.number_of_edges()

        if n_nodes < 3:
            continue

        # 密度 = 实际边数 / 最多可能的边数。
        # 完全图的密度是 1.0（人人互相合作），树状结构约为 2/n。
        density = nx.density(subgraph)

        # 汇总边属性
        total_works = sum(d["weight"] for _, _, d in subgraph.edges(data=True))
        total_pm_edges = sum(1 for _, _, d in subgraph.edges(data=True) if d["paper_mill"] > 0)
        total_pm_works = sum(d["paper_mill"] for _, _, d in subgraph.edges(data=True))

        first_years = [d["first_year"] for _, _, d in subgraph.edges(data=True) if d["first_year"]]
        last_years = [d["last_year"] for _, _, d in subgraph.edges(data=True) if d["last_year"]]
        max_journals = max((d["journals"] or 0) for _, _, d in subgraph.edges(data=True))
        max_fields = max((d["fields"] or 0) for _, _, d in subgraph.edges(data=True))

        span_start = min(first_years) if first_years else None
        span_end = max(last_years) if last_years else None
        year_span = (span_end - span_start + 1) if (span_start and span_end) else None

        # 人均年产：把"产出规模"和"团队规模、时间跨度"归一化。
        # 不做归一化的话，大团队、长时间跨度的社群永远排第一 ——
        # 那是规模效应，不是行为异常。
        works_per_author_year = total_works / n_nodes / year_span if year_span and year_span > 0 else None

        records.append(
            {
                "cluster_id": cid,
                "n_authors": n_nodes,
                "n_edges": n_edges,
                "density": round(density, 6),
                "total_shared_works": int(total_works),
                "n_paper_mill_works": int(total_pm_works),
                "n_paper_mill_edges": int(total_pm_edges),
                "paper_mill_edge_share": round(total_pm_edges / n_edges, 4) if n_edges else 0.0,
                "max_journals_in_edge": int(max_journals or 0),
                "max_fields_in_edge": int(max_fields or 0),
                "first_year": span_start,
                "last_year": span_end,
                "year_span": year_span,
                "works_per_author_year": round(works_per_author_year, 3)
                if works_per_author_year is not None
                else None,
            }
        )

    df = pd.DataFrame.from_records(records)
    logger.info("社群画像完成：%s 个规模 ≥3 的社群", f"{len(df):,}")
    return df


def score_clusters(df: pd.DataFrame) -> pd.DataFrame:
    """给社群打可疑度分数 —— **两个分数，分开用途**。

    【为什么是两个分数】
        第一版只做了一个 `cluster_risk_score`，把 `paper_mill_edge_share`
        以 0.40 权重算进去，然后用"社群是否含 Paper Mill 论文"做标签验证，
        得到 AUC = 0.843 —— 看起来很好。

        但只要停下来想一秒就会发现：**这和期刊评分那次犯的是同一个错误**。
        `paper_mill_edge_share` 是从 Reason 字段派生的，而标签也是从 Reason
        字段派生的 —— 一个没有论文工厂标注的社群，其 paper_mill_edge_share
        必然恰好为 0。**这是目标泄漏，AUC 被系统性虚高了。**

        所以这里强制拆成两个分数，与期刊评分保持完全一致的方法论：

          cluster_risk_score（严重度分）      含 Reason 派生特征 → 仅用于排序已确认问题
          cluster_structural_score（结构分）  零 Reason 派生特征 → 这才是真正可预测的

    【为什么同一个项目里会重复犯同一个错误 —— 这一点比 bug 本身更重要】
        因为"这个特征很有用"的直觉太强了。它确实有用，但它有用是因为它抄了答案。
        **目标泄漏最难防的地方在于：泄漏的特征往往恰恰就是最强的那个特征。**
        唯一的办法是把它写进流程 —— 先按"这个特征的信息来源是什么"给特征分类，
        再决定它能不能进入预测模型。
        （本模块与 SQL 侧的 marts.feature_discriminative_power 用同一套 feature_group 思路。）

    【权重设定依据】研究者先验，不是从数据学来的：
        ---- 严重度分 ----
        paper_mill_edge_share  0.40  最直接，但只覆盖已被标注的部分
        density                0.25  团块结构 —— 流水线需要"人人可挂名"
        works_per_author_year  0.20  产出速率异常
        field_journal_ratio    0.15  学科与期刊不匹配（乱投）
        ---- 结构分（去掉泄漏特征，其余按比例放大）----
        density                0.45
        works_per_author_year  0.35
        field_journal_ratio    0.20
    """
    if df.empty:
        return df

    scored = df.copy()

    # 先把可能含 None 的列统一成 float。
    #
    # 【这里踩过一个坑】最初写成 `.replace(0, pd.NA).astype(float)`，
    # 结果报 `TypeError: float() argument must be ... not 'NAType'`。
    # 原因：`pd.NA` 是 pandas 的可空类型标量，它**不能被转成 numpy 的 float**。
    # 要做数值运算就用 `float("nan")`；`pd.NA` 是给 nullable dtype 用的。
    # 混用两者是 pandas 里很常见的坑，而且报错信息指向 astype 而不是 NA，
    # 排查时需要往回追一步才知道问题出在哪。
    for column in ("max_journals_in_edge", "max_fields_in_edge", "works_per_author_year"):
        scored[column] = pd.to_numeric(scored[column], errors="coerce").astype(float)

    # 学科/期刊比：>1 表示期刊数多于学科数（在多个期刊发同一领域的东西，正常）
    # <1 表示学科数多于期刊数（一本期刊里跨很多学科，可疑）
    # 这里取"学科数 ÷ 期刊数"作为"乱投"的度量，越大越可疑。
    scored["field_journal_ratio"] = scored["max_fields_in_edge"] / scored["max_journals_in_edge"].replace(
        0.0, float("nan")
    )

    for column in ("paper_mill_edge_share", "density", "works_per_author_year", "field_journal_ratio"):
        scored[f"pr_{column}"] = scored[column].rank(pct=True)

    # 缺失值用中位数填充（0.5），而不是 0 或 1 ——
    # 填 0 会系统性低估，填 1 会系统性高估，填中位数是中性选择。
    for column in ("pr_works_per_author_year", "pr_field_journal_ratio"):
        scored[column] = scored[column].fillna(0.5)

    # ---------------- 分数一：严重度分（含泄漏特征） ----------------
    scored["cluster_risk_score"] = (
        100
        * (
            0.40 * scored["pr_paper_mill_edge_share"]
            + 0.25 * scored["pr_density"]
            + 0.20 * scored["pr_works_per_author_year"]
            + 0.15 * scored["pr_field_journal_ratio"]
        )
    ).round(2)

    # ---------------- 分数二：结构分（零泄漏，可用于预测） ----------------
    #
    # 【⚠ 实测结果：这个分数反向预测了，AUC = 0.336（比随机还差）】
    #
    #   查证后的结论比"分数不好用"更有价值，完整记录如下：
    #
    #   结构分最高的几个社群，逐一核查后发现**根本不是论文工厂**：
    #       社群 #11158  180 篇集中在《Repetitorium Anästhesiologie》—— 一本德国麻醉学教科书
    #       社群 #3090   2011 年那次会议论文集批量撤稿（我们在发现 3 里已经见过）
    #       社群 #10614  《The Periodic Table: Nature's Building Blocks》—— 一本化学书
    #       社群 #9026   BIO Web of Conferences
    #   也就是说，**结构分实际上在识别"书籍章节 / 会议论文集的大规模撤稿"**，
    #   而不是论文工厂。两类事件在结构上高度相似（都是同一批人、同一年、
    #   同一本出版物、一次性大量撤稿），但成因完全不同。
    #   这与发现 7（burst_ratio 无区分能力）是**同一个现象的两个侧面**：
    #   「批量撤稿」这种结构特征，无法区分"流水线造假"和"整本出版物出问题"。
    #
    #   还有一个更基础的设计缺陷被暴露出来：**density 在小社群上会饱和**。
    #   实测：3 人社群中 92.8% 的密度恰好等于 1.0（3 个节点最多只有 1 个三角形，
    #   只要合作过就是满密度）。4 人社群 89.2%，5 人 86.4%……
    #   也就是说，density 在小规模区间几乎是常数，**根本不含信息**，
    #   而我把 45% 的权重给了它。
    #
    #   教训：**图指标的取值在不同规模之间不可比。**
    #   密度、聚类系数这类"归一化"指标看起来无量纲，
    #   实际上它们的**方差随规模急剧变化**，直接放进跨规模的排名里是错的。
    #   正确做法是按社群规模分层比较，或改用不随规模饱和的度量
    #   （例如相对随机图的超额连接数、或最大团规模）。
    #   这属于**已知未修复问题**，明确记录在 README 的"后续改进"里。
    scored["cluster_structural_score"] = (
        100
        * (
            0.45 * scored["pr_density"]
            + 0.35 * scored["pr_works_per_author_year"]
            + 0.20 * scored["pr_field_journal_ratio"]
        )
    ).round(2)

    return scored.sort_values("cluster_risk_score", ascending=False).reset_index(drop=True)


def validate_clusters(scored: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, float]]:
    """用"社群是否含有 Paper Mill 论文"作为标签，验证**两个**分数的区分能力。

    与期刊评分用的是同一套 AUC 计算方式（Mann-Whitney U 统计量），
    这样四处验证结果可以横向对比。返回 (带标签的明细表, {分数说明: AUC})。
    """
    if scored.empty:
        return scored, {}

    df = scored.copy()
    df["has_paper_mill"] = df["n_paper_mill_works"] > 0
    n_pos = int(df["has_paper_mill"].sum())
    n_neg = int((~df["has_paper_mill"]).sum())

    if n_pos == 0 or n_neg == 0:
        logger.warning("社群里正例或负例为 0（正例 %d / 负例 %d），无法计算 AUC。", n_pos, n_neg)
        return df, {}

    aucs: dict[str, float] = {}
    for column, label in (
        ("cluster_risk_score", "严重度分（含标签特征 → 有泄漏）"),
        ("cluster_structural_score", "结构分（零标签特征 → 可信）"),
    ):
        # 平均秩处理并列（与 SQL 版本同一套逻辑，避免算出 AUC > 1 的错误）
        ranks = df[column].rank(method="average")
        sum_rank_pos = ranks[df["has_paper_mill"]].sum()
        auc = (sum_rank_pos - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)
        aucs[label] = round(float(auc), 4)
        logger.info("  %-32s AUC = %.4f", label, auc)

    logger.info("（社群正例 %d / 负例 %d）", n_pos, n_neg)
    return df, aucs


# ----------------------------------------------------------------------
# 写回数仓
# ----------------------------------------------------------------------
def persist(
    con: duckdb.DuckDBPyConnection,
    scored: pd.DataFrame,
    membership: dict[str, int],
    aucs: dict[str, float],
) -> None:
    """把结果写回 DuckDB，供看板和后续分析使用。"""
    con.register("_cluster_scores", scored)
    con.execute("CREATE OR REPLACE TABLE marts.author_clusters AS SELECT * FROM _cluster_scores")
    con.unregister("_cluster_scores")

    # 【这里修过一个 bug —— 由质量校验发现】
    # 最初直接把 membership 全量落盘，结果 marts.author_cluster_members 里
    # 有 1,322 行指向**不存在的社群**：因为 profile_communities() 会过滤掉
    # 规模 <3 的社群，而成员表没有做同样的过滤。
    #
    # 这类"两个地方用了不同的过滤条件"是数据管道里的高发问题：
    # 它不会报错，只会在下游 join 时静默丢行，或者在看板上点开社群看到空列表。
    # 我们提前把这条写成了质量规则（[图分析] 社群成员表外键完整），
    # 它第一次运行就抓到了 —— 这正是"把预期写成可执行校验"的价值。
    valid_clusters = set(scored["cluster_id"])
    members = pd.DataFrame(
        [
            {"author_id": author_id, "cluster_id": cluster_id}
            for author_id, cluster_id in membership.items()
            if cluster_id in valid_clusters
        ]
    )
    dropped = len(membership) - len(members)
    if dropped:
        logger.info("成员表已剔除 %s 位属于「被过滤掉的小社群」的作者", f"{dropped:,}")
    con.register("_cluster_members", members)
    con.execute(
        """
        CREATE OR REPLACE TABLE marts.author_cluster_members AS
        SELECT m.cluster_id, m.author_id, a.author_name, a.orcid
        FROM _cluster_members AS m
        LEFT JOIN (
            -- 一位作者可能有多条署名记录，取出现次数最多的那个名字
            SELECT author_id, author_name, any_value(orcid) AS orcid
            FROM staging.openalex_authorships
            WHERE author_id IS NOT NULL
            GROUP BY author_id, author_name
            QUALIFY row_number() OVER (PARTITION BY author_id ORDER BY COUNT(*) DESC) = 1
        ) AS a ON m.author_id = a.author_id
        """
    )
    con.unregister("_cluster_members")

    # 两个分数各自落一行，方便下游用 score_name 过滤。
    # 用 UNION ALL 而不是宽表，是为了让"分数可以继续增加"时不用改表结构。
    validation = pd.DataFrame([{"score_name": name, "auc": auc} for name, auc in aucs.items()])
    con.register("_cluster_validation", validation)
    con.execute(
        """
        CREATE OR REPLACE TABLE marts.author_cluster_validation AS
        SELECT
            v.score_name,
            v.auc,
            (SELECT COUNT(*) FROM marts.author_clusters) AS n_clusters,
            (SELECT COUNT(*) FROM marts.author_clusters WHERE n_paper_mill_works > 0)
                AS n_clusters_with_paper_mill,
            (SELECT COUNT(*) FROM intermediate.coauthorship_edges) AS n_edges_total
        FROM _cluster_validation AS v
        """
    )
    con.unregister("_cluster_validation")
    logger.info("结果已写回：marts.author_clusters / author_cluster_members / author_cluster_validation")


# ----------------------------------------------------------------------
# 入口
# ----------------------------------------------------------------------
def run(settings: Settings | None = None) -> NetworkAnalysisResult:
    """执行完整的网络分析流程。"""
    settings = settings or get_settings()
    from papermill_hunter.warehouse.db import connect  # 延迟导入，避免循环依赖

    con = connect(settings)
    try:
        edges = load_edges(con)
        graph = build_graph(edges)
        membership = detect_communities(graph)
        profiles = profile_communities(graph, membership)
        scored = score_clusters(profiles)
        scored, aucs = validate_clusters(scored)
        persist(con, scored, membership, aucs)

        top_id = int(scored.iloc[0]["cluster_id"]) if not scored.empty else None
        return NetworkAnalysisResult(
            n_nodes=graph.number_of_nodes(),
            n_edges=graph.number_of_edges(),
            n_communities=len(set(membership.values())),
            n_clusters_scored=len(scored),
            top_cluster_id=top_id,
            aucs=aucs,
        )
    finally:
        con.close()
