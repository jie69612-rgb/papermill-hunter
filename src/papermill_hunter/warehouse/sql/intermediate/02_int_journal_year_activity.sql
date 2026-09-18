-- ============================================================================
-- 02_int_journal_year_activity.sql —— 期刊 × 年份 活动表
-- ============================================================================
-- 这张表是**论文工厂侦测的核心**。
--
-- 为什么按"期刊 × 年份"而不是只按期刊聚合？
--   因为论文工厂最典型的信号不是"某期刊撤稿多"，而是
--   **"某期刊在某一年突然爆发式撤稿"**。
--   举例（本数据集里的真实情况）：
--       2011 International Conference on E-Business and E-Government
--           → 全部 1,280 篇撤稿**集中在同一年**
--       Journal of Healthcare Engineering
--           → 2023 年一年撤稿 1,013 篇
--   一个正常的期刊，撤稿应当是**逐年零散分布**的；
--   而流水线作业留下的痕迹，是把上千篇论文在同一时间窗口里一次性暴露出来。
--
--   只按期刊聚合会把这个"爆发"信号平均掉，看不出任何异常。
--   这就是"聚合粒度选择"对分析结论的决定性影响：
--   **粒度选错，异常就被平均成了正常。**
-- ============================================================================

CREATE OR REPLACE TABLE intermediate.journal_year_activity AS
SELECT
    journal,
    retraction_year                                   AS year,

    -- ---------------- 体量 ----------------
    COUNT(*)                                          AS n_retractions,
    SUM(CASE WHEN is_paper_mill THEN 1 ELSE 0 END)    AS n_paper_mill,
    SUM(CASE WHEN is_peer_review_fraud THEN 1 ELSE 0 END) AS n_peer_review_fraud,
    SUM(CASE WHEN is_ai_generated THEN 1 ELSE 0 END)  AS n_ai_generated,
    SUM(CASE WHEN is_rogue_editor THEN 1 ELSE 0 END)  AS n_rogue_editor,

    -- ---------------- 特征比例 ----------------
    ROUND(AVG(CASE WHEN is_paper_mill THEN 1.0 ELSE 0.0 END), 4)        AS paper_mill_share,
    ROUND(AVG(CASE WHEN is_peer_review_fraud THEN 1.0 ELSE 0.0 END), 4)  AS peer_review_fraud_share,
    ROUND(AVG(CASE WHEN is_ai_generated THEN 1.0 ELSE 0.0 END), 4)       AS ai_generated_share,

    -- ---------------- 反应速度 ----------------
    -- 中位数比平均值稳健：少数几条拖了十年才撤的记录不会把整体拉偏
    ROUND(median(retraction_latency_days), 1)         AS median_latency_days,
    ROUND(AVG(retraction_latency_days), 1)            AS mean_latency_days,

    -- ---------------- 作者规模 ----------------
    ROUND(AVG(n_authors), 2)                          AS mean_authors,

    -- ---------------- 国家集中度 ----------------
    ROUND(AVG(CASE WHEN involves_china THEN 1.0 ELSE 0.0 END), 4) AS china_share

FROM intermediate.retractions_enriched
WHERE journal IS NOT NULL
  AND retraction_year IS NOT NULL
GROUP BY journal, retraction_year;
