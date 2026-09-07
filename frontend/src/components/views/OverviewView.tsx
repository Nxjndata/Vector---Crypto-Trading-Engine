'use client';

import React from 'react';
import {
  AlertTriangle,
  ArrowDownRight,
  ArrowUpRight,
  CheckCircle2,
  DollarSign,
  Layers,
  Percent,
  Shield,
  TrendingDown,
  TrendingUp,
  Wallet,
} from 'lucide-react';
import {
  MarketTicker,
  Portfolio,
  Position,
  RiskStatus,
  Strategy,
  SystemStatus,
} from '../../types/trading';

interface OverviewViewProps {
  portfolio: Portfolio | null;
  positions: Position[];
  riskStatus: RiskStatus | null;
  systemStatus: SystemStatus | null;
  strategy: Strategy | null;
  markets: MarketTicker[];
  onNavigateToTab: (tab: any) => void;
}

export const OverviewView: React.FC<OverviewViewProps> = ({
  portfolio,
  positions,
  riskStatus,
  systemStatus,
  strategy,
  markets,
  onNavigateToTab,
}) => {
  const equity = portfolio?.equity ?? 10000;
  const wallet = portfolio?.wallet_balance ?? 10000;
  const available = portfolio?.available_balance ?? 10000;
  const uPnL = portfolio?.unrealized_pnl ?? 0;
  const rPnL = portfolio?.realized_pnl ?? 0;
  const fPnL = portfolio?.funding_pnl ?? 0;
  const totalPnL = portfolio?.total_pnl ?? uPnL + rPnL + fPnL;
  const drawdown = portfolio?.drawdown_pct ?? 0;
  const leverage = portfolio?.effective_leverage ?? 0;
  const totalExposure = portfolio?.total_exposure ?? 0;

  return (
    <div className="space-y-6">
      {/* Top Hero Ticker Ribbon */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {markets.slice(0, 4).map((m) => {
          const isUp = m.price_change_percent_24h >= 0;
          return (
            <div
              key={m.symbol}
              className="p-3 bg-dark-850 border border-dark-750 rounded-lg flex items-center justify-between"
            >
              <div>
                <span className="text-xs font-mono font-bold text-slate-200">{m.symbol}</span>
                <div className="text-sm font-mono font-black text-slate-100 tabular-numbers">
                  ${m.mark_price.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                </div>
              </div>
              <div
                className={`flex items-center text-xs font-mono font-semibold ${
                  isUp ? 'text-trade-long' : 'text-trade-short'
                }`}
              >
                {isUp ? (
                  <ArrowUpRight className="w-3.5 h-3.5 mr-0.5" />
                ) : (
                  <ArrowDownRight className="w-3.5 h-3.5 mr-0.5" />
                )}
                {isUp ? '+' : ''}
                {m.price_change_percent_24h.toFixed(2)}%
              </div>
            </div>
          );
        })}
      </div>

      {/* Main KPI Portfolio Metric Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {/* Equity Card */}
        <div className="p-4 bg-dark-850 border border-dark-750 rounded-lg space-y-1 relative overflow-hidden">
          <div className="flex items-center justify-between text-slate-400 text-xs font-mono uppercase">
            <span>Portfolio Equity</span>
            <DollarSign className="w-4 h-4 text-trade-cyan" />
          </div>
          <div className="text-2xl font-mono font-black text-slate-100 tabular-numbers">
            ${equity.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </div>
          <div className="flex items-center justify-between text-[11px] font-mono pt-2 border-t border-dark-750 text-slate-400">
            <span>Peak: ${portfolio?.peak_equity?.toLocaleString() ?? '10,000.00'}</span>
            <span
              className={`font-semibold ${drawdown > 5 ? 'text-trade-warning' : 'text-slate-400'}`}
            >
              Drawdown: {drawdown.toFixed(2)}%
            </span>
          </div>
        </div>

        {/* Available Wallet Balance */}
        <div className="p-4 bg-dark-850 border border-dark-750 rounded-lg space-y-1 relative overflow-hidden">
          <div className="flex items-center justify-between text-slate-400 text-xs font-mono uppercase">
            <span>Available Balance</span>
            <Wallet className="w-4 h-4 text-trade-accent" />
          </div>
          <div className="text-2xl font-mono font-black text-slate-100 tabular-numbers">
            ${available.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </div>
          <div className="flex items-center justify-between text-[11px] font-mono pt-2 border-t border-dark-750 text-slate-400">
            <span>Wallet: ${wallet.toLocaleString(undefined, { minimumFractionDigits: 2 })}</span>
            <span>Margin: ${portfolio?.margin_used?.toFixed(2) ?? '0.00'}</span>
          </div>
        </div>

        {/* PnL Decomposition */}
        <div className="p-4 bg-dark-850 border border-dark-750 rounded-lg space-y-1 relative overflow-hidden">
          <div className="flex items-center justify-between text-slate-400 text-xs font-mono uppercase">
            <span>Total Account PnL</span>
            {totalPnL >= 0 ? (
              <TrendingUp className="w-4 h-4 text-trade-long" />
            ) : (
              <TrendingDown className="w-4 h-4 text-trade-short" />
            )}
          </div>
          <div
            className={`text-2xl font-mono font-black tabular-numbers ${
              totalPnL >= 0 ? 'text-trade-long' : 'text-trade-short'
            }`}
          >
            {totalPnL >= 0 ? '+' : ''}${totalPnL.toFixed(2)}
          </div>
          <div className="flex items-center justify-between text-[11px] font-mono pt-2 border-t border-dark-750 text-slate-400">
            <span className={uPnL >= 0 ? 'text-trade-long' : 'text-trade-short'}>
              uPnL: {uPnL >= 0 ? '+' : ''}${uPnL.toFixed(2)}
            </span>
            <span className={rPnL >= 0 ? 'text-trade-long' : 'text-trade-short'}>
              rPnL: {rPnL >= 0 ? '+' : ''}${rPnL.toFixed(2)}
            </span>
          </div>
        </div>

        {/* Exposure & Leverage */}
        <div className="p-4 bg-dark-850 border border-dark-750 rounded-lg space-y-1 relative overflow-hidden">
          <div className="flex items-center justify-between text-slate-400 text-xs font-mono uppercase">
            <span>Effective Leverage</span>
            <Percent className="w-4 h-4 text-amber-400" />
          </div>
          <div className="text-2xl font-mono font-black text-slate-100 tabular-numbers">
            {leverage.toFixed(2)}x
          </div>
          <div className="flex items-center justify-between text-[11px] font-mono pt-2 border-t border-dark-750 text-slate-400">
            <span>Exposure: ${totalExposure.toLocaleString(undefined, { minimumFractionDigits: 2 })}</span>
            <span>Open Pos: {positions.length}</span>
          </div>
        </div>
      </div>

      {/* Grid: Active Positions & Risk / Strategy Overview */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Active Positions Summary (2 Cols) */}
        <div className="lg:col-span-2 bg-dark-850 border border-dark-750 rounded-lg p-4 space-y-4">
          <div className="flex items-center justify-between border-b border-dark-750 pb-3">
            <div className="flex items-center gap-2">
              <Layers className="w-4 h-4 text-trade-cyan" />
              <h2 className="text-sm font-mono font-bold uppercase text-slate-200">
                Active Positions ({positions.length})
              </h2>
            </div>
            <button
              onClick={() => onNavigateToTab('positions')}
              className="text-xs font-mono text-trade-accent hover:underline"
            >
              View Full Table &rarr;
            </button>
          </div>

          {positions.length === 0 ? (
            <div className="py-12 text-center text-slate-500 font-mono text-xs">
              No open positions. Strategy is currently in flat state.
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs font-mono text-left">
                <thead className="text-slate-400 border-b border-dark-750 bg-dark-900/60 uppercase text-[10px]">
                  <tr>
                    <th className="py-2 px-3">Symbol</th>
                    <th className="py-2 px-3">Side</th>
                    <th className="py-2 px-3">Size</th>
                    <th className="py-2 px-3">Entry</th>
                    <th className="py-2 px-3">Mark</th>
                    <th className="py-2 px-3 text-right">uPnL ($)</th>
                    <th className="py-2 px-3 text-right">Liq. Dist.</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-dark-750/60">
                  {positions.map((pos) => {
                    const isLong = pos.side === 'LONG';
                    const isProfit = pos.unrealized_pnl >= 0;
                    return (
                      <tr key={pos.symbol} className="hover:bg-dark-800/40">
                        <td className="py-2.5 px-3 font-bold text-slate-200">{pos.symbol}</td>
                        <td className="py-2.5 px-3">
                          <span
                            className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                              isLong
                                ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30'
                                : 'bg-red-500/20 text-red-400 border border-red-500/30'
                            }`}
                          >
                            {pos.side}
                          </span>
                        </td>
                        <td className="py-2.5 px-3 tabular-numbers text-slate-300">
                          {pos.quantity}
                        </td>
                        <td className="py-2.5 px-3 tabular-numbers text-slate-300">
                          ${pos.entry_price.toFixed(2)}
                        </td>
                        <td className="py-2.5 px-3 tabular-numbers text-slate-100 font-semibold">
                          ${pos.mark_price.toFixed(2)}
                        </td>
                        <td
                          className={`py-2.5 px-3 tabular-numbers font-bold text-right ${
                            isProfit ? 'text-trade-long' : 'text-trade-short'
                          }`}
                        >
                          {isProfit ? '+' : ''}${pos.unrealized_pnl.toFixed(2)} (
                          {pos.unrealized_pnl_pct.toFixed(2)}%)
                        </td>
                        <td className="py-2.5 px-3 tabular-numbers text-right text-slate-400">
                          {pos.liquidation_distance_pct !== null
                            ? `${pos.liquidation_distance_pct.toFixed(1)}%`
                            : 'N/A'}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Strategy & Risk Summary Card (1 Col) */}
        <div className="space-y-4">
          {/* Active Strategy Card */}
          <div className="bg-dark-850 border border-dark-750 rounded-lg p-4 space-y-3">
            <div className="flex items-center justify-between border-b border-dark-750 pb-2">
              <span className="text-xs font-mono font-bold uppercase text-slate-300">
                Strategy Profile
              </span>
              <span className="text-[10px] font-mono px-2 py-0.5 bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 rounded">
                ACTIVE
              </span>
            </div>
            <div className="text-sm font-mono font-bold text-slate-100">
              {strategy?.strategy_name || 'SMA Momentum Trend Following v1'}
            </div>
            <div className="grid grid-cols-2 gap-2 text-xs font-mono pt-1">
              <div className="p-2 bg-dark-900 rounded border border-dark-750">
                <span className="text-slate-500 text-[10px]">CURRENT SIGNAL</span>
                <div className="font-bold text-trade-cyan">
                  {strategy?.current_signal || 'HOLD'}
                </div>
              </div>
              <div className="p-2 bg-dark-900 rounded border border-dark-750">
                <span className="text-slate-500 text-[10px]">WIN RATE</span>
                <div className="font-bold text-slate-200">
                  {strategy?.win_rate_pct?.toFixed(1) ?? '66.7'}%
                </div>
              </div>
            </div>
          </div>

          {/* Quick Risk Limit Utilization */}
          <div className="bg-dark-850 border border-dark-750 rounded-lg p-4 space-y-3">
            <div className="flex items-center justify-between border-b border-dark-750 pb-2">
              <div className="flex items-center gap-1.5">
                <Shield className="w-3.5 h-3.5 text-slate-400" />
                <span className="text-xs font-mono font-bold uppercase text-slate-300">
                  Risk Gatekeeper
                </span>
              </div>
              <span
                className={`text-[10px] font-mono px-2 py-0.5 rounded font-bold ${
                  riskStatus?.kill_switch_active
                    ? 'bg-red-500/20 text-red-400 border border-red-500/40'
                    : 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30'
                }`}
              >
                {riskStatus?.kill_switch_active ? 'HALTED' : 'STANDBY'}
              </span>
            </div>

            <div className="space-y-2 text-xs font-mono">
              <div>
                <div className="flex justify-between text-[11px] text-slate-400 mb-1">
                  <span>Portfolio Exposure</span>
                  <span>
                    ${riskStatus?.max_portfolio_exposure_usd?.current ?? 0} / $
                    {riskStatus?.max_portfolio_exposure_usd?.limit ?? 5000}
                  </span>
                </div>
                <div className="w-full bg-dark-750 rounded-full h-1.5 overflow-hidden">
                  <div
                    className="bg-trade-accent h-1.5 rounded-full transition-all"
                    style={{
                      width: `${riskStatus?.max_portfolio_exposure_usd?.utilization_pct ?? 0}%`,
                    }}
                  />
                </div>
              </div>

              <div>
                <div className="flex justify-between text-[11px] text-slate-400 mb-1">
                  <span>Leverage Cap</span>
                  <span>
                    {riskStatus?.max_leverage?.current ?? 0}x / {riskStatus?.max_leverage?.limit ?? 5}x
                  </span>
                </div>
                <div className="w-full bg-dark-750 rounded-full h-1.5 overflow-hidden">
                  <div
                    className="bg-amber-400 h-1.5 rounded-full transition-all"
                    style={{ width: `${riskStatus?.max_leverage?.utilization_pct ?? 0}%` }}
                  />
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
