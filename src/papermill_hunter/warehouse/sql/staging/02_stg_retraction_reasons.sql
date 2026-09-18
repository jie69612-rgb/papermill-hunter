-- ============================================================================
-- 02_stg_retraction_reasons.sql —— 撤稿原因展开表
-- ============================================================================
-- 一条撤稿记录往往有**多个**原因，原始数据把它们挤在一个分号分隔的字段里：
--     'Compromised Peer Review;Investigation by Journal/Publisher;Rogue Editor;'
--
-- 统计"哪种撤稿原因最常见"时，必须先把它们摊开成"一行一个原因"，
-- 否则你会把整串组合当成一个独立的类别，得到的结论毫无意义
-- （会出现"Compromised Peer Review;Rogue Editor 这一组合出现 63 次"这种没用的排名）。
--
-- 这就是数据库设计里经典的**一对多展开（unnest / explode）**。
-- 代价是记录数会膨胀（一条撤回变成 N 行），所以下游做计数时
-- 要注意区分"撤稿记录数"和"原因出现次数"——这是最容易搞混的地方之一。
-- ============================================================================

CREATE OR REPLACE TABLE staging.retraction_reasons AS
SELECT
    r.record_id,
    reason
FROM staging.retraction_watch AS r,
     -- DuckDB 的横向展开：对每一行，把列表里的元素摊成多行
     unnest(rw_split(r.reason_raw)) AS t(reason)
WHERE r.record_id IS NOT NULL
  -- rw_split 已经过滤过空片段，这里再判一次是"防御性编程"：
  -- 万一将来宏的实现被改动，这一层仍然不会漏出空字符串分类。
  AND reason <> '';
