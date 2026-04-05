from __future__ import annotations

from abc import ABC, abstractmethod

from sqlalchemy.orm import Session

from adintel.core.browser import BrowserManager
from adintel.core.models import CollectorRunRequest, CollectorRunResult, PlatformName
from adintel.core.settings import AppSettings


class PlatformCollector(ABC):
    platform: PlatformName
    state_key: str

    def __init__(self, settings: AppSettings, browser: BrowserManager, session: Session) -> None:
        self.settings = settings
        self.browser = browser
        self.session = session

    @abstractmethod
    async def login(self, *, headless: bool = False, use_cdp: bool = False) -> None:
        raise NotImplementedError

    @abstractmethod
    async def collect(
        self,
        request: CollectorRunRequest,
        *,
        use_cdp: bool = False,
    ) -> CollectorRunResult:
        raise NotImplementedError
