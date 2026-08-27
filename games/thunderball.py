from core.models import GameConfig

CONFIG = GameConfig(
    key="thunderball",
    name="Thunderball",
    data_file="thunderball-draw-history.csv",
    main_min=1,
    main_max=39,
    main_pick=5,
    main_columns=("Ball 1", "Ball 2", "Ball 3", "Ball 4", "Ball 5"),
    special_name="Thunderball",
    special_min=1,
    special_max=14,
    special_pick=1,
    special_columns=("Thunderball",),
    default_pool_size=10,
    default_special_pool_size=4,
)
