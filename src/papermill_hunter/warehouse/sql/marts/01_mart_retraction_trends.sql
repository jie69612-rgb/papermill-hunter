-- ============================================================================
-- 01_mart_retraction_trends.sql —— 撤稿趋势年度表
-- ============================================================================
-- 这是回答"全球撤稿的规模和演变是什么样的"这一问题的核心表。
--
-- 【指标设计说明】
--   1. 为什么用「撤稿日期」而不是「论文发表日期」做时间轴？
--      两者回答的是完全不同的问题：
--        - 按撤稿日期 → "学术界的纠错机制每年在被触发多少次"（机制视角）
--        - 按发表日期 → "哪一年产出的论文里造假最多"（源头视角）
--      本表用撤稿日期（机制视角），另外单独给出 original_paper_year 的分布，
--      两个视角都保留，因为它们的结论并不相同 ——
--      一篇 2018 年发表的假论文可能在 2023 年才被撤，
--      只看其中一个维度都会得出片面的结论。
--
--   2. 为什么提供「累计」列？
--      单年数字受个别批量撤稿事件影响会剧烈波动（比如 2023 年的爆发），
--      累计曲线能把"长期趋势"和"单次事件"分离开，避免被尖峰误导。
--
--   3. 为什么计算「同比变化率」？
--      绝对值的变化放在图里一眼能看出方向，但"变化了多少"需要数字。
--      同比是有量纲的业务语言，比"比去年多/少"更精确。
-- ============================================================================

CREATE OR REPLACE TABLE marts.retraction_trends AS
WITH yearly AS (
    SELECT
        retraction_year                                            AS year,
        COUNT(*)                                                   AS n_retractions,
        SUM(CASE WHEN retraction_nature = 'Retraction' THEN 1 ELSE 0 END)
                                                                   AS n_true_retraction,
        SUM(CASE WHEN retraction_nature = 'Expression of concern' THEN 1 ELSE 0 END)
                                                                   AS n_expression_of_concern,
        SUM(CASE WHEN retraction_nature = 'Correction' THEN 1 ELSE 0 END)
                                                                   AS n_correction,
        SUM(CASE WHEN retraction_nature = 'Reinstatement' THEN 1 ELSE 0 END)
                                                                   AS n_reinstatement,

        SUM(CASE WHEN is_paper_mill THEN 1 ELSE 0 END)             AS n_paper_mill,
        SUM(CASE WHEN is_peer_review_fraud THEN 1 ELSE 0 END)      AS n_peer_review_fraud,
        SUM(CASE WHEN is_ai_generated THEN 1 ELSE 0 END)           AS n_ai_generated,
        SUM(CASE WHEN involves_china THEN 1 ELSE 0 END)            AS n_china,
        SUM(CASE WHEN is_international THEN 1 ELSE 0 END)          AS n_international,

        COUNT(DISTINCT journal)                                    AS n_journals,
        ROUND(AVG(retraction_latency_days), 1)                     AS mean_latency_days,
        ROUND(median(retraction_latency_days), 1)                  AS median_latency_days,
        ROUND(AVG(n_authors), 2)                                   AS mean_authors
    FROM intermediate.retractions_enriched
    WHERE retraction_year IS NOT NULL
    GROUP BY retraction_year
)

SELECT
    year,
    n_retractions,
    n_true_retraction,
    n_expression_of_concern,
    n_correction,
    n_reinstatement,
    n_paper_mill,
    n_peer_review_fraud,
    n_ai_generated,
    n_china,
    n_international,
    n_journals,
    mean_latency_days,
    median_latency_days,
    mean_authors,

    -- ---------------- 比例 ----------------
    ROUND(1.0 * n_paper_mill / n_retractions, 4)          AS paper_mill_share,
    ROUND(1.0 * n_china / n_retractions, 4)               AS china_share,
    ROUND(1.0 * n_international / n_retractions, 4)       AS international_share,

    -- ---------------- 同比变化 ----------------
    LAG(n_retractions) OVER (ORDER BY year)                AS prev_year_retractions,
    n_retractions - LAG(n_retractions) OVER (ORDER BY year) AS yoy_change,
    ROUND(
        100.0 * (n_retractions - LAG(n_retractions) OVER (ORDER BY year))
        / NULLIF(LAG(n_retractions) OVER (ORDER BY year), 0),
        1
    )                                                      AS yoy_change_pct,

    -- ---------------- 累计 ----------------
    SUM(n_retractions) OVER (ORDER BY year ROWS UNBOUNDED PRECEDING) AS cumulative_retractions,

    -- ---------------- 移动平均（3 年） ----------------
    -- 平滑掉单次批量撤稿造成的尖峰，让长期趋势更清晰。
    -- 注意：前两年因为窗口不满 3 年，这里的值是基于不足 3 年的数据算的，
    -- 解读时应当忽略最早两年。这是移动平均的固有特性，不是 bug。
    ROUND(
        AVG(n_retractions) OVER (ORDER BY year ROWS BETWEEN 2 PRECEDING AND CURRENT ROW),
        1
    )                                                      AS ma3_retractions

FROM yearly;
