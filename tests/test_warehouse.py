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
    settings = Settings(data_dir=tmp_path / "data", contact_email="t@e.com")
    con = connect(settings)
    try:
        assert settings.warehouse_path.exists()
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
