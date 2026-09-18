"""数仓层单元测试。"""

from __future__ import annotations

import pytest

from papermill_hunter.config import Settings
from papermill_hunter.warehouse.db import (
    build_variables,
    connect,
    render_sql,
    sql_files,
)

# ----------------------------------------------------------------------
# SQL 模板渲染
# ----------------------------------------------------------------------


def test_render_sql_replaces_simple_placeholder() -> None:
    sql = "SELECT * FROM read_csv('{{RETRACTION_WATCH_CSV}}')"
    rendered = render_sql(sql, {"RETRACTION_WATCH_CSV": "/data/rw.csv"})
    assert rendered == "SELECT * FROM read_csv('/data/rw.csv')"


def test_render_sql_handles_placeholder_with_spaces() -> None:
    assert render_sql("FROM {{ A }}", {"A": "x"}) == "FROM x"


def test_render_sql_raises_when_variable_missing() -> None:
    """缺少变量必须立刻报错，而不是把 {{...}} 原样喂给 DuckDB。

    如果把占位符原样送进数据库，你会收到一个语焉不详的语法错误，
    然后花十几分钟去猜是哪里写错了。在这里直接报错，能省下大量排查时间。
    """
    with pytest.raises(ValueError, match="未提供值"):
        render_sql("SELECT * FROM '{{MISSING}}'", {"OTHER": "x"})


def test_render_sql_lists_provided_variables_on_error() -> None:
    """报错信息里要带上已提供的变量，方便一眼看出拼写差异。"""
    with pytest.raises(ValueError) as exc_info:
        render_sql("FROM {{RAW_DIRR}}", {"RAW_DIR": "/data"})
    message = str(exc_info.value)
    assert "RAW_DIRR" in message
    assert "RAW_DIR" in message


def test_render_sql_without_placeholder_is_unchanged() -> None:
    sql = "SELECT 1 AS one"
    assert render_sql(sql, {}) == sql


# ----------------------------------------------------------------------
# 快照发现
# ----------------------------------------------------------------------


def test_latest_snapshot_is_used(tmp_path) -> None:
    """有多份快照时必须只取最新那份，否则同一篇论文会被重复计数。"""
    settings = Settings(data_dir=tmp_path / "data", contact_email="t@e.com")
    rw_dir = settings.raw_dir / "retraction_watch"
    rw_dir.mkdir(parents=True)

    for stamp in ("20250101", "20250601", "20251231"):
        (rw_dir / f"retractionwatch_{stamp}.csv").write_text("Record ID\n1\n", encoding="utf-8")

    variables = build_variables(settings)
    assert variables["RETRACTION_WATCH_CSV"].endswith("retractionwatch_20251231.csv")


def test_missing_snapshot_gives_actionable_error(tmp_path) -> None:
    """找不到数据时，报错信息要告诉用户"该跑哪个脚本"。"""
    settings = Settings(data_dir=tmp_path / "data", contact_email="t@e.com")
    (settings.raw_dir / "retraction_watch").mkdir(parents=True)

    with pytest.raises(FileNotFoundError, match="01_fetch_retraction_watch"):
        build_variables(settings)


# ----------------------------------------------------------------------
# 连接与目录
# ----------------------------------------------------------------------


def test_connect_creates_warehouse_file(tmp_path) -> None:
    """默认行为：仓库不存在时创建它（构建场景需要）。"""
    settings = Settings(data_dir=tmp_path / "data", contact_email="t@e.com")
    con = connect(settings)
    try:
        assert settings.warehouse_path.exists()
        assert con.execute("SELECT 1").fetchone()[0] == 1
    finally:
        con.close()


def test_connect_require_exists_raises_actionable_error(tmp_path) -> None:
    """读取路径必须在仓库缺失时给出**可操作**的错误，而不是创建空库。

    这条测试来自一次真实事故：DuckDB 的 ``connect(path)`` 在文件不存在时会
    自动创建一个空数据库，于是"数据还没准备好"被悄悄变成了"数据库是空的"。
    新用户克隆仓库后打开看板，看到的是
    ``Catalog Error: Table "staging.retraction_watch" does not exist`` ——
    既不解释原因，也不告诉你该做什么。

    断言的三件事：
      1. 抛出 FileNotFoundError（而不是让 DuckDB 抛 CatalogException）
      2. 错误信息里包含**下一步该运行哪个脚本**
      3. **磁盘上不留空文件** —— 否则下次构建时会让人困惑
    """
    settings = Settings(data_dir=tmp_path / "data", contact_email="t@e.com")

    with pytest.raises(FileNotFoundError) as exc_info:
        connect(settings, require_exists=True)

    message = str(exc_info.value)
    assert "数据仓库不存在" in message
    # 错误信息必须告诉用户下一步做什么，否则等于没说
    assert "03_build_warehouse" in message, f"错误信息缺少可操作指引：{message}"
    # 最关键的一条：不能在磁盘上留下空文件
    assert not settings.warehouse_path.exists(), (
        "读取路径不应在磁盘上创建空的数据仓库文件 —— 这会污染后续的构建"
    )


def test_connect_require_exists_succeeds_when_present(tmp_path) -> None:
    """仓库存在时，require_exists=True 应正常连接。"""
    settings = Settings(data_dir=tmp_path / "data", contact_email="t@e.com")

    # 先用默认行为创建
    connect(settings).close()

    con = connect(settings, require_exists=True)
    try:
        assert con.execute("SELECT 1").fetchone()[0] == 1
    finally:
        con.close()


def test_sql_files_are_returned_in_filename_order() -> None:
    """同一层内的执行顺序必须由文件名决定，不能依赖文件系统返回顺序。"""
    files = sql_files("staging")
    names = [f.name for f in files]
    assert names == sorted(names), f"staging 层 SQL 未按文件名排序：{names}"


def test_sql_files_for_unknown_layer_is_empty() -> None:
    assert sql_files("no_such_layer") == []
