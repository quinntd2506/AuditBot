import React from 'react';
import { motion } from 'motion/react';
import { useData } from '../context/DataContext';
import { ContestTable } from '../components/ContestTable';

export const ContestsPage: React.FC = () => {
  const { contests } = useData();

  return (
    <motion.div
      initial={{ opacity: 0, x: 20 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: -20 }}
      className="space-y-6"
    >
      <div className="flex items-center justify-between mb-8">
        <h2 className="text-2xl font-bold text-white">All Contests</h2>
      </div>
      <ContestTable contests={contests} />
    </motion.div>
  );
};
