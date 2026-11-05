# 官方接口探测记录

- 生成时间：2026-11-05 09:41:21
- 探测方式：Playwright 打开交易所动态页面并记录 XHR/fetch；随后用 requests 复现接口。
- 约束：交易所官方接口为主数据源，第三方仅用于故障回退或交叉验证。

| 来源 | 名称 | 方法 | URL | 参数 | 用途 |
| --- | --- | --- | --- | --- | --- |
| SSE | ETF stock list | GET | `https://query.sse.com.cn/commonSoaQuery.do` | `sqlId=FUND_LIST&fundType=00` | 上交所股票ETF清单，含代码、简称、管理人、指数代码、指数名称、上市日期。 |
| SSE | ETF share scale | GET | `https://query.sse.com.cn/commonQuery.do` | `sqlId=COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L&STAT_DATE=YYYY-MM-DD` | 上交所ETF指定日期总份额，单位为万份。 |
| SSE | ETF historical K line | GET | `https://yunhq.sse.com.cn:32042/v1/sh1/dayk/{code}` | `select=date,open,high,low,close,volume,amount&begin=-20&end=-1` | 上交所单只ETF日K线，金额单位为元。 |
| SSE | ETF creation redemption basket | GET | `https://query.sse.com.cn/commonQuery.do` | `sqlId=COMMON_SSE_CP_JJLB_ETFJJGK_GGSGSHQD_JBXX_C&FUNDID2={code}` | 上交所ETF申购赎回清单，取上一交易日单位净值NAV。 |
| SZSE | ETF list and latest NAV | GET | `https://fund.szse.cn/api/report/ShowReport` | `SHOWTYPE=xlsx&CATALOGID=fund_etf|1945&TABKEY=tab1|tab2` | 深交所ETF列表及最新净值官方XLSX整表；各一次请求，JSON分页仅作失败回退。 |
| SZSE | ETF share scale | GET | `https://fund.szse.cn/api/report/ShowReport` | `SHOWTYPE=xlsx&CATALOGID=scsj_fund_jjgm&TABKEY=tab1&jjlb=ETF&txtStart=YYYY-MM-DD&txtEnd=YYYY-MM-DD` | 深交所ETF历史基金规模官方XLSX，单位为份；按自然季度分块，每季度一次请求，JSON分页仅作回退。 |
| SZSE | Fund historical quote | GET | `https://www.szse.cn/api/report/ShowReport/data` | `SHOWTYPE=JSON&CATALOGID=1815_stock_snapshot&TABKEY=tab2&loading=first&txtBeginDate=YYYY-MM-DD&txtEndDate=YYYY-MM-DD&PAGENO=n` | 深交所基金历史行情，含前收、开盘、最高、最低、收盘、成交量、成交额；程序优先访问HTTPS，失败时自动回退HTTP同路径。 |
| CNINFO | CNINFO market data SDK | Playwright/NSDK | `https://webapi.cninfo.com.cn/` | `SDK_REQUEST_KLINE; code.SE; Date/Open/High/Low/Close/Volume/Amount` | 深圳证券信息/巨潮深证信数据服务平台浏览器行情SDK；交易所行情接口不可用时补齐ETF日K线。 |
| CSRC-EID | Public fund NAV | GET/POST | `http://eid.csrc.gov.cn/fund/disclose/getPublicFundJZInfoMore.do` | `aoData=DataTables JSON; fundCode={code}; startDate=endDate=YYYY-MM-DD` | 中国证监会基金电子披露网站公募基金净值信息，可作为交易所净值缺口的官方回退源。 |
| CSRC-EID | Public fund announcements | GET | `http://eid.csrc.gov.cn/fund/disclose/advanced_search_report.do` | `aoData=DataTables JSON; reportType/fundCode/date filters` | 中国证监会基金电子披露网站公告查询，用于公司行为、临时公告和净值日报证据链。 |
| CNINFO | Fund disclosure portal | Browser/API | `https://www.cninfo.com.cn/` | `fund/disclosure search filters` | 巨潮资讯网基金公告入口，适合深市ETF公告、申购赎回相关披露和公司行为交叉验证。 |
| FundCo | Fund manager ETF PCF/NAV pages | Browser/API | `基金管理人官网ETF专区` | `fundCode/date` | 基金公司官网ETF申购赎回清单、净值和产品公告；按管理人补齐，不作为全市场行情主源。 |

## 重要口径

- 成交额只表示二级市场换手、活跃度和分歧，不直接等同资金流入。
- 资金流用份额变化乘以统计日单位净值估算，并在报告中标注非精确现金流。
- 一个 ETF 只能有一个主集群，辅助标签不参与重复聚合。
