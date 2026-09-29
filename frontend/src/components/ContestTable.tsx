import React from 'react';
import { useNavigate } from 'react-router-dom';
import { Calendar, Loader2, ExternalLink, ChevronRight } from 'lucide-react';
import { Contest } from '../types';

export const ContestTable: React.FC<{ contests: Contest[] }> = ({ contests }) => {
  const navigate = useNavigate();

  return (
    <div className="bg-[#0F172A] border border-slate-800/50 rounded-2xl overflow-x-auto">
      <table className="w-full text-left text-sm whitespace-nowrap min-w-[600px]">
        <thead className="bg-slate-900/50 border-b border-slate-800/50">
          <tr>
            <th className="px-6 py-4 font-medium text-slate-400">Contest</th>
            <th className="px-6 py-4 font-medium text-slate-400">Platform</th>
            <th className="px-6 py-4 font-medium text-slate-400">Status</th>
            <th className="px-6 py-4 font-medium text-slate-400">Earnings</th>
            <th className="px-6 py-4 font-medium text-slate-400"></th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-800/50">
          {contests.map(contest => (
            <tr 
              key={contest.id} 
              className="hover:bg-slate-800/20 transition-colors group cursor-pointer" 
              onClick={() => navigate(`/contests/${contest.id}`)}
            >
              <td className="px-6 py-4">
                <div className="font-medium text-white">{contest.title}</div>
                <div className="text-xs text-slate-500 flex items-center gap-1.5 mt-1">
                  <Calendar size={12} className="text-slate-600" />
                  {contest.starts_at ? new Date(contest.starts_at).toLocaleDateString() : 'TBA'} 
                  <span className="text-slate-700 mx-0.5">→</span> 
                  {contest.ends_at ? new Date(contest.ends_at).toLocaleDateString() : 'TBA'}
                </div>
              </td>
              <td className="px-6 py-4">
                <span className={`px-2 py-1 rounded text-[10px] font-bold uppercase tracking-wider ${
                  contest.synced_from === 'immunefi'   ? 'bg-amber-500/20 text-amber-400 border border-amber-500/20' :
                  contest.synced_from === 'code4rena'  ? 'bg-pink-500/20 text-pink-400 border border-pink-500/20' :
                  contest.synced_from === 'sherlock'   ? 'bg-blue-500/20 text-blue-400 border border-blue-500/20' :
                  contest.synced_from === 'guardian'   ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/20' :
                  'bg-slate-800 text-slate-400'
                }`}>
                  {contest.synced_from} &middot; {contest.bot_name}
                </span>
              </td>
              <td className="px-6 py-4">
                <div className="flex items-center gap-2">
                  <div className={`w-1.5 h-1.5 rounded-full ${
                    contest.status === 'active'   ? 'bg-pink-500 animate-pulse' :
                    contest.status === 'upcoming' ? 'bg-amber-400' :
                    contest.status === 'auditing' ? 'bg-blue-500 animate-pulse' :
                    'bg-slate-600'
                  }`} />
                  <span className={`capitalize flex items-center gap-1 ${
                    contest.status === 'auditing' ? 'text-blue-500 font-bold' :
                    contest.status === 'upcoming' ? 'text-amber-400 font-semibold' :
                    ''
                  }`}>
                    {contest.status === 'auditing' && <Loader2 size={12} className="animate-spin inline" />}
                    {contest.status}
                  </span>
                </div>
              </td>
              <td className="px-6 py-4 font-mono text-pink-500">
                ${contest.earnings.toLocaleString()}
              </td>
              <td className="px-6 py-4 text-right">
                <div className="flex items-center justify-end gap-2">
                  {contest.platform_url && (
                    <a
                      href={contest.platform_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      onClick={(e) => e.stopPropagation()}
                      className="p-1.5 hover:bg-slate-800 rounded-lg text-slate-500 hover:text-pink-500 transition-colors"
                      title="View on Platform"
                    >
                      <ExternalLink size={16} />
                    </a>
                  )}
                  <ChevronRight className="inline-block text-slate-600 group-hover:text-white transition-colors" size={16} />
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};
