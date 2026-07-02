# AEGIS TRADER — COMPLETE BUILD BLUEPRINT
# Version: 1.0.0
# Date: 2026-06-19
# Target: Base44 Code Generation
# Purpose: Futures Trading Intelligence Engine (V1 — No Live Execution)

# =============================================================================
# SECTION 0: PROJECT OVERVIEW
# =============================================================================

## One-Sentence Summary
Build a futures trading intelligence engine that turns Telegram signals into 
structured virtual futures trades, monitors them with Bitget market data, 
stores the full history in a database, and learns from outcomes without 
risking real capital.

## What This Is NOT
- NOT a spot trader
- NOT a chatbot
- NOT a live-money execution bot in V1
- NOT a web app
- NOT a general-purpose AI assistant

## What This IS
- A futures signal intelligence system
- A virtual position tracker (no real orders)
- A learning database builder
- A backend system with Telegram I/O
- A system that collects structured trading experience

# =============================================================================
# SECTION 1: HARD RULES (Non-Negotiable)
# =============================================================================

1. No live order execution in V1
2. No real-money trading in V1
3. No leverage abuse or reckless sizing
4. No silent assumptions about signal format
5. No rewriting or deleting historical trade records
6. No pretending spot and futures are the same thing
7. No web app dependency
8. No "AI magic" without audit trails
9. No code that requires demo trading on Bitget if unavailable
10. No module coupling that makes later updates difficult
11. If a detail is missing, log it or set it to null
12. Do not invent fake values

# =============================================================================
# SECTION 2: TECH STACK
# =============================================================================

- Python 3.12+
- SQLite (V1 database)
- pydantic v2 (data validation)
- python-telegram-bot v21+ (async)
- httpx (REST client)
- websockets (future V2)
- python-dotenv (config)
- pytest + pytest-asyncio (testing)
- structlog (structured logging)

# =============================================================================
# SECTION 3: REPOSITORY STRUCTURE
# =============================================================================

aegis-trader/
├── app.py                          # Main entry point / orchestrator
├── requirements.txt                # Dependencies
├── .env.example                    # Environment template
├── .gitignore                      # Git ignore rules
│
├── config/
│   ├── __init__.py
│   ├── settings.py                 # Central config (dataclasses, env vars)
│   └── secrets.py                  # Secret validation helpers
│
├── signals/
│   ├── __init__.py
│   ├── models.py                   # Signal, VirtualPosition, MarketSnapshot
│   ├── parser.py                   # Telegram + webhook signal parser
│   └── telegram_listener.py        # Telegram bot (async, PTB v21+)
│
├── positions/
│   ├── __init__.py
│   ├── state_machine.py            # Strict state transitions + audit log
│   └── position_manager.py         # Virtual position CRUD + lifecycle
│
├── exchange/
│   ├── __init__.py
│   ├── bitget_client.py            # Bitget REST market data (read-only)
│   └── market_monitor.py         # Price → position check bridge
│
├── journal/
│   ├── __init__.py
│   ├── models.py                   # JournalWriter + JournalReader
│   └── analysis_models.py        # Structured analysis output models
│
├── coach/
│   ├── __init__.py
│   ├── coach_engine.py             # Orchestrator: runs analysis after close
│   ├── trade_analyzer.py           # 5-category outcome classification
│   └── regime_detector.py          # Heuristic regime detection
│
├── database/
│   ├── __init__.py
│   ├── connection.py               # SQLite connection manager (thread-local)
│   ├── schema.sql                  # Full DDL: tables, indexes, views
│   └── migrations.py               # Schema version tracking (future)
│
├── tests/
│   ├── __init__.py
│   ├── test_parser.py              # Signal parser tests
│   ├── test_state_machine.py       # State transition tests
│   ├── test_position_manager.py    # Position lifecycle tests
│   ├── test_journal.py             # Database write/read tests
│   ├── test_regime_detector.py     # Regime classification tests
│   └── test_coach.py               # Post-trade analysis tests
│
└── docs/
    └── architecture.md             # This blueprint (human-readable)

# =============================================================================
# SECTION 4: DATA MODELS (Pydantic, All Type-Hinted)
# =============================================================================

## 4.1 Enums

```python
class SignalSource(str, Enum):
    TELEGRAM = "telegram"
    WEBHOOK = "webhook"

class TradeSide(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    CLOSE = "CLOSE"

class MarginMode(str, Enum):
    ISOLATED = "ISOLATED"
    CROSS = "CROSS"

class TradeStatus(str, Enum):
    PENDING = "PENDING"      # Signal received, waiting for fill conditions
    OPEN = "OPEN"            # Virtual position active
    CLOSED = "CLOSED"        # Position closed (TP, SL, manual)
    EXPIRED = "EXPIRED"      # Entry time decay expired
    INVALID = "INVALID"      # Signal could not be parsed or processed

class ExitReason(str, Enum):
    TP_HIT = "TP_HIT"
    SL_HIT = "SL_HIT"
    MANUAL_CLOSE = "MANUAL_CLOSE"
    SIGNAL_CLOSE = "SIGNAL_CLOSE"
    LIQUIDATED = "LIQUIDATED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"

class TradeResult(str, Enum):
    WIN = "WIN"
    LOSS = "LOSS"
    BREAKEVEN = "BREAKEVEN"
    INVALID = "INVALID"

class MarketRegime(str, Enum):
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGING = "RANGING"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    UNKNOWN = "UNKNOWN"
```

## 4.2 Signal Model (Canonical)

```python
class Signal(BaseModel):
    signal_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source: SignalSource
    raw_text: str
    symbol: str = Field(..., pattern=r"^[A-Z0-9]+USDT$")
    contract_type: str = Field(default="PERPETUAL")
    side: TradeSide
    entry: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    leverage: int = Field(default=10, ge=1, le=125)
    margin_mode: MarginMode = Field(default=MarginMode.ISOLATED)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    confidence: Optional[float] = Field(default=None, ge=0, le=100)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def is_entry_signal(self) -> bool:
        return self.side in (TradeSide.LONG, TradeSide.SHORT)

    def is_close_signal(self) -> bool:
        return self.side == TradeSide.CLOSE

    def to_db_dict(self) -> dict[str, Any]: ...
```

## 4.3 VirtualPosition Model (Futures)

```python
class VirtualPosition(BaseModel):
    trade_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    signal_id: str
    symbol: str
    contract_type: str = Field(default="PERPETUAL")
    direction: TradeSide          # LONG or SHORT only (never CLOSE)
    leverage: int = Field(..., ge=1, le=125)
    margin_mode: MarginMode
    entry_price: float = Field(..., gt=0)
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    liquidation_price: Optional[float] = None
    margin_used: Optional[float] = None
    status: TradeStatus = Field(default=TradeStatus.PENDING)
    opened_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[ExitReason] = None
    result: Optional[TradeResult] = None
    pnl_percent: Optional[float] = None
    pnl_absolute: Optional[float] = None
    roi_percent: Optional[float] = None
    trading_fee: Optional[float] = None
    funding_fee: Optional[float] = None
    market_regime: Optional[MarketRegime] = None
    signal_raw: Optional[str] = None
    signal_source: Optional[str] = None
    setup_type: Optional[str] = None
    confidence_score: Optional[float] = None
    notes: Optional[str] = None

    # Runtime-only (not persisted to DB directly)
    max_favorable_excursion: float = 0.0
    max_adverse_excursion: float = 0.0

    def calculate_liquidation_price(self) -> Optional[float]: ...
    def calculate_margin(self, notional_value: float = 100.0) -> float: ...
    def check_exit(self, current_price: float) -> Optional[ExitReason]: ...
    def calculate_pnl(self, exit_price: float, fee_rate: float = 0.0006) -> dict[str, float]: ...
    def close(self, exit_price: float, reason: ExitReason, fee_rate: float = 0.0006) -> None: ...
    def to_db_dict(self) -> dict[str, Any]: ...
```

## 4.4 MarketSnapshot Model

```python
class MarketSnapshot(BaseModel):
    symbol: str
    price: float
    high_24h: Optional[float] = None
    low_24h: Optional[float] = None
    volume_24h: Optional[float] = None
    funding_rate: Optional[float] = None
    open_interest: Optional[float] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
```

## 4.5 TradeAnalysis Model (Coach Output)

```python
class TradeAnalysis(BaseModel):
    trade_id: str
    summary: str
    trade_quality: str = Field(..., pattern=r"^(valid|invalid|mixed)$")
    regime_quality: str = Field(..., pattern=r"^(favorable|unfavorable|mixed)$")
    execution_quality: str = Field(..., pattern=r"^(good|bad|unknown)$")
    lessons: list[str] = Field(default_factory=list)
    confidence: Optional[float] = Field(default=None, ge=0, le=100)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
```

# =============================================================================
# SECTION 5: DATABASE SCHEMA (SQLite, Append-Only)
# =============================================================================

## 5.1 Table: signals

```sql
CREATE TABLE IF NOT EXISTS signals (
    signal_id TEXT PRIMARY KEY,
    source TEXT NOT NULL CHECK(source IN ('telegram', 'webhook')),
    raw_text TEXT NOT NULL,
    symbol TEXT NOT NULL,
    contract_type TEXT DEFAULT 'PERPETUAL',
    side TEXT NOT NULL CHECK(side IN ('LONG', 'SHORT', 'CLOSE')),
    entry REAL,
    stop_loss REAL,
    take_profit REAL,
    leverage INTEGER DEFAULT 10,
    margin_mode TEXT DEFAULT 'ISOLATED' CHECK(margin_mode IN ('ISOLATED', 'CROSS')),
    timestamp TEXT NOT NULL,  -- ISO-8601
    confidence REAL,
    metadata TEXT,  -- JSON
    created_at TEXT DEFAULT (datetime('now'))
);
```

## 5.2 Table: trades (Virtual Positions)

```sql
CREATE TABLE IF NOT EXISTS trades (
    trade_id TEXT PRIMARY KEY,
    signal_id TEXT NOT NULL REFERENCES signals(signal_id),
    symbol TEXT NOT NULL,
    contract_type TEXT DEFAULT 'PERPETUAL',
    direction TEXT NOT NULL CHECK(direction IN ('LONG', 'SHORT')),
    leverage INTEGER NOT NULL,
    margin_mode TEXT NOT NULL CHECK(margin_mode IN ('ISOLATED', 'CROSS')),
    entry_price REAL NOT NULL,
    stop_loss REAL,
    take_profit REAL,
    liquidation_price REAL,
    margin_used REAL,
    status TEXT NOT NULL CHECK(status IN ('PENDING', 'OPEN', 'CLOSED', 'EXPIRED', 'INVALID')),
    opened_at TEXT,
    closed_at TEXT,
    exit_price REAL,
    exit_reason TEXT CHECK(exit_reason IN ('TP_HIT', 'SL_HIT', 'MANUAL_CLOSE', 'SIGNAL_CLOSE', 'LIQUIDATED', 'EXPIRED', 'CANCELLED')),
    result TEXT CHECK(result IN ('WIN', 'LOSS', 'BREAKEVEN', 'INVALID')),
    pnl_percent REAL,
    pnl_absolute REAL,
    roi_percent REAL,
    trading_fee REAL,
    funding_fee REAL,
    market_regime TEXT,
    signal_raw TEXT,
    signal_source TEXT,
    setup_type TEXT,
    confidence_score REAL,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
```

## 5.3 Table: market_context

```sql
CREATE TABLE IF NOT EXISTS market_context (
    context_id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id TEXT NOT NULL REFERENCES trades(trade_id),
    price_at_entry REAL NOT NULL,
    volatility REAL,
    trend_score REAL,
    volume_score REAL,
    session_tag TEXT,
    regime_tag TEXT,
    correlation_notes TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
```

## 5.4 Table: state_transitions (Audit Log)

```sql
CREATE TABLE IF NOT EXISTS state_transitions (
    transition_id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id TEXT NOT NULL REFERENCES trades(trade_id),
    from_state TEXT NOT NULL,
    to_state TEXT NOT NULL,
    trigger TEXT NOT NULL,
    price_at_transition REAL,
    timestamp TEXT DEFAULT (datetime('now'))
);
```

## 5.5 Table: trade_analysis

```sql
CREATE TABLE IF NOT EXISTS trade_analysis (
    analysis_id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id TEXT NOT NULL UNIQUE REFERENCES trades(trade_id),
    summary TEXT,
    trade_quality TEXT CHECK(trade_quality IN ('valid', 'invalid', 'mixed')),
    regime_quality TEXT CHECK(regime_quality IN ('favorable', 'unfavorable', 'mixed')),
    execution_quality TEXT CHECK(execution_quality IN ('good', 'bad', 'unknown')),
    lessons TEXT,  -- JSON array
    confidence REAL,
    created_at TEXT DEFAULT (datetime('now'))
);
```

## 5.6 Table: price_history (MFE/MAE tracking)

```sql
CREATE TABLE IF NOT EXISTS price_history (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id TEXT NOT NULL REFERENCES trades(trade_id),
    price REAL NOT NULL,
    high REAL,
    low REAL,
    timestamp TEXT DEFAULT (datetime('now'))
);
```

## 5.7 Table: system_log

```sql
CREATE TABLE IF NOT EXISTS system_log (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    level TEXT NOT NULL CHECK(level IN ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL')),
    component TEXT NOT NULL,
    message TEXT NOT NULL,
    details TEXT,
    timestamp TEXT DEFAULT (datetime('now'))
);
```

## 5.8 View: trade_summary

```sql
CREATE VIEW IF NOT EXISTS trade_summary AS
SELECT 
    t.trade_id,
    t.symbol,
    t.direction,
    t.leverage,
    t.entry_price,
    t.exit_price,
    t.result,
    t.pnl_percent,
    t.roi_percent,
    t.market_regime,
    t.exit_reason,
    mc.price_at_entry,
    mc.regime_tag,
    ta.summary as analysis_summary,
    ta.trade_quality,
    ta.lessons
FROM trades t
LEFT JOIN market_context mc ON t.trade_id = mc.trade_id
LEFT JOIN trade_analysis ta ON t.trade_id = ta.trade_id;
```

## 5.9 Indexes

```sql
CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals(symbol);
CREATE INDEX IF NOT EXISTS idx_signals_timestamp ON signals(timestamp);
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
CREATE INDEX IF NOT EXISTS idx_trades_signal_id ON trades(signal_id);
CREATE INDEX IF NOT EXISTS idx_transitions_trade_id ON state_transitions(trade_id);
CREATE INDEX IF NOT EXISTS idx_price_history_trade_id ON price_history(trade_id);
CREATE INDEX IF NOT EXISTS idx_system_log_timestamp ON system_log(timestamp);
```

# =============================================================================
# SECTION 6: MODULE SPECIFICATIONS
# =============================================================================

## 6.1 config/settings.py

**Purpose:** Central configuration loaded from environment variables.

**Classes:**
- `TelegramConfig` — bot_token, chat_id, webhook_url, enabled property
- `BitgetConfig` — api_key, secret_key, passphrase, base_url, ws_url, has_credentials property
- `SystemConfig` — log_level, data_dir, database_path (creates dirs on init)
- `TradingConfig` — max_slippage (0.005), entry_time_decay (300s), default_leverage (10), default_margin_mode ("ISOLATED"), trading_fee_rate (0.0006), funding_check_interval (3600)
- `AppConfig` — container with all above

**Functions:**
- `get_config() -> AppConfig` — singleton getter
- `reset_config() -> None` — reset for testing

## 6.2 config/secrets.py

**Functions:**
- `get_telegram_token() -> Optional[str]` — returns None if invalid/missing
- `get_bitget_credentials() -> dict[str, Optional[str]]` — returns empty dict if not configured
- `validate_telegram_token(token: str) -> bool` — checks format (digits:longstring)

## 6.3 database/connection.py

**Functions:**
- `get_db_connection() -> sqlite3.Connection` — thread-local connection with Row factory, PRAGMA foreign_keys=ON
- `init_database(db_path: Optional[Path] = None) -> None` — executes schema.sql, safe to call multiple times

## 6.4 signals/parser.py — SignalParser

**Class:** `SignalParser`

**Methods:**
- `__init__()` — compiles regex patterns
- `parse_telegram(raw_text: str) -> Optional[Signal]` — parses Telegram text, returns None if unparseable
- `parse_webhook(payload: dict[str, Any]) -> Optional[Signal]` — parses JSON webhook payload
- `_detect_side(text: str) -> Optional[TradeSide]` — detects LONG/SHORT/CLOSE from keywords
- `_detect_symbol(text: str) -> Optional[str]` — extracts BTCUSDT, ETH-USDT, #BTC, $BTC patterns
- `_extract_price(text: str, patterns: list[re.Pattern]) -> Optional[float]` — first match
- `_extract_leverage(text: str) -> int` — defaults to 10
- `_detect_margin_mode(text: str) -> MarginMode` — defaults to ISOLATED
- `_normalize_side(side_str: str) -> Optional[TradeSide]`
- `_normalize_margin_mode(mode_str: str) -> MarginMode`
- `_to_float(value: Any) -> Optional[float]` — safe conversion
- `_to_int(value: Any) -> int` — safe conversion, defaults to 10

**Parsing Patterns (compiled on init):**

Price patterns:
- `(?:entry|ent(?:ry)?|buy|sell|open)[\s]*[:\-]?\s*(\d+[.,]?\d*)`
- `(?:entry|ent(?:ry)?|buy|sell|open)[\s]+(?:at|@)?\s*(\d+[.,]?\d*)`
- `(?:at|@)\s*(\d+[.,]?\d*)`

SL patterns:
- `(?:sl|stop[-\s]?loss|stop)[\s]*[:\-]?\s*(\d+[.,]?\d*)`
- `(?:sl|stop[-\s]?loss|stop)[\s]+(?:at|@)?\s*(\d+[.,]?\d*)`

TP patterns:
- `(?:tp|take[-\s]?profit|target)[\s]*[:\-]?\s*(\d+[.,]?\d*)`
- `(?:tp|take[-\s]?profit|target)[\s]+(?:at|@)?\s*(\d+[.,]?\d*)`

Leverage patterns:
- `(?:lev(?:erage)?|x)[\s]*[:\-]?\s*(\d+)x?`
- `(\d+)x(?:\s|$)`

## 6.5 signals/telegram_listener.py — TelegramSignalListener

**Class:** `TelegramSignalListener`

**Constructor:**
- `on_signal: Callable[[Signal], None]` — callback for entry signals
- `on_close_signal: Callable[[Signal], None]` — callback for close signals

**Methods:**
- `async start() -> None` — initializes PTB Application, registers handlers, starts polling
- `async stop() -> None` — graceful shutdown
- `async _handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None` — parses text, routes to callbacks, replies with confirmation
- `async _handle_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None` — responds to /status command
- `async send_notification(chat_id: str, message: str) -> None` — sends message to Telegram

**Handlers:**
- `MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_message)`
- `CommandHandler("status", _handle_status)`

## 6.6 positions/state_machine.py — StateMachine

**Exception:** `StateTransitionError`

**Valid Transitions Map:**
```
(PENDING, "SIGNAL_RECEIVED") -> PENDING
(PENDING, "FILL_CONFIRMED") -> OPEN
(PENDING, "TIME_DECAY") -> EXPIRED
(PENDING, "CANCELLED") -> INVALID
(OPEN, "TP_HIT") -> CLOSED
(OPEN, "SL_HIT") -> CLOSED
(OPEN, "MANUAL_CLOSE") -> CLOSED
(OPEN, "SIGNAL_CLOSE") -> CLOSED
(OPEN, "LIQUIDATED") -> CLOSED
```

**Methods:**
- `__init__(on_transition: Optional[Callable] = None)` — optional callback
- `can_transition(current: TradeStatus, trigger: str) -> bool`
- `transition(trade_id: str, current: TradeStatus, trigger: str, price: Optional[float] = None) -> tuple[TradeStatus, bool]` — raises StateTransitionError if invalid
- `_log_transition(trade_id, from_state, to_state, trigger, price) -> None` — writes to state_transitions table
- `get_valid_triggers(current: TradeStatus) -> list[str]`

## 6.7 positions/position_manager.py — PositionManager

**Exceptions:** `DuplicatePositionError`, `PositionNotFoundError`

**Internal State:**
- `_positions: dict[str, VirtualPosition]` — trade_id -> position
- `_symbol_index: dict[str, str]` — symbol -> trade_id (open positions only)

**Methods:**
- `create_position(signal: Signal) -> Optional[VirtualPosition]` — creates PENDING position, checks for duplicates, persists to DB
- `activate_position(trade_id: str, fill_price: float) -> VirtualPosition` — PENDING -> OPEN, recalculates liq price + margin
- `check_and_close(trade_id: str, current_price: float) -> Optional[VirtualPosition]` — checks TP/SL/liq, closes if hit, updates MFE/MAE
- `close_position(trade_id: str, exit_price: float, reason: ExitReason) -> VirtualPosition` — OPEN -> CLOSED, calculates PnL/ROI/fees
- `handle_close_signal(signal: Signal) -> Optional[VirtualPosition]` — finds matching open position by symbol
- `get_open_positions() -> list[VirtualPosition]`
- `get_pending_positions() -> list[VirtualPosition]`
- `get_position_by_symbol(symbol: str) -> Optional[VirtualPosition]`
- `get_position(trade_id: str) -> Optional[VirtualPosition]`
- `expire_pending(trade_id: str) -> Optional[VirtualPosition]` — PENDING -> EXPIRED
- `_check_fill(position, current_price: float) -> None` — internal fill model (slippage-based)
- `_update_mfe_mae(position, current_price) -> None` — runtime tracking
- `_persist_signal(signal) -> None` — writes to signals table
- `_persist_position(position) -> None` — INSERT into trades
- `_update_position(position) -> None` — UPDATE trades

**Fill Model Rules:**
- If no entry price specified: fill at current price immediately
- If entry price specified:
  - LONG: fill if current_price <= entry * (1 + max_slippage)
  - SHORT: fill if current_price >= entry * (1 - max_slippage)
- max_slippage from config = 0.005 (0.5%)

## 6.8 exchange/bitget_client.py

**Dataclass:** `BitgetTicker`
- symbol, last_price, high_24h, low_24h, volume_24h, change_24h, timestamp

**Class:** `BitgetMarketClient`

**Constants:**
- BASE_URL = "https://api.bitget.com"
- WS_URL = "wss://ws.bitget.com/v2/ws/public"

**Methods:**
- `async _get_rest_client() -> httpx.AsyncClient` — lazy init
- `async get_ticker(symbol: str) -> Optional[BitgetTicker]` — GET /api/v2/mix/market/ticker?symbol={symbol}&productType=USDT-FUTURES
- `async get_candles(symbol: str, granularity: str = "1m", limit: int = 100) -> list[dict[str, Any]]` — GET /api/v2/mix/market/candles
- `async get_funding_rate(symbol: str) -> Optional[float]` — GET /api/v2/mix/market/funding-rate
- `to_market_snapshot(ticker: BitgetTicker) -> MarketSnapshot`
- `async close() -> None` — closes HTTP client

**Class:** `BitgetPriceMonitor`

**Constructor:**
- `client: BitgetMarketClient`
- `check_interval: float = 5.0` (seconds between poll cycles)

**Methods:**
- `register_callback(callback: Callable[[str, float], None]) -> None` — (symbol, price)
- `async monitor_symbols(symbols: list[str]) -> None` — runs until stop(), polls each symbol, calls callbacks
- `stop() -> None`

## 6.9 exchange/market_monitor.py — MarketMonitor

**Constructor:**
- `position_manager: PositionManager`
- `price_monitor: Optional[BitgetPriceMonitor] = None`

**Methods:**
- `_on_price_update(symbol: str, price: float) -> None` — checks open positions for TP/SL, checks pending for fill
- `_check_fill(position, current_price: float) -> None` — delegates to position_manager fill logic
- `add_symbol(symbol: str) -> None`
- `remove_symbol(symbol: str) -> None`
- `async start() -> None` — begins monitoring tracked symbols
- `stop() -> None`

## 6.10 journal/models.py — JournalWriter + JournalReader

**Class:** `JournalWriter`

**Methods:**
- `record_market_context(trade_id, price_at_entry, volatility, trend_score, volume_score, session_tag, regime_tag, correlation_notes) -> None`
- `record_analysis(trade_id, summary, trade_quality, regime_quality, execution_quality, lessons, confidence) -> None`
- `log_system_event(level, component, message, details) -> None`

**Class:** `JournalReader`

**Methods:**
- `get_trade(trade_id: str) -> Optional[dict[str, Any]]` — full record with context + analysis + transitions
- `get_trades_by_regime(regime: str) -> list[dict[str, Any]]`
- `get_performance_summary() -> dict[str, Any]` — total_closed, wins, losses, win_rate, avg_pnl_percent

## 6.11 coach/regime_detector.py — RegimeDetector

**Internal State:**
- `price_history: dict[str, list[float]]` — symbol -> last 100 prices

**Methods:**
- `update_price(symbol: str, price: float) -> None`
- `detect_regime(symbol: str, candles: list[dict[str, Any]]) -> MarketRegime`
  - Returns UNKNOWN if < 20 candles
  - Volatility: avg_range/price > 0.02 → HIGH_VOLATILITY, < 0.005 → LOW_VOLATILITY
  - Trend: EMA(8) vs EMA(21) → TRENDING_UP / TRENDING_DOWN
  - Default: RANGING
- `_ema(prices: list[float], period: int) -> Optional[float]`

## 6.12 coach/trade_analyzer.py — TradeAnalyzer

**Methods:**
- `analyze(position: VirtualPosition) -> dict[str, Any]` — returns structured analysis

**Analysis Logic (must implement all):**

1. **SL too tight check:**
   - If max_adverse_excursion > sl_distance * 1.5 → lesson: "Stop loss was too tight"

2. **Near-TP reversal:**
   - If max_favorable_excursion > tp_distance * 0.7 AND result == LOSS → lesson: "Price came close to TP but reversed"

3. **Immediate reversal (noise):**
   - If duration < 60s AND result == LOSS → lesson: "Trade failed immediately - likely noise or stop hunt"

4. **Regime mismatch:**
   - If regime in (HIGH_VOLATILITY, RANGING) AND result == LOSS → regime_quality = "unfavorable"

5. **Leverage check:**
   - If leverage > 20 → lesson: "High leverage amplified losses"

6. **Liquidation:**
   - If exit_reason == LIQUIDATED → trade_quality = "invalid", execution_quality = "bad"

7. **Win with bad process:**
   - If WIN AND max_adverse_excursion > sl_distance * 0.8 → lesson: "Won but with significant drawdown"

**Output Format:**
```python
{
    "summary": f"{direction} won/lost: {pnl}% in {regime}",
    "trade_quality": "valid|invalid|mixed",
    "regime_quality": "favorable|unfavorable|mixed",
    "execution_quality": "good|bad|unknown",
    "lessons": ["string", "string"],
    "confidence": 70 if valid else 50,
}
```

## 6.13 coach/coach_engine.py — CoachEngine

**Constructor:**
- `analyzer: TradeAnalyzer` (default init)
- `journal: JournalWriter` (default init)

**Methods:**
- `review_trade(position: VirtualPosition) -> dict[str, Any]` — runs analyzer, persists to trade_analysis table, returns analysis

## 6.14 app.py — AegisTrader (Orchestrator)

**Constructor:**
- Initializes all components: PositionManager, BitgetMarketClient, MarketMonitor, CoachEngine, RegimeDetector, JournalWriter

**Methods:**
- `async start() -> None` — init DB, setup monitor, start Telegram if configured, start background monitor task
- `async stop() -> None` — graceful shutdown: cancel monitor task, stop Telegram, close HTTP client
- `_handle_entry_signal(signal: Signal) -> None` — create position, add symbol to monitor, log, notify Telegram
- `_handle_close_signal(signal: Signal) -> None` — find matching position, mark for closure
- `async _run_monitor() -> None` — background loop: get open/pending symbols, add to monitor, start monitoring, handle errors with 10s retry
- `async _on_position_closed(position) -> None` — run coach, notify Telegram with lessons

**Signal Handlers:**
- SIGINT → `app.stop()`
- SIGTERM → `app.stop()`

**Main Loop:**
- `asyncio.run(main())` → create AegisTrader → start → sleep forever

# =============================================================================
# SECTION 7: VIRTUAL FUTURES EXECUTION LOGIC
# =============================================================================

## 7.1 PnL Calculation (Futures, Not Spot)

Standardized to 100 USDT notional for comparison:

```
LONG:  price_change = exit_price - entry_price
SHORT: price_change = entry_price - exit_price

pnl_percent = (price_change / entry_price) * 100
leveraged_pnl = pnl_percent * leverage
trading_fee = notional * fee_rate * 2  # entry + exit
pnl_absolute = (leveraged_pnl / 100) * notional - trading_fee
roi_percent = (pnl_absolute / notional) * 100
```

## 7.2 Liquidation Price (Simplified)

```
mm_rate = 0.004  # maintenance margin ~0.4%

LONG:  liq = entry * (1 - 1/leverage + mm_rate)
SHORT: liq = entry * (1 + 1/leverage - mm_rate)
```

## 7.3 Margin Used

```
margin = notional / leverage
```

## 7.4 Fill Model

```
IF entry_price is None OR entry_price <= 0:
    fill at current_price immediately
ELSE IF direction == LONG:
    max_acceptable = entry * (1 + max_slippage)
    IF current_price <= max_acceptable:
        fill at current_price
ELSE IF direction == SHORT:
    min_acceptable = entry * (1 - max_slippage)
    IF current_price >= min_acceptable:
        fill at current_price
```

max_slippage = 0.005 (0.5%) from config

## 7.5 Exit Detection

```
IF direction == LONG:
    IF current_price >= take_profit:  return TP_HIT
    IF current_price <= stop_loss:    return SL_HIT
    IF current_price <= liquidation:   return LIQUIDATED
ELSE IF direction == SHORT:
    IF current_price <= take_profit:  return TP_HIT
    IF current_price >= stop_loss:    return SL_HIT
    IF current_price >= liquidation:   return LIQUIDATED
```

# =============================================================================
# SECTION 8: TELEGRAM NOTIFICATIONS
# =============================================================================

## 8.1 Signal Received

```
✅ Signal received: LONG BTCUSDT
Entry: 105000 | SL: 103500 | TP: 108000
```

## 8.2 Close Signal Received

```
✅ Close signal: BTCUSDT
```

## 8.3 Position Opened (Virtual)

```
📊 Virtual position opened: BTCUSDT LONG
ID: {trade_id}
Entry: {fill_price} | Leverage: {leverage}x
```

## 8.4 Position Closed

```
🔒 Position closed: BTCUSDT
Result: WIN | PnL: +12.5%
Exit: {exit_price} | Reason: TP_HIT
Lessons:
• Price came close to TP but reversed - consider trailing stops
```

## 8.5 Daily Summary (Future)

```
📈 Daily Summary
Trades: 5 | Wins: 3 | Losses: 2
Win Rate: 60% | Avg PnL: +4.2%
Best Trade: BTCUSDT LONG +15%
Worst Trade: ETHUSDT SHORT -8%
```

## 8.6 Error Notification

```
❌ Error in SIGNAL component
Details: {error_message}
```

# =============================================================================
# SECTION 9: DEVELOPMENT PHASES
# =============================================================================

## Phase 1 — Foundation (MUST complete first)
- [ ] Repository skeleton (all __init__.py files)
- [ ] Configuration system (settings.py, secrets.py, .env)
- [ ] Signal parser (telegram + webhook)
- [ ] Futures state machine (strict transitions)
- [ ] Virtual position manager (CRUD + lifecycle)
- [ ] SQLite schema + connection manager
- [ ] Unit tests for parser, state machine, position manager

## Phase 2 — Market Data
- [ ] Bitget REST client (ticker, candles, funding rate)
- [ ] Price monitor (polling loop)
- [ ] Market monitor bridge (price → position check)
- [ ] Virtual trade closure detection (TP/SL/liq)
- [ ] Journal writer (persist trades + context)

## Phase 3 — Intelligence
- [ ] Regime detector (heuristic, 6 regimes)
- [ ] Performance metrics (win rate, avg PnL, by regime)
- [ ] Market context recording at entry

## Phase 4 — Coach + Notifications
- [ ] Trade analyzer (5-category classification)
- [ ] Coach engine (orchestrator)
- [ ] Telegram bot listener
- [ ] Telegram notifications (signal, open, close, analysis)

## Phase 5 — Polish (Optional)
- [ ] CLI dashboard (simple text/ASCII)
- [ ] Webhook listener (FastAPI minimal endpoint)
- [ ] Configuration validation on startup
- [ ] Health check endpoint

# =============================================================================
# SECTION 10: ACCEPTANCE CRITERIA
# =============================================================================

The implementation is CORRECT only if ALL of the following pass:

1. [ ] A Telegram alert can be parsed into a Signal with correct side, symbol, entry, SL, TP, leverage
2. [ ] A webhook JSON payload can be parsed into an equivalent Signal
3. [ ] A futures virtual position can be created from a parsed Signal
4. [ ] The position enters PENDING state, then transitions to OPEN on fill
5. [ ] Live Bitget market data can be fetched for a symbol
6. [ ] TP hit closes the position with WIN result and positive PnL
7. [ ] SL hit closes the position with LOSS result and negative PnL
8. [ ] The full trade result is written to SQLite trades table
9. [ ] State transitions are logged in state_transitions table
10. [ ] Post-trade analysis is generated and stored in trade_analysis table
11. [ ] NO live order is placed on Bitget (verify: no POST /api/v2/mix/order calls)
12. [ ] Tests pass: pytest tests/ -v (all green)
13. [ ] Parser tests cover: long, short, close, webhook, invalid, missing fields
14. [ ] State machine tests cover: all valid transitions, invalid transition raises
15. [ ] Position manager tests cover: create, activate, close TP, close SL, duplicate error

# =============================================================================
# SECTION 11: CODING STANDARDS
# =============================================================================

- Python 3.12+ syntax
- Type hints on ALL function signatures and variables
- pydantic BaseModel for ALL structured data
- dataclasses for configuration only
- clear logging via structlog or standard logging
- unit tests with pytest, async tests with pytest-asyncio
- defensive parsing: never crash on malformed input
- fail closed, not open: if uncertain, preserve data and log
- modular: each module has single responsibility
- easy to extend: interfaces between modules are clean
- docstrings on all public methods
- no # type: ignore without comment explaining why

# =============================================================================
# SECTION 12: WHAT NOT TO BUILD IN V1
# =============================================================================

- [ ] Live execution (real orders)
- [ ] Exchange order routing
- [ ] Portfolio management (multi-position allocation)
- [ ] Multi-exchange arbitrage
- [ ] Self-modifying strategy logic
- [ ] Reinforcement learning
- [ ] Dashboard with charts/graphs
- [ ] Mobile app frontend
- [ ] WebSocket real-time feed (REST polling is fine for V1)
- [ ] Backtesting engine
- [ ] Machine learning model training
- [ ] Social features (sharing, leaderboards)

# =============================================================================
# SECTION 13: EXAMPLE SIGNAL FORMATS (Parser Must Handle)
# =============================================================================

## Format 1: Structured Telegram
```
🚀 LONG BTCUSDT
Entry: 105000
SL: 103500
TP: 108000
Leverage: 10x
Margin: Isolated
```

## Format 2: Compact Telegram
```
LONG BTCUSDT @ 105000
SL 103500 | TP 108000
10x Cross
```

## Format 3: Short Signal
```
SHORT ETHUSDT
Entry: 4000
SL: 4100
TP: 3800
Leverage: 5x
```

## Format 4: Close Signal
```
CLOSE BTCUSDT
```

## Format 5: Webhook JSON
```json
{
  "symbol": "BTCUSDT",
  "side": "LONG",
  "entry": 105000,
  "sl": 103500,
  "tp": 108000,
  "leverage": 10,
  "margin_mode": "ISOLATED"
}
```

## Format 6: Minimal (Missing Fields)
```
LONG BTCUSDT
```
→ Should parse with defaults: leverage=10, margin=ISOLATED, entry=null, SL=null, TP=null

## Format 7: Hashtag/Symbol Style
```
#BTC LONG at 105000
Stop: 103500
Target: 108000
```

## Format 8: Dollar Sign Style
```
$BTC LONG
Entry 105000
SL 103500
TP 108000
```

# =============================================================================
# SECTION 14: ENVIRONMENT VARIABLES
# =============================================================================

```bash
# Required for Telegram
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrSTUvwxyz
TELEGRAM_CHAT_ID=-1001234567890

# Optional for Bitget (market data works without auth)
BITGET_API_KEY=optional
BITGET_SECRET_KEY=optional
BITGET_PASSPHRASE=optional

# System
LOG_LEVEL=INFO
DATABASE_PATH=./data/journal.db
DATA_DIR=./data
```

# =============================================================================
# SECTION 15: FINAL INSTRUCTION TO BASE44
# =============================================================================

1. Do NOT guess at requirements. If something is ambiguous, ask or implement the safest default.
2. Do NOT invent missing signal formats. The parser must handle the 8 examples in Section 13.
3. Do NOT compress the architecture. Build ALL modules as specified.
4. Do NOT skip tests. Every module must have corresponding test coverage.
5. Do NOT add live execution code. V1 is virtual-only.
6. Do NOT couple modules. Each module must be independently testable.
7. Preserve data at all costs. If uncertain, log and continue.
8. Use the exact field names, table names, and enum values specified.
9. Build in the exact Phase order: 1 → 2 → 3 → 4 → 5.
10. The journal is the primary asset. Every design decision must serve data integrity.

# =============================================================================
# END OF BLUEPRINT
# =============================================================================



# =============================================================================
# SECTION 16: ANALYST ENGINE (NEW)
# =============================================================================

## 16.1 Purpose

The Analyst Engine autonomously scans Bitget futures markets, calculates
technical indicators, detects trade setups, and generates trade candidates
that feed into the existing signal pipeline.

## 16.2 Architecture

```
MarketScanner
    ↓ (fetches candles + ticker + funding)
SetupDetector
    ↓ (calculates indicators, detects patterns)
TradeCandidate
    ↓
AnalystSignalBridge
    ↓ (converts to canonical Signal)
PositionManager (existing)
    ↓
MarketMonitor (existing)
    ↓
Journal (existing)
```

## 16.3 New Modules

### analyst/models.py

**Classes:**
- `SetupType` (enum): EMA_PULLBACK, EMA_BREAKOUT, RANGE_PLAY, MOMENTUM_BURST, VOLUME_SPIKE, SUPPORT_BOUNCE, RESISTANCE_REJECT, TREND_CONTINUATION, UNKNOWN
- `IndicatorSnapshot` — 18 technical indicator fields
- `TradeCandidate` — Complete trade setup with entry, SL, TP, thesis, confidence
- `ScanResult` — Per-symbol scan output
- `MarketScan` — Multi-symbol scan summary

### analyst/indicators.py

**Functions (all pure, no side effects):**
- `calculate_ema(prices, period) -> Optional[float]`
- `calculate_sma(prices, period) -> Optional[float]`
- `calculate_rsi(prices, period=14) -> Optional[float]`
- `calculate_atr(candles, period=14) -> Optional[float]`
- `calculate_bollinger_bands(prices, period=20, std_dev=2.0) -> dict`
- `calculate_macd(prices, fast=12, slow=26, signal=9) -> dict`
- `calculate_adx(candles, period=14) -> dict`
- `calculate_volume_ratio(candles, period=20) -> Optional[float]`
- `calculate_all_indicators(candles) -> dict` — runs all, returns complete snapshot

### analyst/setup_detector.py

**Class:** `SetupDetector`

**Methods:**
- `analyze_symbol(symbol, candles, current_price, funding_rate) -> list[TradeCandidate]` — runs all detectors
- `_detect_ema_pullback(...) -> Optional[TradeCandidate]` — LONG/SHORT pullback to EMA21 in trend
- `_detect_ema_breakout(...) -> Optional[TradeCandidate]` — Break above/below EMA200 with volume
- `_detect_range_play(...) -> Optional[TradeCandidate]` — BB bounce in ranging market
- `_detect_momentum_burst(...) -> Optional[TradeCandidate]` — MACD expansion + ADX + volume
- `_detect_volume_spike(...) -> Optional[TradeCandidate]` — 3x volume with price direction

**Setup Logic:**

EMA Pullback LONG:
- EMA8 > EMA21 > EMA50 (uptrend)
- Price within 0.5% of EMA21
- RSI < 65 (not overbought)
- SL = min(EMA21*0.995, EMA50*0.998)
- TP = entry + (entry - SL) * 2
- Confidence: 65%

EMA Pullback SHORT:
- EMA8 < EMA21 < EMA50 (downtrend)
- Price within 0.5% of EMA21
- RSI > 35 (not oversold)
- SL = max(EMA21*1.005, EMA50*1.002)
- TP = entry - (SL - entry) * 2
- Confidence: 65%

EMA Breakout LONG:
- Price was below EMA200 for 4+ candles
- Now above EMA200 by 0.5%
- Volume > 1.5x average
- RSI 40-70
- SL = EMA200 * 0.995
- TP = entry + (entry - SL) * 2.5
- Confidence: 70%

Range Play LONG:
- ADX < 20 (ranging)
- BB width 0.02-0.08
- Price at lower BB
- RSI < 35
- SL = lower BB * 0.99
- TP = upper BB * 0.995
- Confidence: 55%

Momentum Burst:
- MACD histogram expanding
- ADX > 20
- Volume > 2x average
- Direction from MACD line vs signal
- SL = ATR * 1.5
- TP = ATR * 3
- Confidence: 60%

### analyst/market_scanner.py

**Class:** `MarketScanner`

**Constructor:**
- `symbols: Optional[list[str]]` — default: 12 major pairs
- `timeframe: str` — default: "1H"
- `candle_limit: int` — default: 100
- `min_confidence: float` — default: 55.0
- `on_candidate: Optional[Callable[[TradeCandidate], None]]` — callback

**Methods:**
- `async scan_all() -> MarketScan` — scans all symbols, returns summary
- `async _scan_symbol(symbol) -> ScanResult` — fetches data, runs detector
- `_classify_regime(indicators) -> str` — TRENDING_UP/DOWN, RANGING, HIGH/LOW_VOL, UNKNOWN
- `async run_scheduled(interval_minutes=60) -> None` — background loop
- `stop() -> None`
- `async close() -> None`

**Default Symbols:**
BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT, DOGEUSDT, ADAUSDT, AVAXUSDT, LINKUSDT, MATICUSDT, DOTUSDT, LTCUSDT, BCHUSDT

### analyst/signal_bridge.py

**Class:** `AnalystSignalBridge`

**Constructor:**
- `position_manager: PositionManager` — existing instance

**Methods:**
- `submit_candidate(candidate: TradeCandidate) -> Optional[Signal]` — converts to Signal, creates position
- `submit_candidates(candidates: list[TradeCandidate]) -> list[Signal]` — batch submit

**Conversion Logic:**
1. TradeCandidate.to_signal() → dict
2. Build canonical Signal(source=WEBHOOK, raw_text=analyst description)
3. position_manager.create_position(signal) → VirtualPosition
4. Returns Signal on success, None on failure

## 16.4 Integration with Existing System

The Analyst Engine feeds into the SAME pipeline as Telegram signals:

```python
# In app.py:
self.analyst_bridge = AnalystSignalBridge(self.position_manager)
self.market_scanner = MarketScanner(on_candidate=self._handle_analyst_candidate)

# Callback:
def _handle_analyst_candidate(self, candidate: TradeCandidate):
    signal = self.analyst_bridge.submit_candidate(candidate)
    if signal:
        self.market_monitor.add_symbol(signal.symbol)
        # Same journal, same notifications as manual signals
```

## 16.5 Analyst-Generated Telegram Notifications

**New Position from Analyst:**
```
Analyst position opened: BTCUSDT LONG
ID: {trade_id}
Setup: EMA_PULLBACK
Confidence: 70%
```

**Scan Summary:**
```
Analyst Scan Complete
Scanned: 12 symbols
Candidates: 3
Top setups:
- LONG BTCUSDT (EMA_PULLBACK, 70%)
- SHORT ETHUSDT (RANGE_PLAY, 58%)
- LONG SOLUSDT (MOMENTUM_BURST, 60%)
```

## 16.6 Configuration

No new env vars needed. Uses existing:
- `BITGET_API_KEY` (optional, market data works without)
- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` (for notifications)

Future config (Phase 5):
- `ANALYST_SYMBOLS` — comma-separated list
- `ANALYST_TIMEFRAME` — default "1H"
- `ANALYST_SCAN_INTERVAL` — minutes between scans
- `ANALYST_MIN_CONFIDENCE` — minimum confidence threshold

## 16.7 Testing

**test_analyst.py** covers:
- Indicator calculations (EMA, RSI, ATR, BB, MACD, ADX)
- Setup detection (EMA pullback with synthetic data)
- Candidate structure validation (entry < TP for LONG, etc.)
- Signal bridge conversion

## 16.8 Hard Rules for Analyst

1. Analyst signals are treated EXACTLY like Telegram signals
2. No special privileges — same fill model, same state machine
3. Analyst positions journal with setup_type metadata
4. Coach analyzes analyst trades same as manual trades
5. Analyst can be disabled by not starting the scanner task
6. No additional leverage or risk for analyst signals
7. Analyst thesis is preserved in journal for learning

# =============================================================================
# SECTION 17: UPDATED REPOSITORY STRUCTURE
# =============================================================================

aegis-trader/
├── app.py                          # Updated: includes Analyst Engine
├── requirements.txt                # Same
├── .env.example                    # Same
├── .gitignore                      # Same
│
├── analyst/                        # NEW MODULE
│   ├── __init__.py
│   ├── models.py                   # TradeCandidate, IndicatorSnapshot, ScanResult
│   ├── indicators.py               # Pure technical indicator functions
│   ├── setup_detector.py           # Pattern recognition (5 setups)
│   ├── market_scanner.py           # Multi-symbol scanning engine
│   └── signal_bridge.py            # Converts candidates to Signals
│
├── config/
│   ├── __init__.py
│   ├── settings.py
│   └── secrets.py
│
├── signals/
│   ├── __init__.py
│   ├── models.py
│   ├── parser.py
│   ├── telegram_listener.py
│   └── webhook_listener.py
│
├── positions/
│   ├── __init__.py
│   ├── state_machine.py
│   └── position_manager.py
│
├── exchange/
│   ├── __init__.py
│   ├── bitget_client.py
│   └── market_monitor.py
│
├── journal/
│   ├── __init__.py
│   ├── models.py
│   └── analysis_models.py
│
├── coach/
│   ├── __init__.py
│   ├── coach_engine.py
│   ├── trade_analyzer.py
│   └── regime_detector.py
│
├── database/
│   ├── __init__.py
│   ├── connection.py
│   ├── schema.sql
│   └── migrations.py
│
├── tests/
│   ├── __init__.py
│   ├── test_parser.py
│   ├── test_state_machine.py
│   ├── test_position_manager.py
│   ├── test_journal.py
│   ├── test_regime_detector.py
│   ├── test_coach.py
│   └── test_analyst.py             # NEW
│
└── docs/
    ├── architecture.md
    └── BLUEPRINT.md                # Updated with Analyst sections
