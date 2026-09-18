-- ============================================================================
-- 01_int_retractions_enriched.sql —— 撤稿记录增强表（一行 = 一条撤稿）
-- ============================================================================
-- 这一层开始引入**业务口径**：把原子的、散落在多张 staging 表里的信息，
-- 汇聚成一条"能直接回答业务问题"的记录。
--
-- 【核心陷阱：join 两张一对多表会导致行数爆炸】
--   staging 层里有 5 张展开表（原因、国家、作者、机构、学科），它们都是
--   "一条撤稿 → N 行"。如果直接这样写：
--
--       SELECT ... FROM retraction_watch w
--       LEFT JOIN retraction_reasons r   ON w.record_id = r.record_id
--       LEFT JOIN retraction_authors a   ON w.record_id = a.record_id
--
--   那么一条有 5 个原因、8 个作者的记录会变成 5×8 = 40 行 ——
--   这叫**笛卡尔积放大**。它不会报错，只会让所有计数偏高，
--   而且偏得毫无规律（取决于每条的展开数量），是数据建模中最经典的静默错误之一。
--
--   正确做法：**先把每张展开表各自聚合到 record_id 粒度（1 行），再 join。**
--   下面用 5 个独立的聚合 CTE 实现，最后以 retraction_watch 为主表逐一 LEFT JOIN。
-- ============================================================================

CREATE OR REPLACE TABLE intermediate.retractions_enriched AS
WITH
-- ---------------------------------------------------------------------------
-- 原因侧聚合：把"N 个原因"压缩成"这条记录有哪些原因特征"
-- ---------------------------------------------------------------------------
reason_agg AS (
    SELECT
        record_id,
        COUNT(*)                                                          AS n_reasons,

        -- 关键业务标记：论文工厂
        -- Retraction Watch 使用受控词表，'Paper Mill' 是官方明确标注的类别。
        -- 这给了我们一份**有标签的数据** —— 这是本项目最有价值的一点：
        -- 我们既可以用无监督方法去发现"还没被查出来"的可疑案例，
        -- 也可以用这批标签去**验证**我们的方法到底准不准。
        MAX(CASE WHEN reason = 'Paper Mill' THEN 1 ELSE 0 END)            AS is_paper_mill,

        MAX(CASE WHEN reason IN ('Compromised Peer Review',
                                 'Concerns/Issues about Peer Review')
                 THEN 1 ELSE 0 END)                                       AS is_peer_review_fraud,
        MAX(CASE WHEN reason = 'Rogue Editor' THEN 1 ELSE 0 END)          AS is_rogue_editor,
        MAX(CASE WHEN reason = 'Computer-Aided Content or Computer-Generated Content'
                 THEN 1 ELSE 0 END)                                       AS is_ai_generated,
        MAX(CASE WHEN reason = 'Duplication of/in Image' THEN 1 ELSE 0 END) AS is_image_duplication,
        MAX(CASE WHEN reason = 'Investigation by Third Party' THEN 1 ELSE 0 END)
                                                                          AS is_third_party_investigation,
        MAX(CASE WHEN reason = 'Author Unresponsive' THEN 1 ELSE 0 END)   AS is_author_unresponsive

    FROM staging.retraction_reasons
    GROUP BY record_id
),

-- ---------------------------------------------------------------------------
-- 国家侧聚合
-- ---------------------------------------------------------------------------
country_agg AS (
    SELECT
        record_id,
        COUNT(*)                                   AS n_countries,
        -- 主要国家：取第一个（Retraction Watch 大致按贡献度排序）
        MIN(country)                               AS first_country,
        -- 是否国际合作（涉及多个国家）
        CASE WHEN COUNT(*) > 1 THEN 1 ELSE 0 END   AS is_international,
        -- 是否涉及中国（本项目核心分析对象之一）
        MAX(CASE WHEN country = 'China' THEN 1 ELSE 0 END) AS involves_china
    FROM staging.retraction_countries
    GROUP BY record_id
),

-- ---------------------------------------------------------------------------
-- 作者侧聚合
-- ---------------------------------------------------------------------------
author_agg AS (
    SELECT
        record_id,
        COUNT(*)                                   AS n_authors,
        -- 第一作者与通讯作者（学术惯例：末位通常是通讯作者/导师）
        MAX(CASE WHEN author_position = 1 THEN author_name END)  AS first_author,
        MAX(CASE WHEN author_position = (
                 SELECT MAX(a2.author_position)
                 FROM staging.retraction_authors AS a2
                 WHERE a2.record_id = a.record_id
             ) THEN author_name END)                            AS last_author
    FROM staging.retraction_authors AS a
    GROUP BY record_id
),

-- ---------------------------------------------------------------------------
-- 机构侧聚合
-- ---------------------------------------------------------------------------
institution_agg AS (
    SELECT
        record_id,
        COUNT(*) AS n_institutions
    FROM staging.retraction_institutions
    GROUP BY record_id
),

-- ---------------------------------------------------------------------------
-- 学科侧聚合（含大类编码解析）
-- ---------------------------------------------------------------------------
-- 原始取值形如 '(B/T) Computer Science'、'(HSC) Medicine - Oncology'
-- 括号里是 Retraction Watch 自建的学科大类编码，后面才是具体学科。
-- 解析出编码后，就能按"医学 / 工程 / 计算机"这样的大类做对比分析。
subject_agg AS (
    SELECT
        record_id,
        COUNT(*)                                                       AS n_subjects,
        MIN(subject)                                                   AS primary_subject,
        MIN(regexp_extract(subject, '^\(([^)]+)\)', 1))                AS subject_code,
        MIN(NULLIF(regexp_replace(subject, '^\([^)]+\)\s*', ''), ''))  AS subject_name
    FROM staging.retraction_subjects
    GROUP BY record_id
)

SELECT
    -- ---------------- 主键与基本属性 ----------------
    w.record_id,
    w.title,
    w.journal,
    w.publisher,
    w.retraction_nature,
    w.paywalled,

    -- ---------------- 时间与"撤稿时滞" ----------------
    -- 撤稿时滞 = 撤稿日期 − 原文发表日期。
    -- 这是本项目最核心的指标之一：**造假被发现的平均耗时**。
    -- 它直接衡量学术纠错机制的反应速度，也是论文工厂能长期获利的根本原因
    -- ——如果一篇假论文要 4 年才被撤，那它早就完成了考核、拿到了经费、评上了职称。
    w.original_paper_date,
    w.retraction_date,
    w.original_paper_year,
    w.retraction_year,
    CASE
        WHEN w.original_paper_date IS NOT NULL AND w.retraction_date IS NOT NULL
        THEN date_diff('day', w.original_paper_date, w.retraction_date)
    END                                                     AS retraction_latency_days,
    CASE
        WHEN w.original_paper_date IS NOT NULL AND w.retraction_date IS NOT NULL
        THEN ROUND(date_diff('day', w.original_paper_date, w.retraction_date) / 365.25, 2)
    END                                                     AS retraction_latency_years,

    -- ---------------- 标识符 ----------------
    w.retraction_doi,
    w.original_paper_doi,
    w.original_paper_pubmed_id,

    -- ---------------- 原因派生特征 ----------------
    COALESCE(r.n_reasons, 0)            AS n_reasons,
    COALESCE(r.is_paper_mill, 0) = 1            AS is_paper_mill,
    COALESCE(r.is_peer_review_fraud, 0) = 1     AS is_peer_review_fraud,
    COALESCE(r.is_rogue_editor, 0) = 1          AS is_rogue_editor,
    COALESCE(r.is_ai_generated, 0) = 1          AS is_ai_generated,
    COALESCE(r.is_image_duplication, 0) = 1     AS is_image_duplication,
    COALESCE(r.is_third_party_investigation, 0) = 1 AS is_third_party_investigation,
    COALESCE(r.is_author_unresponsive, 0) = 1   AS is_author_unresponsive,

    -- ---------------- 规模特征 ----------------
    COALESCE(c.n_countries, 0)          AS n_countries,
    COALESCE(c.is_international, 0) = 1 AS is_international,
    COALESCE(c.involves_china, 0) = 1   AS involves_china,
    COALESCE(a.n_authors, 0)            AS n_authors,
    COALESCE(i.n_institutions, 0)       AS n_institutions,
    COALESCE(s.n_subjects, 0)           AS n_subjects,

    c.first_country,
    a.first_author,
    a.last_author,
    s.primary_subject,
    s.subject_code,
    s.subject_name,

    -- ---------------- 血缘 ----------------
    w._source_file,
    w._loaded_at

FROM staging.retraction_watch AS w
LEFT JOIN reason_agg      AS r ON w.record_id = r.record_id
LEFT JOIN country_agg     AS c ON w.record_id = c.record_id
LEFT JOIN author_agg      AS a ON w.record_id = a.record_id
LEFT JOIN institution_agg AS i ON w.record_id = i.record_id
LEFT JOIN subject_agg     AS s ON w.record_id = s.record_id;
