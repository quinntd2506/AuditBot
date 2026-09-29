import React from 'react';
import { motion } from 'motion/react';
import { useParams } from 'react-router-dom';
import { Play, Pause, Plus, Code2, Calendar, Coins, ExternalLink, ChevronRight, Loader2 } from 'lucide-react';
import { useData } from '../context/DataContext';
import { ContestTable } from '../components/ContestTable';

export const BotPage: React.FC<{
  onEditBot: (bot: any) => void;
  onAddContest: (botId: number) => void;
}> = ({ onEditBot, onAddContest }) => {
  const { id } = useParams();
  const { bots, contests, fetchBots } = useData();
  const selectedBot = bots.find(b => b.id === Number(id));

  if (!selectedBot) return <div className="text-slate-500">Bot not found.</div>;

  const handleToggleBotStatus = async () => {
    const newStatus = selectedBot.status === 'paused' ? 'idle' : 'paused';
    const res = await fetch(`/api/bots/${selectedBot.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...selectedBot, status: newStatus }),
    });
    if (res.ok) fetchBots();
  };

  const getBotDisplayStatus = () => {
    if (selectedBot.status === 'paused') return 'paused';
    const botContests = contests.filter(c => c.bot_id === selectedBot.id);
    if (botContests.some(c => c.status === 'auditing')) return 'auditing';
    if (botContests.some(c => c.status === 'submitting')) return 'submitting';
    if (botContests.some(c => c.status === 'active')) return 'active';
    return 'idle';
  };

  const getBotStatusColor = (status: string) => {
    switch (status) {
      case 'paused': return 'text-orange-500';
      case 'auditing': return 'text-blue-500';
      case 'submitting': return 'text-purple-500';
      case 'active': return 'text-green-500';
      default: return 'text-slate-500';
    }
  };

  const botContests = contests.filter(c => c.bot_id === selectedBot.id);

  return (
    <motion.div
      initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -20 }}
      className="space-y-8"
    >
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold text-white">{selectedBot.name}</h2>
          <p className="text-slate-500 text-sm flex items-center gap-2 mt-1">
            <span>Platform: {selectedBot.platform}</span>
            <span>&bull;</span>
            <span>Mode: <span className="uppercase text-pink-500 font-bold">{selectedBot.mode || 'review'}</span></span>
            <span>&bull;</span>
            <span>Status:
              <span className={`uppercase font-bold inline-flex items-center gap-1 ml-1 ${getBotStatusColor(getBotDisplayStatus())}`}>
                {getBotDisplayStatus() === 'auditing' && <Loader2 size={12} className="animate-spin" />}
                {getBotDisplayStatus()}
              </span>
            </span>
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={handleToggleBotStatus}
            className={`flex items-center gap-2 px-4 py-2 rounded-lg font-semibold transition-all border ${selectedBot.status === 'paused' ? 'bg-orange-500/10 text-orange-500 border-orange-500/20 hover:bg-orange-500/20' : 'bg-slate-800 text-white border-slate-700 hover:bg-slate-700'}`}
          >
            {selectedBot.status === 'paused' ? <Play size={18} /> : <Pause size={18} />}
            {selectedBot.status === 'paused' ? 'Resume Bot' : 'Pause Bot'}
          </button>
          <button
            onClick={() => onEditBot(selectedBot)}
            className="flex items-center gap-2 bg-slate-800 hover:bg-slate-700 text-white px-4 py-2 rounded-lg font-semibold transition-all border border-slate-700"
          >
            Edit Config
          </button>
          <button
            onClick={() => onAddContest(selectedBot.id)}
            className="flex items-center gap-2 bg-pink-500 hover:bg-pink-600 text-black px-4 py-2 rounded-lg font-semibold transition-all"
          >
            <Plus size={18} />
            Add Contest
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {botContests.length === 0 && (
          <div className="md:col-span-2 lg:col-span-3 bg-slate-900/30 border border-dashed border-slate-800 rounded-2xl p-12 text-center">
            <div className="w-12 h-12 bg-slate-800 rounded-full flex items-center justify-center mx-auto mb-4">
              <Code2 className="text-slate-500" size={24} />
            </div>
            <h4 className="text-white font-medium mb-1">No active contests found</h4>
            <p className="text-sm text-slate-500 mb-6">This bot is not tracking any repositories yet.</p>
            <button
              onClick={() => onAddContest(selectedBot.id)}
              className="inline-flex items-center gap-2 bg-slate-800 hover:bg-slate-700 text-white px-4 py-2 rounded-lg font-bold transition-all text-sm border border-slate-700"
            >
              <Plus size={16} /> Add First Contest
            </button>
          </div>
        )}
      </div>
      
      {botContests.length > 0 && <ContestTable contests={botContests} />}
    </motion.div>
  );
};
