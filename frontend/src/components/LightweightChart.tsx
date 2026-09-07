'use client';

import React, { useEffect, useRef, useState } from 'react';
import { ColorType, createChart, IChartApi, ISeriesApi } from 'lightweight-charts';
import { KlineCandle } from '../types/trading';

interface LightweightChartProps {
  data: KlineCandle[];
  latestCandle?: KlineCandle | any | null;
  symbol: string;
}

export const LightweightChart: React.FC<LightweightChartProps> = ({
  data,
  latestCandle,
  symbol,
}) => {
  const chartContainerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null);
  const fastSmaSeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const slowSmaSeriesRef = useRef<ISeriesApi<'Line'> | null>(null);

  const [lastFastSma, setLastFastSma] = useState<number | null>(null);
  const [lastSlowSma, setLastSlowSma] = useState<number | null>(null);
  const [showFastSma, setShowFastSma] = useState<boolean>(true);
  const [showSlowSma, setShowSlowSma] = useState<boolean>(true);

  useEffect(() => {
    if (!chartContainerRef.current) return;

    // Initialize Lightweight Chart
    const chart = createChart(chartContainerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: '#0B0F17' },
        textColor: '#94A3B8',
        fontSize: 11,
      },
      grid: {
        vertLines: { color: '#161D2D' },
        horzLines: { color: '#161D2D' },
      },
      crosshair: {
        mode: 1,
        vertLine: { color: '#3B82F6', width: 1, style: 3 },
        horzLine: { color: '#3B82F6', width: 1, style: 3 },
      },
      rightPriceScale: {
        borderColor: '#1E2638',
        scaleMargins: {
          top: 0.1,
          bottom: 0.2,
        },
      },
      timeScale: {
        borderColor: '#1E2638',
        timeVisible: true,
        secondsVisible: false,
      },
    });

    // Candlestick Series
    const candleSeries = chart.addCandlestickSeries({
      upColor: '#10B981',
      downColor: '#EF4444',
      borderUpColor: '#10B981',
      borderDownColor: '#EF4444',
      wickUpColor: '#10B981',
      wickDownColor: '#EF4444',
    });

    // Volume Histogram Series
    const volumeSeries = chart.addHistogramSeries({
      color: '#1E293B',
      priceFormat: {
        type: 'volume',
      },
      priceScaleId: '',
    });

    volumeSeries.priceScale().applyOptions({
      scaleMargins: {
        top: 0.8,
        bottom: 0,
      },
    });

    // Strategy Fast SMA (9) Line Overlay (Cyan)
    const fastSmaSeries = chart.addLineSeries({
      color: '#38BDF8',
      lineWidth: 2,
      title: 'Fast SMA 9',
      crosshairMarkerVisible: true,
      priceLineVisible: false,
    });

    // Strategy Slow SMA (21) Line Overlay (Amber)
    const slowSmaSeries = chart.addLineSeries({
      color: '#F59E0B',
      lineWidth: 2,
      title: 'Slow SMA 21',
      crosshairMarkerVisible: true,
      priceLineVisible: false,
    });

    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;
    volumeSeriesRef.current = volumeSeries;
    fastSmaSeriesRef.current = fastSmaSeries;
    slowSmaSeriesRef.current = slowSmaSeries;

    // Set initial data
    if (data && data.length > 0) {
      candleSeries.setData(
        data.map((d) => ({
          time: d.time as any,
          open: d.open,
          high: d.high,
          low: d.low,
          close: d.close,
        }))
      );

      volumeSeries.setData(
        data.map((d) => ({
          time: d.time as any,
          value: d.volume || (d.high - d.low) * 10,
          color: d.close >= d.open ? 'rgba(16, 185, 129, 0.3)' : 'rgba(239, 68, 68, 0.3)',
        }))
      );

      // Fast SMA Data points
      const fastData = data
        .filter((d) => d.fast_sma !== undefined && d.fast_sma !== null)
        .map((d) => ({
          time: d.time as any,
          value: d.fast_sma as number,
        }));
      fastSmaSeries.setData(fastData);
      if (fastData.length > 0) {
        setLastFastSma(fastData[fastData.length - 1].value);
      }

      // Slow SMA Data points
      const slowData = data
        .filter((d) => d.slow_sma !== undefined && d.slow_sma !== null)
        .map((d) => ({
          time: d.time as any,
          value: d.slow_sma as number,
        }));
      slowSmaSeries.setData(slowData);
      if (slowData.length > 0) {
        setLastSlowSma(slowData[slowData.length - 1].value);
      }

      chart.timeScale().fitContent();
    }

    // Resize observer
    const handleResize = () => {
      if (chartContainerRef.current) {
        chart.applyOptions({
          width: chartContainerRef.current.clientWidth,
          height: chartContainerRef.current.clientHeight,
        });
      }
    };

    window.addEventListener('resize', handleResize);
    handleResize();

    return () => {
      window.removeEventListener('resize', handleResize);
      chart.remove();
    };
  }, [symbol, data]);

  // Handle live incremental candle updates from WebSocket
  useEffect(() => {
    if (latestCandle && candleSeriesRef.current) {
      const candleTime = (latestCandle.time || latestCandle.open_time) as any;
      candleSeriesRef.current.update({
        time: candleTime,
        open: latestCandle.open ?? latestCandle.open_price,
        high: latestCandle.high ?? latestCandle.high_price,
        low: latestCandle.low ?? latestCandle.low_price,
        close: latestCandle.close ?? latestCandle.close_price,
      });

      if (volumeSeriesRef.current && (latestCandle.volume !== undefined)) {
        const closeP = latestCandle.close ?? latestCandle.close_price;
        const openP = latestCandle.open ?? latestCandle.open_price;
        volumeSeriesRef.current.update({
          time: candleTime,
          value: latestCandle.volume,
          color:
            closeP >= openP
              ? 'rgba(16, 185, 129, 0.3)'
              : 'rgba(239, 68, 68, 0.3)',
        });
      }

      // Update Fast SMA
      if (fastSmaSeriesRef.current && latestCandle.fast_sma !== undefined && latestCandle.fast_sma !== null) {
        fastSmaSeriesRef.current.update({
          time: candleTime,
          value: latestCandle.fast_sma,
        });
        setLastFastSma(latestCandle.fast_sma);
      }

      // Update Slow SMA
      if (slowSmaSeriesRef.current && latestCandle.slow_sma !== undefined && latestCandle.slow_sma !== null) {
        slowSmaSeriesRef.current.update({
          time: candleTime,
          value: latestCandle.slow_sma,
        });
        setLastSlowSma(latestCandle.slow_sma);
      }
    }
  }, [latestCandle]);

  return (
    <div className="w-full h-full min-h-[380px] relative bg-dark-900 rounded-lg overflow-hidden border border-dark-750">
      {/* Top Left Symbol Tag */}
      <div className="absolute top-3 left-3 z-10 flex items-center gap-2">
        <div className="font-mono text-xs text-slate-300 bg-dark-850/90 px-2.5 py-1 rounded border border-dark-750 backdrop-blur-sm shadow-md">
          <span className="font-bold text-slate-100">{symbol}</span>
          <span className="text-slate-500 ml-1.5 text-[10px]">USDT-M Perpetual</span>
        </div>
      </div>

      {/* Top Right Strategy Overlays Legend */}
      <div className="absolute top-3 right-3 z-10 flex items-center gap-2">
        {/* Fast SMA Legend */}
        <div className="flex items-center gap-1.5 font-mono text-[11px] bg-dark-850/90 px-2 py-0.5 rounded border border-cyan-500/30 text-cyan-400 backdrop-blur-sm">
          <div className="w-2.5 h-0.5 bg-cyan-400 rounded-full" />
          <span className="font-bold">Fast SMA(9):</span>
          <span>{lastFastSma !== null ? `$${lastFastSma.toFixed(2)}` : 'Buffering...'}</span>
        </div>

        {/* Slow SMA Legend */}
        <div className="flex items-center gap-1.5 font-mono text-[11px] bg-dark-850/90 px-2 py-0.5 rounded border border-amber-500/30 text-amber-400 backdrop-blur-sm">
          <div className="w-2.5 h-0.5 bg-amber-400 rounded-full" />
          <span className="font-bold">Slow SMA(21):</span>
          <span>{lastSlowSma !== null ? `$${lastSlowSma.toFixed(2)}` : 'Buffering...'}</span>
        </div>
      </div>

      <div ref={chartContainerRef} className="w-full h-full" />
    </div>
  );
};
