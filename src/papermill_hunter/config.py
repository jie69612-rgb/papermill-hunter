"""全局配置中心。

为什么要有这么一个文件？
    数据管道里最容易被忽视、却最容易出事的，就是"配置散落各处"。
    如果 API 地址、超时、并发数、邮箱硬编码在每个脚本里，那么：
      - 换一个数据源要改 10 个文件
      - 一不小心把个人邮箱提交到 GitHub
      - 别人想复现你的项目，得一行行翻代码找参数

    所以专业做法是：**一处定义，全局引用；敏感信息走环境变量；写错类型立刻报错**。
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# config.py 位于 <项目根>/src/papermill_hunter/config.py，所以：
#   parents[0] = src/papermill_hunter
#   parents[1] = src
#   parents[2] = <项目根>          <- 我们要的
PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """项目全部可调参数。

    所有字段都可以用环境变量覆盖，前缀为 ``PMH_``，例如：
        PMH_CONTACT_EMAIL=me@example.com
        PMH_HTTP_MAX_RETRIES=8

    同时会自动读取项目根目录下的 ``.env`` 文件。
    """

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        env_prefix="PMH_",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------------- 身份：学术 API 的"礼貌池" ----------------
    contact_email: str = Field(
        default="",
        description="你的邮箱。OpenAlex/Crossref 依此将请求归入 polite pool，限流更宽松。",
    )

    # ---------------- 目录 ----------------
    data_dir: Path = Field(default=PROJECT_ROOT / "data")
    reports_dir: Path = Field(default=PROJECT_ROOT / "reports")

    # ---------------- HTTP 行为 ----------------
    http_timeout: float = Field(default=30.0, gt=0, description="单请求超时（秒）")
    http_max_retries: int = Field(default=5, ge=1, description="最大尝试次数（含首次）")
    http_backoff_base: float = Field(default=1.0, gt=0, description="指数退避基数（秒）")
    http_backoff_max: float = Field(default=60.0, gt=0, description="退避上限（秒）")

    # 大文件下载专用的读超时（秒），大于普通请求的 http_timeout。
    #
    # 【这个数字是怎么定出来的 —— 一次真实的调参过程】
    # 最初的实现直接复用了普通请求的 30 秒读超时，结果这个端点在下载途中
    # 频繁出现几十秒的停顿，于是"下几分钟就被打断、然后从零重下"，
    # 60 MB 的文件永远下不完。
    # 于是矫枉过正改成 300 秒 —— 又走向另一个极端：连接真的断了的时候，
    # 程序会**安静地干等 5 分钟**，从外部看和卡死没有任何区别，
    # 排查时极易误判（我自己就在这里绕了弯路）。
    #
    # 最终取值 90 秒：健康的连接下，90 秒足够传输 10 MB 以上；
    # 而一旦连续 90 秒一个字节都没收到，基本可以断定连接已经死了，应当果断重来。
    # —— 超时参数的本质是"你愿意等多久才承认失败"，它既是技术问题，也是体验问题。
    download_read_timeout: float = Field(default=90.0, gt=0)

    # 下载失败后的重试次数。由于该端点不支持 Range 断点续传，
    # 每次重试都要从零开始，所以次数给得比普通请求多一些。
    download_max_attempts: int = Field(default=8, ge=1)

    # 下载进度播报间隔（MB）。长任务没有进度输出，你无法区分
    # "正在正常下载"和"已经卡死"——只能干等，这是很糟的体验。
    download_progress_every_mb: float = Field(default=5.0, gt=0)

    # 默认 7 天。开发调试时可以设为 0，强制每次都走网络取最新数据。
    cache_ttl_seconds: int = Field(default=60 * 60 * 24 * 7, ge=0)

    # 是否沿用系统/环境变量里的代理设置。
    #
    # 【为什么默认 False —— 这是一个真实踩过的坑】
    # httpx 默认 trust_env=True，会通过 urllib.request.getproxies() 读取代理配置。
    # 在 Windows 上，这个函数连**注册表里的系统代理**都会读。
    # 于是当本机跑着 Clash / V2Ray 之类的工具时（ProxyEnable=1），
    # 所有请求都会被静默地塞进那个本地代理；一旦该代理对某个域名处理不当，
    # 就会报出极具误导性的 `[SSL: WRONG_VERSION_NUMBER]` —— 看起来像证书问题，
    # 实际是代理在中间作梗。而 curl 不读注册表，所以直连反而正常，
    # 这种"两个工具行为不一致"的现象最容易让人怀疑人生。
    #
    # 结论：显式声明你要不要代理，比依赖隐式环境探测可靠得多。
    # 如果你在公司内网、必须走代理才能出网，把它设为 true。
    http_trust_env: bool = Field(default=False)

    # git 命令是否沿用系统/全局代理配置。
    #
    # 【又一个真实踩过的坑】
    # 本机 git 全局配置里写着 http.proxy = http://127.0.0.1:7897（某科学上网工具留下的），
    # 而该代理对 gitlab.com 处理不当，报出的却是极具误导性的
    # `[SSL: WRONG_VERSION_NUMBER]`，看起来完全像证书故障。
    # 一旦显式禁用它，同一个仓库的克隆从"失败"变成"15 秒完成"。
    #
    # 我们只在命令行用 `git -c` 临时覆盖，**绝不修改用户的全局 git 配置** ——
    # 数据管道没有权力擅自改动你机器上的开发环境。
    git_use_proxy: bool = Field(default=False)

    # ---------------- 各数据源限流（QPS） ----------------
    openalex_qps: float = Field(default=8.0, gt=0)
    crossref_qps: float = Field(default=5.0, gt=0)
    generic_qps: float = Field(default=2.0, gt=0)

    # ---------------- OpenAlex ----------------
    openalex_base_url: str = "https://api.openalex.org"
    openalex_per_page: int = Field(default=200, ge=1, le=200)

    # ---------------- Retraction Watch（经 Crossref Labs 提供） ----------------
    retraction_watch_url: str = "https://api.labs.crossref.org/data/retractionwatch"

    # ---------------- 其他 ----------------
    user_agent: str = "PaperMillHunter/0.1 (data-analysis portfolio project)"
    log_level: str = "INFO"

    # ------------------------------------------------------------------
    # 校验
    # ------------------------------------------------------------------
    @field_validator("contact_email", "log_level", mode="before")
    @classmethod
    def _strip_strings(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = value.upper()
        if upper not in allowed:
            raise ValueError(f"log_level 必须是 {sorted(allowed)} 之一，收到 {value!r}")
        return upper

    # ------------------------------------------------------------------
    # 派生目录（不单独配置，避免出现"配置之间互相矛盾"的状态）
    # ------------------------------------------------------------------
    @property
    def raw_dir(self) -> Path:
        """原始层：从数据源拿到的、未经任何修改的数据。"""
        return self.data_dir / "raw"

    @property
    def interim_dir(self) -> Path:
        """中间层：清洗过、但还没建模的数据。"""
        return self.data_dir / "interim"

    @property
    def processed_dir(self) -> Path:
        """成品层：可直接用于分析和可视化的宽表。"""
        return self.data_dir / "processed"

    @property
    def cache_dir(self) -> Path:
        """HTTP 响应缓存目录。"""
        return self.data_dir / ".cache"

    @property
    def warehouse_path(self) -> Path:
        """DuckDB 数据仓库文件。"""
        return self.data_dir / "warehouse.duckdb"

    @property
    def figures_dir(self) -> Path:
        return self.reports_dir / "figures"

    # ------------------------------------------------------------------
    def ensure_dirs(self) -> None:
        """创建所有必需目录。幂等，可重复调用。"""
        for path in (
            self.raw_dir,
            self.interim_dir,
            self.processed_dir,
            self.cache_dir,
            self.figures_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def warn_if_no_email(self) -> None:
        """没有配置邮箱时给出友好提示，而不是静默降级。"""
        if not self.contact_email:
            logger.warning(
                "未配置 PMH_CONTACT_EMAIL，将无法进入 OpenAlex/Crossref 礼貌池，"
                "可能遭遇更严格的限流。请复制 .env.example 为 .env 并填写邮箱。"
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """获取全局唯一的配置实例。

    用 lru_cache 做单例：配置只在首次调用时解析一次，
    之后所有模块共享同一个对象 —— 既省去重复读文件的 I/O，
    也保证整个进程内配置绝对一致。
    """
    return Settings()
