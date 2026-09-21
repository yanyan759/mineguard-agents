# 公开仓库与 Release 分发说明

本文说明 MineGuard Agents 的公开源码、可选研究资料和 GitHub Release 各自承担什么职责。它解决的是下载和复现边界，不改变六 Agent 的运行逻辑。

## 1. 下载什么才能运行

直接下载仓库或某个 Release 的源码包即可获得：

- FastAPI 后端、React 前端和六 Agent 工作流源码；
- Redis、可选数据库和知识服务的 Compose 配置；
- `.env.example`、依赖快照、数据模板和小型 fixture；
- 启动命令、API 说明和测试脚本。

首次运行不需要下载完整事故语料、学位论文、矿方历史报告或训练模型。仓库内的七个小型场景和 README 中的自定义示例用于验证程序链路，默认不代表任何矿井的现场结论。

## 2. Release 是什么

GitHub Release 是某个提交的固定版本快照。例如 `v1.0.0` 表示评审或实验使用的一组确定源码。Release 页面会自动提供该版本的 ZIP/TAR 源码包，也可以附加经过授权的大文件。

Release 不负责启动后端、前端或 Docker；它只是版本归档和可选文件分发入口。用户仍需按 [README 的启动章节](../README.md#7-安装与启动) 安装 Python、Node.js 和 Docker，并运行 Compose 与服务命令。

## 3. 公开仓库保留什么

公开 Git 仓库保留能够让新用户安装和验证项目的最小集合：

| 内容 | 是否进入 Git | 说明 |
| --- | --- | --- |
| Python/React 源码 | 是 | 项目主体 |
| Compose 配置与环境变量模板 | 是 | 不包含凭据 |
| 小型 fixture 和上传模板 | 是 | 用于离线回归与格式验证 |
| 文档、来源索引、校验脚本 | 是 | 说明如何取得可选资料 |
| `node_modules`、虚拟环境、构建产物 | 否 | 用户本机安装或生成 |
| SQLite、Redis volume、会话附件 | 否 | 运行时状态，不属于源码 |
| 原始矿方资料和未脱敏报告 | 否 | 隐私、授权和安全边界 |
| 大型论文、原始数据压缩包 | 通常否 | 通过来源或 Release 按需获取 |

`.gitignore` 只防止后续误添加，不能替代发布前检查。因此提交前运行：

```powershell
python scripts/check_public_package.py
git ls-files
```

检查脚本只读取 Git 已跟踪文件，不会上传或修改本机资料。

## 4. 哪些文件可以作为 Release 附件

只有同时满足“公开、已脱敏、允许再分发、可复现”四个条件的资料才适合作为 Release 附件，例如：

- 已确认许可证的公开历史样本子集；
- 去除矿名、坐标、人员和内部编号的演示语料；
- 与某个脚本和版本严格对应的回放输入包；
- 由项目生成、可以重新计算的样例结果包。

每个附件应在 Release 说明中写明：来源 URL、来源版本或提交号、许可证、文件用途、生成命令和 SHA256。不要把 `.env`、访问令牌、原始矿方数据、未授权论文扫描件或运行数据库作为附件。

对于 NIOSH Elk Creek 等第三方资料，优先链接官方来源；只有确认许可证允许再分发时才上传镜像。当前项目的下载和回放命令见 [外部研究资料与回放数据](external-data.md)。

## 5. 推荐的版本发布流程

在干净的 `main` 分支上完成测试后，固定版本标签并发布：

```powershell
python scripts/check_public_package.py
python tests/validate_fixtures.py
git status --short
git tag -a v1.0.0 -m "MineGuard Agents v1.0.0"
git push origin main --tags
```

如果需要附加经过授权的数据包，可使用 GitHub 网页的 **Releases → Draft a new release**，选择 `v1.0.0`，上传附件并粘贴来源、许可证和 SHA256。也可以使用 GitHub CLI：

```powershell
gh release create v1.0.0 --title "MineGuard Agents v1.0.0" --notes-file release-notes.md
```

没有大型附件时，不需要为了“完整”而制作空 Release。标签源码已经足够固定可复现版本。

## 6. Docker、模型和外部服务怎么分发

- 不上传 Docker Desktop、容器缓存或本机 Docker volume；Compose 文件会拉取所需镜像。
- 如果项目以后构建自有镜像，使用 GHCR 等容器注册表，并在 README 中记录镜像标签和 digest；不要把镜像压缩包混进源码仓库。
- 不把模型权重或 API Key 放进仓库。模型权重按许可证提供下载地址，API Key 只写入用户本机 `.env`。
- RAGFlow、Neo4j、MySQL 和矿方业务接口属于可选接入，配置方式见 README，不是 Release 的隐藏前置依赖。

## 7. 评审者的最短复现路径

评审者可以选择一个固定版本：

```powershell
git clone https://github.com/yanyan759/mineguard-agents.git
cd mineguard-agents
git checkout v1.0.0  # 如果需要固定版本
```

然后按 README 启动 Redis、后端和前端，点击七个演示场景或提交自定义输入。需要公开历史数据回放时，再按照外部资料说明下载并生成回放产物。这样源码下载始终保持轻量，研究资料也有清晰的来源和版本边界。
