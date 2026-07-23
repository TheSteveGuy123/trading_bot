import csv
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

PRICES_FILE = Path("crypto_reference_prices.csv")
PRODUCTS = {
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
    "SOL": "SOL-USD",
    "XRP": "XRP-USD",
}


def save_price(timestamp, symbol, price):
    file_is_new = not PRICES_FILE.exists() or PRICES_FILE.stat().st_size == 0

    with open(PRICES_FILE, "a", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        if file_is_new:
            writer.writerow(["timestamp", "symbol", "price"])
        writer.writerow([timestamp, symbol, price])


def get_reference_price(product_id):
    response = requests.get(
        f"https://api.exchange.coinbase.com/products/{product_id}/ticker",
        timeout=10,
    )
    response.raise_for_status()
    return float(response.json()["price"])


if __name__ == "__main__":
    print("Recording BTC, ETH, SOL, and XRP reference prices every 5 seconds. Press Ctrl+C to stop.")

    while True:
        for symbol, product_id in PRODUCTS.items():
            try:
                price = get_reference_price(product_id)
                timestamp = datetime.now(timezone.utc).isoformat()
                save_price(timestamp, symbol, price)
                print(f"{timestamp} | {symbol} | ${price:,.4f}")
            except (requests.RequestException, KeyError, TypeError, ValueError) as error:
                print(f"Could not update {symbol}: {error}")

        time.sleep(5)
