"""一键复现：按顺序跑完整条流水线。

用法::

    # 完整跑一遍（首次约 25 分钟，主要是 OpenAlex 全量抓取）
    .venv\\Scripts\\python.exe scripts\\00_run_all.py

    # 跳过耗时的全量抓取（数据已存在时用这个，约 4 分钟）
    .venv\\Scripts\\python.exe scripts\\00_run_all.py --skip-fetch

    # 只重跑分析部分（改了 SQL 或分析代码后）
    .venv\\Scripts\\python.exe scripts\\00_run_all.py --only 3 4 5 6

【为什么需要这个脚本】
    README 里写着"快速开始：依次运行 01 ~ 06"，但**文档里的步骤是会腐烂的**：
    有人改了脚本名、有人加了新的前置条件，而 README 不会自动更新。
    结果就是新克隆仓库的人按文档操作到第三步就卡住，
    然后放弃 —— 这是开源项目最常见的"劝退点"。

    把步骤固化成可执行代码，就只有一个地方需要维护，
    而且它**每次跑都会验证自己是否还有效**。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 步骤定义：(编号, 脚本名, 说明, 是否耗时)
STEPS: tuple[tuple[int, str, str, bool], ...] = (
    (1, "01_fetch_retraction_watch.py", "采集 Retraction Watch 撤稿案底", False),
    (2, "02_fetch_openalex.py", "采集 OpenAlex 被撤稿作品（约 21 分钟）", True),
    (3, "03_build_warehouse.py", "构建 DuckDB 数据仓库（三层 26 个模型）", False),
    (4, "04_check_quality.py", "运行 38 条数据质量校验", False),
    (5, "05_analyze_network.py", "合作网络分析与社群发现", False),
    (6, "06_generate_report.py", "生成图表与图文报告", False),
    (7, "07_generate_data_dictionary.py", "从数据库元数据生成数据字典", False),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="按顺序运行完整数据流水线")
    parser.add_argument(
        "--skip-fetch",
        action="store_true",
        help="跳过耗时的数据抓取步骤（适用于数据已经在本地的场景）",
    )
    parser.add_argument(
        "--only",
        nargs="+",
        type=int,
        metavar="N",
        help="只运行指定编号的步骤，例如 --only 3 4 5",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="某一步失败后继续执行后续步骤（默认遇到失败立刻停止）",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    selected = [
        step
        for step in STEPS
        if (not args.only or step[0] in args.only) and not (args.skip_fetch and step[3])
    ]

    if not selected:
        print("没有需要执行的步骤。")
        return 0

    print("=" * 74)
    print(f"PaperMill Hunter · 一键复现（共 {len(selected)} 个步骤）")
    print("=" * 74)
    for number, script, description, heavy in selected:
        marker = "  [耗时] " if heavy else "        "
        print(f"{marker}{number}. {script:34} {description}")
    print("=" * 74)

    results: list[tuple[int, str, float, int]] = []
    started_all = time.monotonic()

    for number, script, description, _heavy in selected:
        path = PROJECT_ROOT / "scripts" / script
        if not path.exists():
            print(f"\n[步骤 {number}] 跳过：找不到 {path}")
            results.append((number, script, 0.0, -1))
            if not args.continue_on_error:
                break
            continue

        print(f"\n{'─' * 74}")
        print(f"[步骤 {number}/{len(selected)}] {description}")
        print(f"{'─' * 74}")

        started = time.monotonic()
        # 用 sys.executable 而不是写死 "python" ——
        # 这样能保证子进程用的是**同一个虚拟环境**里的解释器。
        # 写死 "python" 在 CI、conda、多版本环境下会莫名其妙地用到系统 Python，
        # 然后报"找不到模块"，而错误信息完全指不到真正的原因。
        completed = subprocess.run(
            [sys.executable, str(path)],
            cwd=str(PROJECT_ROOT),
            check=False,
        )
        elapsed = time.monotonic() - started
        results.append((number, script, elapsed, completed.returncode))

        status = "✓ 完成" if completed.returncode == 0 else f"✗ 失败（退出码 {completed.returncode}）"
        print(f"[步骤 {number}] {status}　用时 {elapsed:.1f} 秒")

        if completed.returncode != 0 and not args.continue_on_error:
            print("\n遇到失败，已停止。加 --continue-on-error 可继续执行后续步骤。")
            break

    total = time.monotonic() - started_all
    print(f"\n{'=' * 74}")
    print("执行汇总")
    print("=" * 74)
    for number, script, elapsed, code in results:
        status = "跳过" if code == -1 else ("成功" if code == 0 else f"失败({code})")
        print(f"  {number}. {script:34} {status:>8}　{elapsed:6.1f} 秒")
    print("-" * 74)
    print(f"  总用时：{total / 60:.1f} 分钟")

    failed = [r for r in results if r[3] not in (0, -1)]
    if failed:
        print(f"\n有 {len(failed)} 个步骤失败，流水线未完整跑通。")
        return 1

    print("\n流水线执行完毕。查看结果：")
    print("  reports/REPORT.md        图文分析报告")
    print("  reports/figures/         全部图表")
    print("  data/warehouse.duckdb    数据仓库（可用 DuckDB CLI 或 Python 查询）")
    print("\n启动交互看板：")
    print("  .venv\\Scripts\\streamlit.exe run src\\papermill_hunter\\viz\\dashboard.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
