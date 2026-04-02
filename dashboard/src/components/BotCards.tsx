import type { Bot } from '../types/api';

function fmtPnl(n: number): string {
  return (n >= 0 ? '+$' : '-$') + Math.abs(n).toFixed(2);
}

function signalArrow(signal: string | null) {
  if (!signal) return <span className="text-2xl font-black" style={{ color: 'var(--text-dim)' }}>--</span>;
  if (signal.includes('YES')) return <span className="text-2xl font-black" style={{ color: 'var(--green)' }}>&#9650;</span>;
  if (signal.includes('NO')) return <span className="text-2xl font-black" style={{ color: 'var(--red)' }}>&#9660;</span>;
  return <span className="text-2xl font-black" style={{ color: 'var(--text-dim)' }}>&#9644;</span>;
}

function confColor(c: number): string {
  if (c >= 0.7) return 'var(--green)';
  if (c >= 0.4) return 'var(--gold)';
  return 'var(--red)';
}

interface Props {
  bots: Bot[];
}

export default function BotCards({ bots }: Props) {
  return (
    <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(250px, 1fr))' }}>
      {bots.map((b) => (
        <div key={`${b.strategy}-${b.asset}`}
          className="rounded-xl p-4.5 flex flex-col gap-3 transition-colors"
          style={{
            background: 'var(--card)',
            border: '1px solid var(--border)',
          }}>

          {/* Header */}
          <div className="flex items-center justify-between">
            <span className="text-xs font-bold px-2.5 py-1 rounded-md"
              style={{ background: 'rgba(88,166,255,0.12)', color: 'var(--blue)' }}>
              {b.strategy}/{b.asset}
            </span>
            {signalArrow(b.signal)}
          </div>

          {/* Confidence bar */}
          <div className="h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--border)' }}>
            <div className="h-full rounded-full transition-all duration-400"
              style={{
                width: `${(b.confidence * 100).toFixed(0)}%`,
                background: confColor(b.confidence),
              }} />
          </div>

          {/* Stats grid */}
          <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-[13px]">
            <StatRow label="Win Rate" value={`${b.win_rate.toFixed(1)}%`} />
            <StatRow label="P&L" value={fmtPnl(b.total_pnl)}
              color={b.total_pnl >= 0 ? 'var(--green)' : 'var(--red)'} />
            <StatRow label="Bankroll" value={`$${b.bankroll.toFixed(2)}`} />
            <StatRow label="Trades" value={`${b.trades}${b.open ? ` (${b.open} open)` : ''}`} />
          </div>
        </div>
      ))}
    </div>
  );
}

function StatRow({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="flex justify-between">
      <span style={{ color: 'var(--text-dim)' }}>{label}</span>
      <span className="font-semibold font-mono" style={{ color: color ?? 'var(--text)' }}>{value}</span>
    </div>
  );
}
