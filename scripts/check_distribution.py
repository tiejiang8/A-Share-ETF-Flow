"""Verify a wheel contains default configs and imports outside the source tree."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    wheels = sorted((root / "dist").glob("*.whl")) if args.wheel is None else [args.wheel]
    if not wheels:
        parser.error("No wheel found; run python -m build first.")
    wheel = wheels[-1].resolve()
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert "etf_temperature/_default_config/temperature.yaml" in names
        assert not any(name.startswith(("data/", "outputs/", ".workbuddy/", ".baoyu-skills/")) for name in names)
    scratch_root = root / ".tmp"
    scratch_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="wheel-smoke-", dir=scratch_root) as temporary:
        scratch = Path(temporary)
        target, workspace = scratch / "installed", scratch / "workspace"
        workspace.mkdir()
        env = os.environ.copy()
        env["TMP"] = str(scratch)
        env["TEMP"] = str(scratch)
        env["PIP_CACHE_DIR"] = str(scratch / "pip-cache")
        subprocess.run([sys.executable, "-m", "pip", "install", "--no-deps", "--no-index", "--target", str(target), str(wheel)], env=env, check=True)
        env["PYTHONPATH"] = str(target)
        env.pop("ETF_FLOW_HOME", None)
        code = "\n".join([
            "import json; from pathlib import Path",
            "import etf_temperature",
            "from etf_temperature.official_endpoints import ROOT, CONFIG_DIR",
            "from etf_temperature.config import load_temperature_config",
            "assert Path(etf_temperature.__file__).resolve().is_relative_to(Path(" + repr(str(target)) + "))",
            "assert ROOT.resolve() == Path.cwd().resolve()",
            "assert CONFIG_DIR.name == '_default_config' and (CONFIG_DIR / 'cluster_rules.yaml').is_file()",
            "assert load_temperature_config()['thresholds']['daily_max_nav_lag_days'] == 0",
            "from etf_temperature.cli import main; assert main(['--help']) == 0",
        ])
        # argparse exits 0 on --help. Configuration assertions run first.
        subprocess.run([sys.executable, "-c", code], cwd=workspace, env=env, check=True)
    print(json.dumps({"wheel": str(wheel), "bundled_config": True, "isolated_import": True}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
