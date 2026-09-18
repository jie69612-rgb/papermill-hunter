-- ============================================================================
-- 03_int_journal_profile.sql —— 期刊画像表（一行 = 一个期刊）
-- ============================================================================
-- 【本文件记录了一个真实修掉的 bug —— 值得完整读一遍】
--
--   最初的实现是在 journal_year_activity（**已经按期刊×年份聚合过的表**）之上
--   再聚合，其中"撤稿总数"写成了：
--
--       SELECT journal, COUNT(*) AS n_retractions
--       FROM intermediate.journal_year_activity
--       GROUP BY journal
--
--   看起来天经地义，实际完全错了：`COUNT(*)` 数的是**每个期刊有多少个"年份行"**，
--   而不是有多少篇撤稿。于是一个撤稿 1000 篇、集中在 1 年内的期刊，
--   n_retractions 会被算成 1。
--
--   由此产生的连锁错误：
--       burst_ratio = peak_year_retractions / n_retractions = 1041 / 1 = 1041
--       paper_mill_share = ... 也变成了大于 1 的数
--   而"比例大于 1"在业务上是绝对不可能的。
--
--   ★ 最值得记住的一点：这个 bug **没有被模型验证抓住**。
--     修正前的风险评分 AUC 高达 0.982，看起来"效果极好" ——
--     因为错误的 burst_ratio 恰好和"是否存在论文工厂记录"高度共线，
--     于是模型"表现很好"，但它学到的其实是错的信号。
--
--     抓住它的是最朴素的一步：**检查指标是否落在可能的取值范围内**。
--     一个 0~1 的比例算出了 148，说明数据或逻辑一定有问题。
--
--   教训：**模型指标好，不等于数据是对的。**
--   先做量纲与取值范围的常识校验，再去相信任何评估指标。
--   本文件对应的质量规则见 warehouse/quality.py 中的"期刊画像指标取值范围"。
--
-- 【结构上的修正】
--   现在所有计数类指标都直接从 retractions_enriched（**一行一条撤稿**，
--   这才是正确的聚合粒度）计算，只把"峰值年份"这一类确实需要
--   期刊×年份粒度的指标交给 journal_year_activity。
--   —— 聚合粒度选对，是数据建模中最重要也最容易搞错的一件事。
-- ============================================================================

CREATE OR REPLACE TABLE intermediate.journal_profile AS
WITH
-- ---------------------------------------------------------------------------
-- 从正确的粒度（一行一条撤稿）直接聚合
-- ---------------------------------------------------------------------------
base AS (
    SELECT
        journal,
        MIN(publisher)                                                        AS publisher,

        -- 一行一条撤稿，所以 COUNT(*) 就是撤稿总数
        COUNT(*)                                                              AS n_retractions,
        COUNT(DISTINCT retraction_year)                                       AS active_years,
        MIN(retraction_year)                                                  AS first_year,
        MAX(retraction_year)                                                  AS last_year,

        -- ---------------- 造假特征（计数与比例） ----------------
        SUM(CASE WHEN is_paper_mill THEN 1 ELSE 0 END)                        AS n_paper_mill,
        SUM(CASE WHEN is_peer_review_fraud THEN 1 ELSE 0 END)                 AS n_peer_review_fraud,
        SUM(CASE WHEN is_ai_generated THEN 1 ELSE 0 END)                      AS n_ai_generated,
        SUM(CASE WHEN retraction_nature = 'Expression of concern' THEN 1 ELSE 0 END)
                                                                              AS n_expression_of_concern,
        SUM(CASE WHEN is_international THEN 1 ELSE 0 END)                     AS n_international,

        -- 用 AVG(布尔转 0/1) 直接算比例，比拼两次 COUNT 更不容易出错
        ROUND(AVG(CASE WHEN is_paper_mill THEN 1.0 ELSE 0.0 END), 4)          AS paper_mill_share,
        ROUND(AVG(CASE WHEN is_peer_review_fraud THEN 1.0 ELSE 0.0 END), 4)   AS peer_review_fraud_share,
        ROUND(AVG(CASE WHEN is_ai_generated THEN 1.0 ELSE 0.0 END), 4)        AS ai_generated_share,
        ROUND(AVG(CASE WHEN involves_china THEN 1.0 ELSE 0.0 END), 4)         AS china_share,

        -- ---------------- 其他特征 ----------------
        -- 直接在全量记录上取中位数，而不是"对每年的中位数再取中位数" ——
        -- 后者在数学上不等于整体中位数，是一个常见但隐蔽的近似错误。
        ROUND(median(retraction_latency_days), 1)                             AS median_latency_days,
        ROUND(AVG(n_authors), 2)                                              AS mean_authors
    FROM intermediate.retractions_enriched
    WHERE journal IS NOT NULL
    GROUP BY journal
),

-- ---------------------------------------------------------------------------
-- 峰值年份：这部分确实需要"期刊 × 年份"粒度
-- ---------------------------------------------------------------------------
peak AS (
    SELECT
        journal,
        year             AS peak_year,
        n_retractions    AS peak_year_retractions
    FROM (
        SELECT
            journal,
            year,
            n_retractions,
            -- 并列时按年份取更近的一年，保证结果稳定可复现
            -- （不加这个 tie-breaker，同一份数据多次运行可能给出不同的峰值年份）
            row_number() OVER (
                PARTITION BY journal
                ORDER BY n_retractions DESC, year DESC
            ) AS rn
        FROM intermediate.journal_year_activity
    )
    WHERE rn = 1
)

SELECT
    b.journal,
    b.publisher,
    b.n_retractions,
    b.active_years,
    b.first_year,
    b.last_year,
    b.n_paper_mill,
    b.n_peer_review_fraud,
    b.n_ai_generated,
    b.n_expression_of_concern,
    b.n_international,
    b.paper_mill_share,
    b.peer_review_fraud_share,
    b.ai_generated_share,
    b.median_latency_days,
    b.mean_authors,
    b.china_share,

    p.peak_year,
    p.peak_year_retractions,

    -- 爆发度：撤稿最集中的那一年占了全部撤稿的多大比例（理论取值 0~1）
    ROUND(1.0 * p.peak_year_retractions / b.n_retractions, 4)                 AS burst_ratio,

    -- 年均撤稿量：用于区分"长期高撤稿"与"一次性爆发"
    ROUND(1.0 * b.n_retractions / GREATEST(b.active_years, 1), 2)             AS retractions_per_active_year

FROM base AS b
LEFT JOIN peak AS p ON b.journal = p.journal;
