import { useNavigate, useLocation } from 'react-router-dom';
import { motion } from 'framer-motion';
import {
  LayoutDashboard,
  FolderKanban,
  ClipboardList,
  Bug,
  Sparkles,
  BarChart3,
  LogOut,
  ShieldCheck,
  Shield,
} from 'lucide-react';

const navItems = [
  { path: '/dashboard', icon: LayoutDashboard, label: 'Dashboard' },
  { path: '/projects', icon: FolderKanban, label: 'Projects' },
  { path: '/testcases', icon: ClipboardList, label: 'Test Cases' },
  { path: '/bugs', icon: Bug, label: 'Bug Tracker' },
  { path: '/ai-assistant', icon: Sparkles, label: 'AI Assistant' },
  { path: '/reports', icon: BarChart3, label: 'Reports' },
];

// Shown only to administrators. Hiding the link does not protect the data:
// the server checks the role on every admin request.
const adminNavItem = { path: '/admin', icon: Shield, label: 'Admin' };

const Sidebar = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const getUser = () => {
    try {
      return JSON.parse(localStorage.getItem('user') || '{}');
    } catch {
      return {};
    }
  };
  const user = getUser();
  const links = user.role === 'admin' ? [...navItems, adminNavItem] : navItems;

  const handleLogout = () => {
    localStorage.removeItem('token');
    localStorage.removeItem('user');
    navigate('/login');
  };

  return (
    <aside className="w-64 min-h-screen sticky top-0 flex flex-col glass-strong border-r border-white/10 shrink-0">
      {/* Logo */}
      <div className="p-6 border-b border-white/10">
        <div className="flex items-center gap-3">
          <div className="grid place-items-center w-10 h-10 rounded-xl bg-brand-gradient shadow-glow">
            <ShieldCheck className="w-5 h-5 text-white" strokeWidth={2.2} />
          </div>
          <div>
            <h1 className="text-lg font-display font-bold leading-none text-white">
              QA Platform
            </h1>
            <p className="text-slate-400 text-xs mt-1">Testing, intelligently</p>
          </div>
        </div>
      </div>

      {/* User Info */}
      <div className="p-4 border-b border-white/10">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-full bg-gradient-to-br from-brand-indigo to-brand-teal grid place-items-center font-semibold text-white">
            {user.username?.[0]?.toUpperCase() || '?'}
          </div>
          <div className="min-w-0">
            <p className="font-medium text-slate-100 truncate">{user.username || 'Guest'}</p>
            <p className="text-slate-400 text-xs capitalize">{user.role || 'tester'}</p>
          </div>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 p-3">
        <ul className="space-y-1">
          {links.map((item) => {
            const Icon = item.icon;
            const active = location.pathname === item.path;
            return (
              <li key={item.path}>
                <button
                  onClick={() => navigate(item.path)}
                  aria-current={active ? 'page' : undefined}
                  className={`group relative w-full flex items-center gap-3 px-4 py-2.5 rounded-xl text-left transition-colors ${
                    active
                      ? 'text-white'
                      : 'text-slate-400 hover:text-slate-100 hover:bg-white/5'
                  }`}
                >
                  {active && (
                    <motion.span
                      layoutId="sidebar-active"
                      transition={{ type: 'spring', stiffness: 500, damping: 38 }}
                      className="absolute inset-0 rounded-xl bg-gradient-to-r from-brand-indigo/25 to-brand-teal/10 border border-brand-indigo/40 shadow-glow"
                    />
                  )}
                  {active && (
                    <span className="absolute left-0 top-1/2 -translate-y-1/2 h-6 w-1 rounded-full bg-brand-sky shadow-glow-sky" />
                  )}
                  <Icon
                    className={`relative w-[18px] h-[18px] shrink-0 transition-colors ${
                      active ? 'text-brand-sky' : 'text-slate-400 group-hover:text-slate-200'
                    }`}
                    strokeWidth={2}
                  />
                  <span className="relative text-sm font-medium">{item.label}</span>
                </button>
              </li>
            );
          })}
        </ul>
      </nav>

      {/* Logout */}
      <div className="p-3 border-t border-white/10">
        <button
          onClick={handleLogout}
          className="group w-full flex items-center gap-3 px-4 py-2.5 rounded-xl text-slate-400 hover:text-red-300 hover:bg-red-500/10 transition-colors"
        >
          <LogOut className="w-[18px] h-[18px] shrink-0" strokeWidth={2} />
          <span className="text-sm font-medium">Logout</span>
        </button>
      </div>
    </aside>
  );
};

export default Sidebar;
