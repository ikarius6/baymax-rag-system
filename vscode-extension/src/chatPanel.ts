/**
 * chatPanel.ts — WebviewViewProvider that renders the Baymax chat UI.
 *
 * Message protocol (extension ↔ webview):
 *   webview → extension:  { type: "send", text: string }
 *                         { type: "clear" }
 *                         { type: "ready" }
 *   extension → webview:  { type: "response", message: string, sessionId: string }
 *                         { type: "error", message: string }
 *                         { type: "thinking" }
 *                         { type: "history", messages: Message[] }
 *                         { type: "health", data: HealthResponse }
 */

import * as vscode from "vscode";
import * as path from "path";
import { BaymaxApiClient, HealthResponse } from "./api";

interface Message {
  role: "user" | "assistant";
  content: string;
}

function getNonce(): string {
  let text = "";
  const possible = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
  for (let i = 0; i < 32; i++) {
    text += possible.charAt(Math.floor(Math.random() * possible.length));
  }
  return text;
}

export class ChatPanelProvider implements vscode.WebviewViewProvider {
  private _view?: vscode.WebviewView;
  private _sessionId?: string;
  private _history: Message[] = [];

  constructor(private readonly _context: vscode.ExtensionContext) {}

  private get _apiUrl(): string {
    return vscode.workspace
      .getConfiguration("baymax")
      .get<string>("apiUrl", "http://127.0.0.1:8888");
  }

  private get _client(): BaymaxApiClient {
    return new BaymaxApiClient(this._apiUrl);
  }

  resolveWebviewView(
    webviewView: vscode.WebviewView,
    _context: vscode.WebviewViewResolveContext,
    _token: vscode.CancellationToken
  ): void {
    this._view = webviewView;

    const mediaDir = vscode.Uri.file(
      path.join(this._context.extensionPath, "media")
    );
    webviewView.webview.options = {
      enableScripts: true,
      localResourceRoots: [mediaDir],
    };
    const scriptUri = webviewView.webview.asWebviewUri(
      vscode.Uri.file(path.join(this._context.extensionPath, "media", "webview.js"))
    );
    const logoUri = webviewView.webview.asWebviewUri(
      vscode.Uri.file(path.join(this._context.extensionPath, "media", "baymax-icon.svg"))
    );
    webviewView.webview.html = this._getHtml(webviewView.webview, scriptUri, logoUri);

    // Handle messages from webview
    webviewView.webview.onDidReceiveMessage(async (msg) => {
      switch (msg.type) {
        case "ready":
          // Restore in-memory history when panel reloads
          if (this._history.length > 0) {
            webviewView.webview.postMessage({
              type: "history",
              messages: this._history,
            });
          }
          // Ping health
          this._pingHealth();
          break;

        case "send":
          await this._handleSend(msg.text as string);
          break;

        case "clear":
          await this._handleClear();
          break;

        case "close":
          vscode.commands.executeCommand("workbench.action.closeSidebar");
          break;
      }
    });
  }

  private async _handleSend(text: string): Promise<void> {
    if (!this._view) {
      return;
    }

    // Optimistically add user message
    this._history.push({ role: "user", content: text });
    this._view.webview.postMessage({ type: "thinking" });

    try {
      const res = await this._client.chat(text, this._sessionId);
      this._sessionId = res.session_id;
      this._history.push({ role: "assistant", content: res.message });
      this._view.webview.postMessage({
        type: "response",
        message: res.message,
        sessionId: res.session_id,
      });
    } catch (err: unknown) {
      const msg =
        err instanceof Error ? err.message : "Unknown error contacting Baymax API";
      this._view.webview.postMessage({ type: "error", message: msg });
      // Remove the pending user message from local history on error
      this._history.pop();
    }
  }

  private async _handleClear(): Promise<void> {
    if (this._sessionId) {
      try {
        await this._client.clearHistory(this._sessionId);
      } catch {
        // best-effort
      }
    }
    this._sessionId = undefined;
    this._history = [];
    this._view?.webview.postMessage({ type: "history", messages: [] });
  }

  clearHistory(): void {
    this._handleClear();
  }

  async checkHealth(): Promise<HealthResponse | null> {
    try {
      const data = await this._client.health();
      this._view?.webview.postMessage({ type: "health", data });
      return data;
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      vscode.window.showErrorMessage(`Baymax API unreachable: ${msg}`);
      return null;
    }
  }

  private async _pingHealth(): Promise<void> {
    try {
      const data = await this._client.health();
      this._view?.webview.postMessage({ type: "health", data });
    } catch {
      this._view?.webview.postMessage({
        type: "health",
        data: { status: "offline", chat_model: "–", use_graph: false },
      });
    }
  }

  // ── HTML ─────────────────────────────────────────────────────────────────────

  private _getHtml(webview: vscode.Webview, scriptUri: vscode.Uri, logoUri: vscode.Uri): string {
    const csp = webview.cspSource;
    const nonce = getNonce();
    return /* html */ `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src ${csp}; style-src 'unsafe-inline'; script-src 'nonce-${nonce}' ${csp};"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Baymax Chat</title>
<style>
  :root {
    --bg: var(--vscode-sideBar-background, #1e1e2e);
    --surface: var(--vscode-editorWidget-background, #2a2a3d);
    --border: var(--vscode-editorWidget-border, #3d3d5c);
    --text: var(--vscode-foreground, #cdd6f4);
    --subtext: var(--vscode-descriptionForeground, #6c7086);
    --accent: var(--vscode-button-background, #7c7ff5);
    --accent-fg: var(--vscode-button-foreground, #fff);
    --user-bubble: var(--vscode-button-background, #7c7ff5);
    --user-fg: var(--vscode-button-foreground, #fff);
    --bot-bubble: var(--vscode-editorWidget-background, #2a2a3d);
    --bot-fg: var(--vscode-foreground, #cdd6f4);
    --input-bg: var(--vscode-input-background, #1e1e2e);
    --input-fg: var(--vscode-input-foreground, #cdd6f4);
    --input-border: var(--vscode-input-border, #4a4a6a);
    --radius: 14px;
    --font: var(--vscode-font-family, 'Inter', 'Segoe UI', system-ui, sans-serif);
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    font-family: var(--font);
    background: var(--bg);
    color: var(--text);
    height: 100vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  /* ── Header ── */
  #header {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 14px 8px;
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
  }
  #header svg { flex-shrink: 0; }
  #header img.logo {
    width: 22px;
    height: 22px;
    flex-shrink: 0;
    object-fit: contain;
  }
  #header-title {
    flex: 1;
    font-size: 14px;
    font-weight: 600;
    letter-spacing: 0.02em;
  }
  #status-dot {
    width: 8px; height: 8px;
    border-radius: 50%;
    background: var(--subtext);
    transition: background 0.3s;
    flex-shrink: 0;
  }
  #status-dot.online  { background: #a6e3a1; box-shadow: 0 0 6px #a6e3a1; }
  #status-dot.offline { background: #f38ba8; box-shadow: 0 0 6px #f38ba8; }
  #model-label {
    font-size: 10px;
    color: var(--subtext);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 120px;
  }

  /* ── Messages ── */
  #messages {
    flex: 1;
    overflow-y: auto;
    padding: 12px 10px;
    display: flex;
    flex-direction: column;
    gap: 10px;
    scroll-behavior: smooth;
  }
  #messages::-webkit-scrollbar { width: 4px; }
  #messages::-webkit-scrollbar-track { background: transparent; }
  #messages::-webkit-scrollbar-thumb { background: var(--border); border-radius: 4px; }

  .msg-row {
    display: flex;
    flex-direction: column;
    max-width: 90%;
    animation: fadeUp 0.2s ease;
  }
  @keyframes fadeUp {
    from { opacity: 0; transform: translateY(6px); }
    to   { opacity: 1; transform: translateY(0); }
  }
  .msg-row.user  { align-self: flex-end; align-items: flex-end; }
  .msg-row.bot   { align-self: flex-start; align-items: flex-start; }

  .bubble {
    padding: 9px 13px;
    border-radius: var(--radius);
    font-size: 13px;
    line-height: 1.55;
    word-break: break-word;
    white-space: pre-wrap;
  }
  .user .bubble {
    background: var(--user-bubble);
    color: var(--user-fg);
    border-bottom-right-radius: 4px;
  }
  .bot .bubble {
    background: var(--bot-bubble);
    color: var(--bot-fg);
    border: 1px solid var(--border);
    border-bottom-left-radius: 4px;
  }

  .thinking-dots span {
    display: inline-block;
    width: 6px; height: 6px;
    border-radius: 50%;
    background: var(--subtext);
    animation: bounce 1.2s infinite ease-in-out;
    margin: 0 2px;
  }
  .thinking-dots span:nth-child(2) { animation-delay: 0.2s; }
  .thinking-dots span:nth-child(3) { animation-delay: 0.4s; }
  @keyframes bounce {
    0%, 80%, 100% { transform: scale(0.7); opacity: 0.4; }
    40%           { transform: scale(1);   opacity: 1;   }
  }

  .error-bubble {
    background: rgba(243,139,168,0.15);
    border: 1px solid #f38ba8;
    color: #f38ba8;
    padding: 8px 12px;
    border-radius: 10px;
    font-size: 12px;
    max-width: 90%;
    align-self: center;
  }

  /* Welcome splash */
  #welcome {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 10px;
    flex: 1;
    text-align: center;
    padding: 20px;
    color: var(--subtext);
    font-size: 13px;
  }
  #welcome svg { opacity: 0.5; }

  /* ── Input bar ── */
  #input-area {
    padding: 8px 10px 10px;
    border-top: 1px solid var(--border);
    display: flex;
    gap: 6px;
    align-items: flex-end;
    flex-shrink: 0;
  }
  #input-wrap {
    flex: 1;
    position: relative;
  }
  #msg-input {
    width: 100%;
    min-height: 36px;
    max-height: 120px;
    padding: 8px 36px 8px 12px;
    background: var(--input-bg);
    color: var(--input-fg);
    border: 1px solid var(--input-border);
    border-radius: 10px;
    font-family: var(--font);
    font-size: 13px;
    resize: none;
    outline: none;
    overflow-y: auto;
    line-height: 1.5;
    transition: border-color 0.2s;
  }
  #msg-input:focus { border-color: var(--accent); }
  #msg-input::placeholder { color: var(--subtext); }

  #send-btn {
    position: absolute;
    right: 6px;
    bottom: 6px;
    background: var(--accent);
    color: var(--accent-fg);
    border: none;
    border-radius: 7px;
    width: 26px; height: 26px;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    opacity: 0.9;
    transition: opacity 0.15s, transform 0.1s;
    flex-shrink: 0;
  }
  #send-btn:hover { opacity: 1; transform: scale(1.08); }
  #send-btn:active { transform: scale(0.95); }
  #send-btn:disabled { opacity: 0.35; cursor: not-allowed; transform: none; }

  #clear-btn {
    background: transparent;
    border: 1px solid var(--border);
    border-radius: 8px;
    color: var(--subtext);
    padding: 6px 8px;
    cursor: pointer;
    font-size: 12px;
    transition: color 0.15s, border-color 0.15s;
    flex-shrink: 0;
    white-space: nowrap;
  }
  #clear-btn:hover { color: var(--text); border-color: var(--accent); }

  #close-btn {
    background: transparent;
    border: none;
    color: var(--subtext);
    cursor: pointer;
    font-size: 16px;
    line-height: 1;
    padding: 2px 4px;
    border-radius: 4px;
    transition: color 0.15s, background 0.15s;
    flex-shrink: 0;
  }
  #close-btn:hover { color: var(--text); background: rgba(255,255,255,0.07); }

  /* ── Markdown in bot bubbles ── */
  .bubble.markdown { white-space: normal; }

  .bubble.markdown p  { margin: 0 0 0.5em; line-height: 1.6; }
  .bubble.markdown p:last-child { margin-bottom: 0; }

  .bubble.markdown h1,
  .bubble.markdown h2,
  .bubble.markdown h3,
  .bubble.markdown h4 {
    margin: 0.6em 0 0.3em;
    font-weight: 600;
    line-height: 1.3;
    color: var(--text);
  }
  .bubble.markdown h1 { font-size: 1.1em; }
  .bubble.markdown h2 { font-size: 1.0em; }
  .bubble.markdown h3 { font-size: 0.95em; }

  .bubble.markdown code {
    font-family: 'Cascadia Code', 'Fira Code', Consolas, monospace;
    font-size: 0.85em;
    background: rgba(0,0,0,0.25);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 1px 5px;
  }
  .bubble.markdown pre {
    background: rgba(0,0,0,0.3);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 10px 12px;
    overflow-x: auto;
    margin: 0.5em 0;
  }
  .bubble.markdown pre code {
    background: none;
    border: none;
    padding: 0;
    font-size: 0.82em;
    line-height: 1.55;
  }

  .bubble.markdown ul,
  .bubble.markdown ol {
    margin: 0.3em 0 0.5em 1.2em;
    padding: 0;
  }
  .bubble.markdown li { margin: 0.2em 0; line-height: 1.5; }

  .bubble.markdown a {
    color: var(--accent);
    text-decoration: underline;
    text-underline-offset: 2px;
  }
  .bubble.markdown a:hover { opacity: 0.85; }

  .bubble.markdown blockquote {
    border-left: 3px solid var(--accent);
    background: rgba(0,0,0,0.15);
    margin: 0.4em 0;
    padding: 6px 12px;
    border-radius: 0 6px 6px 0;
    color: var(--subtext);
  }
  .bubble.markdown blockquote p { margin-bottom: 0.3em; }
  .bubble.markdown blockquote p:last-child { margin-bottom: 0; }

  .bubble.markdown strong { font-weight: 600; }
  .bubble.markdown em     { font-style: italic; }
  .bubble.markdown del    { opacity: 0.6; text-decoration: line-through; }
  .bubble.markdown hr {
    border: none;
    border-top: 1px solid var(--border);
    margin: 0.6em 0;
  }
</style>
</head>
<body>

<!-- Header -->
<div id="header">
  <img class="logo" src="${logoUri}" alt="Baymax">
  <span id="header-title">Baymax</span>
  <span id="model-label">–</span>
  <div id="status-dot" title="API status"></div>
  <button id="close-btn" title="Close panel">✕</button>
</div>

<!-- Messages -->
<div id="messages">
  <div id="welcome">
    <img src="${logoUri}" alt="Baymax" style="width:64px;height:64px;object-fit:contain;opacity:0.8">
    <div><strong style="color:var(--text)">Hey, I'm Baymax.</strong><br>Ask me anything about your team docs.</div>
  </div>
</div>

<!-- Input -->
<div id="input-area">
  <div id="input-wrap">
    <textarea id="msg-input" rows="1" placeholder="Ask Baymax…"></textarea>
    <button id="send-btn" title="Send (Enter)">
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M22 2L11 13" stroke="white" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
        <path d="M22 2L15 22L11 13L2 9L22 2Z" stroke="white" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" fill="white"/>
      </svg>
    </button>
  </div>
  <button id="clear-btn" title="Clear conversation">Clear</button>
</div>

<script nonce="${nonce}">window.BAYMAX_LOGO_URI = "${logoUri}";</script>
<script nonce="${nonce}" src="${scriptUri}"></script>
</body>
</html>`;
  }
}
