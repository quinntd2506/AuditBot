import React from 'react';
import { motion } from 'motion/react';
import { Trophy, ShieldAlert, CheckCircle2 } from 'lucide-react';
import { NavLink } from 'react-router-dom';
import { useData } from '../context/DataContext';
import { ContestTable } from '../components/ContestTable';

export const DashboardPage: React.FC = () => {
  const { bots, contests } = useData();

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -10 }}
      className="space-y-8"
    >
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <div className="bg-[#0F172A] border border-slate-800/50 p-6 rounded-2xl">
          <div className="flex items-center justify-between mb-4">
            <div className="p-2 bg-pink-500/10 rounded-lg text-pink-500">
              <Trophy size={20} />
            </div>
            <span className="text-[10px] font-bold text-slate-500 uppercase tracking-wider">Total Earnings</span>
          </div>
          <div className="text-3xl font-bold text-white tracking-tight">$12,450.00</div>
          <div className="mt-2 text-xs text-pink-500 flex items-center gap-1">
            <span>+12% from last month</span>
          </div>
        </div>
        <div className="bg-[#0F172A] border border-slate-800/50 p-6 rounded-2xl">
          <div className="flex items-center justify-between mb-4">
            <div className="p-2 bg-blue-500/10 rounded-lg text-blue-500">
              <ShieldAlert size={20} />
            </div>
            <span className="text-[10px] font-bold text-slate-500 uppercase tracking-wider">Active Audits</span>
          </div>
          <div className="text-3xl font-bold text-white tracking-tight">{contests.filter(c => c.status === 'active').length}</div>
          <div className="mt-2 text-xs text-slate-500">Across {bots.length} platforms</div>
        </div>
        <div className="bg-[#0F172A] border border-slate-800/50 p-6 rounded-2xl">
          <div className="flex items-center justify-between mb-4">
            <div className="p-2 bg-purple-500/10 rounded-lg text-purple-500">
              <CheckCircle2 size={20} />
            </div>
            <span className="text-[10px] font-bold text-slate-500 uppercase tracking-wider">Valid Issues</span>
          </div>
          <div className="text-3xl font-bold text-white tracking-tight">42</div>
          <div className="mt-2 text-xs text-slate-500">89% Accuracy rate</div>
        </div>
      </div>

      <section>
        <div className="flex items-center justify-between mb-6">
          <h2 className="text-lg font-semibold text-white">Recent Contests</h2>
          <NavLink to="/contests" className="text-xs text-pink-500 hover:underline">View All</NavLink>
        </div>
        <ContestTable contests={contests.slice(0, 10)} />
      </section>
    </motion.div>
  );
};
