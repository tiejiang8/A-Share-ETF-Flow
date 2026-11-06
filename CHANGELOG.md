# Changelog

## Unreleased

- Daily collection shares a raw-response index across workers and logs elapsed
  time for each major stage.
- Daily quote caches flag pre-close bars for targeted recollection. Unrepaired
  pre-close data blocks a formal report, while confirmed zero-volume bars are
  eligible.
- Large CSRC NAV gaps use bounded same-date bulk queries with a per-fund
  fallback for the remaining codes.
- Scheduled daily runs reuse complete formal reports and use an operating-system
  lock to prevent concurrent daily writers in the same workspace.

## 0.1.0 — GitHub preparation (2026-11-05)

- Public project name: **A-Share-ETF-Flow**; existing package and CLI names remain
  compatible.
- Added bilingual project guides, a synthetic offline example, JSON output
  contracts, contribution/security notes and portable CI checks.
- Added a source allowlist exporter with a checksum manifest; runtime data and
  local assistant/integration state stay outside the release.
- Bundled default YAML configuration in wheels and selected a writable runtime
  workspace for installed distributions (`ETF_FLOW_HOME`, otherwise the current
  directory; source checkouts retain their existing project root).
- Daily AI summaries distinguish field availability from economic-date freshness,
  publish explicit yuan-to-100m conversion, filter rankings by direction, and
  serialize publication across processes.

### Known local-data regression

`tests/test_flow_structure_metrics.py::test_2026_07_14_daily_report_regression`
expects a historical CSI 1000 flow reversal, while the current local history
produces continued inflow with deceleration. This mismatch predates the summary
fixes and is still unresolved. The original assertion is preserved under the
explicit `local_data` marker. Portable checks do not certify that historical
cache or live exchange availability.

The second `local_data` test checks a historical 512480 corporate-action row.
Neither test can be reproduced from a clean clone without the operator's data.
