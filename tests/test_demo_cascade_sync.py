"""级联同步回归测试（修订版）：上级（案例类型）变更后，下级（案例轨迹）必须落到新组。

背景（2026-09-22 用户实测）：
    工坊页把「② 案例类型」由「经典轨迹」切到「真实评测」后，**③ 案例轨迹 的显示文本
    仍停留在上一组的条目**（`scRNA · 正确执行（Smart-seq2 教科书式）· 85.0 · 通过`），
    而下方案例卡已按新组渲染 `cellvoyager_g`（30.0）。
    根因：`components.py` 在检测到组变更时**只 pop 了下级的 session_state 键**，
    下级控件在同轮已按旧值渲染过；范式级有 pending+rerun、组级漏了。
    修复：改为**显式给出选中项**（session_state 写入新组首个 id + `index`），
    使该控件在本轮即以新组选项与标签重建。

本测试**以 AppTest 断言可观测状态**：
    ① 切换组后，③ 的值必须属于新组案例集合；
    ② ③ 的选项集必须只含新组案例；
    ③ ③ 的值必须等于新组的**首个**案例（即旧实现在界面层未能完成的「自动跳转」）。

**⚠ 本测试的判别力边界（如实标注，据第 16／17 轮独立复核）**：
    该**级联脱节缺陷只在浏览器前端显示层可见**——其服务端状态（session_state 与选项集）
    在修复前后**一直是正确的**（旧实现 `pop` 下级键后，selectbox 回落至首项，服务端值
    同样落在新组首项）。故**本测试无法区分修复前后**，**不构成该缺陷的回归防线**；
    它只能锁住「切组后落到新组首项」这一**服务端契约**。
    该边界经复核**机制分析支持**，惟因只读约束**未获实证**（验证需把基线
    `components.py` 写入可写副本，与只读冲突）。

另：本测试断言与 `demo/data` **强耦合**（`:85` 写死 `len(options)==2`、`:90` 写死首项 id
`cellvoyager_g`）——**数据增删案例即失败**，属已知脆弱点，登记备查。
"""
from __future__ import annotations

import pathlib

import pytest

DEMO_DIR = pathlib.Path(__file__).resolve().parents[1] / "demo"

KW_CLASSIC = "\u7ecf\u5178\u8f68\u8ff9"      # 经典轨迹
KW_REAL = "\u771f\u5b9e\u8bc4\u6d4b"         # 真实评测
KW_SMART = "Smart-seq2"                     # 经典轨迹组典型条目
KW_CV = "CellVoyager"                       # 真实评测组典型条目


def _apptest():
    pytest.importorskip("streamlit", reason="需要 streamlit（pip install -e '.[demo]'）")
    from streamlit.testing.v1 import AppTest
    return AppTest


def _workshop_app():
    AppTest = _apptest()
    app = AppTest.from_file(str(DEMO_DIR / "app.py"), default_timeout=180)
    app.run(timeout=180)
    app.sidebar.radio[0].set_value("workshop")
    app.run(timeout=180)
    assert not app.exception, f"工坊页异常: {app.exception}"
    return app


def test_cascade_group_switch_lands_on_new_group_first_case():
    """切到「真实评测」后，③ 的值与选项都必须落到该组，且值为该组首项。"""
    app = _workshop_app()

    # 前置：默认在经典轨迹组
    assert app.selectbox[1].value == KW_CLASSIC, (
        f"默认案例类型应为「{KW_CLASSIC}」，实测 {app.selectbox[1].value!r}"
    )
    before_opts = list(app.selectbox[2].options)
    assert any(KW_SMART in o for o in before_opts), (
        f"经典轨迹组的③应含 {KW_SMART} 条目；实测 {before_opts}"
    )

    # 切换组
    before_val = app.selectbox[2].value
    app.selectbox[1].set_value(KW_REAL)
    app.run(timeout=180)
    assert not app.exception, f"切组后异常: {app.exception}"

    after = app.selectbox[2]
    after_opts = list(after.options)
    after_val = after.value

    # ① ③ 的选项集必须只含新组案例（不得残留经典轨迹条目）
    assert not any(KW_SMART in o for o in after_opts), (
        f"切到「{KW_REAL}」后，③ 的选项仍含经典轨迹条目（{KW_SMART}）：{after_opts}"
    )
    assert any(KW_CV in o for o in after_opts), (
        f"切到「{KW_REAL}」后，③ 的选项应含 {KW_CV}；实测 {after_opts}"
    )
    # ② ③ 的值必须落在新组（AppTest 的 value 为 id；options 为格式化标签，二者不同形）
    assert KW_SMART not in str(after_val), (
        f"切到「{KW_REAL}」后，③ 仍选中经典轨迹条目：{after_val!r}"
    )
    assert str(after_val).startswith("cellvoyager"), (
        f"切到「{KW_REAL}」后，③ 的值应为该组案例（cellvoyager_*）；实测 {after_val!r}"
    )
    assert len(after_opts) == 2, f"真实评测组应恰有 2 个案例；实测 {after_opts}"
    # ③ **自动跳转到新组首项**——这是用户报告「它依旧没有自动地跳转到某一个」所对应的行为。
    #    **⚠ 本断言不具判别力**（据第 16／17 轮独立复核）：旧实现只 pop 下级键时，selectbox
    #    自行回落至新组首项，**服务端值同样为 cellvoyager_g**，故本断言在修复前后**均通过**。
    #    它锁的是**服务端契约**，不是该缺陷的回归防线——详见本文件 docstring 之「判别力边界」。
    assert after_val == "cellvoyager_g", (
        f"切组后 ③ 应自动跳到新组首个案例 cellvoyager_g；实测 {after_val!r}"
    )
    assert before_val != after_val, (
        "切组前后 ③ 的值未发生变化——说明未发生自动跳转（旧值被沿用）"
    )
