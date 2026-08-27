from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True)
class GameConfig:
    key: str
    name: str
    data_file: str
    main_min: int
    main_max: int
    main_pick: int
    main_columns: tuple[str, ...]
    special_name: str | None = None
    special_min: int | None = None
    special_max: int | None = None
    special_pick: int = 0
    special_columns: tuple[str, ...] = ()
    default_pool_size: int = 10
    default_special_pool_size: int = 0
    notes: str = ""

    @property
    def main_range_size(self) -> int:
        return self.main_max - self.main_min + 1

    @property
    def special_range_size(self) -> int:
        if self.special_min is None or self.special_max is None:
            return 0
        return self.special_max - self.special_min + 1

    def csv_path(self, project_root: Path) -> Path:
        return project_root / "data" / self.data_file


@dataclass(frozen=True)
class Draw:
    draw_date: date
    main: tuple[int, ...]
    special: tuple[int, ...] = ()
    round_id: str | None = None
    draw_number: str | None = None


@dataclass(frozen=True)
class NumberStat:
    number: int
    count: int
    recent_count: int
    gap_draws: int
    frequency_index: float
    recent_index: float
    score: float


@dataclass(frozen=True)
class Ticket:
    main: tuple[int, ...]
    special: tuple[int, ...] = ()

    def display(self, special_name: str | None = None) -> str:
        main = " ".join(f"{n:02d}" for n in self.main)
        if not self.special:
            return main
        label = special_name or "Special"
        special = " ".join(f"{n:02d}" for n in self.special)
        return f"{main}  |  {label}: {special}"
