"""Settings shared by every module: secrets, constants, league and market
tables, and the Firestore client."""

from firebase_admin import firestore
from firebase_functions.params import SecretParam

# The Odds API key lives in Google Cloud Secret Manager, not in this file.
# Set it with:  firebase functions:secrets:set ODDS_API_KEY
ODDS_API_KEY = SecretParam("ODDS_API_KEY")
API_BASE = "https://api.the-odds-api.com/v4"
BOOKMAKERS = ("draftkings", "fanduel")
GAME_MARKETS = ("h2h", "spreads", "totals")  # moneyline, spread, over/under

ODDS_CACHE_MINUTES = 20      # odds younger than this are reused (saves credits)
EVENTS_CACHE_MINUTES = 30    # game lists for the game picker (free calls)
SPORTS_LIST_CACHE_HOURS = 6  # the API's list of in-season leagues (free call)
MAX_BETS_PER_LIST = 20       # per boost combination, per ranking goal

# Per-game markets (player props, and soccer's game markets) cost 1 credit per
# market per game. They're fetched only for games inside an unused boost's
# window. To protect the game-line odds, they stop being fetched when fewer
# than PROP_CREDIT_RESERVE credits remain.
FETCH_PLAYER_PROPS = True
PROP_CREDIT_RESERVE = 100

# App league (BetType.name in Dart) -> The Odds API sport keys.
# A key ending in "*" matches every key with that prefix.
LEAGUE_KEYS = {
    "nfl": ["americanfootball_nfl"],
    "ncaaf": ["americanfootball_ncaaf"],
    "cfl": ["americanfootball_cfl"],
    "ufl": ["americanfootball_ufl"],
    "nba": ["basketball_nba"],
    "wnba": ["basketball_wnba"],
    "ncaab": ["basketball_ncaab"],
    "wncaab": ["basketball_wncaab"],
    "euroleague": ["basketball_euroleague"],
    "mlb": ["baseball_mlb"],
    "collegeBaseball": ["baseball_ncaa"],
    "kbo": ["baseball_kbo"],
    "npb": ["baseball_npb"],
    "nhl": ["icehockey_nhl"],
    "ahl": ["icehockey_ahl"],
    "shl": ["icehockey_sweden_hockey_league"],
    "liiga": ["icehockey_liiga"],
    "epl": ["soccer_epl"],
    "efl": ["soccer_efl_champ"],
    "laLiga": ["soccer_spain_la_liga"],
    "serieA": ["soccer_italy_serie_a"],
    "bundesliga": ["soccer_germany_bundesliga"],
    "ligue1": ["soccer_france_ligue_one"],
    "ucl": ["soccer_uefa_champs_league"],
    "uel": ["soccer_uefa_europa_league"],
    "mls": ["soccer_usa_mls"],
    "ligaMx": ["soccer_mexico_ligamx"],
    "eredivisie": ["soccer_netherlands_eredivisie"],
    "worldCup": ["soccer_fifa_world_cup"],
    "atp": ["tennis_atp*"],
    "wta": ["tennis_wta*"],
    "tennis": ["tennis_*"],
    "mma": ["mma_mixed_martial_arts"],
    "boxing": ["boxing_boxing"],
    "afl": ["aussierules_afl"],
    "nrl": ["rugbyleague_nrl"],
    "cricket": ["cricket_*"],
}

# One player prop market per sport for now (more can be added per league once
# the API plan is upgraded; each extra market costs 1 credit per game).
#
# Soccer also gets two game markets here. DraftKings and FanDuel post only the
# three-way moneyline (with a draw) in the main odds feed for soccer, which
# can't be hedged with two bets, so soccer needs Both Teams to Score (Yes/No)
# and alternate goal totals (Over/Under) from the per-game endpoint.
_FOOTBALL = ["player_reception_yds"]
_BASKETBALL = ["player_points"]
_SOCCER = ["btts", "alternate_totals", "player_shots_on_target"]
EVENT_MARKETS = {
    "nfl": _FOOTBALL, "ncaaf": _FOOTBALL, "cfl": _FOOTBALL,
    "nba": _BASKETBALL, "wnba": _BASKETBALL, "ncaab": _BASKETBALL,
    "mlb": ["pitcher_strikeouts"],
    "nhl": ["player_shots_on_goal"],
    **{league: _SOCCER for league in (
        "epl", "efl", "laLiga", "serieA", "bundesliga", "ligue1", "ucl", "uel",
        "mls", "ligaMx", "eredivisie", "worldCup",
    )},
}
MARKET_LABELS = {
    "h2h": "moneyline",
    "spreads": "spread",
    "totals": "total",
    "btts": "both teams to score",
    "player_reception_yds": "receiving yards",
    "player_points": "points",
    "pitcher_strikeouts": "strikeouts",
    "player_shots_on_goal": "shots on goal",
    "player_shots_on_target": "shots on target",
}

# What a boost can be limited to (PropType.name in Dart) -> the markets it
# covers, as they appear on a matchup (alternate_totals is merged into
# "totals"). A boost with no prop types selected applies to every market.
# Keep in sync with PropType in the app's profit_boost.dart.
PROP_TYPE_MARKETS = {
    "moneyline": ("h2h",),
    "spread": ("spreads",),
    "total": ("totals",),
    "bothTeamsToScore": ("btts",),
    "receivingYards": ("player_reception_yds",),
    "points": ("player_points",),
    "strikeouts": ("pitcher_strikeouts",),
    "shotsOnGoal": ("player_shots_on_goal",),
    "shotsOnTarget": ("player_shots_on_target",),
}

OBJECTIVES = ("guaranteed", "max")  # what hedges and the plan are ranked by

_db = None


def db():
    """Firestore client, created on first use (not at import time)."""
    global _db
    if _db is None:
        _db = firestore.client()
    return _db


LEAGUE_NAMES = {
    "nfl": "NFL", "ncaaf": "College Football", "cfl": "CFL", "ufl": "UFL",
    "nba": "NBA", "wnba": "WNBA", "ncaab": "Men's College Basketball",
    "wncaab": "Women's College Basketball", "euroleague": "EuroLeague",
    "mlb": "MLB", "collegeBaseball": "College Baseball", "kbo": "KBO", "npb": "NPB",
    "nhl": "NHL", "ahl": "AHL", "shl": "SHL", "liiga": "Liiga",
    "epl": "Premier League", "efl": "EFL Championship", "laLiga": "La Liga",
    "serieA": "Serie A", "bundesliga": "Bundesliga", "ligue1": "Ligue 1",
    "ucl": "Champions League", "uel": "Europa League", "mls": "MLS",
    "ligaMx": "Liga MX", "eredivisie": "Eredivisie", "worldCup": "World Cup",
    "atp": "ATP", "wta": "WTA", "tennis": "Tennis", "mma": "UFC/MMA",
    "boxing": "Boxing", "afl": "AFL", "nrl": "NRL", "cricket": "Cricket",
}
