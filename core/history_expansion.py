from __future__ import annotations

import csv
import io
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

from core.data import draw_identity, merge_history
from core.models import Draw, GameConfig
from core.stats import number_stats
from core.rule_eras import filter_current_analysis_draws, rule_profile

_DATE_FORMATS = (
    "%d-%b-%Y",
    "%d/%m/%Y",
    "%Y-%m-%d",
    "%d-%m-%Y",
    "%d %b %Y",
    "%d %B %Y",
)

DEPTH_TARGETS = {
    "powerball": 500,
}
DEFAULT_DEPTH_TARGET = 250


@dataclass(frozen=True)
class ImportIssue:
    severity: str
    row_number: int | None
    detail: str


@dataclass(frozen=True)
class ImportReport:
    path: Path
    rows_total: int
    accepted_rows: int
    rejected_rows: int
    draws: tuple[Draw, ...]
    issues: tuple[ImportIssue, ...]
    mapping_summary: str
    source_duplicate_dates: tuple[date, ...] = ()

    @property
    def usable(self) -> bool:
        return bool(self.draws)


@dataclass(frozen=True)
class FolderImportReport:
    folder: Path
    files_scanned: int
    files_used: int
    files_skipped: int
    rows_total: int
    accepted_rows: int
    rejected_rows: int
    draws: tuple[Draw, ...]
    identical_overlaps: int
    conflicting_overlaps: int
    reports: tuple[ImportReport, ...]
    skipped_files: tuple[str, ...]


@dataclass(frozen=True)
class DepthStatus:
    current: int
    target: int
    percent: float
    tier: str
    remaining: int


@dataclass(frozen=True)
class RankingWindow:
    label: str
    draws: int
    available: bool
    top_numbers: tuple[int, ...]
    overlap_with_all: int
    top_n: int

    @property
    def overlap_label(self) -> str:
        if not self.available:
            return "unavailable"
        return f"{self.overlap_with_all}/{self.top_n}"


def depth_target(config: GameConfig) -> int:
    return DEPTH_TARGETS.get(config.key, DEFAULT_DEPTH_TARGET)


def depth_status(config: GameConfig, current: int) -> DepthStatus:
    target = depth_target(config)
    pct = min(100.0, (current / target * 100.0) if target else 100.0)
    if current < 50:
        tier = "Very limited"
    elif current < 100:
        tier = "Limited"
    elif current < target:
        tier = "Developing"
    else:
        tier = "Established"
    return DepthStatus(
        current=current,
        target=target,
        percent=pct,
        tier=tier,
        remaining=max(0, target - current),
    )


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").strip().lower())


def _find_header(headers: Iterable[str], aliases: Iterable[str]) -> str | None:
    by_norm = {_norm(h): h for h in headers if h is not None}
    for alias in aliases:
        found = by_norm.get(_norm(alias))
        if found is not None:
            return found
    return None


def _parse_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _cell_text(value)
    if "T" in text and len(text) >= 10:
        text = text[:10]
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(
        f"unsupported date {value!r}; use DD-Mon-YYYY, DD/MM/YYYY, YYYY-MM-DD or DD-MM-YYYY"
    )


def _parse_number_list(value: str) -> list[int]:
    return [int(token) for token in re.findall(r"\d+", value or "")]


def _main_aliases(index: int) -> tuple[str, ...]:
    return (
        f"Ball {index}", f"Ball{index}", f"Number {index}", f"Number{index}",
        f"Num {index}", f"Num{index}", f"Main {index}", f"Main{index}",
        f"Winning Number {index}", f"WinningNumber{index}", f"Winning Ball {index}", f"WinningBall{index}",
        f"Main Ball {index}", f"MainBall{index}", f"No {index}", f"No.{index}", f"N{index}",
    )


def _special_aliases(config: GameConfig, index: int) -> tuple[str, ...]:
    canonical = config.special_columns[index - 1] if index - 1 < len(config.special_columns) else ""
    label = config.special_name or "Special"
    aliases = [canonical]
    if config.special_pick == 1:
        aliases.extend([label, label.replace(" ", ""), "Special", "Special Ball", "Bonus"])
        if "powerball" in _norm(label):
            aliases.extend(["PB", "Power Ball"])
        if "life" in _norm(label):
            aliases.extend(["LifeBall"])
        if "thunder" in _norm(label):
            aliases.extend(["Thunder Ball"])
    else:
        singular = label[:-1] if label.endswith("s") else label
        aliases.extend([
            f"{singular} {index}", f"{singular}{index}",
            f"Star {index}", f"Star{index}",
            f"Special {index}", f"Special{index}",
        ])
    return tuple(x for x in aliases if x)




def _decode_tabular_bytes(raw: bytes) -> str:
    """Decode CSV-like bytes conservatively, preserving unusual delimiters."""
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _csv_reader_for_path(path: Path):
    raw = path.read_bytes()
    text = _decode_tabular_bytes(raw)
    stripped = text.lstrip()
    if stripped.lower().startswith(("<!doctype html", "<html", "<?xml")):
        kind = "XML" if stripped.lower().startswith("<?xml") else "HTML"
        raise ValueError(f"File contains {kind}, not a delimited CSV table")

    sample = "\n".join(text.splitlines()[:25])
    delimiter = ","
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        first = next((line for line in text.splitlines() if line.strip()), "")
        counts = {d: first.count(d) for d in (",", ";", "\t", "|")}
        delimiter = max(counts, key=counts.get) if max(counts.values(), default=0) else ","
    handle = io.StringIO(text, newline="")
    return csv.DictReader(handle, delimiter=delimiter), delimiter


class _RowsReader:
    def __init__(self, fieldnames, rows):
        self.fieldnames = fieldnames
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)


def _xlsx_reader_for_path(path: Path):
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise ValueError("Excel import support is not installed in this build") from exc

    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb.active
        values = ws.iter_rows(values_only=True)
        try:
            first = next(values)
        except StopIteration:
            raise ValueError("Excel workbook is empty")
        headers = [str(v).strip() if v is not None else "" for v in first]
        if not any(headers):
            raise ValueError("Excel workbook has no header row")
        rows = []
        for raw in values:
            if not any(v not in (None, "") for v in raw):
                continue
            rows.append({headers[i]: raw[i] if i < len(raw) else None for i in range(len(headers))})
        return _RowsReader(headers, rows), "Excel worksheet"
    finally:
        wb.close()


def _table_reader_for_path(path: Path):
    if path.suffix.lower() in {".xlsx", ".xlsm"}:
        return _xlsx_reader_for_path(path)
    return _csv_reader_for_path(path)


def _cell_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    return str(value).strip()

def _variant_mismatch(path: Path, config: GameConfig) -> bool:
    """Avoid accidentally importing HotPicks as the parent draw game and vice versa."""
    name = _norm(path.stem)
    is_hotpick_file = "hotpick" in name
    is_hotpick_game = "hotpick" in config.key
    if is_hotpick_file and not is_hotpick_game:
        return True
    return False


def load_flexible_csv(path: Path, config: GameConfig) -> ImportReport:
    """Load a results CSV or Excel workbook using conservative column alias detection.

    Canonical DrawWise/National-Lottery-style headers are used directly. Common aliases
    such as Number1/Main1/Star1 are accepted. Invalid rows are reported rather than
    silently written. A file with no unambiguous mapping returns zero accepted rows.
    """
    path = Path(path)
    issues: list[ImportIssue] = []
    draws: list[Draw] = []

    if not path.exists():
        return ImportReport(path, 0, 0, 0, (), (ImportIssue("Error", None, "File not found"),), "No mapping")
    if _variant_mismatch(path, config):
        return ImportReport(
            path, 0, 0, 0, (),
            (ImportIssue("Skip", None, "Filename indicates a HotPicks dataset, not the selected parent game."),),
            "Variant mismatch",
        )

    try:
        reader, delimiter = _table_reader_for_path(path)
    except Exception as exc:
        detail = f"Could not read results file: {exc}"
        return ImportReport(path, 0, 0, 0, (), (ImportIssue("Error", None, detail),), detail)

    headers = reader.fieldnames or []
    if not headers:
        return ImportReport(path, 0, 0, 0, (), (ImportIssue("Error", None, "Results file has no header row"),), "No mapping")

    date_col = _find_header(headers, (
        "DrawDate", "Draw Date", "Date", "Draw_Date", "Drawing Date", "Result Date",
        "Date of Draw", "Draw date and time", "Draw Date/Time",
    ))
    round_col = _find_header(headers, ("Round", "Round ID", "RoundId"))
    draw_number_col = _find_header(headers, ("DrawNumber", "Draw Number", "Drawing Number", "Draw No", "DrawNo"))

    expected_main_draw = len(config.main_columns)
    main_cols: list[str] = []
    for index in range(1, expected_main_draw + 1):
        col = _find_header(headers, _main_aliases(index))
        if col:
            main_cols.append(col)
    combined_main = None
    if len(main_cols) != expected_main_draw:
        main_cols = []
        combined_main = _find_header(headers, ("Winning Numbers", "Main Numbers", "Numbers", "Winning Balls", "Main Balls"))

    special_cols: list[str] = []
    combined_special = None
    if config.special_pick:
        for index in range(1, config.special_pick + 1):
            col = _find_header(headers, _special_aliases(config, index))
            if col:
                special_cols.append(col)
        if len(special_cols) != config.special_pick:
            special_cols = []
            combined_aliases = [config.special_name or "Special balls", "Special Balls", "Lucky Stars", "Stars"]
            combined_special = _find_header(headers, combined_aliases)

    missing = []
    if not date_col:
        missing.append("draw date")
    if not main_cols and not combined_main:
        missing.append(f"{expected_main_draw} draw-number columns (or one combined Winning Numbers column)")
    if config.special_pick and not special_cols and not combined_special:
        missing.append(f"{config.special_pick} {config.special_name or 'special'} column(s)")
    if missing:
        detail = ("Could not map " + ", ".join(missing) + ". "
                  + f"Detected delimiter={delimiter!r}; headers={headers[:20]!r}.")
        return ImportReport(path, 0, 0, 0, (), (ImportIssue("Error", None, detail),), detail)

    mapping_parts = [f"delimiter={delimiter!r}", f"date={date_col}"]
    mapping_parts.append(
        "main=" + (", ".join(main_cols) if main_cols else f"combined:{combined_main}")
    )
    if config.special_pick:
        mapping_parts.append(
            "special=" + (", ".join(special_cols) if special_cols else f"combined:{combined_special}")
        )
    if round_col:
        mapping_parts.append(f"round={round_col}")
    if draw_number_col:
        mapping_parts.append(f"draw#={draw_number_col}")
    mapping_summary = " • ".join(mapping_parts)

    rows_total = 0
    provisional: list[tuple[int, Draw]] = []
    for row_number, row in enumerate(reader, start=2):
        rows_total += 1
        try:
            draw_date = _parse_date(row.get(date_col, ""))
            if main_cols:
                main_values = [int(_cell_text(row.get(col))) for col in main_cols]
            else:
                main_values = _parse_number_list(_cell_text(row.get(combined_main, "")))
                if len(main_values) != expected_main_draw:
                    raise ValueError(
                        f"combined main-number field contains {len(main_values)} values; expected {expected_main_draw}"
                    )

            if config.special_pick:
                if special_cols:
                    special_values = [int(_cell_text(row.get(col))) for col in special_cols]
                else:
                    special_values = _parse_number_list(_cell_text(row.get(combined_special, "")))
                    if len(special_values) != config.special_pick:
                        raise ValueError(
                            f"combined {config.special_name or 'special'} field contains {len(special_values)} values; expected {config.special_pick}"
                        )
            else:
                special_values = []

            main = tuple(sorted(main_values))
            special = tuple(sorted(special_values))
            if len(set(main)) != expected_main_draw:
                raise ValueError("duplicate draw number or wrong draw-number count")
            if config.special_pick and len(set(special)) != config.special_pick:
                raise ValueError("duplicate special number or wrong special-number count")
            bad_main = [n for n in main if not config.main_min <= n <= config.main_max]
            if bad_main:
                raise ValueError(f"main number(s) outside {config.main_min}–{config.main_max}: {bad_main}")
            if config.special_pick:
                assert config.special_min is not None and config.special_max is not None
                bad_special = [n for n in special if not config.special_min <= n <= config.special_max]
                if bad_special:
                    raise ValueError(
                        f"{config.special_name or 'special'} number(s) outside {config.special_min}–{config.special_max}: {bad_special}"
                    )

            round_id = _cell_text(row.get(round_col)) or None if round_col else None
            draw_number = _cell_text(row.get(draw_number_col)) or None if draw_number_col else None
            provisional.append((row_number, Draw(draw_date, main, special, round_id, draw_number)))
        except Exception as exc:
            issues.append(ImportIssue("Reject", row_number, str(exc)))

    # If a source has multiple distinct records on the same date but supplies no round
    # identity, those rows are ambiguous. Refuse them instead of collapsing them.
    date_counts = Counter(draw.draw_date for _row, draw in provisional)
    ambiguous_dates = tuple(sorted(d for d, count in date_counts.items() if count > 1))
    for row_number, draw in provisional:
        if date_counts[draw.draw_date] > 1 and not draw.round_id and not draw.draw_number:
            issues.append(
                ImportIssue(
                    "Reject", row_number,
                    f"multiple records occur on {draw.draw_date:%d-%b-%Y} but the source has no Round/DrawNumber identity",
                )
            )
            continue
        draws.append(draw)

    draws.sort(key=lambda d: (d.draw_date, d.draw_number or "", d.round_id or "", d.main, d.special))
    profile = rule_profile(config)
    analysis_ready = filter_current_analysis_draws(config, draws)
    legacy_count = len(draws) - len(analysis_ready)
    if legacy_count:
        issues.append(
            ImportIssue(
                "Review", None,
                f"{legacy_count} accepted row(s) fall outside the current analysis universe ({profile.current_analysis_label}). "
                "They may be retained in the archive but are excluded from current strategy analysis/backtesting."
            )
        )
    rejected = rows_total - len(draws)
    return ImportReport(
        path=path,
        rows_total=rows_total,
        accepted_rows=len(draws),
        rejected_rows=rejected,
        draws=tuple(draws),
        issues=tuple(issues),
        mapping_summary=mapping_summary,
        source_duplicate_dates=ambiguous_dates,
    )


def scan_import_folder(folder: Path, config: GameConfig) -> FolderImportReport:
    folder = Path(folder)
    csv_files = sorted(p for p in folder.rglob("*.csv") if p.is_file())
    reports: list[ImportReport] = []
    skipped: list[str] = []
    all_draws: list[Draw] = []
    rows_total = accepted = rejected = 0

    for path in csv_files:
        report = load_flexible_csv(path, config)
        rows_total += report.rows_total
        rejected += report.rejected_rows
        if report.usable:
            reports.append(report)
            accepted += report.accepted_rows
            all_draws.extend(report.draws)
        else:
            skipped.append(path.name)

    if all_draws:
        merged, merge_summary = merge_history([], all_draws)
        identical = merge_summary.unchanged_records
        conflicts = merge_summary.replaced_records
    else:
        merged = []
        identical = conflicts = 0

    return FolderImportReport(
        folder=folder,
        files_scanned=len(csv_files),
        files_used=len(reports),
        files_skipped=len(skipped),
        rows_total=rows_total,
        accepted_rows=accepted,
        rejected_rows=rejected,
        draws=tuple(merged),
        identical_overlaps=identical,
        conflicting_overlaps=conflicts,
        reports=tuple(reports),
        skipped_files=tuple(skipped),
    )


def ranking_windows(
    draws: list[Draw],
    config: GameConfig,
    recent_window: int = 20,
    *,
    domain: str = "main",
    top_n: int = 8,
) -> tuple[RankingWindow, ...]:
    if not draws:
        return ()
    domain = "special" if domain == "special" else "main"
    all_stats = number_stats(draws, config, recent_window, domain)
    all_top = tuple(st.number for st in sorted(all_stats, key=lambda s: (-s.score, s.number))[:top_n])
    all_set = set(all_top)
    results: list[RankingWindow] = []
    for label, size in (("20", 20), ("50", 50), ("100", 100), ("250", 250)):
        if len(draws) < size:
            results.append(RankingWindow(label, len(draws), False, (), 0, top_n))
            continue
        subset = draws[-size:]
        stats = number_stats(subset, config, min(recent_window, size), domain)
        top = tuple(st.number for st in sorted(stats, key=lambda s: (-s.score, s.number))[:top_n])
        results.append(RankingWindow(label, size, True, top, len(set(top) & all_set), top_n))
    results.append(RankingWindow("All", len(draws), True, all_top, top_n, top_n))
    return tuple(results)
