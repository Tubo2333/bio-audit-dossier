"""Bio-Audit 演示台入口。

设计依据：深色审计台设计规范（v0.3 定稿）§3.1/§4/§5。

本文件只做四件事：
1. 主题注入（theme.css，深色审计台，data-testid 选择器）；
2. 启动数据校验（demo/data/ 清单 + manifest 指纹，缺失给中文提示不崩溃）；
3. 侧边栏页级导航（四页，不用 st.tabs 承载整页）；
4. 条件渲染（按当前页调用 pages/*.py 的 render()）。

外围层纪律：demo 只调 bioaudit.api + capture 公共类，不碰评分路径
（引擎/规则/本体/黄金资产零改动，golden 0 差异硬验收）。
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import streamlit as st

from bioaudit.report import current_snapshot

DEMO_ROOT = Path(__file__).resolve().parent
# 任意 cwd 启动均可用（streamlit run demo/app.py 或绝对路径）：demo/ 自身入 path
if str(DEMO_ROOT) not in sys.path:
    sys.path.insert(0, str(DEMO_ROOT))

import data_index  # noqa: E402
import narrative  # noqa: E402  # 叙事层（页间过渡；导语由各页自行渲染）

# ── 页面注册表（侧边栏导航顺序即列表顺序）──────────────────────────────
# 导航标签 = 序号 + 页面名 + 本页主题域（副标题给出"这一页在处理什么"，
# 使观众在读页面之前就有预期；页面内导语进一步展开该问题）。
PAGES: list[tuple[str, str, str]] = [
    ("workshop", "① 审计工坊 · 方法学判定", "pages.01_workshop"),
    ("capture", "② 采集演示 · 决策点还原", "pages.02_capture"),
    ("benchmark", "③ 评测与奖励 · 系统可信度", "pages.03_benchmark"),
    ("about", "④ 关于 · 能力与接入", "pages.04_about"),
]
#: 页面内标题（不含序号与副标题；各页 h1 与横幅用）
PAGE_NAMES = {
    "workshop": "审计工坊",
    "capture": "采集演示",
    "benchmark": "评测与奖励",
    "about": "关于",
}
PAGE_TITLES = {pid: label for pid, label, _mod in PAGES}
DEFAULT_PAGE = PAGES[0][0]


def _inject_theme() -> None:
    """注入深色审计台主题（theme.css）。1.x 版本锁定的动机之一：data-testid 稳定。"""
    css = (DEMO_ROOT / "theme.css").read_text(encoding="utf-8")
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def _render_banner(page_title: str) -> None:
    """顶部横幅：品牌 mark + 三元组版本徽章 + 当前页名（可信 UI 起点）。"""
    snap = current_snapshot()
    st.markdown(
        f"""
        <div class="ba-banner">
          <span class="ba-brand">BIO-AUDIT</span>
          <span class="ba-badge">engine {snap.engine_version}</span>
          <span class="ba-badge">ruleset {snap.ruleset_version}</span>
          <span class="ba-badge">ontology {snap.ontology_version}</span>
          <span class="ba-banner-page">{page_title}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_sidebar() -> None:
    """侧边栏页级导航：radio 页级切换（选中态由 theme.css 定制为琥珀左边条）。

    自动 multipage 导航（pages/ 目录扫描）由 theme.css 隐藏——本应用以
    条件渲染为准，避免双导航。
    """
    with st.sidebar:
        st.markdown('<div class="ba-sidebar-title">Bio-Audit 演示</div>',
                    unsafe_allow_html=True)
        st.markdown('<div class="ba-sidebar-sub">科学决策审计 · 深色审计台</div>',
                    unsafe_allow_html=True)
        st.markdown('<div class="ba-nav-rule"></div>', unsafe_allow_html=True)
        st.radio(
            "页面导航",
            options=[pid for pid, _l, _m in PAGES],
            format_func=lambda pid: PAGE_TITLES[pid],
            key="page",
            label_visibility="collapsed",
        )
        st.markdown('<div class="ba-nav-rule"></div>', unsafe_allow_html=True)
        st.caption("数据仅读 demo/data/ + 包内资产")


def _scroll_to_top() -> None:
    """切页后滚回顶部。

    Streamlit 会保留上一页的滚动位置，导致切页后新页从中间开始显示
    （前后顺序看不全）。此处注入一小段 JS 把滚动容器与窗口都归零；
    ``height=0`` 不占布局。
    """
    st.components.v1.html(
        """
        <script>
          (function () {
            var d = window.parent.document;
            var targets = [
              d.querySelector('[data-testid="stMain"]'),
              d.querySelector('section.main'),
              d.querySelector('[data-testid="stAppViewContainer"]'),
              d.scrollingElement,
            ];
            targets.forEach(function (el) { if (el) { el.scrollTop = 0; } });
            try { window.parent.scrollTo(0, 0); } catch (e) {}
          })();
        </script>
        """,
        height=0,
    )


def _render_surface_note() -> None:
    """界面身份与降级声明（降级可见原则）。

    演示台易被误读为「产品面 / 完整产品」——据产品面的身份与降级要求，
    必须在界面上显式声明自身身份，并列出本期未展示的规范能力；
    不得以沉默省略代替声明。
    """
    st.markdown(
        '<div class="ba-surface-note">'
        "<b>本界面为机制演示台</b>（用于沟通与教学）：演示审计如何判定与取证。"
        "它<b>不是产品面</b>，不代表 MVP 用户面、验收、运行证明或任何结案含义；"
        "界面上的分数是<b>决策等级的聚合</b>（审计评分），不是科学总分或质量排名。"
        "<br>本期未展示：结果多维分列（四轴：inference_type / explanation_depth / scope / validation）"
        " · 唯一权威路径标识 · 有界使用 / 报告 / 复核 / 历史视图。"
        "</div>",
        unsafe_allow_html=True,
    )


def _render_page() -> None:
    page_id = st.session_state.page
    # 切页（或首次进入）→ 滚回顶部：导航切换必须从页面开头看起
    if st.session_state.get("_last_page_id") != page_id:
        st.session_state["_last_page_id"] = page_id
        _scroll_to_top()
    mod_name = next((m for pid, _l, m in PAGES if pid == page_id), PAGES[0][2])
    module = importlib.import_module(mod_name)
    module.render()
    # 页间过渡（叙事层）：本页内容之后、页尾之前，指明下一步看什么——
    # 统一在此追加，四页自动获得一致的位置与样式。
    st.markdown(narrative.next_step_html(page_id), unsafe_allow_html=True)
    # 界面身份与降级声明（身份与降级要求）：四页统一页脚
    _render_surface_note()


def main() -> None:
    st.set_page_config(page_title="Bio-Audit 审计演示", page_icon="🔬",
                       layout="wide", initial_sidebar_state="expanded")
    _inject_theme()

    # 页状态：radio(key="page") 持久化；首跑显式初始化（横幅先于侧边栏渲染）
    if "page" not in st.session_state:
        st.session_state.page = DEFAULT_PAGE

    # ── 启动数据校验（自包含性硬约束：demo 只读 demo/data/，缺失不崩溃）──
    problems = data_index.verify_data_ready()
    if problems:
        st.error(
            "演示数据缺失或指纹不匹配（demo/data/ 未就绪）：\n\n"
            + "\n".join(f"- {p}" for p in problems)
            + "\n\n请先运行 `python demo/scripts/export_demo_data.py` 生成演示数据，"
              "然后再启动本应用。"
        )
        st.stop()

    _render_banner(PAGE_TITLES.get(st.session_state.page, "演示"))
    _render_sidebar()
    _render_page()


if __name__ == "__main__":
    main()
