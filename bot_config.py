"""Validated, hot-reloadable configuration for the BTC paper bot."""
import copy
import json
from pathlib import Path

CONFIG_FILE = Path("config.json")

DEFAULT_CONFIG = {
    "paper_trading": {"starting_balance": 10000.0, "leverage": 1.0, "position_size_pct": 10.0, "maker_fee_bps": 5.0, "taker_fee_bps": 5.0, "slippage_bps": 2.0, "max_open_positions": 1},
    "risk_management": {"enable_stop_loss": True, "stop_loss_pct": 1.0, "enable_take_profit": False, "take_profit_pct": 0.0, "enable_trailing_stop": False, "trailing_stop_pct": 0.0, "maximum_daily_loss": 0.0, "maximum_drawdown_pct": 0.0, "allow_long": True, "allow_short": True, "close_on_opposite_signal": True, "flip_positions_automatically": True},
    "execution": {"poll_interval_seconds": 60, "order_type": "taker"},
    "strategies": {"active_strategy": "sma", "sma": {"enabled": True, "fast_period": 12, "slow_period": 36}, "ema": {"enabled": False, "fast_period": 12, "slow_period": 26}, "momentum": {"enabled": True, "lookback": 12, "entry_threshold_pct": 0.0}, "breakout": {"enabled": True, "lookback": 24, "breakout_buffer_pct": 0.0}},
    "backtest": {"initial_balance": 10000.0, "include_fees": True, "include_slippage": True, "candle_granularity_seconds": 3600, "window_days": 30},
    "dashboard": {"refresh_interval_seconds": 5, "chart_history_length": 300, "default_chart_timeframe": "1h", "default_strategy_shown": "sma"},
}

_cached_config = None
_cached_mtime = None


def _merge(defaults, supplied):
    result = copy.deepcopy(defaults)
    for key, value in supplied.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def _validate(config):
    paper, backtest, strategies = config["paper_trading"], config["backtest"], config["strategies"]
    if paper["starting_balance"] <= 0 or backtest["initial_balance"] <= 0:
        raise ValueError("starting balances must be positive")
    if paper["leverage"] <= 0 or not 0 < paper["position_size_pct"] <= 100:
        raise ValueError("leverage must be positive and position_size_pct must be in (0, 100]")
    if paper["maker_fee_bps"] < 0 or paper["taker_fee_bps"] < 0 or paper["slippage_bps"] < 0:
        raise ValueError("fees and slippage cannot be negative")
    risk = config["risk_management"]
    if risk["enable_stop_loss"] and not 0 < risk["stop_loss_pct"] < 100:
        raise ValueError("enabled stop_loss_pct must be in (0, 100)")
    if config["execution"]["order_type"] not in {"maker", "taker"}:
        raise ValueError("execution.order_type must be maker or taker")
    active = strategies["active_strategy"]
    if active not in {"sma", "ema", "momentum", "breakout"}:
        raise ValueError("strategies.active_strategy must be sma, ema, momentum, or breakout")
    if active not in strategies:
        raise ValueError("active strategy must have a configuration section")
    return config


def load_config(force=False):
    """Return config; reload it when config.json is edited. Missing keys use defaults."""
    global _cached_config, _cached_mtime
    mtime = CONFIG_FILE.stat().st_mtime_ns if CONFIG_FILE.exists() else None
    if not force and _cached_config is not None and mtime == _cached_mtime:
        return _cached_config
    supplied = {}
    if CONFIG_FILE.exists():
        try:
            supplied = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ValueError(f"Invalid config.json: {error}") from error
    _cached_config = _validate(_merge(DEFAULT_CONFIG, supplied))
    _cached_mtime = mtime
    return _cached_config
