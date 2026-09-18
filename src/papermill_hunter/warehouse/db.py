"""DuckDB 数据仓库的连接管理与构建流程。

【为什么选 DuckDB，而不是 SQLite / PostgreSQL / Spark？】
    这是数据分析项目，不是高并发的在线业务。选型要服务于场景：

    | 选项        | 擅长           | 为什么本项目不用                                |
    |-------------|----------------|------------------------------------------------|
    | SQLite      | 在线事务处理    | 行式存储，做聚合分析慢一个数量级                 |
    | PostgreSQL  | 在线业务系统    | 需要部署服务；一个分析项目不该背运维成本          |
    | Spark       | TB 级以上数据   | 我们数据不到 1 GB，用它属于高射炮打蚊子           |
    | **DuckDB**  | **分析（OLAP）**| ✅ 列式存储 + 向量化执行，单机跑出集群级速度      |

    DuckDB 的具体优势：
      - **嵌入式**：一个文件、零部署，``pip install duckdb`` 就能用
      - **列式 + 向量化**：同样一条聚合查询，比 SQLite 快几十倍
      - **能直接查 Parquet / CSV / JSON**：不需要"先导入再分析"这一道手续
      - **SQL 完整**：窗口函数、CTE、结构体、列表类型，够用到奢侈

    对面试来说，**"为什么这么选"比"用了什么"更能体现判断力**。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path

import duckdb

from papermill_hunter.config import Settings, get_settings
from papermill_hunter.logging_conf import get_logger

logger = get_logger(__name__)

SQL_DIR: Path = Path(__file__).resolve().parent / "sql"

# 分层顺序：staging 依赖原始数据，intermediate 依赖 staging，marts 依赖 intermediate。
# 按这个顺序执行，保证依赖永远先于使用者就绪。
LAYER_ORDER: tuple[str, ...] = ("staging", "intermediate", "marts")

# 匹配 SQL 里的占位符，例如 {{RETRACTION_WATCH_CSV}}
_PLACEHOLDER_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


# ----------------------------------------------------------------------
# 连接
# ----------------------------------------------------------------------
def connect(
    settings: Settings | None = None,
    *,
    require_exists: bool = False,
) -> duckdb.DuckDBPyConnection:
    """打开本地 DuckDB 数据仓库文件。

    Args:
        settings: 配置对象。
        require_exists: 为 True 时，如果仓库文件不存在就**立刻抛出可操作的错误**，
            而不是安静地创建一个空库。

    【为什么要加 require_exists 这个参数 —— 一次真实事故的产物】
        DuckDB 的 ``connect(path)`` 在文件不存在时会**自动创建一个空数据库**。
        对一个"构建数据仓库"的场景这是想要的行为；
        但对一个"读取数据仓库"的场景（看板、报告、质量校验），
        它会把"数据还没准备好"悄悄变成"数据库是空的"。

        后果是：新用户克隆仓库后直接打开看板，
        看到的是 ``Catalog Error: Table with name "staging.retraction_watch" does not exist``
        —— 一个既不解释原因、也不告诉你该做什么的报错。
        更糟的是它**在磁盘上留下了一个空的 warehouse.duckdb**，
        下次即使跑完了构建脚本，也可能因为这个残留文件而产生困惑。

        所以：读路径显式声明 ``require_exists=True``，把模糊的运行时错误
        提前变成一句"请先运行 scripts/03_build_warehouse.py"。
        —— **错误信息的好坏，决定了用户能不能自己解决问题。**
    """
    settings = settings or get_settings()
    settings.ensure_dirs()

    path = settings.warehouse_path
    if require_exists and not path.exists():
        raise FileNotFoundError(f"数据仓库不存在：{path}\n请先运行：python scripts/03_build_warehouse.py")

    con = duckdb.connect(str(path))
    # 统一时区，避免"同一份数据在不同机器上算出不同的日期"这种经典事故
    con.execute("SET TimeZone='UTC'")
    # 让 DuckDB 能多线程跑满 CPU —— 分析型查询天然适合并行
    con.execute("SET threads TO 4")
    logger.debug("已连接数据仓库：%s", path)
    return con


# ----------------------------------------------------------------------
# SQL 模板渲染
# ----------------------------------------------------------------------
def render_sql(sql: str, variables: Mapping[str, str]) -> str:
    """把 SQL 里的 ``{{变量}}`` 替换成实际值。

    为什么要自己写这一层，而不是把路径硬编码进 SQL？
        因为 SQL 文件应该只描述**逻辑**，而不是"我这份数据在 D:\\projects\\..."
        这样的环境细节。渲染层把逻辑和环境解耦：
        换台机器、换个数据目录，SQL 一行都不用改。

    Windows 路径里的反斜杠在 SQL 字符串中是转义符，会引发难以定位的语法错误，
    所以这里统一转成正斜杠（DuckDB 在 Windows 上同样接受正斜杠）。
    """
    rendered = sql
    for key, value in variables.items():
        rendered = rendered.replace("{{" + key + "}}", value)
        rendered = rendered.replace("{{ " + key + " }}", value)

    # 未替换的占位符必须立刻报错。
    # 如果放任它进入 DuckDB，你会收到一个语法错误，然后花十分钟去找
    # 到底是哪个变量名拼错了 —— 不如在这里一次性说清楚。
    leftover = _PLACEHOLDER_RE.findall(rendered)
    if leftover:
        raise ValueError(
            f"SQL 中存在未提供值的占位符：{sorted(set(leftover))}；已提供的变量为：{sorted(variables)}"
        )
    return rendered


def build_variables(settings: Settings) -> dict[str, str]:
    """构造 SQL 模板所需的变量表。"""
    return {
        "RAW_DIR": settings.raw_dir.as_posix(),
        "RETRACTION_WATCH_CSV": _latest_retraction_watch_csv(settings),
        "OPENALEX_DIR": (settings.raw_dir / "openalex" / "retracted_works").as_posix(),
    }


def _latest_retraction_watch_csv(settings: Settings) -> str:
    """定位 Retraction Watch 的 CSV 文件。

    查找顺序（对应采集层的两种策略）:
      1. ``raw/retraction_watch/gitlab/retraction_watch.csv``
         —— 官方 GitLab 仓库的产物，**推荐路径**
      2. ``raw/retraction_watch/retractionwatch_YYYYMMDD.csv``
         —— 已弃用的 Labs API 下载的快照，作为兜底

    为什么第 2 种情况要取"最新一份"而不是用通配符把所有快照都读进来？
        因为快照是按日期累积的，用通配符会把同一篇论文的不同期快照
        当成两条记录，导致数据被静默地重复计数。
        这类"多做了一步反而引入错误"的坑，在数据管道里非常常见。
    """
    gitlab_csv = settings.raw_dir / "retraction_watch" / "gitlab" / "retraction_watch.csv"
    if gitlab_csv.exists():
        logger.info("使用 GitLab 官方数据：%s", gitlab_csv.name)
        return gitlab_csv.as_posix()

    snapshots = sorted((settings.raw_dir / "retraction_watch").glob("retractionwatch_*.csv"))
    if snapshots:
        latest = snapshots[-1]
        logger.info("使用 Labs API 快照（兜底路径）：%s", latest.name)
        return latest.as_posix()

    raise FileNotFoundError(
        "未找到 Retraction Watch 数据。请先运行：python scripts/01_fetch_retraction_watch.py"
    )


# ----------------------------------------------------------------------
# 构建
# ----------------------------------------------------------------------
def sql_files(layer: str) -> list[Path]:
    """按文件名排序返回某一层的所有 SQL 文件。

    文件名前缀数字（如 ``01_``）用于显式控制执行顺序 ——
    同一层内也可能有依赖关系，不能依赖文件系统的返回顺序。
    """
    layer_dir = SQL_DIR / layer
    if not layer_dir.exists():
        return []
    return sorted(layer_dir.glob("*.sql"))


def build(
    settings: Settings | None = None,
    *,
    layers: Sequence[str] = LAYER_ORDER,
) -> list[str]:
    """按层构建数据仓库，返回已执行的 SQL 文件列表。"""
    settings = settings or get_settings()
    variables = build_variables(settings)
    executed: list[str] = []

    con = connect(settings)
    try:
        for layer in layers:
            files = sql_files(layer)
            if not files:
                logger.warning("层「%s」下没有 SQL 文件，跳过。", layer)
                continue

            logger.info("=" * 66)
            logger.info("构建层：%s（%d 个模型）", layer, len(files))
            logger.info("=" * 66)

            for path in files:
                sql = render_sql(path.read_text(encoding="utf-8"), variables)
                con.execute(sql)
                executed.append(f"{layer}/{path.name}")
                logger.info("  ✓ %s", path.name)

        _log_summary(con)
    finally:
        con.close()

    return executed


def _log_summary(con: duckdb.DuckDBPyConnection) -> None:
    """打印仓库里各张表的行数，作为构建结果的自检。"""
    rows = con.execute(
        """
        SELECT table_schema, table_name
        FROM information_schema.tables
        WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
        ORDER BY table_schema, table_name
        """
    ).fetchall()

    if not rows:
        return

    logger.info("-" * 66)
    logger.info("数据仓库现状：")
    for schema, table in rows:
        try:
            count = con.execute(f'SELECT COUNT(*) FROM "{schema}"."{table}"').fetchone()[0]
            logger.info("  %-12s %-32s %12s 行", schema, table, f"{count:,}")
        except duckdb.Error as exc:  # pragma: no cover
            logger.warning("  %s.%s 行数统计失败：%s", schema, table, exc)
    logger.info("-" * 66)
