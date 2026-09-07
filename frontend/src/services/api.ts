import {
  KlineCandle,
  MarketTicker,
  Order,
  Portfolio,
  Position,
  RiskStatus,
  Strategy,
  SystemStatus,
} from '../types/trading';

const getApiBase = () => {
  if (typeof window !== 'undefined') {
    return process.env.NEXT_PUBLIC_API_URL || '/api';
  }
  return process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000/api';
};

async function fetchJson<T>(endpoint: string, options?: RequestInit): Promise<T> {
  const base = getApiBase();
  const url = endpoint.startsWith('http') ? endpoint : `${base}${endpoint}`;

  let res: Response;
  try {
    res = await fetch(url, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...(options?.headers || {}),
      },
    });
  } catch (netErr: any) {
    throw new Error(`Network connection failed connecting to ${url}: ${netErr.message}`);
  }

  if (!res.ok) {
    let errorMsg = `API Error ${res.status}: ${res.statusText}`;
    try {
      const errBody = await res.json();
      if (errBody.detail) {
        errorMsg = typeof errBody.detail === 'string' ? errBody.detail : JSON.stringify(errBody.detail);
      } else if (errBody.message) {
        errorMsg = errBody.message;
      }
    } catch (_) {}
    throw new Error(errorMsg);
  }

  return res.json();
}

export const api = {
  getPortfolio: () => fetchJson<Portfolio>('/portfolio'),
  getPositions: () => fetchJson<Position[]>('/positions'),
  getOpenOrders: () => fetchJson<Order[]>('/orders/open'),
  getOrderHistory: (limit: number = 50) =>
    fetchJson<Order[]>(`/orders/history?limit=${limit}`),
  getStrategy: () => fetchJson<Strategy>('/strategy'),
  getRiskStatus: () => fetchJson<RiskStatus>('/risk'),
  getSystemStatus: () => fetchJson<SystemStatus>('/system'),
  getMarkets: () => fetchJson<MarketTicker[]>('/markets'),
  getKlines: (symbol: string, timeframe: string = '1m', limit: number = 120) =>
    fetchJson<KlineCandle[]>(`/markets/${symbol}/klines?timeframe=${timeframe}&limit=${limit}`),

  triggerKillSwitch: (reason: string) =>
    fetchJson<{ success: boolean; kill_switch_active: boolean; reason: string }>(
      '/risk/kill-switch/trigger',
      {
        method: 'POST',
        body: JSON.stringify({ reason, confirm: true }),
      }
    ),

  resetKillSwitch: (reason: string = 'Manual operator reset') =>
    fetchJson<{ success: boolean; kill_switch_active: boolean; reason: string }>(
      '/risk/kill-switch/reset',
      {
        method: 'POST',
        body: JSON.stringify({ reason, confirm: true }),
      }
    ),

  forceTestSignal: (payload: {
    symbol: string;
    side: 'BUY' | 'SELL';
    target_exposure: number;
    reason: string;
    confirm: boolean;
  }) =>
    fetchJson<{
      success: boolean;
      signal_id: string;
      symbol: string;
      side: string;
      target_exposure: number;
      mark_price: number;
      status: string;
      message: string;
      timestamp: string;
    }>('/strategy/test-signal', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
};
