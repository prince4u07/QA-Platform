import { readdir, readFile, stat } from "node:fs/promises";
import path from "node:path";
import { randomUUID } from "node:crypto";

const IGNORED_NAMES = new Set([
  ".git", ".hg", ".svn", "node_modules", "dist", "build", "coverage",
  ".next", ".nuxt", ".venv", "venv", "__pycache__", ".pytest_cache",
  "static/uploads"
]);
const SECRET_NAMES = new Set([
  ".env", ".env.local", ".env.production", ".env.development",
  "credentials.json", "service-account.json"
]);
const SOURCE_EXTENSIONS = new Set([
  ".js", ".jsx", ".ts", ".tsx", ".py", ".java", ".go", ".rb", ".php", ".cs"
]);

function isIgnored(relativePath, name) {
  const normalized = relativePath.split(path.sep).join("/");
  return SECRET_NAMES.has(name) || [...IGNORED_NAMES].some(
    (entry) => name === entry || normalized === entry || normalized.startsWith(`${entry}/`)
  );
}

async function walk(root, current = root, files = []) {
  const entries = await readdir(current, { withFileTypes: true });
  for (const entry of entries) {
    const fullPath = path.join(current, entry.name);
    const relativePath = path.relative(root, fullPath);
    if (isIgnored(relativePath, entry.name)) continue;
    if (entry.isDirectory()) {
      await walk(root, fullPath, files);
    } else if (entry.isFile() && SOURCE_EXTENSIONS.has(path.extname(entry.name))) {
      files.push({ fullPath, relativePath });
    }
  }
  return files;
}

function finding(file, line, column, ruleId, message, severity = "low") {
  return {
    id: randomUUID(),
    severity,
    category: "source",
    ruleId,
    title: message,
    message,
    file: file.relativePath.split(path.sep).join("/"),
    line,
    column,
    confidence: "confirmed",
    source: "qa-platform-local-scanner"
  };
}

function scanFile(file, content) {
  const findings = [];
  const lines = content.split(/\r?\n/);
  lines.forEach((lineText, index) => {
    const line = index + 1;
    const todo = lineText.match(/\b(TODO|FIXME)\b/);
    if (todo) {
      findings.push(finding(
        file, line, todo.index + 1, "maintainability.todo",
        `${todo[1]} marker requires review`
      ));
    }
    if (/\b(console\.log|print\s*\()\s*/.test(lineText)) {
      findings.push(finding(
        file, line, lineText.search(/\b(console\.log|print\s*\()/) + 1,
        "quality.debug-output", "Debug output is present in source", "medium"
      ));
    }
    if (/(password|secret|api[_-]?key|token)\s*[:=]\s*["'][^"']+["']/i.test(lineText)) {
      findings.push(finding(
        file, line, 1, "security.possible-secret",
        "Possible hard-coded secret detected; verify and move it to secure configuration",
        "high"
      ));
    }
  });
  return findings;
}

export async function analyzeProject(workspace) {
  const root = path.resolve(workspace);
  const rootStats = await stat(root);
  if (!rootStats.isDirectory()) throw new Error(`Workspace is not a directory: ${root}`);
  const files = await walk(root);
  const issues = [];
  for (const file of files) {
    const content = await readFile(file.fullPath, "utf8");
    issues.push(...scanFile(file, content));
  }
  return {
    schemaVersion: "1.0",
    analysisId: randomUUID(),
    status: "completed",
    workspace: root,
    filesScanned: files.length,
    issues,
    generatedAt: new Date().toISOString()
  };
}
