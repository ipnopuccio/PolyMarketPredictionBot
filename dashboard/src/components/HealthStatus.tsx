import type { Health } from '../types/api';

interface Props {
  health: Health | null;
}

export default function HealthStatus({ health }: Props) {
  if (!health) return null;

  const statusColor = health.status === 'healthy' ? 'var(--green)' : 'var(--gold)';

  return (
    <div className="rounded-xl p-4 flex items-center gap-6 flex-wrap"
      style={{ background: 'var(--card)', border: '1px solid var(--border)' }}>

      <div className="flex items-center gap-2">
        <span className="w-2.5 h-2.5 rounded-full" style={{ background: statusColor }} />
        <span className="font-semibold text-sm uppercase tracking-wider" style={{ color: statusColor }}>
          {health.status}
        </span>
      </div>

      {Object.entries(health.components).map(([name, status]) => (
        <div key={name} className="flex items-center gap-1.5 text-xs">
          <span className="w-1.5 h-1.5 rounded-full" style={{
            background: status === 'ok' || status === 'active' ? 'var(--green)' : 'var(--red)',
          }} />
          <span style={{ color: 'var(--text-dim)' }}>{name}:</span>
          <span className="font-mono font-semibold"
            style={{ color: status === 'ok' || status === 'active' ? 'var(--green)' : 'var(--red)' }}>
            {status}
          </span>
        </div>
      ))}
    </div>
  );
}
