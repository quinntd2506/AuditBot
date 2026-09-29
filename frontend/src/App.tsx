import React, { useState } from 'react';
import { BrowserRouter, Routes, Route, useNavigate, useLocation } from 'react-router-dom';
import { DataProvider } from './context/DataContext';

import { Sidebar } from './components/layout/Sidebar';
import { Header } from './components/layout/Header';
import { SherlockCookieBanner } from './components/SherlockCookieBanner';

import { DashboardPage } from './pages/DashboardPage';
import { ContestsPage } from './pages/ContestsPage';
import { BotPage } from './pages/BotPage';
import { SkillsPage } from './pages/SkillsPage';
import { ContestDetailPage } from './pages/ContestDetailPage';

import { BotModal } from './components/modals/BotModal';
import { ContestModal } from './components/modals/ContestModal';
import { SkillModal } from './components/modals/SkillModal';

import { Bot, Skill } from './types';
import { AnimatePresence } from 'motion/react';

const AppLayout: React.FC = () => {
  const [isBotModalOpen, setBotModalOpen] = useState(false);
  const [botToEdit, setBotToEdit] = useState<Bot | null>(null);

  const [isContestModalOpen, setContestModalOpen] = useState(false);
  const [contestBotId, setContestBotId] = useState<number | null>(null);

  const [isSkillModalOpen, setSkillModalOpen] = useState(false);
  const [skillToEdit, setSkillToEdit] = useState<Skill | null>(null);

  return (
    <div className="min-h-screen bg-[#020617] text-slate-300 font-sans selection:bg-pink-500/30">
      {/* Global Sherlock session banner — appears when cookie expires */}
      <SherlockCookieBanner />

      <Sidebar 
        onAddBotClick={() => {
          setBotToEdit(null);
          setBotModalOpen(true);
        }}
        onDeleteBotClick={async (bot) => {
          if (confirm('Are you sure you want to delete this bot?')) {
            await fetch(`/api/bots/${bot.id}`, { method: 'DELETE' });
            window.location.href = '/'; // simple redirect to refresh state out of bounds
          }
        }}
      />

      <main className="md:pl-64 min-h-screen flex flex-col min-w-0">
        <Header />

        <div className="p-8 max-w-7xl mx-auto w-full">
          <AnimatePresence mode="wait">
            <Routes>
              <Route path="/" element={<DashboardPage />} />
              <Route path="/contests" element={<ContestsPage />} />
              <Route path="/contests/:id" element={<ContestDetailPage />} />
              <Route path="/skills" element={<SkillsPage 
                onAddSkill={() => { setSkillToEdit(null); setSkillModalOpen(true); }}
                onEditSkill={(skill) => { setSkillToEdit(skill); setSkillModalOpen(true); }}
              />} />
              <Route path="/bots/:id" element={<BotPage 
                onEditBot={(bot) => { setBotToEdit(bot); setBotModalOpen(true); }}
                onAddContest={(botId) => { setContestBotId(botId); setContestModalOpen(true); }}
              />} />
            </Routes>
          </AnimatePresence>
        </div>
      </main>

      <AnimatePresence>
        {isBotModalOpen && (
          <BotModal 
            isOpen={isBotModalOpen} 
            onClose={() => setBotModalOpen(false)} 
            botToEdit={botToEdit} 
          />
        )}
        {isContestModalOpen && (
          <ContestModal 
            isOpen={isContestModalOpen} 
            onClose={() => setContestModalOpen(false)} 
            botId={contestBotId}
          />
        )}
        {isSkillModalOpen && (
          <SkillModal 
            isOpen={isSkillModalOpen} 
            onClose={() => setSkillModalOpen(false)} 
            skillToEdit={skillToEdit}
          />
        )}
      </AnimatePresence>
    </div>
  );
};

export default function App() {
  return (
    <BrowserRouter>
      <DataProvider>
        <AppLayout />
      </DataProvider>
    </BrowserRouter>
  );
}
