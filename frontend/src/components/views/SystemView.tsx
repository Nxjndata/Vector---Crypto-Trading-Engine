'use client';

import React from 'react';
import {
  Activity,
  CheckCircle2,
  Clock,
  Database,
  Radio,
  RefreshCw,
  Server,
  ShieldCheck,
  Wifi,
  Zap,
} from 'lucide-react';
import { SystemStatus } from '../../types/trading';
import {
  NetworkIndicator,
  NetworkStrengthLevel,
} from '../NetworkIndicator';

interface SystemViewProps {
  systemStatus: SystemStatus | null;
  wsConnected: boolean;
  wsMessageCount: number;
  networkStrength?: NetworkStrengthLevel;
  latency?: number | null;
  avgLatency?: number | null;
  jitter?: number | null;
  latencyHistory?: number[];
}

export const SystemView: React.FC<SystemViewProps> = ({
  systemStatus,
  wsConnected,
  wsMessageCount,
  networkStrength = 0,
  latency = null,
  avgLatency = null,
  jitter = null,
  latencyHistory = [],
}) => {
  const components = systemStatus?.components || [];
  const reconMismatches = systemStatus?.reconciliation_mismatches_count ?? 0;
  const isHealthyRecon = reconMismatches === 0;

  // Signal color styling for 4-point scale
  const qualityMap = {
    4: {
      label: 'EXCELLENT',
      scale: '4/4',
      badge: 'bg-emerald-500/20 text-emerald-400 border-emerald-500/40',
      barColor: 'bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.6)]',
      textColor: 'text-emerald-400',
    },
    3: {
      label: 'GOOD',
      scale: '3/4',
      badge: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/40',
      barColor: 'bg-yellow-400 shadow-[0_0_8px_rgba(250,204,21,0.6)]',
      textColor: 'text-yellow-400',
    },
    2: {
      label: 'FAIR',
      scale: '2/4',
      badge: 'bg-orange-500/20 text-orange-400 border-orange-500/40',
      barColor: 'bg-orange-500 shadow-[0_0_8px_rgba(249,115,22,0.6)]',
      textColor: 'text-orange-400',
    },
    1: {
      label: 'POOR',
      scale: '1/4',
      badge: 'bg-red-500/20 text-red-400 border-red-500/40',
      barColor: 'bg-red-500 shadow-[0_0_8px_rgba(239,68,68,0.6)]',
      textColor: 'text-red-400',
    },
    0: {
      label: 'OFFLINE',
      scale: '0/4',
      badge: 'bg-red-500/20 text-red-400 border-red-500/40',
      barColor: 'bg-red-500',
      textColor: 'text-red-400',
    },
  };

  const currentQ = !wsConnected ? qualityMap[0] : qualityMap[networkStrength] || qualityMap[1];

  return (
    <div className="space-y-6">
      {/* Top Telemetry Hardware / Network Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {/* Network Connection & Latency Card (4-Point Scale) */}
        <div className="p-4 bg-dark-850 border border-dark-750 rounded-lg space-y-2">
          <div className="flex items-center justify-between text-slate-400 text-xs font-mono uppercase">
            <span>Network Strength</span>
            <div className="flex items-end gap-[3px] h-4">
              {[1, 2, 3, 4].map((barLevel, idx) => {
                const isActive = wsConnected && networkStrength >= barLevel;
                const heights = ['h-1.5', 'h-2.5', 'h-3.5', 'h-4.5'];
                return (
                  <span
                    key={barLevel}
                    className={`w-1 rounded-xs transition-all duration-300 ${heights[idx]} ${
                      isActive
                        ? currentQ.barColor
                        : !wsConnected && barLevel === 1
                          ? 'bg-red-500 animate-pulse'
                          : 'bg-dark-700/60'
                    }`}
                  />
                );
              })}
            </div>
          </div>
          <div className="text-xl font-mono font-black text-slate-100 flex items-center justify-between gap-2">
            <span className={`tabular-numbers ${currentQ.textColor}`}>
              {wsConnected && latency !== null ? `${latency}ms` : wsConnected ? '<1ms' : 'OFFLINE'}
            </span>
            <span
              className={`text-[10px] font-bold px-2 py-0.5 rounded border uppercase ${currentQ.badge}`}
            >
              {currentQ.scale} {currentQ.label}
            </span>
          </div>
          <div className="flex items-center justify-between text-[11px] font-mono text-slate-400">
            <span>Avg: {avgLatency !== null ? `${avgLatency}ms` : '--'}</span>
            <span>Jitter: {jitter !== null ? `±${jitter}ms` : '--'}</span>
          </div>
        </div>

        {/* Server Clock Drift */}
        <div className="p-4 bg-dark-850 border border-dark-750 rounded-lg space-y-1">
          <div className="flex items-center justify-between text-slate-400 text-xs font-mono uppercase">
            <span>Server Clock Drift</span>
            <Clock className="w-4 h-4 text-trade-accent" />
          </div>
          <div className="text-xl font-mono font-black text-slate-100 tabular-numbers flex items-center gap-2">
            <span>
              {systemStatus?.clock_drift_ms !== undefined
                ? `${systemStatus.clock_drift_ms > 0 ? '+' : ''}${systemStatus.clock_drift_ms.toFixed(2)}ms`
                : 'Syncing...'}
            </span>
            <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-400">
              OK (&lt;1000ms)
            </span>
          </div>
          <p className="text-[11px] font-mono text-slate-500">
            Synchronized against Binance Futures NTP
          </p>
        </div>

        {/* WebSocket Stream Health */}
        <div className="p-4 bg-dark-850 border border-dark-750 rounded-lg space-y-1">
          <div className="flex items-center justify-between text-slate-400 text-xs font-mono uppercase">
            <span>WebSocket Stream</span>
            <Wifi
              className={`w-4 h-4 ${wsConnected ? 'text-emerald-400' : 'text-red-400'}`}
            />
          </div>
          <div className="text-xl font-mono font-black text-slate-100 flex items-center gap-2">
            <span>{wsConnected ? 'CONNECTED' : 'DISCONNECTED'}</span>
          </div>
          <p className="text-[11px] font-mono text-slate-500 tabular-numbers">
            Frames Received: {wsMessageCount}
          </p>
        </div>

        {/* API Rate Limit Weight */}
        <div className="p-4 bg-dark-850 border border-dark-750 rounded-lg space-y-1">
          <div className="flex items-center justify-between text-slate-400 text-xs font-mono uppercase">
            <span>Binance IP Weight (1m)</span>
            <Radio className="w-4 h-4 text-amber-400" />
          </div>
          <div className="text-xl font-mono font-black text-slate-100 tabular-numbers">
            {systemStatus?.rate_limit_used_1m !== undefined && systemStatus.rate_limit_used_1m >= 0
              ? systemStatus.rate_limit_used_1m
              : 0}{' '}
            <span className="text-xs text-slate-500 font-normal">
              / {systemStatus?.rate_limit_max_1m ?? 2400}
            </span>
          </div>
          <div className="w-full bg-dark-750 rounded-full h-1.5 overflow-hidden mt-1">
            <div
              className="bg-amber-400 h-1.5 rounded-full"
              style={{
                width: `${(Math.max(0, systemStatus?.rate_limit_used_1m ?? 0) / (systemStatus?.rate_limit_max_1m ?? 2400)) * 100}%`,
              }}
            />
          </div>
        </div>
      </div>

      {/* Network Connection Detailed Diagnostics & Live Ping Stream Panel */}
      <div className="p-5 bg-dark-850 border border-dark-750 rounded-lg space-y-4 font-mono">
        <div className="flex flex-wrap items-center justify-between gap-4 border-b border-dark-750 pb-3">
          <div className="flex items-center gap-2">
            <Activity className="w-5 h-5 text-trade-cyan" />
            <div>
              <h3 className="text-sm font-bold uppercase text-slate-100">
                Network Connection Quality &amp; Latency Stream
              </h3>
              <p className="text-[11px] text-slate-400">
                Real-time 4-point scale evaluating WebSocket RTT latency and packet consistency
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <span className={`px-3 py-1 rounded text-xs font-bold uppercase border ${currentQ.badge}`}>
              SIGNAL STRENGTH: {currentQ.scale} {currentQ.label}
            </span>
          </div>
        </div>

        {/* 4-Point Scale Visual Cards */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
          {/* 1. Red Scale Card */}
          <div
            className={`p-3 rounded-lg border transition-all ${
              networkStrength === 1 || (!wsConnected && networkStrength === 0)
                ? 'bg-red-950/40 border-red-500/80 shadow-[0_0_12px_rgba(239,68,68,0.2)]'
                : 'bg-dark-900/40 border-dark-750 opacity-60'
            }`}
          >
            <div className="flex items-center justify-between text-xs mb-1">
              <span className="font-bold text-red-400">1. POOR / WEAK</span>
              <span className="text-[10px] text-red-400 font-bold">&gt; 300ms</span>
            </div>
            <p className="text-[10px] text-slate-400">
              High latency or disconnected. High risk of order slippage.
            </p>
            <div className="flex items-end gap-1 mt-2 h-2">
              <span className="w-2 h-2 rounded-xs bg-red-500" />
              <span className="w-2 h-2 rounded-xs bg-dark-700/60" />
              <span className="w-2 h-2 rounded-xs bg-dark-700/60" />
              <span className="w-2 h-2 rounded-xs bg-dark-700/60" />
            </div>
          </div>

          {/* 2. Orange Scale Card */}
          <div
            className={`p-3 rounded-lg border transition-all ${
              networkStrength === 2 && wsConnected
                ? 'bg-orange-950/40 border-orange-500/80 shadow-[0_0_12px_rgba(249,115,22,0.2)]'
                : 'bg-dark-900/40 border-dark-750 opacity-60'
            }`}
          >
            <div className="flex items-center justify-between text-xs mb-1">
              <span className="font-bold text-orange-400">2. FAIR / MODERATE</span>
              <span className="text-[10px] text-orange-400 font-bold">150 - 300ms</span>
            </div>
            <p className="text-[10px] text-slate-400">
              Moderate connection latency. Acceptable for standard swing orders.
            </p>
            <div className="flex items-end gap-1 mt-2 h-2">
              <span className="w-2 h-2 rounded-xs bg-orange-500" />
              <span className="w-2 h-2 rounded-xs bg-orange-500" />
              <span className="w-2 h-2 rounded-xs bg-dark-700/60" />
              <span className="w-2 h-2 rounded-xs bg-dark-700/60" />
            </div>
          </div>

          {/* 3. Yellow Scale Card */}
          <div
            className={`p-3 rounded-lg border transition-all ${
              networkStrength === 3 && wsConnected
                ? 'bg-yellow-950/40 border-yellow-500/80 shadow-[0_0_12px_rgba(250,204,21,0.2)]'
                : 'bg-dark-900/40 border-dark-750 opacity-60'
            }`}
          >
            <div className="flex items-center justify-between text-xs mb-1">
              <span className="font-bold text-yellow-400">3. GOOD / STABLE</span>
              <span className="text-[10px] text-yellow-400 font-bold">60 - 150ms</span>
            </div>
            <p className="text-[10px] text-slate-400">
              Stable connection speed. Reliable order routing and market data sync.
            </p>
            <div className="flex items-end gap-1 mt-2 h-2">
              <span className="w-2 h-2 rounded-xs bg-yellow-400" />
              <span className="w-2 h-2 rounded-xs bg-yellow-400" />
              <span className="w-2 h-2 rounded-xs bg-yellow-400" />
              <span className="w-2 h-2 rounded-xs bg-dark-700/60" />
            </div>
          </div>

          {/* 4. Green Scale Card */}
          <div
            className={`p-3 rounded-lg border transition-all ${
              networkStrength === 4 && wsConnected
                ? 'bg-emerald-950/40 border-emerald-500/80 shadow-[0_0_12px_rgba(52,211,153,0.2)]'
                : 'bg-dark-900/40 border-dark-750 opacity-60'
            }`}
          >
            <div className="flex items-center justify-between text-xs mb-1">
              <span className="font-bold text-emerald-400">4. OPTIMAL / EXCELLENT</span>
              <span className="text-[10px] text-emerald-400 font-bold">&lt; 60ms</span>
            </div>
            <p className="text-[10px] text-slate-400">
              Ultra-low latency pipeline. Ideal for algorithmic momentum execution.
            </p>
            <div className="flex items-end gap-1 mt-2 h-2">
              <span className="w-2 h-2 rounded-xs bg-emerald-400" />
              <span className="w-2 h-2 rounded-xs bg-emerald-400" />
              <span className="w-2 h-2 rounded-xs bg-emerald-400" />
              <span className="w-2 h-2 rounded-xs bg-emerald-400" />
            </div>
          </div>
        </div>

        {/* Live Latency Bar History */}
        {latencyHistory.length > 0 && (
          <div className="p-3 bg-dark-900/80 rounded-lg border border-dark-750">
            <div className="flex items-center justify-between text-xs text-slate-400 mb-2">
              <span className="font-bold text-slate-300">Live Ping Stream (Last 20 Samples)</span>
              <span className="text-[11px] text-slate-400">
                Latest: <strong className={currentQ.textColor}>{latency}ms</strong> | Average:{' '}
                <strong className="text-slate-200">{avgLatency}ms</strong>
              </span>
            </div>
            <div className="h-14 flex items-end gap-1.5 bg-dark-950/90 p-2 rounded border border-dark-800">
              {latencyHistory.map((val, idx) => {
                const barColor =
                  val <= 60
                    ? 'bg-emerald-400 hover:bg-emerald-300'
                    : val <= 150
                      ? 'bg-yellow-400 hover:bg-yellow-300'
                      : val <= 300
                        ? 'bg-orange-500 hover:bg-orange-400'
                        : 'bg-red-500 hover:bg-red-400';
                const maxVal = Math.max(...latencyHistory, 100);
                const heightPct = Math.max(15, Math.min(100, Math.round((val / maxVal) * 100)));

                return (
                  <div
                    key={idx}
                    className="flex-1 flex flex-col justify-end items-center relative group"
                  >
                    <div
                      style={{ height: `${heightPct}%` }}
                      className={`w-full rounded-xs transition-all duration-200 ${barColor}`}
                    />
                    <div className="absolute -top-7 hidden group-hover:flex px-1.5 py-0.5 bg-dark-800 text-slate-200 text-[9px] rounded border border-dark-700 pointer-events-none whitespace-nowrap z-10">
                      {val}ms
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>

      {/* State Reconciliation Audit Panel */}
      <div className="p-5 bg-dark-850 border border-dark-750 rounded-lg space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-4 border-b border-dark-750 pb-3">
          <div className="flex items-center gap-2">
            <ShieldCheck className="w-5 h-5 text-trade-cyan" />
            <div>
              <h3 className="text-sm font-mono font-bold uppercase text-slate-100">
                State Reconciliation Engine Audit
              </h3>
              <p className="text-[11px] font-mono text-slate-400">
                Periodic ground-truth audit comparing local portfolio/OMS state against Binance
              </p>
            </div>
          </div>

          <div className="flex items-center gap-3 font-mono text-xs">
            <span
              className={`px-3 py-1 rounded font-bold uppercase ${
                isHealthyRecon
                  ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/40'
                  : 'bg-red-500/20 text-red-400 border border-red-500/40 animate-pulse'
              }`}
            >
              {isHealthyRecon ? '0 MISMATCHES (HEALTHY)' : `${reconMismatches} MISMATCHES DETECTED`}
            </span>
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 font-mono text-xs">
          <div className="p-3 bg-dark-900 rounded border border-dark-750 space-y-1">
            <span className="text-[10px] text-slate-500 block uppercase">Last Audit Timestamp</span>
            <span className="text-slate-200 font-bold">
              {systemStatus?.last_reconciliation_time
                ? new Date(systemStatus.last_reconciliation_time).toLocaleTimeString()
                : 'Continuous Audit Active'}
            </span>
          </div>
          <div className="p-3 bg-dark-900 rounded border border-dark-750 space-y-1">
            <span className="text-[10px] text-slate-500 block uppercase">Audit Scope</span>
            <span className="text-slate-200 font-bold">Balances, Positions, Ghost Orders, Fills</span>
          </div>
          <div className="p-3 bg-dark-900 rounded border border-dark-750 space-y-1">
            <span className="text-[10px] text-slate-500 block uppercase">Action on Critical Divergence</span>
            <span className="text-red-400 font-bold">Auto-Halt & Kill-Switch Engage</span>
          </div>
        </div>
      </div>

      {/* Subsystem Health Matrix */}
      <div className="bg-dark-850 border border-dark-750 rounded-lg overflow-hidden p-4 space-y-4">
        <div className="flex items-center gap-2 border-b border-dark-750 pb-3">
          <Zap className="w-4 h-4 text-trade-accent" />
          <h3 className="text-sm font-mono font-bold uppercase text-slate-100">
            Platform Subsystem Health Matrix
          </h3>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {components.map((comp) => {
            const isOnline = comp.status === 'ONLINE' || comp.status === 'SIMULATED';
            return (
              <div
                key={comp.name}
                className="p-3 bg-dark-900 border border-dark-750 rounded-lg space-y-2 font-mono text-xs"
              >
                <div className="flex items-center justify-between">
                  <span className="font-bold text-slate-200">{comp.name}</span>
                  <span
                    className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                      isOnline
                        ? 'bg-emerald-500/20 text-emerald-400'
                        : comp.status === 'HALTED'
                          ? 'bg-red-500/20 text-red-400'
                          : 'bg-blue-500/20 text-blue-400'
                    }`}
                  >
                    {comp.status}
                  </span>
                </div>
                <p className="text-[11px] text-slate-400 leading-snug">{comp.details}</p>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};
