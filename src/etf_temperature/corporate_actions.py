from __future__ import annotations

from statistics import median

import numpy as np
import pandas as pd
import yaml

from .config import load_temperature_config
from .official_endpoints import CONFIG_DIR, normalize_date


def _number(value: object) -> float:
    value = pd.to_numeric(value, errors="coerce")
    return float(value) if pd.notna(value) else float("nan")


def _ratio(current: object, previous: object) -> float:
    current_value = _number(current)
    previous_value = _number(previous)
    if not np.isfinite(current_value) or not np.isfinite(previous_value) or previous_value <= 0:
        return float("nan")
    return current_value / previous_value


def _corporate_action_config() -> dict:
    config = load_temperature_config()
    return {
        "share_ratio_upper": 1.25,
        "share_ratio_lower": 0.80,
        "nav_product_tolerance": 0.08,
        "price_product_tolerance": 0.10,
        "aum_ratio_lower": 0.75,
        "aum_ratio_upper": 1.30,
        "factor_snap_tolerance": 0.08,
        "suspect_min_reciprocal_move_fraction": 0.40,
        "ex_rights_min_move": 0.30,
        "flat_ratio_tolerance": 0.10,
        "common_factors": [0.1, 0.2, 0.25, 1 / 3, 0.5, 2.0, 3.0, 4.0, 5.0, 10.0],
        **(config.get("corporate_actions", {}) or {}),
    }


def load_official_terminations(path: object = None) -> dict[str, dict]:
    """Load officially confirmed terminations keyed by 6-digit code.

    A fund entering liquidation stops publishing NAV and freezes its share
    balance, but its shares are not cancelled until the liquidation completes,
    so the ``share_ratio == 0`` rule in :func:`detect_etf_corporate_action`
    cannot see it.  This table supplies the official evidence instead.
    """

    config_path = CONFIG_DIR / "official_terminations.yaml"
    if path is not None:
        config_path = path
    if not config_path.exists():
        return {}
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    records = payload.get("terminations") or []
    terminations: dict[str, dict] = {}
    for record in records:
        code = str(record.get("etf_code") or "").strip().zfill(6)
        effective = normalize_date(record.get("effective_date"))
        source_url = str(record.get("source_url") or "").strip()
        if code.strip("0") and effective and source_url:
            terminations[code] = dict(record, effective_date=effective, source_url=source_url)
    return terminations


def _official_termination_event(code: object, date_value: object, terminations: dict[str, dict]) -> dict | None:
    """Return a liquidation event when the code is officially terminated as of ``date_value``."""

    if not terminations:
        return None
    key = str(code).strip().zfill(6)
    record = terminations.get(key)
    if not record:
        return None
    date_text = normalize_date(date_value)
    if not date_text or date_text < str(record["effective_date"]):
        return None
    return {
        "corporate_action_flag": True,
        "corporate_action_type": "liquidation",
        "corporate_action_factor": float("nan"),
        "corporate_action_price_factor": 1.0,
        "corporate_action_confidence": 1.0,
        "corporate_action_reason": (
            f"官方公告确认自 {record['effective_date']} 起进入终止/清算程序：{record.get('reason', '')}".strip()
        ),
        "share_ratio": float("nan"),
        "price_ratio": float("nan"),
        "nav_ratio": float("nan"),
        "price_share_product": float("nan"),
        "nav_share_product": float("nan"),
        "aum_ratio": float("nan"),
    }


def _snap_factor(factor: float, cfg: dict) -> float:
    if not np.isfinite(factor) or factor <= 0:
        return float("nan")
    common = [float(item) for item in cfg.get("common_factors", [])]
    if not common:
        return factor
    nearest = min(common, key=lambda item: abs(item / factor - 1.0))
    return nearest if abs(nearest / factor - 1.0) <= float(cfg["factor_snap_tolerance"]) else factor


def _is_common_factor(factor: float, cfg: dict) -> bool:
    if not np.isfinite(factor) or factor <= 0:
        return False
    return any(abs(float(candidate) / factor - 1.0) <= float(cfg["factor_snap_tolerance"]) for candidate in cfg.get("common_factors", []))


def _reciprocal_move_fraction(observed_ratio: float, share_ratio: float) -> float:
    """Measure how much of the reciprocal split move is visible in price or NAV."""

    if not np.isfinite(observed_ratio) or observed_ratio <= 0 or not np.isfinite(share_ratio) or share_ratio <= 0:
        return 0.0
    expected_ratio = 1.0 / share_ratio
    expected_log_move = abs(np.log(expected_ratio))
    if expected_log_move == 0:
        return 0.0
    observed_log_move = abs(np.log(observed_ratio))
    return float(observed_log_move / expected_log_move)


def _detect_ex_rights_price_adjustment(
    share_ratio: float, price_ratio: float, nav_ratio: float, cfg: dict
) -> dict | None:
    """Detect the ex-rights session of a split whose share/NAV leg landed earlier.

    An ETF share split records the multiplied balance on the registration date,
    but the secondary-market price is only ex-adjusted on a later session.  On
    that session shares and NAV are both flat while the close jumps by exactly
    the split factor.  Because an A-share ETF cannot move more than its daily
    price limit (20% at most), a jump of that size is structurally impossible as
    a market move and must be neutralised rather than reported as a crash.
    """

    if not (np.isfinite(share_ratio) and np.isfinite(price_ratio) and np.isfinite(nav_ratio)):
        return None
    if price_ratio <= 0 or share_ratio <= 0:
        return None
    flat_tolerance = float(cfg["flat_ratio_tolerance"])
    if abs(share_ratio - 1.0) > flat_tolerance or abs(nav_ratio - 1.0) > flat_tolerance:
        return None
    if abs(price_ratio - 1.0) < float(cfg["ex_rights_min_move"]):
        return None
    factor = _snap_factor(1.0 / price_ratio, cfg)
    if not _is_common_factor(factor, cfg):
        return None
    return {
        "corporate_action_flag": True,
        "corporate_action_type": "ex_rights",
        "corporate_action_factor": factor,
        "corporate_action_price_factor": factor,
        "corporate_action_confidence": 0.90,
        "corporate_action_reason": "份额与净值均无变化，但收盘价按常见拆分比例跳变，判定为拆分除权日的价格除权，已还原真实涨跌。",
    }


def detect_etf_corporate_action(
    previous_close: object,
    current_close: object,
    previous_nav: object,
    current_nav: object,
    previous_shares: object,
    current_shares: object,
) -> dict:
    """Classify a split/reverse split using independent official daily fields.

    A price jump alone is deliberately insufficient: a real sell-off must keep its
    return.  A high-confidence event needs an inverse share movement plus a
    continuous NAV-based AUM (or, when NAV is unavailable, the equivalent price
    proxy).
    """

    cfg = _corporate_action_config()
    share_ratio = _ratio(current_shares, previous_shares)
    price_ratio = _ratio(current_close, previous_close)
    nav_ratio = _ratio(current_nav, previous_nav)
    price_share_product = price_ratio * share_ratio if np.isfinite(price_ratio) and np.isfinite(share_ratio) else float("nan")
    nav_share_product = nav_ratio * share_ratio if np.isfinite(nav_ratio) and np.isfinite(share_ratio) else float("nan")
    aum_ratio = nav_share_product

    result = {
        "corporate_action_flag": False,
        "corporate_action_type": None,
        "corporate_action_factor": float("nan"),
        "corporate_action_confidence": 0.0,
        "corporate_action_reason": "",
        "share_ratio": share_ratio,
        "price_ratio": price_ratio,
        "nav_ratio": nav_ratio,
        "price_share_product": price_share_product,
        "nav_share_product": nav_share_product,
        "aum_ratio": aum_ratio,
        "corporate_action_price_factor": 1.0,
    }
    if not np.isfinite(share_ratio):
        return result

    # An exchange-reported share balance that falls exactly to zero cannot be
    # interpreted as an ordinary ETF redemption.  It is a terminal-state
    # signal (fund liquidation/contract termination) or an unusable source row;
    # in both cases price return and estimated flow must be excluded rather than
    # reported as a 100% redemption.
    if share_ratio == 0.0:
        result.update(
            {
                "corporate_action_flag": True,
                "corporate_action_type": "liquidation",
                "corporate_action_confidence": 0.95,
                "corporate_action_reason": "官方份额由正值归零，属于清盘/终止类信号，不能按正常申购赎回解释，已剔除收益与资金流。",
            }
        )
        return result

    candidate = share_ratio >= float(cfg["share_ratio_upper"]) or share_ratio <= float(cfg["share_ratio_lower"])
    price_opposes = np.isfinite(price_ratio) and ((share_ratio > 1 and price_ratio < 1) or (share_ratio < 1 and price_ratio > 1))
    nav_opposes = np.isfinite(nav_ratio) and ((share_ratio > 1 and nav_ratio < 1) or (share_ratio < 1 and nav_ratio > 1))
    price_factor = _snap_factor(1.0 / price_ratio, cfg) if np.isfinite(price_ratio) and price_ratio > 0 else float("nan")
    nav_factor = _snap_factor(1.0 / nav_ratio, cfg) if np.isfinite(nav_ratio) and nav_ratio > 0 else float("nan")
    price_nav_split_like = (
        np.isfinite(price_ratio)
        and np.isfinite(nav_ratio)
        and _is_common_factor(price_factor, cfg)
        and _is_common_factor(nav_factor, cfg)
        and abs(price_ratio / nav_ratio - 1.0) <= float(cfg["price_product_tolerance"])
        and (price_factor >= float(cfg["share_ratio_upper"]) or price_factor <= float(cfg["share_ratio_lower"]))
    )
    if not candidate or not (price_opposes or nav_opposes):
        if price_nav_split_like:
            result.update(
                {
                    "corporate_action_flag": True,
                    "corporate_action_type": "suspect",
                    "corporate_action_factor": price_factor,
                    "corporate_action_confidence": 0.55,
                    "corporate_action_reason": "价格与净值同步呈常见拆分比例，但份额未出现可验证的反向比例变化，已排除等待公告确认。",
                }
            )
            return result
        ex_rights = _detect_ex_rights_price_adjustment(share_ratio, price_ratio, nav_ratio, cfg)
        if ex_rights is not None:
            result.update(ex_rights)
        return result

    nav_consistent = np.isfinite(nav_share_product) and abs(nav_share_product - 1.0) <= float(cfg["nav_product_tolerance"])
    price_consistent = np.isfinite(price_share_product) and abs(price_share_product - 1.0) <= float(cfg["price_product_tolerance"])
    aum_continuous = np.isfinite(aum_ratio) and float(cfg["aum_ratio_lower"]) <= aum_ratio <= float(cfg["aum_ratio_upper"])
    price_aum_continuous = np.isfinite(price_share_product) and float(cfg["aum_ratio_lower"]) <= price_share_product <= float(cfg["aum_ratio_upper"])

    candidates = [share_ratio]
    if nav_consistent and np.isfinite(nav_ratio) and nav_ratio > 0:
        candidates.append(1.0 / nav_ratio)
    if price_consistent and np.isfinite(price_ratio) and price_ratio > 0:
        candidates.append(1.0 / price_ratio)
    factor = _snap_factor(float(median(candidates)), cfg)

    # NAV confirmation is preferred.  Price confirmation remains useful for an
    # otherwise complete quote/share record when the NAV is temporarily absent.
    high_confidence = (nav_consistent and aum_continuous) or (
        not np.isfinite(nav_ratio) and price_consistent and price_aum_continuous
    )
    if high_confidence:
        event_type = "split" if factor > 1 else "reverse_split"
        confidence = 0.95 if nav_consistent and aum_continuous else 0.80
        result.update(
            {
                "corporate_action_flag": True,
                "corporate_action_type": event_type,
                "corporate_action_factor": factor,
                "corporate_action_price_factor": factor if price_consistent else 1.0,
                "corporate_action_confidence": confidence,
                "corporate_action_reason": "份额比例与价格/净值反向变化相互验证，且调整前后资产规模连续。",
            }
        )
        return result

    reciprocal_move_fraction = max(
        _reciprocal_move_fraction(price_ratio, share_ratio) if price_opposes else 0.0,
        _reciprocal_move_fraction(nav_ratio, share_ratio) if nav_opposes else 0.0,
    )
    if reciprocal_move_fraction < float(cfg["suspect_min_reciprocal_move_fraction"]):
        return result

    result.update(
        {
            "corporate_action_flag": True,
            "corporate_action_type": "suspect",
            "corporate_action_factor": factor,
            "corporate_action_price_factor": factor if price_consistent else 1.0,
            "corporate_action_confidence": 0.45,
            "corporate_action_reason": "份额与价格或净值出现反向跳变，但资产规模或比例连续性不足，已按不确定事件处理。",
        }
    )
    return result


def _ensure_raw(out: pd.DataFrame, columns: list[str]) -> None:
    for column in columns:
        if column in out and f"raw_{column}" not in out:
            out[f"raw_{column}"] = out[column]


def apply_corporate_action_adjustments(etf_daily: pd.DataFrame) -> pd.DataFrame:
    if etf_daily.empty:
        return etf_daily
    out = etf_daily.copy()
    if "pct_change" not in out:
        close = pd.to_numeric(out.get("close"), errors="coerce")
        previous_close = pd.to_numeric(out.get("prev_close"), errors="coerce")
        out["pct_change"] = (close / previous_close.replace(0, np.nan) - 1) * 100
    _ensure_raw(
        out,
        [
            "pct_change",
            "share_daily_change",
            "share_daily_change_pct",
            "share_5d_change",
            "share_5d_change_pct",
            "estimated_net_subscription_100m",
            "estimated_net_subscription_5d_100m",
        ],
    )
    out["raw_daily_return"] = pd.to_numeric(out.get("raw_pct_change", out.get("pct_change")), errors="coerce") / 100
    out["adjusted_daily_return"] = out["raw_daily_return"]
    out["adjusted_nav_return"] = pd.to_numeric(out.get("nav"), errors="coerce") / pd.to_numeric(out.get("previous_nav"), errors="coerce") - 1
    out["adjusted_previous_shares"] = pd.to_numeric(out.get("previous_shares"), errors="coerce")
    out["adjusted_share_change"] = pd.to_numeric(out.get("share_daily_change"), errors="coerce")
    out["estimated_net_subscription_100m"] = (
        out["adjusted_share_change"] * pd.to_numeric(out.get("nav"), errors="coerce") / 1e8
    )
    out["corporate_action_flag"] = False
    out["corporate_action_type"] = None
    out["corporate_action_factor"] = np.nan
    out["corporate_action_confidence"] = 0.0
    out["corporate_action_reason"] = ""
    out["corporate_action_status"] = "normal"
    out["corporate_action_review_required"] = False
    out["share_flow_excluded"] = False

    terminations = load_official_terminations()
    for idx, row in out.iterrows():
        event = _official_termination_event(row.get("etf_code"), row.get("date"), terminations)
        if event is None:
            event = detect_etf_corporate_action(
                row.get("prev_close"), row.get("close"), row.get("previous_nav"), row.get("nav"), row.get("previous_shares"), row.get("latest_shares")
            )
        for key in [
            "corporate_action_flag",
            "corporate_action_type",
            "corporate_action_factor",
            "corporate_action_price_factor",
            "corporate_action_confidence",
            "corporate_action_reason",
            "share_ratio",
            "price_ratio",
            "nav_ratio",
            "price_share_product",
            "nav_share_product",
            "aum_ratio",
        ]:
            out.loc[idx, key] = event[key]
        if event["corporate_action_type"] in {"split", "reverse_split"}:
            factor = event["corporate_action_factor"]
            previous_shares = _number(row.get("previous_shares"))
            current_shares = _number(row.get("latest_shares"))
            previous_close = _number(row.get("prev_close"))
            current_close = _number(row.get("close"))
            previous_nav = _number(row.get("previous_nav"))
            current_nav = _number(row.get("nav"))
            adjusted_previous = previous_shares * factor
            adjusted_change = current_shares - adjusted_previous
            price_factor = event["corporate_action_price_factor"]
            adjusted_return = current_close * price_factor / previous_close - 1 if previous_close > 0 else np.nan
            adjusted_nav_return = current_nav * factor / previous_nav - 1 if previous_nav > 0 else np.nan
            out.loc[idx, "adjusted_previous_shares"] = adjusted_previous
            out.loc[idx, "adjusted_share_change"] = adjusted_change
            out.loc[idx, "share_daily_change"] = adjusted_change
            out.loc[idx, "share_daily_change_pct"] = adjusted_change / adjusted_previous if adjusted_previous else np.nan
            out.loc[idx, "adjusted_daily_return"] = adjusted_return
            out.loc[idx, "adjusted_nav_return"] = adjusted_nav_return
            out.loc[idx, "pct_change"] = adjusted_return * 100
            out.loc[idx, "estimated_net_subscription_100m"] = adjusted_change * current_nav / 1e8
            out.loc[idx, "corporate_action_status"] = "adjusted_high_confidence"
            out.loc[idx, "corporate_action_review_required"] = True
        elif event["corporate_action_type"] == "ex_rights":
            # Only the price leg is ex-adjusted here; the share leg already landed
            # on the registration date, so estimated flow must stay untouched.
            previous_close = _number(row.get("prev_close"))
            current_close = _number(row.get("close"))
            adjusted_return = current_close * event["corporate_action_price_factor"] / previous_close - 1 if previous_close > 0 else np.nan
            out.loc[idx, "adjusted_daily_return"] = adjusted_return
            out.loc[idx, "pct_change"] = adjusted_return * 100
            out.loc[idx, "corporate_action_status"] = "ex_rights_adjusted"
            out.loc[idx, "corporate_action_review_required"] = True
        elif event["corporate_action_type"] in {"suspect", "liquidation"}:
            for column in [
                "pct_change",
                "adjusted_daily_return",
                "adjusted_nav_return",
                "share_daily_change",
                "share_daily_change_pct",
                "share_5d_change",
                "share_5d_change_pct",
                "adjusted_share_change",
                "estimated_net_subscription_100m",
                "estimated_net_subscription_5d_100m",
            ]:
                if column in out:
                    out.loc[idx, column] = np.nan
            out.loc[idx, "corporate_action_status"] = (
                "liquidation_excluded" if event["corporate_action_type"] == "liquidation" else "suspect_excluded"
            )
            out.loc[idx, "corporate_action_review_required"] = True
            out.loc[idx, "share_flow_excluded"] = True

    return out


def apply_adjusted_history_features(etf_daily: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
    """Use adjusted daily returns/flows for rolling indicators and temperature.

    The caller supplies the already-materialized dates before the target date;
    therefore a sequential rebuild recalculates the same deterministic history.
    """

    if etf_daily.empty:
        return etf_daily
    current = etf_daily.copy()
    current_dates = set(current.get("date", pd.Series(dtype=str)).astype(str))
    prior = history.copy() if history is not None and not history.empty else pd.DataFrame()
    if not prior.empty and "date" in prior:
        prior = prior[~prior["date"].astype(str).isin(current_dates)].copy()
    combined = pd.concat([prior, current], ignore_index=True, sort=False)
    if combined.empty or not {"date", "etf_code"}.issubset(combined.columns):
        return current
    combined["date_dt"] = pd.to_datetime(combined["date"], errors="coerce")
    combined = combined.sort_values(["etf_code", "date_dt"])
    combined["adjusted_daily_return"] = pd.to_numeric(combined.get("adjusted_daily_return"), errors="coerce")
    combined["estimated_net_subscription_100m"] = pd.to_numeric(combined.get("estimated_net_subscription_100m"), errors="coerce")
    combined["adjusted_share_change"] = pd.to_numeric(combined.get("adjusted_share_change", combined.get("share_daily_change")), errors="coerce")
    combined["return_5d"] = combined.groupby("etf_code")["adjusted_daily_return"].transform(
        lambda values: (1 + values).rolling(5, min_periods=5).apply(np.prod, raw=True) - 1
    )
    combined["return_10d"] = combined.groupby("etf_code")["adjusted_daily_return"].transform(
        lambda values: (1 + values).rolling(10, min_periods=10).apply(np.prod, raw=True) - 1
    )
    combined["estimated_net_subscription_5d_100m"] = combined.groupby("etf_code")["estimated_net_subscription_100m"].transform(
        lambda values: values.rolling(5, min_periods=5).sum()
    )
    combined["flow_5d_valid_days"] = combined.groupby("etf_code")["estimated_net_subscription_100m"].transform(
        lambda values: values.rolling(5, min_periods=1).count()
    )
    combined["share_5d_change"] = combined.groupby("etf_code")["adjusted_share_change"].transform(
        lambda values: values.rolling(5, min_periods=5).sum()
    )
    # `share_5d_change` is rebuilt from *adjusted* daily share changes, but the
    # matching percentage still comes from the unadjusted 5-day-ago balance.
    # For a split/reverse split the two bases disagree: a 1:2 split reports
    # +106% while the adjusted change is only +2.7%.  Recompute it against the
    # adjusted base so the percentage always matches its own numerator.
    adjusted_5d_change = pd.to_numeric(combined.get("share_5d_change"), errors="coerce")
    base_5d = pd.to_numeric(combined.get("latest_shares"), errors="coerce") - adjusted_5d_change
    recomputed_5d_pct = adjusted_5d_change / base_5d.replace(0, np.nan)
    if "share_5d_change_pct" in combined:
        current_5d_pct = pd.to_numeric(combined.get("share_5d_change_pct"), errors="coerce")
        usable = adjusted_5d_change.notna() & base_5d.gt(0)
        combined["share_5d_change_pct"] = current_5d_pct.where(~usable, recomputed_5d_pct)
    result = combined[combined["date"].astype(str).isin(current_dates)].copy()
    result = result.sort_values(["date", "etf_code"]).drop(columns=["date_dt"], errors="ignore")
    return result.reset_index(drop=True)


def flag_share_jumps(etf_daily: pd.DataFrame, threshold: float | None = None) -> pd.DataFrame:
    if etf_daily.empty:
        return pd.DataFrame()
    cfg = _corporate_action_config()
    threshold = float(cfg["share_ratio_upper"]) - 1 if threshold is None else threshold
    raw_change = pd.to_numeric(etf_daily.get("raw_share_daily_change_pct", etf_daily.get("share_daily_change_pct")), errors="coerce")
    flagged = etf_daily[
        raw_change.abs().gt(threshold)
        | etf_daily.get("corporate_action_flag", pd.Series(False, index=etf_daily.index)).fillna(False)
    ].copy()
    if flagged.empty:
        return flagged
    flagged["review_reason"] = flagged.get("corporate_action_reason", "")
    flagged["corporate_action_review_required"] = True
    return flagged
