from pathlib import Path

from adintel.core.catalog import load_catalog


def test_load_catalog_from_example() -> None:
    catalog = load_catalog(Path("config/advertisers.example.yaml"))
    assert len(catalog.advertisers) >= 2
    assert catalog.advertisers[0].name == "Chime"
