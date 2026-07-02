-- Aegis Trader Journal Schema
-- SQLite database for futures trading intelligence
-- Append-only. Historical rows must not be rewritten.

-- ============================================
-- CORE TABLES
-- ============================================

-- Signals: every parsed alert, whether it becomes a trade or not
CREATE TABLE IF NOT EXISTS signals (
    signal_id TEXT PRIMARY KEY,
    source TEXT NOT NULL CHECK(source IN ('telegram', 'webhook', 'analyst', 'groq')),
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

-- Trades: virtual futures positions
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

-- Market Context: snapshot at trade entry
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

-- State Transitions: audit log of every state change
CREATE TABLE IF NOT EXISTS state_transitions (
    transition_id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id TEXT NOT NULL REFERENCES trades(trade_id),
    from_state TEXT NOT NULL,
    to_state TEXT NOT NULL,
    trigger TEXT NOT NULL,  -- what caused the transition
    price_at_transition REAL,
    timestamp TEXT DEFAULT (datetime('now'))
);

-- Analysis: post-trade coach output
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

-- Price History: tracked prices for open positions (for MFE/MAE)
CREATE TABLE IF NOT EXISTS price_history (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id TEXT NOT NULL REFERENCES trades(trade_id),
    price REAL NOT NULL,
    high REAL,
    low REAL,
    timestamp TEXT DEFAULT (datetime('now'))
);

-- System Log: operational events
CREATE TABLE IF NOT EXISTS system_log (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    level TEXT NOT NULL CHECK(level IN ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL')),
    component TEXT NOT NULL,
    message TEXT NOT NULL,
    details TEXT,
    timestamp TEXT DEFAULT (datetime('now'))
);

-- ============================================
-- INDEXES
-- ============================================

CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals(symbol);
CREATE INDEX IF NOT EXISTS idx_signals_timestamp ON signals(timestamp);
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
CREATE INDEX IF NOT EXISTS idx_trades_signal_id ON trades(signal_id);
CREATE INDEX IF NOT EXISTS idx_transitions_trade_id ON state_transitions(trade_id);
CREATE INDEX IF NOT EXISTS idx_price_history_trade_id ON price_history(trade_id);
CREATE INDEX IF NOT EXISTS idx_system_log_timestamp ON system_log(timestamp);

-- ============================================
-- VIEWS
-- ============================================

-- Trade summary with market context
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
