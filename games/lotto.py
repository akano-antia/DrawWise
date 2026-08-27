from core.models import GameConfig

CONFIG = GameConfig(
    key="lotto",
    name="UK Lotto",
    data_file="lotto-draw-history.csv",
    main_min=1,
    main_max=59,
    main_pick=6,
    main_columns=("Ball 1", "Ball 2", "Ball 3", "Ball 4", "Ball 5", "Ball 6"),
    default_pool_size=12,
    notes=(
        "Jackpot requires 6 main numbers. Bonus Ball is not part of the jackpot combination. "
        "Current UK Lotto purchases receive two 6/59 rounds per draw night; DrawWise keeps older 59-ball rounds for compatible historical analysis."
    ),
)
