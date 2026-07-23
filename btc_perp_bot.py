"""BTC perpetual-futures research and paper-trading bot (simulation only).

Examples:
  python btc_perp_bot.py backtest --days 30 --strategy all
  python btc_perp_bot.py paper --strategy sma --once
  python btc_perp_bot.py paper --strategy momentum --interval 60
"""
import argparse
import csv
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

from bot_config import load_config

PRODUCT = "BTC-USD"
RESULTS_FILE = Path("btc_perp_backtests.json")
PAPER_FILE = Path("btc_perp_paper_portfolio.json")
PAPER_TRADES_FILE = Path("btc_perp_paper_trades.csv")
TRADE_HISTORY_FILE = Path("btc_perp_trade_history.csv")
EQUITY_FILE = Path("btc_perp_equity.csv")


def utc_now():
    return datetime.now(timezone.utc)


def candles(days, granularity=None):
    """Fetch hourly public Coinbase candles, requesting no more than 300 per call."""
    granularity = granularity or load_config()["backtest"]["candle_granularity_seconds"]
    end = utc_now()
    start = end - timedelta(days=days)
    rows = []
    cursor = start
    while cursor < end:
        chunk_end = min(cursor + timedelta(seconds=granularity * 300), end)
        response = requests.get(
            f"https://api.exchange.coinbase.com/products/{PRODUCT}/candles",
            params={"start": cursor.isoformat(), "end": chunk_end.isoformat(), "granularity": granularity},
            timeout=15,
        )
        response.raise_for_status()
        rows.extend(response.json())
        cursor = chunk_end
    frame = pd.DataFrame(rows, columns=["time", "low", "high", "open", "close", "volume"])
    if frame.empty:
        raise RuntimeError("Coinbase returned no BTC candles.")
    frame = frame.drop_duplicates("time").sort_values("time")
    frame["time"] = pd.to_datetime(frame["time"], unit="s", utc=True)
    return frame.reset_index(drop=True)


def signal_frame(data, strategy, settings=None):
    settings = settings or load_config()
    strategy_settings = settings["strategies"][strategy]
    out = data.copy()
    if strategy == "sma":
        fast, slow = out.close.rolling(strategy_settings["fast_period"]).mean(), out.close.rolling(strategy_settings["slow_period"]).mean()
        out["fast_sma"], out["slow_sma"] = fast, slow
        out["signal"] = (fast > slow).astype(int).replace({0: -1})
    elif strategy == "ema":
        fast, slow = out.close.ewm(span=strategy_settings["fast_period"], adjust=False).mean(), out.close.ewm(span=strategy_settings["slow_period"], adjust=False).mean()
        out["signal"] = (fast > slow).astype(int).replace({0: -1})
    elif strategy == "momentum":
        change = out.close.pct_change(strategy_settings["lookback"]) * 100
        out["signal"] = (change > strategy_settings["entry_threshold_pct"]).astype(int).replace({0: -1})
    elif strategy == "breakout":
        prior_high = out.high.rolling(strategy_settings["lookback"]).max().shift(1) * (1 + strategy_settings["breakout_buffer_pct"] / 100)
        prior_low = out.low.rolling(strategy_settings["lookback"]).min().shift(1) * (1 - strategy_settings["breakout_buffer_pct"] / 100)
        out["signal"] = 0
        out.loc[out.close > prior_high, "signal"] = 1
        out.loc[out.close < prior_low, "signal"] = -1
        out["signal"] = out.signal.replace(0, pd.NA).ffill().fillna(0).astype(int)
    else:
        raise ValueError(f"Unknown strategy: {strategy}")
    return out.dropna().reset_index(drop=True)


def metrics(equity, trades, starting_balance=None):
    starting_balance = starting_balance if starting_balance is not None else load_config()["backtest"]["initial_balance"]
    series = pd.Series(equity, dtype=float)
    returns = series.pct_change().dropna()
    closed = [trade for trade in trades if trade["action"] == "CLOSE"]
    winners = sum(trade["pnl"] - trade.get("fee", 0.0) > 0 for trade in closed)
    sharpe = None if len(closed) < 2 or returns.empty or returns.std() == 0 else float((returns.mean() / returns.std()) * (24 * 365) ** 0.5)
    drawdown = series / series.cummax() - 1
    return {
        "pnl": round(float(series.iloc[-1] - starting_balance), 2),
        "return_pct": round(float((series.iloc[-1] / starting_balance - 1) * 100), 2),
        "win_rate": round(winners / len(closed) * 100, 2) if closed else None,
        "sharpe_ratio": round(sharpe, 2) if sharpe is not None else None,
        "maximum_drawdown_pct": round(float(drawdown.min() * 100), 2),
        "closed_trades": len(closed),
    }


def empty_paper_state(starting_balance=None):
    starting_balance = starting_balance if starting_balance is not None else load_config()["paper_trading"]["starting_balance"]
    return {
        "starting_balance": starting_balance,
        "available_cash": starting_balance,
        "reserved_margin": 0.0,
        "position": 0,
        "entry_price": 0.0,
        "quantity": 0.0,
        "realized_pnl": 0.0,
        "fees_paid": 0.0,
        "strategy": None,
        "last_price": None,
        "entry_time": None,
        "entry_fee": 0.0,
        "current_signal": 0,
        "current_action": "Waiting for the first market update",
        "fast_sma": None,
        "slow_sma": None,
        "next_exit_condition": "No exit condition while flat.",
        "last_updated": None,
    }


def unrealized_pnl(state, current_price):
    return state["position"] * (current_price - state["entry_price"]) * state["quantity"]


def account_snapshot(state, current_price):
    unrealized = unrealized_pnl(state, current_price)
    total_pnl = state["realized_pnl"] + unrealized - state["fees_paid"]
    return {
        "starting_balance": state["starting_balance"],
        "available_cash": state["available_cash"],
        "reserved_margin": state["reserved_margin"],
        "position": state["position"],
        "entry_price": state["entry_price"],
        "current_price": current_price,
        "quantity": state["quantity"],
        "position_notional": current_price * state["quantity"],
        "realized_pnl": state["realized_pnl"],
        "unrealized_pnl": unrealized,
        "fees_paid": state["fees_paid"],
        "total_pnl": total_pnl,
        "equity": state["starting_balance"] + total_pnl,
    }


def fee_rate(settings, backtest=False):
    if backtest and not settings["backtest"]["include_fees"]:
        return 0.0
    return settings["paper_trading"][f"{settings['execution']['order_type']}_fee_bps"] / 10_000


def stop_loss_price(entry_price, side, settings=None):
    settings = settings or load_config()
    return entry_price * (1 - side * settings["risk_management"]["stop_loss_pct"] / 100)


def stop_loss_hit(entry_price, side, mark, settings=None):
    settings = settings or load_config()
    if not settings["risk_management"]["enable_stop_loss"]:
        return False
    stop_price = stop_loss_price(entry_price, side, settings)
    return mark <= stop_price if side == 1 else mark >= stop_price


def open_position(state, side, price, settings=None, backtest=False):
    settings = settings or load_config()
    if state["position"] or side not in (-1, 1):
        raise ValueError("A flat account and a LONG or SHORT side are required.")
    rate = fee_rate(settings, backtest)
    margin = state["available_cash"] * settings["paper_trading"]["position_size_pct"] / 100 / (1 + rate)
    fee = margin * rate
    state["available_cash"] = max(0.0, state["available_cash"] - margin - fee)
    state["reserved_margin"] = margin
    state["position"] = side
    state["entry_price"] = price
    state["quantity"] = margin * settings["paper_trading"]["leverage"] / price
    state["fees_paid"] += fee
    state["entry_fee"] = fee
    return fee


def close_position(state, price, settings=None, backtest=False):
    settings = settings or load_config()
    if not state["position"]:
        raise ValueError("No position is open.")
    gross_pnl = unrealized_pnl(state, price)
    fee = price * state["quantity"] * fee_rate(settings, backtest)
    state["available_cash"] += state["reserved_margin"] + gross_pnl - fee
    state["realized_pnl"] += gross_pnl
    state["fees_paid"] += fee
    state.update({"reserved_margin": 0.0, "position": 0, "entry_price": 0.0, "quantity": 0.0, "entry_time": None, "entry_fee": 0.0})
    return gross_pnl, fee


def backtest(data, strategy, initial_cash=None, settings=None):
    settings = settings or load_config()
    initial_cash = initial_cash if initial_cash is not None else settings["backtest"]["initial_balance"]
    data = signal_frame(data, strategy, settings)
    state = empty_paper_state(initial_cash)
    trade_log, curve = [], []
    for row in data.itertuples():
        desired = int(row.signal)
        mark = float(row.close)
        if desired != state["position"] and state["position"]:
            slippage = settings["paper_trading"]["slippage_bps"] if settings["backtest"]["include_slippage"] else 0.0
            exit_price = mark * (1 - state["position"] * slippage / 10_000)
            gross_pnl, fee = close_position(state, exit_price, settings, backtest=True)
            trade_log.append({"time": row.time.isoformat(), "action": "CLOSE", "price": exit_price, "pnl": gross_pnl, "fee": fee})
        if desired and not state["position"]:
            slippage = settings["paper_trading"]["slippage_bps"] if settings["backtest"]["include_slippage"] else 0.0
            entry = mark * (1 + desired * slippage / 10_000)
            fee = open_position(state, desired, entry, settings, backtest=True)
            trade_log.append({"time": row.time.isoformat(), "action": "LONG" if desired == 1 else "SHORT", "price": entry, "pnl": 0.0, "fee": fee})
        snapshot = account_snapshot(state, mark)
        curve.append({"time": row.time.isoformat(), "equity": snapshot["equity"], "price": mark, "position": state["position"]})
    return {"strategy": strategy, "metrics": metrics([p["equity"] for p in curve], trade_log, initial_cash), "equity_curve": curve, "trades": trade_log}


def save_backtests(days=None):
    settings = load_config()
    days = days or settings["backtest"]["window_days"]
    data = candles(days, settings["backtest"]["candle_granularity_seconds"])
    results = {name: backtest(data, name, settings=settings) for name in ("sma", "ema", "momentum", "breakout") if settings["strategies"][name]["enabled"]}
    RESULTS_FILE.write_text(json.dumps({"generated_at": utc_now().isoformat(), "days": days, "results": results}, indent=2), encoding="utf-8")
    for name, result in results.items():
        print(name, result["metrics"])


def load_paper():
    if PAPER_FILE.exists():
        state = json.loads(PAPER_FILE.read_text(encoding="utf-8"))
        required = {"starting_balance", "available_cash", "reserved_margin", "position", "entry_price", "quantity", "realized_pnl", "fees_paid", "strategy", "last_price"}
        if required.issubset(state):
            for key, value in empty_paper_state(state["starting_balance"]).items():
                state.setdefault(key, value)
            if state["position"] and not state["entry_time"] and PAPER_TRADES_FILE.exists():
                with PAPER_TRADES_FILE.open(newline="", encoding="utf-8") as file:
                    events = list(csv.DictReader(file))
                expected_action = "LONG" if state["position"] == 1 else "SHORT"
                entry_event = next((event for event in reversed(events) if event.get("action") == expected_action), None)
                if entry_event:
                    state["entry_time"] = entry_event.get("timestamp")
                    state["entry_fee"] = float(entry_event.get("fee") or 0.0)
            state["available_cash"] = max(0.0, state["available_cash"])
            return state
        print("Resetting incomplete or legacy paper portfolio for correct 1x accounting.")
        for path in (PAPER_TRADES_FILE, EQUITY_FILE):
            if path.exists():
                path.replace(path.with_suffix(".legacy.csv"))
    return empty_paper_state()


def append_csv(path, columns, row):
    new = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        if new:
            writer.writeheader()
        writer.writerow(row)


def paper_step(strategy=None):
    settings = load_config()
    strategy = strategy or settings["strategies"]["active_strategy"]
    data = signal_frame(candles(3, settings["backtest"]["candle_granularity_seconds"]), strategy, settings)
    latest = data.iloc[-1]
    signal, mark = int(latest.signal), float(latest.close)
    if signal == 1 and not settings["risk_management"]["allow_long"]:
        signal = 0
    if signal == -1 and not settings["risk_management"]["allow_short"]:
        signal = 0
    state = load_paper()
    if state["strategy"] not in (None, strategy):
        raise RuntimeError("Close/reset the paper portfolio before changing strategy.")
    position = int(state["position"])
    risk = settings["risk_management"]
    closed_this_step = False
    exit_reason = None
    if position and stop_loss_hit(state["entry_price"], position, mark, settings):
        exit_reason = f"{risk['stop_loss_pct']:g}% stop loss"
    if not exit_reason and position and signal != position and risk["close_on_opposite_signal"]:
        exit_reason = "SMA crossover" if strategy == "sma" else "Opposite strategy signal"
    if exit_reason:
        exit_time = utc_now()
        exit_price = mark * (1 - position * settings["paper_trading"]["slippage_bps"] / 10_000)
        quantity = state["quantity"]
        entry_time = state.get("entry_time")
        entry_price = state["entry_price"]
        entry_fee = state.get("entry_fee", 0.0)
        gross_pnl, fee = close_position(state, exit_price, settings)
        closed_this_step = True
        append_csv(PAPER_TRADES_FILE, ["timestamp", "action", "price", "quantity", "realized_pnl", "fee", "strategy"], {"timestamp": exit_time.isoformat(), "action": "CLOSE", "price": exit_price, "quantity": quantity, "realized_pnl": gross_pnl, "fee": fee, "strategy": strategy})
        append_csv(TRADE_HISTORY_FILE, ["entry_time", "exit_time", "side", "entry_price", "exit_price", "quantity", "fees", "realized_pnl", "holding_time", "exit_reason"], {"entry_time": entry_time or "Unknown (legacy position)", "exit_time": exit_time.isoformat(), "side": "LONG" if position == 1 else "SHORT", "entry_price": entry_price, "exit_price": exit_price, "quantity": quantity, "fees": entry_fee + fee, "realized_pnl": gross_pnl - entry_fee - fee, "holding_time": str(exit_time - datetime.fromisoformat(entry_time)) if entry_time else "Unknown", "exit_reason": exit_reason})
        state["current_action"] = f"Closed {'LONG' if position == 1 else 'SHORT'} — {exit_reason}"
    stopped_out = exit_reason is not None and "stop loss" in exit_reason
    can_open = not closed_this_step or (risk["flip_positions_automatically"] and not stopped_out)
    if signal and not state["position"] and can_open:
        entry = mark * (1 + signal * settings["paper_trading"]["slippage_bps"] / 10_000)
        fee = open_position(state, signal, entry, settings)
        state["entry_time"] = utc_now().isoformat()
        state["strategy"] = strategy
        append_csv(PAPER_TRADES_FILE, ["timestamp", "action", "price", "quantity", "realized_pnl", "fee", "strategy"], {"timestamp": utc_now().isoformat(), "action": "LONG" if signal == 1 else "SHORT", "price": entry, "quantity": state["quantity"], "realized_pnl": 0.0, "fee": fee, "strategy": strategy})
        state["current_action"] = f"Opened {'LONG' if signal == 1 else 'SHORT'}"
    elif not closed_this_step:
        state["current_action"] = f"Hold {'LONG' if state['position'] == 1 else 'SHORT'}" if state["position"] else "Stay flat"
    state["current_signal"] = signal
    state["fast_sma"] = float(latest.fast_sma) if strategy == "sma" else None
    state["slow_sma"] = float(latest.slow_sma) if strategy == "sma" else None
    if state["position"]:
        stop_price = stop_loss_price(state["entry_price"], state["position"], settings)
        stop_text = f"BTC closes {'at or below' if state['position'] == 1 else 'at or above'} ${stop_price:,.2f} ({risk['stop_loss_pct']:g}% stop loss)"
        crossover = "the fast SMA crosses at or below the slow SMA" if state["position"] == 1 else "the fast SMA crosses above the slow SMA"
        state["next_exit_condition"] = f"Exit when {stop_text}, or when {crossover}."
    else:
        state["next_exit_condition"] = "No exit condition while flat; wait for the next SMA entry signal."
    state["last_updated"] = utc_now().isoformat()
    state["last_price"] = mark
    snapshot = account_snapshot(state, mark)
    PAPER_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    append_csv(EQUITY_FILE, ["timestamp", "equity", "price", "position", "strategy"], {"timestamp": utc_now().isoformat(), "equity": snapshot["equity"], "price": mark, "position": state["position"], "strategy": strategy})
    print(f"{strategy}: signal={signal:+d}, BTC=${mark:,.2f}, equity=${snapshot['equity']:,.2f}")


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    back = sub.add_parser("backtest"); back.add_argument("--days", type=int); back.add_argument("--strategy", default="all", choices=["all", "sma", "ema", "momentum", "breakout"])
    paper = sub.add_parser("paper"); paper.add_argument("--strategy", choices=["sma", "ema", "momentum", "breakout"]); paper.add_argument("--interval", type=int); paper.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.command == "backtest":
        if args.strategy == "all": save_backtests(args.days)
        else:
            settings = load_config()
            days = args.days or settings["backtest"]["window_days"]
            result = backtest(candles(days, settings["backtest"]["candle_granularity_seconds"]), args.strategy, settings=settings)
            RESULTS_FILE.write_text(json.dumps({"generated_at": utc_now().isoformat(), "days": days, "results": {args.strategy: result}}, indent=2), encoding="utf-8")
            print(result["metrics"])
    else:
        while True:
            paper_step(args.strategy)
            if args.once: break
            interval = args.interval or load_config()["execution"]["poll_interval_seconds"]
            time.sleep(max(interval, 30))


if __name__ == "__main__":
    main()
