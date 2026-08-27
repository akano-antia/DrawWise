from core.models import GameConfig

CONFIG = GameConfig(
    key="euromillions_hotpicks",
    name="EuroMillions HotPicks (Pick 5)",
    data_file="euromillions-hotpicks-draw-history.csv",
    main_min=1,
    main_max=50,
    main_pick=5,
    main_columns=("Ball 1", "Ball 2", "Ball 3", "Ball 4", "Ball 5"),
    default_pool_size=10,
    notes="Configured for Pick 5 from the five EuroMillions main balls.",
)
