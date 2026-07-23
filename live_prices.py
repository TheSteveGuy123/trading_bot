import csv
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import websocket

MARKET_QUESTION = "New Rihanna Album before GTA VI?"
CSV_FILE = Path("live_prices.csv")

response = requests.get(
    "https://gamma-api.polymarket.com/markets",
    params={
        "active": "true",
        "closed": "false",
        "limit": 100,
    },
    timeout=10,
)
response.raise_for_status()

markets = response.json()

market = next(
    item for item in markets
    if item["question"] == MARKET_QUESTION
)

token_ids = json.loads(market["clobTokenIds"])
outcome_names = json.loads(market["outcomes"])
outcomes_by_token = dict(zip(token_ids, outcome_names))

latest_prices = {}


def save_price(outcome, best_bid, best_ask):
    latest_prices[outcome] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "best_bid": best_bid,
        "best_ask": best_ask,
    }

    with open(CSV_FILE, "w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)

        writer.writerow([
            "timestamp",
            "market_name",
            "outcome",
            "best_bid",
            "best_ask",
        ])

        for outcome_name in outcome_names:
            price = latest_prices.get(outcome_name)

            if price:
                writer.writerow([
                    price["timestamp"],
                    MARKET_QUESTION,
                    outcome_name,
                    price["best_bid"],
                    price["best_ask"],
                ])


def show_and_save(outcome, best_bid, best_ask):
    print(f"{outcome} | best bid: {best_bid} | best ask: {best_ask}")
    save_price(outcome, best_bid, best_ask)


def send_heartbeats(ws):
    while True:
        time.sleep(10)
        ws.send("PING")


def on_open(ws):
    ws.send(json.dumps({
        "type": "market",
        "assets_ids": token_ids,
    }))

    threading.Thread(
        target=send_heartbeats,
        args=(ws,),
        daemon=True,
    ).start()

    print(f"Connected. Watching: {MARKET_QUESTION}")


def on_message(ws, message):
    if message in ("PING", "PONG"):
        return

    data = json.loads(message)

    def handle_event(event):
        if isinstance(event, list):
            for item in event:
                handle_event(item)
            return

        if not isinstance(event, dict):
            return

        if event.get("event_type") == "book":
            outcome = outcomes_by_token.get(event["asset_id"], "Unknown")
            best_bid = event["bids"][0]["price"] if event["bids"] else "none"
            best_ask = event["asks"][0]["price"] if event["asks"] else "none"

            show_and_save(outcome, best_bid, best_ask)

        elif event.get("event_type") == "price_change":
            for change in event["price_changes"]:
                outcome = outcomes_by_token.get(change["asset_id"], "Unknown")

                show_and_save(
                    outcome,
                    change["best_bid"],
                    change["best_ask"],
                )

    handle_event(data)


def on_error(ws, error):
    print(f"Connection error: {error}")


ws = websocket.WebSocketApp(
    "wss://ws-subscriptions-clob.polymarket.com/ws/market",
    on_open=on_open,
    on_message=on_message,
    on_error=on_error,
)

ws.run_forever()