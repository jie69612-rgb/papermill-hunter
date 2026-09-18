-- ============================================================================
-- 06_int_work_citation_event_panel.sql —— 引用轨迹事件面板
-- ============================================================================
-- 这是因果推断的**数据底座**。一行 = 一篇作品在某个"相对撤稿时点"的被引次数。
--
-- 【为什么要用"相对时点"，而不是日历年？】
--   如果把所有论文按日历年堆在一起看引用趋势，你会被两件事污染：
--     1. 不同年份撤稿的论文，其"论文年龄"分布不同
--     2. 2023 年撤稿特别多，会把 2023 年前后的曲线整体拉高
--
--   事件研究法（Event Study）的做法是：**以每篇论文自己的撤稿年份为原点**，
--   把时间轴换算成"撤稿前第几年 / 撤稿后第几年"。
--     事件时间 = 引用年份 − 撤稿年份
--        负值 → 撤稿之前
--        0    → 撤稿当年
--        正值 → 撤稿之后
--   这样每篇论文都在同一个"自身坐标系"里，横向可比。
--
-- 【为什么必须做成"平衡面板"—— 这是本文件最关键的取舍】
--   如果只要求"有数据就保留"，会出现**样本构成漂移**：
--     事件时间 = −3 时，只有那些发表较早的论文才有数据；
--     事件时间 = +2 时，换成另一批论文。
--   于是不同时点上的平均引用差异，可能完全来自"样本换了一批人"，
--   而不是来自撤稿的因果效应。这是事件研究里最经典的伪结论来源。
--
--   所以我们强制要求：**每篇进入面板的论文，在窗口内每个时点都必须存在**
--   （即使那一年被引为 0）。条件：
--       publication_year <= retraction_year - 3   保证完整的撤稿前窗口
--       retraction_year + 2 <= 2025               保证完整的撤稿后窗口
--   即窗口取 −3 … +2，共 6 个时点。
--
--   【这个窗口是怎么定下来的 —— 一个真实的取舍】
--     我们本可以用更长的窗口（例如 −5 … +5），但那样就必须要求
--     retraction_year <= 2020，会**把 2023 年那批大规模撤稿整个排除掉** ——
--     而那恰恰是本项目最想研究的对象（2023 年一年撤稿 13,564 篇）。
--     最终选择"较短的窗口 + 保住 2023 年样本"。
--     上界 2025 而不是 2026，是因为当前年份尚未结束，
--     用不完整年份做对比会系统性低估最后一个时点的引用量（partial-year 偏差）。
--
--     —— 样本量和窗口长度的取舍没有标准答案，但**必须显式说明并写进文档**。
--        藏起来的取舍，就是别人复现不出来你的结论的原因。
--
-- 【零值填充】
--   OpenAlex 的 counts_by_year 只列出有引用的年份，被引为 0 的年份直接省略。
--   面板里必须把缺失年份补成 0 —— 否则算平均值时会把"引用为零"的论文整篇丢掉，
--   系统性地高估平均被引量。缺少的行本身就是信息。
-- ============================================================================

CREATE OR REPLACE TABLE intermediate.work_citation_event_panel AS
WITH eligible AS (
    SELECT
        openalex_id,
        publication_year,
        retraction_year,
        is_paper_mill,
        is_peer_review_fraud,
        is_ai_generated,
        field,
        domain,
        cited_by_count,
        n_authors,
        matched_in_retraction_watch
    FROM intermediate.works_enriched
    WHERE retraction_year IS NOT NULL
      AND publication_year IS NOT NULL
      -- 平衡面板的两个硬性条件（详见上方说明）
      AND publication_year <= retraction_year - 3
      AND retraction_year + 2 <= 2025
),

-- 生成"作品 × 事件时间"的完整网格。
-- 注意这里用 range() 生成的是**所有**时点，无论该年是否真的有引用 ——
-- 这正是"补零"的实现方式：先造全网格，再左连接真实数据。
grid AS (
    SELECT
        e.*,
        ev AS event_time,
        e.retraction_year + ev AS citation_year
    FROM eligible AS e,
         range(-3, 3) AS t(ev)          -- range(-3, 3) → -3, -2, -1, 0, 1, 2
)

SELECT
    g.openalex_id,
    g.event_time,
    g.citation_year,
    g.publication_year,
    g.retraction_year,

    -- 论文年龄：从发表到该引用年份经过了多少年。
    -- 保留它是为了能控制"论文老化"这个混杂因素 ——
    -- 引用量随论文年龄自然衰减，如果不控制它，
    -- 就无法区分"撤稿导致引用下降"和"论文本来就在变老"。
    g.citation_year - g.publication_year            AS years_since_publication,

    -- 关键因变量：该年的被引次数，缺失补 0
    COALESCE(c.cited_by_count, 0)                   AS cited_by_count,
    -- 显式标记这一行是"真实的 0"还是"补出来的 0"。
    -- 两者在业务上含义不同：真实的 0 表示"这一年确实没人引"，
    -- 补出来的 0 表示"OpenAlex 没给出这一年的记录"。
    -- 虽然数值上都是 0，但保留这个标记能让下游在需要时区分处理，
    -- 也方便我们事后核查补零逻辑是否合理。
    (c.cited_by_count IS NOT NULL)                  AS has_observed_data,

    g.is_paper_mill,
    g.is_peer_review_fraud,
    g.is_ai_generated,
    g.field,
    g.domain,
    g.cited_by_count                                AS total_cited_by_count,
    g.n_authors

FROM grid AS g
LEFT JOIN staging.openalex_work_citations AS c
       ON g.openalex_id = c.openalex_id
      AND g.citation_year = c.citation_year;
