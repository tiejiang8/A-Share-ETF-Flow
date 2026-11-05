from __future__ import annotations

from .official_endpoints import OFFICIAL_ENDPOINTS, write_probe_outputs


def probe_official_pages() -> dict:
    """Persist the official endpoints discovered by browser network inspection.

    The project setup used Playwright to inspect the SSE/SZSE dynamic pages.
    Runtime probing is intentionally conservative: daily jobs should call the
    discovered official interfaces directly instead of opening browsers.
    """

    payload = {"browser_probe_status": "captured_with_playwright", "endpoint_count": len(OFFICIAL_ENDPOINTS)}
    write_probe_outputs(payload)
    return payload
