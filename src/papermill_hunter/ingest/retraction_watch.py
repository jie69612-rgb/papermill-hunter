"""Retraction Watch 数据库采集。

【数据源说明】
    Retraction Watch 是学术打假领域最权威的独立数据库，由两名记者
    Ivan Oransky 和 Adam Marcus 于 2010 年创办，十几年间人工核实了
    全球绝大多数撤稿事件。2023 年 9 月被 Crossref 收购后向公众开放，
    每个工作日更新一次。

    这是本项目能成立的**关键前提**：不需要写爬虫、不需要对抗反爬、
    不触碰任何灰色地带，就能拿到 7 万多条人工核实过的撤稿记录。
    —— 做数据项目的第一课，是"选一个干净的数据源"，这比技术更重要。

【为什么最终用 GitLab 而不是 HTTP API —— 一次真实的踩坑】
    最初的实现调用 Crossref Labs 的 HTTP 端点
    ``https://api.labs.crossref.org/data/retractionwatch``。
    实测结果非常糟：平均速率只有约 50 KB/s，下载 63 MB 需要数小时，
    而且该端点返回 ``Transfer-Encoding: chunked``、**没有 Content-Length**、
    **不支持 Range 断点续传**，一旦中断只能从零重下。

    查了官方文档才发现真正的原因：**这个 Labs API 端点已被官方弃用**。
    Crossref 在 2026 年 5 月的公告中明确表示不再向该端点推送新数据，
    并建议使用者迁移到 GitLab 数据仓库：
        https://gitlab.com/crossref/retraction-watch-data

    改用 ``git clone`` 之后：**15 秒下载完成**，比原来快约 1000 倍，
    而且天然具备增量更新（``git pull``）、版本追溯和完整性校验。

    【教训】遇到一个又慢又难用的接口时，先花五分钟查官方文档，
    很可能不是你的代码有问题，而是这个接口本身正在被淘汰。
    埋头优化一个正在被废弃的端点，是纯粹的浪费。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from papermill_hunter.config import Settings, get_settings
from papermill_hunter.ingest.http import ApiClient
from papermill_hunter.logging_conf import get_logger

logger = get_logger(__name__)

# 官方数据仓库（Crossref 维护，每个工作日更新）
GITLAB_REPO_URL = "https://gitlab.com/crossref/retraction-watch-data.git"
DATASET_FILENAME = "retraction_watch.csv"
README_FILENAME = "README.md"

# 已弃用的 Labs API 端点，仅作为 GitLab 不可达时的兜底方案保留
LEGACY_LABS_API_URL = "https://api.labs.crossref.org/data/retractionwatch"

# 原始 CSV 的列名 → 规范化列名（snake_case）
#
# 为什么要改名？原始列名有空格、大小写混杂（"Record ID"、"RetractionDOI"），
# 在不同数据库里的引用方式不一致，是"数据管道里最烦人的低级错误来源"。
_COLUMN_MAP: dict[str, str] = {
    "Record ID": "record_id",
    "Title": "title",
    "Subject": "subject",
    "Institution": "institution",
    "Journal": "journal",
    "Publisher": "publisher",
    "Country": "country",
    "Author": "author",
    "URLS": "urls",
    "ArticleType": "article_type",
    "RetractionDate": "retraction_date",
    "RetractionDOI": "retraction_doi",
    "RetractionPubMedID": "retraction_pubmed_id",
    "OriginalPaperDate": "original_paper_date",
    "OriginalPaperDOI": "original_paper_doi",
    "OriginalPaperPubMedID": "original_paper_pubmed_id",
    "RetractionNature": "retraction_nature",
    "Reason": "reason",
    "Paywalled": "paywalled",
    "Notes": "notes",
}


# ----------------------------------------------------------------------
# 路径
# ----------------------------------------------------------------------
def repo_dir(settings: Settings | None = None) -> Path:
    """本地 git 仓库目录。"""
    settings = settings or get_settings()
    return settings.raw_dir / "retraction_watch" / "gitlab"


def dataset_path(settings: Settings | None = None) -> Path:
    """数据文件的标准位置。"""
    return repo_dir(settings) / DATASET_FILENAME


# ----------------------------------------------------------------------
# git 操作
# ----------------------------------------------------------------------
def _git_command(settings: Settings) -> list[str]:
    """构造带代理控制的 git 命令前缀。

    【为什么必须显式处理代理】
        这是本项目踩过的一个真实大坑：本机的 git 全局配置里写着
            http.proxy = http://127.0.0.1:7897
        （某个科学上网工具留下的），而该代理对 gitlab.com 处理不当，
        报出的却是极具误导性的 ``[SSL: WRONG_VERSION_NUMBER]``，
        看起来完全像证书或网络故障，让人往错误方向排查很久。

        关键的工程原则在这里：我们用 ``-c`` 在**命令行临时覆盖**配置，
        而**不是**去修改用户的全局 git 配置。
        一个数据管道没有权力擅自改动你机器上的开发环境 ——
        你永远不会希望"跑个分析脚本"把自己本机的 git 配置给改了。
    """
    cmd = ["git"]
    if not settings.git_use_proxy:
        # 显式置空，覆盖全局/系统级代理设置
        cmd += ["-c", "http.proxy=", "-c", "https.proxy="]
    return cmd


def _run_git(args: list[str], settings: Settings, *, check: bool = True) -> subprocess.CompletedProcess:
    """执行 git 命令并记录日志。"""
    cmd = [*_git_command(settings), *args]
    logger.debug("执行：%s", " ".join(cmd))
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )


def sync_gitlab_repo(settings: Settings | None = None, *, force: bool = False) -> Path:
    """克隆或更新官方数据仓库，返回数据文件路径。

    Args:
        settings: 配置对象。
        force: 为 True 时删除本地仓库重新克隆（用于修复被破坏的本地状态）。

    Returns:
        本地 **CSV 数据文件**的路径（不是仓库目录）。
    """
    settings = settings or get_settings()
    settings.ensure_dirs()
    repo = repo_dir(settings)
    dataset = dataset_path(settings)

    if not shutil.which("git"):
        raise RuntimeError(
            "未找到 git 命令。本项目的 Retraction Watch 数据通过 git 仓库分发，"
            "请先安装 git：https://git-scm.com/downloads"
        )

    if force and repo.exists():
        logger.info("force=True，删除本地仓库后重新克隆……")
        shutil.rmtree(repo)

    # 分支一：首次克隆
    if not (repo / ".git").exists():
        logger.info("首次克隆官方数据仓库（浅克隆，只取最新版本）……")
        logger.info("  来源：%s", GITLAB_REPO_URL)
        started = datetime.now()
        _run_git(
            ["clone", "--depth", "1", GITLAB_REPO_URL, str(repo)],
            settings,
        )
        elapsed = (datetime.now() - started).total_seconds()
        logger.info("克隆完成，耗时 %.1f 秒", elapsed)

    # 分支二：增量更新
    else:
        logger.info("本地仓库已存在，执行增量更新（git pull）……")
        result = _run_git(
            ["-C", str(repo), "pull", "--depth", "1", "--ff-only"],
            settings,
            check=False,
        )
        if result.returncode != 0:
            # 浅克隆仓库的 pull 有时会因为历史不匹配而失败。
            # 与其在这里反复纠缠，不如直接重来 —— 反正只要 15 秒。
            # "重试成本远低于修复成本时，果断重来"是个很实用的工程判断。
            logger.warning(
                "增量更新失败（%s），改为重新克隆。",
                (result.stderr or "").strip().splitlines()[-1] if result.stderr else "未知原因",
            )
            shutil.rmtree(repo, ignore_errors=True)
            _run_git(["clone", "--depth", "1", GITLAB_REPO_URL, str(repo)], settings)
        else:
            logger.info("增量更新完成。")

    if not dataset.exists():
        raise FileNotFoundError(
            f"仓库克隆成功但未找到数据文件：{dataset}\n"
            f"仓库内容可能已变更，请检查 {GITLAB_REPO_URL}"
        )

    size_mb = dataset.stat().st_size / 1024 / 1024
    logger.info("数据文件就绪：%s（%.1f MB）", dataset.name, size_mb)
    logger.info("数据版本日期：%s", repo_generated_date(settings) or "未知")
    return dataset


def repo_generated_date(settings: Settings | None = None) -> str | None:
    """从仓库 README 中解析数据生成日期。

    为什么值得单独做这件事？
        数据管道最隐蔽的风险之一是"数据静默过期"——
        你的代码天天在跑，看板上数字天天在变，
        但上游其实三个月没更新了，而你毫无察觉。
        显式记录并展示数据版本日期，是数据可观测性（data observability）的基本功。
    """
    readme = repo_dir(settings) / README_FILENAME
    if not readme.exists():
        return None
    match = re.search(r"generated on (\d{4}-\d{2}-\d{2})", readme.read_text(encoding="utf-8"))
    return match.group(1) if match else None


def fetch_retraction_watch_via_http(
    settings: Settings | None = None,
    *,
    force: bool = False,
) -> Path:
    """兜底方案：通过（已弃用的）Labs API 下载 CSV 快照。

    保留这个实现有几个理由：
      1. 万一 GitLab 不可达，管道还有第二条路可走
      2. 它完整演示了"大文件流式下载 + 超时调参 + 断点语义"的工程处理
      3. 它记录了一段真实的技术决策过程 —— 面试时这是很好的谈资

    已知限制（详见模块顶部的说明）：速率低、无 Content-Length、不支持 Range。
    """
    settings = settings or get_settings()
    settings.ensure_dirs()
    settings.warn_if_no_email()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    dest = settings.raw_dir / "retraction_watch" / f"retractionwatch_{stamp}.csv"

    if dest.exists() and not force:
        logger.info("快照已存在，跳过下载：%s", dest.name)
        return dest

    params = {"mailto": settings.contact_email} if settings.contact_email else None
    logger.warning(
        "正在使用已被官方弃用的 Labs API 端点下载，速度可能非常慢（实测约 50 KB/s）。"
    )
    with ApiClient(settings, qps=1.0, namespace="retraction_watch") as client:
        client.stream_to_file(LEGACY_LABS_API_URL, dest, params=params)

    return dest


def fetch_retraction_watch(
    settings: Settings | None = None,
    *,
    force: bool = False,
    strategy: str = "auto",
) -> Path:
    """采集 Retraction Watch 数据，返回本地 CSV 路径。

    Args:
        settings: 配置对象。
        force: 强制重新获取。
        strategy: ``"git"``（GitLab 仓库）、``"http"``（Labs API 兜底）
                  或 ``"auto"``（先 git，失败自动降级到 http）。
    """
    settings = settings or get_settings()

    if strategy not in {"git", "http", "auto"}:
        raise ValueError(f"strategy 必须是 git / http / auto 之一，收到 {strategy!r}")

    if strategy == "http":
        return fetch_retraction_watch_via_http(settings, force=force)

    try:
        return sync_gitlab_repo(settings, force=force)
    except (RuntimeError, subprocess.CalledProcessError, FileNotFoundError) as exc:
        if strategy == "git":
            raise
        logger.error("通过 GitLab 获取失败：%s", exc)
        logger.warning("自动降级到 HTTP 兜底方案（将非常慢）……")
        return fetch_retraction_watch_via_http(settings, force=force)


# ----------------------------------------------------------------------
# 读取与体检
# ----------------------------------------------------------------------
def read_retraction_watch(path: Path) -> pd.DataFrame:
    """读取 CSV 并做最低限度的列名规范化。

    这里只做"最低限度的规范化"，不做业务清洗 —— 业务清洗属于数仓层（warehouse）的职责。
    保持这条边界，出问题才能快速定位是"数据没取对"还是"清洗逻辑写错了"。

    三个容易踩的坑：
      1. 原始 CSV 每行末尾多一个逗号，会读出一个名为 ``Unnamed: 20`` 的空列 → 丢掉
      2. 全部按字符串读入（``dtype=str``）。``Record ID`` 是**标识符**而不是数字，
         一旦让 pandas 推断成 int，就会变成 12345 而不是 "12345"，
         后续与其他表 join 时类型不匹配 —— 这是极常见的隐蔽 bug。
      3. 文件里存在跨行的引号字段（Notes 列），必须让 CSV 解析器处理换行，
         不能用"按行切分"的土办法。
    """
    if not path.exists():
        raise FileNotFoundError(f"找不到数据文件：{path}。请先运行 fetch_retraction_watch()。")

    df = pd.read_csv(
        path,
        dtype=str,
        keep_default_na=False,
        na_values=[""],
        encoding="utf-8",
    )

    unnamed = [c for c in df.columns if str(c).startswith("Unnamed:") or str(c).strip() == ""]
    if unnamed:
        df = df.drop(columns=unnamed)
        logger.debug("已丢弃 %d 个无名空列", len(unnamed))

    df = df.rename(columns=_COLUMN_MAP)

    unmapped = [c for c in df.columns if c not in _COLUMN_MAP.values()]
    if unmapped:
        logger.warning("发现未映射的列（数据源可能已变更）：%s", unmapped)

    logger.info("读取成功：%d 行 × %d 列", len(df), df.shape[1])
    return df


def summarize(df: pd.DataFrame) -> dict[str, object]:
    """输出数据体检报告。

    这是数据分析的第一步，也是很多人跳过的一步：
    拿到数据先看**有多少行、缺多少、时间范围、类别分布**，
    而不是急着建模。跳过这一步，后面所有结论都建在流沙上。
    """
    summary: dict[str, object] = {
        "行数": len(df),
        "列数": df.shape[1],
        "缺失率最高的 5 列": (
            (df.isna().mean() * 100).sort_values(ascending=False).head(5).round(1).to_dict()
        ),
    }

    if "retraction_date" in df.columns:
        dates = pd.to_datetime(df["retraction_date"], errors="coerce", format="mixed")
        if dates.notna().any():
            summary["撤稿日期范围"] = f"{dates.min():%Y-%m-%d} ~ {dates.max():%Y-%m-%d}"
        summary["撤稿日期无法解析的行数"] = int(dates.isna().sum())

    if "retraction_nature" in df.columns:
        summary["撤稿性质分布"] = df["retraction_nature"].value_counts().head(10).to_dict()

    if "country" in df.columns:
        # 多值字段：先按 ';' 拆开再统计，否则会把 "China;United States" 当成一个国家
        exploded = (
            df["country"].fillna("").str.split(";").explode().str.strip().replace("", pd.NA).dropna()
        )
        summary["国家分布 Top10（已拆分多值）"] = exploded.value_counts().head(10).to_dict()

    if "reason" in df.columns:
        exploded = (
            df["reason"].fillna("").str.split(";").explode().str.strip().replace("", pd.NA).dropna()
        )
        summary["撤稿原因 Top15（已拆分多值）"] = exploded.value_counts().head(15).to_dict()
        summary["标注为 Paper Mill 的记录数"] = int((exploded == "Paper Mill").sum())

    return summary
