import React, { useState, useEffect, useCallback } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { AlertTriangle, RefreshCw, X } from 'lucide-react';
import { SherlockCookieModal } from './modals/SherlockCookieModal';

export const SherlockCookieBanner: React.FC = () => {
  const [cookieValid, setCookieValid] = useState<boolean | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const [isModalOpen, setModalOpen] = useState(false);

  const checkStatus = useCallback(async () => {
    try {
      const res = await fetch('/api/platform/status');
      if (!res.ok) return;
      const data = await res.json();
      const sherlock = data?.sherlock;
      if (!sherlock) return;

      // cookie_valid is false: session expired
      // authenticated is false: no cookie at all
      const valid = sherlock.cookie_valid === true;
      setCookieValid(valid);

    } catch {
      // API unreachable — don't show banner
    }
  }, []);

  useEffect(() => {
    checkStatus();
    // Poll every 5 minutes
    const interval = setInterval(checkStatus, 5 * 60 * 1000);
    return () => clearInterval(interval);
  }, [checkStatus]);

  // Don't show if: status unknown, cookie is valid, or user dismissed
  if (cookieValid === null || cookieValid === true || dismissed) return null;

  return (
    <>
      <AnimatePresence>
        <motion.div
          initial={{ opacity: 0, y: -16 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -16 }}
          transition={{ type: 'spring', stiffness: 400, damping: 30 }}
          className="fixed top-0 left-0 right-0 z-40 flex items-center justify-between gap-4 px-6 py-3 bg-amber-500/10 border-b border-amber-500/20 backdrop-blur-sm"
          id="sherlock-cookie-banner"
        >
          <div className="flex items-center gap-3 min-w-0">
            <div className="p-1.5 bg-amber-500/20 rounded-lg shrink-0">
              <AlertTriangle size={14} className="text-amber-400" />
            </div>
            <p className="text-sm text-amber-200 truncate">
              <span className="font-semibold">Sherlock session expired</span>
              <span className="text-amber-400/70 ml-1.5">— the bot cannot join contests or submit issues.</span>
            </p>
          </div>

          <div className="flex items-center gap-2 shrink-0">
            <button
              id="sherlock-update-cookie-btn"
              onClick={() => setModalOpen(true)}
              className="flex items-center gap-1.5 px-4 py-1.5 bg-amber-500 hover:bg-amber-400 text-black text-xs font-bold rounded-xl transition-all shadow-lg shadow-amber-500/20"
            >
              <RefreshCw size={12} />
              Update Cookie
            </button>
            <button
              onClick={() => setDismissed(true)}
              className="p-1.5 hover:bg-amber-500/10 rounded-lg transition-colors text-amber-500/50 hover:text-amber-400"
              title="Dismiss"
            >
              <X size={14} />
            </button>
          </div>
        </motion.div>
      </AnimatePresence>

      <SherlockCookieModal
        isOpen={isModalOpen}
        onClose={() => setModalOpen(false)}
        onSuccess={() => {
          setCookieValid(true);
          // Re-check after a short delay to confirm
          setTimeout(checkStatus, 3000);
        }}
      />
    </>
  );
};
