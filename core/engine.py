from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core.backtest import (
    BacktestResult, StrategyComparisonResult, run_backtest, run_strategy_comparison,
)
from core.data import load_draws
from core.models import Draw, GameConfig, NumberStat, Ticket
from core.odds import format_odds, portfolio_jackpot_probability, top_prize_denominator
from core.rule_eras import filter_current_analysis_draws
from core.stats import number_stats
from core.strategies import coverage_metrics, generate_tickets, wheel_guarantees


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
    ) -> GenerationResult:
        draws, main_stats, special_stats = self.analyze(config, recent_window, history_window=None)
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
        )
        metrics = coverage_metrics(tickets, base_pool, config)
        if strategy in {"Diversified Smart Portfolio", "Balanced Random"} or len(base_pool) > 18:
            guarantees = {}
        else:
            guarantees = wheel_guarantees(tickets, base_pool, config)
        p = portfolio_jackpot_probability(config, len({(t.main, t.special) for t in tickets}))
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
