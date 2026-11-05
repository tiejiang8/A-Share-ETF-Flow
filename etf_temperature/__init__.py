"""Source-layout compatibility wrapper for ``python -m etf_temperature.cli``.

The implementation lives under ``src/etf_temperature`` as requested by the
project contract. This wrapper makes module execution work from a fresh clone
without requiring an editable install first.
"""

from pathlib import Path
from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)  # type: ignore[name-defined]
_src_pkg = Path(__file__).resolve().parents[1] / "src" / "etf_temperature"
if _src_pkg.exists():
    __path__.append(str(_src_pkg))  # type: ignore[attr-defined]
