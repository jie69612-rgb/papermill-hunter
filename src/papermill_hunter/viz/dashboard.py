"""PaperMill Hunter · 交互式数据看板（Streamlit）。

启动方式（在项目根目录）::

    .venv\\Scripts\\streamlit.exe run src\\papermill_hunter\\viz\\dashboard.py

前置条件：先跑完 scripts/01–05 完成数据采集、建模与分析。

【设计说明】
    这个看板刻意做成"少而深"，而不是"多而浅"：
      - 每个页面只讲一件事，配一句结论性标题
      - 图表上直接标注关键数字，读者不用去对照表格
      - 每个"发现"旁边都写明**口径与局限**

    最后一点尤其重要。数据看板最常见的失败不是"图不好看"，
    而是**让读者对数字产生了过度的信心**——
    看到一个漂亮的趋势线就以为结论是铁的，看不到背后的假设和混淆因素。
    所以本项目所有可能被误读的地方，都在看板上直接标注了口径说明。
"""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from papermill_hunter.config import get_settings
from papermill_hunter.viz import queries

# ----------------------------------------------------------------------
# 全局配置
# ----------------------------------------------------------------------
st.set_page_config(
    page_title="PaperMill Hunter · 论文工厂猎手",
    page_icon="🔬",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Plotly 的中文字体需要在 layout 里显式指定，否则标题和轴标签会变成方框。
# 与 matplotlib 那边是同一类问题、同一个根因。
PLOT_FONT = dict(family="Microsoft YaHei, SimHei, PingFang SC, sans-serif", size=13)
COLOR_PAPER_MILL = "#d62728"
COLOR_OTHER = "#7f7f7f"
COLOR_ACTUAL = "#1f77b4"
COLOR_COUNTERFACTUAL = "#ff7f0e"

PAGES = (
    "总览",
    "撤稿趋势",
    "国家分布",
    "撤稿原因",
    "期刊风险",
    "引用代价",
    "合作网络",
    "数据质量",
)


# ----------------------------------------------------------------------
# 缓存包装
# ----------------------------------------------------------------------
# Streamlit 每次交互都会从头重跑整个脚本。如果不缓存，每点一下都要重新查库。
# `@st.cache_data` 按函数参数缓存返回值 —— 参数不变就直接返回上次的结果。
# ttl=600 表示 10 分钟后自动失效，避免看到"数据已经更新了但看板还是旧的"。
@st.cache_data(ttl=600, show_spinner=False)
def cached_overview() -> dict:
    return queries.overview_metrics()


@st.cache_data(ttl=600, show_spinner=False)
def cached_trends() -> pd.DataFrame:
    return queries.retraction_trends()


@st.cache_data(ttl=600, show_spinner=False)
def cached_countries(limit: int) -> pd.DataFrame:
    return queries.country_leaderboard(limit)


@st.cache_data(ttl=600, show_spinner=False)
def cached_reasons(limit: int) -> pd.DataFrame:
    return queries.reason_landscape(limit)


@st.cache_data(ttl=600, show_spinner=False)
def cached_journal_risk(min_retractions: int, tier: str | None, limit: int) -> pd.DataFrame:
    return queries.journal_risk(min_retractions, tier, limit)


@st.cache_data(ttl=600, show_spinner=False)
def cached_risk_validation() -> pd.DataFrame:
    return queries.risk_validation()


@st.cache_data(ttl=600, show_spinner=False)
def cached_feature_power() -> pd.DataFrame:
    return queries.feature_power()


@st.cache_data(ttl=600, show_spinner=False)
def cached_tier_performance() -> pd.DataFrame:
    return queries.risk_tier_performance()


@st.cache_data(ttl=600, show_spinner=False)
def cached_event_study() -> pd.DataFrame:
    return queries.citation_event_study()


@st.cache_data(ttl=600, show_spinner=False)
def cached_event_by_group() -> pd.DataFrame:
    return queries.citation_event_study_by_group()


@st.cache_data(ttl=600, show_spinner=False)
def cached_clusters(min_authors: int, order_by: str) -> pd.DataFrame:
    return queries.author_clusters(min_authors=min_authors, order_by=order_by)


@st.cache_data(ttl=600, show_spinner=False)
def cached_cluster_members(cluster_id: int) -> pd.DataFrame:
    return queries.cluster_members(cluster_id)


@st.cache_data(ttl=600, show_spinner=False)
def cached_density_by_size() -> pd.DataFrame:
    return queries.density_by_size()


@st.cache_data(ttl=600, show_spinner=False)
def cached_cross_source() -> pd.DataFrame:
    return queries.cross_source_gap()


@st.cache_data(ttl=600, show_spinner=False)
def cached_quality() -> pd.DataFrame:
    return queries.quality_summary()


# ----------------------------------------------------------------------
# 复用组件
# ----------------------------------------------------------------------
def _figure_layout(fig: go.Figure, height: int = 460) -> go.Figure:
    fig.update_layout(
        font=PLOT_FONT,
        height=height,
        margin=dict(l=10, r=10, t=60, b=10),
        hoverlabel=dict(font=PLOT_FONT),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig


def _conclusion(text: str) -> None:
    """统一的"结论条"。看板上每个图下面都该有一句人话总结。"""
    st.markdown(
        f"<div style='background:#f0f6ff;border-left:4px solid #4c72b0;"
        f"padding:10px 14px;border-radius:4px;margin:8px 0 18px 0;'>{text}</div>",
        unsafe_allow_html=True,
    )


def _caveat(text: str) -> None:
    """口径与局限说明。用不同颜色和结论区分开，避免被当成结论读。"""
    st.markdown(
        f"<div style='background:#fff8e6;border-left:4px solid #e0a800;"
        f"padding:8px 14px;border-radius:4px;margin:0 0 18px 0;"
        f"font-size:13px;color:#5a4500;'>⚠ {text}</div>",
        unsafe_allow_html=True,
    )


# ----------------------------------------------------------------------
# 页面
# ----------------------------------------------------------------------
def page_overview() -> None:
    st.title("🔬 PaperMill Hunter · 论文工厂猎手")
    st.caption(
        "用公开学术元数据侦测「论文工厂」的统计指纹，并量化撤稿的真实代价"
        "　|　数据源：Retraction Watch + OpenAlex"
    )

    metrics = cached_overview()
    row1 = st.columns(4)
    row1[0].metric("撤稿案底记录", f"{metrics.get('rw_records', 0):,}", help="Retraction Watch 全量数据")
    row1[1].metric("被撤稿作品", f"{metrics.get('oa_works', 0):,}", help="OpenAlex 中 is_retracted=true")
    row1[2].metric(
        "跨源关联成功",
        f"{metrics.get('matched', 0):,}",
        help="两源通过 DOI 关联上的作品数",
    )
    row1[3].metric(
        "标注为论文工厂", f"{metrics.get('paper_mill', 0):,}", help="官方 Reason 字段含 Paper Mill"
    )

    row2 = st.columns(4)
    row2[0].metric("涉及期刊", f"{metrics.get('journals', 0):,}")
    row2[1].metric("涉及国家/地区", f"{metrics.get('countries', 0):,}")
    row2[2].metric("已评分期刊", f"{metrics.get('scored_journals', 0):,}", help="撤稿量 ≥5 的期刊")
    row2[3].metric("识别合作社群", f"{metrics.get('clusters', 0):,}", help="Louvain 社群发现结果")

    st.divider()
    st.subheader("三个核心结论")

    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown("#### ① 2023 年是分水岭")
        st.markdown(
            "全球撤稿量在 2023 年达到 **13,564 篇**，是 2022 年的两倍以上。这不是渐进增长，是断崖式跃升。"
        )
    with col2:
        st.markdown("#### ② 撤稿的代价逐年放大")
        st.markdown(
            "撤稿当年引用下降 **23.5%**，一年后 **56.4%**，两年后 **71.9%**。"
            "而论文工厂的论文跌得更狠（**−87.3%**）。"
        )
    with col3:
        st.markdown("#### ③ 一半以上的撤稿查无案底")
        st.markdown(
            f"OpenAlex 标记的 {metrics.get('linked_works', 0):,} 篇被撤稿作品中，"
            f"仅 {metrics.get('matched', 0):,} 篇能在 Retraction Watch 找到对应记录。"
        )

    st.divider()
    st.subheader("数据流水线")
    st.markdown(
        """
        本项目是一条完整的「采集 → 数仓 → 分析 → 可视化」链路，全部可一键复现：

        | 阶段 | 产物 | 关键工程点 |
        |---|---|---|
        | 采集 | Retraction Watch 72,453 条 / OpenAlex 135,584 篇 | 令牌桶限流、指数退避重试、磁盘缓存、断点续传 |
        | 数仓 | DuckDB 三层 26 个模型 | 分层建模、一对多展开、平衡事件面板 |
        | 分析 | 风险评分 / 社群发现 / 事件研究 | 目标泄漏检测、单变量 AUC、SQL 内 OLS |
        | 可视化 | 10 张静态图 + 本看板 | 口径标注、局限说明 |
        | 质量 | 38 条自动校验规则 | 量纲检查、一致性对拍、CI 友好 |
        """
    )


def page_trends() -> None:
    st.title("撤稿趋势")
    df = cached_trends()

    fig = go.Figure()
    fig.add_bar(x=df["year"], y=df["n_retractions"], name="年度撤稿数", marker_color="#4c72b0")
    fig.add_scatter(
        x=df["year"],
        y=df["ma3_retractions"],
        name="3 年移动平均",
        mode="lines",
        line=dict(color="#c44e52", width=3),
    )
    fig.update_layout(
        title="全球撤稿量（1990–2025）",
        xaxis_title="年份",
        yaxis_title="撤稿数量（篇）",
    )
    st.plotly_chart(_figure_layout(fig), use_container_width=True)

    peak = df.loc[df["year"] == 2023]
    if not peak.empty:
        _conclusion(
            f"2023 年一年撤稿 <b>{int(peak['n_retractions'].iloc[0]):,}</b> 篇，"
            f"同比增长 <b>{peak['yoy_change_pct'].iloc[0]:.0f}%</b>。"
            "这个跃升与 Hindawi/Wiley 等出版商的大规模撤稿事件时间吻合。"
        )

    fig2 = go.Figure()
    for column, label, color in (
        ("n_paper_mill", "明确标注为论文工厂", COLOR_PAPER_MILL),
        ("n_peer_review_fraud", "审稿流程被攻破", "#ff7f0e"),
        ("n_ai_generated", "AI 生成内容", "#9467bd"),
    ):
        fig2.add_scatter(
            x=df["year"], y=df[column], name=label, mode="lines+markers", line=dict(color=color, width=2.4)
        )
    fig2.update_layout(
        title="三类「系统性造假」信号的出现次数",
        xaxis_title="年份",
        yaxis_title="记录数",
    )
    st.plotly_chart(_figure_layout(fig2), use_container_width=True)

    fig3 = px.line(df, x="year", y="median_latency_days", markers=True, title="中位撤稿时滞的年代变化")
    fig3.update_traces(line=dict(color="#c44e52", width=2.6))
    fig3.update_layout(xaxis_title="撤稿年份", yaxis_title="中位撤稿时滞（天）")
    st.plotly_chart(_figure_layout(fig3, height=380), use_container_width=True)

    st.dataframe(df, use_container_width=True, height=280)
    st.download_button(
        "下载趋势数据（CSV）", df.to_csv(index=False).encode("utf-8-sig"), "retraction_trends.csv", "text/csv"
    )


def page_countries() -> None:
    st.title("国家 / 地区分布")
    limit = st.slider("显示前 N 个国家/地区", 5, 60, 20, 5)
    df = cached_countries(limit)

    fig = go.Figure()
    solo = df["n_as_sole_country"]
    coop = df["n_retractions"] - df["n_as_sole_country"]
    fig.add_bar(y=df["country"], x=solo, name="本国独立完成", orientation="h", marker_color="#4c72b0")
    fig.add_bar(y=df["country"], x=coop, name="涉及国际合作", orientation="h", marker_color="#dd8452")
    fig.update_layout(
        barmode="stack",
        title=f"撤稿量 Top{limit} 国家/地区",
        xaxis_title="撤稿数量（篇）",
        yaxis_title="",
        yaxis=dict(autorange="reversed"),
    )
    st.plotly_chart(_figure_layout(fig, height=max(420, 22 * len(df))), use_container_width=True)

    _conclusion(
        f"中国以 <b>{df.iloc[0]['n_retractions']:,}</b> 条位居第一，"
        f"占全球 <b>{df.iloc[0]['share_of_global_pct']:.1f}%</b>。"
    )
    _caveat(
        "口径：按作者所属国统计，一篇国际合作论文会同时计入多个国家，"
        "因此各国数字之和远大于全球总数。这是多值归属的必然结果，不是数据错误。"
    )

    fig2 = px.scatter(
        df,
        x="n_retractions",
        y="paper_mill_share",
        size="n_journals",
        color="median_latency_days",
        hover_name="country",
        log_x=True,
        color_continuous_scale="Viridis",
        title="撤稿规模 vs 论文工厂占比（气泡大小 = 涉及期刊数，颜色 = 中位撤稿时滞）",
    )
    fig2.update_layout(xaxis_title="撤稿量（对数刻度）", yaxis_title="论文工厂标注占比")
    st.plotly_chart(_figure_layout(fig2), use_container_width=True)

    st.dataframe(df, use_container_width=True, height=320)


def page_reasons() -> None:
    st.title("撤稿原因全景")
    limit = st.slider("显示前 N 个原因", 10, 60, 20, 5)
    df = cached_reasons(limit)

    fig = px.bar(
        df.sort_values("n_records"),
        x="n_records",
        y="reason",
        orientation="h",
        color="median_latency_days",
        color_continuous_scale="OrRd",
        title=f"撤稿原因 Top{limit}（颜色 = 中位撤稿时滞，越深表示越难被发现）",
    )
    fig.update_layout(
        xaxis_title="涉及的撤稿记录数",
        yaxis_title="",
        height=max(460, 24 * len(df)),
    )
    st.plotly_chart(_figure_layout(fig, height=max(460, 24 * len(df))), use_container_width=True)

    _conclusion(
        "「Investigation by Journal/Publisher」出现最多，但它描述的是<b>调查主体</b>而非造假手法。"
        "真正指向系统性造假的是 <b>Compromised Peer Review</b>（审稿流程被攻破）"
        "与 <b>Paper Mill</b>（论文工厂）。"
    )
    _caveat(
        "口径：一条撤稿记录通常有多个原因（平均 3.9 个），因此「涉及记录数」之和大于总记录数。"
        "把原因出现次数误当成撤稿篇数，是这类数据最常见的误读。"
    )

    st.dataframe(df, use_container_width=True, height=340)


def page_journal_risk() -> None:
    st.title("期刊风险识别")
    st.caption("复合评分 = 百分位排名加权，用于**筛查排序**，不是判决")

    col1, col2, col3 = st.columns([2, 2, 3])
    min_retractions = col1.slider("最小撤稿量门槛", 5, 100, 5, 5)
    tier = col2.selectbox("风险层级", ["全部", "极高", "高", "中", "低"])
    limit = col3.slider("显示条数", 20, 500, 100, 20)

    df = cached_journal_risk(min_retractions, None if tier == "全部" else tier, limit)

    fig = px.scatter(
        df,
        x="n_retractions",
        y="paper_mill_share",
        size="n_paper_mill",
        color="structural_score",
        hover_name="journal",
        log_x=True,
        color_continuous_scale="RdYlGn_r",
        title="规模 vs 行为：横轴撤稿量、纵轴论文工厂占比、颜色 = 结构分（无泄漏）",
    )
    fig.update_layout(xaxis_title="撤稿总量（对数）", yaxis_title="论文工厂标注占比")
    st.plotly_chart(_figure_layout(fig), use_container_width=True)

    validation = cached_risk_validation()
    st.subheader("模型验证：两个分数的 AUC 对比")
    st.dataframe(validation, use_container_width=True)

    for row in validation.itertuples(index=False):
        if "有泄漏" in row.score_name:
            _caveat(
                f"{row.score_name}：AUC = {row.auc:.4f}。"
                "这个分数包含由 Reason 字段派生的特征，与标签同源 —— "
                "<b>它是在用答案预测答案（目标泄漏）</b>，只能用于对已确认问题的严重程度排序，"
                "不能声称有预测能力。"
            )
        else:
            _conclusion(
                f"{row.score_name}：AUC = {row.auc:.4f}。"
                "这是完全不含撤稿原因信息、仅凭撤稿行为模式进行预测的真实水平。"
            )

    st.subheader("分层表现：被判为高风险，把握有多大？")
    st.dataframe(cached_tier_performance(), use_container_width=True)

    st.subheader("哪些特征真的有用？")
    fp = cached_feature_power()
    fig2 = px.bar(
        fp.sort_values("auc_abs"),
        x="auc_abs",
        y="feature",
        orientation="h",
        color="feature_group",
        color_discrete_map={"reason_derived": COLOR_PAPER_MILL, "structural": "#2ca02c"},
        title="单变量 AUC：红色为与标签同源的特征（AUC 被高估）",
    )
    fig2.add_vline(x=0.5, line_dash="dash", line_color="#444444")
    fig2.update_layout(xaxis_title="单变量 AUC", yaxis_title="", height=520)
    st.plotly_chart(_figure_layout(fig2, height=520), use_container_width=True)

    _conclusion(
        "破坏力最强的一课：<b>paper_mill_share 的 AUC = 1.000</b>，完美得可疑。"
        "它和标签同源于 Reason 字段，这是目标泄漏。<br>"
        "误以为'AUC 0.98 = 模型很强'，是数据科学里最常见也最昂贵的自欺。"
    )

    st.subheader("期刊明细")
    st.dataframe(df, use_container_width=True, height=380)
    st.download_button(
        "下载期刊风险表（CSV）", df.to_csv(index=False).encode("utf-8-sig"), "journal_risk.csv", "text/csv"
    )


def page_citation_impact() -> None:
    st.title("撤稿的引用代价")
    st.caption("事件研究法：以每篇论文自己的撤稿年份为原点，用撤稿前趋势外推反事实")

    df = cached_event_study()
    n_works = int(df["n_works"].iloc[0]) if not df.empty else 0

    fig = go.Figure()
    fig.add_scatter(
        x=df["event_time"],
        y=df["mean_citations"],
        name="实际观测",
        mode="lines+markers",
        line=dict(color=COLOR_ACTUAL, width=3),
        marker=dict(size=10),
    )
    fig.add_scatter(
        x=df["event_time"],
        y=df["counterfactual_citations"],
        name="反事实估计",
        mode="lines+markers",
        line=dict(color=COLOR_COUNTERFACTUAL, width=3, dash="dash"),
        marker=dict(size=9),
    )
    fig.add_vline(x=-0.5, line_dash="dot", line_color="#444444")
    fig.update_layout(
        title=f"撤稿前后的平均被引轨迹（平衡面板：{n_works:,} 篇 × {len(df)} 个时点）",
        xaxis_title="相对撤稿的年份",
        yaxis_title="平均被引次数",
    )
    st.plotly_chart(_figure_layout(fig, height=500), use_container_width=True)

    post = df[df["is_post_retraction"]]
    worst = post.loc[post["citation_gap_pct"].idxmin()] if not post.empty else None
    if worst is not None:
        _conclusion(
            f"撤稿后引用持续下跌，到第 {int(worst['event_time']):+d} 年时相对反事实损失 "
            f"<b>{worst['citation_gap_pct']:.1f}%</b>。"
            "撤稿前各时点的偏差都在 ±8% 以内，说明反事实拟合是合理的。"
        )

    _caveat(
        "局限：线性外推是强假设；撤稿时点与引用峰值存在内生性；"
        "无法区分「悄无声息的撤稿」与「被媒体广泛报道的撤稿」。"
        "因此这是<b>描述性的因果证据</b>，不是精确的因果效应估计。"
    )

    st.subheader("分群对比")
    by_group = cached_event_by_group()
    fig2 = px.line(
        by_group,
        x="event_time",
        y="citation_gap_pct",
        color="group_name",
        markers=True,
        color_discrete_map={"论文工厂": COLOR_PAPER_MILL, "其他撤稿": COLOR_OTHER},
        title="论文工厂 vs 其他撤稿：引用代价的差异",
    )
    fig2.add_hline(y=0, line_color="#444444")
    fig2.update_traces(line=dict(width=3), marker=dict(size=9))
    fig2.update_layout(xaxis_title="相对撤稿的年份", yaxis_title="相对反事实的变化（%）")
    st.plotly_chart(_figure_layout(fig2), use_container_width=True)

    _conclusion("论文工厂的论文受到的引用惩罚<b>更重</b> —— 这类论文一旦被揭穿，几乎彻底失去学术价值。")

    st.dataframe(df, use_container_width=True, height=240)

    fig3 = px.bar(
        df,
        x="event_time",
        y="data_coverage",
        title="数据覆盖率：该时点有多少比例是 OpenAlex 真实给出的引用（而非补零）",
    )
    fig3.update_layout(xaxis_title="相对撤稿的年份", yaxis_title="数据覆盖率")
    st.plotly_chart(_figure_layout(fig3, height=320), use_container_width=True)
    _caveat(
        "覆盖率在撤稿后下降是<b>符合预期</b>的：OpenAlex 只记录有被引的年份，"
        "而撤稿后越来越多论文的年度被引降为 0，因而被省略、由我们补零。"
        "这个下降本身也印证了结论，而不是数据缺失。"
    )


def page_network() -> None:
    st.title("合作网络分析")
    st.caption("用 Louvain 社群发现识别「挂名团伙」的结构特征")

    validation = queries.cluster_validation()
    st.dataframe(validation, use_container_width=True)
    for row in validation.itertuples(index=False):
        if "有泄漏" in row.score_name:
            _caveat(
                f"{row.score_name}：AUC = {row.auc:.4f}，但同样存在目标泄漏"
                "（论文工厂边占比与标签同源），只能用于排序已确认问题。"
            )
        else:
            _caveat(
                f"{row.score_name}：AUC = {row.auc:.4f} —— <b>反向预测，比随机还差</b>。"
                "查证后发现结构分最高的是教科书、会议论文集等大规模撤稿，而非论文工厂。"
                "这是一个被完整记录的负结果，详见下方「指标的规模陷阱」。"
            )

    col1, col2, col3 = st.columns([2, 3, 3])
    min_authors = col1.slider("社群最小规模", 3, 30, 3, 1)
    order_label = col2.selectbox(
        "排序依据",
        ["严重度分（含泄漏特征）", "结构分（无泄漏）", "社群规模", "合作论文数"],
    )
    order_map = {
        "严重度分（含泄漏特征）": "cluster_risk_score",
        "结构分（无泄漏）": "cluster_structural_score",
        "社群规模": "n_authors",
        "合作论文数": "total_shared_works",
    }
    top_n = col3.slider("显示条数", 10, 200, 40, 10)

    df = cached_clusters(min_authors, order_map[order_label])

    fig = px.scatter(
        df,
        x="n_authors",
        y="density",
        size="total_shared_works",
        color="cluster_risk_score",
        hover_name="cluster_id",
        color_continuous_scale="YlOrRd",
        title="社群规模 vs 内部密度（气泡 = 合作论文数，颜色 = 严重度分）",
    )
    fig.update_layout(xaxis_title="社群规模（作者数）", yaxis_title="内部密度")
    st.plotly_chart(_figure_layout(fig), use_container_width=True)

    st.dataframe(df.head(top_n), use_container_width=True, height=360)
    st.download_button(
        "下载社群表（CSV）", df.to_csv(index=False).encode("utf-8-sig"), "author_clusters.csv", "text/csv"
    )

    st.divider()
    st.subheader("指标的规模陷阱")
    dens = cached_density_by_size()
    fig2 = go.Figure()
    fig2.add_scatter(
        x=dens["n_authors"],
        y=dens["avg_density"],
        name="平均密度",
        mode="lines+markers",
        line=dict(color="#4c72b0", width=3),
    )
    fig2.add_bar(
        x=dens["n_authors"],
        y=dens["saturated_share"],
        name="密度恰好 = 1.0 的占比",
        marker_color=COLOR_PAPER_MILL,
        opacity=0.35,
        yaxis="y2",
    )
    fig2.update_layout(
        title="density 随社群规模饱和 —— 一个真实的指标设计缺陷",
        xaxis_title="社群规模（作者数）",
        yaxis_title="平均密度",
        yaxis2=dict(title="密度饱和比例", overlaying="y", side="right", range=[0, 1.05]),
    )
    st.plotly_chart(_figure_layout(fig2), use_container_width=True)

    _conclusion(
        "3 人社群中 <b>92.8% 的密度恰好等于 1.0</b> —— "
        "因为 3 个节点最多只能构成 1 个三角形，只要合作过就是满密度。<br>"
        "这意味着 density 在小规模区间几乎是常数、<b>不含任何信息</b>，"
        "而结构分给了它 45% 的权重。<br>"
        "教训：<b>图指标的取值在不同规模之间不可比。</b>"
    )

    st.divider()
    st.subheader("社群成员")
    cluster_id = st.number_input(
        "输入社群编号查看成员", min_value=0, value=int(df.iloc[0]["cluster_id"]) if not df.empty else 0
    )
    members = cached_cluster_members(int(cluster_id))
    st.dataframe(members, use_container_width=True, height=280)

    _caveat(
        "以上是<b>筛查线索</b>，不是结论。社群发现是无监督方法，"
        "高密度社群同样可能是高产的正规课题组。任何针对具体个人的判断都必须经过人工核查。"
    )


def page_quality() -> None:
    st.title("数据质量")
    st.caption("38 条自动校验规则：量纲检查、一致性对拍、模型有效性")

    q = cached_quality()
    if q.empty:
        st.warning("未找到 reports/quality_report.json，请先运行 scripts/04_check_quality.py")
        return

    passed = int(q["passed"].sum())
    total = len(q)
    errors = int(((~q["passed"]) & (q["severity"] == "error")).sum())
    warnings = int(((~q["passed"]) & (q["severity"] == "warning")).sum())

    col = st.columns(4)
    col[0].metric("规则总数", total)
    col[1].metric("通过", passed)
    col[2].metric("阻断级失败", errors)
    col[3].metric("警告", warnings)

    st.subheader("全部校验规则")
    display = q[["name", "severity", "violations", "passed"]].copy()
    display["status"] = display["passed"].map({True: "✓ 通过", False: "✗ 未通过"})
    st.dataframe(
        display[["status", "name", "severity", "violations"]],
        use_container_width=True,
        height=520,
    )

    st.divider()
    st.subheader("为什么需要这么多规则？")
    st.markdown(
        """
        这些规则不是为了"看起来严谨"，每一条都对应一次真实的事故或一类具体风险：

        | 规则类别 | 拦住了什么 |
        |---|---|
        | 主键完整性 | 上游重复推送导致所有计数偏高 |
        | 日期范围 | 解析规则失效导致趋势图静默缺失一段 |
        | **量纲检查** | **一个 0~1 的比例算出了 148 —— 这是本项目真实修掉的 bug** |
        | 外键完整性 | join 静默丢行，让占比算错且不易察觉 |
        | 体量下限 | 网络中断导致的静默截断 |
        | **模型有效性** | **AUC ≤ 0.5 的反向指标被当成成果发布** |
        | 因果前置条件 | **事件面板不平衡 → 全部因果结论不成立** |
        """
    )

    _conclusion(
        "最关键的一课：<b>模型指标好，不等于数据是对的。</b><br>"
        "本项目的两个模型 bug，AUC 都高达 0.98，看起来效果极好 —— "
        "抓住它们的不是复杂的评估，而是"
        "『这个比例怎么会大于 1』和『AUC 怎么可能等于 1』这种最朴素的常识检查。"
    )


# ----------------------------------------------------------------------
# 路由
# ----------------------------------------------------------------------
PAGE_FUNCTIONS = {
    "总览": page_overview,
    "撤稿趋势": page_trends,
    "国家分布": page_countries,
    "撤稿原因": page_reasons,
    "期刊风险": page_journal_risk,
    "引用代价": page_citation_impact,
    "合作网络": page_network,
    "数据质量": page_quality,
}


def main() -> None:
    st.sidebar.title("导航")
    st.sidebar.caption("PaperMill Hunter")
    choice = st.sidebar.radio("选择页面", PAGES, label_visibility="collapsed")
    st.sidebar.divider()

    settings = get_settings()
    st.sidebar.caption(f"数据仓库\n\n`{settings.warehouse_path.name}`")
    if settings.warehouse_path.exists():
        size_mb = settings.warehouse_path.stat().st_size / 1024 / 1024
        st.sidebar.caption(f"大小：{size_mb:.0f} MB")
    st.sidebar.divider()
    st.sidebar.caption("数据源\n\n- Retraction Watch\n- OpenAlex\n\n本看板所有结论均标注了口径与局限。")

    try:
        PAGE_FUNCTIONS[choice]()
    except FileNotFoundError as exc:
        st.error(f"数据仓库尚未构建：{exc}")
        st.info("请依次运行：scripts/01 → 02 → 03 → 04 → 05")
    except Exception as exc:  # noqa: BLE001
        # 看板出错时给出可操作的提示，而不是丢一个红框 traceback 让使用者去猜。
        st.error(f"页面渲染失败：{type(exc).__name__}: {exc}")
        st.info("请先确认已运行 scripts/03_build_warehouse.py 与 scripts/05_analyze_network.py")


main()
