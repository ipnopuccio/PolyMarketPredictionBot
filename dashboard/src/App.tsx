import TopBar from './components/TopBar';
import BotCards from './components/BotCards';
import SignalTable from './components/SignalTable';
import TradesTable from './components/TradesTable';
import PnlChart from './components/PnlChart';
import HealthStatus from './components/HealthStatus';
import { useApi } from './hooks/useApi';
import { useWebSocket } from './hooks/useWebSocket';
import type { Overview, Bot, SignalState, Trade, Health, RiskEvent } from './types/api';

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="text-sm font-semibold uppercase tracking-wider mt-8 mb-3.5"
      style={{ color: 'var(--text-dim)' }}>
      {children}
    </h2>
  );
}

export default function App() {
  // REST polling (fallback + initial data)
  const { data: overview } = useApi<Overview>('/api/overview', 5000);
  const { data: bots } = useApi<Bot[]>('/api/bots', 5000);
  const { data: signals } = useApi<SignalState[]>('/api/signals', 5000);
  const { data: trades } = useApi<Trade[]>('/api/trades?limit=50', 5000);
  const { data: health } = useApi<Health>('/api/v2/health', 15000);
  const { data: riskEvents } = useApi<RiskEvent[]>('/api/risk-events', 10000);

  // WebSocket for real-time updates
  const { connected: wsConnected } = useWebSocket();

  return (
    <>
      <TopBar overview={overview} wsConnected={wsConnected} />

      <main className="max-w-[1400px] mx-auto px-6">
        {/* Bot Cards */}
        <SectionTitle>Active Bots</SectionTitle>
        <BotCards bots={bots ?? []} />

        {/* Charts */}
        {bots && bots.length > 0 && (
          <>
            <SectionTitle>Performance</SectionTitle>
            <PnlChart bots={bots} />
          </>
        )}

        {/* Signals */}
        <SectionTitle>Live Signals</SectionTitle>
        <SignalTable signals={signals ?? []} />

        {/* Trades */}
        <SectionTitle>Recent Trades</SectionTitle>
        <TradesTable trades={trades ?? []} />

        {/* Risk Events */}
        {riskEvents && riskEvents.length > 0 && (
          <>
            <SectionTitle>Risk Events</SectionTitle>
            <div className="rounded-xl overflow-y-auto max-h-[280px] py-1"
              style={{ background: 'var(--card)', border: '1px solid var(--border)' }}>
              {riskEvents.map((e, i) => (
                <div key={i} className="flex gap-3 px-4 py-1.5 text-xs"
                  style={{ borderBottom: '1px solid rgba(48,54,61,0.3)' }}>
                  <span className="font-mono min-w-[150px]" style={{ color: 'var(--text-dim)' }}>
                    {e.timestamp}
                  </span>
                  <span className="font-semibold min-w-[140px]" style={{ color: 'var(--gold)' }}>
                    {e.event_type}
                  </span>
                  <span style={{ color: 'var(--text-dim)' }}>
                    {e.strategy ?? ''}{e.asset ? ` / ${e.asset}` : ''} {e.details ?? ''}
                  </span>
                </div>
              ))}
            </div>
          </>
        )}

        {/* Health */}
        <SectionTitle>System Health</SectionTitle>
        <HealthStatus health={health} />

        <div className="h-8" />
      </main>
    </>
  );
}
