from __future__ import annotations

from math import comb

from core.models import GameConfig
from core.rule_eras import current_purchase_rounds


def ticket_space(config: GameConfig) -> int:
    total = comb(config.main_range_size, config.main_pick)
    if config.special_pick:
        total *= comb(config.special_range_size, config.special_pick)
    return total


def winning_ticket_count_per_draw(config: GameConfig) -> int:
    """How many distinct ticket selections are top-prize winners for one physical round."""
    main_winners = comb(len(config.main_columns), config.main_pick)
    special_winners = 1
    if config.special_pick:
        special_winners = comb(len(config.special_columns), config.special_pick)
    return main_winners * special_winners


def single_round_top_prize_probability(config: GameConfig) -> float:
    return winning_ticket_count_per_draw(config) / ticket_space(config)


def single_round_top_prize_denominator(config: GameConfig) -> float:
    return ticket_space(config) / winning_ticket_count_per_draw(config)


def top_prize_probability_per_line(config: GameConfig) -> float:
    """Current top-prize probability for one purchased line.

    Most games have one physical round per purchase. Current UK Lotto/Lotto HotPicks
    purchases receive two independent rounds on the same draw night, so the purchased-line
    chance is 1 - (1 - p_round)^2 rather than merely reporting a one-round denominator.
    """
    p_round = single_round_top_prize_probability(config)
    rounds = current_purchase_rounds(config)
    if rounds == 1:
        return p_round
    return 1.0 - (1.0 - p_round) ** rounds


def top_prize_denominator(config: GameConfig) -> float:
    if current_purchase_rounds(config) == 1:
        return single_round_top_prize_denominator(config)
    return 1.0 / top_prize_probability_per_line(config)


def portfolio_jackpot_probability(config: GameConfig, unique_lines: int) -> float | None:
    """Current exact portfolio top-prize probability for standard one-winner-combination games.

    For standard games, distinct line events are disjoint inside one physical round. If the
    current purchased line receives multiple rounds (UK Lotto), the same portfolio is entered
    in each independent round and the purchase-level chance is compounded across those rounds.

    HotPicks can have multiple winning ticket selections in one round, so portfolio events can
    overlap and n/space is not generally exact; return None for those games.
    """
    if winning_ticket_count_per_draw(config) != 1:
        return None
    unique_lines = max(0, min(unique_lines, ticket_space(config)))
    p_round = unique_lines / ticket_space(config)
    rounds = current_purchase_rounds(config)
    if rounds == 1:
        return p_round
    return 1.0 - (1.0 - p_round) ** rounds


def format_odds(denominator: float) -> str:
    if denominator <= 1:
        return "1 in 1"
    return f"1 in {denominator:,.0f}"
