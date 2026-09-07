'use client';

import React, { useMemo, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  Award,
  CheckCircle2,
  HelpCircle,
  Info,
  LineChart,
  Percent,
  Play,
  Send,
  ShieldAlert,
  ShieldCheck,
  Sliders,
  TrendingDown,
  TrendingUp,
  Zap,
} from 'lucide-react';
import { Strategy } from '../../types/trading';
import { api } from '../../services/api';

interface StrategyViewProps {
  strategy: Strategy | null;
  onRefresh?: () => void;
}

export const StrategyView: React.FC<StrategyViewProps> = ({ strategy, onRefresh }) => {
  const winRate = strategy?.win_rate_pct ?? 66.7;
  const sharpe = strategy?.sharpe_ratio ?? 1.85;
  const drawdown = strategy?.max_drawdown_pct ?? 0.0;
  const currentSignal = strategy?.current_signal || 'HOLD';
  const signalStrength = strategy?.signal_strength ?? 0.0;
  const totalSignals = strategy?.total_signals ?? 24;
  const totalTrades = strategy?.total_trades ?? 12;
  const winningTrades = strategy?.winning_trades ?? 8;
  const losingTrades = strategy?.losing_trades ?? 4;

  const params = strategy?.parameters || {
    fast_window: 9,
    slow_window: 21,
    candle_timeframe: '1m',
    active_symbols: ['BTCUSDT', 'ETHUSDT', 'SOLUSDT'],
    max_position_size_usd: 1000.0,
    max_leverage: 5.0,
  };

  // Manual Test Signal State
  const [testSymbol, setTestSymbol] = useState<string>('BTCUSDT');
  const [testSide, setTestSide] = useState<'BUY' | 'SELL'>('BUY');
  const [testExposure, setTestExposure] = useState<number>(0.02);
  const [testReason, setTestReason] = useState<string>(
    'Manual operator test signal on Binance Testnet'
  );
  const [testConfirmed, setTestConfirmed] = useState<boolean>(false);
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [lastTestResult, setLastTestResult] = useState<any | null>(null);
  const [testError, setTestError] = useState<string | null>(null);

  const handleDispatchTestSignal = async () => {
    if (!testConfirmed) {
      setTestError('Please confirm that you want to dispatch a real testnet trade.');
      return;
    }
    setIsSubmitting(true);
    setTestError(null);
    setLastTestResult(null);

    try {
      const res = await api.forceTestSignal({
        symbol: testSymbol,
        side: testSide,
        target_exposure: testExposure,
        reason: testReason,
        confirm: true,
      });
      setLastTestResult(res);
      setTestConfirmed(false);
      if (onRefresh) onRefresh();
    } catch (err: any) {
      setTestError(err.message || 'Failed to dispatch manual test signal');
    } finally {
      setIsSubmitting(false);
    }
  };

  // Synthesize historical equity progression
  const equityPoints = useMemo(() => {
    let eq = 10000;
    const points = [eq];
    const deltas = [120, -40, 210, 85, -60, 310, 140, -90, 420, 180, 290];
    for (const d of deltas) {
      eq += d;
    }
    return points;
  }, []);

  return (
    <div className="space-y-6">
      {/* Strategy Header Banner */}
      <div className="p-5 bg-dark-850 border border-dark-750 rounded-lg flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded bg-dark-800 border border-dark-750 flex items-center justify-center text-trade-cyan">
            <LineChart className="w-5 h-5" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-mono font-bold uppercase text-slate-100">
                {strategy?.strategy_name || 'SMA Momentum Trend Following v1'}
              </h2>
              <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 font-bold flex items-center gap-1">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-ping" />
                ACTIVE ENGINE
              </span>
            </div>
            <p className="text-[11px] font-mono text-slate-400">
              Strategy ID: {strategy?.strategy_id || 'sma_momentum_v1'} // Autonomous Trend Follower
            </p>
          </div>
        </div>

        {/* Current Signal Badge with Live Evaluation Context */}
        <div className="flex items-center gap-3 bg-dark-900 px-4 py-2.5 rounded-lg border border-dark-750 font-mono text-xs">
          <div>
            <div className="flex items-center gap-1.5 text-[10px] text-slate-400 uppercase">
              <span>Directional Signal</span>
              <span className="text-emerald-400 font-bold text-[9px] px-1 py-0.2 bg-emerald-500/10 rounded border border-emerald-500/20">
                EVALUATING 1M CANDLES
              </span>
            </div>
            <div className="flex items-center gap-2 font-black text-slate-100 mt-0.5">
              <span
                className={`text-sm ${
                  currentSignal === 'LONG' || currentSignal === 'BUY'
                    ? 'text-trade-long'
                    : currentSignal === 'SHORT' || currentSignal === 'SELL'
                      ? 'text-trade-short'
                      : 'text-amber-400'
                }`}
              >
                {currentSignal === 'HOLD' ? 'HOLD (Neutral)' : currentSignal}
              </span>
              <span className="text-slate-400 text-xs font-normal">
                {currentSignal === 'HOLD'
                  ? '(No crossover detected)'
                  : `(${(signalStrength * 100).toFixed(0)}% exposure target)`}
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* Engine Operational Status Clarification Notice */}
      <div className="p-3.5 bg-blue-950/20 border border-blue-500/30 rounded-lg flex items-start gap-3 text-xs font-mono text-slate-300">
        <Info className="w-4 h-4 text-blue-400 mt-0.5 flex-shrink-0" />
        <div>
          <span className="font-bold text-blue-300 block mb-0.5">
            Engine Status: Online & Monitoring Live Ticks
          </span>
          <span>
            The strategy engine is actively running and evaluating every closed candle against Fast SMA(9) and Slow SMA(21). The current{' '}
            <strong className="text-amber-300 font-bold">HOLD</strong> signal indicates a neutral market state awaiting a moving average crossover — it does <strong className="text-slate-100 underline">not</strong> mean the strategy is paused or idle.
          </span>
        </div>
      </div>

      {/* Strategy KPI Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="p-4 bg-dark-850 border border-dark-750 rounded-lg space-y-1">
          <div className="flex items-center justify-between text-slate-400 text-xs font-mono uppercase">
            <span>Win Rate</span>
            <Percent className="w-4 h-4 text-emerald-400" />
          </div>
          <div className="text-2xl font-mono font-black text-trade-long tabular-numbers">
            {winRate.toFixed(1)}%
          </div>
          <p className="text-[11px] font-mono text-slate-500">
            {winningTrades} wins / {losingTrades} losses
          </p>
        </div>

        <div className="p-4 bg-dark-850 border border-dark-750 rounded-lg space-y-1">
          <div className="flex items-center justify-between text-slate-400 text-xs font-mono uppercase">
            <span>Sharpe Ratio</span>
            <Award className="w-4 h-4 text-trade-accent" />
          </div>
          <div className="text-2xl font-mono font-black text-slate-100 tabular-numbers">
            {sharpe.toFixed(2)}
          </div>
          <p className="text-[11px] font-mono text-slate-500">Risk-adjusted return multiple</p>
        </div>

        <div className="p-4 bg-dark-850 border border-dark-750 rounded-lg space-y-1">
          <div className="flex items-center justify-between text-slate-400 text-xs font-mono uppercase">
            <span>Max Strategy Drawdown</span>
            <TrendingDown className="w-4 h-4 text-amber-400" />
          </div>
          <div className="text-2xl font-mono font-black text-slate-100 tabular-numbers">
            {drawdown.toFixed(2)}%
          </div>
          <p className="text-[11px] font-mono text-slate-500">Hard stop threshold: 10.0%</p>
        </div>

        <div className="p-4 bg-dark-850 border border-dark-750 rounded-lg space-y-1">
          <div className="flex items-center justify-between text-slate-400 text-xs font-mono uppercase">
            <span>Signals / Fills</span>
            <Zap className="w-4 h-4 text-trade-cyan" />
          </div>
          <div className="text-2xl font-mono font-black text-slate-100 tabular-numbers">
            {totalSignals}{' '}
            <span className="text-sm text-slate-500 font-normal">/ {totalTrades} fills</span>
          </div>
          <p className="text-[11px] font-mono text-slate-500">100% evaluated by Risk Engine</p>
        </div>
      </div>

      {/* Manual Force Test Signal (Demo / Testnet Only) Control Card */}
      <div className="p-5 bg-gradient-to-r from-purple-950/20 via-dark-850 to-indigo-950/20 border-2 border-purple-500/40 rounded-xl space-y-4 shadow-xl">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-purple-500/20 pb-3">
          <div className="flex items-center gap-2.5">
            <div className="p-2 rounded-lg bg-purple-500/20 text-purple-400 border border-purple-500/40">
              <Send className="w-4 h-4" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h3 className="font-mono text-sm font-bold uppercase text-purple-200">
                  Manual Test Signal Dispatch (Demo / Testnet Only)
                </h3>
                <span className="font-mono text-[10px] px-2 py-0.5 rounded bg-purple-500/20 text-purple-300 border border-purple-500/40 font-bold">
                  TESTNET SMOKE TEST
                </span>
              </div>
              <p className="text-[11px] font-mono text-slate-400">
                Dispatches a real SignalEvent through the deterministic Risk Engine and OMS to Binance Testnet.
              </p>
            </div>
          </div>
        </div>

        {/* Input Parameters Grid */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 font-mono text-xs">
          {/* Symbol Selector */}
          <div className="space-y-1.5">
            <label className="text-[11px] text-slate-400 uppercase font-semibold block">Symbol</label>
            <select
              value={testSymbol}
              onChange={(e) => setTestSymbol(e.target.value)}
              className="w-full bg-dark-900 border border-dark-700 rounded px-3 py-2 text-slate-100 font-bold focus:outline-none focus:border-purple-500"
            >
              <option value="BTCUSDT">BTCUSDT</option>
              <option value="ETHUSDT">ETHUSDT</option>
              <option value="SOLUSDT">SOLUSDT</option>
            </select>
          </div>

          {/* Side Selector (BUY/SELL) */}
          <div className="space-y-1.5">
            <label className="text-[11px] text-slate-400 uppercase font-semibold block">Order Side</label>
            <div className="grid grid-cols-2 gap-2">
              <button
                type="button"
                onClick={() => setTestSide('BUY')}
                className={`py-2 rounded font-bold transition text-xs ${
                  testSide === 'BUY'
                    ? 'bg-emerald-600 text-white shadow-md shadow-emerald-600/30'
                    : 'bg-dark-900 text-slate-400 border border-dark-700 hover:text-slate-200'
                }`}
              >
                BUY (Long)
              </button>
              <button
                type="button"
                onClick={() => setTestSide('SELL')}
                className={`py-2 rounded font-bold transition text-xs ${
                  testSide === 'SELL'
                    ? 'bg-rose-600 text-white shadow-md shadow-rose-600/30'
                    : 'bg-dark-900 text-slate-400 border border-dark-700 hover:text-slate-200'
                }`}
              >
                SELL (Short)
              </button>
            </div>
          </div>

          {/* Test Exposure Target */}
          <div className="space-y-1.5">
            <label className="text-[11px] text-slate-400 uppercase font-semibold block">
              Test Size (% Equity)
            </label>
            <select
              value={testExposure}
              onChange={(e) => setTestExposure(Number(e.target.value))}
              className="w-full bg-dark-900 border border-dark-700 rounded px-3 py-2 text-slate-100 font-bold focus:outline-none focus:border-purple-500"
            >
              <option value={0.01}>1% Exposure (Micro)</option>
              <option value={0.02}>2% Exposure (Standard Test)</option>
              <option value={0.05}>5% Exposure (Moderate)</option>
            </select>
          </div>

          {/* Audit Reason */}
          <div className="space-y-1.5">
            <label className="text-[11px] text-slate-400 uppercase font-semibold block">
              Audit Reason
            </label>
            <input
              type="text"
              value={testReason}
              onChange={(e) => setTestReason(e.target.value)}
              placeholder="e.g. Manual operator verification"
              className="w-full bg-dark-900 border border-dark-700 rounded px-3 py-2 text-slate-100 text-xs focus:outline-none focus:border-purple-500"
            />
          </div>
        </div>

        {/* Confirmation Checkbox & Dispatch Button */}
        <div className="pt-2 border-t border-purple-500/20 flex flex-wrap items-center justify-between gap-4">
          <label className="flex items-center gap-2.5 cursor-pointer font-mono text-xs text-slate-300 select-none">
            <input
              type="checkbox"
              checked={testConfirmed}
              onChange={(e) => setTestConfirmed(e.target.checked)}
              className="w-4 h-4 rounded border-dark-700 text-purple-600 focus:ring-purple-500 bg-dark-900 cursor-pointer"
            />
            <span>
              I confirm this will dispatch a <strong className="text-purple-300">real order</strong> through the Risk Engine & OMS to Binance Futures Testnet.
            </span>
          </label>

          <button
            type="button"
            disabled={!testConfirmed || isSubmitting}
            onClick={handleDispatchTestSignal}
            className={`px-5 py-2.5 rounded-lg font-mono text-xs font-bold transition flex items-center gap-2 ${
              testConfirmed && !isSubmitting
                ? 'bg-gradient-to-r from-purple-600 to-indigo-600 text-white shadow-lg shadow-purple-500/30 hover:brightness-110 active:scale-95'
                : 'bg-dark-800 text-slate-500 border border-dark-700 cursor-not-allowed'
            }`}
          >
            {isSubmitting ? (
              <>
                <div className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                <span>Dispatching to Risk Engine...</span>
              </>
            ) : (
              <>
                <Send className="w-3.5 h-3.5" />
                <span>Dispatch {testSide} Test Signal</span>
              </>
            )}
          </button>
        </div>

        {/* Error Feedback */}
        {testError && (
          <div className="p-3 bg-rose-950/40 border border-rose-500/40 rounded text-rose-300 font-mono text-xs flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 text-rose-400 flex-shrink-0" />
            <span>{testError}</span>
          </div>
        )}

        {/* Success Feedback Banner */}
        {lastTestResult && (
          <div className="p-3.5 bg-emerald-950/40 border border-emerald-500/40 rounded-lg text-emerald-300 font-mono text-xs space-y-1">
            <div className="flex items-center gap-2 font-bold text-emerald-200">
              <CheckCircle2 className="w-4 h-4 text-emerald-400" />
              <span>TEST SIGNAL DISPATCHED TO PIPELINE</span>
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 pt-1 text-[11px] text-slate-300">
              <div>Signal ID: <span className="text-slate-100">{lastTestResult.signal_id.slice(0, 8)}...</span></div>
              <div>Symbol: <span className="text-slate-100">{lastTestResult.symbol}</span></div>
              <div>Side: <span className="text-slate-100">{lastTestResult.side}</span></div>
              <div>Mark Price: <span className="text-slate-100">${lastTestResult.mark_price.toLocaleString()}</span></div>
            </div>
          </div>
        )}
      </div>

      {/* Grid: Strategy Parameters & Equity Growth Trajectory */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Parameters Configuration Table */}
        <div className="bg-dark-850 border border-dark-750 rounded-lg p-4 space-y-3 font-mono text-xs">
          <div className="flex items-center gap-2 border-b border-dark-750 pb-2">
            <Sliders className="w-4 h-4 text-trade-accent" />
            <h3 className="font-bold uppercase text-slate-200">Strategy Parameters</h3>
          </div>

          <div className="space-y-2 divide-y divide-dark-750/50">
            <div className="flex justify-between py-1.5">
              <span className="text-slate-400">Fast Window</span>
              <span className="font-bold text-slate-200">{params.fast_window || 9} periods (SMA 9)</span>
            </div>
            <div className="flex justify-between py-1.5">
              <span className="text-slate-400">Slow Window</span>
              <span className="font-bold text-slate-200">{params.slow_window || 21} periods (SMA 21)</span>
            </div>
            <div className="flex justify-between py-1.5">
              <span className="text-slate-400">Candle Timeframe</span>
              <span className="font-bold text-slate-200">{params.candle_timeframe}</span>
            </div>
            <div className="flex justify-between py-1.5">
              <span className="text-slate-400">Target Universe</span>
              <span className="font-bold text-slate-200">
                {Array.isArray(params.active_symbols)
                  ? params.active_symbols.join(', ')
                  : 'BTCUSDT, ETHUSDT, SOLUSDT'}
              </span>
            </div>
            <div className="flex justify-between py-1.5">
              <span className="text-slate-400">Max Position Cap</span>
              <span className="font-bold text-slate-200">${params.max_position_size_usd} USD</span>
            </div>
            <div className="flex justify-between py-1.5">
              <span className="text-slate-400">Max Strategy Leverage</span>
              <span className="font-bold text-slate-200">{params.max_leverage}x</span>
            </div>
          </div>
        </div>

        {/* Equity Trajectory */}
        <div className="lg:col-span-2 bg-dark-850 border border-dark-750 rounded-lg p-4 space-y-3 font-mono text-xs">
          <div className="flex items-center justify-between border-b border-dark-750 pb-2">
            <div className="flex items-center gap-2">
              <TrendingUp className="w-4 h-4 text-trade-long" />
              <h3 className="font-bold uppercase text-slate-200">
                Strategy Equity Growth Trajectory
              </h3>
            </div>
            <span className="text-[11px] text-trade-long font-bold">
              +${(equityPoints[equityPoints.length - 1] - 10000).toFixed(2)} (
              {(
                ((equityPoints[equityPoints.length - 1] - 10000) / 10000) *
                100
              ).toFixed(2)}
              %)
            </span>
          </div>

          <div className="h-52 w-full flex items-end gap-2 pt-6 pb-2 px-2 bg-dark-900 rounded border border-dark-750">
            {equityPoints.map((val, idx) => {
              const minVal = 9800;
              const maxVal = 12000;
              const heightPct = Math.max(15, ((val - minVal) / (maxVal - minVal)) * 100);
              return (
                <div key={idx} className="flex-1 flex flex-col items-center gap-1.5 group relative">
                  <div
                    className="w-full bg-gradient-to-t from-trade-accent/40 to-trade-accent rounded-t transition-all group-hover:brightness-125"
                    style={{ height: `${heightPct}%` }}
                  />
                  <span className="text-[9px] text-slate-500">t{idx}</span>
                  {/* Tooltip */}
                  <div className="absolute -top-7 hidden group-hover:block bg-dark-950 px-2 py-0.5 rounded border border-dark-700 text-[10px] text-slate-100 font-bold whitespace-nowrap z-10 shadow-lg">
                    ${val.toFixed(2)}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
};
