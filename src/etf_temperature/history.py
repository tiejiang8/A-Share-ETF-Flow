from __future__ import annotations

from pathlib import Path

import pandas as pd

from .official_endpoints import write_dataframe


def upsert_history(existing: pd.DataFrame, new_rows: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    if new_rows.empty:
        return existing.copy()
    if existing.empty:
        combined = new_rows.copy()
    else:
        combined = pd.concat([existing, new_rows], ignore_index=True, sort=False)
    combined = combined.drop_duplicates(keys, keep="last")
    return combined.sort_values(keys).reset_index(drop=True)


def upsert_history_file(path: Path, new_rows: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    existing = pd.read_parquet(path) if path.exists() else pd.DataFrame()
    partition_keys = keys[:-1]
    if not existing.empty and not new_rows.empty and partition_keys:
        partition_values = new_rows[partition_keys].drop_duplicates()
        existing = existing.merge(
            partition_values.assign(_replace_partition=True),
            on=partition_keys,
            how="left",
        )
        existing = existing.loc[existing["_replace_partition"].isna()].drop(
            columns=["_replace_partition"]
        )
    combined = upsert_history(existing, new_rows, keys)
    write_dataframe(combined, path)
    return combined
