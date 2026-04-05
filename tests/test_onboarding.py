from pathlib import Path

from adintel.core.models import AdvertiserCatalog
from adintel.onboarding import _candidate_score, save_catalog, upsert_catalog_advertiser


def test_candidate_score_prefers_exact_name_match() -> None:
    exact = {"name": "Chime", "publisher_name": "Chime"}
    fuzzy = {"name": "Chime SDK", "publisher_name": "Amazon"}

    assert _candidate_score("Chime", exact) > _candidate_score("Chime", fuzzy)


def test_upsert_catalog_advertiser_replaces_existing_entry() -> None:
    catalog = AdvertiserCatalog.model_validate(
        {
            "advertisers": [
                {
                    "name": "Chime",
                    "countries": ["US"],
                    "platforms": {"sensortower": {"unified_app_id": "old"}},
                }
            ]
        }
    )
    replacement = AdvertiserCatalog.model_validate(
        {
            "advertisers": [
                {
                    "name": "Chime",
                    "countries": ["US"],
                    "platforms": {"sensortower": {"unified_app_id": "new"}},
                }
            ]
        }
    ).advertisers[0]

    upsert_catalog_advertiser(catalog, replacement)

    assert len(catalog.advertisers) == 1
    assert catalog.advertisers[0].platforms.sensortower.unified_app_id == "new"


def test_save_catalog_writes_yaml(tmp_path: Path) -> None:
    target = tmp_path / "advertisers.yaml"
    catalog = AdvertiserCatalog.model_validate(
        {
            "advertisers": [
                {
                    "name": "Chime",
                    "countries": ["US"],
                    "platforms": {"sensortower": {"unified_app_id": "55d93e4402ac645ad20fc82a"}},
                }
            ]
        }
    )

    save_catalog(target, catalog)

    written = target.read_text(encoding="utf-8")
    assert "Chime" in written
    assert "55d93e4402ac645ad20fc82a" in written
