# Aegis Trader Architecture

## Overview

Aegis Trader is a futures trading intelligence engine that collects structured trading experience from signals and turns it into a learning database.

## Core Philosophy

- The journal is the primary asset
- The trade history is the memory
- The analysis layer is secondary
- V1 must not place live orders

## System Layers

```
┌─────────────────────────────────────────┐
│  NOTIFICATION LAYER (Telegram)        │
│  • Receive signals                      │
│  • Send status updates                  │
└─────────────────────────────────────────┘
                    ↑↓
┌─────────────────────────────────────────┐
│  SIGNAL LAYER                           │
│  • Parse Telegram text                  │
│  • Parse webhook JSON                   │
│  • Normalize to canonical Signal        │
└─────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────┐
│  POSITION LAYER                         │
│  • State machine (strict transitions)   │
│  • Virtual position manager             │
│  • Fill model (slippage-based)          │
└─────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────┐
│  EXCHANGE LAYER                         │
│  • Bitget REST market data              │
│  • Price monitoring (polling)           │
│  • NO order execution                   │
└─────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────┐
│  JOURNAL LAYER                          │
│  • SQLite database                      │
│  • Append-only records                  │
│  • Audit trail of state transitions     │
└─────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────┐
│  COACH LAYER                            │
│  • Regime detection                     │
│  • Trade analysis (5 categories)        │
│  • Structured lessons                   │
└─────────────────────────────────────────┘
```

## Data Flow

1. Signal arrives (Telegram or webhook)
2. Parser normalizes to canonical Signal
3. Position manager creates virtual position (PENDING)
4. Market monitor fetches live Bitget price
5. Fill model activates position (PENDING → OPEN)
6. Price monitor watches for TP/SL/liquidation
7. Position closes (OPEN → CLOSED)
8. Coach analyzes the trade
9. Journal records everything
10. Telegram notifies user

## State Machine

```
IDLE → PENDING → OPEN → CLOSED
         ↓         ↓
      EXPIRED   LIQUIDATED
         ↓
      INVALID
```

## Key Design Decisions

- **Virtual execution**: No real orders. Simulated positions against live prices.
- **Standardized notional**: 100 USDT for consistent PnL comparison across leverages.
- **Futures-aware**: Handles long/short, leverage, margin mode, liquidation, funding.
- **Defensive parsing**: Missing fields → null. Malformed input → logged, not crashed.
- **Append-only journal**: Historical records are never modified.
