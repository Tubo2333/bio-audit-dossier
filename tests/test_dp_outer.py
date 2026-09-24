"""DP-OUTER — outer replay binding (C-03): non-public, one-way, verification-only.

T-01 §6.4: the binding must stay outside the public result meaning and outside
result authority; its only permitted role is one-way verification or constraint
of an already governed relationship. It must not create a result or a snapshot,
carry a result as an alternative, become a relation category, become a release or
lane authority, reverse the stage-package/snapshot direction, or be treated as
runtime/replay proof merely because it is present. Exactly one conceptual
acceptance binding (T-01 L338).

Runs via the project pytest `pythonpath=["src"]`.
"""

import inspect
import json
import pathlib

import pytest

from bioaudit.api import (
    dp_acceptance_snapshot_ref,
    dp_outer_binding_store,
    list_dp_outer_bindings,
    record_dp_outer_binding,
    render_dp_report,
)
from bioaudit.dp01_store import DP01JSONLStore
from bioaudit.dp_outer import (
    _ALLOWED_KEYS,
    DIRECTION,
    NON_CLAIM,
    STAGE_PREFIX,
    OuterReplayBindingRecord,
)

_ACCOUNT = "acceptance_snapshot:snapshot:outer-1"
_STAGE = "stage_package:stage:outer-1"


class _HistoryWithChange:
    """One recorded change, in the shape ``render_dp_report`` consumes."""

    def __init__(self, change):
        self.proposed_change = change
        self.earlier_accounts = ()


def _lines(store):
    """Committed ledger lines, for 'rejection must not write' assertions."""
    if not store.path.exists():
        return []
    return [line for line in store.path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _record(store, **kw):
    params = {
        "binding_id": "b1",
        "bound_account": _ACCOUNT,
        "stage_material_ref": _STAGE,
        "verification_statement": "the stage material was checked against the snapshot inputs",
        "verifier": "synthetic:verifier-1",
    }
    params.update(kw)
    return record_dp_outer_binding(store, **params)


@pytest.fixture
def binding_ledger(tmp_path):
    return dp_outer_binding_store(tmp_path / "outer.jsonl")


# 1. a valid binding is recorded with the fixed direction and verification-only flag
def test_valid_binding_recorded(binding_ledger):
    record = _record(binding_ledger)
    assert record.direction == DIRECTION
    assert record.verification_only is True
    again = binding_ledger.get("b1")
    assert again.bound_account == _ACCOUNT
    assert again.stage_material_ref == _STAGE
    assert again.verifier == "synthetic:verifier-1"
    assert [b.binding_id for b in list_dp_outer_bindings(binding_ledger)] == ["b1"]


# 2. CARDINALITY: at most one conceptual acceptance binding per account
def test_second_binding_for_same_account_is_rejected(binding_ledger, tmp_path):
    _record(binding_ledger)
    with pytest.raises(ValueError, match="at most one"):
        _record(binding_ledger, binding_id="b2")
    # fail-closed must not leave a dirty line behind
    assert len(_lines(binding_ledger)) == 1
    assert [b.binding_id for b in list_dp_outer_bindings(binding_ledger)] == ["b1"]
    # a different account may still have its own binding
    _record(binding_ledger, binding_id="b3",
            bound_account="acceptance_snapshot:snapshot:outer-2")
    assert len(_lines(binding_ledger)) == 2


# 3. ONE-WAY: the direction is fixed and cannot be reversed
def test_direction_cannot_be_changed(binding_ledger):
    with pytest.raises(ValueError, match="direction"):
        _record(binding_ledger, direction="acceptance_snapshot_to_stage_material")
    assert _lines(binding_ledger) == []


def test_stage_material_must_be_stage_material(binding_ledger):
    with pytest.raises(ValueError, match="stage_package"):
        _record(binding_ledger, stage_material_ref=_ACCOUNT)
    assert _lines(binding_ledger) == []


def test_stage_material_ref_is_not_constrained_on_the_stage_side(binding_ledger):
    """Cardinality is enforced on the ACCOUNT side only (documented, T-01 L365).

    One stage package may be referenced by bindings for different accounts; this
    test pins that documented scope so a future change to it is deliberate.
    """
    _record(binding_ledger)
    _record(binding_ledger, binding_id="b-other-account",
            bound_account="acceptance_snapshot:snapshot:outer-2")  # same _STAGE
    assert len(_lines(binding_ledger)) == 2


def test_constants_make_the_direction_structurally_one_way():
    """One-way is structural: the two authorized prefixes are disjoint.

    Asserted against the module CONSTANTS (not literals) so a prefix change cannot
    silently invalidate the guarantee.
    """
    import bioaudit.dp_change as dp_change

    account_prefix = dp_change.ACCOUNT_PREFIX
    assert not STAGE_PREFIX.startswith(account_prefix)
    assert not account_prefix.startswith(STAGE_PREFIX)


def test_stage_and_account_prefixes_are_disjoint(binding_ledger):
    """Reversal has no representation: a stage reference cannot carry the account
    prefix, so it can never also be the bound account."""
    import bioaudit.dp_change as dp_change

    account_prefix = dp_change.ACCOUNT_PREFIX
    with pytest.raises(ValueError, match="stage_package"):
        _record(binding_ledger, stage_material_ref=f"{account_prefix}snapshot:x")
    assert _lines(binding_ledger) == []


# 3b. the cardinality guard must run INSIDE the append lock (no TOCTOU window)
def test_cardinality_guard_runs_inside_the_lock(binding_ledger, monkeypatch):
    import contextlib

    state = {"locked": False, "checked_under_lock": None}
    real_lock = binding_ledger._append_lock          # noqa: SLF001 - contract probe
    real_check = binding_ledger._check_additional    # noqa: SLF001

    @contextlib.contextmanager
    def spy_lock():
        with real_lock():
            state["locked"] = True
            try:
                yield
            finally:
                state["locked"] = False

    def spy_check(payload, existing):
        state["checked_under_lock"] = state["locked"]
        return real_check(payload, existing)

    monkeypatch.setattr(binding_ledger, "_append_lock", spy_lock)
    monkeypatch.setattr(binding_ledger, "_check_additional", spy_check)
    _record(binding_ledger)
    assert state["checked_under_lock"] is True


# 3c. verification_statement must stay a bounded prose statement, so it cannot
#     smuggle a result payload in as "an alternative result" (§6.4 must-not 3)
def test_verification_statement_is_bounded_prose(binding_ledger):
    with pytest.raises(ValueError, match="verification_statement"):
        _record(binding_ledger,
                verification_statement="checked " + "x" * 600)
    for payload_like in ('{"validation": "complete"}', "result: [1, 2, 3]",
                         "line one\nline two"):
        with pytest.raises(ValueError, match="verification_statement"):
            _record(binding_ledger, verification_statement=payload_like)
    # every rejection above must have left the ledger empty
    assert _lines(binding_ledger) == []


def test_bound_account_must_be_an_account(binding_ledger):
    with pytest.raises(ValueError, match="invalid outer binding: bound_account"):
        _record(binding_ledger, bound_account="report:some-report")


# 4. NO PROMOTION: proof/result/authority/lane keys are rejected (both sides)
def test_proof_style_keys_are_rejected_on_read_back(binding_ledger):
    """Read-back side of the closed schema.

    These payloads are hand-written into the ledger, so validation happens on the
    read-back path; the construction side is structurally closed instead (see
    ``test_to_dict_can_only_produce_closed_schema_keys``). The legitimate record
    written first must stay readable after the bad lines are appended.
    """
    _record(binding_ledger)
    for key in ("proof", "hash", "signature", "replay_result", "runtime_proof",
                "trust_root", "result", "structured_strength_result",
                "acceptance_snapshot", "lane", "release", "score"):
        payload = {
            "binding_id": f"b-{key}",
            "bound_account": "acceptance_snapshot:snapshot:outer-3",
            "stage_material_ref": "stage_package:stage:outer-3",
            "verification_statement": "checked",
            "verifier": "synthetic:verifier-1",
            "direction": DIRECTION,
            "verification_only": True,
        }
        payload[key] = "forbidden"
        with open(binding_ledger.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, sort_keys=True) + "\n")
        with pytest.raises(ValueError, match="forbidden key present"):
            binding_ledger.get(f"b-{key}")
    # the emitted JSON for the legitimate record is untouched by those bad lines
    first_line = json.loads(_lines(binding_ledger)[0])
    assert first_line["binding_id"] == "b1"
    assert set(first_line) == {*_ALLOWED_KEYS}


def test_to_dict_can_only_produce_closed_schema_keys():
    """Construction-side closure: a record cannot even express a forbidden key."""
    record = OuterReplayBindingRecord(
        binding_id="b-shape", bound_account=_ACCOUNT, stage_material_ref=_STAGE,
        verification_statement="checked", verifier="synthetic:verifier-1",
        version_snapshot={"engine_version": "0"})
    assert set(record.to_dict()) == _ALLOWED_KEYS
    assert set(record.to_dict()) & {"proof", "result", "lane", "release"} == set()


# 5. NOT PROOF: verification_only must be true; the non-claim is stated
def test_verification_only_must_be_true(binding_ledger):
    with pytest.raises(ValueError, match="verification_only"):
        _record(binding_ledger, verification_only=False)


def test_non_claim_is_stated():
    assert "proof" in NON_CLAIM.lower()
    assert "presence" in NON_CLAIM.lower() or "merely" in NON_CLAIM.lower()


def test_module_exposes_no_authority_route():
    import bioaudit.dp_outer as outer

    for forbidden in ("to_snapshot", "as_authority", "as_result", "to_authority",
                      "as_acceptance_snapshot", "proof_of"):
        assert not hasattr(outer, forbidden), forbidden


# 6. NON-PUBLIC: a binding can never be consumed as authority
def test_binding_cannot_be_used_as_authority(binding_ledger):
    record = _record(binding_ledger)
    with pytest.raises(TypeError):
        dp_acceptance_snapshot_ref(record)


# 7. named fields are mandatory
def test_named_fields_required(binding_ledger):
    for field in ("verifier", "verification_statement"):
        with pytest.raises(ValueError, match=field):
            _record(binding_ledger, **{field: ""})


# 8. ledger discipline
def test_duplicate_binding_id_rejected(binding_ledger):
    _record(binding_ledger)
    with pytest.raises(ValueError, match="duplicate"):
        _record(binding_ledger, bound_account="acceptance_snapshot:snapshot:outer-9")


def test_cross_line_duplicate_rejected(binding_ledger):
    record = _record(binding_ledger)
    with open(binding_ledger.path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record.to_dict(), sort_keys=True) + "\n")
    with pytest.raises(ValueError):
        binding_ledger.get("b1")


def test_bad_json_line_reports_line_number(binding_ledger):
    _record(binding_ledger)
    with open(binding_ledger.path, "a", encoding="utf-8") as fh:
        fh.write("{not-json}\n")
    with pytest.raises(ValueError, match="line"):
        binding_ledger.get("b1")


def test_version_mismatch_rejected(binding_ledger):
    record = _record(binding_ledger)
    payload = record.to_dict()
    payload["binding_id"] = "b-ver"
    payload["bound_account"] = "acceptance_snapshot:snapshot:outer-4"
    # an impossible version so a matching runtime version cannot make this pass
    payload["version_snapshot"] = {**payload["version_snapshot"],
                                   "engine_version": "9999.9999.9999"}
    with open(binding_ledger.path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, sort_keys=True) + "\n")
    with pytest.raises(ValueError):
        binding_ledger.get("b-ver")


def test_append_rejects_a_record_without_a_version(binding_ledger):
    """No silent version fill: a record must carry its version binding."""
    bare = OuterReplayBindingRecord(
        binding_id="b-bare", bound_account=_ACCOUNT, stage_material_ref=_STAGE,
        verification_statement="checked", verifier="synthetic:verifier-1")
    with pytest.raises(ValueError, match="version"):
        binding_ledger.append(bare)
    assert _lines(binding_ledger) == []


def test_returned_record_cannot_be_tampered_with_nested(binding_ledger):
    """``frozen=True`` locks attribute rebinding only; the nested version mapping
    is frozen too, so a returned record's content cannot be mutated in place
    (parity with dp_roles.RoleRecord)."""
    record = _record(binding_ledger)
    for returned in (record, binding_ledger.get("b1")):
        with pytest.raises(TypeError):
            returned.version_snapshot["engine_version"] = "TAMPERED"
    with pytest.raises(AttributeError):  # FrozenInstanceError is an AttributeError
        record.version_snapshot = {}
    assert binding_ledger.get("b1").version_snapshot["engine_version"] != "TAMPERED"


# 9b. non-public is regression-protected at the SOURCE level (weaker guard, kept
#     alongside the behavioural guard in scenario 9 below)
def test_report_layer_does_not_import_the_outer_binding():
    import bioaudit.dp_report as dp_report

    source = pathlib.Path(dp_report.__file__).read_text(encoding="utf-8")
    assert "dp_outer" not in source


def test_api_accessors_expose_explicit_parameters():
    """Standards S4: the accessor must not degrade to *args/**kwargs."""
    from bioaudit.api import record_dp_outer_binding as accessor

    parameters = inspect.signature(accessor).parameters
    for name in ("binding_id", "bound_account", "stage_material_ref",
                 "verification_statement", "verifier"):
        assert name in parameters, name
        assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY, name
        assert parameters[name].annotation is not inspect.Parameter.empty, name
    assert parameters["store"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD


def test_append_lock_present(binding_ledger):
    assert hasattr(binding_ledger, "_append_lock")


# 9. NON-PUBLIC in the product surface: the report never contains binding material
def test_report_never_contains_binding_material(tmp_path):
    from bioaudit.api import (
        audit_dp_register,
        build_dp_acceptance_snapshot,
        dp_authority_store,
        dp_role_store,
        record_dp_change,
        record_dp_role,
    )
    from bioaudit.dp_change import DPChangeStore

    integrity = {**{d: {"state": "confirmed"} for d in
                    ("identity", "version", "source", "binding", "snapshot",
                     "permission", "unique_authority")},
                 "material_ids": ["m"], "pending_targets": ["t"]}
    context = {"project_id": "p1", "analysis_id": "a1", "comparison": "c_vs_t",
               "data_type": "bulk_rnaseq", "audit_scope": "s", "intended_use": "u", "exclusions": "none — no explicit exclusions declared"}

    def decl(m):
        return {"method": m, "threshold": 10, "sample_rule": "at_least_two_samples",
                "unit": "gene", "source": "declared"}

    def obs(v):
        return {"source_type": "observed", "verification": "confirmed", "value": v, "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000}

    pairs = [("filtering", "low_count_filter"), ("normalization", "tmm"),
             ("differential_method", "deseq2"), ("multiple_testing_correction", "BH"),
             ("significance_threshold", "padj_0.05")]
    request = {"audit_id": "outer-1", "analysis_context": context,
               "integrity_metadata": integrity,
               "decision_points": {k: {"decision_declaration": decl(m),
                                       "evidence_observations": [obs(m)]}
                                   for k, m in pairs}}
    point_store = DP01JSONLStore(tmp_path / "points.jsonl")
    authority_store = dp_authority_store(tmp_path / "authority.jsonl")
    register = audit_dp_register(request, point_store)
    snapshot = build_dp_acceptance_snapshot(request, point_store, authority_store,
                                            register=register)
    binding = _record(store=dp_outer_binding_store(tmp_path / "o.jsonl"),
                      bound_account=f"acceptance_snapshot:{snapshot.snapshot_id}")

    # A real role record and a real change record: all three report inputs are
    # exercised so "the report never shows binding material" is not vacuously true.
    # The role actor is deliberately a DIFFERENT name than the binding's verifier,
    # so the assertions below distinguish "this string is absent" from "this string
    # is legitimately present".
    role_record = record_dp_role(
        dp_role_store(tmp_path / "roles.jsonl"), record_id="r1", role="reviewed",
        actor="synthetic:reviewer-1",
        target=f"acceptance_snapshot:{snapshot.snapshot_id}",
        scope="outer binding guard")
    change_store = DPChangeStore(tmp_path / "changes.jsonl")
    change_record = record_dp_change(
        change_store, change_id="c1",
        from_account=f"acceptance_snapshot:{snapshot.snapshot_id}",
        to_account="acceptance_snapshot:snapshot:later", change_classes=["intended_use"],
        differences=[{"change_class": "intended_use", "path": "intended_use",
                      "before": "bounded", "after": "bounded (revised)"}],
        reason="bounded follow-up", submitter="synthetic:author-1",
        requires_adjudication=False)

    report = render_dp_report(register, authority=snapshot, roles=[role_record],
                              history=_HistoryWithChange(change_record))

    def strings(node):
        if isinstance(node, str):
            yield node
        elif isinstance(node, dict):
            for value in node.values():
                yield from strings(value)
        elif isinstance(node, (list, tuple)):
            for value in node:
                yield from strings(value)

    values = list(strings(report))
    # the roles/history paths WERE rendered (otherwise this test could pass vacuously)
    assert role_record.actor in values
    assert any(role_record.target in v for v in values)
    assert report["history_and_change"]["proposed_change"]["submitter"] == \
        change_record.submitter
    # ...and no binding material leaked through any of the three inputs
    assert not any(binding.binding_id in v for v in values)
    assert not any(binding.verifier in v for v in values)
    assert not any(binding.stage_material_ref in v for v in values)
    assert not any(binding.verification_statement in v for v in values)
    with pytest.raises(TypeError):
        render_dp_report(register, authority=binding)
    with pytest.raises(TypeError):
        render_dp_report(register, authority=snapshot, bindings=[binding])
