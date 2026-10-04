const vscode = require("vscode");
const fs = require("node:fs/promises");
const path = require("node:path");
const { spawn } = require("node:child_process");
const { chromium } = require("playwright");
const { auditLocalhost } = require("./browser-audit.cjs");
const { loadTestLocalhost } = require("./load-tester.cjs");

const diagnosticCollection = vscode.languages.createDiagnosticCollection("qa-platform");
const sourceExtensions = new Set([".js", ".jsx", ".ts", ".tsx", ".py", ".java", ".go", ".rb", ".php", ".cs"]);
const ignoredNames = new Set([
  ".git", ".hg", ".svn", "node_modules", "dist", "build", "coverage",
  ".next", ".nuxt", ".venv", "venv", "__pycache__", ".pytest_cache"
]);
const secretNames = new Set([".env", ".env.local", ".env.production", ".env.development", "credentials.json", "service-account.json"]);
const cliOutputScripts = new Set(["init_db.py", "migrate_db.py", "backfill_run_metadata.py"]);

function isIgnored(name) {
  return ignoredNames.has(name) || secretNames.has(name);
}

async function collectSourceFiles(root, current = root, result = []) {
  if (result.length >= vscode.workspace.getConfiguration("qaPlatform").get("maxFiles", 5000)) return result;
  const entries = await fs.readdir(current, { withFileTypes: true });
  for (const entry of entries) {
    if (isIgnored(entry.name)) continue;
    const fullPath = path.join(current, entry.name);
    if (entry.isDirectory()) {
      await collectSourceFiles(root, fullPath, result);
    } else if (entry.isFile() && sourceExtensions.has(path.extname(entry.name))) {
      result.push(fullPath);
    }
    if (result.length >= vscode.workspace.getConfiguration("qaPlatform").get("maxFiles", 5000)) break;
  }
  return result;
}

function createDiagnostic(filePath, line, column, message, severity) {
  const range = new vscode.Range(
    Math.max(0, line - 1),
    Math.max(0, column - 1),
    Math.max(0, line - 1),
    Math.max(0, column)
  );
  const diagnostic = new vscode.Diagnostic(
    range,
    message,
    severity === "high"
      ? vscode.DiagnosticSeverity.Error
      : severity === "medium"
        ? vscode.DiagnosticSeverity.Warning
        : vscode.DiagnosticSeverity.Information
  );
  diagnostic.source = "QA Platform";
  return diagnostic;
}

function publishAuditDiagnostics(result, workspacePath) {
  const byFile = new Map();
  for (const finding of result.issues || []) {
    const location = finding.sourceLocation;
    if (!location?.file || !Number.isInteger(location.line) || location.line < 1) continue;
    const filePath = path.resolve(workspacePath, location.file);
    if (!filePath.startsWith(path.resolve(workspacePath) + path.sep)) continue;
    const diagnostics = byFile.get(filePath) || [];
    const diagnostic = createDiagnostic(filePath, location.line, location.column || 1, `QA browser: ${finding.title} (${finding.ruleId})${finding.selector ? ` [${finding.selector}]` : ""}`, finding.severity);
    diagnostic.code = finding.ruleId;
    diagnostics.push(diagnostic);
    byFile.set(filePath, diagnostics);
  }
  for (const [filePath, diagnostics] of byFile) diagnosticCollection.set(vscode.Uri.file(filePath), diagnostics);
}

async function analyzeWorkspace() {
  const workspace = vscode.workspace.workspaceFolders?.[0];
  if (!workspace) {
    vscode.window.showErrorMessage("QA Platform requires an open workspace.");
    return { status: "failed", issueCount: 0, reason: "No workspace is open." };
  }
  const output = vscode.window.createOutputChannel("QA Platform");
  output.show(true);
  output.appendLine(`Analyzing ${workspace.uri.fsPath}`);
  let files;
  try {
    files = await collectSourceFiles(workspace.uri.fsPath);
  } catch (error) {
    vscode.window.showErrorMessage(`QA Platform workspace scan failed: ${error.message}`);
    return { status: "failed", issueCount: 0, reason: error.message };
  }
  diagnosticCollection.clear();
  const diagnosticsByFile = new Map();
  const findings = [];
  let issueCount = 0;

  for (const filePath of files) {
    let content;
    try {
      content = await fs.readFile(filePath, "utf8");
    } catch (error) {
      output.appendLine(`Could not read ${filePath}: ${error.message}`);
      issueCount += 1;
      continue;
    }
    const lines = content.split(/\r?\n/);
    const diagnostics = [];
    lines.forEach((text, index) => {
      const line = index + 1;
      const todo = text.match(/\b(TODO|FIXME)\b/);
      if (todo) {
        const finding = { severity: "low", title: `${todo[1]} marker requires review`, file: path.relative(workspace.uri.fsPath, filePath), line };
        findings.push(finding);
        diagnostics.push(createDiagnostic(filePath, line, todo.index + 1, `QA Platform: ${finding.title}`, finding.severity));
      }

      const secret = text.match(/(password|secret|api[_-]?key|token)\s*[:=]\s*["'][^"']+["']/i);
      if (secret) {
        const finding = { severity: "high", title: "Possible hard-coded secret detected", file: path.relative(workspace.uri.fsPath, filePath), line };
        findings.push(finding);
        diagnostics.push(createDiagnostic(filePath, line, secret.index + 1, `QA Platform: ${finding.title}`, finding.severity));
      }
      const debug = !cliOutputScripts.has(path.basename(filePath))
        ? text.match(/^\s*(?:console\.log|print\s*\()/)
        : null;
      if (debug) {
        const finding = { severity: "medium", title: "Debug output is present in source", file: path.relative(workspace.uri.fsPath, filePath), line };
        findings.push(finding);
        diagnostics.push(createDiagnostic(filePath, line, debug.index + 1, `QA Platform: ${finding.title}`, finding.severity));
      }
    });
    if (diagnostics.length) {
      diagnosticsByFile.set(vscode.Uri.file(filePath), diagnostics);
      issueCount += diagnostics.length;
    }
  }
  for (const [uri, diagnostics] of diagnosticsByFile) diagnosticCollection.set(uri, diagnostics);
  output.appendLine(`Scanned ${files.length} source files; found ${issueCount} issue(s).`);
  vscode.window.showInformationMessage(`QA Platform scanned ${files.length} files and found ${issueCount} issue(s).`);
  return {
    status: issueCount ? "warning" : "passed",
    issueCount,
    filesScanned: files.length,
    findings
  };
}

async function runFullAudit(auditUrl) {
  const workspace = vscode.workspace.workspaceFolders?.[0];
  if (!workspace) {
    vscode.window.showErrorMessage("QA Platform requires an open workspace.");
    return { status: "failed", reason: "No workspace is open." };
  }
  const config = vscode.workspace.getConfiguration("qaPlatform");
  let projectConfig = {};
  const configPath = path.join(workspace.uri.fsPath, ".qa-platform.json");
  try {
    const raw = await fs.readFile(configPath, "utf8");
    projectConfig = JSON.parse(raw.replace(/^\uFEFF/, ""));
    if (!projectConfig || typeof projectConfig !== "object" || Array.isArray(projectConfig)) {
      throw new Error("configuration must be a JSON object");
    }
  } catch (error) {
    if (error.code !== "ENOENT") {
      vscode.window.showErrorMessage(`QA Platform configuration error: ${error.message}`);
      return { status: "failed", reason: error.message };
    }
  }
  const value = auditUrl || await vscode.window.showInputBox({ prompt: "Localhost URL to audit", value: projectConfig.baseUrl || config.get("localhostUrl", "http://localhost:5173") });
  if (!value) return { status: "cancelled" };
  let checkResult = { status: "failed", reason: "The browser audit did not complete." };
  await vscode.window.withProgress({ location: vscode.ProgressLocation.Notification, title: "QA Platform: running local browser audit" }, async () => {
    try {
      const result = await auditLocalhost(value, {
        workspace: workspace.uri.fsPath,
        maxPages: projectConfig.maxPages || config.get("auditMaxPages", 20),
        storageState: projectConfig.storageState || config.get("storageState", "") || undefined,
        importantPages: projectConfig.importantPages || config.get("importantPages", []),
        screenshots: projectConfig.screenshots ?? config.get("auditScreenshots", true),
        slowNavigationMs: projectConfig.slowNavigationMs,
        slowResourceMs: projectConfig.slowResourceMs,
        apiMaxDurationMs: projectConfig.apiMaxDurationMs,
        validateJson: projectConfig.validateJson
      });
      diagnosticCollection.clear();
      publishAuditDiagnostics(result, workspace.uri.fsPath);
      const output = vscode.window.createOutputChannel("QA Platform"); output.show(true); output.appendLine(JSON.stringify(result, null, 2));
      const highIssues = result.issues.filter((finding) => finding.severity === "high").length;
      checkResult = {
        status: highIssues ? "failed" : result.issues.length ? "warning" : "passed",
        issueCount: result.issues.length,
        highIssues,
        pagesScanned: result.pagesScanned,
        findings: result.issues
      };
      const message = `QA Platform audited ${result.pagesScanned} page(s) and found ${result.issues.length} issue(s).`;
      if (checkResult.status === "failed") vscode.window.showErrorMessage(message);
      else if (checkResult.status === "warning") vscode.window.showWarningMessage(message);
      else vscode.window.showInformationMessage(message);
    } catch (error) { vscode.window.showErrorMessage(`QA Platform audit failed: ${error.message}`); }
  });
  return checkResult;
}

async function runProjectTests() {
  const workspace = vscode.workspace.workspaceFolders?.[0];
  if (!workspace) return vscode.window.showErrorMessage("QA Platform requires an open workspace.");
  const root = workspace.uri.fsPath; let command; let args;
  try { const packageJson = JSON.parse(await fs.readFile(path.join(root, "package.json"), "utf8")); if (packageJson.scripts?.test) { command = process.platform === "win32" ? "npm.cmd" : "npm"; args = ["test"]; } } catch {}
  if (!command) { try { await fs.access(path.join(root, "pyproject.toml")); command = process.platform === "win32" ? "python.exe" : "python"; args = ["-m", "pytest", "--tb=short", "-q"]; } catch {} }
  if (!command) return vscode.window.showInformationMessage("No supported existing test command was detected.");
  const output = vscode.window.createOutputChannel("QA Platform Tests"); output.show(true);
  const result = await new Promise((resolve) => { const child = spawn(command, args, { cwd: root, shell: false, windowsHide: true }); let text = ""; child.stdout.on("data", (chunk) => { text += chunk; output.append(chunk.toString()); }); child.stderr.on("data", (chunk) => { text += chunk; output.append(chunk.toString()); }); child.on("close", (code) => resolve({ code, text })); child.on("error", (error) => resolve({ code: 1, text: error.message })); });
  const summary = (result.text.match(/(\d+)\s+(?:failed|passed|skipped)/i) || [])[0] || `exit code ${result.code}`;
  if (result.code) vscode.window.showErrorMessage(`QA tests failed (${summary}).`); else vscode.window.showInformationMessage(`QA tests passed (${summary}).`);
}
async function testLocalhost(testUrl) {
  const configured = vscode.workspace.getConfiguration("qaPlatform").get("localhostUrl", "http://localhost:5173");
  const value = testUrl || await vscode.window.showInputBox({
    prompt: "Localhost URL to test",
    value: configured,
    validateInput: (input) => {
      try {
        const url = new URL(input);
        return ["localhost", "127.0.0.1", "::1"].includes(url.hostname.toLowerCase())
          ? undefined
          : "Only localhost, 127.0.0.1, and ::1 are allowed.";
      } catch {
        return "Enter a valid http or https URL.";
      }

    }
  });
  if (!value) return { status: "cancelled" };
  const url = new URL(value);
  const output = vscode.window.createOutputChannel("QA Platform");
  output.show(true);
  try {
    const response = await fetch(url);
    output.appendLine(`${response.status} ${response.statusText} ${url.href}`);
    if (response.ok) {
      vscode.window.showInformationMessage(`Localhost is reachable (${response.status}).`);
      return { status: "passed", responseStatus: response.status };
    }
    vscode.window.showErrorMessage(`Localhost returned HTTP ${response.status}.`);
    return { status: "failed", responseStatus: response.status };
  } catch (error) {
    output.appendLine(`Localhost test failed: ${error.message}`);
    vscode.window.showErrorMessage(`Localhost is unreachable: ${error.message}`);
    return { status: "failed", reason: error.message };
  }

}

async function runQA() {
  const workspace = vscode.workspace.workspaceFolders?.[0];
  if (!workspace) return vscode.window.showErrorMessage("QA Platform requires an open workspace.");
  const config = vscode.workspace.getConfiguration("qaPlatform");
  const value = await vscode.window.showInputBox({
    prompt: "Localhost URL to check",
    value: config.get("localhostUrl", "http://localhost:5173"),
    validateInput: (input) => {
      try {
        const url = new URL(input);
        return ["localhost", "127.0.0.1", "::1"].includes(url.hostname.toLowerCase())
          ? undefined
          : "Only localhost, 127.0.0.1, and ::1 are allowed.";
      } catch {
        return "Enter a valid http or https URL.";
      }
    }
  });
  if (!value) return;
  const checks = [
    await analyzeWorkspace(),
    await testLocalhost(value),
    await runFullAudit(value)
  ];
  if (checks.some((check) => check?.status === "cancelled")) return;
  let report;
  try {
    report = await writeQAReport(workspace.uri.fsPath, value, checks);
  } catch (error) {
    vscode.window.showErrorMessage(`QA Platform could not generate the PDF report: ${error.message}`);
    return;
  }
  const output = vscode.window.createOutputChannel("QA Platform");
  output.clear();
  output.show(true);
  output.appendLine("QA PLATFORM REPORT");
  output.appendLine("=================");
  output.appendLine(`Target: ${value}`);
  output.appendLine(`Status: ${checks.some((check) => check?.status === "failed") ? "FAILED" : checks.some((check) => check?.status === "warning") ? "WARNINGS" : "PASSED"}`);
  output.appendLine("");
  checks.forEach((check, index) => output.appendLine(`${index + 1}. ${(check?.status || "failed").toUpperCase()} - ${check?.issueCount || 0} issue(s)`));
  const findings = checks.flatMap((check) => check?.findings || []);
  output.appendLine("");
  output.appendLine(findings.length ? "DETECTED ISSUES" : "DETECTED ISSUES: none");
  findings.forEach((finding, index) => {
    const location = finding.file ? ` (${finding.file}${finding.line ? `:${finding.line}` : ""})` : ` (${finding.url || "browser audit"})`;
    output.appendLine(`${index + 1}. [${String(finding.severity || "medium").toUpperCase()}] ${finding.title || finding.message}${location}`);
  });
  output.appendLine("");
  output.appendLine(`PDF report: ${report}`);
  const openReport = await vscode.window.showInformationMessage("QA Platform report generated.", "Open PDF");
  if (openReport === "Open PDF") await vscode.commands.executeCommand("vscode.open", vscode.Uri.file(report));
  const failed = checks.filter((check) => check?.status === "failed").length;
  const warnings = checks.filter((check) => check?.status === "warning").length;
  if (failed) {
    vscode.window.showErrorMessage(`QA Platform finished with ${failed} failed check(s)${warnings ? ` and ${warnings} warning check(s)` : ""}.`);
  } else if (warnings) {
    vscode.window.showWarningMessage(`QA Platform finished with ${warnings} warning check(s).`);
  } else {
    vscode.window.showInformationMessage("QA Platform finished successfully. All checks passed.");
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[character]));
  }

  async function writeQAReport(workspacePath, target, checks) {
    const reportDirectory = path.join(workspacePath, ".qa-platform", "reports");
    await fs.mkdir(reportDirectory, { recursive: true });
    const reportPath = path.join(reportDirectory, `qa-report-${new Date().toISOString().replace(/[:.]/g, "-")}.pdf`);
    const findings = checks.flatMap((check) => check?.findings || []);
    const status = checks.some((check) => check?.status === "failed") ? "FAILED" : checks.some((check) => check?.status === "warning") ? "WARNINGS" : "PASSED";
    const rows = findings.length
      ? findings.map((finding) => `<tr><td>${escapeHtml(String(finding.severity || "medium").toUpperCase())}</td><td>${escapeHtml(finding.title || finding.message)}</td><td>${escapeHtml(finding.file || finding.url || "Browser audit")}${finding.line ? `:${finding.line}` : ""}</td></tr>`).join("")
      : "<tr><td colspan=\"3\">No issues detected.</td></tr>";
    const html = `<!doctype html><html><head><meta charset="utf-8"><style>
      body { font-family: Arial, sans-serif; color: #222; padding: 32px; }
      h1 { margin-bottom: 4px; } .status { font-size: 18px; font-weight: bold; }
      table { border-collapse: collapse; width: 100%; margin-top: 24px; }
      th, td { border: 1px solid #ccc; padding: 8px; text-align: left; }
      th { background: #f0f0f0; } .meta { color: #555; }
    </style></head><body><h1>QA Platform Report</h1>
    <p class="status">Status: ${escapeHtml(status)}</p><p class="meta">Target: ${escapeHtml(target)}<br>Generated: ${escapeHtml(new Date().toISOString())}</p>
    <h2>Checks</h2><ul>${checks.map((check) => `<li>${escapeHtml(String(check?.status || "failed").toUpperCase())}: ${escapeHtml(`${check?.issueCount || 0} issue(s)`)}</li>`).join("")}</ul>
    <h2>Detected Issues</h2><table><thead><tr><th>Severity</th><th>Issue</th><th>Location</th></tr></thead><tbody>${rows}</tbody></table>
    </body></html>`;
    const browser = await chromium.launch({ headless: true });
    try {
      const page = await browser.newPage();
      await page.setContent(html);
      await page.pdf({ path: reportPath, format: "A4", printBackground: true });
    } finally {
      await browser.close();
    }
    return reportPath;
  }
}

async function runLoadTest() {
  const workspace = vscode.workspace.workspaceFolders?.[0];
  if (!workspace) return vscode.window.showErrorMessage("QA Platform requires an open workspace.");
  const config = vscode.workspace.getConfiguration("qaPlatform");
  const url = await vscode.window.showInputBox({ prompt: "Localhost URL for load testing", value: config.get("localhostUrl", "http://localhost:5173") });
  if (!url) return;
  const usersText = await vscode.window.showInputBox({ prompt: "Virtual users (maximum 250)", value: String(config.get("loadTestUsers", 10)), validateInput: (value) => /^\d+$/.test(value) && Number(value) <= 250 && Number(value) > 0 ? undefined : "Enter a number from 1 to 250." });
  if (!usersText) return;
  const users = Number(usersText);
  const confirm = users <= 50 || await vscode.window.showWarningMessage(`Run ${users} virtual users against ${url}? Only run this against a system you own.`, { modal: true }, "Run load test") === "Run load test";
  if (!confirm) return;
  const output = vscode.window.createOutputChannel("QA Platform Load Test"); output.show(true);
  await vscode.window.withProgress({ location: vscode.ProgressLocation.Notification, title: "QA Platform: running load test" }, async () => {
    try {
      const result = await loadTestLocalhost(url, {
        users, confirm, durationSeconds: config.get("loadTestDurationSeconds", 30),
        rampUpSeconds: config.get("loadTestRampUpSeconds", 10),
        paths: config.get("loadTestPaths", ["/"]),
        maxErrorRate: config.get("loadTestMaxErrorRate", 2),
        maxP95Ms: config.get("loadTestMaxP95Ms", 1000)
      });
      output.appendLine(JSON.stringify(result, null, 2));
      vscode.window.showInformationMessage(`Load test: ${result.status}; ${result.requests} requests, p95 ${result.p95ResponseMs}ms, ${result.errorRate}% errors.`);
    } catch (error) { vscode.window.showErrorMessage(`QA load test failed: ${error.message}`); }
  });
}

function activate(context) {
  context.subscriptions.push(
    diagnosticCollection,
    vscode.commands.registerCommand("qaPlatform.runQA", () => runQA()),
    vscode.commands.registerCommand("qaPlatform.analyzeWorkspace", () => analyzeWorkspace()),
    vscode.commands.registerCommand("qaPlatform.testLocalhost", () => testLocalhost()),
    vscode.commands.registerCommand("qaPlatform.runFullAudit", () => runFullAudit()),
    vscode.commands.registerCommand("qaPlatform.runTests", () => runProjectTests()),
    vscode.commands.registerCommand("qaPlatform.runLoadTest", () => runLoadTest()),
    vscode.commands.registerCommand("qaPlatform.clearDiagnostics", () => diagnosticCollection.clear())
  );
}

function deactivate() {
  diagnosticCollection.clear();
  diagnosticCollection.dispose();
}

module.exports = { activate, deactivate };
