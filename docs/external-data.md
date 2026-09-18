# 外部研究资料与回放数据

源码仓库包含启动六 Agent、运行七个演示场景、上传实际资料和执行自动化测试所需的代码与配置。以下大型资料按需从原始来源获取，不是后端启动的前置依赖。

## NIOSH Elk Creek 历史数据回放

- 官方来源：https://github.com/niosh-mining/elkcreek
- 回放脚本参照版本：`e08af5c89a3ac440458d2ac93fe40eb7996a33ea`。
- 固定版本 ZIP：https://github.com/niosh-mining/elkcreek/archive/e08af5c89a3ac440458d2ac93fe40eb7996a33ea.zip
- 本地旧档案约 305 MiB，超过 GitHub 普通 Git 文件大小限制，因此不随本仓库重复分发。

下载 ZIP 后无需解压。在本项目根目录运行（Windows PowerShell）：

```powershell
./.venv/Scripts/python.exe -m pip install pyarrow==25.0.0
./.venv/Scripts/python.exe scripts/replay_elkcreek.py --archive "downloads/elkcreek-e08af5c89a3ac440458d2ac93fe40eb7996a33ea.zip" --output output/validation/elkcreek
```

macOS/Linux 将 Python 路径替换为 `./.venv/bin/python`。脚本读取档案内的原始事件 parquet 与事故标签，校验标签 SHA256 后生成回放结果、样本和来源清单。前端的 NIOSH 历史样本入口使用这些生成文件。档案必须包含 `data/raw/events/rocksigma_events/`、`ims_events/` 与 `burst_events.csv`；若上游分发内容调整，以官方数据获取说明为准，不使用空文件代替。

公开历史回放是特定矿井的研究验证。用户自己的实际数据通过前端资料配置和监测输入提交，无需先下载这份档案。方法、适用边界与核查记录见 [来源核查](public-mine-parameter-evidence-2026-09-12.md) 和 [README 回放说明](../README.md#136-真实历史样本)。

## 千秋煤矿学位论文

《千秋煤矿冲击地压综合预警技术研究》的本地 PDF 约 179 MiB。论文及其同名转存材料不随源码发布；题录可在 [万方 D565115](https://d.wanfangdata.com.cn/thesis/D565115) 查询，全文按出版方或学校图书馆的访问条件获取。

该论文是研究参考资料，不参与默认工作流加载；不提供它不会阻止七个演示场景或用户资料模式运行。用户需要采用论文方法时，仍应核对正文中的矿井、单位、窗口和适用范围。
