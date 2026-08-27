from __future__ import annotations

from collections import Counter
from typing import Iterable

from core.models import Draw, GameConfig, NumberStat


def _percentile_map(values: dict[int, float], reverse: bool = False) -> dict[int, float]:
    if not values:
        return {}
    items = sorted(values.items(), key=lambda kv: (kv[1], kv[0]), reverse=reverse)
    n = len(items)
    if n == 1:
        return {items[0][0]: 1.0}
    # Equal values get close-but-deterministic ranks. This is for selection scoring, not inference.
    return {number: rank / (n - 1) for rank, (number, _) in enumerate(items)}


def number_stats(
    draws: list[Draw],
    config: GameConfig,
    recent_window: int = 20,
    field: str = "main",
) -> list[NumberStat]:
    if field not in {"main", "special"}:
        raise ValueError("field must be 'main' or 'special'")

    if field == "main":
        low, high = config.main_min, config.main_max
        drawn_per_draw = len(config.main_columns)
        extractor = lambda d: d.main
    else:
        if not config.special_columns or config.special_min is None or config.special_max is None:
            return []
        low, high = config.special_min, config.special_max
        drawn_per_draw = len(config.special_columns)
        extractor = lambda d: d.special

    universe = list(range(low, high + 1))
    all_counter = Counter(n for d in draws for n in extractor(d))
    recent_draws = draws[-min(recent_window, len(draws)):]
    recent_counter = Counter(n for d in recent_draws for n in extractor(d))

    gaps: dict[int, int] = {}
    for n in universe:
        gap = len(draws)
        for i, d in enumerate(reversed(draws)):
            if n in extractor(d):
                gap = i
                break
        gaps[n] = gap

    expected_all = max(1e-12, len(draws) * drawn_per_draw / len(universe))
    expected_recent = max(1e-12, len(recent_draws) * drawn_per_draw / len(universe))

    freq_index = {n: all_counter[n] / expected_all for n in universe}
    recent_index = {n: recent_counter[n] / expected_recent for n in universe}

    # Ranking components. Historical and recent frequency dominate; gap is deliberately weak because
    # an 'overdue' number is not made more likely by an independent draw.
    f_rank = _percentile_map(freq_index)
    r_rank = _percentile_map(recent_index)
    g_rank = _percentile_map({n: float(gaps[n]) for n in universe})

    result = []
    for n in universe:
        score = (0.55 * f_rank[n]) + (0.35 * r_rank[n]) + (0.10 * g_rank[n])
        result.append(
            NumberStat(
                number=n,
                count=all_counter[n],
                recent_count=recent_counter[n],
                gap_draws=gaps[n],
                frequency_index=freq_index[n],
                recent_index=recent_index[n],
                score=score,
            )
        )

    return sorted(result, key=lambda s: (-s.score, -s.count, s.number))


def hot_cold_pool(stats: list[NumberStat], size: int) -> list[int]:
    """Return a deliberate hot/cold blend: roughly half top-ranked, half bottom-ranked."""
    size = max(1, min(size, len(stats)))
    hot_n = (size + 1) // 2
    cold_n = size - hot_n
    hot = [s.number for s in stats[:hot_n]]
    cold = [s.number for s in reversed(stats) if s.number not in hot][:cold_n]
    return sorted(hot + cold)


def ranked_pool(stats: list[NumberStat], size: int) -> list[int]:
    size = max(1, min(size, len(stats)))
    return sorted(s.number for s in stats[:size])


def score_lookup(stats: Iterable[NumberStat]) -> dict[int, float]:
    return {s.number: s.score for s in stats}
