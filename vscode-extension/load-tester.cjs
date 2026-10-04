const MAX_VIRTUAL_USERS = 250;

function percentile(values, percentile) {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.ceil(sorted.length * percentile) - 1)];
}

async function loadTestLocalhost(target, options = {}) {
  const users = Number(options.users ?? 10);
  const durationSeconds = Number(options.durationSeconds ?? 30);
  const rampUpSeconds = Number(options.rampUpSeconds ?? 10);
  const requestTimeoutMs = Number(options.requestTimeoutMs ?? 30000);
  if (!Number.isInteger(users) || users < 1 || users > MAX_VIRTUAL_USERS) throw new Error(`Users must be between 1 and ${MAX_VIRTUAL_USERS}.`);
  if (!Number.isFinite(durationSeconds) || durationSeconds <= 0) throw new Error("Duration must be greater than zero.");
  if (!Number.isFinite(rampUpSeconds) || rampUpSeconds < 0) throw new Error("Ramp-up time cannot be negative.");
  if (!Number.isFinite(requestTimeoutMs) || requestTimeoutMs <= 0) throw new Error("Request timeout must be greater than zero.");
  if (users > 50 && !options.confirm) throw new Error("Load tests above 50 users require confirmation.");
  const baseUrl = new URL(target);
  if (!["localhost", "127.0.0.1", "::1"].includes(baseUrl.hostname.toLowerCase())) throw new Error("VS Code load tests are restricted to localhost.");
  const paths = Array.isArray(options.paths) && options.paths.length ? options.paths : ["/"];
  const origin = baseUrl.origin;
  const requestUrls = paths.map((requestPath) => {
    const url = new URL(requestPath, baseUrl);
    if (url.origin !== origin) throw new Error("Load-test paths must stay on the audited localhost origin.");
    return url;
  });
  const endAt = Date.now() + durationSeconds * 1000;
  const samples = [];
  let requests = 0;
  let failed = 0;
  async function user(index) {
    if (rampUpSeconds) await new Promise((resolve) => setTimeout(resolve, rampUpSeconds * 1000 * index / users));
    let requestIndex = index;
    while (Date.now() < endAt) {
      const started = Date.now();
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), requestTimeoutMs);
      try {
        const response = await fetch(requestUrls[requestIndex++ % requestUrls.length], { signal: controller.signal });
        await response.body?.cancel();
        samples.push(Date.now() - started);
        requests += 1;
        if (response.status >= 400) failed += 1;
      } catch {
        requests += 1;
        failed += 1;
      } finally {
        clearTimeout(timeout);
      }
    }
  }
  await Promise.all(Array.from({ length: users }, (_, index) => user(index)));
  const errorRate = requests ? failed / requests * 100 : 100;
  const p95 = percentile(samples, 0.95);
  return {
    virtualUsers: users, durationSeconds, rampUpSeconds, requests,
    successfulRequests: requests - failed, failedRequests: failed,
    errorRate: Number(errorRate.toFixed(2)),
    averageResponseMs: samples.length ? Math.round(samples.reduce((a, b) => a + b, 0) / samples.length) : 0,
    p50ResponseMs: percentile(samples, 0.5), p95ResponseMs: p95,
    p99ResponseMs: percentile(samples, 0.99),
    requestsPerSecond: Number((requests / Math.max(1, durationSeconds)).toFixed(2)),
    status: errorRate > (options.maxErrorRate ?? 2) || p95 > (options.maxP95Ms ?? 1000) ? "warning" : "passed"
  };
}

module.exports = { loadTestLocalhost, MAX_VIRTUAL_USERS };
