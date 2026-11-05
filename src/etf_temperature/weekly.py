from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from .aggregation import build_abnormal_pool
from .anchors import load_core_anchors
from .classification import cluster_frame, load_cluster_rules, semantic_membership_issues
from .classification_quality import classification_coverage
from .config import get_report_config, load_temperature_config
from .flow_structure import (
    flow_direction,
    flow_direction_change,
    flow_offset_metrics,
    flow_speed_metrics,
    flow_structure_label,
)
from .official_endpoints import NORMALIZED_DIR, OUTPUTS_DIR, normalize_date, read_dataframe, write_dataframe
from .snapshot import dataframe_records
from .temperature import add_temperature_state_columns, cluster_temperature_from_raw


@dataclass
class WeeklyResult:
    week: str
    output_dir: Path
    weekly_etf_path: Path
    weekly_cluster_path: Path
    snapshot_path: Path
    validation_path: Path
    report_path: Path | None
    validation: dict


def _numeric_series(frame: pd.DataFrame, column: str, default: float = np.nan) -> pd.Series:
    value = frame[column] if column in frame else pd.Series(default, index=frame.index)
    return pd.to_numeric(value, errors="coerce")


def normalize_week(value: str) -> str:
    if value != "auto":
        return value
    history = read_dataframe(NORMALIZED_DIR / "etf_daily_history.parquet")
    if history.empty:
        raise RuntimeError("etf_daily_history.parquet is empty; run daily pipeline first")
    dates = pd.to_datetime(history["date"], errors="coerce").dropna().dt.date
    if dates.empty:
        raise RuntimeError("etf_daily_history.parquet contains no valid dates")
    candidates = sorted({_week_label(item) for item in dates}, reverse=True)
    today = date.today()
    for candidate in candidates:
        expected_last = _expected_last_trading_day(candidate)
        candidate_dates = dates[[ _week_label(item) == candidate for item in dates ]]
        if today > expected_last and candidate_dates.max() >= expected_last:
            return candidate
    raise RuntimeError("no completed trading week is available; rebuild daily history first")


def week_bounds(week: str) -> tuple[date, date]:
    year_text, week_text = week.split("-W", 1)
    start = date.fromisocalendar(int(year_text), int(week_text), 1)
    return start, start + timedelta(days=6)


def _expected_last_trading_day(week: str) -> date:
    config = load_temperature_config()
    start, _ = week_bounds(week)
    default_end = start + timedelta(days=4)
    calendar_cfg = config.get("trading_calendar", {})
    overrides = calendar_cfg.get("weekly_last_trading_day_overrides", {}) if isinstance(calendar_cfg, dict) else {}
    if isinstance(overrides, dict) and week in overrides:
        return pd.to_datetime(overrides[week]).date()
    return default_end


def _expected_trading_days(week: str) -> int:
    config = load_temperature_config()
    calendar_cfg = config.get("trading_calendar", {})
    overrides = calendar_cfg.get("weekly_expected_trading_days_overrides", {}) if isinstance(calendar_cfg, dict) else {}
    if isinstance(overrides, dict) and week in overrides:
        return int(overrides[week])
    start, _ = week_bounds(week)
    expected_last = _expected_last_trading_day(week)
    return sum(
        1
        for offset in range((expected_last - start).days + 1)
        if (start + timedelta(days=offset)).weekday() < 5
    )


def _expected_trading_days_in_week(week: str) -> list[date]:
    """本周应当有数据的交易日集合。

    以交易日历覆盖为准：从周内最后一个应有交易日向前取 expected_days 个工作日。

    不能直接用「周一到周五」枚举后按周界裁剪：节假日落在周初时（例如 2026-W41
    国庆 10-01~10-07 休市、10-08 才复市），周一到周三并非交易日，枚举法会把
    本周应有交易日算成 5 天，于是全部 ETF 都被判成「周数据不完整」，
    周度资金流整列退化为 NaN，正式周报被误阻断。
    """
    expected_last = _expected_last_trading_day(week)
    expected_days = _expected_trading_days(week)
    days: list[date] = []
    cursor = expected_last
    while len(days) < expected_days:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    return sorted(days)


def _weekly_anchor_metrics(weekly_etf: pd.DataFrame, membership: pd.DataFrame, expected_days: int) -> dict:
    anchors = load_core_anchors()
    etf = weekly_etf.copy()
    if not etf.empty:
        etf["etf_code"] = etf["etf_code"].astype(str).str.zfill(6)
        etf = etf.set_index("etf_code", drop=False)
    members = membership.copy()
    if not members.empty:
        members["etf_code"] = members["etf_code"].astype(str).str.zfill(6)
        members = members.set_index("etf_code", drop=False)

    rows: list[dict] = []
    blocking_codes: list[str] = []
    criteria_total = 0
    criteria_valid = 0

    def inspect(code: str, cluster_id: str, requirement_type: str) -> dict:
        code = str(code).zfill(6)
        assigned = ""
        if not members.empty and code in members.index:
            item = members.loc[code]
            if isinstance(item, pd.DataFrame):
                item = item.iloc[0]
            assigned = str(item.get("primary_cluster") or "")
        exists = not etf.empty and code in etf.index
        quote_ok = share_ok = flow_ok = full_week_ok = False
        if exists:
            item = etf.loc[code]
            if isinstance(item, pd.DataFrame):
                item = item.iloc[-1]
            quote_ok = pd.notna(item.get("week_close"))
            share_ok = pd.notna(item.get("week_end_shares"))
            flow_ok = pd.notna(item.get("weekly_net_subscription_100m"))
            complete_days = item.get("complete_data_days")
            if pd.isna(complete_days):
                complete_days = item.get("trading_days")
            required_days = item.get("expected_data_days")
            if pd.isna(required_days):
                required_days = expected_days
            full_week_ok = int(complete_days or 0) >= int(required_days or expected_days)
        valid = bool(exists and assigned == cluster_id and quote_ok and share_ok and flow_ok and full_week_ok)
        return {
            "etf_code": code,
            "expected_cluster": cluster_id,
            "assigned_cluster": assigned,
            "requirement_type": requirement_type,
            "exists_in_weekly": exists,
            "quote_ok": quote_ok,
            "share_ok": share_ok,
            "flow_ok": flow_ok,
            "full_week_ok": full_week_ok,
            "is_valid": valid,
        }

    for cluster_id, spec in anchors.items():
        required = [str(code).zfill(6) for code in spec.get("required_codes", []) or []]
        required_any = [str(code).zfill(6) for code in spec.get("required_codes_any", []) or []]
        for code in required:
            criteria_total += 1
            row = inspect(code, cluster_id, "required")
            rows.append(row)
            criteria_valid += int(row["is_valid"])
            if not row["is_valid"]:
                blocking_codes.append(code)
        if required_any:
            any_rows = [inspect(code, cluster_id, "required_any") for code in required_any]
            rows.extend(any_rows)
            criteria_total += 1
            any_valid = any(row["is_valid"] for row in any_rows)
            criteria_valid += int(any_valid)
            if not any_valid:
                blocking_codes.extend(required_any)

    audit = pd.DataFrame(rows)
    invalid_codes = list(dict.fromkeys(blocking_codes))
    return {
        "weekly_core_anchor_total": criteria_total,
        "weekly_core_anchor_valid_count": criteria_valid,
        "weekly_core_anchor_coverage": criteria_valid / criteria_total if criteria_total else 0.0,
        "weekly_core_anchor_invalid_codes": invalid_codes,
    }


def _week_label(value: str | date | pd.Timestamp) -> str:
    dt = pd.to_datetime(value).date()
    iso = dt.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _safe_sum(series: pd.Series) -> float:
    values = pd.to_numeric(series, errors="coerce")
    return float(values.sum(min_count=1)) if values.notna().any() else float("nan")


def _first_valid(series: pd.Series):
    values = series.dropna()
    return values.iloc[0] if not values.empty else None


def _last_valid(series: pd.Series):
    values = series.dropna()
    return values.iloc[-1] if not values.empty else None


def _weekly_raw_returns(group: pd.DataFrame) -> pd.Series:
    if "raw_daily_return" in group:
        return pd.to_numeric(group["raw_daily_return"], errors="coerce")
    if "raw_pct_change" in group:
        return pd.to_numeric(group["raw_pct_change"], errors="coerce") / 100
    return pd.to_numeric(group.get("pct_change", pd.Series(np.nan, index=group.index)), errors="coerce") / 100


def _repair_weekly_corporate_action_returns(
    group: pd.DataFrame,
    expected_data_days: int,
) -> tuple[pd.Series, dict[object, dict], bool, str]:
    """Match a detected split factor to the nearby price jump.

    Exchange share/NAV disclosures and the secondary-market ex-right date can be
    one trading day apart.  Daily detection therefore owns the share adjustment,
    while this weekly pass assigns the same factor to the actual price-jump day
    within a small neighbouring window.
    """

    cfg = (load_temperature_config().get("corporate_actions", {}) or {})
    radius = int(cfg.get("weekly_price_match_radius", 2))
    max_adjusted_abs = float(cfg.get("weekly_price_match_max_abs_return", 0.30))
    max_weekly_abs = float(cfg.get("weekly_adjustment_max_abs_return", 0.30))

    effective = pd.to_numeric(
        group.get("adjusted_daily_return", pd.Series(np.nan, index=group.index)),
        errors="coerce",
    ).copy()
    raw = _weekly_raw_returns(group)
    action_type = group.get("corporate_action_type", pd.Series(None, index=group.index))
    event_positions = [
        pos
        for pos, value in enumerate(action_type.tolist())
        if value in {"split", "reverse_split"}
    ]
    repairs: dict[object, dict] = {}
    used_positions: set[int] = set()
    failures: list[str] = []

    for event_pos in event_positions:
        event_index = group.index[event_pos]
        factor = pd.to_numeric(group.iloc[event_pos].get("corporate_action_factor"), errors="coerce")
        if pd.isna(factor) or float(factor) <= 0:
            event_adjusted = effective.iloc[event_pos]
            event_raw = raw.iloc[event_pos]
            if pd.notna(event_adjusted) and (pd.isna(event_raw) or abs(float(event_adjusted) - float(event_raw)) > 1e-9):
                repairs[event_index] = {
                    "status": "adjusted_high_confidence",
                    "effective_price_date": str(group.iloc[event_pos].get("date", "")),
                    "effective_price_row_index": event_index,
                    "adjusted_price_return": float(event_adjusted),
                }
                used_positions.add(event_pos)
                continue
            failures.append(f"{group.iloc[event_pos].get('date')}: invalid factor")
            repairs[event_index] = {"status": "adjustment_failed", "effective_price_date": None}
            continue

        candidates: list[tuple[float, int, int, float]] = []
        left = max(0, event_pos - radius)
        right = min(len(group) - 1, event_pos + radius)
        for price_pos in range(left, right + 1):
            if price_pos in used_positions:
                continue
            raw_return = raw.iloc[price_pos]
            if pd.isna(raw_return) or float(raw_return) <= -1:
                continue
            corrected = (1.0 + float(raw_return)) * float(factor) - 1.0
            if abs(corrected) <= max_adjusted_abs:
                candidates.append((abs(corrected), abs(price_pos - event_pos), price_pos, corrected))

        if not candidates:
            failures.append(f"{group.iloc[event_pos].get('date')}: no nearby price jump matched factor {float(factor):g}")
            repairs[event_index] = {"status": "adjustment_failed", "effective_price_date": None}
            continue

        _, _, price_pos, corrected = min(candidates)
        used_positions.add(price_pos)
        effective.iloc[price_pos] = corrected
        repairs[event_index] = {
            "status": "adjusted_high_confidence",
            "effective_price_date": str(group.iloc[price_pos].get("date", "")),
            "effective_price_row_index": group.index[price_pos],
            "adjusted_price_return": corrected,
        }

    valid_days = int(effective.notna().sum())
    weekly_return = float((1 + effective).prod() - 1) if valid_days >= expected_data_days else float("nan")
    if event_positions and pd.notna(weekly_return) and abs(weekly_return) > max_weekly_abs:
        failures.append(f"adjusted weekly return remains {weekly_return:.2%}")

    return effective, repairs, bool(failures), "; ".join(failures)


def build_weekly_etf(
    week: str,
    history: pd.DataFrame | None = None,
    membership: pd.DataFrame | None = None,
) -> pd.DataFrame:
    history = history if history is not None else read_dataframe(NORMALIZED_DIR / "etf_daily_history.parquet")
    membership = membership if membership is not None else read_dataframe(NORMALIZED_DIR / "etf_cluster_membership.parquet")
    if history.empty:
        return pd.DataFrame()
    history = add_temperature_state_columns(history, "temperature")
    start, end = week_bounds(week)
    df = history.copy()
    df["date_dt"] = pd.to_datetime(df["date"]).dt.date
    first_seen_by_code = df.groupby(df["etf_code"].astype(str))["date_dt"].min().to_dict()
    week_df = df[(df["date_dt"] >= start) & (df["date_dt"] <= end)].copy()
    if week_df.empty:
        return pd.DataFrame()
    prior = df[df["date_dt"] < start].sort_values(["etf_code", "date_dt"])
    prior_weekly_amount = df[df["date_dt"] < start].copy()
    prior_weekly_amount["week"] = prior_weekly_amount["date_dt"].map(_week_label)
    prior_amount = (
        prior_weekly_amount.groupby(["etf_code", "week"], as_index=False)["amount"]
        .sum()
        .sort_values(["etf_code", "week"])
    )

    membership_by_code: dict[str, tuple[str, str]] = {}
    if membership is not None and not membership.empty:
        current = membership.copy()
        current["etf_code"] = current["etf_code"].astype(str).str.zfill(6)
        membership_by_code = {
            str(row["etf_code"]): (
                str(row.get("primary_cluster") or ""),
                str(row.get("primary_cluster_name") or ""),
            )
            for _, row in current.drop_duplicates("etf_code", keep="last").iterrows()
        }

    rows = []
    week_expected_days = _expected_trading_days_in_week(week)
    for code, group in week_df.sort_values(["etf_code", "date_dt"]).groupby("etf_code"):
        group = group.sort_values("date_dt")
        before = prior[prior["etf_code"].astype(str).eq(str(code))].tail(1)
        prev_shares = _last_valid(before["latest_shares"]) if not before.empty and "latest_shares" in before else None
        prev_size = _last_valid(before["fund_size_100m"]) if not before.empty and "fund_size_100m" in before else None
        prev_temp = _last_valid(before["temperature"]) if not before.empty and "temperature" in before else None
        prev_close = _last_valid(before["close"]) if not before.empty and "close" in before else None
        week_open = _first_valid(group["open"])
        week_close = _last_valid(group["close"])
        week_end_shares = _last_valid(group["latest_shares"])
        week_end_size = _last_valid(group["fund_size_100m"])
        week_end_temp = _last_valid(group["temperature"])
        week_amount = _safe_sum(group["amount"])
        code_prior_amount = prior_amount[prior_amount["etf_code"].astype(str).eq(str(code))]["amount"].tail(4)
        prior_amount_mean = code_prior_amount.mean() if not code_prior_amount.empty else float("nan")
        row_date = group["date"].map(normalize_date)
        quote_date = group.get("quote_data_date", pd.Series(None, index=group.index)).map(normalize_date)
        share_date = group.get("share_data_date", pd.Series(None, index=group.index)).map(normalize_date)
        nav_date = group.get("nav_data_date", pd.Series(None, index=group.index)).map(normalize_date)
        max_nav_lag = int(load_temperature_config()["thresholds"].get("daily_max_nav_lag_days", 0))
        nav_lag = (pd.to_datetime(row_date, errors="coerce") - pd.to_datetime(nav_date, errors="coerce")).dt.days
        quote_fresh = group.get("close", pd.Series(np.nan, index=group.index)).notna() & quote_date.eq(row_date)
        share_fresh = group.get("latest_shares", pd.Series(np.nan, index=group.index)).notna() & share_date.eq(row_date)
        nav_fresh = group.get("nav", pd.Series(np.nan, index=group.index)).notna() & nav_lag.ge(0) & nav_lag.le(max_nav_lag)
        flow_status = group.get("flow_validation_status", pd.Series("ok", index=group.index)).fillna("ok")
        flow_fresh = (
            group.get("estimated_net_subscription_100m", pd.Series(np.nan, index=group.index)).notna()
            & quote_fresh
            & share_fresh
            & nav_fresh
            & ~flow_status.eq("unresolved")
        )
        quote_days = int(group.loc[quote_fresh, "date_dt"].nunique())
        share_days = int(group.loc[share_fresh, "date_dt"].nunique())
        flow_days = int(group.loc[flow_fresh, "date_dt"].nunique())
        complete_mask = quote_fresh & share_fresh & flow_fresh
        complete_data_days = int(group.loc[complete_mask, "date_dt"].nunique())
        first_seen = first_seen_by_code.get(str(code), start)
        eligible_start = max(start, first_seen)
        expected_data_days = sum(1 for day in week_expected_days if day >= eligible_start)
        corporate_action_repairs: dict[object, dict] = {}
        corporate_action_adjustment_failed = False
        corporate_action_adjustment_reason = ""
        if "adjusted_daily_return" in group:
            adjusted_returns, corporate_action_repairs, corporate_action_adjustment_failed, corporate_action_adjustment_reason = (
                _repair_weekly_corporate_action_returns(group, expected_data_days)
            )
            raw_returns = _weekly_raw_returns(group)
            return_days = int(adjusted_returns.notna().sum())
            weekly_adjusted_return = (
                float((1 + adjusted_returns).prod() - 1)
                if return_days >= expected_data_days and not corporate_action_adjustment_failed
                else float("nan")
            )
            raw_weekly_return = float((1 + raw_returns).prod() - 1) if raw_returns.notna().sum() >= expected_data_days else float("nan")
        else:
            # Legacy snapshots without daily-return fields remain readable; new
            # production snapshots always take the compound-return branch above.
            return_days = int(group["date_dt"].nunique())
            weekly_adjusted_return = week_close / prev_close - 1 if prev_close not in (None, 0) and pd.notna(week_close) else float("nan")
            raw_weekly_return = weekly_adjusted_return
        weekly_net = _safe_sum(group["estimated_net_subscription_100m"]) if flow_days >= expected_data_days else float("nan")
        if corporate_action_adjustment_failed:
            weekly_net = float("nan")
        valid_daily_flows = pd.to_numeric(
            group.loc[flow_fresh, "estimated_net_subscription_100m"], errors="coerce"
        ).dropna()
        week_start_flow = float(valid_daily_flows.iloc[0]) if not valid_daily_flows.empty else float("nan")
        week_end_flow = float(valid_daily_flows.iloc[-1]) if not valid_daily_flows.empty else float("nan")
        if corporate_action_adjustment_failed:
            week_start_flow = week_end_flow = float("nan")
        speed = flow_speed_metrics(
            week_end_flow,
            weekly_net,
            flow_days,
            required_days=max(expected_data_days, 1),
        )
        adjusted_share_change = pd.to_numeric(
            group.get("adjusted_share_change", group.get("share_daily_change", pd.Series(np.nan, index=group.index))),
            errors="coerce",
        )
        weekly_share_change = _safe_sum(adjusted_share_change) if flow_days >= expected_data_days else float("nan")
        if corporate_action_adjustment_failed:
            weekly_share_change = float("nan")
        action_type = group.get("corporate_action_type", pd.Series(None, index=group.index))
        corporate_action_days = int(action_type.isin(["split", "reverse_split"]).sum())
        corporate_action_suspect_days = int(action_type.eq("suspect").sum())
        corporate_action_events = []
        event_rows = group[action_type.isin(["split", "reverse_split", "suspect"])]
        for event_index, event_row in event_rows.iterrows():
            repair = corporate_action_repairs.get(event_index, {})
            event_type = event_row.get("corporate_action_type")
            if event_type == "suspect":
                result_text = "排除"
            elif repair.get("status") == "adjustment_failed" or corporate_action_adjustment_failed:
                result_text = "调整失败，周度收益和资金流排除"
            else:
                result_text = "高置信度调整"
            adjusted_price_return = repair.get("adjusted_price_return")
            corporate_action_events.append(
                {
                    "date": str(event_row.get("date", "")),
                    "effective_price_date": repair.get("effective_price_date"),
                    "type": event_type,
                    "factor": event_row.get("corporate_action_factor"),
                    "raw_pct_change": event_row.get("raw_pct_change", event_row.get("raw_daily_return", np.nan) * 100),
                    "adjusted_pct_change": (
                        float(adjusted_price_return) * 100
                        if adjusted_price_return is not None and pd.notna(adjusted_price_return)
                        else event_row.get("pct_change")
                    ),
                    "raw_share_change": event_row.get("raw_share_daily_change", event_row.get("share_daily_change")),
                    "adjusted_share_change": event_row.get("adjusted_share_change", event_row.get("share_daily_change")),
                    "result": result_text,
                }
            )
        code_text = str(code).zfill(6)
        current_cluster, current_cluster_name = membership_by_code.get(
            code_text,
            (
                str(_last_valid(group["primary_cluster"]) or ""),
                str(_last_valid(group["primary_cluster_name"]) or ""),
            ),
        )
        rows.append(
            {
                "week": week,
                "week_start": start.strftime("%Y-%m-%d"),
                "week_end": end.strftime("%Y-%m-%d"),
                "week_first_trading_day": group["date_dt"].min().strftime("%Y-%m-%d"),
                "week_last_trading_day": group["date_dt"].max().strftime("%Y-%m-%d"),
                "trading_days": int(group["date_dt"].nunique()),
                "expected_data_days": expected_data_days,
                "quote_days": quote_days,
                "share_days": share_days,
                "flow_days": flow_days,
                "return_days": return_days,
                "complete_data_days": complete_data_days,
                "etf_code": code_text,
                "etf_name": _last_valid(group["etf_name"]),
                "primary_cluster": current_cluster,
                "primary_cluster_name": current_cluster_name,
                "week_open": week_open,
                "week_high": group["high"].max(skipna=True),
                "week_low": group["low"].min(skipna=True),
                "week_close": week_close,
                "previous_week_close": prev_close,
                "raw_weekly_pct_change": raw_weekly_return * 100,
                "adjusted_weekly_pct_change": weekly_adjusted_return * 100,
                "weekly_pct_change": weekly_adjusted_return * 100,
                "weekly_amount_100m": week_amount / 1e8 if pd.notna(week_amount) else float("nan"),
                "weekly_amount_vs_4w": week_amount / prior_amount_mean if pd.notna(prior_amount_mean) and prior_amount_mean else float("nan"),
                "week_end_shares": week_end_shares,
                "weekly_share_change": weekly_share_change,
                "weekly_net_subscription_100m": weekly_net,
                **speed,
                "week_start_flow_100m": week_start_flow,
                "week_end_flow_100m": week_end_flow,
                "week_start_flow_direction": flow_direction(week_start_flow),
                "week_end_flow_direction": flow_direction(week_end_flow),
                "weekly_flow_direction_change": flow_direction_change(week_start_flow, week_end_flow),
                "weekly_net_subscription_to_prev_size": weekly_net / prev_size if prev_size not in (None, 0) and pd.notna(weekly_net) else float("nan"),
                "week_end_size_100m": week_end_size,
                "week_end_temperature": week_end_temp,
                "week_end_temperature_level": _last_valid(group["temperature_level"]),
                "week_end_temperature_direction": _last_valid(group["temperature_direction"]),
                "week_end_temperature_stage": _last_valid(group["temperature_stage"]),
                "temperature_level": _last_valid(group["temperature_level"]),
                "temperature_direction": _last_valid(group["temperature_direction"]),
                "temperature_stage": _last_valid(group["temperature_stage"]),
                "weekly_avg_temperature": group["temperature"].mean(skipna=True),
                "weekly_temperature_change": week_end_temp - prev_temp if prev_temp is not None and pd.notna(week_end_temp) else float("nan"),
                "week_max_temperature": group["temperature"].max(skipna=True),
                "hot_days": int(pd.to_numeric(group["temperature"], errors="coerce").ge(load_temperature_config()["thresholds"]["high_temperature"]).sum()),
                "flow_positive_days": int(pd.to_numeric(group["estimated_net_subscription_100m"], errors="coerce").gt(0).sum()),
                "flow_negative_days": int(pd.to_numeric(group["estimated_net_subscription_100m"], errors="coerce").lt(0).sum()),
                "first_temperature": _first_valid(group["temperature"]),
                "first_temperature_level": _first_valid(group["temperature_level"]),
                "first_temperature_direction": _first_valid(group["temperature_direction"]),
                "first_temperature_stage": _first_valid(group["temperature_stage"]),
                "corporate_action_days": corporate_action_days,
                "corporate_action_suspect_days": corporate_action_suspect_days,
                "corporate_action_events": json.dumps(corporate_action_events, ensure_ascii=False),
                "corporate_action_adjustment_failed": corporate_action_adjustment_failed,
                "corporate_action_adjustment_reason": corporate_action_adjustment_reason,
            }
        )
    return pd.DataFrame(rows)


def _weekly_contributor_stats(subset: pd.DataFrame) -> tuple[str, str, float, float, float, float]:
    """Return the largest same-direction weekly flow contributor."""
    if subset.empty or "weekly_net_subscription_100m" not in subset:
        return "", "", float("nan"), float("nan"), float("nan"), float("nan")
    flows = pd.to_numeric(subset["weekly_net_subscription_100m"], errors="coerce")
    net_flow = flows.sum(min_count=1)
    if pd.isna(net_flow) or abs(float(net_flow)) < 1e-12:
        return "", "", float("nan"), float("nan"), float("nan"), float("nan")
    same_direction = flows[flows.gt(0)] if net_flow > 0 else flows[flows.lt(0)]
    same_direction = same_direction.dropna()
    if same_direction.empty:
        return "", "", float("nan"), float("nan"), float("nan"), float("nan")
    gross_same_direction = float(same_direction.abs().sum())
    ranked = same_direction.abs().sort_values(ascending=False)
    idx = ranked.index[0]
    name = str(subset.loc[idx, "etf_name"]) if "etf_name" in subset and pd.notna(subset.loc[idx, "etf_name"]) else ""
    return (
        str(subset.loc[idx, "etf_code"]),
        name,
        float(flows.loc[idx]),
        float(ranked.iloc[0] / gross_same_direction),
        float(ranked.head(3).sum() / gross_same_direction),
        gross_same_direction if net_flow > 0 else -gross_same_direction,
    )


def build_weekly_cluster(week: str, weekly_etf: pd.DataFrame, daily_history: pd.DataFrame | None = None) -> pd.DataFrame:
    rules = cluster_frame(load_cluster_rules())
    first_day_flow_by_cluster: dict[str, float] = {}
    last_day_flow_by_cluster: dict[str, float] = {}
    flow_days_by_cluster: dict[str, int] = {}
    if daily_history is not None and not daily_history.empty and not weekly_etf.empty:
        start, end = week_bounds(week)
        daily = daily_history.copy()
        daily["date_dt"] = pd.to_datetime(daily["date"], errors="coerce").dt.date
        daily = daily[(daily["date_dt"] >= start) & (daily["date_dt"] <= end)].copy()
        if not daily.empty:
            membership_map = weekly_etf.drop_duplicates("etf_code", keep="last").set_index("etf_code")["primary_cluster"]
            daily["primary_cluster"] = daily["etf_code"].astype(str).map(membership_map)
            valid = ~daily.get("flow_validation_status", pd.Series("ok", index=daily.index)).fillna("ok").isin(
                ["unresolved", "excluded_corporate_action", "stale_source_excluded"]
            )
            valid &= ~daily.get("share_flow_excluded", pd.Series(False, index=daily.index)).fillna(False)
            cluster_days = (
                daily.loc[valid]
                .groupby(["primary_cluster", "date_dt"], as_index=False)["estimated_net_subscription_100m"]
                .sum(min_count=1)
                .dropna(subset=["estimated_net_subscription_100m"])
            )
            for cluster_id, day_group in cluster_days.groupby("primary_cluster"):
                day_group = day_group.sort_values("date_dt")
                first_day_flow_by_cluster[str(cluster_id)] = float(day_group.iloc[0]["estimated_net_subscription_100m"])
                last_day_flow_by_cluster[str(cluster_id)] = float(day_group.iloc[-1]["estimated_net_subscription_100m"])
                flow_days_by_cluster[str(cluster_id)] = int(day_group["date_dt"].nunique())
    rows = []
    for _, rule in rules.iterrows():
        cid = rule["cluster_id"]
        subset = weekly_etf[weekly_etf["primary_cluster"].eq(cid)].copy() if not weekly_etf.empty else pd.DataFrame()
        size = pd.to_numeric(subset.get("week_end_size_100m"), errors="coerce").fillna(0) if not subset.empty else pd.Series(dtype=float)
        total_size = float(size.sum()) if not subset.empty else 0.0
        pct = pd.to_numeric(subset.get("weekly_pct_change"), errors="coerce") if not subset.empty else pd.Series(dtype=float)
        weekly_net = pd.to_numeric(subset.get("weekly_net_subscription_100m"), errors="coerce") if not subset.empty else pd.Series(dtype=float)
        weekly_flow_valid = weekly_net.notna()
        weekly_flow_valid_size = float(size[weekly_flow_valid].sum()) if not subset.empty else 0.0
        amount = pd.to_numeric(subset.get("weekly_amount_100m"), errors="coerce") if not subset.empty else pd.Series(dtype=float)
        amount_vs_4w = pd.to_numeric(subset.get("weekly_amount_vs_4w"), errors="coerce") if not subset.empty else pd.Series(dtype=float)
        share_change = pd.to_numeric(subset.get("weekly_share_change"), errors="coerce") if not subset.empty else pd.Series(dtype=float)
        end_temp = pd.to_numeric(subset.get("week_end_temperature"), errors="coerce") if not subset.empty else pd.Series(dtype=float)
        first_temp = pd.to_numeric(subset.get("first_temperature"), errors="coerce") if not subset.empty else pd.Series(dtype=float)
        weekly_temp_change = pd.to_numeric(subset.get("weekly_temperature_change"), errors="coerce") if not subset.empty else pd.Series(dtype=float)
        return_valid = pct.notna()
        return_size = size[return_valid]
        weighted_pct = float((pct[return_valid] * return_size).sum() / return_size.sum()) if return_size.sum() else float("nan")
        max_code, max_name, max_flow, max_ratio, top3_ratio, same_direction_total = _weekly_contributor_stats(subset)
        weekly_net_total = _safe_sum(weekly_net)
        week_start_flow = first_day_flow_by_cluster.get(cid, float("nan"))
        week_end_flow = last_day_flow_by_cluster.get(cid, float("nan"))
        speed = flow_speed_metrics(
            week_end_flow,
            weekly_net_total,
            flow_days_by_cluster.get(cid, 0),
            required_days=_expected_trading_days(week),
        )
        offset = flow_offset_metrics(
            subset,
            "weekly_net_subscription_100m",
            total_size_100m=total_size,
        )
        min_concentration_members = int(get_report_config().get("min_concentration_member_count", 4))
        median_return = float(pct[return_valid].median()) if return_valid.any() else float("nan")
        up_ratio = float((pct[return_valid] > 0).mean()) if return_valid.any() else float("nan")
        # subset 在整周无数据时是「无列的空表」，subset.get(COL) 返回 None，
        # pd.to_numeric(None) 退化成标量 nan，随后 .fillna 抛 AttributeError。
        # 与下方 hot_days/flow_positive_days 保持同一种守卫写法。
        action_count = (
            int(pd.to_numeric(subset.get("corporate_action_days"), errors="coerce").fillna(0).gt(0).sum())
            if not subset.empty
            else 0
        )
        suspect_count = (
            int(pd.to_numeric(subset.get("corporate_action_suspect_days"), errors="coerce").fillna(0).gt(0).sum())
            if not subset.empty
            else 0
        )
        cluster_return_valid = bool(
            return_valid.any()
            and not (up_ratio >= 0.75 and weighted_pct < -5.0)
            and not (pd.notna(median_return) and abs(weighted_pct - median_return) > 10.0)
        )
        share_increase_ratio = float((share_change.dropna() > 0).mean()) if share_change.notna().any() else float("nan")
        weekly_flow_to_size = weekly_net_total / total_size if total_size and pd.notna(weekly_net_total) else float("nan")
        structure_label = flow_structure_label(
            member_count=len(subset),
            net_flow=offset["net_flow"],
            flow_to_size=weekly_flow_to_size,
            share_increase_member_ratio=share_increase_ratio,
            max_contributor_ratio=max_ratio,
            top3_contributor_ratio=top3_ratio,
            gross_turnover_flow=offset["gross_turnover_flow"],
            flow_offset_ratio=offset["flow_offset_ratio"],
            total_size_100m=total_size,
        )
        rows.append(
            {
                "week": week,
                "cluster_id": cid,
                "cluster_name": rule["cluster_name"],
                "member_count": int(len(subset)),
                "total_size_100m": total_size,
                "estimated_net_subscription_100m": week_end_flow,
                "estimated_net_subscription_5d_100m": weekly_net_total,
                "weekly_net_subscription_100m": weekly_net_total,
                **speed,
                **offset,
                "week_start_flow_100m": week_start_flow,
                "week_end_flow_100m": week_end_flow,
                "week_start_flow_direction": flow_direction(week_start_flow),
                "week_end_flow_direction": flow_direction(week_end_flow),
                "weekly_flow_direction_change": flow_direction_change(week_start_flow, week_end_flow),
                "weekly_net_subscription_to_size": weekly_flow_to_size,
                "weighted_pct_change": weighted_pct,
                "weighted_return_5d": weighted_pct / 100 if pd.notna(weighted_pct) else float("nan"),
                "total_amount_100m": _safe_sum(amount),
                "amount_vs_5d": amount_vs_4w.median(skipna=True),
                "weekly_amount_vs_4w": amount_vs_4w.median(skipna=True),
                "share_increase_member_ratio": share_increase_ratio,
                "member_up_ratio": up_ratio,
                "member_return_median": median_return,
                "corporate_action_member_count": action_count,
                "corporate_action_suspect_count": suspect_count,
                "cluster_return_valid": cluster_return_valid,
                "median_premium_discount": float("nan"),
                "flow_valid_member_count": int(weekly_net.notna().sum()),
                "flow_valid_size_100m": weekly_flow_valid_size,
                "flow_member_coverage": float(weekly_flow_valid.sum() / len(subset)) if len(subset) else 0.0,
                "flow_size_coverage": weekly_flow_valid_size / total_size if total_size else 0.0,
                "one_day_flow_size_coverage": weekly_flow_valid_size / total_size if total_size else 0.0,
                "five_day_flow_size_coverage": weekly_flow_valid_size / total_size if total_size else 0.0,
                "week_end_temperature": end_temp.mean(skipna=True),
                "weekly_avg_temperature": pd.to_numeric(subset.get("weekly_avg_temperature"), errors="coerce").mean(skipna=True) if not subset.empty else float("nan"),
                "weekly_temperature_change": weekly_temp_change.mean(skipna=True),
                "first_temperature": first_temp.mean(skipna=True),
                "week_max_temperature": pd.to_numeric(subset.get("week_max_temperature"), errors="coerce").max(skipna=True) if not subset.empty else float("nan"),
                "hot_days": int(pd.to_numeric(subset.get("hot_days"), errors="coerce").sum()) if not subset.empty else 0,
                "flow_positive_days": int(pd.to_numeric(subset.get("flow_positive_days"), errors="coerce").sum()) if not subset.empty else 0,
                "flow_negative_days": int(pd.to_numeric(subset.get("flow_negative_days"), errors="coerce").sum()) if not subset.empty else 0,
                "max_contributor_code_1d": max_code,
                "max_contributor_name_1d": max_name,
                "max_contributor_flow_1d_100m": max_flow,
                "same_direction_flow_total_1d_100m": same_direction_total,
                "max_contributor_ratio_1d": max_ratio,
                "top3_contributor_ratio_1d": top3_ratio,
                "concentration_sample_valid": bool(len(subset) >= min_concentration_members),
                "flow_structure_label": structure_label,
            }
        )
    return cluster_temperature_from_raw(pd.DataFrame(rows))


def validate_weekly_data(
    week: str,
    weekly_etf: pd.DataFrame,
    weekly_cluster: pd.DataFrame,
    *,
    today: date | None = None,
    history: pd.DataFrame | None = None,
    membership: pd.DataFrame | None = None,
) -> dict:
    config = load_temperature_config()
    thresholds = config["thresholds"]
    universe = read_dataframe(NORMALIZED_DIR / "etf_universe.parquet")
    membership = membership if membership is not None else read_dataframe(NORMALIZED_DIR / "etf_cluster_membership.parquet")
    expected_days = _expected_trading_days(week)
    history = history if history is not None else read_dataframe(NORMALIZED_DIR / "etf_daily_history.parquet")
    expected_count = 0
    expected_cell_count = 0
    if history is not None and not history.empty and {"date", "etf_code"}.issubset(history.columns):
        start, end = week_bounds(week)
        history_dates = pd.to_datetime(history["date"], errors="coerce").dt.date
        expected_rows = history.loc[history_dates.between(start, end), ["date", "etf_code"]].drop_duplicates()
        expected_count = int(expected_rows["etf_code"].astype(str).nunique())
        expected_cell_count = int(len(expected_rows))
    if expected_count == 0:
        expected_count = int(universe["is_a_share_equity_etf"].sum()) if not universe.empty and "is_a_share_equity_etf" in universe else 0
    if not weekly_etf.empty and "expected_data_days" in weekly_etf:
        expected_cell_count = max(
            expected_cell_count,
            int(pd.to_numeric(weekly_etf["expected_data_days"], errors="coerce").fillna(0).sum()),
        )
    if expected_cell_count == 0:
        expected_cell_count = expected_count * expected_days
    trading_days = int(weekly_etf["trading_days"].max()) if not weekly_etf.empty and "trading_days" in weekly_etf else 0
    etf_coverage = len(weekly_etf) / expected_count if expected_count else 0.0
    cluster_count = int(weekly_cluster["cluster_id"].nunique()) if not weekly_cluster.empty else 0
    expected_last_trading_day = _expected_last_trading_day(week)
    today_value = today or date.today()
    week_last_trading_day = (
        pd.to_datetime(weekly_etf["week_last_trading_day"]).max().date()
        if not weekly_etf.empty and "week_last_trading_day" in weekly_etf
        else None
    )
    week_first_trading_day = (
        pd.to_datetime(weekly_etf["week_first_trading_day"]).min().date()
        if not weekly_etf.empty and "week_first_trading_day" in weekly_etf
        else None
    )
    calendar_week_closed = bool(today_value > expected_last_trading_day)
    data_complete_through_week_end = bool(
        week_last_trading_day is not None and week_last_trading_day >= expected_last_trading_day
    )
    complete_trading_week = bool(calendar_week_closed and data_complete_through_week_end)

    if weekly_etf.empty:
        quote_coverage = flow_coverage = full_week_coverage = 0.0
        share_cell_coverage = flow_cell_coverage = 0.0
        flow_size_coverage = 0.0
        missing_flow_size_100m = 0.0
    else:
        quote_ok = _numeric_series(weekly_etf, "week_close").notna()
        flow_ok = _numeric_series(weekly_etf, "weekly_net_subscription_100m").notna()
        complete_days = weekly_etf.get("complete_data_days", weekly_etf.get("trading_days"))
        required_days = weekly_etf.get("expected_data_days", pd.Series(expected_days, index=weekly_etf.index))
        full_week_ok = pd.to_numeric(complete_days, errors="coerce").ge(pd.to_numeric(required_days, errors="coerce"))
        quote_coverage = float(quote_ok.sum() / expected_count) if expected_count else 0.0
        flow_coverage = float(flow_ok.sum() / expected_count) if expected_count else 0.0
        full_week_coverage = float(full_week_ok.sum() / expected_count) if expected_count else 0.0
        share_days = weekly_etf.get("share_days", weekly_etf.get("trading_days"))
        flow_days = weekly_etf.get("flow_days", weekly_etf.get("trading_days"))
        share_cell_coverage = float(pd.to_numeric(share_days, errors="coerce").fillna(0).sum() / expected_cell_count) if expected_cell_count else 0.0
        flow_cell_coverage = float(pd.to_numeric(flow_days, errors="coerce").fillna(0).sum() / expected_cell_count) if expected_cell_count else 0.0
        week_end_size = _numeric_series(weekly_etf, "week_end_size_100m", 0.0).fillna(0)
        total_week_end_size = float(week_end_size.sum())
        valid_flow_size = float(week_end_size[flow_ok].sum())
        flow_size_coverage = valid_flow_size / total_week_end_size if total_week_end_size else 0.0
        missing_flow_size_100m = total_week_end_size - valid_flow_size

    classification_frame = weekly_etf.copy()
    if not classification_frame.empty:
        for column in ["etf_code", "etf_name", "primary_cluster"]:
            if column not in classification_frame:
                classification_frame[column] = ""
        classification_frame["fund_size_100m"] = _numeric_series(classification_frame, "week_end_size_100m")
        classification_frame["amount"] = _numeric_series(classification_frame, "weekly_amount_100m")
        classification_frame["estimated_net_subscription_100m"] = _numeric_series(
            classification_frame, "weekly_net_subscription_100m"
        )
        classification_frame["estimated_net_subscription_5d_100m"] = classification_frame[
            "estimated_net_subscription_100m"
        ]
        for column in ["expanded_name", "tracking_index_name"]:
            if column not in classification_frame:
                source = membership[["etf_code", column]].drop_duplicates("etf_code", keep="last") if column in membership else pd.DataFrame()
                if not source.empty:
                    classification_frame = classification_frame.merge(source, on="etf_code", how="left")
                else:
                    classification_frame[column] = ""
    classification_metrics = classification_coverage(classification_frame)

    material_cluster_threshold = float(thresholds.get("weekly_material_cluster_min_size_share", 0.01))
    minimum_cluster_flow_coverage = float(thresholds.get("weekly_min_material_cluster_flow_size_coverage", 0.90))
    market_size = float(_numeric_series(weekly_etf, "week_end_size_100m", 0.0).fillna(0).sum()) if not weekly_etf.empty else 0.0
    material_clusters = weekly_cluster.copy()
    if not material_clusters.empty:
        material_clusters["size_share"] = _numeric_series(material_clusters, "total_size_100m", 0.0).fillna(0) / market_size if market_size else 0.0
        material_clusters["flow_size_coverage"] = _numeric_series(material_clusters, "flow_size_coverage", 0.0).fillna(0)
        material_clusters = material_clusters[
            material_clusters["size_share"].ge(material_cluster_threshold)
            & _numeric_series(material_clusters, "member_count", 0.0).fillna(0).gt(0)
        ]
    weak_material_clusters = material_clusters[
        material_clusters["flow_size_coverage"].lt(minimum_cluster_flow_coverage)
    ] if not material_clusters.empty else pd.DataFrame()
    material_cluster_flow_size_coverage_min = float(material_clusters["flow_size_coverage"].min()) if not material_clusters.empty else 1.0

    semantic_issues = semantic_membership_issues(membership)
    weekly_membership_mismatch_codes: list[str] = []
    if not weekly_etf.empty and not membership.empty:
        weekly_membership = weekly_etf[["etf_code", "primary_cluster"]].copy()
        weekly_membership["etf_code"] = weekly_membership["etf_code"].astype(str).str.zfill(6)
        current_membership = membership[["etf_code", "primary_cluster"]].copy()
        current_membership["etf_code"] = current_membership["etf_code"].astype(str).str.zfill(6)
        current_membership = current_membership.drop_duplicates("etf_code", keep="last")
        compared = weekly_membership.merge(
            current_membership,
            on="etf_code",
            how="left",
            suffixes=("_weekly", "_current"),
        )
        mismatch = compared[
            compared["primary_cluster_current"].notna()
            & compared["primary_cluster_weekly"].fillna("").ne(compared["primary_cluster_current"].fillna(""))
        ]
        weekly_membership_mismatch_codes = mismatch["etf_code"].drop_duplicates().head(50).tolist()
    anchor_metrics = _weekly_anchor_metrics(weekly_etf, membership, expected_days)
    start, end = week_bounds(week)
    validation = {
        "week": week,
        "week_start": start.strftime("%Y-%m-%d"),
        "week_end": end.strftime("%Y-%m-%d"),
        "week_first_trading_day": week_first_trading_day.strftime("%Y-%m-%d") if week_first_trading_day else None,
        "expected_a_share_equity_etf_count": expected_count,
        "expected_etf_trading_day_cells": expected_cell_count,
        "weekly_etf_count": int(len(weekly_etf)),
        "etf_coverage": float(etf_coverage),
        "weekly_quote_coverage": quote_coverage,
        "weekly_flow_coverage": flow_coverage,
        "weekly_share_cell_coverage": share_cell_coverage,
        "weekly_flow_cell_coverage": flow_cell_coverage,
        "weekly_flow_size_coverage": flow_size_coverage,
        "missing_flow_size_100m": missing_flow_size_100m,
        "material_cluster_flow_size_coverage_min": material_cluster_flow_size_coverage_min,
        "material_cluster_flow_coverage_below_threshold": weak_material_clusters[
            ["cluster_id", "cluster_name", "flow_size_coverage"]
        ].to_dict("records") if not weak_material_clusters.empty else [],
        "full_week_etf_coverage": full_week_coverage,
        "expected_trading_days": expected_days,
        "trading_days": trading_days,
        "trading_week_end": (start + timedelta(days=4)).strftime("%Y-%m-%d"),
        "expected_last_trading_day": expected_last_trading_day.strftime("%Y-%m-%d"),
        "week_last_trading_day": week_last_trading_day.strftime("%Y-%m-%d") if week_last_trading_day else None,
        "calendar_week_closed": calendar_week_closed,
        "data_complete_through_week_end": data_complete_through_week_end,
        "complete_trading_week": complete_trading_week,
        "cluster_count": cluster_count,
        "semantic_membership_issue_count": int(len(semantic_issues)),
        "semantic_membership_issue_codes": semantic_issues.get("etf_code", pd.Series(dtype=str)).astype(str).head(50).tolist(),
        "weekly_membership_mismatch_count": int(len(weekly_membership_mismatch_codes)),
        "weekly_membership_mismatch_codes": weekly_membership_mismatch_codes,
        "has_duplicate_etf": bool(weekly_etf.duplicated(["week", "etf_code"]).any()) if not weekly_etf.empty else False,
        "has_duplicate_cluster": bool(weekly_cluster.duplicated(["week", "cluster_id"]).any()) if not weekly_cluster.empty else False,
    }
    validation.update(anchor_metrics)
    validation.update(classification_metrics)
    # 节假日周的交易日天然少于 5 天，此时以交易日历的 expected_days 为准，
    # 否则中秋/国庆等短周会被“交易日数量不足”永久阻断。全局阈值仍作为常规周的下限。
    required_trading_days = max(1, min(int(thresholds["weekly_min_trading_days"]), int(expected_days)))
    checks = {
        "自然周尚未闭合或数据未到周末": complete_trading_week,
        "交易日数量不足": trading_days >= required_trading_days,
        "ETF覆盖率不足": etf_coverage >= float(thresholds["weekly_min_etf_coverage"]),
        "行情覆盖率不足": quote_coverage >= float(thresholds.get("weekly_min_quote_coverage", 0.98)),
        "资金流只数覆盖率不足": flow_coverage >= float(thresholds.get("weekly_min_flow_coverage", 0.95)),
        "份额单元格覆盖率不足": share_cell_coverage >= float(thresholds.get("weekly_min_share_cell_coverage", 0.98)),
        "资金流单元格覆盖率不足": flow_cell_coverage >= float(thresholds.get("weekly_min_flow_cell_coverage", 0.95)),
        "完整周ETF覆盖率不足": full_week_coverage >= float(thresholds.get("weekly_min_full_week_coverage", 0.98)),
        "资金流有效规模覆盖率不足": flow_size_coverage >= float(thresholds.get("weekly_min_flow_size_coverage", 0.95)),
        "重要集群资金流有效规模覆盖率不足": weak_material_clusters.empty,
        "观察集群数量不足": cluster_count >= int(thresholds["weekly_min_cluster_count"]),
        "核心锚点不完整": validation["weekly_core_anchor_coverage"] == 1.0,
        "存在重要未分类ETF": validation["important_unclassified_count"] == 0,
        "分类规模覆盖率不足": validation["classified_scale_coverage"] >= float(thresholds.get("weekly_min_classified_scale_coverage", 0.95)),
        "分类成交额覆盖率不足": validation["classified_turnover_coverage"] >= float(thresholds.get("weekly_min_classified_turnover_coverage", 0.90)),
        "分类资金流覆盖率不足": validation["classified_flow_coverage"] >= float(thresholds.get("weekly_min_classified_flow_coverage", 0.90)),
        "分类规则与快照不一致": validation["semantic_membership_issue_count"] == 0,
        "周报分类快照错配": validation["weekly_membership_mismatch_count"] == 0,
        "存在重复ETF记录": not validation["has_duplicate_etf"],
        "存在重复集群记录": not validation["has_duplicate_cluster"],
    }
    validation["formal_blockers"] = [label for label, passed in checks.items() if not passed]
    validation["formal_weekly_allowed"] = not validation["formal_blockers"]
    return validation


def build_weekly_snapshot(week: str, weekly_etf: pd.DataFrame, weekly_cluster: pd.DataFrame, validation: dict) -> dict:
    return {
        "week": week,
        "validation": validation,
        "market": {
            "total_size_100m": _safe_sum(weekly_etf.get("week_end_size_100m", pd.Series(dtype=float))),
            "weekly_net_subscription_100m": _safe_sum(weekly_etf.get("weekly_net_subscription_100m", pd.Series(dtype=float))),
            "weekly_amount_100m": _safe_sum(weekly_etf.get("weekly_amount_100m", pd.Series(dtype=float))),
            "weekly_etf_count": int(len(weekly_etf)),
        },
        "top_net_subscription_clusters": weekly_cluster.sort_values("weekly_net_subscription_100m", ascending=False).head(10)[
            ["cluster_id", "cluster_name", "weekly_net_subscription_100m", "cluster_temperature"]
        ].to_dict("records"),
        "top_net_redemption_clusters": weekly_cluster.sort_values("weekly_net_subscription_100m", ascending=True).head(10)[
            ["cluster_id", "cluster_name", "weekly_net_subscription_100m", "cluster_temperature"]
        ].to_dict("records"),
        "weekly_etf": dataframe_records(weekly_etf),
        "weekly_cluster": dataframe_records(weekly_cluster),
    }


def run_weekly(week: str = "auto") -> WeeklyResult:
    from .weekly_report import generate_weekly_report

    week = normalize_week(week)
    output_dir = OUTPUTS_DIR / week
    output_dir.mkdir(parents=True, exist_ok=True)
    history = read_dataframe(NORMALIZED_DIR / "etf_daily_history.parquet")
    membership = read_dataframe(NORMALIZED_DIR / "etf_cluster_membership.parquet")
    weekly_etf = build_weekly_etf(week, history, membership)
    weekly_cluster = build_weekly_cluster(week, weekly_etf, history)
    validation = validate_weekly_data(
        week,
        weekly_etf,
        weekly_cluster,
        history=history,
        membership=membership,
    )
    snapshot = build_weekly_snapshot(week, weekly_etf, weekly_cluster, validation)

    weekly_etf_path = output_dir / "weekly_etf.parquet"
    weekly_cluster_path = output_dir / "weekly_cluster.parquet"
    snapshot_path = output_dir / "weekly_snapshot.json"
    validation_path = output_dir / "weekly_validation.json"
    write_dataframe(weekly_etf, weekly_etf_path)
    write_dataframe(weekly_cluster, weekly_cluster_path)
    snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    validation_path.write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")

    report_path = None
    formal_report_path = output_dir / f"A股ETF资金温度周报_{week}.md"
    appendix_path = output_dir / f"A股ETF资金结构周报_技术附录_{week}.md"
    if validation["formal_weekly_allowed"]:
        report_path = formal_report_path
        generate_weekly_report(week, snapshot, weekly_etf, weekly_cluster, report_path)
    else:
        formal_report_path.unlink(missing_ok=True)
        appendix_path.unlink(missing_ok=True)
    return WeeklyResult(
        week=week,
        output_dir=output_dir,
        weekly_etf_path=weekly_etf_path,
        weekly_cluster_path=weekly_cluster_path,
        snapshot_path=snapshot_path,
        validation_path=validation_path,
        report_path=report_path,
        validation=validation,
    )


def validate_weekly(week: str = "auto") -> dict:
    week = normalize_week(week)
    output_dir = OUTPUTS_DIR / week
    etf_path = output_dir / "weekly_etf.parquet"
    cluster_path = output_dir / "weekly_cluster.parquet"
    weekly_etf = pd.read_parquet(etf_path) if etf_path.exists() else build_weekly_etf(week)
    weekly_cluster = pd.read_parquet(cluster_path) if cluster_path.exists() else build_weekly_cluster(week, weekly_etf)
    history = read_dataframe(NORMALIZED_DIR / "etf_daily_history.parquet")
    membership = read_dataframe(NORMALIZED_DIR / "etf_cluster_membership.parquet")
    validation = validate_weekly_data(
        week,
        weekly_etf,
        weekly_cluster,
        history=history,
        membership=membership,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "weekly_validation.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
    return validation
