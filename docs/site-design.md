# Bio-Audit 站点规范（Site Design）v1.0

> **日期**：2026-08-16（窗口 H，H1.1 产物）
> **范围**：GitHub Pages 文档站（https://tubo2333.github.io/bio-audit/）的**导航结构 / 目录组织 / 双语策略 / 与 Release 衔接**，
> 以及链接规范、数字口径纪律与维护流程。
> **性质**：本文件是站点改动的"宪法"——后续任何站点/文档结构改动，先改本规范再动手（窗口纪律：先定规范再动手）。

---

## 1. 目标与范围

1. 站点 = **项目门面 + 文档中心**：首页（index.md）讲清"这是什么 + 现在做到哪一步"，文档区承载契约/协议/窗口报告。
2. 只组织**仓库内** `bio-audit-v2/docs/`；审计中枢共享内存 `D:\C-file\docs\specs\` **不进 Pages、不动路径**
   （仓库外，本文档中的外部路径一律以反引号文本出现，**不构成站点链接**）。
3. 站点构建走 GitHub Pages 自动构建（`pages-build-deployment`，source = main 分支 / 根目录，legacy build type）；
   **本地绿不算数，部署后必须实测**（教训 #5）。

## 2. 站点机制（2026-08-16 实测事实，改动前先核对）

| 机制 | 事实 |
|---|---|
| Markdown 转换 | GitHub Pages 默认启用 `jekyll-optional-front-matter` + `jekyll-default-layout`：**无 front matter 的 `.md` 也会转成 `.html`** 并套用主题 layout；转换后同时提供 `/x.html`（主题页）与 `/x.md`（原文）双路径 |
| 主题 | `jekyll-theme-cayman`（_config.yml）；主题**无导航栏** → 导航由自定义 layout 实现（见 §3） |
| 特例 | **`README.md` 与 `CONTRIBUTING.md` 不转 `.html`**（GitHub Pages 行为，实测 /README.html、/CONTRIBUTING.html 404；社区讨论 #30162 同题）→ 导航中这两项指向 GitHub 仓库渲染视图，不指向裸 `.md`；**`README.en.md` 正常转换**（实测 /README.en.html 200） |
| 目录 URL | 子目录需要 `index.md` 才提供 `/dir/` 可访问首页（/docs/、/docs/specs/、/docs/migration/ 均配 index） |
| 站点链接 | 站点上 `.md` 相对链接是否被 jekyll-relative-links 改写**不做假设**——仓库内文档一律写**显式相对 `.html` 链接**（双端安全，见 §6） |

## 3. 导航结构（H2.2）

- **实现方案**：自定义 `_layouts/default.html`（仓库根），覆盖 cayman 主题默认 layout；
  `jekyll-default-layout` 使**全站所有页面**（含无 front matter 的旧文档）自动套用，无需逐文件加 front matter。
  layout 内嵌导航样式（不新增 CSS 资产），`aria-label="主导航"`、白色链接深底（对比度达标）。
- **导航项（9 项）**：

| # | 导航项 | 目标 | 说明 |
|---|---|---|---|
| 1 | 首页 | `/` | index.md |
| 2 | README | `https://github.com/Tubo2333/bio-audit` | README.md 不转 HTML → 指向仓库渲染视图（含双语版切换入口） |
| 3 | 快速开始 | `/docs/quickstart.html` | 中英双语（§5） |
| 4 | API 契约 | `/docs/api-contract.html` | 三入口 + 错误码（根级一级文档，§4） |
| 5 | 规则贡献 | `https://github.com/Tubo2333/bio-audit/blob/main/CONTRIBUTING.md` | CONTRIBUTING.md 不转 HTML → 仓库渲染视图 |
| 6 | 设计文档 | `/docs/specs/` | 仓库内规范/设计定稿索引 |
| 7 | 窗口报告 | `/docs/migration/` | 各窗口完成报告索引 |
| 8 | Release | `https://github.com/Tubo2333/bio-audit/releases` | 与 Release 衔接（§5.2） |
| 9 | English | `https://github.com/Tubo2333/bio-audit/blob/main/README.en.md` | 英文版入口（§5.1） |

- 站点 URL 大小写敏感（实测）；导航链接一律小写 `.html` 文件名。

## 4. 目录组织（H2.3）

```
docs/
├── site-design.md           # ★本规范（H1.1 固定路径，不移动）
├── quickstart.md / quickstart.en.md   # 快速开始（双语）
├── api-contract.md          # 契约——根级固定（见"根级保留理由"）
├── mcp-contract.md          # 契约——根级固定（同上）
├── reward-mapping.md        # 映射宪法——根级固定（同上）
├── reward-protocol.md       # reward 协议——根级固定（同上）
├── index.md                 # 文档中心首页
├── specs/                   # 仓库内规范/设计定稿（index.md 索引）
├── migration/               # 窗口完成报告（B1-B6/C2/D3/E4/F1/G2b + agent-eval ×2，index.md 索引）
├── protocols/               # 宪法协议（agent-eval-protocol / benchmark-protocol）
└── environment/             # 环境与部署（github-pages.md）
```

**根级保留理由（契约/宪法文档不移动）**：
1. `tests/test_mcp.py:216` **断言** `docs/mcp-contract.md` 存在（C4 验收项 13）；
2. `src/bioaudit/{api,errors,reward,mcp,capture,ontology}/*.py` 的 docstring 锚定
   `docs/api-contract.md`、`docs/mcp-contract.md`、`docs/reward-mapping.md`、`docs/reward-protocol.md`；
3. 窗口 H 纪律：**只允许改 docs/、README、index.md、_config.yml、.github**（src 仅 G-2 minor 例外）→
   移动这些文件会造成失效引用且无法按纪律更新源码 → **位置固定，作为一级文档**。

**分类规则（新增文档时对号入座）**：
- 契约/宪法/设计定稿 → `docs/specs/` 或根级（如被代码引用则根级）；
- 窗口完成报告/评测报告 → `docs/migration/`；
- 评测/标注宪法协议 → `docs/protocols/`；
- 环境/部署/运维说明 → `docs/environment/`。

## 5. 双语策略与 Release 衔接（H2.4 / H2.5）

### 5.1 双语策略（H11 裁决落地，范围克制）

- **只做核心文档双语**：README（`README.md` 中文主版 + `README.en.md` 英文版）+
  快速开始（`docs/quickstart.md` + `docs/quickstart.en.md`）。README.md 顶部与快速开始互相提供双语切换入口。
- **其余文档不翻译**（决策记录）：契约/协议/窗口报告以中文为准，英文读者经 README.en 引导；
  不搞全站翻译（无验收价值、纯耗时间）。
- 新文档默认中文；若为"核心文档"（面向外部读者的入门类），配 `.en` 版并在导航或文中标注。

### 5.2 与 Release 衔接

- `CHANGELOG.md` = release notes **单一事实源**：每个版本条目引用相关文档（相对路径或站点 URL）；
  站点导航含 Release 项（§3 #8）。
- GitHub Release 页面 body 引用站点文档链接（README / quickstart / api-contract / 窗口报告 / CHANGELOG）。
- 发版流程：bump 版本 → CHANGELOG 条目（含文档链接）→ 打 tag → Release body 引用文档 → 更新站点首页状态卡。

## 6. 链接与数字纪律

### 6.1 链接规范

- **仓库内文档之间**：相对 `.html` 链接（如 `../site-design.html`），GitHub 仓库内同样可读（文件路径一致）；
- **README.md / index.md（双端可见）**：站点页用**绝对站点 URL**（`https://tubo2333.github.io/bio-audit/...`），
  仓库文件用相对路径；
- **外部审计内存**（`D:\C-file\docs\specs\...`）：只以反引号文本出现，**不得写成站点链接**（站点上必 404）；
- 图片只引仓库内 `assets/`（相对路径），不引仓库外资源；
- 每次站点改动后必须**实测链接无 404**（H3.6 验收）。

### 6.2 数字口径纪律（教训 #2 单一事实源）

引用 CellVoyager 分数**必须标注口径**，禁止混写：

| 口径 | 数值 | 出处 |
|---|---|---|
| **demo 轨迹**（2026-08-13 D5 修复后引擎重跑；**2026-08-16 窗口 M 词表补齐后**） | 29 分 · blocked · **L0×3 / L1×5 / L-1×0**（M 前 L0×2 / L1×4 / L-1×2：S7 PCA_arbitrary → L1、S11 no_trajectory → L0，K 遗留①收尾） | 20 条 legacy 轨迹之一；`docs/migration/M1-capture-integrity-report.md`（§5.1） |
| **G-2 真实运行重评**（2026-08-16，GSE115978，declared 注入 + 规则平台键放宽后） | 30.0 needs_correction · L0=0 / L1×7 / L3×1 / L-1×12 | `docs/migration/agent-eval-report-g2.md` |
| **K1 重评**（2026-08-16，GSE115978，immune scRNA 规则落地后，ruleset 1.5.0） | **30.0 needs_correction（不变）** · L0=0 / L1×19 / L3×1 / L-1×0 | `docs/migration/K1-score-correctness-report.md`（§6）/ agent-eval-report-g2.md §8 |
| **10X 黄金对照 A 版**（2026-08-16，窗口 L，GSE132465 CRC 10X，确定性脚本非 LLM，ruleset 1.6.0） | **80.0 pass** · L0=0 / L1=0 / L-1=0 · 11 决策（doublet_detection scDblFinder → **L3**，D1.1 首次真实执行验证） | `docs/migration/L1-broader-eval-report.md`（§4） |
| **10X 变体 B 版采集链路闭环**（2026-08-16，窗口 M，expected_types 强制预期决策点，ruleset 1.7.0） | **63.7 blocked** · doublet_detection 补入（provenance=expected）→ D1.1 **L0** 直接出自采集链路（原"引擎级补验"路径已取代） | `docs/migration/M1-capture-integrity-report.md`（§3.2）/ L1 报告 §4.3.1 |
| **L-b 真实短评测**（2026-08-16，GSE115978 聚焦短分析，真实 LLM deepseek-chat，¥0.43） | **30.0 needs_correction** · L0=0 / L1×4 / L2×1 / L-1×0（5 决策全可评分） | 同上（§7） |

- 禁止用旧口径冒充新结果、禁止把 demo 的 5×L0 叙述安到真实运行头上（真实运行 L0=0）；
- 首页/README 涉及 CellVoyager 分数处必须给出上表口径标注。

## 7. 可访问性与构建纪律（H3）

1. 导航含 `aria-label`；正文链接与背景对比度达标（cayman 默认配色）；`lang` 由 _config.yml 声明；
2. 页面结构保持 cayman 语义（`page-header` / `main#content` / `site-footer`），不破坏现有页面渲染；
3. **部署后实测**：首页 + 导航 9 项 + 文档索引页全部 HTTP 200，站点内链接爬取无 404（教训 #5）；
4. 仓库 CI（双矩阵 pytest + golden + 三/四/五闸）与站点构建互不影响；文档改动后 CI 必须仍绿（H3.7）。

## 8. 维护流程（教训 #4 git 纪律）

1. 文档改动同样 **commit + push**（不 push 等于没做）；
2. push 后确认 GitHub Actions 的 `pages-build-deployment` 构建成功（`gh api repos/Tubo2333/bio-audit/pages/builds/latest`）；
3. 按 §7 清单实测；发现 404 → 定位引用并修复（禁止"移了不管"）；
4. 结构改动先更新本规范（先定规范再动手）。

---
*本规范由窗口 H 落盘；后续改动需与本规范一致，冲突时先改规范再改站点。*
