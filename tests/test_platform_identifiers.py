from adintel.core.models import PlatformIdentifiers
from pydantic import ValidationError


def test_platform_identifiers_fall_back_to_global_ids() -> None:
    identifiers = PlatformIdentifiers(
        ios_app_id="global-ios",
        android_package="global.package",
    )

    assert identifiers.resolve_ios_app_id("US") == "global-ios"
    assert identifiers.resolve_android_package("US") == "global.package"


def test_platform_identifiers_use_country_specific_overrides() -> None:
    identifiers = PlatformIdentifiers(
        ios_app_id="global-ios",
        ios_app_ids_by_country={"TR": "tr-ios"},
        android_package="global.package",
        android_packages_by_country={"BR": "br.package"},
    )

    assert identifiers.resolve_ios_app_id("TR") == "tr-ios"
    assert identifiers.resolve_ios_app_id("US") == "global-ios"
    assert identifiers.resolve_android_package("BR") == "br.package"
    assert identifiers.resolve_android_package("US") == "global.package"


def test_platform_identifiers_normalize_country_override_keys() -> None:
    identifiers = PlatformIdentifiers(
        ios_app_ids_by_country={"tr": "tr-ios"},
        android_packages_by_country={" br ": "br.package"},
    )

    assert identifiers.ios_app_ids_by_country == {"TR": "tr-ios"}
    assert identifiers.android_packages_by_country == {"BR": "br.package"}


def test_platform_identifiers_reject_invalid_country_override_keys() -> None:
    try:
        PlatformIdentifiers(ios_app_ids_by_country={"TUR": "bad"})
    except ValidationError as exc:
        assert "Invalid country code" in str(exc)
    else:
        raise AssertionError("Expected invalid country override key to fail validation")
