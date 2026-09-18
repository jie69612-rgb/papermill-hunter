"""采集层：负责从外部数据源获取原始数据，并落地到 data/raw/。

分层原则：
    ingest  只做"取回来 + 存下来"，不做业务清洗。
    清洗与建模交给 warehouse 层。
    这样职责单一，出问题时能立刻定位是"数据没取到"还是"清洗逻辑写错了"。
"""

from papermill_hunter.ingest.http import ApiClient, TokenBucket

__all__ = ["ApiClient", "TokenBucket"]
