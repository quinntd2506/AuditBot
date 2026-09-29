import React, { useState, useEffect } from 'react';
import { motion } from 'motion/react';
import { XCircle } from 'lucide-react';
import { Skill } from '../../types';
import { useData } from '../../context/DataContext';

type SkillPhase = 'triage' | 'poc' | 'report';

type SkillDraft = {
  name: string;
  description: string;
  content: string;
  phases: SkillPhase[];
  frameworks: string[];
  platforms: string[];
  gate: string;
};

const PHASE_OPTIONS: { value: SkillPhase; label: string }[] = [
  { value: 'triage', label: 'Triage' },
  { value: 'poc', label: 'PoC' },
  { value: 'report', label: 'Report' },
];

const FRAMEWORK_OPTIONS = [
  { value: 'foundry', label: 'Foundry' },
  { value: 'hardhat', label: 'Hardhat' },
  { value: 'cargo-soroban', label: 'Cargo/Soroban' },
  { value: 'cairo', label: 'Cairo' },
  { value: 'move', label: 'Move' },
];

const PLATFORM_OPTIONS = [
  { value: 'sherlock', label: 'Sherlock' },
  { value: 'guardian', label: 'Guardian' },
  { value: 'code4rena', label: 'Code4rena' },
  { value: 'cantina', label: 'Cantina' },
  { value: 'immunefi', label: 'Immunefi' },
];

const GATE_OPTIONS = [
  { value: '', label: 'None' },
  { value: 'foundry_mainnet_fork', label: 'Foundry mainnet fork' },
];

const DEFAULT_PHASES: SkillPhase[] = ['triage', 'poc', 'report'];

const normalizeSkillDraft = (skill?: Skill | null): SkillDraft => ({
  name: skill?.name || '',
  description: skill?.description || '',
  content: skill?.content || '',
  phases: Array.isArray(skill?.phases) && skill.phases.length ? skill.phases : DEFAULT_PHASES,
  frameworks: Array.isArray(skill?.frameworks) ? skill.frameworks : [],
  platforms: Array.isArray(skill?.platforms) ? skill.platforms : [],
  gate: skill?.gate || '',
});

const toggleValue = <T extends string,>(values: T[], value: T): T[] => (
  values.includes(value) ? values.filter(v => v !== value) : [...values, value]
);

const togglePhase = (values: SkillPhase[], value: SkillPhase): SkillPhase[] => {
  if (values.includes(value) && values.length === 1) return values;
  return toggleValue(values, value);
};

export const SkillModal: React.FC<{
  isOpen: boolean;
  onClose: () => void;
  skillToEdit?: Skill | null;
}> = ({ isOpen, onClose, skillToEdit }) => {
  const { fetchSkills } = useData();
  const [skill, setSkill] = useState<SkillDraft>(normalizeSkillDraft());

  useEffect(() => {
    setSkill(normalizeSkillDraft(skillToEdit));
  }, [skillToEdit, isOpen]);

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const url = skillToEdit ? `/api/skills/${skillToEdit.id}` : '/api/skills';
    const method = skillToEdit ? 'PATCH' : 'POST';

    const res = await fetch(url, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(skill)
    });
    if (res.ok) {
      await fetchSkills();
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
        className="relative w-full max-w-2xl max-h-[90vh] overflow-y-auto bg-[#0F172A] border border-slate-800 rounded-2xl p-8 shadow-2xl"
      >
        <div className="flex items-center justify-between mb-6">
          <h3 className="text-xl font-bold text-white">{skillToEdit ? 'Edit Agent Skill' : 'Create New Agent Skill'}</h3>
          <button onClick={onClose} className="text-slate-500 hover:text-white transition-colors">
            <XCircle size={24} />
          </button>
        </div>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-xs font-bold text-slate-500 uppercase tracking-wider mb-2">Skill Name (kebab-case)</label>
            <input
              required type="text"
              className="w-full bg-slate-900 border border-slate-800 rounded-lg px-4 py-2.5 text-sm text-white focus:outline-none focus:border-pink-500 transition-colors placeholder-slate-600"
              placeholder="e.g. solana-auditor"
              value={skill.name}
              onChange={(e) => setSkill({...skill, name: e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, '')})}
            />
          </div>
          <div>
            <label className="block text-xs font-bold text-slate-500 uppercase tracking-wider mb-2">Description</label>
            <input
              required type="text"
              className="w-full bg-slate-900 border border-slate-800 rounded-lg px-4 py-2.5 text-sm text-white focus:outline-none focus:border-pink-500 transition-colors placeholder-slate-600"
              placeholder="What does this skill do and when to use it?"
              value={skill.description}
              onChange={(e) => setSkill({...skill, description: e.target.value})}
            />
          </div>
          <div>
            <label className="block text-xs font-bold text-slate-500 uppercase tracking-wider mb-2">Markdown Instructions (Body)</label>
            <textarea
              required rows={8}
              className="w-full bg-slate-900 border border-slate-800 rounded-lg px-4 py-3 text-white font-mono text-xs focus:outline-none focus:border-pink-500 transition-colors placeholder-slate-600"
              placeholder="# Instructions\n\nWrite your agent workflow and custom prompting here..."
              value={skill.content}
              onChange={(e) => setSkill({...skill, content: e.target.value})}
            />
          </div>
          <div>
            <label className="block text-xs font-bold text-slate-500 uppercase tracking-wider mb-2">Prompt Phases</label>
            <div className="grid grid-cols-3 gap-2">
              {PHASE_OPTIONS.map(option => {
                const selected = skill.phases.includes(option.value);
                return (
                  <button
                    key={option.value}
                    type="button"
                    onClick={() => setSkill({ ...skill, phases: togglePhase(skill.phases, option.value) })}
                    className={`px-3 py-2 rounded-lg border text-xs font-bold transition-colors ${selected ? 'bg-pink-500/20 border-pink-500/50 text-pink-300' : 'bg-slate-900 border-slate-800 text-slate-500 hover:text-slate-300'}`}
                  >
                    {option.label}
                  </button>
                );
              })}
            </div>
          </div>
          <div>
            <label className="block text-xs font-bold text-slate-500 uppercase tracking-wider mb-2">Advanced Gate</label>
            <select
              value={skill.gate}
              onChange={(e) => setSkill({ ...skill, gate: e.target.value })}
              className="w-full bg-slate-900 border border-slate-800 rounded-lg px-4 py-2.5 text-sm text-white focus:outline-none focus:border-pink-500 transition-colors"
            >
              {GATE_OPTIONS.map(option => (
                <option key={option.value || 'none'} value={option.value}>{option.label}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs font-bold text-slate-500 uppercase tracking-wider mb-2">Framework Filters</label>
            <div className="flex flex-wrap gap-2">
              {FRAMEWORK_OPTIONS.map(option => {
                const selected = skill.frameworks.includes(option.value);
                return (
                  <button
                    key={option.value}
                    type="button"
                    onClick={() => setSkill({ ...skill, frameworks: toggleValue(skill.frameworks, option.value) })}
                    className={`px-3 py-1.5 rounded-lg border text-xs font-semibold transition-colors ${selected ? 'bg-slate-700 border-slate-500 text-white' : 'bg-slate-900 border-slate-800 text-slate-500 hover:text-slate-300'}`}
                  >
                    {option.label}
                  </button>
                );
              })}
            </div>
          </div>
          <div>
            <label className="block text-xs font-bold text-slate-500 uppercase tracking-wider mb-2">Platform Filters</label>
            <div className="flex flex-wrap gap-2">
              {PLATFORM_OPTIONS.map(option => {
                const selected = skill.platforms.includes(option.value);
                return (
                  <button
                    key={option.value}
                    type="button"
                    onClick={() => setSkill({ ...skill, platforms: toggleValue(skill.platforms, option.value) })}
                    className={`px-3 py-1.5 rounded-lg border text-xs font-semibold transition-colors ${selected ? 'bg-slate-700 border-slate-500 text-white' : 'bg-slate-900 border-slate-800 text-slate-500 hover:text-slate-300'}`}
                  >
                    {option.label}
                  </button>
                );
              })}
            </div>
          </div>
          <div className="flex justify-end gap-3 mt-6 pt-4 border-t border-slate-800">
            <button
              type="button" onClick={onClose}
              className="px-6 py-2.5 rounded-lg text-sm font-bold text-slate-400 hover:bg-slate-800 transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              className="px-6 py-2.5 rounded-lg text-sm font-bold bg-pink-500 text-black hover:bg-pink-600 transition-colors"
            >
              {skillToEdit ? 'Save Changes' : 'Create Skill'}
            </button>
          </div>
        </form>
      </motion.div>
    </div>
  );
};
