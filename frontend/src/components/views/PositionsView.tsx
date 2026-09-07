'use client';

import React from 'react';
import { AlertCircle, ArrowDown, ArrowUp, Layers, ShieldAlert } from 'lucide-react';
import { Position } from '../../types/trading';

interface PositionsViewProps {
  positions: Position[];
}

export const PositionsView: React.FC<PositionsViewProps> = ({ positions }) => {
  const totalNotional = positions.reduce((acc, p) => acc + p.notional, 0);
  const totalMargin = positions.reduce((acc, p) => acc + p.initial_margin, 0);
  const totalUpnl = positions.reduce((acc, p) => acc + p.unrealized_pnl, 0);

  return (
    <div className="space-y-6">
      {/* Positions Header & Summary Banner */}
      <div className="flex flex-wrap items-center justify-between gap-4 p-4 bg-dark-850 border border-dark-750 rounded-lg">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded bg-dark-800 border border-dark-700 flex items-center justify-center text-trade-cyan">
            <Layers className="w-5 h-5" />
          </div>
          <div>
            <h2 className="text-sm font-mono font-bold uppercase text-slate-100">
              Active Isolated Positions ({positions.length})
            </h2>
            <p className="text-[11px] font-mono text-slate-400">
              Binance USDT-M Futures // Margin Mode: ISOLATED
            </p>
          </div>
        </div>

        <div className="flex items-center gap-6 font-mono text-xs">
          <div>
            <span className="text-[10px] text-slate-500 block uppercase">Total Notional</span>
            <span className="text-slate-100 font-bold tabular-numbers">
              ${totalNotional.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </span>
          </div>
          <div>
            <span className="text-[10px] text-slate-500 block uppercase">Margin Locked</span>
            <span className="text-slate-100 font-bold tabular-numbers">
              ${totalMargin.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </span>
          </div>
          <div>
            <span className="text-[10px] text-slate-500 block uppercase">Net uPnL</span>
            <span
              className={`font-black tabular-numbers ${
                totalUpnl >= 0 ? 'text-trade-long' : 'text-trade-short'
              }`}
            >
              {totalUpnl >= 0 ? '+' : ''}${totalUpnl.toFixed(2)}
            </span>
          </div>
        </div>
      </div>

      {/* Main Positions Table */}
      {positions.length === 0 ? (
        <div className="p-16 text-center bg-dark-850 border border-dark-750 rounded-lg space-y-2">
          <ShieldAlert className="w-8 h-8 text-slate-600 mx-auto" />
          <h3 className="text-sm font-mono font-bold text-slate-300">No Open Positions</h3>
          <p className="text-xs font-mono text-slate-500 max-w-sm mx-auto">
            The trading platform is currently 100% in cash (flat). Strategy signals will populate
            this view upon order execution.
          </p>
        </div>
      ) : (
        <div className="bg-dark-850 border border-dark-750 rounded-lg overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-xs font-mono text-left">
              <thead className="text-slate-400 border-b border-dark-750 bg-dark-900 uppercase text-[10px] tracking-wider">
                <tr>
                  <th className="py-3 px-4">Symbol / Mode</th>
                  <th className="py-3 px-4">Side</th>
                  <th className="py-3 px-4">Size</th>
                  <th className="py-3 px-4">Entry Price</th>
                  <th className="py-3 px-4">Mark Price</th>
                  <th className="py-3 px-4">Notional Value</th>
                  <th className="py-3 px-4">Margin (Initial/Maint)</th>
                  <th className="py-3 px-4">Unrealized PnL</th>
                  <th className="py-3 px-4">Est. Liquidation Price</th>
                  <th className="py-3 px-4">Liquidation Buffer</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-dark-750/70">
                {positions.map((pos) => {
                  const isLong = pos.side === 'LONG';
                  const isProfit = pos.unrealized_pnl >= 0;
                  const liqBuffer = pos.liquidation_distance_pct ?? 100;
                  const bufferColor =
                    liqBuffer >= 20
                      ? 'bg-emerald-500 text-emerald-400'
                      : liqBuffer >= 15
                        ? 'bg-amber-500 text-amber-400'
                        : 'bg-red-500 text-red-400';

                  return (
                    <tr key={pos.symbol} className="hover:bg-dark-800/50 transition">
                      <td className="py-3.5 px-4">
                        <div className="font-bold text-slate-100 text-sm">{pos.symbol}</div>
                        <span className="text-[10px] text-slate-500 uppercase">
                          {pos.margin_mode} // {pos.leverage}x
                        </span>
                      </td>
                      <td className="py-3.5 px-4">
                        <span
                          className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-bold ${
                            isLong
                              ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/40'
                              : 'bg-red-500/20 text-red-400 border border-red-500/40'
                          }`}
                        >
                          {isLong ? (
                            <ArrowUp className="w-3 h-3" />
                          ) : (
                            <ArrowDown className="w-3 h-3" />
                          )}
                          {pos.side}
                        </span>
                      </td>
                      <td className="py-3.5 px-4 tabular-numbers font-semibold text-slate-200">
                        {pos.quantity}
                      </td>
                      <td className="py-3.5 px-4 tabular-numbers text-slate-300">
                        ${pos.entry_price.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                      </td>
                      <td className="py-3.5 px-4 tabular-numbers font-bold text-slate-100">
                        ${pos.mark_price.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                      </td>
                      <td className="py-3.5 px-4 tabular-numbers text-slate-200">
                        ${pos.notional.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                      </td>
                      <td className="py-3.5 px-4 tabular-numbers text-slate-400">
                        ${pos.initial_margin.toFixed(2)} / ${pos.maintenance_margin.toFixed(2)}
                      </td>
                      <td className="py-3.5 px-4 tabular-numbers">
                        <div
                          className={`font-black ${
                            isProfit ? 'text-trade-long' : 'text-trade-short'
                          }`}
                        >
                          {isProfit ? '+' : ''}${pos.unrealized_pnl.toFixed(2)}
                        </div>
                        <div
                          className={`text-[10px] ${
                            isProfit ? 'text-trade-long/80' : 'text-trade-short/80'
                          }`}
                        >
                          {isProfit ? '+' : ''}
                          {pos.unrealized_pnl_pct.toFixed(2)}%
                        </div>
                      </td>
                      <td className="py-3.5 px-4 tabular-numbers text-slate-300">
                        {pos.liquidation_price
                          ? `$${pos.liquidation_price.toLocaleString(undefined, { minimumFractionDigits: 2 })}`
                          : 'None (1x)'}
                      </td>
                      <td className="py-3.5 px-4">
                        <div className="space-y-1">
                          <div className="flex items-center justify-between text-[10px]">
                            <span className="text-slate-400">Distance:</span>
                            <span className="font-bold tabular-numbers text-slate-200">
                              {pos.liquidation_distance_pct !== null
                                ? `${pos.liquidation_distance_pct.toFixed(1)}%`
                                : '>100%'}
                            </span>
                          </div>
                          <div className="w-24 bg-dark-750 rounded-full h-1.5 overflow-hidden">
                            <div
                              className={`h-1.5 rounded-full ${bufferColor.split(' ')[0]}`}
                              style={{
                                width: `${Math.min(100, Math.max(0, pos.liquidation_distance_pct ?? 100))}%`,
                              }}
                            />
                          </div>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
};
