import { useNavigate, useLocation } from 'react-router-dom';
import { useState, useEffect } from 'react';
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

const Sidebar = ({ isOpen: controlledIsOpen, onClose }) => {
  const navigate = useNavigate();
  const location = useLocation();
  const [isMobile, setIsMobile] = useState(false);
  const [isOpen, setIsOpen] = useState(false);

  useEffect(() => {
    const checkMobile = () => setIsMobile(window.innerWidth < 768);
    checkMobile();
    window.addEventListener('resize', checkMobile);
    return () => window.removeEventListener('resize', checkMobile);
  }, []);

  // Use controlled state if provided, otherwise use internal state
  const isSidebarOpen = controlledIsOpen !== undefined ? controlledIsOpen : isOpen;
  const setSidebarOpen = controlledIsOpen !== undefined ? onClose : setIsOpen;

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
    setSidebarOpen(false);
  };

  const handleNavClick = () => {
    if (isMobile) setSidebarOpen(false);
  };

  const sidebarWidth = isMobile ? 'w-72' : 'w-64';
  const sidebarTransform = isMobile && !isSidebarOpen ? 'translate-x-[-100%]' : 'translate-x-0';

  return (
    <>
      {isMobile && isSidebarOpen && (
        <div
          className="fixed inset-0 bg-black/50 z-30 lg:hidden"
          onClick={() => setSidebarOpen(false)}
          aria-hidden="true"
        />
      )}
      <aside
        className={`${sidebarWidth} h-screen fixed top-0 left-0 z-40 flex flex-col glass-strong border-r border-white/10 shrink-0 overflow-y-auto transition-transform duration-300 ease-in-out lg:translate-x-0 ${sidebarTransform}`}
      >
      {/* Logo */}
      <div className="p-6 border-b border-white/10">
        <div className="flex items-center gap-3">
          <motion.div
            initial={{ scale: 0.8, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            transition={{ duration: 0.3 }}
            className="grid place-items-center w-10 h-10 rounded-xl bg-brand-gradient shadow-glow"
          >
            <ShieldCheck className="w-5 h-5 text-white" strokeWidth={2.2} />
          </motion.div>
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
          <div className="relative">
            <div className="avatar avatar-md">
              {user.username?.[0]?.toUpperCase() || '?'}
            </div>
            <span className="absolute -bottom-0.5 -right-0.5 w-3.5 h-3.5 rounded-full bg-emerald-400 border-2 border-surface" />
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
                  onClick={() => {
                    navigate(item.path);
                    handleNavClick();
                  }}
                  aria-current={active ? 'page' : undefined}
                  className={`group relative w-full flex items-center gap-3 px-4 py-2.5 rounded-xl text-left transition-all duration-200 ${
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
                  <span className={`relative grid place-items-center w-8 h-8 rounded-lg transition-all duration-200 ${
                    active
                      ? 'bg-brand-indigo/20 text-brand-sky'
                      : 'text-slate-400 group-hover:text-slate-200 group-hover:bg-white/5'
                  }`}>
                    <Icon className="w-[18px] h-[18px]" strokeWidth={2} />
                  </span>
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
          className="group w-full flex items-center gap-3 px-4 py-2.5 rounded-xl text-slate-400 hover:text-red-300 hover:bg-red-500/10 transition-all duration-200"
        >
          <LogOut className="w-[18px] h-[18px] shrink-0" strokeWidth={2} />
          <span className="text-sm font-medium">Logout</span>
        </button>
      </div>
    </aside>
    </>
  );
};

export default Sidebar;
