"""Books and reading sessions, and their JSON-ready dict form."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Book:
    title: str
    pages: int

    def to_dict(self) -> dict:
        return {"title": self.title, "pages": self.pages}

    @classmethod
    def from_dict(cls, data: dict) -> Book:
        return cls(title=str(data["title"]), pages=int(data["pages"]))


@dataclass(frozen=True)
class Session:
    title: str
    day: date
    pages: int

    def to_dict(self) -> dict:
        return {"title": self.title, "day": self.day.isoformat(), "pages": self.pages}

    @classmethod
    def from_dict(cls, data: dict) -> Session:
        return cls(title=str(data["title"]), day=date.fromisoformat(data["day"]), pages=int(data["pages"]))
