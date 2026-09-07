'use client';

import React, { useEffect, useState } from 'react';
import {
  ArrowDownRight,
  ArrowUpRight,
  CandlestickChart,
  DollarSign,
  Layers,
  Percent,
  Radio,
  Sliders,
} from 'lucide-react';
import { KlineCandle, MarketTicker } from '../../types/trading';
import { LightweightChart } from '../LightweightChart';
import { api } from '../../services/api';

interface MarketsViewProps {
  markets: MarketTicker[];
  latestCandle?: any;
}

export const MarketsView: React.FC<MarketsViewProps> = ({ markets, latestCandle }) => {
  const [selectedSymbol, setSelectedSymbol] = useState<string>(
    markets[0]?.symbol || 'BTCUSDT'
  );
  const [timeframe, setTimeframe] = useState<string>('1m');
  const [chartData, setChartData] = useState<KlineCandle[]>([]);
  const [isLoadingChart, setIsLoadingChart] = useState<boolean>(false);

  const currentMarket =
    markets.find((m) => m.symbol === selectedSymbol) || markets[0];

  // Fetch genuine historical klines from backend for selected symbol & timeframe
  useEffect(() => {
    let unmounted = false;
    setIsLoadingChart(true);

    api
      .getKlines(selectedSymbol, timeframe, 120)
      .then((klines) => {
        if (!unmounted && Array.isArray(klines)) {
          setChartData(klines);
        }
      })
      .catch((err) => {
        console.error(`Failed to load historical klines for ${selectedSymbol}:`, err);
      })
      .finally(() => {
        if (!unmounted) setIsLoadingChart(false);
      });

    return () => {
      unmounted = true;
    };
  }, [selectedSymbol, timeframe]);

  const isUp = (currentMarket?.price_change_percent_24h ?? 0) >= 0;

  return (
    <div className="space-y-6">
      {/* Symbol Selector Bar & 24h Stats Ribbon */}
      <div className="p-4 bg-dark-850 border border-dark-750 rounded-lg space-y-4">
        {/* Symbol Selection Buttons */}
        <div className="flex flex-wrap items-center justify-between gap-4 border-b border-dark-750 pb-3">
          <div className="flex items-center gap-2">
            {markets.map((m) => {
              const isSelected = m.symbol === selectedSymbol;
              return (
                <button
                  key={m.symbol}
                  onClick={() => setSelectedSymbol(m.symbol)}
                  className={`px-3.5 py-1.5 rounded text-xs font-mono font-bold transition ${
                    isSelected
                      ? 'bg-trade-accent text-white shadow-sm shadow-blue-500/30'
                      : 'bg-dark-900 text-slate-400 hover:text-slate-200 border border-dark-750'
                  }`}
                >
                  {m.symbol}
                </button>
              );
            })}
          </div>

          {/* Timeframe selector */}
          <div className="flex items-center gap-1 font-mono text-xs text-slate-400">
            <span className="mr-2 text-[10px] uppercase text-slate-500">Timeframe:</span>
            {['1m', '5m', '15m', '1h', '4h', '1d'].map((tf) => (
              <button
                key={tf}
                onClick={() => setTimeframe(tf)}
                className={`px-2 py-0.5 rounded text-[11px] ${
                  timeframe === tf
                    ? 'bg-dark-750 text-slate-100 font-bold border border-dark-700'
                    : 'text-slate-500 hover:text-slate-300'
                }`}
              >
                {tf}
              </button>
            ))}
          </div>
        </div>

        {/* 24h Market Stat Cards */}
        {currentMarket && (
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 font-mono text-xs">
            <div>
              <span className="text-[10px] text-slate-500 block uppercase">Mark Price</span>
              <span className="text-slate-100 font-black text-sm tabular-numbers">
                ${currentMarket.mark_price.toLocaleString(undefined, { minimumFractionDigits: 2 })}
              </span>
            </div>
            <div>
              <span className="text-[10px] text-slate-500 block uppercase">24h Change</span>
              <span
                className={`font-bold flex items-center ${
                  isUp ? 'text-trade-long' : 'text-trade-short'
                }`}
              >
                {isUp ? (
                  <ArrowUpRight className="w-3.5 h-3.5 mr-0.5" />
                ) : (
                  <ArrowDownRight className="w-3.5 h-3.5 mr-0.5" />
                )}
                {isUp ? '+' : ''}
                {currentMarket.price_change_percent_24h.toFixed(2)}%
              </span>
            </div>
            <div>
              <span className="text-[10px] text-slate-500 block uppercase">Funding Rate</span>
              <span className="text-slate-200 font-semibold tabular-numbers">
                {(currentMarket.funding_rate !== null
                  ? (currentMarket.funding_rate * 100).toFixed(4)
                  : '0.0100')}%
              </span>
            </div>
            <div>
              <span className="text-[10px] text-slate-500 block uppercase">24h High / Low</span>
              <span className="text-slate-300 tabular-numbers">
                ${currentMarket.high_price_24h.toLocaleString()} / $
                {currentMarket.low_price_24h.toLocaleString()}
              </span>
            </div>
            <div>
              <span className="text-[10px] text-slate-500 block uppercase">24h Volume</span>
              <span className="text-slate-300 tabular-numbers">
                {currentMarket.volume_24h.toLocaleString()} {currentMarket.symbol.replace('USDT', '')}
              </span>
            </div>
            <div>
              <span className="text-[10px] text-slate-500 block uppercase">Precision Filters</span>
              <span className="text-slate-400 text-[11px]">
                Tick: {currentMarket.tick_size} | Step: {currentMarket.step_size}
              </span>
            </div>
          </div>
        )}
      </div>

      {/* TradingView Lightweight Candlestick Chart with Fast & Slow SMA Overlays */}
      <div className="h-[420px] relative">
        {isLoadingChart && chartData.length === 0 && (
          <div className="absolute inset-0 z-20 flex items-center justify-center bg-dark-900/80 backdrop-blur-sm">
            <span className="font-mono text-xs text-slate-400 animate-pulse">Loading Binance Testnet Klines...</span>
          </div>
        )}
        <LightweightChart
          data={chartData}
          latestCandle={latestCandle?.symbol === selectedSymbol ? latestCandle : null}
          symbol={selectedSymbol}
        />
      </div>

      {/* Universe Overview Table */}
      <div className="bg-dark-850 border border-dark-750 rounded-lg p-4 space-y-3">
        <h3 className="text-xs font-mono font-bold uppercase text-slate-200">
          Configured Active Universe Pairs
        </h3>
        <div className="overflow-x-auto">
          <table className="w-full text-xs font-mono text-left">
            <thead className="text-slate-400 border-b border-dark-750 bg-dark-900 uppercase text-[10px]">
              <tr>
                <th className="py-2.5 px-3">Symbol</th>
                <th className="py-2.5 px-3">Mark Price</th>
                <th className="py-2.5 px-3">24h Change</th>
                <th className="py-2.5 px-3">Funding Rate</th>
                <th className="py-2.5 px-3">24h Volume (Quote)</th>
                <th className="py-2.5 px-3">Min Notional</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-dark-750/60">
              {markets.map((m) => {
                const up = m.price_change_percent_24h >= 0;
                return (
                  <tr
                    key={m.symbol}
                    onClick={() => setSelectedSymbol(m.symbol)}
                    className="hover:bg-dark-800/40 cursor-pointer"
                  >
                    <td className="py-2.5 px-3 font-bold text-slate-200">{m.symbol}</td>
                    <td className="py-2.5 px-3 tabular-numbers text-slate-100 font-semibold">
                      ${m.mark_price.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                    </td>
                    <td
                      className={`py-2.5 px-3 tabular-numbers font-semibold ${
                        up ? 'text-trade-long' : 'text-trade-short'
                      }`}
                    >
                      {up ? '+' : ''}
                      {m.price_change_percent_24h.toFixed(2)}%
                    </td>
                    <td className="py-2.5 px-3 tabular-numbers text-slate-300">
                      {m.funding_rate ? `${(m.funding_rate * 100).toFixed(4)}%` : '0.0100%'}
                    </td>
                    <td className="py-2.5 px-3 tabular-numbers text-slate-300">
                      ${m.quote_volume_24h.toLocaleString(undefined, { maximumFractionDigits: 0 })} USDT
                    </td>
                    <td className="py-2.5 px-3 tabular-numbers text-slate-400">
                      ${m.min_notional} USDT
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};
