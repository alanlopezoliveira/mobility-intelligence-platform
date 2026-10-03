"""Provider identity, timezone and complete offline pipeline regression checks."""

import json

import pandas as pd
import pytest
from scripts.smoke_pipeline import run_smoke
from src.config.settings import load_provider_config
from src.ingestion.rebuild import local_times
from src.providers.registry import CsvTrips, provider_root


def test_provider_clocks_accept_offsets_and_reject_local_dst_ambiguity():
    values = pd.Series(['2022-11-06T01:30:00', '2022-11-06T01:30:00-04:00', '2022-07-01T12:00:00'])
    times = local_times(values, 'America/New_York')
    assert pd.isna(times.iloc[0])
    assert times.iloc[1] == pd.Timestamp('2022-11-06T05:30Z')
    assert times.iloc[2] == pd.Timestamp('2022-07-01T16:00Z')


def test_provider_config_rejects_paths_and_unknown_adapters():
    with pytest.raises(ValueError):
        load_provider_config('../outside')
    source = load_provider_config().model_dump()
    source['trips'] = {'adapter': 'unimplemented'}
    with pytest.raises(ValueError):
        type(load_provider_config()).model_validate(source)


def test_two_providers_complete_pipeline_without_inventory_archives(tmp_path):
    fixtures = run_smoke(tmp_path)
    reports = []
    for provider, config in fixtures.items():
        root = tmp_path / f'frontend/public/project-data/providers/{provider}/test/2022'
        report = json.loads((root / 'report.json').read_text())
        assert report['provider']['id'] == provider
        assert report['departures'] == 11680
        assert report['stations'][0]['station'] == 'shared/01'
        assert report['models'][0]['timezone'] == config.timezone
        key = report['stations'][0]['export_key']
        assert (root / f'predictions-60-{key}.json').is_file()
        assert (root / 'diagnostics.json').is_file()
        assert (provider_root(tmp_path, config) / 'data/rebuilt/2022/report.json').is_file()
        reports.append(report)
    assert reports[0]['fingerprint'] != reports[1]['fingerprint']


def test_csv_adapter_missing_schema_fails_before_producing_counts(tmp_path):
    config = load_provider_config().model_copy(deep=True)
    config.trips.adapter = 'csv'
    with pytest.raises(ValueError, match='Missing CSV mappings'):
        CsvTrips(config).ingest(tmp_path, 2022)
