const assert = require('node:assert/strict');
const { parseMarkdown, renderMarkdown, chartConfig } = require('./static/fruit_app/js/assistant_render.js');

// Minimal DOM: elements record children; any innerHTML write fails the test.
function fakeDocument() {
    function node(tag) {
        const element = {
            tag, children: [], attributes: {},
            appendChild(child) { this.children.push(child); return child; },
            setAttribute(name, value) { this.attributes[name] = value; },
        };
        Object.defineProperty(element, 'innerHTML', {
            set() { throw new Error('innerHTML must not be used'); },
        });
        return element;
    }
    return {
        createElement: node,
        createTextNode: (text) => ({ tag: '#text', text }),
    };
}

function shape(element) {
    if (element.tag === '#text') return element.text;
    const children = element.children.map(shape);
    return { [element.tag]: children.length === 1 && typeof children[0] === 'string' ? children[0] : children };
}

// Block structure.
assert.deepEqual(parseMarkdown('### 結果\n**炭疽病**最多\n第二行\n\n* 甲\n- 乙\n1. 一\n2) 二\n> 引用\n> 續行'), [
    { type: 'heading', level: 3, inline: [{ type: 'text', text: '結果' }] },
    { type: 'paragraph', lines: [
        [{ type: 'strong', text: '炭疽病' }, { type: 'text', text: '最多' }],
        [{ type: 'text', text: '第二行' }],
    ] },
    { type: 'list', ordered: false, items: [[{ type: 'text', text: '甲' }], [{ type: 'text', text: '乙' }]] },
    { type: 'list', ordered: true, start: 1, items: [[{ type: 'text', text: '一' }], [{ type: 'text', text: '二' }]] },
    { type: 'quote', lines: [[{ type: 'text', text: '引用' }], [{ type: 'text', text: '續行' }]] },
]);

// Rendering uses elements and text nodes only.
const doc = fakeDocument();
const container = renderMarkdown(doc, doc.createElement('div'),
    '## 建議\n用 `manage.py` 啟動，**務必**備份\n\n3. 第三\n4. 第四\n> 注意');
assert.deepEqual(shape(container), { div: [
    { h4: '建議' },
    { p: ['用 ', { code: 'manage.py' }, ' 啟動，', { strong: '務必' }, '備份'] },
    { ol: [{ li: '第三' }, { li: '第四' }] },
    { blockquote: '注意' },
] });
assert.equal(container.children[2].attributes.start, '3');

// HTML in model text stays literal text; nothing is parsed or executed.
const hostile = renderMarkdown(doc, doc.createElement('div'),
    '<img src=x onerror=alert(1)> **<b>粗</b>**\n- <script>x</script>');
assert.deepEqual(shape(hostile), { div: [
    { p: ['<img src=x onerror=alert(1)> ', { strong: '<b>粗</b>' }] },
    { ul: [{ li: '<script>x</script>' }] },
] });
assert.deepEqual(parseMarkdown(null), []);

// Percentage charts share a fixed 0-100 axis; count charts start at zero only.
const compare = chartConfig({
    type: 'bar', title: '瑕疵出現比例比較（上等、加工）', unit: '%', stacked: false, y_max: 100,
    labels: ['色澤異常', '皺褶'], datasets: [{ label: '上等（5 顆）', data: [20, 0] },
        { label: '加工（4 顆）', data: [100, 25] }],
});
assert.equal(compare.options.scales.y.max, 100);
assert.equal(compare.options.scales.y.min, 0);
assert.equal(compare.options.plugins.legend.display, true);
assert.deepEqual(compare.data.datasets.map((d) => d.backgroundColor), ['#176b4a', '#c98a12']);
const single = chartConfig({ type: 'bar', title: '瑕疵出現比例', unit: '%', stacked: false,
    labels: ['擦傷'], datasets: [{ label: '佔已判級果實', data: [35] }] });
assert.equal(single.options.scales.y.max, 100);
const counts = chartConfig({ type: 'bar', title: '四級分布', unit: '顆', stacked: false,
    labels: ['上等'], datasets: [{ label: '果實數', data: [3] }] });
assert.equal(counts.options.scales.y.max, undefined);

console.log('assistant_render tests passed');
