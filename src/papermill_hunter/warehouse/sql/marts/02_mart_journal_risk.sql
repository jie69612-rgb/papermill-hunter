-- ============================================================================
-- 02_mart_journal_risk.sql —— 期刊风险评分（本项目的旗舰产出）
-- ============================================================================
-- ⚠️ 本文件包含一次**自己发现并修掉的方法论事故**，请务必读完再改。
--
-- 【事故：目标泄漏（target leakage）】
--   最初的版本只有 `risk_score` 一个分数，它把 paper_mill_share 以 0.30 的权重
--   算进去，然后用"该期刊是否有 Paper Mill 标注"作为标签去验证，得到 AUC = 0.978。
--   看起来非常漂亮。
--
--   但拆开看每个分项与标签的关系时，问题立刻暴露：
--       paper_mill_share 在正例上的均值 = 0.33
--       paper_mill_share 在负例上的均值 = 0.00   ← 恰好为零，一个都不例外
--
--   原因很简单：**标签就是用 Reason == 'Paper Mill' 定义的，
--   而 paper_mill_share 也是从同一个字段算出来的。**
--   两者是同一份信息的两种写法：有标注 ⟺ 占比 > 0。
--   所以这个"预测"本质上是用答案预测答案 —— 这叫**目标泄漏**。
--   AUC 高不代表模型强，只代表我把答案抄进去了。
--
--   同样的问题也波及 peer_review_fraud_share 和 ai_generated_share
--   —— 它们都来自 Reason 字段，只是泄漏程度轻一些。
--
-- 【修正方案：把两个分数彻底分开】
--   1. `risk_score`（严重度分）—— 保留全部特征，但**明确它只用于"排序已确认问题的严重程度"**，
--      不能声称它有预测能力。回答的问题是：
--         "已知这些期刊出了事，哪个出得更严重？"
--
--   2. `structural_score`（结构分）—— **完全不使用任何来自 Reason 字段的特征**，
--      只靠"撤稿行为本身的结构特征"（爆发度、体量、反应速度）来排序。
--      这才是真正可以拿来做预测的分数。它回答的问题是：
--         "在完全不知道撤稿原因的前提下，仅凭撤稿的时间与规模模式，
--          能不能把有问题的期刊挑出来？"
--
--   两个分数在 03_mart_risk_validation.sql 中分别做 AUC 验证，结果差异一目了然。
--
--   —— 面试时，这个故事比"我的模型 AUC 0.98"有价值得多。
--      它证明的不是"我会调模型"，而是**我会怀疑自己的结果**。
-- ============================================================================

CREATE OR REPLACE TABLE marts.journal_risk AS
WITH eligible AS (
    SELECT
        journal,
        publisher,
        n_retractions,
        active_years,
        first_year,
        last_year,
        peak_year,
        peak_year_retractions,
        burst_ratio,
        n_paper_mill,
        paper_mill_share,
        n_peer_review_fraud,
        peer_review_fraud_share,
        n_ai_generated,
        ai_generated_share,
        n_expression_of_concern,
        n_international,
        median_latency_days,
        mean_authors,
        china_share,
        retractions_per_active_year
    FROM intermediate.journal_profile
    -- ------------------------------------------------------------------------
    -- 最小样本量门槛：为什么是 5？
    --
    -- 这个数字**不是拍脑袋定的**，而是看了数据分布后选的。
    -- （注：下面是修正 n_retractions 计算口径之后重新统计的结果）
    --     门槛 >=  5 篇 → 1,880 个期刊，其中 268 个有论文工厂记录  ← 采用
    --     门槛 >= 10 篇 →   405 个期刊，其中 133 个有
    --     门槛 >= 20 篇 →   151 个期刊，其中  44 个有
    --     门槛 >= 30 篇 →    77 个期刊，其中  20 个有
    --
    -- 门槛越低，负例越多、验证越有说服力，但小样本噪声也越大。
    -- 5 是一个兼顾"样本量足够做验证"和"避免小样本噪声"的折中。
    -- ------------------------------------------------------------------------
    WHERE n_retractions >= 5
),

percentiled AS (
    SELECT
        *,
        -- ---------------- 严重度分（含 Reason 派生特征，存在泄漏，仅供排序） ----------------
        percent_rank() OVER (ORDER BY paper_mill_share)          AS pr_paper_mill,
        -- log 压缩：期刊撤稿量从 5 到 1600 跨越两个多数量级，
        -- 不压缩的话头部几个大期刊会把排名完全拉开，中间的差异被抹平
        percent_rank() OVER (ORDER BY ln(n_retractions))         AS pr_volume,
        percent_rank() OVER (ORDER BY peer_review_fraud_share)   AS pr_peer_review,
        percent_rank() OVER (ORDER BY ai_generated_share)        AS pr_ai,

        -- ---------------- 结构分（完全不含 Reason 派生特征，可用于预测） ----------------
        -- 与"严重度分"共用 pr_volume；再补一个"撤稿强度"。
        percent_rank() OVER (ORDER BY retractions_per_active_year) AS pr_intensity,
        -- 下面这个特征虽然在单变量分析中区分度只有 0.50（几乎无效），
        -- 但仍然保留在表里 —— 保留是为了让"它无效"这个结论**可被复核**，
        -- 而不是把不利证据从数据里删掉。删掉它就没人能验证我的判断了。
        percent_rank() OVER (ORDER BY burst_ratio)               AS pr_burst
    FROM eligible
)

SELECT
    journal,
    publisher,
    n_retractions,
    active_years,
    first_year,
    last_year,
    peak_year,
    peak_year_retractions,
    burst_ratio,
    n_paper_mill,
    paper_mill_share,
    peer_review_fraud_share,
    ai_generated_share,
    n_expression_of_concern,
    n_international,
    median_latency_days,
    mean_authors,
    china_share,
    retractions_per_active_year,

    -- ========================================================================
    -- 一、严重度分（risk_score）
    --     用途：对**已经确认有问题**的期刊排序，看谁更严重。
    --     警告：包含来自 Reason 字段的特征，**不具备预测能力**，不可用于"发现未知问题"。
    --     权重依据（研究者先验，非从数据学得）：
    --        paper_mill_share        0.30  官方明确标注的论文工厂占比，信号最直接
    --        burst_ratio             0.25  撤稿在时间上的集中度
    --        ln(n_retractions)       0.20  体量，用 log 压缩量纲
    --        peer_review_fraud_share 0.15  审稿流程被攻破，是论文工厂的必要条件
    --        ai_generated_share      0.10  新兴信号，权重给得保守
    -- ========================================================================
    ROUND(100 * pr_paper_mill, 1)     AS sev_paper_mill,
    ROUND(100 * pr_burst, 1)          AS sev_burst,
    ROUND(100 * pr_volume, 1)         AS sev_volume,
    ROUND(100 * pr_peer_review, 1)    AS sev_peer_review,
    ROUND(100 * pr_ai, 1)             AS sev_ai,

    ROUND(
        100 * (
            0.30 * pr_paper_mill
          + 0.25 * pr_burst
          + 0.20 * pr_volume
          + 0.15 * pr_peer_review
          + 0.10 * pr_ai
        ),
        2
    ) AS risk_score,

    CASE
        WHEN 100 * (0.30 * pr_paper_mill + 0.25 * pr_burst + 0.20 * pr_volume
                  + 0.15 * pr_peer_review + 0.10 * pr_ai) >= 85 THEN '极高'
        WHEN 100 * (0.30 * pr_paper_mill + 0.25 * pr_burst + 0.20 * pr_volume
                  + 0.15 * pr_peer_review + 0.10 * pr_ai) >= 70 THEN '高'
        WHEN 100 * (0.30 * pr_paper_mill + 0.25 * pr_burst + 0.20 * pr_volume
                  + 0.15 * pr_peer_review + 0.10 * pr_ai) >= 50 THEN '中'
        ELSE '低'
    END AS risk_tier,

    -- ========================================================================
    -- 二、结构分（structural_score）
    --     用途：**在完全不知道撤稿原因的前提下**，仅凭撤稿行为的规模与强度模式
    --          判断一个期刊是否可疑。这是本项目真正的"预测模型"。
    --
    -- 【一次被数据推翻的假设 —— 这是本项目最有价值的发现之一】
    --   设计之初，我坚信 `burst_ratio`（爆发度）是论文工厂的核心指纹：
    --   "上千篇论文在同一年集中被撤"看起来就是流水线作业的铁证。
    --   于是最初给了它 0.40 的权重。
    --
    --   做完单变量 AUC 分析（见 marts.feature_discriminative_power）后发现：
    --       burst_ratio 的 AUC = 0.4966  →  **几乎正好等于 0.5**
    --   也就是说，它**没有任何区分能力**，比随机猜测还略差一点点。
    --
    --   为什么直觉会错？因为"爆发"描述的是**一批论文被同时揭发**，
    --   而论文工厂描述的是**一批论文被批量生产**。两者相关但不等价：
    --   单个期刊的一次性批量撤稿（例如某次会议论文集整体撤稿）
    --   同样会产生极高的 burst_ratio，却与论文工厂无关。
    --   高 burst_ratio 是"事件规模"的信号，不是"造假方式"的信号。
    --
    --   —— 这个故事值得在面试里讲：**我设计了一个自以为很聪明的指标，
    --      然后用数据证明了它是错的。** 它体现的是可证伪的工作方式，
    --      比"AUC 0.98"这种漂亮数字有说服力得多。
    --
    -- 【修正后的组成】只保留实测有效的两个规模类信号：
    --        ln(n_retractions)             0.60  撤稿体量（log 压缩量纲）
    --        retractions_per_active_year   0.40  撤稿强度（年均撤稿量）
    --
    -- 【一个刻意不用的强信号：china_share】
    --       china_share 的 AUC 高达 0.819，是所有结构性特征里最强的。
    --       但它**被有意排除**，有两个理由：
    --         1. 泛化性：把"是否涉及中国"作为风险因子，模型换个国家就失效了 ——
    --            它学到的是"地域"，不是"行为"。
    --         2. 公平性：以国籍/地域作为风险评分的输入，会系统性地歧视特定群体，
    --            在真实的风控场景里这是明确的合规红线。
    --       在风控业务中，"某个特征预测力很强"从来不是使用它的充分理由。
    --       —— 主动放弃一个高 AUC 特征并说明原因，比堆特征更能体现判断力。
    -- ========================================================================
    ROUND(100 * pr_volume, 1)             AS struct_volume,
    ROUND(100 * pr_intensity, 1)          AS struct_intensity,

    ROUND(
        100 * (
            0.60 * pr_volume
          + 0.40 * pr_intensity
        ),
        2
    ) AS structural_score

FROM percentiled;
