"""数据质量校验框架。

【为什么数据项目必须有质量校验】
    数据管道最可怕的故障不是"报错崩掉"，而是 **静默地出错**：
      - 上游少给了 1 万条记录 → 所有报表照常产出，只是数字悄悄变小
      - 日期解析规则失效 → 时间趋势图少了最后三个月，没人察觉
      - 多值字段没拆干净 → 国家占比被系统性低估，但图形看起来完全正常

    这些错误**不会抛异常**。代码跑得通、图表画得出、结论看起来合理，
    直到有人在会上问"这个数字怎么比上个月少了一半"。

    质量校验的作用，就是把"静默错误"变成"响亮的红灯"。

【设计思路：把校验写成 SQL，把判定交给框架】
    每条校验就是一句 SQL，返回**违规行数**。
    框架负责执行、汇总、决定是否让整个任务失败。
    好处是：
      - 加校验只需加一行 SQL，不用写 Python
      - 校验逻辑和数据在同一层，谁都能读懂、能改
      - 严重级别（error / warning）显式声明 ——
        什么必须拦住发布、什么只需记录，是个业务判断，不该藏在代码细节里
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import duckdb

from papermill_hunter.logging_conf import get_logger

logger = get_logger(__name__)

Severity = Literal["error", "warning"]


@dataclass(frozen=True)
class Check:
    """一条数据质量校验。

    Attributes:
        name: 校验名称（人读的）。
        sql: 必须返回**单个数值**，表示违规行数。0 表示通过。
        severity: ``error`` 会让任务失败；``warning`` 只记录。
        rationale: 为什么要校验这一条 —— 写清楚，否则半年后没人敢删。
    """

    name: str
    sql: str
    severity: Severity = "error"
    rationale: str = ""


@dataclass(frozen=True)
class CheckResult:
    """一条校验的执行结果。

    【为什么要有 execution_error 这个字段 —— 一次真实事故的产物】
        最初的实现里，校验 SQL 执行失败时会被记成 ``violations = 1``。
        结果是一条**写错的校验**和一条**发现问题的校验**在输出里长得完全一样：
             ✗ 某校验 —— 违规 1 行
        于是当我在重构中把 ``risk_tier`` 列改名为 ``structural_tier`` 后，
        这条校验引用了不存在的列、每次都执行失败，
        却以"违规 1 行 + warning"的形式安安静静地挂了好几轮，
        没有任何人（包括我）注意到它其实早就失效了。

        **一条悄悄失效的校验，比没有校验更危险** ——
        因为它给了你"这里有人在把关"的错觉。

        所以现在把"执行失败"和"发现违规"彻底分开：
          - execution_error 非空 → 校验本身坏了，必须修
          - execution_error 为空且 violations > 0 → 数据有问题
        两者在报告里用不同的措辞和颜色呈现，也分别统计。
    """

    check: Check
    violations: int
    execution_error: str | None = None

    @property
    def errored(self) -> bool:
        """校验本身执行失败（不代表数据有问题，代表校验写错了）。"""
        return self.execution_error is not None

    @property
    def passed(self) -> bool:
        return not self.errored and self.violations == 0

    @property
    def blocks_release(self) -> bool:
        """是否阻断发布。

        注意：**校验执行失败一律阻断**，与它声明的 severity 无关。
        理由很简单 —— 一条跑不起来的校验等于没有校验，
        而"我们以为有校验"比"我们知道没有校验"危险得多。
        """
        if self.errored:
            return True
        return self.violations > 0 and self.check.severity == "error"


# ============================================================================
# 校验清单
# ============================================================================
CHECKS: tuple[Check, ...] = (
    # ---------------- 主键完整性 ----------------
    Check(
        name="record_id 非空",
        sql="SELECT COUNT(*) FROM staging.retraction_watch WHERE record_id IS NULL",
        rationale=("record_id 是整张表的主键，也是所有展开表的外键。一旦出现空值，下游 join 会静默丢行。"),
    ),
    Check(
        name="record_id 唯一",
        sql=("SELECT COUNT(*) - COUNT(DISTINCT record_id) FROM staging.retraction_watch"),
        rationale=(
            "上游若重复推送同一条记录，所有计数类指标都会偏高。这类错误不会报错，只会让每个数字都大一点点。"
        ),
    ),
    # ---------------- 时间字段 ----------------
    Check(
        name="撤稿日期解析成功率 ≥ 99.5%",
        sql=(
            "SELECT CASE WHEN COUNT(*) = 0 THEN 0 "
            "ELSE CASE WHEN COUNT(retraction_date) * 1.0 / COUNT(*) >= 0.995 THEN 0 ELSE 1 END "
            "END FROM staging.retraction_watch"
        ),
        rationale=(
            "日期是全部时间趋势分析的基础。解析规则一旦失效（上游改了日期格式），"
            "趋势图会静默地少掉一段时间，而图看起来完全正常。"
            "设定阈值而不是要求 100%，是因为上游确实存在极个别格式异常的记录。"
        ),
    ),
    Check(
        name="撤稿日期不晚于今天",
        sql="SELECT COUNT(*) FROM staging.retraction_watch WHERE retraction_date > CURRENT_DATE",
        rationale="未来的撤稿日期说明数据录入或解析有误。",
    ),
    Check(
        name="撤稿日期不早于 1900 年",
        sql=("SELECT COUNT(*) FROM staging.retraction_watch WHERE retraction_date < DATE '1900-01-01'"),
        severity="warning",
        rationale=(
            "数据中确实存在一条 1756 年的撤稿记录，几乎可以确定是录入错误。"
            "标为 warning 而非 error，是因为我们不想因为一条历史脏数据就阻断整条管道 ——"
            "但它必须被看见，不能悄悄混进时间序列里把坐标轴拉歪。"
        ),
    ),
    Check(
        name="撤稿不早于论文发表（时滞非负）",
        sql=(
            "SELECT COUNT(*) FROM staging.retraction_watch "
            "WHERE original_paper_date IS NOT NULL AND retraction_date IS NOT NULL "
            "AND retraction_date < original_paper_date"
        ),
        rationale=(
            "逻辑上不可能：一篇论文不可能在被撤稿之后才发表。出现负时滞说明两个日期中至少有一个是错的。"
        ),
    ),
    # ---------------- 枚举取值 ----------------
    Check(
        name="撤稿性质取值合法",
        sql=(
            "SELECT COUNT(*) FROM staging.retraction_watch "
            "WHERE retraction_nature IS NOT NULL "
            "AND retraction_nature NOT IN "
            "('Retraction', 'Correction', 'Expression of concern', 'Reinstatement')"
        ),
        severity="warning",
        rationale=(
            "Retraction Watch 使用受控词表。出现新取值可能意味着上游扩充了词表，"
            "需要我们同步更新分类逻辑 —— 属于要关注但不该阻断发布的情况。"
        ),
    ),
    # ---------------- 展开表的完整性 ----------------
    Check(
        name="撤稿原因无空值",
        sql=("SELECT COUNT(*) FROM staging.retraction_reasons WHERE reason IS NULL OR TRIM(reason) = ''"),
        rationale=(
            "多值字段拆分后若残留空字符串，会在'撤稿原因分布'里凭空多出一个空分类，"
            "而且它往往会排进前列 —— 因为它聚集了所有'没写原因'的记录。"
        ),
    ),
    Check(
        name="撤稿原因表外键完整",
        sql=(
            "SELECT COUNT(*) FROM staging.retraction_reasons AS c "
            "LEFT JOIN staging.retraction_watch AS p USING (record_id) "
            "WHERE p.record_id IS NULL"
        ),
        rationale="孤儿记录意味着 join 会静默丢数据。",
    ),
    Check(
        name="国家表外键完整",
        sql=(
            "SELECT COUNT(*) FROM staging.retraction_countries AS c "
            "LEFT JOIN staging.retraction_watch AS p USING (record_id) "
            "WHERE p.record_id IS NULL"
        ),
        rationale=(
            "国家维度是'中国撤稿占全球一半'这一核心结论的直接依据。"
            "一旦出现孤儿记录，join 会静默丢行，让占比算错 —— 而且错得不容易察觉。"
        ),
    ),
    Check(
        name="作者表外键完整",
        sql=(
            "SELECT COUNT(*) FROM staging.retraction_authors AS c "
            "LEFT JOIN staging.retraction_watch AS p USING (record_id) "
            "WHERE p.record_id IS NULL"
        ),
        rationale=(
            "作者合作网络与'高产作者'识别都依赖这张表；孤儿记录会让网络分析凭空少掉节点和边，改变中心性排名。"
        ),
    ),
    Check(
        name="作者署名顺序从 1 开始连续",
        sql=(
            "SELECT COUNT(*) FROM ("
            "  SELECT record_id FROM staging.retraction_authors "
            "  GROUP BY record_id "
            "  HAVING MIN(author_position) <> 1 "
            "      OR MAX(author_position) <> COUNT(*)"
            ") "
        ),
        rationale=(
            "署名顺序是本项目判断'挂名异常'的关键特征。"
            "如果序号出现跳号或重复，说明展开逻辑错了，"
            "而基于顺序的分析会得出完全错误的结论。"
        ),
    ),
    # ---------------- 体量与新鲜度 ----------------
    Check(
        name="撤稿记录数不少于 5 万条",
        sql=("SELECT CASE WHEN COUNT(*) < 50000 THEN 1 ELSE 0 END FROM staging.retraction_watch"),
        rationale=(
            "这是最朴素也最有效的一条'防呆'校验："
            "如果某次采集只拿到了几百条（比如网络中断导致的静默截断），"
            "后面所有分析都会基于残缺数据得出结论。"
            "给数据量设一个下限，能拦住绝大多数'采集不全'的事故。"
        ),
    ),
    Check(
        name="数据新鲜度：最近一条撤稿在 400 天内",
        sql=(
            "SELECT CASE WHEN MAX(retraction_date) < CURRENT_DATE - INTERVAL 400 DAY "
            "THEN 1 ELSE 0 END FROM staging.retraction_watch"
        ),
        severity="warning",
        rationale=(
            "数据管道最隐蔽的风险是'数据静默过期'："
            "代码天天在跑、看板天天在变，但上游其实早就停止更新了。"
            "用最新一条记录的日期做哨兵，能及时发现断流。"
        ),
    ),
    # ========================================================================
    # 下面这一组是「指标取值范围校验」。
    #
    # 【为什么要专门有这一组】
    #   因为它们来自一次真实事故：期刊画像里把"撤稿总数"错误地算成了
    #   "有多少个年份行"（在已聚合的表上写了 COUNT(*)），
    #   导致 burst_ratio（爆发度，理论取值 0~1）算出了 148，
    #   paper_mill_share 也大于 1。
    #
    #   ★ 关键在于：**模型验证没有抓住这个 bug**。
    #     修正前的风险评分 AUC 高达 0.982，看起来"效果极好"——
    #     因为错误的 burst_ratio 恰好与"是否为论文工厂"高度共线，
    #     模型表现得很好，但它依赖的是一个完全错误的信号。
    #     抓住它的是最朴素的一步：一个 0~1 的比例算出了 148。
    #
    #   教训：**评估指标好，不等于数据是对的。**
    #   任何指标都必须先过"它有没有落在可能的取值范围内"这一关。
    #   这一组校验成本极低、捕获率极高，是性价比最高的防线。
    # ========================================================================
    Check(
        name="[量纲] 期刊爆发度 burst_ratio 在 (0, 1] 之间",
        sql=(
            "SELECT COUNT(*) FROM intermediate.journal_profile "
            "WHERE burst_ratio IS NOT NULL AND (burst_ratio <= 0 OR burst_ratio > 1.0001)"
        ),
        rationale=(
            "burst_ratio = 峰值年撤稿数 / 撤稿总数，是一个比例，理论上不可能超过 1。"
            "它超过 1 只可能意味着分母（总数）算错了 —— 这正是那次真实事故的症状。"
        ),
    ),
    Check(
        name="[量纲] 期刊单年峰值不超过撤稿总数",
        sql=("SELECT COUNT(*) FROM intermediate.journal_profile WHERE peak_year_retractions > n_retractions"),
        rationale=(
            "单年撤稿量是总数的一部分，不可能大于总数。"
            "这条和上一条是同一个 bug 的两种表述 —— 冗余校验在这里是有意为之："
            "同一个错误若能触发两条独立规则，说明覆盖到位了。"
        ),
    ),
    Check(
        name="[量纲] 期刊各比例指标在 0~1 之间",
        sql=(
            "SELECT COUNT(*) FROM intermediate.journal_profile "
            "WHERE paper_mill_share  NOT BETWEEN 0 AND 1 "
            "   OR peer_review_fraud_share NOT BETWEEN 0 AND 1 "
            "   OR ai_generated_share NOT BETWEEN 0 AND 1 "
            "   OR china_share       NOT BETWEEN 0 AND 1"
        ),
        rationale="所有占比类指标都必须落在 0~1，越界即说明分子分母口径不一致。",
    ),
    Check(
        name="[量纲] 风险评分在 0~100 之间",
        sql="SELECT COUNT(*) FROM marts.journal_risk WHERE risk_score NOT BETWEEN 0 AND 100",
        rationale="评分由百分位排名加权而来，理论上必然落在 0~100。",
    ),
    Check(
        name="[量纲] 国家份额在 0~100 之间",
        sql=(
            "SELECT COUNT(*) FROM marts.country_leaderboard WHERE share_of_global_pct NOT BETWEEN 0 AND 100"
        ),
        rationale="占比超过 100% 只可能意味着分母取错了。",
    ),
    Check(
        name="[一致性] 国家独立完成数不超过总数",
        sql=("SELECT COUNT(*) FROM marts.country_leaderboard WHERE n_as_sole_country > n_retractions"),
        rationale=(
            "'该国独立完成'是'涉及该国'的子集，子集不可能大于全集。"
            "这类集合包含关系的校验，能抓住 join 逻辑写反的错误。"
        ),
    ),
    # ---------------- 行数与 join 完整性 ----------------
    Check(
        name="[一致性] 风险评分表期刊数与合格样本一致",
        sql=(
            "SELECT ABS("
            "  (SELECT COUNT(*) FROM marts.journal_risk) "
            "  - (SELECT COUNT(*) FROM intermediate.journal_profile WHERE n_retractions >= 5)"
            ")"
        ),
        rationale=(
            "评分表应当恰好包含所有达标期刊。数量对不上说明 join 丢了行或放大了行 ——"
            "而 join 丢行是不会报错的，只会让排名悄悄缺几个候选。"
        ),
    ),
    Check(
        name="[一致性] 趋势表年份数与有年份的记录数一致",
        sql=(
            "SELECT ABS("
            "  (SELECT COUNT(*) FROM marts.retraction_trends) "
            "  - (SELECT COUNT(DISTINCT retraction_year) FROM intermediate.retractions_enriched "
            "     WHERE retraction_year IS NOT NULL)"
            ")"
        ),
        rationale="维度表的行数必须覆盖全部维度取值，否则趋势图会缺年份。",
    ),
    Check(
        name="[一致性] 趋势表累计值等于各年之和",
        sql=(
            "SELECT COUNT(*) FROM ("
            "  SELECT year, cumulative_retractions,"
            "         SUM(n_retractions) OVER (ORDER BY year) AS recomputed"
            "  FROM marts.retraction_trends"
            ") WHERE cumulative_retractions <> recomputed"
        ),
        rationale=(
            "窗口函数的累积求和很容易因为漏写 ORDER BY 或写错窗口范围而算错，"
            "而且算错之后曲线依然平滑好看。用重算对拍是最直接的验证。"
        ),
    ),
    # ---------------- 模型有效性 ----------------
    Check(
        name="[量纲] AUC 落在 0~1 之间",
        sql=("SELECT COUNT(*) FROM marts.feature_discriminative_power WHERE auc < 0 OR auc > 1"),
        rationale=(
            "AUC 是概率，理论上必然落在 0~1。它越界只可能说明**秩的计算写错了** ——"
            "这正是本项目真实发生过的事故：并列样本的平均秩取成了各自的行号，"
            "导致 paper_mill_share 的 AUC 算成 1.0008。"
            "又一次印证：抓住逻辑错误的往往不是复杂的评估，"
            "而是'这个数有没有超出它可能的范围'这种最朴素的检查。"
        ),
    ),
    Check(
        name="[模型] 结构分（无泄漏）AUC 高于 0.5",
        sql=(
            "SELECT CASE WHEN (SELECT auc FROM marts.risk_score_validation "
            "                  WHERE score_name LIKE 'structural_score%') < 0.5 "
            "THEN 1 ELSE 0 END"
        ),
        rationale=(
            "这里刻意校验的是**结构分**而不是严重度分。"
            "严重度分包含由 Reason 字段派生的特征，与标签同源（目标泄漏），"
            "它的 AUC 必然很高，拿它做校验等于自己骗自己。"
            "只有完全不含标签信息的结构分，其 AUC 才代表真实的预测能力。"
        ),
    ),
    Check(
        name="[模型] 风险分层无倒挂（低风险层精确率不高于高风险层）",
        sql=(
            "SELECT COUNT(*) FROM ("
            # 列名是 structural_tier —— 它来自结构分（无泄漏的那个分数）。
            # 这里曾经写的是 risk_tier，而那一列在重构时被改名了，
            # 于是这条校验每次都执行失败，却以"违规 1 行 + warning"的形式
            # 安静地挂了好几轮。现在框架会把"执行失败"单独标出来，不会再被埋掉。
            "  SELECT structural_tier,"
            "         min_score,"
            "         precision_rate,"
            "         LAG(precision_rate) OVER (ORDER BY min_score DESC) AS prev_rate"
            "  FROM marts.risk_tier_performance"
            ") WHERE prev_rate IS NOT NULL AND precision_rate > prev_rate + 0.05"
        ),
        severity="warning",
        rationale=(
            "要检查的是'倒挂'：如果被标为【低风险】的那一层，其命中率反而**高于**"
            "【高风险】层，说明分层阈值或权重存在问题，评分的排序方向错了。"
            "允许 5 个百分点的抖动，是因为小样本下分层本身就有波动，"
            "不该因为一次正常波动就告警。"
            "（注意：本条踩过两次坑 —— 最初方向写反了，把'逐层正常下降'误判成违规；"
            " 后来又因为列名改名而整条失效。**校验规则本身也是代码，同样会写错。**）"
        ),
    ),
    # ---------------- OpenAlex ----------------
    Check(
        name="OpenAlex：openalex_id 非空且唯一",
        sql=("SELECT COUNT(*) - COUNT(DISTINCT openalex_id) FROM staging.openalex_works"),
        rationale=(
            "openalex_id 是跨表关联的主键。分页抓取时若因为断点续传逻辑出错"
            "而重复抓取某一页，就会产生重复主键 —— 而重复抓取**不会报错**，"
            "只会让作品总数和引用总量静默偏高。"
        ),
    ),
    Check(
        name="OpenAlex：作者数与服务端计数字段一致",
        sql=(
            "SELECT COUNT(*) FROM staging.openalex_works "
            "WHERE n_authors IS NOT NULL "
            "  AND n_authors <> ("
            "    SELECT COUNT(*) FROM staging.openalex_authorships AS a "
            "    WHERE a.openalex_id = staging.openalex_works.openalex_id)"
        ),
        severity="warning",
        rationale=(
            "n_authors 是我们自己从 JSON 数组长度数出来的，"
            "而作者表是展开后落盘的。两者理论上必须相等。"
            "不等说明展开逻辑漏了行或多了行 —— 这正是'用两个独立途径计算同一个量、"
            "再互相对拍'的经典验证手法。"
        ),
    ),
    Check(
        name="OpenAlex：引用年份早于发表年份的比例低于 2%",
        sql=(
            "SELECT CASE WHEN AVG(CASE WHEN years_since_publication < 0 THEN 1.0 ELSE 0.0 END) > 0.02 "
            "THEN 1 ELSE 0 END FROM staging.openalex_work_citations"
        ),
        severity="warning",
        rationale=(
            "【这条规则最初是 error 级，跑全量数据后被降级为 warning。原因值得记录。】"
            "小样本试跑时它是通过的；全量跑完后报出 2,255 行违规（占 0.77%）。"
            "逐条核查样例后发现，违规作品的标题**全部以 'RETRACTED:' 开头** ——"
            "它们是**撤稿声明本身**，不是原始论文。"
            "OpenAlex 把撤稿通知单独建成一条作品（publication_year 是通知的年份），"
            "但这条记录的 counts_by_year 继承了原论文的引用轨迹，"
            "于是出现了'2022 年发表的作品在 2012 年被引用'这种看似荒谬的数据。"
            ""
            "结论：这是**上游数据特性，不是我们的清洗 bug**。"
            "因此正确的处理不是删数据（那会丢失真实引用信息），"
            "而是把规则降级为 warning 并写清原因 ——"
            "**一条会在正常数据上误报的 error 级规则，只会训练人们忽略告警**，"
            "久而久之整个质量体系就失效了。"
            ""
            "阈值设 2%：低于此视为可接受的上游噪声；超过则说明可能是我们的解析出了问题。"
            "另外，这个缺陷**不会污染事件研究**：事件面板的'论文年龄'是"
            "按面板网格重算的，而不是直接用这个派生列。"
        ),
    ),
    Check(
        name="OpenAlex：作品数不少于 3 万篇",
        sql=("SELECT CASE WHEN COUNT(*) < 30000 THEN 1 ELSE 0 END FROM staging.openalex_works"),
        rationale=(
            "全量应为 13.5 万篇。设 3 万的下限是为了在小样本试跑阶段"
            "（--max-pages 2）不会误报，同时又能拦住『抓取中途静默截断』这类事故。"
        ),
    ),
    # ---------------- 交叉源一致性 ----------------
    Check(
        name="[交叉源] OpenAlex 与 Retraction Watch 的 DOI 可关联数不为零",
        sql=(
            "SELECT CASE WHEN COUNT(*) = 0 THEN 1 ELSE 0 END FROM ("
            "  SELECT w.doi FROM staging.openalex_works AS w "
            "  INNER JOIN staging.retraction_watch AS r ON w.doi = r.original_paper_doi"
            ")"
        ),
        severity="warning",
        rationale=(
            "两个数据源之间的关联完全依赖 DOI 格式统一。"
            "如果关联数为 0，说明 DOI 规范化（去前缀、转小写）有问题 ——"
            "而这不会报错，只会让所有跨源分析悄悄返回空结果。"
            "关联不上时最该怀疑的就是'键的格式不一致'，而不是'数据里正好没有'。"
        ),
    ),
    # ---------------- 因果推断的前置条件 ----------------
    Check(
        name="[因果] 事件面板是平衡的（各时点样本量一致）",
        sql=(
            "SELECT CASE WHEN COUNT(DISTINCT n_works) > 1 THEN 1 ELSE 0 END "
            "FROM marts.retraction_citation_event_study"
        ),
        rationale=(
            "这是事件研究法的**命门**。如果不同事件时点上的论文不是同一批，"
            "那么曲线上的差异可能完全来自'样本换了一批人'，而不是撤稿的因果效应。"
            "一旦这条校验失败，后面所有因果结论都不成立 ——"
            "所以它必须是 error 级而不是 warning 级。"
        ),
    ),
    Check(
        name="[因果] 撤稿前窗口的反事实拟合无系统性偏差",
        sql=(
            "SELECT CASE WHEN ABS(mean_gap_before_retraction) > 0.5 THEN 1 ELSE 0 END "
            "FROM marts.retraction_citation_penalty_summary"
        ),
        severity="warning",
        rationale=(
            "反事实曲线本来就是用撤稿前的数据拟合出来的，所以撤稿前的 gap 理论上应接近 0。"
            "它明显偏离 0，说明拟合过程本身有问题（例如用错了窗口、聚合口径不一致）。"
            "这是结果表的**自检列** —— 一个自己能验证自己的模型，比只有输出的模型可信得多。"
        ),
    ),
    Check(
        name="[因果] 事件面板覆盖了完整的 −3…+2 窗口",
        sql=("SELECT CASE WHEN COUNT(*) <> 6 THEN 1 ELSE 0 END FROM marts.retraction_citation_event_study"),
        rationale=(
            "窗口缺时点说明平衡面板的筛选条件写错了，会导致曲线不完整。"
            "6 = 撤稿前 3 年 + 撤稿当年 + 撤稿后 2 年。"
        ),
    ),
    # ---------------- 跨源关联 ----------------
    Check(
        name="[交叉源] OpenAlex 与 RW 的 DOI 关联率不低于 40%",
        sql=(
            "SELECT CASE WHEN AVG(CASE WHEN matched_in_retraction_watch THEN 1.0 ELSE 0.0 END) < 0.40 "
            "THEN 1 ELSE 0 END FROM intermediate.works_enriched"
        ),
        severity="warning",
        rationale=(
            "两个数据源的口径本就不同（一个标记'作品已撤稿'，一个记录'撤稿事件'），"
            "所以不可能 100% 匹配。实测约 73%，处于合理区间。"
            "但若跌到 40% 以下，多半是 DOI 规范化出了问题，而不是数据本身如此 ——"
            "关联率骤降时，第一个该怀疑的就是'键的格式'。"
        ),
    ),
    # ---------------- 图分析 ----------------
    Check(
        name="[图分析] 社群成员表外键完整",
        sql=(
            "SELECT COUNT(*) FROM marts.author_cluster_members AS m "
            "LEFT JOIN marts.author_clusters AS c USING (cluster_id) "
            "WHERE c.cluster_id IS NULL"
        ),
        rationale=(
            "成员表里出现不存在的社群编号，说明社群画像阶段过滤掉了一些社群"
            "（例如规模 <3 的），但成员表没有同步过滤。"
            "这会导致看板上'点开社群看成员'时出现空列表或错配。"
        ),
    ),
    Check(
        name="[图分析] 三人社群的密度饱和比例低于 99%",
        sql=(
            "SELECT CASE WHEN AVG(CASE WHEN density >= 0.999 THEN 1.0 ELSE 0.0 END) > 0.99 "
            "THEN 1 ELSE 0 END FROM marts.author_clusters WHERE n_authors = 3"
        ),
        severity="warning",
        rationale=(
            "这条规则记录了一个**已知的指标设计缺陷**，而不是在防未来的错误。"
            "实测：3 人社群中 92.8% 的密度恰好等于 1.0 ——"
            "因为 3 个节点最多只能构成 1 个三角形，只要合作过一次就是满密度。"
            "也就是说 density 在小规模区间几乎是常数、不含信息，"
            "而结构分给了它 45% 的权重，直接导致结构分 AUC 只有 0.336（反向预测）。"
            "阈值设 99%（高于当前的 92.8%）是为了：既不在每次运行时制造噪音告警，"
            "又能在指标进一步退化时发出信号。"
            "—— 把'已知缺陷'也写成可执行的校验，好处是它不会随着时间被遗忘。"
        ),
    ),
    Check(
        name="[图分析] 社群严重度分 AUC 高于 0.5",
        sql=("SELECT CASE WHEN MAX(auc) < 0.5 THEN 1 ELSE 0 END FROM marts.author_cluster_validation"),
        rationale=(
            "只需要有一个分数（严重度分）具备排序能力即可。"
            "结构分已知反向预测（AUC 0.336），这是被记录在案的负结果而非故障，"
            "所以这里用 MAX 而不是 MIN —— 避免把'已知的失败'当成'新的故障'来告警。"
        ),
    ),
)

# 质量校验依赖的表。CLI 在运行前会做一次预检，
# 避免"因为表不存在导致所有校验都报违规"这种误导性结果。
REQUIRED_TABLES: tuple[str, ...] = (
    "staging.retraction_watch",
    "staging.retraction_reasons",
    "staging.retraction_countries",
    "staging.retraction_authors",
    "staging.openalex_works",
    "staging.openalex_authorships",
    "staging.openalex_work_citations",
    "intermediate.retractions_enriched",
    "intermediate.journal_profile",
    "intermediate.works_enriched",
    "intermediate.work_citation_event_panel",
    "intermediate.coauthorship_edges",
    "marts.retraction_trends",
    "marts.journal_risk",
    "marts.risk_score_validation",
    "marts.risk_tier_performance",
    "marts.country_leaderboard",
    "marts.retraction_citation_event_study",
    "marts.retraction_citation_penalty_summary",
    "marts.author_clusters",
    "marts.author_cluster_members",
    "marts.author_cluster_validation",
)


# ============================================================================
# 执行
# ============================================================================
def run_checks(
    con: duckdb.DuckDBPyConnection,
    checks: tuple[Check, ...] = CHECKS,
) -> list[CheckResult]:
    """执行全部校验，返回结果列表。

    单条校验执行失败（例如 SQL 里写了不存在的列）**不会**中断整个流程 ——
    它会带着 ``execution_error`` 被记录下来，并在报告里与"发现数据问题"
    **明确区分开**。

    为什么不直接抛异常中断？
        因为质量校验的职责就是"报告问题"。如果它自己一碰问题就崩，
        那它就永远没法告诉你到底哪里有毛病。

    但为什么也不简单地记成"1 条违规"？
        因为那样一条写错的校验会伪装成一条发现问题的校验。
        本项目真发生过：重构时把 ``risk_tier`` 列改名后，
        对应校验每次都执行失败，却以"违规 1 行 + warning"的形式
        安静地挂了很多轮没人发现。
        **一条悄悄失效的校验，比没有校验更危险 ——
        它给了你"这里有人在把关"的错觉。**
    """
    results: list[CheckResult] = []

    for check in checks:
        execution_error: str | None = None
        violations = 0

        try:
            row = con.execute(check.sql).fetchone()
            violations = int(row[0]) if row and row[0] is not None else 0
        except duckdb.Error as exc:
            execution_error = f"{type(exc).__name__}: {exc}"

        result = CheckResult(check=check, violations=violations, execution_error=execution_error)
        results.append(result)

        if result.errored:
            # 校验坏了 —— 用最醒目的方式打出来，绝不能被当成"普通警告"埋掉
            logger.error(
                "  ‼ 校验「%s」执行失败（校验本身有问题，不是数据问题）：%s",
                check.name,
                execution_error,
            )
        elif result.passed:
            logger.info("  ✓ %s", check.name)
        else:
            level = logger.error if check.severity == "error" else logger.warning
            level(
                "  ✗ %s —— 违规 %s 行%s",
                check.name,
                f"{violations:,}",
                "" if check.severity == "error" else "（仅警告，不阻断）",
            )

    return results


def has_blocking_failure(results: list[CheckResult]) -> bool:
    """是否存在会阻断发布的失败。CI 用它决定退出码。"""
    return any(r.blocks_release for r in results)


def summarize(results: list[CheckResult]) -> dict[str, int]:
    """汇总统计，便于写入报告或监控。

    刻意把 ``errored``（校验坏了）与 ``failed_error`` / ``failed_warning``
    （校验正常但数据有问题）分成三个独立计数。
    把它们混在一起，就回到了"分不清是校验坏了还是数据坏了"的老问题上。
    """
    return {
        "total": len(results),
        "passed": sum(1 for r in results if r.passed),
        "errored": sum(1 for r in results if r.errored),
        "failed_error": sum(
            1 for r in results if not r.errored and r.violations > 0 and r.check.severity == "error"
        ),
        "failed_warning": sum(
            1 for r in results if not r.errored and r.violations > 0 and r.check.severity == "warning"
        ),
    }
