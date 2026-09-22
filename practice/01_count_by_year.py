"""练习 1 —— 按年份统计撤稿量

【这个文件是参考答案，不是让你抄的】
    你第一版的结构已经完全正确（函数、字典、header.index、result.get、__name__ 全对），
    错的是 4 个细节。**请先把下面的代码手敲一遍再提交**，
    敲的过程中注意那 4 处和你原版的差异。

【你原版的 4 个问题（按严重程度）】

    ① 🔴 静默错误：年份里带上了时间
        你写：date.split("-")[0].split("/")[-1]
              "10/4/2023 0:00" → "2023 0:00"     ← 错了，但代码不报错！
        正确：用 datetime.strptime 解析

        ★ 这是本次作业最有价值的一条：代码跑通了、有输出、看起来正常，
          但结果全是错的。这就是"静默错误"。

    ② 🟠 path 未定义
        get_year_count(path) 调用时 path 从哪来？→ NameError

    ③ 🟡 列名用了中文
        "撤稿日期" → 实际列名是 "RetractionDate"

    ④ ⚪ 缺 newline=""
        官方推荐加上（保护含跨行字段的文件）
        但实测这个文件里没有跨行字段，加不加结果一样

【核心知识点：为什么用 strptime 而不是手工切字符串】

    |              | 手工 split        | datetime.strptime     |
    |--------------|-------------------|-----------------------|
    | 格式不对时   | 静默给出错的结果  | 立刻抛 ValueError     |
    | 拿到年份     | 字符串 "2023"     | int 2023              |
    | 要月份/日    | 还得再切          | dt.month / dt.day     |
    | 你的那个 bug | ← 就是这么来的    | 不会发生              |

    **手工切不验证，strptime 会验证。**
    遇到脏数据时，前者给你错答案，后者告诉你"这条数据有问题"。
"""

from __future__ import annotations

import csv
from datetime import datetime

# 原始 CSV 的日期格式是美式：'10/4/2023 0:00'（月/日/年 时:分）
DATE_FORMAT = "%m/%d/%Y %H:%M"


def get_year_count(path: str) -> dict[int, int]:
    """统计每一年的撤稿数量。

    Args:
        path: 撤稿 CSV 的文件路径。

    Returns:
        {年份: 该年撤稿数}，年份是 int。

    Raises:
        FileNotFoundError: 文件不存在。
        ValueError: CSV 里找不到 'RetractionDate' 列。
    """
    result: dict[int, int] = {}

    with open(path, encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader)

        # 用 header.index() 查列号，不要硬编码数字 ——
        # 万一上游调整了列顺序，硬编码就会静默地取错列
        date_index = header.index("RetractionDate")

        for row in reader:
            date_text = row[date_index]
            if not date_text:
                continue

            try:
                dt = datetime.strptime(date_text, DATE_FORMAT)
            except ValueError:
                # 解析失败就跳过，而不是让整个脚本崩掉。
                # 数据里确实有例外格式，例如 '6/24/1756 12:00:00 AM'
                # （项目里的 SQL 宏 rw_parse_date 就是为此准备了三套格式）
                continue

            result[dt.year] = result.get(dt.year, 0) + 1

    return result


def main() -> int:
    path = "data/raw/retraction_watch/gitlab/retraction_watch.csv"

    counts = get_year_count(path)

    print(f"共解析出 {len(counts)} 个年份")

    valid_years = sorted(y for y in counts if y >= 2000)
    print(f"其中 2000 年之后的有 {len(valid_years)} 个\n")

    for year in valid_years:
        print(f"  {year}: {counts[year]:>7,}")

    # 自检：项目里已核实 2023 年是 13,564 条，用它对答案
    expected_2023 = 13564
    actual_2023 = counts.get(2023, 0)
    status = "✓ 对上了" if actual_2023 == expected_2023 else f"✗ 对不上（期望 {expected_2023}）"
    print(f"\n自检 2023 年：{actual_2023:,}  {status}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
