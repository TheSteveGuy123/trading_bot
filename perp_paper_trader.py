import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import requests

PORTFOLIO_FILE = Path("perp_paper_portfolio.json")
TRADES_FILE = Path("perp_paper_trades.csv")
SUPPORTED_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
COINBASE_PRODUCTS = {
    "BTCUSDT": "BTC-USD",
    "ETHUSDT": "ETH-USD",
    "SOLUSDT": "SOL-USD",
    "XRPUSDT": "XRP-USD",
}


def load_portfolio():
    if not PORTFOLIO_FILE.exists():
        return {
            "cash": 1000.0,
            "positions": {},
        }

    with open(PORTFOLIO_FILE, encoding="utf-8") as file:
        return json.load(file)


def save_portfolio(portfolio):
    with open(PORTFOLIO_FILE, "w", encoding="utf-8") as file:
        json.dump(portfolio, file, indent=2)


def get_mark_price(symbol):
    # This is a reference price for a simulated perp, not a real exchange order.
    product_id = COINBASE_PRODUCTS[symbol]
    response = requests.get(
        f"https://api.exchange.coinbase.com/products/{product_id}/ticker",
        timeout=10,
    )
    response.raise_for_status()
    return float(response.json()["price"])


def save_trade(action, symbol, price, quantity, pnl=""):
    file_is_new = not TRADES_FILE.exists() or TRADES_FILE.stat().st_size == 0

    with open(TRADES_FILE, "a", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)

        if file_is_new:
            writer.writerow([
                "timestamp",
                "action",
                "symbol",
                "mark_price",
                "quantity",
                "realized_pnl",
            ])

        writer.writerow([
            datetime.now(timezone.utc).isoformat(),
            action,
            symbol,
            price,
            quantity,
            pnl,
        ])


portfolio = load_portfolio()

print("\n--- Paper Perps (1x, simulated only) ---")
print(f"Virtual perp cash: ${portfolio['cash']:.2f}")
print("Available symbols:", ", ".join(SUPPORTED_SYMBOLS))

action = input("Type long, short, or close: ").strip().lower()
symbol = input("Symbol: ").strip().upper()

if symbol not in SUPPORTED_SYMBOLS:
    raise ValueError("Unsupported symbol.")

mark_price = get_mark_price(symbol)
position = portfolio["positions"].get(symbol)

if action in ("long", "short"):
    if position:
        print("Close the existing paper position before opening another.")
    else:
        notional = float(input("Virtual USD notional: "))

        if notional <= 0:
            print("Notional must be positive.")
        elif notional > portfolio["cash"]:
            print("Not enough virtual perp cash.")
        else:
            quantity = notional / mark_price
            portfolio["cash"] -= notional
            portfolio["positions"][symbol] = {
                "side": action.upper(),
                "entry_price": mark_price,
                "quantity": quantity,
                "margin": notional,
            }
            save_portfolio(portfolio)
            save_trade(action.upper(), symbol, mark_price, quantity)
            print(
                f"PAPER {action.upper()} | {symbol} | "
                f"entry ${mark_price:,.2f} | quantity {quantity:.6f}"
            )

elif action == "close":
    if not position:
        print("No open paper perp position for this symbol.")
    else:
        direction = 1 if position["side"] == "LONG" else -1
        pnl = direction * (mark_price - position["entry_price"]) * position["quantity"]

        portfolio["cash"] += position["margin"] + pnl
        del portfolio["positions"][symbol]
        save_portfolio(portfolio)
        save_trade("CLOSE", symbol, mark_price, position["quantity"], pnl)

        print(
            f"PAPER CLOSE | {symbol} | "
            f"mark ${mark_price:,.2f} | P&L ${pnl:,.2f}"
        )

else:
    print("Action must be long, short, or close.")
