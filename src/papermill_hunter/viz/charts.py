"""图表生成：把分析结论画成可以直接放进 README 和简历的图。

【为什么选 matplotlib 而不是只做 Streamlit 看板】
    两者解决的是不同问题，本模块两者都做：
      - **静态图（matplotlib → PNG）**：可以嵌进 README、贴进简历 PDF、
        发给面试官。这是"作品集"的载体。
      - **交互看板（Streamlit）**：面试时现场演示、让面试官自己筛选维度。
        这是"演示"的载体。

    只有看板没有静态图，你的项目在别人点开仓库的第一眼是"什么都没有"；
    只有静态图没有看板，面试时无法互动。
    两者互补，缺一不可。

【中文字体的坑】
    matplotlib 默认字体不含中文，直接画中文会显示成一排方框（豆腐块），
    而且**不会报错** —— 你只有在打开图片时才发现全是方块。
    更隐蔽的是负号：即使字体配置正确，`axes.unicode_minus` 默认用 Unicode 减号，
    中文环境下同样会渲染成方块，于是「−23.5%」变成「□23.5%」。
    所以正确的配置必须两行都写。
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import matplotlib

matplotlib.use("Agg")  # 无界面后端：只出图不弹窗，服务器/CI 环境也能跑

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib import font_manager
from matplotlib.figure import Figure

from papermill_hunter.config import Settings
from papermill_hunter.logging_conf import get_logger

logger = get_logger(__name__)

# 优先使用的中文字体，按可用性依次尝试。
# 覆盖 Windows / macOS / Linux 三类系统，让项目在任何机器上都能出图。
_FONT_CANDIDATES = (
    "Microsoft YaHei",  # Windows 默认中文 UI 字体，字形最好看
    "SimHei",  # Windows 黑体，兼容性最好
    "PingFang SC",  # macOS
    "Hiragino Sans GB",  # macOS
    "Noto Sans CJK SC",  # Linux 常见
    "Source Han Sans SC",
    "WenQuanYi Micro Hei",
    "Arial Unicode MS",
)

# 统一的配色。集中定义而不是散落各处，是为了让所有图的视觉语言一致 ——
# "论文工厂"永远是同一个颜色，读者不需要重新学习图例。
COLOR_PAPER_MILL = "#d62728"  # 红：问题
COLOR_OTHER = "#7f7f7f"  # 灰：对照
COLOR_ACTUAL = "#1f77b4"  # 蓝：实际观测
COLOR_COUNTERFACTUAL = "#ff7f0e"  # 橙：反事实
COLOR_ACCENT = "#2ca02c"  # 绿：正面/通过
COLOR_LEAKY = "#d62728"  # 红：有泄漏/不可信
COLOR_CREDIBLE = "#2ca02c"  # 绿：可信

DPI = 150


def setup_chinese_font() -> str:
    """配置 matplotlib 的中文字体，返回实际使用的字体名。"""
    available = {f.name for f in font_manager.fontManager.ttflist}

    for name in _FONT_CANDIDATES:
        if name in available:
            plt.rcParams["font.sans-serif"] = [name, "DejaVu Sans"]
            # 关键：关掉 Unicode 减号，否则负号会渲染成方框
            plt.rcParams["axes.unicode_minus"] = False
            plt.rcParams["figure.autolayout"] = True
            plt.rcParams["axes.grid"] = True
            plt.rcParams["grid.alpha"] = 0.25
            plt.rcParams["axes.spines.top"] = False
            plt.rcParams["axes.spines.right"] = False
            logger.info("matplotlib 中文字体：%s", name)
            return name

    logger.warning(
        "未找到任何中文字体，图表中的中文将显示为方框。请安装以下任一字体：%s",
        "、".join(_FONT_CANDIDATES[:4]),
    )
    plt.rcParams["axes.unicode_minus"] = False
    return ""


def _save(fig: Figure, settings: Settings, filename: str) -> Path:
    """保存图表并关闭，避免内存泄漏（批量出图时这点很关键）。"""
    settings.figures_dir.mkdir(parents=True, exist_ok=True)
    path = settings.figures_dir / filename
    fig.savefig(path, dpi=DPI, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    logger.info("  已生成 %s", path.name)
    return path


# ======================================================================
# 图 1：撤稿趋势
# ======================================================================
def plot_retraction_trends(con: duckdb.DuckDBPyConnection, settings: Settings) -> Path:
    """年度撤稿量与论文工厂占比的双轴图。"""
    df = con.execute(
        """
        SELECT year, n_retractions, n_paper_mill, paper_mill_share, ma3_retractions
        FROM marts.retraction_trends
        WHERE year BETWEEN 2000 AND 2025
        ORDER BY year
        """
    ).df()

    fig, ax1 = plt.subplots(figsize=(11, 5.5))

    # 用柱状图表示年度绝对量，用折线表示论文工厂占比 —— 两个量纲不同的指标
    # 放在同一张图上时，双轴比"归一化后叠在一起"更容易读。
    ax1.bar(df["year"], df["n_retractions"], color="#4c72b0", alpha=0.85, label="年度撤稿数")
    ax1.plot(
        df["year"],
        df["ma3_retractions"],
        color="#c44e52",
        linewidth=2.2,
        label="3 年移动平均",
        zorder=5,
    )
    ax1.set_xlabel("年份")
    ax1.set_ylabel("撤稿数量（篇）")
    ax1.set_xticks(range(2000, 2026, 5))

    ax2 = ax1.twinx()
    ax2.plot(
        df["year"],
        df["paper_mill_share"] * 100,
        color=COLOR_PAPER_MILL,
        linewidth=1.8,
        linestyle="--",
        marker="o",
        markersize=4,
        label="明确标注为论文工厂的比例",
    )
    ax2.set_ylabel("论文工厂占比（%）", color=COLOR_PAPER_MILL)
    ax2.tick_params(axis="y", labelcolor=COLOR_PAPER_MILL)
    ax2.grid(False)

    # 标注 2023 年这个最显著的异常 —— 一张图必须有一个"看点"，
    # 否则读者看完只会说"哦，在涨"。标注就是告诉读者"该看哪里"。
    peak = df.loc[df["year"] == 2023]
    if not peak.empty:
        value = int(peak["n_retractions"].iloc[0])
        ax1.annotate(
            f"2023 年：{value:,} 篇\n（2022 年的 2 倍以上）",
            xy=(2023, value),
            xytext=(2014.5, value * 0.92),
            fontsize=11,
            color="#8b0000",
            arrowprops={"arrowstyle": "->", "color": "#8b0000", "lw": 1.6},
        )

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left", fontsize=9)

    ax1.set_title("全球撤稿趋势（2000–2025）", fontsize=14, fontweight="bold", pad=14)
    return _save(fig, settings, "01_retraction_trends.png")


# ======================================================================
# 图 2：国家排行
# ======================================================================
def plot_country_leaderboard(con: duckdb.DuckDBPyConnection, settings: Settings) -> Path:
    """撤稿量 Top15 国家/地区，并拆出"国际合作"与"独立完成"。"""
    df = con.execute(
        """
        SELECT country, n_retractions, n_as_sole_country, paper_mill_share
        FROM marts.country_leaderboard
        ORDER BY n_retractions DESC
        LIMIT 15
        """
    ).df()

    df = df.sort_values("n_retractions")
    fig, ax = plt.subplots(figsize=(10.5, 6.5))

    y = np.arange(len(df))
    # 堆叠条形图：总量的构成比总量本身更有信息量。
    # "独立完成"和"国际合作"是两种不同的行为，堆在一起能同时看到规模和结构。
    solo = df["n_as_sole_country"]
    coop = df["n_retractions"] - df["n_as_sole_country"]

    ax.barh(y, solo, color="#4c72b0", label="本国独立完成")
    ax.barh(y, coop, left=solo, color="#dd8452", label="涉及国际合作")

    ax.set_yticks(y)
    ax.set_yticklabels(df["country"])
    ax.set_xlabel("撤稿数量（篇）")

    for yi, (total, share) in enumerate(zip(df["n_retractions"], df["paper_mill_share"], strict=True)):
        ax.text(
            total + max(df["n_retractions"]) * 0.012,
            yi,
            f"{total:,}（工厂标注 {share * 100:.0f}%）",
            va="center",
            fontsize=8.5,
        )

    ax.set_xlim(0, max(df["n_retractions"]) * 1.30)
    ax.legend(loc="lower right", fontsize=9)
    ax.set_title(
        "撤稿量 Top15 国家/地区\n（口径：按作者所属国统计，国际合作论文会重复计入多国）",
        fontsize=13,
        fontweight="bold",
        pad=14,
    )
    return _save(fig, settings, "02_country_leaderboard.png")


# ======================================================================
# 图 3：撤稿原因
# ======================================================================
def plot_reason_landscape(con: duckdb.DuckDBPyConnection, settings: Settings) -> Path:
    """撤稿原因 Top15 —— 用颜色把"系统性造假"与"一般性问题"区分开。"""
    df = con.execute(
        """
        SELECT reason, n_records, median_latency_days, china_share
        FROM marts.reason_landscape
        ORDER BY n_records DESC
        LIMIT 15
        """
    ).df()

    # 人为分组：与"系统性造假/流程失守"直接相关的原因标红。
    # 这个分组是**研究者的判断**，不是数据里带出来的字段 ——
    # 在图上用颜色区分时必须靠图例说明，否则读者会以为这是数据自带的分类。
    fraud_keywords = ("Paper Mill", "Peer Review", "Rogue Editor", "Computer-Generated", "Image")
    df["is_systemic"] = df["reason"].str.contains("|".join(fraud_keywords), case=False, regex=True)

    df = df.sort_values("n_records")
    colors = [COLOR_PAPER_MILL if flag else "#8c8c8c" for flag in df["is_systemic"]]

    fig, ax = plt.subplots(figsize=(11, 7))
    y = np.arange(len(df))
    ax.barh(y, df["n_records"], color=colors)
    ax.set_yticks(y)
    ax.set_yticklabels([r[:44] for r in df["reason"]], fontsize=9)
    ax.set_xlabel("涉及的撤稿记录数")

    for yi, (n, days) in enumerate(zip(df["n_records"], df["median_latency_days"], strict=True)):
        label = f"{n:,}"
        if pd.notna(days):
            label += f"   中位时滞 {days:.0f} 天"
        ax.text(n + max(df["n_records"]) * 0.012, yi, label, va="center", fontsize=8)

    ax.set_xlim(0, max(df["n_records"]) * 1.34)

    from matplotlib.patches import Patch

    ax.legend(
        handles=[
            Patch(color=COLOR_PAPER_MILL, label="与系统性造假 / 流程失守直接相关（研究者归类）"),
            Patch(color="#8c8c8c", label="其他原因"),
        ],
        loc="lower right",
        fontsize=9,
    )
    ax.set_title("撤稿原因 Top15：哪类问题最普遍", fontsize=14, fontweight="bold", pad=14)
    return _save(fig, settings, "03_reason_landscape.png")


# ======================================================================
# 图 4：期刊风险散点
# ======================================================================
def plot_journal_risk_map(con: duckdb.DuckDBPyConnection, settings: Settings) -> Path:
    """期刊风险地图：横轴撤稿量、纵轴论文工厂占比、颜色为结构分。

    这张图的设计意图是**让"规模"和"行为"分离开**：
    横轴代表规模，纵轴代表行为，两者的组合才是风险。
    只大不坏（右下）和只坏不大（左上）是完全不同的两类问题。
    """
    df = con.execute(
        """
        SELECT journal, n_retractions, paper_mill_share, burst_ratio,
               risk_score, structural_score, risk_tier
        FROM marts.journal_risk
        WHERE n_retractions >= 5
        """
    ).df()

    fig, ax = plt.subplots(figsize=(11, 6.5))

    scatter = ax.scatter(
        df["n_retractions"],
        df["paper_mill_share"] * 100,
        c=df["structural_score"],
        s=np.clip(df["n_retractions"] * 1.6, 18, 520),
        cmap="RdYlGn_r",  # 反转色阶：红色 = 结构分低（越"正常"越小？）
        alpha=0.72,
        edgecolors="white",
        linewidths=0.6,
    )

    ax.set_xscale("log")
    ax.set_xlabel("该期刊的撤稿总量（对数刻度）")
    ax.set_ylabel("明确标注为「论文工厂」的撤稿占比（%）")

    # 标注论文工厂占比最高的几个期刊
    top = df.nlargest(6, "paper_mill_share")
    for row in top.itertuples(index=False):
        ax.annotate(
            row.journal[:30],
            xy=(row.n_retractions, row.paper_mill_share * 100),
            xytext=(8, 8),
            textcoords="offset points",
            fontsize=8,
            color="#333333",
            arrowprops={"arrowstyle": "-", "color": "#999999", "lw": 0.7},
        )

    cbar = fig.colorbar(scatter, ax=ax)
    cbar.set_label("结构分（不含撤稿原因信息，越高表示撤稿行为越异常）", fontsize=9)

    ax.set_title(
        "期刊风险地图：规模 vs 行为（气泡面积 = 撤稿总量）",
        fontsize=14,
        fontweight="bold",
        pad=14,
    )
    return _save(fig, settings, "04_journal_risk_map.png")


# ======================================================================
# 图 5：事件研究（头牌图）
# ======================================================================
def plot_event_study(con: duckdb.DuckDBPyConnection, settings: Settings) -> Path:
    """撤稿前后引用轨迹：实际 vs 反事实。这是本项目最有说服力的一张图。"""
    df = con.execute(
        """
        SELECT event_time, n_works, mean_citations, counterfactual_citations,
               citation_gap_pct, is_post_retraction
        FROM marts.retraction_citation_event_study
        ORDER BY event_time
        """
    ).df()

    fig, ax = plt.subplots(figsize=(11, 6.2))

    x = df["event_time"]
    ax.plot(
        x,
        df["mean_citations"],
        marker="o",
        markersize=9,
        linewidth=2.6,
        color=COLOR_ACTUAL,
        label="实际观测到的平均被引",
        zorder=5,
    )
    ax.plot(
        x,
        df["counterfactual_citations"],
        marker="s",
        markersize=8,
        linewidth=2.4,
        linestyle="--",
        color=COLOR_COUNTERFACTUAL,
        label="反事实估计（按撤稿前趋势外推）",
        zorder=4,
    )

    # 用阴影标出"撤稿造成的引用损失" —— 面积本身就是信息，
    # 比在旁边写一个数字更容易让人一眼记住。
    ax.fill_between(
        x,
        df["mean_citations"],
        df["counterfactual_citations"],
        where=df["counterfactual_citations"] >= df["mean_citations"],
        color=COLOR_PAPER_MILL,
        alpha=0.16,
        label="撤稿造成的引用损失",
    )

    ax.axvline(-0.5, color="#444444", linestyle=":", linewidth=2)
    ax.text(-0.42, ax.get_ylim()[1] * 0.94, "撤稿发生", fontsize=11, color="#444444")

    for row in df.itertuples(index=False):
        if row.event_time >= 0:
            ax.annotate(
                f"{row.citation_gap_pct:+.1f}%",
                xy=(row.event_time, row.mean_citations),
                xytext=(0, -22),
                textcoords="offset points",
                ha="center",
                fontsize=10,
                color="#8b0000",
                fontweight="bold",
            )

    ax.set_xticks(list(x))
    ax.set_xticklabels([f"{int(t):+d}" if t != 0 else "撤稿当年" for t in x])
    # 右侧留出空间，否则最后一个时点的百分比标注会被裁掉。
    # （第一版就裁掉了 -71.9%，而代码不会报错 —— 只能靠打开图看。）
    ax.set_xlim(float(x.min()) - 0.35, float(x.max()) + 0.55)
    ax.set_xlabel("相对撤稿的年份")
    ax.set_ylabel("平均被引次数")
    ax.legend(fontsize=10, loc="upper left")
    ax.set_title(
        f"撤稿的引用代价：逐年放大\n（平衡面板，{int(df['n_works'].iloc[0]):,} 篇论文 × {len(df)} 个时点）",
        fontsize=14,
        fontweight="bold",
        pad=14,
    )
    return _save(fig, settings, "05_citation_event_study.png")


# ======================================================================
# 图 6：分群对比
# ======================================================================
def plot_event_study_by_group(con: duckdb.DuckDBPyConnection, settings: Settings) -> Path:
    """论文工厂 vs 其他撤稿的引用代价对比。"""
    df = con.execute(
        """
        SELECT group_name, event_time, citation_gap_pct, n_works
        FROM marts.retraction_citation_event_study_by_group
        WHERE event_time >= 0
        ORDER BY group_name, event_time
        """
    ).df()

    fig, ax = plt.subplots(figsize=(10, 5.8))
    palette = {"论文工厂": COLOR_PAPER_MILL, "其他撤稿": COLOR_OTHER}

    for group, sub in df.groupby("group_name"):
        ax.plot(
            sub["event_time"],
            sub["citation_gap_pct"],
            marker="o",
            markersize=9,
            linewidth=2.6,
            color=palette.get(group, "#333333"),
            label=f"{group}（n={int(sub['n_works'].iloc[0]):,} 篇）",
        )
        for row in sub.itertuples(index=False):
            ax.annotate(
                f"{row.citation_gap_pct:.1f}%",
                xy=(row.event_time, row.citation_gap_pct),
                xytext=(0, 10),
                textcoords="offset points",
                ha="center",
                fontsize=9.5,
                color=palette.get(group, "#333333"),
            )

    # 0% 参考线：没有这条线，读者无法判断"−50%"到底是多是少
    ax.axhline(0, color="#444444", linewidth=1.2, linestyle="-")
    ax.set_xticks(sorted(df["event_time"].unique()))
    ax.set_xticklabels([f"{int(t):+d}" if t != 0 else "撤稿当年" for t in sorted(df["event_time"].unique())])
    ax.set_xlabel("相对撤稿的年份")
    ax.set_ylabel("相对反事实的引用变化（%）")
    ax.legend(fontsize=10, loc="lower left")
    ax.set_title(
        "论文工厂的论文，受到的引用惩罚更重",
        fontsize=14,
        fontweight="bold",
        pad=14,
    )
    return _save(fig, settings, "06_event_study_by_group.png")


# ======================================================================
# 图 7：特征区分力（含目标泄漏的实证）
# ======================================================================
def plot_feature_power(con: duckdb.DuckDBPyConnection, settings: Settings) -> Path:
    """单变量 AUC 对比 —— 让"目标泄漏"这件事一目了然。"""
    df = con.execute(
        """
        SELECT feature, feature_group, auc, auc_abs
        FROM marts.feature_discriminative_power
        ORDER BY auc_abs DESC
        """
    ).df()

    df = df.sort_values("auc_abs")
    colors = [COLOR_LEAKY if g == "reason_derived" else COLOR_CREDIBLE for g in df["feature_group"]]

    fig, ax = plt.subplots(figsize=(10.5, 6))
    y = np.arange(len(df))
    ax.barh(y, df["auc_abs"], color=colors)
    ax.axvline(0.5, color="#444444", linestyle="--", linewidth=1.6)
    # 标签放在最下方而不是最上方 —— 最上方会压住 paper_mill_share 那根柱子
    ax.text(0.508, -1.15, "0.5 = 与随机猜测无异", fontsize=9, color="#444444")

    ax.set_yticks(y)
    ax.set_yticklabels(df["feature"], fontsize=9.5)
    ax.set_xlabel("单变量 AUC（区分'有论文工厂记录'与'没有'的能力）")
    ax.set_xlim(0, 1.10)

    for yi, value in enumerate(df["auc_abs"]):
        ax.text(value + 0.012, yi, f"{value:.3f}", va="center", fontsize=9)

    from matplotlib.patches import Patch

    ax.legend(
        handles=[
            Patch(color=COLOR_LEAKY, label="由 Reason 字段派生 → 与标签同源，AUC 被高估（目标泄漏）"),
            Patch(color=COLOR_CREDIBLE, label="结构性特征 → 可用于预测"),
        ],
        loc="lower right",
        fontsize=9,
    )
    ax.set_title(
        "哪些特征真的能识别论文工厂？\npaper_mill_share 的 AUC = 1.000 —— 完美得可疑，因为它在抄答案",
        fontsize=13.5,
        fontweight="bold",
        pad=14,
    )
    return _save(fig, settings, "07_feature_discriminative_power.png")


# ======================================================================
# 图 8：撤稿时滞分布
# ======================================================================
def plot_retraction_latency(con: duckdb.DuckDBPyConnection, settings: Settings) -> Path:
    """撤稿时滞的分布与年代变化 —— 回答"纠错机制有多快"。"""
    detail = con.execute(
        """
        SELECT retraction_latency_days / 365.25 AS latency_years
        FROM intermediate.retractions_enriched
        WHERE retraction_latency_days IS NOT NULL
          AND retraction_latency_days BETWEEN 0 AND 12000
        """
    ).df()

    by_year = con.execute(
        """
        SELECT year, median_latency_days / 365.25 AS median_years, n_retractions
        FROM marts.retraction_trends
        WHERE year BETWEEN 2005 AND 2025 AND median_latency_days IS NOT NULL
        ORDER BY year
        """
    ).df()

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5.2))

    ax1.hist(detail["latency_years"], bins=60, color="#4c72b0", alpha=0.85, edgecolor="white", linewidth=0.4)
    median_years = detail["latency_years"].median()
    ax1.axvline(median_years, color=COLOR_PAPER_MILL, linestyle="--", linewidth=2)
    ax1.text(
        median_years + 0.15,
        ax1.get_ylim()[1] * 0.85,
        f"中位数 {median_years:.2f} 年\n（约 {median_years * 365.25:.0f} 天）",
        color=COLOR_PAPER_MILL,
        fontsize=10.5,
    )
    ax1.set_xlabel("撤稿时滞（年，撤稿日期 − 发表日期）")
    ax1.set_ylabel("论文数")
    ax1.set_title("撤稿时滞分布：造假平均要 1.4 年才被发现", fontsize=12, fontweight="bold")

    ax2.plot(
        by_year["year"], by_year["median_years"], marker="o", markersize=5, linewidth=2.2, color="#c44e52"
    )
    ax2.set_xlabel("撤稿年份")
    ax2.set_ylabel("中位撤稿时滞（年）")
    ax2.set_title("纠错速度的年代变化", fontsize=12, fontweight="bold")

    fig.suptitle("学术纠错机制有多快？", fontsize=14.5, fontweight="bold", y=1.03)
    return _save(fig, settings, "08_retraction_latency.png")


# ======================================================================
# 图 9：跨源口径缺口
# ======================================================================
def plot_cross_source_gap(con: duckdb.DuckDBPyConnection, settings: Settings) -> Path:
    """OpenAlex 与 Retraction Watch 的口径缺口。"""
    row = con.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(matched_in_retraction_watch::INT) AS matched,
            COUNT(*) - SUM(matched_in_retraction_watch::INT) AS unmatched
        FROM intermediate.works_enriched
        """
    ).fetchone()
    total, matched, unmatched = int(row[0]), int(row[1]), int(row[2])

    by_year = con.execute(
        """
        SELECT publication_year AS year,
               COUNT(*) AS total,
               SUM(matched_in_retraction_watch::INT) AS matched
        FROM intermediate.works_enriched
        WHERE publication_year BETWEEN 2000 AND 2025
        GROUP BY publication_year
        ORDER BY publication_year
        """
    ).df()

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5.4), gridspec_kw={"width_ratios": [1, 1.7]})

    # 左图：占比环
    sizes = [matched, unmatched]
    ax1.pie(
        sizes,
        labels=[
            f"能在 RW 找到案底\n{matched:,}（{matched / total * 100:.1f}%）",
            f"仅 OpenAlex 标记\n{unmatched:,}（{unmatched / total * 100:.1f}%）",
        ],
        colors=["#4c72b0", COLOR_PAPER_MILL],
        autopct="",
        startangle=90,
        wedgeprops={"width": 0.42, "edgecolor": "white", "linewidth": 2},
        textprops={"fontsize": 11},
    )
    ax1.set_title("总体关联率", fontsize=12, fontweight="bold")

    # 右图：按年份的关联率变化 —— 缺口是否在扩大？
    by_year["match_rate"] = by_year["matched"] / by_year["total"] * 100
    ax2.plot(by_year["year"], by_year["match_rate"], marker="o", markersize=5, linewidth=2.2, color="#4c72b0")
    ax2.axhline(100, color="#999999", linestyle=":", linewidth=1.4)
    ax2.set_ylim(0, 105)
    ax2.set_xlabel("论文发表年份")
    ax2.set_ylabel("能在 RW 找到案底的比例（%）")
    ax2.set_title("关联率随发表年份的变化", fontsize=12, fontweight="bold")

    fig.suptitle(
        f"两个数据源的口径缺口：{unmatched:,} 篇被标记撤稿的作品在权威案底库查无记录",
        fontsize=13.5,
        fontweight="bold",
        y=1.02,
    )
    return _save(fig, settings, "09_cross_source_gap.png")


# ======================================================================
# 图 10：图指标的规模陷阱
# ======================================================================
def plot_density_saturation(con: duckdb.DuckDBPyConnection, settings: Settings) -> Path:
    """density 在小社群上饱和 —— 记录一个真实的指标设计缺陷。"""
    df = con.execute(
        """
        SELECT n_authors,
               COUNT(*) AS n_clusters,
               AVG(density) AS avg_density,
               AVG(CASE WHEN density >= 0.999 THEN 1.0 ELSE 0.0 END) AS saturated_share
        FROM marts.author_clusters
        WHERE n_authors BETWEEN 3 AND 15
        GROUP BY n_authors
        ORDER BY n_authors
        """
    ).df()

    fig, ax1 = plt.subplots(figsize=(10.5, 5.6))

    ax1.plot(
        df["n_authors"],
        df["avg_density"],
        marker="o",
        markersize=7,
        linewidth=2.4,
        color="#4c72b0",
        label="平均密度",
    )
    ax1.set_xlabel("社群规模（作者人数）")
    ax1.set_ylabel("内部密度", color="#4c72b0")
    ax1.tick_params(axis="y", labelcolor="#4c72b0")
    ax1.set_ylim(0, 1.08)

    ax2 = ax1.twinx()
    ax2.bar(
        df["n_authors"],
        df["saturated_share"] * 100,
        color=COLOR_PAPER_MILL,
        alpha=0.30,
        label="密度恰好 = 1.0 的社群占比",
    )
    ax2.set_ylabel("密度饱和比例（%）", color=COLOR_PAPER_MILL)
    ax2.tick_params(axis="y", labelcolor=COLOR_PAPER_MILL)
    ax2.set_ylim(0, 108)
    ax2.grid(False)

    three = df[df["n_authors"] == 3]
    if not three.empty:
        rate = float(three["saturated_share"].iloc[0]) * 100
        ax1.annotate(
            f"3 人社群中 {rate:.1f}% 的密度恰好为 1.0\n"
            "（3 个节点最多只有 1 个三角形）\n→ density 在该区间几乎不含信息",
            xy=(3, float(three["avg_density"].iloc[0])),
            xytext=(4.6, 0.55),
            fontsize=10,
            color="#8b0000",
            arrowprops={"arrowstyle": "->", "color": "#8b0000", "lw": 1.6},
        )

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="lower right", fontsize=9)

    ax1.set_title(
        "一个真实的指标设计缺陷：density 会随社群规模饱和",
        fontsize=13.5,
        fontweight="bold",
        pad=14,
    )
    return _save(fig, settings, "10_density_saturation.png")


# ======================================================================
# 统一入口
# ======================================================================
ALL_CHARTS = (
    plot_retraction_trends,
    plot_country_leaderboard,
    plot_reason_landscape,
    plot_journal_risk_map,
    plot_event_study,
    plot_event_study_by_group,
    plot_feature_power,
    plot_retraction_latency,
    plot_cross_source_gap,
    plot_density_saturation,
)


def generate_all(con: duckdb.DuckDBPyConnection, settings: Settings) -> list[Path]:
    """依次生成全部图表。单张图失败不会中断整个流程。"""
    setup_chinese_font()
    settings.figures_dir.mkdir(parents=True, exist_ok=True)

    paths: list[Path] = []
    for func in ALL_CHARTS:
        try:
            paths.append(func(con, settings))
        except Exception as exc:  # noqa: BLE001
            # 一张图出错不该让整份报告出不来。
            # 但必须把异常打出来 —— 静默跳过会让你以为"图都生成了"。
            logger.error("生成图表 %s 失败：%s: %s", func.__name__, type(exc).__name__, exc)
    return paths
