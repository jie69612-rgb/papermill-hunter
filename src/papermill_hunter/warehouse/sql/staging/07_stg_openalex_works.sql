-- ============================================================================
-- 07_stg_openalex_works.sql —— OpenAlex 作品表（一行 = 一篇被撤稿的作品）
-- ============================================================================
-- 【与 Retraction Watch 的处理方式形成鲜明对比：这是 ELT，不是 ETL】
--
--   Retraction Watch 是 CSV，很多清洗（日期解析、多值拆分）用 DuckDB 的宏
--   在 SQL 里做很自然。而 OpenAlex 是**深度嵌套的 JSON**：
--       results[] → authorships[] → institutions[] → lineage[]
--   在 SQL 里逐层 unnest 嵌套结构虽然可行，但可读性会急剧下降。
--
--   所以两种数据源采用了不同策略，而且是有意为之：
--     - CSV 源  → 用 Python/pandas 读，SQL 做清洗（我们已经这么做）
--     - JSON 源 → **原始文件原封不动保留在磁盘上，让 DuckDB 直接读**
--                这叫 ELT（Extract-Load-Transform）：
--                先把原始数据原样加载进来，转换推迟到查询时/建模时做。
--
--   ELT 相对 ETL 的核心优势：
--     1. **原始数据永远可回溯**。ETL 一旦把数据转换后落盘，
--        你就丢失了"原始长什么样"的信息；之后发现转换逻辑写错了，只能重抓。
--     2. **转换逻辑可以随时重写**，不必重新调 API。
--        数据源是稀缺资源（有配额、会限流），转换逻辑是廉价的。
--     3. 出了问题能对比"原始值 vs 清洗后值"，定位是采集问题还是转换问题。
--
--   DuckDB 能直接查 gzip 压缩的 JSON（实测 2000 条约 0.4 秒），
--   这意味着我们**不需要任何导入步骤** —— 原始文件本身就是数据库表。
-- ============================================================================

CREATE OR REPLACE TABLE staging.openalex_works AS
WITH raw AS (
    SELECT
        unnest(results)   AS w,
        -- filename 参数让我们知道这条记录来自哪个分页文件 ——
        -- 和 Retraction Watch 那边的 _source_file 是同一个目的：数据血缘。
        filename          AS _source_file
    FROM read_json_auto(
        '{{OPENALEX_DIR}}/page-*.json.gz',
        filename = true
    )
)

SELECT
    -- ---------------- 标识 ----------------
    -- OpenAlex ID 的完整形式是 'https://openalex.org/W3046275966'，
    -- 这里只保留 'W3046275966'。短 ID 在 join 和展示时都更省事，
    -- 而且前缀永远是同一个，不带任何信息量。
    regexp_extract(w.id, '([^/]+)$', 1)                  AS openalex_id,
    -- 复用 staging/00_init.sql 里定义的 DOI 规范化宏，
    -- 让 OpenAlex 的 DOI 和 Retraction Watch 的 DOI 处于同一格式，
    -- 这是两个数据源能 join 起来的前提。
    rw_normalize_doi(w.doi)                              AS doi,

    -- ---------------- 基本信息 ----------------
    w.title,
    TRY_CAST(w.publication_year AS INTEGER)              AS publication_year,
    TRY_CAST(w.publication_date AS DATE)                 AS publication_date,
    w.type                                               AS work_type,
    w.language,
    w.is_retracted,

    -- ---------------- 影响力 ----------------
    TRY_CAST(w.cited_by_count AS BIGINT)                 AS cited_by_count,
    TRY_CAST(w.referenced_works_count AS BIGINT)         AS referenced_works_count,
    TRY_CAST(w.fwci AS DOUBLE)                           AS fwci,

    -- ---------------- 开放获取 ----------------
    w.open_access.is_oa                                  AS is_oa,
    w.open_access.oa_status                              AS oa_status,
    w.open_access.oa_url                                 AS oa_url,

    -- ---------------- 主题分类（OpenAlex 自己的层级体系） ----------------
    -- OpenAlex 的主题体系是四层：domain > field > subfield > topic。
    -- 保留全部四层，是为了让下游既能按粗粒度（domain：医学/工程/自然科学）
    -- 也能按细粒度（topic：具体研究问题）做分析。
    regexp_extract(w.primary_topic.id, '([^/]+)$', 1)    AS primary_topic_id,
    w.primary_topic.display_name                         AS primary_topic,
    TRY_CAST(w.primary_topic.score AS DOUBLE)            AS primary_topic_score,
    w.primary_topic.domain.display_name                  AS domain,
    w.primary_topic.field.display_name                   AS field,
    w.primary_topic.subfield.display_name                AS subfield,

    -- ---------------- 规模 ----------------
    -- len() 对 JSON 数组返回元素个数；对空数组返回 0，对 NULL 返回 NULL。
    -- 注意这里是**从 JSON 结构里直接数出来的**，和
    -- countries_distinct_count 这类"服务端算好的"字段不同 ——
    -- 两者可以互相对拍，用来验证我们对结构的理解是否正确。
    len(w.authorships)                                   AS n_authors,
    TRY_CAST(w.countries_distinct_count AS INTEGER)      AS countries_distinct_count,
    TRY_CAST(w.institutions_distinct_count AS INTEGER)   AS institutions_distinct_count,

    -- ---------------- 出版信息 ----------------
    w.biblio.volume                                      AS biblio_volume,
    w.biblio.issue                                       AS biblio_issue,
    w.biblio.first_page                                  AS biblio_first_page,
    w.biblio.last_page                                   AS biblio_last_page,

    -- ---------------- 血缘 ----------------
    _source_file,
    CAST(now() AS TIMESTAMP)                             AS _loaded_at

FROM raw
-- 主键为空的行没有分析价值，且会让下游 join 静默丢行
WHERE w.id IS NOT NULL;
