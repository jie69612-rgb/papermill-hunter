-- ============================================================================
-- 04_mart_country_leaderboard.sql —— 国家/地区撤稿画像
-- ============================================================================
-- 【一个必须交代的口径问题】
--   这条统计是按**作者所属国家**计的，而一篇国际合作论文会同时计入多个国家，
--   所以各国数字之和**远大于**全球撤稿总数。这不是错误，是"多值归属"的必然结果。
--   但如果不说明，读者会以为"加起来怎么比总数还多，数据是不是错了"。
--
--   因此本表刻意同时提供两个口径：
--     - n_retractions      ：涉及该国的撤稿记录数（含国际合作，会重复计数）
--     - n_as_sole_country  ：该国独立完成（无国际合作）的撤稿数（不重复）
--   并给出 share_of_global（相对全球总记录数的占比）而不是"占各国之和的比例"。
--
--   在报告里主动交代口径边界，是数据分析师最基本的职业素养 ——
--   口径不说清，结论就会被误读，而误读的责任在分析师，不在读者。
-- ============================================================================

CREATE OR REPLACE TABLE marts.country_leaderboard AS
WITH country_records AS (
    SELECT
        c.country,
        COUNT(DISTINCT c.record_id)                                       AS n_retractions,
        COUNT(DISTINCT CASE WHEN NOT e.is_international THEN c.record_id END)
                                                                          AS n_as_sole_country,
        SUM(CASE WHEN e.is_paper_mill THEN 1 ELSE 0 END)                  AS n_paper_mill,
        SUM(CASE WHEN e.is_peer_review_fraud THEN 1 ELSE 0 END)           AS n_peer_review_fraud,
        SUM(CASE WHEN e.is_ai_generated THEN 1 ELSE 0 END)                AS n_ai_generated,
        ROUND(median(e.retraction_latency_days), 1)                       AS median_latency_days,
        MIN(e.retraction_year)                                            AS first_retraction_year,
        MAX(e.retraction_year)                                            AS last_retraction_year,
        COUNT(DISTINCT e.retraction_year)                                 AS active_years,
        COUNT(DISTINCT e.journal)                                         AS n_journals,
        ROUND(AVG(e.n_authors), 2)                                        AS mean_authors
    FROM staging.retraction_countries AS c
    INNER JOIN intermediate.retractions_enriched AS e
            ON c.record_id = e.record_id
    GROUP BY c.country
),

totals AS (
    SELECT COUNT(*) AS global_retractions FROM intermediate.retractions_enriched
),

recent AS (
    -- 近 5 年（相对数据集内最新年份）的撤稿量，用于观察"近期活跃度"
    SELECT
        c.country,
        COUNT(DISTINCT c.record_id) AS n_recent
    FROM staging.retraction_countries AS c
    INNER JOIN intermediate.retractions_enriched AS e
            ON c.record_id = e.record_id
    WHERE e.retraction_year >= (
        SELECT MAX(retraction_year) - 4 FROM intermediate.retractions_enriched
    )
    GROUP BY c.country
)

SELECT
    cr.country,
    cr.n_retractions,
    cr.n_as_sole_country,
    ROUND(100.0 * cr.n_retractions / t.global_retractions, 2)  AS share_of_global_pct,
    cr.n_paper_mill,
    ROUND(1.0 * cr.n_paper_mill / cr.n_retractions, 4)         AS paper_mill_share,
    cr.n_peer_review_fraud,
    ROUND(1.0 * cr.n_peer_review_fraud / cr.n_retractions, 4)  AS peer_review_fraud_share,
    cr.n_ai_generated,
    ROUND(1.0 * cr.n_ai_generated / cr.n_retractions, 4)       AS ai_generated_share,
    cr.median_latency_days,
    cr.first_retraction_year,
    cr.last_retraction_year,
    cr.active_years,
    cr.n_journals,
    cr.mean_authors,

    COALESCE(r.n_recent, 0)                                    AS n_recent_5y,
    ROUND(1.0 * COALESCE(r.n_recent, 0) / cr.n_retractions, 4) AS recent_5y_share,

    -- 排名：便于直接取 Top N
    ROW_NUMBER() OVER (ORDER BY cr.n_retractions DESC)         AS rank_by_volume
FROM country_records AS cr
CROSS JOIN totals AS t
LEFT JOIN recent AS r ON cr.country = r.country;
