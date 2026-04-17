from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from adintel.core.models import CompetitorGroup, CompetitorGroupCatalog


@dataclass(frozen=True)
class CompetitorRunPlan:
    advertiser: str
    competitors: list[str]
    country: str | None = None


def load_competitor_groups(path: Path) -> CompetitorGroupCatalog:
    if not path.exists():
        return CompetitorGroupCatalog()

    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}

    return CompetitorGroupCatalog.model_validate(data)


def get_competitor_group(catalog: CompetitorGroupCatalog, advertiser_name: str) -> CompetitorGroup | None:
    target = advertiser_name.casefold()
    for group in catalog.groups:
        if group.advertiser.casefold() == target:
            return group
    return None


def build_competitor_run_plan(catalog: CompetitorGroupCatalog, advertiser_name: str) -> CompetitorRunPlan:
    group = get_competitor_group(catalog, advertiser_name)
    competitors = list(group.competitors) if group is not None else []
    deduped: list[str] = []
    seen: set[str] = {advertiser_name.casefold()}
    for competitor in competitors:
        target = competitor.strip()
        if not target:
            continue
        key = target.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(target)
    return CompetitorRunPlan(advertiser=advertiser_name, competitors=deduped, country=group.country if group else None)
