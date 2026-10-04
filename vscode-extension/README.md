# QA Platform Local QA for VS Code

QA Platform Local QA is a VS Code extension that provides local project
analysis and localhost checks. It does not include an AI assistant. Users can
connect it to their existing AI workflow and use the diagnostics shown in the
VS Code Problems panel.

The extension does not use the QA Platform Gemini key, OpenAI key, or any
other AI provider key. It performs deterministic local checks only. The user's
existing AI assistant is responsible for calling tools and making code changes.

## Project configuration

Create `.qa-platform.json` in the workspace root to configure audits without
re-entering settings:

```json
{
  "baseUrl": "http://localhost:5173",
  "maxPages": 20,
  "screenshots": true,
  "importantPages": ["/login", "/privacy", "/terms"],
  "ignoreRules": [],
  "ignoreUrls": [],
  "storageState": ""
}
```

Copy the repository's `.qa-platform.example.json` as a starting point. The
configuration is read locally and is never uploaded.

## Commands

- **QA Platform: Run QA** runs the workspace scan, localhost connectivity check,
  and full browser audit using one command. It prints a readable report in the
  `QA Platform` Output channel and generates a PDF under
  `.qa-platform/reports/`.
- **QA Platform: Analyze Workspace** scans supported source files and shows
  findings at the exact file and line.
- **QA Platform: Test Localhost** checks an approved `localhost`,
  `127.0.0.1`, or `::1` URL.
- **QA Platform: Run Full Local Audit** crawls localhost with Playwright,
  captures screenshots, browser console errors, failed requests, broken links,
  missing image alt text, missing language/title/H1 metadata, and performance
  timings.
- **QA Platform: Run Local Load Test (up to 250 users)** runs an explicit,
  gradually ramped HTTP benchmark against localhost and reports throughput,
  p50/p95/p99 latency, and error rate.
- **QA Platform: Clear Diagnostics** removes QA Platform markers.

The extension excludes dependency folders, build output, virtual environments,
VCS metadata, and common secret files. Source code is not uploaded.

Load testing is opt-in and restricted to localhost. Tests above 50 virtual
users require confirmation, and the hard maximum is 250. This measures the
current local environment; it does not guarantee production capacity.

## Marketplace publishing

1. Create a Visual Studio Marketplace publisher account.
2. Change the `publisher` value in `package.json` to your Marketplace publisher
   ID.
3. Install the packaging tool: `npm install -g @vscode/vsce`.
4. From this directory run `vsce package`.
5. Test the generated `.vsix` in VS Code with **Extensions: Install from VSIX**.
6. Publish with `vsce publish` after creating a Marketplace personal access
   token.

The publisher ID must be yours; `qa-platform` is only a placeholder until you
register the publisher.

After installing the extension, install the Chromium browser used by Playwright
from the extension directory:

```powershell
npx playwright install chromium
```
