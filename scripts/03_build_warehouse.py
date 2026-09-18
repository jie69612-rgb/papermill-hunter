"""脚本 03 —— 构建 DuckDB 数据仓库（staging → intermediate → marts）。

用法::

    .venv\\Scripts\\python.exe scripts\\03_build_warehouse.py
    .venv\\Scripts\\python.exe scripts\\03_build_warehouse.py --layers staging
    .venv\\Scripts\\python.exe scripts\\03_build_warehouse.py --query "SELECT * FROM staging.retraction_watch LIMIT 5"

前置条件：先运行 scripts/01_fetch_retraction_watch.py 采集数据。
"""

from __future__ import annotations

import argparse

from papermill_hunter.config import get_settings
from papermill_hunter.logging_conf import get_logger, setup_logging
from papermill_hunter.warehouse.db import LAYER_ORDER, build, connect


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="构建 DuckDB 数据仓库")
    parser.add_argument(
        "--layers",
        nargs="+",
        default=list(LAYER_ORDER),
        choices=list(LAYER_ORDER),
        help="只构建指定的层（默认全部）",
    )
    parser.add_argument(
        "--query",
        default=None,
        help="构建完成后直接执行一条 SQL 并打印结果（便于快速检查）",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    settings = get_settings()
    setup_logging(settings.log_level)
    log = get_logger("scripts.03_build_warehouse")

    log.info("=" * 70)
    log.info("步骤 3：构建数据仓库")
    log.info("仓库文件：%s", settings.warehouse_path)
    log.info("=" * 70)

    executed = build(settings, layers=tuple(args.layers))
    log.info("本次共执行 %d 个模型。", len(executed))

    if args.query:
        con = connect(settings)
        try:
            log.info("-" * 70)
            log.info("执行查询：%s", args.query)
            log.info("-" * 70)
            result = con.execute(args.query)
            columns = [c[0] for c in result.description]
            rows = result.fetchall()
            print(" | ".join(columns))
            print("-" * 70)
            for row in rows:
                print(" | ".join("" if v is None else str(v) for v in row))
            print(f"({len(rows)} 行)")
        finally:
            con.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
