import React, { useState } from 'react';
import { motion } from 'motion/react';
import { useData } from '../../context/DataContext';

export const ContestModal: React.FC<{
  isOpen: boolean;
  onClose: () => void;
  botId?: number | null;
}> = ({ isOpen, onClose, botId }) => {
  const { fetchContests, bots } = useData();
  const [newContest, setNewContest] = useState<{ title: string, platform_url: string, file: File | null, bot_id: number }>({ 
    title: '', platform_url: '', file: null, bot_id: botId || 0 
  });

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const formData = new FormData();
    formData.append('title', newContest.title);
    formData.append('platform_url', newContest.platform_url);
    if (newContest.bot_id) formData.append('bot_id', newContest.bot_id.toString());
    if (newContest.file) formData.append('file', newContest.file);

    const res = await fetch('/api/contests', {
      method: 'POST',
      body: formData,
    });
    if (res.ok) {
      await fetchContests();
      onClose();
    }
  };

  const selectedBotName = bots.find(b => b.id === newContest.bot_id)?.name;

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
        className="relative w-full max-w-md bg-[#0F172A] border border-slate-800 rounded-2xl p-8 shadow-2xl"
      >
        <h3 className="text-xl font-bold text-white mb-6">Add Contest {selectedBotName ? `to ${selectedBotName}` : ''}</h3>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-xs font-bold text-slate-500 uppercase tracking-wider mb-2">Contest Title</label>
            <input
              type="text" required value={newContest.title}
              onChange={e => setNewContest({ ...newContest, title: e.target.value })}
              placeholder="e.g. Aave V3 Audit"
              className="w-full bg-slate-900 border border-slate-800 rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:border-pink-500 transition-colors text-white"
            />
          </div>
          <div>
            <label className="block text-xs font-bold text-slate-500 uppercase tracking-wider mb-2">Contest Link</label>
            <input
              type="url" required value={newContest.platform_url}
              onChange={e => setNewContest({ ...newContest, platform_url: e.target.value })}
              placeholder="https://audits.sherlock.xyz/contests/..."
              className="w-full bg-slate-900 border border-slate-800 rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:border-pink-500 transition-colors text-white"
            />
          </div>
          <div>
            <label className="block text-xs font-bold text-slate-500 uppercase tracking-wider mb-2">Codebase Archive (.zip)</label>
            <input
              type="file" accept=".zip" required
              onChange={e => setNewContest({ ...newContest, file: e.target.files?.[0] || null })}
              className="w-full bg-slate-900 border border-slate-800 rounded-lg px-4 py-2.5 text-sm text-slate-400 file:mr-4 file:py-1.5 file:px-4 file:rounded-lg file:border-0 file:text-sm file:font-bold file:bg-pink-500 file:text-black hover:file:bg-pink-600 cursor-pointer file:cursor-pointer transition-colors"
            />
          </div>
          <div className="pt-4 flex gap-3">
            <button
              type="button" onClick={onClose}
              className="flex-1 px-4 py-2.5 rounded-lg text-sm font-bold text-slate-400 hover:bg-slate-800 transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              className="flex-1 px-4 py-2.5 rounded-lg text-sm font-bold bg-pink-500 text-black hover:bg-pink-600 transition-colors"
            >
              Start Auditing
            </button>
          </div>
        </form>
      </motion.div>
    </div>
  );
};
