
const DEFAULT_BACKEND = "http://127.0.0.1:5000";

async function getSettings() {
  const { backendUrl, apiToken } = await chrome.storage.sync.get(["backendUrl", "apiToken"]);
  return { backendUrl: (backendUrl || DEFAULT_BACKEND).replace(/\/+$/, ""), apiToken: apiToken || "" };
}

async function callApi(path, { method = "GET", body } = {}) {
  const { backendUrl, apiToken } = await getSettings();
  if (!apiToken) {
    return { ok: false, status: 401, data: { error: "No Sentinel API token configured yet. Click the extension icon and paste your token from the Sentinel web app (About tab)." } };
  }
  let res;
  try {
    res = await fetch(backendUrl + path, {
      method,
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${apiToken}`,
      },
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (err) {
    return { ok: false, status: 0, data: { error: `Could not reach ${backendUrl} — is the Sentinel server running? (${err.message})` } };
  }
  let data;
  try { data = await res.json(); } catch { data = {}; }
  return { ok: res.ok, status: res.status, data };
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    switch (msg.type) {
      case "PING_BACKEND": {
        const { backendUrl } = await getSettings();
        try {
          const res = await fetch(backendUrl + "/api/model-info");
          sendResponse({ ok: res.ok, reachable: true });
        } catch (err) {
          sendResponse({ ok: false, reachable: false, error: err.message });
        }
        break;
      }
      case "WHOAMI": {
        sendResponse(await callApi("/api/account/whoami"));
        break;
      }
      case "IMPORT_JSONLD": {
        sendResponse(await callApi("/api/extension/import-jsonld", {
          method: "POST", body: { jsonld: msg.jsonld, url: msg.url },
        }));
        break;
      }
      case "IMPORT_TEXT": {
        sendResponse(await callApi("/api/parse-text", { method: "POST", body: { text: msg.text } }));
        break;
      }
      case "PREDICT": {
        sendResponse(await callApi("/api/predict", { method: "POST", body: msg.record }));
        break;
      }
      case "SAVE_SCAN": {
        sendResponse(await callApi("/api/scans", {
          method: "POST", body: { record: msg.record, result: msg.result, meta: msg.meta },
        }));
        break;
      }
      case "OPEN_SCAN": {
        const { backendUrl } = await getSettings();
        chrome.tabs.create({ url: `${backendUrl}/?open_scan=${encodeURIComponent(msg.scanId)}` });
        sendResponse({ ok: true });
        break;
      }
      default:
        sendResponse({ ok: false, data: { error: "Unknown message type." } });
    }
  })();
  return true; // keep the message channel open for the async response above
});
