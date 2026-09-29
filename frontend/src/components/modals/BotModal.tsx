import React, { useState, useEffect } from 'react';
import { motion } from 'motion/react';
import { CheckCircle2 } from 'lucide-react';
import { Bot } from '../../types';
import { useData } from '../../context/DataContext';

export const BotModal: React.FC<{
  isOpen: boolean;
  onClose: () => void;
  botToEdit?: Bot | null;
}> = ({ isOpen, onClose, botToEdit }) => {
  const { fetchBots, skills } = useData();
  const [newBot, setNewBot] = useState<{ name: string, platform: string, mode: 'auto' | 'review', skills: string[], status: Bot['status'] }>({ 
    name: '', platform: 'sherlock', mode: 'review', skills: [], status: 'idle'
  });

  useEffect(() => {
    if (botToEdit) {
      setNewBot({
        name: botToEdit.name,
        platform: (botToEdit.platform || '').toLowerCase(),
        mode: botToEdit.mode || 'review',
        skills: Array.isArray(botToEdit.skills) ? botToEdit.skills : [],
        status: botToEdit.status || 'idle'
      });
    } else {
      setNewBot({ name: '', platform: 'sherlock', mode: 'review', skills: [], status: 'idle' });
    }
  }, [botToEdit, isOpen]);

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const url = botToEdit ? `/api/bots/${botToEdit.id}` : '/api/bots';
    const method = botToEdit ? 'PATCH' : 'POST';
    
    const res = await fetch(url, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(newBot),
    });
    
    if (res.ok) {
      await fetchBots();
      onClose();
    }
  };

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
        <h3 className="text-xl font-bold text-white mb-6">{botToEdit ? 'Edit Bot Configuration' : 'Create New Audit Bot'}</h3>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-xs font-bold text-slate-500 uppercase tracking-wider mb-2">Bot Name</label>
            <input
              type="text" required value={newBot.name}
              onChange={e => setNewBot({ ...newBot, name: e.target.value })}
              className="w-full bg-slate-900 border border-slate-800 rounded-lg px-4 py-2.5 text-sm text-white focus:outline-none focus:border-pink-500 transition-colors"
            />
          </div>
          <div>
            <label className="block text-xs font-bold text-slate-500 uppercase tracking-wider mb-2">Target Platform</label>
            <select
              value={newBot.platform}
              onChange={e => setNewBot({ ...newBot, platform: e.target.value })}
              className="w-full bg-slate-900 border border-slate-800 rounded-lg px-4 py-2.5 text-sm text-white focus:outline-none focus:border-pink-500 transition-colors"
            >
              <option value="sherlock">Sherlock</option>
              <option value="cantina">Cantina</option>
              <option value="code4rena">Code4rena</option>
              <option value="immunefi">Immunefi</option>
            </select>
          </div>
          <div>
            <label className="block text-xs font-bold text-slate-500 uppercase tracking-wider mb-2">Operation Mode</label>
            <div className="flex bg-slate-900 rounded-lg border border-slate-800 p-1">
              <button
                type="button"
                onClick={() => setNewBot({ ...newBot, mode: 'review' })}
                className={`flex-1 py-1.5 text-xs font-bold uppercase rounded-md transition-colors ${newBot.mode === 'review' ? 'bg-slate-700 text-white' : 'text-slate-500 hover:text-slate-300'}`}
              >
                Review Mode
              </button>
              <button
                type="button"
                onClick={() => setNewBot({ ...newBot, mode: 'auto' })}
                className={`flex-1 py-1.5 text-xs font-bold uppercase rounded-md transition-colors ${newBot.mode === 'auto' ? 'bg-pink-500/20 text-pink-500' : 'text-slate-500 hover:text-slate-300'}`}
              >
                Auto Mode
              </button>
            </div>
          </div>
          <div>
            <label className="block text-xs font-bold text-slate-500 uppercase tracking-wider mb-2">AI Agent Skills</label>
            <div className="grid grid-cols-1 gap-2 max-h-[220px] overflow-y-auto pr-2">
              {skills.map(skill => {
                const isSelected = newBot.skills.includes(skill.name);
                return (
                  <div
                    key={skill.id}
                    onClick={() => {
                      if (isSelected) {
                        setNewBot({ ...newBot, skills: newBot.skills.filter(s => s !== skill.name) });
                      } else {
                        setNewBot({ ...newBot, skills: [...newBot.skills, skill.name] });
                      }
                    }}
                    className={`p-3 rounded-xl border-2 cursor-pointer transition-all flex items-start gap-3 ${isSelected ? 'bg-pink-500/10 border-pink-500/50' : 'bg-slate-900 border-slate-800 hover:border-slate-700'}`}
                  >
                    <div className={`mt-0.5 w-4 h-4 rounded-md border flex items-center justify-center shrink-0 transition-colors ${isSelected ? 'bg-pink-500 border-pink-500 text-[#020617]' : 'border-slate-600 bg-slate-800'}`}>
                      {isSelected && <CheckCircle2 size={12} strokeWidth={4} />}
                    </div>
                    <div>
                      <div className={`text-sm font-bold ${isSelected ? 'text-pink-500' : 'text-slate-300'}`}>{skill.name}</div>
                      <div className="text-xs text-slate-500 mt-0.5">{skill.description}</div>
                    </div>
                  </div>
                );
              })}
              {skills.length === 0 && (
                <div className="p-3 rounded-xl border border-dashed border-slate-800 bg-slate-900/50 text-xs text-slate-500">
                  No skills in the library yet.
                </div>
              )}
            </div>
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
              {botToEdit ? 'Save Config' : 'Deploy Bot'}
            </button>
          </div>
        </form>
      </motion.div>
    </div>
  );
};
