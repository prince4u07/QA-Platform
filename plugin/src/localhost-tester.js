import { randomUUID } from "node:crypto";

function validateUrl(value) {
  const url = new URL(value);
  if (!["http:", "https:"].includes(url.protocol)) {
    throw new Error("Localhost URL must use http or https");
  }
  const hostname = url.hostname.toLowerCase();
  if (!["localhost", "127.0.0.1", "::1"].includes(hostname)) {
    throw new Error("For safety, localhost testing only accepts localhost, 127.0.0.1, or ::1");
  }
  return url;
}

export async function testLocalhost(urlValue, { workspace } = {}) {
  const url = validateUrl(urlValue);
  const startedAt = Date.now();
  let response;
  try {
    response = await fetch(url, { redirect: "manual" });
  } catch (error) {
    return {
      schemaVersion: "1.0",
      analysisId: randomUUID(),
      status: "failed",
      workspace,
      url: url.href,
      issues: [{
        id: randomUUID(),
        severity: "high",
        category: "availability",
        ruleId: "browser.server-unreachable",
        title: "Local website is unreachable",
        message: error.message,
        file: null,
        line: null,
        confidence: "confirmed"
      }],
      durationMs: Date.now() - startedAt,
      generatedAt: new Date().toISOString()
    };
  }

  const body = await response.text();
  const issues = [];
  if (response.status >= 400) {
    issues.push({
      id: randomUUID(),
      severity: response.status >= 500 ? "high" : "medium",
      category: "availability",
      ruleId: "browser.http-error",
      title: `Local website returned HTTP ${response.status}`,
      message: `The page at ${url.href} returned HTTP ${response.status}.`,
      file: null,
      line: null,
      confidence: "confirmed"
    });
  }
  if (!body.trim()) {
    issues.push({
      id: randomUUID(),
      severity: "medium",
      category: "functional",
      ruleId: "browser.empty-response",
      title: "Local website returned an empty response",
      message: "The response body is empty.",
      file: null,
      line: null,
      confidence: "confirmed"
    });
  }
  return {
    schemaVersion: "1.0",
    analysisId: randomUUID(),
    status: "completed",
    workspace,
    url: url.href,
    httpStatus: response.status,
    contentType: response.headers.get("content-type"),
    issues,
    durationMs: Date.now() - startedAt,
    generatedAt: new Date().toISOString()
  };
}
