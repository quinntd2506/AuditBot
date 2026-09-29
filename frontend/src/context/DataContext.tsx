import React, { createContext, useContext, useState, useEffect } from 'react';
import { Bot, Contest, Skill } from '../types';

interface DataContextType {
  bots: Bot[];
  contests: Contest[];
  skills: Skill[];
  globalUnreadCount: number;
  fetchBots: () => Promise<void>;
  fetchContests: () => Promise<void>;
  fetchSkills: () => Promise<void>;
  fetchGlobalUnreadCount: () => Promise<void>;
  isRefreshing: boolean;
  handleDiscover: () => Promise<void>;
}

const DataContext = createContext<DataContextType | undefined>(undefined);

export const DataProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [bots, setBots] = useState<Bot[]>([]);
  const [contests, setContests] = useState<Contest[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [globalUnreadCount, setGlobalUnreadCount] = useState(0);
  const [isRefreshing, setIsRefreshing] = useState(false);

  const fetchBots = async () => {
    try {
      const res = await fetch('/api/bots');
      if (res.ok) setBots(await res.json());
    } catch (e) {
      console.error(e);
    }
  };

  const fetchContests = async () => {
    try {
      const res = await fetch('/api/contests');
      if (res.ok) setContests(await res.json());
    } catch (e) {
      console.error(e);
    }
  };

  const fetchSkills = async () => {
    try {
      const res = await fetch('/api/skills');
      if (res.ok) setSkills(await res.json());
    } catch (e) {
      console.error(e);
    }
  };

  const fetchGlobalUnreadCount = async () => {
    try {
      const res = await fetch('/api/notifications/unread');
      if (res.ok) {
        const data = await res.json();
        setGlobalUnreadCount(data.count || 0); // Assuming length or raw payload based on previous API style
      } else {
        // The old API actually returned an array of unread objects directly, wait, let me check the previous code
        const resList = await fetch('/api/notifications'); 
        if (resList.ok) {
           const dataList = await resList.json();
           setGlobalUnreadCount(dataList.length);
        }
      }
    } catch (e) {
      console.error(e);
    }
  };

  const handleDiscover = async () => {
    setIsRefreshing(true);
    try {
      await fetch('/api/contests/discover', { method: 'POST' });
      await fetchContests();
    } catch (error) {
      console.error('Error discovering contests:', error);
    } finally {
      setIsRefreshing(false);
    }
  };

  useEffect(() => {
    fetchBots();
    fetchContests();
    fetchSkills();
    fetchGlobalUnreadCount();

    const interval = setInterval(() => {
      fetchContests();
      fetchGlobalUnreadCount();
    }, 10000);

    return () => clearInterval(interval);
  }, []);

  return (
    <DataContext.Provider
      value={{
        bots,
        contests,
        skills,
        globalUnreadCount,
        fetchBots,
        fetchContests,
        fetchSkills,
        fetchGlobalUnreadCount,
        isRefreshing,
        handleDiscover,
      }}
    >
      {children}
    </DataContext.Provider>
  );
};

export const useData = () => {
  const context = useContext(DataContext);
  if (context === undefined) {
    throw new Error('useData must be used within a DataProvider');
  }
  return context;
};
