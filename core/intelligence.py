from __future__ import annotations

"""DrawWise 5.4 mathematical portfolio intelligence.

This module deliberately separates *selection optimisation* from prediction claims.
For a fair lottery every complete valid line has the same jackpot probability.  The
algorithms here optimise a *portfolio* for coverage, overlap, crowd-sharing risk and
chosen match-threshold objectives, then challenge that portfolio against random
portfolios with Monte Carlo simulation.
"""

from collections import Counter
from dataclasses import dataclass
import itertools
import math
import random
from typing import Iterable

from core.models import Draw, GameConfig, Ticket


OBJECTIVES = (
    "Best overall portfolio",
    "Minimise no-win proxy",
    "Maximise 3+ coverage",
    "Minimise prize sharing",
)


@dataclass(frozen=True)
class RandomnessGate:
    normalized_entropy: float
    max_z_deviation: float
    uniformity_score: float
    history_weight: float
    verdict: str


@dataclass(frozen=True)
class PortfolioIntelligence:
    objective: str
    portfolio_rating: float
    pair_coverage: float
    triple_coverage: float
    avg_overlap: float
    max_overlap: int
    crowd_risk: float
    history_gate: RandomnessGate
    candidates_evaluated: int
    search_moves: int
    challenge_draws: int
    challenge_random_portfolios: int
    target_label: str
    target_probability: float
    single_line_target_probability: float
    random_median_probability: float
    random_percentile: float
    jackpot_probability: float | None
    jackpot_odds: str


def _entropy_from_counts(counts: Iterable[int]) -> float:
    values = [max(0, int(v)) for v in counts]
    total = sum(values)
    n = len(values)
    if total <= 0 or n <= 1:
        return 0.0
    h = 0.0
    for value in values:
        if value:
            p = value / total
            h -= p * math.log(p)
    return h / math.log(n)


def randomness_gate(draws: list[Draw], config: GameConfig) -> RandomnessGate:
    """Conservative gate for historical influence.

    This is intentionally not a next-number predictor.  It checks whether marginal
    number counts look materially non-uniform, using entropy plus binomial marginal
    z-deviations.  Even when a deviation is present, historical influence is capped
    at 20% because retrospective deviations are easy to overfit.
    """
    if not draws:
        return RandomnessGate(0.0, 0.0, 0.0, 0.0, "No history")

    universe = list(range(config.main_min, config.main_max + 1))
    counter = Counter(n for draw in draws for n in draw.main)
    counts = [counter[n] for n in universe]
    entropy = _entropy_from_counts(counts)

    n_draws = len(draws)
    drawn_per_draw = len(config.main_columns)
    p = drawn_per_draw / config.main_range_size
    expected = n_draws * p
    variance = max(1e-12, n_draws * p * (1.0 - p))
    sigma = math.sqrt(variance)
    max_z = max(abs(c - expected) / sigma for c in counts) if sigma else 0.0

    # 1.0 means "looks very uniform"; 0.0 means "large descriptive deviation".
    entropy_component = max(0.0, min(1.0, (entropy - 0.94) / 0.06))
    z_component = max(0.0, min(1.0, (5.0 - max_z) / 2.5))
    uniformity = (0.65 * entropy_component) + (0.35 * z_component)

    sample_reliability = min(1.0, n_draws / 300.0)
    departure = 1.0 - uniformity
    # Keep at least a 1% descriptive tie-break weight, cap at 20%.
    history_weight = min(0.20, 0.01 + (0.19 * departure * sample_reliability))

    if entropy >= 0.985 and max_z < 3.5:
        verdict = "Consistent with near-uniform history"
        history_weight = min(history_weight, 0.04)
    elif max_z >= 5.0 or entropy < 0.965:
        verdict = "Descriptive deviation detected — heavily shrink history"
    else:
        verdict = "Mild descriptive deviation — history remains secondary"

    return RandomnessGate(
        normalized_entropy=entropy,
        max_z_deviation=max_z,
        uniformity_score=uniformity,
        history_weight=history_weight,
        verdict=verdict,
    )


def exact_main_match_probability(config: GameConfig, matches: int) -> float:
    """Exact hypergeometric probability of `matches` main balls on one line."""
    k = config.main_pick
    n = config.main_range_size
    if matches < 0 or matches > k:
        return 0.0
    misses = k - matches
    nonwinning = n - k
    if misses > nonwinning:
        return 0.0
    return (math.comb(k, matches) * math.comb(nonwinning, misses)) / math.comb(n, k)


def exact_special_match_probability(config: GameConfig, matches: int) -> float:
    if not config.special_pick:
        return 1.0 if matches == 0 else 0.0
    k = config.special_pick
    n = config.special_range_size
    if matches < 0 or matches > k:
        return 0.0
    misses = k - matches
    nonwinning = n - k
    if misses > nonwinning:
        return 0.0
    return (math.comb(k, matches) * math.comb(nonwinning, misses)) / math.comb(n, k)


def exact_complete_match_probability(config: GameConfig, main_matches: int, special_matches: int = 0) -> float:
    return exact_main_match_probability(config, main_matches) * exact_special_match_probability(config, special_matches)


def exact_main_threshold_probability(config: GameConfig, minimum_matches: int) -> float:
    return sum(exact_main_match_probability(config, r) for r in range(minimum_matches, config.main_pick + 1))


def overlap_histogram(lines: Iterable[tuple[int, ...]]) -> Counter[int]:
    line_sets = [set(line) for line in lines]
    return Counter(len(a & b) for a, b in itertools.combinations(line_sets, 2))


def balanced_overlap_penalty(lines: Iterable[tuple[int, ...]]) -> float:
    """Convex penalty for concentrated ticket overlap.

    The convex cost means one near-duplicate pair is treated as worse than several
    one-number overlaps.  That is the practical majorization idea used by the V5.4
    portfolio optimiser.
    """
    hist = overlap_histogram(lines)
    return sum(count * ((2.0 ** overlap) - 1.0) for overlap, count in hist.items() if overlap > 0)


def unique_tuple_coverage(lines: Iterable[tuple[int, ...]], size: int) -> int:
    covered: set[tuple[int, ...]] = set()
    for line in lines:
        if len(line) >= size:
            covered.update(itertools.combinations(sorted(line), size))
    return len(covered)


def _line_mask(line: Iterable[int]) -> int:
    mask = 0
    for n in line:
        mask |= 1 << int(n)
    return mask


def _portfolio_hit(draw_main_mask: int, draw_special_mask: int, ticket_masks: list[tuple[int, int]], *, main_threshold: int, require_special: bool = False) -> bool:
    for main_mask, special_mask in ticket_masks:
        if (draw_main_mask & main_mask).bit_count() < main_threshold:
            continue
        if require_special and special_mask and (draw_special_mask & special_mask).bit_count() == 0:
            continue
        return True
    return False


def objective_target(config: GameConfig, objective: str) -> tuple[int, bool, str]:
    if objective == "Minimise no-win proxy":
        # Deliberately labelled a proxy rather than "any prize" because prize tables
        # can change and some games have special-ball-only low tiers.
        return 2, False, "2+ main-match coverage proxy"
    if objective == "Maximise 3+ coverage":
        return 3, False, "3+ main-match coverage"
    return 3, False, "3+ main-match coverage"


def monte_carlo_challenge(
    config: GameConfig,
    tickets: list[Ticket],
    objective: str,
    *,
    simulations: int = 8000,
    random_portfolios: int = 16,
    seed: int = 540013,
) -> dict[str, float | int | str]:
    """Challenge one portfolio against random portfolios on identical synthetic draws.

    This evaluates portfolio construction; it does not predict the next draw.
    """
    if not tickets:
        return {
            "draws": 0,
            "random_portfolios": 0,
            "target_label": "—",
            "target_probability": 0.0,
            "random_median_probability": 0.0,
            "random_percentile": 0.0,
        }

    rng = random.Random(seed)
    simulations = max(1000, int(simulations))
    random_portfolios = max(4, int(random_portfolios))
    threshold, require_special, label = objective_target(config, objective)

    main_universe = list(range(config.main_min, config.main_max + 1))
    special_universe = list(range(config.special_min, config.special_max + 1)) if config.special_pick else []

    target_masks = [(_line_mask(t.main), _line_mask(t.special)) for t in tickets]

    random_masks: list[list[tuple[int, int]]] = []
    for _ in range(random_portfolios):
        seen: set[tuple[tuple[int, ...], tuple[int, ...]]] = set()
        portfolio: list[tuple[int, int]] = []
        while len(portfolio) < len(tickets):
            main = tuple(sorted(rng.sample(main_universe, config.main_pick)))
            special = tuple(sorted(rng.sample(special_universe, config.special_pick))) if config.special_pick else ()
            sig = (main, special)
            if sig in seen:
                continue
            seen.add(sig)
            portfolio.append((_line_mask(main), _line_mask(special)))
        random_masks.append(portfolio)

    target_hits = 0
    random_hits = [0] * random_portfolios
    for _ in range(simulations):
        draw_main = _line_mask(rng.sample(main_universe, config.main_pick))
        draw_special = _line_mask(rng.sample(special_universe, config.special_pick)) if config.special_pick else 0
        if _portfolio_hit(draw_main, draw_special, target_masks, main_threshold=threshold, require_special=require_special):
            target_hits += 1
        for i, portfolio in enumerate(random_masks):
            if _portfolio_hit(draw_main, draw_special, portfolio, main_threshold=threshold, require_special=require_special):
                random_hits[i] += 1

    target_p = target_hits / simulations
    random_probs = sorted(h / simulations for h in random_hits)
    mid = len(random_probs) // 2
    if len(random_probs) % 2:
        random_median = random_probs[mid]
    else:
        random_median = (random_probs[mid - 1] + random_probs[mid]) / 2
    beaten = sum(p <= target_p for p in random_probs)
    percentile = 100.0 * beaten / len(random_probs)

    return {
        "draws": simulations,
        "random_portfolios": random_portfolios,
        "target_label": label,
        "target_probability": target_p,
        "random_median_probability": random_median,
        "random_percentile": percentile,
    }


def format_probability_odds(probability: float | None) -> str:
    if probability is None or probability <= 0.0:
        return "—"
    if probability >= 1.0:
        return "1 in 1"
    return f"1 in {1.0 / probability:,.0f}"
