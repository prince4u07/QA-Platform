import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  FolderKanban,
  ClipboardList,
  Bug,
  CheckCircle2,
} from 'lucide-react';
import axios from '../api/axiosConfig';
import Sidebar from '../components/Sidebar';
import AmbientBackground from '../components/AmbientBackground';

const StatCard = ({ icon: Icon, label, value, sub }) => (
  <div className="card p-4">
    <div className="flex items-start justify-between gap-3">
      <div className="min-w-0">
        <p className="text-[13px] text-slate-400">{label}</p>
        <p className="text-2xl font-semibold text-slate-100 mt-1 tabular-nums">{value}</p>
        {sub && <p className="text-xs text-slate-500 mt-1">{sub}</p>}
      </div>
      <div className="grid place-items-center w-9 h-9 rounded-md bg-white/5 border border-white/10 text-slate-400 shrink-0">
        <Icon className="w-[18px] h-[18px]" strokeWidth={2} />
      </div>
    </div>
  </div>
);

const QUICK_ACTIONS = [
  { label: 'New project', hint: 'Register a site to audit', path: '/projects' },
  { label: 'Add test case', hint: 'Manual or automated', path: '/testcases' },
  { label: 'Log a bug', hint: 'File from a test run', path: '/bugs' },
  { label: 'Ask AI assistant', hint: 'Explain a failure', path: '/ai-assistant' },
  { label: 'View reports', hint: 'Runs and coverage', path: '/reports' },
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
    <div className="relative flex min-h-screen">
      <AmbientBackground />
      <Sidebar />

      <div className="flex-1 px-6 py-6 max-w-6xl">
        <div className="mb-6">
          <p className="text-xs text-slate-500 uppercase tracking-wide">Dashboard</p>
          <h1 className="text-xl font-semibold text-slate-100 mt-1">
            {user.username ? `${user.username}'s workspace` : 'Workspace'}
          </h1>
          <p className="text-sm text-slate-500 mt-1">
            {loading ? 'Loading…' : `${stats.projects} projects · ${stats.testCases} test cases · ${stats.bugs} open bugs`}
          </p>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
          <StatCard icon={FolderKanban} label="Projects" value={v(stats.projects)} />
          <StatCard icon={ClipboardList} label="Test cases" value={v(stats.testCases)} />
          <StatCard icon={Bug} label="Open bugs" value={v(stats.bugs)} sub={loading ? '' : stats.bugs === 0 ? 'Nothing open' : 'Needs triage'} />
          <StatCard icon={CheckCircle2} label="Tests passed" value={v(stats.passed)} />
        </div>

        <div className="card p-5">
          <h2 className="text-sm font-semibold text-slate-200">Shortcuts</h2>
          <p className="text-[13px] text-slate-500 mt-0.5 mb-4">Common tasks, nothing fancy.</p>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2.5">
            {QUICK_ACTIONS.map((action) => (
              <button
                key={action.path + action.label}
                onClick={() => navigate(action.path)}
                className="flex flex-col items-start text-left px-3.5 py-3 rounded-md border border-white/10 bg-white/[0.02] hover:bg-white/[0.05] hover:border-white/20 transition-colors"
              >
                <span className="text-sm font-medium text-slate-200">{action.label}</span>
                <span className="text-xs text-slate-500 mt-0.5">{action.hint}</span>
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
};

export default Dashboard;
