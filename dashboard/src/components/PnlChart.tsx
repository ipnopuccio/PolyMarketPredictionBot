import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from 'recharts';
import type { Bot } from '../types/api';

interface Props {
  bots: Bot[];
}

export default function PnlChart({ bots }: Props) {
  const data = bots.map((b) => ({
    name: `${b.strategy}/${b.asset}`,
    pnl: Number(b.total_pnl.toFixed(2)),
    winRate: b.win_rate,
  }));

  return (
    <div className="rounded-xl p-4" style={{ background: 'var(--card)', border: '1px solid var(--border)' }}>
      <h3 className="text-[11px] uppercase tracking-wider mb-3 font-semibold"
        style={{ color: 'var(--text-dim)' }}>
        P&L by Strategy
      </h3>
      <ResponsiveContainer width="100%" height={220}>
        <BarChart data={data} margin={{ top: 5, right: 20, bottom: 5, left: 0 }}>
          <XAxis
            dataKey="name"
            tick={{ fontSize: 11, fill: '#8b949e' }}
            axisLine={{ stroke: '#30363d' }}
            tickLine={false}
          />
          <YAxis
            tick={{ fontSize: 11, fill: '#8b949e' }}
            axisLine={{ stroke: '#30363d' }}
            tickLine={false}
            tickFormatter={(v: number) => `$${v}`}
          />
          <Tooltip
            contentStyle={{
              background: '#161b22',
              border: '1px solid #30363d',
              borderRadius: 8,
              fontSize: 12,
            }}
            formatter={(value) => [`$${Number(value).toFixed(2)}`, 'P&L']}
            labelStyle={{ color: '#e6edf3' }}
          />
          <Bar dataKey="pnl" radius={[4, 4, 0, 0]}>
            {data.map((entry, idx) => (
              <Cell
                key={idx}
                fill={entry.pnl >= 0 ? '#3fb950' : '#f85149'}
                fillOpacity={0.8}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
