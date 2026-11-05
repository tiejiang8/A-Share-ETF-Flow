# A-Share-ETF-Flow

[English](README.md) | [简体中文](README.zh-CN.md)

**Track A-share ETF flows and sector rotation using official disclosures, with automated, auditable daily reports, weekly reports and AI-readable summaries.**

Use it to see which ETFs and sectors receive net creations, whether flows persist
or reverse, how concentrated they are, and whether share changes confirm price
moves. Inputs are disclosed quotes, shares and NAVs; outputs are research reports,
ETF and cluster tables, and structured summaries for AI agents and data pipelines.
Incomplete real-market data produces an explicitly marked preview with blockers.

The project collects exchange and authoritative fund disclosures, keeps dated
source evidence, estimates ETF net creation/redemption, and produces daily and
weekly research reports across **36 configured observation clusters**. Compact
JSON and text summaries make the daily results usable without loading the full
historical snapshot.

| Project | Research question |
| --- | --- |
| [FP-DCF](https://github.com/tiejiang8/FP-DCF) | What business assumptions support a valuation? |
| A-Share-ETF-Flow | Where are ETF shares and estimated creation/redemption flows moving? |

## Quickstart: offline example

Python **3.10+**. Create and activate a virtual environment, then:

```bash
git clone https://github.com/tiejiang8/A-Share-ETF-Flow.git
cd A-Share-ETF-Flow
python -m venv .venv
# Activate .venv with the shell-specific command below.
python -m pip install -e ".[dev]"
python scripts/run_offline_example.py
```

The example uses explicitly **fictional** records and requires no network. Open
`outputs/demo/2026-01-05/llms.txt` or `summary.json`. The example has its own
latest pointer and cannot become a formal market report. Checked-in output is
available under [examples/](examples/README.md).

For PowerShell activation use `.venv\Scripts\Activate.ps1`; for POSIX shells use
`source .venv/bin/activate` after `python -m venv .venv`.

## Collect real data

Run from an environment with working access to the official sources:

```bash
python -m etf_temperature.cli run --date auto
python -m etf_temperature.cli run --date YYYY-MM-DD --refresh-recent-days 2
python -m etf_temperature.cli validate --date YYYY-MM-DD
```

Replace `YYYY-MM-DD` with an actual trading date. Complete snapshots are reused
by default. Recent refreshes check the structured cache before making requests;
they do not unconditionally download every row again. Quote, share and NAV
publication can be asynchronous. The first run may take substantially longer
than an incremental run.

Daily generation and validation return **0** when the formal quality gate
passes, or **2** when a preview is retained because data or validation is
incomplete. Inspect the dated validation artifact; a completed command alone
does not establish data readiness.

Some official quote fallback paths require a browser:

```bash
python -m pip install -e ".[browser,dev]"
python -m playwright install chromium
```

Explicit dates also accept `YYYYMMDD`. See the [operations guide](docs/OPERATIONS.zh-CN.md)
for backfills, complete refreshes, rebuilding and weekly report workflows.

## Outputs

| File | Purpose |
| --- | --- |
| `outputs/<date>/A股ETF资金温度日报_<date>.md` | Daily report, with status and scope |
| `outputs/<date>/validation.md` | Coverage, freshness and formal blockers |
| `outputs/<date>/daily_etf.parquet` | Normalized ETF-level observations |
| `outputs/<date>/daily_cluster.parquet` | Classified cluster aggregates |
| `outputs/<date>/summary.json` | Versioned machine-readable daily summary |
| `outputs/<date>/llms.txt` | Compact text for AI consumption |
| `outputs/<date>/snapshot.md` | Human-readable compact snapshot |
| `outputs/latest.json` | Relative paths and key values for the latest daily summary |
| `outputs/summary_index.json` | One entry per summarized date |

Rebuild summaries without collection or report rendering:

```bash
python -m etf_temperature.cli make-summary --date YYYY-MM-DD
```

This command reads dated local history. It does not refresh market inputs.
Unchanged output preserves bytes and modification times. Cross-process writer
locking protects the index and prevents an older date from replacing the latest
pointer. See [AI integration](docs/AI_INTEGRATION.md) and
[JSON contracts](contracts/README.md). Daily summaries are implemented; weekly
reports currently have no corresponding summary trio.

## Method and data quality

```text
Estimated net creation/redemption (100m yuan)
  = (shares on T - shares on previous trading day) * NAV on T / 100,000,000
```

Shares are in units; NAV is yuan per unit. The five-observation cumulative flow
uses adjusted daily estimates. ETF traded turnover measures secondary-market
activity and is a separate metric. Changes in AUM also contain price effects.

- Preserve raw evidence, source URLs, economic dates and source priorities.
- Distinguish a measured zero from an unavailable flow.
- Publish field availability separately from economic-date freshness.
- Audit corporate actions and extreme flows before admitting them to aggregates.
- Publish both ETF-count and size-weighted coverage.
- Separate ETF-wide totals from classified cluster totals and disclose the
  unclassified contribution.
- Retain a visibly non-formal preview when quality gates fail.

Sources include SSE, SZSE, the CSRC fund disclosure system, CNINFO market-data
services, and verified fund-manager disclosures. Third-party evidence has a
separate validation role. Source details and units are documented in
[official endpoints](docs/official_endpoints.md),
[the data dictionary](docs/data_dictionary.md), and
[cluster methodology](docs/cluster_methodology.md).

The data describes aggregate ETF activity and cannot identify a particular
subscriber such as Central Huijin. Temperature and structure labels are
descriptive observations; predictive performance and profitability are not
established by passing technical validation.

## Project structure

```text
config/                  Authoritative classification and quality policies
contracts/               JSON summary, pointer and index schemas
examples/                Explicitly synthetic offline input and output
src/etf_temperature/     Collection, calculation, report and summary engine
scripts/                 Run, distribution-check and source-export helpers
tests/                   Portable tests plus explicit local-data checks
docs/                    Methodology, AI integration and operations
.github/                 Portable CI and contribution template
data/                    Local source evidence and caches (not published)
outputs/                 Local reports and summaries (not published)
```

Source checkouts retain the project root as their runtime workspace. Installed
wheels use the current directory and include default YAML configuration.
Set `ETF_FLOW_HOME` to use a different writable workspace; a complete `config/`
there overrides bundled defaults. The existing Python distribution name
`a-share-etf-temperature` and command `etf-temperature` remain compatible.

## Development and release

```bash
python -m pytest -q
python -m build
python scripts/check_distribution.py
python scripts/prepare_github_release.py
```

Default tests run without live collection or operator caches. Two tests marked
`local_data` are opt-in via `python -m pytest -q -m local_data`. The known
2026-07-14 historical-cache mismatch remains explicit in [CHANGELOG.md](CHANGELOG.md).
Portable checks do not certify that local history or current exchange access.

The exporter creates a new allowlisted source directory and ZIP with a checksum
manifest. It excludes data, generated real reports, credentials, environments,
logs and assistant state. See [GitHub release preparation](docs/GITHUB_RELEASE.md),
[contributing](CONTRIBUTING.md), and [security](SECURITY.md).

No license has been selected; see the release guide for that status.
