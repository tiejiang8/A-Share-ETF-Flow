# Contributing to A-Share-ETF-Flow

Keep changes small and preserve the distinction between market prices, ETF share
changes, estimated creation/redemption flows, and traded turnover.

## Development

```bash
python -m venv .venv
# Activate the environment for your shell.
python -m pip install -e ".[dev]"
python -m pytest -q
python scripts/run_offline_example.py
```

Portable checks use mocked or synthetic inputs and require no exchange access.
Tests marked `local_data` are excluded by default. Run them explicitly with
`python -m pytest -q -m local_data` when you have the required historical cache.
The historical 2026-07-14 regression currently has a known mismatch against the
operator's mutable local history; see [release notes](CHANGELOG.md). Do not turn
that assertion into an unconditional pass or distribute the operator's cache.

## Changes that require care

- Preserve economic dates, units, source attribution, nulls and quality gates.
- Update the contracts and examples when a public JSON field changes.
- Keep aggregate scope explicit; unclassified ETFs are not in cluster totals.
- Keep the synthetic demo non-formal and isolated from real report pointers.
- Serialize summary publication with the existing cross-process lock.
- Keep credentials, raw responses, caches, generated reports and assistant state
  outside source commits. Use the allowlisted release exporter before publishing.
- Live network tests are an explicit operator action. CI does not run collection
  jobs, posting scripts, scheduled reports or real-market refreshes.

The runtime package and existing `etf-temperature` command retain their names for
compatibility; the public repository name is `A-Share-ETF-Flow`.
