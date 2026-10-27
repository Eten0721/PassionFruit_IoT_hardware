const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(`${__dirname}/static/fruit_app/js/dashboard.js`, 'utf8');
function extract(start, end) {
    const from = source.indexOf(start);
    const to = source.indexOf(end, from);
    assert.ok(from >= 0 && to > from);
    return source.slice(from, to);
}
const context = vm.createContext({
    manualGradePanel: {}, manualGradeButtons: [{}, {}, {}, {}],
    manualAbandonButton: {}, manualSkipButton: {}, manualGradeGuidance: {},
    controlActionInFlight: null, reasonText: reason => reason,
    setTextIfChanged: (element, text) => { element.textContent = text; },
});
vm.runInContext(extract('    function renderManualGrade(', '    function actionGuidanceItems('), context);
context.state = { hardware_mode: 'hardware', can_discard: true, can_manual_grade: true,
    detection_result: { classification: { status: 'needs_review' } } };
vm.runInContext("renderManualGrade(state, { state: 'review' })", context);
assert.equal(context.manualSkipButton.hidden, false);
assert.equal(context.manualSkipButton.disabled, false);
assert.equal(context.manualAbandonButton.hidden, true);
context.state.can_manual_grade = false;
context.state.manual_grade_disabled_reason = 'esp32_offline';
vm.runInContext("renderManualGrade(state, { state: 'review' })", context);
assert.equal(context.manualGradeButtons.every(button => button.disabled), true);
assert.equal(context.manualSkipButton.disabled, false);
context.controlActionInFlight = 'discard';
vm.runInContext("renderManualGrade(state, { state: 'review' })", context);
assert.equal(context.manualSkipButton.disabled, true);
context.controlActionInFlight = null;
context.state.hardware_mode = 'photo_only';
vm.runInContext("renderManualGrade(state, { state: 'review' })", context);
assert.equal(context.manualSkipButton.hidden, true);
assert.equal(context.manualAbandonButton.hidden, false);

vm.runInContext(extract('    function waitingForManualGrade(', '    // The operation bar'), context);
assert.match(context.autoRunGuidanceText({work_mode: 'detection', hardware_mode: 'hardware',
    active_fruit_id: 'fruit_042', auto_run_finishing: true,
    detection_result: {classification: {status: 'needs_review'}}}), /等待人工判級.*跳過當顆/);

async function testSkipRequest() {
    const requests = [];
    let prompt;
    Object.assign(context, {
        lastState: {work_mode: 'detection', hardware_mode: 'hardware',
            active_fruit_id: 'fruit_042', capture_token: 123, gate3_manual_removal_required: true},
        discardButton: {}, confirm: text => { prompt = text; return true; },
        beginControlAction: () => true, endControlAction() {},
        releaseImagesBeforeFileOperation: async () => {},
        postJson: async (url, payload) => { requests.push({url, payload}); return {ok: true}; },
        renderState() {}, setMessage() {}, refreshState: async () => {},
        discardResultText: () => '', warningText: () => '',
    });
    vm.runInContext(extract('    async function discardCurrent()', '    async function resetDataset()'), context);
    await context.discardCurrent();
    assert.match(prompt, /斷電|切斷伺服電源/);
    assert.match(prompt, /確認安全復原/);
    assert.equal(requests.length, 1);
    assert.equal(requests[0].url, '/api/discard/');
    assert.equal(requests[0].payload.fruit_id, 'fruit_042');
    assert.equal(requests[0].payload.capture_token, 123);
    context.lastState = {active_fruit_id: 'fruit_043'};
    context.confirm = () => false;
    await context.discardCurrent();
    assert.equal(requests.length, 1);
    console.log('PASS: hardware skip visibility, offline cleanup, in-flight guard, manual guidance and confirmed discard request.');
}
testSkipRequest().catch(error => { console.error(error); process.exitCode = 1; });
