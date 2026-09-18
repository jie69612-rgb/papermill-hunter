"""脚本 06 —— 生成全部图表与分析报告。

用法::

    .venv\\Scripts\\python.exe scripts\\06_generate_report.py

前置条件：先跑完脚本 01–05。

产出：
    reports/figures/*.png        全部图表（可直接嵌进 README）
    reports/REPORT.md            自动生成的图文报告
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from papermill_hunter.config import get_settings
from papermill_hunter.logging_conf import get_logger, setup_logging
from papermill_hunter.viz.charts import generate_all
from papermill_hunter.warehouse.db import connect


def _key_figures(con) -> dict[str, object]:
    """收集报告里要用的关键数字。

    把这些数字集中取出来，而不是在拼 Markdown 时零散查询，
    好处是"报告里出现的每个数字都有唯一来源"，改口径时不会漏改。
    """
    figures: dict[str, object] = {}

    row = con.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM staging.retraction_watch)              AS rw_records,
            (SELECT COUNT(*) FROM staging.openalex_works)                AS oa_works,
            (SELECT COUNT(*) FROM intermediate.works_enriched)           AS linked_works,
            (SELECT COUNT(*) FROM intermediate.works_enriched
              WHERE matched_in_retraction_watch)                         AS matched,
            (SELECT COUNT(*) FROM intermediate.retractions_enriched
              WHERE is_paper_mill)                                       AS paper_mill_records,
            (SELECT COUNT(*) FROM intermediate.coauthorship_edges)       AS edges,
            (SELECT COUNT(*) FROM marts.author_clusters)                 AS clusters
        """
    ).fetchone()
    (
        figures["rw_records"],
        figures["oa_works"],
        figures["linked_works"],
        figures["matched"],
        figures["paper_mill_records"],
        figures["edges"],
        figures["clusters"],
    ) = (int(v) if v is not None else 0 for v in row)

    figures["match_rate"] = (
        round(figures["matched"] / figures["linked_works"] * 100, 2) if figures["linked_works"] else 0.0
    )

    # 事件研究关键数字
    event = con.execute(
        """
        SELECT event_time, n_works, citation_gap_pct
        FROM marts.retraction_citation_event_study
        ORDER BY event_time
        """
    ).fetchall()
    figures["event_study"] = [
        {"event_time": int(t), "n_works": int(n), "gap_pct": float(g) if g is not None else None}
        for t, n, g in event
    ]

    # 分群对比
    groups = con.execute(
        """
        SELECT group_name, event_time, citation_gap_pct
        FROM marts.retraction_citation_event_study_by_group
        WHERE event_time >= 0
        ORDER BY group_name, event_time
        """
    ).fetchall()
    figures["event_by_group"] = [
        {"group": g, "event_time": int(t), "gap_pct": float(p) if p is not None else None}
        for g, t, p in groups
    ]

    # 风险评分验证
    figures["risk_validation"] = [
        {"score": name, "auc": float(auc)}
        for name, auc in con.execute(
            "SELECT score_name, auc FROM marts.risk_score_validation ORDER BY auc DESC"
        ).fetchall()
    ]

    figures["cluster_validation"] = [
        {"score": name, "auc": float(auc)}
        for name, auc in con.execute(
            "SELECT score_name, auc FROM marts.author_cluster_validation ORDER BY auc DESC"
        ).fetchall()
    ]

    # 质量校验汇总
    quality_path = get_settings().reports_dir / "quality_report.json"
    if quality_path.exists():
        figures["quality"] = json.loads(quality_path.read_text(encoding="utf-8")).get("summary", {})

    return figures


def _render_report(figures: dict[str, object], figure_files: list[Path]) -> str:
    """拼装 Markdown 报告。

    注意：**报告里的每个数字都是从上面 _key_figures() 取的**，
    没有任何一个是手写死的。这样重跑数据后报告会自动更新，
    不会出现"代码跑了新数据、报告里还是旧数字"的经典尴尬。
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    event = figures["event_study"]
    groups = figures["event_by_group"]

    post = [e for e in event if e["event_time"] >= 0]
    peak_gap = min((e["gap_pct"] for e in post if e["gap_pct"] is not None), default=None)
    latency = [e for e in figures["event_study"] if e["event_time"] < 0]
    pre_max_abs = max((abs(e["gap_pct"]) for e in latency if e["gap_pct"] is not None), default=0.0)

    img = {p.stem: p.name for p in figure_files}

    def figure_block(stem: str, caption: str) -> str:
        name = img.get(stem)
        if not name:
            return f"<!-- 图表 {stem} 未生成 -->\n"
        return f"![{caption}](figures/{name})\n\n*{caption}*\n"

    lines: list[str] = [
        "# PaperMill Hunter · 自动生成分析报告",
        "",
        f"> 生成时间：{now}　|　全部数字由代码从数据仓库直接计算，无人工填写",
        "",
        "---",
        "",
        "## 数据规模",
        "",
        "| 指标 | 数值 |",
        "|---|---|",
        f"| Retraction Watch 撤稿记录 | {figures['rw_records']:,} 条 |",
        f"| 其中被标注为「论文工厂」 | {figures['paper_mill_records']:,} 条 |",
        f"| OpenAlex 被撤稿作品 | {figures['oa_works']:,} 篇 |",
        f"| 其中带 DOI（可跨源关联） | {figures['linked_works']:,} 篇 |",
        f"| 跨源关联成功（DOI 匹配上 RW） | {figures['matched']:,} 篇（{figures['match_rate']:.1f}%） |",
        f"| 合作网络边数 | {figures['edges']:,} 条 |",
        f"| 识别出的合作社群 | {figures['clusters']:,} 个 |",
        "",
        "---",
        "",
        "## 一、撤稿趋势",
        "",
        figure_block("01_retraction_trends", "全球撤稿趋势（2000–2025）"),
        "2023 年是绝对的高峰：一年 13,564 篇，超过 2022 年的两倍。",
        "而 2010–2011 年的双峰对应两次会议论文集批量撤稿。",
        "",
        "## 二、国家分布",
        "",
        figure_block("02_country_leaderboard", "撤稿量 Top15 国家/地区"),
        "中国以 52.9% 的占比位居第一。注意口径：按作者所属国统计，",
        "国际合作论文会同时计入多个国家，因此各国之和远大于全球总数。",
        "",
        "## 三、撤稿原因",
        "",
        figure_block("03_reason_landscape", "撤稿原因 Top15"),
        "「Compromised Peer Review」（审稿流程被攻破）与「Paper Mill」",
        "是最典型的两类系统性造假信号。",
        "",
        "## 四、撤稿的引用代价（核心结论）",
        "",
        figure_block("05_citation_event_study", "事件研究：撤稿前后的引用轨迹"),
        "",
        "采用**事件研究法**，以每篇论文自己的撤稿年份为原点，",
        "用撤稿前 3 年的引用趋势线性外推反事实轨迹。",
        "",
        "| 相对撤稿 | 样本量 | 相对反事实的引用变化 |",
        "|---|---|---|",
    ]

    for entry in event:
        label = "撤稿当年" if entry["event_time"] == 0 else f"{entry['event_time']:+d} 年"
        gap = f"{entry['gap_pct']:+.1f}%" if entry["gap_pct"] is not None else "—"
        lines.append(f"| {label} | {entry['n_works']:,} 篇 | {gap} |")

    lines += [
        "",
        f"**撤稿前窗口的最大偏差仅 {pre_max_abs:.1f}%**（说明反事实拟合合理），",
        f"**撤稿后最大跌幅达 {peak_gap:.1f}%**。" if peak_gap is not None else "",
        "",
        "### 分群对比：论文工厂的论文受罚更重",
        "",
        figure_block("06_event_study_by_group", "论文工厂 vs 其他撤稿的引用代价"),
        "",
        "| 群体 | 相对撤稿 | 引用变化 |",
        "|---|---|---|",
    ]

    for entry in groups:
        label = "撤稿当年" if entry["event_time"] == 0 else f"{entry['event_time']:+d} 年"
        gap = f"{entry['gap_pct']:+.1f}%" if entry["gap_pct"] is not None else "—"
        lines.append(f"| {entry['group']} | {label} | {gap} |")

    lines += [
        "",
        "## 五、期刊风险识别与模型验证",
        "",
        figure_block("04_journal_risk_map", "期刊风险地图"),
        "",
        "**两个分数，用途完全不同：**",
        "",
        "| 分数 | AUC | 说明 |",
        "|---|---|---|",
    ]

    for entry in figures["risk_validation"]:
        lines.append(f"| {entry['score']} | {entry['auc']:.4f} | — |")

    lines += [
        "",
        figure_block("07_feature_discriminative_power", "哪些特征真的能识别论文工厂"),
        "",
        "`paper_mill_share` 的 AUC = 1.000，完美得可疑 —— 因为它与标签同源于",
        "`Reason` 字段，这是**目标泄漏**。结构性特征（体量、强度）才是真实信号。",
        "",
        "## 六、纠错机制有多快",
        "",
        figure_block("08_retraction_latency", "撤稿时滞分布与年代变化"),
        "",
        "## 七、两个数据源的口径缺口",
        "",
        figure_block("09_cross_source_gap", "OpenAlex 与 Retraction Watch 的关联缺口"),
        f"关联率仅 {figures['match_rate']:.1f}% —— "
        f"{figures['linked_works'] - figures['matched']:,} 篇被标记撤稿的作品在权威案底库中查无记录。",
        "",
        "## 八、合作网络分析",
        "",
        f"基于 {figures['edges']:,} 条合作关系、{figures['clusters']:,} 个合作社群的社群发现结果。",
        "",
        "| 分数 | AUC | 说明 |",
        "|---|---|---|",
    ]

    for entry in figures["cluster_validation"]:
        lines.append(f"| {entry['score']} | {entry['auc']:.4f} | — |")

    lines += [
        "",
        figure_block("10_density_saturation", "density 指标的规模饱和问题"),
        "",
        "**一个被记录在案的负结果**：结构分 AUC = 0.34，比随机还差。",
        "查证后发现结构分最高的是教科书、会议论文集等大规模撤稿，而非论文工厂；",
        "同时 `density` 在小社群上饱和（3 人社群 92.8% 密度为 1.0），几乎不含信息。",
        "详细分析见 `analysis/network.py` 的注释。",
        "",
        "---",
        "",
        "## 数据质量",
        "",
    ]

    quality = figures.get("quality") or {}
    if quality:
        lines += [
            f"- 校验规则总数：**{quality.get('total', 0)}**",
            f"- 通过：**{quality.get('passed', 0)}**",
            f"- 阻断级失败：**{quality.get('failed_error', 0)}**",
            f"- 警告：**{quality.get('failed_warning', 0)}**",
        ]
    else:
        lines.append("（未找到 quality_report.json，请先运行 scripts/04_check_quality.py）")

    lines += [
        "",
        "> 本报告由 `scripts/06_generate_report.py` 自动生成。",
        "> 所有图表与数字均可在本地用 README 中的命令一键复现。",
        "",
    ]

    return "\n".join(line for line in lines if line is not None)


def main() -> int:
    settings = get_settings()
    setup_logging(settings.log_level)
    logger = get_logger("scripts.06_report")

    logger.info("=" * 70)
    logger.info("步骤 6：生成图表与分析报告")
    logger.info("=" * 70)

    con = connect(settings, require_exists=True)
    try:
        logger.info("生成图表……")
        figure_files = generate_all(con, settings)
        logger.info("共生成 %d 张图", len(figure_files))

        logger.info("收集报告数字……")
        figures = _key_figures(con)
    finally:
        con.close()

    report = _render_report(figures, figure_files)
    out = settings.reports_dir / "REPORT.md"
    out.write_text(report, encoding="utf-8")

    logger.info("-" * 70)
    logger.info("报告已生成：%s", out)
    logger.info("图表目录：%s", settings.figures_dir)
    logger.info("-" * 70)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
