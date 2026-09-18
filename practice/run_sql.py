"""练习用的 SQL 运行器 —— 让你能在命令行直接跑一句 SQL 看结果。

用法（在项目根目录）::

    .venv\\Scripts\\python.exe practice/run_sql.py "SELECT 1"

    # 想写多行 SQL 时，用 --file 指向一个 .sql 文件
    .venv\\Scripts\\python.exe practice/run_sql.py --file practice/q1.sql

【为什么需要这个工具】
    项目自带的 scripts/03_build_warehouse.py 也支持 --query，
    但它会**先把整个数仓重建一遍**（约 3.5 分钟）再执行查询 ——
    那是因为它的定位是"构建"，不是"查询"。

    练 SQL 的时候你一天要跑几十次查询，每次等 3 分钟是不可接受的。
    所以单独写一个只读的轻量运行器：它直接连上已有的数仓文件，
    不重建任何东西，一条查询通常在 0.1 秒内返回。

    —— 这本身也是个工程习惯：**工具要匹配使用场景**。
       同一个功能在"构建"和"探索"两种场景下，最优实现完全不同。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# 让脚本能 import 到 src/ 下的包（不依赖 pip install -e .）
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from papermill_hunter.config import get_settings  # noqa: E402
from papermill_hunter.warehouse.db import connect  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行一句 SQL 并打印结果")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("sql", nargs="?", help="要执行的 SQL 语句")
    group.add_argument("--file", type=Path, help="从文件读取 SQL")
    parser.add_argument("--max-rows", type=int, default=50, help="最多显示多少行（默认 50）")
    return parser.parse_args()


def format_table(columns: list[str], rows: list[tuple], max_rows: int) -> str:
    """把查询结果排成对齐的文本表格。

    为什么要自己排版而不用 pandas？
        因为我们想让**列宽自适应内容**，而且要把 NULL 显示成明确的 '(NULL)'
        而不是空白 —— 空白会让人分不清"值是空字符串"和"值是 NULL"，
        这在练 SQL 时是个很容易混淆的点。
    """
    if not rows:
        return "(没有返回任何行)"

    shown = rows[:max_rows]
    # NULL 显式显示成 (NULL)，而不是留空。
    # 因为"值是 NULL"和"值是空字符串"在 SQL 里是**完全不同**的两件事：
    #   NULL = 未知，任何和它比较的结果还是 NULL（连 NULL = NULL 都是 NULL）
    #   ''   = 已知的空字符串
    # 如果两者都显示成空白，练 SQL 时就分不清自己拿到的是哪种，
    # 而这恰恰是初学者最容易踩的坑之一。
    cells = [["(NULL)" if v is None else str(v) for v in row] for row in shown]

    widths = [len(c) for c in columns]
    for row in cells:
        for i, value in enumerate(row):
            widths[i] = max(widths[i], len(value))

    lines = [
        " | ".join(c.ljust(widths[i]) for i, c in enumerate(columns)),
        "-+-".join("-" * w for w in widths),
    ]
    for row in cells:
        lines.append(" | ".join(v.ljust(widths[i]) for i, v in enumerate(row)))

    if len(rows) > max_rows:
        lines.append(f"... 还有 {len(rows) - max_rows} 行未显示（用 --max-rows 调整）")

    return "\n".join(lines)


def main() -> int:
    args = parse_args()

    sql = args.sql if args.sql else args.file.read_text(encoding="utf-8").strip()

    settings = get_settings()
    # require_exists=True：数仓不存在时给出明确提示，而不是静默建一个空库
    con = connect(settings, require_exists=True)
    try:
        started = time.monotonic()
        try:
            cursor = con.execute(sql)
        except Exception as exc:  # noqa: BLE001
            # SQL 写错是练习的常态，所以要把错误清楚地打出来，
            # 而不是丢一个 Python traceback 让人分不清是自己写错了还是程序坏了
            print(f"\n❌ SQL 执行失败\n   类型: {type(exc).__name__}\n   信息: {exc}\n", file=sys.stderr)
            return 1

        elapsed = time.monotonic() - started

        if cursor.description is None:
            print("✓ 执行成功（该语句没有返回结果集）")
            return 0

        columns = [c[0] for c in cursor.description]
        rows = cursor.fetchall()

        print()
        print(format_table(columns, rows, args.max_rows))
        print(f"\n({len(rows)} 行，耗时 {elapsed * 1000:.0f} 毫秒)")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
