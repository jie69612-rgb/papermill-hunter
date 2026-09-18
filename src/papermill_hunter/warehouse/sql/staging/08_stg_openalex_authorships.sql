-- ============================================================================
-- 08_stg_openalex_authorships.sql —— 署名表（一行 = 一位作者在一篇作品上的署名）
-- ============================================================================
-- 【为什么用 range() 下标而不是直接 unnest？】
--   DuckDB 的 unnest 会把数组摊成多行，但它**不返回元素的下标**。
--   而署名顺序在学术分析里是有信息量的（首位＝主要贡献者，末位＝通讯作者/导师），
--   论文工厂的挂名模式恰恰体现在顺序异常上。
--
--   常见做法是"展开后再用 row_number() 编号"，但这是错的：
--   row_number() 的排序在并行执行下**没有保证**，
--   你会得到一个顺序随机的编号，而且它不报错 —— 又是一个静默错误。
--
--   正确做法是用 range(1, len(list)+1) 生成下标，再按下标取值
--   （DuckDB 的列表索引从 1 开始）。这样顺序严格等于原始 JSON 数组顺序。
--   本项目在 Retraction Watch 的作者展开里用了同样的手法 ——
--   同一个问题用同一种解法，保持一致性。
-- ============================================================================

CREATE OR REPLACE TABLE staging.openalex_authorships AS
WITH expanded AS (
    SELECT
        regexp_extract(w.id, '([^/]+)$', 1) AS openalex_id,
        w.authorships                        AS authors
    FROM (
        SELECT unnest(results) AS w
        FROM read_json_auto('{{OPENALEX_DIR}}/page-*.json.gz')
    )
)

SELECT
    openalex_id,
    i                                                       AS author_seq,
    authors[i].author_position                              AS author_position,
    regexp_extract(authors[i].author.id, '([^/]+)$', 1)     AS author_id,
    authors[i].author.display_name                          AS author_name,
    -- 原始署名串（作者在论文里实际写的样子）也保留：
    -- OpenAlex 的 display_name 是规范化过的，而"原始写法"本身可能是个特征
    -- （论文工厂生成的论文常有异常的署名格式）。
    authors[i].raw_author_name                              AS raw_author_name,
    regexp_extract(authors[i].author.orcid, '([^/]+)$', 1)  AS orcid,
    authors[i].is_corresponding                             AS is_corresponding,
    len(authors[i].institutions)                            AS n_institutions,
    authors[i].countries                                    AS countries

FROM expanded,
     range(1, len(authors) + 1) AS t(i)
WHERE len(authors) > 0;
