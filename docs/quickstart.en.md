# Quick Start

> Bio-Audit Dossier: a shippable, bounded, decision-level audit record for one named
> bulk RNA-seq differential-expression analysis. For the full project overview see the
> [README](https://github.com/Tubo2333/bio-audit-dossier#readme) (Chinese primary; English version
> [README.en.md](https://github.com/Tubo2333/bio-audit-dossier/blob/main/README.en.md)).
> Part of the [bio-audit](https://github.com/Tubo2333/bio-audit) system (audit engine and evaluation layer).
> [中文版](quickstart.html). Requires Python >= 3.10 (CI matrix: 3.10 / 3.12).

## 1. Install

```bash
git clone git@github.com:Tubo2333/bio-audit-dossier.git
cd bio-audit-dossier
pip install -e ".[dev,ui]"                 # core + test + thin UI
# or install from the lockfiles (reproducibility discipline):
pip install -r requirements.lock -r requirements-dev.lock && pip install -e .
```

## 2. One-minute start

```bash
# Audit one v2 trajectory (scoring consumes only `decisions`; version/provenance is metadata)
bio-audit run src/bioaudit/data/trajectories/v2/deg_correct.json

# Golden regression: 20 trajectories / 137 decisions vs the frozen baseline — must be 0 diff
bio-audit golden

# Single-decision audit (--act is required; disambiguates e.g. deg_method across paradigms)
bio-audit audit-decision decision.json --act scrna
```

## 3. Common commands

| Command | Purpose |
|---|---|
| `bio-audit run <trajectory.json>` | Audit one trajectory (report carries the engine/ruleset/ontology snapshot triple) |
| `bio-audit golden` | Golden regression (20/137, 0 diff required, exit 1 on drift) |
| `bio-audit audit-decision <json> --act <paradigm>` | Single-decision audit (B3 contract) |
| `bio-audit validate-ontology` | Ontology validator, three duties (coverage / semantics / conflicts) |
| `bio-audit ruleset-validate` | Ruleset three gates (manifest + conflict + golden) — mandatory for rule changes |
| `bio-audit benchmark-validate` | Benchmark four gates (manifest + contamination + coverage + golden) |
| `bio-audit benchmark-run` | Batch benchmark runner + power report |
| `bio-audit reward-validate` | Reward five gates (mapping / determinism / spike-in / ablation / golden) |
| `bio-audit reward <trajectory.json>` | Reward training signal (consumes final verdicts only) |
| `bio-audit parse-notebook <nb>` / `cross-validate <nb>` | M3 parsing / M1×M3 cross-validation |
| `python -m mcp.server` | Start the MCP server (agent integration); `--selfcheck` smoke test |
| `bio-audit migrate-trajectories` / `trajectory-validate` | Read-only v1→v2 migration / v2 schema validation |

## 4. Integration

- **Python API**: `run_audit` / `audit_decision` / `match_details`
  (pydantic validation + error codes + required `paradigm`) — see
  [API Contract](api-contract.html) (Chinese);
- **MCP**: stdio JSON-RPC, tools = `audit_decision` / `audit_trajectory` / `report` —
  see [MCP Contract](mcp-contract.html) (Chinese);
- **Streamlit shell**: `streamlit run ui/app.py` (calls `bioaudit.api` only).

## 5. Contributing rules & tasks

Rules are code: rule/task-set changes go through PRs gated by the three/four gates
(see [CONTRIBUTING.md](https://github.com/Tubo2333/bio-audit-dossier/blob/main/CONTRIBUTING.md));
scoring-path changes must keep golden at 0 diff (any drift must be explained item by item, C4).

## 6. Tests & regression

```bash
pytest                              # full suite (local / CI dual matrix)
bio-audit golden --json             # 0 diff; diff≠0 turns CI red = human confirmation gate
python scripts/generate_scrna_r0.py --output /tmp/r0.json   # R0 deterministic anchor (byte-identical to packaged)
```

## 7. More

- [Documentation index](index.html) ·
  [Design docs](specs/index.html) · [Releases](https://github.com/Tubo2333/bio-audit-dossier/releases) ·
  License: Apache-2.0
