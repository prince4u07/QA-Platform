const MAX_VIRTUAL_USERS = 250;
const DEFAULTS = {
  users: 10,
  durationSeconds: 30,
  rampUpSeconds: 10,
  paths: ["/"],
  maxErrorRate: 2,
  maxP95Ms: 1000
};

function percentile(values, percentile) {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.ceil(sorted.length * percentile) - 1)];
}

function validateTarget(target, allowNetwork) {
  const url = new URL(target);
  if (!["http:", "https:"].includes(url.protocol)) throw new Error("Load tests require an HTTP(S) URL");
  const hostname = url.hostname.toLowerCase();
  const local = hostname === "localhost" || hostname === "127.0.0.1" || hostname === "::1";
  if (!local && !allowNetwork) throw new Error("Load tests are restricted to localhost; pass --allow-network only with permission");
  return url;
}

export async function loadTestLocalhost(target, options = {}) {
  const config = { ...DEFAULTS, ...options };
  const users = Number(config.users);
  const durationSeconds = Number(config.durationSeconds);
  const rampUpSeconds = Number(config.rampUpSeconds);
  if (!Number.isInteger(users) || users < 1 || users > MAX_VIRTUAL_USERS) {
    throw new Error(`users must be an integer between 1 and ${MAX_VIRTUAL_USERS}`);
  }
  if (!Number.isFinite(durationSeconds) || durationSeconds <= 0 || durationSeconds > 3600) {
    throw new Error("durationSeconds must be between 1 and 3600");
  }
  if (!Number.isFinite(rampUpSeconds) || rampUpSeconds < 0 || rampUpSeconds > durationSeconds) {
    throw new Error("rampUpSeconds must be between 0 and durationSeconds");
  }
  if (users > 50 && !config.confirm) {
    throw new Error("Load tests above 50 users require confirm: true or --confirm");
  }
  const baseUrl = validateTarget(target, config.allowNetwork);
  const paths = Array.isArray(config.paths) && config.paths.length ? config.paths : ["/"];
  const startedAt = Date.now();
  const endAt = startedAt + durationSeconds * 1000;
  const samples = [];
  let failed = 0;
  let completed = 0;
  let stopped = false;

  async function virtualUser(userIndex) {
    const delay = rampUpSeconds ? (rampUpSeconds * 1000 * userIndex) / users : 0;
    if (delay) await new Promise((resolve) => setTimeout(resolve, delay));
    let requestIndex = userIndex;
    while (!stopped && Date.now() < endAt) {
      const pathValue = paths[requestIndex % paths.length];
      requestIndex += 1;
      const url = new URL(pathValue, baseUrl);
      const requestStarted = Date.now();
      try {
        const response = await fetch(url, { redirect: "manual" });
        const durationMs = Date.now() - requestStarted;
        samples.push(durationMs);
        completed += 1;
        if (response.status >= 400) failed += 1;
      } catch {
        completed += 1;
        failed += 1;
      }
    }
  }

  await Promise.all(Array.from({ length: users }, (_, index) => virtualUser(index)));
  stopped = true;
  const elapsedMs = Math.max(1, Date.now() - startedAt);
  const errorRate = completed ? (failed / completed) * 100 : 100;
  const p95Ms = percentile(samples, 0.95);
  const result = {
    schemaVersion: "1.0",
    target: baseUrl.toString(),
    virtualUsers: users,
    durationSeconds,
    rampUpSeconds,
    requests: completed,
    successfulRequests: completed - failed,
    failedRequests: failed,
    errorRate: Number(errorRate.toFixed(2)),
    averageResponseMs: samples.length ? Math.round(samples.reduce((sum, value) => sum + value, 0) / samples.length) : 0,
    p50ResponseMs: percentile(samples, 0.5),
    p95ResponseMs: p95Ms,
    p99ResponseMs: percentile(samples, 0.99),
    requestsPerSecond: Number((completed / (elapsedMs / 1000)).toFixed(2)),
    status: errorRate > config.maxErrorRate || p95Ms > config.maxP95Ms ? "warning" : "passed",
    thresholds: { maxErrorRate: config.maxErrorRate, maxP95Ms: config.maxP95Ms },
    issues: []
  };
  if (errorRate > config.maxErrorRate) result.issues.push({ ruleId: "load.high-error-rate", severity: "high", message: `Error rate ${result.errorRate}% exceeded ${config.maxErrorRate}%` });
  if (p95Ms > config.maxP95Ms) result.issues.push({ ruleId: "load.slow-p95", severity: "medium", message: `p95 response time ${p95Ms}ms exceeded ${config.maxP95Ms}ms` });
  return result;
}

export { MAX_VIRTUAL_USERS };
