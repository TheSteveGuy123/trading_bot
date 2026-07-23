import json
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from bot_config import load_config
from btc_perp_bot import candles, signal_frame

PORTFOLIO_FILE = Path("multi_market_portfolio.json")
TRADES_FILE = Path("paper_trades.csv")
MONITORED_FILE = Path("monitored_markets.json")
REFERENCE_PRICES_FILE = Path("crypto_reference_prices.csv")
PERP_PORTFOLIO_FILE = Path("perp_paper_portfolio.json")
BTC_PERP_RESULTS_FILE = Path("btc_perp_backtests.json")
BTC_PERP_PAPER_FILE = Path("btc_perp_paper_portfolio.json")
BTC_PERP_EQUITY_FILE = Path("btc_perp_equity.csv")
BTC_PERP_TRADES_FILE = Path("btc_perp_paper_trades.csv")
BTC_PERP_TRADE_HISTORY_FILE = Path("btc_perp_trade_history.csv")
TAKE_PROFIT_MULTIPLIER = 1.25
COINBASE_PRODUCTS = {
    "BTCUSDT": "BTC-USD",
    "ETHUSDT": "ETH-USD",
    "SOLUSDT": "SOL-USD",
    "XRPUSDT": "XRP-USD",
}


st.set_page_config(
    page_title="Paper Trading Dashboard",
    layout="wide",
)

dashboard_settings = load_config()["dashboard"]
st_autorefresh(interval=dashboard_settings["refresh_interval_seconds"] * 1000, key="dashboard_refresh")


def load_portfolio():
    if not PORTFOLIO_FILE.exists():
        return {"cash": 1000.0, "positions": {}}

    with open(PORTFOLIO_FILE, encoding="utf-8") as file:
        return json.load(file)


def load_monitored_markets():
    if not MONITORED_FILE.exists():
        return []

    with open(MONITORED_FILE, encoding="utf-8") as file:
        return json.load(file).get("markets", [])


def load_perp_portfolio():
    if not PERP_PORTFOLIO_FILE.exists():
        return {"cash": 1000.0, "positions": {}}

    with open(PERP_PORTFOLIO_FILE, encoding="utf-8") as file:
        return json.load(file)


def load_reference_prices():
    if not REFERENCE_PRICES_FILE.exists():
        return pd.DataFrame(columns=["timestamp", "symbol", "price"])

    try:
        prices = pd.read_csv(REFERENCE_PRICES_FILE)
    except pd.errors.EmptyDataError:
        return pd.DataFrame(columns=["timestamp", "symbol", "price"])

    prices["timestamp"] = pd.to_datetime(prices["timestamp"], utc=True)
    prices["price"] = pd.to_numeric(prices["price"])
    return prices


@st.cache_data(ttl=5)
def get_best_bid(token_id):
    response = requests.get(
        "https://clob.polymarket.com/price",
        params={
            "token_id": token_id,
            "side": "SELL",
        },
        timeout=10,
    )
    response.raise_for_status()

    return float(response.json()["price"])


@st.cache_data(ttl=60)
def get_price_history(token_id):
    response = requests.get(
        "https://clob.polymarket.com/prices-history",
        params={
            "market": token_id,
            "interval": "1d",
            "fidelity": 5,
        },
        timeout=10,
    )
    response.raise_for_status()

    history = response.json().get("history", [])

    if not history:
        return pd.DataFrame(columns=["time", "price"])

    data = pd.DataFrame(history)
    data["time"] = pd.to_datetime(data["t"], unit="s", utc=True)
    data["price"] = pd.to_numeric(data["p"])

    return data[["time", "price"]]


@st.cache_data(ttl=5)
def get_perp_reference_price(symbol):
    product_id = COINBASE_PRODUCTS[symbol]
    response = requests.get(
        f"https://api.exchange.coinbase.com/products/{product_id}/ticker",
        timeout=10,
    )
    response.raise_for_status()
    return float(response.json()["price"])


@st.cache_data(ttl=60)
def get_perp_reference_history(symbol):
    product_id = COINBASE_PRODUCTS[symbol]
    try:
        response = requests.get(
            f"https://api.exchange.coinbase.com/products/{product_id}/candles",
            params={"granularity": 300},
            timeout=10,
        )
        response.raise_for_status()
        candles = response.json()
    except (requests.RequestException, TypeError, ValueError):
        return pd.DataFrame(columns=["time", "price"])

    if not candles:
        return pd.DataFrame(columns=["time", "price"])

    return pd.DataFrame({
        "time": pd.to_datetime([candle[0] for candle in candles], unit="s", utc=True),
        "price": [float(candle[4]) for candle in candles],
    }).sort_values("time")


@st.cache_data(ttl=60)
def get_btc_sma_history(days, granularity, fast_period, slow_period):
    settings = load_config()
    settings["strategies"]["sma"]["fast_period"] = fast_period
    settings["strategies"]["sma"]["slow_period"] = slow_period
    return signal_frame(candles(days, granularity), "sma", settings)


st.title("Paper Trading Dashboard")
st.caption("Simulation only — no real trades or wallet access.")

portfolio = load_portfolio()
positions = portfolio["positions"]
monitored_markets = load_monitored_markets()
reference_prices = load_reference_prices()
perp_portfolio = load_perp_portfolio()

position_rows = []
position_value = 0.0

for token_id, position in positions.items():
    try:
        current_bid = get_best_bid(token_id)
    except requests.RequestException:
        current_bid = None

    shares = position["shares"]
    current_value = shares * current_bid if current_bid else 0.0
    position_value += current_value

    position_rows.append({
        "token_id": token_id,
        "market": position["market_name"],
        "outcome": position["outcome"],
        "shares": shares,
        "entry_price": position["entry_price"],
        "sell_target": (
            position["entry_price"]
            * TAKE_PROFIT_MULTIPLIER
        ),
        "current_bid": current_bid,
        "current_value": current_value,
    })

account_value = portfolio["cash"] + position_value

cash_column, value_column, positions_column = st.columns(3)

cash_column.metric(
    "Virtual cash",
    f"${portfolio['cash']:.2f}",
)

value_column.metric(
    "Estimated account value",
    f"${account_value:.2f}",
)

positions_column.metric(
    "Open positions",
    len(position_rows),
)

st.subheader("Live crypto reference prices")

if reference_prices.empty:
    st.info("Start crypto_reference_feed.py to load the BTC, ETH, SOL, and XRP chart data.")
else:
    latest_reference_prices = (
        reference_prices.sort_values("timestamp")
        .groupby("symbol", as_index=False)
        .tail(1)
        .sort_values("symbol")
    )

    st.dataframe(
        latest_reference_prices[["symbol", "price", "timestamp"]],
        width="stretch",
        hide_index=True,
        column_config={
            "price": st.column_config.NumberColumn(
                "Current price",
                format="$%,.2f",
            ),
        },
    )

    selected_symbol = st.selectbox(
        "Crypto price chart",
        options=sorted(reference_prices["symbol"].unique()),
    )

    selected_prices = reference_prices[
        reference_prices["symbol"] == selected_symbol
    ].sort_values("timestamp")

    reference_figure = go.Figure()
    reference_figure.add_trace(
        go.Scatter(
            x=selected_prices["timestamp"],
            y=selected_prices["price"],
            mode="lines",
            name=selected_symbol,
        )
    )
    reference_figure.update_layout(
        height=360,
        yaxis_title="Reference price (USD)",
        xaxis_title="Time",
        yaxis_tickprefix="$",
        yaxis_tickformat=",.2f",
    )
    st.plotly_chart(reference_figure, width="stretch")

st.subheader("Paper perp positions (1x simulation; Coinbase spot reference)")

perp_rows = []

for symbol, position in perp_portfolio["positions"].items():
    try:
        current_mark = get_perp_reference_price(symbol)
    except requests.RequestException:
        current_mark = None

    direction = 1 if position["side"] == "LONG" else -1
    unrealized_pnl = (
        direction * (current_mark - position["entry_price"]) * position["quantity"]
        if current_mark is not None
        else None
    )

    perp_rows.append({
        "symbol": symbol,
        "side": position["side"],
        "entry_price": position["entry_price"],
        "current_reference": current_mark,
        "quantity": position["quantity"],
        "unrealized_pnl": unrealized_pnl,
    })

if not perp_rows:
    st.info("No paper perp positions. Use perp_paper_trader.py to open a simulated long or short.")
else:
    perp_frame = pd.DataFrame(perp_rows)
    st.dataframe(
        perp_frame,
        width="stretch",
        hide_index=True,
        column_config={
            "entry_price": st.column_config.NumberColumn("Entry", format="$%,.2f"),
            "current_reference": st.column_config.NumberColumn("Reference price", format="$%,.2f"),
            "quantity": st.column_config.NumberColumn("Quantity", format="%.6f"),
            "unrealized_pnl": st.column_config.NumberColumn("Unrealized P&L", format="$%,.2f"),
        },
    )

    selected_perp = st.selectbox(
        "Perp reference-price chart",
        options=perp_frame["symbol"].tolist(),
    )
    selected_position = perp_portfolio["positions"][selected_perp]
    perp_history = get_perp_reference_history(selected_perp)

    perp_figure = go.Figure()
    perp_figure.add_trace(
        go.Scatter(
            x=perp_history["time"],
            y=perp_history["price"],
            mode="lines",
            name="Reference price",
        )
    )
    perp_figure.add_hline(
        y=selected_position["entry_price"],
        line_dash="dash",
        annotation_text=f"Paper entry: ${selected_position['entry_price']:,.2f}",
    )
    perp_figure.update_layout(
        height=360,
        yaxis_title="Reference price (USD)",
        xaxis_title="Time",
        yaxis_tickprefix="$",
        yaxis_tickformat=",.2f",
    )
    st.plotly_chart(perp_figure, width="stretch")

st.subheader("Crypto markets being monitored")

if monitored_markets:
    monitored_frame = pd.DataFrame(monitored_markets)
    st.dataframe(
        monitored_frame[["market_name", "outcome"]],
        width="stretch",
        hide_index=True,
    )
else:
    st.info("Restart the paper bot to publish its current crypto watchlist.")

st.subheader("Open paper positions")

if not position_rows:
    st.info("No open paper positions yet.")
else:
    positions_frame = pd.DataFrame(position_rows)

    st.dataframe(
        positions_frame[
            [
                "market",
                "outcome",
                "shares",
                "entry_price",
                "sell_target",
                "current_bid",
                "current_value",
            ]
        ],
        width="stretch",
        hide_index=True,
        column_config={
            "entry_price": st.column_config.NumberColumn(
                "Bought at",
                format="$%.3f",
            ),
            "sell_target": st.column_config.NumberColumn(
                "Auto-sell target",
                format="$%.3f",
            ),
            "current_bid": st.column_config.NumberColumn(
                "Current best bid",
                format="$%.3f",
            ),
            "current_value": st.column_config.NumberColumn(
                "Current value",
                format="$%.2f",
            ),
            "shares": st.column_config.NumberColumn(
                "Shares",
                format="%.2f",
            ),
        },
    )

    st.subheader("Price chart")

    selected_token = st.selectbox(
        "Choose an open position",
        options=[row["token_id"] for row in position_rows],
        format_func=lambda token_id: (
            f"{positions[token_id]['market_name']} — "
            f"{positions[token_id]['outcome']}"
        ),
    )

    selected_position = positions[selected_token]
    history = get_price_history(selected_token)

    if history.empty:
        st.warning("No historical price data is available for this outcome.")
    else:
        entry_price = selected_position["entry_price"]
        sell_target = entry_price * TAKE_PROFIT_MULTIPLIER

        figure = go.Figure()

        figure.add_trace(
            go.Scatter(
                x=history["time"],
                y=history["price"],
                mode="lines",
                name="Market price",
            )
        )

        figure.add_hline(
            y=entry_price,
            line_dash="dash",
            annotation_text=f"Paper buy: ${entry_price:.3f}",
        )

        figure.add_hline(
            y=sell_target,
            line_dash="dash",
            annotation_text=f"Auto-sell target: ${sell_target:.3f}",
        )

        figure.update_layout(
            height=450,
            yaxis_title="Outcome price",
            xaxis_title="Time",
            yaxis_tickformat=".3f",
        )

        st.plotly_chart(
            figure,
            width="stretch",
        )

st.subheader("Paper-trade history")

if TRADES_FILE.exists():
    trades = pd.read_csv(TRADES_FILE)

    st.dataframe(
        trades.sort_values("timestamp", ascending=False),
        width="stretch",
        hide_index=True,
    )
else:
    st.info("No paper trades have been logged yet.")

st.divider()
st.header("BTC perpetual research & paper trading")
st.caption("Simulated BTC perpetuals using Coinbase BTC-USD reference candles. No live orders or account credentials are used.")

if not BTC_PERP_RESULTS_FILE.exists():
    st.info("Generate comparisons with: python btc_perp_bot.py backtest --days 30 --strategy all")
else:
    try:
        btc_results = json.loads(BTC_PERP_RESULTS_FILE.read_text(encoding="utf-8"))
        comparison_rows = [
            {"strategy": name, **result["metrics"]}
            for name, result in btc_results.get("results", {}).items()
        ]
        comparison = pd.DataFrame(comparison_rows)
        st.subheader("Strategy comparison")
        st.caption(f"Backtest generated {btc_results.get('generated_at', 'unknown')} over {btc_results.get('days', 'unknown')} days.")
        st.dataframe(
            comparison,
            width="stretch",
            hide_index=True,
            column_config={
                "pnl": st.column_config.NumberColumn("P&L", format="$%,.2f"),
                "return_pct": st.column_config.NumberColumn("Return", format="%.2f%%"),
                "win_rate": st.column_config.NumberColumn("Win rate", format="%.2f%%"),
                "sharpe_ratio": st.column_config.NumberColumn("Sharpe ratio", format="%.2f"),
                "maximum_drawdown_pct": st.column_config.NumberColumn("Max drawdown", format="%.2f%%"),
            },
        )
        chart = go.Figure()
        for name, result in btc_results.get("results", {}).items():
            curve = pd.DataFrame(result.get("equity_curve", []))
            if not curve.empty:
                curve["time"] = pd.to_datetime(curve["time"], utc=True)
                curve = curve.tail(dashboard_settings["chart_history_length"])
                chart.add_trace(go.Scatter(x=curve["time"], y=curve["equity"], mode="lines", name=name))
        chart.update_layout(height=380, yaxis_title="Equity (USD)", xaxis_title="Time", yaxis_tickprefix="$", yaxis_tickformat=",.0f")
        st.subheader("Backtest equity curves")
        st.plotly_chart(chart, width="stretch")
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        st.warning(f"Could not read BTC perp backtest results: {error}")

st.subheader("Live paper-trading status")
if not BTC_PERP_PAPER_FILE.exists():
    st.info("Start one cycle with: python btc_perp_bot.py paper --strategy sma --once")
else:
    paper_state = json.loads(BTC_PERP_PAPER_FILE.read_text(encoding="utf-8"))
    live_curve = pd.DataFrame()
    if BTC_PERP_EQUITY_FILE.exists():
        live_curve = pd.read_csv(BTC_PERP_EQUITY_FILE)
        if not live_curve.empty:
            live_curve["timestamp"] = pd.to_datetime(live_curve["timestamp"], utc=True)
    current_price = paper_state.get("last_price")
    if current_price is None and not live_curve.empty:
        current_price = float(live_curve.iloc[-1]["price"])
    if current_price is None:
        current_price = float(paper_state.get("entry_price", 0.0))
    position = int(paper_state.get("position", 0))
    unrealized = position * (current_price - paper_state.get("entry_price", 0.0)) * paper_state.get("quantity", 0.0)
    realized = paper_state.get("realized_pnl", 0.0)
    fees = paper_state.get("fees_paid", 0.0)
    total_pnl = realized + unrealized - fees
    equity = paper_state.get("starting_balance", 10_000.0) + total_pnl
    side = {1: "LONG", -1: "SHORT", 0: "FLAT"}.get(int(paper_state.get("position", 0)), "FLAT")
    live_trades = pd.read_csv(BTC_PERP_TRADES_FILE) if BTC_PERP_TRADES_FILE.exists() else pd.DataFrame()
    trade_history = pd.read_csv(BTC_PERP_TRADE_HISTORY_FILE) if BTC_PERP_TRADE_HISTORY_FILE.exists() else pd.DataFrame()
    closed = live_trades[live_trades["action"] == "CLOSE"] if not live_trades.empty else pd.DataFrame()
    winning = int(((closed["realized_pnl"] - closed["fee"]) > 0).sum()) if not closed.empty else 0
    losing = int(((closed["realized_pnl"] - closed["fee"]) <= 0).sum()) if not closed.empty else 0
    columns = st.columns(4)
    columns[0].metric("Starting balance", f"${paper_state.get('starting_balance', 10_000):,.2f}")
    columns[1].metric("Available cash", f"${paper_state.get('available_cash', 0):,.2f}")
    columns[2].metric("Reserved margin", f"${paper_state.get('reserved_margin', 0):,.2f}")
    columns[3].metric("Position side", side)
    position_columns = st.columns(4)
    position_columns[0].metric("Entry price", f"${paper_state.get('entry_price', 0):,.2f}")
    position_columns[1].metric("Current price", f"${current_price:,.2f}")
    position_columns[2].metric("Position quantity", f"{paper_state.get('quantity', 0):.6f} BTC")
    position_columns[3].metric("Position notional", f"${current_price * paper_state.get('quantity', 0):,.2f}")
    pnl_columns = st.columns(4)
    pnl_columns[0].metric("Realized P&L", f"${realized:,.2f}")
    pnl_columns[1].metric("Unrealized P&L", f"${unrealized:,.2f}")
    pnl_columns[2].metric("Fees paid", f"${fees:,.2f}")
    pnl_columns[3].metric("Total P&L", f"${total_pnl:,.2f}")
    summary_columns = st.columns(4)
    summary_columns[0].metric("Equity", f"${equity:,.2f}")
    summary_columns[1].metric("Closed trades", len(closed))
    summary_columns[2].metric("Winning trades", winning)
    summary_columns[3].metric("Losing trades", losing)

    st.subheader("Strategy Decision")
    fast_sma = paper_state.get("fast_sma")
    slow_sma = paper_state.get("slow_sma")
    signal_label = {1: "LONG", -1: "SHORT", 0: "NEUTRAL"}.get(int(paper_state.get("current_signal", 0)), "UNKNOWN")
    decision_columns = st.columns(5)
    decision_columns[0].metric("Current signal", signal_label)
    decision_columns[1].metric("Current action", paper_state.get("current_action", "Waiting for update"))
    decision_columns[2].metric("Fast SMA", f"${fast_sma:,.2f}" if fast_sma is not None else "N/A")
    decision_columns[3].metric("Slow SMA", f"${slow_sma:,.2f}" if slow_sma is not None else "N/A")
    sma_gap = fast_sma - slow_sma if fast_sma is not None and slow_sma is not None else None
    decision_columns[4].metric("SMA gap", f"${sma_gap:,.2f}" if sma_gap is not None else "N/A")
    st.info(paper_state.get("next_exit_condition", "No exit condition is available yet."))

    st.subheader("Live BTC price and SMA decision chart")
    sma_settings = load_config()
    fast_period = sma_settings["strategies"]["sma"]["fast_period"]
    slow_period = sma_settings["strategies"]["sma"]["slow_period"]
    try:
        btc_chart_data = get_btc_sma_history(
            3,
            sma_settings["backtest"]["candle_granularity_seconds"],
            fast_period,
            slow_period,
        ).tail(dashboard_settings["chart_history_length"])
    except (requests.RequestException, RuntimeError, ValueError, KeyError) as error:
        btc_chart_data = pd.DataFrame()
        st.warning(f"Live BTC chart is temporarily unavailable: {error}")
    if not btc_chart_data.empty:
        btc_figure = go.Figure()
        btc_figure.add_trace(go.Scatter(x=btc_chart_data["time"], y=btc_chart_data["close"], mode="lines", name="BTC close", line={"color": "#D6D9E0"}))
        btc_figure.add_trace(go.Scatter(x=btc_chart_data["time"], y=btc_chart_data["fast_sma"], mode="lines", name=f"Fast SMA ({fast_period})", line={"color": "#00CC96"}))
        btc_figure.add_trace(go.Scatter(x=btc_chart_data["time"], y=btc_chart_data["slow_sma"], mode="lines", name=f"Slow SMA ({slow_period})", line={"color": "#EF553B"}))
        marker_rows = []
        if not trade_history.empty:
            for trade in trade_history.itertuples(index=False):
                marker_rows.extend([
                    {"time": pd.to_datetime(trade.entry_time, utc=True, errors="coerce"), "price": trade.entry_price, "kind": "Open", "label": f"Open {trade.side}"},
                    {"time": pd.to_datetime(trade.exit_time, utc=True, errors="coerce"), "price": trade.exit_price, "kind": "Close", "label": f"Close: {trade.exit_reason}"},
                ])
        if position and paper_state.get("entry_time"):
            marker_rows.append({"time": pd.to_datetime(paper_state["entry_time"], utc=True, errors="coerce"), "price": paper_state["entry_price"], "kind": "Open", "label": f"Open {side}"})
        marker_frame = pd.DataFrame(marker_rows).dropna(subset=["time"]) if marker_rows else pd.DataFrame()
        for kind, symbol, color in (("Open", "triangle-up", "#00CC96"), ("Close", "x", "#EF553B")):
            points = marker_frame[marker_frame["kind"] == kind] if not marker_frame.empty else pd.DataFrame()
            if not points.empty:
                btc_figure.add_trace(go.Scatter(x=points["time"], y=points["price"], mode="markers", name=kind, text=points["label"], hovertemplate="%{text}<br>%{x}<br>$%{y:,.2f}<extra></extra>", marker={"symbol": symbol, "size": 12, "color": color}))
        btc_figure.update_layout(height=460, yaxis_title="BTC price (USD)", xaxis_title="Time", yaxis_tickprefix="$", yaxis_tickformat=",.2f", hovermode="x unified")
        st.plotly_chart(btc_figure, width="stretch")

    st.subheader("BTC trade history")
    if trade_history.empty:
        st.info("No completed BTC paper trades yet. Open and close markers will appear after trades execute.")
    else:
        display_history = trade_history.copy()
        display_history["entry_time"] = pd.to_datetime(display_history["entry_time"], utc=True, errors="coerce")
        display_history["exit_time"] = pd.to_datetime(display_history["exit_time"], utc=True, errors="coerce")
        st.dataframe(
            display_history.sort_values("exit_time", ascending=False),
            width="stretch",
            hide_index=True,
            column_config={
                "entry_price": st.column_config.NumberColumn("Entry price", format="$%,.2f"),
                "exit_price": st.column_config.NumberColumn("Exit price", format="$%,.2f"),
                "quantity": st.column_config.NumberColumn("Quantity", format="%.6f BTC"),
                "fees": st.column_config.NumberColumn("Fees", format="$%,.2f"),
                "realized_pnl": st.column_config.NumberColumn("Realized P&L", format="$%,.2f"),
            },
        )
    if not live_curve.empty:
        live_drawdown = live_curve["equity"] / live_curve["equity"].cummax() - 1
        live_win_rate = (winning / len(closed) * 100) if len(closed) else None
        live_returns = live_curve["equity"].pct_change().dropna()
        live_sharpe = None if len(closed) < 2 or len(live_returns) < 2 or live_returns.std() == 0 else float(live_returns.mean() / live_returns.std() * (365 * 24) ** 0.5)
        risk_columns = st.columns(4)
        risk_columns[0].metric("Win rate", f"{live_win_rate:.2f}%" if live_win_rate is not None else "N/A")
        risk_columns[1].metric("Sharpe ratio", f"{live_sharpe:.2f}" if live_sharpe is not None else "N/A")
        risk_columns[2].metric("Max drawdown", f"{live_drawdown.min() * 100:.2f}%")
        risk_columns[3].metric("Strategy", paper_state.get("strategy") or "Not set")
        live_curve = live_curve.tail(dashboard_settings["chart_history_length"])
        live_figure = go.Figure(go.Scatter(x=live_curve["timestamp"], y=live_curve["equity"], mode="lines", name="Paper equity"))
        live_figure.update_layout(height=320, yaxis_title="Equity (USD)", xaxis_title="Time", yaxis_tickprefix="$", yaxis_tickformat=",.2f")
        st.plotly_chart(live_figure, width="stretch")
    if not live_trades.empty:
        st.dataframe(live_trades.sort_values("timestamp", ascending=False), width="stretch", hide_index=True)
