-- ============================================================================
-- 05_stg_retraction_authors.sql —— 作者展开表（带署名顺序）
-- ============================================================================
-- 为什么要保留"署名顺序"？
--   学术署名顺序有明确含义：第一位通常是主要贡献者，最后一位通常是通讯作者/导师。
--   论文工厂的一个典型特征是**署名顺序异常**——
--   比如同一批人反复以不同顺序互相挂名，或出现大量与论文主题毫无关系的挂名作者。
--   丢掉顺序信息，这些信号就没了。
--
-- 【实现说明】怎么在展开的同时拿到序号？
--   DuckDB 的 unnest 本身不返回下标，所以这里用 range() 生成下标序列，
--   再按 1-based 下标去取列表元素（DuckDB 的列表索引从 1 开始，不是 0）。
--   这比"先展开再 row_number()"更可靠 —— 后者的排序在并行执行下没有保证，
--   你会得到一个"顺序随机"的序号，而且不报错。又是一个静默错误的例子。
-- ============================================================================

CREATE OR REPLACE TABLE staging.retraction_authors AS
WITH exploded AS (
    SELECT
        record_id,
        rw_split(author_raw) AS authors
    FROM staging.retraction_watch
    WHERE record_id IS NOT NULL
)

SELECT
    record_id,
    i              AS author_position,
    authors[i]     AS author_name
FROM exploded,
     range(1, len(authors) + 1) AS t(i)
WHERE len(authors) > 0;
