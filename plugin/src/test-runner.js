import fs from "node:fs/promises";
import path from "node:path";
import { spawn } from "node:child_process";

const candidates = [
  ["package.json", (json) => json.scripts?.test && ["npm", "test"]],
  ["pyproject.toml", () => ["python", "-m", "pytest", "--tb=short", "-q"]],
  ["pytest.ini", () => ["python", "-m", "pytest", "--tb=short", "-q"]],
  ["Cargo.toml", () => ["cargo", "test"]]
];

function parseOutput(text, exitCode) {
  const passed = Number((text.match(/(\d+)\s+pass(?:ed|es)/i) || [])[1] || 0);
  const failed = Number((text.match(/(\d+)\s+fail(?:ed|ures?)/i) || [])[1] || 0);
  const skipped = Number((text.match(/(\d+)\s+skip(?:ped|s)/i) || [])[1] || 0);
  const failures = [...text.matchAll(/(?:FAIL|FAILED|ERROR)[: ]+([^\r\n]+)/gi)].slice(0, 20).map((match) => match[1].trim());
  return { passed, failed, skipped, failures, status: exitCode === 0 ? "passed" : "failed" };
}

async function exists(file) {
  try { await fs.access(file); return true; } catch { return false; }
}

export async function detectTestCommand(workspace) {
  for (const [file, command] of candidates) {
    const fullPath = path.join(workspace, file);
    if (!await exists(fullPath)) continue;
    if (file === "package.json") {
      const contents = await fs.readFile(fullPath, "utf8");
      const json = JSON.parse(contents.replace(/^\uFEFF/, ""));
      if (json.scripts?.test) return { command: process.platform === "win32" ? "npm.cmd" : "npm", args: ["test"], source: file };
    } else return { command: command()[0], args: command().slice(1), source: file };
  }
  return null;
}

export async function runProjectTests(workspace, { timeout = 120000, command, args = [] } = {}) {
  const detected = command ? { command, args, source: "explicit" } : await detectTestCommand(workspace);
  if (!detected) return { status: "not-found", message: "No supported existing test command was detected.", workspace };
  const result = await new Promise((resolve) => {
    const child = spawn(detected.command, detected.args, { cwd: workspace, shell: false, windowsHide: true });
    let output = "";
    const timer = setTimeout(() => { child.kill(); resolve({ status: "timed-out", exitCode: null, output }); }, timeout);
    child.stdout.on("data", (chunk) => { output += chunk; });
    child.stderr.on("data", (chunk) => { output += chunk; });
    child.on("close", (exitCode) => { clearTimeout(timer); resolve({ exitCode, output, ...parseOutput(output, exitCode) }); });
    child.on("error", (error) => { clearTimeout(timer); resolve({ status: "error", message: error.message, output }); });
  });
  return { workspace, command: [detected.command, ...detected.args], source: detected.source, ...result, output: result.output?.slice(-20000) };
}
