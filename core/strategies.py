from __future__ import annotations

import itertools
import math
import random
from collections import Counter
from math import comb
from typing import Iterable

from core.models import Draw, GameConfig, NumberStat, Ticket
from core.stats import hot_cold_pool, ranked_pool, score_lookup
from core.intelligence import OBJECTIVES, balanced_overlap_penalty, randomness_gate, unique_tuple_coverage

STRATEGIES = [
    "Maximum Intelligence",
    "Smart Ensemble",
    "Smart Pick",
    "Diversified Smart Portfolio",
    "Condensed Portfolio",
    "Abbreviated Wheel",
    "Historical Ranked",
    "Hot + Cold Blend",
    "Key Number Wheel",
    "Balanced Random",
    "Full Wheel",
]


def _pairs(line: Iterable[int]):
    return set(itertools.combinations(sorted(line), 2))


def _triples(line: Iterable[int]):
    return set(itertools.combinations(sorted(line), 3))


def crowd_risk_penalty(line: tuple[int, ...], config: GameConfig) -> float:
    """Penalty for patterns humans commonly choose.

    This does NOT change draw probability. It is only intended to reduce the risk of
    sharing a prize if the line happens to win.
    """
    line = tuple(sorted(line))
    penalty = 0.0

    birthday_count = sum(n <= 31 for n in line)
    if birthday_count == len(line):
        penalty += 2.0
    elif birthday_count >= max(4, len(line) - 1):
        penalty += 0.8

    month_count = sum(n <= 12 for n in line)
    if month_count > 2:
        penalty += 0.7 * (month_count - 2)

    multiples_5 = sum(n % 5 == 0 for n in line)
    if multiples_5 >= 3:
        penalty += 0.7

    # Penalize runs of 3+, but do not ban ordinary consecutive pairs.
    run = 1
    longest = 1
    for a, b in zip(line, line[1:]):
        if b == a + 1:
            run += 1
            longest = max(longest, run)
        else:
            run = 1
    if longest >= 3:
        penalty += 1.2

    # Very regular arithmetic spacing is a common human pattern.
    if len(line) >= 4:
        gaps = [b - a for a, b in zip(line, line[1:])]
        if len(set(gaps)) == 1:
            penalty += 1.3

    return penalty


def balance_score(line: tuple[int, ...], config: GameConfig) -> float:
    """Soft diversity score; not a probability claim."""
    k = len(line)
    odd = sum(n % 2 for n in line)
    parity = 1.0 - min(1.0, abs(odd - k / 2) / max(1.0, k / 2))

    midpoint = (config.main_min + config.main_max) / 2
    low = sum(n <= midpoint for n in line)
    range_balance = 1.0 - min(1.0, abs(low - k / 2) / max(1.0, k / 2))

    spread = (max(line) - min(line)) / max(1, config.main_max - config.main_min)
    return (0.4 * parity) + (0.3 * range_balance) + (0.3 * spread)


def historical_line_score(
    line: tuple[int, ...],
    config: GameConfig,
    stats: list[NumberStat],
    draws: list[Draw],
) -> float:
    lookup = score_lookup(stats)
    history_component = sum(lookup.get(n, 0.0) for n in line) / len(line)
    score = (8.0 * history_component) + (2.0 * balance_score(line, config))
    score -= crowd_risk_penalty(line, config)

    if len(config.main_columns) == config.main_pick:
        previous = {tuple(d.main) for d in draws}
        if tuple(sorted(line)) in previous:
            score -= 3.0
    return score


def _all_main_candidates(pool: list[int], pick: int) -> list[tuple[int, ...]]:
    return [tuple(c) for c in itertools.combinations(sorted(pool), pick)]


def _select_greedy(
    candidates: list[tuple[int, ...]],
    count: int,
    score_fn,
    triple_weight: float,
    pair_weight: float,
    overlap_weight: float,
    number_weight: float = 0.0,
) -> list[tuple[int, ...]]:
    count = min(count, len(candidates))
    selected: list[tuple[int, ...]] = []
    uncovered_pairs_seen: set[tuple[int, int]] = set()
    uncovered_triples_seen: set[tuple[int, int, int]] = set()
    usage = Counter()
    seen_numbers: set[int] = set()
    remaining = list(candidates)

    while remaining and len(selected) < count:
        best = None
        best_value = float("-inf")
        for line in remaining:
            new_numbers = len(set(line) - seen_numbers)
            new_pairs = len(_pairs(line) - uncovered_pairs_seen)
            new_triples = len(_triples(line) - uncovered_triples_seen) if len(line) >= 3 else 0
            max_overlap = max((len(set(line) & set(s)) for s in selected), default=0)
            usage_penalty = sum(usage[n] for n in line) / max(1, len(line))
            value = (
                score_fn(line)
                + number_weight * new_numbers
                + pair_weight * new_pairs
                + triple_weight * new_triples
                - overlap_weight * (max_overlap ** 2)
                - 0.08 * usage_penalty
            )
            if value > best_value or (value == best_value and (best is None or line < best)):
                best = line
                best_value = value

        assert best is not None
        selected.append(best)
        seen_numbers |= set(best)
        uncovered_pairs_seen |= _pairs(best)
        uncovered_triples_seen |= _triples(best)
        usage.update(best)
        remaining.remove(best)

    return selected


def _special_combos(config: GameConfig, special_stats: list[NumberStat], pool_size: int):
    if not config.special_pick:
        return [()]
    pool_size = max(config.special_pick, min(pool_size, config.special_range_size))
    pool = ranked_pool(special_stats, pool_size)
    return list(itertools.combinations(pool, config.special_pick))


def _assign_specials(
    main_lines: list[tuple[int, ...]],
    config: GameConfig,
    special_stats: list[NumberStat],
    special_pool_size: int,
) -> list[Ticket]:
    combos = _special_combos(config, special_stats, special_pool_size)
    if not combos:
        combos = [()]
    tickets = []
    # Rotate combinations so multi-line portfolios cover more special-ball possibilities.
    for i, main in enumerate(main_lines):
        tickets.append(Ticket(main=tuple(sorted(main)), special=tuple(sorted(combos[i % len(combos)]))))
    return tickets



def _weighted_sample_without_replacement(
    universe: list[int], weights: dict[int, float], k: int, rng: random.Random
) -> tuple[int, ...]:
    # Efraimidis-Spirakis weighted sampling without replacement.
    keyed = []
    for n in universe:
        w = max(0.001, weights.get(n, 1.0))
        u = max(rng.random(), 1e-12)
        keyed.append((u ** (1.0 / w), n))
    keyed.sort(reverse=True)
    return tuple(sorted(n for _, n in keyed[:k]))


def _diversified_candidates(
    config: GameConfig,
    stats: list[NumberStat],
    lines: int,
    rng: random.Random,
) -> list[tuple[int, ...]]:
    universe = list(range(config.main_min, config.main_max + 1))
    lookup = score_lookup(stats)
    # Historical data is intentionally a soft tilt only: weights stay roughly 0.85-1.15.
    weights = {n: 0.85 + 0.30 * lookup.get(n, 0.5) for n in universe}
    target = min(10000, max(1200, lines * 200))
    candidates = set()
    attempts = 0
    while len(candidates) < target and attempts < target * 5:
        attempts += 1
        candidates.add(_weighted_sample_without_replacement(universe, weights, config.main_pick, rng))
    return sorted(candidates)


def _assign_specials_diversified(
    main_lines: list[tuple[int, ...]],
    config: GameConfig,
    special_stats: list[NumberStat],
    rng: random.Random,
) -> list[Ticket]:
    if not config.special_pick:
        return [Ticket(main=line) for line in main_lines]
    universe = list(range(config.special_min, config.special_max + 1))
    combos = list(itertools.combinations(universe, config.special_pick))
    lookup = score_lookup(special_stats)
    selected = []
    seen_numbers: set[int] = set()
    used = set()
    for _ in range(len(main_lines)):
        best = None
        best_value = float("-inf")
        for combo in combos:
            if combo in used and len(used) < len(combos):
                continue
            new_numbers = len(set(combo) - seen_numbers)
            history = sum(lookup.get(n, 0.5) for n in combo) / len(combo)
            # Coverage dominates; history only breaks close choices.
            value = (3.0 * new_numbers) + (0.35 * history) + rng.random() * 1e-6
            if value > best_value:
                best, best_value = combo, value
        if best is None:
            best = rng.choice(combos)
        selected.append(best)
        used.add(best)
        seen_numbers |= set(best)
    return [Ticket(main=m, special=tuple(s)) for m, s in zip(main_lines, selected)]




def _pair_history_signal(draws: list[Draw]) -> dict[tuple[int, int], float]:
    """Normalised descriptive pair co-occurrence signal from the stored history.

    This is deliberately a small ranking input only; it is not treated as evidence that
    a previously common pair is more likely in an independent future draw.
    """
    counts = Counter()
    for draw in draws:
        counts.update(itertools.combinations(sorted(draw.main), 2))
    if not counts:
        return {}
    peak = max(counts.values())
    return {pair: count / peak for pair, count in counts.items()}


def _rank_component(stats: list[NumberStat], attr: str, *, reverse: bool = False) -> dict[int, float]:
    """Return 0..1 percentile-style ranks for one descriptive statistic.

    Equal values intentionally share the same average rank so the ensemble does not
    create false precision from arbitrary number ordering.
    """
    if not stats:
        return {}
    values = {st.number: float(getattr(st, attr)) for st in stats}
    ordered = sorted(set(values.values()))
    if len(ordered) == 1:
        return {n: 0.5 for n in values}
    rank_for_value = {v: i / (len(ordered) - 1) for i, v in enumerate(ordered)}
    if reverse:
        rank_for_value = {v: 1.0 - r for v, r in rank_for_value.items()}
    return {n: rank_for_value[v] for n, v in values.items()}


def _history_reliability(draws: list[Draw]) -> float:
    """Shrink historical influence when the available sample is small.

    This is a guard against overreacting to a handful of draws, not a claim that a
    larger historical sample makes future random draws predictable.
    """
    if not draws:
        return 0.0
    return min(1.0, max(0.15, len(draws) / 250.0))


def smart_ensemble_score(
    line: tuple[int, ...],
    config: GameConfig,
    stats: list[NumberStat],
    draws: list[Draw],
    pair_signal: dict[tuple[int, int], float] | None = None,
    previous_lines: set[tuple[int, ...]] | None = None,
) -> float:
    """Weighted ensemble score used by the personal Smart Pick front end.

    The score combines several *descriptive* views of the stored history rather than
    treating one hot-number ranking as truth. Historical influence is automatically
    reduced when the sample is shallow. Balance and anti-crowd-pattern terms are kept
    independent from the historical signals. None of these terms changes the physical
    probability of a valid lottery combination being drawn.
    """
    if not line:
        return float('-inf')

    combined = score_lookup(stats)
    freq_rank = _rank_component(stats, 'frequency_index')
    recent_rank = _rank_component(stats, 'recent_index')
    # A gap is deliberately a tiny component: an overdue number is not made due.
    gap_rank = _rank_component(stats, 'gap_draws')
    reliability = _history_reliability(draws)

    def avg(mapping: dict[int, float], default: float = 0.5) -> float:
        return sum(mapping.get(n, default) for n in line) / len(line)

    combined_component = avg(combined)
    long_component = avg(freq_rank)
    recent_component = avg(recent_rank)
    gap_component = avg(gap_rank)

    pairs = _pairs(line)
    pmap = pair_signal or {}
    pair_component = sum(pmap.get(pair, 0.0) for pair in pairs) / max(1, len(pairs))

    # When history is shallow, structural balance contributes more than historical noise.
    historical = reliability * (
        2.25 * combined_component
        + 1.35 * long_component
        + 1.05 * recent_component
        + 0.18 * gap_component
        + 0.32 * pair_component
    )
    structural = (1.45 + (0.45 * (1.0 - reliability))) * balance_score(line, config)
    score = historical + structural
    score -= 0.78 * crowd_risk_penalty(line, config)

    previous = previous_lines if previous_lines is not None else {tuple(d.main) for d in draws}
    if tuple(sorted(line)) in previous:
        score -= 1.35
    return score


def smart_pick_score(
    line: tuple[int, ...],
    config: GameConfig,
    stats: list[NumberStat],
    draws: list[Draw],
    pair_signal: dict[tuple[int, int], float] | None = None,
    previous_lines: set[tuple[int, ...]] | None = None,
) -> float:
    """Backward-compatible alias for the V5.0 Smart Pick scorer."""
    return smart_ensemble_score(line, config, stats, draws, pair_signal, previous_lines)


def _smart_ensemble_candidates(
    config: GameConfig,
    stats: list[NumberStat],
    lines: int,
    rng: random.Random,
) -> list[tuple[int, ...]]:
    """Create a broad candidate set from several independent selection views."""
    candidates: set[tuple[int, ...]] = set(_diversified_candidates(config, stats, max(lines, 10), rng))

    # Long/combined-history concentrated candidates.
    ranked = ranked_pool(stats, min(config.main_range_size, max(config.main_pick + 8, 14)))
    for i, combo in enumerate(itertools.combinations(ranked, config.main_pick)):
        candidates.add(tuple(combo))
        if i >= 1599:
            break

    # Recent-activity view is intentionally separate from the combined historical score.
    recent_order = sorted(stats, key=lambda st: (-st.recent_index, -st.recent_count, st.number))
    recent_pool = sorted(st.number for st in recent_order[:min(config.main_range_size, max(config.main_pick + 8, 14))])
    for i, combo in enumerate(itertools.combinations(recent_pool, config.main_pick)):
        candidates.add(tuple(combo))
        if i >= 1199:
            break

    # Hot/cold blend remains one vote in the ensemble rather than the whole strategy.
    hotcold = hot_cold_pool(stats, min(config.main_range_size, max(config.main_pick + 8, 14)))
    for i, combo in enumerate(itertools.combinations(hotcold, config.main_pick)):
        candidates.add(tuple(combo))
        if i >= 999:
            break

    # Add uniform candidates so the engine can escape historical concentration entirely.
    universe = list(range(config.main_min, config.main_max + 1))
    target_uniform = min(1800, max(700, lines * 120))
    attempts = 0
    while target_uniform > 0 and attempts < target_uniform * 8:
        attempts += 1
        candidate = tuple(sorted(rng.sample(universe, config.main_pick)))
        if crowd_risk_penalty(candidate, config) <= 2.5 or attempts > target_uniform * 5:
            candidates.add(candidate)
        if attempts >= target_uniform:
            # We only need approximately this many extra views; candidate de-duplication is fine.
            break

    return sorted(candidates)


def _assign_specials_ensemble(
    main_lines: list[tuple[int, ...]],
    config: GameConfig,
    special_stats: list[NumberStat],
    draws: list[Draw],
    rng: random.Random,
) -> list[Ticket]:
    """Rank the recommended special selection first, then diversify later lines."""
    if not config.special_pick:
        return [Ticket(main=line) for line in main_lines]

    universe = list(range(config.special_min, config.special_max + 1))
    combos = list(itertools.combinations(universe, config.special_pick))
    lookup = score_lookup(special_stats)
    freq_rank = _rank_component(special_stats, 'frequency_index')
    recent_rank = _rank_component(special_stats, 'recent_index')
    reliability = _history_reliability(draws)

    def combo_score(combo: tuple[int, ...]) -> float:
        hist = sum(lookup.get(n, 0.5) for n in combo) / len(combo)
        freq = sum(freq_rank.get(n, 0.5) for n in combo) / len(combo)
        recent = sum(recent_rank.get(n, 0.5) for n in combo) / len(combo)
        return reliability * ((1.8 * hist) + (0.9 * freq) + (0.7 * recent))

    ordered = sorted(combos, key=lambda c: (-combo_score(c), c))
    selected: list[tuple[int, ...]] = []
    seen_numbers: set[int] = set()
    used: set[tuple[int, ...]] = set()

    for i in range(len(main_lines)):
        if i == 0:
            best = ordered[0]
        else:
            best = None
            best_value = float('-inf')
            for combo in ordered:
                if combo in used and len(used) < len(combos):
                    continue
                new_numbers = len(set(combo) - seen_numbers)
                value = (3.0 * new_numbers) + (0.45 * combo_score(combo)) + rng.random() * 1e-9
                if value > best_value:
                    best, best_value = combo, value
            if best is None:
                best = ordered[i % len(ordered)]
        selected.append(best)
        used.add(best)
        seen_numbers |= set(best)

    return [Ticket(main=m, special=s) for m, s in zip(main_lines, selected)]



def _portfolio_objective_value(
    lines: list[tuple[int, ...]],
    config: GameConfig,
    objective: str,
    line_quality: dict[tuple[int, ...], float],
) -> float:
    """Structural whole-portfolio objective used by Maximum Intelligence.

    The value is a search objective, not a probability claim.  Exact jackpot odds are
    unchanged by how distinct valid lines are arranged; this objective concentrates on
    coverage, convex overlap control and crowd-sharing risk.
    """
    if not lines:
        return float('-inf')
    unique_lines = list(dict.fromkeys(tuple(sorted(line)) for line in lines))
    if len(unique_lines) != len(lines):
        return float('-inf')

    pair_cov = unique_tuple_coverage(unique_lines, 2)
    triple_cov = unique_tuple_coverage(unique_lines, 3)
    numbers = len(set().union(*(set(line) for line in unique_lines)))
    overlap_cost = balanced_overlap_penalty(unique_lines)
    crowd = sum(crowd_risk_penalty(line, config) for line in unique_lines)
    quality = sum(line_quality.get(line, 0.0) for line in unique_lines) / len(unique_lines)

    # Different objectives tune the same mathematically grounded portfolio features.
    if objective == "Minimise no-win proxy":
        return (2.8 * pair_cov) + (1.9 * triple_cov) + (5.0 * numbers) + (1.2 * quality) - (5.2 * overlap_cost) - (2.0 * crowd)
    if objective == "Maximise 3+ coverage":
        return (1.2 * pair_cov) + (4.5 * triple_cov) + (3.0 * numbers) + (1.0 * quality) - (4.8 * overlap_cost) - (1.6 * crowd)
    if objective == "Minimise prize sharing":
        return (1.0 * pair_cov) + (1.3 * triple_cov) + (2.5 * numbers) + (0.8 * quality) - (3.8 * overlap_cost) - (8.5 * crowd)
    # Best overall portfolio
    return (2.1 * pair_cov) + (2.8 * triple_cov) + (4.0 * numbers) + (1.2 * quality) - (4.6 * overlap_cost) - (2.6 * crowd)


def _select_maximum_intelligence(
    config: GameConfig,
    draws: list[Draw],
    stats: list[NumberStat],
    candidates: list[tuple[int, ...]],
    lines: int,
    objective: str,
    rng: random.Random,
) -> tuple[list[tuple[int, ...]], int]:
    """Whole-portfolio optimiser with greedy construction + local/evolutionary refinement."""
    gate = randomness_gate(draws, config)
    pair_signal = _pair_history_signal(draws)
    previous_lines = {tuple(d.main) for d in draws}

    # Structural quality dominates. Historical ensemble information is admitted only
    # through the conservative Randomness Gate and therefore cannot dominate the search.
    raw_history = {
        line: smart_ensemble_score(line, config, stats, draws, pair_signal, previous_lines)
        for line in candidates
    }
    if raw_history:
        lo, hi = min(raw_history.values()), max(raw_history.values())
        span = max(1e-12, hi - lo)
    else:
        lo, span = 0.0, 1.0
    line_quality = {}
    for line in candidates:
        h = (raw_history[line] - lo) / span
        structural = (1.65 * balance_score(line, config)) - (0.90 * crowd_risk_penalty(line, config))
        line_quality[line] = structural + (gate.history_weight * 2.0 * h)

    # Reduce the refinement pool to strong but varied candidates; retain the broad pool
    # for the first greedy pass so coverage can escape a concentrated historical subset.
    seed_lines = _select_greedy(
        candidates,
        lines,
        lambda line: line_quality[line],
        triple_weight=0.35 if objective != "Maximise 3+ coverage" else 0.85,
        pair_weight=1.05,
        overlap_weight=1.15,
        number_weight=5.6,
    )

    ranked_candidates = sorted(candidates, key=lambda x: (-line_quality[x], x))
    refinement_pool = ranked_candidates[:min(len(ranked_candidates), max(600, lines * 90))]
    # Add random candidates from the tail to preserve escape routes from local optima.
    if len(ranked_candidates) > len(refinement_pool):
        tail = ranked_candidates[len(refinement_pool):]
        refinement_pool += rng.sample(tail, min(len(tail), max(120, lines * 20)))

    current = list(seed_lines)
    current_score = _portfolio_objective_value(current, config, objective, line_quality)
    best = list(current)
    best_score = current_score
    moves = 0

    # Deterministic-ish simulated annealing / hill-climb hybrid.  The temperature is
    # intentionally small because the greedy seed is already strong.
    iterations = min(4500, max(900, lines * 180))
    temperature = 3.0
    for step in range(iterations):
        moves += 1
        idx = rng.randrange(len(current))
        replacement = rng.choice(refinement_pool)
        if replacement in current:
            continue
        proposal = list(current)
        proposal[idx] = replacement
        score = _portfolio_objective_value(proposal, config, objective, line_quality)
        delta = score - current_score
        t = max(0.03, temperature * (1.0 - (step / iterations)))
        if delta >= 0 or rng.random() < math.exp(max(-60.0, delta / t)):
            current, current_score = proposal, score
            if score > best_score:
                best, best_score = list(proposal), score

    # Keep the strongest individual candidate first for a stable Recommended card,
    # then preserve the optimized portfolio membership for the alternatives.
    best.sort(key=lambda line: (-line_quality[line], line))
    return best, moves


def generate_tickets(
    config: GameConfig,
    draws: list[Draw],
    main_stats: list[NumberStat],
    special_stats: list[NumberStat],
    strategy: str,
    lines: int,
    pool_size: int,
    special_pool_size: int,
    key_numbers: list[int] | None = None,
    seed: int | None = None,
    excluded_main_lines: set[tuple[int, ...]] | None = None,
    objective: str = "Best overall portfolio",
    diagnostics_out: dict | None = None,
) -> tuple[list[Ticket], list[int]]:
    if strategy not in STRATEGIES:
        raise ValueError(f"Unknown strategy: {strategy}")
    lines = max(1, lines)
    pool_size = max(config.main_pick, min(pool_size, config.main_range_size))
    special_pool_size = max(config.special_pick, special_pool_size) if config.special_pick else 0
    key_numbers = sorted(set(key_numbers or []))
    rng = random.Random(seed)
    excluded_main_lines = {tuple(sorted(line)) for line in (excluded_main_lines or set())}

    if objective not in OBJECTIVES:
        objective = "Best overall portfolio"

    for n in key_numbers:
        if not (config.main_min <= n <= config.main_max):
            raise ValueError(f"Key number {n} is outside {config.main_min}-{config.main_max}")
    if len(key_numbers) >= config.main_pick:
        if len(key_numbers) > config.main_pick:
            raise ValueError(f"Use at most {config.main_pick} key numbers")

    if strategy == "Maximum Intelligence":
        candidates = [
            line for line in _smart_ensemble_candidates(config, main_stats, max(lines, 20), rng)
            if tuple(sorted(line)) not in excluded_main_lines
        ]
        if not candidates:
            raise ValueError("No Maximum Intelligence candidates remain after exclusions")
        search_moves = 0
        if lines == 1:
            # One line cannot benefit from portfolio coverage. Use the same gated
            # line-quality philosophy while retaining structural/crowd controls.
            gate = randomness_gate(draws, config)
            pair_signal = _pair_history_signal(draws)
            previous_lines = {tuple(d.main) for d in draws}
            def one_score(line):
                hist = smart_ensemble_score(line, config, main_stats, draws, pair_signal, previous_lines)
                return (2.0 * balance_score(line, config)) - (1.4 * crowd_risk_penalty(line, config)) + (gate.history_weight * hist)
            main_lines = [max(candidates, key=lambda line: (one_score(line), tuple(-n for n in line)))]
        else:
            main_lines, search_moves = _select_maximum_intelligence(
                config, draws, main_stats, candidates, lines, objective, rng
            )
        if diagnostics_out is not None:
            diagnostics_out["candidates_evaluated"] = len(candidates)
            diagnostics_out["search_moves"] = search_moves
        base_pool = sorted(set(n for line in main_lines for n in line))
        # Special balls are diversified across the portfolio; their historical
        # component is already shrinkage-aware in the ensemble assignment.
        return _assign_specials_ensemble(main_lines, config, special_stats, draws, rng), base_pool

    if strategy in {"Smart Ensemble", "Smart Pick"}:
        # V5.1: several candidate-generation views vote through one shrinkage-aware
        # ensemble scorer. The first selected line is the absolute top-ranked candidate;
        # subsequent lines trade a little score for broader portfolio coverage.
        candidates = [
            line for line in _smart_ensemble_candidates(config, main_stats, lines, rng)
            if tuple(sorted(line)) not in excluded_main_lines
        ]
        if not candidates:
            raise ValueError("No new Smart Ensemble candidates remain after exclusions")
        pair_signal = _pair_history_signal(draws)
        previous_lines = {tuple(d.main) for d in draws}

        def ensemble_score(line):
            return smart_ensemble_score(line, config, main_stats, draws, pair_signal, previous_lines)

        if lines == 1:
            main_lines = [max(candidates, key=lambda line: (ensemble_score(line), tuple(-n for n in line)))]
        else:
            main_lines = _select_greedy(
                candidates,
                lines,
                ensemble_score,
                triple_weight=0.16,
                pair_weight=0.82,
                overlap_weight=1.62,
                number_weight=4.35,
            )
        base_pool = sorted(set(n for line in main_lines for n in line))
        return _assign_specials_ensemble(main_lines, config, special_stats, draws, rng), base_pool

    if strategy == "Diversified Smart Portfolio":
        candidates = _diversified_candidates(config, main_stats, lines, rng)
        lookup = score_lookup(main_stats)
        def diversified_score(line):
            history = sum(lookup.get(n, 0.5) for n in line) / len(line)
            return (1.5 * history) + (1.2 * balance_score(line, config)) - crowd_risk_penalty(line, config)
        main_lines = _select_greedy(
            candidates, lines, diversified_score,
            triple_weight=0.25, pair_weight=1.2, overlap_weight=1.35, number_weight=5.0,
        )
        base_pool = sorted(set(n for line in main_lines for n in line))
        return _assign_specials_diversified(main_lines, config, special_stats, rng), base_pool

    if strategy == "Balanced Random":
        universe = list(range(config.main_min, config.main_max + 1))
        seen = set()
        main_lines = []
        attempts = 0
        while len(main_lines) < lines and attempts < lines * 500:
            attempts += 1
            candidate = tuple(sorted(rng.sample(universe, config.main_pick)))
            if candidate in seen:
                continue
            # Soft crowd-avoidance gate. This affects split-risk only, not draw odds.
            if crowd_risk_penalty(candidate, config) > 2.2 and attempts < lines * 300:
                continue
            seen.add(candidate)
            main_lines.append(candidate)
        base_pool = sorted(set(n for line in main_lines for n in line))
        return _assign_specials_diversified(main_lines, config, special_stats, rng), base_pool

    if strategy == "Hot + Cold Blend":
        base_pool = hot_cold_pool(main_stats, pool_size)
    else:
        base_pool = ranked_pool(main_stats, pool_size)

    if strategy == "Key Number Wheel":
        if not key_numbers:
            raise ValueError("Enter at least one key number for Key Number Wheel")
        # Ensure keys are in the pool, replacing lowest-ranked non-keys if necessary.
        score_order = [s.number for s in main_stats]
        pool = list(base_pool)
        for key in key_numbers:
            if key not in pool:
                removable = [n for n in reversed(score_order) if n in pool and n not in key_numbers]
                if removable:
                    pool.remove(removable[0])
                pool.append(key)
        base_pool = sorted(set(pool))
        need = config.main_pick - len(key_numbers)
        filler = [n for n in base_pool if n not in key_numbers]
        candidates = [tuple(sorted((*key_numbers, *c))) for c in itertools.combinations(filler, need)]
    else:
        candidates = _all_main_candidates(base_pool, config.main_pick)

    if not candidates:
        raise ValueError("No candidate lines can be generated with the selected pool")

    score_fn = lambda line: historical_line_score(line, config, main_stats, draws)

    if strategy == "Full Wheel":
        main_lines = sorted(candidates)
        special_combos = _special_combos(config, special_stats, special_pool_size)
        total_tickets = len(main_lines) * max(1, len(special_combos))
        if total_tickets > 5000:
            raise ValueError(
                f"Full wheel would create {total_tickets:,} complete tickets "
                f"({len(main_lines):,} main combinations × {max(1, len(special_combos)):,} special combinations). "
                "Reduce the pool size; this app caps full wheels at 5,000 complete tickets."
            )
    elif strategy == "Condensed Portfolio":
        # Condensation balances historical ranking with pair/triple coverage inside the pool.
        main_lines = _select_greedy(
            candidates, lines, score_fn,
            triple_weight=1.7, pair_weight=2.4, overlap_weight=0.75,
        )
    elif strategy == "Abbreviated Wheel":
        # An abbreviated wheel is coverage-first. Historical score is deliberately muted so
        # it does not collapse into the same portfolio as Condensed Portfolio.
        coverage_score_fn = lambda line: 0.08 * score_fn(line)
        main_lines = _select_greedy(
            candidates, lines, coverage_score_fn,
            triple_weight=1.15, pair_weight=4.2, overlap_weight=0.35,
            number_weight=0.8,
        )
    elif strategy == "Key Number Wheel":
        main_lines = _select_greedy(
            candidates, lines, score_fn,
            triple_weight=0.35, pair_weight=3.0, overlap_weight=0.55,
        )
    elif strategy == "Historical Ranked":
        # Historical Ranked is intentionally concentration-first: choose the highest-scoring
        # historical candidates rather than reusing the same greedy coverage objective.
        main_lines = [line for _score, line in sorted(
            ((score_fn(line), line) for line in candidates),
            key=lambda item: (-item[0], item[1]),
        )[:min(lines, len(candidates))]]
    else:
        # Hot + Cold Blend keeps its mixed pool but still avoids near-duplicate lines.
        main_lines = _select_greedy(
            candidates, lines, score_fn,
            triple_weight=0.0, pair_weight=0.4, overlap_weight=0.9,
        )

    if strategy == "Full Wheel":
        special_combos = _special_combos(config, special_stats, special_pool_size)
        tickets = [
            Ticket(main=tuple(sorted(main)), special=tuple(sorted(special)))
            for main in main_lines
            for special in special_combos
        ]
        return tickets, base_pool

    return _assign_specials(main_lines, config, special_stats, special_pool_size), base_pool


def coverage_metrics(tickets: list[Ticket], base_pool: list[int], config: GameConfig | None = None) -> dict[str, float]:
    if not tickets or not base_pool:
        return {
            "pair_coverage": 0.0,
            "triple_coverage": 0.0,
            "avg_overlap": 0.0,
            "special_coverage": 0.0,
            "avg_special_overlap": 0.0,
        }
    lines = sorted(set(t.main for t in tickets))
    pair_all = set(itertools.combinations(sorted(base_pool), 2))
    triple_all = set(itertools.combinations(sorted(base_pool), 3))
    pair_used = set().union(*(_pairs(line) for line in lines)) if lines else set()
    triple_used = set().union(*(_triples(line) for line in lines)) if lines and len(lines[0]) >= 3 else set()

    overlaps = [len(set(a) & set(b)) for a, b in itertools.combinations(lines, 2)]
    special_sets = [set(t.special) for t in tickets]
    special_overlaps = [len(a & b) for a, b in itertools.combinations(special_sets, 2)] if len(special_sets) > 1 else []
    covered_special = set().union(*special_sets) if special_sets else set()
    special_range = config.special_range_size if config is not None and config.special_pick else 0

    return {
        "pair_coverage": len(pair_used) / len(pair_all) if pair_all else 1.0,
        "triple_coverage": len(triple_used) / len(triple_all) if triple_all else 1.0,
        "avg_overlap": sum(overlaps) / len(overlaps) if overlaps else 0.0,
        "special_coverage": (len(covered_special) / special_range) if special_range else 0.0,
        "avg_special_overlap": (sum(special_overlaps) / len(special_overlaps)) if special_overlaps else 0.0,
    }


def wheel_guarantees(tickets: list[Ticket], base_pool: list[int], config: GameConfig) -> dict[int, int]:
    """Exact main-number coverage guarantees for the generated abbreviated wheel.

    For each k, assume exactly k winning main numbers are inside the base pool. The result
    is the minimum, across every possible k-subset of that pool, of the best match found
    on any generated ticket.
    """
    if not tickets or not base_pool:
        return {}
    lines = [set(main) for main in sorted(set(t.main for t in tickets))]
    guarantees = {}
    max_k = min(len(config.main_columns), len(base_pool))
    min_k = max(2, min(config.main_pick - 2, max_k))
    for k in range(min_k, max_k + 1):
        subsets = itertools.combinations(base_pool, k)
        guarantee = config.main_pick
        seen_any = False
        for winners in subsets:
            seen_any = True
            w = set(winners)
            best = max(len(line & w) for line in lines)
            guarantee = min(guarantee, best)
            if guarantee == 0:
                break
        if seen_any:
            guarantees[k] = guarantee
    return guarantees
