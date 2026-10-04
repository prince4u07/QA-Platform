import { createInterface } from "node:readline";
import { analyzeProject } from "./project-scanner.js";
import { testLocalhost } from "./localhost-tester.js";
import { auditLocalhost } from "./browser-audit.js";
import { runProjectTests } from "./test-runner.js";
import { applyConfig, loadConfig } from "./config.js";
import { loadTestLocalhost } from "./load-tester.js";

const tools = [
  {
    name: "qa_load_test_localhost",
    description: "Run an explicit, capped HTTP load test against localhost using up to 250 virtual users.",
    inputSchema: {
      type: "object",
      properties: {
        url: { type: "string" }, workspace: { type: "string" }, users: { type: "number", maximum: 250 },
        durationSeconds: { type: "number" }, rampUpSeconds: { type: "number" },
        paths: { type: "array", items: { type: "string" } }, confirm: { type: "boolean" },
        allowNetwork: { type: "boolean" }, maxErrorRate: { type: "number" }, maxP95Ms: { type: "number" }
      },
      required: ["url", "confirm"]
    }
  },
  {
    name: "qa_run_tests",
    description: "Safely detect and run an existing local test command without installing dependencies.",
    inputSchema: { type: "object", properties: { workspace: { type: "string" }, timeout: { type: "number" }, command: { type: "string" }, args: { type: "array", items: { type: "string" } } }, required: ["workspace"] }
  },
  {
    name: "qa_analyze_project",
    description: "Scan a local workspace for safe, line-addressable QA findings.",
    inputSchema: {
      type: "object",
      properties: { workspace: { type: "string" } },
      required: ["workspace"]
    }
  },
  {
    name: "qa_test_localhost",
    description: "Check an approved localhost URL and return structured browser readiness findings.",
    inputSchema: {
      type: "object",
      properties: {
        url: { type: "string" },
        workspace: { type: "string" }
      },
      required: ["url"]
    }
  },
  {
    name: "qa_audit_localhost",
    description: "Crawl localhost with a headless browser and run QA checks with screenshots and evidence.",
    inputSchema: {
      type: "object",
      properties: {
        url: { type: "string" },
        workspace: { type: "string" },
        maxPages: { type: "number" },
        importantPages: { type: "array", items: { type: "string" } },
        storageState: { type: "string", description: "Optional Playwright storage state path relative to workspace." },
        screenshots: { type: "boolean" },
        slowNavigationMs: { type: "number" },
        slowResourceMs: { type: "number" },
        apiMaxDurationMs: { type: "number" },
        validateJson: { type: "boolean" }
      },
      required: ["url"]
    }
  }
];

function result(value) {
  return { content: [{ type: "text", text: JSON.stringify(value, null, 2) }] };
}

async function handle(request) {
  if (request.method === "initialize") {
    return {
      protocolVersion: "2024-11-05",
      capabilities: { tools: {} },
      serverInfo: { name: "qa-platform-local-plugin", version: "0.1.0" }
    };
  }
  if (request.method === "notifications/initialized") return null;
  if (request.method === "tools/list") return { tools };
  if (request.method === "tools/call") {
    const args = request.params?.arguments || {};
    if (request.params?.name === "qa_analyze_project") return result(await analyzeProject(args.workspace));
    if (request.params?.name === "qa_test_localhost") {
      return result(await testLocalhost(args.url, { workspace: args.workspace }));
    }
    if (request.params?.name === "qa_audit_localhost") {
      const config = await loadConfig(args.workspace || process.cwd());
      const audit = await auditLocalhost(args.url, {
        workspace: args.workspace, ...config, ...args,
        maxPages: args.maxPages || config.maxPages,
        importantPages: args.importantPages || config.importantPages
      });
      return result(applyConfig(audit, config));
    }
    if (request.params?.name === "qa_run_tests") return result(await runProjectTests(args.workspace, { timeout: args.timeout, command: args.command, args: args.args }));
    if (request.params?.name === "qa_load_test_localhost") {
      const config = await loadConfig(args.workspace || process.cwd());
      return result(await loadTestLocalhost(args.url, { ...config.loadTest, ...args }));
    }
    throw new Error(`Unknown tool: ${request.params?.name}`);
  }
  throw new Error(`Unsupported MCP method: ${request.method}`);
}

export async function startMcpServer() {
  const input = createInterface({ input: process.stdin });
  for await (const line of input) {
    if (!line.trim()) continue;
    let request;
    try {
      request = JSON.parse(line);
      const response = await handle(request);
      if (response !== null) {
        process.stdout.write(`${JSON.stringify({ jsonrpc: "2.0", id: request.id, result: response })}\n`);
      }
    } catch (error) {
      process.stdout.write(`${JSON.stringify({
        jsonrpc: "2.0",
        id: request?.id ?? null,
        error: { code: -32000, message: error.message }
      })}\n`);
    }
  }
}
