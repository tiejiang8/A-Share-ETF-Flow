# A-Share-ETF-Flow

[English](README.md) | [简体中文](README.zh-CN.md)

从官方与权威披露数据出发，生成可追溯的 **A 股权益 ETF 资金结构日报、周报与 AI 可读摘要**。

项目围绕份额变化、估算净申购赎回、价格与份额背离、资金持续性、集群内部集中度展开分析，配置了 **36 个固定观察集群**。

| 项目 | 回答的问题 |
| --- | --- |
| [FP-DCF](https://github.com/tiejiang8/FP-DCF) | 企业价值需要哪些基本面假设支撑？ |
| A-Share-ETF-Flow | ETF 份额与估算申赎资金正在向哪里迁移？ |

## 先运行离线示例

需要 Python **3.10+**。建议创建并激活虚拟环境：

```powershell
git clone https://github.com/tiejiang8/A-Share-ETF-Flow.git
cd A-Share-ETF-Flow
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python scripts/run_offline_example.py
```

macOS / Linux 使用 `source .venv/bin/activate` 激活。

示例只使用**虚构代码、名称与数字**，不联网，输出到 `outputs/demo/`，并使用独立索引和指针。可打开 `outputs/demo/2026-01-05/llms.txt` 或 `summary.json`。演示数据始终标为非正式，不能成为真实市场日报。[查看示例](examples/README.md)。

## 采集真实市场数据

在能够正常访问官方来源的网络环境中运行：

```powershell
python -m etf_temperature.cli run --date auto
python -m etf_temperature.cli run --date YYYY-MM-DD --refresh-recent-days 2
python -m etf_temperature.cli validate --date YYYY-MM-DD
```

将 `YYYY-MM-DD` 换成真实交易日，也支持 `YYYYMMDD`。已有完整快照时默认从缓存渲染；近期刷新先检查结构化缓存覆盖率，再补缺失请求。首次采集可能明显慢于增量更新。两市份额和净值的披露节奏不同，应以验证产物为准。

日报 `run` 与 `validate` 在正式数据闸门通过时返回 **0**，未通过时保留明确标记的预览并返回 **2**。命令结束不等于数据已经完整。

部分官方行情回退路径需要浏览器：

```powershell
python -m pip install -e ".[browser,dev]"
python -m playwright install chromium
```

历史回填、完整窗口刷新、重建和周报流程见[运行维护手册](docs/OPERATIONS.zh-CN.md)。该手册保留原有维护说明与示例日期。

## 主要产物

| 文件 | 用途 |
| --- | --- |
| `outputs/<date>/A股ETF资金温度日报_<date>.md` | 人读日报，包含正式或预览状态 |
| `outputs/<date>/validation.md` | 覆盖率、时点与阻断项 |
| `outputs/<date>/daily_etf.parquet` | ETF 层标准化数据 |
| `outputs/<date>/daily_cluster.parquet` | 分类集群聚合 |
| `outputs/<date>/summary.json` | 版本化结构化摘要 |
| `outputs/<date>/llms.txt` | 适合 AI 消费的紧凑文本 |
| `outputs/<date>/snapshot.md` | 人读精简快照 |
| `outputs/latest.json` | 最新日报摘要指针与关键读数 |
| `outputs/summary_index.json` | 每个已摘要日期一条记录的索引 |

只重建摘要，不联网、不重新渲染日报：

```powershell
python -m etf_temperature.cli make-summary --date YYYY-MM-DD
```

摘要从本地按日期绑定的历史数据派生，不刷新市场输入。内容未变化时不改写文件；发布过程有跨进程锁，旧日期不会覆盖最新指针，并发不会丢掉索引记录。契约与读法见 [AI 接入说明](docs/AI_INTEGRATION.md) 和 [JSON 契约](contracts/README.md)。当前摘要三件套只覆盖日报，周报尚无同构摘要。

## 计算口径与质量约束

```text
估算净申购赎回（亿元）
  =（当日份额（份）－上一交易日份额（份））× 当日单位净值（元/份）÷ 1e8
```

5 个观察日累计使用调整后的逐日估算值。成交额代表二级市场换手；ETF 规模变化还包含价格影响。这些概念分别呈现。

- 保存原始证据、来源 URL、抓取时间和经济日期。
- 区分“测得零流量”和“没有有效测量”。
- 同时披露字段有值率与时点有效率。
- 公司行为和极端资金流先审计，再决定能否进入聚合。
- 同时检查只数覆盖率与规模加权覆盖率。
- ETF 全市场口径与集群口径分别给出，披露未分类贡献。
- 数据闸门未通过时保留明确标记的预览。

来源包括上交所、深交所、证监会基金披露系统、巨潮深证信行情服务及经核验的基金管理人披露。第三方证据单独用于核验。详见[官方接口](docs/official_endpoints.md)、[数据字典](docs/data_dictionary.md)和[集群方法论](docs/cluster_methodology.md)。

这些数据描述 ETF 的总体申赎，不能识别申购者是否为中央汇金。温度和结构标签是描述性研究结果；技术校验通过不证明预测能力或盈利能力。

## 目录与本地数据边界

```text
config/                  分类与质量规则
contracts/               摘要、指针、索引 JSON Schema
examples/                明确标记的合成输入与输出
src/etf_temperature/     采集、计算、报告与摘要主程序
scripts/                 运行、安装包验证与发布导出工具
tests/                   可移植测试与显式本地数据测试
docs/                    方法论、AI 接入与维护手册
.github/                 CI 与贡献模板
data/                    本机证据和缓存，不随源代码发布
outputs/                 本机日报和摘要，不随源代码发布
```

源码运行默认继续使用项目目录。普通安装包自带默认 YAML 配置，并以当前目录作为运行目录；可设置 `ETF_FLOW_HOME` 指定其他可写目录，其下完整的 `config/` 可覆盖默认配置。Python 分发名称 `a-share-etf-temperature` 与命令 `etf-temperature` 保持兼容。

## 验证与 GitHub 发布整理

```powershell
python -m pytest -q
python -m build
python scripts/check_distribution.py
python scripts/prepare_github_release.py
```

默认测试使用固定样本和模拟响应，不要求真实采集或本机历史库。两项 `local_data` 测试需用 `python -m pytest -q -m local_data` 显式执行，其中 2026-07-14 历史库回归存在已知未解决差异，保留原断言并记录在 [CHANGELOG.md](CHANGELOG.md)。默认测试通过不代表该历史库或当前官方链路通过验证。

发布导出工具在 `dist/github/` 生成新的源代码目录与 ZIP，附 SHA-256 清单，排除运行数据、真实报告、环境、日志、凭据及助手状态。它不会创建远程仓库或推送。[发布说明](docs/GITHUB_RELEASE.md) · [贡献说明](CONTRIBUTING.md) · [安全说明](SECURITY.md)。

本项目尚未选择许可证，整理过程中不代替维护者作出许可决定。
