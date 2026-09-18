-- ============================================================================
-- 03_stg_retraction_countries.sql —— 国家/地区展开表
-- ============================================================================
-- 作者机构可能横跨多国（国际合作论文），原始字段形如 'China;United States'。
-- 若直接对原始字段分组，'China' 和 'China;United States' 会变成两个不同的类别，
-- 导致中国的真实占比被严重低估 —— 这类"看起来在统计、实际统计错了"的问题
-- 是最危险的，因为它不会报错，只会给你一个看似合理的错误答案。
-- ============================================================================

CREATE OR REPLACE TABLE staging.retraction_countries AS
SELECT
    r.record_id,
    country
FROM staging.retraction_watch AS r,
     unnest(rw_split(r.country_raw)) AS t(country)
WHERE r.record_id IS NOT NULL
  AND country <> '';
