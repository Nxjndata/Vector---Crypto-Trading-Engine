'use client';

import React, { useEffect, useState } from 'react';
import { AlertOctagon, AlertTriangle, X } from 'lucide-react';

interface KillSwitchModalProps {
  isOpen: boolean;
  onClose: () => void;
  onConfirm: (reason: string) => void;
  isLoading: boolean;
  serverError?: string | null;
}

export const KillSwitchModal: React.FC<KillSwitchModalProps> = ({
  isOpen,
  onClose,
  onConfirm,
  isLoading,
  serverError,
}) => {
  const [reason, setReason] = useState('Manual operator emergency intervention');
  const [confirmed, setConfirmed] = useState(false);
  const [validationError, setValidationError] = useState<string | null>(null);

  useEffect(() => {
    if (isOpen) {
      setReason('Manual operator emergency intervention');
      setConfirmed(false);
      setValidationError(null);
    }
  }, [isOpen]);

  if (!isOpen) return null;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setValidationError(null);

    if (!reason.trim()) {
      setValidationError('Reason is required to engage the emergency kill-switch.');
      return;
    }

    if (!confirmed) {
      setValidationError(
        'You must check the confirmation checkbox below to authorize the emergency halt.'
      );
      return;
    }

    onConfirm(reason.trim());
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm animate-in fade-in duration-200 font-mono">
      <div className="bg-dark-900 border-2 border-red-500/80 rounded-xl max-w-lg w-full p-6 space-y-5 shadow-2xl shadow-red-950/80">
        {/* Header */}
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-3">
            <div className="p-2.5 rounded-lg bg-red-950/80 border border-red-500/60 text-red-400">
              <AlertOctagon className="w-6 h-6 animate-pulse" />
            </div>
            <div>
              <h2 className="text-base font-bold uppercase text-slate-100">
                Engage Emergency Kill-Switch
              </h2>
              <p className="text-xs text-red-400 font-medium">Pre-Trade Risk Engine Halt</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-slate-500 hover:text-slate-300 p-1 rounded-lg transition"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Server Error Alert */}
        {serverError && (
          <div className="p-3 bg-red-950/80 border border-red-500 rounded-lg text-red-200 text-xs flex items-start gap-2 animate-in slide-in-from-top duration-150">
            <AlertTriangle className="w-4 h-4 text-red-400 shrink-0 mt-0.5" />
            <div>
              <span className="font-bold uppercase">Backend Error: </span>
              <span>{serverError}</span>
            </div>
          </div>
        )}

        {/* Validation Error Alert */}
        {validationError && (
          <div className="p-3 bg-amber-950/80 border border-amber-500 rounded-lg text-amber-200 text-xs flex items-start gap-2 animate-in slide-in-from-top duration-150">
            <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
            <div>
              <span className="font-bold uppercase">Validation Error: </span>
              <span>{validationError}</span>
            </div>
          </div>
        )}

        {/* Form */}
        <form onSubmit={handleSubmit} className="space-y-4 text-xs">
          <div className="p-3 bg-red-950/30 border border-red-900/50 rounded-lg text-slate-300 space-y-1">
            <p className="font-bold text-red-300">CRITICAL OPERATIONAL IMPACT:</p>
            <ul className="list-disc pl-4 space-y-1 text-[11px] text-slate-400">
              <li>Immediately vetoes 100% of incoming Strategy signals in RiskEngine.</li>
              <li>Halts all automated order generation across all active symbols.</li>
              <li>Emits a critical SystemStatusEvent across the EventBus.</li>
            </ul>
          </div>

          <div className="space-y-1.5">
            <label className="block text-slate-300 font-bold uppercase text-[11px]">
              Reason for Kill-Switch Halt: <span className="text-red-400">*</span>
            </label>
            <textarea
              value={reason}
              onChange={(e) => {
                setReason(e.target.value);
                if (validationError) setValidationError(null);
              }}
              required
              rows={2}
              className={`w-full px-3 py-2 bg-dark-950 border rounded text-slate-200 focus:outline-none text-xs font-mono resize-none transition ${
                !reason.trim() && validationError
                  ? 'border-red-500 focus:border-red-400'
                  : 'border-dark-750 focus:border-red-500'
              }`}
              placeholder="e.g. Extreme market volatility, unexpected latency skew..."
            />
          </div>

          <label
            className={`flex items-start gap-2.5 cursor-pointer p-2.5 rounded border transition ${
              !confirmed && validationError
                ? 'bg-red-950/20 border-red-500/60'
                : 'bg-dark-950 border-dark-750'
            }`}
          >
            <input
              type="checkbox"
              checked={confirmed}
              onChange={(e) => {
                setConfirmed(e.target.checked);
                if (validationError) setValidationError(null);
              }}
              className="mt-0.5 rounded border-dark-700 bg-dark-900 text-red-600 focus:ring-red-500 cursor-pointer"
            />
            <span className="text-[11px] text-slate-300 leading-tight">
              I explicitly confirm initiating an immediate emergency kill-switch halt on the trading
              platform. <span className="text-red-400 font-bold">*</span>
            </span>
          </label>

          <div className="flex items-center justify-end gap-3 pt-2">
            <button
              type="button"
              onClick={onClose}
              disabled={isLoading}
              className="px-4 py-2 bg-dark-800 hover:bg-dark-750 border border-dark-700 text-slate-300 rounded font-bold transition disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isLoading}
              className="px-5 py-2 bg-red-600 hover:bg-red-500 text-white rounded font-bold transition shadow-lg shadow-red-950/80 flex items-center gap-2 disabled:opacity-60 cursor-pointer"
            >
              <AlertOctagon className="w-4 h-4" />
              {isLoading ? 'Engaging Halt...' : 'Engage Kill-Switch'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
