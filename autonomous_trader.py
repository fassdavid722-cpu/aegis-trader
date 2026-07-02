#!/usr/bin/env python3
"""
Aegis Autonomous Trader v2 — Groq AI Brain + Paper Trading Training Mode.

ARCHITECTURE CHANGE:
- Groq LLM IS the trader (makes all decisions based on live market data)
- Paper trading mode: simulates fills, no real money
- Scalping-first: 5min/15min timeframes, tight TP/SL
- Hybrid: takes swing trades on high-conviction setups
- Learns from every trade via in-context examples
- Performance gate: only goes live after 50+ demo trades with >52% win rate

Usage:
  python3 autonomous_trader.py              # one cycle
  python3 autonomous_trader.py --loop        # continuous 5-min loop
  python3 autonomous_trader.py --stats       # show training stats
"""
import os, sys, asyncio, json, sqlite3, signal
import urllib.request, urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Setup ──────────────────────────────────────────────────────────
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=True)

# Also load from .agents/.env if running from sandbox
agents_env = ROOT.parent.parent.parent / ".agents" / ".env"
if agents_env.exists():
    load_dotenv(agents_env, override=False)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "6472746064")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")

from database.connection import init_database, set_db_path
DB_PATH = ROOT / "data" / "journal.db"
set_db_path(DB_PATH)

# Scalping symbols (high liquidity)
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]
CYCLE_INTERVAL = 300  # 5 minutes

# ── Telegram ───────────────────────────────────────────────────────
def send_telegram(text: str) -> None:
    if not BOT_TOKEN:
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"
    }).encode()
    try:
        urllib.request.urlopen(url, data, timeout=10)
    except Exception as e:
        print(f"[Telegram] {e}")


# ── Fetch market data ──────────────────────────────────────────────
async def fetch_market_data(symbol: str) -> dict:
    """Fetch 5min + 15min candles + funding rate for a symbol."""
    from exchange.bitget_client import BitgetMarketClient
    client = BitgetMarketClient()

    ticker = await client.get_ticker(symbol)
    candles_5m = await client.get_candles(symbol, "5m", 100)
    candles_15m = await client.get_candles(symbol, "15m", 100)
    funding = await client.get_funding_rate(symbol)

    await client.close()

    return {
        "symbol": symbol,
        "ticker": ticker,
        "candles_5m": candles_5m,
        "candles_15m": candles_15m,
        "funding_rate": funding,
        "current_price": ticker.last_price if ticker else 0,
    }


async def fetch_all_prices(symbols: list[str]) -> dict[str, float]:
    """Get current prices for all symbols (for position monitoring)."""
    from exchange.bitget_client import BitgetMarketClient
    client = BitgetMarketClient()
    prices = {}
    for symbol in symbols:
        ticker = await client.get_ticker(symbol)
        if ticker:
            prices[symbol] = ticker.last_price
    await client.close()
    return prices


# ── Main trading cycle ─────────────────────────────────────────────
async def run_cycle() -> dict:
    """Run one complete trading cycle with Groq as the brain."""
    init_database(DB_PATH)
    utc_now = datetime.now(timezone.utc)
    print(f"\n[{utc_now.strftime('%H:%M UTC')}] === AEGIS GROQ TRADER (PAPER MODE) ===")

    # 1. Initialize Groq trader + paper engine
    from llm.groq_trader import GroqTrader
    from paper_trading.paper_engine import PaperEngine

    trader = GroqTrader()
    engine = PaperEngine()

    if not trader.available:
        print("[FATAL] Groq not available — check LLM_API_KEY")
        return {"error": "no_groq"}

    session, is_active = trader.get_session()
    print(f"[Session] {session} | Active: {is_active}")
    print(f"[Balance] ${engine.get_balance():,.2f}")

    # 2. Check existing positions (close at TP/SL)
    prices = await fetch_all_prices(SYMBOLS)
    closed = engine.check_positions(prices)
    if closed:
        print(f"[Monitor] {len(closed)} position(s) closed")
        # Run Groq self-review on each closed trade
        for trade in closed:
            try:
                review = trader.review_own_trade(trade)
                print(f"[Learn] {trade['symbol']}: {review.get('lesson', 'N/A')[:80]}")
            except Exception as e:
                print(f"[Learn] Review error: {e}")

    # 3. If in active session, scan for new trades
    new_trades = []
    if is_active:
        print(f"[Scan] Scanning {len(SYMBOLS)} symbols for setups...")

        # Get current open positions
        open_positions = engine.get_open_positions()
        open_symbols = {p["symbol"] for p in open_positions}

        for symbol in SYMBOLS:
            if symbol in open_symbols:
                print(f"[Scan] {symbol} already has open position — skip")
                continue

            try:
                data = await fetch_market_data(symbol)
                if not data["candles_5m"] or not data["candles_15m"]:
                    print(f"[Scan] {symbol}: insufficient data")
                    continue

                # Ask Groq to make a trading decision
                decision = trader.make_trading_decision(
                    symbol=symbol,
                    candles_5m=data["candles_5m"],
                    candles_15m=data["candles_15m"],
                    funding_rate=data["funding_rate"],
                    current_price=data["current_price"],
                    open_positions=open_positions,
                )

                if decision and decision.get("decision") == "TRADE":
                    # Execute in paper engine
                    result = engine.open_position(decision)
                    if result:
                        new_trades.append(result)
                        open_positions.append(result)
                        open_symbols.add(symbol)

            except Exception as e:
                print(f"[Scan] {symbol}: error: {e}")
                continue

    # 4. Summary
    stats = engine.get_performance_stats()
    print(f"\n[Summary] Session: {session} | New: {len(new_trades)} | Closed: {len(closed)}")
    print(f"[Stats] {stats['total_trades']} trades | {stats['win_rate']:.0f}% WR | "
          f"{stats['avg_r']:.2f} avg R | Balance: ${stats['balance']:,.2f}")

    if stats["ready_for_live"]:
        print("[GATE] ✅ Ready for LIVE trading! (50+ trades, >52% win rate)")
        send_telegram("🎯 PAPER TRAINING COMPLETE!\n\n"
                       f"Stats: {stats['total_trades']} trades | {stats['win_rate']:.0f}% WR | {stats['avg_r']:.2f} avg R\n"
                       f"Balance: ${stats['balance']:,.2f}\n\n"
                       f"Ready to switch to live trading. Set TRADING_MODE=LIVE to begin.")
    elif stats["total_trades"] > 0 and stats["total_trades"] % 10 == 0:
        # Every 10 trades, send a progress update
        send_telegram(
            f"📊 Training Progress: {stats['total_trades']}/{50} trades\n"
            f"Win rate: {stats['win_rate']:.0f}% (need 52%)\n"
            f"Avg R: {stats['avg_r']:.2f} | Total R: {stats['total_r']:.2f}\n"
            f"Balance: ${stats['balance']:,.2f}\n"
            f"Trades to live: {stats['trades_to_live']}"
        )

    return {
        "session": session,
        "is_active": is_active,
        "new_trades": len(new_trades),
        "closed_trades": len(closed),
        "balance": stats["balance"],
        "total_trades": stats["total_trades"],
        "win_rate": stats["win_rate"],
        "ready_for_live": stats["ready_for_live"],
    }


async def loop():
    """Continuous loop mode."""
    running = True

    def handle_signal(sig, frame):
        nonlocal running
        running = False
        print("\n[Shutdown] Graceful stop...")

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    from llm.groq_trader import GroqTrader
    from paper_trading.paper_engine import PaperEngine

    init_database(DB_PATH)
    trader = GroqTrader()
    engine = PaperEngine()

    stats = engine.get_performance_stats()
    send_telegram(
        f"🤖 Aegis Groq Trader Started (PAPER MODE)\n\n"
        f"Brain: Groq llama-3.3-70b\n"
        f"Style: Scalper-first hybrid\n"
        f"Balance: ${stats['balance']:,.2f}\n"
        f"Trades: {stats['total_trades']}/50 to live\n"
        f"Symbols: {', '.join(SYMBOLS)}\n"
        f"Sessions: London 07-11 UTC | NY 13-17 UTC\n"
        f"Mode: Training (no real money)"
    )

    while running:
        try:
            await run_cycle()
        except Exception as e:
            print(f"[FATAL] {e}")
            import traceback
            traceback.print_exc()

        if running:
            print(f"[Loop] Next cycle in {CYCLE_INTERVAL}s...")
            await asyncio.sleep(CYCLE_INTERVAL)

    send_telegram("🛑 Aegis Trader stopped")


def show_stats():
    """Show current training statistics."""
    init_database(DB_PATH)
    from paper_trading.paper_engine import PaperEngine
    from llm.groq_trader import GroqTrader

    engine = PaperEngine()
    trader = GroqTrader()
    stats = engine.get_performance_stats()

    print("\n" + "="*50)
    print("  AEGIS GROQ TRADER — TRAINING STATS")
    print("="*50)
    print(f"  Mode: PAPER (training)")
    print(f"  Balance: ${stats['balance']:,.2f}")
    print(f"  Total trades: {stats['total_trades']}")
    print(f"  Wins: {stats['wins']} | Losses: {stats['losses']}")
    print(f"  Win rate: {stats['win_rate']:.1f}%")
    print(f"  Avg R: {stats['avg_r']:.2f}")
    print(f"  Total R: {stats['total_r']:.2f}")
    print(f"  Trades to live: {stats['trades_to_live']}")
    print(f"  Win rate gap: {stats['win_rate_gap']:.1f}%")
    print(f"  Ready for live: {'✅ YES' if stats['ready_for_live'] else '❌ Not yet'}")
    print(f"  Groq available: {'✅' if trader.available else '❌'}")
    print("="*50)


def main():
    if "--stats" in sys.argv:
        show_stats()
    elif "--loop" in sys.argv:
        asyncio.run(loop())
    else:
        result = asyncio.run(run_cycle())
        if "error" not in result:
            s = result
            print(f"\n✅ Cycle done: {s['session']} | New: {s['new_trades']} | "
                  f"Closed: {s['closed_trades']} | Balance: ${s['balance']:,.2f} | "
                  f"Total: {s['total_trades']} trades | WR: {s['win_rate']:.0f}%")


if __name__ == "__main__":
    main()
