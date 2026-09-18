-- ============================================================================
-- 12_stg_openalex_audit.sql —— 把 OpenAlex 的加载情况追加进审计表
-- ============================================================================
-- 审计表 staging.load_audit 由 01_stg_retraction_watch.sql 创建，
-- 这里用 INSERT 追加 OpenAlex 的加载记录。
--
-- 【为什么审计比校验更重要】
--   质量校验（warehouse/quality.py）检查的是"数据是否符合预期规则"；
--   审计记录的是"这次加载到底发生了什么"。
--   两者互补：校验告诉你"结果对不对"，审计告诉你"过程发生了什么"。
--   当校验失败时，你第一个会去看的就是审计表 ——
--   读进来多少？保留多少？丢了多少？为什么丢？
--   没有审计，你只能靠猜。
-- ============================================================================

INSERT INTO staging.load_audit
WITH counts AS (
    SELECT
        (SELECT COUNT(*) FROM (
            SELECT unnest(results) AS w
            FROM read_json_auto('{{OPENALEX_DIR}}/page-*.json.gz')
        ))                                                   AS rows_read,
        (SELECT COUNT(*) FROM staging.openalex_works)        AS rows_kept
)
SELECT
    'openalex_retracted_works'   AS dataset,
    '{{OPENALEX_DIR}}/page-*.json.gz' AS source_file,
    rows_read,
    rows_kept,
    rows_read - rows_kept        AS rows_dropped,
    'openalex_id 为空的作品（无法作为主键，会让下游 join 静默丢行）' AS drop_reason,
    CAST(now() AS TIMESTAMP)     AS loaded_at
FROM counts;
