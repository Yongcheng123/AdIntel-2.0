from pydantic import ValidationError

from adintel.core.models import AdvertiserProfile


def test_advertiser_profile_normalizes_country_codes() -> None:
    advertiser = AdvertiserProfile(name="Binance", countries=["us", " tr "])

    assert advertiser.countries == ["US", "TR"]


def test_advertiser_profile_rejects_duplicate_country_codes() -> None:
    try:
        AdvertiserProfile(name="Binance", countries=["US", "us"])
    except ValidationError as exc:
        assert "Duplicate country code" in str(exc)
    else:
        raise AssertionError("Expected duplicate country codes to fail validation")


def test_advertiser_profile_rejects_invalid_country_codes() -> None:
    try:
        AdvertiserProfile(name="Binance", countries=["USA"])
    except ValidationError as exc:
        assert "Invalid country code" in str(exc)
    else:
        raise AssertionError("Expected invalid country codes to fail validation")
