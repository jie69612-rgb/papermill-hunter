-- ============================================================================
-- 07_int_coauthorship_edges.sql —— 撤稿论文的合作网络边表
-- ============================================================================
-- 一行 = 一对共同署名过的作者，以及他们一起发表了多少篇**后来被撤稿**的论文。
--
-- 【为什么合作网络能识别论文工厂】
--   论文工厂不只生产论文，还生产**署名**。
--   为了让论文看起来可信，它需要给每篇论文凑齐一组"看起来像研究者"的作者。
--   于是同一批姓名会在**不同机构、不同期刊、不同学科**的论文里反复共同出现。
--
--   真实的研究合作网络长什么样？
--      - 社群结构强：一个课题组内部高频合作，跨组合作少
--      - 合作关系与机构、学科高度相关
--      - 合作强度随时间是渐进的
--
--   而流水线式的挂名网络长什么样？
--      - **同一批人反复共现**，且跨越大量互不相关的期刊
--      - 合作对象与机构归属对不上（同一位作者在不同论文里挂不同机构）
--      - 短时间内密集产生
--
--   这张表就是把这些信号量化出来的原材料。下游用图算法做社群发现，
--   再把每个社群的特征（规模、密度、论文工厂占比、期刊跨度）算出来排序。
--
-- 【生成方法：自连接】
--   要给每篇论文生成"所有作者两两配对"，就把它自己的作者表和它自己连接一次。
--   关键在于 `a.author_id < b.author_id` 这个条件，它一次解决三个问题：
--     1. 去重：A-B 和 B-A 是同一对，只保留一个方向
--     2. 排除自环：A-A 没有意义
--     3. 效率：把结果集直接减半
--   —— 这是图数据建模里生成无向边集合的标准写法。
-- ============================================================================

CREATE OR REPLACE TABLE intermediate.coauthorship_edges AS
WITH pairs AS (
    SELECT
        a.openalex_id,
        a.author_id     AS author_a,
        b.author_id     AS author_b
    FROM staging.openalex_authorships AS a
    INNER JOIN staging.openalex_authorships AS b
            ON a.openalex_id = b.openalex_id
           AND a.author_id < b.author_id
    -- OpenAlex 里少数署名没有作者 ID（无法识别身份），排除掉。
    -- 用 ID 而不是姓名做节点，是因为姓名有重名和拼写变体问题 ——
    -- 这是我们在 author_activity 里已经承认过的局限，
    -- 而 OpenAlex 的作者 ID 是消歧过的，可靠性高一个量级。
    WHERE a.author_id IS NOT NULL
      AND b.author_id IS NOT NULL
)

SELECT
    p.author_a,
    p.author_b,

    -- ---------------- 合作强度 ----------------
    COUNT(DISTINCT p.openalex_id)                                  AS n_shared_works,
    SUM(CASE WHEN w.is_paper_mill THEN 1 ELSE 0 END)               AS n_shared_paper_mill,
    ROUND(
        1.0 * SUM(CASE WHEN w.is_paper_mill THEN 1 ELSE 0 END)
        / COUNT(DISTINCT p.openalex_id), 4
    )                                                              AS shared_paper_mill_share,

    -- ---------------- 跨度 ----------------
    -- 跨度是关键的判别特征：真实的长期合作会集中在同一学科/期刊，
    -- 而挂名网络往往横跨大量互不相关的领域。
    COUNT(DISTINCT w.rw_journal)                                   AS n_journals,
    COUNT(DISTINCT w.field)                                        AS n_fields,
    COUNT(DISTINCT w.rw_first_country)                             AS n_countries,
    MIN(w.publication_year)                                        AS first_year,
    MAX(w.publication_year)                                        AS last_year,
    MAX(w.publication_year) - MIN(w.publication_year)              AS year_span,
    -- 发表集中度：全部合作集中在几年内？
    -- 越大说明产出越"爆发"，越小说明是长期合作。
    ROUND(
        1.0 * COUNT(DISTINCT p.openalex_id)
        / GREATEST(MAX(w.publication_year) - MIN(w.publication_year) + 1, 1), 2
    )                                                              AS works_per_year

FROM pairs AS p
INNER JOIN intermediate.works_enriched AS w
        ON p.openalex_id = w.openalex_id
GROUP BY p.author_a, p.author_b;
