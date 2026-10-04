import { chromium } from "playwright";
import AxeBuilder from "@axe-core/playwright";
import { randomUUID } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";

const MOBILE = { width: 390, height: 844 };
const SECURITY_HEADERS = ["content-security-policy", "x-content-type-options", "x-frame-options", "referrer-policy", "permissions-policy"];
const SENSITIVE = /password|passwd|token|secret|card|cvv|payment/i;

function issue(category, ruleId, title, message, severity = "medium", extra = {}) {
  return { id: randomUUID(), category, ruleId, title, message, severity, confidence: "confirmed", ...extra };
}

function localUrl(value) {
  const url = new URL(value);
  if (!["localhost", "127.0.0.1", "::1"].includes(url.hostname.toLowerCase())) {
    throw new Error("Browser audit only accepts localhost, 127.0.0.1, or ::1");
  }
  return url;
}

function selectorFor(element) {
  if (element.id) return `#${CSS.escape(element.id)}`;
  const parts = [];
  for (let current = element; current && current.nodeType === 1 && parts.length < 5; current = current.parentElement) {
    let part = current.tagName.toLowerCase();
    if (current.classList.length) part += `.${[...current.classList].slice(0, 2).map(CSS.escape).join(".")}`;
    const siblings = current.parentElement ? [...current.parentElement.children].filter((child) => child.tagName === current.tagName) : [];
    if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(current) + 1})`;
    parts.unshift(part);
  }
  return parts.join(" > ");
}

async function boundingBox(page, selector) {
  if (!selector) return undefined;
  try {
    const locator = page.locator(selector).first();
    const box = await locator.boundingBox();
    return box ? { x: box.x, y: box.y, width: box.width, height: box.height } : undefined;
  } catch {
    return undefined;
  }
}

async function inspectPage(page, pageUrl, options) {
  const issues = [];
  const consoleErrors = [];
  const failedRequests = [];
  const apiResponses = [];
  const responseTasks = [];
  const startedAt = Date.now();
  let response;
  const onConsole = (message) => { if (message.type() === "error") consoleErrors.push(message.text()); };
  const onRequestFailed = (request) => failedRequests.push(`${request.method()} ${request.url()} - ${request.failure()?.errorText || "failed"}`);
  const onResponse = (item) => {
    const task = (async () => {
    const request = item.request();
    if (!["xhr", "fetch"].includes(request.resourceType())) return;
    const started = request.timing().startTime;
    const durationMs = started >= 0 ? Math.max(0, Date.now() - startedAt - started) : undefined;
    const entry = { url: item.url(), method: request.method(), status: item.status(), contentType: item.headers()["content-type"] || "", durationMs };
    apiResponses.push(entry);
    if (item.status() >= 400) issues.push(issue("api", "api.http-error", "API request returned an error", `${request.method()} ${item.url()} returned HTTP ${item.status()}.`, item.status() >= 500 ? "high" : "medium", { url: pageUrl, response: entry }));
    if (options.apiMaxDurationMs && durationMs > options.apiMaxDurationMs) issues.push(issue("api", "api.slow-response", "Slow API response", `${item.url()} took ${durationMs}ms.`, "medium", { url: pageUrl, response: entry }));
    if (options.validateJson && (entry.contentType.includes("json") || item.url().includes("/api/"))) {
      try { await item.json(); } catch { issues.push(issue("api", "api.invalid-json", "Invalid API JSON", `${item.url()} declared JSON but could not be parsed.`, "medium", { url: pageUrl, response: entry })); }
    }
    })();
    responseTasks.push(task);
  };
  page.on("console", onConsole);
  page.on("requestfailed", onRequestFailed);
  page.on("response", onResponse);
  try {
    try {
      response = await page.goto(pageUrl, { waitUntil: "networkidle", timeout: options.timeout });
    } catch (error) {
      issues.push(issue("availability", "browser.navigation-failed", "Page navigation failed", error.message, "high", { url: pageUrl }));
      return { issues, links: [], consoleErrors, failedRequests, apiResponses };
    }
    const data = await page.evaluate(() => {
      const selector = (element) => {
        if (element.id) return `#${CSS.escape(element.id)}`;
        const parts = [];
        for (let current = element; current && current.nodeType === 1 && parts.length < 5; current = current.parentElement) {
          let part = current.tagName.toLowerCase();
          if (current.classList.length) part += `.${[...current.classList].slice(0, 2).map(CSS.escape).join(".")}`;
          const siblings = current.parentElement ? [...current.parentElement.children].filter((child) => child.tagName === current.tagName) : [];
          if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(current) + 1})`;
          parts.unshift(part);
        }
        return parts.join(" > ");
      };
      const meta = (name, property) => document.querySelector(`meta[name="${name}"], meta[property="${property}"]`)?.content || "";
      const controls = [...document.querySelectorAll("button, input[type=submit], input[type=button], a")].map((element) => ({
        tag: element.tagName.toLowerCase(), text: (element.innerText || element.value || "").trim().slice(0, 120),
        href: element.getAttribute("href"), disabled: element.disabled || element.getAttribute("aria-disabled") === "true",
        selector: selector(element),
        sourceFile: element.getAttribute("data-source-file") || element.getAttribute("data-testid-file") || "",
        sourceLine: Number(element.getAttribute("data-source-line") || 0)
      }));
      const forms = [...document.forms].map((form) => ({
        action: form.action, method: form.method, selector: selector(form),
        controls: [...form.elements].filter((element) => element.type !== "hidden").map((element) => ({
          type: element.type, name: element.name, id: element.id, required: element.required,
          label: element.labels?.[0]?.innerText?.trim() || "", autocomplete: element.autocomplete,
          selector: selector(element), validationMessage: element.validationMessage,
          sourceFile: element.getAttribute("data-source-file") || "", sourceLine: Number(element.getAttribute("data-source-line") || 0)
        }))
      }));
      return {
        title: document.title, htmlLang: document.documentElement.lang, headings: [...document.querySelectorAll("h1,h2,h3,h4,h5,h6")].map((h) => ({ level: Number(h.tagName.slice(1)), text: h.innerText.trim() })),
        images: [...document.images].map((image) => ({ alt: image.getAttribute("alt"), src: image.currentSrc || image.src, selector: selector(image), sourceFile: image.getAttribute("data-source-file") || "", sourceLine: Number(image.getAttribute("data-source-line") || 0) })),
        links: [...document.querySelectorAll("a[href]")].map((link) => ({ href: link.href, text: link.innerText.trim(), rel: link.rel })),
        forms, controls, viewportWidth: innerWidth, metaDescription: meta("description"), canonical: document.querySelector('link[rel="canonical"]')?.href || "",
        robots: meta("robots"), viewport: meta("viewport"), og: meta("", "og:title"), twitter: meta("twitter:card"),
        structuredData: [...document.querySelectorAll('script[type="application/ld+json"]')].map((script) => { try { JSON.parse(script.textContent); return true; } catch { return false; } }),
        navigation: performance.getEntriesByType("navigation")[0] ? { domContentLoaded: performance.getEntriesByType("navigation")[0].domContentLoadedEventEnd, load: performance.getEntriesByType("navigation")[0].loadEventEnd } : null,
        resources: performance.getEntriesByType("resource").map((entry) => ({ name: entry.name, duration: entry.duration, size: entry.transferSize || 0 })).slice(0, 200)
      };
    });
    const durationMs = Date.now() - startedAt;
    await Promise.allSettled(responseTasks);
    const headers = Object.fromEntries(response?.headersArray().map(({ name, value }) => [name.toLowerCase(), value]) || []);
    const add = (rule, title, message, severity, extra = {}) => issues.push(issue(rule.startsWith("a11y") ? "accessibility" : rule.startsWith("seo") ? "seo" : rule.startsWith("performance") ? "performance" : "security", rule, title, message, severity, { url: pageUrl, ...extra }));
    if (!data.title.trim()) add("seo.missing-title", "Missing page title", "The document has no title.", "medium");
    if (!data.htmlLang) add("a11y.missing-lang", "Missing document language", "The html element has no lang attribute.", "medium");
    if (!data.headings.some((h) => h.level === 1)) add("seo.missing-h1", "Missing H1 heading", "The page has no H1 heading.", "low");
    if (data.headings.filter((h) => h.level === 1).length > 1) add("seo.multiple-h1", "Multiple H1 headings", "The page has more than one H1 heading.", "low");
    data.images.filter((image) => !image.alt?.trim()).forEach((image) => add("a11y.missing-alt", "Image missing alt text", image.src || "Image has no source", "medium", { selector: image.selector, sourceLocation: image.sourceFile && image.sourceLine ? { file: image.sourceFile, line: image.sourceLine } : undefined }));
    data.forms.forEach((form) => form.controls.forEach((control) => {
      if (!["hidden", "submit", "button"].includes(control.type) && !control.label && !control.name && !control.id) add("a11y.unlabeled-control", "Form control is not labeled", `Unlabeled ${control.type} control`, "medium", { selector: control.selector, sourceLocation: control.sourceFile && control.sourceLine ? { file: control.sourceFile, line: control.sourceLine } : undefined });
      if (control.required && SENSITIVE.test(`${control.name} ${control.id} ${control.autocomplete}`)) return;
      if (control.required && !control.label) add("form.required-unlabeled", "Required field lacks a label", `Required ${control.type} field has no visible label.`, "medium", { selector: control.selector, sourceLocation: control.sourceFile && control.sourceLine ? { file: control.sourceFile, line: control.sourceLine } : undefined });
    }));
    data.controls.filter((control) => control.disabled || (control.tag === "a" && !control.href) || (!control.text && control.tag !== "a")).forEach((control) => issues.push(issue("controls", "controls.dead-or-inactive", "Potentially inactive control", "A control is disabled, empty, or has no usable link target; dynamic behavior was not triggered.", "low", { url: pageUrl, selector: control.selector, confidence: "tentative" })));
    if (!data.metaDescription) add("seo.missing-description", "Missing meta description", "The page has no meta description.", "low");
    if (!data.canonical) add("seo.missing-canonical", "Missing canonical URL", "The page has no canonical link.", "low");
    if (!data.viewport) add("seo.missing-viewport", "Missing viewport metadata", "The page has no responsive viewport meta tag.", "low");
    if (data.structuredData.some((valid) => !valid)) add("seo.invalid-structured-data", "Invalid structured data", "At least one JSON-LD block is not valid JSON.", "medium");
    SECURITY_HEADERS.forEach((header) => { if (!headers[header]) issues.push(issue("security", `security.missing-${header}`, `Missing ${header} header`, `The response does not include ${header}.`, "low", { url: pageUrl })); });
    if (pageUrl.startsWith("https://")) {
      const mixed = data.resources.filter((resource) => resource.name.startsWith("http://"));
      mixed.forEach((resource) => issues.push(issue("security", "security.mixed-content", "Mixed content resource", `${resource.name} was loaded by an HTTPS page.`, "high", { url: pageUrl, resource })));
    }
    if (durationMs > options.slowNavigationMs) issues.push(issue("performance", "performance.slow-navigation", "Slow page navigation", `Navigation took ${durationMs}ms.`, "medium", { url: pageUrl, durationMs }));
    const slowResources = data.resources.filter((resource) => resource.duration > options.slowResourceMs);
    if (slowResources.length) issues.push(issue("performance", "performance.slow-resource", "Slow resource detected", `${slowResources.length} resource(s) exceeded ${options.slowResourceMs}ms.`, "low", { url: pageUrl, resources: slowResources.slice(0, 20) }));
    try {
      const axe = await new AxeBuilder({ page }).analyze();
      axe.violations.forEach((violation) => issues.push(issue("accessibility", `axe.${violation.id}`, violation.help, `${violation.description} (${violation.nodes.length} node(s)).`, violation.impact === "critical" || violation.impact === "serious" ? "high" : "medium", { url: pageUrl, helpUrl: violation.helpUrl, evidence: violation.nodes.slice(0, 10).map((node) => ({ target: node.target, html: node.html })) })));
    } catch (error) {
      issues.push(issue("accessibility", "axe.unavailable", "Accessibility scan unavailable", error.message, "low", { url: pageUrl, confidence: "tentative" }));
    }
    for (const finding of issues) {
      if (finding.selector) finding.boundingBox = await boundingBox(page, finding.selector);
    }
    for (const link of [...new Set(data.links.map((link) => link.href))].filter((link) => link.startsWith(new URL(pageUrl).origin)).slice(0, options.linkLimit)) {
      try { const linkResponse = await fetch(link, { redirect: "manual" }); if (linkResponse.status >= 400) issues.push(issue("links", "links.broken", "Broken internal link", `${link} returned HTTP ${linkResponse.status}.`, "medium", { url: pageUrl, target: link })); } catch (error) { issues.push(issue("links", "links.unreachable", "Unreachable internal link", `${link}: ${error.message}`, "medium", { url: pageUrl, target: link })); }
    }
    consoleErrors.forEach((text) => issues.push(issue("console", "browser.console-error", "Browser console error", text, "high", { url: pageUrl })));
    failedRequests.forEach((text) => issues.push(issue("network", "browser.request-failed", "Network request failed", text, "high", { url: pageUrl })));
    return { issues, links: [...new Set(data.links.map((link) => link.href))], consoleErrors, failedRequests, apiResponses, durationMs, responseStatus: response?.status(), headers, data, metrics: { navigation: data.navigation, resourceCount: data.resources.length, totalTransferSize: data.resources.reduce((sum, resource) => sum + resource.size, 0) } };
  } finally {
    page.off("console", onConsole); page.off("requestfailed", onRequestFailed); page.off("response", onResponse);
  }
}

export async function auditLocalhost(urlValue, {
  workspace = process.cwd(), maxPages = 20, timeout = 30000, screenshots = true, storageState,
  importantPages = [], slowNavigationMs = 3000, slowResourceMs = 2000, apiMaxDurationMs = 3000, validateJson = true
} = {}) {
  const startUrl = localUrl(urlValue).href;
  const browser = await chromium.launch({ headless: true });
  const contextOptions = {};
  if (storageState) {
    const resolved = path.resolve(workspace, storageState);
    if (!resolved.startsWith(path.resolve(workspace) + path.sep)) throw new Error("storageState must be inside the workspace.");
    const state = JSON.parse(await fs.readFile(resolved, "utf8"));
    if (!state || !Array.isArray(state.cookies) || !Array.isArray(state.origins)) throw new Error("Invalid Playwright storageState file.");
    contextOptions.storageState = resolved;
  }
  const context = await browser.newContext(contextOptions);
  const page = await context.newPage();
  const queue = [startUrl, ...importantPages.map((item) => new URL(item, startUrl).href)];
  const visited = new Set(); const issues = []; const pages = [];
  const evidenceDir = path.join(workspace, ".qa-platform", "evidence");
  if (screenshots) await fs.mkdir(evidenceDir, { recursive: true });
  const options = { timeout, linkLimit: 50, slowNavigationMs, slowResourceMs, apiMaxDurationMs, validateJson };
  try {
    while (queue.length && visited.size < maxPages) {
      const pageUrl = queue.shift(); if (visited.has(pageUrl)) continue; visited.add(pageUrl);
      const result = await inspectPage(page, pageUrl, options);
      if (screenshots) { const filename = `page-${visited.size}.png`; await page.screenshot({ path: path.join(evidenceDir, filename), fullPage: true }).catch(() => {}); result.screenshot = path.join(".qa-platform", "evidence", filename); }
      issues.push(...result.issues); pages.push({ url: pageUrl, pageRole: pageUrl === startUrl ? "start" : importantPages.some((item) => new URL(item, startUrl).href === pageUrl) ? "important" : "crawled", ...result });
      for (const link of result.links || []) if (!visited.has(link) && queue.length + visited.size < maxPages) queue.push(link);
    }
    const mobile = await browser.newPage({ viewport: MOBILE });
    const mobileResult = await inspectPage(mobile, startUrl, options);
    if (mobileResult.data?.viewportWidth !== MOBILE.width) issues.push(issue("mobile", "mobile.viewport-mismatch", "Mobile viewport was not applied", "The page did not render at the configured mobile width.", "medium", { url: startUrl }));
    issues.push(...mobileResult.issues.filter((entry) => entry.category !== "availability")); await mobile.close();
  } finally { await browser.close(); }
  return { schemaVersion: "1.1", analysisId: randomUUID(), status: "completed", url: startUrl, workspace: path.resolve(workspace), authenticated: Boolean(storageState), pagesScanned: pages.length, issues, pages, generatedAt: new Date().toISOString() };
}
