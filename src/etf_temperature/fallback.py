from __future__ import annotations

import pandas as pd


def fetch_third_party_fallback(*_args, **_kwargs) -> pd.DataFrame:
    """Third-party fallback hook.

    This project does not use AKShare or other third-party sources as primary
    data. Returned rows must declare source_type=third_party before merging.
    """

    return pd.DataFrame()
