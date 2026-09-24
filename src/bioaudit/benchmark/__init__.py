"""bioaudit.benchmark — 阶段 3 benchmark 评测基准（窗口 D）。

外围层：不改变任何评分路径（golden 0 差异硬验收，D6.14）。
子模块：
  models / manifest     任务集 schema 与 semver 治理（E8/D1.4）
  difficulty            E4 难度独立量化（预注册 rubric，不依赖审计分数）
  protocol              E1 预注册记录（split + gap 容忍区间 + 负向告警）
  generator             E6 任务生成器（语料变换，提示词零规则内容）
  annotation            E3 双标注 IRR（κ/α ≥ 0.8）+ 仲裁 + 共识强度
  runner                D4 评测运行器 + 功效分析（bootstrap CI + 多重比较协议）
  contamination         E2 规则字符串污染扫描（黑盒）
  coverage              E5 规则覆盖审计（34 类型 + 39 规则）
"""
