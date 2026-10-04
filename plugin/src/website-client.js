export async function uploadAnalysis(result, {
  apiUrl = process.env.QA_PLATFORM_API_URL,
  token = process.env.QA_PLATFORM_TOKEN
} = {}) {
  if (!apiUrl) {
    throw new Error("QA_PLATFORM_API_URL is required to upload results");
  }
  if (!token) {
    throw new Error("QA_PLATFORM_TOKEN is required to upload results");
  }
  const endpoint = new URL("/api/plugin/analysis", apiUrl).href;
  const response = await fetch(endpoint, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(result)
  });
  const body = await response.text();
  if (!response.ok) {
    throw new Error(`QA Platform upload failed (${response.status}): ${body.slice(0, 200)}`);
  }
  return body ? JSON.parse(body) : { status: "accepted" };
}
