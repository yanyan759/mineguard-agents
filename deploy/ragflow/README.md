# RAGFlow 知识库部署与导入指引

本目录为煤矿顶板灾变系统的 RAGFlow 知识库部署配套。
导入脚本可在本地离线校验，也可连接已部署的 RAGFlow 服务执行。

## 目录结构

```
deploy/ragflow/
├── README.md              # 本文件
├── docs/                  # build_documents.py 生成的 md 文档
│   ├── safety_regulations/   安全规程条款（10 篇）
│   ├── accident_cases/       历史事故案例（5 篇）
│   └── support_schemes/      支护处置方案（5 篇）
└── scripts/
    ├── build_documents.py # fixture YAML → markdown 文档（离线可运行）
    └── ragflow_import.py  # 建知识库 → 传文档 → 解析 → 建助手（--execute 连接真实服务）
```

## 使用步骤

### 1. 生成文档（离线）

```bash
cd agent1
python deploy/ragflow/scripts/build_documents.py
```

### 2. 部署 RAGFlow（环境就绪后）

按 [RAGFlow 官方文档](https://ragflow.io/docs/dev/) 使用 `docker compose` 部署：
1. 克隆 RAGFlow 仓库，`docker compose -f docker/docker-compose.yml up -d`（约需 4-6 个镜像，内存 ≥16G 建议）
2. 浏览器访问 `http://localhost` 注册管理员账号
3. 在 RAGFlow 界面「API Keys」页生成 API Key

### 3. 配置环境变量

在 `agent1/.env` 填入：

```env
RAGFLOW_API_URL=http://localhost:9380
RAGFLOW_API_KEY=ragflow-xxxxxxxx
```

> 注：RAGFlow Web 默认端口为 80，API 端口为 9380。按实际部署调整。

### 4. 执行导入（连接真实服务）

```bash
python deploy/ragflow/scripts/ragflow_import.py            # 先离线校验文档树
python deploy/ragflow/scripts/ragflow_import.py --execute  # 连接 RAGFlow 导入
```

导入内容：
- **3 个知识库**：`coal_mine_safety_regulations` / `coal_mine_accident_cases` / `coal_mine_support_schemes`
- **3 个检索助手**：煤矿安全规程检索助手 / 顶板事故案例检索助手 / 支护方案检索助手

助手与知识库命名与 `agent1/tools/ragflow_tools.py` 的 `_COAL_MINE_ASSISTANTS` 映射一致，
RAGFlow 上线后工具层无需改动即自动走真实检索。

## 验证

```bash
cd agent1
python -c "from app.tools import get_assistant_list, create_ask_delete; print(get_assistant_list.invoke({})); print(create_ask_delete.invoke({'assistant_name':'煤矿安全规程检索助手','question':'b值下降应如何处理？'}))"
```

RAGFlow 未部署时返回 `offline_fallback` 模式；部署后返回真实检索结果。

## 注意事项

- 本地文档用于开发和验收；生产环境应替换为经过来源核验、版本登记的矿方资料，
  再重新导入并在配置页选择生效版本。
- RAGFlow 各版本 REST 端点可能有差异，导入失败时按官方文档微调 `ragflow_import.py`。
