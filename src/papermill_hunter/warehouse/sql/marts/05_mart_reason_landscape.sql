-- ============================================================================
-- 05_mart_reason_landscape.sql —— 撤稿原因全景
-- ============================================================================
-- 【为什么这张表要区分"出现次数"和"涉及记录数"】
--   一条撤稿记录通常有多个原因（本数据集平均 3.9 个）。
--   所以：
--     - "Paper Mill 出现 11,798 次" 是**原因出现次数**
--     - "有 11,798 条撤稿记录被标注了 Paper Mill" 是**记录数**
--   在本例中两者恰好接近，但对其他原因差别很大：
--     一条记录可能同时有 'Investigation by Journal/Publisher' 和
--     'Unreliable Results' 两个原因，于是它在两个类别里各计一次。
--
--   把这两个口径分开列出来，是为了让读者不会把"原因出现次数"误当成"撤稿篇数"。
--   这是数据报告中极其常见的一类误读，而它恰恰是分析师的表述责任。
--
-- 【为什么要算"时滞"这一列】
--   不同原因的"暴露难度"差别巨大：
--     - 图片重复（Duplication of/in Image）靠工具就能批量比对，暴露快
--     - 同行评议被操纵（Compromised Peer Review）需要有人举报或内审，暴露慢
--   用"中位撤稿时滞"量化这种差异，能把"哪类造假更难被发现"变成一个数字。
-- ============================================================================

CREATE OR REPLACE TABLE marts.reason_landscape AS
WITH exploded AS (
    SELECT
        r.reason,
        r.record_id,
        e.retraction_year,
        e.retraction_latency_days,
        e.is_paper_mill,
        e.involves_china
    FROM staging.retraction_reasons AS r
    INNER JOIN intermediate.retractions_enriched AS e
            ON r.record_id = e.record_id
),

latest_year AS (
    SELECT MAX(retraction_year) AS max_year FROM intermediate.retractions_enriched
)

SELECT
    e.reason,

    -- ---------------- 体量 ----------------
    COUNT(*)                                                        AS n_occurrences,
    COUNT(DISTINCT e.record_id)                                     AS n_records,
    ROUND(
        100.0 * COUNT(DISTINCT e.record_id)
        / (SELECT COUNT(*) FROM intermediate.retractions_enriched),
        2
    )                                                               AS pct_of_all_retractions,

    -- ---------------- 时间特征 ----------------
    MIN(e.retraction_year)                                          AS first_year,
    MAX(e.retraction_year)                                          AS last_year,
    ROUND(median(e.retraction_latency_days), 1)                     AS median_latency_days,

    -- ---------------- 趋势：近期 vs 早期 ----------------
    -- 用"近 5 年出现数"与"全部出现数"的比值刻画这个原因是在上升还是退潮。
    -- 注意：因为近期的时间窗口更短，正常情况下的比值会明显小于 5/总年数，
    -- 所以这个指标只适合**横向比较不同原因之间的相对趋势**，
    -- 不适合单独解读成"增长了百分之多少"。
    SUM(CASE WHEN e.retraction_year >= (SELECT max_year - 4 FROM latest_year) THEN 1 ELSE 0 END)
                                                                    AS n_recent_5y,
    ROUND(
        1.0 * SUM(CASE WHEN e.retraction_year >= (SELECT max_year - 4 FROM latest_year)
                       THEN 1 ELSE 0 END) / COUNT(*),
        4
    )                                                               AS recent_5y_share,

    -- ---------------- 与中国市场的关联 ----------------
    -- 有些撤稿原因在特定国家高度集中，这本身就是有价值的线索
    SUM(CASE WHEN e.involves_china THEN 1 ELSE 0 END)               AS n_involving_china,
    ROUND(
        1.0 * SUM(CASE WHEN e.involves_china THEN 1 ELSE 0 END) / COUNT(*),
        4
    )                                                               AS china_share,

    ROW_NUMBER() OVER (ORDER BY COUNT(DISTINCT e.record_id) DESC)   AS rank_by_records

FROM exploded AS e
GROUP BY e.reason;
