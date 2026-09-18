-- ============================================================================
-- 05_int_works_enriched.sql —— OpenAlex 作品 × Retraction Watch 案底（DOI 关联）
-- ============================================================================
-- 这是**两个数据源的第一次真正汇合**，也是整个项目的枢纽表。
--
-- 【为什么要做这个关联】
--   两个数据源各有一半信息，缺一不可：
--     - OpenAlex：13.5 万篇被标记撤稿的作品，带**逐年引用轨迹**、主题、作者、机构
--                但它**不告诉你为什么撤稿**
--     - Retraction Watch：7.2 万条案底，带**撤稿原因**、时滞、国家
--                但它**没有引用数据**，也没有对照组
--
--   只有按 DOI 关联起来，才能回答"论文工厂的论文，引用轨迹和普通撤稿有何不同"
--   这类需要**同时使用原因和引用**的问题。
--
-- 【关联键的选择】
--   DOI 是两个数据源唯一共有的稳定标识符。但两者的写法不同：
--     OpenAlex  : 'https://doi.org/10.1016/S0140-6736(20)30367-6'
--     RW        : '10.1016/s0140-6736(20)30367-6'（有时带前缀，有时不带）
--   我们在 staging 层已经用同一个宏 rw_normalize_doi() 把两边都规范化了 ——
--   **这就是为什么"统一规范化"必须在最早的清洗层做**：
--   如果留到这一层才处理，任何一处遗漏都会让 join 静默丢行。
--
-- 【一个必须记住的 join 陷阱】
--   Retraction Watch 里**同一个 DOI 可能对应多条记录**
--   （例如同一篇论文先被 Expression of Concern 标记，之后才正式撤稿）。
--   如果直接 LEFT JOIN，主表行数会被放大 —— 一篇作品变成两行，
--   于是"作品总数""平均被引"这些指标全部失真，而且不报错。
--   所以这里**先把 RW 按 DOI 聚合到一行**，再 join。
--   （同样的坑我们在 retractions_enriched 里已经踩过一次了，这里是第二处。）
-- ============================================================================

CREATE OR REPLACE TABLE intermediate.works_enriched AS
WITH rw_by_doi AS (
    SELECT
        original_paper_doi                                 AS doi,
        -- 同一 DOI 的多条记录先压缩成一行
        COUNT(*)                                           AS n_rw_records,
        MIN(retraction_date)                               AS retraction_date,
        MAX(CASE WHEN is_paper_mill THEN 1 ELSE 0 END)     AS rw_is_paper_mill,
        MAX(CASE WHEN is_peer_review_fraud THEN 1 ELSE 0 END) AS rw_is_peer_review_fraud,
        MAX(CASE WHEN is_ai_generated THEN 1 ELSE 0 END)   AS rw_is_ai_generated,
        MAX(CASE WHEN is_image_duplication THEN 1 ELSE 0 END) AS rw_is_image_duplication,
        MAX(CASE WHEN is_rogue_editor THEN 1 ELSE 0 END)   AS rw_is_rogue_editor,
        MIN(retraction_nature)                             AS rw_retraction_nature,
        MIN(journal)                                       AS rw_journal,
        MIN(first_country)                                 AS rw_first_country,
        MIN(retraction_latency_days)                       AS rw_latency_days,
        MIN(original_paper_date)                           AS rw_original_paper_date
    FROM intermediate.retractions_enriched
    WHERE original_paper_doi IS NOT NULL
    GROUP BY original_paper_doi
)

SELECT
    -- ---------------- 来自 OpenAlex ----------------
    w.openalex_id,
    w.doi,
    w.title,
    w.publication_year,
    w.publication_date,
    w.work_type,
    w.language,
    w.cited_by_count,
    w.referenced_works_count,
    w.fwci,
    w.is_oa,
    w.oa_status,
    w.primary_topic,
    w.domain,
    w.field,
    w.subfield,
    w.n_authors,
    w.countries_distinct_count,
    w.institutions_distinct_count,

    -- ---------------- 关联结果 ----------------
    (r.doi IS NOT NULL)                                    AS matched_in_retraction_watch,
    COALESCE(r.n_rw_records, 0)                            AS n_rw_records,

    -- ---------------- 来自 Retraction Watch ----------------
    r.retraction_date,
    YEAR(r.retraction_date)                                AS retraction_year,
    r.rw_retraction_nature                                 AS retraction_nature,
    r.rw_journal                                           AS rw_journal,
    r.rw_first_country                                     AS rw_first_country,

    -- 撤稿时滞：优先用 RW 自己算的值（它基于 RW 记录的原始发表日期），
    -- 缺失时退回用 OpenAlex 的发表日期重算。
    -- 两个来源的发表日期可能不完全一致（OpenAlex 用的是它的规范化日期），
    -- 所以**优先用同一个来源内部自洽的值**，而不是混合两个来源算一个时滞。
    COALESCE(
        r.rw_latency_days,
        date_diff('day', w.publication_date, r.retraction_date)
    )                                                      AS retraction_latency_days,

    COALESCE(r.rw_is_paper_mill, 0) = 1                    AS is_paper_mill,
    COALESCE(r.rw_is_peer_review_fraud, 0) = 1             AS is_peer_review_fraud,
    COALESCE(r.rw_is_ai_generated, 0) = 1                  AS is_ai_generated,
    COALESCE(r.rw_is_image_duplication, 0) = 1             AS is_image_duplication,
    COALESCE(r.rw_is_rogue_editor, 0) = 1                  AS is_rogue_editor,

    -- 引用强度：被引量归一化到"每年"，避免老年论文因为活得久而显得更受欢迎。
    -- 这是一个把"存量"转成"流量"的标准处理。
    CASE
        WHEN w.publication_year IS NOT NULL
         AND YEAR(CURRENT_DATE) > w.publication_year
        THEN ROUND(
            w.cited_by_count * 1.0
            / (YEAR(CURRENT_DATE) - w.publication_year), 3)
    END                                                    AS citations_per_year,

    w._source_file,
    w._loaded_at

FROM staging.openalex_works AS w
LEFT JOIN rw_by_doi AS r ON w.doi = r.doi
-- 只保留有 DOI 的作品：没有 DOI 就无法做跨源关联，
-- 而这一步之后的所有分析都依赖这个关联。
WHERE w.doi IS NOT NULL;
