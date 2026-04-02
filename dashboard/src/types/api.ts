/** TypeScript types matching the Python backend API. */

// ── Overview ─────────────────────────────────────────
export interface Overview {
  total_pnl: number;
  total_bankroll: number;
  total_trades: number;
  win_rate: number;
  mode: string;
}

// ── Bot ──────────────────────────────────────────────
export interface Bot {
  strategy: string;
  asset: string;
  signal: string | null;
  confidence: number;
  win_rate: number;
  total_pnl: number;
  bankroll: number;
  trades: number;
  open: number;
}

// ── Signal State ─────────────────────────────────────
export interface SignalState {
  strategy: string;
  asset: string;
  signal: string;
  confidence: number;
  price: number | null;
  cvd: number | null;
  vwap_change: number | null;
  funding_rate: number | null;
  rsi: number | null;
  bb_pct: number | null;
  regime: string | null;
  updated_at: string | null;
}

// ── Trade ────────────────────────────────────────────
export interface Trade {
  id: number;
  timestamp: string;
  strategy: string;
  asset: string;
  signal: string;
  entry_price: number;
  bet_size: number;
  confidence: number;
  outcome: string | null;
  pnl: number | null;
}

// ── Health ───────────────────────────────────────────
export interface Health {
  status: string;
  mode: string;
  components: {
    database: string;
    vpn: string;
    feeds: string;
  };
  timestamp: string;
}

// ── WebSocket Messages ───────────────────────────────
export type WSChannel = 'prices' | 'signals' | 'metrics' | 'trades';

export interface WSMessage {
  channel: WSChannel;
  data: Record<string, unknown>;
  timestamp: number;
}

export interface WSHeartbeat {
  type: 'heartbeat';
  timestamp: number;
}

export interface WSSnapshot {
  type: 'snapshot';
  data: WSMessage[];
}

export interface WSSubscribed {
  type: 'subscribed';
  channels: string[];
}

export type WSIncoming = WSMessage | WSHeartbeat | WSSnapshot | WSSubscribed;

// ── Risk Event ───────────────────────────────────────
export interface RiskEvent {
  timestamp: string;
  event_type: string;
  strategy: string | null;
  asset: string | null;
  details: string | null;
}
