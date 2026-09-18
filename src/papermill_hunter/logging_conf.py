"""统一日志配置。

为什么数据管道要专门做日志？
    数据管道是"长时间、无人值守"运行的程序。跑 30 分钟后崩了，
    如果没有日志，你唯一的信息就是一句 Traceback，根本不知道
    是哪个数据源、哪一页、第几次重试出的问题。

日志分级约定（这是团队协作的通用语言）：
    DEBUG   —— 排查问题时才开，例如每次请求的 URL
    INFO    —— 进度播报，例如"已抓取 2000/135584 条"
    WARNING —— 可自动恢复的异常，例如"触发限流，退避 3.2 秒后重试"
    ERROR   —— 需要人介入，例如"重试 5 次仍失败，任务中止"
"""

from __future__ import annotations

import logging
import sys

_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)-30s | %(message)s"
_DATEFMT = "%H:%M:%S"

# 这些第三方库在 DEBUG 级别下会刷屏，压掉它们的噪音
_NOISY_LIBRARIES = ("httpx", "httpcore", "urllib3", "asyncio", "matplotlib", "PIL")


def setup_logging(level: str = "INFO") -> None:
    """初始化根日志器。应在每个入口脚本的最开始调用一次。"""
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(logging.Formatter(fmt=_FORMAT, datefmt=_DATEFMT))

    root = logging.getLogger()
    root.handlers.clear()  # 避免重复调用时叠加多个 handler 导致日志打两遍
    root.addHandler(handler)
    root.setLevel(level.upper())

    for name in _NOISY_LIBRARIES:
        logging.getLogger(name).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """获取带命名空间的日志器。惯例是传入 __name__。"""
    return logging.getLogger(name)
