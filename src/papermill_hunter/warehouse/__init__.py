"""数仓层：把原始数据清洗、建模成分析就绪的表。

职责边界（很重要）：
    ingest 层只负责"把数据原封不动取回来"；
    warehouse 层负责"把它变成能直接做分析的样子"。

    这条线划清楚，出问题时能立刻定位：
      - 数据行数不对 → 去 ingest 查
      - 字段算错了   → 去 warehouse 查
    很多数据项目最后变成一团乱麻，就是因为"取数"和"洗数"混在一个脚本里。
"""

from papermill_hunter.warehouse.db import build, connect

__all__ = ["build", "connect"]
