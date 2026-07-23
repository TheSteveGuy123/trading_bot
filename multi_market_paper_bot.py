import csv
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import websocket

SETTINGS_FILE = Path("bot_settings.json")
PORTFOLIO_FILE = Path("multi_market_portfolio.json")
TRADES_FILE = Path("paper_trades.csv")
MONITORED_FILE = Path("monitored_markets.json")


def load_json(file_path, default_value):
    if not file_path.exists():
        return default_value

    with open(file_path, encoding="utf-8") as file:
        return json.load(file)


def save_json(file_path, data):
    with open(file_path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2)


settings = load_json(SETTINGS_FILE, {})

portfolio = load_json(
    PORTFOLIO_FILE,
    {
        "cash": 1000.0,
        "positions": {},
    },
)

crypto_market_count = settings.get("crypto_market_count", 10)
max_trade_cost = settings.get("max_trade_cost", 5.0)
max_open_positions = settings.get("max_open_positions", 3)
buy_below = settings.get("buy_below_or_equal_to", 0.05)
take_profit_multiplier = settings.get("take_profit_multiplier", 1.25)
max_spread = settings.get("max_spread", 0.02)

CRYPTO_KEYWORDS = (
    "bitcoin",
    "btc",
    "ethereum",
    "eth",
    "solana",
    "sol",
    "xrp",
)


def is_short_crypto_market(market):
    question = market.get("question", "").lower()

    if not any(keyword in question for keyword in CRYPTO_KEYWORDS):
        return False

    end_date = market.get("endDate")

    if not end_date:
        return False

    try:
        end_time = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
    except ValueError:
        return False

    hours_remaining = (
        end_time - datetime.now(timezone.utc)
    ).total_seconds() / 3600

    return 0 < hours_remaining <= 24


def is_crypto_market(market):
    question = market.get("question", "").lower()
    return any(keyword in question for keyword in CRYPTO_KEYWORDS)

tags_response = requests.get(
    "https://gamma-api.polymarket.com/tags",
    params={
        "limit": 500,
        "offset": 0,
    },
    timeout=10,
)
tags_response.raise_for_status()

crypto_tag = next(
    (
        tag for tag in tags_response.json()
        if tag.get("slug", "").lower() == "crypto"
    ),
    None,
)

markets = []

if crypto_tag:
    crypto_response = requests.get(
        "https://gamma-api.polymarket.com/markets",
        params={
            "active": "true",
            "closed": "false",
            "tag_id": crypto_tag["id"],
            "limit": 100,
        },
        timeout=10,
    )
    crypto_response.raise_for_status()

    crypto_markets = [
        market for market in crypto_response.json()
        if is_short_crypto_market(market)
    ]

    crypto_markets.sort(
        key=lambda market: float(market.get("volume24hr") or 0),
        reverse=True,
    )

    markets = crypto_markets[:crypto_market_count]

if not markets:
    fallback_response = requests.get(
        "https://gamma-api.polymarket.com/markets",
        params={
            "active": "true",
            "closed": "false",
            "limit": 100,
        },
        timeout=10,
    )
    fallback_response.raise_for_status()

    fallback_crypto_markets = [
        market for market in fallback_response.json()
        if is_crypto_market(market)
    ]

    fallback_crypto_markets.sort(
        key=lambda market: float(market.get("volume24hr") or 0),
        reverse=True,
    )

    markets = fallback_crypto_markets[:crypto_market_count]

if not markets:
    raise RuntimeError(
        "No active short-dated crypto markets were found. Try again later."
    )

token_details = {}
token_ids = []

for market in markets:
    if not market.get("enableOrderBook"):
        continue

    try:
        market_token_ids = json.loads(market["clobTokenIds"])
        outcomes = json.loads(market["outcomes"])
    except (KeyError, json.JSONDecodeError):
        continue

    for token_id, outcome in zip(market_token_ids, outcomes):
        token_ids.append(token_id)
        token_details[token_id] = {
            "market_name": market["question"],
            "outcome": outcome,
        }

save_json(
    MONITORED_FILE,
    {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "markets": [
            {
                "token_id": token_id,
                **details,
            }
            for token_id, details in token_details.items()
        ],
    },
)


def save_trade(action, token_id, price, shares):
    details = token_details[token_id]
    file_is_new = not TRADES_FILE.exists() or TRADES_FILE.stat().st_size == 0

    with open(TRADES_FILE, "a", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)

        if file_is_new:
            writer.writerow([
                "timestamp",
                "action",
                "market_name",
                "outcome",
                "price",
                "shares",
                "cash_after_trade",
            ])

        writer.writerow([
            datetime.now(timezone.utc).isoformat(),
            action,
            details["market_name"],
            details["outcome"],
            price,
            shares,
            portfolio["cash"],
        ])


def try_paper_trade(token_id, best_bid, best_ask):
    if token_id not in token_details:
        return

    if best_bid <= 0 or best_ask <= 0:
        return

    spread = best_ask - best_bid
    position = portfolio["positions"].get(token_id)

    if position:
        target_price = position["entry_price"] * take_profit_multiplier

        if best_bid >= target_price:
            shares = position["shares"]
            portfolio["cash"] += shares * best_bid
            del portfolio["positions"][token_id]

            save_json(PORTFOLIO_FILE, portfolio)
            save_trade("SELL", token_id, best_bid, shares)

            print(
                f"PAPER SELL | {token_details[token_id]['outcome']} | "
                f"${best_bid:.3f} | {shares:.2f} shares"
            )

        return

    if len(portfolio["positions"]) >= max_open_positions:
        return

    if best_ask > buy_below:
        return

    if spread > max_spread:
        return

    trade_cost = min(max_trade_cost, portfolio["cash"])

    if trade_cost <= 0:
        return

    shares = trade_cost / best_ask

    portfolio["cash"] -= trade_cost
    portfolio["positions"][token_id] = {
        "market_name": token_details[token_id]["market_name"],
        "outcome": token_details[token_id]["outcome"],
        "shares": shares,
        "entry_price": best_ask,
    }

    save_json(PORTFOLIO_FILE, portfolio)
    save_trade("BUY", token_id, best_ask, shares)

    print(
        f"PAPER BUY | {token_details[token_id]['outcome']} | "
        f"${best_ask:.3f} | {shares:.2f} shares"
    )


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

    print(
        f"Watching {len(token_ids)} outcomes across "
        f"{len(markets)} markets."
    )
    print(f"Virtual cash: ${portfolio['cash']:.2f}")


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
            bids = event["bids"]
            asks = event["asks"]

            if bids and asks:
                try_paper_trade(
                    event["asset_id"],
                    float(bids[0]["price"]),
                    float(asks[0]["price"]),
                )

        elif event.get("event_type") == "price_change":
            for change in event["price_changes"]:
                try_paper_trade(
                    change["asset_id"],
                    float(change["best_bid"]),
                    float(change["best_ask"]),
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
