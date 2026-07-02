#!/usr/bin/env python3
"""
Aegis Autonomous Trader — Full standalone runner.
NO Claude/LLM agent needed. Uses Groq for AI + Bitget for execution.

Runs the complete cycle every 5 minutes:
1. Health check (Bitget API)
2. Position monitor (sync with exchange, check TP/SL fills)
3. Analyst scan (during London/NY sessions only)
4. Order execution (place actual trades on Bitget)
5. Coach analysis (via Groq LLM on every close)
6. Telegram alerts

Usage:
  python3 autonomous_trader.py              # one cycle
  python3 autonomous_trader.py --loop        # continuous 5-min loop
"""
import os, sys, asyncio, json, sqlite3, uuid, signal, time
import urllib.request, urllib.parse
from datetime import datetime, timezone, date
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

# Bitget credentials
BITGET_API_KEY    = os.getenv("BITGET_API_KEY", "")
BITGET_SECRET     = os.getenv("BITGET_SECRET_KEY", "")
BITGET_PASSPHRASE = os.getenv("BITGET_PASSPHRASE", "")

# Groq
LLM_API_KEY = os.getenv("LLM_API_KEY", "")

from database.connection import init_database, set_db_path
DB_PATH = ROOT / "data" / "journal.db"
set_db_path(DB_PATH)

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT",
           "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT"]
MAX_DAILY_TRADES = 3
DAILY_LOSS_LIMIT = -3.0
MIN_CONFIDENCE = 65.0
CYCLE_INTERVAL = 300  # 5 minutes

# ── Telegram ───────────────────────────────────────────────────────
def send_telegram(text: str) -> None:
    if not BOT_TOKEN:
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }).encode()
    try:
        urllib.request.urlopen(url, data, timeout=10)
    except Exception as e:
        print(f"[Telegram] {e}")


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


# ── Guards ─────────────────────────────────────────────────────────
def is_already_open(symbol: str, direction: str) -> bool:
    conn = _get_conn()
    row = conn.execute("""
        SELECT count(*) n FROM trades
        WHERE symbol=? AND direction=? AND status IN ('OPEN','PARTIAL')
    """, (symbol, direction)).fetchone()
    conn.close()
    return (row["n"] > 0) if row else False


def trades_opened_today() -> int:
    conn = _get_conn()
    today = date.today().isoformat()
    row = conn.execute("""
        SELECT count(*) n FROM trades
        WHERE date(opened_at) = ? AND status IN ('OPEN','PARTIAL','CLOSED')
    """, (today,)).fetchone()
    conn.close()
    return row["n"] if row else 0


def daily_pnl_percent() -> float:
    """Calculate today's P&L as percentage."""
    conn = _get_conn()
    today = date.today().isoformat()
    rows = conn.execute("""
        SELECT actual_r, pnl_percent FROM trades
        WHERE date(closed_at) = ? AND status = 'CLOSED'
    """, (today,)).fetchall()
    conn.close()
    if not rows:
        return 0.0
    total_pnl = 0.0
    for r in rows:
        risk = r["pnl_percent"] or 0
        r_multiple = r["actual_r"] or 0
        total_pnl += (r_multiple * 1.5 if r_multiple else risk)
    return total_pnl


def is_daily_limit_hit() -> bool:
    if trades_opened_today() >= MAX_DAILY_TRADES:
        return True
    if daily_pnl_percent() <= DAILY_LOSS_LIMIT:
        return True
    return False


# ── Session check ──────────────────────────────────────────────────
def get_session() -> tuple[str, bool]:
    hour = datetime.now(timezone.utc).hour
    if 7 <= hour < 9:
        return "LONDON", True
    elif 13 <= hour < 15:
        return "NY", True
    elif 0 <= hour < 3:
        return "ASIA", False
    else:
        return "OFF_HOURS", False


# ── Execute trade on Bitget ────────────────────────────────────────
async def execute_trade(candidate, trading_client) -> Optional[dict]:
    """Place actual orders on Bitget for a trade candidate."""
    if not trading_client:
        print("[Execute] No trading client — skipping order placement")
        return None

    # Get account balance for position sizing
    balance = await trading_client.get_account_balance()
    if not balance:
        print("[Execute] Could not get account balance")
        return None

    equity = float(balance.get("accountEquity", 0))
    if equity <= 0:
        print("[Execute] No account equity")
        return None

    # Calculate position size
    from exchange.bitget_trading_client import calculate_position_size
    size = calculate_position_size(
        account_equity=equity,
        risk_percent=candidate.risk_percent,
        entry_price=candidate.entry,
        stop_loss=candidate.stop_loss,
        leverage=10,
    )

    if size == "0":
        print(f"[Execute] Position size calculated as 0 — skipping")
        return None

    # Determine order side
    side = "buy" if candidate.side == "LONG" else "sell"
    close_side = "sell" if candidate.side == "LONG" else "buy"

    print(f"[Execute] Placing {candidate.side} {candidate.symbol} size={size} @ {candidate.entry}")

    # 1. Place market order to enter
    order_result = await trading_client.place_market_order(
        symbol=candidate.symbol,
        side=side,
        size=size,
        leverage=10,
    )

    if not order_result:
        print(f"[Execute] Order failed for {candidate.symbol}")
        send_telegram(f"⚠️ ORDER FAILED — {candidate.symbol} {candidate.side}\nCould not place entry order on Bitget")
        return None

    order_id = order_result.get("orderId", "")
    print(f"[Execute] Entry order placed: {order_id}")

    # 2. Place SL plan order
    sl_result = await trading_client.place_plan_order(
        symbol=candidate.symbol,
        side=close_side,
        size=size,
        trigger_price=candidate.stop_loss,
        plan_type="normal_plan",
        reduce_only=True,
    )

    # 3. Place TP1 plan order (50% at 1.5R)
    tp1_size = str(max(int(int(size) * 0.5), 1))
    tp1_result = await trading_client.place_plan_order(
        symbol=candidate.symbol,
        side=close_side,
        size=tp1_size,
        trigger_price=candidate.take_profit_1,
        plan_type="profit_loss",
        reduce_only=True,
    )

    # 4. Place TP2 plan order (remaining at 3R)
    tp2_size = str(int(size) - int(tp1_size))
    if int(tp2_size) > 0:
        tp2_result = await trading_client.place_plan_order(
            symbol=candidate.symbol,
            side=close_side,
            size=tp2_size,
            trigger_price=candidate.take_profit_2,
            plan_type="profit_loss",
            reduce_only=True,
        )

    return {
        "order_id": order_id,
        "size": size,
        "equity": equity,
        "sl_placed": sl_result is not None,
        "tp1_placed": tp1_result is not None,
    }


# ── Save trade to journal ──────────────────────────────────────────
def save_trade_to_journal(candidate, execution_result: Optional[dict]) -> Optional[str]:
    """Save trade candidate + execution info to journal."""
    conn = _get_conn()
    signal_id = f"pa-{uuid.uuid4().hex[:12]}"
    trade_id  = f"trade-{uuid.uuid4().hex[:12]}"
    now       = datetime.now(timezone.utc).isoformat()
    sig       = candidate.to_signal()

    meta = sig.get("metadata", {})
    if candidate.confidence_breakdown:
        meta["confidence_breakdown"] = candidate.confidence_breakdown
    if candidate.regime_evidence:
        meta["regime_evidence"] = candidate.regime_evidence
    meta["risk_percent"]    = candidate.risk_percent
    meta["conflict_status"] = candidate.conflict_status
    meta["tp1"]             = candidate.take_profit_1
    meta["take_profit_1"]   = candidate.take_profit_1
    if candidate.zone:
        meta["zone_top"]    = candidate.zone.top
        meta["zone_bottom"] = candidate.zone.bottom
        meta["zone_type"]   = candidate.zone.zone_type.value if candidate.zone.zone_type else "UNKNOWN"
    meta["thesis"]          = candidate.thesis
    if execution_result:
        meta["bitget_order_id"] = execution_result.get("order_id", "")
        meta["position_size"]   = execution_result.get("size", "")
        meta["account_equity"]  = execution_result.get("equity", 0)
        meta["sl_placed"]       = execution_result.get("sl_placed", False)
        meta["tp1_placed"]      = execution_result.get("tp1_placed", False)

    conn.execute("""
        INSERT INTO signals
            (signal_id, source, raw_text, symbol, side,
             entry, stop_loss, take_profit, leverage, margin_mode,
             timestamp, confidence, metadata)
        VALUES (?, 'analyst', ?, ?, ?, ?, ?, ?, 10, 'ISOLATED', ?, ?, ?)
    """, (
        signal_id, sig["raw_text"], candidate.symbol, candidate.side,
        candidate.entry, candidate.stop_loss, candidate.take_profit_2,
        now, candidate.confidence, json.dumps(meta),
    ))

    liq = round(candidate.entry * (1 - 1/10), 2) if candidate.side == "LONG" \
          else round(candidate.entry * (1 + 1/10), 2)

    risk_pts = abs(candidate.entry - candidate.stop_loss) if candidate.entry and candidate.stop_loss else 0
    expected_r = round(abs(candidate.take_profit_2 - candidate.entry) / risk_pts, 2) if risk_pts else 3.0

    conn.execute("""
        INSERT INTO trades
            (trade_id, signal_id, symbol, direction,
             leverage, margin_mode, entry_price, stop_loss, take_profit,
             liquidation_price, status, opened_at,
             setup_type, confidence_score, signal_source, signal_raw,
             market_regime, expected_r, created_at, updated_at)
        VALUES (?, ?, ?, ?, 10, 'ISOLATED', ?, ?, ?, ?, 'OPEN', ?,
                'PRICE_ACTION', ?, 'analyst', ?, ?, ?, ?, ?)
    """, (
        trade_id, signal_id, candidate.symbol, candidate.side,
        candidate.entry, candidate.stop_loss, candidate.take_profit_2,
        liq, now,
        candidate.confidence, sig["raw_text"],
        candidate.regime.value if candidate.regime else "UNKNOWN",
        expected_r, now, now,
    ))

    conn.execute("""
        INSERT INTO market_context
            (trade_id, price_at_entry, session_tag, regime_tag)
        VALUES (?, ?, ?, ?)
    """, (
        trade_id, candidate.entry,
        candidate.session.value if candidate.session else "",
        candidate.regime.value if candidate.regime else "",
    ))

    conn.execute("""
        INSERT INTO state_transitions
            (trade_id, from_state, to_state, trigger, price_at_transition)
        VALUES (?, 'PENDING', 'OPEN', 'analyst_signal', ?)
    """, (trade_id, candidate.entry))

    conn.commit()
    conn.close()
    return trade_id


# ── Format alerts ──────────────────────────────────────────────────
def format_entry_alert(candidate, trade_id: str, execution: Optional[dict]) -> str:
    direction_emoji = "🟢" if candidate.side == "LONG" else "🔴"
    bd_text = ""
    if candidate.confidence_breakdown:
        lines = []
        for f in candidate.confidence_breakdown.get("factors", []):
            icon = "✅" if f["score"] > 0 else ("⚠️" if f["score"] < 0 else "➖")
            sign = "+" if f["score"] >= 0 else ""
            lines.append(f"{icon} {f['name']}: {sign}{f['score']:.0f} — {f['reason']}")
        bd_text = "\n".join(lines)

    exec_text = ""
    if execution:
        exec_text = f"\n📊 Size: {execution['size']} contracts | Equity: ${execution['equity']:.2f}\n"
        exec_text += f"🔗 Bitget Order: {execution.get('order_id', 'N/A')}\n"
        exec_text += f"🛡️ SL on exchange: {'✅' if execution.get('sl_placed') else '❌'}\n"
        exec_text += f"🎯 TP1 on exchange: {'✅' if execution.get('tp1_placed') else '❌'}"

    return (
        f"{direction_emoji} TRADE EXECUTED — {candidate.symbol}\n\n"
        f"Direction: {candidate.side}\n"
        f"Entry:  {candidate.entry:,.4f}\n"
        f"SL:     {candidate.stop_loss:,.4f}\n"
        f"TP1:    {candidate.take_profit_1:,.4f}  (+1.5R — 50% close)\n"
        f"TP2:    {candidate.take_profit_2:,.4f}  (+3R  — full exit)\n\n"
        f"── CONFIDENCE ──\n{bd_text}\n\n"
        f"Score: {candidate.confidence:.0f}% | Confluence: {candidate.confluence_score}/5\n"
        f"Risk: {candidate.risk_percent:.1f}% account\n\n"
        f"Thesis: {candidate.thesis}{exec_text}\n\n"
        f"ID: {trade_id}"
    )


# ── Monitor positions (sync with exchange) ─────────────────────────
async def monitor_positions(trading_client) -> list[dict]:
    """Check exchange positions and sync with journal."""
    if not trading_client:
        return []

    exchange_positions = await trading_client.get_positions()
    conn = _get_conn()
    db_trades = conn.execute(
        "SELECT * FROM trades WHERE status IN ('OPEN', 'PARTIAL')"
    ).fetchall()

    closed_trades = []

    for trade in db_trades:
        trade = dict(trade)
        # Check if position still exists on exchange
        exchange_pos = None
        for pos in exchange_positions:
            if pos.get("symbol") == trade["symbol"]:
                exchange_pos = pos
                break

        if exchange_pos is None:
            # Position was closed by exchange (TP/SL hit)
            # Get the last price for exit
            from exchange.bitget_client import BitgetMarketClient
            market = BitgetMarketClient()
            ticker = await market.get_ticker(trade["symbol"])
            await market.close()
            exit_price = ticker.last_price if ticker else trade["entry_price"]

            # Determine exit reason
            entry = trade["entry_price"]
            sl = trade["stop_loss"]
            tp = trade["take_profit"]
            direction = trade["direction"]

            if direction == "LONG":
                if exit_price <= sl:
                    trigger = "SL_HIT"
                elif exit_price >= tp:
                    trigger = "TP_HIT"
                else:
                    trigger = "MANUAL_CLOSE"
            else:
                if exit_price >= sl:
                    trigger = "SL_HIT"
                elif exit_price <= tp:
                    trigger = "TP_HIT"
                else:
                    trigger = "MANUAL_CLOSE"

            # Close in journal
            from positions.monitor import close_trade_atomically, run_coach_analysis, format_close_alert
            updated = close_trade_atomically(
                trade_id=trade["trade_id"],
                exit_price=exit_price,
                trigger=trigger,
                current_status=trade["status"],
            )

            if updated:
                # Run Groq coach analysis
                try:
                    from llm.groq_coach import GroqCoach
                    coach = GroqCoach()
                    analysis = coach.review_and_save(updated)
                    # Send Telegram close alert
                    alert = format_close_alert(updated, analysis)
                    send_telegram(alert)
                except Exception as e:
                    print(f"[Monitor] Coach error: {e}")
                    # Still send basic close alert
                    send_telegram(f"{'🟢✅' if updated.get('result')=='WIN' else '🔴❌'} CLOSED — {trade['symbol']} {trigger}")

                closed_trades.append(updated)

    conn.close()
    return closed_trades


# ── Analyst scan + execution ───────────────────────────────────────
async def run_analyst_and_execute(trading_client) -> list[tuple]:
    """Run analyst scan and execute any candidates found."""
    from analyst import MarketScannerV2, SessionFilter

    session_name = SessionFilter.get_session_name()
    is_active = SessionFilter.is_trade_session()
    print(f"[Analyst] Session: {session_name} | Active: {is_active}")

    if not is_active:
        return []

    if is_daily_limit_hit():
        print("[Guard] Daily limit hit — no new trades")
        send_telegram("⛔ Daily limit reached — no new trades today")
        return []

    scanner = MarketScannerV2(
        symbols=SYMBOLS,
        min_confidence=MIN_CONFIDENCE,
    )
    scan = await scanner.scan_all()
    await scanner.close()

    candidates = [c for r in scan.results for c in r.candidates]
    executed = []

    for candidate in candidates:
        # Guards
        if is_already_open(candidate.symbol, candidate.side):
            print(f"[Guard] {candidate.symbol} {candidate.side} already open — skip")
            continue
        if trades_opened_today() >= MAX_DAILY_TRADES:
            print(f"[Guard] Daily cap reached — skip {candidate.symbol}")
            break

        # Correlation guard
        from positions.correlation_guard import CorrelationGuard
        allowed, reason = CorrelationGuard.allows(candidate.symbol, candidate.side)
        if not allowed:
            print(f"[Guard] Correlation block: {reason}")
            continue

        # Execute on Bitget
        execution = await execute_trade(candidate, trading_client)

        # Save to journal
        trade_id = save_trade_to_journal(candidate, execution)

        if trade_id:
            alert = format_entry_alert(candidate, trade_id, execution)
            send_telegram(alert)
            executed.append((candidate, trade_id, execution))
            print(f"[Execute] Trade saved: {trade_id}")

    return executed


# ── Health check ───────────────────────────────────────────────────
async def health_check(trading_client) -> dict:
    """Quick health check."""
    from exchange.bitget_client import BitgetMarketClient
    client = BitgetMarketClient()
    btc = await client.get_ticker("BTCUSDT")
    eth = await client.get_ticker("ETHUSDT")
    await client.close()

    has_trading = bool(BITGET_API_KEY and BITGET_SECRET and BITGET_PASSPHRASE)
    has_groq = bool(LLM_API_KEY)

    return {
        "btc": btc.last_price if btc else None,
        "eth": eth.last_price if eth else None,
        "trading_enabled": has_trading,
        "groq_enabled": has_groq,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ── Main cycle ─────────────────────────────────────────────────────
async def run_cycle() -> dict:
    """Run one complete cycle."""
    init_database(DB_PATH)
    utc_now = datetime.now(timezone.utc)
    print(f"\n[{utc_now.strftime('%H:%M UTC')}] === AEGIS AUTONOMOUS CYCLE ===")

    # 1. Health check
    health = await health_check(None)
    print(f"[Health] BTC: ${health['btc']:,.2f}" if health['btc'] else "[Health] BTC: N/A")
    print(f"[Health] Trading: {'✅' if health['trading_enabled'] else '❌ NO CREDS'} | Groq: {'✅' if health['groq_enabled'] else '❌'}")

    # 2. Create trading client if credentials available
    trading_client = None
    if health["trading_enabled"]:
        from exchange.bitget_trading_client import BitgetTradingClient
        trading_client = BitgetTradingClient(
            api_key=BITGET_API_KEY,
            secret_key=BITGET_SECRET,
            passphrase=BITGET_PASSPHRASE,
        )

    # 3. Monitor existing positions
    closed = await monitor_positions(trading_client)
    if closed:
        print(f"[Monitor] {len(closed)} position(s) closed")

    # 4. Session check + analyst scan + execution
    session_name, is_active = get_session()
    executed_trades = []
    if is_active:
        print(f"[Analyst] {session_name} session active — scanning...")
        executed_trades = await run_analyst_and_execute(trading_client)
    else:
        print(f"[Analyst] {session_name} — standby")

    # 5. Summary
    conn = _get_conn()
    open_count = conn.execute("SELECT count(*) FROM trades WHERE status IN ('OPEN','PARTIAL')").fetchone()[0]
    total_trades = conn.execute("SELECT count(*) FROM trades").fetchone()[0]
    today_count = trades_opened_today()
    conn.close()

    summary = {
        "time": utc_now.strftime("%H:%M UTC"),
        "session": session_name,
        "btc": health["btc"],
        "open_positions": open_count,
        "total_trades": total_trades,
        "trades_today": today_count,
        "new_trades": len(executed_trades),
        "closed_trades": len(closed),
        "trading_enabled": health["trading_enabled"],
        "groq_enabled": health["groq_enabled"],
    }

    print(f"[Summary] Open: {open_count} | Today: {today_count}/{MAX_DAILY_TRADES} | Total: {total_trades}")

    if trading_client:
        await trading_client.close()

    return summary


async def loop():
    """Continuous loop mode."""
    running = True

    def handle_signal(sig, frame):
        nonlocal running
        running = False
        print("\n[Shutdown] Graceful stop...")

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    send_telegram("🤖 Aegis Autonomous Trader started\nMode: Continuous 5-min loop\nGroq AI: " + ("✅" if LLM_API_KEY else "❌") + "\nBitget Trading: " + ("✅" if BITGET_API_KEY else "❌ NO CREDS"))

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

    send_telegram("🛑 Aegis Autonomous Trader stopped")


def main():
    if "--loop" in sys.argv:
        asyncio.run(loop())
    else:
        result = asyncio.run(run_cycle())
        # Print summary for automation
        s = result
        if s["new_trades"] > 0:
            print(f"EXECUTED: {s['new_trades']} new trades")
        elif s["closed_trades"] > 0:
            print(f"CLOSED: {s['closed_trades']} positions")
        else:
            print(f"OK: {s['session']} | BTC ${s['btc']:,.0f} | Open: {s['open_positions']} | Today: {s['trades_today']}/{MAX_DAILY_TRADES}")


if __name__ == "__main__":
    main()
