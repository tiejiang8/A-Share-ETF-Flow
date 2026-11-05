# Daily summary contracts

Version: `1.0`. Schemas use JSON Schema draft 2020-12.

| Schema | Payload |
| --- | --- |
| `summary.schema.json` | Dated `summary.json` |
| `latest.schema.json` | Top-level `latest.json` pointer |
| `summary_index.schema.json` | Top-level `summary_index.json` |

Unknown numeric observations use `null`. All amounts with the `_100m` suffix are
in 100m yuan. Availability and freshness have separate fields; ratio comparisons
should allow for floating point error near one.

The summary includes scope, validation status, units, aggregate values, clusters,
directional ETF rankings and a content hash. When `scope.synthetic_demo` is true,
`formal_report_allowed` must be false. A schema pass checks structure and types;
the runtime quality gate and source validation remain separate requirements.

Examples are deliberately synthetic. Tests validate the checked-in examples and
fresh output from the current writer against these schemas. See
[AI integration](../docs/AI_INTEGRATION.md) for consumer behavior and versioning.
