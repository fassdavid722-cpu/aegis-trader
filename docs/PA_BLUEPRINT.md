# PURE PRICE ACTION ANALYST ENGINE
## Aegis Trader v2.0 - Strategy Specification

---

## PHILOSOPHY

**No indicators. No lag. No order book spoofing.**

This engine uses:
1. **Price structure** (swings, BOS, CHOCH, zones)
2. **Funding rate** (hard to manipulate, real cost)
3. **Session timing** (high-probability windows only)

---

## FULL SIGNAL FLOW (Every 5 Minutes)

```
STEP 1: Check session
        Is it London (07-09 UTC) or NY (13-15 UTC)?
        NO → Skip scan, wait
        YES → Continue

STEP 2: Check daily limits
        Daily loss < -3%? → HALT
        Trades today >= 3? → HALT

STEP 3: Fetch dual timeframes
        4H candles → Regime detection
        15min candles → Structure + zones

STEP 4: Classify regime (4H)
        HH + HL → BULL_TREND (long bias only)
        LH + LL → BEAR_TREND (short bias only)
        Range boundaries → SIDEWAYS (both directions)
        Candle 3x avg → HIGH_VOLATILITY (skip)

STEP 5: Find supply/demand zones (15min)
        Demand: strong bullish candle body
        Supply: strong bearish candle body
        Filter: only fresh zones (not violated)

STEP 6: Detect structure (15min)
        BOS_BULL: price breaks above last swing high
        BOS_BEAR: price breaks below last swing low
        CHOCH_BULL: in downtrend, breaks above last lower high
        CHOCH_BEAR: in uptrend, breaks below last higher low

STEP 7: Check funding rate
        > +0.1% → OVERLEVERAGED_LONG → favor shorts
        < -0.1% → OVERLEVERAGED_SHORT → favor longs
        -0.05% to +0.05% → NEUTRAL
        > +0.3% or < -0.3% → EXTREME_SQUEEZE → counter-trend

STEP 8: Check confluence (ALL must align)
        - Price at valid zone
        - Rejection candle confirmed
        - Structure supports direction
        - Regime allows direction
        - Funding aligns or is extreme
        - Session is London or NY
        - Confluence score >= 3/5

STEP 9: Generate candidate
        Entry: current price
        SL: below/above zone
        TP1: 1.5R (50% exit)
        TP2: 3R (50% exit)
        Risk: 1.5% account

STEP 10: Submit to pipeline
        Convert to Signal → PositionManager → MarketMonitor → Journal
```

---

## CONFLUENCE SCORING

| Factor | Points | Condition |
|--------|--------|-----------|
| Regime | +1 | Clear bull/bear/sideways (not unknown) |
| Structure | +1 | Valid BOS or CHOCH detected |
| Fresh Zone | +1 | Zone not yet violated |
| Session | +1 | London or NY open |
| Funding | +1 | Overleveraged or extreme squeeze |
| **Minimum** | **3** | Need 3+ to generate signal |

---

## RISK MANAGEMENT

| Parameter | Value |
|-----------|-------|
| Risk per trade | 1.5% account |
| Stop Loss | Below/above entry zone |
| TP1 | 1.5R (close 50%) |
| TP2 | 3R (close 50%) |
| Max trades/day | 3 |
| Daily loss limit | -3% (halt trading) |
| High volatility | Skip trades |
| Leverage | 10x default |

---

## MODULES

| Module | Purpose |
|--------|---------|
| `models_v2.py` | Data models: regimes, sessions, structures, zones, candidates |
| `price_structure.py` | Swing detection, BOS/CHOCH, supply/demand zones |
| `regime_detector_v2.py` | 4H regime: HH/HL, LH/LL, range, high vol |
| `session_filter.py` | UTC session detection (London/NY/Asia/Futures) |
| `funding_filter.py` | Funding rate interpretation and trade alignment |
| `setup_detector_v2.py` | Full 6-step confluence logic |
| `market_scanner_v2.py` | Dual timeframe scanning (4H + 15min) |
| `signal_bridge_v2.py` | Converts candidates to canonical Signals |

---

## TELEGRAM FORMAT

```
Analyst Signal: LONG BTCUSDT
Entry: 67250.00
SL: 66800.00 | TP1: 67825.00 | TP2: 68575.00
Regime: BULL_TREND
Session: LONDON
Structure: BOS_BULL
Funding: OVERLEVERAGED_SHORT
Zone: DEMAND 67000-67200
Confluence: 4/5 | Confidence: 75%
Thesis: LONG at DEMAND zone. Regime: BULL_TREND | Structure: BOS_BULL | Fresh zone | Session: LONDON | Funding: OVERLEVERAGED_SHORT
```

---

## WHAT MAKES THIS DIFFERENT

| Other Bots | This Engine |
|-----------|-------------|
| Lagging indicators (EMA, RSI) | Pure price structure |
| Order book reading (spoofable) | Ignores order book |
| 24/7 random trading | Session-filtered entries |
| One-size-fits-all | Regime-adaptive |
| No external data | Funding rate as filter |
| No loss protection | Hard daily stop |
| Single TP | Dual TP system (1.5R + 3R) |

---

## FILES

- `analyst/models_v2.py` — 270 lines
- `analyst/price_structure.py` — 290 lines
- `analyst/regime_detector_v2.py` — 120 lines
- `analyst/session_filter.py` — 70 lines
- `analyst/funding_filter.py` — 90 lines
- `analyst/setup_detector_v2.py` — 280 lines
- `analyst/market_scanner_v2.py` — 200 lines
- `analyst/signal_bridge_v2.py` — 80 lines
- `tests/test_price_action.py` — 200 lines
- `app.py` — Updated orchestrator

**Total: ~1,600 lines of pure price action logic**
