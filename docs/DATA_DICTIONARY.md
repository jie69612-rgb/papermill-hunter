# 数据字典

> 自动生成于 2026-09-18 16:14　|　共 **32** 张表、**413** 个字段

本文件由 `scripts/07_generate_data_dictionary.py` 从数据库元数据生成。
**表结构部分永远与代码同步**（因为它就是查询数据库得到的），
语义说明部分由人工维护，未覆盖的字段会被显式标记。

---

## 分层说明

- **`staging`** —— **清洗层** —— 只做类型转换、缺失值统一、多值展开。不做任何业务判断。
- **`intermediate`** —— **口径层** —— 引入业务定义、合并多源、构造分析所需的结构。
- **`marts`** —— **交付层** —— 可直接用于看板与报告的结果表，每张表回答一个明确问题。

---

## staging

### `load_audit`

**行数**：2　|　**用途**：**加载审计表**。记录每次加载读入/保留/丢弃了多少行以及为什么，防止数据静默缺失

| 字段 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `dataset` | VARCHAR | YES | — |
| `source_file` | VARCHAR | YES | — |
| `rows_read` | BIGINT | YES | — |
| `rows_kept` | BIGINT | YES | — |
| `rows_dropped` | BIGINT | YES | 被丢弃的行数。当前：RW 丢弃 149 行完全空白行；OpenAlex 丢弃 0 行 |
| `drop_reason` | VARCHAR | YES | — |
| `loaded_at` | TIMESTAMP | YES | — |

### `openalex_authorship_institutions`

**行数**：578,718　|　**用途**：署名的机构归属（三层嵌套展开：作品 → 署名 → 机构）

| 字段 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `openalex_id` | VARCHAR | YES | 标识/血缘字段 |
| `author_seq` | BIGINT | YES | — |
| `author_id` | VARCHAR | YES | 标识/血缘字段 |
| `author_name` | VARCHAR | YES | — |
| `institution_id` | VARCHAR | YES | — |
| `institution_name` | VARCHAR | YES | — |
| `country_code` | VARCHAR | YES | — |
| `institution_type` | VARCHAR | YES | — |
| `ror` | VARCHAR | YES | Research Organization Registry ID，机构的权威唯一标识 |

### `openalex_authorships`

**行数**：504,077　|　**用途**：署名表。用 range() 下标展开，保证顺序严格等于原始 JSON 数组

| 字段 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `openalex_id` | VARCHAR | YES | 标识/血缘字段 |
| `author_seq` | BIGINT | YES | — |
| `author_position` | VARCHAR | YES | — |
| `author_id` | VARCHAR | YES | OpenAlex 消歧过的作者 ID，比姓名可靠得多 |
| `author_name` | VARCHAR | YES | — |
| `raw_author_name` | VARCHAR | YES | — |
| `orcid` | VARCHAR | YES | — |
| `is_corresponding` | BOOLEAN | YES | — |
| `n_institutions` | BIGINT | YES | — |
| `countries` | VARCHAR[] | YES | — |

### `openalex_work_citations`

**行数**：293,747　|　**用途**：**逐年引用轨迹**。因果推断的核心数据，做事件研究的基础

| 字段 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `openalex_id` | VARCHAR | YES | 标识/血缘字段 |
| `publication_year` | INTEGER | YES | — |
| `citation_year` | INTEGER | YES | — |
| `cited_by_count` | BIGINT | YES | 该年的被引次数。**OpenAlex 省略被引为 0 的年份** |
| `years_since_publication` | INTEGER | YES | 引用年份 − 发表年份。**约 0.77% 为负值** —— 经核查是上游特性（撤稿声明继承了原论文的引用轨迹），不是解析错误，详见 quality.py |

### `openalex_work_topics`

**行数**：352,915　|　**用途**：作品主题展开表（OpenAlex 四层主题体系：domain/field/subfield/topic）

| 字段 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `openalex_id` | VARCHAR | YES | 标识/血缘字段 |
| `topic_id` | VARCHAR | YES | — |
| `topic` | VARCHAR | YES | — |
| `topic_score` | DOUBLE | YES | — |
| `domain` | VARCHAR | YES | — |
| `field` | VARCHAR | YES | — |
| `subfield` | VARCHAR | YES | — |
| `is_primary` | BOOLEAN | YES | — |

### `openalex_works`

**行数**：135,584　|　**用途**：OpenAlex 被撤稿作品表。一行一篇作品

| 字段 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `openalex_id` | VARCHAR | YES | OpenAlex 作品 ID（已去掉 https://openalex.org/ 前缀） |
| `doi` | VARCHAR | YES | DOI，已用与 Retraction Watch 相同的宏规范化 |
| `title` | VARCHAR | YES | — |
| `publication_year` | INTEGER | YES | — |
| `publication_date` | DATE | YES | — |
| `work_type` | VARCHAR | YES | — |
| `language` | VARCHAR | YES | — |
| `is_retracted` | BOOLEAN | YES | OpenAlex 的撤稿标记。注意它标记的是**作品**，不是撤稿事件 |
| `cited_by_count` | BIGINT | YES | — |
| `referenced_works_count` | BIGINT | YES | — |
| `fwci` | DOUBLE | YES | 领域加权引用影响力（Field-Weighted Citation Impact） |
| `is_oa` | BOOLEAN | YES | — |
| `oa_status` | VARCHAR | YES | — |
| `oa_url` | VARCHAR | YES | — |
| `primary_topic_id` | VARCHAR | YES | — |
| `primary_topic` | VARCHAR | YES | — |
| `primary_topic_score` | DOUBLE | YES | — |
| `domain` | VARCHAR | YES | — |
| `field` | VARCHAR | YES | — |
| `subfield` | VARCHAR | YES | — |
| `n_authors` | BIGINT | YES | — |
| `countries_distinct_count` | INTEGER | YES | — |
| `institutions_distinct_count` | INTEGER | YES | — |
| `biblio_volume` | VARCHAR | YES | — |
| `biblio_issue` | VARCHAR | YES | — |
| `biblio_first_page` | VARCHAR | YES | — |
| `biblio_last_page` | VARCHAR | YES | — |
| `_source_file` | VARCHAR | YES | 标识/血缘字段 |
| `_loaded_at` | TIMESTAMP | YES | 标识/血缘字段 |

### `retraction_authors`

**行数**：307,596　|　**用途**：作者展开表，保留署名顺序（首/末位在学术上有含义）

| 字段 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `record_id` | BIGINT | YES | — |
| `author_position` | BIGINT | YES | — |
| `author_name` | VARCHAR | YES | — |

### `retraction_countries`

**行数**：91,618　|　**用途**：国家展开表。一行 = 记录 × 国家

| 字段 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `record_id` | BIGINT | YES | — |
| `country` | VARCHAR | YES | — |

### `retraction_institutions`

**行数**：169,576　|　**用途**：机构展开表（自由文本，未做机构消歧）

| 字段 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `record_id` | BIGINT | YES | — |
| `institution` | VARCHAR | YES | — |

### `retraction_reasons`

**行数**：281,827　|　**用途**：撤稿原因**展开表**。一行 = 记录 × 原因（一对多展开，行数会膨胀）

| 字段 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `record_id` | BIGINT | YES | — |
| `reason` | VARCHAR | YES | — |

### `retraction_subjects`

**行数**：197,446　|　**用途**：学科展开表。一行 = 记录 × 学科

| 字段 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `record_id` | BIGINT | YES | — |
| `subject` | VARCHAR | YES | — |

### `retraction_watch`

**行数**：72,453　|　**用途**：Retraction Watch 清洗表。一行一条撤稿记录，已做类型转换与缺失值统一

| 字段 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `record_id` | BIGINT | YES | Retraction Watch 内部记录 ID，本表主键 |
| `title` | VARCHAR | YES | — |
| `journal` | VARCHAR | YES | — |
| `publisher` | VARCHAR | YES | — |
| `institution_raw` | VARCHAR | YES | — |
| `author_raw` | VARCHAR | YES | — |
| `country_raw` | VARCHAR | YES | 作者所属国原始串，分号分隔多值 |
| `subject_raw` | VARCHAR | YES | — |
| `article_type_raw` | VARCHAR | YES | — |
| `reason_raw` | VARCHAR | YES | 撤稿原因原始串，分号分隔多值且**带尾分号**，用 rw_split() 拆分 |
| `notes` | VARCHAR | YES | — |
| `urls` | VARCHAR | YES | — |
| `retraction_date` | DATE | YES | 撤稿声明发布日期。解析自美式格式，含 AM/PM 兜底 |
| `original_paper_date` | DATE | YES | 原文发表日期。149 条为空 |
| `retraction_year` | BIGINT | YES | — |
| `original_paper_year` | BIGINT | YES | — |
| `retraction_doi` | VARCHAR | YES | — |
| `original_paper_doi` | VARCHAR | YES | 原文 DOI，**跨源关联的唯一钥匙**（已去前缀、转小写） |
| `retraction_pubmed_id` | VARCHAR | YES | — |
| `original_paper_pubmed_id` | VARCHAR | YES | — |
| `retraction_nature` | VARCHAR | YES | 撤稿性质：Retraction / Correction / Expression of concern / Reinstatement |
| `paywalled` | VARCHAR | YES | — |
| `_source_file` | VARCHAR | YES | 数据血缘：这条记录来自哪份数据文件 |
| `_loaded_at` | TIMESTAMP | YES | 标识/血缘字段 |

## intermediate

### `author_activity`

**行数**：190,500　|　**用途**：作者画像（按姓名）。**姓名非唯一标识，此表只作筛查线索**

| 字段 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `author_name` | VARCHAR | YES | — |
| `n_retractions` | BIGINT | YES | — |
| `n_journals` | BIGINT | YES | — |
| `n_subject_areas` | BIGINT | YES | — |
| `n_active_years` | BIGINT | YES | — |
| `n_countries` | BIGINT | YES | — |
| `first_paper_year` | BIGINT | YES | — |
| `last_paper_year` | BIGINT | YES | — |
| `first_retraction_year` | BIGINT | YES | — |
| `last_retraction_year` | BIGINT | YES | — |
| `n_paper_mill` | HUGEINT | YES | — |
| `n_peer_review_fraud` | HUGEINT | YES | — |
| `n_ai_generated` | HUGEINT | YES | — |
| `n_first_author` | HUGEINT | YES | — |
| `n_last_author` | HUGEINT | YES | — |
| `median_latency_days` | DOUBLE | YES | — |

### `coauthorship_edges`

**行数**：1,063,933　|　**用途**：合作网络边表。一行一对作者，权重 = 共同署名的被撤稿论文数

| 字段 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `author_a` | VARCHAR | YES | — |
| `author_b` | VARCHAR | YES | — |
| `n_shared_works` | BIGINT | YES | 两位作者共同署名的**被撤稿**论文数（边权重） |
| `n_shared_paper_mill` | HUGEINT | YES | — |
| `shared_paper_mill_share` | DOUBLE | YES | — |
| `n_journals` | BIGINT | YES | — |
| `n_fields` | BIGINT | YES | — |
| `n_countries` | BIGINT | YES | — |
| `first_year` | INTEGER | YES | — |
| `last_year` | INTEGER | YES | — |
| `year_span` | INTEGER | YES | — |
| `works_per_year` | DOUBLE | YES | — |

### `journal_profile`

**行数**：8,748　|　**用途**：期刊画像。含爆发度、论文工厂占比、撤稿时滞等

| 字段 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `journal` | VARCHAR | YES | — |
| `publisher` | VARCHAR | YES | — |
| `n_retractions` | BIGINT | YES | — |
| `active_years` | BIGINT | YES | — |
| `first_year` | BIGINT | YES | — |
| `last_year` | BIGINT | YES | — |
| `n_paper_mill` | HUGEINT | YES | — |
| `n_peer_review_fraud` | HUGEINT | YES | — |
| `n_ai_generated` | HUGEINT | YES | — |
| `n_expression_of_concern` | HUGEINT | YES | — |
| `n_international` | HUGEINT | YES | — |
| `paper_mill_share` | DOUBLE | YES | — |
| `peer_review_fraud_share` | DOUBLE | YES | — |
| `ai_generated_share` | DOUBLE | YES | — |
| `median_latency_days` | DOUBLE | YES | — |
| `mean_authors` | DOUBLE | YES | — |
| `china_share` | DOUBLE | YES | — |
| `peak_year` | BIGINT | YES | — |
| `peak_year_retractions` | BIGINT | YES | — |
| `burst_ratio` | DOUBLE | YES | 峰值年撤稿数 ÷ 撤稿总数，取值 0~1。**实测 AUC 仅 0.497，无区分能力** |
| `retractions_per_active_year` | DOUBLE | YES | — |

### `journal_year_activity`

**行数**：18,242　|　**用途**：期刊 × 年份活动表。**异常检测的正确粒度** —— 只按期刊聚合会把'爆发'平均掉

| 字段 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `journal` | VARCHAR | YES | — |
| `year` | BIGINT | YES | — |
| `n_retractions` | BIGINT | YES | — |
| `n_paper_mill` | HUGEINT | YES | — |
| `n_peer_review_fraud` | HUGEINT | YES | — |
| `n_ai_generated` | HUGEINT | YES | — |
| `n_rogue_editor` | HUGEINT | YES | — |
| `paper_mill_share` | DOUBLE | YES | — |
| `peer_review_fraud_share` | DOUBLE | YES | — |
| `ai_generated_share` | DOUBLE | YES | — |
| `median_latency_days` | DOUBLE | YES | — |
| `mean_latency_days` | DOUBLE | YES | — |
| `mean_authors` | DOUBLE | YES | — |
| `china_share` | DOUBLE | YES | — |

### `retractions_enriched`

**行数**：72,453　|　**用途**：撤稿记录增强表。合并原因/国家/作者/机构/学科特征（先聚合再 join，避免笛卡尔积放大）

| 字段 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `record_id` | BIGINT | YES | — |
| `title` | VARCHAR | YES | — |
| `journal` | VARCHAR | YES | — |
| `publisher` | VARCHAR | YES | — |
| `retraction_nature` | VARCHAR | YES | — |
| `paywalled` | VARCHAR | YES | — |
| `original_paper_date` | DATE | YES | — |
| `retraction_date` | DATE | YES | — |
| `original_paper_year` | BIGINT | YES | — |
| `retraction_year` | BIGINT | YES | — |
| `retraction_latency_days` | BIGINT | YES | 撤稿时滞 = 撤稿日期 − 原文发表日期。全局中位数 500 天 |
| `retraction_latency_years` | DOUBLE | YES | — |
| `retraction_doi` | VARCHAR | YES | — |
| `original_paper_doi` | VARCHAR | YES | — |
| `original_paper_pubmed_id` | VARCHAR | YES | — |
| `n_reasons` | BIGINT | YES | — |
| `is_paper_mill` | BOOLEAN | YES | **标签字段**：官方 Reason 含 'Paper Mill'。所有模型验证都用它做正例 |
| `is_peer_review_fraud` | BOOLEAN | YES | — |
| `is_rogue_editor` | BOOLEAN | YES | — |
| `is_ai_generated` | BOOLEAN | YES | — |
| `is_image_duplication` | BOOLEAN | YES | — |
| `is_third_party_investigation` | BOOLEAN | YES | — |
| `is_author_unresponsive` | BOOLEAN | YES | — |
| `n_countries` | BIGINT | YES | — |
| `is_international` | BOOLEAN | YES | — |
| `involves_china` | BOOLEAN | YES | — |
| `n_authors` | BIGINT | YES | — |
| `n_institutions` | BIGINT | YES | — |
| `n_subjects` | BIGINT | YES | — |
| `first_country` | VARCHAR | YES | — |
| `first_author` | VARCHAR | YES | — |
| `last_author` | VARCHAR | YES | — |
| `primary_subject` | VARCHAR | YES | — |
| `subject_code` | VARCHAR | YES | — |
| `subject_name` | VARCHAR | YES | — |
| `_source_file` | VARCHAR | YES | 标识/血缘字段 |
| `_loaded_at` | TIMESTAMP | YES | 标识/血缘字段 |

### `work_citation_event_panel`

**行数**：65,298　|　**用途**：**平衡事件面板**。10,883 篇 × 6 时点，强制补零，因果推断的数据底座

| 字段 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `openalex_id` | VARCHAR | YES | 标识/血缘字段 |
| `event_time` | BIGINT | YES | 相对撤稿的年份（−3…+2）。事件研究的时间轴 |
| `citation_year` | BIGINT | YES | — |
| `publication_year` | INTEGER | YES | — |
| `retraction_year` | BIGINT | YES | — |
| `years_since_publication` | BIGINT | YES | — |
| `cited_by_count` | BIGINT | YES | **已补零**：0 既可能是真实无引用，也可能是 OpenAlex 省略后被补上 |
| `has_observed_data` | BOOLEAN | YES | 区分上面两种 0：True = OpenAlex 真实给出，False = 我们补的 |
| `is_paper_mill` | BOOLEAN | YES | — |
| `is_peer_review_fraud` | BOOLEAN | YES | — |
| `is_ai_generated` | BOOLEAN | YES | — |
| `field` | VARCHAR | YES | — |
| `domain` | VARCHAR | YES | — |
| `total_cited_by_count` | BIGINT | YES | — |
| `n_authors` | BIGINT | YES | — |

### `works_enriched`

**行数**：134,510　|　**用途**：**跨源枢纽表**：OpenAlex × Retraction Watch 按 DOI 关联

| 字段 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `openalex_id` | VARCHAR | YES | 标识/血缘字段 |
| `doi` | VARCHAR | YES | — |
| `title` | VARCHAR | YES | — |
| `publication_year` | INTEGER | YES | — |
| `publication_date` | DATE | YES | — |
| `work_type` | VARCHAR | YES | — |
| `language` | VARCHAR | YES | — |
| `cited_by_count` | BIGINT | YES | — |
| `referenced_works_count` | BIGINT | YES | — |
| `fwci` | DOUBLE | YES | — |
| `is_oa` | BOOLEAN | YES | — |
| `oa_status` | VARCHAR | YES | — |
| `primary_topic` | VARCHAR | YES | — |
| `domain` | VARCHAR | YES | — |
| `field` | VARCHAR | YES | — |
| `subfield` | VARCHAR | YES | — |
| `n_authors` | BIGINT | YES | — |
| `countries_distinct_count` | INTEGER | YES | — |
| `institutions_distinct_count` | INTEGER | YES | — |
| `matched_in_retraction_watch` | BOOLEAN | YES | 是否能在 RW 找到对应案底。实测仅 44.8% 能匹配上 |
| `n_rw_records` | BIGINT | YES | — |
| `retraction_date` | DATE | YES | — |
| `retraction_year` | BIGINT | YES | — |
| `retraction_nature` | VARCHAR | YES | — |
| `rw_journal` | VARCHAR | YES | — |
| `rw_first_country` | VARCHAR | YES | — |
| `retraction_latency_days` | BIGINT | YES | — |
| `is_paper_mill` | BOOLEAN | YES | — |
| `is_peer_review_fraud` | BOOLEAN | YES | — |
| `is_ai_generated` | BOOLEAN | YES | — |
| `is_image_duplication` | BOOLEAN | YES | — |
| `is_rogue_editor` | BOOLEAN | YES | — |
| `citations_per_year` | DOUBLE | YES | — |
| `_source_file` | VARCHAR | YES | 标识/血缘字段 |
| `_loaded_at` | TIMESTAMP | YES | 标识/血缘字段 |

## marts

### `author_cluster_members`

**行数**：75,498　|　**用途**：社群成员明细

| 字段 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `cluster_id` | BIGINT | YES | 标识/血缘字段 |
| `author_id` | VARCHAR | YES | 标识/血缘字段 |
| `author_name` | VARCHAR | YES | — |
| `orcid` | VARCHAR | YES | — |

### `author_cluster_validation`

**行数**：2　|　**用途**：社群分数的标签验证结果

| 字段 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `score_name` | VARCHAR | YES | — |
| `auc` | DOUBLE | YES | — |
| `n_clusters` | BIGINT | YES | — |
| `n_clusters_with_paper_mill` | BIGINT | YES | — |
| `n_edges_total` | BIGINT | YES | — |

### `author_clusters`

**行数**：11,089　|　**用途**：合作社群画像与评分

| 字段 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `cluster_id` | BIGINT | YES | 标识/血缘字段 |
| `n_authors` | BIGINT | YES | — |
| `n_edges` | BIGINT | YES | — |
| `density` | DOUBLE | YES | 社群内部密度。**3 人社群中 92.8% 恰好为 1.0（饱和），该区间不含信息** |
| `total_shared_works` | BIGINT | YES | — |
| `n_paper_mill_works` | BIGINT | YES | — |
| `n_paper_mill_edges` | BIGINT | YES | — |
| `paper_mill_edge_share` | DOUBLE | YES | — |
| `max_journals_in_edge` | DOUBLE | YES | — |
| `max_fields_in_edge` | DOUBLE | YES | — |
| `first_year` | BIGINT | YES | — |
| `last_year` | BIGINT | YES | — |
| `year_span` | BIGINT | YES | — |
| `works_per_author_year` | DOUBLE | YES | — |
| `field_journal_ratio` | DOUBLE | YES | — |
| `pr_paper_mill_edge_share` | DOUBLE | YES | — |
| `pr_density` | DOUBLE | YES | — |
| `pr_works_per_author_year` | DOUBLE | YES | — |
| `pr_field_journal_ratio` | DOUBLE | YES | — |
| `cluster_risk_score` | DOUBLE | YES | 社群严重度分。**同样存在目标泄漏**（paper_mill_edge_share 与标签同源） |
| `cluster_structural_score` | DOUBLE | YES | 社群结构分。**AUC 0.336，反向预测** —— 已知负结果，见 README 发现 10 |
| `has_paper_mill` | BOOLEAN | YES | — |

### `country_leaderboard`

**行数**：184　|　**用途**：国家/地区撤稿画像，区分独立完成与国际合作

| 字段 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `country` | VARCHAR | YES | — |
| `n_retractions` | BIGINT | YES | — |
| `n_as_sole_country` | BIGINT | YES | — |
| `share_of_global_pct` | DOUBLE | YES | 占全球总记录数的比例。**多值归属，各国之和不等于 100%** |
| `n_paper_mill` | HUGEINT | YES | — |
| `paper_mill_share` | DOUBLE | YES | — |
| `n_peer_review_fraud` | HUGEINT | YES | — |
| `peer_review_fraud_share` | DOUBLE | YES | — |
| `n_ai_generated` | HUGEINT | YES | — |
| `ai_generated_share` | DOUBLE | YES | — |
| `median_latency_days` | DOUBLE | YES | — |
| `first_retraction_year` | BIGINT | YES | — |
| `last_retraction_year` | BIGINT | YES | — |
| `active_years` | BIGINT | YES | — |
| `n_journals` | BIGINT | YES | — |
| `mean_authors` | DOUBLE | YES | — |
| `n_recent_5y` | BIGINT | YES | — |
| `recent_5y_share` | DOUBLE | YES | — |
| `rank_by_volume` | BIGINT | YES | — |

### `feature_discriminative_power`

**行数**：12　|　**用途**：**单变量 AUC**。检测目标泄漏的关键工具 —— paper_mill_share 的 AUC = 1.000

| 字段 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `feature` | VARCHAR | YES | — |
| `feature_group` | VARCHAR | YES | — |
| `auc` | DOUBLE | YES | — |
| `auc_abs` | DOUBLE | YES | — |
| `suspicious_direction` | VARCHAR | YES | — |
| `leakage_note` | VARCHAR | YES | — |

### `journal_risk`

**行数**：1,734　|　**用途**：**期刊风险评分**。严重度分与结构分双轨，含分层标签

| 字段 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `journal` | VARCHAR | YES | — |
| `publisher` | VARCHAR | YES | — |
| `n_retractions` | BIGINT | YES | — |
| `active_years` | BIGINT | YES | — |
| `first_year` | BIGINT | YES | — |
| `last_year` | BIGINT | YES | — |
| `peak_year` | BIGINT | YES | — |
| `peak_year_retractions` | BIGINT | YES | — |
| `burst_ratio` | DOUBLE | YES | — |
| `n_paper_mill` | HUGEINT | YES | — |
| `paper_mill_share` | DOUBLE | YES | — |
| `peer_review_fraud_share` | DOUBLE | YES | — |
| `ai_generated_share` | DOUBLE | YES | — |
| `n_expression_of_concern` | HUGEINT | YES | — |
| `n_international` | HUGEINT | YES | — |
| `median_latency_days` | DOUBLE | YES | — |
| `mean_authors` | DOUBLE | YES | — |
| `china_share` | DOUBLE | YES | — |
| `retractions_per_active_year` | DOUBLE | YES | — |
| `sev_paper_mill` | DOUBLE | YES | — |
| `sev_burst` | DOUBLE | YES | — |
| `sev_volume` | DOUBLE | YES | — |
| `sev_peer_review` | DOUBLE | YES | — |
| `sev_ai` | DOUBLE | YES | — |
| `risk_score` | DOUBLE | YES | 严重度分（0~100）。**含由 Reason 派生的特征，存在目标泄漏**，只能用于排序已确认问题的严重程度，不具备预测能力 |
| `risk_tier` | VARCHAR | YES | 风险分层：极高/高/中/低。分层会丢失信息，应与原始分数同时使用 |
| `struct_volume` | DOUBLE | YES | — |
| `struct_intensity` | DOUBLE | YES | — |
| `structural_score` | DOUBLE | YES | 结构分（0~100）。零 Reason 派生特征，AUC 0.736，是真正的预测分数 |

### `reason_landscape`

**行数**：112　|　**用途**：撤稿原因全景（含中位时滞与近 5 年趋势）

| 字段 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `reason` | VARCHAR | YES | — |
| `n_occurrences` | BIGINT | YES | — |
| `n_records` | BIGINT | YES | 涉及该原因的**记录数**（去重），区别于 n_occurrences（原因出现次数） |
| `pct_of_all_retractions` | DOUBLE | YES | — |
| `first_year` | BIGINT | YES | — |
| `last_year` | BIGINT | YES | — |
| `median_latency_days` | DOUBLE | YES | — |
| `n_recent_5y` | HUGEINT | YES | — |
| `recent_5y_share` | DOUBLE | YES | — |
| `n_involving_china` | HUGEINT | YES | — |
| `china_share` | DOUBLE | YES | — |
| `rank_by_records` | BIGINT | YES | — |

### `retraction_citation_event_study`

**行数**：6　|　**用途**：**事件研究主表**：各时点的实际引用 vs 反事实引用

| 字段 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `event_time` | BIGINT | YES | — |
| `n_works` | BIGINT | YES | — |
| `n_observations` | BIGINT | YES | — |
| `mean_citations` | DOUBLE | YES | — |
| `median_citations` | DOUBLE | YES | — |
| `data_coverage` | DOUBLE | YES | — |
| `counterfactual_citations` | DOUBLE | YES | 反事实引用：用撤稿前 3 年的趋势做 OLS 线性外推得到的'如果没被撤稿会怎样' |
| `citation_gap` | DOUBLE | YES | — |
| `citation_gap_pct` | DOUBLE | YES | 相对反事实的引用变化百分比。撤稿后最大 −71.9% |
| `pre_trend_slope` | DOUBLE | YES | — |
| `pre_trend_intercept` | DOUBLE | YES | — |
| `is_post_retraction` | BOOLEAN | YES | — |

### `retraction_citation_event_study_by_group`

**行数**：12　|　**用途**：分群事件研究：论文工厂 vs 其他撤稿

| 字段 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `group_name` | VARCHAR | YES | — |
| `event_time` | BIGINT | YES | — |
| `n_works` | BIGINT | YES | — |
| `mean_citations` | DOUBLE | YES | — |
| `median_citations` | DOUBLE | YES | — |
| `counterfactual_citations` | DOUBLE | YES | — |
| `citation_gap` | DOUBLE | YES | — |
| `citation_gap_pct` | DOUBLE | YES | — |
| `is_post_retraction` | BOOLEAN | YES | — |

### `retraction_citation_penalty_summary`

**行数**：1　|　**用途**：事件研究结论摘要（含反事实拟合的自检列）

| 字段 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `n_post_periods` | BIGINT | YES | — |
| `mean_gap_after_retraction` | DOUBLE | YES | — |
| `mean_gap_pct_after_retraction` | DOUBLE | YES | — |
| `mean_gap_before_retraction` | DOUBLE | YES | — |
| `pre_trend_slope` | DOUBLE | YES | — |

### `retraction_trends`

**行数**：58　|　**用途**：年度趋势（含同比、累计、3 年移动平均）

| 字段 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `year` | BIGINT | YES | — |
| `n_retractions` | BIGINT | YES | — |
| `n_true_retraction` | HUGEINT | YES | — |
| `n_expression_of_concern` | HUGEINT | YES | — |
| `n_correction` | HUGEINT | YES | — |
| `n_reinstatement` | HUGEINT | YES | — |
| `n_paper_mill` | HUGEINT | YES | — |
| `n_peer_review_fraud` | HUGEINT | YES | — |
| `n_ai_generated` | HUGEINT | YES | — |
| `n_china` | HUGEINT | YES | — |
| `n_international` | HUGEINT | YES | — |
| `n_journals` | BIGINT | YES | — |
| `mean_latency_days` | DOUBLE | YES | — |
| `median_latency_days` | DOUBLE | YES | — |
| `mean_authors` | DOUBLE | YES | — |
| `paper_mill_share` | DOUBLE | YES | — |
| `china_share` | DOUBLE | YES | — |
| `international_share` | DOUBLE | YES | — |
| `prev_year_retractions` | BIGINT | YES | — |
| `yoy_change` | BIGINT | YES | — |
| `yoy_change_pct` | DOUBLE | YES | — |
| `cumulative_retractions` | HUGEINT | YES | — |
| `ma3_retractions` | DOUBLE | YES | — |

### `risk_score_validation`

**行数**：2　|　**用途**：期刊评分的标签验证结果（AUC）

| 字段 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `score_name` | VARCHAR | YES | — |
| `n_journals_with_paper_mill` | BIGINT | YES | — |
| `n_journals_without_paper_mill` | BIGINT | YES | — |
| `n_journals_total` | BIGINT | YES | — |
| `auc` | DOUBLE | YES | — |
| `auc_grade` | VARCHAR | YES | — |

### `risk_tier_performance`

**行数**：4　|　**用途**：风险分层的精确率与提升度（lift）

| 字段 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `structural_tier` | VARCHAR | YES | — |
| `n_journals` | BIGINT | YES | — |
| `n_with_paper_mill` | HUGEINT | YES | — |
| `precision_rate` | DOUBLE | YES | — |
| `min_score` | DOUBLE | YES | — |
| `max_score` | DOUBLE | YES | — |
| `overall_base_rate` | DOUBLE | YES | — |
| `lift` | DOUBLE | YES | — |

---

## 文档覆盖情况

- 字段总数：**413**
- 有详细语义说明：**35**（8.5%）
- 其余字段：名称自解释，或属于标识/血缘类字段

> ⚠ 未覆盖的字段会在表中显示为 `—`。**留空是刻意的** —— 比起编一句看起来像回事但没人验证过的说明，承认'这里还没写清楚'对使用者更负责任。

## 关于数据本身的已知问题

| 问题 | 影响范围 | 处理方式 |
|---|---|---|
| 上游 CSV 含 149 行完全空白行 | Retraction Watch | staging 层剔除，记入 `load_audit` |
| 约 0.77% 的引用年份早于发表年份 | OpenAlex | 上游特性（撤稿声明继承原论文引用），质量规则降级为 warning |
| OpenAlex 省略被引为 0 的年份 | 引用轨迹 | 事件面板强制补零，并用 `has_observed_data` 标记 |
| 超过一半的被标记作品在 RW 查无案底 | 跨源关联 | 保留为待研究问题，见 README 发现 6 |
| 作者姓名不是唯一标识 | `author_activity` | 网络分析改用 OpenAlex 消歧 ID；该表仅作线索 |
