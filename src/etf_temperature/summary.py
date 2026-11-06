"""AI-readable summary artifacts for the daily report.

Design goals are borrowed from the external ``national-team-etf-flow`` project
(``latest.json`` + ``llms.txt`` + ``snapshot.md``) while deliberately fixing the
defects that review identified in that repository:

1. *Missing values are never rendered as zero.*  Every aggregate is published
   together with the number of contributing rows and the coverage ratio, so a
   consumer can tell "no flow" from "not measured".
2. *The economic date of every input is explicit.*  Quote/share/NAV dates are
   published, and NAV rows whose economic date lags behind the report date are
   listed explicitly instead of being silently reused.
3. *Completeness accompanies the numbers.*  Coverage ratios and missing codes
   are part of the payload, not an afterthought.
4. *No identity claims.*  The payload describes net creation/redemption of the
   whole A-share equity ETF universe; it cannot identify who subscribed.
5. *Byte-stable output.*  When the substance of a summary does not change, the
   files are not rewritten, so downstream diffing stays meaningful.  This is
   the same "no change, no write" idea the external repository uses for git.
6. *Incremental index.*  A per-run index is appended rather than recomputed
   from scratch, mirroring the repository's incremental-cache design.

The module deliberately reads only the official-data outputs of the existing
pipeline.  It never performs network access and never becomes an alternative
source of truth.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from ._atomic import atomic_replace, exclusive_file_lock
from .official_endpoints import OUTPUTS_DIR, normalize_date


SUMMARY_SCHEMA_VERSION = "1.0"

SUMMARY_JSON_NAME = "summary.json"
SUMMARY_LLMS_NAME = "llms.txt"
SUMMARY_SNAPSHOT_NAME = "snapshot.md"
SUMMARY_INDEX_NAME = "summary_index.json"
LATEST_POINTER_NAME = "latest.json"

#: Number of rows published for the ETF level ranking tables.
TOP_ETF_COUNT = 20
#: Number of rows published for the cluster level ranking tables.
TOP_CLUSTER_COUNT = 20

_IDENTITY_NOTE = (
    "本数据为全市场参与者净申购赎回的金额估算，无法识别申购者是否为中央汇金或任何特定机构。"
)
_FLOW_FORMULA = "净申购金额(亿元) = (当日份额(份) - 上一交易日份额(份)) × 当日单位净值(元/份) ÷ 1e8"
_UNIT = "亿元"
_COVERAGE_NOTE = (
    "quote_coverage/share_coverage/nav_coverage 为字段有值率；对应 *_fresh_coverage 为按项目时点规则"
    "校验后的有效率（行情、份额要求当日，净值按允许时滞规则，默认要求当日）；"
    "缺失标的未以零填充，汇总同时给出参与只数。"
)


def _num(frame: pd.DataFrame, column: str) -> pd.Series:
    """Return a numeric series for ``column`` or an empty series when absent.

    ``pd.to_numeric(frame.get(column))`` silently degrades to a scalar NaN when
    the column is missing, which then breaks every chained ``.notna()`` call.
    Guard the column first.
    """
    if frame is None or frame.empty or column not in frame.columns:
        return pd.Series(dtype="float64")
    return pd.to_numeric(frame[column], errors="coerce")


def _bool_mask(frame: pd.DataFrame, column: str, default: bool = False) -> pd.Series:
    """Build an index-aligned boolean mask.

    Returning an *empty* Series when the column is absent would produce an
    unalignable boolean indexer, so a same-length default mask is returned
    instead.  This is the guard required for every "apply/mask as indexer"
    pattern in this codebase.
    """
    if frame is None or frame.empty:
        return pd.Series(dtype=bool)
    if column not in frame.columns:
        return pd.Series(bool(default), index=frame.index, dtype=bool)
    values = frame[column].fillna(default)
    if values.dtype == object:
        values = values.map(lambda value: bool(value) if isinstance(value, (bool, np.bool_)) else default)
    return values.astype(bool)


def _finite(value) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _fmt(value, digits: int = 2, *, signed: bool = False) -> str:
    number = _finite(value)
    if number is None:
        return "-"
    prefix = "+" if signed and number > 0 else ""
    return f"{prefix}{number:,.{digits}f}"


def _fmt_pct(value, digits: int = 2) -> str:
    number = _finite(value)
    if number is None:
        return "-"
    return f"{number * 100:.{digits}f}%"


def _code(value) -> str:
    if value is None or (isinstance(value, float) and value != value):
        return "-"
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text.zfill(6) if text.isdigit() else text


def _json_safe(value):
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float):
        return None if value != value or value in (float("inf"), float("-inf")) else value
    if value is pd.NA:
        return None
    try:
        if not isinstance(value, (str, bytes)) and pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _digest(payload: dict) -> str:
    """Hash the substantive part of a payload, ignoring volatile metadata."""
    material = {key: value for key, value in payload.items() if key not in {"generated_at", "content_hash"}}
    blob = json.dumps(_json_safe(material), ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _write_if_changed(path: Path, text: str) -> bool:
    """Write ``text`` to ``path`` only when the content differs.

    Returns True when the file was actually written.  Preserving the file on
    unchanged content keeps mtimes stable so external watchers can rely on
    "changed file means changed substance".
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            if path.read_text(encoding="utf-8") == text:
                return False
        except (OSError, UnicodeDecodeError):
            pass
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(text, encoding="utf-8")
    atomic_replace(tmp, path)
    return True


def _summary_publication_lock(outputs_root: Path, *, timeout: float = 30.0):
    """Serialize the entire summary publication across processes.

    Atomic replacement protects one file, but not the read/modify/write of the
    index and latest pointer. Keep the lock file in place: deleting it after
    release could let different writers lock different files at the same path.
    The operating system releases the lock if its owner exits unexpectedly.
    """
    return exclusive_file_lock(outputs_root / ".summary.lock", timeout=timeout)


def _effective_flow_frame(etf_daily: pd.DataFrame) -> pd.DataFrame:
    """Rows whose flow figure may be summed.

    Rows flagged ``unresolved``/``excluded_corporate_action`` or excluded by a
    corporate action carry no usable flow and are dropped here rather than
    being counted as zero.
    """
    if etf_daily is None or etf_daily.empty:
        return pd.DataFrame()
    frame = etf_daily.copy()
    excluded = pd.Series(False, index=frame.index, dtype=bool)
    if "flow_validation_status" in frame.columns:
        status = frame["flow_validation_status"].astype("object").where(frame["flow_validation_status"].notna(), "")
        excluded = excluded | status.astype(str).isin(["unresolved", "excluded_corporate_action"])
    excluded = excluded | _bool_mask(frame, "share_flow_excluded", default=False)
    return frame.loc[~excluded].copy()


def _data_date_block(etf_daily: pd.DataFrame, report_date: str) -> dict:
    """Explicit economic dates of the three primary inputs."""
    frame = etf_daily if etf_daily is not None else pd.DataFrame()
    block: dict = {"report_date": report_date}
    for label, column in [("quote", "quote_data_date"), ("share", "share_data_date"), ("nav", "nav_data_date")]:
        if frame.empty or column not in frame.columns:
            block[f"{label}_date"] = None
            block[f"{label}_date_mismatch_count"] = 0
            block[f"{label}_date_mismatch_codes"] = []
            continue
        dates = frame[column].astype("object").where(frame[column].notna(), None)
        same_day = dates.eq(report_date)
        mismatch = dates.notna() & ~same_day
        block[f"{label}_date"] = report_date if bool(same_day.any()) else None
        block[f"{label}_date_mismatch_count"] = int(mismatch.sum())
        block[f"{label}_date_mismatch_codes"] = (
            _code(code) for code in frame.loc[mismatch, "etf_code"].tolist()
        ) if "etf_code" in frame.columns else []
        block[f"{label}_date_mismatch_codes"] = list(block[f"{label}_date_mismatch_codes"])[:50]
    return block


def _cluster_rows(cluster_daily: pd.DataFrame) -> list[dict]:
    if cluster_daily is None or cluster_daily.empty:
        return []
    rows: list[dict] = []
    for _, row in cluster_daily.iterrows():
        rows.append(
            {
                "cluster_id": str(row.get("cluster_id", "")),
                "cluster_name": str(row.get("cluster_name", "")),
                "member_count": int(_finite(row.get("member_count")) or 0),
                "total_size_100m": _finite(row.get("total_size_100m")),
                "flow_1d_100m": _finite(row.get("estimated_net_subscription_100m")),
                "flow_5d_100m": _finite(row.get("estimated_net_subscription_5d_100m")),
                "flow_size_coverage": _finite(row.get("flow_size_coverage")),
                "one_day_flow_member_coverage": _finite(row.get("one_day_flow_member_coverage")),
                "cluster_temperature": _finite(row.get("cluster_temperature")),
                "temperature_level": row.get("temperature_level"),
                "temperature_direction": row.get("temperature_direction"),
                "temperature_phase": row.get("temperature_phase"),
                "flow_structure_label": row.get("flow_structure_label"),
                "structure_judgement": row.get("structure_judgement"),
                "gross_inflow_100m": _finite(row.get("gross_inflow")),
                "gross_outflow_100m": _finite(row.get("gross_outflow")),
                "max_contributor_name_1d": row.get("max_contributor_name_1d"),
                "max_contributor_flow_1d_100m": _finite(row.get("max_contributor_flow_1d_100m")),
                "top3_contributor_ratio_1d": _finite(row.get("top3_contributor_ratio_1d")),
                "weighted_pct_change": _finite(row.get("weighted_pct_change")),
            }
        )
    return rows


def _etf_ranking(etf_daily: pd.DataFrame, ascending: bool, limit: int) -> list[dict]:
    frame = _effective_flow_frame(etf_daily)
    if frame.empty or "estimated_net_subscription_100m" not in frame.columns:
        return []
    flow = _num(frame, "estimated_net_subscription_100m")
    usable = frame.loc[flow.lt(0) if ascending else flow.gt(0)].copy()
    if usable.empty:
        return []
    usable["_flow"] = _num(usable, "estimated_net_subscription_100m")
    usable = usable.sort_values("_flow", ascending=ascending).head(limit)
    rows: list[dict] = []
    for _, row in usable.iterrows():
        rows.append(
            {
                "etf_code": _code(row.get("etf_code")),
                "etf_name": row.get("etf_name"),
                "cluster_name": row.get("primary_cluster_name"),
                "flow_1d_100m": _finite(row.get("estimated_net_subscription_100m")),
                "flow_5d_100m": _finite(row.get("estimated_net_subscription_5d_100m")),
                "fund_size_100m": _finite(row.get("fund_size_100m")),
                "share_daily_change_pct": _finite(row.get("share_daily_change_pct")),
                "nav": _finite(row.get("nav")),
                "nav_data_date": row.get("nav_data_date"),
                "share_data_date": row.get("share_data_date"),
                "pct_change": _finite(row.get("pct_change")),
            }
        )
    return rows


def build_summary(
    report_date: str,
    etf_daily: pd.DataFrame,
    cluster_daily: pd.DataFrame,
    metrics: dict,
) -> dict:
    """Build the machine-readable summary payload for one report date."""
    date_text = normalize_date(report_date) or str(report_date)
    universe = etf_daily if etf_daily is not None else pd.DataFrame()
    effective = _effective_flow_frame(universe)

    flow_1d = _num(effective, "estimated_net_subscription_100m")
    flow_5d = _num(effective, "estimated_net_subscription_5d_100m")
    size = _num(universe, "fund_size_100m")
    turnover = _num(universe, "amount")

    universe_count = int(metrics.get("a_share_equity_etf_count") or len(universe))
    flow_valid_count = int(flow_1d.notna().sum())
    flow_missing_count = max(universe_count - flow_valid_count, 0)

    gross_inflow = float(flow_1d[flow_1d > 0].sum()) if flow_1d.notna().any() else None
    gross_outflow = float(flow_1d[flow_1d < 0].sum()) if flow_1d.notna().any() else None

    cluster_rows = _cluster_rows(cluster_daily)
    cluster_flow_sum = None
    if cluster_rows:
        values = [row["flow_1d_100m"] for row in cluster_rows if row["flow_1d_100m"] is not None]
        cluster_flow_sum = float(sum(values)) if values else None

    unclassified = pd.DataFrame()
    if not universe.empty and "primary_cluster" in universe.columns:
        cluster_labels = universe["primary_cluster"].astype("object").where(universe["primary_cluster"].notna(), "")
        unclassified_mask = cluster_labels.astype(str).str.strip().eq("")
        unclassified = universe.loc[unclassified_mask]
    unclassified_flow = None
    if not unclassified.empty:
        values = _num(unclassified, "estimated_net_subscription_100m")
        unclassified_flow = float(values.sum(min_count=1)) if values.notna().any() else None

    blockers = list(metrics.get("formal_blockers") or [])
    allowed = bool(metrics.get("formal_report_allowed"))

    payload: dict = {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "asof": date_text,
        "report_status": "正式报告" if allowed else "预览/数据待补全",
        "formal_report_allowed": allowed,
        "formal_blockers": blockers,
        "scope": {
            "universe": "A股权益ETF全市场",
            "etf_count": universe_count,
            "cluster_count": len(cluster_rows),
            "identity_note": _IDENTITY_NOTE,
        },
        "method": {
            "net_subscription_formula": _FLOW_FORMULA,
            "unit": _UNIT,
            "share_unit": "份",
            "nav_unit": "元/份",
            "yuan_per_100m": 100_000_000,
            "flow_1d_definition": "单日估算净申购（亿元），份额差（份）×当日单位净值（元/份）÷1e8",
            "flow_5d_definition": "5日累计估算净申购，按逐日净值累加（不是首末日净值直接相乘）",
            "caveats": [
                "估算净申购不等同基金精确现金流，二级市场成交额不能替代该口径。",
                "ETF总规模变化同时包含价格影响，不能直接当作净流入。",
                "份额发生拆分/折算的标的按公司行为单独处理，不做粗粒度置零。",
            ],
        },
        "coverage": {
            "quote_coverage": _finite(metrics.get("quote_coverage")),
            "share_coverage": _finite(metrics.get("share_coverage")),
            "nav_coverage": _finite(metrics.get("nav_coverage")),
            "quote_fresh_coverage": _finite(metrics.get("quote_fresh_coverage")),
            "share_fresh_coverage": _finite(metrics.get("share_fresh_coverage")),
            "nav_fresh_coverage": _finite(metrics.get("nav_fresh_coverage")),
            "flow_member_coverage": _finite(metrics.get("flow_member_coverage")),
            "flow_size_coverage": _finite(metrics.get("flow_size_coverage")),
            "weighted_share_coverage": _finite(metrics.get("weighted_share_coverage")),
            "missing_flow_size_100m": _finite(metrics.get("missing_flow_size_100m")),
            "official_ratio": _finite(metrics.get("official_ratio")),
            "browser_fallback_count": int(metrics.get("browser_fallback_count") or 0),
            "third_party_fallback_count": int(metrics.get("third_party_fallback_count") or 0),
            "date_mismatch_count": int(metrics.get("date_mismatch_count") or 0),
            "stale_quote_count": int(metrics.get("stale_quote_count") or 0),
            "stale_share_count": int(metrics.get("stale_share_count") or 0),
            "stale_nav_count": int(metrics.get("stale_nav_count") or 0),
            "max_nav_lag_days": int(metrics.get("max_nav_lag_days") or 0),
            "extreme_flow_trigger_count": int(metrics.get("extreme_flow_trigger_count") or 0),
            "extreme_flow_unresolved_count": int(metrics.get("extreme_flow_unresolved_count") or 0),
            "unclassified_etf_count": int(metrics.get("unclassified_count") or 0),
            "missing_quote_codes": [str(code) for code in (metrics.get("missing_quote_codes") or [])],
            "missing_share_codes": [str(code) for code in (metrics.get("missing_share_codes") or [])],
            "missing_nav_codes": [str(code) for code in (metrics.get("missing_nav_codes") or [])],
            "coverage_note": _COVERAGE_NOTE,
        },
        "data_dates": _data_date_block(universe, date_text),
        "market": {
            "total_size_100m": _finite(size.sum(min_count=1)) if size.notna().any() else None,
            "flow_1d_100m": _finite(flow_1d.sum(min_count=1)) if flow_1d.notna().any() else None,
            "flow_5d_100m": _finite(flow_5d.sum(min_count=1)) if flow_5d.notna().any() else None,
            "gross_inflow_100m": gross_inflow,
            "gross_outflow_100m": gross_outflow,
            "turnover_100m": _finite(turnover.sum(min_count=1) / 1e8) if turnover.notna().any() else None,
            "flow_valid_etf_count": flow_valid_count,
            "flow_missing_etf_count": flow_missing_count,
            "cluster_scope_flow_1d_100m": cluster_flow_sum,
            "unclassified_flow_1d_100m": unclassified_flow,
            "scope_note": (
                "全市场单日估算净申购以ETF层口径为准；集群层合计不含未分类ETF，两者存在口径差异，"
                "差额即未分类ETF的净申购。"
            ),
        },
        "clusters": cluster_rows,
        "top_inflow_etfs": _etf_ranking(universe, ascending=False, limit=TOP_ETF_COUNT),
        "top_outflow_etfs": _etf_ranking(universe, ascending=True, limit=TOP_ETF_COUNT),
    }
    if metrics.get("synthetic_demo"):
        payload["scope"]["synthetic_demo"] = True
        payload["scope"]["universe"] = "合成ETF样本（离线演示，非真实市场数据）"
        payload["formal_report_allowed"] = False
        payload["report_status"] = "预览/数据待补全"
        payload["formal_blockers"] = [*blockers, "合成演示数据，不能作为真实市场报告"]
    payload["content_hash"] = _digest(payload)
    return payload


def _render_llms(payload: dict) -> str:
    market = payload.get("market", {})
    coverage = payload.get("coverage", {})
    dates = payload.get("data_dates", {})
    lines: list[str] = []
    lines.append(f"# A股ETF资金结构日报 摘要 {payload.get('asof')}")
    lines.append("")
    lines.append(f"报告状态：{payload.get('report_status')}；正式报告阻断项：{'；'.join(map(str, payload.get('formal_blockers') or [])) or '无'}。")
    lines.append("")
    lines.append("## 口径")
    lines.append("")
    lines.append(f"- 范围：{payload['scope']['universe']}，{payload['scope']['etf_count']} 只 ETF，{payload['scope']['cluster_count']} 个集群。")
    lines.append(f"- 公式：{payload['method']['net_subscription_formula']}（单位 {payload['method']['unit']}）。")
    lines.append(f"- {payload['method']['flow_5d_definition']}。")
    lines.append(f"- 重要限制：{payload['scope']['identity_note']}")
    for caveat in payload.get("method", {}).get("caveats", []):
        lines.append(f"- {caveat}")
    lines.append("")
    lines.append("## 全市场")
    lines.append("")
    lines.append("| 指标 | 读数 |")
    lines.append("|:---|---:|")
    lines.append(f"| A股权益ETF总规模 | {_fmt(market.get('total_size_100m'), 1)} {_UNIT} |")
    lines.append(f"| 单日估算净申购 | {_fmt(market.get('flow_1d_100m'), 2, signed=True)} {_UNIT} |")
    lines.append(f"| 5日估算净申购 | {_fmt(market.get('flow_5d_100m'), 2, signed=True)} {_UNIT} |")
    lines.append(f"| 单日整体流入 | {_fmt(market.get('gross_inflow_100m'), 1)} {_UNIT} |")
    lines.append(f"| 单日整体流出 | {_fmt(abs(market.get('gross_outflow_100m')) if market.get('gross_outflow_100m') is not None else None, 1)} {_UNIT} |")
    lines.append(f"| 总成交额 | {_fmt(market.get('turnover_100m'), 1)} {_UNIT} |")
    lines.append(f"| 资金流有效只数 | {market.get('flow_valid_etf_count')} 只（缺失 {market.get('flow_missing_etf_count')} 只，未填零） |")
    lines.append(f"| 集群口径单日净申购 | {_fmt(market.get('cluster_scope_flow_1d_100m'), 2, signed=True)} {_UNIT} |")
    lines.append(f"| 未分类ETF单日净申购 | {_fmt(market.get('unclassified_flow_1d_100m'), 2, signed=True)} {_UNIT} |")
    lines.append("")
    lines.append("## 数据完整率与时点")
    lines.append("")
    lines.append(f"- 字段有值率：行情 {_fmt_pct(coverage.get('quote_coverage'))}，份额 {_fmt_pct(coverage.get('share_coverage'))}，净值 {_fmt_pct(coverage.get('nav_coverage'))}。")
    lines.append(f"- 时点有效率：行情 {_fmt_pct(coverage.get('quote_fresh_coverage'))}，份额 {_fmt_pct(coverage.get('share_fresh_coverage'))}，净值 {_fmt_pct(coverage.get('nav_fresh_coverage'))}（按项目时点规则，净值默认要求当日）。")
    lines.append(f"- 资金流只数覆盖率 {_fmt_pct(coverage.get('flow_member_coverage'))}，规模加权覆盖率 {_fmt_pct(coverage.get('flow_size_coverage'))}。")
    lines.append(f"- 官方来源占比 {_fmt_pct(coverage.get('official_ratio'))}，浏览器兜底 {coverage.get('browser_fallback_count')} 条，第三方兜底 {coverage.get('third_party_fallback_count')} 条。")
    lines.append(f"- 数据经济日期：行情 {dates.get('quote_date') or '-'}，份额 {dates.get('share_date') or '-'}，净值 {dates.get('nav_date') or '-'}；净值滞后 {coverage.get('stale_nav_count')} 只，最大滞后 {coverage.get('max_nav_lag_days')} 天。")
    lines.append(f"- 极端资金流触发 {coverage.get('extreme_flow_trigger_count')} 只，未解决 {coverage.get('extreme_flow_unresolved_count')} 只。")
    lines.append(f"- {coverage.get('coverage_note')}")
    lines.append("")
    lines.append("## 集群单日估算净申购（前列）")
    lines.append("")
    lines.append("| 集群 | 单日净申购(亿元) | 5日净申购(亿元) | 规模覆盖率 | 成员数 | 温度 |")
    lines.append("|:---|---:|---:|---:|---:|---:|")
    ranked = sorted(
        [row for row in payload.get("clusters", []) if row.get("flow_1d_100m") is not None],
        key=lambda row: row["flow_1d_100m"],
        reverse=True,
    )
    for row in ranked[:TOP_CLUSTER_COUNT]:
        lines.append(
            f"| {row.get('cluster_name')} | {_fmt(row.get('flow_1d_100m'), 1, signed=True)} | "
            f"{_fmt(row.get('flow_5d_100m'), 1, signed=True)} | {_fmt_pct(row.get('flow_size_coverage'))} | "
            f"{row.get('member_count')} | {_fmt(row.get('cluster_temperature'), 1)} |"
        )
    lines.append("")
    lines.append("## 单只ETF单日估算净申购 Top")
    lines.append("")
    lines.append("| 代码 | 名称 | 集群 | 单日净申购(亿元) | 规模(亿元) | 净值日期 |")
    lines.append("|:---|:---|:---|---:|---:|:---|")
    for row in payload.get("top_inflow_etfs", []):
        lines.append(
            f"| {row.get('etf_code')} | {row.get('etf_name')} | {row.get('cluster_name')} | "
            f"{_fmt(row.get('flow_1d_100m'), 2, signed=True)} | {_fmt(row.get('fund_size_100m'), 1)} | {row.get('nav_data_date') or '-'} |"
        )
    lines.append("")
    lines.append("## 单只ETF单日估算净赎回 Top")
    lines.append("")
    lines.append("| 代码 | 名称 | 集群 | 单日净申购(亿元) | 规模(亿元) | 净值日期 |")
    lines.append("|:---|:---|:---|---:|---:|:---|")
    for row in payload.get("top_outflow_etfs", []):
        lines.append(
            f"| {row.get('etf_code')} | {row.get('etf_name')} | {row.get('cluster_name')} | "
            f"{_fmt(row.get('flow_1d_100m'), 2, signed=True)} | {_fmt(row.get('fund_size_100m'), 1)} | {row.get('nav_data_date') or '-'} |"
        )
    lines.append("")
    return "\n".join(lines) + "\n"


def _render_snapshot(payload: dict) -> str:
    market = payload.get("market", {})
    coverage = payload.get("coverage", {})
    lines: list[str] = []
    lines.append(f"# A股ETF资金结构快照 {payload.get('asof')}")
    lines.append("")
    if payload.get("scope", {}).get("synthetic_demo"):
        lines.append("> 合成演示数据；代码、名称和数字均为虚构，不能作为真实市场报告。")
        lines.append("")
    lines.append(f"- 报告状态：{payload.get('report_status')}")
    lines.append(f"- 范围：{payload['scope']['etf_count']} 只 A股权益ETF / {payload['scope']['cluster_count']} 个集群")
    lines.append(f"- 单日估算净申购：**{_fmt(market.get('flow_1d_100m'), 2, signed=True)} {_UNIT}**（有效 {market.get('flow_valid_etf_count')} 只 / 缺失 {market.get('flow_missing_etf_count')} 只）")
    lines.append(f"- 5日估算净申购：**{_fmt(market.get('flow_5d_100m'), 2, signed=True)} {_UNIT}**")
    lines.append(f"- 总规模：{_fmt(market.get('total_size_100m'), 1)} {_UNIT}；总成交额 {_fmt(market.get('turnover_100m'), 1)} {_UNIT}")
    lines.append(
        f"- 字段有值率：行情 {_fmt_pct(coverage.get('quote_coverage'))} / 份额 {_fmt_pct(coverage.get('share_coverage'))} / 净值 {_fmt_pct(coverage.get('nav_coverage'))}；资金流规模加权覆盖率 {_fmt_pct(coverage.get('flow_size_coverage'))}"
    )
    lines.append(
        f"- 时点有效率：行情 {_fmt_pct(coverage.get('quote_fresh_coverage'))} / 份额 {_fmt_pct(coverage.get('share_fresh_coverage'))} / 净值 {_fmt_pct(coverage.get('nav_fresh_coverage'))}（按项目时点规则，净值默认要求当日）"
    )
    lines.append(f"- 限制：{payload['scope']['identity_note']}")
    lines.append("")
    lines.append("## 集群流向")
    lines.append("")
    lines.append("| 集群 | 单日净申购(亿元) | 5日净申购(亿元) | 温度 | 结构标签 |")
    lines.append("|:---|---:|---:|---:|:---|")
    ranked = sorted(
        [row for row in payload.get("clusters", []) if row.get("flow_1d_100m") is not None],
        key=lambda row: row["flow_1d_100m"],
        reverse=True,
    )
    for row in ranked:
        lines.append(
            f"| {row.get('cluster_name')} | {_fmt(row.get('flow_1d_100m'), 1, signed=True)} | "
            f"{_fmt(row.get('flow_5d_100m'), 1, signed=True)} | {_fmt(row.get('cluster_temperature'), 1)} | {row.get('flow_structure_label') or '-'} |"
        )
    lines.append("")
    return "\n".join(lines) + "\n"


def write_summary_artifacts(
    output_dir: Path,
    report_date: str,
    etf_daily: pd.DataFrame,
    cluster_daily: pd.DataFrame,
    metrics: dict,
    *,
    outputs_dir: Path | None = None,
) -> dict:
    """Write the summary artifacts and refresh the top-level pointer/index.

    Returns a dict describing what was produced and whether each file changed.
    """
    output_dir = Path(output_dir)
    outputs_root = Path(outputs_dir or OUTPUTS_DIR)
    payload = build_summary(report_date, etf_daily, cluster_daily, metrics)

    json_text = json.dumps(_json_safe(payload), ensure_ascii=False, indent=2, default=str, allow_nan=False) + "\n"
    llms_text = _render_llms(payload)
    snapshot_text = _render_snapshot(payload)

    with _summary_publication_lock(outputs_root):
        written = {
            SUMMARY_JSON_NAME: _write_if_changed(output_dir / SUMMARY_JSON_NAME, json_text),
            SUMMARY_LLMS_NAME: _write_if_changed(output_dir / SUMMARY_LLMS_NAME, llms_text),
            SUMMARY_SNAPSHOT_NAME: _write_if_changed(output_dir / SUMMARY_SNAPSHOT_NAME, snapshot_text),
        }
        index_changed = _update_summary_index(outputs_root, payload)
        pointer_changed = _update_latest_pointer(outputs_root, payload, output_dir)

    return {
        "report_date": payload["asof"],
        "summary_path": str(output_dir / SUMMARY_JSON_NAME),
        "llms_path": str(output_dir / SUMMARY_LLMS_NAME),
        "snapshot_path": str(output_dir / SUMMARY_SNAPSHOT_NAME),
        "index_path": str(outputs_root / SUMMARY_INDEX_NAME),
        "latest_pointer_path": str(outputs_root / LATEST_POINTER_NAME),
        "content_hash": payload["content_hash"],
        "files_changed": written,
        "index_changed": index_changed,
        "latest_pointer_changed": pointer_changed,
    }


def _update_summary_index(outputs_root: Path, payload: dict) -> bool:
    """Append the current run to the incremental index.

    The index is append-only per report date: a re-run for the same date
    replaces that date's entry instead of duplicating it, so the file keeps
    growing by one row per trading day rather than being rebuilt.
    The caller must hold ``_summary_publication_lock``.
    """
    index_path = outputs_root / SUMMARY_INDEX_NAME
    entries: list[dict] = []
    previous_document: dict = {}
    if index_path.exists():
        try:
            previous_document = json.loads(index_path.read_text(encoding="utf-8"))
            entries = list(previous_document.get("entries", []))
        except (OSError, ValueError, json.JSONDecodeError):
            previous_document = {}
            entries = []
    asof = payload["asof"]
    market = payload.get("market", {})
    coverage = payload.get("coverage", {})
    entry = {
        "asof": asof,
        "report_status": payload.get("report_status"),
        "formal_report_allowed": payload.get("formal_report_allowed"),
        "flow_1d_100m": market.get("flow_1d_100m"),
        "flow_5d_100m": market.get("flow_5d_100m"),
        "total_size_100m": market.get("total_size_100m"),
        "flow_valid_etf_count": market.get("flow_valid_etf_count"),
        "flow_missing_etf_count": market.get("flow_missing_etf_count"),
        "flow_size_coverage": coverage.get("flow_size_coverage"),
        "quote_coverage": coverage.get("quote_coverage"),
        "share_coverage": coverage.get("share_coverage"),
        "nav_coverage": coverage.get("nav_coverage"),
        "quote_fresh_coverage": coverage.get("quote_fresh_coverage"),
        "share_fresh_coverage": coverage.get("share_fresh_coverage"),
        "nav_fresh_coverage": coverage.get("nav_fresh_coverage"),
        "extreme_flow_unresolved_count": coverage.get("extreme_flow_unresolved_count"),
        "content_hash": payload.get("content_hash"),
    }
    replaced = False
    for index, existing in enumerate(entries):
        if existing.get("asof") == asof:
            entries[index] = entry
            replaced = True
            break
    if not replaced:
        entries.append(entry)
    entries.sort(key=lambda item: str(item.get("asof", "")))
    # The index carries a timestamp, but it must only advance when the rows
    # themselves change.  Otherwise every re-run would rewrite the file and the
    # "no change, no write" guarantee would be void.
    previous_entries = previous_document.get("entries", [])
    if previous_entries == entries:
        return False
    document = {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "entries": entries,
    }
    text = json.dumps(_json_safe(document), ensure_ascii=False, indent=2, default=str, allow_nan=False) + "\n"
    return _write_if_changed(index_path, text)


def _update_latest_pointer(outputs_root: Path, payload: dict, output_dir: Path) -> bool:
    """Point ``outputs/latest.json`` at the newest report date.

    The pointer never moves backwards: re-running an older date must not
    replace a newer pointer.
    The caller must hold ``_summary_publication_lock``.
    """
    pointer_path = outputs_root / LATEST_POINTER_NAME
    asof = payload["asof"]
    if pointer_path.exists():
        try:
            previous = json.loads(pointer_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            previous = {}
        previous_asof = str(previous.get("asof", ""))
        if previous_asof and previous_asof > asof:
            return False
    market = payload.get("market", {})
    coverage = payload.get("coverage", {})
    document = {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "asof": asof,
        "report_status": payload.get("report_status"),
        "formal_report_allowed": payload.get("formal_report_allowed"),
        "formal_blockers": payload.get("formal_blockers"),
        "flow_1d_100m": market.get("flow_1d_100m"),
        "flow_5d_100m": market.get("flow_5d_100m"),
        "total_size_100m": market.get("total_size_100m"),
        "flow_valid_etf_count": market.get("flow_valid_etf_count"),
        "flow_missing_etf_count": market.get("flow_missing_etf_count"),
        "flow_size_coverage": coverage.get("flow_size_coverage"),
        "quote_coverage": coverage.get("quote_coverage"),
        "share_coverage": coverage.get("share_coverage"),
        "nav_coverage": coverage.get("nav_coverage"),
        "quote_fresh_coverage": coverage.get("quote_fresh_coverage"),
        "share_fresh_coverage": coverage.get("share_fresh_coverage"),
        "nav_fresh_coverage": coverage.get("nav_fresh_coverage"),
        "coverage_note": coverage.get("coverage_note"),
        "identity_note": payload.get("scope", {}).get("identity_note"),
        "content_hash": payload.get("content_hash"),
        "summary_path": str((output_dir / SUMMARY_JSON_NAME).relative_to(outputs_root)).replace("\\", "/"),
        "llms_path": str((output_dir / SUMMARY_LLMS_NAME).relative_to(outputs_root)).replace("\\", "/"),
        "snapshot_path": str((output_dir / SUMMARY_SNAPSHOT_NAME).relative_to(outputs_root)).replace("\\", "/"),
    }
    text = json.dumps(_json_safe(document), ensure_ascii=False, indent=2, default=str, allow_nan=False) + "\n"
    return _write_if_changed(pointer_path, text)
