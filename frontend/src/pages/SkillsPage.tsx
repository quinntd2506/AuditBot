import React from 'react';
import { motion } from 'motion/react';
import { Plus, BrainCircuit, XCircle } from 'lucide-react';
import { useData } from '../context/DataContext';

export const SkillsPage: React.FC<{
  onAddSkill: () => void;
  onEditSkill: (skill: any) => void;
}> = ({ onAddSkill, onEditSkill }) => {
  const { skills, fetchSkills } = useData();

  const handleDelete = async (e: React.MouseEvent, id: number) => {
    e.stopPropagation();
    if(confirm("Are you sure you want to delete this custom skill?")) {
      await fetch(`/api/skills/${id}`, { method: 'DELETE' });
      fetchSkills();
    }
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -10 }}
      className="space-y-8"
    >
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold text-white">Skills Library</h2>
          <p className="text-slate-500 text-sm mt-1">Manage physical SKILL.md rules generated into .codex/skills automatically.</p>
        </div>
        <button
          onClick={onAddSkill}
          className="flex items-center gap-2 bg-pink-500 hover:bg-pink-600 text-black px-4 py-2 rounded-lg font-semibold transition-all"
        >
          <Plus size={18} />
          New Skill
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {skills.map(skill => (
          <div 
            key={skill.id} 
            onClick={() => onEditSkill(skill)}
            className="bg-[#0F172A] border border-slate-800/50 p-6 rounded-2xl flex flex-col hover:border-pink-500/50 hover:ring-2 hover:ring-pink-500/20 cursor-pointer transition-all"
          >
            <div className="flex items-start justify-between mb-4">
              <div className="p-3 bg-slate-900 rounded-xl">
                <BrainCircuit className="text-pink-500" size={24} />
              </div>
              <button onClick={(e) => handleDelete(e, skill.id)} className="text-slate-600 hover:text-red-500 p-1 transition-colors" title="Delete Skill">
                <XCircle size={16} />
              </button>
            </div>
            <h3 className="text-lg font-bold text-white mb-2">{skill.name}</h3>
            <p className="text-sm text-slate-400 mb-6 flex-1">{skill.description}</p>
            <div className="flex flex-wrap gap-1.5 mb-4">
              {(skill.phases || []).map((phase: string) => (
                <span key={phase} className="px-2 py-1 rounded-md bg-pink-500/10 text-pink-300 text-[10px] font-bold uppercase">
                  {phase}
                </span>
              ))}
              {skill.gate && (
                <span className="px-2 py-1 rounded-md bg-slate-800 text-slate-300 text-[10px] font-bold uppercase">
                  {skill.gate.replaceAll('_', ' ')}
                </span>
              )}
            </div>
            <div className="text-[10px] font-mono text-slate-600 border-t border-slate-800/50 pt-4 mt-auto">
              Output: .codex/skills/{skill.name}/SKILL.md
            </div>
          </div>
        ))}
        {skills.length === 0 && (
            <div className="col-span-full text-slate-500 flex justify-center py-10">No skills found. Create one.</div>
        )}
      </div>
    </motion.div>
  );
};
