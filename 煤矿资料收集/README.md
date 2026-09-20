# 公开资料索引与可选语料

本目录只发布资料分类、来源索引和获取说明，不随源码仓库分发煤矿报告、论文、标准全文或任何矿方原始数据。需要资料时，请先核对来源、授权和适用范围，再下载到本机的外部目录。

## 使用方式

1. 打开各分类目录的 `清单.md`，按来源链接获取资料。
2. 将资料放在仓库外，例如 `E:/mineguard-corpus/`，不要直接复制进公共仓库。
3. 设置 `MINEGUARD_SOURCE_CORPUS_DIR` 指向该目录。
4. 运行 `python deploy/ragflow/scripts/prepare_real_docs.py` 生成 RAGFlow 导入目录。
5. 设置 `MINEGUARD_CORPUS_DIR` 指向生成目录，再按 `deploy/ragflow/README.md` 导入。

索引中的文件名是建议保存名，不代表仓库已经提供全文。网页、论文和事故报告可能有访问权限或分发限制，下载前由使用者负责确认授权。资料进入 RAGFlow 后仍须在前端配置页选择适用版本，不能因为导入成功就自动成为矿井阈值或执行方案。

默认演示和基础测试不依赖本目录；它们使用 `tests/fixtures/` 中的小型脱敏 fixture。
