import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
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
  const [environment, setEnvironment] = useState('dev');

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
    setEnvironment('dev');
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
    setEnvironment(p.environment);
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
      environment,
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

  const envColor = (env) => {
    if (env === 'prod') return 'bg-red-500/10 text-red-300 border border-red-500/20';
    if (env === 'staging') return 'bg-amber-500/10 text-amber-300 border border-amber-500/20';
    return 'bg-white/5 text-slate-300 border border-white/10';
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
    'w-full rounded-md bg-[#0f1419] border border-white/10 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-500 ' +
    'focus:outline-none focus:border-[#4c8dff] transition';
  const toggleClass = (active) =>
    'flex-1 cursor-pointer px-3 py-2 border rounded-md text-center text-sm transition ' +
    (active
      ? 'border-[#4c8dff]/60 bg-[#2f6fed]/15 text-slate-100 font-medium'
      : 'border-white/10 text-slate-400 hover:text-slate-200 hover:bg-white/5');

  return (
    <div className="relative flex min-h-screen">
      <AmbientBackground />
      <Sidebar />

      <div className="flex-1 px-6 py-6 max-w-6xl">
        <div className="flex justify-between items-center mb-6 gap-4">
          <div>
            <p className="text-xs text-slate-500 uppercase tracking-wide">Projects</p>
            <h1 className="text-xl font-semibold text-slate-100 mt-1">Test projects</h1>
            <p className="text-sm text-slate-500 mt-1">
              {totalProjects > 0 ? `${totalProjects} total` : 'Register a site to start auditing it'}
            </p>
          </div>
          <button
            onClick={openCreateModal}
            className="btn-primary flex items-center gap-2 px-4 py-2 text-sm"
          >
            <Plus className="w-4 h-4" /> New project
          </button>
        </div>

        {loading ? (
          <div className="flex items-center justify-center gap-2 py-20 text-slate-500 text-sm">
            <Loader2 className="w-4 h-4 animate-spin" /> Loading projects…
          </div>
        ) : projects.length === 0 ? (
          <div className="card p-10 text-center">
            <div className="grid place-items-center w-11 h-11 mx-auto rounded-md bg-white/5 border border-white/10 text-slate-400 mb-3">
              <FolderOpen className="w-5 h-5" />
            </div>
            <h3 className="text-[15px] font-semibold text-slate-200 mb-1">No projects yet</h3>
            <p className="text-sm text-slate-500 mb-5">
              Add your first site to run tests against it.
            </p>
            <button
              onClick={openCreateModal}
              className="btn-primary inline-flex items-center gap-2 px-4 py-2 text-sm"
            >
              <Plus className="w-4 h-4" /> Create project
            </button>
          </div>
        ) : (
          <div>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {projects.map((p) => (
                <div key={p.id} className="h-full">
                  <div className="card p-5 h-full flex flex-col">
                      <div className="flex justify-between items-start mb-3 gap-2">
                        <h3 className="text-[15px] font-semibold text-slate-100 truncate flex-1">
                          {p.name}
                        </h3>
                        <span className={'text-[11px] font-medium px-2 py-0.5 rounded ' + envColor(p.environment)}>
                          {p.environment.toUpperCase()}
                        </span>
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
                        className="inline-flex items-center gap-1.5 text-[13px] text-[#7aa8ff] hover:underline break-all mb-2.5"
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
                            'w-full flex items-center justify-center gap-2 text-[13px] py-2 rounded-md font-medium transition mb-2.5 border ' +
                            (openingLoginFor === p.id
                              ? 'bg-white/5 text-slate-400 border-white/10 cursor-wait'
                              : p.has_active_session
                                ? 'bg-white/5 hover:bg-white/10 text-slate-200 border-white/10'
                                : 'bg-[#2f6fed] hover:bg-[#3b7bf5] text-white border-transparent')
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
                            className="flex-1 flex items-center justify-center gap-1.5 text-[13px] bg-transparent hover:bg-white/5 text-slate-300 border border-white/10 py-1.5 rounded-md transition"
                          >
                            <Pencil className="w-3.5 h-3.5" /> Edit
                          </button>
                          <button
                            onClick={() => handleDelete(p.id, p.name)}
                            className="flex-1 flex items-center justify-center gap-1.5 text-[13px] bg-transparent hover:bg-red-500/10 text-slate-400 hover:text-red-300 border border-white/10 hover:border-red-500/25 py-1.5 rounded-md transition"
                          >
                            <Trash2 className="w-3.5 h-3.5" /> Delete
                          </button>
                        </div>
                      </div>
                    </div>
                  </div>
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
                        'w-8 h-8 rounded-md text-[13px] transition border ' +
                        (currentPage === page
                          ? 'bg-[#2f6fed] text-white border-transparent font-medium'
                          : 'bg-transparent hover:bg-white/5 text-slate-400 border-white/10')
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
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center p-4 z-50">
          <div className="card w-full max-w-lg max-h-screen overflow-y-auto">
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
                  placeholder="Optional note about what this covers"
                  rows="2"
                  className={inputBase}
                />
              </div>

              <div>
                <label className="block text-slate-300 text-[13px] font-medium mb-1">Environment</label>
                <div className="flex gap-2">
                  {['dev', 'staging', 'prod'].map((env) => (
                    <label key={env} className={toggleClass(environment === env) + ' capitalize'}>
                      <input
                        type="radio"
                        value={env}
                        checked={environment === env}
                        onChange={(e) => setEnvironment(e.target.value)}
                        className="hidden"
                      />
                      {env}
                    </label>
                  ))}
                </div>
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
          </div>
        </div>
      )}

      {/* MANUAL LOGIN PROMPT */}
      {loginProject && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center p-4 z-50">
          <div className="card w-full max-w-md">
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
          </div>
        </div>
      )}
    </div>
  );
};

export default Projects;
