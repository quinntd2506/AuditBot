import React, { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { 
  XCircle, CheckCircle2, Loader2, Hourglass, PlayCircle, 
  AlertCircle, ShieldCheck, ShieldX, SkipForward 
} from 'lucide-react';
import { Issue, VerificationJob } from '../../types';

interface BatchVerificationModalProps {
  isOpen: boolean;
  onClose: () => void;
  jobId: string | null;
  issues: Issue[];
  onComplete: () => void;
}

export const BatchVerificationModal: React.FC<BatchVerificationModalProps> = ({ 
  isOpen, onClose, jobId, issues, onComplete 
}) => {
  const [job, setJob] = useState<VerificationJob | null>(null);
  const [polling, setPolling] = useState(false);

  useEffect(() => {
    if (isOpen && jobId) {
      setPolling(true);
      fetchJobStatus();
    } else {
      setPolling(false);
      setJob(null);
    }
  }, [isOpen, jobId]);

  useEffect(() => {
    let interval: number;
    if (polling && jobId && job?.status !== 'done' && job?.status !== 'failed') {
      interval = window.setInterval(fetchJobStatus, 3000);
    }
    return () => clearInterval(interval);
  }, [polling, jobId, job]);

  const fetchJobStatus = async () => {
    if (!jobId) return;
    try {
      const res = await fetch(`/api/jobs/${jobId}`);
      if (res.ok) {
        const data = await res.json();
        setJob(data);
        if (data.status === 'done' || data.status === 'failed') {
          setPolling(false);
          onComplete();
        }
      }
    } catch (e) {
      console.error('Failed to fetch job status:', e);
    }
  };

  if (!isOpen) return null;

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'queued': return <Hourglass size={16} className="text-slate-500" />;
      case 'verifying': return <Loader2 size={16} className="text-purple-400 animate-spin" />;
      case 'verified': return <ShieldCheck size={16} className="text-emerald-500" />;
      case 'unverified': return <ShieldX size={16} className="text-rose-500" />;
      case 'invalid': return <ShieldX size={16} className="text-rose-500" />;
      case 'unsupported': return <AlertCircle size={16} className="text-amber-400" />;
      case 'skipped': return <SkipForward size={16} className="text-slate-400" />;
      case 'failed': return <AlertCircle size={16} className="text-red-500" />;
      default: return <PlayCircle size={16} className="text-slate-500" />;
    }
  };

  const getStatusText = (status: string) => {
    switch (status) {
      case 'queued': return 'In Queue';
      case 'verifying': return 'Generating PoC...';
      case 'verified': return 'Verified (PoC Passed)';
      case 'unverified': return 'Not Verified';
      case 'invalid': return 'Invalid (PoC Failed)';
      case 'unsupported': return 'Unsupported Framework';
      case 'skipped': return 'Skipped (Triage)';
      case 'failed': return 'Error';
      default: return 'Starting...';
    }
  };

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4">
      <motion.div
        initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
        className="absolute inset-0 bg-black/90 backdrop-blur-md"
      />
      <motion.div
        initial={{ opacity: 0, scale: 0.9, y: 20 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.9, y: 20 }}
        className="relative w-full max-w-xl bg-[#0F172A] border border-slate-800 rounded-3xl shadow-2xl overflow-hidden flex flex-col"
      >
        <div className="p-6 border-b border-slate-800 flex justify-between items-center bg-slate-900/50">
          <div>
            <h3 className="text-xl font-bold text-white flex items-center gap-3">
              <RefreshCw size={20} className={`text-purple-500 ${polling ? 'animate-spin' : ''}`} />
              Agentic Verification
            </h3>
            <p className="text-xs text-slate-500 mt-1 uppercase tracking-widest font-semibold">
              Batch Job: {jobId?.substring(0, 8)}...
            </p>
          </div>
          {(!polling || job?.status === 'done' || job?.status === 'failed') && (
            <button onClick={onClose} className="text-slate-400 hover:text-white transition-colors">
              <XCircle size={24} />
            </button>
          )}
        </div>

        <div className="flex-1 overflow-y-auto p-6 space-y-3 max-h-[500px]">
          {issues.map(issue => {
            const result = job?.results[issue.id.toString()];
            const status = result?.status || 'queued';
            
            return (
              <div 
                key={issue.id}
                className={`p-4 rounded-2xl border transition-all duration-300 ${
                  status === 'verifying' ? 'bg-purple-500/10 border-purple-500/30' :
                  status === 'verified' ? 'bg-emerald-500/5 border-emerald-500/20' :
                  status === 'invalid' ? 'bg-rose-500/5 border-rose-500/20' :
                  status === 'unsupported' ? 'bg-amber-500/5 border-amber-500/20' :
                  'bg-slate-900/50 border-slate-800'
                }`}
              >
                <div className="flex justify-between items-start gap-4">
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-1">
                      <span className={`text-[10px] font-black uppercase tracking-tighter px-1.5 py-0.5 rounded border ${
                        issue.severity === 'High' ? 'text-red-500 border-red-500/20 bg-red-500/10' :
                        'text-orange-500 border-orange-500/20 bg-orange-500/10'
                      }`}>
                        {issue.severity}
                      </span>
                      <h4 className="text-sm font-bold text-white line-clamp-1">{issue.title}</h4>
                    </div>
                    {result?.reason && (
                      <p className="text-[10px] text-slate-500 italic mt-1">{result.reason}</p>
                    )}
                    {(result?.framework || result?.attempts || result?.command) && (
                      <p className="text-[10px] text-slate-600 mt-1 font-mono">
                        {result.framework && `framework=${result.framework}`}
                        {result.attempts ? ` attempts=${result.attempts}` : ''}
                        {result.command ? ` command=${result.command}` : ''}
                      </p>
                    )}
                    {result?.error && (
                      <p className="text-[10px] text-red-400 mt-1">{result.error}</p>
                    )}
                    {result?.stderr && ['invalid', 'unsupported', 'failed'].includes(status) && (
                      <pre className="mt-2 max-h-24 overflow-y-auto whitespace-pre-wrap text-[10px] text-slate-500 bg-slate-950/60 border border-slate-800 rounded-lg p-2">
                        {result.stderr}
                      </pre>
                    )}
                  </div>
                  <div className="flex flex-col items-end gap-1 shrink-0">
                    <div className="flex items-center gap-2 text-xs font-medium">
                      {getStatusIcon(status)}
                      <span className={
                        status === 'verified' ? 'text-emerald-500' :
                        status === 'verifying' ? 'text-purple-400' :
                        status === 'unverified' || status === 'invalid' ? 'text-rose-500' :
                        status === 'unsupported' ? 'text-amber-400' :
                        'text-slate-400'
                      }>
                        {getStatusText(status)}
                      </span>
                    </div>
                  </div>
                </div>
                
                {status === 'verifying' && (
                  <div className="mt-3 w-full bg-slate-800 h-1 rounded-full overflow-hidden">
                    <motion.div 
                      className="bg-purple-500 h-full"
                      animate={{ x: [-100, 400] }}
                      transition={{ repeat: Infinity, duration: 1.5, ease: "linear" }}
                    />
                  </div>
                )}
              </div>
            );
          })}
        </div>

        <div className="p-6 border-t border-slate-800 bg-slate-900/50 flex flex-col gap-4">
          <div className="flex justify-between items-center text-xs">
            <span className="text-slate-500 uppercase tracking-widest font-bold">Progress</span>
            <span className="text-white font-mono">
              {Object.values(job?.results || {}).filter(r => ['verified', 'unverified', 'invalid', 'unsupported', 'skipped', 'failed'].includes(r.status)).length} / {issues.length} Complete
            </span>
          </div>
          
          <div className="w-full bg-slate-800 h-2 rounded-full overflow-hidden">
            <motion.div 
              className="bg-purple-500 h-full"
              initial={{ width: 0 }}
              animate={{ 
                width: `${(Object.values(job?.results || {}).filter(r => ['verified', 'unverified', 'invalid', 'unsupported', 'skipped', 'failed'].includes(r.status)).length / issues.length) * 100}%`
              }}
            />
          </div>

          <div className="flex justify-end pt-2">
            <button
              onClick={onClose}
              className={`px-8 py-3 rounded-xl text-sm font-bold transition-all ${
                job?.status === 'done' || job?.status === 'failed'
                  ? 'bg-purple-600 hover:bg-purple-700 text-white shadow-lg shadow-purple-600/20'
                  : 'text-slate-500 hover:text-slate-300'
              }`}
            >
              {job?.status === 'done' || job?.status === 'failed' ? 'Done' : 'Run in Background'}
            </button>
          </div>
        </div>
      </motion.div>
    </div>
  );
};

const RefreshCw = ({ size, className }: { size: number, className?: string }) => (
  <svg 
    width={size} height={size} viewBox="0 0 24 24" fill="none" 
    stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" 
    className={className}
  >
    <path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8" />
    <path d="M21 3v5h-5" />
    <path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16" />
    <path d="M3 21v-5h5" />
  </svg>
);
