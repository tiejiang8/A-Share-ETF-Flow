# A-Share-ETF-Flow

[简体中文](README.md) | [English](README.en.md)

**看 A 股 ETF 的钱往哪儿走：哪些 ETF 获得净申购，哪些板块持续有资金进来。**

这个工具从[上交所](https://www.sse.com.cn/)、[深交所](https://fund.szse.cn/)、[证监会基金电子披露网站](http://eid.csrc.gov.cn/fund/)等网站获取 ETF 份额、净值和行情，估算净申购、净赎回，生成日报和周报。每天的结果还会整理成文字和 JSON 摘要，方便交给 AI 阅读或接入自己的程序。

## 能用它看什么

- 哪些 ETF、哪些板块当天获得净申购，哪些出现净赎回？
- 最近 5 个观察日，资金是在持续流入，还是已经转向？
- 流入是集中在少数几只 ETF，还是同一板块的大多数 ETF 都在增加份额？
- 价格上涨时，ETF 份额有没有一起增加？

程序把 ETF 分成 **36 个指数、行业和主题分组**。每只 ETF 只计入一个主要分组，避免同一笔估算资金被重复相加。

与另一个项目 [FP-DCF](https://github.com/tiejiang8/FP-DCF) 配合使用时：FP-DCF 用来拆解公司估值，这个项目用来观察 ETF 申赎和板块资金变化。

## 数据从哪些网站来

| 网站 | 主要取什么数据 |
| --- | --- |
| [上海证券交易所](https://www.sse.com.cn/) | ETF 名单、基金份额、行情、申购赎回清单 |
| [深圳证券交易所基金专区](https://fund.szse.cn/) | ETF 名单、历史份额、行情和净值 |
| [证监会基金电子披露网站](http://eid.csrc.gov.cn/fund/) | 基金净值和公告，补查交易所数据缺口 |
| [巨潮资讯网](https://www.cninfo.com.cn/) | 基金公告，核查拆分、份额折算等特殊情况 |
| [巨潮深证信数据服务平台](https://webapi.cninfo.com.cn/) | 交易所行情请求失败时，补取日行情 |

必要时，还会结合对应基金公司网站上的净值、申购赎回清单和公告核对。程序会保存来源网址、原始文件、抓取时间和数据所属日期，便于回头查数。具体请求地址和单位见[数据来源说明](docs/official_endpoints.md)。

**各网站更新时间不同，今天能拿到的行情、份额和净值不一定属于同一天。** 数据缺失、过期或检查未通过时，程序会把报告标成“预览”，并写明缺什么、哪里有问题。

## 先看一下输出长什么样

需要 Python **3.10 或更新版本**。Windows PowerShell 中运行：

```powershell
git clone https://github.com/tiejiang8/A-Share-ETF-Flow.git
cd A-Share-ETF-Flow
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python scripts/run_offline_example.py
```

macOS / Linux 创建虚拟环境后，用 `source .venv/bin/activate` 激活，再执行安装和示例命令。

这个示例**不联网，只用虚构数据**演示分组计算和摘要输出。运行后打开：

- `outputs/demo/2026-01-05/snapshot.md`：精简文字快照。
- `outputs/demo/2026-01-05/llms.txt`：适合交给 AI 阅读的文字。
- `outputs/demo/2026-01-05/summary.json`：供程序读取的数据。

示例有独立的目录和索引，不会混进真实日报。也可以直接查看仓库里的[示例文件](examples/README.md)。

## 跑一份真实日报

在能访问上面这些网站的网络环境中运行：

```powershell
# 让程序选择统计日期
python -m etf_temperature.cli run --date auto

# 指定交易日，并检查、补取最近 2 个交易日的数据
python -m etf_temperature.cli run --date YYYY-MM-DD --refresh-recent-days 2

# 查看该日期的数据检查结果
python -m etf_temperature.cli validate --date YYYY-MM-DD
```

把 `YYYY-MM-DD` 换成真实交易日，也支持 `YYYYMMDD`。已有完整数据时，程序会优先使用本地缓存；首次采集通常比后续更新慢。

运行后先看 `outputs/<date>/validation.md`：它会说明拿到了多少数据、数据日期是否合适，以及能否生成正式报告。日报生成和检查命令返回 **0** 表示正式报告检查通过，返回 **2** 表示保留预览并列出未通过的原因。

部分行情补取功能需要浏览器组件：

```powershell
python -m pip install -e ".[browser,dev]"
python -m playwright install chromium
```

周报、历史数据补取、完整刷新等操作见[运行维护手册](docs/OPERATIONS.zh-CN.md)。

## 结果保存在哪里

| 文件 | 用途 |
| --- | --- |
| `outputs/<date>/A股ETF资金温度日报_<date>.md` | 完整日报，包含正式或预览状态 |
| `outputs/<date>/validation.md` | 数据拿全没有、日期是否合适、哪些检查未通过 |
| `outputs/<date>/snapshot.md` | 精简版文字快照 |
| `outputs/<date>/llms.txt` | 给 AI 阅读的精简文字 |
| `outputs/<date>/summary.json` | 给程序读取的摘要数据 |
| `outputs/<date>/daily_etf.parquet` | 每只 ETF 的明细数据 |
| `outputs/<date>/daily_cluster.parquet` | 按指数、行业和主题分组的汇总数据 |
| `outputs/latest.json` | 最新一份日报摘要的位置和关键数字 |
| `outputs/summary_index.json` | 已生成摘要的日期列表 |

只想用本地数据重新生成摘要，可以运行：

```powershell
python -m etf_temperature.cli make-summary --date YYYY-MM-DD
```

这条命令不联网，也不重写日报。摘要内容没有变化时，文件和修改时间保持不变；重建旧日期的摘要不会把“最新摘要”退回旧日期。**目前只有日报有这套摘要，周报暂未提供对应摘要。** AI 接入方法和字段说明见 [AI 接入说明](docs/AI_INTEGRATION.md)及 [JSON 格式说明](contracts/README.md)。

## “资金流入”是怎么算的

这里估算的是 ETF 的**净申购赎回**：

```text
估算净申购赎回（亿元）
  =（当日份额（份）－上一交易日份额（份））× 当日单位净值（元/份）÷ 1亿
```

例如，某只 ETF 的份额增加了 1 亿份，当日单位净值为 1.20 元，估算净申购就是 **1.20 亿元**。遇到份额拆分、折算等特殊情况，程序会先检查并调整，再决定是否纳入汇总。最近 5 个观察日的累计值，由调整后的逐日估算值相加得到。

阅读时请分清几个数字：

- **净申购赎回**：根据份额变化和净值估算，不能当作精确现金流水。
- **成交额**：二级市场买卖了多少，不等于有多少资金净流入 ETF。
- **基金规模变化**：同时受份额变化和价格涨跌影响。

没有拿到有效数据时会显示缺失，不会直接填成 0。报告也会分别说明“字段有数值”和“数据日期符合要求”的比例，并同时检查 ETF 只数及规模覆盖情况。全市场合计与已分类分组合计分开列出，未分类 ETF 的贡献会单独说明。

这些数据无法识别申购者是谁，也不能据此断定中央汇金是否买入。报告里的“温度”、流入持续性等标签用于描述已发生的变化，不代表经过验证的买卖信号。详细字段和分类规则见[数据字典](docs/data_dictionary.md)及[分组方法](docs/cluster_methodology.md)。

## 给维护者

```text
config/                  分类和数据检查规则
contracts/               JSON 摘要格式定义
examples/                虚构数据示例
src/etf_temperature/     采集、计算、报告和摘要代码
scripts/                 运行、安装包检查和发布工具
tests/                   测试
docs/                    方法说明和维护手册
data/                    本机原始数据及缓存，不上传
outputs/                 本机报告及摘要，不上传
```

从源码运行时，数据默认放在项目目录。通过安装包运行时，默认放在当前目录；可用 `ETF_FLOW_HOME` 指定其他可写目录，其下完整的 `config/` 可以覆盖默认配置。安装包名称仍为 `a-share-etf-temperature`，命令仍为 `etf-temperature`。

```powershell
python -m pytest -q
python -m build
python scripts/check_distribution.py
python scripts/prepare_github_release.py
```

默认测试使用固定样本和模拟响应，不访问交易所，也不需要本机历史数据。两项历史数据测试需用 `python -m pytest -q -m local_data` 单独执行；其中 2026-07-14 的历史回归仍有已知差异，见 [CHANGELOG.md](CHANGELOG.md)。默认测试通过不代表当天数据已采集完整。

发布工具会在 `dist/github/` 生成新的源码目录和 ZIP，附带文件校验清单，排除本机数据、真实报告、运行环境、日志、凭据和助手状态。[发布说明](docs/GITHUB_RELEASE.md) · [参与开发](CONTRIBUTING.md) · [安全说明](SECURITY.md)。

项目目前尚未选择许可证。
