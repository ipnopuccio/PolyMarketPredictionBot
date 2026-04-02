import { useCallback, useEffect, useState } from 'react';

/**
 * Generic hook for polling a REST endpoint.
 * Falls back to polling when WebSocket is not connected.
 */
export function useApi<T>(
  url: string,
  interval: number = 5000,
): { data: T | null; loading: boolean; error: string | null; refresh: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();
      setData(json);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  }, [url]);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, interval);
    return () => clearInterval(timer);
  }, [refresh, interval]);

  return { data, loading, error, refresh };
}
