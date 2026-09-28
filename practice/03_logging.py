import logging
import sys

_NOISY_LIBRARIES = ("httpx", "urllib3")

def setup_logging(level="INFO"): # 参数level，默认值是字符串INFO
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s | %(levelname)-7s | %(name)-20s | %(message)s",
            datefmt="%H:%M:%S",
        )
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    for name in _NOISY_LIBRARIES:
        logging.getLogger(name).setLevel(logging.WARNING)

def get_logger(name):
    return logging.getLogger(name)

if __name__ == "__main__":
    setup_logging("DEBUG")
    log = get_logger("demo")

    log.debug("这是一个DEBUG -- 排查问题才开")
    log.info("这是一个INFO -- 一般性信息")
    log.warning("这是一个WARNING -- 警告信息")
    log.error("这是一个ERROR -- 错误信息")

    print()
    log.info("演示：压制第三方库噪音")
    noisy = get_logger("httpx")
    noisy.info("这行【不会】显示 —— httpx 被压到 WARNING 级")
    noisy.warning("这行会显示")

    print()
    log.info("演示：异常处理")
    try:
        value = int("abc")
    except ValueError as exc:
        log.warning("解析失败，降级用 0 代替：%s", exc)
        value = 0
    log.info("降级后的值：%s", value)
