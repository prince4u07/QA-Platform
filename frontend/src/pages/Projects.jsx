import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { motion } from 'framer-motion';
import {
  Plus,
  FolderOpen,
  ShieldCheck,
  Unlock,
  ExternalLink,
  Lock,
  Lightbulb,
  KeyRound,
  RefreshCw,
  Loader2,
  Pencil,
  Trash2,
  ChevronLeft,
  ChevronRight,
  X,
  Check,
  Menu,
} from 'lucide-react';
import Sidebar from '../components/Sidebar';
import AmbientBackground from '../components/AmbientBackground';
import {
  getProjects,
  createProject,
  updateProject,
  deleteProject,
  startLogin,
  saveSession,
  cancelLogin,
} from '../api/projects';

const Projects = () => {
  const navigate = useNavigate();
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const [projects, setProjects] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);

  // Pagination state
  const [currentPage, setCurrentPage] = useState(1);
  const [totalPages, setTotalPages] = useState(0);
  const [totalProjects, setTotalProjects] = useState(0);
  const PROJECTS_PER_PAGE = 10;

  const [name, setName] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [description, setDescription] = useState('');

  // For "Open & Login" button feedback
  const [openingLoginFor, setOpeningLoginFor] = useState(null);
  // The project whose manual-login window is currently open (drives the prompt modal)
  const [loginProject, setLoginProject] = useState(null);
  const [savingSession, setSavingSession] = useState(false);

  // Bumped to trigger a re-fetch of the project list from outside the effect.
  const [reloadFlag, setReloadFlag] = useState(0);
  const reloadProjects = () => setReloadFlag((f) => f + 1);

  useEffect(() => {
    if (!localStorage.getItem('token')) {
      navigate('/login');
      return;
    }

    const fetchProjects = async () => {
      try {
        setLoading(true);
        const res = await getProjects(currentPage, PROJECTS_PER_PAGE);
        setProjects(res.data.data);
        setTotalPages(res.data.pagination.pages);
        setTotalProjects(res.data.pagination.total);
      } catch (error) {
        if (error.response?.status === 401) {
          localStorage.clear();
          navigate('/login');
        } else {
          setError('Failed to load projects');
        }
      } finally {
        setLoading(false);
      }
    };

    fetchProjects();
  }, [navigate, currentPage, reloadFlag]);

  const isUrlValid = /^https?:\/\/.+\..+/.test(baseUrl);
  const isNameValid = name.trim().length >= 3;

  const canSubmit = () => !saving && isNameValid && isUrlValid;

  const resetForm = () => {
    setName('');
    setBaseUrl('');
    setDescription('');
    setEditingId(null);
    setError('');
  };

  const openCreateModal = () => {
    resetForm();
    setShowModal(true);
  };

  const openEditModal = (p) => {
    setName(p.name);
    setBaseUrl(p.base_url);
    setDescription(p.description || '');
    setEditingId(p.id);
    setError('');
    setShowModal(true);
  };

  const closeModal = () => {
    if (saving) return;
    setShowModal(false);
    resetForm();
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!canSubmit()) return;
    setSaving(true);
    setError('');

    const payload = {
      name: name.trim(),
      base_url: baseUrl.trim(),
      description: description.trim(),
    };

    try {
      if (editingId) {
        await updateProject(editingId, payload);
      } else {
        await createProject(payload);
      }
      closeModal();
      setCurrentPage(1); // Reset to first page
      reloadProjects();
    } catch (_) {
      setError(_.response?.data?.error || 'Failed to save project');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id, projectName) => {
    if (!window.confirm('Delete project "' + projectName + '"? This will also delete all test cases and bugs.')) {
      return;
    }
    try {
      await deleteProject(id);
      setCurrentPage(1); // Reset to first page
      reloadProjects();
    } catch {
      alert('Failed to delete project');
    }
  };

  // Open a headed Chromium window where the user logs in manually.
  const handleOpenLogin = async (project) => {
    setOpeningLoginFor(project.id);
    try {
      await startLogin(project.id);
      // Window is open in the background; show the prompt to confirm/cancel.
      setLoginProject(project);
    } catch (err) {
      alert(err.response?.data?.error || 'Failed to open the login window.');
    } finally {
      setOpeningLoginFor(null);
    }
  };

  // User finished logging in -> persist the session cookies.
  const handleSaveSession = async () => {
    if (!loginProject) return;
    setSavingSession(true);
    try {
      await saveSession(loginProject.id);
      setLoginProject(null);
      reloadProjects(); // refresh badges (has_active_session / captured_at)
    } catch (err) {
      alert(err.response?.data?.error || 'Failed to save session. Try logging in again.');
    } finally {
      setSavingSession(false);
    }
  };

  // Close the login window without saving.
  const handleCancelLogin = async () => {
    if (!loginProject) return;
    const id = loginProject.id;
    setLoginProject(null);
    try {
      await cancelLogin(id);
    } catch {
      // Best-effort; the window auto-closes on timeout anyway.
    }
  };

  // Format the session captured_at date
  const formatSessionAge = (capturedAt) => {
    if (!capturedAt) return null;
    try {
      const captured = new Date(capturedAt);
      const now = new Date();
      const diffMs = now - captured;
      const diffMins = Math.floor(diffMs / 60000);
      const diffHours = Math.floor(diffMins / 60);
      const diffDays = Math.floor(diffHours / 24);
      if (diffDays > 0) return diffDays + ' day' + (diffDays > 1 ? 's' : '') + ' ago';
      if (diffHours > 0) return diffHours + 'h ago';
      if (diffMins > 0) return diffMins + 'm ago';
      return 'just now';
    } catch {
      return null;
    }
  };

  // ----- shared styles -----
  const inputBase =
    'w-full rounded-xl bg-white/5 border border-white/10 px-4 py-2.5 text-sm text-slate-100 placeholder:text-slate-500 ' +
    'focus:outline-none focus:ring-2 focus:ring-brand-sky/60 focus:border-brand-sky/40 transition';

  return (
    <div className="relative flex h-screen overflow-hidden">
      <AmbientBackground />
      <Sidebar isOpen={sidebarOpen} onClose={() => setSidebarOpen(false)} />

      <div className="flex-1 px-6 py-6 max-w-6xl overflow-y-auto lg:ml-64 mx-auto">
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
              <h1 className="text-3xl font-display font-bold text-white">Test <span className="text-gradient">projects</span></h1>
              <p className="text-sm text-slate-400 mt-1">
                {totalProjects > 0 ? `${totalProjects} total` : 'Register a site to start auditing it'}
              </p>
            </div>
          </div>
          <motion.button
            whileHover={{ scale: 1.03 }}
            whileTap={{ scale: 0.97 }}
            onClick={openCreateModal}
            className="bg-brand-gradient text-white shadow-glow hover:shadow-glow-teal flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-medium transition-all"
          >
            <Plus className="w-4 h-4" /> New project
          </motion.button>
        </motion.div>

        {loading ? (
          <div className="flex items-center justify-center gap-2 py-20 text-slate-500 text-sm">
            <Loader2 className="w-4 h-4 animate-spin" /> Loading projects…
          </div>
        ) : projects.length === 0 ? (
          <motion.div
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.4 }}
            className="glass rounded-2xl p-10 text-center"
          >
            <div className="grid place-items-center w-11 h-11 mx-auto rounded-xl bg-brand-indigo/15 text-brand-sky mb-3">
              <FolderOpen className="w-5 h-5" />
            </div>
            <h3 className="text-[15px] font-semibold text-white mb-1">No projects yet</h3>
            <p className="text-sm text-slate-400 mb-5">
              Add your first site to run tests against it.
            </p>
            <motion.button
              whileHover={{ scale: 1.03 }}
              whileTap={{ scale: 0.97 }}
              onClick={openCreateModal}
              className="bg-brand-gradient text-white shadow-glow hover:shadow-glow-teal inline-flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-medium transition-all"
            >
              <Plus className="w-4 h-4" /> Create project
            </motion.button>
          </motion.div>
        ) : (
          <div>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {projects.map((p, i) => (
                <motion.div
                  key={p.id}
                  initial={{ opacity: 0, y: 12 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.35, delay: Math.min(i * 0.05, 0.3) }}
                  whileHover={{ y: -4 }}
                  className="h-full"
                >
                  <div className="glass rounded-2xl p-5 h-full flex flex-col hover:bg-white/[0.07] hover:border-brand-indigo/40 hover:shadow-glow transition-all">
                      <div className="flex justify-between items-start mb-3 gap-2">
                        <h3 className="text-[15px] font-semibold text-white truncate flex-1">
                          {p.name}
                        </h3>
                      </div>

                      <div className="mb-2.5 flex flex-wrap gap-1.5">
                        {p.has_active_session ? (
                          <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-300 border border-emerald-500/20">
                            <ShieldCheck className="w-3.5 h-3.5" /> Logged in
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded bg-white/5 text-slate-500 border border-white/10">
                            <Unlock className="w-3.5 h-3.5" /> No session
                          </span>
                        )}
                      </div>

                      <a
                        href={p.base_url}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center gap-1.5 text-[13px] text-brand-sky hover:text-brand-teal hover:underline break-all mb-2.5 transition-colors"
                      >
                        <ExternalLink className="w-3.5 h-3.5 shrink-0" />
                        {p.base_url && p.base_url.length > 46 ? p.base_url.substring(0, 46) + '...' : p.base_url}
                      </a>

                      {p.description && (
                        <p className="text-[13px] text-slate-500 mb-2.5 line-clamp-2">{p.description}</p>
                      )}

                      {/* Session info text */}
                      {p.has_active_session && p.session_captured_at ? (
                        <div className="flex items-center gap-1.5 text-xs text-emerald-300/90 mb-2.5">
                          <Lock className="w-3.5 h-3.5" /> Session saved {formatSessionAge(p.session_captured_at)}
                        </div>
                      ) : !p.has_active_session ? (
                        <div className="flex items-center gap-1.5 text-xs text-slate-500 mb-2.5">
                          <Lightbulb className="w-3.5 h-3.5" /> Log in first if the site needs auth
                        </div>
                      ) : null}

                      <div className="mt-auto">
                        <button
                          onClick={() => handleOpenLogin(p)}
                          disabled={openingLoginFor === p.id}
                          className={
                            'w-full flex items-center justify-center gap-2 text-[13px] py-2 rounded-xl font-medium transition-all mb-2.5 border ' +
                            (openingLoginFor === p.id
                              ? 'bg-white/5 text-slate-400 border-white/10 cursor-wait'
                              : p.has_active_session
                                ? 'bg-white/5 hover:bg-brand-indigo/20 hover:border-brand-indigo/40 hover:text-white text-slate-200 border-white/10'
                                : 'bg-brand-gradient text-white shadow-glow hover:shadow-glow-teal border-transparent')
                          }
                        >
                          {openingLoginFor === p.id ? (
                            <><Loader2 className="w-4 h-4 animate-spin" /> Opening browser…</>
                          ) : p.has_active_session ? (
                            <><RefreshCw className="w-4 h-4" /> Re-login</>
                          ) : (
                            <><KeyRound className="w-4 h-4" /> Open &amp; log in</>
                          )}
                        </button>

                        <div className="flex gap-2 pt-2.5 border-t border-white/10">
                          <button
                            onClick={() => openEditModal(p)}
                            className="flex-1 flex items-center justify-center gap-1.5 text-[13px] bg-transparent hover:bg-brand-sky/15 hover:text-white hover:border-brand-sky/40 text-slate-300 border border-white/10 py-1.5 rounded-xl transition-all"
                          >
                            <Pencil className="w-3.5 h-3.5" /> Edit
                          </button>
                          <button
                            onClick={() => handleDelete(p.id, p.name)}
                            className="flex-1 flex items-center justify-center gap-1.5 text-[13px] bg-transparent hover:bg-red-500/15 text-slate-400 hover:text-red-200 border border-white/10 hover:border-red-500/40 hover:shadow-glow py-1.5 rounded-xl transition-all"
                          >
                            <Trash2 className="w-3.5 h-3.5" /> Delete
                          </button>
                        </div>
                      </div>
                    </div>
                  </motion.div>
              ))}
            </div>

            {/* Pagination Controls */}
            {totalPages > 1 && (
              <div className="flex items-center gap-1.5 mt-6 flex-wrap">
                <button
                  onClick={() => setCurrentPage((prev) => Math.max(1, prev - 1))}
                  disabled={currentPage === 1}
                  className="btn-ghost flex items-center gap-1 px-3 py-1.5 text-[13px] disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  <ChevronLeft className="w-4 h-4" /> Prev
                </button>
                <div className="flex items-center gap-1">
                  {Array.from({ length: totalPages }, (_, i) => i + 1).map((page) => (
                    <button
                      key={page}
                      onClick={() => setCurrentPage(page)}
                      className={
                        'w-8 h-8 rounded-xl text-[13px] transition-all border ' +
                        (currentPage === page
                          ? 'bg-brand-gradient text-white shadow-glow border-transparent font-medium'
                          : 'bg-transparent hover:bg-white/10 hover:border-brand-indigo/40 text-slate-400 border-white/10')
                      }
                    >
                      {page}
                    </button>
                  ))}
                </div>
                <button
                  onClick={() => setCurrentPage((prev) => Math.min(totalPages, prev + 1))}
                  disabled={currentPage === totalPages}
                  className="btn-ghost flex items-center gap-1 px-3 py-1.5 text-[13px] disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  Next <ChevronRight className="w-4 h-4" />
                </button>
                <span className="ml-2 text-[13px] text-slate-500">
                  Page {currentPage} of {totalPages} ({totalProjects} total)
                </span>
              </div>
            )}
          </div>
        )}
      </div>

      {/* CREATE / EDIT MODAL */}
      {showModal && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4 z-50">
          <motion.div
            initial={{ opacity: 0, scale: 0.97, y: 10 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            transition={{ duration: 0.25 }}
            className="glass-strong rounded-2xl shadow-card w-full max-w-lg max-h-screen overflow-y-auto"
          >
            <div className="px-5 py-4 border-b border-white/10 flex items-start justify-between gap-4">
              <div>
                <h2 className="text-[15px] font-semibold text-slate-100">
                  {editingId ? 'Edit project' : 'New project'}
                </h2>
                <p className="text-[13px] text-slate-500 mt-0.5">
                  Site details. You can save a login session afterwards.
                </p>
              </div>
              <button
                type="button"
                onClick={closeModal}
                className="text-slate-500 hover:text-slate-200 transition"
                aria-label="Close"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <form onSubmit={handleSubmit} className="p-5 space-y-4">
              {error && (
                <div className="bg-red-500/10 border border-red-500/25 text-red-300 px-3 py-2 rounded-md text-[13px]">
                  {error}
                </div>
              )}

              <div>
                <label className="block text-slate-300 text-[13px] font-medium mb-1">Name *</label>
                <input
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="e.g. Marketing site"
                  className={`${inputBase} ${
                    name && !isNameValid
                      ? 'border-red-500/50'
                      : 'border-white/10'
                  }`}
                />
                {name && !isNameValid && (
                  <p className="text-red-400 text-xs mt-1">At least 3 characters.</p>
                )}
              </div>

              <div>
                <label className="block text-slate-300 text-[13px] font-medium mb-1">URL *</label>
                <input
                  type="text"
                  value={baseUrl}
                  onChange={(e) => setBaseUrl(e.target.value)}
                  placeholder="https://example.com"
                  className={`${inputBase} ${
                    baseUrl && !isUrlValid
                      ? 'border-red-500/50'
                      : 'border-white/10'
                  }`}
                />
                <p className="text-xs text-slate-500 mt-1">Test runs target this address.</p>
                {baseUrl && !isUrlValid && (
                  <p className="text-red-400 text-xs mt-1">Must start with http:// or https://</p>
                )}
              </div>

              <div>
                <label className="block text-slate-300 text-[13px] font-medium mb-1">Description</label>
                <textarea
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="What this project covers, which environments/URLs matter, anything your testers should know"
                  rows="3"
                  className={inputBase}
                />
              </div>

              {!editingId && (
                <div className="bg-white/[0.02] border border-white/10 rounded-md p-3 text-[13px]">
                  <p className="font-medium text-slate-200 flex items-center gap-1.5">
                    <Lightbulb className="w-4 h-4 text-slate-400" /> Login sessions
                  </p>
                  <p className="text-slate-500 mt-1 text-xs leading-relaxed">
                    After creating the project, use “Open &amp; log in” on the card if the
                    site needs auth. A browser opens, you sign in, and the session is kept
                    for test runs.
                  </p>
                </div>
              )}

              <div className="flex gap-2 pt-3 border-t border-white/10">
                <button
                  type="button"
                  onClick={closeModal}
                  disabled={saving}
                  className="btn-ghost flex-1 py-2 text-sm disabled:opacity-50"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={!canSubmit()}
                  className="btn-primary flex-1 py-2 text-sm flex items-center justify-center gap-2"
                >
                  {saving && <Loader2 className="w-4 h-4 animate-spin" />}
                  {saving ? 'Saving…' : editingId ? 'Save changes' : 'Create project'}
                </button>
              </div>
            </form>
          </motion.div>
        </div>
      )}

      {/* MANUAL LOGIN PROMPT */}
      {loginProject && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4 z-50">
          <motion.div
            initial={{ opacity: 0, scale: 0.97, y: 10 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            transition={{ duration: 0.25 }}
            className="glass-strong rounded-2xl shadow-card w-full max-w-md"
          >
            <div className="px-5 py-4 border-b border-white/10">
              <h2 className="text-[15px] font-semibold text-slate-100 flex items-center gap-2">
                <KeyRound className="w-4 h-4 text-slate-400" /> Log in to {loginProject.name}
              </h2>
            </div>

            <div className="p-5 space-y-3">
              <div className="bg-white/[0.02] border border-white/10 rounded-md p-3.5 text-[13px] text-slate-300">
                A browser window opened at{' '}
                <span className="font-mono text-[12px] break-all">{loginProject.base_url}</span>.
                <ol className="list-decimal list-inside mt-2 space-y-1 text-slate-400">
                  <li>Sign in there (SSO, OTP, captcha — whatever it uses).</li>
                  <li>Come back here and confirm to save the session.</li>
                </ol>
              </div>
              <p className="text-xs text-slate-500">
                The window closes on its own after 10 minutes.
              </p>

              <div className="flex gap-2 pt-1">
                <button
                  type="button"
                  onClick={handleCancelLogin}
                  disabled={savingSession}
                  className="btn-ghost flex-1 py-2 text-sm disabled:opacity-50"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={handleSaveSession}
                  disabled={savingSession}
                  className="btn-primary flex-1 py-2 text-sm flex items-center justify-center gap-2"
                >
                  {savingSession ? (
                    <><Loader2 className="w-4 h-4 animate-spin" /> Saving…</>
                  ) : (
                    <><Check className="w-4 h-4" /> I&apos;m logged in</>
                  )}
                </button>
              </div>
            </div>
          </motion.div>
        </div>
      )}
    </div>
  );
};

export default Projects;
