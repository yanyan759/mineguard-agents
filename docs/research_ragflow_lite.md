# RAGFlow 轻量部署调研（能否避开完整 ES/MySQL/Redis/MinIO/Infinity 全栈）

> 调研日期：2026-08-08
> 调研对象：RAGFlow 官方主分支（`main`，对应最新稳定版 v0.26.4）的源码与官方文档
> 调研方式：静态源码/文档/镜像元数据核实。本机 Docker 无外网拉取镜像能力，未执行 `docker pull`；Docker Hub 镜像仓库 API 在本机不可达（curl 超时），涉及各镜像"精确体积"之处已用文档/社区数据并标注近似值。
> 优先级规则：官方文档与源码存在矛盾时，以源码（github.com/infiniflow/ragflow 的 `docker/` 目录及 `.env`、`service_conf.yaml.template`）为准。

---

## 结论摘要

**RAGFlow 可以明显轻量化，但不是通过"无 ES 的独立精简 compose 文件"，而是通过官方内置的文档引擎切换：`DOC_ENGINE=infinity` 即可不再启动 Elasticsearch。**

1. **官方不提供 `docker-compose-sans-es.yml` 之类的精简 compose 文件**。`docker/docker-compose-base.yml` 把各可选文档引擎（es/infinity/opensearch/oceanbase/serenedb/seekdb）放在独立的 compose profile 下，由 `COMPOSE_PROFILES=${DOC_ENGINE},${DEVICE}` 决定启动哪一个。ES 只是默认值（`DOC_ENGINE=${DOC_ENGINE:-elasticsearch}`），**并非唯一选择**。

2. **无论选哪个文档引擎，有三个服务是"必备"、无 profile、永远随 compose 启动**：`mysql`（元数据）、`redis`（实际镜像为 valkey/valkey:8，缓存）、`minio`（对象存储）。`ragflow-cpu` 服务 `depends_on` 只有 `mysql`（`condition: service_healthy`）。

3. **切换到 Infinity 后，默认 docker compose 栈从"RAGFlow + ES + MySQL + MinIO + Redis"变成"RAGFlow + Infinity + MySQL + MinIO + Redis"**。由于当前（v0.22 起）RAGFlow 镜像只发布 slim 版（约 2GB，官方 README 注明），而 MinIO 仅约 53MB、valkey 约几十 MB，整个栈的**压缩下载量大约在 3GB 出头**，远小于旧版"约 9GB 全量镜像 + ES"时代的 ~11GB+（解压后磁盘占用约 2–3 倍，旧版给用户"~16GB"印象）。

4. **官方硬件下限与 ES 无关**：README 标注最低 `CPU ≥ 4 核 / RAM ≥ 16GB / Disk ≥ 50GB`。切换 Infinity 不会让官方把这个下限降到 8GB——该要求是整个产品（文档解析、embedding、RAGFlow 服务）的整体要求。**内存大头不在 ES 镜像体积，而在运行内存**（ES 需 `bootstrap.memory_lock` 与 JVM 堆；Infinity 单二进制、内存开销低于 ES）。

5. **对本机（Windows 11 + Docker Desktop，15.37GiB 内存配额）的可行路径**：`DOC_ENGINE=infinity` + 复用已有 Ollama（bge-m3:567m，约 1.2GB）做 embedding 即可，不必为 ES 预留大量内存；可保留/跳过 MinIO 见下文第 3 节。注意 Infinity 官方声明 **Linux/arm64 不支持**（Windows Docker Desktop 的 arm 模拟场景需谨慎）。

---

## 1. RAGFlow 官方 docker 部署的最小依赖是什么？ES 必选还是可选？

**结论：ES 不是必选，是"文档引擎"的默认选项之一。必备服务只有 mysql、redis(valkey)、minio。**

源码依据（primary source，来自 main 分支）：

- `docker-compose.yml` 只 `include: ./docker-compose-base.yml`，自身仅定义 `deepdoc`（profile `deepdoc`，默认不启用）、`ragflow-cpu`（profile `cpu`）、`ragflow-gpu`（profile `gpu`）。其中 `ragflow-cpu.depends_on` **只有** `mysql`：
  ```yaml
  depends_on:
    mysql:
      condition: service_healthy
  ```
  → https://raw.githubusercontent.com/infiniflow/ragflow/main/docker/docker-compose.yml

- `docker-compose-base.yml` 中各服务的 profile 归属：
  | 服务 | 镜像 | profile | 默认是否启动 |
  |---|---|---|---|
  | `es01` | `elasticsearch:${STACK_VERSION:-8.11.3}` | `elasticsearch` | 否（DOC_ENGINE=elasticsearch 时启动） |
  | `opensearch01` | `opensearchproject/opensearch:2.19.1` | `opensearch` | 否 |
  | `infinity` | `infiniflow/infinity:v0.7.2-x64-v3` | `infinity` | 否（DOC_ENGINE=infinity 时启动） |
  | `serenedb` | `serenedb/serenedb:26.07.5` | `serenedb` | 否 |
  | `oceanbase` | `oceanbase/oceanbase-ce:...` | `oceanbase` | 否 |
  | `seekdb` | `oceanbase/seekdb:latest` | `seekdb` | 否 |
  | `mysql` | `mysql:8.0.40` | 无 | **是（必备）** |
  | `minio` | `pgsty/minio:RELEASE.2026-03-25T00-00-00Z` | 无 | **是（必备）** |
  | `redis` | `valkey/valkey:8` | 无 | **是（必备）** |
  | `jaeger`/`nats`/`kibana`/`clickhouse`/`tei-cpu`/`tei-gpu`/`sandbox-*` | — | 各自 profile | 否 |
  → https://raw.githubusercontent.com/infiniflow/ragflow/main/docker/docker-compose-base.yml

- `.env` 中引擎切换机制（primary source）：
  ```
  DOC_ENGINE=${DOC_ENGINE:-elasticsearch}
  DEVICE=${DEVICE:-cpu}
  COMPOSE_PROFILES=${DOC_ENGINE},${DEVICE}
  ```
  `DOC_ENGINE` 可用值注释为：`elasticsearch`(默认)、`infinity`、`oceanbase`、`opensearch`、`seekdb`（另外 compose 中还有 `serenedb`）。
  → https://raw.githubusercontent.com/infiniflow/ragflow/main/docker/.env

- 官方 FAQ 的定性说明："Currently, only Elasticsearch and [Infinity] meet the hybrid search requirements of RAGFlow. Most open-source vector databases have limited support for full-text search, ... "（即：目前只有 ES 与 Infinity 满足 RAGFlow 的混合检索需求；这也是官方自研 Infinity 的原因。）
  → https://github.com/infiniflow/ragflow/blob/main/docs/faq.mdx

---

## 2. 是否有 "sans-ES" / 精简 / 单容器 / 轻量部署模式？

**结论：没有名为 `docker-compose-sans-es` 的独立 compose 文件，也没有官方"单容器"模式；官方提供的轻量路径就是 `DOC_ENGINE=infinity`（用官方自研的 Infinity 向量库替换 ES）。**

源码依据：

- `docker/` 目录实测文件列表（GitHub API）：`.env`、`.env.single-bucket-example`、`README.md`、`docker-compose-CN-oc9.yml`、`docker-compose-base.yml`、`docker-compose-macos.yml`、`docker-compose.yml`、`entrypoint.sh`、`infinity_conf.toml`、`init-clickhouse.sql`、`init.sql`、`launch_backend_service.sh`、`migration.sh`、`nginx/`、`oceanbase-entrypoint.sh`、`oceanbase/`、`service_conf.yaml.template`。**不存在任何 `*-sans-es*` 文件**。
  → https://github.com/infiniflow/ragflow/tree/main/docker （内容经 https://api.github.com/repos/infiniflow/ragflow/contents/docker 核对）

- 官方 README 明确给出切换文档引擎到 Infinity 的步骤（primary source）：
  1. `docker compose -f docker/docker-compose.yml down -v`
  2. 在 `docker/.env` 中设置 `DOC_ENGINE=infinity`
  3. `docker compose -f docker/docker-compose.yml up -d`
  ⚠️ 并注明："Switching to Infinity on a Linux/arm64 machine is not yet officially supported."（Linux/arm64 上切换 Infinity 尚未正式支持）
  → https://raw.githubusercontent.com/infiniflow/ragflow/main/README.md

- 官方 FAQ 同样有 "How to switch the document engine to Infinity?" 一节，步骤一致。
  → https://github.com/infiniflow/ragflow/blob/main/docs/faq.mdx

- `docker/README.md` 对两个 compose 文件的定位："docker-compose.yml …；docker-compose-base.yml Sets up environment for RAGFlow's dependencies: Elasticsearch/[Infinity], MySQL, MinIO, and Redis." 并注明 `docker-compose-CN-oc9.yml`、`docker-compose-macos.yml` **不被官方积极维护**。
  → https://raw.githubusercontent.com/infiniflow/ragflow/main/docker/README.md

- 关于"轻量镜像"：FAQ 明确 "Starting from v0.22.0, we ship only the slim edition and no longer append the **-slim** suffix to the image tag."（自 v0.22.0 起只发布 slim 版，不再有 -slim 后缀）。即当前 v0.26.x 镜像本来就是不含本地 embedding 模型的 slim 版，**不存在"更大更重的全量版"可选**。
  → https://github.com/infiniflow/ragflow/blob/main/docs/faq.mdx

---

## 3. 保留完整部署时各镜像体积是多少？是否有已知裁剪方案？

### 3.1 各镜像体积

> 说明：本机无法访问 Docker Hub/registry API，精确体积未能在本机核实；以下为官方文档与社区实测的**压缩后（下载传输）体积**，解压后磁盘占用通常约为其 2–3 倍。

| 组件 | 镜像 | 压缩体积（下载） | 数据来源 |
|---|---|---|---|
| RAGFlow（当前 v0.22+，已不含 embedding 模型） | `infiniflow/ragflow:v0.26.4` | **≈ 2 GB**（官方 README："This image is approximately 2 GB in size and relies on external LLM and embedding services."） | https://raw.githubusercontent.com/infiniflow/ragflow/main/README.md |
| RAGFlow 旧版全量（v0.19，内嵌 bge 等 embedding 模型） | `infiniflow/ragflow:v0.19.1` | ≈ 9 GB（社区实测；这也是"全栈约 16GB"印象的主要来源） | https://github.com/infiniflow/ragflow/issues/10992 ；https://ragflow.com.cn/docs/dev/build_docker_image |
| Elasticsearch | `elasticsearch:8.11.3` | ≈ 640–750 MB（linux/amd64 约 642 MB；Elastic 官方 registry 标 748 MB，含多架构） | https://docker.elastic.dev/r/elasticsearch ；https://hub.docker.com/r/tyymj/elasticsearch/tags |
| MySQL | `mysql:8.0.40` | ≈ 0.5–0.6 GB（社区常规值，未能在本机精确核实） | 官方镜像 https://hub.docker.com/_/mysql （体积未在本机核实） |
| Redis（RAGFlow 用 valkey 镜像） | `valkey/valkey:8` | 约 15–60 MB（alpine 变体约 16 MB；默认 tag 稍大，8.1 起明显增大，见 valkey-container issue #68） | https://hub.docker.com/r/valkey/valkey/tags ；https://github.com/valkey-io/valkey-container/issues/68 |
| MinIO | `pgsty/minio:latest` | **约 53 MB**（linux/amd64 53.01 MB） | https://hub.docker.com/layers/pgsty/minio/latest |
| Infinity（替代 ES 的文档引擎） | `infiniflow/infinity:v0.7.2-x64-v3` | 约 0.5–1 GB（官方定位"single-binary architecture with no dependencies"；精确体积未能在本机核实，显著小于 ES） | https://github.com/infiniflow/infinity/blob/main/README.md |
| OpenSearch（可选替代） | `opensearchproject/opensearch:2.19.1` | ≈ 1–1.2 GB（社区常规值，未在本机核实） | https://hub.docker.com/r/opensearchproject/opensearch |

**体积构成小结**：若用旧版全量镜像 + ES（社区常见场景），下载约 11–12GB、解压后磁盘占用 15GB+，这基本就是用户"~16GB 全栈"的由来。当前 v0.26 已改为 slim 版（~2GB），加 ES 后下载约 3.4GB；换成 Infinity 后约 3GB 出头。

### 3.2 已知裁剪方案

1. **去掉 ES（最大收益，也是官方文档方案）**：`DOC_ENGINE=infinity`。额外收益是运行内存显著下降（ES 有 `bootstrap.memory_lock=false`、JVM 堆与 `mem_limit` 配置；Infinity 是单二进制、轻量）。
   - 官方 README 切换步骤：https://raw.githubusercontent.com/infiniflow/ragflow/main/README.md
   - 官方 FAQ：https://github.com/infiniflow/ragflow/blob/main/docs/faq.mdx
   - 亦可选 `DOC_ENGINE=opensearch`（OpenSearch 社区版、也是社区可自行托管的替代）或 `serenedb` / `seekdb` / `oceanbase`，但这些不在官方 FAQ 的"满足混合检索"推荐里。

2. **去掉 MinIO（官方支持，但需外部 S3/OSS 后端）**：对象存储后端由 `STORAGE_IMPL` 选择，默认 `MINIO`，支持 `AWS_S3`、`OSS`（阿里云）、`AZURE_SPN`/`AZURE_SAS`、`GCS`、`OPENDAL` 等。配置都在 `docker/service_conf.yaml.template`（minio 段为默认激活，s3/oss/azure/gcs/opendal 段为注释模板）。改法：`.env` 设 `STORAGE_IMPL=OSS`（或 S3 等）+ 相应密钥/endpoint，并在模板中取消注释对应段。
   - service_conf.yaml.template（primary source，含注释的 s3/oss/azure/gcs/opendal 配置段）→ https://raw.githubusercontent.com/infiniflow/ragflow/main/docker/service_conf.yaml.template
   - 官方相关 issue（用 S3 替代 MinIO）：https://github.com/infiniflow/ragflow/issues/7717
   - 注意：官方 compose 默认仍会启动 MinIO；"去掉 MinIO"需手工修改 compose/使用外部存储，属于配置裁剪而非官方开箱即用。
   - 另见 `docker/.env.single-bucket-example`（单桶模式、含 `STORAGE_IMPL=MINIO` 说明）→ https://raw.githubusercontent.com/infiniflow/ragflow/main/docker/.env.single-bucket-example

3. **去掉本地 embedding 模型**：v0.22+ 镜像已不含 embedding 模型，必须外接 embedding 服务（官方默认注释了 `tei-cpu`/`tei-gpu` profile；也可指向现有 Ollama）。本机已装 Ollama bge-m3:567m，可作为 RAGFlow 的 embedding 提供方，无需为 RAGFlow 额外下载 embedding 模型。FAQ："RAGFlow Docker image does not include embedding models." → https://raw.githubusercontent.com/infiniflow/ragflow/main/docker/README.md

4. **可选大件全部默认关闭**：`deepdoc`（OCR/DLA 视觉解析服务，profile `deepdoc`）、`kibana`、`jaeger`、`nats`（profile `ragflow-go`）、`clickhouse`、`sandbox` 默认都不随 compose 启动，除非手动追加到 `COMPOSE_PROFILES`。所以默认栈实际就是：`ragflow-cpu + 文档引擎 + mysql + minio + redis`。

---

## 4. RAGFlow 官方最新稳定版本 tag

**结论：`v0.26.4`（2026-07-07 发布，非 pre-release / 非 draft）。**

- GitHub Releases API（primary source）：`https://api.github.com/repos/infiniflow/ragflow/releases/latest` → `"tag_name": "v0.26.4"`、`"prerelease": false`、`"draft": false`、`published_at: 2026-07-07`。
- 与主分支 `docker/.env` 一致：`RAGFLOW_IMAGE=infiniflow/ragflow:v0.26.4` → https://raw.githubusercontent.com/infiniflow/ragflow/main/docker/.env
- 镜像下载镜像（中国区，写死同样 v0.26.4）：
  - `swr.cn-north-4.myhuaweicloud.com/infiniflow/ragflow:v0.26.4`
  - `registry.cn-hangzhou.aliyuncs.com/infiniflow/ragflow:v0.26.4`
  - 来源同上（docker/.env）

---

## 5. 与本机现状/其他方案的体积对比

| 方案 | 下载体积（压缩，近似） | 运行内存要点 | 备注 |
|---|---|---|---|
| **RAGFlow 官方默认全栈（ES）** | RAGFlow 2GB + ES 0.64GB + MySQL 0.6GB + MinIO 0.05GB + valkey ~0.06GB ≈ **3.3GB**；旧版全量镜像时代 ≈ 11GB+ | ES 需内存锁/JVM 堆；官方下限 RAM≥16GB | 与"16GB"印象差异在于：旧版 9GB 全量镜像 + 解压后磁盘占用 2–3 倍 |
| **RAGFlow 轻量（Infinity）** | RAGFlow 2GB + Infinity ~0.5–1GB + MySQL 0.6GB + MinIO 0.05GB + valkey ~0.06GB ≈ **3.0–3.3GB** | Infinity 单二进制、内存低于 ES | 官方推荐低内存/低延迟场景；Linux/arm64 不支持 |
| 本机已有 Dify 1.16.0 | 已在运行（weaviate/postgres/redis 等） | — | 若只想要"开箱即用的 RAG 知识库"，Dify 本身也带知识库能力，可评估是否还需要再部署一套 RAGFlow |
| 本机 Ollama（bge-m3:567m） | 1.2GB 已装 | 约 1–2GB 驻留 | 可复用为 RAGFlow 的 embedding 提供方，省去 embedding 镜像 |

> 体积数据注：所有"≈"均为压缩后下载体积、且部分来自社区实测/官方文档标注，本机无法连 Docker Hub 做精确核验。磁盘占用以 `docker system df` 为准，通常为下载体积的 2–3 倍。

---

## 6. 给本机部署的一句话建议

在 Windows 11 + Docker Desktop（内存配额 15.37GiB）上部署 RAGFlow：
- 使用**默认最新稳定版 v0.26.4**（本来就是 slim 版，~2GB）；
- `docker/.env` 设 **`DOC_ENGINE=infinity`** 避开 ES（官方 README/FAQ 明确支持，且 Infinity 内存开销低）；
- embedding 复用本机 **Ollama bge-m3:567m**，不启用 `tei-cpu`/`tei-gpu` profile；
- 若本机还有富余且不介意改动 compose，可将 MinIO 替换为外部 S3/OSS（`STORAGE_IMPL`）；
- 预期整体镜像下载约 **3GB 出头**、运行时内存需留意 MySQL + RAGFlow + embedding 之和（官方整体下限 RAM≥16GB，本机 15.37GiB 配额略低于官方推荐，宜关闭不需要的 profile、控制 embedding 并发）。

---

## 来源清单（primary sources）

1. docker-compose.yml（main 分支）：https://raw.githubusercontent.com/infiniflow/ragflow/main/docker/docker-compose.yml
2. docker-compose-base.yml（main 分支）：https://raw.githubusercontent.com/infiniflow/ragflow/main/docker/docker-compose-base.yml
3. docker/.env（main 分支）：https://raw.githubusercontent.com/infiniflow/ragflow/main/docker/.env
4. docker/.env.single-bucket-example：https://raw.githubusercontent.com/infiniflow/ragflow/main/docker/.env.single-bucket-example
5. docker/service_conf.yaml.template：https://raw.githubusercontent.com/infiniflow/ragflow/main/docker/service_conf.yaml.template
6. docker/README.md：https://raw.githubusercontent.com/infiniflow/ragflow/main/docker/README.md
7. 项目 README.md（含硬件下限与"Switch doc engine to Infinity"、镜像约 2GB）：https://raw.githubusercontent.com/infiniflow/ragflow/main/README.md
8. 官方 FAQ docs/faq.mdx（含"仅 ES/Infinity 满足混合检索"、v0.22 起仅 slim 版、切换 Infinity 步骤）：https://github.com/infiniflow/ragflow/blob/main/docs/faq.mdx
9. GitHub Releases API（最新稳定版 v0.26.4）：https://api.github.com/repos/infiniflow/ragflow/releases/latest
10. docker/ 目录清单（确认无 sans-es 文件）：https://api.github.com/repos/infiniflow/ragflow/contents/docker
11. Infinity README（单二进制、Docker 部署）：https://github.com/infiniflow/infinity/blob/main/README.md
12. 镜像体积参考（社区/官方 registry 页面）：Elastic registry https://docker.elastic.dev/r/elasticsearch ；valkey https://github.com/valkey-io/valkey-container/issues/68 ；pgsty/minio https://hub.docker.com/layers/pgsty/minio/latest ；RAGFlow slim 体积讨论 https://github.com/infiniflow/ragflow/issues/10992

> 注：GitHub（api.github.com / raw.githubusercontent.com）在本机可直接访问并已核实；hub.docker.com / registry-1.docker.io 在本机不可达，故表格中标注"≈"的镜像体积来自官方文档标注或社区实测，未能在本机用 registry API 复核。
