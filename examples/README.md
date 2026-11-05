# Offline synthetic example

Every code, name and value in `synthetic_input.json` is fictional. This is a
software example, not a dated market assessment or investment recommendation.

```bash
python scripts/run_offline_example.py
```

The example exercises the real cluster aggregation and summary writer with four
synthetic ETF rows, including a zero flow, a missing flow and a stale NAV. It
writes to `outputs/demo/`, which has its own latest pointer and index. Existing
real-market reports and their pointers are not touched.

Checked-in output under `synthetic_output/` demonstrates the same JSON and text
contracts. Its status is explicitly non-formal. Expected totals: net flow **7**,
gross inflow **10**, gross outflow **-3** (all in 100m yuan); NAV availability
**100%**, economic-date freshness **75%**.
