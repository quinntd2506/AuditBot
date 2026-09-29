import React, { useState, useEffect } from 'react';
import { motion } from 'motion/react';
import { useNavigate, useParams } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { 
  ExternalLink, Info, Code2, ChevronUp, ChevronDown, 
  MessageSquare, Loader2, CheckCircle2, XCircle, Zap, RefreshCw, AlertTriangle, AlertCircle, Bell, Trash2, ShieldCheck,
  Calendar, Copy
} from 'lucide-react';
import { useData } from '../context/DataContext';
import { PreviewSubmitModal } from '../components/modals/PreviewSubmitModal';
import { BatchVerificationModal } from '../components/modals/BatchVerificationModal';
import { Issue, AppNotification, Contest } from '../types';

const formatDateTime = (value?: string) => {
  if (!value) return 'TBA';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'TBA';
  return date.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
};

export const ContestDetailPage: React.FC = () => {
  const { id } = useParams();
  const navigate = useNavigate();
  const contestId = Number(id);
  const { bots, contests, fetchContests } = useData();
  const [localContest, setLocalContest] = useState<Contest | null>(null);
  
  const [issues, setIssues] = useState<Issue[]>([]);
  const [notifications, setNotifications] = useState<AppNotification[]>([]);
  const [isPinging, setIsPinging] = useState(false);
  const [isAuditing, setIsAuditing] = useState(false);
  const [isJoining, setIsJoining] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [confirmWithdraw, setConfirmWithdraw] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [issueSeverityFilter, setIssueSeverityFilter] = useState('All Severities');

  const [selectedIssueIds, setSelectedIssueIds] = useState<number[]>([]);
  const [expandedIssueIds, setExpandedIssueIds] = useState<Set<number>>(new Set());
  const [isPreviewingSubmit, setIsPreviewingSubmit] = useState(false);
  const [verifyingIssueIds, setVerifyingIssueIds] = useState<Set<number>>(new Set());
  const [toastMessage, setToastMessage] = useState<string | null>(null);

  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [currentBatchIds, setCurrentBatchIds] = useState<number[]>([]);
  const [isJobModalOpen, setIsJobModalOpen] = useState(false);
  const [isDescriptionExpanded, setIsDescriptionExpanded] = useState(false);
  const [activeTab, setActiveTab] = useState<'bot' | 'platform'>('bot');
  const [showSkipped, setShowSkipped] = useState(false);
  const [scanStatus, setScanStatus] = useState<{exists: boolean, scan_status: string, total: number, completed: number} | null>(null);

  const selectedContest = localContest || contests.find(c => c.id === contestId);
  const selectedBot = bots.find(b => b.id === selectedContest?.bot_id);

  useEffect(() => {
    if (contestId) {
      fetchContestDetail(contestId);
      fetchIssues(contestId);
      fetchNotifications(contestId);
    }
  }, [contestId]);

  useEffect(() => {
    if (!contestId) return;
    const checkScanStatus = async () => {
      try {
        const res = await fetch(`/api/contests/${contestId}/trufy-scan`);
        if (res.ok) {
          const data = await res.json();
          setScanStatus(data);
          if (data.exists && data.scan_status === 'pending') {
            fetchIssues(contestId); // Auto-refresh issues as they come in!
          }
        }
      } catch (e) {}
    };
    
    checkScanStatus();
    const interval = setInterval(checkScanStatus, 5000);
    return () => clearInterval(interval);
  }, [contestId]);

  const fetchContestDetail = async (cid: number) => {
    try {
      const res = await fetch(`/api/contests/${cid}`);
      if (res.ok) {
        setLocalContest(await res.json());
      }
    } catch (e) {
      console.error(e);
    }
  };


  const handleJoin = async () => {
    if (!contestId) return;
    setIsJoining(true);
    try {
      const res = await fetch(`/api/contests/${contestId}/join`, { method: 'POST' });
      if (res.ok) {
        await fetchContestDetail(contestId);
        await fetchContests(); // refresh global state
      }
    } catch (e) {
      console.error(e);
    } finally {
      setIsJoining(false);
    }
  };

  const handleWithdrawRequest = () => {
    setConfirmWithdraw(true);
    // Auto-cancel after 5 seconds to avoid stale confirmation
    setTimeout(() => setConfirmWithdraw(false), 5000);
  };

  const handleWithdrawConfirm = async () => {
    if (!contestId) return;
    setConfirmWithdraw(false);
    try {
      await fetch(`/api/contests/${contestId}/skip-autojoin`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ skip: true }),
      });
      await fetchContestDetail(contestId);
      await fetchContests();
    } catch (e) {
      console.error(e);
    }
  };

  const handleReenroll = async () => {
    if (!contestId) return;
    try {
      await fetch(`/api/contests/${contestId}/skip-autojoin`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ skip: false }),
      });
      await fetchContestDetail(contestId);
      await fetchContests();
    } catch (e) {
      console.error(e);
    }
  };



  const fetchIssues = async (cid: number) => {
    const res = await fetch(`/api/issues/${cid}`);
    const data: Issue[] = await res.json();
    
    const severityRank: Record<string, number> = { 
      'critical': 4, 
      'high': 3, 
      'medium': 2, 
      'low': 1, 
      'qa': 1,
      'informational': 0,
      'unknown': 0
    };
    const confidenceRank: Record<string, number> = { 'high': 3, 'medium': 2, 'low': 1, 'informational': 0 };
    
    data.sort((a, b) => {
      const sA = (a.severity || '').toLowerCase();
      const sB = (b.severity || '').toLowerCase();
      const cA = (a.confidence || '').toLowerCase();
      const cB = (b.confidence || '').toLowerCase();

      const sevDiff = (severityRank[sB] || 0) - (severityRank[sA] || 0);
      if (sevDiff !== 0) return sevDiff;
      
      const confDiff = (confidenceRank[cB] || 0) - (confidenceRank[cA] || 0);
      return confDiff;
    });
    
    setIssues(data);
  };

  const fetchNotifications = async (cid: number) => {
    const res = await fetch(`/api/notifications?contestId=${cid}`);
    setNotifications(await res.json());
  };

  const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

  const forcePing = async () => {
    setIsPinging(true);
    try {
      await fetch(`/api/contests/${contestId}/ping`, { method: 'POST' });
      for (let i = 0; i < 6; i += 1) {
        await sleep(i === 0 ? 1000 : 2500);
        await Promise.all([
          fetchIssues(contestId),
          fetchNotifications(contestId),
        ]);
      }
    } finally {
      setIsPinging(false);
    }
  };

  const handleDeleteContest = async () => {
    if (!confirmDelete) {
      setConfirmDelete(true);
      return;
    }
    setIsDeleting(true);
    try {
      const res = await fetch(`/api/contests/${contestId}`, { method: 'DELETE' });
      if (res.ok) {
        await fetchContests();
        navigate('/contests');
      }
    } finally {
      setIsDeleting(false);
    }
  };

  const updateIssueStatus = async (issueId: number, status: string) => {
    await fetch(`/api/issues/${issueId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status }),
    });
    fetchIssues(contestId);
  };

  const handleBulkSubmit = async () => {
    let submitted = 0;
    for (const issueId of selectedIssueIds) {
      const res = await fetch(`/api/issues/${issueId}/submit-to-platform`, { method: 'POST' });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        showToast(`Submit failed for issue ${issueId}: ${body.detail || res.statusText}`);
        continue;
      }
      submitted += 1;
    }
    setSelectedIssueIds([]);
    setIsPreviewingSubmit(false);
    await Promise.all([
      fetchIssues(contestId),
      fetchNotifications(contestId),
    ]);
    if (submitted > 0) {
      showToast(`Submitted ${submitted} issue${submitted === 1 ? '' : 's'} to platform`);
    }
  };

  const showToast = (msg: string) => {
    setToastMessage(msg);
    setTimeout(() => setToastMessage(null), 4000);
  };

  const verifyIssue = async (issueId: number) => {
    handleBatchVerify([issueId]);
  };

  const handleBatchVerify = async (ids: number[]) => {
    if (ids.length === 0) return;
    
    setVerifyingIssueIds(prev => {
      const next = new Set(prev);
      ids.forEach(id => next.add(id));
      return next;
    });

    try {
      const res = await fetch('/api/issues/batch-verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ issue_ids: ids }),
      });
      if (res.ok) {
        const { job_id } = await res.json();
        setActiveJobId(job_id);
        setCurrentBatchIds(ids);
        setIsJobModalOpen(true);
      } else {
        setVerifyingIssueIds(prev => {
          const next = new Set(prev);
          ids.forEach(id => next.delete(id));
          return next;
        });
        showToast('❌ Failed to start verification job');
      }
    } catch (e) {
      console.error(e);
      setVerifyingIssueIds(prev => {
        const next = new Set(prev);
        ids.forEach(id => next.delete(id));
        return next;
      });
      showToast('❌ Failed to start verification job');
    }
  };

  const getSeverityColor = (severity: string) => {
    switch (severity) {
      case 'High': return 'text-red-500 bg-red-500/10 border-red-500/20';
      case 'Medium': return 'text-orange-500 bg-orange-500/10 border-orange-500/20';
      case 'Low': return 'text-yellow-500 bg-yellow-500/10 border-yellow-500/20';
      default: return 'text-blue-500 bg-blue-500/10 border-blue-500/20';
    }
  };

  const isPlatformSubmission = (issue: Issue) => {
    const classification = (issue.classification || '').toLowerCase();
    if (classification === 'platform-submission') return true;
    if (issue.linked_issue_id !== null && issue.linked_issue_id !== undefined) return true;

    return Boolean(issue.platform_issue_id) && (
      issue.platform_issue_id.startsWith('c4-') || 
      issue.platform_issue_id.startsWith('sherlock-') || 
      issue.platform_issue_id.startsWith('guardian-') ||
      issue.platform_issue_id.startsWith('immunefi-')
    );
  };

  const filteredIssues = issues.filter(issue => {
    const filter = issueSeverityFilter.toLowerCase();
    const s = (issue.severity || '').toLowerCase();
    if (filter === 'all severities') return true;
    if (filter === 'others') return s !== 'high' && s !== 'medium';
    return s === filter;
  });

  const botFindings = filteredIssues.filter(issue => {
    if (isPlatformSubmission(issue)) return false;
    if (issue.status === 'skipped') return showSkipped;
    return true;
  });
  const platformSubmissions = filteredIssues.filter(issue => isPlatformSubmission(issue));

  const renderIssueCard = (issue: Issue, isPlatform: boolean) => (
    <div id={`issue-${issue.id}`} key={issue.id} className={`bg-[#0F172A] border rounded-[2rem] p-8 transition-all relative overflow-hidden group ${
      issue.status === 'skipped' ? 'opacity-40 hover:opacity-100 border-slate-800/40 hover:border-slate-800' :
      !isPlatform && selectedIssueIds.includes(issue.id) ? 'border-pink-500/50 shadow-pink-500/5' : 'border-slate-800/80 hover:border-slate-700'
    }`}>
      {/* Background Glow */}
      <div className="absolute -top-24 -right-24 w-48 h-48 bg-pink-500/5 blur-[100px] rounded-full group-hover:bg-pink-500/10 transition-all pointer-events-none" />

      {/* Header Row */}
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-4">
          <button 
            onClick={() => {
              const newSet = new Set(expandedIssueIds);
              if (newSet.has(issue.id)) newSet.delete(issue.id);
              else newSet.add(issue.id);
              setExpandedIssueIds(newSet);
            }}
            className="text-slate-500 hover:text-white transition-colors"
          >
            {expandedIssueIds.has(issue.id) ? <ChevronUp size={18} /> : <ChevronDown size={18} />}
          </button>

          <div className="flex items-center gap-2">
             <span className={`px-4 py-1.5 rounded-lg text-[10px] font-black uppercase tracking-widest border transition-all ${
               (issue.severity || '').toLowerCase() === 'high' ? 'bg-red-500/20 text-red-500 border-red-500/20' :
               (issue.severity || '').toLowerCase() === 'medium' ? 'bg-orange-500/20 text-orange-500 border-orange-500/20' :
               (issue.severity || '').toLowerCase() === 'low' ? 'bg-yellow-500/20 text-yellow-500 border-yellow-500/20' :
               'bg-blue-500/20 text-blue-500 border-blue-500/20'
             }`}>
              {issue.severity || 'Unknown'}
            </span>
            {issue.confidence && (
              <span className="px-4 py-1.5 rounded-lg text-[10px] font-black uppercase tracking-widest border transition-all bg-slate-800/50 text-slate-400 border-slate-700">
                {issue.confidence} Confidence
              </span>
            )}
            <span className="text-[10px] text-slate-500 font-bold uppercase tracking-[0.2em] px-2">
              {issue.classification || 'Logic'}
            </span>
            {isPlatform && issue.linked_issue_id && (
               <button 
                 onClick={(e) => {
                   e.stopPropagation();
                   setActiveTab('bot');
                   setTimeout(() => {
                     const el = document.getElementById(`issue-${issue.linked_issue_id}`);
                     if (el) {
                       el.scrollIntoView({ behavior: 'smooth', block: 'center' });
                       el.classList.add('ring-2', 'ring-pink-500', 'ring-offset-2', 'ring-offset-[#0A0F1C]');
                       setTimeout(() => el.classList.remove('ring-2', 'ring-pink-500', 'ring-offset-2', 'ring-offset-[#0A0F1C]'), 2000);
                     }
                   }, 100);
                 }}
                 className="flex items-center gap-1.5 px-3 py-1.5 bg-indigo-500/10 border border-indigo-500/20 rounded-lg hover:bg-indigo-500/20 transition-all text-[10px] font-black text-indigo-400 uppercase tracking-widest"
               >
                 🤖 Bot Origin
               </button>
            )}
            {!isPlatform && issues.find(i => i.linked_issue_id === issue.id) && (
               <button 
                 onClick={(e) => {
                   e.stopPropagation();
                   setActiveTab('platform');
                   const linkedIssue = issues.find(i => i.linked_issue_id === issue.id);
                   setTimeout(() => {
                     const el = document.getElementById(`issue-${linkedIssue?.id}`);
                     if (el) {
                       el.scrollIntoView({ behavior: 'smooth', block: 'center' });
                       el.classList.add('ring-2', 'ring-blue-500', 'ring-offset-2', 'ring-offset-[#0f172a]');
                       setTimeout(() => el.classList.remove('ring-2', 'ring-blue-500', 'ring-offset-2', 'ring-offset-[#0f172a]'), 2000);
                     }
                   }, 100);
                 }}
                 className="flex items-center gap-1.5 px-3 py-1.5 bg-blue-500/10 border border-blue-500/20 rounded-lg hover:bg-blue-500/20 transition-all text-[10px] font-black text-blue-400 uppercase tracking-widest"
               >
                 🔗 View Submission
               </button>
            )}
            {notifications.some(n => n.issue_id === issue.id) && (
              <button 
                onClick={(e) => {
                  e.stopPropagation();
                  const el = document.getElementById(`feedback-${issue.id}`);
                  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
                }}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-pink-500/5 border border-pink-500/20 rounded-lg hover:bg-pink-500/10 transition-all group/fb"
              >
                <MessageSquare size={12} className="text-pink-500 group-hover/fb:scale-110 transition-transform" />
                <span className="text-[10px] font-black text-pink-500 uppercase tracking-widest">Feedback</span>
              </button>
            )}
          </div>
        </div>

        <div className="flex items-center gap-6">
          {/* Status Badge */}
          <div className="flex items-center gap-2">
            {issue.status === 'valid' && (
              <div className="flex items-center gap-1.5 text-[11px] font-black uppercase tracking-[0.2em] text-emerald-500">
                <CheckCircle2 size={16} />
                Valid
              </div>
            )}
            {issue.status === 'verified' && (
              <div className="flex items-center gap-1.5 text-[11px] font-black uppercase tracking-[0.2em] text-purple-500">
                <CheckCircle2 size={16} />
                Verified
              </div>
            )}
            {issue.status === 'invalid' && (
              <div className="flex items-center gap-1.5 text-[11px] font-black uppercase tracking-[0.2em] text-rose-500">
                <XCircle size={16} />
                Invalid
              </div>
            )}
            {issue.status === 'duplicate' && (
              <div className="flex items-center gap-1.5 text-[11px] font-black uppercase tracking-[0.2em] text-slate-400">
                <Copy size={16} />
                Duplicate
              </div>
            )}
            {issue.status === 'unsupported' && (
              <div className="flex items-center gap-1.5 text-[11px] font-black uppercase tracking-[0.2em] text-amber-400">
                <AlertCircle size={16} />
                Unsupported
              </div>
            )}
            {issue.status === 'submitted' && (
              <div className="flex items-center gap-1.5 text-[11px] font-black uppercase tracking-[0.2em] text-blue-500">
                {isPlatform ? null : <Loader2 size={16} className="animate-spin" />}
                {isPlatform ? 'Pending Judge' : 'Reviewing'}
              </div>
            )}
            {issue.status === 'skipped' && (
              <div className="flex items-center gap-1.5 text-[11px] font-black uppercase tracking-[0.2em] text-slate-500">
                <XCircle size={16} />
                Skipped by Triage
              </div>
            )}
            {!isPlatform && issue.status === 'pending' && verifyingIssueIds.has(issue.id) && (
              <div className="flex items-center gap-1.5 text-[11px] font-black uppercase tracking-[0.2em] text-yellow-500">
                <Loader2 size={16} className="animate-spin" />
                Verifying
              </div>
            )}
          </div>

          {!isPlatform && issue.status === 'pending' && (
            <input
              type="checkbox"
              checked={selectedIssueIds.includes(issue.id)}
              onChange={(e) => {
                if (verifyingIssueIds.has(issue.id)) {
                  showToast('⚠️ This issue is already queued for verification.');
                }
                if (e.target.checked) setSelectedIssueIds([...selectedIssueIds, issue.id]);
                else setSelectedIssueIds(selectedIssueIds.filter(id => id !== issue.id));
              }}
              className="w-5 h-5 rounded-lg border-slate-700 text-pink-500 bg-slate-900 cursor-pointer accent-pink-500 transition-all border-2"
            />
          )}
        </div>
      </div>

      <h4 className="text-lg font-bold text-white mb-4 leading-relaxed max-w-full break-words">
        {issue.title}
      </h4>
      
      {expandedIssueIds.has(issue.id) && (
        <div className="max-h-[72vh] overflow-y-auto pr-3 custom-scrollbar animate-in fade-in slide-in-from-top-2 duration-300">
          <div className="markdown-body mb-8">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{issue.description}</ReactMarkdown>
          </div>

          {issue.poc_code && (
            <div className="mt-6 border-t border-slate-800/40 pt-6">
              <h5 className="text-sm font-bold text-emerald-400 mb-4 uppercase tracking-widest flex items-center gap-2">
                <CheckCircle2 size={16} /> Verified PoC Code
              </h5>
              <pre className="bg-slate-950 text-emerald-300 text-[12px] p-5 rounded-xl border border-emerald-500/20 max-h-[32rem] overflow-auto custom-scrollbar font-mono leading-relaxed whitespace-pre">{issue.poc_code}</pre>
            </div>
          )}

          {issue.submission_report && (
            <div className="mt-6 border-t border-slate-800/40 pt-6">
              <h5 className="text-sm font-bold text-purple-400 mb-4 uppercase tracking-widest flex items-center gap-2">
                <CheckCircle2 size={16} /> Generated Submission Report
              </h5>
              <div className="prose prose-invert prose-sm max-w-none text-slate-300 bg-slate-900/50 p-6 rounded-xl border border-slate-800 max-h-[32rem] overflow-y-auto pr-4 custom-scrollbar">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{issue.submission_report}</ReactMarkdown>
              </div>
            </div>
          )}

          {/* Judge Feedback Box */}
          {notifications.filter(n => n.issue_id === issue.id).length > 0 && (
            <div id={`feedback-${issue.id}`} className="mt-6 pt-6 border-t border-slate-800/40">
              {notifications.filter(n => n.issue_id === issue.id).map(n => (
                <div 
                  key={n.id} 
                  className="relative p-6 rounded-[1.5rem] bg-pink-500/[0.03] border border-pink-500/10 group/feedback transition-all hover:bg-pink-500/[0.05]"
                >
                  <div className="flex gap-4">
                    <div className="shrink-0 mt-1">
                      <div className="p-2 bg-pink-500/10 rounded-xl border border-pink-500/20">
                        <MessageSquare size={16} className="text-pink-500" />
                      </div>
                    </div>
                    <div className="space-y-3">
                      <div className="text-slate-400 italic text-[14px] leading-relaxed selection:bg-pink-500/30">
                        {n.message.length > 500 ? n.message.substring(0, 500) + '...' : n.message}
                      </div>
                      <div className="flex items-center gap-3">
                        <time className="text-[10px] text-slate-600 font-bold tracking-widest uppercase">
                          {new Date(n.created_at).toLocaleDateString()} &middot; {new Date(n.created_at).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'})}
                        </time>
                        {!n.is_read && (
                          <span className="w-1.5 h-1.5 bg-pink-500 rounded-full animate-pulse" />
                        )}
                      </div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}

          {!isPlatform && ['pending', 'verified', 'unsupported'].includes(issue.status) && (
            <div className="flex gap-3 pt-6 border-t border-slate-800/40 mt-6">
              <button
                onClick={() => verifyIssue(issue.id)}
                disabled={verifyingIssueIds.has(issue.id)}
                className="px-4 py-2 bg-purple-500/10 text-purple-400 text-[10px] font-black uppercase tracking-widest rounded-xl hover:bg-purple-500/20 transition-all border border-purple-500/20 flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {verifyingIssueIds.has(issue.id)
                  ? <><Loader2 size={14} className="animate-spin" /> Agent Working...</>
                  : <><Zap size={14} /> Verify &amp; Generate PoC</>
                }
              </button>
            </div>
          )}

          {!isPlatform && ['submitted'].includes(issue.status) && notifications.filter(n => n.issue_id === issue.id).length === 0 && (
            <div className="flex gap-3 pt-6 border-t border-slate-800/40">
              <button
                onClick={() => updateIssueStatus(issue.id, 'valid')}
                className="px-4 py-2 bg-emerald-500/10 text-emerald-500 text-[10px] font-black uppercase tracking-widest rounded-xl hover:bg-emerald-500/20 transition-all border border-emerald-500/20"
              >
                Accept Finding
              </button>
              <button
                onClick={() => updateIssueStatus(issue.id, 'invalid')}
                className="px-4 py-2 bg-rose-500/10 text-rose-500 text-[10px] font-black uppercase tracking-widest rounded-xl hover:bg-rose-500/20 transition-all border border-rose-500/20"
              >
                Reject Finding
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );

  if (!selectedContest) return <div className="text-slate-500">Contest not found.</div>;

  const contestStatusDot =
    selectedContest.status === 'active' ? 'bg-pink-500 animate-pulse' :
    selectedContest.status === 'upcoming' ? 'bg-amber-400' :
    selectedContest.status === 'auditing' || selectedContest.status === 'submitting' ? 'bg-blue-500 animate-pulse' :
    'bg-slate-600';

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.98 }}
      animate={{ opacity: 1, scale: 1 }}
      exit={{ opacity: 0, scale: 1.02 }}
      className="space-y-8"
    >
      {/* Toast Notification */}
      {toastMessage && (
        <div className="fixed bottom-6 right-6 z-50 bg-slate-800 border border-purple-500/30 text-purple-300 text-sm px-5 py-3 rounded-2xl shadow-xl flex items-center gap-3 animate-in slide-in-from-bottom-4 duration-300">
          <Loader2 size={16} className="animate-spin text-purple-400" />
          {toastMessage}
        </div>
      )}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <div>
            <h2 className="text-2xl font-bold text-white">{selectedContest.title}</h2>
            <div className="flex items-center gap-3 mt-1">
              <span className="text-xs text-pink-500 font-medium tracking-wide">Analyst Bot: {selectedContest.bot_name || 'Guardian'}</span>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-3">
          {!selectedContest.has_joined && !selectedContest.skip_autojoin && selectedContest.status !== 'completed' && selectedContest.synced_from !== 'manual' && (
            <button
              onClick={handleJoin}
              disabled={isJoining}
              className="flex items-center gap-2 bg-pink-600 hover:bg-pink-700 disabled:opacity-50 text-white px-6 py-2 rounded-lg font-bold transition-all shadow-lg shadow-pink-600/20"
            >
              {isJoining ? <Loader2 size={18} className="animate-spin" /> : <Zap size={18} />}
              {isJoining ? 'Joining...' : 'Join Contest'}
            </button>
          )}

          {selectedContest.has_joined && !selectedContest.skip_autojoin && (
            <div className="flex items-center gap-2">
              <div className="flex items-center gap-2 bg-emerald-500/10 border border-emerald-500/20 text-emerald-500 px-4 py-2 rounded-lg text-xs font-bold uppercase tracking-widest">
                <CheckCircle2 size={16} />
                Bot Enrolled
              </div>
              {selectedContest.synced_from !== 'manual' && selectedContest.status !== 'completed' && (
                confirmWithdraw ? (
                  <div className="flex items-center gap-2 bg-red-950/40 border border-red-500/30 rounded-lg px-3 py-1.5 animate-in fade-in slide-in-from-right-2 duration-200">
                    <AlertTriangle size={13} className="text-red-400 shrink-0" />
                    <span className="text-[10px] text-red-300 font-semibold">Bot will stop auto-joining. Confirm?</span>
                    <button
                      onClick={handleWithdrawConfirm}
                      className="text-[10px] font-black uppercase tracking-widest bg-red-500/20 hover:bg-red-500/40 text-red-400 px-2.5 py-1 rounded-md transition-all"
                    >
                      Yes, Withdraw
                    </button>
                    <button
                      onClick={() => setConfirmWithdraw(false)}
                      className="text-[10px] font-black uppercase tracking-widest text-slate-500 hover:text-slate-300 px-2 py-1 rounded-md transition-all"
                    >
                      Cancel
                    </button>
                  </div>
                ) : (
                  <button
                    onClick={handleWithdrawRequest}
                    className="flex items-center gap-2 bg-slate-800 hover:bg-red-900/30 border border-slate-700 hover:border-red-500/30 text-slate-400 hover:text-red-400 px-4 py-2 rounded-lg text-xs font-bold uppercase tracking-widest transition-all"
                    title="Stop auto-joining this contest"
                  >
                    <XCircle size={14} />
                    Withdraw Bot
                  </button>
                )
              )}
            </div>
          )}

          {selectedContest.skip_autojoin && (
            <div className="flex items-center gap-2">
              <div className="flex items-center gap-2 bg-slate-800/50 border border-slate-700 text-slate-400 px-4 py-2 rounded-lg text-xs font-bold uppercase tracking-widest">
                <XCircle size={16} />
                Not Enrolled
              </div>
              {selectedContest.status !== 'completed' && selectedContest.synced_from !== 'manual' && (
                <button
                  onClick={handleReenroll}
                  className="flex items-center gap-2 bg-pink-600/10 hover:bg-pink-600/20 border border-pink-500/30 text-pink-400 px-4 py-2 rounded-lg text-xs font-bold uppercase tracking-widest transition-all"
                >
                  <Zap size={14} />
                  Re-enroll Bot
                </button>
              )}
            </div>
          )}


          {selectedContest.platform_url && (

            <a
              href={selectedContest.platform_url}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-2 bg-slate-800 hover:bg-slate-700 text-white px-4 py-2 rounded-lg font-semibold transition-all border border-slate-700"
            >
              <ExternalLink size={18} />
              View on {selectedContest.synced_from === 'guardian' ? 'Guardian Defender' : selectedContest.synced_from === 'sherlock' ? 'Sherlock' : 'Platform'}
            </a>
          )}

          {confirmDelete ? (
            <div className="flex items-center gap-2 bg-red-950/40 border border-red-500/30 rounded-lg px-3 py-1.5">
              <AlertTriangle size={13} className="text-red-400 shrink-0" />
              <span className="text-[10px] text-red-300 font-semibold">Delete local contest?</span>
              <button
                onClick={handleDeleteContest}
                disabled={isDeleting}
                className="text-[10px] font-black uppercase tracking-widest bg-red-500/20 hover:bg-red-500/40 text-red-400 px-2.5 py-1 rounded-md transition-all disabled:opacity-50"
              >
                {isDeleting ? 'Deleting...' : 'Delete'}
              </button>
              <button
                onClick={() => setConfirmDelete(false)}
                disabled={isDeleting}
                className="text-[10px] font-black uppercase tracking-widest text-slate-500 hover:text-slate-300 px-2 py-1 rounded-md transition-all disabled:opacity-50"
              >
                Cancel
              </button>
            </div>
          ) : (
            <button
              onClick={handleDeleteContest}
              className="flex items-center gap-2 bg-slate-800 hover:bg-red-900/30 border border-slate-700 hover:border-red-500/30 text-slate-400 hover:text-red-400 px-4 py-2 rounded-lg text-xs font-bold uppercase tracking-widest transition-all"
              title="Delete local contest"
            >
              <Trash2 size={14} />
              Delete
            </button>
          )}
        </div>
      </div>

      {selectedContest.description && (
        <div className="bg-[#0F172A] border border-slate-800/50 p-6 rounded-2xl">
          <div className="flex justify-between items-center mb-3">
            <h3 className="text-xs font-bold text-slate-500 uppercase tracking-[0.2em] flex items-center gap-2">
                <Info size={14} className="text-pink-500" />
                Contest Overview
            </h3>
            {selectedContest.description.length > 300 && (
              <button 
                onClick={() => setIsDescriptionExpanded(!isDescriptionExpanded)}
                className="text-slate-500 hover:text-white transition-all p-1 hover:bg-slate-800 rounded-lg"
                title={isDescriptionExpanded ? "Show Less" : "Show More"}
              >
                {isDescriptionExpanded ? <ChevronUp size={20} /> : <ChevronDown size={20} />}
              </button>
            )}
          </div>
          <div className={`markdown-body relative ${!isDescriptionExpanded && selectedContest.description.length > 300 ? 'max-h-32 overflow-hidden' : 'max-h-96 overflow-y-auto pr-3 custom-scrollbar'}`}>
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{selectedContest.description}</ReactMarkdown>
            {!isDescriptionExpanded && selectedContest.description.length > 300 && (
              <div className="absolute bottom-0 left-0 right-0 h-16 bg-gradient-to-t from-[#0F172A] to-transparent pointer-events-none" />
            )}
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        <div className="lg:col-span-2 space-y-6">
          {selectedContest.code_urls && selectedContest.code_urls.length > 0 && (
            <div className="bg-[#0F172A] border border-slate-800/50 rounded-2xl p-6">
              <h3 className="text-sm font-bold text-white uppercase tracking-wider mb-4 flex items-center gap-2">
                <Code2 size={16} className="text-pink-500" />
                Contest Repositories
              </h3>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {selectedContest.code_urls.map((url, i) => (
                  <a 
                    key={i} 
                    href={url} 
                    target="_blank" 
                    rel="noopener noreferrer"
                    className="flex items-center gap-2 p-3 bg-slate-900 border border-slate-800 hover:border-pink-500/50 rounded-xl transition-all group"
                  >
                    <ExternalLink size={14} className="text-slate-500 group-hover:text-pink-500" />
                    <span className="text-xs text-slate-300 group-hover:text-white truncate">
                      {url.replace('https://github.com/', '')}
                    </span>
                  </a>
                ))}
              </div>
            </div>
          )}

          <div className="flex items-center justify-between mt-4">
            <h3 className="text-2xl font-bold text-white tracking-tight">Issues Found ({issues.length})</h3>
            <div className="flex gap-3">
              {selectedBot?.mode === 'review' && selectedIssueIds.filter(id => {
                const i = issues.find(issue => issue.id === id);
                return i && i.status === 'pending';
              }).length > 0 && (
                <div className="flex gap-2">
                  <button
                    onClick={() => handleBatchVerify(selectedIssueIds.filter(id => {
                      const i = issues.find(iss => iss.id === id);
                      return i && i.status === 'pending';
                    }))}
                    className="bg-purple-600 hover:bg-purple-700 text-white text-[11px] font-bold uppercase tracking-wider px-5 py-2 rounded-xl transition-all shadow-xl shadow-purple-600/10 border border-purple-500/50 flex items-center gap-2"
                  >
                    <ShieldCheck size={14} />
                    Verify Selected ({selectedIssueIds.filter(id => {
                      const i = issues.find(issue => issue.id === id);
                      return i && i.status === 'pending';
                    }).length})
                  </button>
                  <button
                    onClick={() => setIsPreviewingSubmit(true)}
                    className="bg-pink-600 hover:bg-pink-700 text-white text-[11px] font-bold uppercase tracking-wider px-5 py-2 rounded-xl transition-all shadow-xl shadow-pink-600/10 border border-pink-500/50"
                  >
                    Preview Submission ({selectedIssueIds.filter(id => {
                      const i = issues.find(issue => issue.id === id);
                      return i && i.status === 'pending';
                    }).length})
                  </button>
                </div>
              )}
              {activeTab === 'bot' && issues.some(i => i.status === 'skipped') && (
                <label className="flex items-center gap-2 text-xs font-bold text-slate-400 bg-slate-800/40 border border-slate-800 py-2.5 px-4 rounded-xl cursor-pointer hover:bg-slate-800 hover:text-slate-200 transition-all select-none">
                  <input 
                    type="checkbox" 
                    checked={showSkipped} 
                    onChange={(e) => setShowSkipped(e.target.checked)}
                    className="rounded text-pink-500 bg-slate-900 border-slate-700 cursor-pointer accent-pink-500" 
                  />
                  Show Skipped ({issues.filter(i => i.status === 'skipped').length})
                </label>
              )}
              <div className="relative">
                <select 
                  value={issueSeverityFilter}
                  onChange={(e) => setIssueSeverityFilter(e.target.value)}
                  className="bg-[#0F172A] border border-slate-800 text-sm text-slate-300 rounded-xl pl-4 pr-10 py-2.5 focus:outline-none focus:border-pink-500/50 appearance-none cursor-pointer transition-all hover:bg-slate-800/50"
                >
                  <option>All Severities</option>
                  <option>High</option>
                  <option>Medium</option>
                  <option>Others</option>
                </select>
                <ChevronDown size={14} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500 pointer-events-none" />
              </div>
            </div>
          </div>

          <div className="flex items-end justify-between border-b border-slate-800/50 pb-px mt-12 mb-6">
            <div className="flex gap-4">
              <button
                onClick={() => setActiveTab('bot')}
                className={`pb-4 px-2 text-sm font-bold uppercase tracking-widest transition-all border-b-2 ${
                  activeTab === 'bot' 
                    ? 'text-pink-400 border-pink-500' 
                    : 'text-slate-500 border-transparent hover:text-slate-300'
                }`}
              >
                <div className="flex items-center gap-2">
                  <ShieldCheck size={18} />
                  Bot Findings
                  <span className="bg-slate-800 text-slate-300 py-0.5 px-2 rounded-full text-[10px]">{botFindings.length}</span>
                </div>
              </button>
              <button
                onClick={() => setActiveTab('platform')}
                className={`pb-4 px-2 text-sm font-bold uppercase tracking-widest transition-all border-b-2 ${
                  activeTab === 'platform' 
                    ? 'text-blue-400 border-blue-500' 
                    : 'text-slate-500 border-transparent hover:text-slate-300'
                }`}
              >
                <div className="flex items-center gap-2">
                  <Code2 size={18} />
                  Platform Submissions
                  <span className="bg-slate-800 text-slate-300 py-0.5 px-2 rounded-full text-[10px]">{platformSubmissions.length}</span>
                </div>
              </button>
            </div>
            
            {scanStatus && scanStatus.exists && (
              <div className="pb-3 hidden md:block">
                {scanStatus.scan_status === 'pending' ? (
                  <div className="flex items-center gap-2 px-3 py-1.5 bg-pink-500/10 border border-pink-500/20 rounded-full text-pink-400 text-xs font-medium animate-pulse shadow-[0_0_15px_rgba(236,72,153,0.15)]">
                    <Loader2 size={12} className="animate-spin" />
                    Scanning Repositories ({scanStatus.completed}/{scanStatus.total})
                  </div>
                ) : (
                  <div className="flex items-center gap-2 px-3 py-1.5 bg-emerald-500/10 border border-emerald-500/20 rounded-full text-emerald-400 text-xs font-medium transition-all">
                    <CheckCircle2 size={12} />
                    Scans Complete ({scanStatus.completed}/{scanStatus.total})
                  </div>
                )}
              </div>
            )}
          </div>

          <div className="space-y-8">
            {activeTab === 'bot' && (
              <div className="space-y-6 animate-in fade-in slide-in-from-bottom-2 duration-300">
                {botFindings.length > 0 ? (
                  botFindings.map(issue => renderIssueCard(issue, false))
                ) : (
                  <div className="text-center py-12 bg-slate-900/30 rounded-2xl border border-dashed border-slate-800">
                    <p className="text-slate-500 font-medium">No bot findings available.</p>
                  </div>
                )}
              </div>
            )}
            
            {activeTab === 'platform' && (
              <div className="space-y-6 animate-in fade-in slide-in-from-bottom-2 duration-300">
                {platformSubmissions.length > 0 ? (
                  platformSubmissions.map(issue => renderIssueCard(issue, true))
                ) : (
                  <div className="text-center py-12 bg-slate-900/30 rounded-2xl border border-dashed border-slate-800">
                    <p className="text-slate-500 font-medium">No platform submissions yet.</p>
                  </div>
                )}
              </div>
            )}
            {issues.length === 0 && !isAuditing && (
              <div className="bg-slate-900/30 border border-dashed border-slate-800 rounded-[2rem] p-16 text-center">
                <div className="w-16 h-16 bg-slate-800/50 rounded-full flex items-center justify-center mx-auto mb-6 border border-slate-700">
                  <AlertTriangle className="text-slate-600" size={28} />
                </div>
                <h4 className="text-white text-xl font-bold mb-2 tracking-tight">No issues found yet</h4>
                <p className="text-slate-500 max-w-sm mx-auto leading-relaxed text-sm">Run the bot audit or Wait for platform synchronization to see the vulnerabilities list here.</p>
              </div>
            )}
          </div>
        </div>

        <div className="space-y-6">
          <div className="bg-[#0F172A] border border-slate-800/50 rounded-2xl p-4">
            <div className="space-y-3">
              <div className="flex items-center justify-between gap-4">
                <span className="text-[10px] font-bold uppercase tracking-widest text-slate-500">Status</span>
                <span className="flex items-center gap-2 text-xs font-semibold capitalize text-white">
                  <span className={`h-1.5 w-1.5 rounded-full ${contestStatusDot}`} />
                  {selectedContest.status}
                </span>
              </div>
              <div className="flex items-center justify-between gap-4">
                <span className="flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-widest text-slate-500">
                  <Calendar size={12} className="text-slate-600" />
                  Start
                </span>
                <span className="text-right text-xs font-medium text-slate-300">{formatDateTime(selectedContest.starts_at)}</span>
              </div>
              <div className="flex items-center justify-between gap-4">
                <span className="flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-widest text-slate-500">
                  <Calendar size={12} className="text-slate-600" />
                  End
                </span>
                <span className="text-right text-xs font-medium text-slate-300">{formatDateTime(selectedContest.ends_at)}</span>
              </div>
            </div>
          </div>

          <div className="bg-[#0F172A] border border-slate-800/50 rounded-2xl p-6">
            <h3 className="text-sm font-bold text-white uppercase tracking-wider mb-4">Contest Stats</h3>
            <div className="space-y-4">
              <div className="flex justify-between items-center">
                <span className="text-xs text-slate-500">High Severity</span>
                <span className="text-xs font-mono text-red-500">{issues.filter(i => i.severity === 'High').length}</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-xs text-slate-500">Medium Severity</span>
                <span className="text-xs font-mono text-orange-500">{issues.filter(i => i.severity === 'Medium').length}</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-xs text-slate-500">Low Severity</span>
                <span className="text-xs font-mono text-yellow-500">{issues.filter(i => i.severity === 'Low').length}</span>
              </div>
              <div className="pt-4 border-t border-slate-800/50 flex justify-between items-center">
                <span className="text-xs text-white font-bold">Total Issues</span>
                <span className="text-xs font-mono text-white">{issues.length}</span>
              </div>
            </div>
          </div>

          {selectedBot && (
            <div className="bg-pink-500/5 border border-pink-500/20 rounded-2xl p-6">
              <h3 className="text-sm font-bold text-pink-500 uppercase tracking-wider mb-4">Bot Strategy</h3>
              <p className="text-xs text-pink-500/70 leading-relaxed">
                This bot is optimized for {selectedBot.platform} rules. It prioritizes high-impact logic errors and access control vulnerabilities that are frequently rewarded in these contests.
              </p>
            </div>
          )}
          
          <div className="bg-[#0F172A] border border-slate-800/50 rounded-2xl flex flex-col max-h-[400px]">
            <div className="p-4 border-b border-slate-800/50 flex items-center justify-between">
              <h3 className="text-sm font-bold text-white uppercase tracking-wider flex items-center gap-2">
                <Bell size={16} className="text-pink-500" />
                Platform Responses
              </h3>
              <button
                onClick={forcePing}
                disabled={isPinging}
                className="px-3 py-1.5 bg-pink-500/10 hover:bg-pink-500/20 text-pink-500 text-[10px] font-bold uppercase rounded flex items-center gap-1 disabled:opacity-50 transition-colors"
              >
                {isPinging ? (
                  <RefreshCw size={12} className="animate-spin" />
                ) : (
                  <Zap size={12} />
                )}
                {isPinging ? 'Pinging...' : 'Force Ping'}
              </button>
            </div>
            <div className="flex-1 overflow-y-auto p-4 space-y-3">
              {notifications.map(n => (
                <div 
                  key={n.id} 
                  onClick={async () => {
                    if (!n.is_read) {
                      await fetch(`/api/notifications/${n.id}/read`, { method: 'PATCH' });
                      fetchNotifications(contestId);
                    }
                  }}
                  className={`p-3 rounded-lg border text-sm cursor-pointer transition-colors ${n.is_read ? 'bg-slate-900/50 border-slate-800/50 text-slate-500' : 'bg-pink-500/10 border-pink-500/30 text-white'}`}
                >
                  <div className="flex gap-2 items-start">
                    <MessageSquare size={14} className={`mt-0.5 shrink-0 ${n.is_read ? 'text-slate-600' : 'text-pink-500'}`} />
                    <div>
                      <p className="leading-snug">
                        {n.message.length > 300 ? n.message.substring(0, 300) + '...' : n.message}
                      </p>
                      <span className="text-[10px] text-slate-500 mt-1 block">
                        {new Date(n.created_at).toLocaleString()}
                      </span>
                    </div>
                  </div>
                </div>
              ))}
              {notifications.length === 0 && (
                  <div className="text-center text-slate-500 text-xs py-8">No responses from platform yet. Ping to check.</div>
              )}
            </div>
          </div>
        </div>
      </div>

      <PreviewSubmitModal
        isOpen={isPreviewingSubmit}
        onClose={() => setIsPreviewingSubmit(false)}
        issues={issues}
        selectedIssueIds={selectedIssueIds}
        onSubmit={handleBulkSubmit}
      />

      <BatchVerificationModal
        isOpen={isJobModalOpen}
        onClose={() => {
          setIsJobModalOpen(false);
          setActiveJobId(null);
          setCurrentBatchIds([]);
        }}
        jobId={activeJobId}
        issues={issues.filter(i => currentBatchIds.includes(i.id))}
        onComplete={() => {
          setVerifyingIssueIds(prev => {
            const next = new Set(prev);
            currentBatchIds.forEach(id => next.delete(id));
            return next;
          });
          fetchIssues(contestId);
          fetchNotifications(contestId);
        }}
      />
    </motion.div>
  );
};
