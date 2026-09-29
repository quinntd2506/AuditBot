import React from 'react';
import { motion } from 'motion/react';
import { XCircle, CheckCircle2 } from 'lucide-react';
import { Issue } from '../../types';

export const PreviewSubmitModal: React.FC<{
  isOpen: boolean;
  onClose: () => void;
  issues: Issue[];
  selectedIssueIds: number[];
  onSubmit: () => void;
}> = ({ isOpen, onClose, issues, selectedIssueIds, onSubmit }) => {
  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <motion.div
        initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
        onClick={onClose}
        className="absolute inset-0 bg-black/80 backdrop-blur-sm"
      />
      <motion.div
        initial={{ opacity: 0, scale: 0.95, y: 20 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.95, y: 20 }}
        className="relative w-full max-w-2xl max-h-[80vh] flex flex-col bg-[#0F172A] border border-slate-800 rounded-2xl shadow-2xl"
      >
        <div className="p-6 border-b border-slate-800 flex justify-between items-center">
          <h3 className="text-xl font-bold text-white">Review Submission Report</h3>
          <button onClick={onClose} className="text-slate-400 hover:text-white">
            <XCircle size={24} />
          </button>
        </div>
        <div className="p-6 overflow-y-auto flex-1 font-mono text-xs text-slate-300 space-y-4">
          {issues.filter(i => selectedIssueIds.includes(i.id)).map(issue => (
            <div key={issue.id} className="pb-4 border-b border-slate-800 border-dashed last:border-0">
              <div className="text-pink-500 font-bold mb-2">### [{issue.severity}] {issue.title}</div>
              <div className="text-slate-400 mb-2">**Severity:** {issue.severity}</div>
              <div className="text-slate-400 mb-2">**Context:** `{issue.classification}`</div>
              <div className="whitespace-pre-wrap">{issue.description}</div>
            </div>
          ))}
        </div>
        <div className="p-6 border-t border-slate-800 flex justify-end gap-3">
          <button
            type="button" onClick={onClose}
            className="px-6 py-2.5 rounded-lg text-sm font-bold text-slate-400 hover:bg-slate-800 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={onSubmit}
            className="px-6 py-2.5 rounded-lg text-sm font-bold bg-pink-500 text-black hover:bg-pink-600 transition-colors flex items-center gap-2"
          >
            <CheckCircle2 size={18} />
            Auto Submit to Platform
          </button>
        </div>
      </motion.div>
    </div>
  );
};
