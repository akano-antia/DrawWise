from games.lotto import CONFIG as LOTTO
from games.euromillions import CONFIG as EUROMILLIONS
from games.euromillions_hotpicks import CONFIG as EUROMILLIONS_HOTPICKS
from games.lotto_hotpicks import CONFIG as LOTTO_HOTPICKS
from games.set_for_life import CONFIG as SET_FOR_LIFE
from games.thunderball import CONFIG as THUNDERBALL
from games.powerball import CONFIG as POWERBALL

ALL_GAMES = [
    LOTTO,
    EUROMILLIONS,
    EUROMILLIONS_HOTPICKS,
    LOTTO_HOTPICKS,
    SET_FOR_LIFE,
    THUNDERBALL,
    POWERBALL,
]

BY_KEY = {g.key: g for g in ALL_GAMES}
BY_NAME = {g.name: g for g in ALL_GAMES}
