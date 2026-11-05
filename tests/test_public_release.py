from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch
import zipfile

from jsonschema import Draft202012Validator, FormatChecker, ValidationError
import pytest

from etf_temperature.official_endpoints import _project_paths


ROOT = Path(__file__).resolve().parents[1]


def _script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _validator(name):
    schema = json.loads((ROOT / 'contracts' / name).read_text(encoding='utf-8'))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


@pytest.mark.parametrize(('schema', 'example'), [
    ('summary.schema.json', '2026-01-05/summary.json'),
    ('latest.schema.json', 'latest.json'),
    ('summary_index.schema.json', 'summary_index.json'),
])
def test_checked_in_synthetic_examples_match_contracts(schema, example):
    payload = json.loads((ROOT / 'examples/synthetic_output' / example).read_text(encoding='utf-8'))
    _validator(schema).validate(payload)


def test_schema_rejects_formal_synthetic_output_and_wrong_ranking_sign():
    payload = json.loads((ROOT / 'examples/synthetic_output/2026-01-05/summary.json').read_text(encoding='utf-8'))
    validator = _validator('summary.schema.json')
    formal = deepcopy(payload)
    formal['formal_report_allowed'] = True
    with pytest.raises(ValidationError):
        validator.validate(formal)
    wrong_sign = deepcopy(payload)
    wrong_sign['top_inflow_etfs'][0]['flow_1d_100m'] = -1
    with pytest.raises(ValidationError):
        validator.validate(wrong_sign)


def test_offline_example_is_synthetic_nonformal_and_contract_valid(tmp_path):
    example = _script('run_offline_example')
    with patch('requests.sessions.Session.request', side_effect=AssertionError('network forbidden')):
        result = example.run_example(ROOT / 'examples/synthetic_input.json', tmp_path)
    payload = json.loads(Path(result['summary_path']).read_text(encoding='utf-8'))
    _validator('summary.schema.json').validate(payload)
    assert payload['scope']['synthetic_demo'] is True
    assert payload['formal_report_allowed'] is False
    assert payload['market']['flow_1d_100m'] == 7.0
    assert payload['coverage']['nav_coverage'] == 1.0
    assert payload['coverage']['nav_fresh_coverage'] == 0.75
    assert '合成演示数据' in Path(result['snapshot_path']).read_text(encoding='utf-8')


def test_source_checkout_retains_its_runtime_root(tmp_path, monkeypatch):
    monkeypatch.delenv('ETF_FLOW_HOME', raising=False)
    package = tmp_path / 'checkout/src/etf_temperature'
    package.mkdir(parents=True)
    root = package.parents[1]
    (root / 'pyproject.toml').write_text('', encoding='utf-8')
    (root / 'config').mkdir()
    assert _project_paths(package) == (root, root / 'config')


def test_installed_distribution_uses_writable_workspace_and_bundled_config(tmp_path, monkeypatch):
    monkeypatch.delenv('ETF_FLOW_HOME', raising=False)
    package = tmp_path / 'installed/etf_temperature'
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    assert _project_paths(package) == (workspace, package / '_default_config')
    override = tmp_path / 'override'
    (override / 'config').mkdir(parents=True)
    monkeypatch.setenv('ETF_FLOW_HOME', str(override))
    assert _project_paths(package) == (override, override / 'config')


def test_source_export_uses_allowlist_and_preserves_private_files(tmp_path):
    exporter = _script('prepare_github_release')
    source = tmp_path / 'source'
    files = {
        'README.md': '# Public project', 'src/etf_temperature/main.py': 'PUBLIC = True',
        'config/temperature.yaml': 'public: true',
        'scripts/example.ps1': 'Write-Output hello\r\n',
        'data/raw/response.json': 'PRIVATE_RAW', 'outputs/report.md': 'PRIVATE_REPORT',
        '.workbuddy/memory/MEMORY.md': 'PRIVATE_HISTORY', '.baoyu-skills/.env': 'PRIVATE_CREDENTIAL',
        '.tmp/scratch.txt': 'PRIVATE_SCRATCH', 'debug.log': 'PRIVATE_LOG',
        'src/example.egg-info/SOURCES.txt': 'GENERATED_METADATA',
        'docs/.env.private.json': 'PRIVATE_ENV',
    }
    for name, content in files.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
    result = exporter.prepare_release(tmp_path / 'release', root=source)
    destination = Path(result['directory'])
    manifest = json.loads((destination / 'RELEASE_MANIFEST.json').read_text(encoding='utf-8'))
    included = {item['path'] for item in manifest['files']}
    assert included == {'README.md', 'src/etf_temperature/main.py', 'config/temperature.yaml', 'scripts/example.ps1'}
    assert (destination / 'scripts/example.ps1').read_bytes() == b'Write-Output hello\n'
    assert all((source / name).read_bytes() == content.encode('utf-8') for name, content in files.items())
    with zipfile.ZipFile(result['archive']) as archive:
        assert all('PRIVATE_' not in archive.read(name).decode('utf-8') for name in archive.namelist())
    with pytest.raises(FileExistsError):
        exporter.prepare_release(destination, root=source)
