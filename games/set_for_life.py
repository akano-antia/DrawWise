from core.models import GameConfig

CONFIG = GameConfig(
    key="set_for_life",
    name="Set For Life",
    data_file="set for life-draw-history.csv",
    main_min=1,
    main_max=47,
    main_pick=5,
    main_columns=("Ball 1", "Ball 2", "Ball 3", "Ball 4", "Ball 5"),
    special_name="Life Ball",
    special_min=1,
    special_max=10,
    special_pick=1,
    special_columns=("Life Ball",),
    default_pool_size=10,
    default_special_pool_size=3,
)
