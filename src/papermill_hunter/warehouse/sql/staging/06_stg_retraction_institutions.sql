-- ============================================================================
-- 06_stg_retraction_institutions.sql —— 机构展开表
-- ============================================================================
-- 机构字段是自由文本（作者在论文里自己写的），格式极不统一：
--     'G.Narayanamma Institute of Technology and Science, Hyderabad, India'
--     'Computer Science and Engineering, Jaypee University of Engineering...'
-- 也就是说，这里既有机构名，也有院系名和城市/国家。
--
-- 【本层不做的事】不做机构名称归一化。
--   那需要机构消歧（同一个机构有几十种写法），属于专门问题，
--   通常要借助 ROR（Research Organization Registry）之类的权威库。
--   在 staging 层硬做模糊匹配，只会制造出"看起来规范、实际错配"的脏数据。
--   本项目在 intermediate 层做**保守的**字符串归一（去标点、统一大小写），
--   并明确记录其局限 —— 承认方法的边界，比假装它很准要专业得多。
-- ============================================================================

CREATE OR REPLACE TABLE staging.retraction_institutions AS
SELECT
    r.record_id,
    institution
FROM staging.retraction_watch AS r,
     unnest(rw_split(r.institution_raw)) AS t(institution)
WHERE r.record_id IS NOT NULL
  AND institution <> '';
