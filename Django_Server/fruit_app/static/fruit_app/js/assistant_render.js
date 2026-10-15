// Pure rendering helpers for the assistant chat, kept DOM-light for node tests.
// Markdown: a small, safe subset for replies (headings, bold, inline code,
// unordered/ordered lists, block quotes, paragraphs and line breaks). Model
// text only ever becomes text nodes (createTextNode), so any HTML in a reply
// is shown literally and never parsed; innerHTML is never used.
// Charts: Chart.js config from Django's chart spec; percentage charts keep a
// fixed 0-100 axis so separate charts are never drawn on different scales.
(function (root) {
    'use strict';

    const INLINE = /(`[^`\n]+`|\*\*[^*\n]+\*\*|__[^_\n]+__)/g;

    function parseInline(text) {
        const parts = [];
        let last = 0;
        for (const match of text.matchAll(INLINE)) {
            if (match.index > last) parts.push({ type: 'text', text: text.slice(last, match.index) });
            const token = match[0];
            parts.push(token.startsWith('`')
                ? { type: 'code', text: token.slice(1, -1) }
                : { type: 'strong', text: token.slice(2, -2) });
            last = match.index + token.length;
        }
        if (last < text.length) parts.push({ type: 'text', text: text.slice(last) });
        return parts;
    }

    function parseMarkdown(text) {
        const blocks = [];
        let open = null;  // the paragraph, list or quote that following lines extend
        String(text ?? '').split(/\r?\n/).forEach((raw) => {
            const line = raw.replace(/\s+$/, '');
            let match;
            if (!line.trim()) {
                open = null;
            } else if ((match = line.match(/^\s{0,3}(#{1,6})\s+(.*)$/))) {
                blocks.push({ type: 'heading', level: match[1].length, inline: parseInline(match[2]) });
                open = null;
            } else if ((match = line.match(/^\s*([-*+]|\d{1,3}[.)])\s+(.*)$/))) {
                const ordered = /\d/.test(match[1]);
                if (!(open && open.type === 'list' && open.ordered === ordered)) {
                    open = { type: 'list', ordered, items: [] };
                    if (ordered) open.start = parseInt(match[1], 10);
                    blocks.push(open);
                }
                open.items.push(parseInline(match[2]));
            } else if ((match = line.match(/^\s*>\s?(.*)$/))) {
                if (!(open && open.type === 'quote')) {
                    open = { type: 'quote', lines: [] };
                    blocks.push(open);
                }
                open.lines.push(parseInline(match[1]));
            } else {
                if (!(open && open.type === 'paragraph')) {
                    open = { type: 'paragraph', lines: [] };
                    blocks.push(open);
                }
                open.lines.push(parseInline(line.trim()));
            }
        });
        return blocks;
    }

    function appendInline(doc, parent, parts) {
        parts.forEach((part) => {
            if (part.type === 'text') {
                parent.appendChild(doc.createTextNode(part.text));
                return;
            }
            const element = doc.createElement(part.type === 'code' ? 'code' : 'strong');
            element.appendChild(doc.createTextNode(part.text));
            parent.appendChild(element);
        });
    }

    function appendLines(doc, parent, lines) {
        lines.forEach((parts, index) => {
            if (index) parent.appendChild(doc.createElement('br'));
            appendInline(doc, parent, parts);
        });
    }

    function renderMarkdown(doc, container, text) {
        parseMarkdown(text).forEach((block) => {
            let element;
            if (block.type === 'heading') {
                // Chat bubbles are small: every level renders as a compact heading.
                element = doc.createElement('h4');
                appendInline(doc, element, block.inline);
            } else if (block.type === 'list') {
                element = doc.createElement(block.ordered ? 'ol' : 'ul');
                if (block.ordered && block.start !== 1) element.setAttribute('start', String(block.start));
                block.items.forEach((parts) => {
                    const item = doc.createElement('li');
                    appendInline(doc, item, parts);
                    element.appendChild(item);
                });
            } else {
                element = doc.createElement(block.type === 'quote' ? 'blockquote' : 'p');
                appendLines(doc, element, block.lines);
            }
            container.appendChild(element);
        });
        return container;
    }

    const CHART_COLORS = ['#176b4a', '#c98a12', '#2f6fb3', '#b42318', '#7a3fa0', '#4a7a2c'];

    function chartConfig(spec) {
        const single = spec.datasets.length === 1;
        const percent = spec.unit === '%';
        return {
            type: spec.type,
            data: {
                labels: spec.labels,
                datasets: spec.datasets.map((dataset, index) => ({
                    label: dataset.label,
                    data: dataset.data,
                    backgroundColor: single
                        ? spec.labels.map((_, labelIndex) => CHART_COLORS[labelIndex % CHART_COLORS.length])
                        : CHART_COLORS[index % CHART_COLORS.length],
                    borderColor: CHART_COLORS[index % CHART_COLORS.length],
                    tension: 0.2,
                })),
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: false,
                plugins: {
                    title: { display: true, text: spec.title },
                    legend: { display: !single, labels: { boxWidth: 12 } },
                    tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label}：${ctx.formattedValue} ${spec.unit}` } },
                },
                scales: {
                    x: { stacked: Boolean(spec.stacked) },
                    y: {
                        stacked: Boolean(spec.stacked), beginAtZero: true,
                        min: 0,
                        ...(percent ? { max: spec.y_max ?? 100 } : {}),
                        title: { display: true, text: spec.unit },
                    },
                },
            },
        };
    }

    const api = { parseInline, parseMarkdown, renderMarkdown, chartConfig };
    root.AssistantRender = api;
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
}(typeof window !== 'undefined' ? window : globalThis));
