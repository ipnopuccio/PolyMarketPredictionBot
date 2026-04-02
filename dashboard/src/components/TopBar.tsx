import type { Overview } from '../types/api';

function fmtPnl(n: number): string {
  const prefix = n >= 0 ? '+$' : '-$';
  return prefix + Math.abs(n).toFixed(2);
}

interface Props {
  overview: Overview | null;
  wsConnected: boolean;
}

export default function TopBar({ overview, wsConnected }: Props) {
  const o = overview;

  return (
    <header className="flex items-center gap-8 px-7 py-4 border-b flex-wrap"
      style={{ background: 'var(--card)', borderColor: 'var(--border)' }}>

      <span className="text-lg font-bold" style={{ color: 'var(--blue)' }}>
        Polymarket Bot v2
      </span>

      {/* Connection dot */}
      <span
        className="w-2 h-2 rounded-full shrink-0"
        style={{ background: wsConnected ? 'var(--green)' : 'var(--red)' }}
        title={wsConnected ? 'WebSocket connected' : 'Polling (WS disconnected)'}
      />

      <Stat label="Total P&L" value={o ? fmtPnl(o.total_pnl) : '--'}
        color={o && o.total_pnl >= 0 ? 'var(--green)' : 'var(--red)'} />
      <Stat label="Bankroll" value={o ? `$${o.total_bankroll.toFixed(2)}` : '--'} />
      <Stat label="Win Rate" value={o ? `${o.win_rate.toFixed(1)}%` : '--'} />
      <Stat label="Trades" value={o ? String(o.total_trades) : '--'} />

      <div className="flex-1" />

      <span className="px-3 py-1 rounded-xl text-xs font-semibold uppercase tracking-wider"
        style={{
          background: o?.mode === 'paper' ? 'rgba(210,153,34,0.15)' : 'rgba(63,185,80,0.15)',
          color: o?.mode === 'paper' ? 'var(--gold)' : 'var(--green)',
          border: `1px solid ${o?.mode === 'paper' ? 'var(--gold)' : 'var(--green)'}`,
        }}>
        {o?.mode?.toUpperCase() ?? '--'}
      </span>
    </header>
  );
}

function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[11px] uppercase tracking-wider" style={{ color: 'var(--text-dim)' }}>
        {label}
      </span>
      <span className="text-xl font-bold font-mono" style={{ color: color ?? 'var(--text)' }}>
        {value}
      </span>
    </div>
  );
}
