import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { motion } from 'framer-motion';
import {
  Users, UserCheck, UserX, Shield, Trash2, Loader2, RefreshCw, Menu,
  FolderKanban, Play, Bug, HeartPulse, AlertTriangle,
} from 'lucide-react';
import Sidebar from '../components/Sidebar';
import AmbientBackground from '../components/AmbientBackground';
import {
  isAdmin, getOverview, getUsers, getUserDetail, updateUser, deleteUser,
} from '../api/admin';

const Stat = ({ value, label, Icon }) => (
  <div className="glass rounded-2xl p-5">
    <div className="flex items-start justify-between">
      <div>
        <div className="text-2xl font-bold text-white">{value}</div>
        <div className="text-xs text-slate-400 mt-1">{label}</div>
      </div>
      <Icon className="w-5 h-5 text-brand-sky" />
    </div>
  </div>
);

const Admin = () => {
  const navigate = useNavigate();
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [overview, setOverview] = useState(null);
  const [users, setUsers] = useState([]);
  const [detail, setDetail] = useState(null);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [reloadFlag, setReloadFlag] = useState(0);

  // Deleting an account destroys every project, run and bug it owns, so it
  // asks for the username to be typed rather than relying on a confirm box.
  const [deleting, setDeleting] = useState(null);
  const [confirmName, setConfirmName] = useState('');

  useEffect(() => {
    if (!localStorage.getItem('token')) {
      navigate('/login');
      return;
    }
    // Not the security boundary, just avoids showing a page that would only
    // return 403. The server checks the role on every request regardless.
    if (!isAdmin()) {
      navigate('/dashboard');
      return;
    }

    const load = async () => {
      setRefreshing(true);
      try {
        const [o, u] = await Promise.all([getOverview(), getUsers()]);
        setOverview(o.data);
        setUsers(Array.isArray(u.data) ? u.data : []);
        setError('');
      } catch (err) {
        if (err.response?.status === 401) {
          localStorage.clear();
          navigate('/login');
        } else if (err.response?.status === 403) {
          navigate('/dashboard');
        } else {
          setError('Could not load the admin data.');
        }
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    };
    load();
  }, [navigate, reloadFlag]);

  const reload = () => setReloadFlag((f) => f + 1);

  const openUser = async (userId) => {
    try {
      const res = await getUserDetail(userId);
      setDetail(res.data);
    } catch {
      setError('Could not open that account.');
    }
  };

  const setActive = async (user, active) => {
    try {
      await updateUser(user.id, { is_active: active });
      setMessage(`${user.username} ${active ? 'reactivated' : 'deactivated'}`);
      reload();
    } catch (err) {
      setError(err.response?.data?.error || 'Could not update that account.');
    }
  };

  const setRole = async (user, role) => {
    try {
      await updateUser(user.id, { role });
      setMessage(`${user.username} is now ${role}`);
      reload();
    } catch (err) {
      setError(err.response?.data?.error || 'Could not change that role.');
    }
  };

  const confirmDelete = async () => {
    if (!deleting || confirmName !== deleting.username) return;
    try {
      await deleteUser(deleting.id, confirmName);
      setMessage(`Deleted ${deleting.username} and all their data`);
      setDeleting(null);
      setConfirmName('');
      setDetail(null);
      reload();
    } catch (err) {
      setError(err.response?.data?.error || 'Could not delete that account.');
    }
  };

  if (loading) {
    return (
      <div className="relative flex min-h-screen text-slate-200">
        <AmbientBackground />
        <Sidebar />
        <div className="flex-1 flex items-center justify-center">
          <div className="flex items-center gap-2 text-slate-400">
            <Loader2 className="w-5 h-5 animate-spin" /> Loading admin data...
          </div>
        </div>
      </div>
    );
  }

  const totals = overview?.totals || {};

  return (
    <div className="relative flex h-screen overflow-hidden text-slate-200">
      <AmbientBackground />
      <Sidebar isOpen={sidebarOpen} onClose={() => setSidebarOpen(false)} />

      <div className="flex-1 p-8 overflow-y-auto lg:ml-64 max-w-7xl mx-auto">
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4 }}
          className="flex justify-between items-center mb-6 gap-4"
        >
          <div className="flex items-center gap-4">
            <button
              className="lg:hidden p-2 rounded-lg text-slate-400 hover:text-white hover:bg-white/10 transition-colors"
              onClick={() => setSidebarOpen(true)}
              aria-label="Open menu"
            >
              <Menu className="w-6 h-6" />
            </button>
            <div>
              <h1 className="text-3xl font-display font-bold text-white flex items-center gap-2">
                <Shield className="w-6 h-6 text-brand-sky" /> Administration
              </h1>
              <p className="text-slate-400 mt-1 flex items-center gap-2">
              Every account on this platform
              {refreshing && (
                <span role="status" aria-live="polite"
                  className="inline-flex items-center gap-1.5 text-xs text-brand-sky">
                  <Loader2 className="w-3 h-3 animate-spin" /> Updating
                </span>
              )}
            </p>
            </div>
          </div>
          <button onClick={reload}
            className="inline-flex items-center gap-2 glass hover:bg-white/10 text-slate-200 px-5 py-2.5 rounded-xl font-medium transition">
            <RefreshCw className="w-4 h-4" /> Refresh
          </button>
        </motion.div>

        {error && (
          <div className="mb-4 rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-2.5 text-sm text-red-200">
            {error}
          </div>
        )}
        {message && (
          <div className="mb-4 rounded-xl border border-brand-teal/30 bg-brand-teal/10 px-4 py-2.5 text-sm text-brand-teal">
            {message}
          </div>
        )}

        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4 mb-8">
          <Stat value={`${totals.users_active ?? 0}/${totals.users_total ?? 0}`} label="Active users" Icon={Users} />
          <Stat value={totals.projects ?? 0} label="Projects" Icon={FolderKanban} />
          <Stat value={totals.runs ?? 0} label="Test runs" Icon={Play} />
          <Stat value={totals.bugs_open ?? 0} label="Open bugs" Icon={Bug} />
          <Stat value={totals.bugs ?? 0} label="Total bugs" Icon={Bug} />
          <Stat value={totals.avg_health ?? '—'} label="Avg health" Icon={HeartPulse} />
        </div>

        <div className="glass rounded-2xl p-5 mb-8">
          <h2 className="text-lg font-display font-bold text-white mb-4">Accounts</h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm min-w-[640px]">
              <thead>
                <tr className="text-xs text-slate-500 uppercase tracking-wider">
                  <th className="text-left pb-2 font-medium">User</th>
                  <th className="text-left pb-2 font-medium">Role</th>
                  <th className="text-right pb-2 font-medium pr-4">Projects</th>
                  <th className="text-right pb-2 font-medium pr-4">Tests</th>
                  <th className="text-right pb-2 font-medium pr-4">Runs</th>
                  <th className="text-right pb-2 font-medium">Actions</th>
                </tr>
              </thead>
              <tbody>
                {users.map((u) => (
                  <tr key={u.id} className="border-t border-white/10">
                    <td className="py-3">
                      <button onClick={() => openUser(u.id)}
                        className="text-left hover:text-brand-sky transition">
                        <div className="text-white font-medium flex items-center gap-2">
                          {u.username}
                          {u.is_self && <span className="text-xs text-slate-500">(you)</span>}
                          {!u.is_active && (
                            <span className="text-xs px-1.5 py-0.5 rounded bg-red-500/20 text-red-300">
                              deactivated
                            </span>
                          )}
                        </div>
                        <div className="text-xs text-slate-500">{u.email}</div>
                      </button>
                    </td>
                    <td className="py-3">
                      <span className={
                        'text-xs px-2 py-0.5 rounded ' +
                        (u.role === 'admin'
                          ? 'bg-brand-indigo/20 text-brand-indigo'
                          : 'bg-white/5 text-slate-400')
                      }>
                        {u.role}
                      </span>
                    </td>
                    <td className="py-3 text-right pr-4 text-slate-300">{u.projects}</td>
                    <td className="py-3 text-right pr-4 text-slate-300">{u.test_cases}</td>
                    <td className="py-3 text-right pr-4 text-slate-300">{u.runs}</td>
                    <td className="py-3 text-right">
                      {u.is_self ? (
                        // The server refuses these on your own account too.
                        // Hiding them here just avoids offering a dead button.
                        <span className="text-xs text-slate-600">your account</span>
                      ) : (
                        <div className="flex gap-1.5 justify-end">
                          <button onClick={() => setActive(u, !u.is_active)}
                            title={u.is_active ? 'Deactivate' : 'Reactivate'}
                            className="p-1.5 rounded-lg bg-white/5 hover:bg-white/10 text-slate-300">
                            {u.is_active ? <UserX className="w-4 h-4" /> : <UserCheck className="w-4 h-4" />}
                          </button>
                          <button onClick={() => setRole(u, u.role === 'admin' ? 'tester' : 'admin')}
                            title={u.role === 'admin' ? 'Make tester' : 'Make admin'}
                            className="p-1.5 rounded-lg bg-white/5 hover:bg-white/10 text-slate-300">
                            <Shield className="w-4 h-4" />
                          </button>
                          <button onClick={() => { setDeleting(u); setConfirmName(''); }}
                            title="Delete account and all data"
                            className="p-1.5 rounded-lg bg-red-500/10 hover:bg-red-500/20 text-red-300">
                            <Trash2 className="w-4 h-4" />
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div className="glass rounded-2xl p-5">
          <h2 className="text-lg font-display font-bold text-white mb-4">Recent activity, all accounts</h2>
          {(overview?.recent_runs || []).length === 0 ? (
            <p className="text-sm text-slate-500">No runs on the platform yet.</p>
          ) : (
            <div className="space-y-1.5">
              {overview.recent_runs.map((r) => (
                <div key={r.id} className="flex items-center gap-3 text-sm py-1.5 border-b border-white/5 last:border-0">
                  <span className={
                    'text-xs px-2 py-0.5 rounded flex-shrink-0 ' +
                    (r.status === 'Pass' ? 'bg-brand-teal/20 text-brand-teal' : 'bg-red-500/20 text-red-300')
                  }>
                    {r.status}
                  </span>
                  <span className="text-slate-300 truncate flex-1">{r.project} / {r.test_case}</span>
                  <span className="text-xs text-slate-500 flex-shrink-0">{r.owner}</span>
                  <span className="text-xs text-slate-400 w-10 text-right flex-shrink-0">{r.health_score}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* One account's detail */}
      {detail && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4 z-50"
          onClick={() => setDetail(null)}>
          <div className="glass-strong rounded-2xl w-full max-w-2xl max-h-[85vh] overflow-y-auto p-6"
            onClick={(e) => e.stopPropagation()}>
            <h2 className="text-xl font-display font-bold text-white mb-1">{detail.user.username}</h2>
            <p className="text-sm text-slate-400 mb-4">{detail.user.email} · {detail.user.role}</p>

            <h3 className="text-sm font-semibold text-slate-200 mt-4 mb-2">Projects</h3>
            {detail.projects.length === 0 ? (
              <p className="text-sm text-slate-500">No projects.</p>
            ) : detail.projects.map((p) => (
              <div key={p.id} className="flex justify-between text-sm py-1.5 border-b border-white/5">
                <span className="text-slate-200">{p.name}</span>
                <span className="text-slate-500 text-xs truncate max-w-[50%]">{p.base_url}</span>
              </div>
            ))}

            <h3 className="text-sm font-semibold text-slate-200 mt-5 mb-2">Recent runs</h3>
            {detail.runs.length === 0 ? (
              <p className="text-sm text-slate-500">No runs.</p>
            ) : detail.runs.map((r) => (
              <div key={r.id} className="flex justify-between text-sm py-1.5 border-b border-white/5">
                <span className="text-slate-300">{r.test_case}</span>
                <span className="text-slate-500 text-xs">{r.status} · {r.health_score}</span>
              </div>
            ))}

            <button onClick={() => setDetail(null)}
              className="mt-6 w-full bg-white/5 hover:bg-white/10 text-slate-200 py-2.5 rounded-xl transition">
              Close
            </button>
          </div>
        </div>
      )}

      {/* Deletion, typed confirmation */}
      {deleting && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4 z-50">
          <div className="glass-strong rounded-2xl w-full max-w-md p-6">
            <h2 className="text-lg font-display font-bold text-white flex items-center gap-2 mb-2">
              <AlertTriangle className="w-5 h-5 text-red-400" /> Delete {deleting.username}?
            </h2>
            <p className="text-sm text-slate-300 mb-1">
              This permanently removes their {deleting.projects} project(s), {deleting.runs} run(s)
              and every bug they logged. It cannot be undone.
            </p>
            <p className="text-sm text-slate-400 mb-4">
              Deactivating instead keeps their data and stops them signing in.
            </p>
            <label className="block text-xs text-slate-400 mb-1.5">
              Type <span className="font-mono text-slate-200">{deleting.username}</span> to confirm
            </label>
            <input value={confirmName} onChange={(e) => setConfirmName(e.target.value)}
              className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-white mb-4 focus:outline-none focus:border-red-500/50" />
            <div className="flex gap-2">
              <button onClick={() => { setDeleting(null); setConfirmName(''); }}
                className="flex-1 bg-white/5 hover:bg-white/10 text-slate-200 py-2.5 rounded-xl transition">
                Cancel
              </button>
              <button onClick={confirmDelete} disabled={confirmName !== deleting.username}
                className="flex-1 py-2.5 rounded-xl font-medium transition bg-red-500/20 text-red-200 hover:bg-red-500/30 disabled:opacity-40 disabled:cursor-not-allowed">
                Delete permanently
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default Admin;
