-- ============================================================================
-- 09_stg_openalex_authorship_institutions.sql —— 署名的机构归属（三层展开）
-- ============================================================================
-- 这张表是"三层嵌套展开"：作品 → 署名 → 机构。
-- 一条署名可能挂多个机构（作者同时隶属于大学和研究所），所以还要再展开一层。
--
-- 为什么要费力做这层？
--   因为"哪些机构在被撤稿"是一个高价值问题，而它比"哪些国家"更细、
--   比"哪些作者"更稳健 ——
--   作者姓名有重名和拼写变体问题（见 author_activity 的局限说明），
--   而机构有 ROR 这个权威唯一标识符，可以做可靠的去重和关联。
--   在数据不可靠时，**换一个更可依赖的分析粒度**，往往比硬着头皮清洗原粒度更明智。
--
-- ROR（Research Organization Registry）是学术机构的权威标识体系，
-- 类似"机构的身份证号"。有了它，'Tsinghua Univ.' 和 'Tsinghua University'
-- 才能被确定地识别为同一家机构。
-- ============================================================================

CREATE OR REPLACE TABLE staging.openalex_authorship_institutions AS
WITH works AS (
    SELECT
        regexp_extract(w.id, '([^/]+)$', 1) AS openalex_id,
        w.authorships                        AS authors
    FROM (
        SELECT unnest(results) AS w
        FROM read_json_auto('{{OPENALEX_DIR}}/page-*.json.gz')
    )
),

authorships AS (
    SELECT
        openalex_id,
        i                AS author_seq,
        authors[i]       AS a
    FROM works,
         range(1, len(authors) + 1) AS t(i)
    WHERE len(authors) > 0
)

SELECT
    openalex_id,
    author_seq,
    regexp_extract(a.author.id, '([^/]+)$', 1)  AS author_id,
    a.author.display_name                       AS author_name,
    -- 机构字段
    regexp_extract(inst.id, '([^/]+)$', 1)      AS institution_id,
    inst.display_name                           AS institution_name,
    inst.country_code                           AS country_code,
    inst.type                                   AS institution_type,
    -- ROR 是机构的权威标识，跨数据源关联时优先用它而不是机构名
    regexp_extract(inst.ror, '([^/]+)$', 1)     AS ror

FROM authorships,
     unnest(a.institutions) AS t(inst)
WHERE len(a.institutions) > 0;
