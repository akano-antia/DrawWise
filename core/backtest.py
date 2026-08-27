from __future__ import annotations

import itertools
import math
import random
from dataclasses import dataclass
from statistics import mean, stdev
from typing import Callable, Iterable

from core.models import Draw, GameConfig, Ticket
from core.stats import number_stats
from core.strategies import generate_tickets


class BacktestCancelled(Exception):
    """Raised when a caller asks a long-running walk-forward test to stop."""


def _ci95(values: list[float]) -> tuple[float, float]:
    """Simple 95% confidence interval for the mean of target-level benchmark values."""
    if not values:
        return (0.0, 0.0)
    m = mean(values)
    if len(values) < 2:
        return (m, m)
    margin = 1.96 * stdev(values) / math.sqrt(len(values))
    return (m - margin, m + margin)


def _pattern_label(config: GameConfig, main_hits: int, special_hits: int) -> str:
    if config.special_pick:
        return f"{main_hits}+{special_hits}"
    return str(main_hits)


def _ticket_hits(ticket: Ticket, draw: Draw) -> tuple[int, int]:
    return (
        len(set(ticket.main) & set(draw.main)),
        len(set(ticket.special) & set(draw.special)),
    )


def _portfolio_metrics(config: GameConfig, tickets: list[Ticket], draw: Draw):
    """Return main, complete-component and pattern diagnostics for one portfolio.

    ``total`` is the number of matched ticket components (main + separately selected
    special balls). It is a descriptive comparison score, not a prize-tier value.
    """
    matches = [_ticket_hits(ticket, draw) for ticket in tickets]
    best_main = max((m for m, _s in matches), default=0)
    # Total components first, then main hits, then special hits as deterministic tie-breaks.
    best_complete = max(matches, key=lambda pair: (pair[0] + pair[1], pair[0], pair[1]), default=(0, 0))
    best_total = best_complete[0] + best_complete[1]
    patterns_present = {_pattern_label(config, m, s) for m, s in matches}
    special_coverage = 0.0
    if config.special_pick and config.special_range_size:
        covered = set().union(*(set(ticket.special) for ticket in tickets)) if tickets else set()
        special_coverage = len(covered) / config.special_range_size
    return best_main, best_total, best_complete, patterns_present, special_coverage


def _random_portfolio(config: GameConfig, lines: int, rng: random.Random) -> list[Ticket]:
    main_universe = list(range(config.main_min, config.main_max + 1))
    special_universe = (
        list(range(config.special_min, config.special_max + 1))
        if config.special_pick and config.special_min is not None and config.special_max is not None
        else []
    )
    tickets: list[Ticket] = []
    seen: set[tuple[tuple[int, ...], tuple[int, ...]]] = set()
    attempts = 0
    while len(tickets) < lines and attempts < max(500, lines * 250):
        attempts += 1
        main = tuple(sorted(rng.sample(main_universe, config.main_pick)))
        special = tuple(sorted(rng.sample(special_universe, config.special_pick))) if config.special_pick else ()
        signature = (main, special)
        if signature in seen:
            continue
        seen.add(signature)
        tickets.append(Ticket(main=main, special=special))
    return tickets


def _random_metrics(config: GameConfig, draw: Draw, lines: int, rng: random.Random) -> tuple[int, int]:
    tickets = _random_portfolio(config, lines, rng)
    best_main, best_total, _pattern, _patterns, _coverage = _portfolio_metrics(config, tickets, draw)
    return best_main, best_total


@dataclass(frozen=True)
class BacktestResult:
    tested_draws: int
    avg_pool_hits: float
    expected_pool_hits: float
    pool_lift: float
    avg_best_line_hits: float
    random_avg_best_line_hits: float
    best_line_lift: float
    max_best_line_hits: int
    threshold_counts: tuple[tuple[int, int], ...]
    beat_random_draws: int
    below_random_draws: int
    notes: str
    # V3.6 complete-ticket diagnostics.
    avg_best_total_hits: float = 0.0
    random_avg_best_total_hits: float = 0.0
    total_hit_lift: float = 0.0
    max_best_special_hits: int = 0
    best_complete_pattern: str = "—"
    pattern_counts: tuple[tuple[str, int], ...] = ()
    avg_special_coverage: float = 0.0
    random_ci_low: float = 0.0
    random_ci_high: float = 0.0
    random_total_ci_low: float = 0.0
    random_total_ci_high: float = 0.0


def _empty_backtest(note: str) -> BacktestResult:
    return BacktestResult(
        tested_draws=0,
        avg_pool_hits=0,
        expected_pool_hits=0,
        pool_lift=0,
        avg_best_line_hits=0,
        random_avg_best_line_hits=0,
        best_line_lift=0,
        max_best_line_hits=0,
        threshold_counts=(),
        beat_random_draws=0,
        below_random_draws=0,
        notes=note,
    )


def run_backtest(
    config: GameConfig,
    draws: list[Draw],
    strategy: str,
    lines: int,
    pool_size: int,
    special_pool_size: int,
    recent_window: int,
    key_numbers: list[int] | None = None,
    min_history: int | None = None,
    random_trials_per_draw: int = 30,
    max_tests: int | None = None,
    progress_callback: Callable[[int, int, object], None] | None = None,
    cancel_event=None,
) -> BacktestResult:
    if len(draws) < 12:
        return _empty_backtest("Not enough history for a meaningful walk-forward test. Add more draw rows.")

    min_history = min_history or max(10, min(30, len(draws) // 2))
    min_history = min(min_history, len(draws) - 1)
    rng = random.Random(20260815)

    pool_hits: list[int] = []
    expected_pool_hits_values: list[float] = []
    best_hits: list[int] = []
    best_total_hits: list[int] = []
    best_complete_pairs: list[tuple[int, int]] = []
    random_best_hits: list[float] = []
    random_total_hits: list[float] = []
    special_coverages: list[float] = []
    pattern_counter: dict[str, int] = {}

    strategy_for_test = "Condensed Portfolio" if strategy == "Full Wheel" else strategy
    target_indices = list(range(min_history, len(draws)))
    if max_tests is not None and max_tests > 0 and len(target_indices) > max_tests:
        target_indices = target_indices[-max_tests:]
    total_tests = len(target_indices)

    for completed_index, idx in enumerate(target_indices, start=1):
        if cancel_event is not None and cancel_event.is_set():
            raise BacktestCancelled()
        history = draws[:idx]
        target = draws[idx]
        main_stats = number_stats(history, config, recent_window, "main")
        special_stats = number_stats(history, config, recent_window, "special")

        try:
            tickets, base_pool = generate_tickets(
                config=config,
                draws=history,
                main_stats=main_stats,
                special_stats=special_stats,
                strategy=strategy_for_test,
                lines=lines,
                pool_size=pool_size,
                special_pool_size=special_pool_size,
                key_numbers=key_numbers,
                seed=idx,
            )
        except ValueError as exc:
            return _empty_backtest(f"Backtest could not run: {exc}")

        winning = set(target.main)
        pool_hits.append(len(set(base_pool) & winning))
        expected_pool_hits_values.append(config.main_pick * len(set(base_pool)) / config.main_range_size)

        best_main, best_total, best_pair, patterns_present, special_coverage = _portfolio_metrics(config, tickets, target)
        best_hits.append(best_main)
        best_total_hits.append(best_total)
        best_complete_pairs.append(best_pair)
        special_coverages.append(special_coverage)
        for pattern in patterns_present:
            pattern_counter[pattern] = pattern_counter.get(pattern, 0) + 1

        trial_main: list[int] = []
        trial_total: list[int] = []
        for _ in range(random_trials_per_draw):
            r_main, r_total = _random_metrics(config, target, len(tickets), rng)
            trial_main.append(r_main)
            trial_total.append(r_total)
        random_best_hits.append(mean(trial_main))
        random_total_hits.append(mean(trial_total))

        if progress_callback is not None:
            progress_callback(completed_index, total_tests, target.draw_date)
        if cancel_event is not None and cancel_event.is_set():
            raise BacktestCancelled()

    tested = len(pool_hits)
    expected_pool = mean(expected_pool_hits_values)
    avg_pool = mean(pool_hits)
    avg_best = mean(best_hits)
    avg_random_best = mean(random_best_hits)
    avg_total = mean(best_total_hits)
    avg_random_total = mean(random_total_hits)
    random_ci = _ci95(random_best_hits)
    random_total_ci = _ci95(random_total_hits)

    if tested < 20:
        note = (
            "Small out-of-sample window. Treat this as a diagnostic only, not evidence that the "
            "strategy predicts future independent draws."
        )
    else:
        note = (
            "Walk-forward test: each target draw is hidden from the strategy until after selection. "
            "Positive lift is descriptive and can disappear out of sample."
        )

    thresholds = tuple(
        (threshold, sum(1 for hits in best_hits if hits >= threshold))
        for threshold in range(2, config.main_pick + 1)
    )
    beat_random = sum(1 for hits, baseline in zip(best_hits, random_best_hits) if hits > baseline)
    below_random = sum(1 for hits, baseline in zip(best_hits, random_best_hits) if hits < baseline)

    best_pair = max(best_complete_pairs, key=lambda pair: (pair[0] + pair[1], pair[0], pair[1]))
    # Sort complete hit-pattern diagnostics by main hits, then special hits.
    def _pattern_sort(item: tuple[str, int]):
        label, _count = item
        if "+" in label:
            a, b = label.split("+", 1)
            return (-int(a), -int(b), label)
        return (-int(label), 0, label)

    pattern_counts = tuple(sorted(pattern_counter.items(), key=_pattern_sort))

    return BacktestResult(
        tested_draws=tested,
        avg_pool_hits=avg_pool,
        expected_pool_hits=expected_pool,
        pool_lift=avg_pool - expected_pool,
        avg_best_line_hits=avg_best,
        random_avg_best_line_hits=avg_random_best,
        best_line_lift=avg_best - avg_random_best,
        max_best_line_hits=max(best_hits),
        threshold_counts=thresholds,
        beat_random_draws=beat_random,
        below_random_draws=below_random,
        notes=note,
        avg_best_total_hits=avg_total,
        random_avg_best_total_hits=avg_random_total,
        total_hit_lift=avg_total - avg_random_total,
        max_best_special_hits=max((s for _m, s in best_complete_pairs), default=0),
        best_complete_pattern=_pattern_label(config, best_pair[0], best_pair[1]),
        pattern_counts=pattern_counts,
        avg_special_coverage=mean(special_coverages) if special_coverages else 0.0,
        random_ci_low=random_ci[0],
        random_ci_high=random_ci[1],
        random_total_ci_low=random_total_ci[0],
        random_total_ci_high=random_total_ci[1],
    )


STRATEGY_LAB_STRATEGIES = (
    "Diversified Smart Portfolio",
    "Condensed Portfolio",
    "Abbreviated Wheel",
    "Historical Ranked",
    "Hot + Cold Blend",
    "Balanced Random",
)


@dataclass(frozen=True)
class StrategyComparisonRow:
    strategy: str
    tested_draws: int
    avg_best_line_hits: float
    random_avg_best_line_hits: float
    best_line_lift: float
    max_best_line_hits: int
    threshold_counts: tuple[tuple[int, int], ...]
    beat_random_draws: int
    below_random_draws: int
    tied_random_draws: int
    error: str | None = None
    avg_best_total_hits: float = 0.0
    random_avg_best_total_hits: float = 0.0
    total_hit_lift: float = 0.0
    best_complete_pattern: str = "—"
    avg_special_coverage: float = 0.0


@dataclass(frozen=True)
class StrategyComparisonResult:
    tested_draws: int
    random_trials_per_draw: int
    random_avg_best_line_hits: float
    rows: tuple[StrategyComparisonRow, ...]
    notes: str
    random_avg_best_total_hits: float = 0.0
    random_ci_low: float = 0.0
    random_ci_high: float = 0.0
    random_total_ci_low: float = 0.0
    random_total_ci_high: float = 0.0
    similarity_warnings: tuple[str, ...] = ()

    @property
    def winner(self) -> StrategyComparisonRow | None:
        valid = [row for row in self.rows if not row.error]
        if not valid:
            return None
        return max(
            valid,
            key=lambda row: (
                row.avg_best_line_hits,
                row.avg_best_total_hits,
                dict(row.threshold_counts).get(4, 0),
                dict(row.threshold_counts).get(3, 0),
                row.beat_random_draws,
                -row.below_random_draws,
            ),
        )


def _portfolio_signature(tickets: list[Ticket]) -> frozenset[tuple[tuple[int, ...], tuple[int, ...]]]:
    return frozenset((ticket.main, ticket.special) for ticket in tickets)


def run_strategy_comparison(
    config: GameConfig,
    draws: list[Draw],
    strategies: Iterable[str],
    lines: int,
    pool_size: int,
    special_pool_size: int,
    recent_window: int,
    min_history: int | None = None,
    random_trials_per_draw: int = 50,
    max_tests: int | None = None,
    progress_callback: Callable[[int, int, str, str, object], None] | None = None,
    cancel_event=None,
) -> StrategyComparisonResult:
    """Compare strategies on one shared walk-forward main + complete-ticket baseline."""
    strategies = tuple(dict.fromkeys(strategies))
    if not strategies:
        raise ValueError("Choose at least one strategy to compare")
    if random_trials_per_draw < 1:
        raise ValueError("Random benchmark trials must be at least 1")

    if len(draws) < 12:
        return StrategyComparisonResult(
            tested_draws=0,
            random_trials_per_draw=random_trials_per_draw,
            random_avg_best_line_hits=0.0,
            rows=(),
            notes="Not enough history for Strategy Lab. Add more draw rows first.",
        )

    min_history = min_history or max(10, min(30, len(draws) // 2))
    min_history = min(min_history, len(draws) - 1)
    target_indices = list(range(min_history, len(draws)))
    if max_tests is not None and max_tests > 0 and len(target_indices) > max_tests:
        target_indices = target_indices[-max_tests:]
    tested = len(target_indices)
    total_work = tested * (1 + len(strategies))
    completed_work = 0

    random_main_by_index: dict[int, float] = {}
    random_total_by_index: dict[int, float] = {}
    for idx in target_indices:
        if cancel_event is not None and cancel_event.is_set():
            raise BacktestCancelled()
        target = draws[idx]
        rng = random.Random(20260816 + idx * 1009 + lines * 37)
        main_trials: list[int] = []
        total_trials: list[int] = []
        for _ in range(random_trials_per_draw):
            main_hits, total_hits = _random_metrics(config, target, lines, rng)
            main_trials.append(main_hits)
            total_trials.append(total_hits)
        random_main_by_index[idx] = mean(main_trials)
        random_total_by_index[idx] = mean(total_trials)
        completed_work += 1
        if progress_callback is not None:
            progress_callback(completed_work, total_work, "benchmark", "Shared random benchmark", target.draw_date)

    benchmark_main = mean(random_main_by_index.values())
    benchmark_total = mean(random_total_by_index.values())
    main_ci = _ci95(list(random_main_by_index.values()))
    total_ci = _ci95(list(random_total_by_index.values()))

    rows: list[StrategyComparisonRow] = []
    signatures: dict[str, list[frozenset]] = {}

    for strategy in strategies:
        best_hits: list[int] = []
        best_totals: list[int] = []
        complete_pairs: list[tuple[int, int]] = []
        strategy_random_main: list[float] = []
        strategy_random_total: list[float] = []
        special_coverages: list[float] = []
        strategy_signatures: list[frozenset] = []
        strategy_error: str | None = None

        for local_pos, idx in enumerate(target_indices):
            if cancel_event is not None and cancel_event.is_set():
                raise BacktestCancelled()

            history = draws[:idx]
            target = draws[idx]
            main_stats = number_stats(history, config, recent_window, "main")
            special_stats = number_stats(history, config, recent_window, "special")
            strategy_for_test = "Condensed Portfolio" if strategy == "Full Wheel" else strategy

            try:
                tickets, _base_pool = generate_tickets(
                    config=config,
                    draws=history,
                    main_stats=main_stats,
                    special_stats=special_stats,
                    strategy=strategy_for_test,
                    lines=lines,
                    pool_size=pool_size,
                    special_pool_size=special_pool_size,
                    key_numbers=None,
                    seed=idx,
                )
                best_main, best_total, best_pair, _patterns, special_coverage = _portfolio_metrics(config, tickets, target)
                best_hits.append(best_main)
                best_totals.append(best_total)
                complete_pairs.append(best_pair)
                special_coverages.append(special_coverage)
                strategy_signatures.append(_portfolio_signature(tickets))
                strategy_random_main.append(random_main_by_index[idx])
                strategy_random_total.append(random_total_by_index[idx])
            except ValueError as exc:
                strategy_error = str(exc)
                remaining_indices = target_indices[local_pos:]
                for skip_idx in remaining_indices:
                    completed_work += 1
                    if progress_callback is not None:
                        progress_callback(completed_work, total_work, "strategy", strategy, draws[skip_idx].draw_date)
                break

            completed_work += 1
            if progress_callback is not None:
                progress_callback(completed_work, total_work, "strategy", strategy, target.draw_date)

        signatures[strategy] = strategy_signatures
        if strategy_error or not best_hits:
            rows.append(
                StrategyComparisonRow(
                    strategy=strategy,
                    tested_draws=0,
                    avg_best_line_hits=0.0,
                    random_avg_best_line_hits=benchmark_main,
                    best_line_lift=0.0,
                    max_best_line_hits=0,
                    threshold_counts=(),
                    beat_random_draws=0,
                    below_random_draws=0,
                    tied_random_draws=0,
                    error=strategy_error or "No valid historical tests completed",
                    random_avg_best_total_hits=benchmark_total,
                )
            )
            continue

        avg_best = mean(best_hits)
        avg_random = mean(strategy_random_main)
        avg_total = mean(best_totals)
        avg_random_total = mean(strategy_random_total)
        thresholds = tuple(
            (threshold, sum(1 for hits in best_hits if hits >= threshold))
            for threshold in range(2, config.main_pick + 1)
        )
        beat = sum(1 for hits, baseline in zip(best_hits, strategy_random_main) if hits > baseline)
        below = sum(1 for hits, baseline in zip(best_hits, strategy_random_main) if hits < baseline)
        tied = len(best_hits) - beat - below
        best_pair = max(complete_pairs, key=lambda pair: (pair[0] + pair[1], pair[0], pair[1]))
        rows.append(
            StrategyComparisonRow(
                strategy=strategy,
                tested_draws=len(best_hits),
                avg_best_line_hits=avg_best,
                random_avg_best_line_hits=avg_random,
                best_line_lift=avg_best - avg_random,
                max_best_line_hits=max(best_hits),
                threshold_counts=thresholds,
                beat_random_draws=beat,
                below_random_draws=below,
                tied_random_draws=tied,
                avg_best_total_hits=avg_total,
                random_avg_best_total_hits=avg_random_total,
                total_hit_lift=avg_total - avg_random_total,
                best_complete_pattern=_pattern_label(config, best_pair[0], best_pair[1]),
                avg_special_coverage=mean(special_coverages) if special_coverages else 0.0,
            )
        )

    rows.sort(
        key=lambda row: (
            row.error is not None,
            -row.avg_best_line_hits if not row.error else 0.0,
            -row.avg_best_total_hits if not row.error else 0.0,
            -dict(row.threshold_counts).get(4, 0) if not row.error else 0,
            -dict(row.threshold_counts).get(3, 0) if not row.error else 0,
            -row.beat_random_draws if not row.error else 0,
            row.strategy,
        )
    )

    similarity_warnings: list[str] = []
    valid_names = [row.strategy for row in rows if not row.error and len(signatures.get(row.strategy, [])) == tested]
    for a, b in itertools.combinations(valid_names, 2):
        sig_a = signatures[a]
        sig_b = signatures[b]
        exact = 0
        sims: list[float] = []
        for pa, pb in zip(sig_a, sig_b):
            if pa == pb:
                exact += 1
            union = pa | pb
            sims.append(len(pa & pb) / len(union) if union else 1.0)
        exact_rate = exact / tested if tested else 0.0
        avg_similarity = mean(sims) if sims else 0.0
        if exact_rate >= 0.50 or avg_similarity >= 0.65:
            similarity_warnings.append(
                f"{a} vs {b}: {exact_rate:.0%} exactly identical portfolios; average ticket-set overlap {avg_similarity:.0%}."
            )

    note = (
        f"Shared benchmark uses {random_trials_per_draw} independent random portfolios per hidden draw. "
        f"Main benchmark 95% interval: {main_ci[0]:.3f}–{main_ci[1]:.3f}. "
        "Complete-ticket component matching is diagnostic only and is not a prize-value scale. "
        "Ranking is historical evidence only; it does not change future draw mechanics."
    )
    return StrategyComparisonResult(
        tested_draws=tested,
        random_trials_per_draw=random_trials_per_draw,
        random_avg_best_line_hits=benchmark_main,
        rows=tuple(rows),
        notes=note,
        random_avg_best_total_hits=benchmark_total,
        random_ci_low=main_ci[0],
        random_ci_high=main_ci[1],
        random_total_ci_low=total_ci[0],
        random_total_ci_high=total_ci[1],
        similarity_warnings=tuple(similarity_warnings),
    )
