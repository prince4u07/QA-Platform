# QA Platform Local Plugin

This package is the local bridge for the QA Platform website. It does not
contain an AI assistant. It exposes deterministic local analysis and localhost
testing tools to the user's existing AI assistant through an MCP-compatible
stdio server and a CLI.

The plugin never uses the QA Platform Gemini key, OpenAI key, or any other AI
provider key. It has no AI SDK dependency and does not send prompts to an AI
service. The external AI assistant controls the plugin by calling its tools.

## Development

```powershell
cd plugin
npm run check
node src/cli.js analyze ..
node src/cli.js test-localhost http://localhost:5173 --workspace ..
node src/cli.js audit-localhost http://localhost:5173 --workspace ..
node src/cli.js run-tests ..
```

The scanner intentionally excludes dependency folders, VCS metadata, virtual
environments, uploads, and common secret files. Findings contain a relative
file path, line, column, severity, rule ID, and confidence.

Create `.qa-platform.json` in the workspace root to configure `baseUrl`,
`maxPages`, `importantPages`, `ignoreRules`, `ignoreUrls`, `storageState`,
screenshots, and performance thresholds. The repository template is
`.qa-platform.example.json`.

## Optional website synchronization (not AI)

Analysis remains local unless the user explicitly uploads a reviewed result:

```powershell
$env:QA_PLATFORM_API_URL = "https://your-qa-platform.example"
$env:QA_PLATFORM_TOKEN = "your-website-access-token"
node src/cli.js upload-results .qa-platform-analysis.json
```

The upload contract is `POST /api/plugin/analysis` with a bearer token. The
token is only for authenticating to the QA Platform website; it is not an AI
API key. The website can reject the request or store it according to its own
permissions. The plugin never uploads source files automatically.

## MCP configuration

Point an MCP-compatible client at:

```text
node <repository>\plugin\src\cli.js mcp
```

The initial tools are:

- `qa_analyze_project`
- `qa_test_localhost`
- `qa_audit_localhost`
- `qa_run_tests`
- `qa_load_test_localhost`

`audit-localhost` runs a real Playwright crawl with screenshots, console and
network capture, broken-link discovery, form-label checks, missing-alt checks,
SEO checks, security-header checks, performance timing, and a mobile viewport
pass. Install browser binaries once with `npx playwright install chromium`.

`run-tests` detects and runs an existing `npm test`, `pytest`, or `cargo test`
command without shell expansion or dependency installation. It returns parsed
pass/fail/skip counts and a bounded failure log.

The opt-in `load-test` command and `qa_load_test_localhost` MCP tool simulate
up to 250 HTTP virtual users and report throughput, p50/p95/p99 latency, and
error rate. Tests above 50 users require confirmation. They are restricted to
localhost by default and should only be used against systems you own.

The server uses newline-delimited JSON-RPC over stdio and does not upload
source code. Website synchronization can be added as an explicit opt-in
transport after the local results are reviewed.
