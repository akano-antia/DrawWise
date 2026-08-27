from __future__ import annotations

import csv
import html
import io
import re
import tempfile
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable, MutableMapping

from core.data import merge_history
from core.history_expansion import load_flexible_csv
from core.models import Draw, GameConfig


class OnlineUpdateError(RuntimeError):
    """Raised when an official results source cannot be fetched or parsed safely.

    V4.1 keeps the raw response (when available) so the GUI can save a diagnostic
    file instead of forcing the user to guess what the website returned.
    """

    def __init__(
        self,
        message: str,
        *,
        diagnostic_bytes: bytes | None = None,
        diagnostic_url: str | None = None,
        diagnostic_extension: str = ".txt",
        diagnostic_detail: str = "",
    ):
        super().__init__(message)
        self.diagnostic_bytes = diagnostic_bytes
        self.diagnostic_url = diagnostic_url
        self.diagnostic_extension = diagnostic_extension
        self.diagnostic_detail = diagnostic_detail


@dataclass(frozen=True)
class OfficialSource:
    game_key: str
    provider: str
    data_urls: tuple[str, ...]
    page_url: str
    source_kind: str
    feed_key: str
    parent_game_key: str | None = None
    note: str = ""

    @property
    def data_url(self) -> str:
        """Compatibility alias: first structured endpoint attempted."""
        return self.data_urls[0]


@dataclass(frozen=True)
class OnlineResult:
    game_key: str
    provider: str
    data_url: str
    page_url: str
    fetched_at: datetime
    draws: tuple[Draw, ...]
    note: str = ""
    parser_used: str = ""

    @property
    def latest_date(self) -> date | None:
        return self.draws[-1].draw_date if self.draws else None


@dataclass(frozen=True)
class UpdatePreview:
    local_rows: int
    online_rows: int
    final_rows: int
    new_records: int
    corrected_records: int
    unchanged_records: int
    date_normalisations: int
    local_latest: date | None
    online_latest: date | None
    status: str


_NL_BASE = "https://www.national-lottery.co.uk/results"


def _nl_urls(slug: str) -> tuple[str, ...]:
    # V4.0 found that some machines receive a successful HTTP response from /csv
    # that is not actually a CSV table. V4.1 prefers the official XML endpoint and
    # transparently falls back to the CSV endpoint. Neither path bypasses access controls.
    return (
        f"{_NL_BASE}/{slug}/draw-history/xml",
        f"{_NL_BASE}/{slug}/draw-history/csv",
    )


SOURCES: dict[str, OfficialSource] = {
    "lotto": OfficialSource(
        "lotto", "The National Lottery", _nl_urls("lotto"),
        f"{_NL_BASE}/lotto/draw-history", "national_lottery_auto", "lotto",
        note="Official Lotto draw-history feed. Lotto HotPicks is derived from the same main-ball result stream.",
    ),
    "lotto_hotpicks": OfficialSource(
        "lotto_hotpicks", "The National Lottery", _nl_urls("lotto"),
        f"{_NL_BASE}/lotto/draw-history", "national_lottery_auto", "lotto", parent_game_key="lotto",
        note="Derived from the official Lotto main-ball feed; no separate HotPicks result download is required.",
    ),
    "euromillions": OfficialSource(
        "euromillions", "The National Lottery", _nl_urls("euromillions"),
        f"{_NL_BASE}/euromillions/draw-history", "national_lottery_auto", "euromillions",
        note="Official EuroMillions draw-history feed. EuroMillions HotPicks is derived from the same five main balls.",
    ),
    "euromillions_hotpicks": OfficialSource(
        "euromillions_hotpicks", "The National Lottery", _nl_urls("euromillions"),
        f"{_NL_BASE}/euromillions/draw-history", "national_lottery_auto", "euromillions", parent_game_key="euromillions",
        note="Derived from the official EuroMillions main-ball feed; Lucky Stars are deliberately excluded.",
    ),
    "set_for_life": OfficialSource(
        "set_for_life", "The National Lottery", _nl_urls("set-for-life"),
        f"{_NL_BASE}/set-for-life/draw-history", "national_lottery_auto", "set_for_life",
        note="Official Set For Life draw-history feed.",
    ),
    "thunderball": OfficialSource(
        "thunderball", "The National Lottery", _nl_urls("thunderball"),
        f"{_NL_BASE}/thunderball/draw-history", "national_lottery_auto", "thunderball",
        note="Official Thunderball draw-history feed.",
    ),
    "powerball": OfficialSource(
        "powerball", "Powerball / MUSL",
        ("https://www.powerball.com/previous-results",),
        "https://www.powerball.com/previous-results", "powerball_html", "powerball",
        note="Official Powerball previous-results page. DrawWise uses the official U.S. draw date as the canonical date.",
    ),
}

# Five physical result feeds support seven DrawWise games. HotPicks are derived.
RESULT_FEED_KEYS = ("lotto", "euromillions", "set_for_life", "thunderball", "powerball")
DERIVED_CHILDREN = {
    "lotto": ("lotto_hotpicks",),
    "euromillions": ("euromillions_hotpicks",),
}


def source_feed_count() -> int:
    return len(RESULT_FEED_KEYS)


def source_game_key(game_key: str) -> str:
    source = SOURCES.get(game_key)
    return source.feed_key if source else game_key


def official_source(config: GameConfig) -> OfficialSource:
    source = SOURCES.get(config.key)
    if source is None:
        raise OnlineUpdateError(f"No official online source is configured for {config.name}.")
    return source


def _default_fetch(url: str, timeout: int = 20) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 DrawWise/5.1",
            "Accept": "application/xml,text/xml,text/csv,text/html,application/xhtml+xml;q=0.8,*/*;q=0.5",
            "Accept-Language": "en-GB,en;q=0.9",
            "Cache-Control": "no-cache",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            raise OnlineUpdateError(
                "The official website refused the automated request (HTTP 403). "
                "Use 'Open official page' and import the official file manually; DrawWise will not bypass the site's access controls.",
                diagnostic_url=url,
            ) from exc
        raise OnlineUpdateError(f"Official source returned HTTP {exc.code}: {exc.reason}", diagnostic_url=url) from exc
    except urllib.error.URLError as exc:
        raise OnlineUpdateError(f"Could not reach the official source: {exc.reason}", diagnostic_url=url) from exc
    except TimeoutError as exc:
        raise OnlineUpdateError("Official results request timed out.", diagnostic_url=url) from exc


def _fetch_with_retry(fetch: Callable[[str, int], bytes], url: str, timeout: int, attempts: int = 2) -> bytes:
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return fetch(url, timeout)
        except OnlineUpdateError as exc:
            last = exc
            # Access-control responses should not be hammered with retries.
            if "403" in str(exc):
                raise
        except Exception as exc:  # custom test fetchers / transient socket errors
            last = exc
        if attempt + 1 < attempts:
            time.sleep(0.25 * (attempt + 1))
    if isinstance(last, OnlineUpdateError):
        raise last
    raise OnlineUpdateError(f"Could not fetch official results: {last}", diagnostic_url=url)


def _strip_html(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _payload_kind(raw: bytes) -> str:
    probe = raw[:4096].lstrip().lower()
    if probe.startswith(b"<!doctype html") or probe.startswith(b"<html") or b"<html" in probe[:1000]:
        return "html"
    if probe.startswith(b"<?xml") or probe.startswith(b"<draw") or b"<draw-date" in probe or b"<drawdate" in probe:
        return "xml"
    return "csv"


def _diagnostic_extension(raw: bytes) -> str:
    return {"html": ".html", "xml": ".xml", "csv": ".csv"}.get(_payload_kind(raw), ".txt")


_PB_RESULT_RE = re.compile(
    r"(?P<date>(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),\s+"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s+\d{4})\s+"
    r"(?P<n1>\d{1,2})\s+(?P<n2>\d{1,2})\s+(?P<n3>\d{1,2})\s+"
    r"(?P<n4>\d{1,2})\s+(?P<n5>\d{1,2})\s+(?P<pb>\d{1,2})\s+"
    r"Power\s*Play\s+\d+\s*[xX]",
    flags=re.I,
)


def parse_powerball_html(raw: bytes) -> tuple[Draw, ...]:
    text = _strip_html(raw)
    draws: list[Draw] = []
    seen: set[tuple] = set()
    for match in _PB_RESULT_RE.finditer(text):
        draw_date = datetime.strptime(match.group("date"), "%a, %b %d, %Y").date()
        main = tuple(sorted(int(match.group(f"n{i}")) for i in range(1, 6)))
        special = (int(match.group("pb")),)
        if len(set(main)) != 5 or any(not 1 <= n <= 69 for n in main) or not 1 <= special[0] <= 26:
            continue
        key = (draw_date, main, special)
        if key in seen:
            continue
        seen.add(key)
        draws.append(Draw(draw_date=draw_date, main=main, special=special))
    if not draws:
        raise OnlineUpdateError(
            "The official Powerball page was reached, but DrawWise could not recognise any result rows. "
            "The site layout may have changed; no local data was modified.",
            diagnostic_bytes=raw,
            diagnostic_extension=".html",
        )
    draws.sort(key=lambda d: d.draw_date)
    return tuple(draws)


def _local_tag(tag: str) -> str:
    if "}" in tag:
        tag = tag.rsplit("}", 1)[1]
    return re.sub(r"[^a-z0-9]+", "", tag.lower())


def _date_from_text(value: str) -> date | None:
    value = (value or "").strip()
    formats = ("%Y-%m-%d", "%d-%b-%Y", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d %B %Y")
    if "T" in value:
        value = value.split("T", 1)[0]
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    return None


def _ints(value: str) -> list[int]:
    return [int(x) for x in re.findall(r"\d+", value or "")]


def _element_value_numbers(elem: ET.Element) -> list[int]:
    values: list[int] = []
    text = (elem.text or "").strip()
    if text:
        values.extend(_ints(text))
    for key, val in elem.attrib.items():
        if _local_tag(key) in {"value", "number", "ball", "no", "num"}:
            values.extend(_ints(val))
    return values


def _classify_xml_numbers(record: ET.Element, config: GameConfig) -> tuple[list[int], list[int], str | None, str | None]:
    main: list[int] = []
    special: list[int] = []
    round_id = None
    draw_number = None

    special_tokens = ("luckystar", "star", "lifeball", "thunderball", "powerball", "specialball")
    metadata_tokens = ("machine", "ballset", "setnumber", "drawnumber", "drawno", "jackpot", "winner", "prize")

    parent = {child: node for node in record.iter() for child in node}

    def context(elem: ET.Element) -> str:
        bits = [_local_tag(elem.tag)]
        bits.extend(_local_tag(k) + _local_tag(v) for k, v in elem.attrib.items())
        cur = elem
        for _ in range(3):
            cur = parent.get(cur)
            if cur is None:
                break
            bits.append(_local_tag(cur.tag))
            bits.extend(_local_tag(k) + _local_tag(v) for k, v in cur.attrib.items())
        return " ".join(bits)

    for elem in record.iter():
        tag = _local_tag(elem.tag)
        text = (elem.text or "").strip()
        if tag in {"round", "roundid", "roundnumber"} and text and round_id is None:
            round_id = text
        if tag in {"drawnumber", "drawno", "drawingnumber"} and text and draw_number is None:
            draw_number = text

        if tag in {"drawdate", "drawingdate", "date", "resultdate", "drawnumber", "drawno", "drawingnumber", "round", "roundid", "roundnumber"}:
            continue
        nums = _element_value_numbers(elem)
        if not nums:
            continue
        ctx = context(elem)
        if any(tok in ctx for tok in metadata_tokens):
            continue
        is_special = any(tok in ctx for tok in special_tokens)
        # Generic ball/number elements are useful; container text is only used when
        # its tag explicitly describes a number set.
        numberish = any(tok in tag for tok in ("ball", "number", "main", "winning", "star", "special"))
        if not numberish and len(list(elem)):
            continue
        target = special if is_special else main
        for n in nums:
            if n not in target:
                target.append(n)

    return main, special, round_id, draw_number


def _record_from_xml_node(record: ET.Element, config: GameConfig) -> Draw | None:
    draw_date = None
    for elem in record.iter():
        if _local_tag(elem.tag) in {"drawdate", "drawingdate", "date", "resultdate"}:
            draw_date = _date_from_text(elem.text or "")
            if draw_date:
                break
    if draw_date is None:
        return None

    main, special, round_id, draw_number = _classify_xml_numbers(record, config)
    expected_main = len(config.main_columns)

    # If the source represents number sets as generic <set><ball>...</ball></set>,
    # classify groups by cardinality when the semantic tags were not enough.
    if len(main) < expected_main or (config.special_pick and len(special) < config.special_pick):
        groups: list[list[int]] = []
        for elem in record.iter():
            if _local_tag(elem.tag) not in {"set", "balls", "numbers", "winningnumbers", "mainnumbers", "luckystars"}:
                continue
            vals: list[int] = []
            for child in elem.iter():
                if child is elem:
                    continue
                for n in _element_value_numbers(child):
                    if n not in vals:
                        vals.append(n)
            if vals:
                groups.append(vals)
        for vals in groups:
            clean_main = [n for n in vals if config.main_min <= n <= config.main_max]
            if len(main) < expected_main and len(clean_main) >= expected_main:
                main = clean_main[:expected_main]
                continue
            if config.special_pick and config.special_min is not None and config.special_max is not None:
                clean_special = [n for n in vals if config.special_min <= n <= config.special_max]
                if len(special) < config.special_pick and len(clean_special) >= config.special_pick:
                    special = clean_special[:config.special_pick]

    # Lotto XML can include the bonus ball after the six main balls. It is not a
    # user-selected special ball, so only the first six main draw values are retained.
    main = [n for n in main if config.main_min <= n <= config.main_max]
    if len(main) < expected_main:
        return None
    main = main[:expected_main]
    if len(set(main)) != expected_main:
        return None

    if config.special_pick:
        assert config.special_min is not None and config.special_max is not None
        special = [n for n in special if config.special_min <= n <= config.special_max]
        if len(special) < config.special_pick:
            # Last-resort support for XML that puts main and special values in a
            # single ordered ball list: values after the main selection become specials.
            all_ball_values: list[int] = []
            for elem in record.iter():
                if "ball" in _local_tag(elem.tag) or "number" in _local_tag(elem.tag):
                    all_ball_values.extend(_element_value_numbers(elem))
            tail = all_ball_values[expected_main:]
            special = [n for n in tail if config.special_min <= n <= config.special_max]
        special = special[:config.special_pick]
        if len(special) != config.special_pick or len(set(special)) != config.special_pick:
            return None
    else:
        special = []

    return Draw(draw_date, tuple(sorted(main)), tuple(sorted(special)), round_id, draw_number)


def _xml_candidate_records(root: ET.Element) -> list[ET.Element]:
    records: list[ET.Element] = []
    for elem in root.iter():
        local = _local_tag(elem.tag)
        if local in {"draw", "result", "drawresult", "drawing", "entry", "record"}:
            date_nodes = [x for x in elem.iter() if _local_tag(x.tag) in {"drawdate", "drawingdate", "date", "resultdate"}]
            if date_nodes:
                records.append(elem)
    # Prefer the smallest containers: remove any candidate that strictly contains
    # another candidate with a date.
    ids = {id(x) for x in records}
    smallest = []
    for elem in records:
        nested = [x for x in elem.iter() if x is not elem and id(x) in ids]
        if not nested:
            smallest.append(elem)
    return smallest or records


def _parse_xml_by_date_chunks(text: str, config: GameConfig) -> list[Draw]:
    """Regex fallback for historical XML where draw metadata and ball sets are siblings.

    Some National Lottery XML snapshots place </draw><balls>...</balls> after the date,
    so slicing at <draw-date> does not produce a well-formed XML fragment. This parser
    deliberately uses only narrow result tags and then validates the complete Draw.
    """
    date_re = re.compile(
        r"<(?:draw-date|drawdate|drawing-date|date)[^>]*>\s*"
        r"(\d{4}-\d{2}-\d{2}|\d{1,2}[-/][A-Za-z]{3}[-/]\d{4}|\d{1,2}/\d{1,2}/\d{4})\s*</",
        re.I,
    )
    matches = list(date_re.finditer(text))
    draws: list[Draw] = []
    expected_main = len(config.main_columns)

    def values_from_fragment(fragment: str) -> list[int]:
        vals: list[int] = []
        # Normal element text, e.g. <ball>17</ball> or <number>17</number>.
        for m in re.finditer(r"<(?:ball|number|num|value)[^>]*>\s*(\d{1,3})\s*</", fragment, re.I):
            n = int(m.group(1))
            if n not in vals:
                vals.append(n)
        # Attribute-only shapes, e.g. <ball value="17"/>.
        for m in re.finditer(r"<(?:ball|number)[^>]*(?:value|number)=[\"'](\d{1,3})[\"'][^>]*/?>", fragment, re.I):
            n = int(m.group(1))
            if n not in vals:
                vals.append(n)
        return vals

    for i, match in enumerate(matches):
        draw_date = _date_from_text(match.group(1))
        if draw_date is None:
            continue
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        chunk = text[start:end]

        draw_no_m = re.search(r"<(?:draw-number|drawnumber|draw-no|drawing-number)[^>]*>\s*([^<]+)", chunk, re.I)
        round_m = re.search(r"<(?:round|round-id|round-number)[^>]*>\s*([^<]+)", chunk, re.I)
        draw_number = draw_no_m.group(1).strip() if draw_no_m else None
        round_id = round_m.group(1).strip() if round_m else None

        set_fragments = re.findall(r"<set\b[^>]*>(.*?)</set>", chunk, flags=re.I | re.S)
        groups = [values_from_fragment(fragment) for fragment in set_fragments]
        groups = [g for g in groups if g]

        main_values: list[int] = []
        special_values: list[int] = []
        if groups:
            for group in groups:
                candidates = [n for n in group if config.main_min <= n <= config.main_max]
                if len(candidates) >= expected_main:
                    main_values = candidates[:expected_main]
                    break
            if config.special_pick and config.special_min is not None and config.special_max is not None:
                # Prefer a later set for specials. A Lucky Star/Life Ball/Thunderball
                # set naturally has the requested cardinality and valid range.
                main_index = next((idx for idx, g in enumerate(groups) if [n for n in g if config.main_min <= n <= config.main_max][:expected_main] == main_values), -1)
                for idx, group in enumerate(groups):
                    if idx == main_index:
                        continue
                    candidates = [n for n in group if config.special_min <= n <= config.special_max]
                    if len(candidates) >= config.special_pick:
                        special_values = candidates[:config.special_pick]
                        break

        if len(main_values) < expected_main:
            # Narrow fallback to explicit ball/number tags in document order.
            all_values = values_from_fragment(chunk)
            main_values = [n for n in all_values if config.main_min <= n <= config.main_max][:expected_main]
            if config.special_pick and config.special_min is not None and config.special_max is not None:
                tail = all_values[expected_main:]
                special_values = [n for n in tail if config.special_min <= n <= config.special_max][:config.special_pick]

        if len(main_values) != expected_main or len(set(main_values)) != expected_main:
            continue
        if config.special_pick:
            if len(special_values) != config.special_pick or len(set(special_values)) != config.special_pick:
                continue
        else:
            special_values = []

        draws.append(
            Draw(
                draw_date,
                tuple(sorted(main_values)),
                tuple(sorted(special_values)),
                round_id,
                draw_number,
            )
        )
    return draws


def parse_national_lottery_xml(raw: bytes, config: GameConfig) -> tuple[Draw, ...]:
    text = raw.decode("utf-8-sig", errors="replace")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise OnlineUpdateError(
            f"The official XML response is not well formed: {exc}",
            diagnostic_bytes=raw, diagnostic_extension=".xml",
        ) from exc

    # Work outward from every date element until a container has enough context
    # to reconstruct one complete draw. This supports both nested <result> records
    # and the National Lottery's historical metadata/balls sibling layouts.
    parent = {child: node for node in root.iter() for child in node}
    date_nodes = [
        elem for elem in root.iter()
        if _local_tag(elem.tag) in {"drawdate", "drawingdate", "date", "resultdate"}
        and _date_from_text(elem.text or "") is not None
    ]
    draws: list[Draw] = []
    for date_node in date_nodes:
        node = date_node
        for _ in range(7):
            node = parent.get(node)
            if node is None:
                break
            node_dates = [
                e for e in node.iter()
                if _local_tag(e.tag) in {"drawdate", "drawingdate", "date", "resultdate"}
                and _date_from_text(e.text or "") is not None
            ]
            if len(node_dates) > 1:
                continue
            draw = _record_from_xml_node(node, config)
            if draw and draw.draw_date == _date_from_text(date_node.text or ""):
                draws.append(draw)
                break

    if not draws:
        draws = _parse_xml_by_date_chunks(text, config)

    # Deduplicate exact records while preserving legitimate same-date rounds.
    unique: dict[tuple, Draw] = {}
    for draw in draws:
        key = (draw.draw_date, draw.draw_number or "", draw.round_id or "", draw.main, draw.special)
        unique[key] = draw
    draws = sorted(unique.values(), key=lambda d: (d.draw_date, d.draw_number or "", d.round_id or ""))
    if not draws:
        tags = sorted({_local_tag(e.tag) for e in root.iter()})[:40]
        raise OnlineUpdateError(
            "The official XML was downloaded, but DrawWise could not map any complete draw records. "
            f"Detected tags: {tags}",
            diagnostic_bytes=raw, diagnostic_extension=".xml",
            diagnostic_detail=f"Detected XML tags: {tags}",
        )
    return tuple(draws)


def _parse_national_lottery_csv(raw: bytes, config: GameConfig, *, file_stem: str) -> tuple[Draw, ...]:
    if _payload_kind(raw) == "html":
        preview = _strip_html(raw)[:350]
        raise OnlineUpdateError(
            "The structured-results endpoint returned HTML instead of a CSV table. DrawWise will try the alternate official feed.",
            diagnostic_bytes=raw, diagnostic_extension=".html", diagnostic_detail=preview,
        )
    with tempfile.TemporaryDirectory(prefix="drawwise-online-") as tmp:
        path = Path(tmp) / f"{file_stem}.csv"
        path.write_bytes(raw)
        report = load_flexible_csv(path, config)
        if not report.draws:
            issue_text = "; ".join(issue.detail for issue in report.issues[:4]) or "No usable rows found"
            raise OnlineUpdateError(
                "The official CSV was downloaded, but its columns could not be mapped safely. "
                f"No local data was modified. Detail: {issue_text}",
                diagnostic_bytes=raw,
                diagnostic_extension=".csv",
                diagnostic_detail=issue_text,
            )
        return report.draws


def _parse_national_lottery_payload(raw: bytes, config: GameConfig, *, file_stem: str) -> tuple[tuple[Draw, ...], str]:
    kind = _payload_kind(raw)
    if kind == "xml":
        return parse_national_lottery_xml(raw, config), "National Lottery XML"
    if kind == "html":
        preview = _strip_html(raw)[:350]
        raise OnlineUpdateError(
            "The structured-results endpoint returned an HTML page rather than machine-readable draw history.",
            diagnostic_bytes=raw, diagnostic_extension=".html", diagnostic_detail=preview,
        )
    return _parse_national_lottery_csv(raw, config, file_stem=file_stem), "National Lottery CSV"


def _project_parent_draws(game_key: str, parent_draws: tuple[Draw, ...]) -> tuple[Draw, ...]:
    if game_key == "euromillions_hotpicks":
        return tuple(Draw(d.draw_date, d.main, (), d.round_id, d.draw_number) for d in parent_draws)
    if game_key == "lotto_hotpicks":
        # Lotto HotPicks Pick 5 is evaluated against all six Lotto draw balls.
        return tuple(Draw(d.draw_date, d.main, (), d.round_id, d.draw_number) for d in parent_draws)
    return parent_draws


def derive_result_for_game(config: GameConfig, parent_result: OnlineResult) -> OnlineResult:
    """Project a checked parent feed into a derived HotPicks result without another network call."""
    source = official_source(config)
    return OnlineResult(
        game_key=config.key,
        provider=parent_result.provider,
        data_url=parent_result.data_url,
        page_url=source.page_url,
        fetched_at=parent_result.fetched_at,
        draws=_project_parent_draws(config.key, parent_result.draws),
        note=source.note,
        parser_used=parent_result.parser_used + " → derived HotPicks",
    )


def fetch_official_results(
    config: GameConfig,
    *,
    registry: dict[str, GameConfig] | None = None,
    fetcher: Callable[[str, int], bytes] | None = None,
    cache: MutableMapping[str, bytes] | None = None,
    timeout: int = 20,
) -> OnlineResult:
    source = official_source(config)
    fetch = fetcher or _default_fetch
    cache = cache if cache is not None else {}

    parse_config = config
    if source.parent_game_key:
        if registry is None or source.parent_game_key not in registry:
            raise OnlineUpdateError(f"Parent game configuration {source.parent_game_key!r} is required for {config.name}.")
        parse_config = registry[source.parent_game_key]

    last_error: OnlineUpdateError | None = None
    for url in source.data_urls:
        try:
            raw = cache.get(url)
            if raw is None:
                raw = _fetch_with_retry(fetch, url, timeout, attempts=2)
                cache[url] = raw

            if source.source_kind == "powerball_html":
                draws = parse_powerball_html(raw)
                parser_used = "Powerball official HTML"
            elif source.source_kind == "national_lottery_auto":
                parent_draws, parser_used = _parse_national_lottery_payload(raw, parse_config, file_stem=parse_config.key)
                draws = _project_parent_draws(config.key, parent_draws)
            else:
                raise OnlineUpdateError(f"Unsupported online source kind: {source.source_kind}")

            return OnlineResult(
                game_key=config.key,
                provider=source.provider,
                data_url=url,
                page_url=source.page_url,
                fetched_at=datetime.now(),
                draws=tuple(sorted(draws, key=lambda d: (d.draw_date, d.round_id or "", d.draw_number or ""))),
                note=source.note,
                parser_used=parser_used,
            )
        except OnlineUpdateError as exc:
            # Preserve the actual URL for diagnostics, then try the next official
            # structured endpoint when available.
            if not exc.diagnostic_url:
                exc.diagnostic_url = url
            last_error = exc
            continue

    if last_error is not None:
        msg = str(last_error)
        if len(source.data_urls) > 1:
            msg += " Both official structured feed formats were tried; no local history was modified."
        raise OnlineUpdateError(
            msg,
            diagnostic_bytes=last_error.diagnostic_bytes,
            diagnostic_url=last_error.diagnostic_url,
            diagnostic_extension=last_error.diagnostic_extension,
            diagnostic_detail=last_error.diagnostic_detail,
        )
    raise OnlineUpdateError("No official endpoint could be checked.")


def _signature(draw: Draw) -> tuple[tuple[int, ...], tuple[int, ...]]:
    return draw.main, draw.special


def normalise_existing_for_official_merge(
    config: GameConfig, existing: list[Draw], incoming: tuple[Draw, ...] | list[Draw]
) -> tuple[list[Draw], int]:
    """Remove known date-shift duplicates before merging official results.

    Earlier DrawWise Powerball seed data used the UK calendar date (the day after the
    U.S. draw). Official Powerball results use the U.S. draw date. If the complete
    winning-number signature is identical and the dates differ by exactly one day,
    the older shifted local record is removed so the official date becomes canonical.
    """
    if config.key != "powerball":
        return list(existing), 0

    official_by_sig = {_signature(draw): draw for draw in incoming}
    cleaned: list[Draw] = []
    normalised = 0
    for local in existing:
        official = official_by_sig.get(_signature(local))
        if official is not None and local.draw_date != official.draw_date:
            delta = abs((local.draw_date - official.draw_date).days)
            if delta == 1:
                normalised += 1
                continue
        cleaned.append(local)
    return cleaned, normalised


def preview_official_update(config: GameConfig, existing: list[Draw], result: OnlineResult) -> UpdatePreview:
    cleaned_existing, date_normalisations = normalise_existing_for_official_merge(config, existing, result.draws)
    merged, summary = merge_history(cleaned_existing, list(result.draws))
    local_latest = max((d.draw_date for d in existing), default=None)
    online_latest = max((d.draw_date for d in result.draws), default=None)
    if summary.new_records or summary.replaced_records or date_normalisations:
        status = "Update available"
    elif online_latest and local_latest and online_latest < local_latest:
        status = "Local history newer"
    else:
        status = "Up to date"
    return UpdatePreview(
        local_rows=len(existing),
        online_rows=len(result.draws),
        final_rows=len(merged),
        new_records=summary.new_records,
        corrected_records=summary.replaced_records,
        unchanged_records=summary.unchanged_records,
        date_normalisations=date_normalisations,
        local_latest=local_latest,
        online_latest=online_latest,
        status=status,
    )


def merge_official_update(
    config: GameConfig, existing: list[Draw], result: OnlineResult
) -> tuple[list[Draw], UpdatePreview]:
    preview = preview_official_update(config, existing, result)
    cleaned_existing, _ = normalise_existing_for_official_merge(config, existing, result.draws)
    merged, _ = merge_history(cleaned_existing, list(result.draws))
    return merged, preview
