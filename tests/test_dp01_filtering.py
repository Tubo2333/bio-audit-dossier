"""DP-01 W2 red product-contract tests for the authorized public seam."""

from pathlib import Path
import sys


# Keep runner provenance local to this isolated workline; do not alter package/config files.
_ISOLATED_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ISOLATED_ROOT / "src"))

from bioaudit.api import audit_dp01_filtering


_COMPLETE_REQUEST = {
    "analysis_context": {
        "project_id": "project-1",
        "analysis_id": "analysis-1",
        "comparison": "case_vs_control",
        "data_type": "bulk_rnaseq",
        "audit_scope": "filtering",
        "intended_use": "scientific_analysis",
        "exclusions": "none — no explicit exclusions declared",
        "exclusions": "none — no explicit exclusions declared",
        "exclusions": "none — no explicit exclusions declared",
    },
    "decision_declaration": {
        "method": "low_count_filter",
        "threshold": 10,
        "sample_rule": "at_least_two_samples",
        "unit": "gene",
        "source": "declared",
    },
    "evidence_observations": [
        {"source_type": "declared", "verification": "unverified", "value": "low_count_filter", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000},
        {"source_type": "observed", "verification": "confirmed", "value": "low_count_filter", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000},
        {"source_type": "referenced", "verification": "confirmed", "value": "D1.2-DEG-001", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000},
    ],
    "integrity_metadata": {**{domain: {"state": "confirmed"} for domain in ("identity", "version", "source", "binding", "snapshot", "permission", "unique_authority")}, "material_ids": ["material-1"], "pending_targets": ["filtering-decision"]},
}

def _result(request):
    return audit_dp01_filtering(request, {})


def test_complete_context_reaches_named_dp01_seam():
    # Authorization package §4 lines 94-119 and §8 lines 189-208; W4 source
    # `DP01FilteringResult` lines 52-60 and adapter lines 141-151.
    result = _result(_COMPLETE_REQUEST)
    assert result.scope == "DP-01/filtering"
    assert result.judgment == "auditable"
    assert result.declaration is not None
    assert result.declaration.method == "low_count_filter"
    assert {item.source_type for item in result.evidence} == {"declared", "observed", "referenced"}
    assert result.next_actions == ()
    assert not hasattr(result, "level")
    assert not hasattr(result, "score")
    assert not hasattr(result, "authority")


def test_missing_critical_context_reaches_named_dp01_seam():
    # Authorization package §5 lines 139-143 and §6 lines 149-169; W4 source
    # `_fail_closed` lines 52-65.
    request = {**_COMPLETE_REQUEST, "analysis_context": {"project_id": "project-1"}}
    result = _result(request)
    assert result.scope == "DP-01/filtering"
    assert result.judgment == "not_auditable"
    assert result.declaration is None
    assert {action.action for action in result.next_actions} == {"request_evidence", "stop"}
    assert all(action.target in {"analysis_context", "current_attempt"} for action in result.next_actions)


def test_declaration_without_real_observation_reaches_named_dp01_seam():
    # Authorization package §3 lines 73-90; W4 source adapter lines 147-151.
    request = {**_COMPLETE_REQUEST, "evidence_observations": [
        {"source_type": "declared", "verification": "unverified", "value": "low_count_filter", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000},
    ]}
    result = _result(request)
    assert result.scope == "DP-01/filtering"
    assert result.judgment == "scientifically_limited"
    assert all(item.source_type != "observed" for item in result.evidence)
    assert [action.action for action in result.next_actions] == ["request_evidence"]
    assert result.next_actions[0].target == "evidence_observations"


def test_unknown_filtering_method_reaches_named_dp01_seam():
    # Authorization package §5 lines 125-143; W4 source adapter lines 97-109.
    request = {**_COMPLETE_REQUEST, "decision_declaration": {
        **_COMPLETE_REQUEST["decision_declaration"], "method": "unknown_filtering_method",
    }}
    result = _result(request)
    assert result.scope == "DP-01/filtering"
    assert result.judgment == "scientifically_limited"
    assert result.declaration is not None
    assert result.declaration.method == "unknown_filtering_method"
    assert {action.action for action in result.next_actions} == {"request_review", "submit_correction"}
    assert result.judgment != "scientifically_wrong"


def test_conflicting_declared_and_observed_evidence_reaches_named_dp01_seam():
    # Authorization package §5 lines 139-145; W4 source adapter lines 90-124.
    request = {**_COMPLETE_REQUEST, "evidence_observations": [
        {"source_type": "declared", "verification": "unverified", "value": "low_count_filter", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000},
        {"source_type": "observed", "verification": "conflicted", "value": "no_filter", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000},
    ]}
    result = _result(request)
    assert result.scope == "DP-01/filtering"
    assert result.judgment == "conflicted"
    assert {(item.source_type, item.value) for item in result.evidence} == {
        ("declared", "low_count_filter"),
        ("observed", "no_filter"),
    }
    assert {action.action for action in result.next_actions} == {"request_evidence", "request_review"}


def test_conflicted_observed_same_method_is_not_auditable():
    request = {**_COMPLETE_REQUEST, "evidence_observations": [
        {"source_type": "declared", "verification": "unverified", "value": "low_count_filter", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000},
        {"source_type": "observed", "verification": "conflicted", "value": "low_count_filter", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000},
    ]}
    result = _result(request)
    assert result.judgment != "auditable"
    assert result.judgment == "scientifically_limited"
    assert result.limitations
    assert any(action.action == "request_evidence" for action in result.next_actions)


def test_result_boundary_reaches_named_dp01_seam():
    # Authorization package §4 lines 109-119 and §8 lines 200-208; W4 source
    # `DP01FilteringResult` lines 52-60 exposes no analysis-level authority or
    # legacy score as formal output.
    result = _result(_COMPLETE_REQUEST)
    assert result.scope == "DP-01/filtering"
    assert not hasattr(result, "acceptance_snapshot")
    assert not hasattr(result, "release_decision")
    assert not hasattr(result, "lane")
    assert not hasattr(result, "level")
    assert not hasattr(result, "score")
    assert not hasattr(result, "authority")
