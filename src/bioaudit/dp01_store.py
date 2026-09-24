"""Explicit append-only JSONL persistence for DP-01 audits."""
from __future__ import annotations
import copy, json, os
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping
import math
from bioaudit.report import current_snapshot
from datetime import datetime

from bioaudit.dp01_models import DP01_EVIDENCE_ROLES, _freeze, _parse_affected_evidence, DP01_INPUT_FORMAT_VERSION, DP01_ADAPTER_VERSION, DP01AttemptChange, DP01Change, DP01ChangeSummary, DP01FilteringResult, EvidenceItem, FilteringDeclaration, ControlledNextAction, DP01Finding, DP01Limitation, DP01EvidenceGap, DP01LocalContribution, DP01_JUDGMENTS, DP01_ACTIONS, DP01_FINDING_KINDS, DP01_EVIDENCE_STATES, DP01_FINDING_STATES, DP01_LIMITATION_KINDS, DP01_GAP_KINDS, DP01_CONTRIBUTION_STATUSES, DP01_EVIDENCE_SOURCE_TYPES

def _identity_neutral(result):
    """Return a comparison view of a DP01FilteringResult excluding audit identity."""
    return DP01FilteringResult(
        result.scope, _plain(result.analysis_context), result.declaration, result.evidence,
        result.diagnostic_explanation, result.matched_rule_ids, result.judgment, result.next_actions,
        result.findings, result.limitations, result.evidence_gaps, result.local_contribution,
    )


def _thaw(value):
    if isinstance(value, Mapping): return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)): return [_thaw(item) for item in value]
    return value


def _current_version_snapshot() -> Mapping[str, str]:
    snapshot = current_snapshot().as_dict()
    snapshot["input_format_version"] = DP01_INPUT_FORMAT_VERSION
    snapshot["adapter_version"] = DP01_ADAPTER_VERSION
    return snapshot


@dataclass(frozen=True)
class DP01AuditRecord:
    audit_id: str
    input_snapshot: Mapping[str, Any]
    result: DP01FilteringResult
    parent_audit_id: str | None = None
    change: DP01AttemptChange | None = None
    operation: str | None = None
    change_summary: DP01ChangeSummary = DP01ChangeSummary()
    version_snapshot: Mapping[str, str] = field(default_factory=_current_version_snapshot)
    source_version_snapshot: Mapping[str, str] | None = None
    def __post_init__(self):
        object.__setattr__(self, "input_snapshot", _freeze(copy.deepcopy(_thaw(self.input_snapshot))))
        vs = dict(self.version_snapshot)
        required_version_keys = {"ruleset_version", "ontology_version", "engine_version", "input_format_version", "adapter_version"}
        if set(vs) != required_version_keys or not all(isinstance(v, str) for v in vs.values()):
            raise ValueError("invalid DP-01 version snapshot")
        object.__setattr__(self, "version_snapshot", _freeze(vs))
        if self.source_version_snapshot is not None:
            svs = dict(self.source_version_snapshot)
            if set(svs) != required_version_keys or not all(isinstance(v, str) for v in svs.values()):
                raise ValueError("invalid DP-01 source version snapshot")
            object.__setattr__(self, "source_version_snapshot", _freeze(svs))
        if self.parent_audit_id is None and self.change is not None: raise ValueError("first audit cannot have change metadata")
        if self.parent_audit_id is not None and (not isinstance(self.parent_audit_id, str) or not self.parent_audit_id or self.change is None): raise ValueError("child audit requires parent and change metadata")
        if self.operation not in (None, "re_audit"): raise ValueError("invalid DP-01 operation")
        if self.operation == "re_audit" and self.source_version_snapshot is None: raise ValueError("re-audit requires source version snapshot")
        if self.operation != "re_audit" and self.source_version_snapshot is not None: raise ValueError("source version snapshot requires re-audit")
        if self.operation == "re_audit" and self.parent_audit_id is None: raise ValueError("re-audit requires parent")

def _plain(value):
    if isinstance(value, Mapping):
        out = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("DP-01 mapping keys must be strings")
            out[key] = _plain(item)
        return out
    if isinstance(value, (tuple,list)): return [_plain(v) for v in value]
    if isinstance(value,(str,int,bool)) or value is None: return value
    if isinstance(value, float):
        if not math.isfinite(value): raise ValueError("non-finite float is unsupported in DP-01 JSONL")
        return value
    raise TypeError(f"unsupported value for DP-01 serialization: {type(value).__name__}")

def _result_to_dict(r):
    d = r.declaration.to_dict() if r.declaration else None
    # Evidence is persisted with its FULL qualification (P-02 §4.1): the ledger is
    # the durable record, so dropping the freshness window or the provenance here
    # would lose exactly what a later reader needs to judge the item.
    evidence = []
    for e in r.evidence:
        item = {"source_type": e.source_type, "verification": e.verification,
                "value": _plain(e.value),
                "observed_at": e.observed_at.isoformat(timespec="seconds"),
                "valid_for_seconds": e.valid_for_seconds,
                "evidence_role": e.evidence_role}
        if e.provenance is not None:
            item["provenance"] = e.provenance
        if e.provenance_subject is not None:
            item["provenance_subject"] = e.provenance_subject
        if e.supports is not None:
            item["supports"] = e.supports
        evidence.append(item)
    out = {"scope":r.scope,"analysis_context":_plain(r.analysis_context),"declaration":d,"evidence":evidence,"diagnostic_explanation":r.diagnostic_explanation,"matched_rule_ids":list(r.matched_rule_ids),"judgment":r.judgment,"next_actions":[a.to_dict() for a in r.next_actions],"findings":[x.to_dict() for x in r.findings],"limitations":[x.to_dict() for x in r.limitations],"evidence_gaps":[x.to_dict() for x in r.evidence_gaps],"local_contribution":r.local_contribution.to_dict()}
    if r.audit_id is not None:
        out["audit_id"] = r.audit_id
    if r.version_snapshot is not None:
        out["version_snapshot"] = _plain(r.version_snapshot)
    return out

def _result_from_dict(v):
    req={"scope","analysis_context","declaration","evidence","diagnostic_explanation","matched_rule_ids","judgment","next_actions","findings","limitations","evidence_gaps","local_contribution"}
    optional={"audit_id","version_snapshot"}
    if not isinstance(v,dict) or not req <= set(v) or not set(v) <= req | optional:
        raise ValueError("invalid DP-01 record result shape")
    if not isinstance(v["scope"], str) or not v["scope"]: raise ValueError("invalid DP-01 result scope")
    if "audit_id" in v and (not isinstance(v["audit_id"], str) or not v["audit_id"]): raise ValueError("invalid DP-01 result audit identity")
    if "version_snapshot" in v:
        vs = v["version_snapshot"]
        if not isinstance(vs, dict) or set(vs) != {"ruleset_version", "ontology_version", "engine_version", "input_format_version", "adapter_version"} or not all(isinstance(x, str) for x in vs.values()):
            raise ValueError("invalid DP-01 result version snapshot")
    if v["judgment"] not in DP01_JUDGMENTS: raise ValueError("invalid DP-01 result judgment")
    if not isinstance(v["matched_rule_ids"], list) or not all(isinstance(x, str) for x in v["matched_rule_ids"]): raise ValueError("invalid DP-01 result matched rule ids")
    dec=v["declaration"]
    if dec is not None:
        if not isinstance(dec, dict) or set(dec) != {"method","threshold","sample_rule","unit","source"}: raise ValueError("invalid DP-01 result declaration")
        if not isinstance(dec["method"], str) or not dec["method"] or not isinstance(dec["unit"], str) or not dec["unit"] or dec["source"] != "declared": raise ValueError("invalid DP-01 result declaration")
        dec=FilteringDeclaration(**dec)
    if not isinstance(v["analysis_context"], dict): raise ValueError("invalid DP-01 result context")
    if not isinstance(v["diagnostic_explanation"], str): raise ValueError("invalid DP-01 result explanation")
    if not isinstance(v["evidence"], list): raise ValueError("invalid DP-01 result evidence")
    ev_out=[]
    for x in v["evidence"]:
        # full qualification required: the durable record must be able to answer
        # "when was this observed, how long does it hold, whose is it" (P-02 §4.1)
        required = {"source_type","verification","value","observed_at","valid_for_seconds"}
        allowed = required | {"provenance","provenance_subject","supports","evidence_role"}
        if not isinstance(x, dict) or not required <= set(x) or not set(x) <= allowed:
            raise ValueError("invalid DP-01 result evidence item")
        if x["source_type"] not in DP01_EVIDENCE_SOURCE_TYPES or x["verification"] not in DP01_EVIDENCE_STATES:
            raise ValueError("invalid DP-01 result evidence item")
        if x.get("evidence_role","support") not in DP01_EVIDENCE_ROLES:
            raise ValueError("invalid DP-01 result evidence role")
        if isinstance(x["value"], (Mapping, list, tuple, set)): raise ValueError("invalid DP-01 result evidence value")
        if not isinstance(x["valid_for_seconds"], int) or x["valid_for_seconds"] <= 0:
            raise ValueError("invalid DP-01 result evidence validity window")
        try:
            observed_at = datetime.fromisoformat(x["observed_at"])
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid DP-01 result evidence observation time") from exc
        if observed_at.tzinfo is None:
            raise ValueError("DP-01 result evidence observation time must carry a timezone")
        ev_out.append(EvidenceItem(
            source_type=x["source_type"], verification=x["verification"], value=x["value"],
            observed_at=observed_at, valid_for_seconds=x["valid_for_seconds"],
            provenance=x.get("provenance"), provenance_subject=x.get("provenance_subject"),
            supports=x.get("supports"), evidence_role=x.get("evidence_role","support")))
    ev=tuple(ev_out)
    if not isinstance(v["next_actions"], list): raise ValueError("invalid DP-01 result next actions")
    acts_out=[]
    for x in v["next_actions"]:
        if not isinstance(x, dict) or set(x) != {"action","reason","target","next_state"}: raise ValueError("invalid DP-01 result next action")
        if x["action"] not in DP01_ACTIONS: raise ValueError("invalid DP-01 result next action")
        acts_out.append(ControlledNextAction(**x))
    acts=tuple(acts_out)
    if not isinstance(v["findings"], list): raise ValueError("invalid DP-01 result findings")
    findings_out=[]
    for x in v["findings"]:
        if not isinstance(x, dict) or set(x) != {"kind","source","state","value"}: raise ValueError("invalid DP-01 result finding")
        if x["kind"] not in DP01_FINDING_KINDS or x["state"] not in DP01_FINDING_STATES: raise ValueError("invalid DP-01 result finding")
        findings_out.append(DP01Finding(**x))
    findings=tuple(findings_out)
    if not isinstance(v["limitations"], list): raise ValueError("invalid DP-01 result limitations")
    limitations_out=[]
    for x in v["limitations"]:
        if not isinstance(x, dict) or set(x) != {"kind","state","detail"}: raise ValueError("invalid DP-01 result limitation")
        if x["kind"] not in DP01_LIMITATION_KINDS or x["state"] not in DP01_FINDING_STATES: raise ValueError("invalid DP-01 result limitation")
        limitations_out.append(DP01Limitation(**x))
    limitations=tuple(limitations_out)
    if not isinstance(v["evidence_gaps"], list): raise ValueError("invalid DP-01 result evidence gaps")
    gaps_out=[]
    for x in v["evidence_gaps"]:
        if not isinstance(x, dict) or set(x) != {"kind","source","state","target"}: raise ValueError("invalid DP-01 result evidence gap")
        if x["kind"] not in DP01_GAP_KINDS or x["state"] not in DP01_FINDING_STATES or not isinstance(x["target"], str) or not x["target"]: raise ValueError("invalid DP-01 result evidence gap")
        gaps_out.append(DP01EvidenceGap(**x))
    gaps=tuple(gaps_out)
    if not isinstance(v["local_contribution"], dict) or set(v["local_contribution"]) != {"status","scope","detail"}: raise ValueError("invalid DP-01 result local contribution")
    if v["local_contribution"]["status"] not in DP01_CONTRIBUTION_STATUSES: raise ValueError("invalid DP-01 result local contribution")
    contribution=DP01LocalContribution(**v["local_contribution"])
    return DP01FilteringResult(v["scope"],v["analysis_context"],dec,ev,v["diagnostic_explanation"],tuple(v["matched_rule_ids"]),v["judgment"],acts,findings,limitations,gaps,contribution,v.get("audit_id"),v.get("version_snapshot"))

class DP01JSONLStore:
    """Append-only JSONL audit ledger with a fail-closed crash-durability contract.

    - ``append()`` returns only after fsync, so a confirmed audit is durable.
    - A process killed mid-append leaves either the prior state or the prior
      state plus exactly one complete JSONL line; a torn fragment is never
      silently accepted.
    - Any malformed line fails the whole store closed, and the error message
      pins the offending line for manual recovery; there is no automatic
      tail-drop or repair.
    - The advisory ``.lock`` sibling may remain after use; it is harmless.
    """

    def __init__(self,path):
        if not path: raise ValueError("DP-01 store path is required")
        self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True)

    @staticmethod
    def serialize(record):
        p={"audit_id":record.audit_id,"input_snapshot":_plain(record.input_snapshot),"version_snapshot":_plain(record.version_snapshot),"result":_result_to_dict(record.result)}
        if record.parent_audit_id is not None: p.update(parent_audit_id=record.parent_audit_id,change=record.change.to_dict())
        if record.operation is not None: p["operation"] = record.operation
        if record.operation == "re_audit": p["change_summary"] = record.change_summary.to_dict()
        if record.source_version_snapshot is not None: p["source_version_snapshot"] = _plain(record.source_version_snapshot)
        return json.dumps(p,ensure_ascii=False,sort_keys=True,separators=(",", ":"), allow_nan=False)+"\n"

    @contextmanager
    def _append_lock(self):
        lock_path = self.path.with_name(self.path.name + ".lock")
        with lock_path.open("a+") as lock:
            if os.name == "nt":
                import msvcrt
                lock.seek(0); lock.write("0"); lock.flush(); lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
                try: yield
                finally: lock.seek(0); msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                try: yield
                finally: fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _record_from_payload(p):
        if "version_snapshot" not in p: raise ValueError("missing DP-01 version snapshot")
        required={"audit_id","input_snapshot","version_snapshot","result"}
        allowed={"audit_id","input_snapshot","version_snapshot","result","parent_audit_id","change","operation","change_summary","source_version_snapshot"}
        if set(p) - allowed or not required <= set(p): raise ValueError("invalid DP-01 record shape")
        if not isinstance(p["version_snapshot"],dict): raise ValueError("invalid DP-01 version snapshot")
        required_version_keys = {"ruleset_version", "ontology_version", "engine_version", "input_format_version", "adapter_version"}
        if set(p["version_snapshot"]) != required_version_keys: raise ValueError("invalid DP-01 version snapshot shape")
        mismatches = {key: (p["version_snapshot"][key], _current_version_snapshot()[key]) for key in required_version_keys if p["version_snapshot"][key] != _current_version_snapshot()[key]}
        if mismatches:
            raise ValueError(f"runtime snapshot mismatch: {mismatches}")
        if not isinstance(p["input_snapshot"],dict): raise ValueError("invalid DP-01 input_snapshot")
        metadata = p["input_snapshot"].get("integrity_metadata")
        if not isinstance(metadata, dict): raise ValueError("invalid DP-01 integrity metadata")
        domains = {"identity", "version", "source", "binding", "snapshot", "permission", "unique_authority"}
        if set(metadata) != domains | {"material_ids", "pending_targets"}: raise ValueError("invalid DP-01 integrity metadata")
        for key in domains:
            if not isinstance(metadata[key], dict) or set(metadata[key]) != {"state"} or metadata[key]["state"] not in {"confirmed", "failed", "unverified", "conflicted"}:
                raise ValueError("invalid DP-01 integrity metadata")
        for key in ("material_ids", "pending_targets"):
            if not isinstance(metadata[key], list) or not all(isinstance(x, str) and x for x in metadata[key]): raise ValueError("invalid DP-01 integrity targets")
        parent=p.get("parent_audit_id"); change=None
        has_change_key = "change" in p
        if parent is not None:
            if not isinstance(parent,str) or not parent: raise ValueError("invalid DP-01 child relation")
            if not has_change_key: raise ValueError("child relation requires change")
            c=p["change"]
            if not isinstance(c,dict) or not {"change_kind","change_reason"} <= set(c) <= {"change_kind","change_reason","submitter","affected_evidence"}: raise ValueError("invalid DP-01 child relation")
            change=DP01AttemptChange(c["change_kind"],c["change_reason"],c.get("submitter"),_parse_affected_evidence(c.get("affected_evidence")))
        elif has_change_key:
            raise ValueError("invalid DP-01 child relation: change without parent")
        operation=p.get("operation")
        if operation not in (None,"re_audit") or (operation=="re_audit" and parent is None): raise ValueError("invalid DP-01 operation")
        has_summary_key = "change_summary" in p
        if operation == "re_audit" and not has_summary_key:
            raise ValueError("re_audit requires change_summary")
        if operation != "re_audit" and has_summary_key:
            raise ValueError("change_summary requires re_audit operation")
        summary=DP01ChangeSummary()
        if "change_summary" in p:
            raw=p["change_summary"]
            if not isinstance(raw,dict) or set(raw)!={"changes"} or not isinstance(raw["changes"],list): raise ValueError("invalid DP-01 change summary")
            changes=[]
            for x in raw["changes"]:
                if not isinstance(x,dict) or set(x)-{"path","before","after"} or not isinstance(x.get("path"),str): raise ValueError("invalid DP-01 change summary")
                changes.append(DP01Change(x["path"],x.get("before"),x.get("after")))
            summary=DP01ChangeSummary(tuple(changes))
        result=_result_from_dict(p["result"])
        verified = _result_from_dict(p["result"])
        if verified.audit_id is not None and verified.audit_id != p["audit_id"]:
            raise ValueError("result audit identity mismatch")
        if verified.version_snapshot is not None and dict(verified.version_snapshot) != dict(p["version_snapshot"]):
            raise ValueError("result version snapshot mismatch")
        result = verified
        source_snapshot = p.get("source_version_snapshot")
        if operation == "re_audit" and source_snapshot is None:
            raise ValueError("re_audit requires source_version_snapshot")
        if operation != "re_audit" and source_snapshot is not None:
            raise ValueError("source_version_snapshot requires re_audit operation")
        if source_snapshot is not None:
            if not isinstance(source_snapshot, dict) or set(source_snapshot) != {"ruleset_version", "ontology_version", "engine_version", "input_format_version", "adapter_version"} or not all(isinstance(x, str) for x in source_snapshot.values()):
                raise ValueError("invalid DP-01 source version snapshot")
        return DP01AuditRecord(p["audit_id"],p["input_snapshot"],result,parent,change,operation,summary,p["version_snapshot"],source_snapshot)

    def _payloads(self):
        if not self.path.exists(): return []
        out=[]; seen=set()
        for lineno, raw in enumerate(self.path.read_text(encoding="utf-8").splitlines(), start=1):
            if not raw.strip(): raise ValueError(f"malformed DP-01 JSONL line (line {lineno})")
            try: p=json.loads(raw)
            except json.JSONDecodeError as e: raise ValueError(f"malformed DP-01 JSONL line (line {lineno})") from e
            if not isinstance(p,dict): raise ValueError("invalid DP-01 record shape")
            audit_id=p.get("audit_id")
            if not isinstance(audit_id,str) or not audit_id: raise ValueError("invalid DP-01 record audit_id")
            if audit_id in seen: raise ValueError("duplicate audit_id in DP-01 JSONL")
            seen.add(audit_id); self._record_from_payload(p); out.append(p)
        ids=set(seen)
        for p in out:
            parent=p.get("parent_audit_id")
            if parent is not None and parent not in ids: raise ValueError("invalid DP-01 parent reference")
        return out

    def append(self,record):
        """Persist one record under the append lock, fsync before returning (durability contract: see class docstring)."""
        line=self.serialize(record)
        with self._append_lock():
            payloads=self._payloads()
            if any(p.get("audit_id") == record.audit_id for p in payloads): raise ValueError("duplicate audit_id or invalid record")
            if record.parent_audit_id is not None and not any(p.get("audit_id")==record.parent_audit_id for p in payloads): raise KeyError(f"missing parent_audit_id: {record.parent_audit_id}")
            try:
                candidate = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError("invalid DP-01 candidate record") from exc
            self._record_from_payload(candidate)
            with self.path.open("a",encoding="utf-8") as f: f.write(line); f.flush(); os.fsync(f.fileno())

    def get(self,audit_id):
        with self._append_lock():
            for p in self._payloads():
                if p["audit_id"]==audit_id:
                    return self._record_from_payload(p)
        raise KeyError(f"missing audit_id: {audit_id}")

    def replay(self,audit_id,replay_fn):
        """Replay a persisted record from its recorded input snapshot.

        The record is loaded under the same fail-closed validation every read
        uses: the five-field version snapshot must match the current runtime,
        or the store raises before any comparison. For root records the audit
        is recomputed from the recorded input snapshot via `replay_fn` (a pure
        function with no persistence side effects) and the identity-neutral
        result must equal the recorded result, otherwise the store fails
        closed with ValueError. Child-attempt records persist a pending
        submission form without a final judgment, so no comparison is
        performed and the record is returned as-is.
        """
        if not callable(replay_fn):
            raise TypeError("replay_fn must be callable")
        with self._append_lock():
            payloads = self._payloads()
            for p in payloads:
                if p["audit_id"] == audit_id:
                    record = self._record_from_payload(p)
                    # Child-attempt records persist a pending submission form, not
                    # a final judgment; replaying them is not a same-version
                    # determinism check, so comparison is skipped.
                    if record.parent_audit_id is not None:
                        return record
                    replayed = replay_fn(_thaw(record.input_snapshot))
                    if _identity_neutral(replayed) != _identity_neutral(record.result):
                        raise ValueError(f"replay mismatch for audit_id: {audit_id}")
                    return record
        raise KeyError(f"missing audit_id: {audit_id}")

__all__=["DP01AuditRecord","DP01JSONLStore"]
