import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { motion } from 'framer-motion';
import {
  FolderKanban,
  ClipboardList,
  Bug,
  CheckCircle2,
  FolderPlus,
  FilePlus2,
  Sparkles,
  BarChart3,
  ArrowRight,
} from 'lucide-react';
import axios from '../api/axiosConfig';
import Sidebar from '../components/Sidebar';
import AmbientBackground from '../components/AmbientBackground';

const StatCard = ({ icon: Icon, label, value, accent, delay }) => (
  <motion.div
    initial={{ opacity: 0, y: 12 }}
    animate={{ opacity: 1, y: 0 }}
    transition={{ duration: 0.4, delay, ease: 'easeOut' }}
    whileHover={{ y: -3 }}
    className="glass rounded-2xl p-5 transition-colors hover:bg-white/[0.07] hover:border-brand-indigo/30 hover:shadow-glow cursor-default"
  >
    <div className="flex items-center justify-between">
      <div>
        <p className="text-slate-400 text-sm">{label}</p>
        <p className="text-3xl font-display font-bold text-white mt-1 tabular-nums">{value}</p>
      </div>
      <div className={`grid place-items-center w-12 h-12 rounded-xl ${accent}`}>
        <Icon className="w-6 h-6" strokeWidth={2} />
      </div>
    </div>
  </motion.div>
);

const QUICK_ACTIONS = [
  { label: 'New Project', hint: 'Register a site to audit', icon: FolderPlus, path: '/projects' },
  { label: 'Add Test Case', hint: 'Manual or automated', icon: FilePlus2, path: '/testcases' },
  { label: 'Log Bug', hint: 'File from a test run', icon: Bug, path: '/bugs' },
  { label: 'AI Suggestions', hint: 'Explain a failure', icon: Sparkles, path: '/ai-assistant' },
  { label: 'View Reports', hint: 'Runs and coverage', icon: BarChart3, path: '/reports' },
];

const Dashboard = () => {
  const navigate = useNavigate();
  const getUser = () => {
    try {
      return JSON.parse(localStorage.getItem('user') || '{}');
    } catch {
      return {};
    }
  };
  const user = getUser();
  const token = localStorage.getItem('token');

  const [stats, setStats] = useState({
    projects: 0,
    testCases: 0,
    bugs: 0,
    passed: 0,
    totalRuns: 0,
    manualRuns: 0,
    automatedRuns: 0,
    failed: 0,
    critical: 0,
    avgHealth: 0,
    automationCoverage: 0,
  });
  const [runTypeFilter, setRunTypeFilter] = useState('');
  const [severityFilter, setSeverityFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [filteredRuns, setFilteredRuns] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!token) {
      navigate('/login');
      return;
    }

    const fetchStats = async () => {
      try {
        const res = await axios.get('/projects/stats', {
          headers: { Authorization: `Bearer ${token}` }
        });
        let summary = {};
        try {
          const s = await axios.get('/reports/summary', {
            headers: { Authorization: `Bearer ${token}` }
          });
          summary = s.data || {};
        } catch {
          summary = {};
        }
        setStats({
          projects: res.data.total_projects,
          testCases: res.data.total_testcases,
          bugs: res.data.open_bugs,
          passed: res.data.tests_passed,
          totalRuns: summary.total_runs ?? 0,
          manualRuns: summary.manual_runs ?? 0,
          automatedRuns: summary.automated_runs ?? 0,
          failed: summary.failed ?? 0,
          critical: summary.critical_issues ?? 0,
          avgHealth: summary.avg_health_score ?? 0,
          automationCoverage: summary.automation_coverage ?? 0,
        });
      } catch (error) {
        console.error('Failed to load stats', error);
        if (error.response?.status === 401) {
          localStorage.clear();
          navigate('/login');
        }
      } finally {
        setLoading(false);
      }
    };

    fetchStats();
  }, [token, navigate]);

  useEffect(() => {
    if (!token) return;
    const load = async () => {
      try {
        const q = new URLSearchParams({
          ...(runTypeFilter ? { run_type: runTypeFilter } : {}),
          ...(severityFilter ? { severity: severityFilter } : {}),
          ...(statusFilter ? { status: statusFilter } : {}),
        }).toString();
        const res = await axios.get(`/reports/run-stats${q ? `?${q}` : ''}`, {
          headers: { Authorization: `Bearer ${token}` }
        });
        setFilteredRuns(res.data);
      } catch {
        setFilteredRuns(null);
      }
    };
    load();
  }, [token, runTypeFilter, severityFilter, statusFilter]);

  const v = (n) => (loading ? '—' : n);

  return (
    <div className="relative flex min-h-screen text-slate-200">
      <AmbientBackground />
      <Sidebar />

      <div className="flex-1 p-8 max-w-6xl">
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4 }}
          className="mb-8"
        >
          <h2 className="text-3xl font-display font-bold text-white">
            Welcome back, <span className="text-gradient">{user.username || 'tester'}</span>
          </h2>
          <p className="text-slate-400 mt-1">
            {loading ? 'Loading…' : `${stats.projects} projects · ${stats.testCases} test cases · ${stats.bugs} open bugs`}
          </p>
        </motion.div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-5 mb-8">
          <StatCard icon={FolderKanban} label="Total Projects" value={v(stats.projects)} accent="bg-brand-indigo/15 text-brand-indigo" delay={0} />
          <StatCard icon={ClipboardList} label="Test Cases" value={v(stats.testCases)} accent="bg-brand-sky/15 text-brand-sky" delay={0.05} />
          <StatCard icon={Bug} label="Open Bugs" value={v(stats.bugs)} accent="bg-rose-500/15 text-rose-400" delay={0.1} />
          <StatCard icon={CheckCircle2} label="Tests Passed" value={v(stats.passed)} accent="bg-brand-teal/15 text-brand-teal" delay={0.15} />
        </div>

        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, delay: 0.15 }}
          className="glass rounded-2xl p-6 mb-8"
        >
          <div className="flex items-center justify-between flex-wrap gap-3 mb-4">
            <div>
              <h3 className="text-xl font-display font-bold text-white">Run Statistics</h3>
              <p className="text-sm text-slate-500">
                {v(stats.totalRuns)} total runs · {v(stats.manualRuns)} manual · {v(stats.automatedRuns)} automated · {v(stats.failed)} failed · {v(stats.critical)} critical open · avg health {v(stats.avgHealth)} · automation {v(stats.automationCoverage)}%
              </p>
            </div>
            <select value={runTypeFilter} onChange={(e) => setRunTypeFilter(e.target.value)}
              className="bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none">
              <option value="" className="bg-slate-900">All runs</option>
              <option value="MANUAL" className="bg-slate-900">Manual only</option>
              <option value="AUTOMATED" className="bg-slate-900">Automated only</option>
            </select>
            <select value={severityFilter} onChange={(e) => setSeverityFilter(e.target.value)}
              className="bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none">
              <option value="" className="bg-slate-900">Any severity</option>
              <option value="critical" className="bg-slate-900">Critical</option>
              <option value="serious" className="bg-slate-900">Serious</option>
              <option value="moderate" className="bg-slate-900">Moderate</option>
              <option value="minor" className="bg-slate-900">Minor</option>
            </select>
            <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}
              className="bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none">
              <option value="" className="bg-slate-900">Any status</option>
              <option value="Pass" className="bg-slate-900">Passed</option>
              <option value="Fail" className="bg-slate-900">Failed</option>
            </select>
          </div>
          {filteredRuns && (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-center">
              {[
                ['Total', filteredRuns.total],
                ['Passed', filteredRuns.passed],
                ['Failed', filteredRuns.failed],
                ['Avg health', filteredRuns.avg_health_score],
              ].map(([label, value]) => (
                <div key={label} className="rounded-xl border border-white/10 bg-white/[0.03] p-3">
                  <div className="text-xs text-slate-500">{label}{runTypeFilter ? ` (${runTypeFilter})` : ''}</div>
                  <div className="text-xl font-bold text-white">{value}</div>
                </div>
              ))}
            </div>
          )}
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, delay: 0.2 }}
          className="glass rounded-2xl p-6"
        >
          <h3 className="text-xl font-display font-bold text-white mb-1">Quick Actions</h3>
          <p className="text-sm text-slate-500 mb-4">Jump into common tasks.</p>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {QUICK_ACTIONS.map((action, i) => {
              const Icon = action.icon || ClipboardList;
              return (
                <motion.button
                  key={action.path + action.label}
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.3, delay: 0.2 + i * 0.05 }}
                  whileHover={{ y: -2 }}
                  whileTap={{ scale: 0.98 }}
                  onClick={() => navigate(action.path)}
                  className="group flex items-center gap-3 p-4 rounded-xl border border-white/10 bg-white/[0.03] hover:bg-white/[0.07] hover:border-brand-indigo/40 hover:shadow-glow transition-all text-left"
                >
                  <span className="grid place-items-center w-9 h-9 rounded-lg bg-brand-indigo/15 text-brand-sky group-hover:text-brand-teal transition-colors">
                    <Icon className="w-[18px] h-[18px]" strokeWidth={2} />
                  </span>
                  <span className="min-w-0">
                    <span className="block font-medium text-slate-200">{action.label}</span>
                    {action.hint && <span className="block text-xs text-slate-500 mt-0.5">{action.hint}</span>}
                  </span>
                  <ArrowRight className="w-4 h-4 ml-auto shrink-0 text-slate-600 group-hover:text-brand-sky group-hover:translate-x-0.5 transition" />
                </motion.button>
              );
            })}
          </div>
        </motion.div>
      </div>
    </div>
  );
};

export default Dashboard;
