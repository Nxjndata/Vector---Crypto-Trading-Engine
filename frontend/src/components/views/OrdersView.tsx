'use client';

import React, { useState } from 'react';
import {
  ArrowDown,
  ArrowUp,
  Clock,
  Filter,
  RefreshCw,
  ScrollText,
  Search,
} from 'lucide-react';
import { Order } from '../../types/trading';

interface OrdersViewProps {
  openOrders: Order[];
  orderHistory: Order[];
  onRefreshOrders: () => void;
}

export const OrdersView: React.FC<OrdersViewProps> = ({
  openOrders,
  orderHistory,
  onRefreshOrders,
}) => {
  const [activeSubTab, setActiveSubTab] = useState<'open' | 'history'>('open');
  const [searchSymbol, setSearchSymbol] = useState('');

  const displayOrders = activeSubTab === 'open' ? openOrders : orderHistory;
  const filteredOrders = searchSymbol
    ? displayOrders.filter((o) => o.symbol.toLowerCase().includes(searchSymbol.toLowerCase()))
    : displayOrders;

  return (
    <div className="space-y-6">
      {/* Header & Sub-Tabs */}
      <div className="flex flex-wrap items-center justify-between gap-4 p-4 bg-dark-850 border border-dark-750 rounded-lg">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded bg-dark-800 border border-dark-700 flex items-center justify-center text-trade-cyan">
            <ScrollText className="w-5 h-5" />
          </div>
          <div>
            <h2 className="text-sm font-mono font-bold uppercase text-slate-100">
              Order Management System (OMS)
            </h2>
            <p className="text-[11px] font-mono text-slate-400">
              Client-side tick/step validation & state machine lifecycle tracking
            </p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          {/* Search Filter */}
          <div className="relative">
            <Search className="w-3.5 h-3.5 text-slate-500 absolute left-2.5 top-2.5" />
            <input
              type="text"
              placeholder="Filter by symbol..."
              value={searchSymbol}
              onChange={(e) => setSearchSymbol(e.target.value)}
              className="pl-8 pr-3 py-1.5 bg-dark-900 border border-dark-750 rounded text-xs font-mono text-slate-200 placeholder-slate-500 focus:outline-none focus:border-trade-accent"
            />
          </div>

          <button
            onClick={onRefreshOrders}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-dark-800 hover:bg-dark-750 border border-dark-700 rounded text-xs font-mono text-slate-300 transition"
          >
            <RefreshCw className="w-3.5 h-3.5" />
            Refresh
          </button>
        </div>
      </div>

      {/* Sub-Tab Navigation */}
      <div className="flex items-center gap-2 border-b border-dark-750 font-mono text-xs">
        <button
          onClick={() => setActiveSubTab('open')}
          className={`px-4 py-2 border-b-2 font-bold transition ${
            activeSubTab === 'open'
              ? 'border-trade-accent text-slate-100'
              : 'border-transparent text-slate-400 hover:text-slate-200'
          }`}
        >
          Open Resting Orders ({openOrders.length})
        </button>
        <button
          onClick={() => setActiveSubTab('history')}
          className={`px-4 py-2 border-b-2 font-bold transition ${
            activeSubTab === 'history'
              ? 'border-trade-accent text-slate-100'
              : 'border-transparent text-slate-400 hover:text-slate-200'
          }`}
        >
          Order Execution History ({orderHistory.length})
        </button>
      </div>

      {/* Orders Table */}
      {filteredOrders.length === 0 ? (
        <div className="p-16 text-center bg-dark-850 border border-dark-750 rounded-lg space-y-2">
          <Clock className="w-8 h-8 text-slate-600 mx-auto" />
          <h3 className="text-sm font-mono font-bold text-slate-300">
            No {activeSubTab === 'open' ? 'Open Resting' : 'Historical'} Orders
          </h3>
          <p className="text-xs font-mono text-slate-500 max-w-sm mx-auto">
            {activeSubTab === 'open'
              ? 'All orders are currently filled or closed.'
              : 'No order history has been recorded yet.'}
          </p>
        </div>
      ) : (
        <div className="bg-dark-850 border border-dark-750 rounded-lg overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-xs font-mono text-left">
              <thead className="text-slate-400 border-b border-dark-750 bg-dark-900 uppercase text-[10px]">
                <tr>
                  <th className="py-3 px-4">Client Order ID</th>
                  <th className="py-3 px-4">Symbol</th>
                  <th className="py-3 px-4">Side</th>
                  <th className="py-3 px-4">Type / TIF</th>
                  <th className="py-3 px-4">Qty</th>
                  <th className="py-3 px-4">Price</th>
                  <th className="py-3 px-4">Filled / Rem</th>
                  <th className="py-3 px-4">Status</th>
                  <th className="py-3 px-4">Fee Paid</th>
                  <th className="py-3 px-4">Latency</th>
                  <th className="py-3 px-4 text-right">Created</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-dark-750/70">
                {filteredOrders.map((o) => {
                  const isBuy = o.side === 'BUY';
                  const isFilled = o.status === 'FILLED';
                  const isCancelled = o.status === 'CANCELLED';

                  return (
                    <tr key={o.client_order_id} className="hover:bg-dark-800/50 transition">
                      <td className="py-3 px-4">
                        <div className="font-mono text-slate-200 font-semibold truncate max-w-[140px]" title={o.client_order_id}>
                          {o.client_order_id}
                        </div>
                        {o.exchange_order_id && (
                          <span className="text-[10px] text-slate-500">
                            ExID: {o.exchange_order_id}
                          </span>
                        )}
                      </td>
                      <td className="py-3 px-4 font-bold text-slate-100">{o.symbol}</td>
                      <td className="py-3 px-4">
                        <span
                          className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-bold ${
                            isBuy
                              ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30'
                              : 'bg-red-500/20 text-red-400 border border-red-500/30'
                          }`}
                        >
                          {isBuy ? <ArrowUp className="w-3 h-3" /> : <ArrowDown className="w-3 h-3" />}
                          {o.side}
                        </span>
                      </td>
                      <td className="py-3 px-4 text-slate-300">
                        {o.order_type} <span className="text-slate-500">({o.time_in_force})</span>
                      </td>
                      <td className="py-3 px-4 tabular-numbers text-slate-200">{o.quantity}</td>
                      <td className="py-3 px-4 tabular-numbers text-slate-200">
                        {o.price ? `$${o.price.toLocaleString(undefined, { minimumFractionDigits: 2 })}` : 'MARKET'}
                      </td>
                      <td className="py-3 px-4 tabular-numbers text-slate-300">
                        <span className="font-semibold text-slate-100">{o.filled_qty}</span> / {o.remaining_qty}
                      </td>
                      <td className="py-3 px-4">
                        <span
                          className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase ${
                            isFilled
                              ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/40'
                              : isCancelled
                                ? 'bg-slate-700 text-slate-400'
                                : 'bg-blue-500/20 text-blue-400 border border-blue-500/40'
                          }`}
                        >
                          {o.status}
                        </span>
                      </td>
                      <td className="py-3 px-4 tabular-numbers text-slate-400">
                        ${o.fee_paid.toFixed(4)} {o.fee_asset}
                      </td>
                      <td className="py-3 px-4 tabular-numbers text-slate-400">
                        {o.latency_ms !== null ? `${o.latency_ms.toFixed(1)}ms` : '-'}
                      </td>
                      <td className="py-3 px-4 tabular-numbers text-right text-slate-500 text-[11px]">
                        {new Date(o.created_at).toLocaleTimeString()}
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
