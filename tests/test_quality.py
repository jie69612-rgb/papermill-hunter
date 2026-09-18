"""数据质量校验框架的单元测试。

这些测试全部使用**内存 DuckDB + 合成数据**，不依赖真实数据集：
  - 快：毫秒级
  - 隔离：不依赖"今天上游有没有出问题"
  - 可复现：能精确构造出超长、负值、重复键这些边界情况
"""

from __future__ import annotations

import duckdb
import pytest

from papermill_hunter.warehouse.quality import (
    CHECKS,
    Check,
    CheckResult,
    has_blocking_failure,
    run_checks,
    summarize,
)


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    """一个内存数据库，带一张有重复主键的表。"""
    connection = duckdb.connect(":memory:")
    connection.execute("CREATE TABLE t AS SELECT * FROM (VALUES (1), (2), (2), (3)) AS v(id)")
    yield connection
    connection.close()


# ----------------------------------------------------------------------
# 核心行为
# ----------------------------------------------------------------------


def test_passing_check_reports_zero_violations(con: duckdb.DuckDBPyConnection) -> None:
    checks = (Check(name="id 非空", sql="SELECT COUNT(*) FROM t WHERE id IS NULL"),)
    results = run_checks(con, checks)

    assert len(results) == 1
    assert results[0].violations == 0
    assert results[0].passed
    assert not results[0].blocks_release


def test_failing_check_counts_violations(con: duckdb.DuckDBPyConnection) -> None:
    checks = (Check(name="id 唯一", sql="SELECT COUNT(*) - COUNT(DISTINCT id) FROM t"),)
    results = run_checks(con, checks)

    assert results[0].violations == 1, "有 4 行但只有 3 个不同 id，应有 1 行重复"
    assert not results[0].passed


def test_broken_check_sql_is_recorded_not_raised(con: duckdb.DuckDBPyConnection) -> None:
    """校验语句本身写错时，应该被记录成"执行失败"，而不是让整个流程崩掉。

    理由：质量校验的职责就是"报告问题"。如果它一碰到问题自己就崩了，
    那它就永远没法告诉你到底哪里出了问题 —— 这是一个自相矛盾的设计。

    【关键区分】执行失败（``errored``）与发现违规（``violations > 0``）必须分开。
    最初两者都记成"违规 1 行"，导致一条写错的校验可以伪装成一条发现问题的校验，
    安静地挂很久没人发现。本项目真的发生过：重构时改了一列的名字，
    对应校验失效了好几轮，而报告上只显示成一条普通的 warning。
    """
    checks = (Check(name="坏 SQL", sql="SELECT COUNT(*) FROM 这张表不存在"),)
    results = run_checks(con, checks)  # 不应抛异常

    assert results[0].errored is True, "执行失败必须被标记为 errored"
    assert results[0].execution_error is not None
    # 执行失败不应被计成"发现了 N 条违规" —— 那是两件完全不同的事
    assert results[0].violations == 0, "执行失败的校验不应伪装成有违规数据"
    assert not results[0].passed


def test_errored_check_always_blocks_release(con: duckdb.DuckDBPyConnection) -> None:
    """校验执行失败一律阻断发布，与它声明的 severity 无关。

    这条规则是刻意的：**一条跑不起来的校验等于没有校验**，
    而"我们以为有校验"比"我们知道没有校验"危险得多。
    所以哪怕它标了 warning，只要没跑起来就必须红灯。
    """
    checks = (Check(name="坏 SQL", sql="SELECT * FROM 不存在的表", severity="warning"),)
    results = run_checks(con, checks)

    assert results[0].errored is True
    assert results[0].blocks_release is True, "执行失败的 warning 校验也必须阻断"
    assert has_blocking_failure(results) is True


def test_error_severity_blocks_release(con: duckdb.DuckDBPyConnection) -> None:
    checks = (
        Check(
            name="重复主键",
            sql="SELECT COUNT(*) - COUNT(DISTINCT id) FROM t",
            severity="error",
        ),
    )
    results = run_checks(con, checks)

    assert has_blocking_failure(results) is True


def test_warning_severity_does_not_block_release(con: duckdb.DuckDBPyConnection) -> None:
    """warning 级别失败只记录，不阻断发布。

    "什么必须拦住发布、什么只需记录"是一个业务判断，
    不该被隐藏在代码细节里，所以它在 Check 定义上显式声明。
    """
    checks = (
        Check(
            name="历史异常日期",
            sql="SELECT COUNT(*) - COUNT(DISTINCT id) FROM t",
            severity="warning",
        ),
    )
    results = run_checks(con, checks)

    assert results[0].passed is False
    assert results[0].blocks_release is False
    assert has_blocking_failure(results) is False


def test_summarize_counts_by_severity(con: duckdb.DuckDBPyConnection) -> None:
    """汇总必须把「校验坏了」和「数据有问题」分成独立计数。

    混在一起就又回到了"分不清是校验坏了还是数据坏了"的老问题上。
    """
    checks = (
        Check(name="通过", sql="SELECT 0"),
        Check(name="error 失败", sql="SELECT 3", severity="error"),
        Check(name="warning 失败", sql="SELECT 5", severity="warning"),
        Check(name="执行失败", sql="SELECT * FROM 不存在的表", severity="error"),
    )
    stats = summarize(run_checks(con, checks))

    assert stats == {
        "total": 4,
        "passed": 1,
        "errored": 1,
        "failed_error": 1,
        "failed_warning": 1,
    }


def test_null_result_is_treated_as_zero_violations(con: duckdb.DuckDBPyConnection) -> None:
    """聚合查询在空表上返回 NULL，应视为 0 条违规而不是崩溃。"""
    checks = (Check(name="空表聚合", sql="SELECT MAX(id) FROM t WHERE 1 = 0"),)
    results = run_checks(con, checks)

    assert results[0].violations == 0
    assert results[0].passed


# ----------------------------------------------------------------------
# CheckResult 语义
# ----------------------------------------------------------------------


def test_check_result_semantics() -> None:
    error_check = Check(name="x", sql="", severity="error")
    warning_check = Check(name="y", sql="", severity="warning")

    assert CheckResult(error_check, 0).passed is True
    assert CheckResult(error_check, 0).blocks_release is False
    assert CheckResult(error_check, 5).blocks_release is True
    assert CheckResult(warning_check, 5).passed is False
    assert CheckResult(warning_check, 5).blocks_release is False


# ----------------------------------------------------------------------
# 校验清单本身的规范性
# ----------------------------------------------------------------------


def test_every_check_has_a_name() -> None:
    for check in CHECKS:
        assert check.name.strip(), f"存在没有名称的校验：{check}"


def test_every_error_check_documents_its_rationale() -> None:
    """每条 error 级校验都必须写清楚"为什么校验这一条"。

    这是一条"元规则"：没有理由的校验，半年后没人敢删、也没人敢改，
    因为它看起来很重要却不知道重要在哪。
    强制写 rationale，是让校验清单能长期维护的最低成本手段。
    """
    missing = [c.name for c in CHECKS if c.severity == "error" and not c.rationale.strip()]
    assert not missing, f"以下 error 级校验缺少 rationale：{missing}"


def test_check_sql_returns_exactly_one_column() -> None:
    """校验 SQL 必须只返回一列（违规行数），否则框架取 row[0] 会取错值。

    这类约定如果不测，很容易在新增校验时被悄悄破坏：
    有人返回了两列，框架只读第一列，校验照常"通过"，但检查的其实不是他想检查的东西。
    """
    con = duckdb.connect(":memory:")
    con.execute("CREATE SCHEMA staging")
    con.execute("CREATE TABLE staging.retraction_watch(record_id BIGINT)")
    try:
        for check in CHECKS:
            try:
                cursor = con.execute(check.sql)
            except duckdb.Error:
                # 表结构不完整导致的失败不算问题，这个测试只关心列数
                continue
            assert len(cursor.description) == 1, (
                f"校验「{check.name}」返回了 {len(cursor.description)} 列，应当只返回 1 列"
            )
    finally:
        con.close()
