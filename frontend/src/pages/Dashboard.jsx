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
    passed: 0
  });
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
        setStats({
          projects: res.data.total_projects,
          testCases: res.data.total_testcases,
          bugs: res.data.open_bugs,
          passed: res.data.tests_passed
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
