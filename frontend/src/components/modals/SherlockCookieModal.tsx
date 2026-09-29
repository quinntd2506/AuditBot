import React, { useState } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { X, Terminal, CheckCircle2, Loader2, AlertTriangle, ExternalLink } from 'lucide-react';

interface SherlockCookieModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: () => void;
}

const STEPS = [
  { step: '01', text: 'Open your browser and go to', link: 'https://audits.sherlock.xyz', linkLabel: 'audits.sherlock.xyz' },
  { step: '02', text: 'Make sure you are logged in with your MetaMask wallet.' },
  { step: '03', text: 'Open DevTools → Network tab (F12 or Cmd+Option+I).' },
  { step: '04', text: 'Reload the page, then click any request to audits.sherlock.xyz.' },
  { step: '05', text: 'Under Request Headers, find and copy the full Cookie header value.' },
];

export const SherlockCookieModal: React.FC<SherlockCookieModalProps> = ({ isOpen, onClose, onSuccess }) => {
  const [cookie, setCookie] = useState('');
  const [status, setStatus] = useState<'idle' | 'loading' | 'success' | 'error'>('idle');
  const [errorMsg, setErrorMsg] = useState('');

  const handleSave = async () => {
    if (!cookie.trim()) return;
    setStatus('loading');
    setErrorMsg('');
    try {
      const res = await fetch('/api/platform/sherlock/cookie', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cookie: cookie.trim() }),
      });
      if (res.ok) {
        setStatus('success');
        setTimeout(() => {
          onSuccess();
          onClose();
          setCookie('');
          setStatus('idle');
        }, 1500);
      } else {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || 'Failed to update cookie');
      }
    } catch (e: any) {
      setStatus('error');
      setErrorMsg(e.message || 'Unknown error');
    }
  };

  return (
    <AnimatePresence>
      {isOpen && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          className="fixed inset-0 z-50 flex items-center justify-center p-4"
        >
          {/* Backdrop */}
          <div className="absolute inset-0 bg-black/70 backdrop-blur-md" onClick={onClose} />

          <motion.div
            initial={{ scale: 0.95, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            exit={{ scale: 0.95, opacity: 0 }}
            transition={{ type: 'spring', stiffness: 300, damping: 24 }}
            className="relative w-full max-w-xl bg-[#0a0f1d] border border-amber-500/20 rounded-3xl shadow-2xl shadow-amber-500/5 overflow-hidden"
          >
            {/* Accent top bar */}
            <div className="h-1 w-full bg-gradient-to-r from-amber-500 via-orange-400 to-amber-500" />

            <div className="p-8">
              {/* Header */}
              <div className="flex items-start justify-between mb-8">
                <div>
                  <div className="flex items-center gap-2 mb-1">
                    <div className="p-1.5 bg-amber-500/10 rounded-lg border border-amber-500/20">
                      <Terminal size={14} className="text-amber-400" />
                    </div>
                    <span className="text-[10px] font-black uppercase tracking-[0.2em] text-amber-400">Action Required</span>
                  </div>
                  <h2 className="text-xl font-bold text-white tracking-tight">Refresh Sherlock Session</h2>
                  <p className="text-sm text-slate-500 mt-1">Your session has expired. Paste a fresh cookie to reconnect.</p>
                </div>
                <button onClick={onClose} className="p-2 hover:bg-slate-800 rounded-xl transition-colors text-slate-500 hover:text-white">
                  <X size={18} />
                </button>
              </div>

              {/* Steps */}
              <div className="space-y-3 mb-8">
                {STEPS.map(({ step, text, link, linkLabel }) => (
                  <div key={step} className="flex items-start gap-4">
                    <span className="text-[10px] font-black text-amber-500/60 font-mono mt-0.5 shrink-0">{step}</span>
                    <p className="text-sm text-slate-400 leading-relaxed">
                      {text}{' '}
                      {link && (
                        <a href={link} target="_blank" rel="noopener noreferrer" className="text-amber-400 hover:text-amber-300 inline-flex items-center gap-1 transition-colors">
                          {linkLabel} <ExternalLink size={10} />
                        </a>
                      )}
                    </p>
                  </div>
                ))}
              </div>

              {/* Cookie Input */}
              <div className="mb-6">
                <label className="block text-[10px] font-black text-slate-500 uppercase tracking-[0.2em] mb-2">
                  Paste Cookie Header Value
                </label>
                <textarea
                  id="sherlock-cookie-input"
                  value={cookie}
                  onChange={(e) => { setCookie(e.target.value); setStatus('idle'); }}
                  placeholder="session=abc123; _ga=GA1.1...; wagmi.store=..."
                  rows={4}
                  className="w-full bg-[#0F172A] border border-slate-700 text-slate-300 text-xs font-mono rounded-2xl p-4 resize-none focus:outline-none focus:border-amber-500/50 placeholder:text-slate-700 transition-all"
                />
              </div>

              {/* Error */}
              {status === 'error' && (
                <div className="flex items-center gap-2 mb-4 px-4 py-3 bg-red-500/10 border border-red-500/20 rounded-xl text-red-400 text-sm">
                  <AlertTriangle size={14} />
                  {errorMsg}
                </div>
              )}

              {/* Actions */}
              <div className="flex gap-3">
                <button
                  onClick={onClose}
                  className="flex-1 px-4 py-3 bg-slate-800/80 hover:bg-slate-800 text-slate-400 text-sm font-semibold rounded-2xl transition-all border border-slate-700/50"
                >
                  Cancel
                </button>
                <button
                  id="sherlock-save-cookie-btn"
                  onClick={handleSave}
                  disabled={!cookie.trim() || status === 'loading' || status === 'success'}
                  className="flex-[2] px-4 py-3 bg-amber-500 hover:bg-amber-400 disabled:opacity-50 text-black font-bold text-sm rounded-2xl transition-all flex items-center justify-center gap-2 shadow-xl shadow-amber-500/20"
                >
                  {status === 'loading' && <Loader2 size={16} className="animate-spin" />}
                  {status === 'success' && <CheckCircle2 size={16} />}
                  {status === 'success' ? 'Connected!' : status === 'loading' ? 'Saving...' : 'Save & Reconnect'}
                </button>
              </div>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
};
