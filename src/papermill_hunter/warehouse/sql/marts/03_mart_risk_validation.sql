-- ============================================================================
-- 03_mart_risk_validation.sql —— 用已知标签验证评分（含目标泄漏的实证）
-- ============================================================================
-- 【为什么这一步是整个项目最关键的环节】
--
--   做异常检测最容易陷入的自欺是：设计了一个看起来很聪明的分数，
--   挑出几个"看起来很像"的案例，在报告里展示这几个案例，
--   让读者误以为方法有效。这叫"用案例证明方法"，是**不可证伪**的。
--
--   我们很幸运：Retraction Watch 有官方标注的 'Paper Mill' 原因，
--   相当于提供了一批**已知答案**。于是可以问一个可证伪的问题：
--
--       "在不知道标签的情况下，这个分数能不能把'有论文工厂记录的期刊'
--        排在'没有的期刊'前面？"
--
--   标准统计量是 **AUC（ROC 曲线下面积）**：
--       AUC = 1.0 → 完美排序   AUC = 0.5 → 等价随机猜测   AUC < 0.5 → 方向反了
--
-- 【AUC 可以直接用 SQL 算出来】
--   不需要任何机器学习库。AUC 数学上等价于 Mann-Whitney U 统计量：
--       AUC = P(随机取一个正例，其得分 > 随机取一个负例的得分)
--   用秩表示：把全部样本按得分排序，正例秩之和为 R_pos，则
--       AUC = (R_pos − n_pos(n_pos+1)/2) / (n_pos × n_neg)
--   本文件就是这句公式的 SQL 实现。
--   **能自己推出并写出这个公式，比调用 sklearn.metrics.roc_auc_score
--     更能说明你理解自己在做什么。**
--
-- 【并列（tie）的处理】
--   很多期刊的某个特征取值完全相同（例如 paper_mill_share 大量为 0）。
--   按行号硬排的话，谁前谁后取决于引擎的任意顺序，
--   同一份数据跑两次会得到不同的 AUC —— 这是绝对不可接受的。
--   正确做法是给并列样本**取平均秩**，这也是 Mann-Whitney U 的标准处理。
-- ============================================================================

CREATE OR REPLACE TABLE marts.risk_score_validation AS
WITH base AS (
    SELECT
        journal,
        (n_paper_mill > 0) AS label,
        risk_score,
        structural_score
    FROM marts.journal_risk
),

-- 对两个分数分别做同样的秩计算
by_score AS (
    SELECT
        'risk_score（严重度分，含标签特征 → 有泄漏）' AS score_name,
        label,
        risk_score AS score
    FROM base
    UNION ALL
    SELECT
        'structural_score（结构分，无标签特征 → 可信）',
        label,
        structural_score
    FROM base
),

ranked AS (
    SELECT
        score_name,
        label,
        score,
        -- 用 rank() 而不是 row_number()：rank() 对并列样本返回**相同的最小秩**，
        -- 这正是计算平均秩所需要的起点。
        --
        -- 【这里修过一个 bug】最初写的是：
        --     row_number() ... AS rn
        --     rn + (tie_count - 1) / 2.0 AS avg_rank
        -- 看起来对，其实错了：并列组内每一行的 rn 都不同（它是行号），
        -- 于是同一组并列样本会拿到**互不相同**的平均秩，
        -- 最终算出的 paper_mill_share AUC = 1.0008 —— **AUC 不可能大于 1**。
        --
        -- 又一次是"取值超出理论范围"暴露了逻辑错误。
        -- 正确公式：平均秩 = 组内最小秩 + (组大小 - 1) / 2
        rank()       OVER (PARTITION BY score_name ORDER BY score) AS min_rank,
        COUNT(*)     OVER (PARTITION BY score_name, score)         AS tie_count
    FROM by_score
),

average_ranks AS (
    SELECT
        score_name,
        label,
        min_rank + (tie_count - 1) / 2.0 AS avg_rank
    FROM ranked
),

u_statistic AS (
    SELECT
        score_name,
        SUM(CASE WHEN label THEN avg_rank ELSE 0 END) AS sum_rank_positive,
        COUNT(*) FILTER (WHERE label)                 AS n_positive,
        COUNT(*) FILTER (WHERE NOT label)             AS n_negative
    FROM average_ranks
    GROUP BY score_name
)

SELECT
    score_name,
    n_positive                                        AS n_journals_with_paper_mill,
    n_negative                                        AS n_journals_without_paper_mill,
    n_positive + n_negative                           AS n_journals_total,
    ROUND(
        (sum_rank_positive - n_positive * (n_positive + 1) / 2.0)
        / NULLIF(n_positive * n_negative, 0),
        4
    )                                                 AS auc,
    CASE
        WHEN (sum_rank_positive - n_positive * (n_positive + 1) / 2.0)
             / NULLIF(n_positive * n_negative, 0) >= 0.90 THEN '优秀'
        WHEN (sum_rank_positive - n_positive * (n_positive + 1) / 2.0)
             / NULLIF(n_positive * n_negative, 0) >= 0.80 THEN '良好'
        WHEN (sum_rank_positive - n_positive * (n_positive + 1) / 2.0)
             / NULLIF(n_positive * n_negative, 0) >= 0.70 THEN '可用'
        WHEN (sum_rank_positive - n_positive * (n_positive + 1) / 2.0)
             / NULLIF(n_positive * n_negative, 0) >= 0.60 THEN '偏弱'
        ELSE '接近随机'
    END                                               AS auc_grade
FROM u_statistic
ORDER BY auc DESC;


-- ============================================================================
-- 每个特征的单独区分能力（单变量 AUC）
-- ============================================================================
-- 【这张表是发现"目标泄漏"的关键工具】
--
--   把每个特征单独拿出来算 AUC，会得到两类截然不同的结果：
--     - paper_mill_share 的 AUC 会接近 1.0 —— 因为它和标签同源（都是 Reason 字段）
--     - burst_ratio / 体量 / 时滞 的 AUC 只在 0.5~0.7 之间 —— 这才是真实信号
--
--   如果不做这张表，你会以为"综合分 AUC 0.98"是靠多个信号一起努力得来的；
--   做完才发现，分数里绝大部分的"预测力"其实只是把答案抄了一遍。
--
--   **单变量 AUC 是检测目标泄漏最实用的手段之一。**
--
-- 【关于 direction 列】
--   有些特征方向是"越大越可疑"（爆发度、体量），有些是"越小越可疑"
--   （撤稿时滞短 = 很快被揪出来）。如果方向搞反，AUC 会小于 0.5。
--   这里用 GREATEST(auc, 1-auc) 给出"无论方向如何的区分能力"，
--   同时明确标出哪个方向才是可疑方向 ——
--   避免下游使用者把方向搞反，得到一个"反向指标"却浑然不知。
-- ============================================================================

CREATE OR REPLACE TABLE marts.feature_discriminative_power AS
WITH base AS (
    SELECT
        journal,
        (n_paper_mill > 0) AS label,
        -- 结构性特征（不含任何 Reason 派生信息）
        burst_ratio,
        ln(n_retractions)                          AS ln_n_retractions,
        n_retractions,
        active_years,
        median_latency_days,
        mean_authors,
        retractions_per_active_year,
        china_share,
        1.0 * n_international / n_retractions      AS international_share,
        -- 原因派生特征（存在目标泄漏风险），一并列出以作对照
        paper_mill_share,
        peer_review_fraud_share,
        ai_generated_share
    FROM marts.journal_risk
),

long_format AS (
    SELECT journal, label, 'burst_ratio'                AS feature, burst_ratio                AS value, 'structural' AS feature_group FROM base
    UNION ALL SELECT journal, label, 'ln_n_retractions',           ln_n_retractions,           'structural' FROM base
    UNION ALL SELECT journal, label, 'n_retractions',              n_retractions,              'structural' FROM base
    UNION ALL SELECT journal, label, 'active_years',               active_years,               'structural' FROM base
    UNION ALL SELECT journal, label, 'median_latency_days',        median_latency_days,        'structural' FROM base
    UNION ALL SELECT journal, label, 'mean_authors',               mean_authors,               'structural' FROM base
    UNION ALL SELECT journal, label, 'retractions_per_active_year', retractions_per_active_year, 'structural' FROM base
    UNION ALL SELECT journal, label, 'china_share',                china_share,                'structural' FROM base
    UNION ALL SELECT journal, label, 'international_share',        international_share,        'structural' FROM base
    UNION ALL SELECT journal, label, 'paper_mill_share',           paper_mill_share,           'reason_derived' FROM base
    UNION ALL SELECT journal, label, 'peer_review_fraud_share',    peer_review_fraud_share,    'reason_derived' FROM base
    UNION ALL SELECT journal, label, 'ai_generated_share',         ai_generated_share,         'reason_derived' FROM base
),

ranked AS (
    SELECT
        feature,
        feature_group,
        label,
        value,
        -- 同上的并列秩处理：用 rank()（返回组内最小秩），
        -- 而不是 row_number()（返回行号，会让并列样本拿到不同秩，算出 AUC > 1）
        rank()       OVER (PARTITION BY feature ORDER BY value) AS min_rank,
        COUNT(*)     OVER (PARTITION BY feature, value)         AS tie_count
    FROM long_format
    WHERE value IS NOT NULL
),

average_ranks AS (
    SELECT
        feature,
        feature_group,
        label,
        min_rank + (tie_count - 1) / 2.0 AS avg_rank
    FROM ranked
),

u_statistic AS (
    SELECT
        feature,
        feature_group,
        SUM(CASE WHEN label THEN avg_rank ELSE 0 END) AS sum_rank_positive,
        COUNT(*) FILTER (WHERE label)                 AS n_positive,
        COUNT(*) FILTER (WHERE NOT label)             AS n_negative
    FROM average_ranks
    GROUP BY feature, feature_group
),

with_auc AS (
    SELECT
        feature,
        feature_group,
        n_positive,
        n_negative,
        (sum_rank_positive - n_positive * (n_positive + 1) / 2.0)
            / NULLIF(n_positive * n_negative, 0) AS auc
    FROM u_statistic
)

SELECT
    feature,
    feature_group,
    ROUND(auc, 4)                                        AS auc,
    -- 无论方向如何的区分能力：< 0.5 说明原方向反了
    ROUND(GREATEST(auc, 1 - auc), 4)                     AS auc_abs,
    CASE WHEN auc >= 0.5 THEN '取值越大越可疑' ELSE '取值越小越可疑' END AS suspicious_direction,
    -- 泄漏嫌疑标记：由原因字段派生的特征天然与标签同源
    CASE WHEN feature_group = 'reason_derived'
         THEN '⚠ 由 Reason 字段派生，与标签同源，AUC 被高估'
         ELSE '结构性特征，可用于预测' END                AS leakage_note
FROM with_auc
ORDER BY auc_abs DESC;


-- ============================================================================
-- 分层表现：结构分（可信的那个）各风险层的精确率
-- ============================================================================
-- AUC 是一个整体数字，业务方更关心："你说这家期刊高风险，我信它的把握有多大？"
-- 这张表回答这个问题的 —— 给出每个风险层的**精确率（precision）**：
-- 被判为高风险的期刊里，真正有论文工厂记录的比例。
--
-- 提升度（lift）= 该层命中率 ÷ 整体基准命中率。
-- lift = 3 意味着"这一层出问题的可能性是平均水平的 3 倍"。
-- ============================================================================
CREATE OR REPLACE TABLE marts.risk_tier_performance AS
WITH base AS (
    SELECT
        CASE
            WHEN structural_score >= 80 THEN '1_极高'
            WHEN structural_score >= 65 THEN '2_高'
            WHEN structural_score >= 45 THEN '3_中'
            ELSE '4_低'
        END AS structural_tier,
        structural_score,
        (n_paper_mill > 0) AS has_known_paper_mill
    FROM marts.journal_risk
),
overall AS (
    SELECT AVG(CASE WHEN has_known_paper_mill THEN 1.0 ELSE 0.0 END) AS base_rate
    FROM base
)
SELECT
    b.structural_tier,
    COUNT(*)                                                            AS n_journals,
    SUM(CASE WHEN b.has_known_paper_mill THEN 1 ELSE 0 END)             AS n_with_paper_mill,
    ROUND(AVG(CASE WHEN b.has_known_paper_mill THEN 1.0 ELSE 0.0 END), 4) AS precision_rate,
    ROUND(MIN(b.structural_score), 2)                                   AS min_score,
    ROUND(MAX(b.structural_score), 2)                                   AS max_score,
    ROUND(o.base_rate, 4)                                               AS overall_base_rate,
    ROUND(
        AVG(CASE WHEN b.has_known_paper_mill THEN 1.0 ELSE 0.0 END)
        / NULLIF(o.base_rate, 0),
        2
    )                                                                   AS lift
FROM base AS b
CROSS JOIN overall AS o
GROUP BY b.structural_tier, o.base_rate
ORDER BY b.structural_tier;
