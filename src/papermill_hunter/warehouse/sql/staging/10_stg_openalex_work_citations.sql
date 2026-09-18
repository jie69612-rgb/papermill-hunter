-- ============================================================================
-- 10_stg_openalex_work_citations.sql —— 逐年引用轨迹（一行 = 一篇作品在某年被引次数）
-- ============================================================================
-- ★ 这张表是整个项目做**因果推断**的基础，重要性怎么强调都不过分。
--
-- 【为什么需要"逐年"引用，而不是一个总被引数？】
--   如果只有"这篇论文总共被引 500 次"，你无法回答：
--       - 撤稿之后，它的被引量是**下降**了，还是继续增长？
--       - 下降是**因为撤稿**，还是因为它本来就过了引用高峰？
--   这两个问题都要求我们看到**撤稿前后每一年的引用变化** ——
--   也就是一条时间序列，而不是一个标量。
--
--   有了逐年轨迹，我们才能构造因果推断里最核心的那个对照：
--       处理组（被撤稿论文）的引用轨迹
--           vs
--       对照组（同类未撤稿论文）的引用轨迹
--   两者之差，才是"撤稿造成的净影响"。
--   这比"撤稿论文引用量很低"这种描述性说法有力得多 ——
--   因为后者无法排除"这些论文本来就没人引"这个替代解释。
--
-- 【关于 counts_by_year 的一个使用陷阱】
--   OpenAlex 的 counts_by_year 只列出**有被引的年份**，被引为 0 的年份直接省略。
--   所以如果直接用这张表算"某年平均被引"，会把没有引用的年份漏掉，
--   系统性地**高估**平均被引量。
--   下游做时间序列分析时，必须先把缺失年份补 0（这属于 intermediate 层的职责）。
--   这是"稀疏存储"数据结构常见的坑：数据没丢，但缺的行本身携带信息。
-- ============================================================================

CREATE OR REPLACE TABLE staging.openalex_work_citations AS
WITH raw AS (
    SELECT unnest(results) AS w
    FROM read_json_auto('{{OPENALEX_DIR}}/page-*.json.gz')
)

SELECT
    regexp_extract(w.id, '([^/]+)$', 1)      AS openalex_id,
    TRY_CAST(w.publication_year AS INTEGER)  AS publication_year,
    TRY_CAST(c.year AS INTEGER)              AS citation_year,
    TRY_CAST(c.cited_by_count AS BIGINT)     AS cited_by_count,
    -- 引用发生的"年龄"：这篇论文发表后第几年产生的引用。
    -- 事件研究法需要以"相对撤稿的时间"为横轴，年龄是构造它的基础。
    TRY_CAST(c.year AS INTEGER)
        - TRY_CAST(w.publication_year AS INTEGER) AS years_since_publication

FROM raw,
     unnest(w.counts_by_year) AS t(c)
WHERE w.id IS NOT NULL
  AND c.year IS NOT NULL;
