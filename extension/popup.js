const $ = id => document.getElementById(id);
const DEFAULT_BACKEND = "https://sentinel-job-fraud-scanner.onrender.com";

function showStatus(msg, ok) {
  const el = $("status");
  el.textContent = msg;
  el.className = ok ? "ok" : "err";
}

async function load() {
  const { backendUrl, apiToken } = await chrome.storage.sync.get(["backendUrl", "apiToken"]);
  $("backend-url").value = backendUrl || DEFAULT_BACKEND;
  $("api-token").value = apiToken || "";
  if (apiToken) checkConnection(false);
}

async function ensureHostPermission(url) {
  try {
    const origin = new URL(url).origin + "/*";
    const has = await chrome.permissions.contains({ origins: [origin] });
    if (has) return true;
    return await chrome.permissions.request({ origins: [origin] });
  } catch {
    return false; // invalid URL — let the save/test call surface the error
  }
}

async function checkConnection(afterSave) {
  const testBtn = $("test-btn");
  testBtn.disabled = true;
  testBtn.textContent = "Testing...";
  const ping = await chrome.runtime.sendMessage({ type: "PING_BACKEND" });
  if (!ping.reachable) {
    showStatus(`Can't reach the backend. Is "python app.py" running?`, false);
    testBtn.disabled = false; testBtn.textContent = "Test connection";
    return;
  }
  const who = await chrome.runtime.sendMessage({ type: "WHOAMI" });
  if (who.ok) {
    showStatus(`Connected as ${who.data.username}`, true);
  } else {
    showStatus(who.data?.error || "Backend reachable, but the token was rejected.", false);
  }
  testBtn.disabled = false;
  testBtn.textContent = "Test connection";
}

$("save-btn").addEventListener("click", async () => {
  const backendUrl = ($("backend-url").value || DEFAULT_BACKEND).trim().replace(/\/+$/, "");
  const apiToken = $("api-token").value.trim();
  const granted = await ensureHostPermission(backendUrl);
  if (!granted) {
    showStatus("Permission to contact that URL was not granted.", false);
    return;
  }
  await chrome.storage.sync.set({ backendUrl, apiToken });
  showStatus("Saved.", true);
  checkConnection(true);
});

$("test-btn").addEventListener("click", () => checkConnection(false));

load();
