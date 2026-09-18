"""脚本 04 —— 运行数据质量校验。

用法::

    .venv\\Scripts\\python.exe scripts\\04_check_quality.py

退出码：
    0 —— 全部通过（可能有 warning）
    1 —— 存在 error 级别失败

之所以要严格区分退出码，是为了能在 CI / 定时任务里使用：
只要数据质量不达标，就让整条流水线红灯，而不是把脏数据发布出去。
"""

from __future__ import annotations

import json
import sys

from papermill_hunter.config import get_settings
from papermill_hunter.logging_conf import get_logger, setup_logging
from papermill_hunter.warehouse.db import connect
from papermill_hunter.warehouse.quality import (
    CHECKS,
    REQUIRED_TABLES,
    has_blocking_failure,
    run_checks,
    summarize,
)


def preflight(con) -> list[str]:
    """预检：确认校验所依赖的表都已构建。

    为什么需要这一步？
        质量校验的 SQL 引用了具体的表。如果表还不存在（比如只构建了 staging
        就跑校验），每条校验都会因"表不存在"而报违规 ——
        结果是一屏红色的失败，但真实原因只有一个："你还没建完数仓"。
        这种误导性的输出比没有输出更浪费时间。

        所以先做一次明确的预检，缺表就直说缺哪张、该跑哪个脚本。
    """
    existing = {
        f"{schema}.{table}"
        for schema, table in con.execute(
            """
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_schema IN ('staging', 'intermediate', 'marts')
            """
        ).fetchall()
    }
    return [t for t in REQUIRED_TABLES if t not in existing]


def main() -> int:
    settings = get_settings()
    setup_logging(settings.log_level)
    log = get_logger("scripts.04_quality")

    con = connect(settings, require_exists=True)
    try:
        missing = preflight(con)
        if missing:
            log.error("数据仓库尚未构建完整，缺少以下表：")
            for table in missing:
                log.error("  - %s", table)
            log.error("请先运行：python scripts/03_build_warehouse.py")
            return 2
    finally:
        con.close()

    log.info("=" * 70)
    log.info("步骤 4：数据质量校验（共 %d 条规则）", len(CHECKS))
    log.info("=" * 70)

    con = connect(settings, require_exists=True)
    try:
        results = run_checks(con)

        # 顺带展示审计表：读了多少行、保留多少行、丢了多少行
        #
        # 【一个刚犯过的错误】
        # 这段最初写的是 `except Exception: log.debug(...)`，结果审计表因为一个
        # 类型转换问题（TIMESTAMPTZ 需要 pytz）查不出来时，错误被**静默吞掉**了 ——
        # 输出里只是"少了一段"，没有任何提示，我花了额外时间才定位到。
        # 教训：捕获宽泛异常本身没错，但**必须把异常内容记录下来**。
        # 静默的 except 会把"明确的失败"变成"神秘的缺失"，后者排查成本高得多。
        try:
            cursor = con.execute("SELECT * FROM staging.load_audit")
            columns = [c[0] for c in cursor.description]
            rows = cursor.fetchall()
            log.info("-" * 70)
            log.info("加载审计（读入 / 保留 / 丢弃）：")
            for row in rows:
                for name, value in zip(columns, row, strict=True):
                    log.info("  %-14s %s", name, value)
        except Exception as exc:  # noqa: BLE001
            log.warning("读取加载审计表失败（不影响质量校验）：%s: %s", type(exc).__name__, exc)
    finally:
        con.close()

    stats = summarize(results)

    log.info("-" * 70)
    log.info(
        "校验汇总：共 %d 条 | 通过 %d | 数据问题(error) %d | 数据问题(warning) %d | 校验失效 %d",
        stats["total"],
        stats["passed"],
        stats["failed_error"],
        stats["failed_warning"],
        stats["errored"],
    )

    # 把"校验本身坏了"单独再喊一遍。
    # 这类问题会被统计埋没，而它的危害是"你失去了一道防线却不知道" ——
    # 所以它值得在结尾再被点名一次。
    errored = [r for r in results if r.errored]
    if errored:
        log.error("-" * 70)
        log.error("以下 %d 条校验**执行失败**（是校验本身写错了，不是数据有问题）：", len(errored))
        for r in errored:
            log.error("    ‼ %s", r.check.name)
            log.error("      %s", r.execution_error)
        log.error("请修正这些校验的 SQL —— 失效的校验等于没有校验。")

    # 保存机器可读的结果，便于趋势监控（哪天开始变差的？）
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    out = settings.reports_dir / "quality_report.json"
    payload = {
        "summary": stats,
        "results": [
            {
                "name": r.check.name,
                "severity": r.check.severity,
                "violations": r.violations,
                # errored 字段让下游（看板、监控）能区分"校验坏了"与"数据坏了"。
                # 只存 passed 是不够的 —— 两者 passed 都是 False。
                "errored": r.errored,
                "execution_error": r.execution_error,
                "passed": r.passed,
                "rationale": r.check.rationale,
            }
            for r in results
        ],
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("质量报告已保存：%s", out)

    if has_blocking_failure(results):
        if errored:
            log.error("存在执行失败的校验，任务判定为未通过（失效的校验必须修）。")
        else:
            log.error("存在阻断级数据质量问题，校验未通过。")
        return 1

    log.info("数据质量校验通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
