'use client';

import React from 'react';
import {
  Activity,
  AlertOctagon,
  CandlestickChart,
  Layers,
  LayoutDashboard,
  LineChart,
  Radio,
  RotateCcw,
  ScrollText,
  Server,
  ShieldAlert,
  Zap,
} from 'lucide-react';
import { RiskStatus, SystemStatus } from '../types/trading';
import {
  NetworkIndicator,
  NetworkStrengthLevel,
} from './NetworkIndicator';

export type TabType =
  | 'overview'
  | 'positions'
  | 'risk'
  | 'system'
  | 'orders'
  | 'markets'
  | 'strategy';

interface NavigationProps {
  activeTab: TabType;
  onSelectTab: (tab: TabType) => void;
  systemStatus: SystemStatus | null;
  riskStatus: RiskStatus | null;
  wsConnected: boolean;
  wsMessageCount: number;
  networkStrength?: NetworkStrengthLevel;
  latency?: number | null;
  avgLatency?: number | null;
  jitter?: number | null;
  latencyHistory?: number[];
  lastMessageTime?: Date | null;
  onOpenKillSwitchModal: () => void;
  onResetKillSwitch: () => void;
}

export const Navigation: React.FC<NavigationProps> = ({
  activeTab,
  onSelectTab,
  systemStatus,
  riskStatus,
  wsConnected,
  wsMessageCount,
  networkStrength = 0,
  latency = null,
  avgLatency = null,
  jitter = null,
  latencyHistory = [],
  lastMessageTime = null,
  onOpenKillSwitchModal,
  onResetKillSwitch,
}) => {
  const env = systemStatus?.environment || 'TESTNET';
  const isKillSwitchActive = riskStatus?.kill_switch_active ?? false;

  const navItems: { id: TabType; label: string; icon: React.ElementType }[] = [
    { id: 'overview', label: 'Overview', icon: LayoutDashboard },
    { id: 'positions', label: 'Positions', icon: Layers },
    { id: 'risk', label: 'Risk & Limits', icon: ShieldAlert },
    { id: 'system', label: 'System Health', icon: Server },
    { id: 'orders', label: 'Orders & Fills', icon: ScrollText },
    { id: 'markets', label: 'Markets & Chart', icon: CandlestickChart },
    { id: 'strategy', label: 'Strategy KPIs', icon: LineChart },
  ];

  return (
    <header className="border-b border-dark-750 bg-dark-900 sticky top-0 z-40">
      {/* Kill Switch Top Alert Banner */}
      {isKillSwitchActive && (
        <div className="bg-red-950/80 border-b border-red-500/50 px-4 py-2 flex items-center justify-between animate-pulse-subtle text-xs text-red-200">
          <div className="flex items-center gap-2 font-mono">
            <AlertOctagon className="w-4 h-4 text-red-400 animate-bounce" />
            <span className="font-bold tracking-wide uppercase text-red-400">
              EMERGENCY KILL-SWITCH ENGAGED
            </span>
            <span className="text-red-300/80">|</span>
            <span>{riskStatus?.kill_switch_reason || 'Manual Halt Triggered'}</span>
          </div>
          <button
            onClick={onResetKillSwitch}
            className="flex items-center gap-1.5 px-3 py-1 bg-red-600 hover:bg-red-500 text-white font-semibold rounded transition shadow-sm hover:shadow-red-600/30"
          >
            <RotateCcw className="w-3.5 h-3.5" />
            Reset & Resume Trading
          </button>
        </div>
      )}

      {/* Main Header Bar */}
      <div className="px-4 py-3 flex flex-wrap items-center justify-between gap-4">
        {/* Brand & Mode */}
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded bg-dark-800 border border-dark-700 flex items-center justify-center text-trade-cyan font-mono font-black text-sm">
            <Zap className="w-4 h-4 text-trade-cyan" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="font-bold text-sm tracking-wider font-mono uppercase text-slate-100">
                VECTOR
              </h1>
              <span
                className={`text-[10px] px-2 py-0.5 rounded font-mono font-bold tracking-widest uppercase ${
                  env === 'LIVE'
                    ? 'bg-red-500/20 text-red-400 border border-red-500/40'
                    : env === 'TESTNET'
                      ? 'bg-amber-500/20 text-amber-400 border border-amber-500/40'
                      : 'bg-blue-500/20 text-blue-400 border border-blue-500/40'
                }`}
              >
                {env}
              </span>
            </div>
            <p className="text-[11px] text-slate-400 font-mono">
              Algorithmic Crypto Trading Engine // Isolated Margin
            </p>
          </div>
        </div>

        {/* Real-time Status Badges & Quick Action */}
        <div className="flex items-center gap-2.5 text-xs font-mono">
          {/* Live Network Strength & Latency Indicator (4-Point Scale) */}
          <NetworkIndicator
            strength={networkStrength}
            latency={latency}
            avgLatency={avgLatency}
            jitter={jitter}
            isConnected={wsConnected}
            messageCount={wsMessageCount}
            lastMessageTime={lastMessageTime}
            latencyHistory={latencyHistory}
          />

          {/* WebSocket Status */}
          <div className="flex items-center gap-2 px-2.5 py-1 rounded bg-dark-850 border border-dark-750 text-slate-300">
            <div
              className={`w-2 h-2 rounded-full ${
                wsConnected ? 'bg-emerald-400 animate-pulse' : 'bg-red-500'
              }`}
            />
            <span className="text-[11px]">
              {wsConnected ? `WS LIVE (${wsMessageCount})` : 'WS RECONNECTING'}
            </span>
          </div>

          {/* Clock Drift */}
          <div className="hidden sm:flex items-center gap-1.5 px-2.5 py-1 rounded bg-dark-850 border border-dark-750 text-slate-400">
            <Activity className="w-3.5 h-3.5 text-slate-500" />
            <span className="text-[11px]">
              Drift:{' '}
              <span className="text-slate-200">
                {systemStatus?.clock_drift_ms !== undefined
                  ? `${systemStatus.clock_drift_ms > 0 ? '+' : ''}${systemStatus.clock_drift_ms}ms`
                  : 'Syncing...'}
              </span>
            </span>
          </div>

          {/* Rate Limit Weight */}
          <div className="hidden md:flex items-center gap-1.5 px-2.5 py-1 rounded bg-dark-850 border border-dark-750 text-slate-400">
            <Radio className="w-3.5 h-3.5 text-slate-500" />
            <span className="text-[11px]">
              IP Weight:{' '}
              <span className="text-slate-200">
                {systemStatus?.rate_limit_used_1m !== undefined && systemStatus.rate_limit_used_1m >= 0
                  ? systemStatus.rate_limit_used_1m
                  : 0}
                /{systemStatus?.rate_limit_max_1m ?? 2400}
              </span>
            </span>
          </div>

          {/* Emergency Kill Switch Trigger Button */}
          {!isKillSwitchActive ? (
            <button
              onClick={onOpenKillSwitchModal}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-red-950 hover:bg-red-900 border border-red-700/60 text-red-300 hover:text-red-100 font-bold rounded transition text-[11px]"
              title="Emergency Kill-Switch: Halt all incoming signals immediately"
            >
              <AlertOctagon className="w-3.5 h-3.5 text-red-400" />
              Kill-Switch
            </button>
          ) : (
            <button
              onClick={onResetKillSwitch}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-emerald-950 hover:bg-emerald-900 border border-emerald-700/60 text-emerald-300 hover:text-emerald-100 font-bold rounded transition text-[11px]"
            >
              <RotateCcw className="w-3.5 h-3.5 text-emerald-400" />
              Reset Halt
            </button>
          )}
        </div>
      </div>

      {/* Navigation Tabs */}
      <nav className="px-4 flex items-center gap-1 overflow-x-auto border-t border-dark-800/80 bg-dark-950/40">
        {navItems.map((item) => {
          const Icon = item.icon;
          const isActive = activeTab === item.id;
          return (
            <button
              key={item.id}
              onClick={() => onSelectTab(item.id)}
              className={`flex items-center gap-2 px-3.5 py-2.5 text-xs font-mono font-medium transition border-b-2 whitespace-nowrap ${
                isActive
                  ? 'border-trade-accent text-slate-100 bg-dark-850/60 font-semibold'
                  : 'border-transparent text-slate-400 hover:text-slate-200 hover:bg-dark-850/30'
              }`}
            >
              <Icon
                className={`w-3.5 h-3.5 ${isActive ? 'text-trade-accent' : 'text-slate-500'}`}
              />
              <span>{item.label}</span>
            </button>
          );
        })}
      </nav>
    </header>
  );
};
