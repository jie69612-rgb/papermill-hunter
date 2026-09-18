"""脚本 05 —— 合作网络分析与"挂名团伙"识别。

用法::

    .venv\\Scripts\\python.exe scripts\\05_analyze_network.py

前置条件：先运行脚本 03 构建数据仓库（需要 intermediate.coauthorship_edges）。

产出三张表：
    marts.author_clusters            —— 每个社群的可疑度评分与特征
    marts.author_cluster_members     —— 社群成员明细
    marts.author_cluster_validation  —— 分数的标签验证结果（AUC）
"""

from __future__ import annotations

from papermill_hunter.analysis.network import run
from papermill_hunter.config import get_settings
from papermill_hunter.logging_conf import get_logger, setup_logging


def main() -> int:
    settings = get_settings()
    setup_logging(settings.log_level)
    log = get_logger("scripts.05_network")

    log.info("=" * 70)
    log.info("步骤 5：合作网络分析与挂名团伙识别")
    log.info("=" * 70)

    result = run(settings)

    log.info("-" * 70)
    log.info("网络规模：%s 个节点 / %s 条边（合作 ≥2 篇）", f"{result.n_nodes:,}", f"{result.n_edges:,}")
    log.info(
        "发现社群：%s 个（其中 %s 个规模 ≥3 并参与评分）",
        f"{result.n_communities:,}",
        f"{result.n_clusters_scored:,}",
    )
    log.info("分数验证（对照官方 Paper Mill 标签）：")
    for name, auc in result.aucs.items():
        log.info("    %-32s AUC = %.4f", name, auc)
    log.info("-" * 70)

    # 展示可疑度最高的社群及其成员
    from papermill_hunter.warehouse.db import connect

    con = connect(settings)
    try:
        log.info("【严重度分最高的 10 个合作社群】")
        sql = """
            SELECT cluster_id, n_authors, n_edges, density, total_shared_works,
                   n_paper_mill_works, paper_mill_edge_share, max_journals_in_edge,
                   max_fields_in_edge, first_year, last_year, works_per_author_year,
                   cluster_risk_score, cluster_structural_score
            FROM marts.author_clusters
            ORDER BY cluster_risk_score DESC
            LIMIT 10
        """
        cursor = con.execute(sql)
        # 从同一条查询里取列名，避免"列名列表和结果集对不上"（这里踩过一次）
        columns = [c[0] for c in cursor.description]
        rows = cursor.fetchall()

        for row in rows:
            data = dict(zip(columns, row, strict=True))
            log.info(
                "  社群 #%-5d 作者 %3d 人 | 内部边 %5d | 密度 %.3f | 合作 %5d 篇 | "
                "含论文工厂 %4d 篇 | 期刊 %2d / 学科 %2d | %s~%s | 严重度 %.1f",
                data["cluster_id"],
                data["n_authors"],
                data["n_edges"],
                data["density"],
                data["total_shared_works"],
                data["n_paper_mill_works"],
                data["max_journals_in_edge"],
                data["max_fields_in_edge"],
                data["first_year"],
                data["last_year"],
                data["cluster_risk_score"],
            )

        log.info("-" * 70)
        log.info("【结构分最高的 5 个社群（完全没用撤稿原因信息）】")
        cursor = con.execute(
            """
            SELECT cluster_id, n_authors, density, total_shared_works,
                   n_paper_mill_works, max_journals_in_edge, max_fields_in_edge,
                   first_year, last_year, works_per_author_year, cluster_structural_score
            FROM marts.author_clusters
            ORDER BY cluster_structural_score DESC
            LIMIT 5
            """
        )
        columns = [c[0] for c in cursor.description]
        for row in cursor.fetchall():
            data = dict(zip(columns, row, strict=True))
            log.info(
                "  社群 #%-5d 作者 %3d 人 | 密度 %.3f | 合作 %5d 篇 | "
                "含论文工厂 %4d 篇 | 期刊 %2d / 学科 %2d | %s~%s | 人均年产 %.2f | 结构分 %.1f",
                data["cluster_id"],
                data["n_authors"],
                data["density"],
                data["total_shared_works"],
                data["n_paper_mill_works"],
                data["max_journals_in_edge"],
                data["max_fields_in_edge"],
                data["first_year"],
                data["last_year"],
                data["works_per_author_year"] or 0.0,
                data["cluster_structural_score"],
            )

        if result.top_cluster_id is not None:
            log.info("-" * 70)
            log.info("【严重度最高社群 #%d 的成员（前 15 位）】", result.top_cluster_id)
            members = con.execute(
                """
                SELECT m.author_name, m.orcid
                FROM marts.author_cluster_members AS m
                WHERE m.cluster_id = ?
                ORDER BY m.author_name
                LIMIT 15
                """,
                [result.top_cluster_id],
            ).fetchall()
            for name, orcid in members:
                log.info("    %-38s %s", name or "(未知)", orcid or "")

        log.info("-" * 70)
        log.warning(
            "⚠ 以上是**筛查线索**，不是结论。社群发现是无监督方法，"
            "高密度社群同样可能是高产的正规课题组。"
            "任何针对具体个人的判断都必须经过人工核查。"
        )
    finally:
        con.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
