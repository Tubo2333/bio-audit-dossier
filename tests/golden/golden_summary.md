# Golden 基线（D5 修复后 + DEG 统一后冻结）

- 冻结时间：2026-08-13
- 轨迹数：20，决策数：137
- 引擎状态：fullflow-demo 当前（D5 修复后 + DEG 双副本统一 + mappings 补齐）

| 轨迹 | Act | n | 分数 | verdict |
|------|-----|---|------|---------|
| deg_correct | deg | 5 | 85.0 | pass |
| deg_edge_n2 | deg | 1 | 0.0 | blocked |
| deg_edge_nofilter | deg | 3 | 15.0 | blocked |
| deg_error | deg | 5 | 15.0 | blocked |
| pan_correct | pan | 16 | 85.0 | pass |
| pan_edge_claim | pan | 1 | 0.0 | blocked |
| pan_edge_consistency | pan | 1 | 30.0 | needs_correction |
| pan_edge_epv | pan | 1 | 0.0 | blocked |
| pan_edge_purity | pan | 1 | 0.0 | blocked |
| pan_error | pan | 16 | 15.0 | blocked |
| scrna_correct | scrna | 12 | 85.0 | pass |
| scrna_crc_correct | scrna | 12 | 85.0 | pass |
| scrna_crc_error | scrna | 12 | 29.0 | blocked |
| scrna_edge_default | scrna | 1 | 30.0 | needs_correction |
| scrna_edge_nodoublet | scrna | 1 | 0.0 | blocked |
| scrna_edge_singleanno | scrna | 1 | 60.0 | pass |
| scrna_error | scrna | 12 | 40.0 | blocked |
| scrna_melanoma_cellvoyager | scrna | 12 | 29.0 | blocked |
| scrna_melanoma_correct | scrna | 12 | 85.0 | pass |
| scrna_nsclc_correct | scrna | 12 | 85.0 | pass |

> 修复任何引擎/规则后，重跑本脚本并 diff golden_expected_output.json，观察分数漂移。