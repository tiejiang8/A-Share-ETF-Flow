from __future__ import annotations

import pandas as pd


def fetch_fund_company_supplements(*_args, **_kwargs) -> pd.DataFrame:
    """Reserved official-fund-company fallback hook.

    The exchange official interfaces are the primary source. This hook exists so
    missing NAV, announcements, or corporate-action evidence can be added without
    changing the pipeline contract.
    """

    return pd.DataFrame()
