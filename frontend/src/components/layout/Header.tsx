import React, { useState, useEffect, useRef } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import {
  ChevronRight,
  RefreshCw,
  Bell,
  Search
} from 'lucide-react';
import { useData } from '../../context/DataContext';
import { AppNotification } from '../../types';

export const Header: React.FC<{ breadcrumb?: React.ReactNode }> = ({ breadcrumb }) => {
  const { globalUnreadCount, isRefreshing, handleDiscover, fetchGlobalUnreadCount } = useData();
  const [isDropdownOpen, setIsDropdownOpen] = useState(false);
  const [notifications, setNotifications] = useState<AppNotification[]>([]);
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsDropdownOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  useEffect(() => {
    if (isDropdownOpen) {
      fetch('/api/notifications').then(res => res.json()).then(data => {
        setNotifications(data);
      });
    }
  }, [isDropdownOpen]);

  const markAsRead = async (id: number) => {
    await fetch(`/api/notifications/${id}/read`, { method: 'PATCH' });
    setNotifications(prev => prev.filter(n => n.id !== id));
    fetchGlobalUnreadCount();
  };

  return (
    <header className="h-auto min-h-16 py-3 border-b border-slate-800/50 bg-[#020617]/80 backdrop-blur-md sticky top-0 z-10 px-4 sm:px-8 flex flex-wrap items-center justify-between gap-4">
      <div className="flex items-center gap-2 text-xs sm:text-sm text-slate-500 truncate font-medium">
        <NavLink to="/" className="hover:text-pink-500 transition-colors">
          Dashboard
        </NavLink>
        {breadcrumb}
      </div>
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2 px-3 py-1.5 bg-pink-500/10 border border-pink-500/20 rounded-full">
          <button
            onClick={handleDiscover}
            disabled={isRefreshing}
            className="text-[10px] text-pink-500 hover:text-pink-400 transition-colors font-bold uppercase flex items-center gap-1 disabled:opacity-50"
          >
            <RefreshCw size={10} className={isRefreshing ? 'animate-spin' : ''} />
            Refresh
          </button>
        </div>
        
        <div className="relative" ref={dropdownRef}>
          <button 
            className="relative p-1.5 text-slate-400 hover:text-pink-500 transition-colors focus:outline-none" 
            title="Notifications"
            onClick={() => setIsDropdownOpen(!isDropdownOpen)}
          >
            <Bell size={18} />
            {globalUnreadCount > 0 && (
              <span className="absolute top-1 right-1 w-2 h-2 bg-pink-500 rounded-full animate-pulse border border-[#020617]" />
            )}
          </button>
          
          {isDropdownOpen && (
            <div className="absolute right-0 mt-2 w-80 bg-[#0f172a] border border-slate-800 shadow-xl rounded-xl overflow-hidden z-50 flex flex-col max-h-96">
              <div className="px-4 py-3 border-b border-slate-800/50 flex justify-between items-center bg-[#020617]/50">
                <span className="text-xs font-bold text-slate-200">Unread Notifications</span>
                {notifications.length > 0 && (
                  <span className="text-[10px] bg-pink-500/10 text-pink-500 px-2 py-0.5 rounded-full font-black">
                    {notifications.length}
                  </span>
                )}
              </div>
              <div className="overflow-y-auto flex-1">
                {notifications.length === 0 ? (
                  <div className="px-4 py-8 text-center text-slate-500 text-xs">
                    No unread notifications
                  </div>
                ) : (
                  <div className="flex flex-col">
                    {notifications.map(n => (
                      <div key={n.id} className="p-3 border-b border-slate-800/30 hover:bg-slate-800/30 transition-colors group cursor-pointer" onClick={() => markAsRead(n.id)}>
                        <div className="flex justify-between items-start mb-1">
                          <span className="text-[10px] font-black text-pink-500 uppercase tracking-widest truncate max-w-[80%]">
                            {n.contest_title || `Contest #${n.contest_id}`}
                          </span>
                          <span className="text-[9px] text-slate-500">
                            {new Date(n.created_at).toLocaleDateString()}
                          </span>
                        </div>
                        <p className="text-xs text-slate-300 line-clamp-2">
                          {n.message}
                        </p>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
        <div className="relative flex-1 sm:flex-none">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" size={14} />
          <input
            type="text"
            placeholder="Search..."
            className="bg-slate-900/50 border border-slate-800 rounded-full py-1.5 pl-9 pr-4 text-xs focus:outline-none focus:border-pink-500/50 w-full sm:w-48 lg:w-64 transition-all text-white"
          />
        </div>
        <NavLink 
          to="/skills"
          className="w-8 h-8 rounded-full bg-gradient-to-tr from-pink-500 to-rose-400 border border-white/10 hover:ring-2 hover:ring-pink-500/50 transition-all focus:outline-none flex-shrink-0"
          title="Open Skills Hub"
        />
      </div>
    </header>
  );
};
