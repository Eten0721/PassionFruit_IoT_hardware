// Dashboard read-only assistant chat. The conversation lives only in this
// page's memory; charts are drawn from Django's tool output, not model text.
(function () {
    'use strict';

    const MAX_HISTORY = 12;
    const launcher = document.getElementById('assistant-launcher');
    const panel = document.getElementById('assistant-panel');
    const closeButton = document.getElementById('assistant-close');
    const log = document.getElementById('assistant-log');
    const form = document.getElementById('assistant-form');
    const input = document.getElementById('assistant-input');
    const sendButton = document.getElementById('assistant-send');
    const history = [];
    let inFlight = false;

    function setOpen(open) {
        panel.hidden = !open;
        launcher.setAttribute('aria-expanded', String(open));
        if (open) input.focus();
    }

    function appendEntry(role, text, { markdown = false } = {}) {
        const item = document.createElement('li');
        item.className = `assistant-entry ${role}`;
        if (markdown) {
            const body = document.createElement('div');
            body.className = 'assistant-markdown';
            window.AssistantRender.renderMarkdown(document, body, text);
            item.appendChild(body);
        } else {
            const body = document.createElement('p');
            body.textContent = text;
            item.appendChild(body);
        }
        log.appendChild(item);
        log.scrollTop = log.scrollHeight;
        return item;
    }

    function appendChart(item, spec) {
        const figure = document.createElement('figure');
        figure.className = 'assistant-chart';
        const canvas = document.createElement('canvas');
        canvas.setAttribute('role', 'img');
        canvas.setAttribute('aria-label', spec.title);
        figure.appendChild(canvas);
        item.appendChild(figure);
        if (typeof window.Chart !== 'function') {
            canvas.replaceWith(document.createTextNode('圖表元件未載入，無法顯示圖表。'));
            return;
        }
        new window.Chart(canvas, window.AssistantRender.chartConfig(spec));
    }

    function appendSources(item, sources) {
        if (!sources.length) return;
        const list = document.createElement('ul');
        list.className = 'assistant-sources';
        sources.forEach((source) => {
            const entry = document.createElement('li');
            const link = document.createElement('a');
            link.href = source.uri;
            link.target = '_blank';
            link.rel = 'noopener noreferrer';
            link.textContent = source.title;
            entry.appendChild(link);
            list.appendChild(entry);
        });
        item.appendChild(document.createTextNode('來源：'));
        item.appendChild(list);
    }

    async function ask(event) {
        event.preventDefault();
        const text = input.value.trim();
        if (!text || inFlight) return;
        inFlight = true;
        sendButton.disabled = true;
        input.value = '';
        history.push({ role: 'user', text });
        appendEntry('user', text);
        const pending = appendEntry('model pending', '查詢中…');
        try {
            const response = await fetch('/api/assistant/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ messages: history.slice(-MAX_HISTORY) }),
            });
            const payload = await response.json().catch(() => ({}));
            pending.remove();
            if (!response.ok || !payload.ok) {
                // A failed turn is dropped so the next question starts clean.
                history.pop();
                appendEntry('error', payload.message || `助手無法回覆（HTTP ${response.status}）`);
                return;
            }
            history.push({ role: 'model', text: payload.reply });
            const item = appendEntry('model', payload.reply, { markdown: true });
            (payload.charts || []).forEach((spec) => appendChart(item, spec));
            appendSources(item, payload.sources || []);
            (payload.notices || []).forEach((notice) => {
                const note = document.createElement('p');
                note.className = 'assistant-notice';
                note.textContent = `⚠ ${notice}`;
                item.appendChild(note);
            });
        } catch (error) {
            pending.remove();
            history.pop();
            appendEntry('error', `無法連線到 Dashboard 伺服器：${error.message}`);
        } finally {
            inFlight = false;
            sendButton.disabled = false;
        }
    }

    const launcherIcon = document.getElementById('assistant-launcher-icon');
    const launcherText = document.getElementById('assistant-launcher-text');
    const iconInput = document.getElementById('assistant-icon-input');
    const iconReset = document.getElementById('assistant-icon-reset');
    const iconStatus = document.getElementById('assistant-icon-status');

    function showIcon(present) {
        launcher.classList.toggle('has-icon', present);
        launcherIcon.hidden = !present;
        launcherText.classList.toggle('visually-hidden', present);
    }

    function loadIcon() {
        // A missing icon is a 404; the text button simply stays.
        launcherIcon.onload = () => showIcon(true);
        launcherIcon.onerror = () => showIcon(false);
        launcherIcon.src = `/api/assistant/icon.png?v=${Date.now()}`;
    }

    async function postIcon(body, doneText) {
        iconStatus.textContent = '處理中…';
        try {
            const response = await fetch('/api/assistant/icon/', { method: 'POST', body });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                iconStatus.textContent = payload.message || `更換失敗（HTTP ${response.status}）`;
                return;
            }
            iconStatus.textContent = doneText;
            if (payload.has_icon) loadIcon(); else showIcon(false);
        } catch (error) {
            iconStatus.textContent = `更換失敗：${error.message}`;
        }
    }

    iconInput.addEventListener('change', () => {
        const file = iconInput.files[0];
        if (!file) return;
        const body = new FormData();
        body.append('icon', file);
        iconInput.value = '';
        postIcon(body, '已更換按鈕圖片。');
    });
    iconReset.addEventListener('click', () => {
        const body = new FormData();
        body.append('reset', 'true');
        postIcon(body, '已恢復文字按鈕。');
    });
    loadIcon();

    launcher.addEventListener('click', () => setOpen(panel.hidden));
    closeButton.addEventListener('click', () => setOpen(false));
    form.addEventListener('submit', ask);
    input.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
            form.requestSubmit();
            event.preventDefault();
        }
    });
}());
