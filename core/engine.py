from __future__ import annotations

from dataclasses import dataclass
import math
import os
from pathlib import Path
from typing import Optional

from core.backtest import (
    BacktestResult, StrategyComparisonResult, run_backtest, run_strategy_comparison,
)
from core.data import load_draws
from core.models import Draw, GameConfig, NumberStat, Ticket
from core.odds import format_odds, portfolio_jackpot_probability, top_prize_denominator
from core.intelligence import (PortfolioIntelligence, exact_main_threshold_probability, format_probability_odds, monte_carlo_challenge, objective_target, overlap_histogram, randomness_gate, unique_tuple_coverage)
from core.rule_eras import filter_current_analysis_draws
from core.stats import number_stats
from core.strategies import coverage_metrics, crowd_risk_penalty, generate_tickets, wheel_guarantees


@dataclass
class GenerationResult:
    config: GameConfig
    draws: list[Draw]
    main_stats: list[NumberStat]
    special_stats: list[NumberStat]
    tickets: list[Ticket]
    base_pool: list[int]
    metrics: dict[str, float]
    guarantees: dict[int, int]
    top_prize_odds: str
    portfolio_probability: Optional[float]
    intelligence: PortfolioIntelligence | None = None


class LotteryEngine:
    def __init__(self, project_root: Path):
        self.project_root = Path(project_root)

    def load(self, config: GameConfig) -> list[Draw]:
        return load_draws(config.csv_path(self.project_root), config)

    def analysis_draws(self, config: GameConfig) -> list[Draw]:
        """Return only draws compatible with the configured current analysis universe."""
        return filter_current_analysis_draws(config, self.load(config))

    def analyze(self, config: GameConfig, recent_window: int = 20, history_window: int | None = None):
        draws = self.analysis_draws(config)
        if history_window is not None and history_window > 0:
            draws = draws[-history_window:]
        main = number_stats(draws, config, recent_window, "main")
        special = number_stats(draws, config, recent_window, "special")
        return draws, main, special

    def evaluate_intelligence(
        self,
        config: GameConfig,
        draws: list[Draw],
        tickets: list[Ticket],
        base_pool: list[int],
        objective: str = "Best overall portfolio",
        *,
        candidates_evaluated: int = 0,
        search_moves: int = 0,
        seed: int | None = None,
    ) -> PortfolioIntelligence:
        metrics = coverage_metrics(tickets, base_pool, config)
        gate = randomness_gate(draws, config)
        hist = overlap_histogram([t.main for t in tickets])
        max_overlap = max(hist, default=0)
        crowd = (sum(crowd_risk_penalty(t.main, config) for t in tickets) / len(tickets)) if tickets else 0.0
        if os.environ.get("DRAWWISE_TEST_FAST") == "1":
            challenge_simulations = 2500
            challenge_portfolios = 8
        else:
            challenge_simulations = (100000 if len(tickets) <= 5 else 50000 if len(tickets) <= 10 else 20000)
            challenge_portfolios = (64 if len(tickets) <= 5 else 48 if len(tickets) <= 10 else 32)
        challenge = monte_carlo_challenge(
            config, tickets, objective,
            simulations=challenge_simulations,
            random_portfolios=challenge_portfolios,
            seed=(seed if seed is not None else 540013),
        )
        threshold, _require_special, _target_label = objective_target(config, objective)
        single_target = exact_main_threshold_probability(config, threshold)
        p = portfolio_jackpot_probability(config, len({(t.main, t.special) for t in tickets}))

        # Structural efficiency: 1.0 means every pair/triple slot contributed a
        # previously unseen tuple. This avoids inflating a score by choosing a tiny
        # artificial base pool.
        main_lines = [t.main for t in tickets]
        pair_capacity = max(1, len(main_lines) * math.comb(config.main_pick, 2))
        triple_capacity = max(1, len(main_lines) * math.comb(config.main_pick, 3))
        pair = unique_tuple_coverage(main_lines, 2) / pair_capacity
        triple = unique_tuple_coverage(main_lines, 3) / triple_capacity
        overlap_score = max(0.0, 1.0 - (metrics.get("avg_overlap", 0.0) / max(1.0, config.main_pick - 1)))
        crowd_score = max(0.0, 1.0 - min(1.0, crowd / 3.0))
        challenge_score = float(challenge["random_percentile"]) / 100.0
        rating = 100.0 * ((0.23 * pair) + (0.25 * triple) + (0.18 * overlap_score) + (0.12 * crowd_score) + (0.22 * challenge_score))

        return PortfolioIntelligence(
            objective=objective,
            portfolio_rating=max(0.0, min(100.0, rating)),
            pair_coverage=pair,
            triple_coverage=triple,
            avg_overlap=metrics.get("avg_overlap", 0.0),
            max_overlap=max_overlap,
            crowd_risk=crowd,
            history_gate=gate,
            candidates_evaluated=int(candidates_evaluated),
            search_moves=int(search_moves),
            challenge_draws=int(challenge["draws"]),
            challenge_random_portfolios=int(challenge["random_portfolios"]),
            target_label=str(challenge["target_label"]),
            target_probability=float(challenge["target_probability"]),
            single_line_target_probability=single_target,
            random_median_probability=float(challenge["random_median_probability"]),
            random_percentile=float(challenge["random_percentile"]),
            jackpot_probability=p,
            jackpot_odds=format_probability_odds(p),
        )

    def generate(
        self,
        config: GameConfig,
        strategy: str,
        lines: int,
        pool_size: int,
        special_pool_size: int,
        recent_window: int,
        key_numbers: list[int] | None = None,
        seed: int | None = None,
        excluded_main_lines: set[tuple[int, ...]] | None = None,
        objective: str = "Best overall portfolio",
    ) -> GenerationResult:
        draws, main_stats, special_stats = self.analyze(config, recent_window, history_window=None)
        generation_diagnostics: dict = {}
        tickets, base_pool = generate_tickets(
            config=config,
            draws=draws,
            main_stats=main_stats,
            special_stats=special_stats,
            strategy=strategy,
            lines=lines,
            pool_size=pool_size,
            special_pool_size=special_pool_size,
            key_numbers=key_numbers,
            seed=seed,
            excluded_main_lines=excluded_main_lines,
            objective=objective,
            diagnostics_out=generation_diagnostics,
        )
        metrics = coverage_metrics(tickets, base_pool, config)
        if strategy in {"Diversified Smart Portfolio", "Balanced Random"} or len(base_pool) > 18:
            guarantees = {}
        else:
            guarantees = wheel_guarantees(tickets, base_pool, config)
        p = portfolio_jackpot_probability(config, len({(t.main, t.special) for t in tickets}))

        intelligence = None
        if strategy == "Maximum Intelligence":
            intelligence = self.evaluate_intelligence(
                config, draws, tickets, base_pool, objective,
                candidates_evaluated=int(generation_diagnostics.get("candidates_evaluated", 0)),
                search_moves=int(generation_diagnostics.get("search_moves", 0)),
                seed=seed,
            )

        return GenerationResult(
            config=config,
            draws=draws,
            main_stats=main_stats,
            special_stats=special_stats,
            tickets=tickets,
            base_pool=base_pool,
            metrics=metrics,
            guarantees=guarantees,
            top_prize_odds=format_odds(top_prize_denominator(config)),
            portfolio_probability=p,
            intelligence=intelligence,
        )

    def backtest(
        self,
        config: GameConfig,
        strategy: str,
        lines: int,
        pool_size: int,
        special_pool_size: int,
        recent_window: int,
        key_numbers: list[int] | None = None,
        max_tests: int | None = None,
        progress_callback=None,
        cancel_event=None,
    ) -> BacktestResult:
        draws = self.analysis_draws(config)
        return run_backtest(
            config=config,
            draws=draws,
            strategy=strategy,
            lines=lines,
            pool_size=pool_size,
            special_pool_size=special_pool_size,
            recent_window=recent_window,
            key_numbers=key_numbers,
            max_tests=max_tests,
            progress_callback=progress_callback,
            cancel_event=cancel_event,
        )


    def compare_strategies(
        self,
        config: GameConfig,
        strategies: list[str] | tuple[str, ...],
        lines: int,
        pool_size: int,
        special_pool_size: int,
        recent_window: int,
        random_trials_per_draw: int = 50,
        max_tests: int | None = None,
        progress_callback=None,
        cancel_event=None,
    ) -> StrategyComparisonResult:
        draws = self.analysis_draws(config)
        return run_strategy_comparison(
            config=config,
            draws=draws,
            strategies=strategies,
            lines=lines,
            pool_size=pool_size,
            special_pool_size=special_pool_size,
            recent_window=recent_window,
            random_trials_per_draw=random_trials_per_draw,
            max_tests=max_tests,
            progress_callback=progress_callback,
            cancel_event=cancel_event,
        )
