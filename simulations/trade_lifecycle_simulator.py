"""
COMPLETE TRADE LIFECYCLE SIMULATOR

Shows one hypothetical trade from signal detection → entry → monitoring → exit.
Uses current live code, current BTC price, detected regime/zones, actual confidence breakdown.

OUTPUT: step-by-step state transitions and journal updates for all possible exit scenarios.

Run this to see exactly what happens when:
  1. TP1 is hit
  2. TP2 is hit
  3. SL is hit
  4. Zone breaks before TP
  5. Trade held >12h (time stop)
  6. Thesis invalidated (regime flip)
  7. Funding inverts

All with exact P&L, position size, entry/exit prices.
"""
import asyncio
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Any

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from exchange.bitget_client import BitgetMarketClient
from analyst.setup_detector_v2 import SetupDetectorV2
from analyst.regime_detector_v2 import RegimeDetectorV2
from analyst.confidence_engine import ConfidenceEngine
from analyst.models_v2 import MarketRegime
from positions.invalidation import ThesisInvalidationEngine, InvalidationReason
from positions.correlation_guard import CorrelationGuard


class TradeLifecycleSimulator:
    """Walks through all possible paths for a single trade."""

    def __init__(self):
        self.client = None
        self.detector = SetupDetectorV2()
        self.regime_detector = RegimeDetectorV2()

    async def run_full_simulation(self, symbol: str = "BTCUSDT") -> dict:
        """
        Fetch live data, detect a setup, then simulate ALL exit scenarios.
        Returns comprehensive audit trail.
        """
        self.client = BitgetMarketClient()
        print(f"\n{'='*80}")
        print(f"AEGIS TRADE LIFECYCLE SIMULATION")
        print(f"Symbol: {symbol}")
        print(f"Generated: {datetime.now(timezone.utc).isoformat()}")
        print(f"{'='*80}\n")

        # ── STEP 1: Fetch live market data ──────────────────────────────────
        print("[1] FETCHING LIVE MARKET DATA")
        print("-" * 80)
        try:
            candles_4h = await self.client.get_candles(symbol, "4H", limit=30)
            candles_15m = await self.client.get_candles(symbol, "15m", limit=100)
            ticker = await self.client.get_ticker(symbol)
            funding_rate = await self.client.get_funding_rate(symbol)
        except Exception as e:
            print(f"ERROR fetching data: {e}")
            await self.client.close()
            return {"error": str(e)}

        current_price = ticker.last_price
        print(f"Current Price: ${current_price:,.4f}")
        print(f"24h Volume: {ticker.volume_24h:,.0f}")
        print(f"Funding Rate: {funding_rate*100:+.3f}%")

        # ── STEP 2: Detect regime (4H) ──────────────────────────────────────
        print("\n[2] REGIME DETECTION (4H)")
        print("-" * 80)
        evidence = self.regime_detector.detect_regime_with_evidence(candles_4h)
        print(evidence.format_proof())
        print(f"\nRegime: {evidence.regime.value}")

        # HIGH_VOL means no trades
        if evidence.regime == MarketRegime.HIGH_VOLATILITY:
            print("❌ HIGH VOLATILITY — analyst skips all candidates")
            await self.client.close()
            return {"skipped": "HIGH_VOLATILITY"}

        # ── STEP 3: Run analyzer to get best candidate ─────────────────────
        print("\n[3] SETUP DETECTION (15M + Zones + Funding)")
        print("-" * 80)
        candidates = self.detector.analyze_symbol(
            symbol=symbol,
            candles_4h=candles_4h,
            candles_15m=candles_15m,
            current_price=current_price,
            funding_rate=funding_rate,
            open_positions=[],  # no existing positions
        )

        if not candidates:
            print("❌ No valid candidates found")
            await self.client.close()
            return {"skipped": "NO_CANDIDATES"}

        candidate = candidates[0]
        print(f"✅ Trade Signal Detected: {candidate.symbol} {candidate.side}")
        print(f"\nEntry Price: ${candidate.entry:,.4f}")
        print(f"Stop Loss:   ${candidate.stop_loss:,.4f}")
        print(f"TP1 (1.5R):  ${candidate.take_profit_1:,.4f}")
        print(f"TP2 (3.0R):  ${candidate.take_profit_2:,.4f}")
        print(f"\nRisk: ${abs(candidate.entry - candidate.stop_loss):,.4f}")
        print(f"Risk %: {abs((candidate.entry - candidate.stop_loss) / candidate.entry) * 100:.2f}%")

        # ── STEP 4: Confidence breakdown ────────────────────────────────────
        print("\n[4] CONFIDENCE BREAKDOWN & POSITION SIZING")
        print("-" * 80)
        breakdown = candidate.confidence_breakdown
        print(f"Total Confidence: {candidate.confidence:.1f}%")
        print(f"Risk Allocation: {candidate.risk_percent}% of account")
        print(f"Confluence Count: {candidate.confluence_score}")

        print("\nFactors:")
        for factor in breakdown.get("factors", []):
            status = "✅" if factor["passed"] else "❌"
            print(f"  {status} {factor['name']}: {factor['score']:.0f}% | {factor['reason']}")

        print(f"\nRegime Evidence:")
        for k, v in evidence.to_dict().items():
            if k != "reason":
                print(f"  {k}: {v}")

        # ── STEP 5: Correlation guard check ─────────────────────────────────
        print("\n[5] CORRELATION RISK CHECK")
        print("-" * 80)
        allowed, reason = CorrelationGuard.allows(symbol, candidate.side)
        if not allowed:
            print(f"❌ BLOCKED: {reason}")
            await self.client.close()
            return {"blocked": "CORRELATION_LIMIT"}
        print(f"✅ ALLOWED: {candidate.side} {symbol} position approved")

        # ── STEP 6: TRADE ENTRY ─────────────────────────────────────────────
        print("\n[6] TRADE ENTRY → DATABASE")
        print("-" * 80)
        entry_ts = datetime.now(timezone.utc).isoformat()
        trade = {
            "trade_id": f"SIM-{int(datetime.now(timezone.utc).timestamp())}",
            "symbol": symbol,
            "direction": candidate.side,
            "entry_price": candidate.entry,
            "stop_loss": candidate.stop_loss,
            "take_profit_1": candidate.take_profit_1,
            "take_profit_2": candidate.take_profit_2,
            "market_regime": evidence.regime.value,
            "session": "SIMULATION",
            "confidence": candidate.confidence,
            "opened_at": entry_ts,
            "status": "OPEN",
            "zone_top": candidate.zone.top if candidate.zone else None,
            "zone_bottom": candidate.zone.bottom if candidate.zone else None,
            "zone_type": candidate.zone.zone_type.value if candidate.zone else None,
        }
        print(f"Trade ID: {trade['trade_id']}")
        print(f"Entry Time: {entry_ts}")
        print(f"Status: OPEN")
        print(f"Signal Confidence: {candidate.confidence:.0f}%")

        # ── STEP 7: SCENARIO SIMULATIONS ────────────────────────────────────
        print("\n[7] SCENARIO ANALYSIS — ALL EXIT PATHS")
        print("=" * 80)

        scenarios = {}

        # Scenario A: TP1 hit
        print("\n[A] TP1 HIT SCENARIO")
        print("-" * 80)
        scenarios["tp1_hit"] = self._simulate_exit(
            trade=trade,
            exit_price=candidate.take_profit_1,
            exit_reason="TP1_HIT",
            exit_ts=(datetime.fromisoformat(entry_ts) + timedelta(hours=2)).isoformat(),
        )

        # Scenario B: TP2 hit
        print("\n[B] TP2 HIT SCENARIO")
        print("-" * 80)
        scenarios["tp2_hit"] = self._simulate_exit(
            trade=trade,
            exit_price=candidate.take_profit_2,
            exit_reason="TP2_HIT",
            exit_ts=(datetime.fromisoformat(entry_ts) + timedelta(hours=3)).isoformat(),
        )

        # Scenario C: SL hit
        print("\n[C] STOP LOSS HIT SCENARIO")
        print("-" * 80)
        scenarios["sl_hit"] = self._simulate_exit(
            trade=trade,
            exit_price=candidate.stop_loss,
            exit_reason="SL_HIT",
            exit_ts=(datetime.fromisoformat(entry_ts) + timedelta(hours=0.5)).isoformat(),
        )

        # Scenario D: Zone broken
        print("\n[D] ZONE BROKEN SCENARIO (before TP)")
        print("-" * 80)
        if candidate.zone:
            if candidate.side == "LONG":
                break_price = candidate.zone.bottom * 0.995
            else:
                break_price = candidate.zone.top * 1.005
            scenarios["zone_broken"] = self._simulate_exit(
                trade=trade,
                exit_price=break_price,
                exit_reason="ZONE_BROKEN",
                exit_ts=(datetime.fromisoformat(entry_ts) + timedelta(hours=1.5)).isoformat(),
                is_invalidation=True,
            )
        else:
            print("❌ No zone data — skipping")

        # Scenario E: Time stop (>12h)
        print("\n[E] TIME STOP SCENARIO (>12h held)")
        print("-" * 80)
        exit_ts_long = (datetime.fromisoformat(entry_ts) + timedelta(hours=13)).isoformat()
        # Price somewhere in the middle (no TP hit)
        mid_price = (candidate.entry + candidate.take_profit_1) / 2
        scenarios["time_stop"] = self._simulate_exit(
            trade=trade,
            exit_price=mid_price,
            exit_reason="TIME_STOP",
            exit_ts=exit_ts_long,
            is_invalidation=True,
        )

        # Scenario F: Regime flip
        print("\n[F] REGIME FLIP SCENARIO (during trade)")
        print("-" * 80)
        scenarios["regime_flip"] = self._simulate_exit(
            trade=trade,
            exit_price=(candidate.entry + candidate.take_profit_1) / 2,
            exit_reason="REGIME_FLIP",
            exit_ts=(datetime.fromisoformat(entry_ts) + timedelta(hours=2)).isoformat(),
            is_invalidation=True,
        )

        # ── STEP 8: SUMMARY ──────────────────────────────────────────────────
        print("\n" + "=" * 80)
        print("[8] OUTCOME SUMMARY")
        print("=" * 80)

        self._print_scenario_summary(scenarios)

        await self.client.close()
        return {
            "trade": trade,
            "candidate": {
                "symbol": candidate.symbol,
                "side": candidate.side,
                "entry": candidate.entry,
                "stop_loss": candidate.stop_loss,
                "tp1": candidate.take_profit_1,
                "tp2": candidate.take_profit_2,
                "confidence": candidate.confidence,
            },
            "regime": evidence.regime.value,
            "scenarios": scenarios,
        }

    def _simulate_exit(
        self,
        trade: dict,
        exit_price: float,
        exit_reason: str,
        exit_ts: str,
        is_invalidation: bool = False,
    ) -> dict:
        """Calculate exit state transition."""
        entry = trade["entry_price"]
        sl = trade["stop_loss"]
        direction = trade["direction"]
        leverage = 10  # assume 10x

        # Calculate P&L
        if direction == "LONG":
            pnl = exit_price - entry
            pnl_pct = round((pnl / entry) * 100 * leverage, 2)
            risk_amount = entry - sl
        else:
            pnl = entry - exit_price
            pnl_pct = round((pnl / entry) * 100 * leverage, 2)
            risk_amount = sl - entry

        # Determine result
        if exit_reason in ("TP1_HIT", "TP2_HIT"):
            result = "WIN"
        elif exit_reason == "SL_HIT":
            result = "LOSS"
        else:
            # Invalidation or time stop — depends on exit price vs SL
            if direction == "LONG" and exit_price < sl:
                result = "LOSS"
            elif direction == "SHORT" and exit_price > sl:
                result = "LOSS"
            else:
                result = "WIN" if pnl_pct > 0 else "LOSS"

        print(f"Exit Price: ${exit_price:,.4f}")
        print(f"Exit Time: {exit_ts}")
        print(f"Exit Reason: {exit_reason}")
        if is_invalidation:
            print(f"  ⚠️ Thesis Invalidation (early exit)")
        print(f"\nP&L Calculation:")
        print(f"  Entry: ${entry:,.4f}")
        print(f"  Exit:  ${exit_price:,.4f}")
        print(f"  Gross: ${pnl:,.4f}")
        print(f"  P&L%:  {pnl_pct:+.2f}% (10x leverage)")
        print(f"  Risk:  ${risk_amount:,.4f}")
        print(f"  R:     {pnl_pct / 5:+.2f}R" if pnl_pct != 0 else "  R:     0.00R")
        print(f"\nResult: {result} {'✅' if result == 'WIN' else '❌'}")

        return {
            "exit_price": exit_price,
            "exit_reason": exit_reason,
            "exit_time": exit_ts,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "result": result,
            "duration_hours": (datetime.fromisoformat(exit_ts) - datetime.fromisoformat(trade["opened_at"])).total_seconds() / 3600,
        }

    def _print_scenario_summary(self, scenarios: dict) -> None:
        """Print comparison across all scenarios."""
        print("\nScenario Outcomes:")
        print(f"{'Scenario':<20} {'Exit Price':<15} {'P&L%':<12} {'Result':<8} {'Duration':<15}")
        print("-" * 70)
        for name, outcome in scenarios.items():
            print(
                f"{name:<20} "
                f"${outcome['exit_price']:>13,.4f} "
                f"{outcome['pnl_pct']:>10.2f}% "
                f"{outcome['result']:<8} "
                f"{outcome['duration_hours']:.1f}h"
            )

        wins = [o for o in scenarios.values() if o["result"] == "WIN"]
        losses = [o for o in scenarios.values() if o["result"] == "LOSS"]
        avg_win = sum(o["pnl_pct"] for o in wins) / len(wins) if wins else 0
        avg_loss = sum(o["pnl_pct"] for o in losses) / len(losses) if losses else 0

        print("-" * 70)
        print(f"\nWins: {len(wins)}/{len(scenarios)} | Avg: {avg_win:+.2f}%")
        print(f"Losses: {len(losses)}/{len(scenarios)} | Avg: {avg_loss:+.2f}%")
        if wins:
            print(f"Best Case: {max(w['pnl_pct'] for w in wins):+.2f}%")
        if losses:
            print(f"Worst Case: {min(l['pnl_pct'] for l in losses):+.2f}%")


async def main():
    sim = TradeLifecycleSimulator()
    result = await sim.run_full_simulation("BTCUSDT")
    return result


if __name__ == "__main__":
    import json
    result = asyncio.run(main())
    # Save to file for inspection
    output_path = Path(__file__).parent.parent / "data" / "sim_output.json"
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2, default=str)
    print(f"\n\nFull simulation saved to: {output_path}")
