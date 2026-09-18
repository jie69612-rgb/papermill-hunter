-- ============================================================================
-- 01_stg_retraction_watch.sql —— Retraction Watch 清洗表（一行 = 一条撤稿记录）
-- ============================================================================
-- 这一层只做四件事，**不做任何业务判断**：
--   1. 列名规范化（"Record ID" → record_id）
--   2. 类型转换（字符串 → BIGINT / DATE）
--   3. 缺失值统一（'' / 'unavailable' / '0' 等哨兵值 → 真正的 NULL）
--   4. 剔除完全空白行，并记入审计表
--
-- 为什么不在这里算"撤稿时滞""是否论文工厂"这些业务指标？
--   因为那些属于业务口径。业务口径会变（比如"时滞按天还是按月"），
--   而"数据本身长什么样"不会变。把两者混在一起，改一次口径就要重跑整条链路。
--   分层就是为了让变化被隔离在最小的范围内。
-- ============================================================================

CREATE OR REPLACE TABLE staging.retraction_watch AS
WITH raw AS (
    SELECT
        -- 原始 CSV 每行末尾多一个逗号，DuckDB 会把它命名成 column20。
        -- 这是"上游数据格式瑕疵"，在 staging 层就地消化掉，
        -- 不让它传播到下游任何一张表。
        -- 注意：EXCLUDE 必须紧跟在 `*` 之后，不能写到 FROM 子句后面。
        * EXCLUDE (column20),
        -- 记录数据来源文件，便于追溯"这条记录是哪天那份数据里的"
        '{{RETRACTION_WATCH_CSV}}' AS _source_file,
        -- 注意这里显式 CAST 成 TIMESTAMP（不带时区）。
        -- 原因：now() 返回的是 TIMESTAMP WITH TIME ZONE，而 DuckDB 的 Python
        -- 客户端要把带时区的时间戳转成 Python 对象时**依赖 pytz 库**；
        -- 没装就会在 fetchall() 时报 "Required module 'pytz' failed to import"。
        -- 我们在连接时已经 SET TimeZone='UTC'，所以转成 naive 时间戳是确定性的。
        -- 结论：不为了一个显示需求去增加依赖，而是把类型收窄成真正需要的样子。
        CAST(now() AS TIMESTAMP) AS _loaded_at
    FROM read_csv(
        '{{RETRACTION_WATCH_CSV}}',
        header = true,
        -- 全部按字符串读入，理由见下方 record_id 的说明
        all_varchar = true
    )
)

SELECT
    -- ---------------- 标识 ----------------
    -- 为什么用 TRY_CAST 而不是 CAST？
    --   万一上游出现一条 Record ID 非数字的记录，CAST 会让整个构建任务崩掉。
    --   TRY_CAST 转成 NULL，我们事后能通过"主键为空"的数据质量检查发现它，
    --   而不是让整条管道停摆。
    --   原则：**清洗层应当对脏数据有韧性，把判断权交给质量检查，而不是崩溃。**
    TRY_CAST("Record ID" AS BIGINT)                     AS record_id,

    -- ---------------- 内容 ----------------
    rw_clean_text("Title")                              AS title,
    rw_clean_text("Journal")                            AS journal,
    rw_clean_text("Publisher")                          AS publisher,
    rw_clean_text("Institution")                        AS institution_raw,
    rw_clean_text("Author")                             AS author_raw,
    rw_clean_text("Country")                            AS country_raw,
    rw_clean_text("Subject")                            AS subject_raw,
    rw_clean_text("ArticleType")                        AS article_type_raw,
    rw_clean_text("Reason")                             AS reason_raw,
    rw_clean_text("Notes")                              AS notes,
    rw_clean_text("URLS")                               AS urls,

    -- ---------------- 日期 ----------------
    rw_parse_date("RetractionDate")                     AS retraction_date,
    rw_parse_date("OriginalPaperDate")                  AS original_paper_date,
    YEAR(rw_parse_date("RetractionDate"))               AS retraction_year,
    YEAR(rw_parse_date("OriginalPaperDate"))            AS original_paper_year,

    -- ---------------- 标识符（跨源关联用） ----------------
    rw_normalize_doi("RetractionDOI")                   AS retraction_doi,
    rw_normalize_doi("OriginalPaperDOI")                AS original_paper_doi,
    rw_normalize_pmid("RetractionPubMedID")             AS retraction_pubmed_id,
    rw_normalize_pmid("OriginalPaperPubMedID")          AS original_paper_pubmed_id,

    -- ---------------- 分类 ----------------
    -- 官方文档：Retraction / Correction / Expression of concern / Reinstatement
    rw_clean_text("RetractionNature")                   AS retraction_nature,
    rw_clean_text("Paywalled")                          AS paywalled,

    -- ---------------- 血缘 ----------------
    _source_file,
    _loaded_at

FROM raw
-- ----------------------------------------------------------------------------
-- 剔除完全空白行。
--
-- 这份数据里有 149 行**所有字段都是空的**。我们做过交叉验证：
-- Python 的 csv 模块同样读出了这 149 行空白，所以它是**上游数据本身的瑕疵**，
-- 不是 DuckDB 的解析假象。
--
-- 这个区分很重要：如果不做交叉验证，你可能会花时间去"修"一个不存在的解析 bug。
-- 遇到可疑数据时，用第二个独立工具验证一遍，是最省时间的做法。
--
-- 这些行不含任何信息，留在表里只会让所有下游统计都要额外写一遍过滤条件，
-- 所以在这里一次性剔除，并把剔除数量记入审计表（见下方 load_audit）。
-- ----------------------------------------------------------------------------
WHERE TRIM(COALESCE("Record ID", '')) <> '';


-- ============================================================================
-- 加载审计表（load audit）
-- ============================================================================
-- 为什么要有这张表？
--   数据管道最怕的不是"报错"，而是**静默地少数据**。
--   如果某天上游突然少给了 1 万条记录，你的所有报表都会照常产出，
--   只是数字悄悄变小了 —— 没有人会发现。
--
--   把"读入多少行 / 保留多少行 / 丢弃多少行"显式记录下来，
--   才能让这类问题变成"一眼可见"而不是"永远不知道"。
--   这就是数据可观测性（data observability）最基本的实践。
-- ============================================================================
CREATE OR REPLACE TABLE staging.load_audit AS
WITH counts AS (
    SELECT
        (SELECT COUNT(*) FROM read_csv(
            '{{RETRACTION_WATCH_CSV}}', header = true, all_varchar = true
        ))                                   AS rows_read,
        (SELECT COUNT(*) FROM staging.retraction_watch) AS rows_kept
)
SELECT
    'retraction_watch'      AS dataset,
    '{{RETRACTION_WATCH_CSV}}' AS source_file,
    rows_read,
    rows_kept,
    rows_read - rows_kept   AS rows_dropped,
    -- 丢弃原因写清楚，半年后回来看这张表时才不会一脸茫然
    '完全空白行（上游数据瑕疵，已交叉验证）' AS drop_reason,
    CAST(now() AS TIMESTAMP) AS loaded_at
FROM counts;
