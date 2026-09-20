# RAGFlow 知识库部署与导入指引

本目录提供 MineGuard Agents 的可选知识检索接入。RAGFlow 不是默认运行依赖：源码下载后，即使没有外部语料，也可以用 `tests/fixtures/` 启动六 Agent 工作流并得到可复现结果。只有需要用法规全文、事故调查报告或矿方方案增强检索时，才部署 RAGFlow 并导入仓库外的资料。

## 目录结构

```
deploy/ragflow/
├── README.md
├── docs/real/              # 默认只有 README；真实资料不随源码发布
│   ├── coal_mine_safety_regulations/
│   ├── coal_mine_accident_cases/
│   └── coal_mine_support_schemes/
└── scripts/
    ├── build_documents.py # fixture YAML → 演示 Markdown 文档
    ├── prepare_real_docs.py# 外部资料 → 三个本地导入目录
    └── ragflow_import.py  # 建库 → 上传 → 解析 → 建助手
```

## 使用步骤

### 1. 生成演示文档（可选）

```bash
cd agent1
python deploy/ragflow/scripts/build_documents.py
```

该步骤只读取 `tests/fixtures/accident_cases.yaml`、`regulations.yaml` 和 `support_plans.yaml`，不会读取或下载真实事故资料。生成的演示文档用于本地检查导入脚本；默认真实资料目录仍为空。

### 2. 准备外部真实资料（可选）

把经授权的资料放在仓库外，并按 `docs/ragflow_knowledge_mapping.md` 的来源目录命名。然后运行：

```powershell
$env:MINEGUARD_SOURCE_CORPUS_DIR = "D:\mineguard-data\source"
python deploy/ragflow/scripts/prepare_real_docs.py
```

没有设置该变量，或源目录尚未准备好时，脚本会创建三个 README 占位目录并正常结束，离线校验仍可通过。也可以把已经整理好的三个知识库目录直接放在仓库外，并设置：

```powershell
$env:MINEGUARD_CORPUS_DIR = "D:\mineguard-data\ragflow-docs"
```

### 3. 部署 RAGFlow（需要知识检索时）

按 [RAGFlow 官方文档](https://ragflow.io/docs/dev/) 使用 `docker compose` 部署：
1. 克隆 RAGFlow 仓库，`docker compose -f docker/docker-compose.yml up -d`（约需 4-6 个镜像，内存 ≥16G 建议）
2. 浏览器访问 `http://localhost` 注册管理员账号
3. 在 RAGFlow 界面「API Keys」页生成 API Key

### 4. 配置环境变量

在 `agent1/.env` 填入：

```env
RAGFLOW_API_URL=http://localhost:9380
RAGFLOW_API_KEY=ragflow-xxxxxxxx
```

> 注：RAGFlow Web 默认端口为 80，API 端口为 9380。按实际部署调整。

### 5. 执行导入（连接真实服务）

```bash
python deploy/ragflow/scripts/ragflow_import.py            # 先离线校验文档树
python deploy/ragflow/scripts/ragflow_import.py --execute  # 连接 RAGFlow 导入
```

导入内容：
- **3 个知识库**：`coal_mine_safety_regulations` / `coal_mine_accident_cases` / `coal_mine_support_schemes`
- **3 个检索助手**：煤矿安全规程检索助手 / 顶板事故案例检索助手 / 支护方案检索助手

助手与知识库命名与 `tools/ragflow_tools.py` 的 `_COAL_MINE_ASSISTANTS` 映射一致。只有 `docs/real/<知识库名>/` 或 `MINEGUARD_CORPUS_DIR/<知识库名>/` 中实际存在的文件才会上传；README 和 `.gitkeep` 会被跳过。

## 验证

```bash
cd agent1
python -c "from app.tools import get_assistant_list, create_ask_delete; print(get_assistant_list.invoke({})); print(create_ask_delete.invoke({'assistant_name':'煤矿安全规程检索助手','question':'b值下降应如何处理？'}))"
```

RAGFlow 未部署时工具返回 `offline_fallback`，六 Agent 仍从 fixture 知识源生成结果；部署并解析外部资料后，工具才会返回 RAGFlow 的真实检索结果。结果中的来源、版本和配置快照应在运行记录中保留，不能把“上传成功”当成“已验证适用”。

## 注意事项

- 默认源码包不含大型 PDF、事故调查报告、论文全文和矿方内部文件；请通过受控存储或 Release 按需提供，并在部署方记录哈希与授权。
- 生产环境应使用经过来源核验、版本登记和矿方审批的资料，再重新导入并在配置页选择生效版本。
- RAGFlow 解析完成、检索命中和条款适用性需要分别核验；未完成的状态应保持为待核验。
- RAGFlow 各版本 REST 端点可能有差异，导入失败时按官方文档微调 `ragflow_import.py`。
