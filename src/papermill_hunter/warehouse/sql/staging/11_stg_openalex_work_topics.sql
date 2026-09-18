-- ============================================================================
-- 11_stg_openalex_work_topics.sql —— 作品主题展开表（一行 = 作品 × 主题）
-- ============================================================================
-- OpenAlex 为每篇作品最多给出 3 个主题（topics），并附带置信度分数。
-- primary_topic 是其中分数最高的一个，已经在 works 表里保留了；
-- 这里把**全部**主题摊开，用于做"跨主题的论文工厂识别"。
--
-- 为什么要全部主题而不只是主主题？
--   论文工厂的选题往往集中在少数几个"好发"的方向上
--   （工程类、计算机应用类、医学病例类）。
--   如果只看主主题，一篇被判定为"工程"的论文里隐藏的"医学"属性就丢失了；
--   而主题共现模式（例如大量论文同时挂"计算机"和"医学"两个相距甚远的主题）
--   本身就是可疑信号 —— 真正的研究通常不会横跨这么远的领域。
-- ============================================================================

CREATE OR REPLACE TABLE staging.openalex_work_topics AS
WITH raw AS (
    SELECT unnest(results) AS w
    FROM read_json_auto('{{OPENALEX_DIR}}/page-*.json.gz')
)

SELECT
    regexp_extract(w.id, '([^/]+)$', 1)         AS openalex_id,
    regexp_extract(t.id, '([^/]+)$', 1)         AS topic_id,
    t.display_name                              AS topic,
    TRY_CAST(t.score AS DOUBLE)                 AS topic_score,
    t.domain.display_name                       AS domain,
    t.field.display_name                        AS field,
    t.subfield.display_name                     AS subfield,
    -- 标记该主题是否为这篇作品的主主题。
    -- 保留这个标记，下游就能按需选择"只看主主题"或"看全部主题"，
    -- 而不必重新回原始 JSON 里算一遍。
    (t.id = w.primary_topic.id)                 AS is_primary

FROM raw,
     unnest(w.topics) AS t(t)
WHERE w.id IS NOT NULL;
