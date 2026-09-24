"""API 单一入口包（v1 蓝图：run_audit / audit_decision / reward；B3 契约完成）。

B3（2026-08-14）：输入 pydantic 校验 + 错误码体系 + audit_decision 必填 paradigm +
human_overrides -1..4 校验；契约文档 docs/api-contract.md。

**面（surface）澄清**：本模块是**内部 Python 面**，不是产品/用户面。DP 系列的
seam 一并挂在这里，其中 `*_outer_binding*`（外层校验绑定，T-01 §6.4 / P-03 row143）
**永不进入产品面**：报告（`render_dp_report`）不呈现绑定内容，喂给报告/权威 seam
的绑定对象按形状 fail-closed（TypeError）。此处暴露三个访问器是为了让调用方与
测试不必伸手进实现模块，不构成任何"取权威/取快照"路径。
"""

from bioaudit.api.audit import run_audit, audit_decision, match_details
from bioaudit.errors import BioAuditError, ErrorCode


def audit_dp01_filtering(*args, **kwargs):
    from bioaudit.dp01_adapter import audit_dp01_filtering as _audit_dp01_filtering
    return _audit_dp01_filtering(*args, **kwargs)


def replay_dp01_filtering(store, audit_id):
    """Replay one persisted DP-01 audit through its recorded input snapshot.

    Upper-layer seam over ``DP01JSONLStore.replay``: supplies the pure adapter
    as the replay function and returns the replayed ``DP01AuditRecord``.
    Failures propagate fail-closed: ValueError on version or replay mismatch,
    KeyError on a missing audit_id.
    """
    from bioaudit.dp01_adapter import _audit_dp01_filtering as _replay_fn
    return store.replay(audit_id, _replay_fn)


def audit_dp02_normalization(*args, **kwargs):
    from bioaudit.dp02_adapter import audit_dp02_normalization as _audit_dp02_normalization
    return _audit_dp02_normalization(*args, **kwargs)


def replay_dp02_normalization(store, audit_id):
    """Replay one persisted DP-02 normalization audit via the shared ledger.

    Supplies the DP-02 pure adapter as the replay function; failures propagate
    fail-closed (ValueError on version/replay mismatch, KeyError on missing id).
    """
    from bioaudit.dp02_adapter import _audit_dp02_normalization as _replay_fn
    return store.replay(audit_id, _replay_fn)


def audit_dp03_method(*args, **kwargs):
    from bioaudit.dp03_adapter import audit_dp03_method as _audit_dp03_method
    return _audit_dp03_method(*args, **kwargs)


def replay_dp03_method(store, audit_id):
    """Replay one persisted DP-03 differential-analysis method audit.

    Supplies the DP-03 pure adapter as the replay function; failures propagate
    fail-closed (ValueError on version/replay mismatch, KeyError on missing id).
    """
    from bioaudit.dp03_adapter import _audit_dp03_method as _replay_fn
    return store.replay(audit_id, _replay_fn)


def audit_dp04_correction(*args, **kwargs):
    from bioaudit.dp04_adapter import audit_dp04_correction as _audit_dp04_correction
    return _audit_dp04_correction(*args, **kwargs)


def replay_dp04_correction(store, audit_id):
    """Replay one persisted DP-04 multiple-testing correction audit.

    Supplies the DP-04 pure adapter as the replay function; failures propagate
    fail-closed (ValueError on version/replay mismatch, KeyError on missing id).
    """
    from bioaudit.dp04_adapter import _audit_dp04_correction as _replay_fn
    return store.replay(audit_id, _replay_fn)


def audit_dp05_threshold(*args, **kwargs):
    from bioaudit.dp05_adapter import audit_dp05_threshold as _audit_dp05_threshold
    return _audit_dp05_threshold(*args, **kwargs)


def replay_dp05_threshold(store, audit_id):
    """Replay one persisted DP-05 significance/effect-size threshold audit.

    Supplies the DP-05 pure adapter as the replay function; failures propagate
    fail-closed (ValueError on version/replay mismatch, KeyError on missing id).
    """
    from bioaudit.dp05_adapter import _audit_dp05_threshold as _replay_fn
    return store.replay(audit_id, _replay_fn)


def audit_dp_register(*args, **kwargs):
    """Five-point register calling surface (MVP assembly, first product-facing step).

    Runs the five point-local public seams over the shared ledger from one
    assembly request and returns a plain per-scope register view preserving
    per-point meaning. The register is NOT a second result-authority container
    and NOT an acceptance/release; fail-closed on malformed shape.
    """
    from bioaudit.dp_assembly import audit_dp_register as _audit_dp_register
    return _audit_dp_register(*args, **kwargs)


def render_dp_report(register, *, authority=None, roles=None, history=None, lane=None, now=None):
    """Five-point register report layer (MVP report — first product-facing step).

    Pure function that renders the register view into an ordered explanatory
    report following the P-01 §7 / P-02 §6.2 meaning order. The report is
    explanatory and NOT a second result-authority container / NOT acceptance;
    it contains no total score / winner / summary judgment.

    ``authority`` (optional): an already-loaded authority object; the report then
    names which result is authoritative (id + basis + version binding) and states
    that it is neither that authority nor a second container.
    ``roles`` (optional): already-loaded role records; the report then names who
    authored / independently reviewed / adjudicated / receipted, and within what
    scope. With no roles supplied it states explicitly that none are recorded.
    ``history`` (optional): already-loaded earlier accounts and an optional
    proposed change; earlier accounts are marked historical-only and a proposed
    change shows the proposed/current difference plus the adjudicating authority.
    ``lane`` (optional): already-loaded lane decision record(s); the report then
    states the requested and decided use/release ceiling, the reasons the facts
    support no higher one, the binding restrictions, and what evidence would be
    required next. It is a ceiling, never a scientific result or a release.
    """
    from bioaudit.dp_report import render_dp_report as _render_dp_report
    return _render_dp_report(register, authority=authority, roles=roles,
                             history=history, lane=lane, now=now)


def dp_change_store(path):
    """Open the append-only change ledger (own file, closed schema).

    Change records relate two accounts; they are explanatory and never a result
    authority, and no rollback/migration mechanism is implemented.
    """
    from bioaudit.dp_change import DPChangeStore
    return DPChangeStore(path)


def record_dp_change(*args, **kwargs):
    """Record one bounded change relation between a prior and a later account."""
    from bioaudit.dp_change import record_change as _record_change
    return _record_change(*args, **kwargs)


def list_dp_changes(*args, **kwargs):
    """List recorded change relations."""
    from bioaudit.dp_change import list_changes as _list_changes
    return _list_changes(*args, **kwargs)


def dp_outer_binding_store(path: str):
    """Open the append-only outer-binding ledger (own file, closed schema).

    NOTE: "api" here is the internal Python surface, NOT the product surface. The
    outer binding is non-public, one-way and verification-only (T-01 §6.4): it is
    never rendered in the report, and it can never become a result, a snapshot or
    an authority.
    """
    from bioaudit.dp_outer import DPOuterBindingStore
    return DPOuterBindingStore(path)


def record_dp_outer_binding(store, *, binding_id: str, bound_account: str,
                            stage_material_ref: str, verification_statement: str,
                            verifier: str, direction: str | None = None,
                            verification_only: bool = True):
    """Record one non-public, one-way, verification-only outer binding."""
    from bioaudit.dp_outer import DIRECTION, record_outer_binding as _record_outer_binding
    return _record_outer_binding(
        store, binding_id=binding_id, bound_account=bound_account,
        stage_material_ref=stage_material_ref,
        verification_statement=verification_statement, verifier=verifier,
        direction=DIRECTION if direction is None else direction,
        verification_only=verification_only)


def list_dp_outer_bindings(store):
    """List outer bindings (internal surface only; never user-facing)."""
    from bioaudit.dp_outer import list_outer_bindings as _list_outer_bindings
    return _list_outer_bindings(store)


def dp_lane_store(path: str):
    """Open the append-only lane-decision ledger (own file, closed schema).

    A lane is a governed use/release ceiling under the existing closed domain
    (``research-draft``/``candidate``/``validated``/``production``). Recording a
    decision is not a release, not a deployment, and not a promotion: this
    module implements no release machinery, no transition and no replay.
    """
    from bioaudit.dp_lane import DPLaneStore
    return DPLaneStore(path)


def record_dp_lane_decision(lane_store, **kwargs):
    """Record one lane decision from evidenced facts, failing closed on the rest.

    The decided lane is DERIVED from the facts and clamped to the evidenced ceiling
    (C-04 §8.10) — a request for a higher lane does not raise; it simply lands at the
    ceiling while the record keeps both values. What does fail closed is claiming an
    authorising outcome where the facts establish no valid release or lane
    conclusion (C-04 §6 rows 1-2): that raises, and the caller must record
    ``rejected`` or ``return_for_evidence`` instead.

    The record stores facts, not conclusions: reading it back recomputes the ceiling,
    the currency and whether it authorises, so a hand-edited ledger line cannot buy
    a lane the evidence does not support.
    """
    from bioaudit.dp_lane import record_lane_decision as _record_lane_decision
    return _record_lane_decision(lane_store, **kwargs)


def list_dp_lane_decisions(lane_store, acceptance_snapshot_ref: str | None = None):
    """List lane decisions, optionally restricted to one acceptance snapshot.

    Every record carries its own stored write time (``recorded_at``), so the caller
    sees the same times the report does.
    """
    if acceptance_snapshot_ref is None:
        return lane_store.all()
    return lane_store.for_snapshot(acceptance_snapshot_ref)


def derive_dp_strength(*args, **kwargs):
    """Four-axis structured result VIEW (MVP combined-account meaning).

    Pure, view-only function that derives the combined-account four-axis meaning
    (inference_type / explanation_depth / scope / validation) from the register.
    It is NOT a persisted carrier / NOT a second result-authority container, and
    contains no scalar / ranking / lane / confidence.
    """
    from bioaudit.dp_strength import derive_dp_strength as _derive_dp_strength
    return _derive_dp_strength(*args, **kwargs)


def build_dp_acceptance_snapshot(assembly_request, point_store, authority_store, *,
                                 snapshot_id: str | None = None, register=None):
    """Build the sole result-authority snapshot (inherited C-03 route).

    ``stage_package`` (pre-snapshot input) → ``acceptance_snapshot`` containing
    exactly one embedded ``structured_strength_result``. Persists to its own
    authority ledger; forbidden shortcuts (second snapshot / second result /
    copied result / alternative carrier / ``structured_strength_result_ref`` /
    back-reference) fail closed.

    Passing an already-built ``register`` reuses it instead of re-running the
    point registration (so register and snapshot compose in the natural order);
    it is verified per point against the persisted point records.
    """
    from bioaudit.dp_authority import build_acceptance_snapshot as _build
    return _build(assembly_request, point_store, authority_store,
                  snapshot_id=snapshot_id, register=register)


def dp_acceptance_snapshot_ref(*args, **kwargs):
    """Return the ONE-WAY ``acceptance_snapshot_ref`` for a snapshot.

    Exposes the snapshot id and version binding only — no result content, no
    second container path. This is the only route by which a later release
    decision may reach result authority.
    """
    from bioaudit.dp_authority import acceptance_snapshot_ref as _ref
    return _ref(*args, **kwargs)


def dp_authority_store(path):
    """Open the append-only authority ledger (own file, closed schema).

    Exposed through the public seam so callers (and tests) do not reach into the
    implementation module directly.
    """
    from bioaudit.dp_authority import DPAuthorityStore
    return DPAuthorityStore(path)


def dp_role_store(path: str):
    """Open the append-only role+scope ledger (own file, closed schema).

    Role records (authored / reviewed / receipted / adjudicated) are explanatory
    and can never become result authority.
    """
    from bioaudit.dp_roles import DPRoleStore
    return DPRoleStore(path)


def record_dp_role(role_store, *, record_id: str, role: str, actor: str,
                   target: str, scope: str, note: str | None = None,
                   receipt_kind: str | None = None):
    """Record that a role was exercised: role + named actor + named target + named scope.

    Records only — the system does not conduct review or adjudicate. A
    ``receipted`` record requires an administrative ``receipt_kind``; no other
    role may carry one (a receipt is never substantive review).
    """
    from bioaudit.dp_roles import record_role as _record_role
    return _record_role(role_store, record_id=record_id, role=role, actor=actor,
                        target=target, scope=scope, note=note,
                        receipt_kind=receipt_kind)


def list_dp_roles(role_store, target: str | None = None):
    """List role+scope records, optionally filtered to one named target."""
    from bioaudit.dp_roles import list_roles as _list_roles
    return _list_roles(role_store, target=target)


__all__ = ["run_audit", "audit_decision", "match_details", "audit_dp01_filtering", "replay_dp01_filtering", "audit_dp02_normalization", "replay_dp02_normalization", "audit_dp03_method", "replay_dp03_method", "audit_dp04_correction", "replay_dp04_correction", "audit_dp05_threshold", "replay_dp05_threshold", "audit_dp_register", "render_dp_report", "derive_dp_strength", "build_dp_acceptance_snapshot", "dp_acceptance_snapshot_ref", "dp_authority_store", "dp_role_store", "record_dp_role", "list_dp_roles", "dp_change_store", "record_dp_change", "list_dp_changes", "dp_outer_binding_store", "record_dp_outer_binding", "list_dp_outer_bindings", "dp_lane_store", "record_dp_lane_decision", "list_dp_lane_decisions", "BioAuditError", "ErrorCode"]
