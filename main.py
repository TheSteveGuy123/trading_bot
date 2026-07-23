"""Launch the BTC paper-trading bot and its Streamlit dashboard."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
import webbrowser
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start the paper trader and dashboard.")
    parser.add_argument("--port", type=int, default=8501, help="Dashboard port (default: 8501)")
    parser.add_argument("--strategy", choices=("sma", "ema", "momentum", "breakout"))
    parser.add_argument("--bot-only", action="store_true", help="Start only the trading bot")
    parser.add_argument("--dashboard-only", action="store_true", help="Start only the dashboard")
    parser.add_argument("--no-browser", action="store_true", help="Do not open the dashboard in a browser")
    args = parser.parse_args()
    if args.bot_only and args.dashboard_only:
        parser.error("--bot-only and --dashboard-only cannot be used together")
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    return args


def stop_processes(processes: list[subprocess.Popen]) -> None:
    for process in processes:
        if process.poll() is None:
            process.terminate()
    for process in processes:
        if process.poll() is None:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def main() -> int:
    args = parse_args()
    processes: list[subprocess.Popen] = []

    try:
        if not args.dashboard_only:
            bot_command = [sys.executable, "-u", "btc_perp_bot.py", "paper"]
            if args.strategy:
                bot_command.extend(("--strategy", args.strategy))
            processes.append(subprocess.Popen(bot_command, cwd=APP_DIR))
            print("Started BTC perpetual paper trader.")

        if not args.bot_only:
            dashboard_command = [
                sys.executable,
                "-m",
                "streamlit",
                "run",
                "dashboard.py",
                "--server.port",
                str(args.port),
                "--server.headless",
                "true",
            ]
            processes.append(subprocess.Popen(dashboard_command, cwd=APP_DIR))
            dashboard_url = f"http://localhost:{args.port}"
            print(f"Started dashboard at {dashboard_url}")
            if not args.no_browser:
                time.sleep(2)
                webbrowser.open(dashboard_url)

        print("Press Ctrl+C to stop the program.")
        while True:
            for process in processes:
                return_code = process.poll()
                if return_code is not None:
                    print(f"A program component exited with code {return_code}.", file=sys.stderr)
                    return return_code or 1
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping program...")
        return 0
    finally:
        stop_processes(processes)


if __name__ == "__main__":
    raise SystemExit(main())
