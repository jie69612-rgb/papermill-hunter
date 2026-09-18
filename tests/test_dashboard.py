"""看板渲染测试。

【为什么用 AppTest 而不是"启动服务再 curl 一下"】
    启动服务然后请求首页，只能证明 **HTTP 外壳**加载成功了。
    但页面里的 Python 代码是通过 WebSocket 增量渲染的 ——
    如果某个页面的查询写错了，`curl` 依然返回 200，
    而错误只在浏览器里显示成一个红框。

    也就是说，"服务起得来"和"页面渲染得出来"是两件事，
    用前者验证后者是一种**虚假的安心感**。

    Streamlit 官方提供了 ``AppTest``：它能在进程内把 App 完整跑一遍，
    包括执行页面代码、捕获异常、读取渲染出的组件。
    这才是真正验证"看板能用"的方式。

【这个测试覆盖了什么】
    逐个切换全部 8 个页面，断言每个页面渲染时**没有抛出异常**。
    这样新增页面时如果写错了查询，测试会立刻失败，
    而不是等到演示时当着面试官的面崩掉。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

DASHBOARD = Path(__file__).resolve().parents[1] / "src" / "papermill_hunter" / "viz" / "dashboard.py"

PAGES = (
    "总览",
    "撤稿趋势",
    "国家分布",
    "撤稿原因",
    "期刊风险",
    "引用代价",
    "合作网络",
    "数据质量",
)


@pytest.fixture(scope="module")
def app() -> AppTest:
    """启动看板（不切页面），后续测试复用同一个 AppTest 实例。"""
    if not DASHBOARD.exists():
        pytest.skip(f"未找到看板文件：{DASHBOARD}")

    at = AppTest.from_file(str(DASHBOARD), default_timeout=120)
    at.run()
    return at


def _skip_if_no_warehouse(at: AppTest) -> None:
    """数据仓库不存在时跳过 —— 数据没准备好不是代码的错。

    【这里修过一个会让 CI 徽章永远变红的问题】
        最初的实现只检查 ``at.exception``。但看板**会优雅降级**：
        它捕获 FileNotFoundError 并渲染成一个 ``st.error`` 提示，
        所以 ``at.exception`` 是**空的** —— 跳过逻辑完全不生效，
        紧接着的 ``assert len(app.metric) >= 8`` 就直接失败。

        在 GitHub CI 的全新检出环境里（没有任何数据），这个测试会稳定失败 ——
        也就是说**仓库的 CI 徽章从第一天起就是红的**。
        技术面试官点开仓库第一眼看的就是徽章，一个常年红色的徽章
        比没有 CI 更糟糕：它传递的信号是"这个项目是坏的"。

        修正方式：除了 exception，还要检查渲染出来的 error / info 组件。
        —— 教训：**"优雅降级"和"测试跳过"必须成对设计。**
           前端把错误处理得越友好，测试就越难靠"有没有抛异常"发现问题。
    """
    markers = ("数据仓库尚未构建", "请依次运行", "No such file", "does not exist")

    def _matches(text: str) -> bool:
        return any(marker in text for marker in markers)

    if at.exception:
        messages = " ".join(str(e.value) for e in at.exception)
        if _matches(messages):
            pytest.skip(f"数据仓库未构建，跳过看板渲染测试：{messages[:120]}")

    # 看板把异常转成了 UI 组件，所以要从渲染结果里找线索
    for element in list(at.error) + list(at.info) + list(at.warning):
        text = str(getattr(element, "value", ""))
        if _matches(text):
            pytest.skip(f"数据仓库未构建，看板已降级为提示页：{text[:120]}")


def test_dashboard_loads_without_exception(app: AppTest) -> None:
    """看板首次加载不应抛异常。"""
    _skip_if_no_warehouse(app)
    assert not app.exception, f"看板加载失败：{[str(e.value) for e in app.exception]}"


def test_sidebar_has_all_pages(app: AppTest) -> None:
    """侧边栏必须提供全部页面入口。"""
    _skip_if_no_warehouse(app)
    assert len(app.sidebar.radio) >= 1, "侧边栏缺少页面选择控件"
    options = app.sidebar.radio[0].options
    assert set(PAGES).issubset(set(options)), f"侧边栏缺少页面：{set(PAGES) - set(options)}"


@pytest.mark.parametrize("page", PAGES)
def test_every_page_renders(app: AppTest, page: str) -> None:
    """逐个切换页面，断言渲染无异常。

    参数化让失败信息直接告诉你是哪个页面坏了，
    而不是笼统地报"看板挂了"。
    """
    _skip_if_no_warehouse(app)
    app.sidebar.radio[0].set_value(page).run()
    _skip_if_no_warehouse(app)

    assert not app.exception, f"页面「{page}」渲染失败：{[str(e.value) for e in app.exception]}"
    # 每个页面都应当至少渲染出一个标题
    assert len(app.title) >= 1 or len(app.subheader) >= 1, f"页面「{page}」没有渲染任何标题"


def test_overview_shows_key_metrics(app: AppTest) -> None:
    """总览页必须展示核心指标卡，而不是空页面。"""
    _skip_if_no_warehouse(app)
    app.sidebar.radio[0].set_value("总览").run()
    _skip_if_no_warehouse(app)

    assert not app.exception
    assert len(app.metric) >= 8, f"总览页指标卡数量异常：{len(app.metric)}"
