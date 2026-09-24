"""demo 定制组件封装。

设计依据：深色审计台设计规范 §3.1（组件职责澄清）/ §3.2（工坊页）。

职责边界（职责边界，勿混淆）：
- ``Cascader`` 第一级 = 范式（DEG / Pan-Cancer / scRNA）——范式选择唯一入口；
  第二级 = 案例类型分组（Group Select 语义：经典轨迹 / 黄金对照 / 真实评测）
  ——只整理轨迹，不选范式（分组永不改变范式状态）；上级变更清空下级状态；
- ``compare_panel`` = 轨迹对比面板（基准 + ≤2 对比项并排；基准锁定不可移除、
  琥珀标记，对比项青色可逐条增删，决策行按 ontology 顺序对齐，缺失列显示"无此决策"）；
- ``split_button`` = 主操作（运行审计）+ 下拉（导出 JSON / 复制证据链 /
  查看规则匹配明细）。

契约：全部交互状态经 st.session_state 持久化（刷新不丢）；组件独占各自的
key 命名空间（key_prefix），恢复演示默认态由页面调用 reset 方法实现。
"""
from __future__ import annotations

from typing import Callable

import streamlit as st

#: 案例类型分组（供 Cascader 第二级 / GroupSelect 共用；组与范式的
#: 可用性约束见 :func:`groups_for_paradigm`）
GROUPS = ("经典轨迹", "黄金对照", "真实评测")

#: 范式选项（第一级；顺序即展示顺序）
PARADIGMS = ("deg", "pan", "scrna")
PARADIGM_LABELS = {"deg": "DEG", "pan": "Pan-Cancer", "scrna": "scRNA"}

#: 轨迹上限（对比并排；设计 §3.2 钉死 ≤3）
MAX_COMPARE = 3


def groups_for_paradigm(paradigm: str | None) -> tuple[str, ...]:
    """范式 → 可用案例类型组。

    黄金对照/真实评测仅 scRNA 范式（设计 §3.2：DEG/pan 下为空 →
    引导文案"黄金对照仅 scRNA 范式"，不出现空列表）。
    未选范式（None）时返回全组——由 UI 层在未选态展示引导，不产生空列表。
    """
    if paradigm in ("deg", "pan"):
        return ("经典轨迹",)
    return GROUPS


#: 案例（轨迹卡）统一形状：{id, label, sub, paradigm, group}
#: 由页面经 :meth:`Cascader.render` 的 resolver 提供（数据层不耦合组件）
Case = dict


class Cascader:
    """范式 → 案例类型 → 轨迹 三级联动（上级变更清空下级状态）。

    交互状态键（``{key_prefix}_paradigm/_group/_trajectory``）由本组件独占，
    st.session_state 持久化——刷新不丢级联状态。上级变更时显式清空下级键
    （st.selectbox 的 key 值若不在新选项中会静默回落，必须主动 reset）。
    未选过（首次渲染）时应用 ``defaults``（演示默认路径，见设计 §3.2）。
    """

    def __init__(self, key_prefix: str = "cascader", defaults: dict | None = None):
        self.key_prefix = key_prefix
        self.k_paradigm = f"{key_prefix}_paradigm"
        self.k_group = f"{key_prefix}_group"
        self.k_trajectory = f"{key_prefix}_trajectory"
        # 上级值的"派生基线"（值变化 → 清空下级）；与控件键分离，避免误清
        self.k_paradigm_base = f"{key_prefix}_paradigm_base"
        self.k_group_base = f"{key_prefix}_group_base"
        # 默认演示路径由调用方（页面）单一事实源传入（
        # 组件不内置第二份默认值，防漂移）
        if defaults is None:
            raise ValueError("Cascader.defaults 必填（演示默认路径由页面常量提供）")
        self.defaults = defaults

    # ── 状态管理 ──

    def _apply_defaults(self) -> None:
        """首次渲染（或 reset 后）把默认演示路径写入控件键。

        派生基线同步初始化：首次渲染不把已播种值误判为"上级变更"而清空
        下级（预置态如 黄金对照 + 指定轨迹 直接可用）。
        """
        for key, value in self.defaults.items():
            k = getattr(self, f"k_{key}")
            if k not in st.session_state:
                st.session_state[k] = value
        if self.k_paradigm_base not in st.session_state:
            st.session_state[self.k_paradigm_base] = (
                st.session_state.get(self.k_paradigm))
        if self.k_group_base not in st.session_state:
            st.session_state[self.k_group_base] = st.session_state.get(self.k_group)

    def reset_defaults(self) -> None:
        """恢复演示默认态：三个控件键直接写回默认值（页面随后 rerun）。"""
        st.session_state[self.k_paradigm] = self.defaults["paradigm"]
        st.session_state[self.k_group] = self.defaults["group"]
        st.session_state[self.k_trajectory] = self.defaults["trajectory"]

    # ── 渲染 ──

    def render(
        self,
        resolver: Callable[[str, str], list[Case]],
    ) -> tuple[str | None, str | None, str | None]:
        """渲染三级联动；返回 (paradigm, group, case_id)。

        Parameters
        ----------
        resolver : (paradigm, group) -> list[Case]
            按范式×组解析可选案例（由页面从 demo/data 构建；组件不碰数据层）。
        """
        self._apply_defaults()

        # ── 第一级：范式（变更 → 清空组与轨迹）──
        paradigm = st.selectbox(
            "① 分析范式",
            options=list(PARADIGMS),
            format_func=lambda p: PARADIGM_LABELS[p],
            key=self.k_paradigm,
            help="范式决定规则集（deg / pan / scrna）与可用案例类型。",
        )
        if paradigm != st.session_state.get(self.k_paradigm_base):
            for k in (self.k_group, self.k_trajectory, self.k_group_base):
                st.session_state.pop(k, None)
            st.session_state[self.k_paradigm_base] = paradigm

        # ── 第二级：案例类型（Group Select 语义：只整理轨迹，不选范式）──
        groups = groups_for_paradigm(paradigm)
        if st.session_state.get(self.k_group) not in groups:
            st.session_state.pop(self.k_group, None)
        group = st.selectbox(
            "② 案例类型",
            options=list(groups),
            key=self.k_group,
            help="黄金对照 / 真实评测仅 scRNA 范式提供。",
        )
        if group != st.session_state.get(self.k_group_base):
            st.session_state.pop(self.k_trajectory, None)
            st.session_state[self.k_group_base] = group

        # DEG/pan 下黄金对照/真实评测为空 → 引导文案（设计 §3.2：
        # "黄金对照仅 scRNA 范式"，不出现空列表）
        if paradigm in ("deg", "pan"):
            st.markdown(
                '<div class="ba-guide">黄金对照与真实评测仅 <b>scRNA</b> 范式'
                "提供——当前 DEG / Pan-Cancer 范式无对应资产，"
                "切换到 scRNA 后可查看。</div>",
                unsafe_allow_html=True,
            )

        # ── 第三级：轨迹 ──
        cases = resolver(paradigm, group)
        if not cases:
            # 该组无案例（DEG/pan 的黄金对照等在上方已有引导；此处通用兜底）
            st.markdown(
                '<div class="ba-guide">当前「%s」组下暂无案例。</div>'
                % st.session_state.get(self.k_group, group),
                unsafe_allow_html=True,
            )
            return paradigm, group, None

        # 缺陷修复（2026-09-22，用户实测 + AppTest 复核）：
        # 原实现只 `pop(k_trajectory)` 就交给 selectbox。该 `pop` 发生在**本控件渲染之前**、
        # 但在**上级（组）变更被检测到的同一轮**里——widget 已由上一轮带着旧值画过，
        # 前端因此留着旧标签（实测表现：切到「真实评测」后 ③ 仍显示经典轨迹的
        # `… Smart-seq2 … · 85.0 · 通过`，而下方案例卡已按新组渲染 30.0）。
        # 正解不是"再 pop 一次"，而是**显式给出选中项**（session_state 有效值 + index），
        # 使该控件在**本轮**就带着新组的选项与标签重建。
        _ids = [c["id"] for c in cases]
        _cur = st.session_state.get(self.k_trajectory)
        if _cur not in _ids:
            _cur = _ids[0]
            st.session_state[self.k_trajectory] = _cur
        trajectory = st.selectbox(
            "③ 案例轨迹",
            options=_ids,
            index=_ids.index(_cur),
            format_func=lambda cid: next(
                (c["label"] for c in cases if c["id"] == cid), cid
            ),
            key=self.k_trajectory,
        )
        return paradigm, group, trajectory


def compare_panel(
    available: list[Case],
    primary_id: str | None,
    key: str = "traj_compare",
) -> list[str]:
    """轨迹对比面板（**基准 + ≤2 对比项**；自定义渲染，主从可辨）。

    设计（2026-09-20 重构）：
    - **基准**：由「③ 案例轨迹」级联选中项决定（``primary_id``）——它是参照系，
      锁定在并排第一位、**不可移除**（琥珀标记，注明"跟随 ③"）。
    - **对比项**：用户添加的附加轨迹——青色「对比 N」标记，可逐条移除。
    - 并排总数上限 ``MAX_COMPARE``(3) = 基准 1 条 + 对比项 ≤2 条。
    - 状态：``session_state[key]`` 只存**对比项** id 列表（基准不进状态，避免重复）。

    返回并排顺序列表 ``[基准, 对比 1, …]``；基准缺失（未选/无效）时返回 []。
    """
    import html as _html  # 局部导入：仅本面板做 HTML 转义

    if not available:
        st.markdown(
            '<div class="ba-guide">当前范式暂无经典轨迹可对比。</div>',
            unsafe_allow_html=True,
        )
        return []

    by_id = {c["id"]: c for c in available}
    if not primary_id or primary_id not in by_id:
        st.markdown(
            '<div class="ba-guide">请先在上方「③ 案例轨迹」选一条<b>基准</b>轨迹'
            "（它是对比的参照系），再添加对比项。</div>",
            unsafe_allow_html=True,
        )
        return []

    max_vs = max(0, MAX_COMPARE - 1)
    # 对比项：过滤失效 id 与基准自身（基准永不作为对比项）
    compare: list[str] = [
        t for t in st.session_state.get(key, []) if t in by_id and t != primary_id
    ]
    st.session_state[key] = compare

    st.markdown(
        f'<div class="ba-section-sub">并排清单：基准 1 条 + 对比项 ≤{max_vs} 条'
        f"（上限 {MAX_COMPARE} 条并排）</div>",
        unsafe_allow_html=True,
    )

    # ── 基准行（锁定，不可移除）──
    st.markdown(
        '<div class="ba-cmp-row ba-cmp-row-primary">'
        '<span class="ba-cmp-tag ba-cmp-tag-primary">基准</span>'
        f'<span class="ba-cmp-name">{_html.escape(str(by_id[primary_id]["label"]))}</span>'
        '<span class="ba-cmp-lock">跟随 ③ 案例轨迹 · 不可移除</span></div>',
        unsafe_allow_html=True,
    )

    # ── 对比项行（可逐条移除）──
    for i, tid in enumerate(list(compare), start=1):
        c_left, c_right = st.columns([5, 1.4], gap="small")
        with c_left:
            st.markdown(
                '<div class="ba-cmp-row ba-cmp-row-vs">'
                f'<span class="ba-cmp-tag ba-cmp-tag-vs">对比 {i}</span>'
                f'<span class="ba-cmp-name">{_html.escape(str(by_id[tid]["label"]))}</span>'
                "</div>",
                unsafe_allow_html=True,
            )
        with c_right:
            if st.button("移除", key=f"{key}_rm_{tid}", use_container_width=True):
                compare.remove(tid)
                st.session_state[key] = compare
                st.rerun()

    # ── 添加行（选择即加入；候选排除基准与已选项）──
    if len(compare) < max_vs:
        candidates = [cid for cid in by_id if cid != primary_id and cid not in compare]
        if candidates:
            pick = st.selectbox(
                "＋ 添加对比项",
                options=candidates,
                index=None,
                key=f"{key}_pick",
                format_func=lambda cid: next(
                    (c["label"] for c in available if c["id"] == cid), cid
                ),
                placeholder="选择要添加的对比项（不含基准）",
                help="加入后与基准并排审计；结果表列头标「对比 N」。",
            )
            if pick and pick not in compare:
                st.session_state[key] = compare + [pick]
                st.rerun()
    else:
        st.markdown(
            f'<div class="ba-guide">已达并排上限（{MAX_COMPARE} 条）——'
            "如需添加，请先移除一条对比项。</div>",
            unsafe_allow_html=True,
        )

    return [primary_id] + compare


def case_selectbox(
    key: str = "capture_case",
    label: str = "案例（选择要拆解的采集链路）",
) -> str:
    """采集案例选择器（选项来自 ``case_registry``；返回 case_id）。

    设计：采集页**自选案例**，不与审计工坊页联动——两页分工不同
    （工坊 = 结果层/现象；采集 = 机制层/拆解），各自自治，避免"硬凑关联"。
    每条案例演示的采集机制不同（全一致 / 虚报撤销 / 预期补入 / 方法学风险）。
    """
    import case_registry

    ids = case_registry.case_ids()
    return st.selectbox(
        label,
        options=ids,
        format_func=lambda cid: case_registry.get_case(cid).label,
        key=key,
        help="各案例的核验口径（分数/verdict）见下方案例卡片；数字由链路实时重算。",
    )


def process_animation(
    title: str,
    steps: list[tuple[str, str]],
    step_seconds: float = 0.95,
    wait: bool = True,
) -> None:
    """审计过程动画（**独立文档渲染**：每次必然从头播放）。

    为什么用 iframe（2026-09-20 复盘后的载体决定，非时序补丁）：
    Streamlit 在 rerun 时**复用同一 DOM 元素**（只替换内容）——
      · 依赖「元素重建」的 CSS 动画不会重播：结构趋同后只更新文本，
        动画停在终态（表现为"所有步骤一下全亮"，且此后一直如此）；
      · 依赖「Python 多次 markdown 推送」的分帧方案，浏览器端可能被合并。
    iframe（``st.components.v1.html``）是**独立文档**：srcdoc 一变，浏览器必然
    重建文档 → 内部 CSS 动画从零播放，与 Streamlit 的 diff 行为解耦。
    这是「选择 → 结果」过渡唯一可靠的载体。

    Parameters
    ----------
    title : str
        过程块标题（如「审计过程（6 步）」）。
    steps : list[tuple[str, str]]
        ``(步骤名, 该步真实小结)``——小结填实测数字。
    step_seconds : float
        每步时长（默认 0.95s）。
    wait : bool
        是否阻塞等待动画播完（默认 True）：播完再渲染结果区，
        保证「先判断过程、后给结论」的顺序。
    """
    import html as _html
    import time

    rows = "".join(
        f'<div class="s" style="animation-delay:{i * step_seconds:.2f}s">'
        '<span class="m">✓</span>'
        f'<span class="t"><b>{_html.escape(name)}</b>'
        f'<span class="d">｜{_html.escape(detail)}</span></span>'
        f'<span class="i">{i + 1}/{len(steps)}</span></div>'
        for i, (name, detail) in enumerate(steps)
    )
    total = step_seconds * len(steps)
    # 自包含文档：不依赖 theme.css（iframe 内不继承父页样式）
    doc = (
        '<!DOCTYPE html><html><head><meta charset="utf-8"><style>'
        "*{box-sizing:border-box}"
        "body{margin:0;background:#1a1d24;color:#e5e7eb;"
        'font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif}'
        ".wrap{padding:12px 14px;border:1px solid #2a2f3a;border-radius:10px;"
        "background:#1a1d24}"
        ".head{font-size:.84rem;color:#22d3ee;margin-bottom:8px;letter-spacing:.02em}"
        ".bar{height:4px;border-radius:999px;background:#2a2f3a;overflow:hidden;"
        "margin-bottom:10px}"
        ".fill{height:100%;width:0%;"
        "background:linear-gradient(90deg,#22d3ee,#f59e0b);"
        f"animation:grow {total:.1f}s linear forwards}}"
        "@keyframes grow{from{width:0%}to{width:100%}}"
        ".s{display:flex;align-items:baseline;gap:10px;padding:6px 10px;"
        "border-radius:8px;border-left:3px solid rgba(16,185,129,.45);opacity:0;"
        "animation:stepin .45s ease-out forwards}"
        ".s:last-child{background:rgba(245,158,11,.06);border-left-color:#f59e0b}"
        "@keyframes stepin{from{opacity:0;transform:translateY(5px)}"
        "to{opacity:1;transform:translateY(0)}}"
        ".m{flex:0 0 auto;width:14px;color:#10b981;"
        'font-family:Consolas,monospace;font-size:.82rem}'
        ".s:last-child .m{color:#f59e0b}"
        ".t{font-size:.85rem;line-height:1.6}"
        ".d{color:#9ca3af}"
        ".i{margin-left:auto;color:#6b7280;font-family:Consolas,monospace;"
        "font-size:.74rem}"
        "</style></head><body><div class='wrap'>"
        f"<div class='head'>{_html.escape(title)}</div>"
        "<div class='bar'><div class='fill'></div></div>"
        f"{rows}</div></body></html>"
    )
    st.components.v1.html(doc, height=64 + 36 * len(steps), scrolling=False)
    if wait:
        time.sleep(total)


def split_button(
    primary_label: str,
    key: str = "split_run",
    on_run: Callable[[], None] | None = None,
    render_menu: Callable[[], None] | None = None,
    disabled: bool = False,
    rerun_if_no_results: bool = False,
) -> str | None:
    """Split Button：主按钮（运行审计）+ 下拉菜单（导出/复制/明细）。

    返回触发动作："run"（主按钮）或菜单项写入的动作 id（``{key}_menu_action``）；
    未触发返回 None。菜单内容由 ``render_menu`` 在 popover 内渲染（页面注入
    数据与按钮）。菜单按钮通过写 ``st.session_state[f"{key}_menu_action"]``
    回传动作——popover 点击后自动关闭，页面在下一轮 rerun 消费该动作。

    ``rerun_if_no_results``：菜单内容依赖「本轮刚算出的结果」时置 True。
    成因（2026-09-23 实测修正的「状态滞后一次交互」缺陷）：菜单在控件位渲染，
    而结果由页面在**其后**写入 session_state——同一轮脚本内菜单看不到新结果，
    表现为「点一次运行后下拉仍为空态，须再点一次」。置 True 时，主按钮触发且
    本轮尚未有结果，则补一次 rerun：此时结果已就绪，菜单在同一轮即可见。
    仅在「无结果」时触发，**不改变**已就绪状态下的任何行为。
    """
    c_main, c_caret = st.columns([5, 1], gap="small")
    with c_main:
        clicked = st.button(
            primary_label,
            key=f"{key}_primary",
            type="primary",
            use_container_width=True,
            disabled=disabled,
        )
    with c_caret:
        with st.popover("▾", use_container_width=True, disabled=disabled):
            if render_menu is not None:
                render_menu()
    if clicked:
        if on_run is not None:
            on_run()
        if rerun_if_no_results and not st.session_state.get(f"{key}_results_ready"):
            # 本轮开始前尚无结果 ⇒ 页面稍后会写入本次结果；补一次 rerun 使
            # 菜单在结果就绪后再渲染（标记由页面置位，见页面「② 运行」段）。
            st.rerun()
        return "run"
    return st.session_state.pop(f"{key}_menu_action", None)
