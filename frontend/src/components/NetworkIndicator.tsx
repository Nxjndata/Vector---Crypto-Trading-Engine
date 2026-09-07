'use client';

import React, { useState } from 'react';
import { Activity, CheckCircle2, ChevronDown, Radio, Wifi, WifiOff } from 'lucide-react';

export type NetworkStrengthLevel = 0 | 1 | 2 | 3 | 4;

export interface NetworkQuality {
  level: NetworkStrengthLevel;
  label: string;
  color: 'red' | 'orange' | 'yellow' | 'green';
  description: string;
}

export interface NetworkIndicatorProps {
  strength: NetworkStrengthLevel;
  latency: number | null;
  avgLatency?: number | null;
  jitter?: number | null;
  isConnected: boolean;
  messageCount?: number;
  lastMessageTime?: Date | null;
  latencyHistory?: number[];
  compact?: boolean;
}

export const NetworkIndicator: React.FC<NetworkIndicatorProps> = ({
  strength,
  latency,
  avgLatency,
  jitter,
  isConnected,
  messageCount = 0,
  lastMessageTime,
  latencyHistory = [],
  compact = false,
}) => {
  const [isOpen, setIsOpen] = useState(false);

  // Configuration for 4-point scale colors and styling
  const colorMap = {
    green: {
      bar: 'bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.6)]',
      text: 'text-emerald-400',
      badge: 'bg-emerald-500/20 text-emerald-300 border-emerald-500/40',
      dot: 'bg-emerald-400',
      label: 'EXCELLENT',
      scale: '4/4',
    },
    yellow: {
      bar: 'bg-yellow-400 shadow-[0_0_8px_rgba(250,204,21,0.6)]',
      text: 'text-yellow-400',
      badge: 'bg-yellow-500/20 text-yellow-300 border-yellow-500/40',
      dot: 'bg-yellow-400',
      label: 'GOOD',
      scale: '3/4',
    },
    orange: {
      bar: 'bg-orange-500 shadow-[0_0_8px_rgba(249,115,22,0.6)]',
      text: 'text-orange-400',
      badge: 'bg-orange-500/20 text-orange-300 border-orange-500/40',
      dot: 'bg-orange-400',
      label: 'FAIR',
      scale: '2/4',
    },
    red: {
      bar: 'bg-red-500 shadow-[0_0_8px_rgba(239,68,68,0.6)]',
      text: 'text-red-400',
      badge: 'bg-red-500/20 text-red-300 border-red-500/40',
      dot: 'bg-red-500',
      label: isConnected ? 'POOR' : 'OFFLINE',
      scale: isConnected ? '1/4' : '0/4',
    },
  };

  const currentQuality: {
    color: 'green' | 'yellow' | 'orange' | 'red';
    label: string;
    scale: string;
  } = !isConnected || strength === 0
    ? { color: 'red', label: 'OFFLINE', scale: '0/4' }
    : strength === 4
      ? { color: 'green', label: 'EXCELLENT', scale: '4/4' }
      : strength === 3
        ? { color: 'yellow', label: 'GOOD', scale: '3/4' }
        : strength === 2
          ? { color: 'orange', label: 'FAIR', scale: '2/4' }
          : { color: 'red', label: 'POOR', scale: '1/4' };

  const currentStyles = colorMap[currentQuality.color];

  // Heights for 4 graduated signal bars
  const barHeights = ['h-1.5', 'h-2.5', 'h-3.5', 'h-4.5'];

  return (
    <div className="relative inline-block text-left">
      {/* Trigger Button / Badge */}
      <button
        onClick={() => setIsOpen(!isOpen)}
        onMouseEnter={() => setIsOpen(true)}
        onMouseLeave={() => setIsOpen(false)}
        aria-expanded={isOpen}
        aria-label={`Network status: ${currentQuality.label} (${currentQuality.scale}), Latency: ${latency ?? 0}ms`}
        className={`flex items-center gap-2 px-2.5 py-1 rounded bg-dark-850 hover:bg-dark-800 border border-dark-750 transition-colors font-mono text-xs cursor-pointer group select-none ${
          !isConnected ? 'border-red-900/50 bg-red-950/20' : ''
        }`}
      >
        {/* 4-Bar Signal Indicator */}
        <div className="flex items-end gap-[2px] h-4.5 py-0.5 px-0.5" title={`Signal: ${currentQuality.scale}`}>
          {[1, 2, 3, 4].map((barLevel, idx) => {
            const isActive = isConnected && strength >= barLevel;
            const isDisconnected = !isConnected && barLevel === 1;

            return (
              <span
                key={barLevel}
                className={`w-1 rounded-xs transition-all duration-300 ${barHeights[idx]} ${
                  isActive
                    ? currentStyles.bar
                    : isDisconnected
                      ? 'bg-red-500/80 animate-pulse'
                      : 'bg-dark-700/60'
                }`}
              />
            );
          })}
        </div>

        {/* Latency Number & Quality Label */}
        <div className="flex items-center gap-1.5">
          <span
            className={`text-[11px] font-bold tabular-numbers ${
              isConnected ? currentStyles.text : 'text-red-400'
            }`}
          >
            {isConnected ? (latency !== null ? `${latency}ms` : '<1ms') : 'OFFLINE'}
          </span>

          {!compact && (
            <span
              className={`text-[9px] font-black px-1.5 py-0.2 rounded border uppercase tracking-wider ${currentStyles.badge}`}
            >
              {currentQuality.scale} {currentQuality.label}
            </span>
          )}
        </div>

        <ChevronDown className="w-3 h-3 text-slate-500 group-hover:text-slate-300 transition-transform" />
      </button>

      {/* Rich Telemetry Popover Tooltip */}
      {isOpen && (
        <div
          onMouseEnter={() => setIsOpen(true)}
          onMouseLeave={() => setIsOpen(false)}
          className="absolute right-0 mt-2 w-72 p-4 bg-dark-900/95 backdrop-blur-md border border-dark-700 rounded-lg shadow-2xl z-50 text-xs font-mono text-slate-200 animate-in fade-in zoom-in-95 duration-150"
        >
          {/* Popover Header */}
          <div className="flex items-center justify-between pb-3 border-b border-dark-800">
            <div className="flex items-center gap-2">
              <div className={`w-2.5 h-2.5 rounded-full ${currentStyles.dot} ${isConnected ? 'animate-pulse' : ''}`} />
              <span className="font-bold tracking-wide uppercase text-slate-100">
                Network Telemetry
              </span>
            </div>
            <span className={`text-[10px] font-bold px-2 py-0.5 rounded border uppercase ${currentStyles.badge}`}>
              {currentQuality.label} ({currentQuality.scale})
            </span>
          </div>

          {/* 4-Point Scale Legend Bar */}
          <div className="py-3 space-y-1.5 border-b border-dark-800">
            <div className="text-[10px] text-slate-400 uppercase tracking-wider flex justify-between">
              <span>Network Scale (4-Point)</span>
              <span className="text-slate-300">Live RTT</span>
            </div>

            <div className="grid grid-cols-4 gap-1 text-[9px] text-center font-bold">
              {/* Level 1: Red */}
              <div
                className={`py-1 rounded border ${
                  strength === 1 || (!isConnected && strength === 0)
                    ? 'bg-red-500/20 border-red-500 text-red-300'
                    : 'bg-dark-800/40 border-dark-750 text-slate-500'
                }`}
              >
                <div className="text-red-400">1. POOR</div>
                <div className="text-[8px] text-slate-400">&gt;300ms</div>
              </div>

              {/* Level 2: Orange */}
              <div
                className={`py-1 rounded border ${
                  strength === 2
                    ? 'bg-orange-500/20 border-orange-500 text-orange-300'
                    : 'bg-dark-800/40 border-dark-750 text-slate-500'
                }`}
              >
                <div className="text-orange-400">2. FAIR</div>
                <div className="text-[8px] text-slate-400">150-300ms</div>
              </div>

              {/* Level 3: Yellow */}
              <div
                className={`py-1 rounded border ${
                  strength === 3
                    ? 'bg-yellow-500/20 border-yellow-500 text-yellow-300'
                    : 'bg-dark-800/40 border-dark-750 text-slate-500'
                }`}
              >
                <div className="text-yellow-400">3. GOOD</div>
                <div className="text-[8px] text-slate-400">60-150ms</div>
              </div>

              {/* Level 4: Green */}
              <div
                className={`py-1 rounded border ${
                  strength === 4
                    ? 'bg-emerald-500/20 border-emerald-500 text-emerald-300'
                    : 'bg-dark-800/40 border-dark-750 text-slate-500'
                }`}
              >
                <div className="text-emerald-400">4. OPTIMAL</div>
                <div className="text-[8px] text-slate-400">&lt;60ms</div>
              </div>
            </div>
          </div>

          {/* Live Metrics Grid */}
          <div className="py-3 space-y-2 border-b border-dark-800">
            <div className="flex items-center justify-between">
              <span className="text-slate-400">Round-Trip Time (RTT):</span>
              <span className={`font-bold tabular-numbers ${currentStyles.text}`}>
                {latency !== null ? `${latency} ms` : '--'}
              </span>
            </div>

            {avgLatency !== undefined && avgLatency !== null && (
              <div className="flex items-center justify-between">
                <span className="text-slate-400">Average RTT (20 samples):</span>
                <span className="text-slate-200 tabular-numbers">{avgLatency} ms</span>
              </div>
            )}

            {jitter !== undefined && jitter !== null && (
              <div className="flex items-center justify-between">
                <span className="text-slate-400">Network Jitter:</span>
                <span className="text-slate-200 tabular-numbers">±{jitter} ms</span>
              </div>
            )}

            <div className="flex items-center justify-between">
              <span className="text-slate-400">WebSocket State:</span>
              <span className={`font-semibold ${isConnected ? 'text-emerald-400' : 'text-red-400'}`}>
                {isConnected ? 'OPEN & STREAMING' : 'DISCONNECTED'}
              </span>
            </div>

            <div className="flex items-center justify-between">
              <span className="text-slate-400">Total Frames Processed:</span>
              <span className="text-slate-200 tabular-numbers">{messageCount.toLocaleString()}</span>
            </div>

            {lastMessageTime && (
              <div className="flex items-center justify-between">
                <span className="text-slate-400">Last Frame Received:</span>
                <span className="text-slate-300 text-[10px]">
                  {new Date(lastMessageTime).toLocaleTimeString()}
                </span>
              </div>
            )}
          </div>

          {/* Sparkline / Ping History Bar */}
          {latencyHistory.length > 0 && (
            <div className="pt-2">
              <div className="flex items-center justify-between text-[10px] text-slate-400 mb-1.5">
                <span>Recent Ping Stream</span>
                <span className="text-[9px] text-slate-500">Last {latencyHistory.length} pings</span>
              </div>
              <div className="h-8 flex items-end gap-1 bg-dark-950/80 p-1 rounded border border-dark-800">
                {latencyHistory.map((val, idx) => {
                  const barColor =
                    val <= 60
                      ? 'bg-emerald-400'
                      : val <= 150
                        ? 'bg-yellow-400'
                        : val <= 300
                          ? 'bg-orange-500'
                          : 'bg-red-500';
                  // Scale bar height between 15% and 100%
                  const maxVal = Math.max(...latencyHistory, 100);
                  const heightPct = Math.max(15, Math.min(100, Math.round((val / maxVal) * 100)));

                  return (
                    <div
                      key={idx}
                      className="flex-1 flex flex-col justify-end items-center group/bar relative"
                      title={`${val}ms`}
                    >
                      <div
                        style={{ height: `${heightPct}%` }}
                        className={`w-full rounded-xs transition-all ${barColor}`}
                      />
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Bottom Execution Speed Note */}
          <div className="mt-3 pt-2 border-t border-dark-800/80 text-[10px] text-slate-400 flex items-center gap-1.5">
            <Radio className="w-3 h-3 text-trade-cyan shrink-0" />
            <span>
              {strength === 4
                ? 'Optimal sub-60ms pipeline for rapid order dispatch'
                : strength === 3
                  ? 'Stable latency within normal algorithmic tolerance'
                  : strength === 2
                    ? 'Elevated latency detected; slippage guards active'
                    : isConnected
                      ? 'High latency warning; review connection speed'
                      : 'Connection lost; attempting background reconnection'}
            </span>
          </div>
        </div>
      )}
    </div>
  );
};
