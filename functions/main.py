# Welcome to Cloud Functions for Firebase for Python!
# To get started, simply uncomment the below code or create your own.
# Deploy with `firebase deploy`

from firebase_functions import https_fn
from firebase_functions.options import set_global_options
from firebase_admin import initialize_app

# For cost control, you can set the maximum number of containers that can be
# running at the same time. This helps mitigate the impact of unexpected
# traffic spikes by instead downgrading performance. This limit is a per-function
# limit. You can override the limit for each function using the max_instances
# parameter in the decorator, e.g. @https_fn.on_request(max_instances=5).
set_global_options(max_instances=10)

from datetime import datetime
import json
import time
import requests

# Configs
API_KEY = "5dd8a236308205f8cf22f966bee1f42d"
SPORT = "americanfootball_nfl"
REGIONS = "us"
MARKETS = "spreads,totals"
INTERVAL_SECONDS = 7200  # 2 hours


def fetch_odds_for_bookmaker(bookmaker_key):
  """Fetches odds from The Odds API and parses them into a list of dictionaries

  for a specific bookmaker (e.g., 'draftkings' or 'fanduel').
  """
  url = f"https://api.the-odds-api.com/v4/sports/{SPORT}/odds"
  params = {import os
import pandas as pd
import requests

# Configs
API_KEY = "5dd8a236308205f8cf22f966bee1f42d"
SPORT = "americanfootball_nfl"
REGIONS = "us"
MARKETS = "spreads,totals"


def fetch_odds_for_bookmaker(bookmaker_key):
  """Fetches odds from The Odds API and parses them into a Pandas DataFrame

  for a specific bookmaker (e.g., 'draftkings' or 'fanduel').
  """
  url = f"https://api.the-odds-api.com/v4/sports/{SPORT}/odds"
  params = {
      "api_key": API_KEY,
      "regions": REGIONS,
      "markets": MARKETS,
      "oddsFormat": "american",
  }

  response = requests.get(url, params=params)
  if response.status_code != 200:
    print(
        f"Error fetching data for {bookmaker_key}: {response.status_code} -"
        f" {response.text}"
    )
    return None

  data = response.json()
  parsed_rows = []

  for game in data:
    home_team = game["home_team"]
    away_team = game["away_team"]
    commence_time = game["commence_time"]

    bookmakers = game.get("bookmakers", [])
    # Filter for the specific bookmaker passed into the function
    bk_data = next((bk for bk in bookmakers if bk["key"] == bookmaker_key), None)

    if not bk_data:
      continue  # Skip if this bookmaker doesn't have odds for this game yet

    row = {
        "commence_time": commence_time,
        "home_team": home_team,
        "away_team": away_team,
        "spread_home": None,
        "spread_home_price": None,
        "spread_away": None,
        "spread_away_price": None,
        "total_line": None,
        "over_price": None,
        "under_price": None,
    }

    for market in bk_data.get("markets", []):
      market_key = market["key"]
      outcomes = market.get("outcomes", [])

      if market_key == "spreads":
        for outcome in outcomes:
          team = outcome["name"]
          if team == home_team:
            row["spread_home"] = outcome.get("point")
            row["spread_home_price"] = outcome.get("price")
          elif team == away_team:
            row["spread_away"] = outcome.get("point")
            row["spread_away_price"] = outcome.get("price")

      elif market_key == "totals":
        for outcome in outcomes:
          name = outcome["name"]
          if name == "Over":
            row["total_line"] = outcome.get("point")
            row["over_price"] = outcome.get("price")
          elif name == "Under":
            row["under_price"] = outcome.get("price")

    parsed_rows.append(row)

  return pd.DataFrame(parsed_rows)


if __name__ == "__main__":
  INTERVAL_SECONDS = 7200

  print("Starting continuous odds tracker (runs every 2 hours)...")

  while True:

    print("Fetching and comparing odds between DraftKings and FanDuel...")

    df_dk = fetch_odds_for_bookmaker("draftkings")
    df_fd = fetch_odds_for_bookmaker("fanduel")

    if df_dk is not None and df_fd is not None:
        # Merge the two dataframes on game identifiers using suffixes to tell them apart
        merged_df = pd.merge(
            df_dk,
            df_fd,
            on=["commence_time", "home_team", "away_team"],
            suffixes=("_dk", "_fd"),
        )

        # Check spreads and totals; if they don't match, replace the line with "N/A"
        # (Prices are left alone so you can still view differing juice)
        merged_df["spread_home_match"] = (
            merged_df["spread_home_dk"] == merged_df["spread_home_fd"]
        )
        merged_df["spread_away_match"] = (
            merged_df["spread_away_dk"] == merged_df["spread_away_fd"]
        )
        merged_df["total_match"] = (
            merged_df["total_line_dk"] == merged_df["total_line_fd"]
        )

        # Convert numeric columns to object type so they can accept strings like "N/A"
        cols_to_fix = [
            "spread_home_dk",
            "spread_home_fd",
            "spread_away_dk",
            "spread_away_fd",
            "total_line_dk",
            "total_line_fd",
        ]
        for col in cols_to_fix:
        merged_df[col] = merged_df[col].astype(object)

        # Now apply "N/A" where lines disagree
        merged_df.loc[~merged_df["spread_home_match"], "spread_home_dk"] = "N/A"
        merged_df.loc[~merged_df["spread_home_match"], "spread_home_fd"] = "N/A"

        merged_df.loc[~merged_df["spread_away_match"], "spread_away_dk"] = "N/A"
        merged_df.loc[~merged_df["spread_away_match"], "spread_away_fd"] = "N/A"

        merged_df.loc[~merged_df["total_match"], "total_line_dk"] = "N/A"
        merged_df.loc[~merged_df["total_match"], "total_line_fd"] = "N/A"

        # Clean up columns to show a side-by-side comparison
        final_view = merged_df[[
            "commence_time",
            "away_team",
            "home_team",
            "spread_home_dk",
            "spread_home_fd",
            "spread_home_price_dk",
            "spread_home_price_fd",
            "total_line_dk",
            "total_line_fd",
        ]]

        print(f"\n--- Compared Odds ({len(final_view)} overlapping games) ---")
        print(final_view.head(10))

        final_view.to_csv("compared_odds.csv", index=False)
        print("\nSaved comparison to compared_odds.csv")
    else:
        print("Error fetching data from one or both books.")

    time.sleep(INTERVAL_SECONDS)
      "api_key": API_KEY,
      "regions": REGIONS,
      "markets": MARKETS,
      "oddsFormat": "american",
  }

  try:
    response = requests.get(url, params=params)
    if response.status_code != 200:
      return None

    data = response.json()
    parsed_rows = []

    for game in data:
      home_team = game.get("home_team")
      away_team = game.get("away_team")
      commence_time = game.get("commence_time")

      bookmakers = game.get("bookmakers", [])
      bk_data = next((bk for bk in bookmakers if bk["key"] == bookmaker_key), None)

      if not bk_data:
        continue

      row = {
          "commence_time": commence_time,
          "home_team": home_team,
          "away_team": away_team,
          "spread_home": None,
          "spread_home_price": None,
          "spread_away": None,
          "spread_away_price": None,
          "total_line": None,
          "over_price": None,
          "under_price": None,
      }

      for market in bk_data.get("markets", []):
        market_key = market.get("key")
        outcomes = market.get("outcomes", [])

        if market_key == "spreads":
          for outcome in outcomes:
            team = outcome.get("name")
            if team == home_team:
              row["spread_home"] = outcome.get("point")
              row["spread_home_price"] = outcome.get("price")
            elif team == away_team:
              row["spread_away"] = outcome.get("point")
              row["spread_away_price"] = outcome.get("price")

        elif market_key == "totals":
          for outcome in outcomes:
            name = outcome.get("name")
            if name == "Over":
              row["total_line"] = outcome.get("point")
              row["over_price"] = outcome.get("price")
            elif name == "Under":
              row["under_price"] = outcome.get("price")

      parsed_rows.append(row)

    return parsed_rows
  except Exception:
    return None


def american_to_decimal(american_odds):
  """Converts American odds to Decimal odds."""
  if american_odds > 0:
    return (american_odds / 100) + 1
  else:
    return (100 / abs(american_odds)) + 1


def round_to_nearest_half(val):
  """Rounds a monetary value to the nearest 0.50."""
  return round(round(val * 2) / 2, 2)


def calculate_hedging_opportunities(
    merged_rows, promotions, current_time=None
):
  """Calculates optimal hedging with stakes rounded to the nearest $0.50."""
  if current_time is None:
    current_time = datetime.utcnow()

  opportunities = []
  seen_bets = set()

  dk_promos = [
      p for p in promotions if p["bookmaker"].lower() == "draftkings"
  ]
  fd_promos = [p for p in promotions if p["bookmaker"].lower() == "fanduel"]

  for game in merged_rows:
    game_time = datetime.fromisoformat(
        game["commence_time"].replace("Z", "+00:00")
    )

    active_dk_promo = next(
        (
            p
            for p in dk_promos
            if datetime.fromisoformat(
                p["startTime"].replace("Z", "+00:00")
            )
            <= game_time
            <= datetime.fromisoformat(p["endTime"].replace("Z", "+00:00"))
        ),
        None,
    )
    active_fd_promo = next(
        (
            p
            for p in fd_promos
            if datetime.fromisoformat(
                p["startTime"].replace("Z", "+00:00")
            )
            <= game_time
            <= datetime.fromisoformat(p["endTime"].replace("Z", "+00:00"))
        ),
        None,
    )

    matchup_scenarios = [
        {
            "marketDesc": f"{game['home_team']} Spread",
            "dk_odds": game["spread_home_price_dk"],
            "fd_odds": game["spread_away_price_fd"],
        },
        {
            "marketDesc": f"{game['away_team']} Spread",
            "dk_odds": game["spread_away_price_dk"],
            "fd_odds": game["spread_home_price_fd"],
        },
    ]

    for scenario in matchup_scenarios:
      dk_odds = scenario["dk_odds"]
      fd_odds = scenario["fd_odds"]

      if dk_odds is None or fd_odds is None:
        continue

      # --- SCENARIO A: TWO-WAY BOOST ---
      if active_dk_promo and active_fd_promo:
        if (
            active_dk_promo["minOdds"] <= dk_odds <= active_dk_promo["maxOdds"]
            and active_fd_promo["minOdds"]
            <= fd_odds
            <= active_fd_promo["maxOdds"]
        ):

          stake_dk = active_dk_promo["maxBetSize"]
          boost_mult_dk = 1 + (active_dk_promo["boostPercentage"] / 100)
          boost_mult_fd = 1 + (active_fd_promo["boostPercentage"] / 100)

          dec_dk = american_to_decimal(dk_odds)
          dec_fd = american_to_decimal(fd_odds)

          boosted_return_mult_dk = 1 + ((dec_dk - 1) * boost_mult_dk)
          boosted_return_mult_fd = 1 + ((dec_fd - 1) * boost_mult_fd)

          total_return_if_dk_wins = stake_dk * boosted_return_mult_dk

          # Calculate raw hedge stake, then round to nearest $0.50
          raw_stake_fd = total_return_if_dk_wins / boosted_return_mult_fd
          stake_fd = round_to_nearest_half(raw_stake_fd)

          # Recalculate profit based on the rounded stake
          payout_dk = stake_dk * boosted_return_mult_dk
          payout_fd = stake_fd * boosted_return_mult_fd
          total_investment = stake_dk + stake_fd

          # Guaranteed profit is minimum possible return minus investment
          guaranteed_profit = min(payout_dk, payout_fd) - total_investment
          roi = (guaranteed_profit / total_investment) * 100

          unique_key = f"{game['away_team']}_at_{game['home_team']}_{scenario['marketDesc']}_TWO_WAY"

          if unique_key not in seen_bets and guaranteed_profit > 0:
            seen_bets.add(unique_key)
            opportunities.append({
                "game": f"{game['away_team']} @ {game['home_team']}",
                "market": scenario["marketDesc"],
                "type": "Two-Way Boost",
                "boostedBook1": "draftkings",
                "stake1": stake_dk,
                "odds1": dk_odds,
                "boostedBook2": "fanduel",
                "stake2": stake_fd,
                "odds2": fd_odds,
                "guaranteedProfit": round(guaranteed_profit, 2),
                "roiPercent": round(roi, 2),
            })

      # --- SCENARIO B: ONE-WAY BOOSTS ---
      one_way_options = []
      if active_dk_promo:
        one_way_options.append(
            ("draftkings", "fanduel", active_dk_promo, dk_odds, fd_odds)
        )
      if active_fd_promo:
        one_way_options.append(
            ("fanduel", "draftkings", active_fd_promo, fd_odds, dk_odds)
        )

      for boosted_book, hedge_book, promo, b_odds, h_odds in one_way_options:
        if not (promo["minOdds"] <= b_odds <= promo["maxOdds"]):
          continue

        stake = promo["maxBetSize"]
        boost_mult = 1 + (promo["boostPercentage"] / 100)

        b_dec = american_to_decimal(b_odds)
        h_dec = american_to_decimal(h_odds)

        boosted_profit_mult = 1 + ((b_dec - 1) * boost_mult)
        total_boosted_return = stake * boosted_profit_mult

        # Calculate raw hedge stake, then round to nearest $0.50
        raw_hedge_stake = total_boosted_return / h_dec
        hedge_stake = round_to_nearest_half(raw_hedge_stake)

        # Recalculate true returns with rounded stake
        payout_boosted = stake * boosted_profit_mult
        payout_hedge = hedge_stake * h_dec
        total_investment = stake + hedge_stake

        guaranteed_profit = min(payout_boosted, payout_hedge) - total_investment
        roi = (guaranteed_profit / total_investment) * 100

        unique_key = f"{game['away_team']}_at_{game['home_team']}_{scenario['marketDesc']}_{boosted_book}_ONE_WAY"

        if unique_key not in seen_bets and guaranteed_profit > 0:
          seen_bets.add(unique_key)
          opportunities.append({
              "game": f"{game['away_team']} @ {game['home_team']}",
              "market": scenario["marketDesc"],
              "type": f"One-Way Boost ({boosted_book})",
              "boostedBook": boosted_book,
              "hedgeBook": hedge_book,
              "boostStake": stake,
              "boostOdds": b_odds,
              "hedgeStake": hedge_stake,
              "hedgeOdds": h_odds,
              "guaranteedProfit": round(guaranteed_profit, 2),
              "roiPercent": round(roi, 2),
          })

  opportunities.sort(key=lambda x: x["guaranteedProfit"], reverse=True)
  return opportunities


def run_tracker():
  dk_rows = fetch_odds_for_bookmaker("draftkings")
  fd_rows = fetch_odds_for_bookmaker("fanduel")

  if not dk_rows or not fd_rows:
    return

  fd_map = {}
  for row in fd_rows:
    key = f"{row['commence_time']}_{row['home_team']}_{row['away_team']}"
    fd_map[key] = row

  merged_rows = []

  for dk in dk_rows:
    key = f"{dk['commence_time']}_{dk['home_team']}_{dk['away_team']}"
    fd = fd_map.get(key)

    if not fd:
      continue

    spread_home_match = dk["spread_home"] == fd["spread_home"]
    spread_away_match = dk["spread_away"] == fd["spread_away"]
    total_match = dk["total_line"] == fd["total_line"]

    merged_rows.append({
        "commence_time": dk["commence_time"],
        "away_team": dk["away_team"],
        "home_team": dk["home_team"],
        "spread_home_dk": dk["spread_home"] if spread_home_match else "N/A",
        "spread_home_fd": fd["spread_home"] if spread_home_match else "N/A",
        "spread_home_price_dk": dk["spread_home_price"],
        "spread_home_price_fd": fd["spread_home_price"],
        "spread_away_price_dk": dk["spread_away_price"],
        "spread_away_price_fd": fd["spread_away_price"],
        "total_line_dk": dk["total_line"] if total_match else "N/A",
        "total_line_fd": fd["total_line"] if total_match else "N/A",
    })

  # Example active promotions list
  active_promotions = [{
      "bookmaker": "draftkings",
      "boostPercentage": 25,
      "maxBetSize": 50,
      "minOdds": -300,
      "maxOdds": 500,
      "startTime": "2026-10-01T00:00:00Z",
      "endTime": "2026-10-31T23:59:59Z",
  }]

  best_hedges = calculate_hedging_opportunities(merged_rows, active_promotions)

  # Save hedging results to JSON
  if best_hedges:
    with open("best_hedges.json", "w") as f:
      json.dump(best_hedges, f, indent=2)

# initialize_app()
#
#
# @https_fn.on_request()
# def on_request_example(req: https_fn.Request) -> https_fn.Response:
#     return https_fn.Response("Hello world!")
