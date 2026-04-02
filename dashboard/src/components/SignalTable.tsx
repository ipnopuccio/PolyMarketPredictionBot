import type { SignalState } from '../types/api';

function fmt(n: number | null, d: number = 4): string {
  return n != null ? n.toFixed(d) : '--';
}

function shortTime(ts: string | null): string {
  if (!ts) return '--';
  try {
    return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  } catch {
    return ts.slice(11, 19);
  }
}

interface Props {
  signals: SignalState[];
}

export default function SignalTable({ signals }: Props) {
  return (
    <div className="rounded-xl overflow-x-auto" style={{ background: 'var(--card)', border: '1px solid var(--border)' }}>
      <table className="w-full border-collapse text-[13px]">
        <thead>
          <tr>
            {['Bot', 'Price', 'CVD', 'VWAP %', 'Funding', 'RSI', 'BB %', 'Regime', 'Updated'].map((h) => (
              <th key={h} className="text-left px-3.5 py-2.5 text-[11px] uppercase tracking-wider whitespace-nowrap"
                style={{ color: 'var(--text-dim)', borderBottom: '1px solid var(--border)' }}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {signals.map((s) => (
            <tr key={`${s.strategy}-${s.asset}`} className="hover:bg-white/[0.02]">
              <td className="px-3.5 py-2 font-mono font-semibold" style={{ borderBottom: '1px solid rgba(48,54,61,0.5)' }}>
                {s.strategy}/{s.asset}
              </td>
              <td className="px-3.5 py-2 font-mono" style={{ borderBottom: '1px solid rgba(48,54,61,0.5)' }}>
                {s.price != null ? `$${s.price.toFixed(2)}` : '--'}
              </td>
              <td className="px-3.5 py-2 font-mono" style={{ borderBottom: '1px solid rgba(48,54,61,0.5)' }}>
                {fmt(s.cvd)}
              </td>
              <td className="px-3.5 py-2 font-mono" style={{ borderBottom: '1px solid rgba(48,54,61,0.5)' }}>
                {fmt(s.vwap_change)}
              </td>
              <td className="px-3.5 py-2 font-mono" style={{ borderBottom: '1px solid rgba(48,54,61,0.5)' }}>
                {fmt(s.funding_rate)}
              </td>
              <td className="px-3.5 py-2 font-mono" style={{
                borderBottom: '1px solid rgba(48,54,61,0.5)',
                color: s.rsi != null ? (s.rsi < 30 ? 'var(--green)' : s.rsi > 70 ? 'var(--red)' : 'var(--text)') : 'var(--text-dim)',
              }}>
                {s.rsi != null ? s.rsi.toFixed(1) : '--'}
              </td>
              <td className="px-3.5 py-2 font-mono" style={{ borderBottom: '1px solid rgba(48,54,61,0.5)' }}>
                {s.bb_pct != null ? s.bb_pct.toFixed(3) : '--'}
              </td>
              <td className="px-3.5 py-2 font-semibold" style={{ borderBottom: '1px solid rgba(48,54,61,0.5)', color: 'var(--purple)' }}>
                {s.regime ?? '--'}
              </td>
              <td className="px-3.5 py-2 text-xs" style={{ borderBottom: '1px solid rgba(48,54,61,0.5)', color: 'var(--text-dim)' }}>
                {shortTime(s.updated_at)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
