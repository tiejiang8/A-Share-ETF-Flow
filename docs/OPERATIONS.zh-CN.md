# 运行与维护手册

> 保留原有维护流程及历史示例日期。项目介绍和首次使用入口见 [项目首页](../README.md)；最新摘要契约见 [AI 接入说明](AI_INTEGRATION.md)。

# A股ETF资金结构日报与周报

本项目从上海证券交易所、深圳证券交易所及权威披露源采集 A 股权益 ETF 的行情、份额、净值和规模数据，生成全市场资金结构日报、周报、36 个固定观察集群和完整排行榜。

项目用于观察：

- 资金偏向大盘、中小盘还是双创成长；
- 红利防御与科技成长之间的资金迁移；
- ETF 价格变化是否得到份额增加确认；
- 单日异动是否发展为 3 日、5 日持续趋势；
- 集群资金流是否广泛，还是由少数 ETF 主导；
- 价涨份额增、价涨份额降、价跌份额增、价跌份额降等结构信号。

成交额只代表二级市场换手、活跃度和分歧，不直接等同于资金流入。估算净申购/赎回金额由 ETF 份额变化乘以单位净值得到，不等于基金精确现金流。

---

## 1. 目录结构

```text
config/                  分类、温度、数据源和交易周配置
data/raw/                官方接口原始响应，主要用于审计
data/cache/              按 date + etf_code 去重的结构化 CSV 缓存
data/normalized/         标准化 parquet 数据和历史库
docs/                    数据字典、官方接口和方法论
outputs/                 日报、周报、校验、审计和图表
scripts/run_daily.ps1    自动日期日报入口
scripts/run_weekly.ps1   自动周报入口
src/etf_temperature/     主程序源码
tests/                   测试
```

### `data/raw` 与 `data/cache` 的区别

`data/raw/` 保存交易所和权威数据源的原始响应，用于追溯请求和审计：

- 文件按抓取时间、接口前缀和 URL 哈希命名；
- 相同 URL 且响应内容相同，不重复写入；
- 带明确历史截止日期的请求可长期复用；
- “最新列表、最新净值”等请求使用短 TTL，默认 6 小时；
- 网络失败时可以读取已有原始响应作为兜底。

`data/cache/` 是实际用于增量计算和历史重建的结构化缓存：

```text
data/cache/etf_quotes.csv
data/cache/etf_shares.csv
data/cache/etf_nav.csv
```

三张表统一使用：

```text
date + etf_code
```

作为唯一键。官方优先级更高的数据覆盖低优先级数据，同优先级使用抓取时间更新的数据。重复执行不会增加重复记录。

---

## 2. 环境安装

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

只有在官方接口需要浏览器回退（Browser fallback）时再安装：

```powershell
python -m pip install -e ".[browser,dev]"
python -m playwright install chromium
```

运行测试：

```powershell
pytest -q
```

---

## 3. 生成日报

### 3.1 使用本地快照或自动补采

> **网络执行优先级：沙盒外本机网络第一优先，并作为默认采集方式。** 只要任务需要访问上交所、深交所、CNINFO、中国证监会信息披露系统或基金公司官网，就应直接通过沙盒外本机网络运行，不先尝试沙盒网络。只有完全基于已验证本地缓存的重算、渲染和校验才在受限沙盒内执行。

```powershell
python -m etf_temperature.cli run --date 2026-07-02
```

默认行为：

- 如果该日期已经存在完整日报快照，只读取本地历史数据并重新生成报告、校验和图表；
- 如果本地缺少完整日报快照，通过沙盒外本机网络自动采集官方数据；
- `--date` 支持 `YYYY-MM-DD`、`YYYYMMDD` 和 `auto`。

自动识别最新可报告交易日：

```powershell
python -m etf_temperature.cli run --date auto
```

### 3.2 刷新参数

| 参数 | 含义 |
|---|---|
| 无刷新参数 | 有完整快照时仅从本地重绘；缺数据时自动采集 |
| `--refresh` | 触发最近 2 个交易日刷新；若结构化缓存已满足覆盖率，会跳过对应官方请求 |
| `--refresh-recent-days N` | 刷新最近 N 个交易日，自动包含 `--refresh` 语义；同样先检查结构化缓存 |
| `--full-refresh` | 刷新完整指标回看窗口，自动包含 `--refresh` 语义 |

刷新最近 2 个交易日：

```powershell
python -m etf_temperature.cli run --date 2026-07-02 --refresh
```

刷新最近 3 个交易日：

```powershell
python -m etf_temperature.cli run --date 2026-07-02 --refresh-recent-days 3
```

完整窗口刷新：

```powershell
python -m etf_temperature.cli run --date 2026-07-02 --full-refresh
```

以下写法继续兼容：

```powershell
python -m etf_temperature.cli run --date 2026-07-02 --refresh --full-refresh
```

`--full-refresh` 与 `--refresh-recent-days` 不能同时使用，因为一个表示完整窗口，一个表示最近 N 日窗口。冲突命令会直接报错：

```powershell
# 不允许
python -m etf_temperature.cli run --date 2026-07-02 --full-refresh --refresh-recent-days 3
```

### 3.3 三种刷新模式的实际区别

#### 默认模式

```powershell
python -m etf_temperature.cli run --date 2026-07-02
```

适合已经生成过的历史日报。不会无意义地重复访问交易所。

#### 近期刷新

```powershell
python -m etf_temperature.cli run --date 2026-07-02 --refresh-recent-days 3
```

适合交易所补发、修订最近份额或净值数据的情况。程序只刷新近期窗口，再与结构化缓存合并。

近期刷新不是无条件重拉交易所数据。程序会先检查 `data/cache/` 中目标交易日的 `date + etf_code` 覆盖率：

- 份额缓存满足门槛时，日志会显示 `daily shares cache window ready; skip official shares fetch`；
- 行情缓存满足门槛时，日志会显示 `daily quotes cache window ready; skip official quotes fetch`；
- 对周中新上市 ETF，不再要求上市前日期必须存在行情行，避免反复补拉不存在的数据。

交易所采集还遵循以下性能约束：

- 日报补净值时，深交所只请求 `TABKEY=tab2` 的净值表，不再连带重拉 ETF 基础名单；
- 深交所首次请求确定可用站点后，后续分页优先复用该站点；净值分页最多使用 4 个独立会话并发，既缩短等待时间，也避免共享 HTTP 会话；
- 上交所日 K 线会额外多回看一个位置，确保在 T+1 生成日报时，`points=2` 能同时覆盖 T 日和 T-1 日，而不是漏掉报告日后再全部走行情回退源。

#### 完整窗口刷新

```powershell
python -m etf_temperature.cli run --date 2026-07-02 --full-refresh
```

适合以下情况：

- MA5、MA10、5 日或 10 日指标异常；
- 本地历史窗口不完整；
- 近期缓存可能缺失；
- 需要重新计算完整指标窗口。

`--full-refresh` 会重新处理完整回看窗口，但不会删除 `data/raw`。原始 HTTP 响应是否重新访问，仍遵循历史请求长期缓存和最新接口短 TTL 的规则。

### 3.4 日报输出

```text
outputs/YYYY-MM-DD/A股ETF资金温度日报_YYYY-MM-DD.md
outputs/YYYY-MM-DD/A股ETF资金温度日报_技术附录_YYYY-MM-DD.md
outputs/YYYY-MM-DD/daily_etf.parquet
outputs/YYYY-MM-DD/daily_cluster.parquet
outputs/YYYY-MM-DD/daily_snapshot.json
outputs/YYYY-MM-DD/summary.json
outputs/YYYY-MM-DD/llms.txt
outputs/YYYY-MM-DD/snapshot.md
outputs/YYYY-MM-DD/validation.md
outputs/YYYY-MM-DD/charts/
outputs/latest.json
outputs/summary_index.json
```

CLI 输出中会显示：

```text
used_cache
cache_reason
metrics.render_mode
metrics.source_refresh_mode
metrics.official_refresh_performed
```

日志写入 stderr，JSON 结果写入 stdout。便于确认本次是本地重绘、近期刷新、完整窗口刷新，还是本地缓存重算。

日报校验同时检查“字段存在”和“字段时点新鲜”：

- 行情和份额的 `*_data_date` 必须等于报告日；
- 净值时滞由 `daily_max_nav_lag_days` 控制，默认必须是报告日净值；
- 来源时点过期或未解决的极端资金流会保留原始审计值，但在温度和集群聚合前剔除；
- 资金流同时计算 ETF 只数覆盖率和规模加权覆盖率；大规模 ETF 缺失时不会被大量小 ETF 的有效记录掩盖；
- 集群温度中的资金流分量按有效规模覆盖率向中性 50 分收敛，并同步降低置信度；
- 若交易所申购赎回清单提供最小申购赎回单位，会保留 `creation_redemption_unit`、`estimated_creation_redemption_units_1d` 和整数贴合误差，作为份额变化资金流的辅助校验；
- `validate --date` 只读取该日期的历史快照，不再混用最新 `etf_daily.parquet`；
- 正式闸门失败时仍可生成明确标注的预览报告，但 `run`、`validate` 和 `validate-clusters` 返回退出码 `2`。

所有 parquet 先写临时文件并完成可读性检查，再原子替换（atomic replace）正式文件，避免中断后留下半写文件。

### 3.5 AI 可读摘要（summary.json / llms.txt / snapshot.md）

日报正文面向人，摘要产物面向程序与 AI。每次 `run` 与 `rebuild` 都会自动生成，无需额外命令。

| 文件 | 用途 |
|:---|:---|
| `outputs/<date>/summary.json` | 结构化摘要，含口径、完整率、数据时点、集群与个股排名 |
| `outputs/<date>/llms.txt` | 纯文本摘要，可直接投喂 LLM |
| `outputs/<date>/snapshot.md` | 人可读快照 |
| `outputs/latest.json` | 顶层指针，指向最新一期摘要的关键读数 |
| `outputs/summary_index.json` | 增量索引，每个交易日追加一行 |

摘要与日报同源，均由同一次 pipeline 的官方数据产物派生，因此两者的数字不可能不一致。

单独重建某日摘要（只读本地缓存，不联网）：

```bash
.venv/Scripts/python.exe -m etf_temperature.cli make-summary --date 2026-11-04
```

摘要遵守以下约束，任何一条都不得在后续迭代中放宽：

- **缺失不填零**。没有资金流记录的标的不计入合计，同时给出有效只数与缺失只数，读者能区分「没有流量」和「没有测量」。
- **时点显式**。行情日、份额日、净值日分别发布；净值经济日期滞后于报告日的标的单独列出，不静默沿用旧净值。
- **完整率伴随**。每个汇总都附带覆盖率、缺失代码清单与未解决极端资金流数量。
- **有值率与时点有效率区分**。`quote_coverage` / `share_coverage` / `nav_coverage` 表示字段有值率；对应的 `*_fresh_coverage` 按项目时点规则判断是否有效，行情、份额要求当日，净值按允许时滞规则（默认当日）。摘要、最新指针和索引均保留这两类指标。
- **单位明确**。份额以份计、单位净值以元/份计，估算金额换算为亿元时须除以 `1e8`；摘要直接复用已计算的亿元金额。
- **榜单方向一致**。净申购榜只收正值，净赎回榜只收负值；零值和缺失值均不入榜，同方向不足 20 只时按实际数量输出。
- **不声称身份**。数据描述的是全市场参与者的净申赎金额，无法识别申购者是否为中央汇金或任何特定机构。
- **口径差异披露**。ETF 层合计与集群层合计的差额（未分类 ETF）单独给出。
- **字节稳定**。内容未变化时不重写文件；`outputs/latest.json` 指针只前进不回退。
- **并发发布串行化**。摘要三件套、索引和指针的完整写入过程受跨进程锁保护，避免并发运行造成指针回退或索引漏日期。`outputs/.summary.lock` 是保留的锁文件，进程退出即释放操作系统锁；不要手动删除正在使用的锁文件。

---

## 4. 历史净值补采与区间重建

历史行情和份额完整、但历史净值缺失时，可直接从中国证监会基金电子披露网站按日期批量补采：

```powershell
python -m etf_temperature.cli backfill-nav --start 20260706 --end 20260709
```

补采结果写入：

```text
data/cache/etf_nav.csv
data/normalized/etf_nav.parquet
outputs/nav_backfill_2026-07-06_2026-07-09.json
```

`rebuild` 现在会同时检查行情、份额和净值缓存。即使旧日报快照已经存在，只要补到了新的市场数据，也会自动重新计算目标日期，而不是仅重绘旧报告：

```powershell
python -m etf_temperature.cli rebuild --start 20260706 --end 20260710
python -m etf_temperature.cli run-weekly --week 2026-W28
```

W28 可直接执行：

```powershell
.\scripts\backfill_w28.ps1
```

### 4.1 ETF 市场数据回填（年初至今 / 任意区间）

需要跨日研究资金速度、反转、内部换仓或构建自己的分析表时，先使用市场数据回填入口。该命令只负责从官方来源补齐行情、份额、净值和 AUM（Assets Under Management，资产管理规模），不计算温度或其他衍生指标，也不会生成 Markdown 日报、图表或逐日快照。

回填 2026 年初至今（截至最近完整交易日）：

```powershell
python -m etf_temperature.cli backfill-market-data `
  --start 20260101 `
  --end 20260717
```

若深交所历史份额站点暂时不可用，可先完成行情和净值并保留份额缺口，稍后再续跑份额：

```powershell
python -m etf_temperature.cli backfill-market-data `
  --start 20260101 `
  --end 20260717 `
  --skip-szse-shares
```

若只想验证交易所链路、暂不抓取基金公司官网，可增加：

```powershell
python -m etf_temperature.cli backfill-market-data `
  --start 20260101 `
  --end 20260717 `
  --skip-fund-company-aum
```

执行过程：

1. 按代码批次补齐行情；上交所份额按交易日分块，深交所份额使用官方 XLSX 按自然季度分块（2026 年初至 7 月中旬仅需 3 个文件），并按 `date + etf_code` 原子写入缓存；中断后再次执行会根据缓存覆盖率跳过已完整的代码或日期。
2. 从已验证的基金公司产品页采集带披露日期的 AUM 与同日 NAV，保存原始 URL、抓取时间、证据哈希、日期口径和推算份额。
3. `AUM ÷ 同日 NAV` 只证明数学上的推算份额。只有推算值与交易所**同一经济日期**的官方份额形成至少 5 只产品的重叠校验，并同时通过中位数与 95 分位误差门槛，才写入主份额缓存。仅与前一交易日吻合时，只标记“公式通过、份额日期未识别”，不生成流入流出。
4. 从行情覆盖率识别真实交易日，逐日校验行情、份额、净值、AUM 与规模覆盖率，生成可直接供后续本地计算使用的覆盖清单。

输出文件：

```text
data/cache/etf_quotes.csv
data/cache/etf_shares.csv
data/cache/etf_nav.csv
data/cache/etf_aum.csv
data/normalized/etf_daily_quotes_history.parquet
data/normalized/etf_share.parquet
data/normalized/etf_nav.parquet
data/normalized/etf_aum.parquet
data/normalized/fund_company_implied_shares_2026_ytd.parquet
data/normalized/trading_calendar_2026_ytd.parquet
data/normalized/market_data_coverage_2026_ytd.parquet
outputs/market_data_backfill_2026_ytd.json
outputs/market_data_backfill_checkpoint_2026-01-01_2026-07-17.json
outputs/fund_company_source_coverage_2026_ytd.json
```

完成标准不是“命令跑完”。汇总 JSON 中 `market_data_ready=true` 表示资金流研究所需的交易日跨度完整，且每个交易日的行情、份额、净值覆盖率分别达到 98%、98%、95%；`scale_data_ready=true` 则表示行情与可识别规模覆盖率分别达到 98%、95%。只有 AUM 而没有可识别份额时，后续分析只展示 `fund_size_yuan`，`estimated_net_subscription_*` 保持为空，绝不把规模变化伪装成申购赎回。若部分日期失败，已获取的数据仍会保留，命令返回退出码 `2`，具体缺口见 `market_data_coverage_*.parquet` 和 `blocked_dates`。温度、速度、反转等指标在数据完成后再从本地缓存计算。

### 4.2 来源分层与深交所访问方式

- **本机网络采集契约（Codex 环境）**：沙盒外本机网络是官方数据采集的第一优先级和默认执行环境，不应先尝试沙盒网络。凡是需要访问上交所、深交所、CNINFO、中国证监会信息披露系统或基金公司官网的命令，都必须直接通过沙盒外本机网络执行。受限沙盒只用于读取已验证本地缓存、重算指标、生成报告和执行校验。
- 如果因误操作在沙盒内启动采集，其超时、连接拒绝、`WinError 10013`、目标交易日返回 0 行或日期停留在上一交易日等结果均不作为数据证据，不得据此判断“官方尚未披露”或把缺失数据写成 0；应终止该次采集，改用沙盒外本机网络重新运行，并以目标日期的 `outputs/YYYY-MM-DD/validation.md` 为最终完成依据。
- 本机网络采集失败时，流水线必须保留真实错误和覆盖缺口，继续维持预览/数据待补全状态；不得静默切换为伪造数据、第三方主数据或形式上的正式报告。
- 主数据层只接受深交所、上交所、中国证监会信息披露系统和已验证的基金公司官网。深交所基金站 `fund.szse.cn` 的公开接口可直接通过 HTTPS 访问，不需要 Cookie、浏览器指纹或验证码绕过。本项目优先使用 `/api/report/ShowReport` 官方 XLSX：ETF 列表和最新 NAV 各 1 次请求，历史份额按自然季度 1 次请求；`/data` JSON 分页只作失败回退。
- 若在 Codex 受限沙箱内出现 `WinError 10013`，这是本地执行环境禁止网络连接，不代表深交所封禁；在获准的正常系统网络中运行同一命令即可。原始 XLSX 按 URL 摘要长期缓存，历史区间再次执行不会重复下载。
- `docs.static.szse.cn` 可作为深交所官方月报、期末表和公告的低频锚点，但不能替代没有披露在静态文件中的每日份额序列。
- 新浪财经等第三方页面只进入验证层。带明确数据日期的 NAV/行情可以用于缺口发现和交叉核验；“最新规模”若没有自己的经济日期，或申购赎回/总份额为空，不写入 AUM/份额主缓存，不参与资金流计算。

历史成分口径当前使用“最新官方 ETF 名单 + 上市日期过滤”。这能避免把 ETF 放到上市前，但尚不能恢复历史上已经退市且不在最新名单中的产品；汇总文件会明确记录 `point_in_time_delisting_supported=false`，做严格存续偏差（survivorship bias）研究时需另补历史退市名单。

## 5. 按区间重建历史

```powershell
python -m etf_temperature.cli rebuild `
  --start 20260629 `
  --end 20260703
```

`rebuild` 不会为每个报告日重复抓取高度重叠的历史区间。执行流程为：

```text
一次性预取：预热起点 → 结束日期
        ↓
按 date + etf_code 写入 CSV 缓存
        ↓
按目标日期从本地缓存切片
        ↓
逐日计算并生成历史日报
```

预热窗口用于计算 MA5、MA10、5 日/10 日收益、成交额均值和份额变化，因此实际抓取起点会早于 `--start`。

重建摘要写入：

```text
outputs/rebuild_prefetch_开始日期_结束日期.json
```

主要字段：

```text
quote_cache_hit
share_cache_hit
fetched_quote_rows
fetched_share_rows
quote_cache_rows
share_cache_rows
```

相同区间再次执行时，如果日期和 ETF 代码覆盖达到门槛，将直接复用结构化缓存。

如果分类规则、报告模板或计算逻辑更新，需要用已有市场数据重算历史日报，而不是只从旧快照重渲染，使用：

```powershell
python -m etf_temperature.cli rebuild `
  --start 20260629 `
  --end 20260703 `
  --recompute
```

`--recompute` 只从本地 `data/cache/` 和 `data/normalized/` 切片重算日报历史，不执行官方预取，不补拉交易所、CNINFO 或 CSRC 数据。适合修复分类、温度、周报聚合和 Markdown 生成逻辑后重建周报基础数据。

重建结果会增加 `formal_report_allowed_by_date`。只要任一交易日仍是预览，命令返回退出码 `2`；这通常表示本地缓存缺少该日新鲜净值/份额，而不是重建过程被静默视为成功。

---

## 6. 生成周报

生成最近一个完整交易周：

```powershell
python -m etf_temperature.cli run-weekly --week auto
```

生成指定 ISO 周：

```powershell
python -m etf_temperature.cli run-weekly --week 2026-W27
```

校验周报：

```powershell
python -m etf_temperature.cli validate-weekly --week 2026-W27
```

周报只有在以下条件满足时才生成正式报告：

- 完整交易周已经结束；
- 数据覆盖到该周最后一个交易日；
- ETF、行情、份额和资金流的代码覆盖率、交易日单元格覆盖率达到配置门槛；
- 全市场资金流规模加权覆盖率不低于 95%，规模占比至少 1% 的重要集群资金流规模覆盖率不低于 90%；
- 分类后的规模、成交额和资金流覆盖率达到配置门槛，且不存在重要未分类 ETF；
- 36 个观察集群完整；
- 核心锚点完整；
- 不存在重复记录或分类语义错误。

如果校验失败：

```text
report_path: null
formal_weekly_allowed: false
```

校验失败时会移除同目录中过期的正式周报和技术附录，防止旧报告在新闸门下仍被误发。周度资金流只在每个交易日的行情、份额、净值时点都合格时才求和；缺一日即记为缺失，不再把部分周数据冒充完整周。

周涨跌幅口径：

```text
有效交易日的 adjusted_daily_return 连乘 - 1
```

无公司行为时，该口径等同于“本周最后一个交易日收盘价 / 上一交易周最后一个交易日收盘价 - 1”。公司行为识别同时核验价格、净值、份额比例和资产规模连续性：高置信度的拆分/合并会调整份额变化与资金流；价格和净值呈拆分比例但份额无法确认的事件标记为 `suspect`；官方份额从正值精确归零时标记为 `liquidation`（清盘/终止类事件）。后两类事件的收益和资金流均为缺失值，不进入排行、集群收益或温度。

日报保留 `raw_daily_return`、`adjusted_daily_return`、`raw_share_daily_change`、`adjusted_share_change`、`raw_estimated_net_subscription_100m`、`corporate_action_factor` 等内部审计字段。公司行为疑似判定不仅要求份额与价格或净值方向相反，还要求价格或净值至少呈现配置比例的反向幅度；份额大增同时价格或净值仅随市场小幅下跌时，继续按真实申购处理。周报默认只使用调整后的周收益和资金流；若集群收益与成员收益中位数或上涨广度明显矛盾，报告会标记“待确认”，不输出强弱结论。阈值配置在 `config/temperature.yaml` 的 `corporate_actions` 区段。

周报输出：

```text
outputs/YYYY-Www/A股ETF资金温度周报_YYYY-Www.md
outputs/YYYY-Www/A股ETF资金结构周报_技术附录_YYYY-Www.md
outputs/YYYY-Www/weekly_etf.parquet
outputs/YYYY-Www/weekly_cluster.parquet
outputs/YYYY-Www/weekly_snapshot.json
outputs/YYYY-Www/weekly_validation.json
```

---

## 7. 日报与周报内容

### 日报

日报正文保留总判断、资金速度与内部结构、数据状态、资金迁移各方向 Top20、背离/集中度和重点 ETF 综合观察 Top20；当符合某一资金方向且通过资金规模覆盖门槛的集群不足 20 个时，正文展示全部符合项并明确实际数量。技术附录保留完整 Top50，包括：

两个正文深度分别由 `config/temperature.yaml` 的 `report.main_cluster_flow_top_n` 和 `report.main_abnormal_top_n` 控制，默认均为 20。

- 36 个观察集群完整总表和各类排名；
- 单日净申购、净赎回排名；
- 5 日净申购、净赎回排名；
- 份额增长、份额减少排名；
- 成交额、成交放大排名；
- 涨幅、跌幅排名；
- 温度、升温、降温排名；
- 价涨份额降、价跌份额增；
- 异常折溢价；
- 异常热度补充池。

报告首先区分价格层、份额层和温度层，不把价格下跌中的份额增加误写成板块价格走强。正文末尾固定披露计算方法、历史样本门槛和使用边界。份额变化率、折溢价等内部小数比例在展示时统一乘以 100，避免 `0.15` 被误写为 `0.15%`。

资金速度以当日对比前4日为主，使用 `flow_1d`、`flow_5d`、`flow_prev4_total`、`flow_prev4_daily_avg`、`flow_acceleration_vs_prev4` 和 `flow_acceleration_ratio_vs_prev4`。`flow_5d` 继续保留为窗口合计；前4日数据不足时标记“方向不明确”，前4日均值接近0时保留绝对加速度但不输出异常倍数。

集群内部结构同时保留毛流入、毛流出、净流量、毛流量和抵消率。净额接近0但毛流量很大时优先标记“高分歧换仓”；资金结构标签再结合金额/规模、份额增加成员比例、最大与前3只同向ETF贡献率和成员数，区分全面扩散、头部集中、全面撤出、金额与广度错位、小样本及低流量状态。成员少于4只不作集中度判断。

C10 观察集群统一命名为“科创100/科创成长”。科创创业类产品仅在跟踪指数代码或明确指数名称命中时纳入；普通创业板、创业板50及与科创板无关的成长产品不进入该集群。

### 周报

周报正文用于识别：

- 大盘和中小盘的周度资金迁移；
- 红利防御与科技成长的周度变化；
- 周初和周末结构差异；
- 本周资金速度及周末相对前一阶段日均的加速、减速和反转；
- 集群毛流入、毛流出、抵消率，以及单边流入与高分歧换仓排名；
- 资金金额与成员广度是否同步改善；
- 单日异动与持续趋势；
- 资金流集中度；
- 资金规模覆盖率达标的集群净申购、净赎回 Top5。

公司行为审计、连续流入/流出、36 个观察集群全表、集群完整排名和单只 ETF Top50 均放入周报技术附录，避免正文重复堆叠榜单。

---

## 8. 温度指标

温度是价格、动量、成交活跃度、份额变化、资金广度和折溢价风险的摘要指标，不是收益预测。

报告同时展示：

```text
temperature
temperature_change_1d
temperature_change_3d
temperature_slope_5d
temperature_level
temperature_direction
temperature_phase
temperature_stage
temperature_confidence
```

温度等级和阶段由温度计算模块一次生成，日报、明日观察和周报只读取这些字段。统一区间为：低温 `<30`、偏冷 `30—45`、中性 `45—60`、偏热 `60—75`、高温 `>=75`。

常见阶段包括：

- 冷启动；
- 持续升温；
- 持续偏热；
- 高温延续；
- 高温降温；
- 中温转强；
- 中温走弱；
- 低温延续。

温度配置位于：

```text
config/temperature.yaml
```

---

## 9. 数据源和回退

主要数据源：

1. 上海证券交易所官方接口；
2. 深圳证券交易所官方接口；
3. 中国证监会基金电子披露网站；
4. 巨潮资讯/深证信；
5. 基金公司官网。

深交所接口优先使用 HTTPS，失败时自动回退同路径 HTTP。深市行情缺口可使用 CNINFO 深证信浏览器 SDK，净值缺口可使用 CSRC-EID。

所有结果尽量保留：

```text
source
source_priority
source_url
retrieved_at
data_date
validation_status
```

---

## 10. 微信公众号草稿发送

项目可以将已经生成并校验通过的 Markdown 日报或周报发送到微信公众号草稿箱。微信公众号模块只负责 Markdown 转换、图片上传和创建草稿，不重新计算报告数据。

本地配置：

```text
.baoyu-skills/.env
```

内容：

```text
WECHAT_APP_ID=...
WECHAT_APP_SECRET=...
```

发送日报示例：

```powershell
$date = "2026-07-02"
$report = "outputs\$date\A股ETF资金温度日报_$date.md"
$cover = "outputs\$date\charts\cover.png"
$summary = "$date A股ETF资金结构日报：区分价格、份额、成交活跃度和估算净申购。"

.\scripts\publish_wechat_checked.ps1 `
  -Report $report `
  -Title "A股ETF资金结构日报 $date" `
  -Summary $summary `
  -Cover $cover `
  -Theme default
```

发送周报前必须先运行：

```powershell
python -m etf_temperature.cli validate-weekly --week 2026-W27
```

只有 `formal_weekly_allowed=true` 时才应创建草稿。

发送周报示例：

```powershell
$week = "2026-W27"
$weekEndDate = "2026-07-03"
$report = "outputs\$week\A股ETF资金温度周报_$week.md"
$cover = "outputs\$weekEndDate\charts\cover.png"
$summary = "$week A股ETF资金结构周报：总结一周价格、份额、温度和集群资金迁移。"

.\scripts\publish_wechat_checked.ps1 `
  -Report $report `
  -Title "A股ETF资金结构周报 $week" `
  -Summary $summary `
  -Cover $cover `
  -Theme default
```

说明：`publish_wechat_checked.ps1` 会先把 Markdown 渲染为微信 HTML 并检查大小，默认阈值为 19,000 bytes。未超限时直接调用微信公众号 API 创建草稿；超限时停止上传并输出报告路径、封面路径和 HTML 预览路径，由你手工上传。脚本不会压缩、删减或改写日报/周报正文。

---

## 11. 常用维护命令

重建 ETF 总池和集群：

```powershell
python -m etf_temperature.cli rebuild-universe
python -m etf_temperature.cli validate-clusters
```

校验核心锚点：

```powershell
python -m etf_temperature.cli validate-anchors --date 2026-07-02
```

审计异常资金流：

```powershell
python -m etf_temperature.cli audit-flows --date 2026-07-02
```

探测官方接口：

```powershell
python -m etf_temperature.cli probe-official
```

重建某日 AI 可读摘要（只读本地缓存，不联网）：

```powershell
python -m etf_temperature.cli make-summary --date 2026-11-04
```

查看 CLI 帮助：

```powershell
python -m etf_temperature.cli --help
python -m etf_temperature.cli run --help
```

常用退出码：

| 退出码 | 含义 |
|---:|---|
| `0` | 校验通过，正式产物允许使用 |
| `1` | 运行错误或周报/审计未通过 |
| `2` | 日报、集群校验或区间重建存在预览日期 |

---

## 12. 项目边界

- 不把成交额等同于资金流入；
- 不把份额增加直接等同于长期配置资金；
- 不将温度作为买卖信号；
- 不预测 ETF 未来涨跌；
- 不输出交易建议；
- 36 个集群是固定观察面板，不代表全部 ETF 经济分类。

---

## 13. 数据缓存与交易所请求边界

### `data/raw` 能否删除

`data/raw/` 保存交易所或权威数据源的原始 HTTP 响应，主要用于审计、排错和 raw 级复用；它不是日报、周报计算主数据。删除 `data/raw/` 不会删除已经规范化的行情、份额、净值、温度或排行榜数据。

不要删除：

```text
data/cache/
data/normalized/
```

这两个目录分别保存结构化增量缓存和规范化计算数据。

### 沪深交易所行情请求边界

行情数据必须在请求边界限定到当前 A 股权益 ETF 观察池：

- 上交所行情使用目标 `sse_codes` 按代码请求；
- 深交所行情使用目标 `szse_codes`，并在 URL 中传入 `txtDMorJC=<ETF代码>`；
- 禁止在常规 `run` 或 `rebuild` 路径中无代码条件拉取深交所全量基金行情后再过滤。

如果本地已经存在完整日报快照，`rebuild` 会跳过官方预取，只从本地快照重渲染报告。预取是否跳过可查看：

```text
outputs/rebuild_prefetch_开始日期_结束日期.json
```

如果需要强制重算已有快照，使用 `rebuild --recompute`。该模式的边界是本地缓存重算，不会因为某个代码缺行情或净值而自动访问外部接口。

### 执行日志

CLI 默认输出进度日志，关键节点包括：

- 数据源模式：`daily source mode=...`；
- 份额缓存检查：`daily shares cache check ...`；
- 行情缺口检查：`daily quotes incremental missing_codes=...`；
- 本地重算边界：`market_data_cache_only=True`；
- 报告生成完成：`daily run finished ... formal_allowed=...`。

日志用于判断程序执行到哪一步；最终机器可读结果仍是命令结束时输出的 JSON。
