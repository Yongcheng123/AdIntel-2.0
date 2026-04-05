from __future__ import annotations

from pathlib import Path

import yaml

from adintel.core.models import AdvertiserCatalog


def load_catalog(path: Path) -> AdvertiserCatalog:
    if not path.exists():
        return AdvertiserCatalog()

    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}

    return AdvertiserCatalog.model_validate(data)
