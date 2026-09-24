"""A1 输入面 · Web 表单形态（Flask）。

这是 `scripts/submit_analysis.py` 的**第二个入口形态**：同一个契约、同一套三道门、
同一份产物——区别只在「研究者怎么把分析送进来」：
- CLI 形态：填 JSON 文件 → 命令提交（`scripts/submit_analysis.py --file …`）
- **Web 形态（本文件）**：浏览器里填表 → 点提交 → 服务端调同一个 `submit_json()`

人类定的形态口径（2026-09-14）：
- **独立 Web 服务**（Flask；已随 streamlit 传递安装，显式声明进 `a1` 可选依赖）；
- **上传材料只作人工参照**：浏览器可上传 trace/文本，服务只**读回文本送回页面参照区**，
  字段仍由研究者手填——与当前契约能力一致，**不自动解析**（那是第二版 trace 解析的事）。

路由
----
- `GET /`            提交表单页
- `POST /api/upload` 上传材料 → 返回 {name, text[:N]}（只做参照，不落盘仓库）
- `POST /api/submit` 收表单 JSON → 走 submit_json()（三道门）→ 返回判定与产物链接
- `GET /var/<path>`  产物服务（report.html / deliverable.html / report.json）

边界（与全项目一致）
--------------------
- 复用既有契约与管线：**不新增校验、不新增判定、不新增公共语义**。
- secrets 禁令：表单**不索取**任何密码/密钥/令牌；上传物是分析材料，不是凭据。
- 上传文本**不落进仓库**（只存在内存/临时，供参照）；产物写 `var/`（已 gitignore）。
- 不做生产部署声称；默认本地回环 127.0.0.1。

运行
----
    .\\.venv\\Scripts\\python.exe ui\\a1_submit_app.py
    # → http://127.0.0.1:5050
"""

from __future__ import annotations

import io
import json
import pathlib
import sys

import flask

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
SRC = REPO / "src"

sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SCRIPTS))

import submit_analysis as submit  # noqa: E402  (CLI/Web 共用的管线函数)

app = flask.Flask(__name__)
VAR = REPO / "var"

_UPLOAD_TEXT_LIMIT = 400_000   # 参照文本上限（字符）


def _submission_from_payload(payload: dict) -> tuple[str | None, dict | None]:
    """把表单 JSON 组装成契约形状。返回 (err, submission)。"""
    need = ("audit_id", "analysis_context", "integrity_metadata", "decision_points")
    for k in need:
        if not payload.get(k):
            return f"缺少 {k}", None
    return None, payload


@app.get("/")
def index():
    html = pathlib.Path(__file__).with_name("a1_form.html").read_text(encoding="utf-8")
    return flask.Response(html, mimetype="text/html")


@app.get("/api/skeleton")
def skeleton():
    """下载空白提交模板（A1 骨架）：研究者/工具填好后再导入回填（接口同 /api/import 的格式）。"""
    sys.path.insert(0, str(SCRIPTS))
    import make_submission_skeleton as sk  # noqa: PLC0415
    out = sk.build(None, "audit-web-your-id")
    out["_template_note"] = (
        "把<必填:…>替成你的内容；观测行照行复制、删多余行；"
        "value 必须等于该点 method；observed_at 用带时区的过去时刻（如 2026-09-13T00:00:00+00:00）。"
        "填好后回到 A1 表单页 → 导入 → 检查 → 提交。")
    return flask.jsonify(out)


@app.post("/api/upload")
def upload():
    """上传材料：只读回文本供页面参照，不落盘、不解析。"""
    f = flask.request.files.get("material")
    if f is None or not f.filename:
        return flask.jsonify({"error": "没有收到上传文件"}), 400
    raw = (f.read(_UPLOAD_TEXT_LIMIT + 1) or b"").decode("utf-8", errors="replace")
    if len(raw) > _UPLOAD_TEXT_LIMIT:
        return flask.jsonify({"error": f"参照材料过大（上限 {_UPLOAD_TEXT_LIMIT} 字符）"}), 413
    return flask.jsonify({"name": f.filename, "text": raw[:_UPLOAD_TEXT_LIMIT]})


@app.post("/api/submit")
def api_submit():
    body = flask.request.get_json(silent=True)
    if not isinstance(body, dict):
        return flask.jsonify({"error": "请求体必须是 JSON 对象"}), 400
    err, submission = _submission_from_payload(body)
    if err:
        return flask.jsonify({"error": err}), 400

    rc, payload = submit.submit_json(
        submission, out_dir=str(VAR), now=flask.request.args.get("now"))
    if rc == 0:
        rel = pathlib.Path(payload["dir"]).name
        payload["links"] = {
            "report.html": f"/var/{rel}/report.html",
            "deliverable.html": f"/var/{rel}/deliverable.html",
            "report.json": f"/var/{rel}/report.json",
        }
        return flask.jsonify(payload)
    if rc == 2:
        return flask.jsonify({"reasons": payload.get("reasons", []), "stage": "freshness"}), 422
    if rc == 3:
        err = payload.get("error", "")
        if "duplicate" in err.lower() and "audit_id" in err.lower():
            err = ("该 audit_id 已被登记过——账本只认一次（append-only）。"
                   "请把上面的 audit_id 换一个再提交。")
        return flask.jsonify({"error": err, "stage": "registration"}), 422
    return flask.jsonify({"error": payload.get("error"), "stage": "usage"}), 400


@app.get("/var/<path:filepath>")
def serve_artifact(filepath):
    target = (VAR / filepath).resolve()
    if not str(target).startswith(str(VAR.resolve())) or not target.is_file():
        return flask.abort(404)
    return flask.send_file(target)


if __name__ == "__main__":
    import argparse
    import threading
    import webbrowser

    ap = argparse.ArgumentParser(description="A1 输入面 Web 表单")
    ap.add_argument("--no-browser", action="store_true",
                    help="启动后不自动打开浏览器（测试/脚本用）")
    ap.add_argument("--port", type=int, default=5050)
    args = ap.parse_args()

    url = f"http://127.0.0.1:{args.port}"
    if not args.no_browser:
        # 用 Python 的 webbrowser 打开系统默认浏览器——比在 cmd 里 start+timeout
        # 更稳（CMD 的引号/开关转义把"无效开关 - /t"这类错误带进窗口，实测踩过）。
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    # 默认只监听本机回环；本形态是本地/内网演示入口，不做生产部署声称。
    app.run(host="127.0.0.1", port=args.port, debug=False)