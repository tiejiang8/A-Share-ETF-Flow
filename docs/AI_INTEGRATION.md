# AI and programmatic integration

Read `outputs/latest.json` first. Resolve its relative paths against the
`outputs/` directory, then load the dated `summary.json` or `llms.txt`.
`snapshot.md` is a compact human-readable view; `summary_index.json` is the
per-date lookup index. None of these files triggers collection when read.

## Contract version 1.0

Schemas live under [contracts/](../contracts/). JSON uses `null` for unknown
numeric values. Small floating point errors around ratios of one can occur;
compare ratios with numerical tolerance.

| Field | Interpretation |
| --- | --- |
| `asof` | Economic date of the report, not the file creation date |
| `formal_report_allowed` | Result of the existing data/report quality gate |
| `formal_blockers` | Reasons the snapshot is non-formal |
| `scope.synthetic_demo` | When true, fictional input; never a real market report |
| `coverage.*_coverage` | Field availability; see the field-specific definition |
| `coverage.quote_fresh_coverage` | Valid same-date quote coverage |
| `coverage.share_fresh_coverage` | Valid same-date share coverage |
| `coverage.nav_fresh_coverage` | NAV coverage within configured date-lag limits; default is same date |
| `market.flow_1d_100m` | ETF-level estimated net creation/redemption, in 100m yuan |
| `market.flow_5d_100m` | Sum of adjusted daily estimated flows over five observations |
| `market.cluster_scope_flow_1d_100m` | Classified cluster scope; excludes unclassified ETFs |
| `market.unclassified_flow_1d_100m` | Unclassified ETF contribution |
| `market.flow_valid_etf_count` | Rows with a usable one-day flow, including measured zero |
| `market.flow_missing_etf_count` | Rows without a usable one-day flow |
| `content_hash` | SHA-256 of the substantive summary payload |

Do not treat field availability as freshness. A stale NAV can contribute to
`nav_coverage` while failing `nav_fresh_coverage`.

Do not combine ETF-scope and cluster-scope totals. Positive ETF rankings contain
only positive flows, and negative rankings only negative flows; fewer than 20
entries is valid. Missing measurements are not zero.

## Safe consumer sequence

1. Check `schema_version` before consuming the payload.
2. Inspect report status, blockers, freshness, missing rows and scope.
3. Check `scope.synthetic_demo` before interpreting example output.
4. Use the explicitly stated 100m-yuan units and formula conversion.
5. If reading during publication, compare the pointer's `content_hash` with the
   dated summary and retry on a mismatch. Writer locking prevents lost updates;
   it does not make several separate files a single reader transaction.

The data cannot identify subscribers as Central Huijin or another institution.
Temperature and structure labels are descriptive research outputs, with no
claimed prediction, profitability validation or trade execution.

`make-summary --date YYYY-MM-DD` rebuilds summaries from local dated history only.
It does not refresh market data and does not create a weekly summary. Weekly
report generation is a separate workflow.
