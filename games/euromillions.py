from core.models import GameConfig

CONFIG = GameConfig(
    key="euromillions",
    name="EuroMillions",
    data_file="euromillions-draw-history.csv",
    main_min=1,
    main_max=50,
    main_pick=5,
    main_columns=("Ball 1", "Ball 2", "Ball 3", "Ball 4", "Ball 5"),
    special_name="Lucky Stars",
    special_min=1,
    special_max=12,
    special_pick=2,
    special_columns=("Lucky Star 1", "Lucky Star 2"),
    default_pool_size=10,
    default_special_pool_size=5,
)
