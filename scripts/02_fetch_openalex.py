"""脚本 02 —— 抓取 OpenAlex 全量撤稿作品。

用法::

    # 小样本试跑（先抓 2 页 = 400 条，验证管道是否通畅）
    .venv\\Scripts\\python.exe scripts\\02_fetch_openalex.py --max-pages 2

    # 全量抓取（678 页 / 约 29 分钟 / 约 250 MB）
    .venv\\Scripts\\python.exe scripts\\02_fetch_openalex.py

    # 强制重新抓取
    .venv\\Scripts\\python.exe scripts\\02_fetch_openalex.py --force

任务被中断（网络故障 / Ctrl+C / 电脑休眠）后，直接重新运行同一条命令即可，
脚本会自动从断点继续，不会重复抓取已完成的部分。
"""

from __future__ import annotations

import argparse

from papermill_hunter.config import get_settings
from papermill_hunter.ingest.openalex import dataset_dir, fetch_retracted_works
from papermill_hunter.logging_conf import get_logger, setup_logging


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="抓取 OpenAlex 中全部被撤稿的学术作品")
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        metavar="N",
        help="最多抓取 N 页（每页 200 条），用于小样本试跑；不指定则抓全量",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="清空已有数据后重新抓取",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    settings = get_settings()
    setup_logging(settings.log_level)
    log = get_logger("scripts.02_fetch_openalex")

    log.info("=" * 70)
    log.info("步骤 2：采集 OpenAlex 全量撤稿作品")
    if args.max_pages:
        log.info("模式：小样本试跑（最多 %d 页）", args.max_pages)
    else:
        log.info("模式：全量抓取（约 678 页，预计 25~35 分钟）")
    log.info("=" * 70)

    out_dir = fetch_retracted_works(settings, max_pages=args.max_pages, force=args.force)

    total_bytes = sum(f.stat().st_size for f in out_dir.glob("page-*.json.gz"))
    n_pages = len(list(out_dir.glob("page-*.json.gz")))

    log.info("-" * 70)
    log.info("数据落盘目录：%s", out_dir)
    log.info("分页文件：%d 个，合计 %.1f MB（gzip 压缩后）", n_pages, total_bytes / 1024 / 1024)
    log.info("-" * 70)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
