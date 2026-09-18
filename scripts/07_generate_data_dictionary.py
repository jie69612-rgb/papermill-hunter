"""脚本 07 —— 从数据库元数据自动生成数据字典。

用法::

    .venv\\Scripts\\python.exe scripts\\07_generate_data_dictionary.py

产出 ``docs/DATA_DICTIONARY.md``。

【为什么数据字典要自动生成，而不是手写】
    手写的 schema 文档有 100% 的概率在两周内过时：
    有人加了一列、改了类型、删了张表，但没人会记得回去改文档。
    然后文档就从"资产"变成了"负债" —— 新人照着它写 SQL，
    报出"列不存在"，从此不再相信任何文档。

    从 ``information_schema`` 生成则天然与代码同步：
    **文档的正确性由构建流程保证，而不是由人的自觉保证。**

【但自动生成只能覆盖"结构"，覆盖不了"含义"】
    元数据能告诉你有一列叫 ``burst_ratio``，类型是 DOUBLE；
    但没法告诉你它是"峰值年撤稿数 ÷ 撤稿总数"、取值范围是 0~1、
    以及**为什么这个指标实测无效**。

    所以本脚本采用混合方式：
      - 表名、列名、类型、行数 → 从数据库自动读取（永远准确）
      - 语义说明 → 从下面手写的 ``SEMANTICS`` 字典补充（人工维护，缺失会显式标注）
    缺失说明的列会被标记出来，提醒补充 —— 而不是悄悄留空。
"""

from __future__ import annotations

from datetime import datetime

import duckdb

from papermill_hunter.config import get_settings
from papermill_hunter.logging_conf import get_logger, setup_logging
from papermill_hunter.warehouse.db import connect

# ----------------------------------------------------------------------
# 人工维护的语义层
# ----------------------------------------------------------------------
# 只写"元数据说不出来"的东西：这个字段是什么意思、口径是什么、有什么坑。
# 键为 "schema.table.column"，值为说明文本。
SEMANTICS: dict[str, str] = {
    # ---------------- staging：Retraction Watch ----------------
    "staging.retraction_watch.record_id": "Retraction Watch 内部记录 ID，本表主键",
    "staging.retraction_watch.retraction_date": "撤稿声明发布日期。解析自美式格式，含 AM/PM 兜底",
    "staging.retraction_watch.original_paper_date": "原文发表日期。149 条为空",
    "staging.retraction_watch.original_paper_doi": "原文 DOI，**跨源关联的唯一钥匙**（已去前缀、转小写）",
    "staging.retraction_watch.retraction_nature": "撤稿性质：Retraction / Correction / Expression of concern / Reinstatement",
    "staging.retraction_watch.reason_raw": "撤稿原因原始串，分号分隔多值且**带尾分号**，用 rw_split() 拆分",
    "staging.retraction_watch.country_raw": "作者所属国原始串，分号分隔多值",
    "staging.retraction_watch._source_file": "数据血缘：这条记录来自哪份数据文件",
    # ---------------- staging：OpenAlex ----------------
    "staging.openalex_works.openalex_id": "OpenAlex 作品 ID（已去掉 https://openalex.org/ 前缀）",
    "staging.openalex_works.doi": "DOI，已用与 Retraction Watch 相同的宏规范化",
    "staging.openalex_works.is_retracted": "OpenAlex 的撤稿标记。注意它标记的是**作品**，不是撤稿事件",
    "staging.openalex_works.fwci": "领域加权引用影响力（Field-Weighted Citation Impact）",
    "staging.openalex_work_citations.years_since_publication": (
        "引用年份 − 发表年份。**约 0.77% 为负值** —— "
        "经核查是上游特性（撤稿声明继承了原论文的引用轨迹），不是解析错误，详见 quality.py"
    ),
    "staging.openalex_work_citations.cited_by_count": "该年的被引次数。**OpenAlex 省略被引为 0 的年份**",
    "staging.openalex_authorships.author_id": "OpenAlex 消歧过的作者 ID，比姓名可靠得多",
    "staging.openalex_authorship_institutions.ror": "Research Organization Registry ID，机构的权威唯一标识",
    # ---------------- intermediate ----------------
    "intermediate.retractions_enriched.retraction_latency_days": "撤稿时滞 = 撤稿日期 − 原文发表日期。全局中位数 500 天",
    "intermediate.retractions_enriched.is_paper_mill": "**标签字段**：官方 Reason 含 'Paper Mill'。所有模型验证都用它做正例",
    "intermediate.works_enriched.matched_in_retraction_watch": "是否能在 RW 找到对应案底。实测仅 44.8% 能匹配上",
    "intermediate.work_citation_event_panel.event_time": "相对撤稿的年份（−3…+2）。事件研究的时间轴",
    "intermediate.work_citation_event_panel.cited_by_count": "**已补零**：0 既可能是真实无引用，也可能是 OpenAlex 省略后被补上",
    "intermediate.work_citation_event_panel.has_observed_data": "区分上面两种 0：True = OpenAlex 真实给出，False = 我们补的",
    "intermediate.coauthorship_edges.n_shared_works": "两位作者共同署名的**被撤稿**论文数（边权重）",
    "intermediate.journal_profile.burst_ratio": "峰值年撤稿数 ÷ 撤稿总数，取值 0~1。**实测 AUC 仅 0.497，无区分能力**",
    # ---------------- marts ----------------
    "marts.journal_risk.risk_score": (
        "严重度分（0~100）。**含由 Reason 派生的特征，存在目标泄漏**，"
        "只能用于排序已确认问题的严重程度，不具备预测能力"
    ),
    "marts.journal_risk.structural_score": "结构分（0~100）。零 Reason 派生特征，AUC 0.736，是真正的预测分数",
    "marts.journal_risk.risk_tier": "风险分层：极高/高/中/低。分层会丢失信息，应与原始分数同时使用",
    "marts.retraction_citation_event_study.counterfactual_citations": (
        "反事实引用：用撤稿前 3 年的趋势做 OLS 线性外推得到的'如果没被撤稿会怎样'"
    ),
    "marts.retraction_citation_event_study.citation_gap_pct": "相对反事实的引用变化百分比。撤稿后最大 −71.9%",
    "marts.author_clusters.cluster_risk_score": "社群严重度分。**同样存在目标泄漏**（paper_mill_edge_share 与标签同源）",
    "marts.author_clusters.cluster_structural_score": "社群结构分。**AUC 0.336，反向预测** —— 已知负结果，见 README 发现 10",
    "marts.author_clusters.density": "社群内部密度。**3 人社群中 92.8% 恰好为 1.0（饱和），该区间不含信息**",
    "marts.country_leaderboard.share_of_global_pct": "占全球总记录数的比例。**多值归属，各国之和不等于 100%**",
    "marts.reason_landscape.n_records": "涉及该原因的**记录数**（去重），区别于 n_occurrences（原因出现次数）",
    "staging.load_audit.rows_dropped": "被丢弃的行数。当前：RW 丢弃 149 行完全空白行；OpenAlex 丢弃 0 行",
}

# 表的用途说明
TABLE_PURPOSE: dict[str, str] = {
    "staging.retraction_watch": "Retraction Watch 清洗表。一行一条撤稿记录，已做类型转换与缺失值统一",
    "staging.retraction_reasons": "撤稿原因**展开表**。一行 = 记录 × 原因（一对多展开，行数会膨胀）",
    "staging.retraction_countries": "国家展开表。一行 = 记录 × 国家",
    "staging.retraction_subjects": "学科展开表。一行 = 记录 × 学科",
    "staging.retraction_authors": "作者展开表，保留署名顺序（首/末位在学术上有含义）",
    "staging.retraction_institutions": "机构展开表（自由文本，未做机构消歧）",
    "staging.openalex_works": "OpenAlex 被撤稿作品表。一行一篇作品",
    "staging.openalex_authorships": "署名表。用 range() 下标展开，保证顺序严格等于原始 JSON 数组",
    "staging.openalex_authorship_institutions": "署名的机构归属（三层嵌套展开：作品 → 署名 → 机构）",
    "staging.openalex_work_citations": "**逐年引用轨迹**。因果推断的核心数据，做事件研究的基础",
    "staging.openalex_work_topics": "作品主题展开表（OpenAlex 四层主题体系：domain/field/subfield/topic）",
    "staging.load_audit": "**加载审计表**。记录每次加载读入/保留/丢弃了多少行以及为什么，防止数据静默缺失",
    "intermediate.retractions_enriched": "撤稿记录增强表。合并原因/国家/作者/机构/学科特征（先聚合再 join，避免笛卡尔积放大）",
    "intermediate.journal_year_activity": "期刊 × 年份活动表。**异常检测的正确粒度** —— 只按期刊聚合会把'爆发'平均掉",
    "intermediate.journal_profile": "期刊画像。含爆发度、论文工厂占比、撤稿时滞等",
    "intermediate.author_activity": "作者画像（按姓名）。**姓名非唯一标识，此表只作筛查线索**",
    "intermediate.works_enriched": "**跨源枢纽表**：OpenAlex × Retraction Watch 按 DOI 关联",
    "intermediate.work_citation_event_panel": "**平衡事件面板**。10,883 篇 × 6 时点，强制补零，因果推断的数据底座",
    "intermediate.coauthorship_edges": "合作网络边表。一行一对作者，权重 = 共同署名的被撤稿论文数",
    "marts.retraction_trends": "年度趋势（含同比、累计、3 年移动平均）",
    "marts.journal_risk": "**期刊风险评分**。严重度分与结构分双轨，含分层标签",
    "marts.risk_score_validation": "期刊评分的标签验证结果（AUC）",
    "marts.feature_discriminative_power": "**单变量 AUC**。检测目标泄漏的关键工具 —— paper_mill_share 的 AUC = 1.000",
    "marts.risk_tier_performance": "风险分层的精确率与提升度（lift）",
    "marts.country_leaderboard": "国家/地区撤稿画像，区分独立完成与国际合作",
    "marts.reason_landscape": "撤稿原因全景（含中位时滞与近 5 年趋势）",
    "marts.retraction_citation_event_study": "**事件研究主表**：各时点的实际引用 vs 反事实引用",
    "marts.retraction_citation_event_study_by_group": "分群事件研究：论文工厂 vs 其他撤稿",
    "marts.retraction_citation_penalty_summary": "事件研究结论摘要（含反事实拟合的自检列）",
    "marts.author_clusters": "合作社群画像与评分",
    "marts.author_cluster_members": "社群成员明细",
    "marts.author_cluster_validation": "社群分数的标签验证结果",
}

LAYER_DESCRIPTIONS = {
    "staging": "**清洗层** —— 只做类型转换、缺失值统一、多值展开。不做任何业务判断。",
    "intermediate": "**口径层** —— 引入业务定义、合并多源、构造分析所需的结构。",
    "marts": "**交付层** —— 可直接用于看板与报告的结果表，每张表回答一个明确问题。",
}


def _describe(con: duckdb.DuckDBPyConnection) -> dict[str, list[tuple]]:
    """列出所有表的列信息与行数。"""
    tables = con.execute(
        """
        SELECT table_schema, table_name
        FROM information_schema.tables
        WHERE table_schema IN ('staging', 'intermediate', 'marts')
        ORDER BY table_schema, table_name
        """
    ).fetchall()

    result: dict[str, list[tuple]] = {}
    for schema, table in tables:
        key = f"{schema}.{table}"
        columns = con.execute(
            """
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = ? AND table_name = ?
            ORDER BY ordinal_position
            """,
            [schema, table],
        ).fetchall()
        try:
            count = con.execute(f'SELECT COUNT(*) FROM "{schema}"."{table}"').fetchone()[0]
        except duckdb.Error:
            count = -1
        result[key] = (columns, count)
    return result


def render(con: duckdb.DuckDBPyConnection) -> str:
    info = _describe(con)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    total_tables = len(info)
    total_columns = sum(len(cols) for cols, _ in info.values())
    documented = 0

    lines: list[str] = [
        "# 数据字典",
        "",
        f"> 自动生成于 {now}　|　共 **{total_tables}** 张表、**{total_columns}** 个字段",
        "",
        "本文件由 `scripts/07_generate_data_dictionary.py` 从数据库元数据生成。",
        "**表结构部分永远与代码同步**（因为它就是查询数据库得到的），",
        "语义说明部分由人工维护，未覆盖的字段会被显式标记。",
        "",
        "---",
        "",
        "## 分层说明",
        "",
    ]

    for layer, desc in LAYER_DESCRIPTIONS.items():
        lines.append(f"- **`{layer}`** —— {desc}")

    lines += ["", "---", ""]

    for layer in ("staging", "intermediate", "marts"):
        layer_tables = {k: v for k, v in info.items() if k.startswith(f"{layer}.")}
        if not layer_tables:
            continue

        lines += [f"## {layer}", ""]

        for key, (columns, count) in layer_tables.items():
            _, table = key.split(".", 1)
            purpose = TABLE_PURPOSE.get(key, "⚠ 未填写用途说明")
            lines += [
                f"### `{table}`",
                "",
                f"**行数**：{count:,}　|　**用途**：{purpose}",
                "",
                "| 字段 | 类型 | 可空 | 说明 |",
                "|---|---|---|---|",
            ]
            for name, dtype, nullable in columns:
                semantic = SEMANTICS.get(f"{key}.{name}")
                if semantic:
                    documented += 1
                    note = semantic
                elif name.startswith("_") or name in ("cluster_id", "author_id", "openalex_id"):
                    note = "标识/血缘字段"
                else:
                    note = "—"
                lines.append(f"| `{name}` | {dtype} | {nullable} | {note} |")
            lines.append("")

    coverage = documented / total_columns * 100 if total_columns else 0
    lines += [
        "---",
        "",
        "## 文档覆盖情况",
        "",
        f"- 字段总数：**{total_columns}**",
        f"- 有详细语义说明：**{documented}**（{coverage:.1f}%）",
        "- 其余字段：名称自解释，或属于标识/血缘类字段",
        "",
        "> ⚠ 未覆盖的字段会在表中显示为 `—`。**留空是刻意的** —— "
        "比起编一句看起来像回事但没人验证过的说明，"
        "承认'这里还没写清楚'对使用者更负责任。",
        "",
        "## 关于数据本身的已知问题",
        "",
        "| 问题 | 影响范围 | 处理方式 |",
        "|---|---|---|",
        "| 上游 CSV 含 149 行完全空白行 | Retraction Watch | staging 层剔除，记入 `load_audit` |",
        "| 约 0.77% 的引用年份早于发表年份 | OpenAlex | 上游特性（撤稿声明继承原论文引用），"
        "质量规则降级为 warning |",
        "| OpenAlex 省略被引为 0 的年份 | 引用轨迹 | 事件面板强制补零，并用 `has_observed_data` 标记 |",
        "| 超过一半的被标记作品在 RW 查无案底 | 跨源关联 | 保留为待研究问题，见 README 发现 6 |",
        "| 作者姓名不是唯一标识 | `author_activity` | 网络分析改用 OpenAlex 消歧 ID；该表仅作线索 |",
        "",
    ]

    return "\n".join(lines)


def main() -> int:
    settings = get_settings()
    setup_logging(settings.log_level)
    logger = get_logger("scripts.07_dictionary")

    logger.info("=" * 70)
    logger.info("步骤 7：生成数据字典")
    logger.info("=" * 70)

    con = connect(settings, require_exists=True)
    try:
        content = render(con)
    finally:
        con.close()

    docs_dir = settings.data_dir.parent / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    out = docs_dir / "DATA_DICTIONARY.md"
    out.write_text(content, encoding="utf-8")

    lines_count = content.count("\n") + 1
    logger.info("已生成：%s（%d 行）", out, lines_count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
