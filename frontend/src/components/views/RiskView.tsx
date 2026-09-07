'use client';

import React, { useState } from 'react';
import {
  AlertOctagon,
  AlertTriangle,
  CheckCircle2,
  Filter,
  RotateCcw,
  Shield,
  ShieldAlert,
  ShieldCheck,
  XCircle,
} from 'lucide-react';
import { RiskStatus } from '../../types/trading';

interface RiskViewProps {
  riskStatus: RiskStatus | null;
  onOpenKillSwitchModal: () => void;
  onResetKillSwitch: () => void;
}

export const RiskView: React.FC<RiskViewProps> = ({
  riskStatus,
  onOpenKillSwitchModal,
  onResetKillSwitch,
}) => {
  const [filterDecision, setFilterDecision] = useState<string>('ALL');
  const isKillSwitchActive = riskStatus?.kill_switch_active ?? false;

  const limits = [
    {
      title: 'Max Position Size',
      desc: 'Cap per individual symbol exposure',
      metric: riskStatus?.max_position_size_usd,
      color: 'bg-trade-accent',
    },
    {
      title: 'Max Portfolio Exposure',
      desc: 'Gross aggregate notional cap',
      metric: riskStatus?.max_portfolio_exposure_usd,
      color: 'bg-trade-cyan',
    },
    {
      title: 'Max Aggregate Leverage',
      desc: 'Portfolio notional / Account equity',
      metric: riskStatus?.max_leverage,
      color: 'bg-amber-400',
    },
    {
      title: 'Max Drawdown Threshold',
      desc: 'Peak-to-trough equity breach guard',
      metric: riskStatus?.max_drawdown_pct,
      color: 'bg-red-400',
    },
  ];

  const decisions = riskStatus?.recent_decisions || [];
  const filteredDecisions =
    filterDecision === 'ALL'
      ? decisions
      : decisions.filter((d) => d.decision === filterDecision);

  return (
    <div className="space-y-6">
      {/* Hero Kill-Switch Control Box */}
      <div
        className={`p-6 rounded-lg border transition-all ${
          isKillSwitchActive
            ? 'bg-red-950/40 border-red-500/60 shadow-lg shadow-red-950/50'
            : 'bg-dark-850 border-dark-750'
        }`}
      >
        <div className="flex flex-wrap items-center justify-between gap-6">
          <div className="flex items-start gap-4">
            <div
              className={`p-3 rounded-lg border ${
                isKillSwitchActive
                  ? 'bg-red-900/60 border-red-500 text-red-300 animate-pulse'
                  : 'bg-dark-800 border-dark-700 text-emerald-400'
              }`}
            >
              <AlertOctagon className="w-8 h-8" />
            </div>
            <div className="space-y-1">
              <div className="flex items-center gap-3">
                <h2 className="text-base font-mono font-black uppercase text-slate-100">
                  Pre-Trade Risk Engine & Kill-Switch
                </h2>
                <span
                  className={`text-xs font-mono font-black px-2.5 py-0.5 rounded uppercase tracking-wider ${
                    isKillSwitchActive
                      ? 'bg-red-500 text-white'
                      : 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/40'
                  }`}
                >
                  {isKillSwitchActive ? 'HALTED (KILL-SWITCH ENGAGED)' : 'OPERATIONAL (STANDBY)'}
                </span>
              </div>
              <p className="text-xs font-mono text-slate-400 max-w-xl">
                {isKillSwitchActive
                  ? `Active Reason: ${riskStatus?.kill_switch_reason || 'Manual Emergency Halt'}. All subsequent strategy intent signals are immediately rejected before touching the OMS.`
                  : 'The Risk Engine continuously validates all strategy signals against non-negotiable deterministic risk rules, margin constraints, and liquidity buffers.'}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-3">
            {!isKillSwitchActive ? (
              <button
                onClick={onOpenKillSwitchModal}
                className="flex items-center gap-2 px-4 py-2.5 bg-red-600 hover:bg-red-500 text-white font-mono font-bold text-xs rounded transition shadow-md shadow-red-900/30"
              >
                <AlertOctagon className="w-4 h-4" />
                Engage Emergency Halt
              </button>
            ) : (
              <button
                onClick={onResetKillSwitch}
                className="flex items-center gap-2 px-4 py-2.5 bg-emerald-600 hover:bg-emerald-500 text-white font-mono font-bold text-xs rounded transition shadow-md shadow-emerald-900/30"
              >
                <RotateCcw className="w-4 h-4" />
                Reset & Disengage Halt
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Risk Limit Utilization Gauges */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {limits.map((lim) => {
          const util = lim.metric?.utilization_pct ?? 0;
          const isWarning = util >= 80;
          const isBreach = util >= 100;

          return (
            <div key={lim.title} className="p-4 bg-dark-850 border border-dark-750 rounded-lg space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-xs font-mono font-bold text-slate-200">{lim.title}</span>
                <span
                  className={`text-[10px] font-mono font-bold px-1.5 py-0.5 rounded ${
                    isBreach
                      ? 'bg-red-500/20 text-red-400'
                      : isWarning
                        ? 'bg-amber-500/20 text-amber-400'
                        : 'bg-dark-800 text-slate-400'
                  }`}
                >
                  {util.toFixed(1)}% Utilized
                </span>
              </div>

              <div>
                <div className="text-lg font-mono font-black text-slate-100 tabular-numbers">
                  {lim.metric?.current ?? 0} {lim.metric?.unit}{' '}
                  <span className="text-xs text-slate-500 font-normal">
                    / {lim.metric?.limit ?? 0} {lim.metric?.unit}
                  </span>
                </div>
                <p className="text-[10px] font-mono text-slate-500">{lim.desc}</p>
              </div>

              <div className="w-full bg-dark-750 rounded-full h-2 overflow-hidden">
                <div
                  className={`h-2 rounded-full transition-all ${
                    isBreach ? 'bg-red-500' : isWarning ? 'bg-amber-400' : lim.color
                  }`}
                  style={{ width: `${Math.min(100, Math.max(0, util))}%` }}
                />
              </div>
            </div>
          );
        })}
      </div>

      {/* Immutable Risk Decision Audit Trail */}
      <div className="bg-dark-850 border border-dark-750 rounded-lg overflow-hidden space-y-4 p-4">
        <div className="flex flex-wrap items-center justify-between gap-4 border-b border-dark-750 pb-3">
          <div className="flex items-center gap-2">
            <ShieldCheck className="w-4 h-4 text-trade-cyan" />
            <h3 className="text-sm font-mono font-bold uppercase text-slate-200">
              Immutable Risk Decision Audit Log
            </h3>
          </div>

          <div className="flex items-center gap-2 text-xs font-mono">
            <Filter className="w-3.5 h-3.5 text-slate-500" />
            <span className="text-slate-400">Filter:</span>
            {['ALL', 'APPROVED', 'RESIZED', 'REJECTED'].map((f) => (
              <button
                key={f}
                onClick={() => setFilterDecision(f)}
                className={`px-2 py-0.5 rounded text-[11px] ${
                  filterDecision === f
                    ? 'bg-dark-750 text-slate-100 font-bold border border-dark-700'
                    : 'text-slate-500 hover:text-slate-300'
                }`}
              >
                {f}
              </button>
            ))}
          </div>
        </div>

        {filteredDecisions.length === 0 ? (
          <div className="py-12 text-center text-slate-500 font-mono text-xs">
            No risk decisions recorded for the selected filter.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs font-mono text-left">
              <thead className="text-slate-400 border-b border-dark-750 bg-dark-900 uppercase text-[10px]">
                <tr>
                  <th className="py-2.5 px-3">Timestamp</th>
                  <th className="py-2.5 px-3">Signal ID</th>
                  <th className="py-2.5 px-3">Symbol</th>
                  <th className="py-2.5 px-3">Decision</th>
                  <th className="py-2.5 px-3">Original Intent</th>
                  <th className="py-2.5 px-3">Approved Exposure</th>
                  <th className="py-2.5 px-3">Evaluation Reason</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-dark-750/60">
                {filteredDecisions.map((d, i) => {
                  const isApprove = d.decision === 'APPROVED';
                  const isReject = d.decision === 'REJECTED';

                  return (
                    <tr key={i} className="hover:bg-dark-800/40">
                      <td className="py-2.5 px-3 tabular-numbers text-slate-400">
                        {new Date(d.evaluated_at).toLocaleTimeString()}
                      </td>
                      <td className="py-2.5 px-3 font-mono text-slate-300">
                        {d.signal_id.slice(0, 8)}...
                      </td>
                      <td className="py-2.5 px-3 font-bold text-slate-200">{d.symbol}</td>
                      <td className="py-2.5 px-3">
                        <span
                          className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                            isApprove
                              ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30'
                              : isReject
                                ? 'bg-red-500/20 text-red-400 border border-red-500/30'
                                : 'bg-amber-500/20 text-amber-400 border border-amber-500/30'
                          }`}
                        >
                          {d.decision}
                        </span>
                      </td>
                      <td className="py-2.5 px-3 tabular-numbers text-slate-300">
                        {(d.original_size * 100).toFixed(1)}% Eq
                      </td>
                      <td className="py-2.5 px-3 tabular-numbers font-semibold text-slate-100">
                        {(d.approved_size * 100).toFixed(1)}% Eq
                      </td>
                      <td className="py-2.5 px-3 text-slate-400 max-w-xs truncate" title={d.reject_reason || ''}>
                        {d.reject_reason || 'Passed all risk rules'}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
};
