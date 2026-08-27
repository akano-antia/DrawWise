from core.models import GameConfig

CONFIG = GameConfig(
    key="lotto_hotpicks",
    name="Lotto HotPicks (Pick 5)",
    data_file="lotto hotpicks-draw-history.csv",
    main_min=1,
    main_max=59,
    main_pick=5,
    main_columns=("Ball 1", "Ball 2", "Ball 3", "Ball 4", "Ball 5", "Ball 6"),
    default_pool_size=10,
    notes=(
        "The draw has six Lotto balls; Pick 5 wins by matching all five selected numbers among those six. "
        "Current purchases follow Lotto's two-round draw-night format."
    ),
)
