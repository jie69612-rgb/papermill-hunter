-- ============================================================================
-- 04_stg_retraction_subjects.sql —— 学科领域展开表
-- ============================================================================
-- Subject 字段使用 Retraction Watch 自建的学科分类体系，形如：
--     '(B/T) Computer Science;(HSC) Medicine - Oncology;'
-- 括号里的前缀是它的大类编码（B/T = Basic/Technical，HSC = Health Sciences 等）。
-- 这里先保留原始值，把"拆前缀"这个动作留给 intermediate 层 ——
-- 因为那属于口径解释，不是数据清洗。
-- ============================================================================

CREATE OR REPLACE TABLE staging.retraction_subjects AS
SELECT
    r.record_id,
    subject
FROM staging.retraction_watch AS r,
     unnest(rw_split(r.subject_raw)) AS t(subject)
WHERE r.record_id IS NOT NULL
  AND subject <> '';
