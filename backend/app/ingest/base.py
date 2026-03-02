from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class SourceConnector(ABC):
    name: str

    @abstractmethod
    def load(self, path: Path) -> list[tuple[Path, dict]]:
        raise NotImplementedError

    @abstractmethod
    def normalize(self, payload: dict) -> dict:
        raise NotImplementedError

    @abstractmethod
    def upsert(self, session, payload: dict) -> dict:
        raise NotImplementedError
