from pathlib import Path

import yaml


def test_bicimad_provider_config_contains_explicit_direct_urls():
    config_path = Path(__file__).resolve().parents[2] / "config" / "providers" / "bicimad.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    assert config["provider"] == "bicimad"
    assert config["station_master"]["direct_url"].startswith("http")
    years = {entry["year"] for entry in config["historical"]}
    assert years == {2017, 2018, 2019, 2020, 2021, 2022, 2023}
    assert len(config["historical"]) == 7
