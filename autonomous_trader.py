#!/usr/bin/env python3
"""
Aegis Autonomous Trader v2 — Groq AI Hunter + Paper Trading Training Mode.

The LLM IS the trader. It receives pre-processed market intelligence
(momentum, volume, patterns, key levels) and makes all decisions.

Scalping-first hybrid: 5min/15min, tight SL, quick TP, wider sessions.
Paper mode: $10,000 virtual balance, simulated slippage + fees, no real money.
Learning loop: reviews every closed trade, feeds lessons into future decisions.
Performance gate: 50+ demo trades at >52% win rate → go live.
"""
import os, sys, asyncio, json, sqlite3, signal
import urllib.request, urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=True)

agents_env = ROOT.parent.parent.parent / ".agents" / ".env"
if agents_env.exists():
    load_dotenv(agents_env, override=False)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "6472746064")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")

from database.connection import init_database, set_db_path
DB_PATH = ROOT / "data" / "journal.db"
set_db_path(DB_PATH)

# Scalping symbols — high liquidity only
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


# ── Market data ────────────────────────────────────────────────────
async def fetch_market_data(symbol: str, client=None) -> dict:
    """Fetch 5min + 15min candles + funding + order book + L/S ratio + taker flow.

    CRITICAL: Drops the last (still-forming) candle from both timeframes.
    The LLM and all indicators only see CLOSED candles — no look-ahead bias.
    Real-time price comes from the ticker, not a forming candle.
    """
    from exchange.bitget_client import BitgetMarketClient
    from datetime import datetime, timezone
    own_client = client is None
    if own_client:
        client = BitgetMarketClient()
    try:
        ticker = await client.get_ticker(symbol)
        candles_5m = await client.get_candles(symbol, "5m", 100)
        candles_15m = await client.get_candles(symbol, "15m", 100)
        funding = await client.get_funding_rate(symbol)
        orderbook = await client.get_orderbook(symbol)
        ls_ratio = await client.get_long_short_ratio(symbol)
    finally:
        if own_client:
            await client.close()

    # Drop forming candles — only feed CLOSED candles to the LLM/indicators.
    # The last candle is typically still forming (mid-period). Including it
    # gives the LLM a sneak peek at recent price action, making it describe
    # the past instead of predict the future. This is look-ahead bias.
    now = datetime.now(timezone.utc)
    if candles_5m and len(candles_5m) > 1:
        last_ts = candles_5m[-1].get("timestamp")
        if isinstance(last_ts, str):
            age_s = (now - datetime.fromisoformat(last_ts)).total_seconds()
        elif isinstance(last_ts, (int, float)):
            age_s = (now.timestamp() - last_ts / 1000)
        else:
            age_s = 999
        if age_s < 300:  # 5m candle still forming
            candles_5m = candles_5m[:-1]

    if candles_15m and len(candles_15m) > 1:
        last_ts = candles_15m[-1].get("timestamp")
        if isinstance(last_ts, str):
            age_s = (now - datetime.fromisoformat(last_ts)).total_seconds()
        elif isinstance(last_ts, (int, float)):
            age_s = (now.timestamp() - last_ts / 1000)
        else:
            age_s = 999
        if age_s < 900:  # 15m candle still forming
            candles_15m = candles_15m[:-1]

    return {
        "symbol": symbol,
        "ticker": ticker,
        "candles_5m": candles_5m,
        "candles_15m": candles_15m,
        "funding_rate": funding,
        "current_price": ticker.last_price if ticker else 0,
        "orderbook": orderbook,
        "ls_ratio": ls_ratio,
    }


async def fetch_all_prices(symbols: list[str]) -> dict[str, float]:
    """Get current prices for position monitoring."""
    from exchange.bitget_client import BitgetMarketClient
    client = BitgetMarketClient()
    prices = {}
    for symbol in symbols:
        ticker = await client.get_ticker(symbol)
        if ticker:
            prices[symbol] = ticker.last_price
    await client.close()
    return prices


# ── Main cycle ─────────────────────────────────────────────────────
async def run_cycle() -> dict:
    """One complete trading cycle — Groq brain + paper execution."""
    init_database(DB_PATH)
    utc_now = datetime.now(timezone.utc)
    print(f"\n[{utc_now.strftime('%H:%M UTC')}] ═══ AEGIS GROQ HUNTER (PAPER) ═══")

    from llm.groq_trader import GroqTrader
    from paper_trading.paper_engine import PaperEngine

    trader = GroqTrader()
    engine = PaperEngine()

    if not trader.available:
        print("[FATAL] Groq not available — check LLM_API_KEY")
        return {"error": "no_groq"}

    session, is_active, progress = trader.get_session()
    print(f"[Session] {session} ({progress*100:.0f}% in) | Active: {is_active}")
    print(f"[Balance] ${engine.get_balance():,.2f}")

    # 1. Check & close positions at TP/SL
    prices = await fetch_all_prices(SYMBOLS)

    # Also check for open positions on symbols we monitor
    open_positions = engine.get_open_positions()
    for pos in open_positions:
        if pos["symbol"] not in prices:
            prices[pos["symbol"]] = pos["entry_price"]  # fallback

    closed = engine.check_positions(prices)

    # 2. Close stale scalps (max 25 min hold)
    stale_closed = engine.close_stale_positions(prices, max_hold_minutes=25)
    closed.extend(stale_closed)

    if closed:
        print(f"[Monitor] {len(closed)} position(s) closed")
        # Groq self-review on each close
        for trade in closed:
            try:
                review = trader.review_own_trade(trade)
                lesson = review.get("lesson", "N/A")
                print(f"[Learn] {trade['symbol']}: {lesson[:80]}")
            except Exception as e:
                print(f"[Learn] Review error: {e}")

    # 3. Scan for new trades (only in active session)
    new_trades = []
    if is_active:
        print(f"[Hunt] Scanning for setups...")

        # Market regime check — top-down approach
        from analyst.regime_detector_v2 import detect_regime
        try:
            from exchange.bitget_client import BitgetMarketClient
            btc_client = BitgetMarketClient()
            btc_1h = await btc_client.get_candles("BTCUSDT", "1H", 50)
            btc_15m = await btc_client.get_candles("BTCUSDT", "15m", 50)
            await btc_client.close()
            market_regime = detect_regime(btc_1h, btc_15m)
            print(f"[Regime] {market_regime.regime} ({market_regime.strength}/10) — {market_regime.bias}")
            print(f"[Regime] {market_regime.description}")
        except Exception as e:
            print(f"[Regime] Detection failed: {e}")
            market_regime = None

        open_positions = engine.get_open_positions()
        open_symbols = {p["symbol"] for p in open_positions}

        # Fetch market context (BTC, breadth, funding) once for the cycle
        from analyst.market_context import fetch_market_context, SymbolContext
        from analyst.advanced_indicators import build_advanced_indicators
        from exchange.bitget_client import BitgetMarketClient

        ctx_client = BitgetMarketClient()
        funding_map = {}
        # We'll build this as we go
        symbol_contexts = {}
        market_ctx = None

        try:
            # Get all tickers for breadth
            all_tickers = await ctx_client.get_all_tickers()
            btc_ticker = all_tickers.get("BTCUSDT")
            btc_price = btc_ticker.last_price if btc_ticker else 0
            btc_change = btc_ticker.change_24h if btc_ticker else 0
            alt_tickers = {k: v for k, v in all_tickers.items() if k in SYMBOLS and k != "BTCUSDT"}
            breadth_up = sum(1 for t in alt_tickers.values() if t.change_24h > 0)
            breadth_down = sum(1 for t in alt_tickers.values() if t.change_24h <= 0)

            from analyst.market_context import MarketContext
            market_ctx = MarketContext(
                btc_price=btc_price,
                btc_change_24h=btc_change,
                btc_trend="UP" if btc_change > 1.0 else "DOWN" if btc_change < -1.0 else "FLAT",
                breadth_up=breadth_up,
                breadth_down=breadth_down,
                breadth_signal="RISK_ON" if breadth_up > breadth_down * 2 else "RISK_OFF" if breadth_down > breadth_up * 2 else "NEUTRAL",
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            print(f"[Market] BTC ${btc_price:,.0f} ({btc_change:+.2f}%) | Breadth: {breadth_up}up/{breadth_down}down — {market_ctx.breadth_signal}")
        except Exception as e:
            print(f"[Market] Context fetch failed: {e}")
            market_ctx = None

        # Rotate 2 symbols per cycle to stay within Groq's 100k daily token budget
        import time as _time
        cycle_idx = int(_time.time() // 300)  # changes every 5-min cycle
        n_scan = 2
        scan_list = [SYMBOLS[(cycle_idx * n_scan + i) % len(SYMBOLS)] for i in range(n_scan)]
        # Always include symbols with open positions for monitoring
        scan_list = list(set(scan_list + list(open_symbols)))
        print(f"[Hunt] Scanning {len(scan_list)} symbols: {', '.join(scan_list)}")

        for symbol in scan_list:
            if symbol in open_symbols and symbol not in scan_list[:n_scan]:
                continue  # monitor open positions but don't re-scan for new entries

            # Small delay between symbols to avoid Groq rate limiting
            if symbol != scan_list[0]:
                await asyncio.sleep(5)

            try:
                data = await fetch_market_data(symbol, client=ctx_client)
                if not data["candles_5m"] or not data["candles_15m"]:
                    continue

                # Build advanced indicators
                indicators = build_advanced_indicators(data["candles_5m"], data["candles_15m"])

                # Build symbol context from fetched data
                sym_ctx = SymbolContext(
                    orderbook=data.get("orderbook"),
                    long_short=data.get("ls_ratio"),
                )
                symbol_contexts[symbol] = sym_ctx

                decision = trader.make_trading_decision(
                    symbol=symbol,
                    candles_5m=data["candles_5m"],
                    candles_15m=data["candles_15m"],
                    funding_rate=data["funding_rate"],
                    open_positions=open_positions,
                    market_regime=market_regime,
                    advanced_indicators=indicators,
                    market_context=market_ctx,
                    symbol_context=sym_ctx,
                )

                if decision and decision.get("decision") == "TRADE":
                    result = engine.open_position(decision)
                    if result:
                        new_trades.append(result)
                        open_positions.append(result)
                        open_symbols.add(symbol)

            except Exception as e:
                print(f"[Hunt] {symbol}: error: {e}")

        # Close the shared client
        try:
            await ctx_client.close()
        except Exception:
            pass

    # 4. Summary + stats
    stats = engine.get_performance_stats()

    print(f"\n═══ CYCLE SUMMARY ═══")
    print(f"Session: {session} ({progress*100:.0f}%) | New: {len(new_trades)} | Closed: {len(closed)}")
    print(f"Stats: {stats['total_trades']} trades | {stats['win_rate']:.0f}% WR | "
          f"{stats['avg_r']:.2f} avg R | Balance: ${stats['balance']:,.2f}")

    if stats["ready_for_live"]:
        print("🎯 GATE PASSED — Ready for live trading!")
        send_telegram(
            "🎯 TRAINING COMPLETE!\n\n"
            f"{stats['total_trades']} trades | {stats['win_rate']:.0f}% WR | {stats['avg_r']:.2f} avg R\n"
            f"Balance: ${stats['balance']:,.2f}\n\n"
            f"Ready for live. Set Bitget credentials to begin."
        )
    elif stats["total_trades"] > 0 and stats["total_trades"] % 10 == 0:
        send_telegram(
            f"📊 Training: {stats['total_trades']}/50 trades\n"
            f"Win rate: {stats['win_rate']:.0f}% (need 52%)\n"
            f"Avg R: {stats['avg_r']:.2f} | Total R: {stats['total_r']:.2f}\n"
            f"Balance: ${stats['balance']:,.2f}\n"
            f"Trades to live: {stats['trades_to_live']}"
        )

    return {
        "session": session,
        "progress": progress,
        "is_active": is_active,
        "new_trades": len(new_trades),
        "closed_trades": len(closed),
        "balance": stats["balance"],
        "total_trades": stats["total_trades"],
        "win_rate": stats["win_rate"],
        "ready_for_live": stats["ready_for_live"],
    }


async def loop():
    """Continuous 5-min loop."""
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
        "🤖 AEGIS GROQ HUNTER — STARTED\n\n"
        f"Mode: PAPER (training)\n"
        f"Brain: Groq llama-3.3-70b\n"
        f"Style: Scalper-first hunter\n"
        f"Balance: ${stats['balance']:,.2f}\n"
        f"Trades: {stats['total_trades']}/50 to live\n"
        f"Symbols: {', '.join(SYMBOLS)}\n"
        f"Sessions: London 07-11 | NY 13-17 UTC\n"
        f"Max hold: 25 min (scalps)\n"
        f"Cycle: every 5 min\n\n"
        f"🧠 The hunter is hungry. Let's eat."
    )

    while running:
        try:
            await run_cycle()
        except Exception as e:
            print(f"[FATAL] {e}")
            import traceback
            traceback.print_exc()

        if running:
            await asyncio.sleep(CYCLE_INTERVAL)

    send_telegram("🛑 Aegis Hunter stopped")


def show_stats():
    """Print training stats."""
    init_database(DB_PATH)
    from paper_trading.paper_engine import PaperEngine
    from llm.groq_trader import GroqTrader

    engine = PaperEngine()
    trader = GroqTrader()
    stats = engine.get_performance_stats()

    print("\n" + "═"*55)
    print("  AEGIS GROQ HUNTER — TRAINING STATS")
    print("═"*55)
    print(f"  Mode:       PAPER (training)")
    print(f"  Balance:    ${stats['balance']:,.2f}")
    print(f"  Total:      {stats['total_trades']} trades")
    print(f"  W/L:        {stats['wins']}W / {stats['losses']}L")
    print(f"  Win rate:   {stats['win_rate']:.1f}%")
    print(f"  Avg R:      {stats['avg_r']:.2f}")
    print(f"  Total R:    {stats['total_r']:.2f}")
    print(f"  To live:    {stats['trades_to_live']} trades, {stats['win_rate_gap']:.1f}% WR gap")
    print(f"  Ready:      {'✅ YES' if stats['ready_for_live'] else '❌ Not yet'}")
    print(f"  Groq:       {'✅' if trader.available else '❌'}")
    print("═"*55)


def main():
    if "--stats" in sys.argv:
        show_stats()
    elif "--loop" in sys.argv:
        asyncio.run(loop())
    else:
        result = asyncio.run(run_cycle())
        if "error" not in result:
            s = result
            print(f"\n✅ Done: {s['session']} ({s['progress']*100:.0f}%) | "
                  f"New: {s['new_trades']} | Closed: {s['closed_trades']} | "
                  f"Balance: ${s['balance']:,.2f} | Total: {s['total_trades']} trades | "
                  f"WR: {s['win_rate']:.0f}%")


if __name__ == "__main__":
    main()
