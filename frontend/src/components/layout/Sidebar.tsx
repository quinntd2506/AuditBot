import React from 'react';
import { NavLink } from 'react-router-dom';
import {
  LayoutDashboard,
  Bot as BotIcon,
  ShieldAlert,
  Plus,
  MoreVertical,
  Library
} from 'lucide-react';
import { useData } from '../../context/DataContext';
import { Bot, Contest } from '../../types';

export const Sidebar: React.FC<{
  onAddBotClick: () => void;
  onDeleteBotClick: (bot: Bot) => void;
}> = ({ onAddBotClick, onDeleteBotClick }) => {
  const { bots, contests } = useData();

  const getBotDisplayStatus = (bot: Bot) => {
    if (bot.status === 'paused') return 'paused';
    const botContests = contests.filter(c => c.bot_id === bot.id);
    if (botContests.some(c => c.status === 'auditing')) return 'auditing';
    if (botContests.some(c => c.status === 'submitting')) return 'submitting';
    if (botContests.some(c => c.status === 'active')) return 'active';
    return 'idle';
  };

  const getBotStatusBg = (status: string) => {
    switch (status) {
      case 'paused': return 'bg-orange-500';
      case 'auditing': return 'bg-blue-500 animate-pulse';
      case 'submitting': return 'bg-purple-500 animate-pulse';
      case 'active': return 'bg-green-500';
      default: return 'bg-slate-500';
    }
  };

  const navLinkClass = ({ isActive }: { isActive: boolean }) =>
    `flex items-center gap-3 px-3 py-2 rounded-lg transition-colors ${
      isActive ? 'bg-pink-500/10 text-pink-500' : 'hover:bg-slate-800/50 text-slate-400'
    }`;

  return (
    <aside className="fixed left-0 top-0 bottom-0 w-64 bg-[#0F172A] border-r border-slate-800/50 p-6 hidden md:flex flex-col gap-8 z-20">
      <div className="flex items-center gap-3 px-2">
        <div className="w-8 h-8 bg-pink-500 rounded-lg flex items-center justify-center">
          <ShieldAlert className="text-black w-5 h-5" />
        </div>
        <h1 className="text-white font-bold tracking-tight text-lg">AuditBot AI</h1>
      </div>

      <nav className="flex flex-col gap-1">
        <NavLink to="/" className={navLinkClass}>
          <LayoutDashboard size={18} />
          <span className="font-medium text-sm">Dashboard</span>
        </NavLink>
        <NavLink to="/contests" className={navLinkClass}>
          <ShieldAlert size={18} />
          <span className="font-medium text-sm">All Audits</span>
        </NavLink>
        <NavLink to="/skills" className={navLinkClass}>
          <Library size={18} />
          <span className="font-medium text-sm">Skills Hub</span>
        </NavLink>

        <div className="mt-4 px-3 text-[10px] font-bold text-slate-500 uppercase tracking-widest">Active Bots</div>
        {bots.map(bot => (
          <div key={bot.id} className="group flex items-center justify-between rounded-lg transition-colors hover:bg-slate-800/50">
            <NavLink
              to={`/bots/${bot.id}`}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-2 flex-1 text-left ${isActive ? 'text-pink-500' : 'text-slate-400'}`
              }
            >
              <div className="relative">
                <BotIcon size={18} />
                <div className={`absolute -bottom-1 -right-1 w-2.5 h-2.5 rounded-full border-2 border-[#0F172A] ${getBotStatusBg(getBotDisplayStatus(bot))}`} />
              </div>
              <span className="font-medium text-sm truncate">{bot.name}</span>
            </NavLink>
            <button
              onClick={(e) => {
                e.stopPropagation();
                onDeleteBotClick(bot);
              }}
              className="p-2 transition-opacity opacity-0 group-hover:opacity-100 hover:text-white"
              title="Delete Bot"
            >
              <MoreVertical size={14} />
            </button>
          </div>
        ))}
      </nav>

      <div className="mt-auto">
        <button
          onClick={onAddBotClick}
          className="w-full flex items-center justify-center gap-2 bg-slate-800 hover:bg-slate-700 text-white py-2 rounded-lg transition-all text-sm font-medium border border-slate-700"
        >
          <Plus size={16} />
          New Bot
        </button>
      </div>
    </aside>
  );
};
