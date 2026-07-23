import csv
import json
from pathlib import Path

PORTFOLIO_FILE = Path("paper_portfolio.json")
PRICES_FILE = Path("live_prices.csv")


def load_portfolio():
    with open(PORTFOLIO_FILE, encoding="utf-8") as file:
        return json.load(file)


def save_portfolio(portfolio):
    with open(PORTFOLIO_FILE, "w", encoding="utf-8") as file:
        json.dump(portfolio, file, indent=2)


def load_prices():
    prices = {}

    with open(PRICES_FILE, newline="", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            prices[row["outcome"]] = {
                "best_bid": float(row["best_bid"]),
                "best_ask": float(row["best_ask"]),
            }

    return prices


portfolio = load_portfolio()
prices = load_prices()

print("\n--- Paper Trading ---")
print(f"Virtual cash: ${portfolio['cash']:.2f}")

for outcome in ["Yes", "No"]:
    quote = prices[outcome]
    shares = portfolio["positions"][outcome]

    print(
        f"{outcome}: "
        f"bid ${quote['best_bid']:.2f}, "
        f"ask ${quote['best_ask']:.2f}, "
        f"shares {shares:.2f}"
    )

action = input("\nType buy or sell: ").strip().lower()
outcome = input("Choose Yes or No: ").strip().title()
shares = float(input("Number of shares: "))

if action == "buy":
    price = prices[outcome]["best_ask"]
    cost = price * shares

    if cost > portfolio["cash"]:
        print("Not enough virtual cash.")
    else:
        portfolio["cash"] -= cost
        portfolio["positions"][outcome] += shares
        save_portfolio(portfolio)
        print(f"Paper-bought {shares:.2f} {outcome} shares at ${price:.2f}.")

elif action == "sell":
    price = prices[outcome]["best_bid"]

    if shares > portfolio["positions"][outcome]:
        print("Not enough virtual shares to sell.")
    else:
        proceeds = price * shares
        portfolio["cash"] += proceeds
        portfolio["positions"][outcome] -= shares
        save_portfolio(portfolio)
        print(f"Paper-sold {shares:.2f} {outcome} shares at ${price:.2f}.")

else:
    print("Action must be buy or sell.")