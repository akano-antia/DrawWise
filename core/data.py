from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from core.models import Draw, GameConfig

_DATE_FORMATS = (
    "%d-%b-%Y",
    "%d/%m/%Y",
    "%Y-%m-%d",
    "%d-%m-%Y",
)


@dataclass(frozen=True)
class MergeSummary:
    incoming_rows: int
    previous_rows: int
    final_rows: int
    new_dates: int
    replaced_dates: int
    unchanged_dates: int

    @property
    def new_records(self) -> int:
        return self.new_dates

    @property
    def replaced_records(self) -> int:
        return self.replaced_dates

    @property
    def unchanged_records(self) -> int:
        return self.unchanged_dates


def _parse_date(value: str):
    value = value.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Unsupported DrawDate format: {value!r}")


def draw_identity(draw: Draw) -> tuple:
    """Stable identity for a draw record.

    Most games have one draw per calendar date, so the date is enough. Some source
    files (notably Lotto in the bundled sample) can contain multiple rounds on the
    same date. When Round and/or DrawNumber are available they are part of identity
    so an import never collapses legitimate same-date rounds.
    """
    if draw.round_id or draw.draw_number:
        return (draw.draw_date, draw.draw_number or "", draw.round_id or "")
    return (draw.draw_date,)


def _sort_key(draw: Draw):
    return (draw.draw_date, draw.draw_number or "", draw.round_id or "", draw.main, draw.special)


def load_draws(path: Path, config: GameConfig) -> list[Draw]:
    if not path.exists():
        raise FileNotFoundError(f"History file not found: {path}")

    draws: list[Draw] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError(f"CSV has no header: {path}")

        required = ["DrawDate", *config.main_columns, *config.special_columns]
        missing = [c for c in required if c not in reader.fieldnames]
        if missing:
            raise ValueError(f"{path.name} is missing columns: {missing}")

        for row_number, row in enumerate(reader, start=2):
            try:
                draw_date = _parse_date(row["DrawDate"])
                main = tuple(sorted(int(row[c]) for c in config.main_columns))
                special = tuple(sorted(int(row[c]) for c in config.special_columns))
            except Exception as exc:
                raise ValueError(f"Bad data in {path.name}, row {row_number}: {exc}") from exc

            if len(set(main)) != len(main):
                raise ValueError(f"Duplicate main number in {path.name}, row {row_number}")
            if len(set(special)) != len(special):
                raise ValueError(f"Duplicate special number in {path.name}, row {row_number}")

            for n in main:
                if not (config.main_min <= n <= config.main_max):
                    raise ValueError(f"Main number {n} outside range in {path.name}, row {row_number}")
            for n in special:
                if config.special_min is None or config.special_max is None:
                    raise ValueError(f"Unexpected special number in {path.name}, row {row_number}")
                if not (config.special_min <= n <= config.special_max):
                    raise ValueError(f"Special number {n} outside range in {path.name}, row {row_number}")

            round_id = (row.get("Round") or "").strip() or None
            draw_number = (row.get("DrawNumber") or "").strip() or None
            draws.append(
                Draw(
                    draw_date=draw_date,
                    main=main,
                    special=special,
                    round_id=round_id,
                    draw_number=draw_number,
                )
            )

    if not draws:
        raise ValueError(f"No draw rows found in {path}")

    draws.sort(key=_sort_key)
    return draws


def write_draws(path: Path, config: GameConfig, draws: list[Draw]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    include_round = any(draw.round_id for draw in draws)
    include_draw_number = any(draw.draw_number for draw in draws)
    fieldnames = ["DrawDate"]
    if include_round:
        fieldnames.append("Round")
    fieldnames += [*config.main_columns, *config.special_columns]
    if include_draw_number:
        fieldnames.append("DrawNumber")

    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for draw in sorted(draws, key=_sort_key):
            row = {"DrawDate": draw.draw_date.strftime("%d-%b-%Y")}
            if include_round:
                row["Round"] = draw.round_id or ""
            row.update({name: value for name, value in zip(config.main_columns, draw.main)})
            row.update({name: value for name, value in zip(config.special_columns, draw.special)})
            if include_draw_number:
                row["DrawNumber"] = draw.draw_number or ""
            writer.writerow(row)


def merge_history(existing: list[Draw], incoming: list[Draw]) -> tuple[list[Draw], MergeSummary]:
    """Merge draw records without collapsing legitimate same-date rounds.

    If Round/DrawNumber metadata exists, it participates in identity. If an incoming
    row lacks that identity while the existing file already contains multiple records
    on the same date, the import is ambiguous and is rejected rather than silently
    replacing or creating the wrong round.
    """
    existing_by_date = Counter(draw.draw_date for draw in existing)
    for draw in incoming:
        if not draw.round_id and not draw.draw_number and existing_by_date[draw.draw_date] > 1:
            raise ValueError(
                f"Incoming record for {draw.draw_date:%d-%b-%Y} has no Round/DrawNumber, "
                "but existing history has multiple records on that date. Use a source CSV that includes round identity."
            )

    current = {draw_identity(d): d for d in existing}
    new_records = replaced = unchanged = 0

    # Migration safety for history written by pre-3.7 versions: an older file may
    # contain a date-only record where the new source supplies explicit Round/DrawNumber.
    # Once an identified source row is available, remove the ambiguous legacy date-only
    # record so it cannot coexist as a phantom extra draw.
    incoming_by_date: dict[object, list[Draw]] = {}
    for draw in incoming:
        incoming_by_date.setdefault(draw.draw_date, []).append(draw)
    for draw_date, rows in incoming_by_date.items():
        if any(row.round_id or row.draw_number for row in rows):
            legacy_key = (draw_date,)
            if legacy_key in current:
                current.pop(legacy_key)
                replaced += 1

    for draw in incoming:
        key = draw_identity(draw)
        old = current.get(key)
        if old is None:
            new_records += 1
        elif old == draw:
            unchanged += 1
        else:
            replaced += 1
        current[key] = draw

    merged = sorted(current.values(), key=_sort_key)
    return merged, MergeSummary(
        incoming_rows=len(incoming),
        previous_rows=len(existing),
        final_rows=len(merged),
        new_dates=new_records,
        replaced_dates=replaced,
        unchanged_dates=unchanged,
    )
