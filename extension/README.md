# Knowledge Agent — Browser Extension 🧠

Save any page, article, or video to your Notion knowledge base with one click.

## Features

- 📥 **One-click save** — Click the extension icon to save the current page
- 🖱️ **Right-click menu** — "Save to Knowledge Agent" on any page, link, or selection
- ⌨️ **Keyboard shortcut** — `Alt+K` (Win/Linux) / `⌘+K` (Mac)
- 🌐 **Custom URL** — Paste any URL manually in the popup
- 📋 **Context menu for links** — Right-click any link to save directly
- 🔔 **Desktop notifications** — Get notified when saved successfully

## Installation

### 1. Load Unpacked (Developer Mode)

1. Open Chrome/Brave/Edge and go to `chrome://extensions`
2. Enable **Developer mode** (top-right toggle)
3. Click **Load unpacked**
4. Select the `extension` folder inside the knowledge-agent project

### 2. Pack & Install (For regular use)

```bash
cd knowledge-agent
zip -r extension.zip extension/
# Then drag & drop extension.zip into chrome://extensions
```

## Setup

1. Make sure your **Knowledge Agent server** is running (default: `http://localhost:8080`)
2. Click the extension icon → **Settings** (gear icon)
3. Set the correct **Server URL** if your agent runs on a different address
4. Click **Save**

## Usage

| Method | How |
|--------|-----|
| **Toolbar icon** | Click → current page URL auto-filled → click "Save to Notion" |
| **Right-click page** | Right-click anywhere → "Save to Knowledge Agent" |
| **Right-click link** | Right-click a link → "Save This Link to Notion" |
| **Keyboard** | Press `Alt+K` (Win) or `⌘+K` (Mac) to save instantly |
| **Custom URL** | Open popup → paste any URL → save |

## Tech

- Manifest V3
- No external dependencies
- Communicates with agent via REST API
- Settings synced via `chrome.storage.sync`
