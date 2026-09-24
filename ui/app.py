"""Bio-Audit 薄壳入口（v2 B1）：Act 选择 + 轨迹选择，只调 bioaudit.api。

相对 fullflow-demo/src/ui/app.py：
- 移除 sys.path hack（包已安装，import bioaudit）
- 轨迹文件路径经 bioaudit.paths 锚定（F7）
- 移除 unsafe_allow_html script 注入（审计 D12）
- 标签不再承载分值（P-04 v0.2 §4.3）：分值仅在审计结果与报告中呈现，
  标签只保留分析标识（正确／错误／边缘）；原「标签保持 A4 实测分数」已废
"""
import json

import streamlit as st

from bioaudit.paths import TRAJECTORIES_DIR

st.set_page_config(page_title="Bio-Audit 全流程演示", page_icon="🔬", layout="wide")

ACTS = {
    "deg": {
        "id": "deg",
        "title": "Act 1 · DEG 审计 / DEG Audit",
        "sub": "单基因差异表达 — 4 条轨迹 (2 边缘案例) / 5 条规则 / 4 trajectories (2 edges)",
        "task": "CSTB gene differential expression in TCGA-COAD colorectal cancer",
        "modules": {
            "information_retrieval": {"query": "CSTB TCGA-COAD RNA-seq", "databases": ["TCGA-COAD (n=290 tumor, 41 normal)"], "result": "Retrieved level 3 RNA-seq data for 331 samples, 20,531 genes"},
            "literature_analysis": {"key_findings": ["CSTB is an inhibitor of cysteine proteases", "Upregulated in multiple cancer types including CRC", "Associated with tumor invasion and metastasis"], "cited_papers": 3},
        },
        "trajectories": {
            "correct":  {"label": "✅ 正确分析 / Correct", "file_name": "deg_correct.json", "is_correct": True},
            "error":    {"label": "⚠ 错误分析 / Error", "file_name": "deg_error.json", "is_correct": False},
            "edge_n2":  {"label": "🔬 边缘: n=2 统计推断 / Edge: n=2 inference", "file_name": "deg_edge_n2.json", "is_correct": False},
            "edge_nf":  {"label": "🔬 边缘: 不过滤+不校正 / Edge: no filter+correction", "file_name": "deg_edge_nofilter.json", "is_correct": False},
        },
        "color": "#00B07C",
    },
    "pan": {
        "id": "pan",
        "title": "Act 2 · 泛癌 7 模块 / Pan-Cancer 7 Modules",
        "sub": "CSTB 多模块端到端 — 6 条轨迹 (4 边缘) / 16 条规则 / 6 trajectories (4 edges)",
        "task": "CSTB gene pan-cancer multi-module end-to-end analysis",
        "modules": {
            "information_retrieval": {"query": "CSTB pan-cancer multi-omics", "databases": ["TCGA Pan-Cancer (33 cancer types)", "GDSC2", "cBioPortal"], "result": "Retrieved expression, survival, mutation, drug sensitivity across 33 cancer types"},
            "literature_analysis": {"key_findings": ["CSTB overexpressed in majority of cancers", "Significant prognostic value in 12/33 cancer types", "Associated with immune infiltration", "Potential drug target"], "cited_papers": 5},
        },
        "trajectories": {
            "correct":      {"label": "✅ 正确分析 / Correct", "file_name": "pan_correct.json", "is_correct": True},
            "error":        {"label": "⚠ 错误分析 / Error", "file_name": "pan_error.json", "is_correct": False},
            "edge_claim":   {"label": "🔬 边缘: 单变量声称独立预后 / Univariate claims independent", "file_name": "pan_edge_claim.json", "is_correct": False},
            "edge_epv":     {"label": "🔬 边缘: EPV<1 多变量Cox / EPV<1 multivariate Cox", "file_name": "pan_edge_epv.json", "is_correct": False},
            "edge_consist": {"label": "🔬 边缘: 表达-预后方向矛盾 / Direction conflict", "file_name": "pan_edge_consistency.json", "is_correct": False},
            "edge_purity":  {"label": "🔬 边缘: 肿瘤纯度未校正 / Purity not corrected", "file_name": "pan_edge_purity.json", "is_correct": False},
        },
        "color": "#0395D8",
    },
    "scrna": {
        "id": "scrna",
        "title": "Act 3 · 单细胞审计 / scRNA-seq Audit",
        "sub": "CellVoyager 真实 + 3 种肿瘤微环境 — 10 条轨迹 / 22 条规则 / 10 trajectories",
        "task": "Tumor single-cell RNA-seq analysis (CRC / NSCLC / Melanoma)",
        "modules": {
            "information_retrieval": {"query": "CRC/NSCLC/Melanoma tumor scRNA-seq 10X", "databases": ["GEO GSE132465 (CRC)", "GEO GSE131907 (NSCLC)", "GEO GSE115978 (Melanoma)"], "result": "Retrieved 3 .h5ad datasets: CRC (immune-infiltrated), NSCLC (immune-desert), Melanoma (immune-hot)"},
            "literature_analysis": {"key_findings": ["TME-specific analysis decisions validated", "Real CellVoyager output scored 29 (D5 修复后实测; 5 L0 detected)", "Audit engine detects methodological omissions", "Hand-crafted ideal trajectories scored 85.0"], "cited_papers": 12},
        },
        "trajectories": {
            "correct":         {"label": "✅ 正确分析 / Correct", "file_name": "scrna_correct.json", "is_correct": True},
            "error":           {"label": "⚠ 错误分析 / Error", "file_name": "scrna_error.json", "is_correct": False},
            "edge_nodoublet":  {"label": "🔬 边缘: 不做双联体检测 / No doublet detection", "file_name": "scrna_edge_nodoublet.json", "is_correct": False},
            "edge_default":    {"label": "🔬 边缘: 默认参数聚类无理由 / Default params no rationale", "file_name": "scrna_edge_default.json", "is_correct": False},
            "edge_singleanno": {"label": "🔬 边缘: 单方法注释 / Single-method annotation", "file_name": "scrna_edge_singleanno.json", "is_correct": False},
            "crc":             {"label": "🧬 CRC 免疫浸润型 （正确版） / CRC immune-infiltrated", "file_name": "scrna_crc_correct.json", "is_correct": True},
            "crc_error":       {"label": "🧬 CRC 免疫浸润型 （错误版） / CRC error version", "file_name": "scrna_crc_error.json", "is_correct": False},
            "nsclc":           {"label": "🧬 NSCLC 免疫沙漠型 （正确版） / NSCLC immune-desert", "file_name": "scrna_nsclc_correct.json", "is_correct": True},
            "melanoma":        {"label": "🧬 Melanoma 免疫热型 （正确版） / Melanoma immune-hot", "file_name": "scrna_melanoma_correct.json", "is_correct": True},
            "cellvoyager":     {"label": "🤖 CellVoyager 真实运行: Melanoma（含 5 个 L0）/ REAL agent run", "file_name": "scrna_melanoma_cellvoyager.json", "is_correct": False},
        },
        "color": "#8B0000",
    },
}

for k in ["act", "demo_mode", "trajectory", "human_overrides", "audit_result"]:
    if k not in st.session_state:
        st.session_state[k] = None

st.markdown("""<style>
.act-card { padding:16px; border-radius:10px; border:2px solid #c0c0c0; transition:all 0.2s; margin-bottom:6px; }
.act-card:hover { border-color:#666; transform:translateY(-2px); box-shadow:0 4px 12px rgba(0,0,0,0.1); }
.act-sel { border-color:#0395D8 !important; background:#0395D815 !important; box-shadow:0 0 0 3px rgba(149,216,0.2) !important; }
.traj-zone { background:#f5f5f5; padding:16px 20px; border-radius:12px; margin:10px 0; }
button { border-radius:8px !important; font-weight:bold !important; transition:all 0.2s !important; }
button:hover { transform:translateY(-1px) !important; box-shadow:0 2px 8px rgba(0,0,0,0.12) !important; }
small { font-size:0.9rem !important; color:#333 !important; }
p, li, label { font-size:1rem !important; color:#222 !important; }
h1 { font-size:2.2rem !important; } h2 { font-size:1.6rem !important; } h3 { font-size:1.3rem !important; }
.stCaption { color:#444 !important; }
div[data-testid="stRadio"] label { font-size:0.95rem !important; line-height:1.6 !important; }
</style>""", unsafe_allow_html=True)

# P-04 §4.1（机制演示身份声明）+ UID-08（降级可见）：本界面易被误读为产品面，
# 故必须显式声明自身身份并列出本期未展示的能力，不得以沉默省略代替声明。
# 置于标题之前，以免读者先读到标题再被补充声明纠正。
st.markdown(
    "<div style='padding:10px 14px;border-radius:8px;background:#f0f6ff;"
    "border-left:4px solid #0395D8;font-size:0.9rem;color:#333;margin-bottom:10px'>"
    "<b>本界面为机制演示</b>（用于沟通与教学）：演示审计如何判定与取证。"
    "它<b>不是产品面</b>，不代表 MVP 用户面、验收、运行证明或任何结案含义；"
    "界面上的分数是<b>决策等级的聚合</b>（审计评分），不是科学总分或质量排名。"
    "<br>本期未展示：结果多维分列（P-02 §4.7 四轴：inference_type / explanation_depth / "
    "scope / validation）· 唯一权威路径标识 · 有界使用 / 报告 / 复核 / 历史视图。"
    "</div>",
    unsafe_allow_html=True,
)

st.title("🔬 Bio-Audit: 生信 AI Agent 决策审计系统")
st.caption("当 AI Agent 自动做生信分析时 — 谁来检查它做对了没有? / Who verifies the science when AI Agents run bioinformatics autonomously?")
with st.expander("📖 评分体系 / Scoring System", expanded=False):
    st.markdown("| Level | 含义 | 说明 |\n|-------|------|------|\n| **3** | 正确 / Correct | 方法选择科学合理, 符合领域共识标准 |\n| **2** | 可接受 / Acceptable | 基本正确, 有更优选择或需注意的瑕疵 |\n| **1** | 有风险 / Risky | 方法选择值得商榷, 可能导致结论不可靠 |\n| **0** | 危险 / Dangerous | 方法选择将导致确信的错误结论 |")
st.divider()

st.subheader("选择演示 / Select Demo")
cols = st.columns(3)
for key, act in ACTS.items():
    i = list(ACTS.keys()).index(key)
    with cols[i]:
        sel = "act-sel" if st.session_state.act == key else ""
        st.markdown(f"<div class='act-card {sel}' style='border-left:5px solid {act['color']};min-height:90px'><b style='color:{act['color']};font-size:16px'>{act['title']}</b><br><small style='color:#444'>{act['sub']}</small></div>", unsafe_allow_html=True)
        if st.button(f"🔍 进入 / Enter", key=f"act_btn_{key}", use_container_width=True):
            st.session_state.act = key
            st.session_state.act_title = act["title"]
            st.session_state.act_task = act["task"]
            st.session_state.act_modules = act["modules"]
            st.session_state.act_color = act["color"]
            st.rerun()

if st.session_state.act:
    act = ACTS.get(st.session_state.act)
    if act:
        st.divider()
        ac = st.session_state.get("act_color", "#0395D8")
        st.markdown(f"<div style='padding:14px 18px;border-radius:10px;background:{ac}12;border:2px solid {ac}'><span style='font-size:20px;font-weight:bold;color:{ac}'>✅ 已选 / Selected: {st.session_state.act_title}</span></div>", unsafe_allow_html=True)
        st.caption(f"📋 任务 / Task: {st.session_state.act_task}")

        traj_opts = act.get("trajectories", {})
        if traj_opts:
            st.markdown("<div class='traj-zone'>", unsafe_allow_html=True)
            st.markdown("**选择待审计的轨迹 / Select Trajectory to Audit:**")
            keys = list(traj_opts.keys())
            choice = st.radio("", keys, format_func=lambda k: traj_opts[k]["label"], key=f"traj_sel_{st.session_state.act}", label_visibility="collapsed")
            st.markdown("</div>", unsafe_allow_html=True)
            if st.button("▶ 运行审计 / Run Audit", key=f"run_{st.session_state.act}", type="primary", use_container_width=True):
                traj_file = TRAJECTORIES_DIR / traj_opts[choice]["file_name"]
                traj_data = json.loads(traj_file.read_text(encoding="utf-8"))
                # B4: v2 轨迹为对象（version/provenance/decisions），v1 为裸数组 —— 统一取 decisions
                traj_decisions = traj_data if isinstance(traj_data, list) else traj_data["decisions"]
                st.session_state.demo_mode = choice
                st.session_state.is_correct_traj = traj_opts[choice].get("is_correct", False)
                st.session_state.trajectory = {"task": st.session_state.act_task, "modules": st.session_state.act_modules, "decisions": traj_decisions}
                st.session_state.human_overrides = {}
                st.session_state.audit_result = None
                st.switch_page("pages/02_audit.py")

st.divider()
with st.expander("📊 这个评分系统可信吗? / Is this scoring system trustworthy? — 独立验证证据 R0-R3", expanded=False):
    st.markdown("在构建上述 Demo 之前, 评分系统经过了 4 轮独立验证 (R0-R3)。/ The scoring system was validated through 4 rounds of independent testing before any demo was built.")
    vcols = st.columns(5)
    VD = [("R0 scRNA 真值锚定","Spearman ρ 0.9747 · τ_b 0.9487（仅 scRNA R0 工件落地，numpy 模拟 n=5）","PASS","#00B07C"),("R1 酵母基准","85 vs 0","PASS","#00B07C"),("R2 跨基因","零差异","PASS","#00B07C"),("R3A 合规","1/2","PARTIAL","#E8A000"),("R3B 权威案例","100%","PASS","#00B07C")]
    for i, (lb, mt, sts, cl) in enumerate(VD):
        with vcols[i]:
            st.markdown(f"**{lb}**")
            st.markdown(f"<span style='font-size:16px;font-weight:bold;color:{cl}'>{sts}</span>", unsafe_allow_html=True)
            st.caption(mt)
    st.caption("4位独立专家 x 2轮交叉审查 | 设计迭代 v1.0-v2.0-v2.1 / 4 independent experts x 2 review rounds")
st.caption("v2 单仓库（bio-audit-v2, B1）| 引擎 = fullflow-demo D5 修复后基线 | golden 20 轨迹 137 决策 0 差异")
