# RAGFlow 资料分类映射

本文定义 MineGuard Agents 可选 RAGFlow 接入的三个知识库。公开源码只提供分类规则、字段约定和小型 fixture；法规全文、事故调查报告、论文和矿方方案由部署方在仓库外准备，并在导入前核对来源、版本和授权。

默认运行不依赖 RAGFlow。没有外部资料时，六 Agent 仍使用 `tests/fixtures/` 的脱敏演示数据完成可复现流程；启用 RAGFlow 后，知识检索节点才读取配置的资料库。

## 知识库与助手

| 知识库 | 助手 | 资料范围 | 默认状态 |
|---|---|---|---|
| `coal_mine_safety_regulations` | 煤矿安全规程检索助手 | 安全规程、防冲规范、监测制度 | 空目录，仅导入外部资料 |
| `coal_mine_accident_cases` | 顶板事故案例检索助手 | 事故通报、调查报告、前兆研究 | 空目录，仅导入外部资料 |
| `coal_mine_support_schemes` | 支护方案检索助手 | 应急预案、支护规范、经审核的方案 | 空目录，仅导入外部资料 |

三者的名称必须与 `tools/ragflow_tools.py` 中的助手映射一致。资料数量以本机实际导入日志为准，不能用目录中 README 的存在推断资料已经入库或解析成功。

## 外部资料准备

建议把已获授权的文件放在仓库外，例如：

```text
D:/mineguard-data/source/
├── 01_煤矿安全规程_顶板管理_冲击地压章节/
├── 02_煤矿顶板事故应急预案/
├── 03_冲击地压防治技术规范/
├── 04_煤矿支护技术规范GB系列/
├── 05_近五年煤矿顶板事故通报/
├── 06_事故前兆数据_学术论文/
└── 08_监测制度_专家经验/
```

设置 `MINEGUARD_SOURCE_CORPUS_DIR` 后运行 `prepare_real_docs.py`，脚本会按上表复制支持的 PDF、HTML、TXT、DOC 和 DOCX 到本地导入目录。脚本不会下载文件，也不会把外部资料重新加入 Git：

```powershell
$env:MINEGUARD_SOURCE_CORPUS_DIR = "D:\mineguard-data\source"
python deploy/ragflow/scripts/prepare_real_docs.py
```

如果资料已经按知识库整理，也可以直接设置 `MINEGUARD_CORPUS_DIR` 指向包含三个子目录的目录，跳过复制步骤。导入前应记录资料来源 URL、版本、下载时间、SHA-256 和授权范围；这些元数据由配置页面或部署方的受控台账保存。

## 导入与核验

```powershell
# 无 RAGFlow 也可运行，检查目录和文件数量
python deploy/ragflow/scripts/ragflow_import.py

# 配置 RAGFLOW_API_URL、RAGFLOW_API_KEY 后执行上传和解析
python deploy/ragflow/scripts/ragflow_import.py --execute
```

导入后需要在 RAGFlow 中确认每份文档达到解析完成状态，并用三个助手分别检索一条可追溯原文。RAGFlow 的检索结果只能作为知识候选，风险分级和高风险动作仍受确定性规则、矿方参数和人工审批门约束。07 类阈值研究资料只能用于参数评审，不会自动改写运行配置。

更多配置字段和外部资料目录约定见 [实际数据配置指南](actual-data-guide.md) 与 [外部资料说明](external-data.md)。
