import { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { motion } from 'framer-motion';
import {
  Plus, FlaskConical, Bot, Play, History, Pencil, Trash2,
  CheckCircle2, XCircle, RotateCcw, Wand2, X, Clock, Gauge,
  Package, Repeat, Link2, Bug, ImageOff, Search, ShieldAlert,
  Accessibility, Smartphone, Camera, Loader2, Inbox, PartyPopper,
  ChevronDown, ChevronRight, Network, AlertTriangle, MousePointerClick, FormInput, Eye, FileWarning,
} from 'lucide-react';
import Sidebar from '../components/Sidebar';
import AmbientBackground from '../components/AmbientBackground';
import { assetUrl } from '../config';
import { getAllProjects } from '../api/projects';
import {
  getTestCases,
  createTestCase,
  updateTestCase,
  updateTestCaseStatus,
  deleteTestCase,
} from '../api/testcases';
import { runTestCaseAsync, getRunJob, cancelRunJob, getTestRuns, getCrawledPages,
  startManualRun, getManualRunStatus, finishManualRun, cancelManualRun,
  autoCrawlManualRun } from '../api/runner';
import { createBugsFromTestRun } from '../api/bugs';

// ---- pure helpers (module scope) ----
const scoreColor = (score) => {
  if (score >= 90) return { text: 'text-brand-teal', border: 'border-brand-teal/50', bar: 'bg-brand-teal', label: 'Excellent' };
  if (score >= 70) return { text: 'text-brand-sky', border: 'border-brand-sky/50', bar: 'bg-brand-sky', label: 'Good' };
  if (score >= 50) return { text: 'text-amber-400', border: 'border-amber-400/50', bar: 'bg-amber-400', label: 'Needs Improvement' };
  return { text: 'text-red-400', border: 'border-red-400/50', bar: 'bg-red-400', label: 'Poor' };
};

const ScoreIcon = ({ score, className }) => {
  if (score >= 90) return <CheckCircle2 className={className} />;
  if (score >= 70) return <CheckCircle2 className={className} />;
  if (score >= 50) return <AlertTriangle className={className} />;
  return <XCircle className={className} />;
};

const urlPath = (url) => {
  try {
    const u = new URL(url);
    return u.pathname || '/';
  } catch {
    return url;
  }
};

const parsePageFindings = (jsonStr) => {
  if (!jsonStr) return null;
  try {
    return typeof jsonStr === 'string' ? JSON.parse(jsonStr) : jsonStr;
  } catch {
    return null;
  }
};

// Category metadata for combined issue lists / per-page sections
const CATEGORY_META = {
  broken_links: { Icon: Link2, label: 'Broken Links', tone: 'text-red-300 bg-red-500/15' },
  console_errors: { Icon: Bug, label: 'JavaScript Console Errors', tone: 'text-red-300 bg-red-500/15' },
  missing_alt_images: { Icon: ImageOff, label: 'Images Missing Alt Text', tone: 'text-amber-300 bg-amber-500/15' },
  seo_issues: { Icon: Search, label: 'SEO Issues', tone: 'text-amber-300 bg-amber-500/15' },
  security_issues: { Icon: ShieldAlert, label: 'Security Issues', tone: 'text-red-300 bg-red-500/15' },
  accessibility_issues: { Icon: Accessibility, label: 'Accessibility Issues', tone: 'text-brand-indigo bg-brand-indigo/15' },
  mobile_issues: { Icon: Smartphone, label: 'Mobile Issues', tone: 'text-brand-sky bg-brand-sky/15' },
  interaction_issues: { Icon: MousePointerClick, label: 'Functional / Interaction Issues', tone: 'text-orange-300 bg-orange-500/15' },
  form_issues: { Icon: FormInput, label: 'Form Validation Issues', tone: 'text-yellow-300 bg-yellow-500/15' },
  visual_issues: { Icon: Eye, label: 'Visual / Design Issues', tone: 'text-fuchsia-300 bg-fuchsia-500/15' },
  content_issues: { Icon: FileWarning, label: 'Content / Data Issues', tone: 'text-red-300 bg-red-500/15' },
};

const SEVERITY_TONE = {
  critical: 'bg-red-500/20 text-red-300 border-red-500/30',
  major:    'bg-orange-500/20 text-orange-300 border-orange-500/30',
  minor:    'bg-amber-500/20 text-amber-300 border-amber-500/30',
  info:     'bg-slate-500/20 text-slate-300 border-slate-500/30',
};

// Extract a short, readable page label from a full URL.
const pageLabel = (url) => {
  if (!url) return '';
  try {
    const u = new URL(url);
    return u.pathname === '/' ? u.host : (u.pathname + (u.search || ''));
  } catch {
    return url;
  }
};

const IssueCategory = ({ categoryKey, items }) => {
  if (!items || items.length === 0) return null;
  const meta = CATEGORY_META[categoryKey] || { Icon: Bug, label: categoryKey, tone: 'text-slate-300 bg-white/10' };
  const { Icon, label, tone } = meta;
  return (
    <div className="mb-4">
      <h3 className="font-semibold text-slate-100 mb-2 flex items-center gap-2">
        <Icon className="w-4 h-4" />
        <span>{label}</span>
        <span className={'text-xs px-2 py-0.5 rounded-full ' + tone}>{items.length}</span>
      </h3>
      <div className="bg-white/5 border border-white/10 rounded-xl p-3 text-sm max-h-56 overflow-y-auto space-y-1.5">
        {items.map((item, i) => {
          const isStr = typeof item === 'string';
          const text = isStr ? item : (item.display || item.url || item.issue || JSON.stringify(item));
          const severity = isStr ? null : item.severity;
          const pageUrl = isStr ? '' : item.page_url;
          const crop = isStr ? null : item.screenshot_crop;
          const impact = isStr ? null : item.why;
          return (
            <div key={i} className="text-slate-300 break-words">
              <div className="flex flex-wrap items-baseline gap-2">
                <span className="text-slate-500 flex-shrink-0">•</span>
                {severity && (
                  <span className={'text-[10px] uppercase tracking-wide font-semibold px-1.5 py-0.5 rounded border ' + (SEVERITY_TONE[severity] || SEVERITY_TONE.minor)}>
                    {severity}
                  </span>
                )}
                <span className="flex-1 min-w-0">{text}</span>
                {pageUrl && (
                  <span className="text-[11px] text-slate-400 bg-white/5 border border-white/10 px-1.5 py-0.5 rounded" title={pageUrl}>
                    on {pageLabel(pageUrl)}
                  </span>
                )}
              </div>
              {impact && (
                <p className="ml-4 mt-0.5 text-[11px] text-slate-400 italic">Why it matters: {impact}</p>
              )}
              {crop && (
                <a href={assetUrl(crop)} target="_blank" rel="noopener noreferrer"
                   className="mt-1.5 ml-4 block w-fit" title="Screenshot of this exact issue">
                  <img src={assetUrl(crop)} alt="Issue screenshot" loading="lazy"
                       className="max-h-28 rounded-md border border-white/15" />
                </a>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};

// Per-page expandable row (detailed)
const PageRow = ({ page, isExpanded, onToggle }) => {
  const sc = scoreColor(page.health_score || 0);
  const findings = isExpanded ? parsePageFindings(page.findings_evidence) : null;

  const categoryStats = findings ? {
    'Broken Links': (findings.broken_links || []).length,
    'JS Errors': (findings.console_errors || []).length,
    'Missing Alt': (findings.missing_alt_images || []).length,
    'SEO': (findings.seo_issues || []).length,
    'Security': (findings.security_issues || []).length,
    'Accessibility': (findings.accessibility_issues || []).length,
    'Mobile': (findings.mobile_issues || []).length,
    'Functional': (findings.interaction_issues || []).length,
    'Forms': (findings.form_issues || []).length,
    'Visual': (findings.visual_issues || []).length,
    'Content': (findings.content_issues || []).length,
  } : null;

  return (
    <div className={'border-l-4 rounded-r-xl bg-white/5 border border-white/10 hover:bg-white/[0.07] transition ' + sc.border}>
      <button onClick={onToggle} className="w-full text-left p-3 transition">
        <div className="flex items-start gap-3">
          <ScoreIcon score={page.health_score || 0} className={'w-5 h-5 flex-shrink-0 ' + sc.text} />
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-0.5 flex-wrap">
              <span className="font-mono text-sm text-slate-200 truncate">{urlPath(page.url)}</span>
              {page.status_code && page.status_code !== 200 && (
                <span className="text-xs bg-red-500/15 text-red-300 px-1.5 py-0.5 rounded">HTTP {page.status_code}</span>
              )}
            </div>
            {page.page_title && <p className="text-xs text-slate-500 truncate">{page.page_title}</p>}
          </div>
          <div className="text-right flex-shrink-0">
            <div className={'text-lg font-bold ' + sc.text}>{page.health_score || 0}/100</div>
            <div className="text-xs text-slate-500">
              {page.issues_found || 0} issue{page.issues_found !== 1 ? 's' : ''}
            </div>
          </div>
          <div className="text-slate-500 flex-shrink-0">
            {isExpanded ? <ChevronDown className="w-5 h-5" /> : <ChevronRight className="w-5 h-5" />}
          </div>
        </div>
      </button>

      {isExpanded && (
        <div className="border-t border-white/10 p-4">
          {!findings ? (
            <div className="text-sm text-slate-500 italic">No issue details available for this page.</div>
          ) : (
            <>
              <div className="text-sm text-slate-400 mb-3">
                <span className="font-medium">URL:</span>{' '}
                <a href={page.url} target="_blank" rel="noopener noreferrer" className="text-brand-sky hover:underline break-all">
                  {page.url}
                </a>
              </div>
              {findings.error ? (
                <div className="bg-red-500/10 border border-red-500/30 text-red-300 p-3 rounded-xl text-sm">
                  Failed to test this page: {findings.error}
                </div>
              ) : (
                <>
                  {categoryStats && (
                    <div className="flex flex-wrap gap-2 mb-4">
                      {Object.entries(categoryStats).map(([name, count]) =>
                        count > 0 ? (
                          <span key={name} className="text-xs bg-white/5 border border-white/10 px-2 py-1 rounded">
                            {name}: <strong className="text-slate-200">{count}</strong>
                          </span>
                        ) : null
                      )}
                      {Object.values(categoryStats).every((c) => c === 0) && (
                        <span className="text-xs text-brand-teal">No issues found on this page!</span>
                      )}
                    </div>
                  )}

                  {Object.keys(CATEGORY_META).map((key) => {
                    const arr = findings[key];
                    if (!arr || arr.length === 0) return null;
                    const { Icon, label } = CATEGORY_META[key];
                    const shown = arr.slice(0, 5);
                    return (
                      <div key={key} className="mb-3">
                        <p className="text-xs font-semibold text-slate-400 mb-1 flex items-center gap-1.5">
                          <Icon className="w-3.5 h-3.5" /> {label}
                        </p>
                        <ul className="text-xs text-slate-300 space-y-1">
                          {shown.map((f, i) => (
                            <li key={i} className="bg-white/5 p-2 rounded-lg border border-white/10 break-all">
                              {f.severity && (
                                <span className={
                                  'inline-block mr-1.5 px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase align-middle ' +
                                  (f.severity === 'critical' ? 'bg-red-500/20 text-red-300'
                                    : f.severity === 'major' ? 'bg-orange-500/20 text-orange-300'
                                    : f.severity === 'minor' ? 'bg-amber-500/15 text-amber-300'
                                    : 'bg-slate-500/20 text-slate-400')
                                }>{f.severity}</span>
                              )}
                              {f.display || f.url || f.text || f.issue || f.src || JSON.stringify(f)}
                              {f.why && (
                                <span className="block mt-0.5 text-[11px] text-slate-400 italic">Why it matters: {f.why}</span>
                              )}
                              {f.screenshot_crop && (
                                <a href={assetUrl(f.screenshot_crop)} target="_blank" rel="noopener noreferrer"
                                   className="mt-1.5 block w-fit" title="Screenshot of this exact issue">
                                  <img src={assetUrl(f.screenshot_crop)} alt="Issue screenshot" loading="lazy"
                                       className="max-h-28 rounded-md border border-white/15" />
                                </a>
                              )}
                            </li>
                          ))}
                          {arr.length > 5 && (
                            <li className="text-slate-500 italic">...and {arr.length - 5} more</li>
                          )}
                        </ul>
                      </div>
                    );
                  })}

                  {page.screenshot && (
                    <div className="mt-3">
                      <p className="text-xs font-semibold text-slate-400 mb-1 flex items-center gap-1.5">
                        <Camera className="w-3.5 h-3.5" /> Page Screenshot
                      </p>
                      <a href={assetUrl(page.screenshot)} target="_blank" rel="noopener noreferrer">
                        <img
                          src={assetUrl(page.screenshot)}
                          alt={'Screenshot of ' + page.url}
                          className="max-h-48 border border-white/10 rounded-lg hover:border-brand-sky/50 transition"
                        />
                      </a>
                    </div>
                  )}
                </>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
};

const TestCases = () => {
  const navigate = useNavigate();

  const [projects, setProjects] = useState([]);
  const [testCases, setTestCases] = useState([]);
  const [selectedProject, setSelectedProject] = useState('');
  const [loading, setLoading] = useState(true);

  const [showModal, setShowModal] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [formError, setFormError] = useState('');
  const [saving, setSaving] = useState(false);

  // Multiple test cases can run in parallel — every state shape is keyed by
  // testcase id, so one run's progress / disabled state doesn't block another.
  const [runningIds, setRunningIds] = useState(() => new Set());
  const [runProgressMap, setRunProgressMap] = useState(() => new Map()); // tcId -> { tested, total, current }
  const [runJobIds, setRunJobIds] = useState(() => new Map());           // tcId -> jobId (for cancel)
  const [runResult, setRunResult] = useState(null);
  const [showResultModal, setShowResultModal] = useState(false);

  // Manual (tracked) run — headed browser window + auto screenshots.
  const [manualRunFor, setManualRunFor] = useState(null);   // testCase object
  const [manualSnap, setManualSnap] = useState(null);       // server snapshot
  const [manualNote, setManualNote] = useState('');
  const [manualBusy, setManualBusy] = useState(false);
  const [manualMessage, setManualMessage] = useState('');

  const [convertingToBugs, setConvertingToBugs] = useState(false);
  const [conversionMessage, setConversionMessage] = useState('');

  const [historyTestCase, setHistoryTestCase] = useState(null);
  const [historyRuns, setHistoryRuns] = useState([]);
  const [loadingHistory, setLoadingHistory] = useState(false);

  const [crawledPages, setCrawledPages] = useState([]);
  const [loadingPages, setLoadingPages] = useState(false);
  const [expandedPageId, setExpandedPageId] = useState(null);

  const [projectId, setProjectId] = useState('');
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [steps, setSteps] = useState('');
  const [expectedResult, setExpectedResult] = useState('');
  const [priority, setPriority] = useState('Medium');
  const [status, setStatus] = useState('Pending');
  const [testType, setTestType] = useState('manual');
  const [maxPages, setMaxPages] = useState(25);

  // Bumped to trigger a re-fetch of the test case list from outside the effect.
  const [reloadFlag, setReloadFlag] = useState(0);
  const reloadTestCases = () => setReloadFlag((f) => f + 1);

  useEffect(() => {
    if (!localStorage.getItem('token')) {
      navigate('/login');
      return;
    }

    const loadProjects = async () => {
      try {
        const list = await getAllProjects();
        setProjects(list);
      } catch (err) {
        if (err.response?.status === 401) {
          localStorage.clear();
          navigate('/login');
        } else {
          setProjects([]);
        }
      }
    };

    loadProjects();
  }, [navigate]);

  useEffect(() => {
    if (projects.length === 0) return;

    const loadTestCases = async () => {
      setLoading(true);
      try {
        // '' (All Projects) -> backend returns ALL of the user's test cases
        const res = await getTestCases(selectedProject);
        setTestCases(res.data);
      } catch {
        setTestCases([]);
      } finally {
        setLoading(false);
      }
    };

    loadTestCases();
  }, [selectedProject, projects, reloadFlag]);

  useEffect(() => {
    if (!(showResultModal && runResult && runResult.is_multi_page && runResult.run_id)) {
      return;
    }

    const loadCrawledPages = async (runId) => {
      setLoadingPages(true);
      setCrawledPages([]);
      try {
        const res = await getCrawledPages(runId);
        const sorted = [...res.data].sort((a, b) => (a.health_score || 0) - (b.health_score || 0));
        setCrawledPages(sorted);
      } catch (err) {
        console.error('Failed to load per-page results', err);
      } finally {
        setLoadingPages(false);
      }
    };

    loadCrawledPages(runResult.run_id);
  }, [showResultModal, runResult]);

  const priorityColor = (p) => {
    if (p === 'High') return 'bg-red-500/15 text-red-300';
    if (p === 'Medium') return 'bg-amber-500/15 text-amber-300';
    return 'bg-brand-sky/15 text-brand-sky';
  };

  const statusColor = (s) => {
    if (s === 'Pass') return 'bg-brand-teal/15 text-brand-teal';
    if (s === 'Fail') return 'bg-red-500/15 text-red-300';
    return 'bg-white/10 text-slate-400';
  };

  // ===== Derived values =====
  const stats = {
    total: testCases.length,
    passed: testCases.filter((tc) => tc.status === 'Pass').length,
    failed: testCases.filter((tc) => tc.status === 'Fail').length,
    pending: testCases.filter((tc) => tc.status !== 'Pass' && tc.status !== 'Fail').length,
  };

  const isTitleValid = title.trim().length >= 3;
  const isStepsValid = steps.trim().length > 0;
  const isExpectedValid = expectedResult.trim().length > 0;
  const canSubmit = Boolean(projectId) && isTitleValid && isStepsValid && isExpectedValid && !saving;

  // ===== Form helpers =====
  const resetForm = () => {
    setProjectId(selectedProject || '');
    setTitle('');
    setDescription('');
    setSteps('');
    setExpectedResult('');
    setPriority('Medium');
    setStatus('Pending');
    setTestType('manual');
    setMaxPages(25);
    setFormError('');
  };

  const openCreateModal = () => {
    setEditingId(null);
    resetForm();
    setShowModal(true);
  };

  const openEditModal = (tc) => {
    setEditingId(tc.id);
    setProjectId(String(tc.project_id));
    setTitle(tc.title || '');
    setDescription(tc.description || '');
    setSteps(tc.steps || '');
    setExpectedResult(tc.expected_result || '');
    setPriority(tc.priority || 'Medium');
    setStatus(tc.status || 'Pending');
    setTestType(tc.test_type || 'manual');
    setMaxPages(tc.max_pages || 25);
    setFormError('');
    setShowModal(true);
  };

  const closeModal = () => {
    setShowModal(false);
    setEditingId(null);
    setFormError('');
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!canSubmit) return;
    setSaving(true);
    setFormError('');

    const payload = {
      project_id: projectId,
      title: title.trim(),
      description: description.trim(),
      steps: steps.trim(),
      expected_result: expectedResult.trim(),
      priority,
      status,
      test_type: testType,
      // Automated = crawl the whole site; manual = just the single page.
      crawl_pages: testType === 'automated',
      max_pages: testType === 'automated' ? maxPages : 1,
    };

    try {
      if (editingId) {
        await updateTestCase(editingId, payload);
      } else {
        await createTestCase(payload);
      }
      closeModal();
      reloadTestCases();
    } catch (err) {
      setFormError(err.response?.data?.error || 'Failed to save test case');
    } finally {
      setSaving(false);
    }
  };

  // ===== Row actions =====
  const handleStatusChange = async (id, newStatus) => {
    try {
      await updateTestCaseStatus(id, newStatus);
      setTestCases((prev) => prev.map((tc) => (tc.id === id ? { ...tc, status: newStatus } : tc)));
    } catch (_) {
      console.error('Failed to update status', _);
    }
  };

  const handleDelete = async (id, tcTitle) => {
    if (!window.confirm(`Delete test case "${tcTitle}"? This cannot be undone.`)) return;
    try {
      await deleteTestCase(id);
      setTestCases((prev) => prev.filter((tc) => tc.id !== id));
    } catch (_) {
      console.error('Failed to delete test case', _);
    }
  };

  const markRunning = (tcId, on) =>
    setRunningIds((prev) => {
      const next = new Set(prev);
      if (on) next.add(tcId); else next.delete(tcId);
      return next;
    });

  const setRunProgress = (tcId, progress) =>
    setRunProgressMap((prev) => {
      const next = new Map(prev);
      if (progress === null) next.delete(tcId); else next.set(tcId, progress);
      return next;
    });

  // Format milliseconds as "1.2s", or "—" when the value is missing (e.g. an
  // interrupted run has no timing) so the UI never shows "NaNs".
  const fmtSecs = (ms) => (Number.isFinite(ms) ? (ms / 1000).toFixed(1) + 's' : '—');

  const setRunJobId = (tcId, jobId) =>
    setRunJobIds((prev) => {
      const next = new Map(prev);
      if (jobId === null) next.delete(tcId); else next.set(tcId, jobId);
      return next;
    });

  const handleCancelRun = async (tcId) => {
    const jobId = runJobIds.get(tcId);
    if (!jobId) return;
    try {
      await cancelRunJob(jobId);
    } catch (err) {
      console.error('cancel failed', err);
    }
  };

  const handleRun = async (tc) => {
    markRunning(tc.id, true);
    setRunProgress(tc.id, null);
    setConversionMessage('');
    try {
      // Queue the run on a background worker, then poll for progress/result.
      // If the project has no login session, the backend blocks with 409 so we
      // can warn that the crawl would run logged-out; re-send with force on OK.
      let startRes;
      try {
        startRes = await runTestCaseAsync(tc.id);
      } catch (err) {
        if (err.response?.status === 409 && err.response?.data?.needs_login_confirm) {
          const proceed = window.confirm(
            (err.response.data.error || 'No login session for this project.') +
            '\n\nClick OK to run anyway as a logged-out visitor, or Cancel to log in '
            + 'first via "Open & Login" on the Projects page.'
          );
          if (!proceed) return;   // finally{} resets the running state
          startRes = await runTestCaseAsync(tc.id, true);
        } else {
          throw err;
        }
      }
      const jobId = startRes.data.job_id;
      setRunJobId(tc.id, jobId);

      const data = await pollRunJob(tc.id, jobId);

      if (data.status === 'failed') {
        throw new Error(data.error || 'Test run failed');
      }
      const result = data.result || {};
      setRunResult({ ...result, testCaseTitle: tc.title });
      setShowResultModal(true);
      if (result.status) {
        setTestCases((prev) =>
          prev.map((t) => (t.id === tc.id ? { ...t, status: result.status } : t))
        );
      }
    } catch (err) {
      setRunResult({
        testCaseTitle: tc.title,
        status: 'Fail',
        health_score: 0,
        issues_found: 0,
        error_message: err.response?.data?.error || err.message || 'Test run failed',
      });
      setShowResultModal(true);
    } finally {
      markRunning(tc.id, false);
      setRunProgress(tc.id, null);
      setRunJobId(tc.id, null);
    }
  };

  // Holds the pending poll timeout + an alive flag so the recursive poll loop
  // can be stopped the moment the component unmounts. Without this the setTimeout
  // chain keeps hitting the API and calling setState on an unmounted component
  // (memory leak + React warning + a closed modal getting resurrected).
  const pollTimerRef = useRef(null);
  const pollAliveRef = useRef(true);

  useEffect(() => {
    pollAliveRef.current = true;
    return () => {
      pollAliveRef.current = false;
      if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
    };
  }, []);

  // Poll a queued run until it finishes, updating live progress as it goes.
  const pollRunJob = (tcId, jobId) =>
    new Promise((resolve, reject) => {
      const tick = async () => {
        if (!pollAliveRef.current) return;   // component gone — stop polling
        try {
          const res = await getRunJob(jobId);
          if (!pollAliveRef.current) return;
          const job = res.data;
          if (job.progress) setRunProgress(tcId, job.progress);
          if (job.status === 'done' || job.status === 'failed' || job.status === 'cancelled') {
            resolve(job);
          } else {
            pollTimerRef.current = setTimeout(tick, 2000);
          }
        } catch (e) {
          if (!pollAliveRef.current) return;
          // A 404 mid-poll means the in-memory job vanished — almost always the
          // backend restarted (a .py save triggers Flask's reloader) and killed
          // the crawl. Give a clear cause instead of a bare "Job not found".
          if (e.response?.status === 404) {
            reject(new Error(
              'The run was interrupted — the backend restarted before it finished '
              + '(this happens if a server file is saved mid-run). Please run the test again.'
            ));
          } else {
            reject(e);
          }
        }
      };
      tick();
    });

  // ===== Manual (tracked) run =====
  const openManualRun = async (tc) => {
    setManualBusy(true);
    setManualMessage('');
    setManualNote('');
    setManualSnap(null);
    setManualRunFor(tc);
    try {
      const res = await startManualRun(tc.id);
      setManualSnap(res.data.run);
      setManualMessage(res.data.message || 'Browser opened — start your test.');
    } catch (err) {
      setManualMessage(err.response?.data?.error || 'Failed to open browser.');
      setManualRunFor(null);
    } finally {
      setManualBusy(false);
    }
  };

  // User has finished logging in and clicked "start crawl" — tell the worker to
  // take over and BFS-walk the authenticated app. The crawl never starts on its
  // own, so it can't run before the user is logged in.
  const triggerAutoCrawl = async () => {
    if (!manualRunFor) return;
    setManualBusy(true);
    setManualMessage('');
    try {
      await autoCrawlManualRun(manualRunFor.id);
      setManualMessage('Crawl started — walking the pages now.');
    } catch (err) {
      setManualMessage(err.response?.data?.error || 'Could not start the crawl.');
    } finally {
      setManualBusy(false);
    }
  };

  // Poll the manual run snapshot every 2s while the modal is open.
  useEffect(() => {
    if (!manualRunFor) return undefined;
    let alive = true;
    const tick = async () => {
      try {
        const res = await getManualRunStatus(manualRunFor.id);
        if (!alive) return;
        setManualSnap(res.data);
      } catch {
        /* swallow polling errors */
      }
    };
    tick();
    const id = setInterval(tick, 2000);
    return () => { alive = false; clearInterval(id); };
  }, [manualRunFor]);

  const finishManual = async (outcome) => {
    if (!manualRunFor) return;
    setManualBusy(true);
    setManualMessage('');
    try {
      const res = await finishManualRun(manualRunFor.id, outcome, manualNote);
      setManualMessage(
        `${res.data.status} — ${res.data.pages_visited} page(s) captured.`
      );
      setTestCases((prev) =>
        prev.map((t) => (t.id === manualRunFor.id ? { ...t, status: res.data.status } : t))
      );
      setTimeout(() => {
        setManualRunFor(null);
        setManualSnap(null);
      }, 1200);
    } catch (err) {
      setManualMessage(err.response?.data?.error || 'Failed to save manual run.');
    } finally {
      setManualBusy(false);
    }
  };

  const cancelManual = async () => {
    if (!manualRunFor) return;
    setManualBusy(true);
    try {
      await cancelManualRun(manualRunFor.id);
    } catch {
      /* ignore */
    } finally {
      setManualRunFor(null);
      setManualSnap(null);
      setManualNote('');
      setManualBusy(false);
    }
  };

  const handleConvertToBugs = async () => {
    if (!runResult?.run_id) return;
    setConvertingToBugs(true);
    setConversionMessage('');
    try {
      const res = await createBugsFromTestRun(runResult.run_id);
      const created = res.data.created ?? 0;
      const skipped = res.data.skipped_duplicates ?? 0;
      setConversionMessage(
        `Created ${created} bug${created !== 1 ? 's' : ''}` +
          (skipped ? ` (skipped ${skipped} duplicate${skipped !== 1 ? 's' : ''})` : '') + '.'
      );
    } catch (err) {
      setConversionMessage(err.response?.data?.error || 'Failed to convert findings to bugs.');
    } finally {
      setConvertingToBugs(false);
    }
  };

  // ===== History =====
  const openHistory = async (tc) => {
    setHistoryTestCase(tc);
    setLoadingHistory(true);
    setHistoryRuns([]);
    try {
      const res = await getTestRuns(tc.id);
      setHistoryRuns(res.data);
    } catch (_) {
      console.error('Failed to load run history', _);
      setHistoryRuns([]);
    } finally {
      setLoadingHistory(false);
    }
  };

  const closeHistory = () => {
    setHistoryTestCase(null);
    setHistoryRuns([]);
  };

  const closeResultModal = () => {
    setShowResultModal(false);
    setCrawledPages([]);
    setExpandedPageId(null);
  };

  // ---- shared styles ----
  const inputBase =
    'w-full rounded-xl bg-white/5 border px-4 py-2.5 text-slate-100 placeholder:text-slate-500 ' +
    'focus:outline-none focus:ring-2 transition';
  const toggleClass = (active) =>
    'flex-1 cursor-pointer px-4 py-2.5 border rounded-xl text-center text-sm transition ' +
    (active
      ? 'border-brand-indigo/60 bg-brand-indigo/15 text-white font-medium'
      : 'border-white/10 text-slate-400 hover:text-slate-200 hover:bg-white/5');

  return (
    <div className="relative flex min-h-screen text-slate-200">
      <AmbientBackground />
      <Sidebar />

      <div className="flex-1 p-8">
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4 }}
          className="flex justify-between items-center mb-6 gap-4"
        >
          <div>
            <h1 className="text-3xl font-display font-bold text-white">Test Cases</h1>
            <p className="text-slate-400 mt-1">Define manual and automated tests for your projects</p>
          </div>
          <button
            onClick={openCreateModal}
            disabled={projects.length === 0}
            className={
              'flex items-center gap-2 px-5 py-2.5 rounded-xl font-medium transition-all ' +
              (projects.length === 0
                ? 'bg-white/5 text-slate-500 border border-white/10 cursor-not-allowed'
                : 'bg-brand-gradient text-white shadow-glow hover:shadow-glow-teal')
            }
          >
            <Plus className="w-4 h-4" /> New Test Case
          </button>
        </motion.div>

        {projects.length === 0 && (
          <div className="bg-amber-500/10 border border-amber-500/30 text-amber-200 px-4 py-3 rounded-xl mb-6 text-sm">
            You need at least one project before creating test cases.{' '}
            <a href="/projects" className="underline font-medium text-amber-100">Create a project first</a>
          </div>
        )}

        {projects.length > 0 && (
          <div className="glass rounded-2xl p-4 mb-6 flex flex-wrap items-center gap-4">
            <div className="flex items-center gap-2">
              <label className="text-sm font-medium text-slate-300">Project:</label>
              <select
                value={selectedProject}
                onChange={(e) => setSelectedProject(e.target.value)}
                className="px-3 py-2 rounded-lg bg-white/5 border border-white/10 text-sm text-slate-100 focus:outline-none focus:ring-2 focus:ring-brand-sky/60"
              >
                <option value="" className="bg-surface">All Projects</option>
                {projects.map((p) => (
                  <option key={p.id} value={p.id} className="bg-surface">{p.name}</option>
                ))}
              </select>
            </div>

            <div className="flex gap-2 ml-auto text-sm">
              <span className="px-3 py-1 bg-white/5 text-slate-300 rounded-lg font-medium">Total: {stats.total}</span>
              <span className="px-3 py-1 bg-brand-teal/15 text-brand-teal rounded-lg font-medium">Pass: {stats.passed}</span>
              <span className="px-3 py-1 bg-red-500/15 text-red-300 rounded-lg font-medium">Fail: {stats.failed}</span>
              <span className="px-3 py-1 bg-white/5 text-slate-400 rounded-lg font-medium">Pending: {stats.pending}</span>
            </div>
          </div>
        )}

        {loading ? (
          <div className="flex items-center justify-center gap-2 py-20 text-slate-400">
            <Loader2 className="w-5 h-5 animate-spin" /> Loading test cases...
          </div>
        ) : projects.length === 0 ? null : testCases.length === 0 ? (
          <div className="glass rounded-2xl p-12 text-center">
            <div className="grid place-items-center w-16 h-16 mx-auto rounded-2xl bg-brand-indigo/15 text-brand-indigo mb-4">
              <FlaskConical className="w-8 h-8" />
            </div>
            <h3 className="text-xl font-display font-semibold text-white mb-2">No test cases yet</h3>
            <p className="text-slate-400 mb-6">
              {selectedProject ? 'Create your first test case to start testing' : 'Pick a project above, or create a new test case'}
            </p>
            <button
              onClick={openCreateModal}
              className="inline-flex items-center gap-2 bg-brand-gradient text-white px-6 py-3 rounded-xl font-medium shadow-glow"
            >
              <Plus className="w-4 h-4" /> Create Test Case
            </button>
          </div>
        ) : (
          <div className="space-y-4">
            {testCases.map((tc, i) => (
              <motion.div
                key={tc.id}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.3, delay: Math.min(i * 0.03, 0.25) }}
                className="glass rounded-2xl p-5 hover:bg-white/[0.07] transition-colors"
              >
                <div className="flex justify-between items-start gap-4">
                  <div className="flex-1 min-w-0">
                    <div className="flex flex-wrap items-center gap-2 mb-2">
                      <span className={'text-xs font-semibold px-2 py-1 rounded-md ' + priorityColor(tc.priority)}>
                        {(tc.priority || 'medium').toUpperCase()}
                      </span>
                      <span className={'text-xs font-semibold px-2 py-1 rounded-md ' + statusColor(tc.status)}>
                        {tc.status}
                      </span>
                      {tc.test_type === 'automated' ? (
                        <span className="inline-flex items-center gap-1 text-xs font-semibold px-2 py-1 rounded-md bg-brand-indigo/15 text-brand-indigo">
                          <Bot className="w-3.5 h-3.5" /> Automated
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 text-xs font-semibold px-2 py-1 rounded-md bg-brand-sky/15 text-brand-sky">
                          <FlaskConical className="w-3.5 h-3.5" /> Manual
                        </span>
                      )}
                      {!selectedProject && tc.project_name && (
                        <span className="text-xs text-slate-500">in {tc.project_name}</span>
                      )}
                    </div>

                    <h3 className="text-lg font-display font-bold text-white">{tc.title}</h3>

                    {tc.description && <p className="text-sm text-slate-400 mt-1">{tc.description}</p>}

                    <div className="mt-3 bg-white/5 border border-white/10 rounded-xl p-3">
                      <p className="text-xs font-semibold text-slate-500 mb-1">STEPS</p>
                      <pre className="text-sm text-slate-300 whitespace-pre-wrap font-sans">{tc.steps}</pre>
                    </div>

                    <div className="mt-2">
                      <p className="text-xs font-semibold text-slate-500">EXPECTED RESULT</p>
                      <p className="text-sm text-slate-300">{tc.expected_result}</p>
                    </div>
                  </div>
                </div>

                <div className="flex flex-wrap gap-2 mt-4 pt-4 border-t border-white/10">
                  {tc.test_type === 'automated' && (() => {
                    const isRunning = runningIds.has(tc.id);
                    const progress = runProgressMap.get(tc.id);
                    return (
                      <button
                        onClick={() => handleRun(tc)}
                        disabled={isRunning}
                        className={
                          'inline-flex items-center gap-1.5 text-sm px-4 py-1.5 rounded-lg transition font-medium ' +
                          (isRunning
                            ? 'bg-brand-indigo/20 text-brand-indigo cursor-wait'
                            : 'bg-brand-gradient text-white shadow-glow')
                        }
                      >
                        {isRunning ? (
                          <>
                            <Loader2 className="w-4 h-4 animate-spin" />
                            {progress
                              ? `Testing ${progress.tested}/${progress.total}...`
                              : 'Starting...'}
                          </>
                        ) : (
                          <><Play className="w-4 h-4" /> Run Full Site Test</>
                        )}
                      </button>
                    );
                  })()}
                  {runningIds.has(tc.id) && runProgressMap.get(tc.id)?.current && (
                    <span className="text-xs text-slate-400 truncate max-w-[220px]" title={runProgressMap.get(tc.id).current}>
                      {runProgressMap.get(tc.id).current}
                    </span>
                  )}
                  {runningIds.has(tc.id) && runJobIds.has(tc.id) && (
                    <button
                      onClick={() => handleCancelRun(tc.id)}
                      title="Stop this run. Whatever's already been crawled is kept."
                      className="inline-flex items-center gap-1.5 text-sm bg-red-500/10 hover:bg-red-500/20 text-red-300 px-3 py-1.5 rounded-lg transition">
                      <XCircle className="w-3.5 h-3.5" /> Cancel
                    </button>
                  )}

                  {tc.test_type === 'manual' && (
                    <button onClick={() => openManualRun(tc)}
                      disabled={manualRunFor !== null}
                      className={
                        'inline-flex items-center gap-1.5 text-sm px-4 py-1.5 rounded-lg transition font-medium ' +
                        (manualRunFor !== null
                          ? 'bg-white/5 text-slate-500 cursor-not-allowed'
                          : 'bg-brand-gradient text-white shadow-glow')
                      }
                      title="Open a browser to walk through this test, with auto screenshots">
                      <Play className="w-4 h-4" /> Run Manual Test
                    </button>
                  )}

                  <button onClick={() => handleStatusChange(tc.id, 'Pass')}
                    className="inline-flex items-center gap-1.5 text-sm bg-brand-teal/10 hover:bg-brand-teal/20 text-brand-teal px-3 py-1.5 rounded-lg transition">
                    <CheckCircle2 className="w-3.5 h-3.5" /> Pass
                  </button>
                  <button onClick={() => handleStatusChange(tc.id, 'Fail')}
                    className="inline-flex items-center gap-1.5 text-sm bg-red-500/10 hover:bg-red-500/20 text-red-300 px-3 py-1.5 rounded-lg transition">
                    <XCircle className="w-3.5 h-3.5" /> Fail
                  </button>
                  <button onClick={() => handleStatusChange(tc.id, 'Pending')}
                    className="inline-flex items-center gap-1.5 text-sm bg-white/5 hover:bg-white/10 text-slate-400 px-3 py-1.5 rounded-lg transition">
                    <RotateCcw className="w-3.5 h-3.5" /> Reset
                  </button>

                  {tc.test_type === 'automated' && (
                    <button onClick={() => openHistory(tc)}
                      className="inline-flex items-center gap-1.5 text-sm bg-white/5 hover:bg-white/10 text-slate-300 px-3 py-1.5 rounded-lg transition">
                      <History className="w-3.5 h-3.5" /> History
                    </button>
                  )}

                  <div className="ml-auto flex gap-2">
                    <button onClick={() => openEditModal(tc)}
                      className="inline-flex items-center gap-1.5 text-sm bg-white/5 hover:bg-white/10 text-slate-300 px-4 py-1.5 rounded-lg transition">
                      <Pencil className="w-3.5 h-3.5" /> Edit
                    </button>
                    <button onClick={() => handleDelete(tc.id, tc.title)}
                      className="inline-flex items-center gap-1.5 text-sm bg-red-500/10 hover:bg-red-500/20 text-red-300 px-4 py-1.5 rounded-lg transition">
                      <Trash2 className="w-3.5 h-3.5" /> Delete
                    </button>
                  </div>
                </div>
              </motion.div>
            ))}
          </div>
        )}
      </div>

      {/* CREATE/EDIT MODAL */}
      {showModal && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4 z-50">
          <motion.div
            initial={{ opacity: 0, scale: 0.97, y: 10 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            transition={{ duration: 0.25 }}
            className="glass-strong rounded-2xl shadow-card w-full max-w-lg max-h-screen overflow-y-auto"
          >
            <div className="p-6 border-b border-white/10 flex items-center justify-between">
              <h2 className="text-xl font-display font-bold text-white">
                {editingId ? 'Edit Test Case' : 'Create New Test Case'}
              </h2>
              <button onClick={closeModal} className="text-slate-500 hover:text-slate-200 transition" aria-label="Close">
                <X className="w-5 h-5" />
              </button>
            </div>

            <form onSubmit={handleSubmit} className="p-6 space-y-4">
              {formError && (
                <div className="bg-red-500/10 border border-red-500/30 text-red-300 px-4 py-2.5 rounded-xl text-sm">
                  {formError}
                </div>
              )}

              <div>
                <label className="block text-slate-300 text-sm font-medium mb-1.5">Project *</label>
                <select
                  value={projectId}
                  onChange={(e) => setProjectId(e.target.value)}
                  disabled={editingId !== null}
                  className={`${inputBase} border-white/10 focus:ring-brand-sky/60 disabled:opacity-60`}
                >
                  <option value="" className="bg-surface">Select a project</option>
                  {projects.map((p) => (
                    <option key={p.id} value={p.id} className="bg-surface">{p.name}</option>
                  ))}
                </select>
              </div>

              <div>
                <label className="block text-slate-300 text-sm font-medium mb-1.5">Test Type *</label>
                <div className="flex gap-3">
                  <label className={toggleClass(testType === 'manual')}>
                    <input type="radio" value="manual" checked={testType === 'manual'}
                      onChange={(e) => setTestType(e.target.value)} className="hidden" />
                    <span className="inline-flex items-center gap-1.5"><FlaskConical className="w-4 h-4" /> Manual</span>
                  </label>
                  <label className={toggleClass(testType === 'automated')}>
                    <input type="radio" value="automated" checked={testType === 'automated'}
                      onChange={(e) => setTestType(e.target.value)} className="hidden" />
                    <span className="inline-flex items-center gap-1.5"><Bot className="w-4 h-4" /> Automated</span>
                  </label>
                </div>
              </div>

              {testType === 'automated' && (
                <div className="bg-brand-indigo/10 border border-brand-indigo/30 rounded-xl p-4">
                  <div className="flex items-start gap-3">
                    <Network className="w-6 h-6 text-brand-sky flex-shrink-0" />
                    <div className="flex-1">
                      <p className="font-medium text-white">Full Site Test</p>
                      <p className="text-xs text-slate-300 mt-1">
                        The platform will automatically discover and test every page on your site
                        (up to 100 pages). Each page is checked for broken links, accessibility,
                        security, mobile usability, SEO, and more.
                      </p>
                      <p className="text-xs text-brand-sky mt-2">Larger sites take 2-10 minutes to complete.</p>

                      <div className="mt-3 flex items-center gap-2">
                        <label className="text-xs text-slate-300">Max pages to crawl:</label>
                        <input
                          type="number"
                          min="1"
                          max="100"
                          value={maxPages}
                          onChange={(e) => {
                            const n = parseInt(e.target.value, 10);
                            setMaxPages(Number.isNaN(n) ? 1 : Math.min(100, Math.max(1, n)));
                          }}
                          className="w-20 rounded-lg bg-white/5 border border-white/10 px-2 py-1 text-sm text-slate-100 focus:outline-none focus:ring-2 focus:ring-brand-sky/60"
                        />
                        <span className="text-xs text-slate-500">(1–100)</span>
                      </div>
                    </div>
                  </div>
                </div>
              )}

              <div>
                <label className="block text-slate-300 text-sm font-medium mb-1.5">Title *</label>
                <input type="text" value={title} onChange={(e) => setTitle(e.target.value)}
                  placeholder="User can search for products"
                  className={`${inputBase} ${title && !isTitleValid ? 'border-red-500/60 focus:ring-red-500/50' : 'border-white/10 focus:ring-brand-sky/60'}`} />
                {title && !isTitleValid && (
                  <p className="text-red-400 text-xs mt-1">Title must be at least 3 characters</p>
                )}
              </div>

              <div>
                <label className="block text-slate-300 text-sm font-medium mb-1.5">Description</label>
                <textarea value={description} onChange={(e) => setDescription(e.target.value)}
                  placeholder="What does this test verify?" rows="2"
                  className={`${inputBase} border-white/10 focus:ring-brand-sky/60`} />
              </div>

              <div>
                <label className="block text-slate-300 text-sm font-medium mb-1.5">Steps * (one per line)</label>
                <textarea value={steps} onChange={(e) => setSteps(e.target.value)}
                  placeholder={'1. Open the homepage\n2. Click the search bar\n3. Type a query\n4. Press Enter'} rows="4"
                  className={`${inputBase} font-mono text-sm ${steps && !isStepsValid ? 'border-red-500/60 focus:ring-red-500/50' : 'border-white/10 focus:ring-brand-sky/60'}`} />
              </div>

              <div>
                <label className="block text-slate-300 text-sm font-medium mb-1.5">Expected Result *</label>
                <textarea value={expectedResult} onChange={(e) => setExpectedResult(e.target.value)}
                  placeholder="What should happen if the test passes?" rows="2"
                  className={`${inputBase} ${expectedResult && !isExpectedValid ? 'border-red-500/60 focus:ring-red-500/50' : 'border-white/10 focus:ring-brand-sky/60'}`} />
              </div>

              <div>
                <label className="block text-slate-300 text-sm font-medium mb-1.5">Priority</label>
                <div className="flex gap-3">
                  {['Low', 'Medium', 'High'].map((p) => (
                    <label key={p} className={toggleClass(priority === p)}>
                      <input type="radio" value={p} checked={priority === p}
                        onChange={(e) => setPriority(e.target.value)} className="hidden" />
                      {p}
                    </label>
                  ))}
                </div>
              </div>

              <div>
                <label className="block text-slate-300 text-sm font-medium mb-1.5">Status</label>
                <div className="flex gap-3">
                  {['Pending', 'Pass', 'Fail'].map((s) => (
                    <label key={s} className={toggleClass(status === s)}>
                      <input type="radio" value={s} checked={status === s}
                        onChange={(e) => setStatus(e.target.value)} className="hidden" />
                      {s}
                    </label>
                  ))}
                </div>
              </div>

              <div className="flex gap-3 pt-4 border-t border-white/10">
                <button type="button" onClick={closeModal}
                  className="flex-1 bg-white/5 hover:bg-white/10 text-slate-200 py-3 rounded-xl font-medium transition">
                  Cancel
                </button>
                <button type="submit" disabled={!canSubmit}
                  className={
                    'flex-1 py-3 rounded-xl font-semibold transition flex items-center justify-center gap-2 ' +
                    (canSubmit ? 'bg-brand-gradient text-white shadow-glow hover:shadow-glow-teal' : 'bg-white/5 text-slate-500 border border-white/10 cursor-not-allowed')
                  }>
                  {saving && <Loader2 className="w-4 h-4 animate-spin" />}
                  {saving ? 'Saving...' : editingId ? 'Update Test Case' : 'Create Test Case'}
                </button>
              </div>
            </form>
          </motion.div>
        </div>
      )}

      {/* RESULTS MODAL */}
      {showResultModal && runResult && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4 z-50">
          <motion.div
            initial={{ opacity: 0, scale: 0.97, y: 10 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            transition={{ duration: 0.25 }}
            className="glass-strong rounded-2xl shadow-card w-full max-w-4xl max-h-screen overflow-y-auto"
          >
            <div className="p-6 border-b border-white/10 flex justify-between items-start">
              <div>
                <h2 className="text-xl font-display font-bold text-white">Test Run Results</h2>
                <p className="text-sm text-slate-400 mt-1">{runResult.testCaseTitle}</p>
                {runResult.is_multi_page && (
                  <p className="inline-flex items-center gap-1.5 text-xs text-brand-sky mt-1">
                    <Network className="w-3.5 h-3.5" /> Tested {runResult.pages_tested} pages on this site
                  </p>
                )}
              </div>
              <button onClick={closeResultModal} className="text-slate-500 hover:text-slate-200 transition" aria-label="Close">
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="p-6 space-y-5">
              {runResult.first_page_error && (
                <div className="bg-red-500/10 border border-red-500/30 rounded-xl px-4 py-3 text-sm">
                  <div className="font-semibold text-red-300 mb-1">Could not reach the site</div>
                  <div className="text-red-200/80 break-words">{runResult.first_page_error}</div>
                  <div className="text-xs text-red-200/60 mt-2">
                    The crawl was aborted because the starting URL didn't load. Check the project URL is reachable from this machine, then run the test again.
                  </div>
                </div>
              )}
              {runResult.session_warning && (
                <div className="bg-amber-500/10 border border-amber-500/30 rounded-xl px-4 py-3 text-sm">
                  <div className="font-semibold text-amber-300 mb-1">Session warning</div>
                  <div className="text-amber-200/80 break-words">{runResult.session_warning}</div>
                </div>
              )}
              {(() => {
                const sc = scoreColor(runResult.health_score || 0);
                return (
                  <div className="p-6 rounded-2xl bg-white/5 border border-white/10">
                    <div className="flex items-center gap-6">
                      <div className="relative w-28 h-28 flex-shrink-0">
                        <svg className="w-28 h-28 transform -rotate-90">
                          <circle cx="56" cy="56" r="48" stroke="currentColor" strokeWidth="8" fill="none" className="text-white/10" />
                          <circle cx="56" cy="56" r="48" stroke="currentColor" strokeWidth="8" fill="none"
                            strokeDasharray={2 * Math.PI * 48}
                            strokeDashoffset={2 * Math.PI * 48 * (1 - (runResult.health_score || 0) / 100)}
                            className={sc.text} strokeLinecap="round" />
                        </svg>
                        <div className="absolute inset-0 flex items-center justify-center">
                          <span className={'text-3xl font-bold ' + sc.text}>{runResult.health_score || 0}</span>
                        </div>
                      </div>

                      <div className="flex-1">
                        <div className="flex items-center gap-2 mb-1">
                          <span className={'text-2xl font-display font-bold ' + sc.text}>{sc.label}</span>
                          <span className={
                            'inline-flex items-center gap-1 text-xs font-semibold px-2 py-1 rounded-md ' +
                            (runResult.status === 'Pass' ? 'bg-brand-teal/20 text-brand-teal' : 'bg-red-500/20 text-red-300')
                          }>
                            {runResult.status === 'Pass' ? <><CheckCircle2 className="w-3.5 h-3.5" /> PASS</> : <><XCircle className="w-3.5 h-3.5" /> FAIL</>}
                          </span>
                        </div>
                        <p className="text-slate-300 text-sm">
                          {runResult.issues_found} issue{runResult.issues_found !== 1 ? 's' : ''} detected
                          {runResult.is_multi_page ? ' across ' + runResult.pages_tested + ' pages' : ''}
                        </p>
                      </div>
                    </div>
                  </div>
                );
              })()}

              {runResult.issues_found > 0 && runResult.run_id && (
                <div className="bg-gradient-to-r from-brand-indigo/15 to-brand-teal/10 border border-brand-indigo/30 rounded-2xl p-4">
                  <div className="flex items-center justify-between gap-4 flex-wrap">
                    <div>
                      <h3 className="font-display font-bold text-white mb-1 flex items-center gap-2">
                        <Wand2 className="w-4 h-4 text-brand-sky" /> Auto-Create Bugs
                      </h3>
                      <p className="text-sm text-slate-300">
                        Turn all {runResult.issues_found} findings into trackable bugs in the Bug Tracker.
                      </p>
                    </div>
                    <button onClick={handleConvertToBugs} disabled={convertingToBugs}
                      className={
                        'inline-flex items-center gap-2 px-5 py-2 rounded-xl font-medium whitespace-nowrap transition ' +
                        (convertingToBugs ? 'bg-brand-indigo/20 text-brand-indigo cursor-wait' : 'bg-brand-gradient text-white shadow-glow')
                      }>
                      {convertingToBugs ? <><Loader2 className="w-4 h-4 animate-spin" /> Converting...</> : <><Wand2 className="w-4 h-4" /> Convert to Bugs</>}
                    </button>
                  </div>
                  {conversionMessage && (
                    <div className="mt-3 text-sm text-slate-200 bg-white/5 rounded-lg p-2 border border-white/10">
                      {conversionMessage}
                      {conversionMessage.includes('Created') && (
                        <a href="/bugs" className="text-brand-sky hover:underline ml-2 font-medium">
                          View in Bug Tracker →
                        </a>
                      )}
                    </div>
                  )}
                </div>
              )}

              {runResult.severity_breakdown && (
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <span className="text-slate-500">Severity:</span>
                  {[['critical', 'bg-red-500/20 text-red-300'],
                    ['major', 'bg-orange-500/20 text-orange-300'],
                    ['minor', 'bg-amber-500/15 text-amber-300'],
                    ['info', 'bg-slate-500/20 text-slate-400']].map(([sev, tone]) => (
                    <span key={sev} className={`px-2 py-1 rounded-lg font-semibold ${tone} ${!runResult.severity_breakdown[sev] ? 'opacity-40' : ''}`}>
                      {runResult.severity_breakdown[sev] || 0} {sev}
                    </span>
                  ))}
                </div>
              )}

              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <div className="bg-white/5 border border-white/10 rounded-xl p-3 text-center">
                  <div className="text-xs text-slate-500 mb-1 flex items-center justify-center gap-1"><Clock className="w-3.5 h-3.5" /> Total Time</div>
                  <div className="text-lg font-bold text-white">{fmtSecs(runResult.duration_ms)}</div>
                </div>
                <div className="bg-white/5 border border-white/10 rounded-xl p-3 text-center">
                  <div className="text-xs text-slate-500 mb-1 flex items-center justify-center gap-1"><Gauge className="w-3.5 h-3.5" /> Page Load</div>
                  <div className={
                    'text-lg font-bold ' +
                    (runResult.page_load_time_ms > 5000 ? 'text-red-400'
                      : runResult.page_load_time_ms > 3000 ? 'text-amber-400'
                      : 'text-brand-teal')
                  }>
                    {fmtSecs(runResult.page_load_time_ms)}
                  </div>
                </div>
                <div className="bg-white/5 border border-white/10 rounded-xl p-3 text-center">
                  <div className="text-xs text-slate-500 mb-1 flex items-center justify-center gap-1"><Package className="w-3.5 h-3.5" /> Page Size</div>
                  <div className="text-lg font-bold text-white">{runResult.total_page_size_kb || 0} KB</div>
                </div>
                <div className="bg-white/5 border border-white/10 rounded-xl p-3 text-center">
                  <div className="text-xs text-slate-500 mb-1 flex items-center justify-center gap-1">
                    {runResult.is_multi_page ? <><FlaskConical className="w-3.5 h-3.5" /> Pages</> : <><Repeat className="w-3.5 h-3.5" /> Requests</>}
                  </div>
                  <div className="text-lg font-bold text-white">
                    {runResult.is_multi_page ? runResult.pages_tested : (runResult.total_requests || 0)}
                  </div>
                </div>
              </div>

              {runResult.error_message && (
                <div className="bg-red-500/10 border border-red-500/30 text-red-300 px-4 py-3 rounded-xl">
                  <strong>Error:</strong> {runResult.error_message}
                </div>
              )}

              {/* PER-PAGE BREAKDOWN */}
              {runResult.is_multi_page && (
                <div className="bg-gradient-to-r from-brand-indigo/10 to-brand-sky/10 border border-brand-indigo/30 rounded-2xl p-5">
                  <h3 className="font-display font-bold text-white mb-2 flex items-center gap-2">
                    <Network className="w-4 h-4" />
                    <span>Pages Tested</span>
                    <span className="text-xs font-normal bg-white/10 text-brand-sky px-2 py-1 rounded-full border border-white/10">
                      {crawledPages.length}
                    </span>
                    <span className="text-xs font-normal text-slate-500 ml-auto">Sorted by worst score first · Click to expand</span>
                  </h3>

                  {loadingPages ? (
                    <div className="flex items-center justify-center gap-2 py-6 text-slate-400 text-sm">
                      <Loader2 className="w-4 h-4 animate-spin" /> Loading per-page results...
                    </div>
                  ) : crawledPages.length === 0 ? (
                    <div className="text-center py-6 text-slate-500 text-sm">No per-page data available for this test run.</div>
                  ) : (
                    <div className="space-y-2 mt-3">
                      {crawledPages.map((page) => (
                        <PageRow
                          key={page.id}
                          page={page}
                          isExpanded={expandedPageId === page.id}
                          onToggle={() => setExpandedPageId(expandedPageId === page.id ? null : page.id)}
                        />
                      ))}
                    </div>
                  )}
                </div>
              )}

              {/* Combined issue lists - single-page */}
              {!runResult.is_multi_page && (
                <>
                  <IssueCategory categoryKey="broken_links" items={runResult.broken_links} />
                  <IssueCategory categoryKey="console_errors" items={runResult.console_errors} />
                  <IssueCategory categoryKey="missing_alt_images" items={runResult.missing_alt_images} />
                  <IssueCategory categoryKey="seo_issues" items={runResult.seo_issues} />
                  <IssueCategory categoryKey="security_issues" items={runResult.security_issues} />
                  <IssueCategory categoryKey="accessibility_issues" items={runResult.accessibility_issues} />
                  <IssueCategory categoryKey="mobile_issues" items={runResult.mobile_issues} />
                  <IssueCategory categoryKey="interaction_issues" items={runResult.interaction_issues} />
                  <IssueCategory categoryKey="form_issues" items={runResult.form_issues} />
                  <IssueCategory categoryKey="visual_issues" items={runResult.visual_issues} />
                  <IssueCategory categoryKey="content_issues" items={runResult.content_issues} />
                </>
              )}

              {runResult.status === 'Pass' && runResult.issues_found === 0 && (
                <div className="text-center py-6 bg-brand-teal/10 rounded-2xl border border-brand-teal/30">
                  <PartyPopper className="w-10 h-10 mx-auto mb-2 text-brand-teal" />
                  <p className="text-slate-100 font-medium">No issues detected!</p>
                  <p className="text-sm text-slate-400 mt-1">All checks passed.</p>
                </div>
              )}

              <div className="pt-4 border-t border-white/10">
                <button onClick={closeResultModal}
                  className="w-full bg-brand-gradient text-white py-3 rounded-xl font-medium shadow-glow">
                  Close
                </button>
              </div>
            </div>
          </motion.div>
        </div>
      )}

      {/* MANUAL RUN MODAL */}
      {manualRunFor && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4 z-50">
          <motion.div
            initial={{ opacity: 0, scale: 0.96 }}
            animate={{ opacity: 1, scale: 1 }}
            className="glass-strong w-full max-w-3xl rounded-2xl border border-white/10 overflow-hidden flex flex-col max-h-[85vh]">
            <div className="px-6 py-4 border-b border-white/10 flex items-center gap-3">
              <Play className="w-5 h-5 text-brand-indigo" />
              <div className="flex-1">
                <div className="text-lg font-semibold text-white">Manual Test: {manualRunFor.title}</div>
                <div className="text-xs text-slate-400">
                  Status:&nbsp;
                  <span className="text-brand-teal">{manualSnap?.status || 'starting'}</span>
                  &nbsp;·&nbsp;Pages captured:&nbsp;
                  <span className="text-white">{manualSnap?.pages_visited ?? 0}</span>
                </div>
              </div>
              <button onClick={cancelManual} disabled={manualBusy}
                className="text-slate-400 hover:text-white text-sm px-3 py-1.5 rounded-lg bg-white/5 hover:bg-white/10">
                Close & discard
              </button>
            </div>

            <div className="px-6 py-3 text-sm text-slate-300 bg-white/[0.02] border-b border-white/10">
              A browser window has opened at the project URL. Log in manually
              (handle any OTP / CAPTCHA there). When you have finished logging in
              and are on a page inside the app, click <strong className="text-white">
              I'm logged in — start crawl</strong> and the system will walk every
              reachable page for you. Click <strong className="text-white">Mark Pass</strong> /
              <strong className="text-white"> Mark Fail</strong> when done.
            </div>

            <div className="px-6 py-3 border-b border-white/10 flex items-center gap-2 bg-brand-indigo/5">
              {manualSnap?.status === 'crawling' ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin text-brand-indigo" />
                  <span className="text-sm text-brand-indigo font-medium">
                    Crawling… {manualSnap.pages_visited}/{manualSnap.max_pages}
                  </span>
                </>
              ) : manualSnap?.status === 'ready' ? (
                <>
                  <span className="w-2 h-2 rounded-full bg-brand-teal animate-pulse" />
                  <span className="text-sm text-slate-300 flex-1">
                    Finish logging in, then start the crawl when you're ready.
                  </span>
                  <button onClick={triggerAutoCrawl} disabled={manualBusy}
                    className="inline-flex items-center gap-1.5 text-sm px-3 py-1.5 rounded-lg bg-brand-gradient text-white shadow-glow disabled:opacity-50">
                    <Play className="w-4 h-4" /> I'm logged in — start crawl
                  </button>
                </>
              ) : manualSnap?.status === 'done' ? (
                <span className="text-sm text-brand-teal">Crawl finished.</span>
              ) : (
                <span className="text-sm text-slate-400">Launching browser…</span>
              )}
            </div>

            <div className="flex-1 overflow-y-auto px-6 py-4 space-y-2">
              {(manualSnap?.pages || []).length === 0 ? (
                <div className="text-center text-slate-500 text-sm py-8">
                  Browser ready. Pages will appear here as you navigate.
                </div>
              ) : (
                manualSnap.pages.map((p, i) => (
                  <a key={i} href={assetUrl(p.screenshot)} target="_blank" rel="noreferrer"
                    className="flex gap-3 items-center bg-white/5 hover:bg-white/10 border border-white/10 rounded-lg p-2 transition">
                    <span className="w-7 h-7 flex-shrink-0 flex items-center justify-center rounded bg-brand-indigo/20 text-brand-indigo text-xs font-bold">
                      {i + 1}
                    </span>
                    <img src={assetUrl(p.screenshot)} alt=""
                      className="w-16 h-12 object-cover rounded border border-white/10" />
                    <div className="flex-1 min-w-0">
                      <div className="text-sm text-white truncate">{p.url}</div>
                      <div className="text-xs text-slate-500">{p.captured_at?.slice(11, 19)}</div>
                    </div>
                  </a>
                ))
              )}
            </div>

            <div className="px-6 py-4 border-t border-white/10 space-y-3">
              <textarea
                value={manualNote}
                onChange={(e) => setManualNote(e.target.value)}
                placeholder="Optional note (what did you observe? any issues?)"
                rows={2}
                className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-white placeholder:text-slate-500 resize-none focus:outline-none focus:border-brand-indigo" />
              {manualMessage && (
                <div className="text-sm text-brand-teal">{manualMessage}</div>
              )}
              <div className="flex gap-2 justify-end">
                <button onClick={cancelManual} disabled={manualBusy}
                  className="text-sm px-4 py-2 rounded-lg bg-white/5 hover:bg-white/10 text-slate-300">
                  Cancel
                </button>
                <button onClick={() => finishManual('Fail')} disabled={manualBusy}
                  className="inline-flex items-center gap-1.5 text-sm px-4 py-2 rounded-lg bg-red-500/15 hover:bg-red-500/25 text-red-300">
                  <XCircle className="w-4 h-4" /> Mark Fail
                </button>
                <button onClick={() => finishManual('Pass')} disabled={manualBusy}
                  className="inline-flex items-center gap-1.5 text-sm px-4 py-2 rounded-lg bg-brand-gradient text-white shadow-glow">
                  <CheckCircle2 className="w-4 h-4" /> Mark Pass
                </button>
              </div>
            </div>
          </motion.div>
        </div>
      )}

      {/* HISTORY MODAL */}
      {historyTestCase && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4 z-50">
          <motion.div
            initial={{ opacity: 0, scale: 0.97, y: 10 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            transition={{ duration: 0.25 }}
            className="glass-strong rounded-2xl shadow-card w-full max-w-2xl max-h-screen overflow-y-auto"
          >
            <div className="p-6 border-b border-white/10 flex justify-between items-start">
              <div>
                <h2 className="text-xl font-display font-bold text-white">Run History</h2>
                <p className="text-sm text-slate-400 mt-1">{historyTestCase.title}</p>
              </div>
              <button onClick={closeHistory} className="text-slate-500 hover:text-slate-200 transition" aria-label="Close">
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="p-6">
              {loadingHistory ? (
                <div className="flex items-center justify-center gap-2 py-10 text-slate-400">
                  <Loader2 className="w-5 h-5 animate-spin" /> Loading history...
                </div>
              ) : historyRuns.length === 0 ? (
                <div className="text-center py-10">
                  <Inbox className="w-10 h-10 mx-auto mb-2 text-slate-500" />
                  <p className="text-slate-300">No runs yet</p>
                </div>
              ) : (
                <div className="space-y-3">
                  {historyRuns.map((run) => {
                    const sc = scoreColor(run.health_score || 0);
                    return (
                      <div key={run.id} className="bg-white/5 border border-white/10 rounded-xl p-4">
                        <div className="flex justify-between items-center mb-2">
                          <div className="flex items-center gap-2">
                            <span className={
                              'inline-flex items-center gap-1 text-xs font-semibold px-2 py-1 rounded-md ' +
                              (run.status === 'Pass' ? 'bg-brand-teal/15 text-brand-teal' : 'bg-red-500/15 text-red-300')
                            }>
                              {run.status === 'Pass' ? <><CheckCircle2 className="w-3.5 h-3.5" /> PASS</> : <><XCircle className="w-3.5 h-3.5" /> FAIL</>}
                            </span>
                            <span className={'text-sm font-bold ' + sc.text}>Score: {run.health_score || 0}/100</span>
                          </div>
                          <span className="text-xs text-slate-500">{new Date(run.run_at).toLocaleString()}</span>
                        </div>
                        <div className="text-sm text-slate-300">
                          {run.issues_found} issue{run.issues_found !== 1 ? 's' : ''} ·{' '}
                          {fmtSecs(run.duration_ms)}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </motion.div>
        </div>
      )}
    </div>
  );
};

export default TestCases;