"""看板的数据访问层。

【为什么把查询和 UI 分开】
    如果把 SQL 直接写在 Streamlit 页面代码里，会带来两个问题：
      1. **无法测试**。Streamlit 页面代码在 import 时就会执行，
         测试框架没法在不起服务的情况下验证查询是否正确。
      2. **无法复用**。同一份数据要在看板、报告、导出功能里用三次，
         就得抄三遍 SQL —— 然后三份 SQL 慢慢长歪，数字对不上。

    所以这里把所有查询集中成一个函数集合：输入参数、输出 DataFrame，
    不依赖 Streamlit。dashboard.py 只负责把这些结果画出来。
    —— 这条边界和"采集层不做清洗"是同一种思路：
      **把可测试的计算逻辑，和不可测试的展示逻辑分开。**
"""

from __future__ import annotations

import pandas as pd

from papermill_hunter.config import Settings, get_settings
from papermill_hunter.warehouse.db import connect


def _query(sql: str, params: list[object] | None = None, settings: Settings | None = None) -> pd.DataFrame:
    """执行查询并返回 DataFrame。每次调用开一个新连接并关闭。

    为什么不为每次查询复用连接？
        看板是"交互式、低频"的访问模式：用户点一下，查一次，几毫秒。
        连接开销在这个量级下可以忽略，而每次关闭连接能避免
        DuckDB 在多线程/多会话下的写锁问题（Streamlit 会并发跑多个会话）。
        **为使用场景选择合适的取舍**，而不是无脑套用"连接池更快"。
    """
    settings = settings or get_settings()
    # require_exists=True：读路径不允许"顺手创建一个空库"。
    # 详见 warehouse/db.py 里 connect() 的说明 —— 这个参数来自一次真实事故。
    con = connect(settings, require_exists=True)
    try:
        return con.execute(sql, params or []).df()
    finally:
        con.close()


# ======================================================================
# 总览
# ======================================================================
def overview_metrics(settings: Settings | None = None) -> dict[str, int]:
    """看板顶部的核心指标卡。"""
    df = _query(
        """
        SELECT
            (SELECT COUNT(*) FROM staging.retraction_watch)                 AS rw_records,
            (SELECT COUNT(*) FROM staging.openalex_works)                   AS oa_works,
            (SELECT COUNT(*) FROM intermediate.works_enriched
              WHERE matched_in_retraction_watch)                            AS matched,
            (SELECT COUNT(*) FROM intermediate.retractions_enriched
              WHERE is_paper_mill)                                          AS paper_mill,
            (SELECT COUNT(*) FROM marts.journal_risk)                       AS scored_journals,
            (SELECT COUNT(*) FROM marts.author_clusters)                    AS clusters,
            (SELECT COUNT(DISTINCT country) FROM staging.retraction_countries) AS countries,
            (SELECT COUNT(DISTINCT journal) FROM staging.retraction_watch
              WHERE journal IS NOT NULL)                                    AS journals
        """,
        settings=settings,
    )
    return {k: int(v) for k, v in df.iloc[0].to_dict().items() if pd.notna(v)}


def quality_summary(settings: Settings | None = None) -> pd.DataFrame:
    """质量校验结果（读 reports/quality_report.json 落成的表）。"""
    settings = settings or get_settings()
    path = settings.reports_dir / "quality_report.json"
    if not path.exists():
        return pd.DataFrame()
    import json

    payload = json.loads(path.read_text(encoding="utf-8"))
    return pd.DataFrame(payload.get("results", []))


# ======================================================================
# 趋势
# ======================================================================
def retraction_trends(settings: Settings | None = None) -> pd.DataFrame:
    return _query(
        """
        SELECT year, n_retractions, n_paper_mill, n_peer_review_fraud, n_ai_generated,
               paper_mill_share, median_latency_days, ma3_retractions, yoy_change_pct,
               cumulative_retractions
        FROM marts.retraction_trends
        WHERE year BETWEEN 1990 AND 2030
        ORDER BY year
        """,
        settings=settings,
    )


# ======================================================================
# 国家
# ======================================================================
def country_leaderboard(limit: int = 30, settings: Settings | None = None) -> pd.DataFrame:
    return _query(
        """
        SELECT country, n_retractions, n_as_sole_country, share_of_global_pct,
               n_paper_mill, paper_mill_share, peer_review_fraud_share,
               ai_generated_share, median_latency_days, n_recent_5y,
               active_years, n_journals, rank_by_volume
        FROM marts.country_leaderboard
        ORDER BY n_retractions DESC
        LIMIT ?
        """,
        [limit],
        settings,
    )


# ======================================================================
# 原因
# ======================================================================
def reason_landscape(limit: int = 25, settings: Settings | None = None) -> pd.DataFrame:
    return _query(
        """
        SELECT reason, n_occurrences, n_records, pct_of_all_retractions,
               median_latency_days, n_recent_5y, recent_5y_share, china_share
        FROM marts.reason_landscape
        ORDER BY n_records DESC
        LIMIT ?
        """,
        [limit],
        settings,
    )


# ======================================================================
# 期刊风险
# ======================================================================
def journal_risk(
    min_retractions: int = 5,
    tier: str | None = None,
    limit: int = 500,
    settings: Settings | None = None,
) -> pd.DataFrame:
    sql = """
        SELECT journal, publisher, n_retractions, active_years, first_year, last_year,
               peak_year, peak_year_retractions, burst_ratio,
               n_paper_mill, paper_mill_share, peer_review_fraud_share,
               ai_generated_share, median_latency_days, china_share,
               risk_score, risk_tier, structural_score
        FROM marts.journal_risk
        WHERE n_retractions >= ?
    """
    params: list[object] = [min_retractions]
    if tier:
        sql += " AND risk_tier = ?"
        params.append(tier)
    sql += " ORDER BY risk_score DESC LIMIT ?"
    params.append(limit)
    return _query(sql, params, settings)


def risk_validation(settings: Settings | None = None) -> pd.DataFrame:
    return _query(
        "SELECT score_name, n_journals_total, auc, auc_grade FROM marts.risk_score_validation "
        "ORDER BY auc DESC",
        settings=settings,
    )


def feature_power(settings: Settings | None = None) -> pd.DataFrame:
    return _query(
        """
        SELECT feature, feature_group, auc, auc_abs, suspicious_direction, leakage_note
        FROM marts.feature_discriminative_power
        ORDER BY auc_abs DESC
        """,
        settings=settings,
    )


def risk_tier_performance(settings: Settings | None = None) -> pd.DataFrame:
    return _query(
        "SELECT * FROM marts.risk_tier_performance ORDER BY min_score DESC",
        settings=settings,
    )


# ======================================================================
# 引用代价
# ======================================================================
def citation_event_study(settings: Settings | None = None) -> pd.DataFrame:
    return _query(
        """
        SELECT event_time, n_works, mean_citations, counterfactual_citations,
               citation_gap, citation_gap_pct, data_coverage, is_post_retraction
        FROM marts.retraction_citation_event_study
        ORDER BY event_time
        """,
        settings=settings,
    )


def citation_event_study_by_group(settings: Settings | None = None) -> pd.DataFrame:
    return _query(
        """
        SELECT group_name, event_time, n_works, mean_citations,
               counterfactual_citations, citation_gap, citation_gap_pct
        FROM marts.retraction_citation_event_study_by_group
        ORDER BY group_name, event_time
        """,
        settings=settings,
    )


def citation_penalty_summary(settings: Settings | None = None) -> pd.DataFrame:
    return _query("SELECT * FROM marts.retraction_citation_penalty_summary", settings=settings)


# ======================================================================
# 合作网络
# ======================================================================
def author_clusters(
    min_authors: int = 3,
    limit: int = 300,
    order_by: str = "cluster_risk_score",
    settings: Settings | None = None,
) -> pd.DataFrame:
    # 排序字段必须白名单校验 —— 它会被直接拼进 SQL。
    # 这个看板只在本地跑，但"用户输入拼进 SQL"是必须养成的习惯：
    # 参数化查询能防注入的**值**，防不了**标识符**（列名、表名），
    # 而标识符只能用白名单。把这条纪律带到生产代码里，能省掉事故。
    allowed = {"cluster_risk_score", "cluster_structural_score", "n_authors", "total_shared_works"}
    if order_by not in allowed:
        raise ValueError(f"order_by 必须是 {sorted(allowed)} 之一，收到 {order_by!r}")

    return _query(
        f"""
        SELECT cluster_id, n_authors, n_edges, density, total_shared_works,
               n_paper_mill_works, paper_mill_edge_share, max_journals_in_edge,
               max_fields_in_edge, first_year, last_year, year_span,
               works_per_author_year, cluster_risk_score, cluster_structural_score
        FROM marts.author_clusters
        WHERE n_authors >= ?
        ORDER BY {order_by} DESC
        LIMIT ?
        """,
        [min_authors, limit],
        settings,
    )


def cluster_members(cluster_id: int, limit: int = 50, settings: Settings | None = None) -> pd.DataFrame:
    return _query(
        """
        SELECT author_name, orcid, author_id
        FROM marts.author_cluster_members
        WHERE cluster_id = ?
        ORDER BY author_name
        LIMIT ?
        """,
        [cluster_id, limit],
        settings,
    )


def cluster_validation(settings: Settings | None = None) -> pd.DataFrame:
    return _query("SELECT * FROM marts.author_cluster_validation ORDER BY auc DESC", settings=settings)


def density_by_size(settings: Settings | None = None) -> pd.DataFrame:
    """社群规模 vs 密度的关系 —— 用来看 density 的规模饱和问题。"""
    return _query(
        """
        SELECT n_authors,
               COUNT(*) AS n_clusters,
               ROUND(AVG(density), 4) AS avg_density,
               ROUND(AVG(CASE WHEN density >= 0.999 THEN 1.0 ELSE 0.0 END), 4) AS saturated_share
        FROM marts.author_clusters
        WHERE n_authors BETWEEN 3 AND 30
        GROUP BY n_authors
        ORDER BY n_authors
        """,
        settings=settings,
    )


# ======================================================================
# 跨源
# ======================================================================
def cross_source_gap(settings: Settings | None = None) -> pd.DataFrame:
    return _query(
        """
        SELECT publication_year AS year,
               COUNT(*) AS total,
               SUM(matched_in_retraction_watch::INT) AS matched,
               COUNT(*) - SUM(matched_in_retraction_watch::INT) AS unmatched,
               ROUND(100.0 * SUM(matched_in_retraction_watch::INT) / COUNT(*), 2) AS match_rate
        FROM intermediate.works_enriched
        WHERE publication_year IS NOT NULL
        GROUP BY publication_year
        ORDER BY publication_year
        """,
        settings=settings,
    )
