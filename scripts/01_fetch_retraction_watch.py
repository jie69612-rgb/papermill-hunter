"""脚本 01 —— 采集 Retraction Watch 全量撤稿数据库，并输出数据体检报告。

运行方式（在项目根目录）::

    .venv\\Scripts\\python.exe scripts\\01_fetch_retraction_watch.py
    .venv\\Scripts\\python.exe scripts\\01_fetch_retraction_watch.py --force

数据通过 ``git clone`` 从 Crossref 官方 GitLab 仓库获取（约 63 MB，15 秒左右）。
重复运行会自动执行 ``git pull`` 增量更新，不会重复下载。

这是整个项目的数据起点：把"案底库"拉到本地。
"""

from __future__ import annotations

import argparse
import json

from papermill_hunter.config import get_settings
from papermill_hunter.ingest.retraction_watch import (
    fetch_retraction_watch,
    read_retraction_watch,
    repo_generated_date,
    summarize,
)
from papermill_hunter.logging_conf import get_logger, setup_logging


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="采集 Retraction Watch 撤稿数据库")
    parser.add_argument(
        "--force",
        action="store_true",
        help="删除本地仓库后重新克隆",
    )
    parser.add_argument(
        "--strategy",
        choices=["auto", "git", "http"],
        default="auto",
        help="获取策略：git（推荐）/ http（已弃用的 Labs API 兜底）/ auto",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    settings = get_settings()
    setup_logging(settings.log_level)
    log = get_logger("scripts.01_fetch")

    log.info("=" * 70)
    log.info("步骤 1：采集 Retraction Watch 全量撤稿数据库")
    log.info("=" * 70)

    path = fetch_retraction_watch(settings, force=args.force, strategy=args.strategy)
    log.info("数据文件：%s", path)
    log.info("数据版本：%s（由上游生成）", repo_generated_date(settings) or "未知")

    df = read_retraction_watch(path)
    report = summarize(df)

    log.info("-" * 70)
    log.info("【数据体检报告】")
    log.info("-" * 70)

    print(f"  行数：{report['行数']:,}")
    print(f"  列数：{report['列数']}")
    print(f"  撤稿日期范围：{report.get('撤稿日期范围', '未知')}")
    print(f"  日期无法解析：{report.get('撤稿日期无法解析的行数', 0):,} 行")

    print("\n  缺失率最高的 5 列：")
    for column, rate in report.get("缺失率最高的 5 列", {}).items():
        print(f"    {rate:5.1f}%  {column}")

    print("\n  撤稿性质分布：")
    for value, count in report.get("撤稿性质分布", {}).items():
        print(f"    {count:7,d}  {value or '(空)'}")

    print("\n  国家/地区 Top10（多值已拆分）：")
    for value, count in report.get("国家分布 Top10（已拆分多值）", {}).items():
        print(f"    {count:7,d}  {value}")

    print("\n  撤稿原因 Top15（多值已拆分）：")
    for value, count in report.get("撤稿原因 Top15（已拆分多值）", {}).items():
        print(f"    {count:7,d}  {value}")

    paper_mill = report.get("标注为 Paper Mill 的记录数", 0)
    print(f"\n  ★ 官方明确标注为「Paper Mill」的记录：{paper_mill:,} 条")
    print("    这是本项目的'标准答案'——有标签才能做有监督验证。")

    # 顺手把体检报告存成 JSON，方便后续对比不同日期的数据变化
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    out = settings.reports_dir / "data_health_retraction_watch.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    log.info("体检报告已保存：%s", out)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
