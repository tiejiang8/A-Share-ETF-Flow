from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .official_endpoints import NORMALIZED_DIR, normalize_date, read_dataframe


def dataframe_records(frame: pd.DataFrame) -> list[dict]:
    if frame.empty:
        return []
    clean = frame.astype(object).where(pd.notna(frame), None)
    return clean.to_dict("records")


def _json_safe(value):
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (float, np.floating)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, np.integer):
        return int(value)
    if value is pd.NA or (not isinstance(value, (str, bytes)) and pd.isna(value)):
        return None
    return value


def build_daily_snapshot(
    report_date: str,
    etf_daily: pd.DataFrame,
    cluster_daily: pd.DataFrame,
    validation: dict,
) -> dict:
    total_size = pd.to_numeric(etf_daily.get("fund_size_100m"), errors="coerce")
    net_flow = pd.to_numeric(etf_daily.get("flow_1d", etf_daily.get("estimated_net_subscription_100m")), errors="coerce")
    return {
        "date": normalize_date(report_date) or report_date,
        "validation": validation,
        "market": {
            "total_size_100m": float(total_size.sum(min_count=1)) if total_size.notna().any() else None,
            "flow_1d": float(net_flow.sum(min_count=1)) if net_flow.notna().any() else None,
            "etf_count": int(len(etf_daily)),
            "cluster_count": int(len(cluster_daily)),
        },
        "etf_daily": dataframe_records(etf_daily),
        "cluster_daily": dataframe_records(cluster_daily),
    }


def write_snapshot_json(payload: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_json_safe(payload), ensure_ascii=False, indent=2, default=str, allow_nan=False),
        encoding="utf-8",
    )
    return path


@dataclass(frozen=True)
class DailySnapshot:
    """A date-bound view of all derived daily artifacts.

    The pipeline keeps convenient ``current`` parquet files, but validation and
    historical commands must never use those aliases for an explicitly requested
    date.  This value object makes the date binding explicit.
    """

    report_date: str
    etf_daily: pd.DataFrame
    cluster_daily: pd.DataFrame

    def assert_consistent(self) -> None:
        for label, frame, key in [
            ("ETF 日频快照", self.etf_daily, "etf_code"),
            ("集群日频快照", self.cluster_daily, "cluster_id"),
        ]:
            if frame.empty:
                raise RuntimeError(f"{label}缺少 {self.report_date} 数据。")
            if "date" not in frame:
                raise RuntimeError(f"{label}缺少 date 字段。")
            dates = set(frame["date"].map(normalize_date).dropna())
            if dates != {self.report_date}:
                raise RuntimeError(f"{label}日期不一致: expected={self.report_date}, actual={sorted(dates)}")
            if key in frame and frame.duplicated(key).any():
                raise RuntimeError(f"{label}存在重复主键: {key}")

    def membership(self) -> pd.DataFrame:
        columns = [
            "etf_code",
            "exchange",
            "etf_name",
            "expanded_name",
            "tracking_index_code",
            "tracking_index_name",
            "primary_cluster",
            "primary_cluster_name",
            "secondary_tags",
            "classification_basis",
            "confidence",
            "source",
        ]
        available = [column for column in columns if column in self.etf_daily]
        membership = self.etf_daily[available].drop_duplicates("etf_code", keep="last").copy()
        if "primary_cluster" not in membership:
            membership["primary_cluster"] = ""
        return membership


class SnapshotStore:
    def __init__(self, normalized_dir: Path | None = None) -> None:
        self.normalized_dir = Path(normalized_dir or NORMALIZED_DIR)

    @staticmethod
    def _slice(frame: pd.DataFrame, report_date: str) -> pd.DataFrame:
        if frame.empty or "date" not in frame:
            return pd.DataFrame()
        target = normalize_date(report_date) or report_date
        dates = frame["date"].map(normalize_date)
        return frame.loc[dates.eq(target)].copy()

    def load(self, report_date: str) -> DailySnapshot:
        target = normalize_date(report_date)
        if not target:
            raise ValueError(f"invalid report date: {report_date}")
        etf_history = read_dataframe(self.normalized_dir / "etf_daily_history.parquet")
        cluster_history = read_dataframe(self.normalized_dir / "cluster_daily_history.parquet")
        snapshot = DailySnapshot(
            report_date=target,
            etf_daily=self._slice(etf_history, target),
            cluster_daily=self._slice(cluster_history, target),
        )
        snapshot.assert_consistent()
        return snapshot

    def history_trading_days_through(self, report_date: str) -> int:
        target = normalize_date(report_date)
        history = read_dataframe(self.normalized_dir / "etf_daily_history.parquet")
        if not target or history.empty or "date" not in history:
            return 0
        dates = history["date"].map(normalize_date)
        return int(dates.loc[dates.le(target)].dropna().nunique())
