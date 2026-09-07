'use client';

import { useEffect, useRef, useState } from 'react';
import { WebSocketMessage } from '../types/trading';
import { NetworkStrengthLevel } from '../components/NetworkIndicator';

interface WebSocketCallbacks {
  onMessage?: (msg: WebSocketMessage) => void;
  onCandle?: (data: any) => void;
  onMarketTick?: (data: any) => void;
  onFill?: (data: any) => void;
  onOrderUpdate?: (data: any) => void;
  onPositionUpdate?: (data: any) => void;
  onRiskDecision?: (data: any) => void;
  onSystemStatus?: (data: any) => void;
  onPortfolioUpdate?: (data: any) => void;
}

export function useTradingWebSocket(callbacks: WebSocketCallbacks = {}) {
  const [isConnected, setIsConnected] = useState(false);
  const [messageCount, setMessageCount] = useState(0);
  const [lastMessageTime, setLastMessageTime] = useState<Date | null>(null);
  const [latency, setLatency] = useState<number | null>(null);
  const [avgLatency, setAvgLatency] = useState<number | null>(null);
  const [jitter, setJitter] = useState<number | null>(null);
  const [latencyHistory, setLatencyHistory] = useState<number[]>([]);
  const [isBrowserOnline, setIsBrowserOnline] = useState(true);

  const socketRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const pingIntervalRef = useRef<NodeJS.Timeout | null>(null);
  const pingSentTimeRef = useRef<number | null>(null);
  const historyRef = useRef<number[]>([]);
  const callbacksRef = useRef<WebSocketCallbacks>(callbacks);

  // Keep callbacks ref updated to avoid stale closures
  useEffect(() => {
    callbacksRef.current = callbacks;
  }, [callbacks]);

  // Online / Offline browser event listener
  useEffect(() => {
    if (typeof window === 'undefined') return;

    const handleOnline = () => setIsBrowserOnline(true);
    const handleOffline = () => {
      setIsBrowserOnline(false);
      setIsConnected(false);
    };

    window.addEventListener('online', handleOnline);
    window.addEventListener('offline', handleOffline);

    setIsBrowserOnline(window.navigator.onLine);

    return () => {
      window.removeEventListener('online', handleOnline);
      window.removeEventListener('offline', handleOffline);
    };
  }, []);

  useEffect(() => {
    let unmounted = false;

    const getWsUrl = () => {
      if (process.env.NEXT_PUBLIC_WS_URL) return process.env.NEXT_PUBLIC_WS_URL;
      if (typeof window !== 'undefined') {
        const host = window.location.hostname || '127.0.0.1';
        return `ws://${host}:8000/ws`;
      }
      return 'ws://127.0.0.1:8000/ws';
    };

    const wsUrl = getWsUrl();

    const sendPing = () => {
      if (socketRef.current && socketRef.current.readyState === WebSocket.OPEN) {
        pingSentTimeRef.current = performance.now();
        try {
          socketRef.current.send('ping');
        } catch (e) {
          // Socket write error will trigger onerror/onclose
        }
      }
    };

    function connect() {
      if (unmounted) return;

      try {
        const ws = new WebSocket(wsUrl);
        socketRef.current = ws;

        ws.onopen = () => {
          if (!unmounted) {
            setIsConnected(true);
            // Send initial ping immediately upon connection
            sendPing();
          }
        };

        ws.onmessage = (event) => {
          if (unmounted) return;
          try {
            // Heartbeat latency measurement
            if (event.data === 'pong') {
              if (pingSentTimeRef.current !== null) {
                const now = performance.now();
                const rtt = Math.max(1, Math.round(now - pingSentTimeRef.current));
                pingSentTimeRef.current = null;
                setLatency(rtt);

                // Update rolling latency history (keep last 20 samples)
                const updated = [...historyRef.current.slice(-19), rtt];
                historyRef.current = updated;
                setLatencyHistory(updated);

                // Compute average latency
                const sum = updated.reduce((acc, v) => acc + v, 0);
                const avg = Math.round(sum / updated.length);
                setAvgLatency(avg);

                // Compute jitter (mean absolute deviation between consecutive measurements)
                if (updated.length > 1) {
                  let totalDiff = 0;
                  for (let i = 1; i < updated.length; i++) {
                    totalDiff += Math.abs(updated[i] - updated[i - 1]);
                  }
                  const jit = Math.round(totalDiff / (updated.length - 1));
                  setJitter(jit);
                } else {
                  setJitter(0);
                }
              }
              return;
            }

            const msg: WebSocketMessage = JSON.parse(event.data);
            setMessageCount((prev) => prev + 1);
            setLastMessageTime(new Date());

            // Generic callback
            callbacksRef.current.onMessage?.(msg);

            // Specific typed event dispatch
            switch (msg.event_type) {
              case 'candle':
                callbacksRef.current.onCandle?.(msg.data);
                break;
              case 'market_tick':
                callbacksRef.current.onMarketTick?.(msg.data);
                break;
              case 'fill':
                callbacksRef.current.onFill?.(msg.data);
                break;
              case 'order_update':
                callbacksRef.current.onOrderUpdate?.(msg.data);
                break;
              case 'position_update':
                callbacksRef.current.onPositionUpdate?.(msg.data);
                break;
              case 'risk_decision':
                callbacksRef.current.onRiskDecision?.(msg.data);
                break;
              case 'system_status':
                callbacksRef.current.onSystemStatus?.(msg.data);
                break;
              case 'portfolio_update':
                callbacksRef.current.onPortfolioUpdate?.(msg.data);
                break;
            }
          } catch (err) {
            console.error('Error parsing WebSocket frame:', err);
          }
        };

        ws.onclose = () => {
          if (!unmounted) {
            setIsConnected(false);
            setLatency(null);
            reconnectTimeoutRef.current = setTimeout(connect, 3000);
          }
        };

        ws.onerror = () => {
          ws.close();
        };
      } catch (e) {
        if (!unmounted) {
          reconnectTimeoutRef.current = setTimeout(connect, 3000);
        }
      }
    }

    connect();

    // Responsive Heartbeat ping interval every 2.5 seconds
    pingIntervalRef.current = setInterval(sendPing, 2500);

    return () => {
      unmounted = true;
      if (pingIntervalRef.current) clearInterval(pingIntervalRef.current);
      if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current);
      if (socketRef.current) socketRef.current.close();
    };
  }, []);

  // Compute 4-point network strength rating:
  // 4: Green (<60ms) - Optimal execution
  // 3: Yellow (60ms-150ms) - Good / Stable
  // 2: Orange (150ms-300ms) - Fair / Moderate
  // 1: Red (>300ms) - Poor / High latency
  // 0: Offline / Disconnected
  let networkStrength: NetworkStrengthLevel = 0;
  if (isConnected && isBrowserOnline) {
    if (latency === null) {
      networkStrength = 4; // Initial connection handshake pending first ping
    } else if (latency <= 60) {
      networkStrength = 4;
    } else if (latency <= 150) {
      networkStrength = 3;
    } else if (latency <= 300) {
      networkStrength = 2;
    } else {
      networkStrength = 1;
    }
  }

  return {
    isConnected: isConnected && isBrowserOnline,
    messageCount,
    lastMessageTime,
    latency,
    avgLatency,
    jitter,
    networkStrength,
    latencyHistory,
  };
}
