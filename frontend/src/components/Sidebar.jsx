import { useNavigate, useLocation } from 'react-router-dom';
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
  { path: '/bugs', icon: Bug, label: 'Bugs' },
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
    <aside className="w-60 min-h-screen sticky top-0 flex flex-col bg-surface border-r border-white/10 shrink-0">
      <div className="px-5 py-4 border-b border-white/10">
        <div className="flex items-center gap-2.5">
          <div className="grid place-items-center w-8 h-8 rounded-md bg-[#2f6fed]">
            <ShieldCheck className="w-4 h-4 text-white" strokeWidth={2.2} />
          </div>
          <span className="text-[15px] font-semibold text-slate-100">QA Platform</span>
        </div>
      </div>

      <div className="px-5 py-3 border-b border-white/10">
        <p className="text-sm text-slate-200 truncate">{user.username || 'Guest'}</p>
        <p className="text-xs text-slate-500 capitalize">{user.role || 'tester'}</p>
      </div>

      <nav className="flex-1 px-2 py-3">
        <ul className="space-y-0.5">
          {links.map((item) => {
            const Icon = item.icon;
            const active = location.pathname === item.path;
            return (
              <li key={item.path}>
                <button
                  onClick={() => navigate(item.path)}
                  aria-current={active ? 'page' : undefined}
                  className={
                    'w-full flex items-center gap-2.5 px-3 py-2 rounded-md text-left text-sm transition-colors ' +
                    (active
                      ? 'bg-white/10 text-slate-100'
                      : 'text-slate-400 hover:text-slate-200 hover:bg-white/5')
                  }
                >
                  <Icon className="w-4 h-4 shrink-0" strokeWidth={2} />
                  <span>{item.label}</span>
                </button>
              </li>
            );
          })}
        </ul>
      </nav>

      <div className="p-2 border-t border-white/10">
        <button
          onClick={handleLogout}
          className="w-full flex items-center gap-2.5 px-3 py-2 rounded-md text-sm text-slate-400 hover:text-slate-200 hover:bg-white/5 transition-colors"
        >
          <LogOut className="w-4 h-4 shrink-0" strokeWidth={2} />
          <span>Log out</span>
        </button>
      </div>
    </aside>
  );
};

export default Sidebar;
