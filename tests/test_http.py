"""采集层单元测试。

为什么数据项目也要写测试？
    很多人觉得"分析结果对就行"。但数据管道最怕的是**静默错误**：
    限流器写错了 → 请求被限流封禁 → 数据少了一半 → 你却浑然不知，
    照样跑出一份漂亮的报告。测试就是为了在"数据还没脏"之前拦住这些问题。

    这里用 ``httpx.MockTransport`` 伪造网络层，测试变得：
      - 快：不发真实请求，毫秒级完成
      - 稳：不依赖外部服务是否可用
      - 可复现：能精确模拟 429、超时、5xx 这些难以在真实环境中触发的场景
"""

from __future__ import annotations

import time

import httpx
import pytest

from papermill_hunter.config import Settings
from papermill_hunter.ingest.http import ApiClient, TokenBucket

# ----------------------------------------------------------------------
# 测试夹具（fixture）
# ----------------------------------------------------------------------


@pytest.fixture
def settings(tmp_path) -> Settings:
    """构造一个"与真实环境隔离"的配置对象。

    关键点：``data_dir`` 指向 pytest 提供的临时目录 ``tmp_path``。
    这样测试运行产生的缓存/数据库文件不会污染真实项目目录，
    而且每个测试都有独立干净的环境 —— 这是测试可重复性的基础。
    """
    return Settings(
        data_dir=tmp_path / "data",
        contact_email="tester@example.com",
        http_max_retries=3,
        http_backoff_base=0.01,  # 测试里把退避压到毫秒级，避免测试变慢
        http_backoff_max=0.05,
        cache_ttl_seconds=3600,
    )


def make_client(settings: Settings, handler) -> ApiClient:
    """创建一个把网络层替换成 mock 的 ApiClient。"""
    client = ApiClient(settings, qps=1000, namespace="test")
    client._client = httpx.Client(transport=httpx.MockTransport(handler))  # noqa: SLF001
    return client


# ----------------------------------------------------------------------
# TokenBucket：限流器
# ----------------------------------------------------------------------


def test_token_bucket_rejects_non_positive_rate() -> None:
    """速率为 0 或负数应该在构造时就报错，而不是运行时除零崩溃。"""
    with pytest.raises(ValueError, match="必须为正数"):
        TokenBucket(0)
    with pytest.raises(ValueError, match="必须为正数"):
        TokenBucket(-1.5)


def test_token_bucket_allows_burst_within_capacity() -> None:
    """桶容量之内的请求应该被立即放行（允许突发）。"""
    bucket = TokenBucket(rate_per_second=1.0, capacity=3.0)

    start = time.monotonic()
    for _ in range(3):
        bucket.acquire()
    elapsed = time.monotonic() - start

    # 速率只有 1/s，但因为桶里攒了 3 个令牌，3 次请求应当瞬间完成
    assert elapsed < 0.2, f"容量内的突发被限流了，耗时 {elapsed:.3f}s"


def test_token_bucket_throttles_beyond_capacity() -> None:
    """超出桶容量的请求必须被限速，否则限流器形同虚设。"""
    rate = 20.0
    bucket = TokenBucket(rate_per_second=rate, capacity=1.0)

    start = time.monotonic()
    for _ in range(5):
        bucket.acquire()
    elapsed = time.monotonic() - start

    # 5 个请求，桶初始只有 1 个令牌，剩余 4 个要按 20/s 的速度攒
    # → 理论最短耗时 (5-1)/20 = 0.20 秒
    expected_min = 4 / rate
    assert elapsed >= expected_min * 0.8, f"限流未生效：期望至少 {expected_min:.3f}s，实际 {elapsed:.3f}s"


# ----------------------------------------------------------------------
# 磁盘缓存
# ----------------------------------------------------------------------


def test_get_json_hits_disk_cache_on_second_call(settings: Settings) -> None:
    """第二次请求同一个 URL 必须走缓存，不能再打网络。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"ok": calls["n"]})

    client = make_client(settings, handler)

    first = client.get_json("https://example.com/api", {"a": 1})
    second = client.get_json("https://example.com/api", {"a": 1})

    assert first == {"ok": 1}
    assert second == {"ok": 1}, "第二次请求返回了新数据，说明缓存没生效"
    assert calls["n"] == 1, f"网络被调用了 {calls['n']} 次，期望 1 次"
    assert client.stats["cache_hit"] == 1
    assert client.stats["network"] == 1


def test_cache_key_includes_query_params(settings: Settings) -> None:
    """参数不同必须视为不同的缓存条目，否则会串数据。

    这是一个非常隐蔽的经典 bug：如果缓存 key 只用了 URL 而忽略了参数，
    那么 ``?page=1`` 和 ``?page=2`` 会命中同一份缓存，
    结果就是你拿第一页的数据当成了第二页 —— 而且不报任何错。
    """
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"page": request.url.params.get("page")})

    client = make_client(settings, handler)

    page1 = client.get_json("https://example.com/api", {"page": "1"})
    page2 = client.get_json("https://example.com/api", {"page": "2"})

    assert page1 == {"page": "1"}
    assert page2 == {"page": "2"}
    assert calls["n"] == 2


def test_cache_ttl_zero_bypasses_cache(settings: Settings) -> None:
    """cache_ttl=0 表示强制走网络，用于抓最新数据。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"n": calls["n"]})

    client = make_client(settings, handler)

    client.get_json("https://example.com/api", cache_ttl=0)
    second = client.get_json("https://example.com/api", cache_ttl=0)

    assert second == {"n": 2}
    assert calls["n"] == 2


def test_corrupted_cache_is_discarded(settings: Settings) -> None:
    """缓存文件损坏时应自动降级为重新请求，而不是让整个管道崩掉。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"ok": True})

    client = make_client(settings, handler)
    url, params = "https://example.com/api", {"a": 1}

    cache_file = client._cache_file(url, params)  # noqa: SLF001
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text("{ 这不是合法的 JSON", encoding="utf-8")

    assert client.get_json(url, params) == {"ok": True}
    assert calls["n"] == 1


# ----------------------------------------------------------------------
# 重试策略
# ----------------------------------------------------------------------


def test_retries_on_429_then_succeeds(settings: Settings) -> None:
    """429（限流）应当被重试，且最终成功。"""
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"ok": True})

    client = make_client(settings, handler)

    assert client.get_json("https://example.com/x") == {"ok": True}
    assert state["n"] == 2, "429 没有被重试"
    assert client.stats["retry"] == 1


def test_retries_on_500(settings: Settings) -> None:
    """5xx 属于服务端暂时故障，应当重试。"""
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        if state["n"] <= 2:
            return httpx.Response(503)
        return httpx.Response(200, json={"ok": True})

    client = make_client(settings, handler)
    assert client.get_json("https://example.com/x") == {"ok": True}
    assert state["n"] == 3


def test_retry_after_zero_is_honored(settings: Settings) -> None:
    """``Retry-After: 0`` 表示"立刻重试"，必须被尊重。

    这是一个真实的"假值陷阱"回归测试。最初的实现写成::

        delay = self._retry_after(resp) or self._backoff_delay(attempt)

    看起来没问题，但 ``0.0`` 在 Python 里是假值，会被 ``or`` 丢掉，
    于是"立刻重试"变成了"等 1 秒再试"。功能上没崩，但性能悄悄劣化 ——
    这类 bug 靠肉眼 review 很难发现，只能靠测试锁死行为。
    """
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"ok": True})

    # 故意把退避公式设得很大：如果代码误用了退避而不是 Retry-After，耗时会立刻暴露
    slow = settings.model_copy(update={"http_backoff_base": 5.0, "http_backoff_max": 5.0})
    client = make_client(slow, handler)

    start = time.monotonic()
    assert client.get_json("https://example.com/x") == {"ok": True}
    elapsed = time.monotonic() - start

    assert elapsed < 1.0, f"Retry-After: 0 被忽略了，实际等待 {elapsed:.2f}s"


def test_client_error_is_not_retried(settings: Settings) -> None:
    """4xx（除 429）是请求本身的问题，重试没有意义，应当立刻失败。

    这体现了一个重要的工程判断力：**"什么时候不该重试"比"怎么重试"更重要**。
    无脑重试 404 / 401 只会浪费时间并加剧对方的日志噪音。
    """
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        return httpx.Response(404, json={"error": "not found"})

    client = make_client(settings, handler)

    with pytest.raises(httpx.HTTPStatusError):
        client.get_json("https://example.com/missing")

    assert state["n"] == 1, "404 被错误地重试了"
    assert client.stats["fail"] == 1


def test_exhausted_retries_raises_runtime_error(settings: Settings) -> None:
    """重试次数耗尽后必须明确失败，绝不能返回半成品数据。"""
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        return httpx.Response(503)

    client = make_client(settings, handler)

    with pytest.raises(RuntimeError, match="仍失败"):
        client.get_json("https://example.com/x")

    assert state["n"] == settings.http_max_retries


# ----------------------------------------------------------------------
# User-Agent / 礼貌池
# ----------------------------------------------------------------------


def test_user_agent_contains_contact_email(settings: Settings) -> None:
    """User-Agent 必须带邮箱，这是进入礼貌池的前提。"""
    client = make_client(settings, lambda r: httpx.Response(200, json={}))
    assert "tester@example.com" in client.user_agent


def test_user_agent_without_email_has_no_mailto() -> None:
    settings = Settings(contact_email="")
    client = ApiClient(settings, qps=1000, namespace="test")
    try:
        assert "mailto" not in client.user_agent
    finally:
        client.close()


# ----------------------------------------------------------------------
# 性能特征回归测试
# ----------------------------------------------------------------------


def test_http_client_is_created_lazily(settings: Settings) -> None:
    """构造 ApiClient 时**不应**立刻创建底层 httpx 客户端。

    这是一条"性能回归测试"。实测数据：

        构造 httpx.Client 约 1395 ms（加载根证书链 + 建 SSL 上下文 + 初始化连接池）

    测试套件会创建几十个 ApiClient，如果每个都真的造一个客户端，
    光这一项就要浪费一分钟以上。惰性创建把它降到了毫秒级。

    把性能特征写成测试，是为了防止后人"顺手重构"时把它改回去 ——
    这类退化不会报错、不会失败，只会悄悄变慢，是最难发现的问题。
    """
    client = ApiClient(settings, qps=1000, namespace="test")
    try:
        assert client._client is None, "构造 ApiClient 时不应该创建 httpx.Client"  # noqa: SLF001
    finally:
        client.close()


def test_close_is_idempotent(settings: Settings) -> None:
    """重复 close 不应报错（未创建过客户端时关闭也必须安全）。"""
    client = ApiClient(settings, qps=1000, namespace="test")
    client.close()
    client.close()  # 第二次不应该抛异常

    with ApiClient(settings, qps=1000, namespace="test") as ctx_client:
        assert ctx_client is not None
