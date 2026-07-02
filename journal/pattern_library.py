"""Pattern Library — Aegis's long-term statistical memory.

Answers Q5: "Does the analyst engine adapt from journal results?"

SINGLE OFFICIAL ROADMAP (resolves the earlier contradiction):

Phase 1 — NOW (0–29 trades per factor):
    Pure rules. Statistics accumulate silently in pattern tables.
    No weight changes. MIN_SAMPLE_SIZE=30 is the hard gate.
    Status: LEARNING

Phase 2 — 30+ trades per factor:
    Empirical weight calibration. ConfidenceEngine.WEIGHTS are
    replaced with DB-learned values per factor.
    Factors that predict wins: weight stays or rises.
    Factors that predict losses: weight reduced.
    Status: CALIBRATING
    
    RISK SIZING NOTE: The confidence→risk_percent table (60-69=0.5%,
    70-79=1.0%, 80-89=1.5%, 90+=2.0%) is intentionally FIXED and
    does NOT adjust from learning results. It is hardcoded in
    setup_detector_v2.py and must be changed only manually.

Phase 3 — 500+ trades:
    Setup fingerprint analysis. Identify which exact combinations
    (zone+session+regime) produce consistent positive expectancy.
    
Phase 4 — 500+ trades + LLM (Grok/Claude):
    Narrative pattern synthesis — qualitative insights across trade clusters.

This module handles Phase 1 and prepares the schema for Phase 2.

IMPORTANT — overfitting guard:
    Weights are only updated when n_samples >= MIN_SAMPLE_SIZE (30 per factor).
    Below that threshold, original ConfidenceEngine.WEIGHTS stay unchanged.
    This prevents the system from "learning" from statistical noise.
"""
from __future__ import annotations

import sqlite3
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

MIN_SAMPLE_SIZE = 30   # minimum trades per factor before weight adjustment
DB_PATH = Path(__file__).parent.parent / "data" / "journal.db"


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def ensure_pattern_tables() -> None:
    """Create pattern library tables if they don't exist."""
    conn = _get_conn()
    conn.executescript("""
        -- Factor win-rate tracking
        CREATE TABLE IF NOT EXISTS pattern_factor_stats (
            factor_name   TEXT NOT NULL,
            regime        TEXT NOT NULL DEFAULT 'ALL',
            session       TEXT NOT NULL DEFAULT 'ALL',
            n_wins        INTEGER NOT NULL DEFAULT 0,
            n_losses      INTEGER NOT NULL DEFAULT 0,
            n_total       INTEGER NOT NULL DEFAULT 0,
            win_rate      REAL NOT NULL DEFAULT 0.0,
            avg_pnl_pct   REAL NOT NULL DEFAULT 0.0,
            last_updated  TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (factor_name, regime, session)
        );

        -- Regime-level stats
        CREATE TABLE IF NOT EXISTS pattern_regime_stats (
            regime          TEXT PRIMARY KEY,
            n_wins          INTEGER DEFAULT 0,
            n_losses        INTEGER DEFAULT 0,
            win_rate        REAL DEFAULT 0.0,
            avg_pnl_pct     REAL DEFAULT 0.0,
            last_updated    TEXT DEFAULT (datetime('now'))
        );

        -- Setup combination stats (confluence fingerprint)
        CREATE TABLE IF NOT EXISTS pattern_setup_stats (
            setup_fingerprint  TEXT PRIMARY KEY,  -- e.g. "DEMAND|LONDON|SIDEWAYS|BOS|NEUTRAL"
            n_wins   INTEGER DEFAULT 0,
            n_losses INTEGER DEFAULT 0,
            win_rate REAL DEFAULT 0.0,
            avg_pnl  REAL DEFAULT 0.0,
            avg_duration_min  REAL DEFAULT 0.0,
            last_updated TEXT DEFAULT (datetime('now'))
        );

        -- Calibrated weights (populated when n >= MIN_SAMPLE_SIZE)
        CREATE TABLE IF NOT EXISTS pattern_calibrated_weights (
            factor_name    TEXT PRIMARY KEY,
            original_weight REAL NOT NULL,
            calibrated_weight REAL NOT NULL,
            sample_size     INTEGER NOT NULL,
            last_calibrated TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit()
    conn.close()


def record_closed_trade(trade_id: str) -> None:
    """
    Called on every trade close. Updates all pattern tables.
    This is the core of the learning loop.
    """
    conn = _get_conn()

    # Load trade + signal metadata
    trade = conn.execute("SELECT * FROM trades WHERE trade_id=?", (trade_id,)).fetchone()
    if not trade:
        conn.close()
        return
    trade = dict(trade)

    result  = trade.get("result")
    pnl     = trade.get("pnl_percent") or 0
    is_win  = (result == "WIN")

    sig = conn.execute(
        "SELECT metadata FROM signals WHERE signal_id=?", (trade.get("signal_id",""),)
    ).fetchone()
    meta = json.loads(sig[0] or "{}") if sig else {}

    regime  = trade.get("market_regime", "UNKNOWN") or "UNKNOWN"
    session = meta.get("session", "UNKNOWN")
    bd      = meta.get("confidence_breakdown", {})
    factors = [f["name"] for f in bd.get("factors", []) if f.get("score", 0) > 0]

    # Duration
    try:
        from datetime import datetime
        t0 = datetime.fromisoformat(trade["opened_at"].replace("Z","+00:00"))
        t1 = datetime.fromisoformat(trade["closed_at"].replace("Z","+00:00"))
        dur = (t1 - t0).total_seconds() / 60
    except Exception:
        dur = 0

    now = datetime.now(timezone.utc).isoformat()

    # ── Factor stats ─────────────────────────────────────
    for factor in factors:
        for grp_regime in [regime, "ALL"]:
            for grp_session in [session, "ALL"]:
                existing = conn.execute("""
                    SELECT n_wins, n_losses, avg_pnl_pct FROM pattern_factor_stats
                    WHERE factor_name=? AND regime=? AND session=?
                """, (factor, grp_regime, grp_session)).fetchone()

                if existing:
                    nw = existing["n_wins"]  + (1 if is_win else 0)
                    nl = existing["n_losses"] + (0 if is_win else 1)
                    nt = nw + nl
                    old_avg = existing["avg_pnl_pct"] * (nt - 1)
                    new_avg = (old_avg + pnl) / nt
                    wr  = nw / nt if nt > 0 else 0
                    conn.execute("""
                        UPDATE pattern_factor_stats
                        SET n_wins=?, n_losses=?, n_total=?, win_rate=?, avg_pnl_pct=?, last_updated=?
                        WHERE factor_name=? AND regime=? AND session=?
                    """, (nw, nl, nt, wr, new_avg, now, factor, grp_regime, grp_session))
                else:
                    conn.execute("""
                        INSERT INTO pattern_factor_stats
                            (factor_name, regime, session, n_wins, n_losses, n_total,
                             win_rate, avg_pnl_pct, last_updated)
                        VALUES (?,?,?,?,?,1,?,?,?)
                    """, (factor, grp_regime, grp_session,
                          1 if is_win else 0, 0 if is_win else 1,
                          1.0 if is_win else 0.0, pnl, now))

    # ── Regime stats ─────────────────────────────────────
    ex = conn.execute("SELECT n_wins,n_losses,avg_pnl_pct FROM pattern_regime_stats WHERE regime=?", (regime,)).fetchone()
    if ex:
        nw = ex["n_wins"] + (1 if is_win else 0)
        nl = ex["n_losses"] + (0 if is_win else 1)
        nt = nw + nl
        new_avg = (ex["avg_pnl_pct"] * (nt-1) + pnl) / nt
        conn.execute("UPDATE pattern_regime_stats SET n_wins=?,n_losses=?,win_rate=?,avg_pnl_pct=?,last_updated=? WHERE regime=?",
                     (nw, nl, nw/nt, new_avg, now, regime))
    else:
        conn.execute("INSERT INTO pattern_regime_stats (regime,n_wins,n_losses,win_rate,avg_pnl_pct,last_updated) VALUES (?,?,?,?,?,?)",
                     (regime, 1 if is_win else 0, 0 if is_win else 1, 1.0 if is_win else 0.0, pnl, now))

    # ── Setup fingerprint ────────────────────────────────
    fp = "|".join(sorted(factors)) + f"|{regime}|{session}"
    ex2 = conn.execute("SELECT n_wins,n_losses,avg_pnl,avg_duration_min FROM pattern_setup_stats WHERE setup_fingerprint=?", (fp,)).fetchone()
    if ex2:
        nw = ex2["n_wins"] + (1 if is_win else 0)
        nl = ex2["n_losses"] + (0 if is_win else 1)
        nt = nw + nl
        new_pnl  = (ex2["avg_pnl"] * (nt-1) + pnl) / nt
        new_dur  = (ex2["avg_duration_min"] * (nt-1) + dur) / nt
        conn.execute("UPDATE pattern_setup_stats SET n_wins=?,n_losses=?,win_rate=?,avg_pnl=?,avg_duration_min=?,last_updated=? WHERE setup_fingerprint=?",
                     (nw, nl, nw/nt, new_pnl, new_dur, now, fp))
    else:
        conn.execute("INSERT INTO pattern_setup_stats (setup_fingerprint,n_wins,n_losses,win_rate,avg_pnl,avg_duration_min,last_updated) VALUES (?,?,?,?,?,?,?)",
                     (fp, 1 if is_win else 0, 0 if is_win else 1, 1.0 if is_win else 0.0, pnl, dur, now))

    conn.commit()
    conn.close()


def get_performance_report() -> dict:
    """
    Returns current statistical memory — what Aegis has learned so far.
    Called by coach and heartbeat summary.
    """
    ensure_pattern_tables()
    conn = _get_conn()

    # Overall
    total = conn.execute("SELECT count(*) FROM trades WHERE status='CLOSED'").fetchone()[0]
    wins  = conn.execute("SELECT count(*) FROM trades WHERE result='WIN'").fetchone()[0]
    losses= conn.execute("SELECT count(*) FROM trades WHERE result='LOSS'").fetchone()[0]

    # Best factors
    best_factors = conn.execute("""
        SELECT factor_name, win_rate, avg_pnl_pct, n_total
        FROM pattern_factor_stats
        WHERE regime='ALL' AND session='ALL' AND n_total >= 3
        ORDER BY win_rate DESC, avg_pnl_pct DESC
        LIMIT 5
    """).fetchall()

    # Regime stats
    regime_stats = conn.execute(
        "SELECT regime, win_rate, avg_pnl_pct, n_wins+n_losses n FROM pattern_regime_stats WHERE n_wins+n_losses > 0"
    ).fetchall()

    conn.close()
    return {
        "total_closed": total,
        "wins":  wins,
        "losses": losses,
        "win_rate": round(wins/total, 3) if total > 0 else 0,
        "best_factors": [dict(r) for r in best_factors],
        "regime_stats": [dict(r) for r in regime_stats],
        "min_sample_for_weight_update": MIN_SAMPLE_SIZE,
        "status": (
            "LEARNING — accumulating data" if total < MIN_SAMPLE_SIZE
            else "CALIBRATING — enough data to adjust weights"
        ),
    }


def format_report_telegram(report: dict) -> str:
    """Format pattern library report as Telegram message."""
    lines = [
        "📊 AEGIS PATTERN LIBRARY\n",
        f"Closed trades: {report['total_closed']}",
        f"Win rate:      {report['win_rate']*100:.1f}%",
        f"Status:        {report['status']}",
    ]
    if report["regime_stats"]:
        lines.append("\nRegime performance:")
        for r in report["regime_stats"]:
            lines.append(f"  {r['regime']:15s} {r['win_rate']*100:.0f}% WR | {r['avg_pnl_pct']:+.1f}% avg | n={r['n']}")
    if report["best_factors"]:
        lines.append("\nTop factors by win rate:")
        for f in report["best_factors"]:
            lines.append(f"  {f['factor_name']:25s} {f['win_rate']*100:.0f}% WR | n={f['n_total']}")
    lines.append(f"\n⚠️ Weight calibration unlocks at {report['min_sample_for_weight_update']} trades per factor")
    return "\n".join(lines)
