# bio-audit-dossier

**A bounded, decision-level audit record, deliverable alongside a manuscript or dataset.**

This repository provides the **audit delivery surface** of the Bio-Audit system. It accepts the
methodological decision declarations and the execution observations of one named analysis,
returns a per-decision judgment together with its basis, its limits and its stop conditions,
and produces archivable deliverables. **The system does not assign a score, a ranking, or a
conclusion about scientific correctness.**

Part of the system: [bio-audit](https://github.com/Tubo2333/bio-audit) (audit engine, rule
library, evaluation sets and demonstration console).

---

## 1. What this repository addresses

Automated analysis pipelines can fail methodologically in three ways, none of which is
accompanied by a runtime error:

- **Declaration inconsistent with execution** — the submitter declares a method for which the
  execution record contains no corresponding operation;
- **Expected decision point absent** — a decision that the standard workflow for the given
  platform and paradigm should contain was neither executed nor declared;
- **Method inappropriate to context** — the method is incompatible with the selected threshold,
  sample rule or data structure, so the strength of the resulting conclusion is overstated.

These problems cannot be found by code review or by inspecting results; they require
verification, decision by decision, against domain methodological consensus. This system
structures that verification and makes its outcome deliverable, traceable and re-computable.

## 2. Audit target and scope

The audit target is **one named bulk RNA-seq differential-expression analysis**, comprising the
following five decision points:

| ID | Decision point | The question it answers |
|---|---|---|
| DP-01 | Low-expression gene filtering | Whether filtering was performed, on what threshold, and to what degree the evidence supports it |
| DP-02 | Normalization method | The adequacy of the selected method for the present analysis context, and its evidence |
| DP-03 | Differential-analysis method | The relevance of the method to the statistical question and the data structure |
| DP-04 | Multiple-testing correction | The correction method, its evidence and its limits |
| DP-05 | Significance and effect-size thresholds | The boundary of interpretability of the thresholds (not a judgment of causality or biological importance) |

The five decision points are mutually independent: they may not be merged, and evidence from one
may not be used to cover a gap in another.

**Not covered in this version**: other analysis types (single-cell, pan-cancer, DEG and others);
further decision points; public presentation of analysis-level structured results; complete
presentation of reviews and historical changes; runtime proof; deployment and package-level
closure. Each of these is a scheduling and authorization boundary rather than a removal of
capability; their place in the system is preserved (see §7).

## 3. Judgment model

For each decision point the system emits a **categorical judgment**, not a numerical score.
Three classes of failure are strictly separated in meaning:

| Judgment | Meaning | System behaviour |
|---|---|---|
| **auditable** | Key facts, context and evidence can be confirmed | States the supported range and the limits of that point |
| **scientifically inadequate** | The facts are largely clear, but the method or the evidence supports only a weaker conclusion | Retains the supportable part and states the limitation explicitly |
| **not auditable** | Key facts, context or evidence cannot be confirmed | Stops the affected judgment and the conclusions downstream of it; does not fill the gap with defaults, similar projects or historical records |
| **integrity / authority failure** | Failure of identity, version, source, binding, snapshot, permission or unique-authority relation | Isolates the affected material and preserves the item for adjudication; is not downgraded to ordinary scientific inadequacy |

**The provenance of evidence and its verification status are recorded separately and may not be
conflated**:

- Provenance type: `declared` (submitter's declaration) · `observed` (scripts, logs, parameters,
  artifacts) · `referenced` (rules, literature, protocols) · `derived` (system inference)
- Verification status: `confirmed` · `unverified` · `conflicted` · `not_applicable`

Hard constraints: `declared` does not equal `confirmed`; `derived` does not equal `observed`;
a rule or a publication does not equal an executed fact. In the absence of execution
observations, the audit reaches the **declaration level** only and may not claim that execution
has been confirmed.

## 4. Inputs

A submission comprises four layers:

1. **Analysis context** — project identifier, analysis identifier, comparison, data type and
   paradigm, audit scope, intended use;
2. **Decision declarations** — for each of the five decision points, the method, thresholds,
   sample rules, unit of operation and the provenance of the declaration;
3. **Evidence and observations** — declarations, script parameters, logs, notebooks, actual
   artifacts, and the rules and literature relied upon (each retaining its provenance type and
   verification status);
4. **Correction relation** — for a re-audit, a reference to the existing audit record and a
   statement of what has changed.

**Seven completeness domains** must each pass: `identity` · `version` · `source` · `binding` ·
`snapshot` · `permission` · `unique_authority`. Failure of any domain results in a
**per-decision** `not_auditable`; the batch layer validates type and shape only (top-level
structure, and a decision-point key set of exactly five), and a missing decision point is a
**batch-level rejection**, with no record written.

## 5. Outputs

Each audit produces three artifacts:

| Artifact | Description |
|---|---|
| `report.json` | The structured audit record |
| `report.html` | The review page: for the person responsible for the analysis to verify, with three reading depths (conclusion / reasoning / evidence chain) and a criteria panel |
| `deliverable.html` | The deliverable: a single-file HTML document with print styling, archivable alongside a manuscript or dataset |

The deliverable is subject to eight hard constraints: it may not become a second authority; it
may not synthesize the four axes into a single score; limitations and stop consequences may not
be omitted; the stated bounds of use must be reproduced exactly as recorded in the audit record
and may not be raised because the format is more formal; conclusion words such as
"pass / qualified / trustworthy / verified" may not appear; it must carry the generation instant
and a snapshot reference to the record it renders; it must declare that the Chinese layer is a
rendering-level translation and not the authoritative statement; and it may not substitute for
any claim of acceptance, release or package-level closure.

## 6. Permitted next actions

The system emits only the following four actions, each with its accompanying non-claim:

| Action | Meaning | Non-claim |
|---|---|---|
| `request_evidence` | Supply the specified evidence | ≠ the evidence has been accepted |
| `submit_correction` | Submit a new analysis or decision version | ≠ the correction has taken effect |
| `request_review` | Request scientific or governance review | ≠ the reviewer has approved |
| `stop` | Stop or abandon this round | ≠ the historical record has been deleted |

Supplying evidence or submitting a correction **does not overwrite** the original record: the
system establishes a new complete input snapshot and a new audit attempt, and preserves the
relation between the old and the new record.

## 7. Relationship to the rest of the system

This repository reuses the engine, rule library and decision ontology of
[bio-audit](https://github.com/Tubo2333/bio-audit), but **the audit record does not consume that
engine's aggregate scores, levels, evaluation scores or training signals**; those values remain
confined to the internal diagnostic and regression layers.

| | bio-audit (parent repository) | This repository |
|---|---|---|
| Surface | Engine, rule library, evaluation sets, demonstration console | Audit delivery surface, criteria chain, deliverable document, submission surface |
| Output | Trajectory scores, verdicts, dimension scores, benchmark, reward | Categorical judgments, basis, limits, bounds of use, controlled actions |
| Caliber | Quantifiable; for evaluation and regression | Not quantifiable; for bounded use only |

**The numerical calibers of the two repositories may not be mixed**: the parent repository's
scores do not constitute this repository's conclusions, and this repository's judgments do not
constitute the parent repository's scores.

This repository additionally contains a four-page interactive demonstration console (`demo/`),
which presents how the engine and the evaluation layer operate. **The scores and benchmarks
described by that console belong to the parent repository's caliber and are not part of this
repository's audit records.**

## 8. Quick start

```bash
pip install -e ".[dev,a1]"       # requires Python >= 3.10; the a1 extra is needed for the web submission form

# Submit one analysis and produce the three artifacts (written to var/ by default)
python scripts/submit_analysis.py --file <submission.json>

# Generate a complete example submission (including counterexamples, to verify fail-closed behaviour)
python scripts/make_example_submission.py --out-dir _tmp_a1

# Web submission form (local service, port 5050 by default)
python ui/a1_submit_app.py --port 5050
```

On Windows, `start-a1-web.bat` launches the submission form and `启动演示.bat` launches the
four-page demonstration console.

## 9. How the engineering is verified

```bash
python -m pytest -q                     # full test suite
python ui/verify_report.py              # artifact-level assertions for the review page (19 checks)
python ui/verify_deliverable.py         # artifact-level assertions for the deliverable (10 checks)
python scripts/check_rule_lifecycle.py  # rule-library lifecycle (manifest consistency / copy consistency / placeholder self-description / review window)
```

Every rule in the rule library carries its literature basis, and the persistent identifiers of
those bases have been verified item by item. The lifecycle check of the rule library is wired
into continuous integration.

## 10. Boundary statement

- This system does **not** issue an analysis score, a ranking, a total quality score or a
  confidence value;
- This system does **not** determine scientific correctness, and does not replace the judgment of
  a statistician, a reviewer or a domain expert;
- This system's judgments hold only within the project, analysis and audit scope that they
  declare;
- The deliverable is a **rendered snapshot** at one instant; it creates no new conclusion and
  grants no new authorization;
- Chinese is a rendering-level translation, not the authoritative statement.

## 11. License

[Apache-2.0](LICENSE).
