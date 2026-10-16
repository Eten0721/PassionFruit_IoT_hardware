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
    const suggestions = document.getElementById('assistant-suggestions');
    const INPUT_MAX_HEIGHT = 160;
    const history = [];
    let inFlight = false;

    // Suggested prompts only greet an empty conversation.
    function syncSuggestions() {
        suggestions.hidden = history.length > 0;
    }

    function syncInput() {
        input.style.height = 'auto';
        input.style.height = `${Math.min(input.scrollHeight, INPUT_MAX_HEIGHT)}px`;
        sendButton.disabled = inFlight || !input.value.trim();
    }

    function setOpen(open) {
        panel.hidden = !open;
        launcher.setAttribute('aria-expanded', String(open));
        if (!open) return;
        restoreSize();
        positionPanel();
        input.focus();
    }

    // Floating launcher: drag (or arrow keys) moves it anywhere on screen; the
    // spot is kept per browser as a fraction of the viewport.
    const POSITION_KEY = 'assistant-launcher-position';
    const EDGE = 12;
    const PANEL_GAP = 12;
    const DRAG_THRESHOLD = 5;
    let drag = null;
    let suppressClick = false;

    function clamp(value, min, max) {
        return Math.min(Math.max(value, min), Math.max(min, max));
    }

    function placeLauncher(left, top) {
        const rect = launcher.getBoundingClientRect();
        launcher.style.left = `${clamp(left, EDGE, window.innerWidth - rect.width - EDGE)}px`;
        launcher.style.top = `${clamp(top, EDGE, window.innerHeight - rect.height - EDGE)}px`;
        launcher.style.right = 'auto';
        launcher.style.bottom = 'auto';
        positionPanel();
    }

    // The panel opens toward the roomier side of the launcher.
    function positionPanel() {
        if (panel.hidden) return;
        const anchor = launcher.getBoundingClientRect();
        const box = panel.getBoundingClientRect();
        const onRight = anchor.left + anchor.width / 2 > window.innerWidth / 2;
        const onBottom = anchor.top + anchor.height / 2 > window.innerHeight / 2;
        const left = onRight ? anchor.right - box.width : anchor.left;
        const top = onBottom ? anchor.top - PANEL_GAP - box.height : anchor.bottom + PANEL_GAP;
        panel.style.left = `${clamp(left, EDGE, window.innerWidth - box.width - EDGE)}px`;
        panel.style.top = `${clamp(top, EDGE, window.innerHeight - box.height - EDGE)}px`;
        panel.style.right = 'auto';
        panel.style.bottom = 'auto';
        // The resize grip sits on the corner away from the launcher.
        panel.dataset.grip = `${onBottom ? 'top' : 'bottom'}-${onRight ? 'left' : 'right'}`;
    }

    // Resizable panel: the corner next to the launcher stays put while the
    // grip on the far corner grows or shrinks it.
    const SIZE_KEY = 'assistant-panel-size';
    const MIN_WIDTH = 320;
    const MIN_HEIGHT = 360;
    const resizeGrip = document.getElementById('assistant-resize');
    let resize = null;

    function applySize(width, height) {
        panel.style.width = `${clamp(width, MIN_WIDTH, window.innerWidth - EDGE * 2)}px`;
        panel.style.height = `${clamp(height, MIN_HEIGHT, window.innerHeight - EDGE * 2)}px`;
        positionPanel();
    }

    function saveSize() {
        const box = panel.getBoundingClientRect();
        try {
            localStorage.setItem(SIZE_KEY, JSON.stringify({ width: box.width, height: box.height }));
        } catch (error) {
            // Storage blocked: the size simply lasts until reload.
        }
    }

    function restoreSize() {
        let saved = null;
        try {
            saved = JSON.parse(localStorage.getItem(SIZE_KEY));
        } catch (error) {
            saved = null;
        }
        if (!saved || !Number.isFinite(saved.width) || !Number.isFinite(saved.height)) return;
        applySize(saved.width, saved.height);
    }

    function resetSize() {
        panel.style.width = '';
        panel.style.height = '';
        try {
            localStorage.removeItem(SIZE_KEY);
        } catch (error) {
            // Nothing stored to clear.
        }
        positionPanel();
    }

    function gripSigns() {
        const grip = panel.dataset.grip || 'top-left';
        return { x: grip.endsWith('left') ? -1 : 1, y: grip.startsWith('top') ? -1 : 1 };
    }

    resizeGrip.addEventListener('pointerdown', (event) => {
        if (event.button !== 0) return;
        const box = panel.getBoundingClientRect();
        resize = { id: event.pointerId, x: event.clientX, y: event.clientY, width: box.width, height: box.height, signs: gripSigns() };
        resizeGrip.setPointerCapture(event.pointerId);
        panel.classList.add('is-resizing');
        event.preventDefault();
    });
    resizeGrip.addEventListener('pointermove', (event) => {
        if (!resize || event.pointerId !== resize.id) return;
        applySize(
            resize.width + (event.clientX - resize.x) * resize.signs.x,
            resize.height + (event.clientY - resize.y) * resize.signs.y,
        );
    });
    function endResize(event) {
        if (!resize || event.pointerId !== resize.id) return;
        resize = null;
        panel.classList.remove('is-resizing');
        saveSize();
    }
    resizeGrip.addEventListener('pointerup', endResize);
    resizeGrip.addEventListener('pointercancel', endResize);
    resizeGrip.addEventListener('dblclick', resetSize);
    resizeGrip.addEventListener('keydown', (event) => {
        const steps = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
        const step = steps[event.key];
        if (!step) return;
        event.preventDefault();
        const box = panel.getBoundingClientRect();
        const signs = gripSigns();
        const distance = event.shiftKey ? 80 : 20;
        applySize(box.width + step[0] * distance * signs.x, box.height + step[1] * distance * signs.y);
        saveSize();
    });

    function savePosition() {
        const rect = launcher.getBoundingClientRect();
        const center = {
            x: (rect.left + rect.width / 2) / window.innerWidth,
            y: (rect.top + rect.height / 2) / window.innerHeight,
        };
        try {
            localStorage.setItem(POSITION_KEY, JSON.stringify(center));
        } catch (error) {
            // Storage blocked: the position simply lasts until reload.
        }
    }

    function restorePosition() {
        let saved = null;
        try {
            saved = JSON.parse(localStorage.getItem(POSITION_KEY));
        } catch (error) {
            saved = null;
        }
        if (!saved || !Number.isFinite(saved.x) || !Number.isFinite(saved.y)) {
            positionPanel();
            return;
        }
        const rect = launcher.getBoundingClientRect();
        placeLauncher(saved.x * window.innerWidth - rect.width / 2, saved.y * window.innerHeight - rect.height / 2);
    }

    function moveBy(dx, dy) {
        const rect = launcher.getBoundingClientRect();
        placeLauncher(rect.left + dx, rect.top + dy);
        savePosition();
    }

    function endDrag(event, released) {
        if (!drag || event.pointerId !== drag.id) return;
        if (drag.moved) {
            savePosition();
            suppressClick = released;
        }
        launcher.classList.remove('is-dragging');
        drag = null;
    }

    launcher.addEventListener('pointerdown', (event) => {
        if (event.button !== 0) return;
        const rect = launcher.getBoundingClientRect();
        suppressClick = false;
        drag = {
            id: event.pointerId,
            startX: event.clientX,
            startY: event.clientY,
            offsetX: event.clientX - rect.left,
            offsetY: event.clientY - rect.top,
            moved: false,
        };
        launcher.setPointerCapture(event.pointerId);
    });
    launcher.addEventListener('pointermove', (event) => {
        if (!drag || event.pointerId !== drag.id) return;
        const distance = Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY);
        if (!drag.moved && distance < DRAG_THRESHOLD) return;
        drag.moved = true;
        launcher.classList.add('is-dragging');
        placeLauncher(event.clientX - drag.offsetX, event.clientY - drag.offsetY);
    });
    launcher.addEventListener('pointerup', (event) => endDrag(event, true));
    launcher.addEventListener('pointercancel', (event) => endDrag(event, false));
    launcher.addEventListener('keydown', (event) => {
        const steps = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
        const step = steps[event.key];
        if (!step) return;
        event.preventDefault();
        const distance = event.shiftKey ? 64 : 16;
        moveBy(step[0] * distance, step[1] * distance);
    });
    window.addEventListener('resize', () => {
        if (panel.style.width) {
            const box = panel.getBoundingClientRect();
            applySize(box.width, box.height);
        }
        if (launcher.style.left) {
            const rect = launcher.getBoundingClientRect();
            placeLauncher(rect.left, rect.top);
            return;
        }
        positionPanel();
    });

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
        input.value = '';
        syncInput();
        history.push({ role: 'user', text });
        syncSuggestions();
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
                syncSuggestions();
                appendEntry('error', payload.message || `助手無法回覆（HTTP ${response.status}）`);
                return;
            }
            // The server only trusts model turns that come back with its signature.
            history.push({ role: 'model', text: payload.reply, signature: payload.signature });
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
            syncSuggestions();
            appendEntry('error', `無法連線到 Dashboard 伺服器：${error.message}`);
        } finally {
            inFlight = false;
            syncInput();
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
        // The button changes size with the icon, so re-centre it on its spot.
        restorePosition();
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
    restorePosition();
    loadIcon();

    launcher.addEventListener('click', () => {
        if (suppressClick) {
            suppressClick = false;
            return;
        }
        setOpen(panel.hidden);
    });
    closeButton.addEventListener('click', () => setOpen(false));
    form.addEventListener('submit', ask);
    input.addEventListener('input', syncInput);
    suggestions.addEventListener('click', (event) => {
        const button = event.target.closest('.assistant-suggestion');
        if (!button || inFlight) return;
        input.value = button.dataset.prompt;
        syncInput();
        form.requestSubmit();
    });
    syncInput();
    input.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
            form.requestSubmit();
            event.preventDefault();
        }
    });
}());
