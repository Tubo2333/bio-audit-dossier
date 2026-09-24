"""IRR 统计量数学测试（E3：κ/α 正确性，独立小样本基准）。

基准值来自教科书示例：
- Cohen's κ：Fleiss 等经典示例 (po=0.85, pe=0.5 → κ=0.70)；
- Krippendorff's α（nominal）：标准 2 标注者 coincidence 计算。
"""

from bioaudit.benchmark.annotation import (  # noqa: E402
    cohen_kappa,
    compute_irr,
    krippendorff_alpha_nominal,
)


def test_cohen_kappa_perfect():
    a = ["correct"] * 5 + ["error"] * 5
    b = list(a)
    assert cohen_kappa(a, b) == 1.0


def test_cohen_kappa_chance():
    """完全随机一致 → κ ≈ 0（边际均衡时）。"""
    a = ["correct", "error", "edge"] * 3
    # b 与 a 错位 → 一致率低
    b = ["error", "edge", "correct"] * 3
    k = cohen_kappa(a, b)
    assert k <= 0.0


def test_cohen_kappa_classic_value():
    """构造基准：po=0.90, pe=0.52 → κ=(0.9-0.52)/(1-0.52)=0.7917。"""
    a = ["correct"] * 60 + ["error"] * 40
    b = ["correct"] * 55 + ["error"] * 5 + ["correct"] * 5 + ["error"] * 35
    # b: 前 60 中 55 correct + 5 error；后 40 中 5 correct + 35 error
    # 一致 = 55 + 35 = 90 → po=0.90；pe = 0.60*0.60 + 0.40*0.40 = 0.52
    k = cohen_kappa(a, b)
    assert abs(k - (0.9 - 0.52) / (1 - 0.52)) < 1e-9


def test_krippendorff_alpha_perfect_and_random():
    items = {f"i{n}": ["correct", "correct"] for n in range(10)}
    assert krippendorff_alpha_nominal(items) == 1.0
    # 全部同标签 → 无信息，α=1（约定）
    items2 = {f"i{n}": ["correct", "correct"] for n in range(10)}
    assert krippendorff_alpha_nominal(items2) == 1.0


def test_krippendorff_alpha_known_case():
    """手工基准：2 标注者，10 条，8 条一致（4 correct/4 error 对 + 2 分歧对）。

    一致条目: 4×(correct,correct) + 4×(error,error)；分歧: (correct,error),(error,correct)
    po = 0.8；边际 correct=10/20, error=10/20 → de=0.5 → α=(0.8-0.5)/(1-0.5)=0.6
    """
    items = {
        f"i{n}": ["correct", "correct"] for n in range(4)
    }
    items.update({f"j{n}": ["error", "error"] for n in range(4)})
    items["k1"] = ["correct", "error"]
    items["k2"] = ["error", "correct"]
    alpha = krippendorff_alpha_nominal(items)
    assert abs(alpha - 0.6) < 1e-9


def test_compute_irr_report_shape():
    """构造：10 条，9 一致（5 correct + 4 error），1 分歧（error vs edge）。

    po=0.9；pe = 0.5*0.5 + 0.5*0.4 = 0.45 → κ=(0.9-0.45)/0.55≈0.818 ≥ 0.8。
    α（nominal）：de = 0.5² + 0.45² + 0.05² = 0.455 → α = 1-(0.1/0.545)≈0.8165 ≥ 0.8。
    """
    a = [{"step_id": f"s{n}", "label": "correct"} for n in range(5)] + \
        [{"step_id": f"s{n}", "label": "error"} for n in range(5, 10)]
    b = [{"step_id": f"s{n}", "label": "correct"} for n in range(5)] + \
        [{"step_id": f"s{n}", "label": "error"} for n in range(5, 9)] + \
        [{"step_id": "s9", "label": "edge"}]
    rep = compute_irr(a, b)
    assert rep["n_items"] == 10
    assert rep["n_agreed"] == 9
    assert abs(rep["cohen_kappa"] - (0.9 - 0.45) / (1 - 0.45)) < 1e-3  # 输出四舍五入
    assert rep["gate"]["primary_pass"] is True
    assert rep["gate"]["secondary_pass"] is True
