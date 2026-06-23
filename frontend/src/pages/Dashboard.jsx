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
    className="glass rounded-2xl p-5 transition-colors hover:bg-white/[0.07]"
  >
    <div className="flex items-center justify-between">
      <div>
        <p className="text-slate-400 text-sm">{label}</p>
        <p className="text-3xl font-display font-bold text-white mt-1">{value}</p>
      </div>
      <div className={`grid place-items-center w-12 h-12 rounded-xl ${accent}`}>
        <Icon className="w-6 h-6" strokeWidth={2} />
      </div>
    </div>
  </motion.div>
);

const QUICK_ACTIONS = [
  { label: 'New Project', icon: FolderPlus, path: '/projects' },
  { label: 'Add Test Case', icon: FilePlus2, path: '/testcases' },
  { label: 'Log Bug', icon: Bug, path: '/bugs' },
  { label: 'AI Suggestions', icon: Sparkles, path: '/ai-assistant' },
  { label: 'View Reports', icon: BarChart3, path: '/reports' },
];

const Dashboard = () => {
  const navigate = useNavigate();
  const user = JSON.parse(localStorage.getItem('user') || '{}');
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

      <div className="flex-1 p-8">
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4 }}
          className="mb-8"
        >
          <h2 className="text-3xl font-display font-bold text-white">
            Welcome back, <span className="text-gradient">{user.username || 'tester'}</span>
          </h2>
          <p className="text-slate-400 mt-1">Here&apos;s your QA platform overview</p>
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
          <h3 className="text-xl font-display font-bold text-white mb-4">Quick Actions</h3>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
            {QUICK_ACTIONS.map((action) => {
              const Icon = action.icon;
              return (
                <button
                  key={action.path}
                  onClick={() => navigate(action.path)}
                  className="group flex items-center gap-3 p-4 rounded-xl border border-white/10 bg-white/[0.03] hover:bg-white/[0.07] hover:border-brand-indigo/40 hover:shadow-glow transition-all"
                >
                  <span className="grid place-items-center w-9 h-9 rounded-lg bg-brand-indigo/15 text-brand-sky">
                    <Icon className="w-[18px] h-[18px]" strokeWidth={2} />
                  </span>
                  <span className="font-medium text-slate-200">{action.label}</span>
                  <ArrowRight className="w-4 h-4 ml-auto text-slate-600 group-hover:text-brand-sky group-hover:translate-x-0.5 transition" />
                </button>
              );
            })}
          </div>
        </motion.div>
      </div>
    </div>
  );
};

export default Dashboard;
