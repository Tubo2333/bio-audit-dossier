"""本体加载器 — 决策类型本体的唯一读取入口（B2）。

- ``Ontology`` 类：加载 paradigms / stages / decision_types / aliases /
  input_synonyms / topics / backlog（全部包内锚定，零 cwd 依赖，F7）
- 查询接口：dimension / depends_on / dep_graph（error_tracer 兼容格式）/
  display / stage / aliases（跨范式同源，非匹配通道）/ input_synonyms（匹配通道）/
  coverage_matrix（范式 × 阶段 × 类型）
- 加载即做结构校验（缺键 / 非法 missing 档位 / 非法 dimension / 非法 stage →
  抛 ``OntologyError``）；语义深度校验（A2 / G3 / G4 / G5 / 冲突 / 对称性）在
  ``bioaudit.ontology.validator``（P1 校验器三职责）。
"""

import yaml
from pathlib import Path
from typing import Optional

from bioaudit.paths import ONTOLOGY_DIR

# 语义常量（与 validator 共享）
VALID_MISSING = {"fail-closed", "skip", "fail-open"}
VALID_DIMENSIONS = {"data_handling", "method_selection", "statistical_rigor"}
REQUIRED_TYPE_KEYS = (
    "decision_type", "display", "stage", "paradigms",
    "dimension", "optional", "depends_on", "context_schema",
)
SCHEMA_KEY_FIELDS = ("key", "type", "required", "missing")


class OntologyError(Exception):
    """本体结构错误（加载阶段即可发现的硬错误）。"""


def _load_yaml(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        raise OntologyError(f"本体文件缺失: {path}")
    if not isinstance(data, dict):
        raise OntologyError(f"本体文件必须是 YAML 映射: {path}")
    return data


class Ontology:
    """决策类型本体（单一事实源）。

    用法::

        ont = Ontology()            # 包内默认目录
        ont.dimension("deg_method")          # -> "method_selection"
        ont.depends_on("deg_method")         # -> [...]
        ont.dep_graph()                      # error_tracer 兼容格式
        ont.coverage_matrix()                # {paradigm: {stage: [types]}}
    """

    def __init__(self, ontology_dir: Optional[str | Path] = None):
        self.dir = Path(ontology_dir) if ontology_dir else ONTOLOGY_DIR
        self._types: Optional[dict[str, dict]] = None
        self._stages: Optional[dict] = None
        self._paradigms: Optional[dict] = None
        self._aliases: Optional[dict] = None
        self._input_synonyms: Optional[dict] = None
        self._topics: Optional[dict] = None
        self._backlog: Optional[list] = None
        self._version: Optional[str] = None

    # ── 加载 ──

    @property
    def version(self) -> str:
        if self._version is None:
            p = _load_yaml(self.dir / "paradigms.yaml")
            self._version = str(p.get("ontology_version", "0.0.0"))
        return self._version

    @property
    def types(self) -> dict[str, dict]:
        if self._types is None:
            self._types = {}
            for path in sorted((self.dir / "decision_types").glob("*.yaml")):
                data = _load_yaml(path)
                tid = data.get("decision_type")
                if not tid:
                    raise OntologyError(f"缺 decision_type 键: {path}")
                if tid in self._types:
                    raise OntologyError(f"决策类型 ID 重复: {tid} ({path})")
                self._validate_type_shape(tid, data, path)
                self._types[tid] = data
        return self._types

    @property
    def stages(self) -> dict:
        if self._stages is None:
            self._stages = _load_yaml(self.dir / "stages.yaml")["stages"]
        return self._stages

    @property
    def paradigms(self) -> dict:
        if self._paradigms is None:
            self._paradigms = _load_yaml(self.dir / "paradigms.yaml")["paradigms"]
        return self._paradigms

    @property
    def aliases(self) -> dict:
        """跨范式同源声明（同源 ID 表；非匹配通道，见 input_synonyms）。"""
        if self._aliases is None:
            self._aliases = _load_yaml(self.dir / "aliases.yaml")
        return self._aliases

    @property
    def input_synonyms(self) -> dict[str, str]:
        """输入归一化映射（matcher 匹配通道：同义词 → 规范 ID）。"""
        if self._input_synonyms is None:
            self._input_synonyms = _load_yaml(self.dir / "input_synonyms.yaml")
        return self._input_synonyms

    @property
    def topics(self) -> dict:
        if self._topics is None:
            self._topics = _load_yaml(self.dir / "topics.yaml")["topics"]
        return self._topics

    @property
    def backlog(self) -> list[dict]:
        if self._backlog is None:
            self._backlog = _load_yaml(self.dir / "backlog.yaml").get("backlog", [])
        return self._backlog

    # ── 查询 ──

    def get_type(self, tid: str) -> Optional[dict]:
        return self.types.get(tid)

    def is_known(self, tid: str) -> bool:
        return tid in self.types

    def dimension(self, tid: str) -> Optional[str]:
        t = self.types.get(tid)
        return t["dimension"] if t else None

    def stage_of(self, tid: str) -> Optional[str]:
        t = self.types.get(tid)
        return t["stage"] if t else None

    def depends_on(self, tid: str) -> list[str]:
        t = self.types.get(tid)
        return list(t.get("depends_on", [])) if t else []

    def aliases_for(self, tid: str) -> list[str]:
        """该类型的同源声明（homology；如 filtering → [qc_filtering]）。"""
        t = self.types.get(tid)
        return list(t.get("aliases", [])) if t else []

    def display_of(self, tid: str) -> Optional[dict]:
        t = self.types.get(tid)
        return t.get("display") if t else None

    def optional_of(self, tid: str) -> bool:
        t = self.types.get(tid)
        return bool(t.get("optional")) if t else False

    def when_not_applicable_of(self, tid: str) -> Optional[str]:
        t = self.types.get(tid)
        return t.get("when_not_applicable") if t else None

    def dep_graph(self) -> dict[str, list[str]]:
        """错误传播依赖图（error_tracer 兼容格式：下游类型 → 上游依赖列表）。"""
        return {tid: list(t.get("depends_on", [])) for tid, t in self.types.items()}

    def coverage_matrix(self) -> dict[str, dict[str, list[str]]]:
        """范式 × 阶段 → 决策类型列表（覆盖报告 / 流程正推对比用）。"""
        matrix: dict[str, dict[str, list[str]]] = {}
        for paradigm in self.paradigms:
            matrix[paradigm] = {}
            for stage in self.stages:
                matrix[paradigm][stage] = []
        for tid, t in self.types.items():
            for paradigm in t["paradigms"]:
                if paradigm in matrix:
                    matrix[paradigm][t["stage"]].append(tid)
        for paradigm in matrix:
            for stage in matrix[paradigm]:
                matrix[paradigm][stage].sort()
        return matrix

    def canonicalize(self, raw_type: str) -> str:
        """输入归一化（同义词 → 规范 ID；未知名保持原样，由调用方标 unclassified）。"""
        return self.input_synonyms.get(raw_type, raw_type)

    # ── 结构校验（加载期） ──

    @staticmethod
    def _validate_type_shape(tid: str, data: dict, path: Path) -> None:
        missing = [k for k in REQUIRED_TYPE_KEYS if k not in data]
        if missing:
            raise OntologyError(
                f"{path.name}: 决策类型 {tid} 缺必填键 {missing}"
            )
        display = data["display"]
        if not isinstance(display, dict) or not display.get("cn") or not display.get("en"):
            raise OntologyError(f"{path.name}: display 必须含非空 cn/en 双语")
        stage = data["stage"]
        if not isinstance(stage, str):
            raise OntologyError(f"{path.name}: stage 必须为字符串")
        if data["dimension"] not in VALID_DIMENSIONS:
            raise OntologyError(
                f"{path.name}: dimension {data['dimension']!r} 非法 "
                f"（合法: {sorted(VALID_DIMENSIONS)}）"
            )
        if not isinstance(data["paradigms"], list) or not data["paradigms"]:
            raise OntologyError(f"{path.name}: paradigms 必须为非空列表")
        if not isinstance(data["depends_on"], list):
            raise OntologyError(f"{path.name}: depends_on 必须为列表")
        if not isinstance(data["context_schema"], list) or not data["context_schema"]:
            raise OntologyError(f"{path.name}: context_schema 必须为非空列表")
        for item in data["context_schema"]:
            for field in SCHEMA_KEY_FIELDS:
                if field not in item:
                    raise OntologyError(
                        f"{path.name}: context_schema 键 {item.get('key', '?')} 缺 {field}"
                    )
            if item["missing"] not in VALID_MISSING:
                raise OntologyError(
                    f"{path.name}: 键 {item['key']} missing 档位 {item['missing']!r} 非法 "
                    f"（合法: {sorted(VALID_MISSING)}）"
                )
            if item["type"] == "enum" and (
                not isinstance(item.get("values"), list) or not item["values"]
            ):
                raise OntologyError(f"{path.name}: 键 {item['key']} enum 必须含非空 values")
            if item["type"] == "int" and "min" not in item:
                raise OntologyError(f"{path.name}: 键 {item['key']} int 必须含 min")
            if item["type"] not in {"enum", "int", "float", "bool", "string"}:
                raise OntologyError(f"{path.name}: 键 {item['key']} type {item['type']!r} 非法")
            if item["type"] == "bool" and "values" in item:
                raise OntologyError(f"{path.name}: 键 {item['key']} bool 不应含 values")
        aliases = data.get("aliases", [])
        if not isinstance(aliases, list):
            raise OntologyError(f"{path.name}: aliases 必须为列表")


# 模块级缓存实例（引擎接线处复用，避免重复 IO）
_DEFAULT: Optional[Ontology] = None


def get_ontology() -> Ontology:
    """包内默认本体（惰性单例）。"""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = Ontology()
    return _DEFAULT


__all__ = [
    "Ontology", "OntologyError", "get_ontology",
    "VALID_MISSING", "VALID_DIMENSIONS",
]
