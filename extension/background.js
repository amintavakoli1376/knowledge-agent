// Knowledge Agent — Background Service Worker
const DEFAULT_SERVER = 'http://localhost:8080';

// ===== Initialize =====
chrome.runtime.onInstalled.addListener(() => {
  // Create context menu
  chrome.contextMenus.create({
    id: 'save-to-notion',
    title: 'Save to Knowledge Agent 📚',
    contexts: ['page', 'link', 'selection', 'video', 'image']
  });

  chrome.contextMenus.create({
    id: 'save-link-to-notion',
    title: 'Save This Link to Notion',
    contexts: ['link']
  });

  // Set default server URL
  chrome.storage.sync.get(['serverUrl'], (result) => {
    if (!result.serverUrl) {
      chrome.storage.sync.set({ serverUrl: DEFAULT_SERVER });
    }
  });
});

// ===== Context Menu Handler =====
chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId === 'save-to-notion') {
    const url = info.linkUrl || info.srcUrl || tab?.url;
    const title = info.selectionText || tab?.title;
    saveToNotion(url, title, tab?.id);
  } else if (info.menuItemId === 'save-link-to-notion' && info.linkUrl) {
    saveToNotion(info.linkUrl, '', tab?.id);
  }
});

// ===== Keyboard Shortcut =====
chrome.commands.onCommand.addListener((command) => {
  if (command === 'save-page') {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      const tab = tabs[0];
      if (tab) {
        saveToNotion(tab.url, tab.title, tab.id);
      }
    });
  }
});

// ===== Action Icon Click =====
chrome.action.onClicked.addListener((tab) => {
  saveToNotion(tab.url, tab.title, tab.id);
});

// ===== Core Save Function =====
async function saveToNotion(url, title, tabId) {
  if (!url) {
    showNotification('Error', 'No URL to save.');
    return;
  }

  try {
    // Get server URL from storage
    const result = await chrome.storage.sync.get(['serverUrl']);
    const serverUrl = result.serverUrl || DEFAULT_SERVER;

    // Show saving indicator
    if (tabId) {
      chrome.action.setBadgeText({ text: '...', tabId });
      chrome.action.setBadgeBackgroundColor({ color: '#6366F1', tabId });
    }

    const response = await fetch(`${serverUrl}/api/save`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, note: title || '' })
    });

    const data = await response.json();

    // Clear badge
    if (tabId) {
      chrome.action.setBadgeText({ text: '', tabId });
    }

    if (data.success) {
      showNotification(
        '✅ Saved to Notion!',
        `${data.title?.slice(0, 60) || 'Article'} → ${data.category || 'General'}`,
        data.notion_url
      );
    } else {
      showNotification('❌ Save Failed', data.error?.slice(0, 100) || 'Unknown error');
    }

  } catch (error) {
    if (tabId) {
      chrome.action.setBadgeText({ text: '!', tabId });
      chrome.action.setBadgeBackgroundColor({ color: '#EF4444', tabId });
      setTimeout(() => {
        chrome.action.setBadgeText({ text: '', tabId });
      }, 5000);
    }
    showNotification('❌ Connection Error', 
      'Cannot reach Knowledge Agent. Is the server running?');
  }
}

// ===== Notification Helper =====
function showNotification(title, message, url) {
  const id = 'ka-' + Date.now();
  chrome.notifications.create(id, {
    type: 'basic',
    iconUrl: 'icon128.png',
    title: title,
    message: message.slice(0, 200),
    buttons: url ? [{ title: '📖 Open in Notion' }] : [],
    priority: 2,
    requireInteraction: false
  });

  // Handle button click
  if (url) {
    const listener = (notifId, btnIdx) => {
      if (notifId === id && btnIdx === 0) {
        chrome.tabs.create({ url });
      }
      chrome.notifications.onButtonClicked.removeListener(listener);
    };
    chrome.notifications.onButtonClicked.addListener(listener);

    // Auto remove listener after 30s
    setTimeout(() => {
      chrome.notifications.onButtonClicked.removeListener(listener);
    }, 30000);
  }
}
