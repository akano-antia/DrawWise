from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable

from core.models import Draw, GameConfig


@dataclass(frozen=True)
class ArchiveEra:
    key: str
    label: str
    start: date | None
    end: date | None
    main_shape: str
    special_shape: str | None = None
    current_analysis_compatible: bool = False
    note: str = ""

    def contains(self, value: date) -> bool:
        if self.start is not None and value < self.start:
            return False
        if self.end is not None and value > self.end:
            return False
        return True

    @property
    def range_label(self) -> str:
        left = self.start.isoformat() if self.start else "earliest"
        right = self.end.isoformat() if self.end else "present"
        return f"{left} to {right}"


@dataclass(frozen=True)
class RuleProfile:
    game_key: str
    current_analysis_start: date | None
    current_analysis_label: str
    current_purchase_rounds: int = 1
    current_purchase_effective: date | None = None
    purchase_note: str = ""
    eras: tuple[ArchiveEra, ...] = ()


# Rule-era dates are used conservatively: DrawWise never upgrades an older draw into the
# current statistical universe merely because all observed numbers happen to fall inside
# today's numeric limits.
_PROFILES: dict[str, RuleProfile] = {
    "euromillions": RuleProfile(
        game_key="euromillions",
        current_analysis_start=date(2016, 9, 27),
        current_analysis_label="Current 5/50 + 2/12 Lucky Stars era",
        eras=(
            ArchiveEra(
                "em_12_star", "Current 12-Lucky-Star era", date(2016, 9, 27), None,
                "5 from 1-50", "2 Lucky Stars from 1-12", True,
                "Recommended full-ticket analysis era.",
            ),
            ArchiveEra(
                "em_11_star", "11-Lucky-Star era", date(2011, 5, 10), date(2016, 9, 23),
                "5 from 1-50", "2 Lucky Stars from 1-11", False,
                "Main-ball universe is comparable, but Lucky-Star probabilities differ from the current game.",
            ),
            ArchiveEra(
                "em_9_star", "9-Lucky-Star era", None, date(2011, 5, 6),
                "5 from 1-50", "2 Lucky Stars from 1-9", False,
                "Legacy Lucky-Star universe; excluded from current full-ticket analysis.",
            ),
        ),
    ),
    "powerball": RuleProfile(
        game_key="powerball",
        current_analysis_start=date(2015, 10, 7),
        current_analysis_label="Current 5/69 + 1/26 Powerball era",
        eras=(
            ArchiveEra(
                "pb_current", "Current 5/69 + 1/26 era", date(2015, 10, 7), None,
                "5 from 1-69", "1 Powerball from 1-26", True,
                "Recommended analysis era for the current Powerball matrix.",
            ),
            ArchiveEra(
                "pb_legacy", "Legacy Powerball matrix", None, date(2015, 10, 3),
                "legacy main-ball matrix", "legacy Powerball matrix", False,
                "Older matrices are retained only when structurally importable and are excluded from current analysis.",
            ),
        ),
    ),
    "lotto": RuleProfile(
        game_key="lotto",
        current_analysis_start=date(2015, 10, 10),
        current_analysis_label="59-ball Lotto era",
        current_purchase_rounds=2,
        current_purchase_effective=date(2026, 6, 10),
        purchase_note=(
            "Since 10-Jun-2026, one purchased Lotto line is entered into two independent 6/59 rounds on each draw night. "
            "Historical 59-ball rounds before that date remain useful for number-analysis/backtest purposes."
        ),
        eras=(
            ArchiveEra(
                "lotto_59_dual", "59-ball two-round purchase era", date(2026, 6, 10), None,
                "6 from 1-59", None, True,
                "Current purchase format: the same line receives two Lotto rounds per draw night.",
            ),
            ArchiveEra(
                "lotto_59_single", "59-ball single-round era", date(2015, 10, 10), date(2026, 6, 6),
                "6 from 1-59", None, True,
                "Same 6/59 number universe; compatible with current main-number historical analysis.",
            ),
            ArchiveEra(
                "lotto_49", "Legacy 49-ball era", None, date(2015, 10, 7),
                "6 from 1-49", None, False,
                "Different main-number universe; excluded from current 6/59 analysis.",
            ),
        ),
    ),
    "lotto_hotpicks": RuleProfile(
        game_key="lotto_hotpicks",
        current_analysis_start=date(2015, 10, 10),
        current_analysis_label="59-ball Lotto HotPicks era",
        current_purchase_rounds=2,
        current_purchase_effective=date(2026, 6, 10),
        purchase_note=(
            "Current Lotto HotPicks follows Lotto's two-round draw-night format. Historical 59-ball rounds remain compatible with the current number universe."
        ),
        eras=(
            ArchiveEra(
                "lhp_59_dual", "59-ball two-round purchase era", date(2026, 6, 10), None,
                "Pick 5 from Lotto 1-59", None, True,
            ),
            ArchiveEra(
                "lhp_59_single", "59-ball single-round era", date(2015, 10, 10), date(2026, 6, 6),
                "Pick 5 from Lotto 1-59", None, True,
            ),
            ArchiveEra(
                "lhp_legacy", "Legacy Lotto matrix", None, date(2015, 10, 7),
                "legacy Lotto main-ball matrix", None, False,
            ),
        ),
    ),
}


def rule_profile(config: GameConfig) -> RuleProfile:
    return _PROFILES.get(
        config.key,
        RuleProfile(
            game_key=config.key,
            current_analysis_start=None,
            current_analysis_label="Stored history uses the configured current number universe",
            current_purchase_rounds=1,
            eras=(
                ArchiveEra(
                    f"{config.key}_configured", "Configured game era", None, None,
                    f"{config.main_pick} from {config.main_min}-{config.main_max}",
                    (
                        f"{config.special_pick} {config.special_name or 'special'} from {config.special_min}-{config.special_max}"
                        if config.special_pick else None
                    ),
                    True,
                    "No separate legacy matrix is currently configured for this game.",
                ),
            ),
        ),
    )


def classify_draw(config: GameConfig, draw: Draw) -> ArchiveEra:
    profile = rule_profile(config)
    for era in profile.eras:
        if era.contains(draw.draw_date):
            return era
    # Defensive fallback for a deliberately sparse rule table.
    return ArchiveEra(
        "unclassified", "Unclassified rule era", None, None,
        f"{config.main_pick} from {config.main_min}-{config.main_max}",
        None, False,
        "The draw date is not covered by DrawWise's configured era table.",
    )


def filter_current_analysis_draws(config: GameConfig, draws: Iterable[Draw]) -> list[Draw]:
    profile = rule_profile(config)
    if profile.current_analysis_start is None:
        return sorted(list(draws), key=lambda d: (d.draw_date, d.round_id or "", d.draw_number or ""))
    compatible = [d for d in draws if d.draw_date >= profile.current_analysis_start]
    return sorted(compatible, key=lambda d: (d.draw_date, d.round_id or "", d.draw_number or ""))


def era_counts(config: GameConfig, draws: Iterable[Draw]) -> list[tuple[ArchiveEra, int]]:
    profile = rule_profile(config)
    counts = {era.key: 0 for era in profile.eras}
    extras = 0
    for draw in draws:
        era = classify_draw(config, draw)
        if era.key in counts:
            counts[era.key] += 1
        else:
            extras += 1
    rows = [(era, counts[era.key]) for era in profile.eras]
    if extras:
        rows.append((ArchiveEra("unclassified", "Unclassified", None, None, "—"), extras))
    return rows


def era_summary(config: GameConfig, draws: Iterable[Draw]) -> str:
    draws = list(draws)
    compatible = filter_current_analysis_draws(config, draws)
    profile = rule_profile(config)
    bits = [f"Analysis era: {profile.current_analysis_label}", f"analysis-ready {len(compatible)}/{len(draws)} stored"]
    for era, count in era_counts(config, draws):
        if count:
            bits.append(f"{era.label}: {count}")
    if profile.current_purchase_rounds > 1:
        bits.append(f"current purchase rounds/line: {profile.current_purchase_rounds}")
    return " • ".join(bits)


def era_detail_lines(config: GameConfig, draws: Iterable[Draw]) -> list[str]:
    profile = rule_profile(config)
    rows = []
    for era, count in era_counts(config, draws):
        compat = "analysis-compatible" if era.current_analysis_compatible else "legacy/excluded"
        special = f" + {era.special_shape}" if era.special_shape else ""
        rows.append(f"{era.label} ({era.range_label}): {count} record(s) • {era.main_shape}{special} • {compat}")
    if profile.purchase_note:
        rows.append("Current purchase rule: " + profile.purchase_note)
    return rows


def current_purchase_rounds(config: GameConfig) -> int:
    return max(1, rule_profile(config).current_purchase_rounds)
