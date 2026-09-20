# Public Reproducible Package Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 MineGuard Agents 整理为不默认携带大型语料、但可从全新环境启动并运行六 Agent 演示的公开源码包。

**Architecture:** 以 `tests/fixtures` 的小型脱敏数据作为默认确定性知识源；大型真实资料只通过索引和可选导入目录接入。代码对可选资料缺失保持可运行，并在结果证据中标记实际数据来源。

**Tech Stack:** Python、FastAPI、LangGraph、Redis Stack、React、Vite、Git、PowerShell。

## Global Constraints

- 只修改 `agent1/`，不修改上级 `code/`。
- 不把隐私数据、真实 `.env`、用户附件、运行账本或未经授权的资料提交到公开仓库。
- 保留小型脱敏 fixture，使无可选语料环境仍能得到六 Agent 演示结果。
- 可选真实资料缺失时不得阻断默认 YAML 工作流。
- 所有路径、来源和运行状态说明必须与实际代码一致。

### Task 1: 清理默认语料并建立可选资料边界

**Files:**
- Remove from Git: `煤矿资料收集/` 下的 PDF、HTML、DOC、DOCX、ZIP 原始资料
- Remove from Git: `deploy/ragflow/docs/real/` 下的大型资料文件
- Create: `煤矿资料收集/README.md`
- Create: `deploy/ragflow/docs/real/README.md`
- Modify: `.gitignore`

**Verification:** 跟踪文件只保留索引、模板和说明；默认 YAML fixture 路径仍存在；仓库工作树无大于 10 MB 的文件。

### Task 2: 让可选资料缺失时安全运行

**Files:**
- Modify: `agent/knowledge_sources.py`
- Modify: `services/verified_knowledge.py`
- Modify: `deploy/ragflow/scripts/prepare_real_docs.py`
- Modify: `deploy/ragflow/scripts/ragflow_import.py`
- Test: `tests/test_public_package_without_corpus.py`

**Verification:** 删除可选资料目录内容后，导入模块、默认知识检索和基础工作流仍返回结构化结果；资料导入命令给出清晰的缺失目录提示。

### Task 3: 整理真实数据投喂入口

**Files:**
- Modify: `真实数据投喂/README.md`
- Modify: `真实数据投喂/01_事故案例整理/README.md`
- Modify: `真实数据投喂/02_UIE标注数据/README.md`
- Modify: `docs/真实资料爬取与整理要求清单.md`
- Modify: `docs/资料爬取任务书.md`

**Verification:** 文档明确区分源码 fixture、用户上传资料、离线模板和可选语料；不出现“默认仓库含完整真实资料”的表述。

### Task 4: 更新 README 和发布索引

**Files:**
- Modify: `README.md`
- Modify: `docs/external-data.md`
- Create: `docs/public-data-manifest.json`

**Verification:** 新用户按 README 只安装源码依赖即可运行演示；可选资料索引包含来源、授权、下载方式和 SHA-256 占位规则；README 不要求先下载大型资料。

### Task 5: 发布前检查

**Files:**
- Test: `tests/validate_public_package.py`

**Verification commands:**

```powershell
D:\python\python.exe tests/validate_public_package.py
D:\python\python.exe tests/validate_fixtures.py
D:\python\python.exe -c "import api.server; print('API import OK')"
cd frontend
npm.cmd run build
```

完成后检查 Git 状态、跟踪文件体积、重复文件和敏感内容，再提交并同步 GitHub。
