from core.models import GameConfig

CONFIG = GameConfig(
    key="powerball",
    name="Powerball",
    data_file="powerball-draw-history.csv",
    main_min=1,
    main_max=69,
    main_pick=5,
    main_columns=("Ball 1", "Ball 2", "Ball 3", "Ball 4", "Ball 5"),
    special_name="Powerball",
    special_min=1,
    special_max=26,
    special_pick=1,
    special_columns=("Powerball",),
    default_pool_size=12,
    default_special_pool_size=5,
    notes="Historical file currently contains a small sample; treat rankings as exploratory until more draws are added.",
)
