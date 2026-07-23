// Knowledge Agent — Popup Script
const DEFAULT_SERVER = 'http://localhost:8080';
let currentUrl = '';
let lastResult = null;

// ===== Initialize =====
document.addEventListener('DOMContentLoaded', async () => {
  // Load URL from current tab
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  if (tabs[0]) {
    currentUrl = tabs[0].url;
    document.getElementById('urlInput').value = currentUrl;
  }

  // Check server status
  checkServerStatus();
});

// ===== Save Button =====
document.getElementById('saveBtn').addEventListener('click', saveUrl);

// ===== URL Input Enter =====
document.getElementById('urlInput').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') saveUrl();
});

// ===== Current Page Button =====
document.getElementById('currentPageBtn').addEventListener('click', async () => {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  if (tabs[0]) {
    currentUrl = tabs[0].url;
    document.getElementById('urlInput').value = currentUrl;
    saveUrl();
  }
});

// ===== Settings Button =====
document.getElementById('settingsBtn').addEventListener('click', () => {
  chrome.runtime.openOptionsPage();
});

// ===== Save Function =====
async function saveUrl() {
  const url = document.getElementById('urlInput').value.trim();
  if (!url || !url.startsWith('http')) {
    showStatus('Please enter a valid URL starting with http:// or https://', 'error');
    return;
  }

  const btn = document.getElementById('saveBtn');
  btn.disabled = true;
  btn.textContent = '⏳ Saving...';
  showStatus('Sending to Knowledge Agent...', 'info');

  try {
    const result = await chrome.storage.sync.get(['serverUrl']);
    const serverUrl = result.serverUrl || DEFAULT_SERVER;

    const response = await fetch(`${serverUrl}/api/save`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url })
    });

    const data = await response.json();

    if (data.success) {
      lastResult = data;
      showStatus(
        `✅ Saved!\n📄 ${data.title?.slice(0, 50)}\n📂 ${data.category}`,
        'success'
      );
      btn.textContent = '✅ Saved!';
      btn.className = 'btn btn-success';

      // Show Notion link
      const link = document.getElementById('openNotionLink');
      if (data.notion_url) {
        link.href = data.notion_url;
        link.style.display = 'block';
        link.onclick = () => chrome.tabs.create({ url: data.notion_url });
      }
    } else {
      showStatus(`❌ ${data.error || 'Save failed'}`, 'error');
      btn.textContent = '❌ Failed — Try Again';
      btn.className = 'btn btn-error';
    }

  } catch (error) {
    showStatus(
      '❌ Cannot reach server. Is the Agent running?\n' +
      'Check Settings → Server URL',
      'error'
    );
    btn.textContent = '❌ Connection Error';
    btn.className = 'btn btn-error';
    updateServerStatus(false);
  }

  setTimeout(() => {
    btn.disabled = false;
    btn.textContent = '📥 Save to Notion';
    btn.className = 'btn';
  }, 3000);
}

// ===== Server Status Check =====
async function checkServerStatus() {
  try {
    const result = await chrome.storage.sync.get(['serverUrl']);
    const serverUrl = result.serverUrl || DEFAULT_SERVER;

    const response = await fetch(`${serverUrl}/health`, { signal: AbortSignal.timeout(3000) });
    const data = await response.json();
    
    const isOk = data.status === 'ok';
    updateServerStatus(isOk);
  } catch {
    updateServerStatus(false);
  }
}

function updateServerStatus(isConnected) {
  const el = document.getElementById('serverStatus');
  if (isConnected) {
    el.textContent = '🟢 Server Connected';
    el.style.color = '#10b981';
  } else {
    el.textContent = '🔴 Server Offline';
    el.style.color = '#ef4444';
  }
}

// ===== Show Status =====
function showStatus(message, type) {
  const el = document.getElementById('status');
  el.textContent = message;
  el.className = `status show ${type}`;
}
