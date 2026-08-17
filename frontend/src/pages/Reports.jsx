import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { motion } from 'framer-motion';
import {
  LineChart, Line,
  BarChart, Bar,
  PieChart, Pie, Cell,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer
} from 'recharts';
import {
  FileText, RefreshCw, BarChart3, FolderKanban, ClipboardList, Play,
  HeartPulse, Bug, CheckCircle2, TrendingUp, Clock, Trophy, Calendar,
  History, Loader2, LineChart as LineChartIcon, PieChart as PieChartIcon,
  CheckCircle, XCircle,
} from 'lucide-react';
import Sidebar from '../components/Sidebar';
import AmbientBackground from '../components/AmbientBackground';
import {
  getSummary,
  getHealthTrend,
  getBugBreakdown,
  getTopCategories,
  getTestPassRate,
  getRecentRuns,
} from '../api/reports';

const Kpi = ({ value, label, Icon, tone, suffix }) => (
  <div className="glass rounded-2xl p-5">
    <div className="flex items-start justify-between">
      <div>
        <div className={'text-3xl font-display font-bold ' + (tone || 'text-white')}>
          {value}{suffix}
        </div>
        <div className="text-sm text-slate-400 mt-1">{label}</div>
      </div>
      <div className="grid place-items-center w-10 h-10 rounded-xl bg-white/5 text-slate-300">
        <Icon className="w-5 h-5" />
      </div>
    </div>
  </div>
);

const ChartCard = ({ title, Icon, note, hasData, children }) => (
  <div className="glass rounded-2xl p-5">
    <div className="flex justify-between items-center mb-4">
      <h2 className="text-lg font-display font-bold text-white flex items-center gap-2">
        <Icon className="w-4 h-4 text-brand-sky" /> {title}
      </h2>
      <span className="text-xs text-slate-500">{note}</span>
    </div>
    {hasData ? children : <div className="text-center py-12 text-slate-500 text-sm">No data yet</div>}
  </div>
);

const Reports = () => {
  const navigate = useNavigate();

  // Two different states, deliberately. `loading` is the very first load, when
  // there is genuinely nothing to show yet. `refreshing` is every load after
  // that, where the previous numbers are still on screen and still true.
  // Collapsing them into one is what made every refresh feel like a page
  // reload: the whole dashboard was unmounted and rebuilt to fetch the same
  // shape of data, so charts flashed and scroll position was lost.
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [summary, setSummary] = useState(null);
  const [healthTrend, setHealthTrend] = useState([]);
  const [bugBreakdown, setBugBreakdown] = useState({ by_status: [], by_severity: [] });
  const [topCategories, setTopCategories] = useState([]);
  const [passRate, setPassRate] = useState([]);
  const [recentRuns, setRecentRuns] = useState([]);
  const [exportingPdf, setExportingPdf] = useState(false);
  // Bumped by the Refresh button to re-run the report load effect.
  const [reloadFlag, setReloadFlag] = useState(0);
  const reloadReports = () => setReloadFlag((f) => f + 1);

  useEffect(() => {
    if (!localStorage.getItem('token')) {
      navigate('/login');
      return;
    }

    const loadAllReports = async () => {
      // Only blank the screen when there is nothing on it yet.
      setRefreshing(true);
      try {
        const [s, h, b, c, p, r] = await Promise.all([
          getSummary(),
          getHealthTrend(),
          getBugBreakdown(),
          getTopCategories(),
          getTestPassRate(),
          getRecentRuns(),
        ]);
        setSummary(s.data);
        // Guard every list at the boundary. The charts call .length and .map
        // on these, so a single endpoint returning null or an unexpected
        // shape used to take the entire dashboard down with it. One panel
        // having no data is not a reason to lose the other five.
        const list = (value) => (Array.isArray(value) ? value : []);

        setHealthTrend(list(h.data));
        setBugBreakdown({
          by_status: list(b.data?.by_status),
          by_severity: list(b.data?.by_severity),
        });
        setTopCategories(list(c.data));
        setPassRate(list(p.data));
        setRecentRuns(list(r.data));
      } catch (error) {
        if (error.response?.status === 401) {
          localStorage.clear();
          navigate('/login');
        } else {
          console.error('Failed to load reports', error);
        }
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    };

    loadAllReports();
  }, [navigate, reloadFlag]);

  const handleExportPDF = async () => {
    setExportingPdf(true);
    try {
      const baseUrl = 'http://127.0.0.1:5000/api';
      const response = await fetch(`${baseUrl}/reports/export-pdf`, {
        headers: {
          Authorization: 'Bearer ' + localStorage.getItem('token'),
        },
      });

      if (!response.ok) {
        throw new Error('PDF generation failed');
      }

      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'qa-platform-report-' + new Date().toISOString().split('T')[0] + '.pdf';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      window.URL.revokeObjectURL(url);
    } catch (err) {
      alert('Failed to export PDF. Please try again.');
      console.error('PDF export error:', err);
    } finally {
      setExportingPdf(false);
    }
  };

  // Chart color palettes (vibrant on dark)
  const STATUS_COLORS = {
    'Open': '#f87171',
    'In Progress': '#fbbf24',
    'Resolved': '#2dd4bf',
    'Closed': '#94a3b8',
  };

  const SEVERITY_COLORS = {
    'Critical': '#ef4444',
    'Major': '#fb923c',
    'Minor': '#fbbf24',
  };

  const scoreColorClass = (score) => {
    if (score >= 90) return 'text-brand-teal';
    if (score >= 70) return 'text-brand-sky';
    if (score >= 50) return 'text-amber-400';
    return 'text-red-400';
  };

  // shared recharts props for dark theme
  const axisTick = { fontSize: 11, fill: '#94a3b8' };
  const gridStroke = 'rgba(255,255,255,0.08)';
  const tooltipStyle = {
    backgroundColor: '#161b22',
    border: '1px solid rgba(255,255,255,0.1)',
    borderRadius: 12,
    color: '#e2e8f0',
  };
  const legendStyle = { fontSize: 12, color: '#94a3b8' };

  // Only the very first load gets the empty screen. Every refresh after that
  // keeps the dashboard mounted so charts, scroll position and focus survive.
  if (loading) {
    return (
      <div className="relative flex min-h-screen text-slate-200">
        <AmbientBackground />
        <Sidebar />
        <div className="flex-1 flex items-center justify-center">
          <div className="flex items-center gap-2 text-slate-400">
            <Loader2 className="w-5 h-5 animate-spin" /> Loading reports...
          </div>
        </div>
      </div>
    );
  }

  const hasNoData = summary && summary.total_runs === 0 && summary.total_bugs === 0;

  return (
    <div className="relative flex min-h-screen text-slate-200">
      <AmbientBackground />
      <Sidebar />

      <div className="flex-1 p-8">
        {/* HEADER */}
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4 }}
          className="flex justify-between items-center mb-6 gap-4"
        >
          <div>
            <h1 className="text-3xl font-display font-bold text-white">Reports &amp; Analytics</h1>
            <p className="text-slate-400 mt-1 flex items-center gap-2">
              Insights from your testing activity
              {/* A quiet marker instead of tearing the dashboard down. The
                  numbers on screen stay readable while the new ones arrive. */}
              {refreshing && (
                <span role="status" aria-live="polite"
                  className="inline-flex items-center gap-1.5 text-xs text-brand-sky">
                  <Loader2 className="w-3 h-3 animate-spin" /> Updating
                </span>
              )}
            </p>
          </div>
          <div className="flex gap-3">
            <button
              onClick={handleExportPDF}
              disabled={exportingPdf || hasNoData}
              className={
                'inline-flex items-center gap-2 px-5 py-2.5 rounded-xl font-medium transition ' +
                (exportingPdf
                  ? 'bg-brand-indigo/20 text-brand-indigo cursor-wait'
                  : hasNoData
                  ? 'bg-white/5 text-slate-500 border border-white/10 cursor-not-allowed'
                  : 'bg-brand-gradient text-white shadow-glow hover:shadow-glow-teal')
              }
            >
              {exportingPdf ? <><Loader2 className="w-4 h-4 animate-spin" /> Generating...</> : <><FileText className="w-4 h-4" /> Export PDF</>}
            </button>
            <button
              onClick={reloadReports}
              className="inline-flex items-center gap-2 glass hover:bg-white/10 text-slate-200 px-5 py-2.5 rounded-xl font-medium transition"
            >
              <RefreshCw className="w-4 h-4" /> Refresh
            </button>
          </div>
        </motion.div>

        {hasNoData ? (
          <div className="glass rounded-2xl p-12 text-center">
            <div className="grid place-items-center w-16 h-16 mx-auto rounded-2xl bg-brand-indigo/15 text-brand-indigo mb-4">
              <BarChart3 className="w-8 h-8" />
            </div>
            <h3 className="text-xl font-display font-semibold text-white mb-2">No data yet</h3>
            <p className="text-slate-400 mb-6">Run some automated tests and log some bugs to see beautiful reports here!</p>
            <div className="flex gap-3 justify-center">
              <button onClick={() => navigate('/projects')} className="bg-white/5 hover:bg-white/10 text-slate-200 px-5 py-2.5 rounded-xl">Go to Projects</button>
              <button onClick={() => navigate('/testcases')} className="bg-brand-gradient text-white px-5 py-2.5 rounded-xl shadow-glow">Run Tests</button>
            </div>
          </div>
        ) : (
          <>
            {/* SUMMARY KPI CARDS */}
            {summary && (
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
                <Kpi value={summary.total_projects} label="Projects" Icon={FolderKanban} tone="text-brand-indigo" />
                <Kpi value={summary.total_testcases} label="Test Cases" Icon={ClipboardList} tone="text-brand-sky" />
                <Kpi value={summary.total_runs} label="Test Runs" Icon={Play} tone="text-brand-sky" />
                <Kpi value={summary.avg_health_score} label="Avg Health" Icon={HeartPulse} tone={scoreColorClass(summary.avg_health_score)} />
                <Kpi value={summary.total_bugs} label="Total Bugs" Icon={Bug} tone="text-red-400" />
                <Kpi value={summary.resolved_bugs} label="Resolved" Icon={CheckCircle2} tone="text-brand-teal" />
                <Kpi value={summary.resolution_rate} suffix="%" label="Resolution Rate" Icon={TrendingUp} tone="text-amber-400" />
                <Kpi value={summary.avg_resolution_days || '—'} suffix={summary.avg_resolution_days > 0 ? 'd' : ''} label="Avg Resolution" Icon={Clock} tone="text-white" />
              </div>
            )}

            {/* CHARTS GRID */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
              <ChartCard title="Health Score Trend" Icon={LineChartIcon} note="Last 20 runs" hasData={healthTrend.length > 0}>
                <ResponsiveContainer width="100%" height={280}>
                  <LineChart data={healthTrend}>
                    <CartesianGrid strokeDasharray="3 3" stroke={gridStroke} />
                    <XAxis dataKey="run_number" tick={axisTick} />
                    <YAxis domain={[0, 100]} tick={axisTick} />
                    <Tooltip contentStyle={tooltipStyle} labelStyle={{ color: '#e5e7eb' }} />
                    <Line type="monotone" dataKey="health_score" stroke="#6366f1" strokeWidth={3} dot={{ fill: '#6366f1', r: 4 }} activeDot={{ r: 6 }} name="Health Score" />
                  </LineChart>
                </ResponsiveContainer>
              </ChartCard>

              <ChartCard title="Bug Status" Icon={PieChartIcon} note="Current state" hasData={bugBreakdown.by_status.length > 0}>
                <ResponsiveContainer width="100%" height={280}>
                  <PieChart>
                    <Pie data={bugBreakdown.by_status} cx="50%" cy="50%" innerRadius={50} outerRadius={90} paddingAngle={3} dataKey="value"
                      label={(entry) => entry.name + ': ' + entry.value} labelLine={false}>
                      {bugBreakdown.by_status.map((entry, i) => (
                        <Cell key={i} fill={STATUS_COLORS[entry.name] || '#94a3b8'} />
                      ))}
                    </Pie>
                    <Tooltip contentStyle={tooltipStyle} />
                    <Legend wrapperStyle={legendStyle} />
                  </PieChart>
                </ResponsiveContainer>
              </ChartCard>

              <ChartCard title="Bug Severity" Icon={PieChartIcon} note="By priority" hasData={bugBreakdown.by_severity.length > 0}>
                <ResponsiveContainer width="100%" height={280}>
                  <PieChart>
                    <Pie data={bugBreakdown.by_severity} cx="50%" cy="50%" innerRadius={60} outerRadius={90} paddingAngle={3} dataKey="value"
                      label={(entry) => entry.name + ': ' + entry.value} labelLine={false}>
                      {bugBreakdown.by_severity.map((entry, i) => (
                        <Cell key={i} fill={SEVERITY_COLORS[entry.name] || '#94a3b8'} />
                      ))}
                    </Pie>
                    <Tooltip contentStyle={tooltipStyle} />
                    <Legend wrapperStyle={legendStyle} />
                  </PieChart>
                </ResponsiveContainer>
              </ChartCard>

              <ChartCard title="Top Issue Categories" Icon={Trophy} note="Most common" hasData={topCategories.length > 0}>
                <ResponsiveContainer width="100%" height={280}>
                  <BarChart data={topCategories} layout="vertical">
                    <CartesianGrid strokeDasharray="3 3" stroke={gridStroke} />
                    <XAxis type="number" tick={axisTick} />
                    <YAxis type="category" dataKey="category" width={130} tick={axisTick} />
                    <Tooltip contentStyle={tooltipStyle} cursor={{ fill: 'rgba(255,255,255,0.04)' }} />
                    <Bar dataKey="count" fill="#6366f1" radius={[0, 6, 6, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </ChartCard>
            </div>

            {/* PASS RATE */}
            <div className="glass rounded-2xl p-5 mb-6">
              <div className="flex justify-between items-center mb-4">
                <h2 className="text-lg font-display font-bold text-white flex items-center gap-2">
                  <Calendar className="w-4 h-4 text-brand-sky" /> Test Pass Rate Over Time
                </h2>
                <span className="text-xs text-slate-500">Last 14 days</span>
              </div>
              {passRate.length === 0 ? (
                <div className="text-center py-12 text-slate-500 text-sm">No test runs in the last 14 days</div>
              ) : (
                <ResponsiveContainer width="100%" height={250}>
                  <BarChart data={passRate}>
                    <CartesianGrid strokeDasharray="3 3" stroke={gridStroke} />
                    <XAxis dataKey="date" tick={axisTick} />
                    <YAxis tick={axisTick} />
                    <Tooltip contentStyle={tooltipStyle} cursor={{ fill: 'rgba(255,255,255,0.04)' }} />
                    <Legend wrapperStyle={legendStyle} />
                    <Bar dataKey="passed" stackId="a" fill="#2dd4bf" name="Passed" />
                    <Bar dataKey="failed" stackId="a" fill="#f87171" name="Failed" />
                  </BarChart>
                </ResponsiveContainer>
              )}
            </div>

            {/* RECENT RUNS */}
            <div className="glass rounded-2xl p-5">
              <div className="flex justify-between items-center mb-4">
                <h2 className="text-lg font-display font-bold text-white flex items-center gap-2">
                  <History className="w-4 h-4 text-brand-sky" /> Recent Test Runs
                </h2>
                <span className="text-xs text-slate-500">Latest 10</span>
              </div>

              {recentRuns.length === 0 ? (
                <div className="text-center py-12 text-slate-500 text-sm">No test runs yet. Try the test runner!</div>
              ) : (
                <div className="space-y-2">
                  {recentRuns.map((run) => (
                    <div key={run.id} className="flex items-center justify-between p-3 bg-white/5 border border-white/10 rounded-xl hover:bg-white/[0.08] transition">
                      <div className="flex items-center gap-3 flex-1 min-w-0">
                        <span className={
                          'inline-flex items-center gap-1 text-xs font-semibold px-2 py-1 rounded-md ' +
                          (run.status === 'Pass' ? 'bg-brand-teal/15 text-brand-teal' : 'bg-red-500/15 text-red-300')
                        }>
                          {run.status === 'Pass' ? <CheckCircle className="w-3.5 h-3.5" /> : <XCircle className="w-3.5 h-3.5" />} {run.status}
                        </span>
                        <div className="min-w-0 flex-1">
                          <p className="text-sm font-medium text-slate-100 truncate">{run.test_case_title}</p>
                          <p className="text-xs text-slate-500">{run.project_name} · {new Date(run.run_at).toLocaleString()}</p>
                        </div>
                      </div>

                      <div className="flex items-center gap-4 flex-shrink-0">
                        <div className="text-right">
                          <div className={'text-sm font-bold ' + scoreColorClass(run.health_score)}>{run.health_score}/100</div>
                          <div className="text-xs text-slate-500">Health</div>
                        </div>
                        <div className="text-right">
                          <div className="text-sm font-medium text-slate-200">{run.issues_found}</div>
                          <div className="text-xs text-slate-500">Issues</div>
                        </div>
                        <div className="text-right">
                          <div className="text-sm font-medium text-slate-200">{(run.duration_ms / 1000).toFixed(1)}s</div>
                          <div className="text-xs text-slate-500">Time</div>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
};

export default Reports;
