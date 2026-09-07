'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { Navigation, TabType } from '../components/Navigation';
import { KillSwitchModal } from '../components/KillSwitchModal';
import { OverviewView } from '../components/views/OverviewView';
import { PositionsView } from '../components/views/PositionsView';
import { RiskView } from '../components/views/RiskView';
import { SystemView } from '../components/views/SystemView';
import { OrdersView } from '../components/views/OrdersView';
import { MarketsView } from '../components/views/MarketsView';
import { StrategyView } from '../components/views/StrategyView';
import { useTradingWebSocket } from '../hooks/useTradingWebSocket';
import { api } from '../services/api';
import {
  MarketTicker,
  Order,
  Portfolio,
  Position,
  RiskStatus,
  Strategy,
  SystemStatus,
} from '../types/trading';
import { AlertTriangle, CheckCircle, Info, X } from 'lucide-react';

interface Toast {
  id: string;
  type: 'info' | 'success' | 'warning' | 'error';
  title: string;
  message: string;
}

export default function DashboardPage() {
  const [activeTab, setActiveTab] = useState<TabType>('overview');

  // Core platform states
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [positions, setPositions] = useState<Position[]>([]);
  const [openOrders, setOpenOrders] = useState<Order[]>([]);
  const [orderHistory, setOrderHistory] = useState<Order[]>([]);
  const [strategy, setStrategy] = useState<Strategy | null>(null);
  const [riskStatus, setRiskStatus] = useState<RiskStatus | null>(null);
  const [systemStatus, setSystemStatus] = useState<SystemStatus | null>(null);
  const [markets, setMarkets] = useState<MarketTicker[]>([]);
  const [latestCandle, setLatestCandle] = useState<any>(null);

  // UI Modals & Errors
  const [isKillSwitchModalOpen, setIsKillSwitchModalOpen] = useState(false);
  const [isLoadingKillSwitch, setIsLoadingKillSwitch] = useState(false);
  const [killSwitchError, setKillSwitchError] = useState<string | null>(null);
  const [toasts, setToasts] = useState<Toast[]>([]);

  const addToast = useCallback(
    (type: Toast['type'], title: string, message: string) => {
      const id = `${Date.now()}_${Math.random()}`;
      setToasts((prev) => [...prev.slice(-4), { id, type, title, message }]);
      setTimeout(() => {
        setToasts((prev) => prev.filter((t) => t.id !== id));
      }, 5000);
    },
    []
  );

  const removeToast = (id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  };

  // Initial Data Fetch
  const refreshAllData = useCallback(async () => {
    try {
      const [p, pos, oo, oh, st, r, sys, m] = await Promise.allSettled([
        api.getPortfolio(),
        api.getPositions(),
        api.getOpenOrders(),
        api.getOrderHistory(50),
        api.getStrategy(),
        api.getRiskStatus(),
        api.getSystemStatus(),
        api.getMarkets(),
      ]);

      if (p.status === 'fulfilled') setPortfolio(p.value);
      if (pos.status === 'fulfilled') setPositions(pos.value);
      if (oo.status === 'fulfilled') setOpenOrders(oo.value);
      if (oh.status === 'fulfilled') setOrderHistory(oh.value);
      if (st.status === 'fulfilled') setStrategy(st.value);
      if (r.status === 'fulfilled') setRiskStatus(r.value);
      if (sys.status === 'fulfilled') setSystemStatus(sys.value);
      if (m.status === 'fulfilled') setMarkets(m.value);
    } catch (err) {
      console.error('Error hydrating platform dashboard:', err);
    }
  }, []);

  useEffect(() => {
    refreshAllData();
    const interval = setInterval(refreshAllData, 8000);
    return () => clearInterval(interval);
  }, [refreshAllData]);

  // Real-time WebSocket Event Subscriptions & Network Telemetry
  const {
    isConnected,
    messageCount,
    lastMessageTime,
    latency,
    avgLatency,
    jitter,
    networkStrength,
    latencyHistory,
  } = useTradingWebSocket({
    onCandle: (candle) => {
      setLatestCandle(candle);
    },
    onMarketTick: (tick) => {
      if (!tick || !tick.symbol) return;
      setMarkets((prevMarkets) => {
        const sym = tick.symbol.toUpperCase();
        const existingIdx = prevMarkets.findIndex((m) => m.symbol === sym);
        if (existingIdx === -1) {
          return [
            ...prevMarkets,
            {
              symbol: sym,
              mark_price: tick.mark_price,
              last_price: tick.last_price || tick.mark_price,
              index_price: tick.index_price || tick.mark_price,
              funding_rate: tick.funding_rate ?? 0.0001,
              next_funding_time: null,
              price_change_percent_24h: tick.price_change_percent_24h ?? 0.0,
              high_price_24h: tick.high_price_24h ?? tick.mark_price * 1.01,
              low_price_24h: tick.low_price_24h ?? tick.mark_price * 0.99,
              volume_24h: tick.volume_24h ?? 1000.0,
              quote_volume_24h: tick.quote_volume_24h ?? 1000.0 * tick.mark_price,
              tick_size: 0.1,
              step_size: 0.001,
              min_notional: 5.0,
              last_updated: new Date().toISOString(),
            },
          ];
        }
        const updated = [...prevMarkets];
        const prev = updated[existingIdx];
        updated[existingIdx] = {
          ...prev,
          mark_price: tick.mark_price !== undefined ? tick.mark_price : prev.mark_price,
          last_price: tick.last_price !== undefined ? tick.last_price : prev.last_price,
          index_price: tick.index_price !== undefined ? tick.index_price : prev.index_price,
          funding_rate: tick.funding_rate !== undefined ? tick.funding_rate : prev.funding_rate,
          price_change_percent_24h:
            tick.price_change_percent_24h !== undefined
              ? tick.price_change_percent_24h
              : prev.price_change_percent_24h,
          high_price_24h:
            tick.high_price_24h !== undefined ? tick.high_price_24h : prev.high_price_24h,
          low_price_24h:
            tick.low_price_24h !== undefined ? tick.low_price_24h : prev.low_price_24h,
          volume_24h:
            tick.volume_24h !== undefined ? tick.volume_24h : prev.volume_24h,
          quote_volume_24h:
            tick.quote_volume_24h !== undefined ? tick.quote_volume_24h : prev.quote_volume_24h,
          last_updated: new Date().toISOString(),
        };
        return updated;
      });
    },
    onFill: (fill) => {
      addToast(
        'success',
        `FILL EXECUTED: ${fill.symbol}`,
        `${fill.side} ${fill.quantity} @ $${fill.price.toLocaleString()} (Fee: ${fill.fee} ${fill.fee_asset})`
      );
      api.getPortfolio().then(setPortfolio).catch(console.error);
      api.getPositions().then(setPositions).catch(console.error);
      api.getOrderHistory(50).then(setOrderHistory).catch(console.error);
    },
    onOrderUpdate: () => {
      api.getOpenOrders().then(setOpenOrders).catch(console.error);
    },
    onPositionUpdate: () => {
      api.getPositions().then(setPositions).catch(console.error);
    },
    onPortfolioUpdate: (data) => {
      setPortfolio((prev) => (prev ? { ...prev, ...data } : null));
    },
    onRiskDecision: (decision) => {
      if (decision.decision === 'REJECTED') {
        addToast(
          'warning',
          `SIGNAL REJECTED: ${decision.symbol}`,
          `Risk rule triggered: ${decision.reason}`
        );
      }
      api.getRiskStatus().then(setRiskStatus).catch(console.error);
    },
    onSystemStatus: (status) => {
      if (status.status === 'HALTED') {
        addToast('error', 'SYSTEM HALTED', status.message);
        setRiskStatus((prev) =>
          prev
            ? {
                ...prev,
                kill_switch_active: true,
                kill_switch_reason: status.message,
              }
            : null
        );
      } else if (status.status === 'ONLINE' && status.component === 'RiskEngine') {
        setRiskStatus((prev) =>
          prev
            ? {
                ...prev,
                kill_switch_active: false,
                kill_switch_reason: null,
              }
            : null
        );
      }
      api.getSystemStatus().then(setSystemStatus).catch(console.error);
      api.getRiskStatus().then(setRiskStatus).catch(console.error);
    },
  });

  // Kill Switch Handlers
  const handleTriggerKillSwitch = async (reason: string) => {
    setIsLoadingKillSwitch(true);
    setKillSwitchError(null);
    try {
      const res = await api.triggerKillSwitch(reason);
      if (res.success) {
        // Immediate optimistic update
        setRiskStatus((prev) =>
          prev
            ? {
                ...prev,
                kill_switch_active: true,
                kill_switch_reason: reason,
              }
            : null
        );
        addToast('error', 'KILL-SWITCH ENGAGED', `Reason: ${reason}`);
        setIsKillSwitchModalOpen(false);
        await refreshAllData();
      } else {
        throw new Error('Server returned unconfirmed response');
      }
    } catch (err: any) {
      console.error('Kill-switch trigger error:', err);
      setKillSwitchError(err.message || 'Failed to engage emergency kill switch');
      addToast('error', 'FAILED TO ENGAGE KILL-SWITCH', err.message || 'API request failed');
    } finally {
      setIsLoadingKillSwitch(false);
    }
  };

  const handleResetKillSwitch = async () => {
    try {
      const res = await api.resetKillSwitch('Operator manually cleared emergency halt');
      if (res.success) {
        setRiskStatus((prev) =>
          prev
            ? {
                ...prev,
                kill_switch_active: false,
                kill_switch_reason: null,
              }
            : null
        );
        addToast('success', 'KILL-SWITCH RESET', 'Platform resumed normal operations.');
        await refreshAllData();
      } else {
        throw new Error('Server returned unconfirmed response');
      }
    } catch (err: any) {
      console.error('Kill-switch reset error:', err);
      addToast('error', 'FAILED TO RESET KILL-SWITCH', err.message || 'API request failed');
    }
  };

  const openKillSwitchModal = () => {
    setKillSwitchError(null);
    setIsKillSwitchModalOpen(true);
  };

  return (
    <div className="min-h-screen flex flex-col bg-dark-950 font-sans">
      {/* Institutional Top Navigation */}
      <Navigation
        activeTab={activeTab}
        onSelectTab={setActiveTab}
        systemStatus={systemStatus}
        riskStatus={riskStatus}
        wsConnected={isConnected}
        wsMessageCount={messageCount}
        networkStrength={networkStrength}
        latency={latency}
        avgLatency={avgLatency}
        jitter={jitter}
        latencyHistory={latencyHistory}
        lastMessageTime={lastMessageTime}
        onOpenKillSwitchModal={openKillSwitchModal}
        onResetKillSwitch={handleResetKillSwitch}
      />

      {/* Main Content Area */}
      <main className="flex-1 max-w-7xl w-full mx-auto p-4 sm:p-6">
        {activeTab === 'overview' && (
          <OverviewView
            portfolio={portfolio}
            positions={positions}
            riskStatus={riskStatus}
            systemStatus={systemStatus}
            strategy={strategy}
            markets={markets}
            onNavigateToTab={setActiveTab}
          />
        )}
        {activeTab === 'positions' && <PositionsView positions={positions} />}
        {activeTab === 'risk' && (
          <RiskView
            riskStatus={riskStatus}
            onOpenKillSwitchModal={openKillSwitchModal}
            onResetKillSwitch={handleResetKillSwitch}
          />
        )}
        {activeTab === 'system' && (
          <SystemView
            systemStatus={systemStatus}
            wsConnected={isConnected}
            wsMessageCount={messageCount}
            networkStrength={networkStrength}
            latency={latency}
            avgLatency={avgLatency}
            jitter={jitter}
            latencyHistory={latencyHistory}
          />
        )}
        {activeTab === 'orders' && (
          <OrdersView
            openOrders={openOrders}
            orderHistory={orderHistory}
            onRefreshOrders={refreshAllData}
          />
        )}
        {activeTab === 'markets' && (
          <MarketsView markets={markets} latestCandle={latestCandle} />
        )}
        {activeTab === 'strategy' && <StrategyView strategy={strategy} />}
      </main>

      {/* Emergency Kill-Switch Trigger Modal */}
      <KillSwitchModal
        isOpen={isKillSwitchModalOpen}
        onClose={() => setIsKillSwitchModalOpen(false)}
        onConfirm={handleTriggerKillSwitch}
        isLoading={isLoadingKillSwitch}
        serverError={killSwitchError}
      />

      {/* Toast Notification Container */}
      <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 max-w-md w-full pointer-events-none font-mono">
        {toasts.map((toast) => {
          const isError = toast.type === 'error';
          const isSuccess = toast.type === 'success';
          const isWarning = toast.type === 'warning';

          return (
            <div
              key={toast.id}
              className={`pointer-events-auto p-3.5 rounded-lg border shadow-xl flex items-start justify-between gap-3 text-xs animate-in slide-in-from-right duration-200 ${
                isError
                  ? 'bg-red-950/95 border-red-500/80 text-red-100'
                  : isSuccess
                    ? 'bg-emerald-950/95 border-emerald-500/80 text-emerald-100'
                    : isWarning
                      ? 'bg-amber-950/95 border-amber-500/80 text-amber-100'
                      : 'bg-dark-900/95 border-dark-700 text-slate-100'
              }`}
            >
              <div className="flex items-start gap-2.5">
                {isError && <AlertTriangle className="w-4 h-4 text-red-400 mt-0.5 shrink-0" />}
                {isSuccess && <CheckCircle className="w-4 h-4 text-emerald-400 mt-0.5 shrink-0" />}
                {isWarning && <AlertTriangle className="w-4 h-4 text-amber-400 mt-0.5 shrink-0" />}
                {!isError && !isSuccess && !isWarning && (
                  <Info className="w-4 h-4 text-trade-cyan mt-0.5 shrink-0" />
                )}
                <div>
                  <div className="font-bold uppercase tracking-wide">{toast.title}</div>
                  <div className="text-[11px] text-slate-300 mt-0.5">{toast.message}</div>
                </div>
              </div>
              <button
                onClick={() => removeToast(toast.id)}
                className="text-slate-400 hover:text-white p-0.5 rounded cursor-pointer"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}
