"""
Aegis Trader — Heartbeat Runner v3 (Production-safe)
Runs every 5 minutes via Base44 automation.

Production fixes:
- Duplicate guard: no new trade if same symbol+direction already OPEN/PARTIAL
- Daily trade cap: reads closed trades from DB (survives restarts)
- Single canonical DB path via config
- Full exception isolation per component
- TP1 partial stored in signal metadata
"""
import os, sys, asyncio, json, sqlite3, uuid
import urllib.request, urllib.parse
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env", override=True)
sys.path.insert(0, str(ROOT))

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "6472746064")

# ── Single canonical DB path (no more hardcoded duplication) ──────────────
from database.connection import init_database, set_db_path
DB_PATH = ROOT / "data" / "journal.db"
set_db_path(DB_PATH)


def send_telegram(text: str) -> None:
    url  = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": CHAT_ID, "text": text}).encode()
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


# ── Duplicate guard ────────────────────────────────────────────────────────
def is_already_open(symbol: str, direction: str) -> bool:
    """Return True if there's already an OPEN or PARTIAL trade for this symbol+direction."""
    conn = _get_conn()
    row  = conn.execute("""
        SELECT count(*) n FROM trades
        WHERE symbol=? AND direction=? AND status IN ('OPEN','PARTIAL')
    """, (symbol, direction)).fetchone()
    conn.close()
    return (row["n"] > 0) if row else False


# ── Daily trade cap (DB-backed, survives restarts) ─────────────────────────
MAX_DAILY_TRADES = 3

def trades_opened_today() -> int:
    """Count trades opened today (UTC) from DB."""
    conn  = _get_conn()
    today = date.today().isoformat()  # "YYYY-MM-DD"
    row   = conn.execute("""
        SELECT count(*) n FROM trades
        WHERE date(opened_at) = ? AND status IN ('OPEN','PARTIAL','CLOSED')
    """, (today,)).fetchone()
    conn.close()
    return row["n"] if row else 0


def save_candidate(candidate) -> Optional[str]:
    """
    Save a TradeCandidate to journal. Returns trade_id or None if duplicate/capped.
    """
    # ── Duplicate guard ──────────────────────────────
    if is_already_open(candidate.symbol, candidate.side):
        print(f"[Guard] {candidate.symbol} {candidate.side} already OPEN — skipping")
        return None

    # ── Daily cap ────────────────────────────────────
    if trades_opened_today() >= MAX_DAILY_TRADES:
        print(f"[Guard] Daily cap ({MAX_DAILY_TRADES}) reached — skipping {candidate.symbol}")
        return None

    # ── Correlation guard ─────────────────────────────
    from positions.correlation_guard import CorrelationGuard
    allowed, corr_reason = CorrelationGuard.allows(candidate.symbol, candidate.side)
    if not allowed:
        print(f"[CorrelationGuard] {candidate.symbol} {candidate.side} BLOCKED — {corr_reason}")
        return None

    conn      = _get_conn()
    signal_id = f"pa-{uuid.uuid4().hex[:12]}"
    trade_id  = f"trade-{uuid.uuid4().hex[:12]}"
    now       = datetime.now(timezone.utc).isoformat()
    sig       = candidate.to_signal()

    meta = sig.get("metadata", {})
    if candidate.confidence_breakdown:
        meta["confidence_breakdown"] = candidate.confidence_breakdown
    if candidate.regime_evidence:
        meta["regime_evidence"] = candidate.regime_evidence  # Q4: full proof stored
    meta["risk_percent"]    = candidate.risk_percent         # Q3: confidence-based sizing
    meta["conflict_status"] = candidate.conflict_status      # Q2: conflict audit
    # Store TP1 in metadata so monitor can partial-close at 1.5R
    meta["tp1"]             = candidate.take_profit_1
    meta["take_profit_1"]   = candidate.take_profit_1
    # Store zone geometry for thesis invalidation checks
    if candidate.zone:
        meta["zone_top"]    = candidate.zone.top
        meta["zone_bottom"] = candidate.zone.bottom
        meta["zone_type"]   = candidate.zone.zone_type.value if candidate.zone.zone_type else "UNKNOWN"
    meta["thesis"]          = candidate.thesis               # readable thesis string

    conn.execute("""
        INSERT INTO signals
            (signal_id, source, raw_text, symbol, side,
             entry, stop_loss, take_profit, leverage, margin_mode,
             timestamp, confidence, metadata)
        VALUES (?, 'telegram', ?, ?, ?, ?, ?, ?, 10, 'ISOLATED', ?, ?, ?)
    """, (
        signal_id, sig["raw_text"], candidate.symbol, candidate.side,
        candidate.entry, candidate.stop_loss, candidate.take_profit_2,
        now, candidate.confidence, json.dumps(meta),
    ))

    liq = round(candidate.entry * (1 - 1/10), 2) if candidate.side == "LONG" \
          else round(candidate.entry * (1 + 1/10), 2)

    # expected_R = TP2 distance / risk = always 3.0 by design
    entry_p   = candidate.entry
    sl_p      = candidate.stop_loss
    tp2_p     = candidate.take_profit_2
    risk_pts  = abs(entry_p - sl_p) if (entry_p and sl_p) else 0
    expected_r = round(abs(tp2_p - entry_p) / risk_pts, 2) if risk_pts else 3.0

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
        candidate.session.value  if candidate.session  else "",
        candidate.regime.value   if candidate.regime   else "",
    ))

    conn.execute("""
        INSERT INTO state_transitions
            (trade_id, from_state, to_state, trigger, price_at_transition)
        VALUES (?, 'PENDING', 'OPEN', 'analyst_signal', ?)
    """, (trade_id, candidate.entry))

    conn.commit()
    conn.close()
    return trade_id


def format_signal_alert(candidate, trade_id: str) -> str:
    direction_emoji = "🟢" if candidate.side == "LONG" else "🔴"

    bd_text = ""
    if candidate.confidence_breakdown:
        lines = []
        for f in candidate.confidence_breakdown.get("factors", []):
            icon = "✅" if f["score"] > 0 else ("⚠️" if f["score"] < 0 else "➖")
            sign = "+" if f["score"] >= 0 else ""
            lines.append(f"{icon} {f['name']}: {sign}{f['score']:.0f} — {f['reason']}")
        bd_text = "\n".join(lines)

    # Q4: regime proof
    re = candidate.regime_evidence or {}
    regime_proof = re.get("reason", "N/A")

    # Q3: risk sizing
    risk_label = f"{candidate.risk_percent:.1f}% account risk"

    return (
        f"{direction_emoji} NEW SETUP — {candidate.symbol}\n\n"
        f"Direction: {candidate.side}\n"
        f"Entry:  {candidate.entry:,.4f}\n"
        f"SL:     {candidate.stop_loss:,.4f}\n"
        f"TP1:    {candidate.take_profit_1:,.4f}  (+1.5R — partial)\n"
        f"TP2:    {candidate.take_profit_2:,.4f}  (+3R  — full exit)\n\n"
        f"── CONFIDENCE ──\n"
        f"{bd_text}\n\n"
        f"Score: {candidate.confidence:.0f}% | Confluence: {candidate.confluence_score}/5\n"
        f"Risk sizing: {risk_label}\n\n"
        f"── REGIME PROOF ──\n"
        f"Regime: {candidate.regime.value if candidate.regime else 'UNKNOWN'}\n"
        f"Proof:  {regime_proof}\n\n"
        f"Thesis: {candidate.thesis}\n\n"
        f"ID: {trade_id}"
    )


async def run_analyst() -> tuple:
    from analyst import MarketScannerV2, SessionFilter
    session_name = SessionFilter.get_session_name()
    is_active    = SessionFilter.is_trade_session()
    print(f"[Analyst] Session: {session_name} | Active: {is_active}")

    scanner = MarketScannerV2(
        symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT",
                 "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT"],
        min_confidence=60.0,
    )
    scan = await scanner.scan_all()
    await scanner.close()

    candidates = [c for r in scan.results for c in r.candidates]
    return candidates, session_name, is_active


def count_open_trades() -> int:
    conn = _get_conn()
    n = conn.execute("SELECT count(*) FROM trades WHERE status IN ('OPEN','PARTIAL')").fetchone()[0]
    conn.close()
    return n


async def main():
    # DB init
    init_database(DB_PATH)

    utc_now = datetime.now(timezone.utc)
    print(f"\n[{utc_now.strftime('%H:%M UTC')}] == Aegis Heartbeat ==")

    # Health check
    btc_price = "N/A"
    try:
        from exchange.bitget_client import BitgetMarketClient
        client = BitgetMarketClient()
        btc    = await client.get_ticker("BTCUSDT")
        if btc:
            btc_price = f"${btc.last_price:,.2f}"
        await client.close()
        print(f"[Health] Bitget: {btc_price}")
    except Exception as e:
        print(f"[Health] Bitget error: {e}")

    # Position monitor -- TIERED FREQUENCY
    # Positions open  -> poll every 30s for up to 4.5min (9 polls), then analyst
    # No positions    -> single check, then analyst immediately
    n_open = count_open_trades()
    monitor_summary = "no open positions"
    try:
        from positions.monitor import check_positions
        if n_open > 0:
            print(f"[Monitor] {n_open} open position(s) -- entering 30s poll loop")
            POLL_SECONDS  = 30
            POLL_DURATION = 270   # 4.5 min
            polls_done    = 0
            last_result   = ""
            for _ in range(POLL_DURATION // POLL_SECONDS):
                last_result = await check_positions()
                polls_done += 1
                print(f"[Monitor] Poll {polls_done}: {last_result}")
                if count_open_trades() == 0:
                    print("[Monitor] All positions closed -- exiting poll loop early")
                    break
                await asyncio.sleep(POLL_SECONDS)
            monitor_summary = f"Polled {polls_done}x at 30s | {last_result}"
        else:
            monitor_summary = await check_positions()
            print(f"[Monitor] {monitor_summary}")
    except Exception as e:
        print(f"[Monitor] Error: {e}")
        import traceback; traceback.print_exc()

    # Analyst scan
    new_setups = 0
    try:
        candidates, session_name, _ = await run_analyst()
        print(f"[Analyst] {len(candidates)} raw candidate(s)")

        for c in candidates:
            trade_id = save_candidate(c)
            if trade_id:
                alert = format_signal_alert(c, trade_id)
                send_telegram(alert)
                print(f"[Analyst] Saved: {c.symbol} {c.side} @ {c.entry} | {c.confidence:.0f}%")
                new_setups += 1
    except Exception as e:
        print(f"[Analyst] Error: {e}")
        import traceback; traceback.print_exc()

    # Journal summary
    conn    = _get_conn()
    n_open  = conn.execute("SELECT count(*) FROM trades WHERE status IN ('OPEN','PARTIAL')").fetchone()[0]
    n_total = conn.execute("SELECT count(*) FROM trades").fetchone()[0]
    n_wins  = conn.execute("SELECT count(*) FROM trades WHERE result='WIN'").fetchone()[0]
    n_loss  = conn.execute("SELECT count(*) FROM trades WHERE result='LOSS'").fetchone()[0]
    today_n = trades_opened_today()
    conn.close()

    record = f"{n_wins}W/{n_loss}L" if (n_wins + n_loss) > 0 else "no closed trades"
    print(f"[Journal] Open: {n_open} | Today: {today_n}/{MAX_DAILY_TRADES} | Total: {n_total} | {record}")
    print("[Heartbeat] Complete")


if __name__ == "__main__":
    asyncio.run(main())
