from __future__ import annotations

import argparse
from pathlib import Path

from core.engine import LotteryEngine
from core.strategies import STRATEGIES
from games.registry import BY_KEY

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description="DrawWise 5.4 command-line generator")
    parser.add_argument("--game", choices=BY_KEY, required=True)
    parser.add_argument("--strategy", choices=STRATEGIES, default="Maximum Intelligence")
    parser.add_argument("--lines", type=int, default=10)
    parser.add_argument("--pool", type=int, default=None)
    parser.add_argument("--special-pool", type=int, default=None)
    parser.add_argument("--recent", type=int, default=20)
    parser.add_argument("--keys", default="", help="Comma-separated key numbers")
    args = parser.parse_args()

    cfg = BY_KEY[args.game]
    pool = args.pool or cfg.default_pool_size
    special_pool = args.special_pool if args.special_pool is not None else cfg.default_special_pool_size
    keys = [int(x.strip()) for x in args.keys.split(",") if x.strip()]

    engine = LotteryEngine(ROOT)
    result = engine.generate(
        config=cfg,
        strategy=args.strategy,
        lines=args.lines,
        pool_size=pool,
        special_pool_size=special_pool,
        recent_window=args.recent,
        key_numbers=keys,
        seed=42,
    )

    print(f"{cfg.name} — {args.strategy}")
    print(f"History: {len(result.draws)} draws")
    print(f"Top-prize odds per line: {result.top_prize_odds}")
    print(f"Base pool: {result.base_pool}")
    print(f"Pair coverage: {result.metrics['pair_coverage']:.1%}")
    print(f"Triple coverage: {result.metrics['triple_coverage']:.1%}")
    print()
    for i, ticket in enumerate(result.tickets, 1):
        print(f"{i:03d}. {ticket.display(cfg.special_name)}")


if __name__ == "__main__":
    main()
