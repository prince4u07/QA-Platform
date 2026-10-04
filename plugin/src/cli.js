#!/usr/bin/env node

import { analyzeProject } from "./project-scanner.js";
import { testLocalhost } from "./localhost-tester.js";
import { auditLocalhost } from "./browser-audit.js";
import { runProjectTests } from "./test-runner.js";
import { applyConfig, loadConfig } from "./config.js";
import { startMcpServer } from "./mcp-server.js";
import { uploadAnalysis } from "./website-client.js";
import { loadTestLocalhost, MAX_VIRTUAL_USERS } from "./load-tester.js";
import { writeFile } from "node:fs/promises";
import { readFile } from "node:fs/promises";
import path from "node:path";

function usage() {
  console.error(`QA Platform local plugin

Commands:
  qa-platform analyze [workspace] [--json <file>]
  qa-platform test-localhost <url> [--workspace <path>] [--json <file>]
  qa-platform audit-localhost <url> [--workspace <path>] [--max-pages <n>] [--storage-state <file>] [--important-page <path>] [--json <file>]
  qa-platform upload-results <file>
  qa-platform run-tests [workspace] [--timeout <ms>]
  qa-platform load-test <url> [--workspace <path>] [--users <n>] [--duration <seconds>] [--ramp-up <seconds>] [--path <path>] [--confirm] [--allow-network] [--json <file>]
  qa-platform mcp

The plugin performs local analysis and does not contain an AI assistant.
`);
}

function optionValue(args, name) {
  const index = args.indexOf(name);
  return index === -1 ? undefined : args[index + 1];
}

async function writeResult(result, outputPath) {
  const json = JSON.stringify(result, null, 2);
  if (outputPath) {
    await writeFile(path.resolve(outputPath), `${json}\n`, "utf8");
  }
  console.log(json);
}

const [command, ...args] = process.argv.slice(2);

try {
  if (command === "analyze") {
    const workspace = args.find((arg) => !arg.startsWith("--")) || process.cwd();
    await writeResult(await analyzeProject(workspace), optionValue(args, "--json"));
  } else if (command === "test-localhost") {
    const url = args.find((arg) => !arg.startsWith("--"));
    if (!url) {
      usage();
      process.exitCode = 2;
    } else {
      const workspace = optionValue(args, "--workspace") || process.cwd();
      await writeResult(
        await testLocalhost(url, { workspace }),
        optionValue(args, "--json")
      );
    }
  } else if (command === "audit-localhost") {
    const url = args.find((arg) => !arg.startsWith("--"));
    if (!url) {
      usage();
      process.exitCode = 2;
    } else {
      const workspace = optionValue(args, "--workspace") || process.cwd();
      const config = await loadConfig(workspace);
      const importantPages = args.flatMap((arg, index) => arg === "--important-page" ? [args[index + 1]] : []).filter(Boolean);
      const result = await auditLocalhost(url, {
        workspace,
        ...config,
        maxPages: Number(optionValue(args, "--max-pages") || config.maxPages),
        importantPages: importantPages.length ? importantPages : config.importantPages,
        storageState: optionValue(args, "--storage-state") || config.storageState,
        screenshots: args.includes("--no-screenshots") ? false : config.screenshots
      });
      await writeResult(applyConfig(result, config), optionValue(args, "--json"));
    }
  } else if (command === "mcp") {
    await startMcpServer();
  } else if (command === "load-test") {
    const url = args.find((arg) => !arg.startsWith("--"));
    if (!url) {
      usage();
      process.exitCode = 2;
    } else {
      const workspace = optionValue(args, "--workspace") || process.cwd();
      const config = await loadConfig(workspace);
      const paths = args.flatMap((arg, index) => arg === "--path" ? [args[index + 1]] : []).filter(Boolean);
      const settings = {
        ...config.loadTest,
        users: Number(optionValue(args, "--users") || config.loadTest.users),
        durationSeconds: Number(optionValue(args, "--duration") || config.loadTest.durationSeconds),
        rampUpSeconds: Number(optionValue(args, "--ramp-up") || config.loadTest.rampUpSeconds),
        paths: paths.length ? paths : config.loadTest.paths,
        confirm: args.includes("--confirm"),
        allowNetwork: args.includes("--allow-network")
      };
      if (settings.users > MAX_VIRTUAL_USERS) throw new Error(`The plugin supports at most ${MAX_VIRTUAL_USERS} virtual users`);
      await writeResult(await loadTestLocalhost(url, settings), optionValue(args, "--json"));
    }
  } else if (command === "upload-results") {
    const inputPath = args.find((arg) => !arg.startsWith("--"));
    if (!inputPath) {
      usage();
      process.exitCode = 2;
    } else {
      const result = JSON.parse(await readFile(path.resolve(inputPath), "utf8"));
      console.log(JSON.stringify(await uploadAnalysis(result), null, 2));
    }
  } else if (command === "run-tests") {
    const workspace = args.find((arg) => !arg.startsWith("--")) || process.cwd();
    await writeResult(await runProjectTests(path.resolve(workspace), { timeout: Number(optionValue(args, "--timeout") || 120000) }));
  } else {
    usage();
    process.exitCode = command ? 2 : 0;
  }
} catch (error) {
  console.error(`QA Platform plugin failed: ${error.message}`);
  process.exitCode = 1;
}
