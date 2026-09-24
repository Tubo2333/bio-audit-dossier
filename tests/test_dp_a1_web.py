"""A1 Web 表单形态（ui/a1_submit_app.py）的测试。

覆盖：
- GET / 表单页可达；
- /api/submit 合法提交 → 200 + 五点判定 + 产物链接可访问；
- /api/submit 未来观测时点 → 422（机械合理性，未写账）；
- /api/submit 缺决策点 → 422（批次级拒绝）；
- /api/submit 缺关键字段 → 400；
- /api/upload 只读回文本作参照（不落盘）；
- 产物服务 /var/<id>/report.html 只服务 var 内的文件（越界 404）。

边界：不改产品语义；表单与管线共用 `scripts/submit_analysis.submit_json`。
flask 未安装时本文件整组 skip（见下方 `importorskip` 注释）。
"""

from __future__ import annotations

import importlib.util
import io
import json
import pathlib
import sys

import pytest

#: flask 属 `a1` extra（pyproject.toml）；CI 只装 `.[demo]`、requirements*.lock
#: 亦不含 flask ⇒ 未装时本组应 **skip** 而非 error（同 tests/test_demo_smoke.py:143
#: 对 streamlit 的既有惯例）。成因：本文件导入 ui/a1_submit_app.py 需 flask，
#: 原先未守卫，缺依赖时整组 10 项报 ModuleNotFoundError（2026-09-23 实测）。
pytest.importorskip("flask", reason="需要 flask（pip install -e '.[a1]'）")

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
UI = REPO / "ui"

sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(SCRIPTS))

_FIXED = "2026-09-14T10:00:00+00:00"


def _load(name: str):
    saved = sys.stdout
    try:
        sys.stdout = io.StringIO()
        spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.stdout = saved
    return mod


@pytest.fixture(scope="module")
def ex():
    return _load("make_example_submission")


@pytest.fixture()
def app(ex, tmp_path, monkeypatch):
    """spec 加载 ui/a1_submit_app.py（ui 不是包；与其他 ui 测试一致的做法）。"""
    saved = sys.stdout
    try:
        sys.stdout = io.StringIO()
        spec = importlib.util.spec_from_file_location("a1_submit_app", UI / "a1_submit_app.py")
        a1 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(a1)
    finally:
        sys.stdout = saved
    monkeypatch.setattr(a1, "VAR", tmp_path / "var")
    a1.app.config["TESTING"] = True
    return a1.app.test_client()


def _post(client, payload):
    return client.post("/api/submit", json=payload)


def test_index_form_reachable(app):
    r = app.get("/")
    assert r.status_code == 200
    # 2C 笔 2（2026-09-15）：标题改「提交一次分析」——内部码「A1 输入面」退出
    # 用户可见文案；断言锚随标题更新，意图不变（表单页可达且带识别标题）。
    assert "提交一次分析" in r.get_data(as_text=True)


def test_submit_ok_and_artifacts_served(app, ex):
    payload = ex.build_submission()
    r = _post(app, payload)
    assert r.status_code == 200, r.get_data(as_text=True)[:200]
    j = r.get_json()
    assert set(j["judgments"].values()) == {"auditable"}
    assert set(j["links"]) == {"report.html", "deliverable.html", "report.json"}
    page = app.get(j["links"]["report.html"])
    assert page.status_code == 200
    assert "链条总览" in page.get_data(as_text=True)
    assert "ui/report.sample.json" not in page.get_data(as_text=True)


def test_submit_rejects_impossible_freshness(app, ex):
    payload = ex.build_submission()
    payload["decision_points"]["filtering"]["evidence_observations"][0]["observed_at"] = \
        "2099-01-01T00:00:00+00:00"
    r = _post(app, payload)
    assert r.status_code == 422
    j = r.get_json()
    assert j.get("stage") == "freshness"
    assert j.get("reasons")


def test_submit_rejects_missing_point_at_registration(app, ex):
    payload = ex.build_submission()
    payload["decision_points"].pop("multiple_testing_correction")
    r = _post(app, payload)
    assert r.status_code == 422
    assert r.get_json().get("stage") == "registration"


def test_submit_duplicate_audit_id_gets_friendly_message(app, ex):
    """同一 audit_id 提交两次：第二次被账本拒绝，且错误是可读的提示而非裸 ValueError。"""
    payload = ex.build_submission()
    assert _post(app, payload).status_code == 200
    r2 = _post(app, payload)
    assert r2.status_code == 422
    body = r2.get_json()
    assert body.get("stage") == "registration"
    assert "audit_id" in body.get("error", "")
    assert "ValueError" not in body.get("error", "")


def test_submit_requires_key_fields(app):
    r = _post(app, {"audit_id": "x"})
    assert r.status_code == 400


def test_upload_returns_text_as_reference_only(app):
    r = app.post("/api/upload", data={"material": (io.BytesIO("TMM; DESeq2 1.42.0".encode()),
                                                  "trace.txt")},
                 content_type="multipart/form-data")
    assert r.status_code == 200
    j = r.get_json()
    assert j["name"] == "trace.txt"
    assert "DESeq2" in j["text"]


def test_skeleton_template_endpoint(app):
    """下载空白模板：形状与契约一致、带必填占位、可作为导入回填的输入。"""
    r = app.get("/api/skeleton")
    assert r.status_code == 200
    j = r.get_json()
    assert set(j["decision_points"]) == {"filtering", "normalization",
                                         "differential_method", "multiple_testing_correction",
                                         "significance_threshold"}
    dp = j["decision_points"]["filtering"]["decision_declaration"]
    assert set(dp) == {"method", "threshold", "sample_rule", "unit", "source"}
    obs = j["decision_points"]["filtering"]["evidence_observations"][0]
    assert "observed_at" in obs and "valid_for_seconds" in obs
    assert j.get("_template_note")


def test_artifact_service_rejects_path_escape(app):
    r = app.get("/var/../pyproject.toml")
    assert r.status_code == 404


def test_artifacts_land_in_gitignored_var_dir(app, ex, tmp_path):
    """产物写进 var/（仓库已 gitignore），不污染工作树。"""
    payload = ex.build_submission()
    r = _post(app, payload)
    assert r.status_code == 200
    out_dir = tmp_path / "var" / payload["audit_id"]
    assert out_dir.exists()
    for name in ("report.json", "report.html", "deliverable.html",
                 "ledger.points.jsonl", "ledger.authority.jsonl"):
        assert (out_dir / name).is_file(), f"缺产物 {name}"