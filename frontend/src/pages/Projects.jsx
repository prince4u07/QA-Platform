import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { motion } from 'framer-motion';
import Tilt from 'react-parallax-tilt';
import {
  Plus,
  FolderOpen,
  Globe,
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
  FolderPlus,
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
  const [requiresLogin, setRequiresLogin] = useState(false);

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

  const canSubmit = () => {
    if (saving) return false;
    if (!isNameValid) return false;
    return isUrlValid;
  };

  const resetForm = () => {
    setName('');
    setBaseUrl('');
    setDescription('');
    setRequiresLogin(false);
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
    setRequiresLogin(!!p.requires_login);
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

    try {
      if (editingId) {
        await updateProject(editingId, {
          name: name.trim(),
          base_url: baseUrl.trim(),
          description: description.trim(),
          requires_login: requiresLogin,
        });
      } else {
        await createProject({
          name: name.trim(),
          base_url: baseUrl.trim(),
          description: description.trim(),
          requires_login: requiresLogin,
        });
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
    'w-full rounded-xl bg-white/5 border px-4 py-2.5 text-slate-100 placeholder:text-slate-500 ' +
    'focus:outline-none focus:ring-2 transition';

  return (
    <div className="relative flex min-h-screen text-slate-200">
      <AmbientBackground />
      <Sidebar />

      <div className="flex-1 p-8">
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4 }}
          className="flex justify-between items-center mb-8 gap-4"
        >
          <div>
            <h1 className="text-3xl font-display font-bold text-white">Test Projects</h1>
            <p className="text-slate-400 mt-1">
              Test live websites — automated and manual QA
            </p>
          </div>
          <button
            onClick={openCreateModal}
            className="flex items-center gap-2 bg-brand-gradient text-white px-5 py-2.5 rounded-xl font-medium shadow-glow hover:shadow-glow-teal transition-all"
          >
            <Plus className="w-4 h-4" /> New Project
          </button>
        </motion.div>

        {loading ? (
          <div className="flex items-center justify-center gap-2 py-20 text-slate-400">
            <Loader2 className="w-5 h-5 animate-spin" /> Loading projects...
          </div>
        ) : projects.length === 0 ? (
          <div className="glass rounded-2xl p-12 text-center">
            <div className="grid place-items-center w-16 h-16 mx-auto rounded-2xl bg-brand-indigo/15 text-brand-indigo mb-4">
              <FolderOpen className="w-8 h-8" />
            </div>
            <h3 className="text-xl font-display font-semibold text-white mb-2">No projects yet</h3>
            <p className="text-slate-400 mb-6">
              Create your first test project to start automating tests
            </p>
            <button
              onClick={openCreateModal}
              className="inline-flex items-center gap-2 bg-brand-gradient text-white px-6 py-3 rounded-xl font-medium shadow-glow"
            >
              <Plus className="w-4 h-4" /> Create Your First Project
            </button>
          </div>
        ) : (
          <div>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
              {projects.map((p, i) => (
                <motion.div
                  key={p.id}
                  initial={{ opacity: 0, y: 14 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.35, delay: Math.min(i * 0.04, 0.3) }}
                >
                  <Tilt
                    tiltMaxAngleX={6}
                    tiltMaxAngleY={6}
                    scale={1.02}
                    transitionSpeed={1200}
                    glareEnable
                    glareMaxOpacity={0.08}
                    glareColor="#6366f1"
                    glarePosition="all"
                    glareBorderRadius="1rem"
                    className="h-full"
                  >
                    <div className="glass rounded-2xl p-6 h-full flex flex-col hover:border-brand-indigo/30 transition-colors">
                      <div className="flex justify-between items-start mb-3 gap-2">
                        <h3 className="text-lg font-display font-bold text-white truncate flex-1">
                          {p.name}
                        </h3>
                      </div>

                      <div className="mb-3 flex flex-wrap gap-2">
                        <span className="inline-flex items-center gap-1 text-xs font-semibold px-2 py-1 rounded-md bg-brand-sky/15 text-brand-sky">
                          <Globe className="w-3.5 h-3.5" /> Live URL
                        </span>

                        {p.has_active_session ? (
                          <span className="inline-flex items-center gap-1 text-xs font-semibold px-2 py-1 rounded-md bg-brand-teal/15 text-brand-teal">
                            <ShieldCheck className="w-3.5 h-3.5" /> Logged in
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-xs font-semibold px-2 py-1 rounded-md bg-white/5 text-slate-400">
                            <Unlock className="w-3.5 h-3.5" /> No session
                          </span>
                        )}
                      </div>

                      <a
                        href={p.base_url}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center gap-1.5 text-sm text-brand-sky hover:underline break-all mb-3"
                      >
                        <ExternalLink className="w-3.5 h-3.5 shrink-0" />
                        {p.base_url && p.base_url.length > 46 ? p.base_url.substring(0, 46) + '...' : p.base_url}
                      </a>

                      {p.description && (
                        <p className="text-sm text-slate-400 mb-3 line-clamp-2">{p.description}</p>
                      )}

                      {/* Session info text */}
                      {p.has_active_session && p.session_captured_at && (
                        <div className="flex items-center gap-1.5 text-xs text-brand-teal mb-3">
                          <Lock className="w-3.5 h-3.5" /> Session captured {formatSessionAge(p.session_captured_at)}
                        </div>
                      )}
                      {!p.has_active_session && (
                        <div className="flex items-center gap-1.5 text-xs text-slate-500 mb-3">
                          <Lightbulb className="w-3.5 h-3.5" /> If site requires login, click below before testing
                        </div>
                      )}

                      <div className="mt-auto">
                        <button
                          onClick={() => handleOpenLogin(p)}
                          disabled={openingLoginFor === p.id}
                          className={
                            'w-full flex items-center justify-center gap-2 text-sm py-2 rounded-xl font-medium transition mb-3 ' +
                            (openingLoginFor === p.id
                              ? 'bg-brand-indigo/20 text-brand-indigo cursor-wait'
                              : p.has_active_session
                                ? 'bg-white/5 hover:bg-white/10 text-brand-sky border border-brand-indigo/30'
                                : 'bg-brand-gradient text-white shadow-glow hover:shadow-glow-teal')
                          }
                        >
                          {openingLoginFor === p.id ? (
                            <><Loader2 className="w-4 h-4 animate-spin" /> Opening browser...</>
                          ) : p.has_active_session ? (
                            <><RefreshCw className="w-4 h-4" /> Re-login</>
                          ) : (
                            <><KeyRound className="w-4 h-4" /> Open & Login</>
                          )}
                        </button>

                        <div className="flex gap-2 pt-3 border-t border-white/10">
                          <button
                            onClick={() => openEditModal(p)}
                            className="flex-1 flex items-center justify-center gap-1.5 text-sm bg-white/5 hover:bg-white/10 text-slate-200 py-2 rounded-xl transition"
                          >
                            <Pencil className="w-3.5 h-3.5" /> Edit
                          </button>
                          <button
                            onClick={() => handleDelete(p.id, p.name)}
                            className="flex-1 flex items-center justify-center gap-1.5 text-sm bg-red-500/10 hover:bg-red-500/20 text-red-300 py-2 rounded-xl transition"
                          >
                            <Trash2 className="w-3.5 h-3.5" /> Delete
                          </button>
                        </div>
                      </div>
                    </div>
                  </Tilt>
                </motion.div>
              ))}
            </div>

            {/* Pagination Controls */}
            {totalPages > 1 && (
              <div className="flex justify-center items-center gap-2 mt-8 flex-wrap">
                <button
                  onClick={() => setCurrentPage((prev) => Math.max(1, prev - 1))}
                  disabled={currentPage === 1}
                  className="flex items-center gap-1 px-4 py-2 glass rounded-xl hover:bg-white/10 disabled:opacity-40 disabled:cursor-not-allowed transition"
                >
                  <ChevronLeft className="w-4 h-4" /> Previous
                </button>
                <div className="flex items-center gap-1.5">
                  {Array.from({ length: totalPages }, (_, i) => i + 1).map((page) => (
                    <button
                      key={page}
                      onClick={() => setCurrentPage(page)}
                      className={
                        'w-9 h-9 rounded-lg text-sm transition ' +
                        (currentPage === page
                          ? 'bg-brand-gradient text-white font-semibold shadow-glow'
                          : 'glass hover:bg-white/10 text-slate-300')
                      }
                    >
                      {page}
                    </button>
                  ))}
                </div>
                <button
                  onClick={() => setCurrentPage((prev) => Math.min(totalPages, prev + 1))}
                  disabled={currentPage === totalPages}
                  className="flex items-center gap-1 px-4 py-2 glass rounded-xl hover:bg-white/10 disabled:opacity-40 disabled:cursor-not-allowed transition"
                >
                  Next <ChevronRight className="w-4 h-4" />
                </button>
                <span className="ml-3 text-sm text-slate-500">
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
            <div className="p-6 border-b border-white/10 flex items-start justify-between gap-4">
              <div className="flex items-center gap-3">
                <div className="grid place-items-center w-10 h-10 rounded-xl bg-brand-gradient shadow-glow">
                  <FolderPlus className="w-5 h-5 text-white" />
                </div>
                <div>
                  <h2 className="text-xl font-display font-bold text-white">
                    {editingId ? 'Edit Project' : 'Create New Test Project'}
                  </h2>
                  <p className="text-sm text-slate-400">
                    Enter the website details. You&apos;ll log in manually after creation.
                  </p>
                </div>
              </div>
              <button
                type="button"
                onClick={closeModal}
                className="text-slate-500 hover:text-slate-200 transition"
                aria-label="Close"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <form onSubmit={handleSubmit} className="p-6 space-y-4">
              {error && (
                <div className="bg-red-500/10 border border-red-500/30 text-red-300 px-4 py-2.5 rounded-xl text-sm">
                  {error}
                </div>
              )}

              <div>
                <label className="block text-slate-300 text-sm font-medium mb-1.5">Project Name *</label>
                <input
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="My Portfolio Site"
                  className={`${inputBase} ${
                    name && isNameValid
                      ? 'border-brand-teal/60 focus:ring-brand-teal/50'
                      : name
                      ? 'border-red-500/60 focus:ring-red-500/50'
                      : 'border-white/10 focus:ring-brand-sky/60'
                  }`}
                />
                {name && !isNameValid && (
                  <p className="text-red-400 text-xs mt-1">Name must be at least 3 characters</p>
                )}
              </div>

              <div>
                <label className="block text-slate-300 text-sm font-medium mb-1.5">Target Website URL *</label>
                <input
                  type="text"
                  value={baseUrl}
                  onChange={(e) => setBaseUrl(e.target.value)}
                  placeholder="https://example.com"
                  className={`${inputBase} ${
                    baseUrl && isUrlValid
                      ? 'border-brand-teal/60 focus:ring-brand-teal/50'
                      : baseUrl
                      ? 'border-red-500/60 focus:ring-red-500/50'
                      : 'border-white/10 focus:ring-brand-sky/60'
                  }`}
                />
                <p className="text-xs text-slate-500 mt-1">Tests will run against this URL</p>
                {baseUrl && !isUrlValid && (
                  <p className="text-red-400 text-xs mt-1">Must start with http:// or https://</p>
                )}
              </div>

              <label className="flex items-start gap-3 cursor-pointer rounded-xl border border-white/10 bg-white/5 p-3">
                <input
                  type="checkbox"
                  checked={requiresLogin}
                  onChange={(e) => setRequiresLogin(e.target.checked)}
                  className="mt-0.5 h-4 w-4 accent-brand-indigo"
                />
                <span className="text-sm text-slate-300">
                  This site requires login to access its main content
                  <span className="block text-xs text-slate-500 mt-0.5">
                    If ticked, running a test without a saved login session will warn you
                    first (so login-gated pages aren&apos;t skipped). Leave unchecked for
                    public sites.
                  </span>
                </span>
              </label>

              <div>
                <label className="block text-slate-300 text-sm font-medium mb-1.5">Description</label>
                <textarea
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="What does this project test?"
                  rows="2"
                  className={`${inputBase} border-white/10 focus:ring-brand-sky/60`}
                />
              </div>


              {/* Info banner about manual login */}
              {!editingId && (
                <div className="bg-brand-indigo/10 border border-brand-indigo/30 rounded-xl p-3 text-sm">
                  <p className="font-medium text-white flex items-center gap-2">
                    <Lightbulb className="w-4 h-4 text-brand-sky" /> About Login
                  </p>
                  <p className="text-xs text-slate-300 mt-1">
                    If your site needs login, after creating the project click
                    <strong className="text-brand-sky"> Open &amp; Login</strong> on the project card. A browser
                    will open where you can log in manually (OTP, captcha, anything works).
                    Your session is saved for testing.
                  </p>
                </div>
              )}

              <div className="flex gap-3 pt-4 border-t border-white/10">
                <button
                  type="button"
                  onClick={closeModal}
                  disabled={saving}
                  className="flex-1 bg-white/5 hover:bg-white/10 text-slate-200 py-3 rounded-xl font-medium transition disabled:opacity-50"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={!canSubmit()}
                  className={
                    'flex-1 py-3 rounded-xl font-semibold transition flex items-center justify-center gap-2 ' +
                    (canSubmit()
                      ? 'bg-brand-gradient text-white shadow-glow hover:shadow-glow-teal'
                      : 'bg-white/5 text-slate-500 border border-white/10 cursor-not-allowed')
                  }
                >
                  {saving && <Loader2 className="w-4 h-4 animate-spin" />}
                  {saving
                    ? 'Saving...'
                    : editingId
                    ? 'Update Project'
                    : 'Create Project'}
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
            <div className="p-6 border-b border-white/10">
              <h2 className="text-xl font-display font-bold text-white flex items-center gap-2">
                <KeyRound className="w-5 h-5 text-brand-sky" /> Log in to {loginProject.name}
              </h2>
            </div>

            <div className="p-6 space-y-4">
              <div className="bg-brand-indigo/10 border border-brand-indigo/30 rounded-xl p-4 text-sm text-slate-200">
                A Chromium window has opened at{' '}
                <span className="font-mono text-brand-sky break-all">{loginProject.base_url}</span>.
                <ol className="list-decimal list-inside mt-2 space-y-1 text-slate-300">
                  <li>Log in there manually (email, OTP, captcha — anything works).</li>
                  <li>Come back here and click <strong className="text-white">“I’m logged in”</strong> to save the session.</li>
                </ol>
              </div>
              <p className="text-xs text-slate-500">
                The window auto-closes after 10 minutes if you don’t finish.
              </p>

              <div className="flex gap-3 pt-2">
                <button
                  type="button"
                  onClick={handleCancelLogin}
                  disabled={savingSession}
                  className="flex-1 bg-white/5 hover:bg-white/10 text-slate-200 py-3 rounded-xl font-medium transition disabled:opacity-50"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={handleSaveSession}
                  disabled={savingSession}
                  className={
                    'flex-1 py-3 rounded-xl font-semibold transition flex items-center justify-center gap-2 ' +
                    (savingSession
                      ? 'bg-brand-teal/30 text-brand-teal cursor-wait'
                      : 'bg-brand-gradient text-white shadow-glow hover:shadow-glow-teal')
                  }
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
