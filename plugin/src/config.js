import fs from "node:fs/promises";
import path from "node:path";

const DEFAULTS = {
  maxPages: 20,
  screenshots: true,
  importantPages: [],
  ignoreRules: [],
  ignoreUrls: [],
  slowNavigationMs: 3000,
  slowResourceMs: 2000,
  apiMaxDurationMs: 3000,
  validateJson: true,
  loadTest: {
    users: 10,
    durationSeconds: 30,
    rampUpSeconds: 10,
    paths: ["/"],
    maxErrorRate: 2,
    maxP95Ms: 1000
  },
};

export async function loadConfig(workspace) {
  const filePath = path.join(path.resolve(workspace), ".qa-platform.json");
  try {
    const raw = await fs.readFile(filePath, "utf8");
    const config = JSON.parse(raw.replace(/^\uFEFF/, ""));
    if (!config || typeof config !== "object" || Array.isArray(config)) {
      throw new Error(".qa-platform.json must contain a JSON object");
    }
    return {
      ...DEFAULTS,
      ...config,
      loadTest: { ...DEFAULTS.loadTest, ...(config.loadTest || {}) },
      configPath: filePath
    };
  } catch (error) {
    if (error.code === "ENOENT") return { ...DEFAULTS, configPath: filePath };
    throw new Error(`Invalid ${filePath}: ${error.message}`);
  }
}

export function applyConfig(result, config) {
  const ignoredRules = new Set(config.ignoreRules || []);
  const ignoredUrls = config.ignoreUrls || [];
  result.issues = (result.issues || []).filter((finding) => {
    if (ignoredRules.has(finding.ruleId)) return false;
    return !ignoredUrls.some((value) => finding.url && finding.url.includes(value));
  });
  return result;
}
