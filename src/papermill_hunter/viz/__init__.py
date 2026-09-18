"""可视化层：把分析结论变成人能看懂的东西。

包含两个互补的交付物：
    charts.py     静态图表（matplotlib → PNG），用于 README、简历、报告
    dashboard.py  交互看板（Streamlit），用于面试现场演示与自主探索

为什么两者都要？
    静态图是"作品集"，交互看板是"演示"。面试官点开 GitHub 仓库时看到的是静态图，
    而面试过程中你能打开看板让他自己筛选 —— 这两件事无法互相替代。
"""

from papermill_hunter.viz import charts

__all__ = ["charts"]
