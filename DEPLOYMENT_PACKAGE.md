# AEGIS TRADER — COMPLETE DEPLOYMENT PACKAGE
## Version: 2.0 (Analyst Engine + Invalidation + Correlation Guard)
## Generated: 2026-06-20

This document contains everything needed to deploy Aegis Trader on any Linux server
**without access to Base44**. A developer who has never seen this project before
should be able to run it within 30 minutes using only this document.

---

## ARCHITECTURE OVERVIEW

```
Bitget Market Data (public WebSocket / REST)
        ↓
  Exchange Client (exchange/bitget_client.py)
        ↓
  ┌─────────────────────────────────────────┐
  │         ANALYST ENGINE V2               │
  │  RegimeDetectorV2  (4H timeframe)       │
  │  PriceStructureAnalyzer (15m)           │
  │  SetupDetectorV2 (zone + structure)     │
  │  ConfidenceEngine (explainable score)   │
  │  FundingFilter (regime context)         │
  │  SessionFilter (London/NY only)         │
  └─────────────────────────────────────────┘
        ↓
  CorrelationGuard (prevents correlated overexposure)
        ↓
  SQLite Journal (data/journal.db)
        ↓
  ┌─────────────────────────────────────────┐
  │         POSITION MONITOR                │
  │  Polls every 30s when positions open    │
  │  ThesisInvalidationEngine checks:       │
  │    - Zone break (HIGH severity → exit)  │
  │    - Regime flip (HIGH → exit)          │
  │    - Funding flip (LOW → warn only)     │
  │    - Time stop 12h (MEDIUM → exit)      │
  │  SL / TP1 / TP2 price checks           │
  └─────────────────────────────────────────┘
        ↓
  Coach Engine (post-trade learning)
        ↓
  Pattern Library (SQLite — pattern_library table)
        ↓
  Telegram Alerts (every event)
```

### Key Design Decisions
| Decision | Rationale |
|---|---|
| Session-only scanning | London 07:00–09:00 UTC, NY 13:00–15:00 UTC — highest institutional liquidity |
| 4H regime + 15m structure | 4H = context / directional gate, 15m = zone and entry decision |
| Confidence gate ≥60%, confluence ≥3/5 | Minimum bar; most live setups score 90–95% |
| Hard SL always set | Machine stops — no discretion on losses |
| Correlation cap 1 BTC + 1 ALT | Prevents correlated overexposure in crypto |
| Time stop 12h | Intraday mandate — no accidental swings |

---

## WHAT YOU NEED TO RUN THIS

### Hardware
- Any VPS with 512MB RAM minimum (1GB recommended)
- 1 CPU core minimum
- Ubuntu 22.04 or 24.04 (or any modern Linux)

### Software
- Python 3.11 or 3.12
- pip
- git (optional, for version control)

---

## ENVIRONMENT VARIABLES (required)

Create a `.env` file in the project root (`analyst-engine/aegis-trader/.env`):

```env
# Telegram — Aegis sends all alerts here
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here

# Bitget API — used for live trading (NOT needed for market data / paper trading)
BITGET_API_KEY=your_api_key
BITGET_SECRET_KEY=your_secret_key
BITGET_PASSPHRASE=your_passphrase

# Database path (optional — defaults to data/journal.db)
DB_PATH=data/journal.db
```

**How to get Telegram credentials:**
1. Message @BotFather on Telegram → /newbot → copy the token
2. Your chat ID: message @userinfobot or use `getUpdates` API endpoint
3. Chat ID in this deployment: `6472746064`

**How to get Bitget API keys:**
1. Bitget.com → Settings → API Management
2. Create API key with "Read" + "Futures Trade" permissions
3. IP whitelist your server IP for security

---

## INSTALLATION

```bash
# 1. Get the code
git clone <your_repo_url> aegis-trader
cd aegis-trader/analyst-engine/aegis-trader

# OR: unzip the exported package
unzip aegis-trader.zip
cd aegis-trader

# 2. Create virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create .env file (see section above)
cp .env.example .env
nano .env   # fill in your credentials

# 5. Initialize database
python3 -c "from database.connection import init_database, set_db_path; set_db_path('data/journal.db'); init_database('data/journal.db')"

# 6. Test the system
python3 run_analyst.py
# Should print: [Health] Bitget: $XX,XXX
# Should print: [Monitor] no open positions
# Should print: [Heartbeat] Complete
```

---

## RUNNING CONTINUOUSLY

### Option A: systemd service (recommended for production)

```bash
# Create service file
sudo nano /etc/systemd/system/aegis-trader.service
```

```ini
[Unit]
Description=Aegis Trader Heartbeat
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/aegis-trader/analyst-engine/aegis-trader
Environment=PATH=/home/ubuntu/aegis-trader/venv/bin
ExecStart=/home/ubuntu/aegis-trader/venv/bin/python3 -c "
import asyncio
import sys
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv('.env', override=True)
from database.connection import init_database, set_db_path
set_db_path('data/journal.db')
init_database('data/journal.db')

import time
import subprocess

while True:
    subprocess.run(['python3', 'run_analyst.py'])
    time.sleep(300)  # run every 5 minutes
"
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable aegis-trader
sudo systemctl start aegis-trader
sudo systemctl status aegis-trader
sudo journalctl -u aegis-trader -f  # view logs
```

### Option B: cron job (simpler)

```bash
crontab -e
# Add:
*/5 * * * * cd /home/ubuntu/aegis-trader/analyst-engine/aegis-trader && /home/ubuntu/aegis-trader/venv/bin/python3 run_analyst.py >> /var/log/aegis.log 2>&1
```

### Option C: screen/tmux session (for testing)

```bash
screen -S aegis
# Inside screen:
cd analyst-engine/aegis-trader
source venv/bin/activate
while true; do python3 run_analyst.py; sleep 300; done
# Ctrl+A then D to detach
```

---

## FILE STRUCTURE

```
aegis-trader/
├── run_analyst.py          ← MAIN ENTRY POINT — run this every 5 minutes
├── requirements.txt
├── .env                    ← credentials (never commit this)
├── data/
│   └── journal.db          ← SQLite database (auto-created)
│
├── analyst/                ← Core intelligence
│   ├── setup_detector_v2.py    ← Main setup detection logic
│   ├── regime_detector_v2.py   ← 4H regime: BULL/BEAR/SIDEWAYS/HIGH_VOL
│   ├── price_structure.py      ← Swing points, BOS, CHOCH, demand/supply zones
│   ├── confidence_engine.py    ← Explainable confidence score (50 base + factors)
│   ├── session_filter.py       ← London/NY session gating
│   ├── funding_filter.py       ← Funding rate interpretation
│   └── models_v2.py            ← Pydantic data models
│
├── positions/              ← Position lifecycle
│   ├── monitor.py              ← SL/TP/invalidation checks (30s when open)
│   ├── invalidation.py         ← ThesisInvalidationEngine (4 triggers)
│   ├── correlation_guard.py    ← Prevents correlated overexposure
│   ├── state_machine.py        ← PENDING→OPEN→CLOSED transitions
│   └── position_manager.py     ← Position sizing
│
├── exchange/               ← Market data
│   ├── bitget_client.py        ← Bitget REST + WebSocket client
│   └── candle_normalizer.py
│
├── database/               ← Persistence
│   ├── connection.py           ← SQLite connection + init
│   └── schema.sql              ← Full schema (see below)
│
├── coach/                  ← Post-trade learning
│   ├── coach_engine.py         ← Generates Why/Lesson for every closed trade
│   └── trade_analyzer.py
│
├── journal/                ← Pattern library
│   └── pattern_library.py      ← Records outcomes, tracks setup win rates
│
└── signals/                ← External signal handling (Telegram/webhook)
    ├── telegram_listener.py
    └── webhook_listener.py
```

---

## DATABASE SCHEMA

```sql
-- Every trade signal generated by the analyst
CREATE TABLE signals (
    signal_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,           -- 'telegram' | 'webhook'
    raw_text TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,             -- 'LONG' | 'SHORT'
    entry REAL,
    stop_loss REAL,
    take_profit REAL,
    leverage INTEGER DEFAULT 10,
    confidence REAL,
    metadata TEXT,                  -- JSON: confidence_breakdown, regime_evidence, zone geometry
    timestamp TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

-- Every opened trade (state machine: PENDING → OPEN → CLOSED)
CREATE TABLE trades (
    trade_id TEXT PRIMARY KEY,
    signal_id TEXT REFERENCES signals(signal_id),
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,        -- 'LONG' | 'SHORT'
    leverage INTEGER NOT NULL,
    entry_price REAL NOT NULL,
    stop_loss REAL,
    take_profit REAL,               -- TP2 (full exit)
    status TEXT NOT NULL,           -- 'PENDING' | 'OPEN' | 'PARTIAL' | 'CLOSED'
    opened_at TEXT,
    closed_at TEXT,
    exit_price REAL,
    exit_reason TEXT,               -- 'TP_HIT' | 'SL_HIT' | 'MANUAL_CLOSE' (zone break, time stop)
    result TEXT,                    -- 'WIN' | 'LOSS' | 'BREAKEVEN'
    pnl_percent REAL,
    market_regime TEXT,
    confidence_score REAL,
    expected_r REAL,
    actual_r REAL,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Every state change recorded (full audit trail)
CREATE TABLE state_transitions (
    transition_id INTEGER PRIMARY KEY,
    trade_id TEXT REFERENCES trades(trade_id),
    from_state TEXT NOT NULL,
    to_state TEXT NOT NULL,
    trigger TEXT NOT NULL,          -- what caused the transition
    price_at_transition REAL,
    timestamp TEXT DEFAULT (datetime('now'))
);

-- Post-trade coach analysis
CREATE TABLE trade_analysis (
    analysis_id INTEGER PRIMARY KEY,
    trade_id TEXT UNIQUE REFERENCES trades(trade_id),
    summary TEXT,
    trade_quality TEXT,             -- 'valid' | 'invalid' | 'mixed'
    regime_quality TEXT,
    execution_quality TEXT,
    lessons TEXT,                   -- JSON array of lesson strings
    confidence REAL,
    created_at TEXT DEFAULT (datetime('now'))
);
```

---

## CONFIDENCE SCORE SYSTEM

**Formula:** `score = 50 (base) + sum(factor deltas)`

| Factor | Condition | Points |
|---|---|---|
| Demand/Supply Zone | Zone is FRESH (untested) | +25 |
| Demand/Supply Zone | Zone touched 1–2 times | +10 |
| Session | London (07:00–09:00 UTC) | +15 |
| Session | NY (13:00–15:00 UTC) | +10 |
| Structure | BOS/CHOCH aligns with direction | +15 |
| Regime | Trend confirms direction | +15 |
| Funding | Neutral (no squeeze risk) | +5 |
| Funding | Extreme squeeze (contrarian) | +5 |
| Funding | Overleveraged against direction | −15 |

**Confluence gate:** minimum 3/5 positive factors to trade
**Confidence bands:** 60–69% → 0.5% risk | 70–79% → 1.0% | 80–89% → 1.5% | 90%+ → 2.0%

**KNOWN ISSUE:** 77% of all tradeable combinations score 90–95%.
Score has only 8 distinct values (60, 65, 70, 75, 80, 85, 90, 95).
This means confidence is NOT meaningfully differentiating quality at the high end.
Future fix: add volume confirmation, multi-timeframe confluence, or spread scores.

---

## INVALIDATION ENGINE

Located: `positions/invalidation.py`

Four triggers checked on every 30-second monitor cycle while a trade is open:

| Trigger | Severity | Condition | Action |
|---|---|---|---|
| ZONE_BROKEN | HIGH | Price closes 0.2% beyond zone boundary | Close trade immediately |
| REGIME_FLIP | HIGH | 4H regime flips against direction | Close trade immediately |
| FUNDING_FLIP | LOW | Funding exceeds 0.05% against direction | Log warning, hold |
| TIME_STOP | MEDIUM | Trade open > 12 hours with no TP | Close trade, free capital |

Severity HIGH and MEDIUM trigger early exit. LOW is a warning only.

---

## CORRELATION GUARD

Located: `positions/correlation_guard.py`

Asset buckets:
- BTC bucket: `{BTCUSDT}` — max 1 LONG, 1 SHORT simultaneously
- ALT bucket: `{ETHUSDT, SOLUSDT, XRPUSDT, DOGEUSDT, ADAUSDT, AVAXUSDT, LINKUSDT}` — max 1 LONG, 1 SHORT

This prevents opening BTCUSDT LONG + ETHUSDT LONG + SOLUSDT LONG (which is effectively 3× BTC beta).
Maximum simultaneous exposure: 1 BTC + 1 ALT = 2 positions max via correlation rules.

---

## OPERATIONAL RULES (hardcoded in run_analyst.py)

```python
MAX_DAILY_TRADES = 3          # Never open more than 3 trades per calendar day UTC
DAILY_LOSS_LIMIT = -3.0       # Stop trading day if drawdown exceeds -3%
LEVERAGE = 10                 # Fixed 10× for all trades
SCAN_SYMBOLS = [              # 8 symbols scanned every cycle
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT",
    "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT"
]
TRADE_SESSIONS = {
    "LONDON": (7, 9),         # UTC hours
    "NY": (13, 15),
}
```

---

## BACKTEST RESULTS (10-day baseline, June 2026)

⚠️ **These are raw baseline numbers — not optimized. Do not change the engine based on these.**

```
Setups generated:    19  (~13/week)
Avg R per trade:     -0.37R
Max single loss:     -1.00R

Exit breakdown:
  TP2 (full win):    3  (15.8%)
  SL hit:           16  (84.2%)
  TP1 reach rate:   42.1%

By Session:
  London:   9 trades  TP2: 33.3%  AvgR: +0.33
  NY:      10 trades  TP2:  0.0%  AvgR: -1.00

By Regime:
  BEAR_TREND:  8 trades  TP2: 37.5%  AvgR: +0.50
  BULL_TREND:  5 trades  TP2:  0.0%  AvgR: -1.00
  SIDEWAYS:    6 trades  TP2:  0.0%  AvgR: -1.00
```

**Interpretation:**
- 19 setups in 10 days across 6 symbols = low-frequency engine (intentional — quality over quantity)
- All 19 scored 90–95% (confirms confidence concentration problem above)
- London session shows positive expectancy (+0.33R), NY shows negative (-1.00R)
- BEAR_TREND regime outperforms — zones respected more in trending down markets
- NY session and BULL_TREND/SIDEWAYS need investigation before live trading
- Need 100+ trades before drawing statistical conclusions

---

## QUICK HEALTH CHECK

```bash
# Check system is working
python3 run_analyst.py
# Expected output:
# [HH:MM UTC] == Aegis Heartbeat ==
# [Health] Bitget: $XX,XXX.XX
# [Monitor] no open positions
# [Analyst] 0 raw candidate(s)  (if outside session)
# [Journal] Open: 0 | Today: 0/3 | Total: X | no closed trades
# [Heartbeat] Complete

# Check database
sqlite3 data/journal.db "SELECT count(*) FROM trades; SELECT count(*) FROM signals;"

# Check Telegram is working
python3 -c "
import os; from dotenv import load_dotenv; load_dotenv('.env')
import urllib.request, urllib.parse
token = os.getenv('TELEGRAM_BOT_TOKEN')
chat  = os.getenv('TELEGRAM_CHAT_ID')
url   = f'https://api.telegram.org/bot{token}/sendMessage'
data  = urllib.parse.urlencode({'chat_id': chat, 'text': 'Aegis health check OK'}).encode()
print(urllib.request.urlopen(url, data).read())
"
```

---

## TROUBLESHOOTING

| Symptom | Cause | Fix |
|---|---|---|
| `AttributeError: NEW_YORK` | Old session enum reference | Use `Session.NY` not `Session.NEW_YORK` |
| `ValidationError: created_at required` | PriceZone missing fields | Pass `created_at`, `source_candle_*` fields |
| `TELEGRAM_CHAT_ID not configured` | Missing env var | Add to `.env` file |
| No setups generated | Outside session window OR price not in zone | Normal behaviour — wait for London/NY |
| All confidence scores are 90–95% | Known architectural issue | See "Confidence Score System" section |

---

## CREDITS

Built in ~24 hours using Base44 Superagent (AI coding assistant).
Architecture designed collaboratively by the founder and the AI.
All trading logic is deterministic and auditable — no black boxes.

The next milestone: **100 closed trades → statistical edge validation.**
