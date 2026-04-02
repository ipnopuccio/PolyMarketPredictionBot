import type { Trade } from '../types/api';

function fmtPnl(n: number | null): string {
  if (n == null) return '--';
  return (n >= 0 ? '+$' : '-$') + Math.abs(n).toFixed(2);
}

function shortTime(ts: string): string {
  try {
    return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  } catch {
    return ts.slice(11, 19);
  }
}

interface Props {
  trades: Trade[];
}

export default function TradesTable({ trades }: Props) {
  return (
    <div className="rounded-xl overflow-y-auto max-h-[500px]"
      style={{ background: 'var(--card)', border: '1px solid var(--border)' }}>
      <table className="w-full border-collapse text-[13px]">
        <thead>
          <tr>
            {['Time', 'Bot', 'Signal', 'Entry', 'Size', 'Outcome', 'P&L'].map((h) => (
              <th key={h} className="text-left px-3.5 py-2.5 text-[11px] uppercase tracking-wider whitespace-nowrap sticky top-0"
                style={{ color: 'var(--text-dim)', borderBottom: '1px solid var(--border)', background: 'var(--card)' }}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {trades.map((t) => (
            <tr key={t.id} className="hover:bg-white/[0.02]">
              <td className="px-3.5 py-2 text-xs" style={{ borderBottom: '1px solid rgba(48,54,61,0.5)', color: 'var(--text-dim)' }}>
                {shortTime(t.timestamp)}
              </td>
              <td className="px-3.5 py-2 font-mono font-semibold" style={{ borderBottom: '1px solid rgba(48,54,61,0.5)' }}>
                {t.strategy}/{t.asset}
              </td>
              <td className="px-3.5 py-2 font-mono text-xs" style={{ borderBottom: '1px solid rgba(48,54,61,0.5)' }}>
                <span style={{ color: t.signal.includes('YES') ? 'var(--green)' : t.signal.includes('NO') ? 'var(--red)' : 'var(--text-dim)' }}>
                  {t.signal.includes('YES') ? '▲' : t.signal.includes('NO') ? '▼' : '—'}
                </span>
                {' '}{t.signal}
              </td>
              <td className="px-3.5 py-2 font-mono" style={{ borderBottom: '1px solid rgba(48,54,61,0.5)' }}>
                {t.entry_price.toFixed(4)}
              </td>
              <td className="px-3.5 py-2 font-mono" style={{ borderBottom: '1px solid rgba(48,54,61,0.5)' }}>
                ${t.bet_size.toFixed(2)}
              </td>
              <td className="px-3.5 py-2 font-semibold" style={{
                borderBottom: '1px solid rgba(48,54,61,0.5)',
                color: t.outcome === 'WIN' ? 'var(--green)' : t.outcome === 'LOSS' ? 'var(--red)' : 'var(--gold)',
              }}>
                {t.outcome ?? 'OPEN'}
              </td>
              <td className="px-3.5 py-2 font-mono" style={{
                borderBottom: '1px solid rgba(48,54,61,0.5)',
                color: t.pnl != null ? (t.pnl >= 0 ? 'var(--green)' : 'var(--red)') : 'var(--text-dim)',
              }}>
                {fmtPnl(t.pnl)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
