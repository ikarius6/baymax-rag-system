// media/webview.js — Baymax Chat webview script
// Loaded as an external file via webview.asWebviewUri() to satisfy VS Code CSP.

(function () {
  const vscode = acquireVsCodeApi();

  const messagesEl = document.getElementById('messages');
  const inputEl    = document.getElementById('msg-input');
  const sendBtn    = document.getElementById('send-btn');
  const clearBtn   = document.getElementById('clear-btn');
  const statusDot  = document.getElementById('status-dot');
  const modelLabel = document.getElementById('model-label');
  const closeBtn   = document.getElementById('close-btn');

  console.log('[Baymax] webview.js loaded');

  let thinking = null;

  // ── Close panel ─────────────────────────────────────────────────────────────
  closeBtn.addEventListener('click', () => {
    vscode.postMessage({ type: 'close' });
  });

  // ── Auto-resize textarea ─────────────────────────────────────────────────────
  function autoResize() {
    inputEl.style.height = 'auto';
    inputEl.style.height = Math.min(inputEl.scrollHeight, 120) + 'px';
  }
  inputEl.addEventListener('input', autoResize);

  // ── Send on Enter (Shift+Enter = newline) ────────────────────────────────────
  inputEl.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });
  sendBtn.addEventListener('click', sendMessage);
  clearBtn.addEventListener('click', clearChat);

  function sendMessage() {
    const text = inputEl.value.trim();
    if (!text || sendBtn.disabled) return;
    appendBubble('user', text);
    inputEl.value = '';
    autoResize();
    sendBtn.disabled = true;
    vscode.postMessage({ type: 'send', text });
  }

  function clearChat() {
    vscode.postMessage({ type: 'clear' });
  }

  // ── Lightweight Markdown renderer ────────────────────────────────────────────
  // No external deps — runs entirely in the sandboxed webview.
  function escapeHtml(str) {
    return str
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function renderMarkdown(raw) {
    let html = '';
    const lines  = raw.split('\n');
    let i = 0;

    // helpers
    const inlineRender = (s) => s
      // fenced inline code  `code`
      .replace(/`([^`]+)`/g, (_, c) => '<code>' + escapeHtml(c) + '</code>')
      // bold+italic  ***text***
      .replace(/\*{3}(.+?)\*{3}/g, '<strong><em>$1</em></strong>')
      // bold  **text**
      .replace(/\*{2}(.+?)\*{2}/g, '<strong>$1</strong>')
      // italic  *text* or _text_
      .replace(/(\*|_)(.+?)\1/g, '<em>$2</em>')
      // strikethrough  ~~text~~
      .replace(/~~(.+?)~~/g, '<del>$1</del>')
      // links [label](url)
      .replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g,
        '<a href="$2" target="_blank" rel="noopener">$1</a>');

    while (i < lines.length) {
      const line = lines[i];

      // ── Fenced code block  ```[lang] ... ``` ──────────────────────────────
      const fenceMatch = line.match(/^\s*```(\w*)/);
      if (fenceMatch) {
        const lang = fenceMatch[1] || '';
        const codeLines = [];
        i++;
        while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) {
          codeLines.push(escapeHtml(lines[i]));
          i++;
        }
        i++; // consume closing ```
        html += `<pre><code${lang ? ' class="lang-' + lang + '"' : ''}>${codeLines.join('\n')}</code></pre>`;
        continue;
      }

      // ── Headings  # ## ### ────────────────────────────────────────────────
      const hMatch = line.match(/^(#{1,6})\s+(.*)/);
      if (hMatch) {
        const level = hMatch[1].length;
        html += `<h${level}>${inlineRender(escapeHtml(hMatch[2]))}</h${level}>`;
        i++;
        continue;
      }

      // ── Horizontal rule ───────────────────────────────────────────────────
      if (/^(\*{3,}|-{3,}|_{3,})$/.test(line.trim())) {
        html += '<hr>';
        i++;
        continue;
      }

      // ── Blockquote ─────────────────────────────────────────────────────
      if (/^>\s?/.test(line)) {
        const bqLines = [];
        while (i < lines.length && /^>\s?/.test(lines[i])) {
          bqLines.push(lines[i].replace(/^>\s?/, ''));
          i++;
        }
        html += '<blockquote>' + renderMarkdown(bqLines.join('\n')) + '</blockquote>';
        continue;
      }

      // ── Unordered list ────────────────────────────────────────────────────
      if (/^[\*\-\+]\s+/.test(line)) {
        html += '<ul>';
        while (i < lines.length && /^[\*\-\+]\s+/.test(lines[i])) {
          let item = lines[i].replace(/^[\*\-\+]\s+/, '');
          i++;
          // gather continuation lines (indented, not a new item/block)
          while (i < lines.length && lines[i].trim() !== '' &&
                 /^\s+/.test(lines[i]) && !/^\s*```/.test(lines[i]) &&
                 !/^[\*\-\+]\s+/.test(lines[i]) && !/^\d+\.\s+/.test(lines[i])) {
            item += ' ' + lines[i].trim();
            i++;
          }
          html += '<li>' + inlineRender(escapeHtml(item)) + '</li>';
        }
        html += '</ul>';
        continue;
      }

      // ── Ordered list ──────────────────────────────────────────────────────
      if (/^\d+\.\s+/.test(line)) {
        html += '<ol>';
        while (i < lines.length && /^\d+\.\s+/.test(lines[i])) {
          let item = lines[i].replace(/^\d+\.\s+/, '');
          i++;
          // gather continuation lines (indented, not a new item/block)
          while (i < lines.length && lines[i].trim() !== '' &&
                 /^\s+/.test(lines[i]) && !/^\s*```/.test(lines[i]) &&
                 !/^[\*\-\+]\s+/.test(lines[i]) && !/^\d+\.\s+/.test(lines[i])) {
            item += ' ' + lines[i].trim();
            i++;
          }
          html += '<li>' + inlineRender(escapeHtml(item)) + '</li>';
        }
        html += '</ol>';
        continue;
      }

      // ── Blank line → paragraph break ──────────────────────────────────────
      if (line.trim() === '') {
        i++;
        continue;
      }

      // ── Normal paragraph: accumulate until blank line ─────────────────────
      const paraLines = [];
      while (i < lines.length && lines[i].trim() !== '' &&
             !/^(#{1,6}\s|[\*\-\+]\s|\d+\.\s|\s*```|>\s?|(\*{3,}|-{3,}|_{3,})$)/.test(lines[i])) {
        paraLines.push(lines[i]);
        i++;
      }
      if (paraLines.length) {
        html += '<p>' + inlineRender(escapeHtml(paraLines.join(' '))) + '</p>';
      }
    }

    return html;
  }

  // ── Bubble renderer ──────────────────────────────────────────────────────────
  function getContainer() {
    const welcome = document.getElementById('welcome');
    if (welcome) welcome.remove();
    return messagesEl;
  }

  function appendBubble(role, content) {
    const container = getContainer();
    const row = document.createElement('div');
    row.className = 'msg-row ' + (role === 'user' ? 'user' : 'bot');
    const bub = document.createElement('div');
    bub.className = 'bubble' + (role === 'bot' ? ' markdown' : '');

    if (role === 'bot') {
      bub.innerHTML = renderMarkdown(content);
    } else {
      // User messages stay as safe plain text
      bub.textContent = content;
    }

    row.appendChild(bub);
    container.appendChild(row);
    scrollBottom();
    return row;
  }

  function appendError(msg) {
    const container = getContainer();
    const el = document.createElement('div');
    el.className = 'error-bubble';
    el.textContent = '\u26a0 ' + msg;
    container.appendChild(el);
    scrollBottom();
  }

  function showThinking() {
    removeThinking();
    const container = getContainer();
    const row = document.createElement('div');
    row.className = 'msg-row bot';
    const bub = document.createElement('div');
    bub.className = 'bubble thinking-dots';
    bub.innerHTML = '<span></span><span></span><span></span>';
    row.appendChild(bub);
    container.appendChild(row);
    thinking = row;
    scrollBottom();
  }

  function removeThinking() {
    if (thinking) { thinking.remove(); thinking = null; }
  }

  function scrollBottom() {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  // ── Restore history ──────────────────────────────────────────────────────────
  function restoreHistory(messages) {
    messagesEl.innerHTML = '';
    if (messages.length === 0) {
      const w = document.createElement('div');
      w.id = 'welcome';
      w.innerHTML = `<img src="${window.BAYMAX_LOGO_URI}" alt="Baymax" style="width:64px;height:64px;object-fit:contain;opacity:0.8"><div><strong style="color:var(--text)">Hey, I'm Baymax.</strong><br>Ask me anything about your team docs.</div>`;
      messagesEl.appendChild(w);
      return;
    }
    messages.forEach(m => appendBubble(m.role === 'user' ? 'user' : 'bot', m.content));
  }

  // ── Extension → webview messages ─────────────────────────────────────────────
  window.addEventListener('message', event => {
    const msg = event.data;
    switch (msg.type) {
      case 'thinking':
        showThinking();
        break;

      case 'response':
        removeThinking();
        appendBubble('bot', msg.message);
        sendBtn.disabled = false;
        inputEl.focus();
        break;

      case 'error':
        removeThinking();
        appendError(msg.message);
        sendBtn.disabled = false;
        inputEl.focus();
        break;

      case 'history':
        restoreHistory(msg.messages);
        break;

      case 'health': {
        const d = msg.data;
        if (d.status === 'ok' || d.status === 'online') {
          statusDot.className = 'online';
          statusDot.title = 'API online';
          modelLabel.textContent = d.chat_model + (d.use_graph ? ' \u00b7 graph' : '');
        } else {
          statusDot.className = 'offline';
          statusDot.title = 'API offline \u2013 start api.py';
          modelLabel.textContent = 'offline';
        }
        break;
      }
    }
  });

  console.log('[Baymax] posting ready');
  vscode.postMessage({ type: 'ready' });
  inputEl.focus();
}());
