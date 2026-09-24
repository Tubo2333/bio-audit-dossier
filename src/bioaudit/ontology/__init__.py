"""决策类型本体包（阶段 1 B2 落地）。

设计依据：docs/specs/2026-08-13-ontology-design-v1.md（定稿）+
docs/diagrams/2026-08-13-decision-type-ontology.md（34 类型图）+
refactor-plan-v1.1（A4 design→fail-closed / G1 internal_ref / G3 unit /
G4 confound / G5 when_not_applicable / P1 校验器三职责）。

目录结构（全部包内锚定，见 bioaudit.paths.ONTOLOGY_DIR）：
- paradigms.yaml        3 范式（bulk-DEG 标注"骨架待补全"）+ 版本
- stages.yaml           6 阶段骨架（跨范式共享）
- aliases.yaml          跨范式同源声明（3 组：filtering↔qc_filtering、
                        normalization↔scRNA_normalization；deg_method 仅同源注释）
- input_synonyms.yaml   输入归一化映射（matcher 匹配通道用，同源声明不是匹配通道）
- topics.yaml           8 个范式内主题族（知识组织，不产生新 ID）
- backlog.yaml          待补清单（G2/G6：参考基因组/注释版本/marker 版本/DEG 方向性）
- decision_types/*.yaml 34 个决策类型定义（context_schema + missing 三档）

对外接口：
- Ontology（loader.py）：加载 + 查询（dimension / depends_on / display / stage /
  aliases / input_synonyms / 覆盖矩阵）
- validate（validator.py）：P1 校验器三职责（覆盖报告 / 语义边界 / 冲突完整性）
"""

# B2 落地；C1 快照三元组之 ontology 版本（0.1.3 = 窗口 M：api_data_integrity
# data_source enum→string，A3 枚举外值误报修复；0.1.2 = K1：immune 决策类型扩 scRNA）
ONTOLOGY_VERSION = "0.1.3"
