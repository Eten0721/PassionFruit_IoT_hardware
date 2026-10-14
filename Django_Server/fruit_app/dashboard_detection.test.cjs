const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const elements = new Map();
function element(id) {
    if (!elements.has(id)) elements.set(id, {
        textContent: '', dataset: {}, hidden: false, disabled: false,
        addEventListener() {},
    });
    return elements.get(id);
}
const buttons = ['high', 'medium', 'low', 'processing'].map(element);
const context = vm.createContext({
    document: {
        getElementById: element,
        querySelectorAll: (selector) => selector === '.manual-grade-button' ? buttons : [],
        addEventListener() {},
    },
    window: { setTimeout() {}, clearTimeout() {} },
});
vm.runInContext(fs.readFileSync(`${__dirname}/static/fruit_app/js/dashboard.js`, 'utf8'), context);

function render(classification, sorting, storage = 'saved') {
    context.state = {
        hardware_mode: sorting.status === 'skipped' ? 'photo_only' : 'hardware',
        can_manual_grade: classification.status === 'needs_review',
        can_manual_abandon: sorting.status === 'skipped',
        detection_result: { classification, sorting, storage: { status: storage },
                            complete: classification.source === 'model', images: {} },
    };
    vm.runInContext('renderGradeCard(state)', context);
    return element('detection-flow-status').textContent;
}
const grade = { status: 'graded', source: 'model', label: '上等' };
assert.equal(render(grade, { status: 'pending' }), '已判級 → 已保存 → 分槽中：等待 ESP32');
assert.equal(render(grade, { status: 'running' }), '已判級 → 已保存 → 分槽中：控制流程執行中');
assert.equal(render(grade, { status: 'completed' }), '已判級 → 已保存 → 分槽完成：控制流程已回報');
assert.match(render(grade, { status: 'failed', recovery_required: true, reason: 'motor_failed' }),
             /已保存 → 分槽失敗 → 需復原：motor_failed/);
assert.match(render(grade, { status: 'timeout', recovery_required: true, reason: 'classifier_timeout' }),
             /分槽失敗：回報逾時 → 需復原/);
assert.match(render(grade, { status: 'not_started' }, 'failed'), /已判級 → 保存失敗 → 尚未分槽/);
assert.equal(render(grade, { status: 'skipped' }), '已判級 → 已保存 → 純拍攝略過分槽');
assert.match(render({ ...grade, source: 'manual' }, { status: 'pending' }), /已判級 → 已保存 → 分槽中/);
assert.equal(element('detection-grade').textContent, '上等（人工判定）');
render({ status: 'needs_review', reason: 'roi_not_detected' }, { status: 'not_started' });
assert.equal(element('manual-grade-panel').hidden, false);
assert.equal(element('btn-manual-abandon').hidden, true);
assert.equal(buttons.every((button) => !button.disabled), true);
render({ status: 'needs_review' }, { status: 'skipped' });
assert.equal(element('btn-manual-abandon').hidden, false);
assert.equal(element('btn-manual-abandon').disabled, false);
console.log('PASS: 11 Dashboard classification, storage, sorting and manual takeover states.');
