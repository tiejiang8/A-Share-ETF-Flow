# Preparing the GitHub repository

Target repository name: **A-Share-ETF-Flow**, alongside `tiejiang8/FP-DCF`.

Suggested description:

> 看 A 股 ETF 的钱往哪儿走：从上交所、深交所、证监会等网站获取份额、净值和行情，生成资金流向日报、周报和 AI 摘要。

The default [README](../README.md) is in Chinese. An [English version](../README.en.md)
is available separately; the repository About description is primarily Chinese.

Suggested topics: `etf`, `a-shares`, `fund-flows`, `market-structure`, `finance`,
`python`, `llm`.

## Build an inspectable source export

```bash
python -m pytest -q
python scripts/run_offline_example.py
python -m build
python scripts/check_distribution.py
python scripts/prepare_github_release.py
```

The last command creates a new source directory and ZIP under `dist/github/`,
with `RELEASE_MANIFEST.json` listing included files, sizes and SHA-256 hashes.
Text files are exported with LF line endings, and checksums describe the exact
exported bytes. Git attributes retain LF on checkout and archive, including
PowerShell scripts.
It never deletes local data, copies `.git`, or overwrites an existing export.
Use this clean directory for the initial repository upload rather than uploading
the working directory wholesale.

Included: source, default configs, tests, contracts, synthetic examples,
documentation and GitHub CI. Excluded: raw exchange data, CSV/parquet caches,
generated real reports, virtual environments, logs, credentials, local assistant
state and optional integration settings.

## Initial upload

Create an empty GitHub repository named `A-Share-ETF-Flow` in the intended owner
account, then run from the exported directory:

```bash
git init -b main
git add .
git diff --cached --stat
git commit -m "Prepare A-Share-ETF-Flow source release"
git remote add origin https://github.com/tiejiang8/A-Share-ETF-Flow.git
git push -u origin main
```

These are operator instructions. The export command does not create a remote
repository, publish data or push commits. Keep `FP-DCF` unchanged; a sibling
repository does not require merging either codebase.

## License status

No license has been selected for this project. The preparation does not invent
one or change the maintainer's licensing decision. If a license is selected,
add `LICENSE` before exporting; the exporter includes it automatically.

## Verification boundary

Portable CI uses fixtures and mocked responses. It does not prove current
exchange access or historical-cache correctness. Two explicit `local_data`
tests remain outside that suite; the known 2026-07-14 mismatch is retained in
[the changelog](../CHANGELOG.md).
