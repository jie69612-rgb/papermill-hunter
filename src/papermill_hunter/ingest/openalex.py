"""OpenAlex 采集：抓取全部被撤稿的学术作品。

【为什么需要 OpenAlex，光有 Retraction Watch 不够吗？】
    Retraction Watch 给的是"案底"——哪些论文被撤了、为什么撤。
    但它**没有对照组**：没有那些"同样可疑、只是还没被查出来"的论文，
    也没有所有论文的逐年引用轨迹。

    OpenAlex 补上了这两块：
      - 全量 2.5 亿作品图谱，可以构造"已撤稿 vs 未撤稿"的对照
      - 每篇作品带 ``counts_by_year``（逐年被引次数），
        这是做"撤稿代价"因果推断的关键因变量

    两份数据一结合，才可能从"讲故事"升级到"做推断"。

【核心工程问题：29 分钟的任务必须能断点续传】
    全量 135,584 条、678 页、约 29 分钟。这么长的任务，
    网络抖动、电脑休眠、手滑 Ctrl+C 都是必然事件。
    如果第 677 页失败就丢掉前面全部成果，这个管道是不可用的。

    所以设计成"每抓一页就立刻落盘 + 更新状态文件"：
      - 状态文件记录下一页的 cursor、已完成页数/条数
      - 中断后重跑，自动从断点继续
      - 每页独立成文件，天然可并行、可审计、可局部重跑
    —— 这叫**幂等 + 可恢复**，是生产级数据管道的基本要求。
"""

from __future__ import annotations

import gzip
import json
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from papermill_hunter.config import Settings, get_settings
from papermill_hunter.ingest.http import ApiClient
from papermill_hunter.logging_conf import get_logger

logger = get_logger(__name__)

# 只取本项目需要的字段。
#
# OpenAlex 的单条完整记录有 40 多个字段（含摘要倒排索引、
# 所有参考文献 ID、所有关联作品 ID 等），全量拉下来体积会翻好几倍。
# ``select`` 是 OpenAlex 官方支持的服务端字段裁剪 ——
# 在"源头"就减掉不需要的数据，比拉回来再丢弃高效得多。
# 这是数据工程里「尽早裁剪」原则的体现：能不下传的字节，就别下传。
SELECT_FIELDS = ",".join(
    [
        "id",
        "doi",
        "title",
        "publication_year",
        "publication_date",
        "type",
        "is_retracted",
        "cited_by_count",
        "authorships",
        "institutions",
        "primary_topic",
        "topics",
        "counts_by_year",
        "open_access",
        "language",
        "fwci",
        "referenced_works_count",
        "countries_distinct_count",
        "institutions_distinct_count",
        "biblio",
    ]
)

DEFAULT_FILTER = "is_retracted:true"


# ----------------------------------------------------------------------
# 目录与状态文件
# ----------------------------------------------------------------------
def dataset_dir(settings: Settings | None = None) -> Path:
    """原始数据落盘目录。"""
    settings = settings or get_settings()
    return settings.raw_dir / "openalex" / "retracted_works"


def _state_path(out_dir: Path) -> Path:
    return out_dir / "_state.json"


def _load_state(out_dir: Path) -> dict[str, Any] | None:
    path = _state_path(out_dir)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        # 状态文件损坏不能让整个任务失败 —— 最坏情况是重头再抓一遍
        logger.warning("状态文件损坏，将从头开始：%s", path)
        return None


def _save_state(out_dir: Path, state: dict[str, Any]) -> None:
    """原子写入状态文件。

    这里同样用"写临时文件 → 原子改名"。想象一下：
    如果正在写状态文件时断电，留下半个 JSON，下次启动解析失败 ——
    虽然我们做了降级（重头再来），但那意味着 29 分钟的抓取白费。
    原子写入把这种风险彻底消除。
    """
    path = _state_path(out_dir)
    state["updated_at"] = datetime.now(UTC).isoformat()
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _write_page(out_dir: Path, page_no: int, payload: dict[str, Any]) -> Path:
    """把一页原始响应写成 gzip 压缩的 JSON。

    为什么用 gzip？
        全量原始 JSON 约 1.6 GB。JSON 是纯文本、重复度极高（字段名反复出现），
        gzip 通常能压到原来的 1/6 左右，约 250 MB。
        而且 DuckDB / pandas 都能直接读 .json.gz，零额外代价。
        —— 存储成本几乎总是比计算成本更值得优化，因为它是长期的。
    """
    dest = out_dir / f"page-{page_no:05d}.json.gz"
    tmp = dest.with_name(dest.name + ".part")
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    tmp.replace(dest)
    return dest


# ----------------------------------------------------------------------
# 主抓取流程
# ----------------------------------------------------------------------
def fetch_retracted_works(
    settings: Settings | None = None,
    *,
    max_pages: int | None = None,
    force: bool = False,
) -> Path:
    """抓取全部被撤稿的作品，落盘为分页 gzip JSON。

    Args:
        settings: 配置对象。
        max_pages: 只抓前 N 页（用于小样本试跑），None 表示抓全量。
        force: 为 True 时清空已有数据重新抓取。

    Returns:
        数据集目录路径。
    """
    settings = settings or get_settings()
    settings.ensure_dirs()
    settings.warn_if_no_email()

    out_dir = dataset_dir(settings)
    out_dir.mkdir(parents=True, exist_ok=True)

    if force:
        removed = 0
        for stale in out_dir.glob("page-*.json.gz"):
            stale.unlink()
            removed += 1
        _state_path(out_dir).unlink(missing_ok=True)
        if removed:
            logger.info("force=True，已清空 %d 个旧分页文件", removed)

    state = _load_state(out_dir)

    if state and state.get("complete") and max_pages is None:
        logger.info(
            "数据已完整抓取（%s 条 / %d 页），跳过。需要重抓请加 --force",
            f"{state.get('records_done', 0):,}",
            state.get("pages_done", 0),
        )
        return out_dir

    if state is None:
        state = {
            "filter": DEFAULT_FILTER,
            "select": SELECT_FIELDS,
            "next_cursor": "*",
            "pages_done": 0,
            "records_done": 0,
            "total": None,
            "complete": False,
            "started_at": datetime.now(UTC).isoformat(),
        }
    else:
        logger.info(
            "检测到断点：已完成 %d 页 / %s 条，从断点继续。",
            state.get("pages_done", 0),
            f"{state.get('records_done', 0):,}",
        )

    url = f"{settings.openalex_base_url}/works"
    started = time.monotonic()

    with ApiClient(settings, qps=settings.openalex_qps, namespace="openalex") as client:
        while True:
            params = {
                "filter": state["filter"],
                "per-page": settings.openalex_per_page,
                "cursor": state["next_cursor"],
                "select": state["select"],
            }

            # 关闭缓存：cursor 每次都不同，缓存永远命中不了，
            # 只会白白在磁盘上堆积 1.6 GB 的无用文件。
            # "知道什么时候不该缓存"和"知道什么时候该缓存"同样重要。
            payload = client.get_json(url, params, use_cache=False)

            results = payload.get("results") or []
            meta = payload.get("meta") or {}

            if state.get("total") is None:
                state["total"] = meta.get("count")

            if not results:
                logger.info("服务端返回空结果，视为抓取完成。")
                state["complete"] = True
                _save_state(out_dir, state)
                break

            _write_page(out_dir, state["pages_done"], payload)
            state["pages_done"] += 1
            state["records_done"] += len(results)
            state["next_cursor"] = meta.get("next_cursor")
            _save_state(out_dir, state)

            _log_progress(state, started)

            if not state["next_cursor"]:
                state["complete"] = True
                _save_state(out_dir, state)
                logger.info("已到达数据末尾，抓取完成。")
                break

            if max_pages is not None and state["pages_done"] >= max_pages:
                logger.info(
                    "已达到 --max-pages=%d 限制，主动停止。状态已保存，下次运行可继续。",
                    max_pages,
                )
                break

        logger.info("本次运行统计：%s", client.describe_stats())

    return out_dir


def _log_progress(state: dict[str, Any], started: float) -> None:
    """打印带 ETA 的进度。

    长任务的可观测性非常重要：没有进度输出，你无法判断
    任务是"在正常跑"还是"已经卡死了"—— 只能干等。
    """
    done = state["records_done"]
    total = state["total"]
    elapsed = time.monotonic() - started

    if total:
        rate = done / elapsed if elapsed > 0 else 0.0
        remaining = (total - done) / rate / 60 if rate > 0 else 0.0
        logger.info(
            "进度 %s/%s（%.1f%%）| 已用 %.0fs | %.1f 条/秒 | 预计剩余 %.1f 分钟",
            f"{done:,}",
            f"{total:,}",
            done / total * 100,
            elapsed,
            rate,
            remaining,
        )
    else:
        logger.info("进度 %s 条 | 已用 %.0fs", f"{done:,}", elapsed)


# ----------------------------------------------------------------------
# 读回原始数据
# ----------------------------------------------------------------------
def iter_raw_pages(out_dir: Path) -> Iterator[dict[str, Any]]:
    """按页码顺序迭代原始分页文件。"""
    files = sorted(out_dir.glob("page-*.json.gz"))
    for path in files:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            yield json.load(fh)


def iter_raw_works(out_dir: Path) -> Iterator[dict[str, Any]]:
    """逐条迭代原始作品记录。"""
    for page in iter_raw_pages(out_dir):
        yield from page.get("results") or []
