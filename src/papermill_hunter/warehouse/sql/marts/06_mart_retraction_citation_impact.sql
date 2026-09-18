-- ============================================================================
-- 06_mart_retraction_citation_impact.sql —— 撤稿的引用代价（事件研究法）
-- ============================================================================
-- 【要回答的问题】
--   "一篇论文被撤稿之后，它损失了多少引用？"
--
-- 【为什么不能直接比较"撤稿论文 vs 未撤稿论文"的引用量】
--   因为这两组论文**本来就不一样**。撤稿论文可能本身质量就低、选题就冷门，
--   引用少是"因为差"而不是"因为被撤"。这叫**选择性偏差（selection bias）**，
--   是最常见的因果推断陷阱：把相关当成因果。
--
-- 【事件研究法的思路：让每篇论文做自己的对照】
--   既然找不到完美的对照组，那就换个角度 —— 看**同一篇论文在撤稿前后的变化**。
--   撤稿发生在一个明确的时间点上，于是可以问：
--       "如果这篇论文没有被撤稿，它后面几年的引用会是多少？"
--
--   这个"如果"（反事实）无法直接观测，但可以**估计**：
--   用撤稿前 3 年的引用趋势做线性外推。
--   论文的引用量通常随年龄自然衰减或增长，这个趋势在短期内是稳定的；
--   如果撤稿真的造成伤害，那么撤稿后的实际引用会**显著低于**这条外推线。
--
--       实际引用 − 反事实引用 = 撤稿造成的净影响（gap）
--
--   这就是"中断时间序列 / 事件研究"的标准做法。
--
-- 【在 SQL 里做最小二乘回归】
--   一元线性回归不需要任何统计库，闭式解就是：
--       斜率 slope = (n·Σxy − Σx·Σy) / (n·Σx² − (Σx)²)
--       截距 intercept = (Σy − slope·Σx) / n
--   本文件就是这两个公式的 SQL 实现。
--   **能自己写出这个公式，说明你理解回归在做什么，而不是把它当黑箱调用。**
--
-- 【必须说清楚的局限 —— 不写出来就是不诚实】
--   1. 线性外推是强假设。如果引用趋势本身是弯曲的（先升后降），
--      直线外推会高估或低估反事实，从而把趋势误判为"撤稿效应"。
--   2. 撤稿往往发生在论文已经被大量引用之后（先被引，后被发现问题），
--      所以"撤稿时点"本身可能与引用峰值相关 —— 存在**内生性**。
--   3. 我们无法观测到"撤稿的暴露程度"：有些撤稿悄无声息，
--      有些被媒体广泛报道。把所有撤稿混在一起平均，
--      得到的是"平均处理效应"，掩盖了巨大的异质性。
--
--   因此本表的结论应当被表述为**描述性的因果证据**，
--   而不是精确的因果效应估计。真正的因果识别需要工具变量、
--   断点回归或随机实验设计 —— 那超出了公开数据的支持范围。
--
--   —— 在简历和面试里，**主动列出方法的局限，远比宣称结论有多可靠有说服力**。
--      面试官想确认的是：你知道自己结论的边界在哪里。
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 主表：各事件时点的实际引用 vs 反事实引用
-- ----------------------------------------------------------------------------
CREATE OR REPLACE TABLE marts.retraction_citation_event_study AS
WITH by_event AS (
    SELECT
        event_time,
        COUNT(DISTINCT openalex_id)          AS n_works,
        COUNT(*)                             AS n_observations,
        ROUND(AVG(cited_by_count), 4)        AS mean_citations,
        ROUND(median(cited_by_count), 3)     AS median_citations,
        -- 覆盖率：该时点有多少比例的数据是 OpenAlex 真实给出的（而非补零）。
        -- 如果某个时点覆盖率异常低，说明那一年的引用数据可能不完整，
        -- 对应的均值就不可信。这是判断"这条曲线能不能用"的关键诊断列。
        ROUND(AVG(CASE WHEN has_observed_data THEN 1.0 ELSE 0.0 END), 4) AS data_coverage
    FROM intermediate.work_citation_event_panel
    GROUP BY event_time
),

-- 只用撤稿前的时点拟合趋势线（这是"反事实"的基石）
pre_fit AS (
    SELECT
        COUNT(*)                          AS n,
        SUM(event_time)                   AS sx,
        SUM(mean_citations)               AS sy,
        SUM(event_time * mean_citations)  AS sxy,
        SUM(event_time * event_time)      AS sxx
    FROM by_event
    WHERE event_time < 0
),

params AS (
    SELECT
        n, sx, sy,
        (n * sxy - sx * sy) / NULLIF(n * sxx - sx * sx, 0)                       AS slope,
        (sy - ((n * sxy - sx * sy) / NULLIF(n * sxx - sx * sx, 0)) * sx) / n     AS intercept
    FROM pre_fit
)

SELECT
    b.event_time,
    b.n_works,
    b.n_observations,
    b.mean_citations,
    b.median_citations,
    b.data_coverage,

    -- 反事实：如果没被撤稿，按撤稿前趋势本应有多少引用
    ROUND(p.intercept + p.slope * b.event_time, 4)                     AS counterfactual_citations,

    -- 净影响：实际 − 反事实。负值 = 撤稿造成了引用损失。
    ROUND(b.mean_citations - (p.intercept + p.slope * b.event_time), 4) AS citation_gap,

    -- 相对损失率：把绝对差距换算成百分比，便于跨论文比较。
    -- 分母用反事实值而不是实际值 —— 因为我们要问的是
    -- "相对于本该有的引用，损失了百分之多少"。
    ROUND(
        100.0 * (b.mean_citations - (p.intercept + p.slope * b.event_time))
        / NULLIF(p.intercept + p.slope * b.event_time, 0),
        2
    )                                                                  AS citation_gap_pct,

    ROUND(p.slope, 4)                                                  AS pre_trend_slope,
    ROUND(p.intercept, 4)                                              AS pre_trend_intercept,
    (b.event_time >= 0)                                                AS is_post_retraction

FROM by_event AS b
CROSS JOIN params AS p
ORDER BY b.event_time;


-- ----------------------------------------------------------------------------
-- 分群事件研究：论文工厂 vs 其他撤稿
-- ----------------------------------------------------------------------------
-- 为什么必须分群？
--   把所有撤稿平均在一起，会得到一个"平均处理效应"，
--   而平均值很可能掩盖了完全相反的两种现象：
--     - 论文工厂的论文：本来就没人读，撤稿后引用变化可能很小
--     - 高影响力的诚实论文因错误被撤：引用可能断崖式下跌
--   **只报平均值，等于把两种故事糊成了一个没有信息量的数字。**
-- ----------------------------------------------------------------------------
CREATE OR REPLACE TABLE marts.retraction_citation_event_study_by_group AS
WITH labelled AS (
    SELECT
        *,
        CASE WHEN is_paper_mill THEN '论文工厂' ELSE '其他撤稿' END AS group_name
    FROM intermediate.work_citation_event_panel
),

by_event_group AS (
    SELECT
        group_name,
        event_time,
        COUNT(DISTINCT openalex_id)      AS n_works,
        AVG(cited_by_count)              AS mean_citations,
        median(cited_by_count)           AS median_citations
    FROM labelled
    GROUP BY group_name, event_time
),

pre_fit AS (
    SELECT
        group_name,
        COUNT(*)                          AS n,
        SUM(event_time)                   AS sx,
        SUM(mean_citations)               AS sy,
        SUM(event_time * mean_citations)  AS sxy,
        SUM(event_time * event_time)      AS sxx
    FROM by_event_group
    WHERE event_time < 0
    GROUP BY group_name
),

params AS (
    SELECT
        group_name,
        (n * sxy - sx * sy) / NULLIF(n * sxx - sx * sx, 0)                   AS slope,
        (sy - ((n * sxy - sx * sy) / NULLIF(n * sxx - sx * sx, 0)) * sx) / n AS intercept
    FROM pre_fit
)

SELECT
    g.group_name,
    g.event_time,
    g.n_works,
    ROUND(g.mean_citations, 4)                                          AS mean_citations,
    ROUND(g.median_citations, 3)                                        AS median_citations,
    ROUND(p.intercept + p.slope * g.event_time, 4)                      AS counterfactual_citations,
    ROUND(g.mean_citations - (p.intercept + p.slope * g.event_time), 4) AS citation_gap,
    ROUND(
        100.0 * (g.mean_citations - (p.intercept + p.slope * g.event_time))
        / NULLIF(p.intercept + p.slope * g.event_time, 0),
        2
    )                                                                    AS citation_gap_pct,
    (g.event_time >= 0)                                                 AS is_post_retraction
FROM by_event_group AS g
INNER JOIN params AS p ON g.group_name = p.group_name
ORDER BY g.group_name, g.event_time;


-- ----------------------------------------------------------------------------
-- 结论摘要：一句话能说清的数字
-- ----------------------------------------------------------------------------
CREATE OR REPLACE TABLE marts.retraction_citation_penalty_summary AS
SELECT
    COUNT(*) FILTER (WHERE is_post_retraction)                        AS n_post_periods,
    ROUND(AVG(citation_gap) FILTER (WHERE is_post_retraction), 4)     AS mean_gap_after_retraction,
    ROUND(AVG(citation_gap_pct) FILTER (WHERE is_post_retraction), 2) AS mean_gap_pct_after_retraction,
    -- 撤稿前窗口的 gap 理论上应当接近 0（因为反事实就是从这段拟合出来的）。
    -- 如果不接近 0，说明拟合本身有问题 —— 这是一个**自检列**。
    -- 我们刻意把它放在结果表里，让任何人都能一眼验证拟合是否合理。
    ROUND(AVG(citation_gap) FILTER (WHERE NOT is_post_retraction), 4) AS mean_gap_before_retraction,
    ROUND(MAX(pre_trend_slope), 4)                                    AS pre_trend_slope
FROM marts.retraction_citation_event_study;
