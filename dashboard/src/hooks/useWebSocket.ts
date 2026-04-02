import { useCallback, useEffect, useRef, useState } from 'react';
import type { WSChannel, WSIncoming, WSMessage } from '../types/api';

interface UseWebSocketOptions {
  channels?: WSChannel[];
  maxReconnectDelay?: number;
}

interface UseWebSocketReturn {
  connected: boolean;
  lastMessage: WSMessage | null;
  messages: WSMessage[];
  send: (data: unknown) => void;
}

/**
 * React hook for WebSocket connection with auto-reconnect.
 *
 * Connects to ws://host/api/v1/stream, subscribes to channels,
 * and handles reconnection with exponential backoff (1s→30s).
 */
export function useWebSocket(
  options: UseWebSocketOptions = {},
): UseWebSocketReturn {
  const {
    channels = ['prices', 'signals', 'metrics', 'trades'],
    maxReconnectDelay = 30000,
  } = options;

  const [connected, setConnected] = useState(false);
  const [lastMessage, setLastMessage] = useState<WSMessage | null>(null);
  const [messages, setMessages] = useState<WSMessage[]>([]);

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectDelay = useRef(1000);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const mountedRef = useRef(true);

  const connect = useCallback(() => {
    if (!mountedRef.current) return;

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/api/v1/stream`;

    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      if (!mountedRef.current) return;
      setConnected(true);
      reconnectDelay.current = 1000;

      // Subscribe to channels
      ws.send(JSON.stringify({
        action: 'subscribe',
        channels,
      }));
    };

    ws.onmessage = (event) => {
      if (!mountedRef.current) return;
      try {
        const msg: WSIncoming = JSON.parse(event.data);

        if ('channel' in msg) {
          const wsMsg = msg as WSMessage;
          setLastMessage(wsMsg);
          setMessages((prev) => [...prev.slice(-99), wsMsg]);
        }
        // heartbeat and snapshot are handled silently
      } catch {
        // ignore parse errors
      }
    };

    ws.onclose = () => {
      if (!mountedRef.current) return;
      setConnected(false);

      // Exponential backoff reconnect
      reconnectTimer.current = setTimeout(() => {
        reconnectDelay.current = Math.min(
          reconnectDelay.current * 2,
          maxReconnectDelay,
        );
        connect();
      }, reconnectDelay.current);
    };

    ws.onerror = () => {
      ws.close();
    };
  }, [channels, maxReconnectDelay]);

  const send = useCallback((data: unknown) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(data));
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    connect();

    return () => {
      mountedRef.current = false;
      clearTimeout(reconnectTimer.current);
      wsRef.current?.close();
    };
  }, [connect]);

  return { connected, lastMessage, messages, send };
}
