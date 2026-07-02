"""
Aegis Trader — Off-Session Heartbeat
Runs health checks + position monitoring when outside London/NY sessions.
No analyst scan to save API calls.
"""
import os, sys, asyncio, json, sqlite3, urllib.request, urllib.parse
from datetime import datetime, timezone, date
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

# ── Load env ────────────────────────────────────────────────────────────
env_path = Path("/app/.agents/.env")
if env_path.exists():
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                if line.startswith("export "):
                    line = line[7:]
                key, val = line.split("=", 1)
                val = val.strip().strip("'").strip('"').replace("\\'", "'")
                os.environ[key] = val

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "6472746064")

from database.connection import init_database, set_db_path
DB_PATH = ROOT / "data" / "journal.db"
set_db_path(DB_PATH)


def send_telegram(text: str) -> None:
    if not BOT_TOKEN:
        print("[Telegram] No token configured")
        return
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


async def main():
    init_database(DB_PATH)
    utc_now = datetime.now(timezone.utc)
    print(f"\n[{utc_now.strftime('%H:%M UTC')}] == Aegis Off-Session Heartbeat ==")

    # ── 1) Health check ──────────────────────────────────────────────────
    btc_price = "N/A"
    eth_price = "N/A"
    try:
        from exchange.bitget_client import BitgetMarketClient
        client = BitgetMarketClient()
        btc = await client.get_ticker("BTCUSDT")
        eth = await client.get_ticker("ETHUSDT")
        if btc:
            btc_price = f"${btc.last_price:,.2f}"
        if eth:
            eth_price = f"${eth.last_price:,.2f}"
        await client.close()
        print(f"[Health] BTC: {btc_price} | ETH: {eth_price}")
    except Exception as e:
        print(f"[Health] Bitget error: {e}")

    # ── 2) Position monitoring ────────────────────────────────────────────
    conn = _get_conn()
    open_trades = conn.execute(
        "SELECT * FROM trades WHERE status IN ('OPEN', 'PARTIAL')"
    ).fetchall()
    n_open = len(open_trades)
    
    monitor_summary = "no open positions"
    tp_sl_hits = []
    
    if n_open > 0:
        print(f"[Monitor] {n_open} open position(s) — checking TP/SL")
        try:
            from positions.monitor import check_positions
            result = await check_positions()
            monitor_summary = result
            print(f"[Monitor] {monitor_summary}")
            
            # Re-check for any TP/SL hits that fired
            tp_hits = conn.execute(
                "SELECT * FROM state_transitions WHERE trigger IN ('TP_HIT','SL_HIT','TP1_HIT') "
                "ORDER BY timestamp DESC LIMIT 5"
            ).fetchall()
            for hit in tp_hits:
                tp_sl_hits.append(dict(hit))
        except Exception as e:
            print(f"[Monitor] Error: {e}")
            import traceback; traceback.print_exc()
    else:
        print("[Monitor] No open positions")
    
    # Refresh open count
    n_open = conn.execute(
        "SELECT count(*) FROM trades WHERE status IN ('OPEN','PARTIAL')"
    ).fetchone()[0]
    conn.close()
    
    # ── 3) Journal stats ──────────────────────────────────────────────────
    conn = _get_conn()
    n_total = conn.execute("SELECT count(*) FROM trades").fetchone()[0]
    n_wins  = conn.execute("SELECT count(*) FROM trades WHERE result='WIN'").fetchone()[0]
    n_loss  = conn.execute("SELECT count(*) FROM trades WHERE result='LOSS'").fetchone()[0]
    conn.close()
    
    wr = f"{n_wins}W/{n_loss}L" if (n_wins + n_loss) > 0 else "no closed trades"
    
    # ── 4) Build summary ──────────────────────────────────────────────────
    session_label = "OFF-HOURS (Asia/Overnight)"
    
    summary_lines = [
        f"🔄 Aegis Heartbeat — {utc_now.strftime('%H:%M UTC')}",
        f"📡 Session: {session_label}",
        f"💹 BTC: {btc_price}",
        f"💹 ETH: {eth_price}",
        f"📊 Positions: {n_open} open",
        f"📈 Record: {wr} | Total trades: {n_total}",
    ]
    
    if tp_sl_hits:
        summary_lines.append("")
        summary_lines.append("⚡ RECENT TP/SL HITS:")
        for hit in tp_sl_hits:
            summary_lines.append(
                f"  Trade {hit['trade_id'][:16]}... | {hit['trigger']} @ {hit.get('price_at_transition', 'N/A')}"
            )
    
    summary = "\n".join(summary_lines)
    print(f"\n[Summary]\n{summary}")
    print("[Heartbeat] Complete")
    
    return summary, n_open, btc_price, session_label


if __name__ == "__main__":
    summary, n_open, btc_price, session_label = asyncio.run(main())
    
    # Only send Telegram if there are open positions or TP/SL hits
    # (reduce noise during off-hours)
    if n_open > 0:
        send_telegram(summary)
