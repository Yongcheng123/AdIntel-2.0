from pathlib import Path

from adintel.core.competitor_groups import build_competitor_run_plan, load_competitor_groups
from adintel.core.models import CompetitorGroup, CompetitorGroupCatalog


def test_build_competitor_run_plan_uses_configured_competitors() -> None:
    groups = load_competitor_groups(Path("config/socialpeta_groups.example.yaml"))

    plan = build_competitor_run_plan(groups, "Chime")

    assert plan.advertiser == "Chime"
    assert plan.country == "US"
    assert plan.competitors == ["Current", "Dave", "MoneyLion"]


def test_build_competitor_run_plan_deduplicates_and_skips_empty_entries() -> None:
    groups = CompetitorGroupCatalog(
        groups=[
            CompetitorGroup(
                advertiser="Acme",
                country="CA",
                competitors=["  Rival One  ", "", "Acme", "Rival One", "Rival Two"],
            )
        ]
    )

    plan = build_competitor_run_plan(groups, "Acme")

    assert plan.country == "CA"
    assert plan.competitors == ["Rival One", "Rival Two"]
