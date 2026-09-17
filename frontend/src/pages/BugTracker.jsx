import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { motion } from 'framer-motion';
import {
  Plus, Bug, AlertCircle, Clock, CheckCircle2, AlertTriangle, Camera,
  X, Link2, ImageOff, Search, ShieldAlert, Accessibility, Smartphone,
  Bot, Eye, Pencil, Trash2, RotateCcw, ArrowRight, Sparkles, Loader2,
  RefreshCw, MapPin, Code, Lock, FileText, Menu,
} from 'lucide-react';
import Sidebar from '../components/Sidebar';
import AmbientBackground from '../components/AmbientBackground';
import { getProjects } from '../api/projects';
import {
  getBugs,
  getBug,
  createBug,
  updateBug,
  updateBugStatus,
  deleteBug,
} from '../api/bugs';
import { analyzeBug } from '../api/ai';
import { imgUrl } from '../api/axiosConfig';

const CATEGORY_ICON = {
  'broken-link': Link2, 'console-error': Bug, 'missing-alt': ImageOff,
  'seo': Search, 'security': ShieldAlert, 'accessibility': Accessibility,
  'mobile': Smartphone, 'performance': Clock, 'functional': AlertCircle,
  'api': Code, 'validation': FileText, 'execution': RefreshCw, 'other': Bug,
};

const CATEGORY_LABEL = {
  'broken-link': 'Broken Link', 'console-error': 'JS Error',
  'missing-alt': 'Missing Alt', 'seo': 'SEO', 'security': 'Security',
  'accessibility': 'Accessibility', 'mobile': 'Mobile',
  'performance': 'Performance', 'functional': 'Feature Broken',
  'api': 'Data Request Failed', 'validation': 'Form Problem',
  'execution': 'Audit Incomplete', 'other': 'Other',
};

const friendlyDetectedBug = (bug) => {
  if (!bug?.test_run_id || bug.category !== 'console-error') return bug;
  const raw = `${bug.title || ''} ${bug.actual_behavior || ''}`;
  const statusMatch = raw.match(/(?:status of|HTTP\s*)(\d{3})/i);
  if (!/failed to load resource/i.test(raw) || !statusMatch) return bug;

  const status = Number(statusMatch[1]);
  const meaning = {
    400: 'the request was invalid',
    401: 'sign-in may be required',
    403: 'access was denied',
    404: 'the resource was not found',
    500: 'the server encountered an error',
    502: 'the server is unavailable',
    503: 'the service is temporarily unavailable',
  }[status] || `the server returned HTTP ${status}`;
  const guidance = {
    400: 'The page sent information the server could not accept.',
    401: 'The user may need to sign in again or the session may have expired.',
    403: 'The current user may not have permission to access it.',
    404: 'The requested page or file may no longer exist at that address.',
  }[status] || 'The server could not provide the resource the page requested.';
  const evidence = typeof bug.evidence === 'string'
    ? (() => { try { return JSON.parse(bug.evidence); } catch { return {}; } })()
    : (bug.evidence || {});
  const source = evidence.source_location?.url;
  const sourceLine = Number.isInteger(evidence.source_location?.lineNumber)
    ? `:${evidence.source_location.lineNumber + 1}` : '';

  return {
    ...bug,
    displayTitle: `The page could not load a required resource (${meaning})`,
    displayDescription: `The page tried to load a required resource, but the server returned HTTP ${status}. ${guidance}` +
      ` Where to change: ${source ? `inspect ${source}${sourceLine} and fix the request there.` : 'open DevTools > Network, select the failed request, and fix the frontend request or backend API route.'}`,
  };
};

const StatTile = ({ value, label, tone, Icon }) => (
  <div className="glass rounded-xl p-4 text-center">
    <div className={'text-2xl font-display font-bold flex items-center justify-center gap-1.5 ' + tone}>
      {Icon && <Icon className="w-5 h-5" />} {value}
    </div>
    <div className="text-xs text-slate-500 mt-1">{label}</div>
  </div>
);

const BugTracker = () => {
  const navigate = useNavigate();
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const [bugs, setBugs] = useState([]);
  const [projects, setProjects] = useState([]);
  const [loading, setLoading] = useState(true);
  // Bumped to trigger a re-fetch of the bug list from outside the effect.
  const [reloadFlag, setReloadFlag] = useState(0);

  const [filterStatus, setFilterStatus] = useState('');
  const [filterSeverity, setFilterSeverity] = useState('');
  const [filterCategory, setFilterCategory] = useState('');
  const [filterProject, setFilterProject] = useState('');
  const [filterHasEvidence, setFilterHasEvidence] = useState(false);

  const [showModal, setShowModal] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState('');

  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [severity, setSeverity] = useState('Major');
  const [statusField, setStatusField] = useState('Open');
  const [category, setCategory] = useState('other');
  const [stepsRepro, setStepsRepro] = useState('');
  const [expectedBehavior, setExpectedBehavior] = useState('');
  const [actualBehavior, setActualBehavior] = useState('');

  const [detailBug, setDetailBug] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [aiAnalysis, setAiAnalysis] = useState(null);
  const [analyzingAI, setAnalyzingAI] = useState(false);

  const [enlargedEvidence, setEnlargedEvidence] = useState(null);

  // Parse evidence safely from a bug list item (may be string or object)
  const parseEvidence = (ev) => {
    if (!ev) return null;
    if (typeof ev === 'object') return ev;
    if (typeof ev === 'string') {
      try { return JSON.parse(ev); } catch { return null; }
    }
    return null;
  };

  const bugHasImage = (bug) => {
    const ev = parseEvidence(bug.evidence);
    return !!(ev && ev.screenshot_crop);
  };

  const bugHasMeaningfulEvidence = (bug) => {
    const ev = parseEvidence(bug.evidence);
    if (!ev) return false;
    if (ev.screenshot_crop) return true;
    if (ev.url) return true;
    if (ev.src) return true;
    if (ev.header) return true;
    if (ev.bounding_box) return true;
    if (ev.element_text && ev.element_text.length > 0) return true;
    return false;
  };

  const reloadBugs = () => setReloadFlag((f) => f + 1);

  useEffect(() => {
    if (!localStorage.getItem('token')) {
      navigate('/login');
      return;
    }

    const loadProjects = async () => {
      try {
        const res = await getProjects();
        // /projects is paginated -> { data: [...], pagination }. Support both shapes.
        const list = Array.isArray(res.data) ? res.data : (res.data?.data || []);
        setProjects(list);
      } catch (error) {
        if (error.response?.status === 401) {
          localStorage.clear();
          navigate('/login');
        }
      }
    };

    loadProjects();
  }, [navigate]);

  useEffect(() => {
    const loadBugs = async () => {
      try {
        setLoading(true);
        const res = await getBugs({
          status: filterStatus,
          severity: filterSeverity,
          category: filterCategory,
          project_id: filterProject,
        });
        setBugs(res.data);
      } catch (error) {
        if (error.response?.status === 401) {
          localStorage.clear();
          navigate('/login');
        }
      } finally {
        setLoading(false);
      }
    };

    loadBugs();
  }, [filterStatus, filterSeverity, filterCategory, filterProject, reloadFlag, navigate]);

  const displayedBugs = filterHasEvidence ? bugs.filter(bugHasImage) : bugs;

  const stats = {
    total: bugs.length,
    open: bugs.filter((b) => b.status === 'Open').length,
    inProgress: bugs.filter((b) => b.status === 'In Progress').length,
    resolved: bugs.filter((b) => b.status === 'Resolved').length,
    closed: bugs.filter((b) => b.status === 'Closed').length,
    critical: bugs.filter((b) => b.severity === 'Critical' && ['Open', 'In Progress'].includes(b.status)).length,
    withImage: bugs.filter(bugHasImage).length,
  };

  const isTitleValid = title.trim().length >= 3;
  const canSubmit = isTitleValid && !saving;

  const resetForm = () => {
    setTitle('');
    setDescription('');
    setSeverity('Major');
    setStatusField('Open');
    setCategory('other');
    setStepsRepro('');
    setExpectedBehavior('');
    setActualBehavior('');
    setEditingId(null);
    setFormError('');
  };

  const openCreateModal = () => {
    resetForm();
    setShowModal(true);
  };

  const openEditModal = (bug) => {
    setTitle(bug.title);
    setDescription(bug.description || '');
    setSeverity(bug.severity);
    setStatusField(bug.status);
    setCategory(bug.category || 'other');
    setStepsRepro(bug.steps_to_reproduce || '');
    setExpectedBehavior(bug.expected_behavior || '');
    setActualBehavior(bug.actual_behavior || '');
    setEditingId(bug.id);
    setFormError('');
    setShowModal(true);
  };

  const closeModal = () => {
    setShowModal(false);
    resetForm();
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!canSubmit) return;
    setSaving(true);
    setFormError('');

    const payload = {
      title: title.trim(),
      description: description.trim(),
      severity,
      status: statusField,
      category,
      steps_to_reproduce: stepsRepro.trim(),
      expected_behavior: expectedBehavior.trim(),
      actual_behavior: actualBehavior.trim(),
    };

    try {
      if (editingId) {
        await updateBug(editingId, payload);
      } else {
        await createBug(payload);
      }
      closeModal();
      reloadBugs();
    } catch (err) {
      setFormError(err.response?.data?.error || 'Failed to save bug');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id, bugTitle) => {
    if (!window.confirm('Delete bug "' + bugTitle + '"?')) return;
    try {
      await deleteBug(id);
      reloadBugs();
    } catch {
      alert('Failed to delete bug');
    }
  };

  const handleStatusChange = async (id, newStatus) => {
    try {
      await updateBugStatus(id, newStatus);
      reloadBugs();
      if (detailBug && detailBug.id === id) {
        setDetailBug({ ...detailBug, status: newStatus });
      }
    } catch {
      alert('Failed to update status');
    }
  };

  const handleAIAnalyze = async () => {
    if (!detailBug) return;
    setAnalyzingAI(true);
    setAiAnalysis(null);
    try {
      const res = await analyzeBug(detailBug.id);
      setAiAnalysis(res.data);
    } catch (err) {
      const errorMsg = err.response?.data?.error || 'AI analysis failed';
      setAiAnalysis({
        explanation: errorMsg,
        why_it_matters: '',
        how_to_fix: '',
        code_example: 'N/A',
        isError: true,
      });
    } finally {
      setAnalyzingAI(false);
    }
  };

  const openDetail = async (bug) => {
    setDetailBug(bug);
    setAiAnalysis(null);
    setDetailLoading(true);
    try {
      const res = await getBug(bug.id);
      setDetailBug(res.data);
    } catch (err) {
      console.error('Failed to load bug details', err);
    } finally {
      setDetailLoading(false);
    }
  };

  const closeDetail = () => {
    setDetailBug(null);
    setAiAnalysis(null);
  };

  const clearFilters = () => {
    setFilterStatus('');
    setFilterSeverity('');
    setFilterCategory('');
    setFilterProject('');
    setFilterHasEvidence(false);
  };

  const hasActiveFilters = filterStatus || filterSeverity || filterCategory || filterProject || filterHasEvidence;

  const severityColor = (s) => {
    if (s === 'Critical') return 'badge badge-danger';
    if (s === 'Major') return 'badge badge-warning';
    return 'badge badge-info';
  };

  const severityBorder = (s) => {
    if (s === 'Critical') return 'border-red-500/60';
    if (s === 'Major') return 'border-orange-400/60';
    return 'border-amber-400/60';
  };

  const statusColor = (s) => {
    if (s === 'Open') return 'badge badge-danger';
    if (s === 'In Progress') return 'badge badge-warning';
    if (s === 'Resolved') return 'badge badge-success';
    return 'badge badge-neutral';
  };

  const categoryLabel = (c) => CATEGORY_LABEL[c] || c;

  const nextStatus = (current) => {
    if (current === 'Open') return 'In Progress';
    if (current === 'In Progress') return 'Resolved';
    if (current === 'Resolved') return 'Closed';
    return null;
  };

  const selectClass =
    'px-3 py-2 rounded-lg bg-white/5 border border-white/10 text-sm text-slate-100 focus:outline-none focus:ring-2 focus:ring-brand-sky/60';
  const inputBase =
    'w-full rounded-xl bg-white/5 border px-4 py-2.5 text-slate-100 placeholder:text-slate-500 focus:outline-none focus:ring-2 transition';

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
              <h1 className="text-3xl font-display font-bold text-white">Bug Tracker</h1>
              <p className="text-slate-400 mt-1">Manage bugs found manually or detected by automated tests</p>
            </div>
          </div>
          <button
            onClick={openCreateModal}
            className="flex items-center gap-2 bg-brand-gradient text-white px-5 py-2.5 rounded-xl font-medium shadow-glow hover:shadow-glow-teal transition-all"
          >
            <Plus className="w-4 h-4" /> Log New Bug
          </button>
        </motion.div>

        {/* Stats */}
        <div className="grid grid-cols-2 md:grid-cols-6 gap-3 mb-6">
          <StatTile value={stats.total} label="Total" tone="text-white" Icon={Bug} />
          <StatTile value={stats.open} label="Open" tone="text-red-300" Icon={AlertCircle} />
          <StatTile value={stats.inProgress} label="In Progress" tone="text-amber-300" Icon={Clock} />
          <StatTile value={stats.resolved} label="Resolved" tone="text-brand-teal" Icon={CheckCircle2} />
          <StatTile value={stats.critical} label="Critical Open" tone="text-red-400" Icon={AlertTriangle} />
          <StatTile value={stats.withImage} label="With Proof" tone="text-brand-sky" Icon={Camera} />
        </div>

        {/* Filters */}
        <div className="glass-card p-4 mb-6">
          <div className="flex flex-wrap items-center gap-3">
            <span className="text-sm font-medium text-slate-300 shrink-0">Filters:</span>

            <select value={filterStatus} onChange={(e) => setFilterStatus(e.target.value)} className={`${selectClass} w-full sm:w-auto`}>
              <option value="" className="bg-surface">All Status</option>
              <option value="Open" className="bg-surface">Open</option>
              <option value="In Progress" className="bg-surface">In Progress</option>
              <option value="Resolved" className="bg-surface">Resolved</option>
              <option value="Closed" className="bg-surface">Closed</option>
            </select>

            <select value={filterSeverity} onChange={(e) => setFilterSeverity(e.target.value)} className={`${selectClass} w-full sm:w-auto`}>
              <option value="" className="bg-surface">All Severity</option>
              <option value="Critical" className="bg-surface">Critical</option>
              <option value="Major" className="bg-surface">Major</option>
              <option value="Minor" className="bg-surface">Minor</option>
            </select>

            <select value={filterCategory} onChange={(e) => setFilterCategory(e.target.value)} className={`${selectClass} w-full sm:w-auto`}>
              <option value="" className="bg-surface">All Categories</option>
              {Object.keys(CATEGORY_LABEL).map((c) => (
                <option key={c} value={c} className="bg-surface">{CATEGORY_LABEL[c]}</option>
              ))}
            </select>

            <select value={filterProject} onChange={(e) => setFilterProject(e.target.value)} className={`${selectClass} w-full sm:w-auto`}>
              <option value="" className="bg-surface">All Projects</option>
              {projects.map((p) => (
                <option key={p.id} value={p.id} className="bg-surface">{p.name}</option>
              ))}
            </select>

            <label className="flex items-center gap-2 cursor-pointer shrink-0 px-3 py-2 border border-brand-sky/30 rounded-lg bg-brand-sky/10 hover:bg-brand-sky/15 transition">
              <input type="checkbox" checked={filterHasEvidence} onChange={(e) => setFilterHasEvidence(e.target.checked)} className="w-4 h-4 accent-brand-sky" />
              <span className="text-sm text-brand-sky font-medium inline-flex items-center gap-1"><Camera className="w-3.5 h-3.5" /> Only with screenshot</span>
            </label>

            {hasActiveFilters && (
              <button onClick={clearFilters} className="text-sm text-brand-sky hover:underline shrink-0">Clear filters</button>
            )}
          </div>
        </div>

        {loading ? (
          <div className="flex items-center justify-center gap-2 py-20 text-slate-400">
            <Loader2 className="w-5 h-5 animate-spin" /> Loading bugs...
          </div>
        ) : displayedBugs.length === 0 ? (
          <div className="glass rounded-2xl p-12 text-center">
            <div className="grid place-items-center w-16 h-16 mx-auto rounded-2xl bg-red-500/15 text-red-300 mb-4">
              <Bug className="w-8 h-8" />
            </div>
            <h3 className="text-xl font-display font-semibold text-white mb-2">
              {hasActiveFilters ? 'No bugs match these filters' : 'No bugs yet'}
            </h3>
            <p className="text-slate-400 mb-6">
              {hasActiveFilters ? 'Try clearing the filters to see all bugs' : 'Log bugs manually or run automated tests to detect issues automatically'}
            </p>
            {!hasActiveFilters && (
              <button onClick={openCreateModal} className="inline-flex items-center gap-2 bg-brand-gradient text-white px-6 py-3 rounded-xl font-medium shadow-glow">
                <Plus className="w-4 h-4" /> Log Your First Bug
              </button>
            )}
          </div>
        ) : (
          <div className="space-y-3">
            {displayedBugs.map((bug, i) => {
              const displayBug = friendlyDetectedBug(bug);
              const hasImage = bugHasImage(bug);
              const CIcon = CATEGORY_ICON[bug.category] || Bug;
              return (
                <motion.div
                  key={bug.id}
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.3, delay: Math.min(i * 0.03, 0.25) }}
                  className={'glass rounded-2xl p-5 border-l-4 hover:bg-white/[0.07] transition-colors ' + severityBorder(bug.severity)}
                >
                  <div className="flex items-start gap-4">
                    <div className="grid place-items-center w-10 h-10 rounded-xl bg-white/5 text-slate-300 flex-shrink-0">
                      <CIcon className="w-5 h-5" />
                    </div>

                    <div className="flex-1 min-w-0">
                      <div className="flex flex-wrap items-center gap-2 mb-1">
                        <span className={'text-xs font-semibold px-2 py-1 rounded-md ' + severityColor(bug.severity)}>{bug.severity}</span>
                        <span className={'text-xs font-semibold px-2 py-1 rounded-md ' + statusColor(bug.status)}>{bug.status}</span>
                        <span className="text-xs text-slate-500">{categoryLabel(bug.category)}</span>
                        {bug.project_name && <span className="text-xs text-slate-500">· {bug.project_name}</span>}
                        {bug.test_run_id && (
                          <span className="inline-flex items-center gap-1 text-xs text-brand-indigo bg-brand-indigo/15 px-2 py-0.5 rounded">
                            <Bot className="w-3 h-3" /> Auto-detected
                          </span>
                        )}
                        {hasImage && (
                          <span className="inline-flex items-center gap-1 text-xs text-brand-sky bg-brand-sky/15 border border-brand-sky/30 px-2 py-0.5 rounded font-medium">
                            <Camera className="w-3 h-3" /> Has Proof
                          </span>
                        )}
                      </div>

                      <h3 className="text-base font-semibold text-white cursor-pointer hover:text-brand-sky break-words" onClick={() => openDetail(bug)}>
                        {displayBug.displayTitle || bug.title}
                      </h3>

                      {(displayBug.displayDescription || bug.description) && <p className="text-sm text-slate-400 mt-1 line-clamp-2">{displayBug.displayDescription || bug.description}</p>}

                      <div className="flex flex-wrap gap-2 mt-3">
                        {nextStatus(bug.status) && (
                          <button onClick={() => handleStatusChange(bug.id, nextStatus(bug.status))}
                            className="inline-flex items-center gap-1 text-xs bg-brand-indigo/15 hover:bg-brand-indigo/25 text-brand-sky px-3 py-1 rounded-lg transition whitespace-nowrap">
                            <ArrowRight className="w-3 h-3" /> {nextStatus(bug.status)}
                          </button>
                        )}
                        {bug.status !== 'Open' && (
                          <button onClick={() => handleStatusChange(bug.id, 'Open')}
                            className="inline-flex items-center gap-1 text-xs bg-white/5 hover:bg-white/10 text-slate-300 px-3 py-1 rounded-lg transition whitespace-nowrap">
                            <RotateCcw className="w-3 h-3" /> Reopen
                          </button>
                        )}
                        <button onClick={() => openDetail(bug)} className="inline-flex items-center gap-1 text-xs bg-white/5 hover:bg-white/10 text-slate-300 px-3 py-1 rounded-lg transition whitespace-nowrap">
                          <Eye className="w-3 h-3" /> View
                        </button>
                        <button onClick={() => openEditModal(bug)} className="inline-flex items-center gap-1 text-xs bg-white/5 hover:bg-white/10 text-slate-300 px-3 py-1 rounded-lg transition whitespace-nowrap">
                          <Pencil className="w-3 h-3" /> Edit
                        </button>
                        <button onClick={() => handleDelete(bug.id, bug.title)} className="inline-flex items-center gap-1 text-xs bg-red-500/10 hover:bg-red-500/20 text-red-300 px-3 py-1 rounded-lg transition whitespace-nowrap">
                          <Trash2 className="w-3 h-3" /> Delete
                        </button>
                      </div>
                    </div>

                    <div className="text-xs text-slate-500 flex-shrink-0">
                      {new Date(bug.created_at).toLocaleDateString()}
                    </div>
                  </div>
                </motion.div>
              );
            })}
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
            <div className="p-6 border-b border-white/10 flex items-center justify-between">
              <h2 className="text-xl font-display font-bold text-white">{editingId ? 'Edit Bug' : 'Log New Bug'}</h2>
              <button onClick={closeModal} className="text-slate-500 hover:text-slate-200 transition" aria-label="Close"><X className="w-5 h-5" /></button>
            </div>

            <form onSubmit={handleSubmit} className="p-6 space-y-4">
              {formError && (
                <div className="bg-red-500/10 border border-red-500/30 text-red-300 px-4 py-2.5 rounded-xl text-sm">{formError}</div>
              )}

              <div>
                <label className="block text-slate-300 text-sm font-medium mb-1.5">Title *</label>
                <input type="text" value={title} onChange={(e) => setTitle(e.target.value)}
                  placeholder="Brief description of the bug"
                  className={`${inputBase} ${title && !isTitleValid ? 'border-red-500/60 focus:ring-red-500/50' : 'border-white/10 focus:ring-brand-sky/60'}`} />
                {title && !isTitleValid && <p className="text-red-400 text-xs mt-1">Title must be at least 3 characters</p>}
              </div>

              <div>
                <label className="block text-slate-300 text-sm font-medium mb-1.5">Description</label>
                <textarea value={description} onChange={(e) => setDescription(e.target.value)} placeholder="More details about this bug..." rows="2"
                  className={`${inputBase} border-white/10 focus:ring-brand-sky/60`} />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-slate-300 text-sm font-medium mb-1.5">Severity</label>
                  <select value={severity} onChange={(e) => setSeverity(e.target.value)} className={`${inputBase} border-white/10 focus:ring-brand-sky/60`}>
                    <option value="Critical" className="bg-surface">Critical</option>
                    <option value="Major" className="bg-surface">Major</option>
                    <option value="Minor" className="bg-surface">Minor</option>
                  </select>
                </div>
                <div>
                  <label className="block text-slate-300 text-sm font-medium mb-1.5">Status</label>
                  <select value={statusField} onChange={(e) => setStatusField(e.target.value)} className={`${inputBase} border-white/10 focus:ring-brand-sky/60`}>
                    <option value="Open" className="bg-surface">Open</option>
                    <option value="In Progress" className="bg-surface">In Progress</option>
                    <option value="Resolved" className="bg-surface">Resolved</option>
                    <option value="Closed" className="bg-surface">Closed</option>
                  </select>
                </div>
              </div>

              <div>
                <label className="block text-slate-300 text-sm font-medium mb-1.5">Category</label>
                <select value={category} onChange={(e) => setCategory(e.target.value)} className={`${inputBase} border-white/10 focus:ring-brand-sky/60`}>
                  {Object.keys(CATEGORY_LABEL).map((c) => (
                    <option key={c} value={c} className="bg-surface">{CATEGORY_LABEL[c]}</option>
                  ))}
                </select>
              </div>

              <div>
                <label className="block text-slate-300 text-sm font-medium mb-1.5">Steps to Reproduce</label>
                <textarea value={stepsRepro} onChange={(e) => setStepsRepro(e.target.value)}
                  placeholder={'1. Go to homepage\n2. Click login\n3. Enter credentials'} rows="3"
                  className={`${inputBase} border-white/10 focus:ring-brand-sky/60 font-mono text-sm`} />
              </div>

              <div>
                <label className="block text-slate-300 text-sm font-medium mb-1.5">Expected Behavior</label>
                <textarea value={expectedBehavior} onChange={(e) => setExpectedBehavior(e.target.value)} placeholder="What should happen?" rows="2"
                  className={`${inputBase} border-white/10 focus:ring-brand-sky/60`} />
              </div>

              <div>
                <label className="block text-slate-300 text-sm font-medium mb-1.5">Actual Behavior</label>
                <textarea value={actualBehavior} onChange={(e) => setActualBehavior(e.target.value)} placeholder="What's happening instead?" rows="2"
                  className={`${inputBase} border-white/10 focus:ring-brand-sky/60`} />
              </div>

              <div className="flex gap-3 pt-4 border-t border-white/10">
                <button type="button" onClick={closeModal} className="flex-1 bg-white/5 hover:bg-white/10 text-slate-200 py-3 rounded-xl font-medium transition">Cancel</button>
                <button type="submit" disabled={!canSubmit}
                  className={'flex-1 py-3 rounded-xl font-semibold transition flex items-center justify-center gap-2 ' +
                    (canSubmit ? 'bg-brand-gradient text-white shadow-glow hover:shadow-glow-teal' : 'bg-white/5 text-slate-500 border border-white/10 cursor-not-allowed')}>
                  {saving && <Loader2 className="w-4 h-4 animate-spin" />}
                  {saving ? 'Saving...' : editingId ? 'Update Bug' : 'Log Bug'}
                </button>
              </div>
            </form>
          </motion.div>
        </div>
      )}

      {/* DETAIL MODAL */}
      {detailBug && (() => {
        const displayBug = friendlyDetectedBug(detailBug);
        const evidence = parseEvidence(detailBug.evidence);
        const showEvidencePanel = bugHasMeaningfulEvidence(detailBug);

        return (
          <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4 z-50">
            <motion.div
              initial={{ opacity: 0, scale: 0.97, y: 10 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              transition={{ duration: 0.25 }}
              className="glass-strong rounded-2xl shadow-card w-full max-w-3xl max-h-screen overflow-y-auto"
            >
              <div className="p-6 border-b border-white/10 flex justify-between items-start gap-4">
                <div className="flex-1">
                  <div className="flex flex-wrap items-center gap-2 mb-2">
                    <span className={'text-xs font-semibold px-2 py-1 rounded-md ' + severityColor(detailBug.severity)}>{detailBug.severity}</span>
                    <span className={'text-xs font-semibold px-2 py-1 rounded-md ' + statusColor(detailBug.status)}>{detailBug.status}</span>
                    <span className="text-xs text-slate-500">{categoryLabel(detailBug.category)}</span>
                    {detailBug.test_run_id && (
                      <span className="inline-flex items-center gap-1 text-xs text-brand-indigo bg-brand-indigo/15 px-2 py-0.5 rounded"><Bot className="w-3 h-3" /> Auto-detected</span>
                    )}
                  </div>
                  <h2 className="text-xl font-display font-bold text-white break-words">{displayBug.displayTitle || detailBug.title}</h2>
                </div>
                <button onClick={closeDetail} className="text-slate-500 hover:text-slate-200 transition" aria-label="Close"><X className="w-5 h-5" /></button>
              </div>

              <div className="p-6 space-y-4">
                {detailLoading && (
                  <div className="flex items-center justify-center gap-2 py-2 text-slate-400 text-sm"><Loader2 className="w-4 h-4 animate-spin" /> Loading evidence...</div>
                )}

                {/* VISUAL EVIDENCE */}
                {!detailLoading && showEvidencePanel && evidence && (
                  <div className="bg-brand-sky/10 border border-brand-sky/30 rounded-2xl p-5">
                    <h3 className="font-display font-bold text-white mb-3 flex items-center gap-2">
                      <Camera className="w-4 h-4 text-brand-sky" />
                      <span>Visual Evidence</span>
                      <span className="text-xs font-normal text-slate-500 ml-auto">Captured during automated scan</span>
                    </h3>

                    {evidence.screenshot_crop && (
                      <div className="mb-4">
                        <p className="text-xs font-semibold text-brand-sky mb-2">SCREENSHOT — CLICK TO ENLARGE</p>
                        <div
                          className="border border-brand-sky/40 rounded-lg overflow-hidden bg-white/5 inline-block cursor-pointer hover:border-brand-sky transition"
                          onClick={() => setEnlargedEvidence(imgUrl(evidence.screenshot_crop))}
                        >
                          <img src={imgUrl(evidence.screenshot_crop)} alt="Visual proof of the issue"
                            className="max-w-full max-h-64 object-contain"
                            onError={(e) => { e.target.parentElement.style.display = 'none'; }} />
                        </div>
                        <p className="text-xs text-slate-500 mt-2">This is the exact area on the page where the issue was detected.</p>
                      </div>
                    )}

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                      {evidence.url && (
                        <div className="bg-white/5 rounded-lg p-3 border border-white/10">
                          <p className="text-xs font-semibold text-brand-sky mb-1 flex items-center gap-1"><Link2 className="w-3 h-3" /> URL</p>
                          <p className="text-sm text-slate-200 break-all font-mono">{evidence.url}</p>
                          {evidence.status_code && <p className="text-xs text-red-300 mt-1">HTTP {evidence.status_code}</p>}
                        </div>
                      )}
                      {evidence.src && (
                        <div className="bg-white/5 rounded-lg p-3 border border-white/10">
                          <p className="text-xs font-semibold text-brand-sky mb-1 flex items-center gap-1"><ImageOff className="w-3 h-3" /> IMAGE SOURCE</p>
                          <p className="text-sm text-slate-200 break-all font-mono">{evidence.src}</p>
                        </div>
                      )}
                      {evidence.element_text && (
                        <div className="bg-white/5 rounded-lg p-3 border border-white/10">
                          <p className="text-xs font-semibold text-brand-sky mb-1 flex items-center gap-1"><FileText className="w-3 h-3" /> ELEMENT TEXT</p>
                          <p className="text-sm text-slate-200 italic">&quot;{evidence.element_text}&quot;</p>
                        </div>
                      )}
                      {evidence.parent_context && (
                        <div className="bg-white/5 rounded-lg p-3 border border-white/10">
                          <p className="text-xs font-semibold text-brand-sky mb-1 flex items-center gap-1"><MapPin className="w-3 h-3" /> SURROUNDING TEXT</p>
                          <p className="text-sm text-slate-300">{evidence.parent_context}</p>
                        </div>
                      )}
                      {evidence.element_selector && evidence.element_selector !== 'console' && (
                        <div className="bg-white/5 rounded-lg p-3 border border-white/10 md:col-span-2">
                          <p className="text-xs font-semibold text-brand-sky mb-1 flex items-center gap-1"><Code className="w-3 h-3" /> ELEMENT LOCATION (CSS PATH)</p>
                          <p className="text-xs text-slate-200 font-mono break-all">{evidence.element_selector}</p>
                        </div>
                      )}
                      {evidence.bounding_box && (
                        <div className="bg-white/5 rounded-lg p-3 border border-white/10">
                          <p className="text-xs font-semibold text-brand-sky mb-1">COORDINATES</p>
                          <p className="text-xs text-slate-300 font-mono">
                            x: {evidence.bounding_box.x}px, y: {evidence.bounding_box.y}px<br />
                            {evidence.bounding_box.width}px × {evidence.bounding_box.height}px
                          </p>
                        </div>
                      )}
                      {evidence.header && (
                        <div className="bg-white/5 rounded-lg p-3 border border-white/10">
                          <p className="text-xs font-semibold text-brand-sky mb-1 flex items-center gap-1"><Lock className="w-3 h-3" /> SECURITY HEADER</p>
                          <p className="text-sm text-slate-200 font-mono">{evidence.header}</p>
                          {evidence.purpose && <p className="text-xs text-slate-500 mt-1">{evidence.purpose}</p>}
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {/* AI ANALYSIS */}
                <div className="bg-brand-indigo/10 border border-brand-indigo/30 rounded-2xl p-4">
                  <div className="flex items-center justify-between gap-4 flex-wrap">
                    <div>
                      <h3 className="font-display font-bold text-white mb-1 flex items-center gap-2"><Sparkles className="w-4 h-4 text-brand-sky" /> AI Bug Analysis</h3>
                      <p className="text-sm text-slate-300">Get a plain-English explanation, impact analysis, and fix suggestions.</p>
                    </div>
                    <button onClick={handleAIAnalyze} disabled={analyzingAI}
                      className={'inline-flex items-center gap-2 px-5 py-2 rounded-xl font-medium whitespace-nowrap transition ' +
                        (analyzingAI ? 'bg-brand-indigo/20 text-brand-indigo cursor-wait' : 'bg-brand-gradient text-white shadow-glow')}>
                      {analyzingAI ? <><Loader2 className="w-4 h-4 animate-spin" /> Thinking...</> : aiAnalysis ? <><RefreshCw className="w-4 h-4" /> Re-analyze</> : <><Sparkles className="w-4 h-4" /> Analyze with AI</>}
                    </button>
                  </div>

                  {aiAnalysis && (
                    <div className="mt-4 space-y-3">
                      {aiAnalysis.isError ? (
                        <div className="bg-red-500/10 border border-red-500/30 text-red-300 p-3 rounded-lg text-sm">{aiAnalysis.explanation}</div>
                      ) : (
                        <>
                          <div className="bg-white/5 rounded-lg p-3 border border-white/10">
                            <p className="text-xs font-semibold text-brand-sky mb-1">WHAT THIS MEANS</p>
                            <p className="text-sm text-slate-200">{aiAnalysis.explanation}</p>
                          </div>
                          {aiAnalysis.why_it_matters && (
                            <div className="bg-white/5 rounded-lg p-3 border border-white/10">
                              <p className="text-xs font-semibold text-brand-sky mb-1">WHY IT MATTERS</p>
                              <p className="text-sm text-slate-200">{aiAnalysis.why_it_matters}</p>
                            </div>
                          )}
                          {aiAnalysis.how_to_fix && (
                            <div className="bg-white/5 rounded-lg p-3 border border-white/10">
                              <p className="text-xs font-semibold text-brand-sky mb-1">HOW TO FIX</p>
                              <p className="text-sm text-slate-200 whitespace-pre-wrap">{aiAnalysis.how_to_fix}</p>
                            </div>
                          )}
                          {aiAnalysis.code_example && aiAnalysis.code_example !== 'N/A' && (
                            <div className="bg-black/40 border border-white/10 rounded-lg p-3">
                              <p className="text-xs font-semibold text-brand-indigo mb-2">CODE EXAMPLE</p>
                              <pre className="text-xs text-brand-teal whitespace-pre-wrap font-mono overflow-x-auto">{aiAnalysis.code_example}</pre>
                            </div>
                          )}
                        </>
                      )}
                    </div>
                  )}
                </div>

                {(displayBug.displayDescription || detailBug.description) && (
                  <div>
                    <p className="text-xs font-semibold text-slate-500 mb-1">DESCRIPTION</p>
                    <pre className="text-sm text-slate-300 whitespace-pre-wrap font-sans">{displayBug.displayDescription || detailBug.description}</pre>
                  </div>
                )}
                {detailBug.steps_to_reproduce && (
                  <div>
                    <p className="text-xs font-semibold text-slate-500 mb-1">STEPS TO REPRODUCE</p>
                    <pre className="text-sm text-slate-300 whitespace-pre-wrap font-sans bg-white/5 border border-white/10 p-3 rounded-lg">{detailBug.steps_to_reproduce}</pre>
                  </div>
                )}
                {detailBug.expected_behavior && (
                  <div>
                    <p className="text-xs font-semibold text-slate-500 mb-1">EXPECTED BEHAVIOR</p>
                    <p className="text-sm text-slate-200 bg-brand-teal/10 border border-brand-teal/20 p-3 rounded-lg">{detailBug.expected_behavior}</p>
                  </div>
                )}
                {detailBug.actual_behavior && (
                  <div>
                    <p className="text-xs font-semibold text-slate-500 mb-1">ACTUAL BEHAVIOR</p>
                    <p className="text-sm text-slate-200 bg-red-500/10 border border-red-500/20 p-3 rounded-lg">{detailBug.actual_behavior}</p>
                  </div>
                )}

                <div className="grid grid-cols-2 gap-3 text-xs text-slate-500 pt-4 border-t border-white/10">
                  <div><p className="font-semibold text-slate-400">Project</p><p>{detailBug.project_name || '—'}</p></div>
                  <div><p className="font-semibold text-slate-400">Test Case</p><p>{detailBug.test_case_title || '—'}</p></div>
                  <div><p className="font-semibold text-slate-400">Created</p><p>{new Date(detailBug.created_at).toLocaleString()}</p></div>
                  {detailBug.resolved_at && (
                    <div><p className="font-semibold text-slate-400">Resolved</p><p>{new Date(detailBug.resolved_at).toLocaleString()}</p></div>
                  )}
                </div>

                <div className="flex flex-wrap gap-2 pt-4 border-t border-white/10">
                  {nextStatus(detailBug.status) && (
                    <button onClick={() => handleStatusChange(detailBug.id, nextStatus(detailBug.status))}
                      className="bg-brand-gradient text-white px-4 py-2 rounded-xl text-sm font-medium shadow-glow">
                      Move to {nextStatus(detailBug.status)}
                    </button>
                  )}
                  {detailBug.status !== 'Open' && (
                    <button onClick={() => handleStatusChange(detailBug.id, 'Open')}
                      className="bg-white/5 hover:bg-white/10 text-slate-200 px-4 py-2 rounded-xl text-sm font-medium">Reopen</button>
                  )}
                  <button onClick={() => { closeDetail(); openEditModal(detailBug); }}
                    className="bg-white/5 hover:bg-white/10 text-slate-200 px-4 py-2 rounded-xl text-sm font-medium ml-auto">Edit</button>
                </div>
              </div>
            </motion.div>
          </div>
        );
      })()}

      {enlargedEvidence && (
        <div className="fixed inset-0 bg-black/90 flex items-center justify-center p-4 z-[60] cursor-pointer" onClick={() => setEnlargedEvidence(null)}>
          <button className="absolute top-4 right-4 text-white hover:text-slate-300" onClick={() => setEnlargedEvidence(null)}><X className="w-8 h-8" /></button>
          <img src={enlargedEvidence} alt="Evidence enlarged" className="max-w-full max-h-full object-contain" onClick={(e) => e.stopPropagation()} />
        </div>
      )}
    </div>
  );
};

export default BugTracker;
