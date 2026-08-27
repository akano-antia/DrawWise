from __future__ import annotations

import calendar
import csv
import statistics
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from core.models import Draw, GameConfig
from core.data import draw_identity

_DATE_FORMATS = (
    "%d-%b-%Y",
    "%d/%m/%Y",
    "%Y-%m-%d",
    "%d-%m-%Y",
)


@dataclass(frozen=True)
class DataIssue:
    severity: str
    category: str
    row_number: int | None
    draw_date: date | None
    detail: str


@dataclass(frozen=True)
class HistoryAudit:
    path: Path
    total_rows: int
    valid_rows: int
    invalid_rows: int
    unique_draw_dates: int
    duplicate_dates: tuple[date, ...]
    suspected_missing_dates: tuple[date, ...]
    earliest: date | None
    latest: date | None
    inferred_weekdays: tuple[int, ...]
    completeness_pct: float
    quality_score: int
    max_gap_days: int
    median_gap_days: float
    configured_shape: str
    observed_main_range: tuple[int, int] | None
    observed_special_range: tuple[int, int] | None
    issues: tuple[DataIssue, ...]
    valid_draws: tuple[Draw, ...]

    @property
    def weekday_label(self) -> str:
        if not self.inferred_weekdays:
            return "Not enough data"
        return ", ".join(calendar.day_abbr[d] for d in self.inferred_weekdays)

    @property
    def observed_main_label(self) -> str:
        if self.observed_main_range is None:
            return "—"
        return f"{self.observed_main_range[0]}–{self.observed_main_range[1]}"

    @property
    def observed_special_label(self) -> str:
        if self.observed_special_range is None:
            return "—"
        return f"{self.observed_special_range[0]}–{self.observed_special_range[1]}"


def _parse_date(value: str) -> date:
    value = (value or "").strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unsupported DrawDate {value!r}")


def configured_shape(config: GameConfig) -> str:
    shape = f"{config.main_pick} from {config.main_min}–{config.main_max}"
    if config.special_pick:
        shape += (
            f" + {config.special_pick} {config.special_name or 'special'} "
            f"from {config.special_min}–{config.special_max}"
        )
    return shape


def _infer_weekdays(draw_dates: list[date]) -> tuple[int, ...]:
    """Infer likely draw weekdays from the file itself.

    This is intentionally descriptive rather than an official schedule table. It lets
    DrawWise flag *suspected* gaps even when a game's published schedule changes over
    time. A weekday must occur at least half as often as the most common weekday.
    """
    if len(draw_dates) < 4:
        return tuple(sorted({d.weekday() for d in draw_dates}))
    counts = Counter(d.weekday() for d in draw_dates)
    peak = max(counts.values())
    threshold = max(2, peak * 0.50)
    selected = tuple(sorted(day for day, count in counts.items() if count >= threshold))
    return selected or (counts.most_common(1)[0][0],)


def _suspected_missing(draw_dates: list[date], weekdays: tuple[int, ...]) -> tuple[date, ...]:
    if len(draw_dates) < 2 or not weekdays:
        return ()
    observed = set(draw_dates)
    start, end = min(draw_dates), max(draw_dates)
    missing: list[date] = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() in weekdays and cursor not in observed:
            missing.append(cursor)
        cursor += timedelta(days=1)
    return tuple(missing)


def audit_history(path: Path, config: GameConfig) -> HistoryAudit:
    """Audit a history CSV without failing at the first bad row.

    The normal loader remains strict. This audit is deliberately permissive so the
    Data Manager can explain what is wrong with a damaged or incomplete file.
    """
    path = Path(path)
    issues: list[DataIssue] = []
    valid_draws: list[Draw] = []
    total_rows = 0
    invalid_rows = 0

    if not path.exists():
        issues.append(DataIssue("Error", "File", None, None, f"History file not found: {path}"))
        return HistoryAudit(
            path=path, total_rows=0, valid_rows=0, invalid_rows=0, unique_draw_dates=0,
            duplicate_dates=(), suspected_missing_dates=(), earliest=None, latest=None,
            inferred_weekdays=(), completeness_pct=0.0, quality_score=0,
            max_gap_days=0, median_gap_days=0.0, configured_shape=configured_shape(config),
            observed_main_range=None, observed_special_range=None, issues=tuple(issues), valid_draws=(),
        )

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        required = ["DrawDate", *config.main_columns, *config.special_columns]
        missing_columns = [name for name in required if name not in fieldnames]
        if missing_columns:
            issues.append(
                DataIssue(
                    "Error", "Schema", 1, None,
                    "Missing required column(s): " + ", ".join(missing_columns),
                )
            )
            # Continue through rows only if DrawDate exists; values cannot be validated reliably.
            if "DrawDate" not in fieldnames:
                return HistoryAudit(
                    path=path, total_rows=0, valid_rows=0, invalid_rows=0, unique_draw_dates=0,
                    duplicate_dates=(), suspected_missing_dates=(), earliest=None, latest=None,
                    inferred_weekdays=(), completeness_pct=0.0, quality_score=0,
                    max_gap_days=0, median_gap_days=0.0, configured_shape=configured_shape(config),
                    observed_main_range=None, observed_special_range=None,
                    issues=tuple(issues), valid_draws=(),
                )

        for row_number, row in enumerate(reader, start=2):
            total_rows += 1
            try:
                draw_date = _parse_date(row.get("DrawDate", ""))
            except Exception as exc:
                invalid_rows += 1
                issues.append(DataIssue("Error", "Date", row_number, None, str(exc)))
                continue

            try:
                main = tuple(sorted(int(row[name]) for name in config.main_columns))
                special = tuple(sorted(int(row[name]) for name in config.special_columns))
            except Exception as exc:
                invalid_rows += 1
                issues.append(
                    DataIssue("Error", "Number", row_number, draw_date, f"Could not parse number: {exc}")
                )
                continue

            row_errors: list[str] = []
            if len(set(main)) != len(main):
                row_errors.append("duplicate main number in the same draw")
            if len(set(special)) != len(special):
                row_errors.append("duplicate special number in the same draw")
            bad_main = [n for n in main if not (config.main_min <= n <= config.main_max)]
            if bad_main:
                row_errors.append(f"main number(s) outside {config.main_min}–{config.main_max}: {bad_main}")
            if special:
                if config.special_min is None or config.special_max is None:
                    row_errors.append("unexpected special number column/value")
                else:
                    bad_special = [n for n in special if not (config.special_min <= n <= config.special_max)]
                    if bad_special:
                        row_errors.append(
                            f"special number(s) outside {config.special_min}–{config.special_max}: {bad_special}"
                        )

            if row_errors:
                invalid_rows += 1
                for detail in row_errors:
                    issues.append(DataIssue("Error", "Number", row_number, draw_date, detail))
                continue

            round_id = (row.get("Round") or "").strip() or None
            draw_number = (row.get("DrawNumber") or "").strip() or None
            valid_draws.append(
                Draw(
                    draw_date=draw_date, main=main, special=special,
                    round_id=round_id, draw_number=draw_number,
                )
            )

    valid_rows = len(valid_draws)
    identity_counts = Counter(draw_identity(draw) for draw in valid_draws)
    duplicate_identities = [identity for identity, count in identity_counts.items() if count > 1]
    duplicate_dates = tuple(sorted({identity[0] for identity in duplicate_identities}))
    for identity in duplicate_identities:
        duplicate = identity[0]
        issues.append(
            DataIssue(
                "Warning", "Duplicate record", None, duplicate,
                f"{identity_counts[identity]} rows share the same draw identity {identity}; keep one verified record.",
            )
        )

    unique_dates = sorted({draw.draw_date for draw in valid_draws})
    weekdays = _infer_weekdays(unique_dates)
    suspected_missing = _suspected_missing(unique_dates, weekdays)
    for missing in suspected_missing[:80]:
        issues.append(
            DataIssue(
                "Review", "Suspected gap", None, missing,
                "No row on an inferred draw weekday. Verify against the official result source before adding it.",
            )
        )
    if len(suspected_missing) > 80:
        issues.append(
            DataIssue(
                "Review", "Suspected gap", None, None,
                f"{len(suspected_missing) - 80} additional suspected gap date(s) are not shown in the table.",
            )
        )

    gaps = [(b - a).days for a, b in zip(unique_dates, unique_dates[1:])]
    max_gap = max(gaps) if gaps else 0
    median_gap = float(statistics.median(gaps)) if gaps else 0.0
    earliest = unique_dates[0] if unique_dates else None
    latest = unique_dates[-1] if unique_dates else None
    expected_slots = len(unique_dates) + len(suspected_missing)
    completeness = (len(unique_dates) / expected_slots * 100.0) if expected_slots else 0.0
    validity = (valid_rows / total_rows * 100.0) if total_rows else 0.0
    duplicate_penalty = min(20.0, 5.0 * len(duplicate_identities))
    score = int(round(max(0.0, min(100.0, 0.55 * validity + 0.45 * completeness - duplicate_penalty))))

    main_values = [n for draw in valid_draws for n in draw.main]
    special_values = [n for draw in valid_draws for n in draw.special]
    observed_main = (min(main_values), max(main_values)) if main_values else None
    observed_special = (min(special_values), max(special_values)) if special_values else None

    if not issues and valid_rows:
        issues.append(
            DataIssue(
                "OK", "Structural audit", None, None,
                "No invalid rows, duplicate draw identities or suspected cadence gaps were detected.",
            )
        )

    return HistoryAudit(
        path=path,
        total_rows=total_rows,
        valid_rows=valid_rows,
        invalid_rows=invalid_rows,
        unique_draw_dates=len(identity_counts),
        duplicate_dates=duplicate_dates,
        suspected_missing_dates=suspected_missing,
        earliest=earliest,
        latest=latest,
        inferred_weekdays=weekdays,
        completeness_pct=completeness,
        quality_score=score,
        max_gap_days=max_gap,
        median_gap_days=median_gap,
        configured_shape=configured_shape(config),
        observed_main_range=observed_main,
        observed_special_range=observed_special,
        issues=tuple(issues),
        valid_draws=tuple(valid_draws),
    )


def cleaned_unique_draws(audit: HistoryAudit) -> list[Draw]:
    """Return valid rows with one deterministic row per draw identity (last valid row wins)."""
    by_identity: dict[tuple, Draw] = {}
    for draw in audit.valid_draws:
        by_identity[draw_identity(draw)] = draw
    return sorted(
        by_identity.values(),
        key=lambda item: (item.draw_date, item.draw_number or "", item.round_id or "", item.main),
    )
