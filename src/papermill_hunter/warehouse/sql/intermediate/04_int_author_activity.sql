-- ============================================================================
-- 04_int_author_activity.sql —— 作者活动画像（一行 = 一个作者姓名）
-- ============================================================================
-- 【必须先说清楚这张表的局限，否则它是危险的】
--
--   这里用**作者姓名字符串**作为身份标识，而姓名不是唯一标识：
--     - 同名不同人：'Wang Wei' 在中国可能有成千上万人
--     - 同一人不同写法：'John Smith' / 'J. Smith' / 'Smith, John'
--     - 姓名顺序差异：中文姓名在不同期刊里的呈现方式不同
--
--   所以这张表**不能**用来断言"某某学者有问题"。它是一个**筛查线索**，
--   而不是结论。真正严谨的做法是接入 ORCID（学术界的唯一作者标识），
--   这也是本项目在"后续改进"里明确列出的方向。
--
--   ——在简历和面试里，**主动说出方法的边界，比强调方法多厉害更有说服力**。
--   面试官想看的不是你假装数据很干净，而是你知道它哪里不干净、打算怎么办。
--
-- 【即便如此，这张表依然有价值】
--   论文工厂的一个显著特征是"批量挂名"：同一批姓名在短时间内大量重复出现，
--   且跨越多个不相关的期刊与学科。这种**共现模式**即使存在同名噪音，
--   在统计上依然会凸显出来 —— 因为噪音是随机分布的，而流水线是集中的。
-- ============================================================================

CREATE OR REPLACE TABLE intermediate.author_activity AS
SELECT
    a.author_name,

    -- ---------------- 体量 ----------------
    COUNT(DISTINCT a.record_id)                                       AS n_retractions,
    COUNT(DISTINCT e.journal)                                         AS n_journals,
    COUNT(DISTINCT e.subject_code)                                    AS n_subject_areas,
    COUNT(DISTINCT e.retraction_year)                                 AS n_active_years,
    COUNT(DISTINCT e.first_country)                                   AS n_countries,

    -- ---------------- 时间跨度 ----------------
    MIN(e.original_paper_year)                                        AS first_paper_year,
    MAX(e.original_paper_year)                                        AS last_paper_year,
    MIN(e.retraction_year)                                            AS first_retraction_year,
    MAX(e.retraction_year)                                            AS last_retraction_year,

    -- ---------------- 造假特征 ----------------
    SUM(CASE WHEN e.is_paper_mill THEN 1 ELSE 0 END)                  AS n_paper_mill,
    SUM(CASE WHEN e.is_peer_review_fraud THEN 1 ELSE 0 END)           AS n_peer_review_fraud,
    SUM(CASE WHEN e.is_ai_generated THEN 1 ELSE 0 END)                AS n_ai_generated,

    -- ---------------- 署名位置 ----------------
    -- 第一作者与通讯作者（末位）占比。
    -- 论文工厂常把"买卖来的作者"放在中间位置，而把真正操盘的人
    -- 固定放在首/末位 —— 因此这两个比例是有信息量的。
    SUM(CASE WHEN a.author_position = 1 THEN 1 ELSE 0 END)            AS n_first_author,
    SUM(CASE WHEN a.author_position = e.n_authors THEN 1 ELSE 0 END)  AS n_last_author,

    ROUND(median(e.retraction_latency_days), 1)                       AS median_latency_days

FROM staging.retraction_authors AS a
INNER JOIN intermediate.retractions_enriched AS e
        ON a.record_id = e.record_id
GROUP BY a.author_name;
