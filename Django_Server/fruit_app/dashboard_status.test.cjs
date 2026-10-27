const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const elements = new Map();
function element(id) {
    if (!elements.has(id)) elements.set(id, {
        textContent: '', value: '', dataset: {}, hidden: false, disabled: false,
        classList: { add() {}, remove() {}, toggle() {} },
        setAttribute(name, value) { this[name] = value; },
        getAttribute(name) { return this[name]; },
        removeAttribute(name) { delete this[name]; },
        replaceChildren(...children) { this.children = children; },
        append() {}, appendChild() {}, add() {},
        querySelector: selector => element(`${id} ${selector}`),
        querySelectorAll: () => [],
        addEventListener(event, handler) { this[event] = handler; },
    });
    return elements.get(id);
}
// Use the same Chinese descriptions supplied by Django's template.
const reasons = Object.fromEntries(
    [...fs.readFileSync(`${__dirname}/reason_text.py`, 'utf8')
        .matchAll(/^    '([^']+)': '([^']*)',/gm)].map(match => [match[1], match[2]]),
);
element('reason-text').textContent = JSON.stringify(reasons);
element('status-text').textContent = '{}';
let timerId = 0;
const timers = new Map();
const context = vm.createContext({
    document: {
        getElementById: element,
        createElement: tag => element(Symbol(tag)),
        createTextNode: text => ({ textContent: text }),
        querySelectorAll: () => [], addEventListener() {},
    },
    window: {
        localStorage: { getItem() { return null; }, setItem() {} },
        setTimeout(handler, delay) { timers.set(++timerId, { handler, delay }); return timerId; },
        clearTimeout(id) { timers.delete(id); },
    },
    Option: function(text, value) { return { textContent: text, value }; },
});
vm.runInContext(fs.readFileSync(`${__dirname}/static/fruit_app/js/dashboard.js`, 'utf8'), context);
const message = element('message');
function render(overrides = {}) {
    context.state = { hardware_mode: 'hardware', work_mode: 'detection', status: 'idle', ...overrides };
    vm.runInContext('renderState(state)', context);
}
function feedback(text) {
    context.feedback = text;
    vm.runInContext('setMessage(feedback)', context);
}
function expireFeedback() {
    for (const [id, timer] of [...timers]) {
        if (timer.delay !== 5000) continue;
        timers.delete(id);
        timer.handler();
    }
}

render();
feedback('已確認人工復原，等待 ESP32 安全歸位與清鎖交握。');
assert.match(message.textContent, /已確認人工復原/);
const timeout = {
    status: 'waiting_fruit', auto_run_enabled: false,
    last_error_reason: 'feeder_max_run_timeout',
    auto_run_recovery_reason: 'feeder_max_run_timeout',
    operator_alert: {
        reason: 'feeder_max_run_timeout',
        instruction: '已自動停止拍攝；請檢查送料筒、出口、卡料與感測區域。',
        feeder_elapsed_ms: 5012, feeder_max_run_ms: 5000,
        feeder_stop_reason: 'max_run_timeout',
    },
};
render(timeout);
assert.equal(message.textContent, '送料機構運轉超時；桶內可能已空料。');
assert.equal(message.dataset.state, 'error');
const alertText = message.textContent;
render({ ...timeout, can_start_auto_run: true, can_finish_feeder_wait: true,
    auto_run_start_disabled_reason: null, auto_run_disabled_reason: 'system_not_idle' });
assert.equal(element('btn-auto-run').disabled, false);
assert.equal(element('btn-finish-feeder-wait').hidden, false);
assert.equal(element('btn-finish-feeder-wait').disabled, false);
assert.match(vm.runInContext('autoRunGuidanceText(state)', context), /重新送料.*結束送料等待/);
const alertTimer = timerId;
feedback('已顯示維護資訊');
assert.equal(message.textContent, alertText, 'action feedback must not hide the alert');
render(timeout);
assert.equal(timerId, alertTimer, 'polling must not restart the five-second timer');
assert.equal(timers.get(alertTimer).delay, 5000);
expireFeedback();
assert.equal(message.textContent, '', 'runtime alert must disappear after five seconds');
assert.equal(message.dataset.state, '');
render(timeout);
assert.equal(message.textContent, '', 'polling must not redisplay the same unresolved alert');
render({ ...timeout, operator_alert: { ...timeout.operator_alert, command_id: 42 } });
assert.equal(message.textContent, alertText, 'a new command with the same error gets a new alert');
expireFeedback();
render();
render(timeout);
assert.equal(message.textContent, alertText, 'a resolved error can be shown when it happens again');

render({ last_error_reason: 'feeder_sensor_unavailable', operator_alert: {
    reason: 'feeder_sensor_unavailable', instruction: '請檢查 HC-SR04 接線。',
} });
assert.match(message.textContent, /沒有有效回音/);
assert.doesNotMatch(message.textContent, /空料|5012|5000/);
render({ auto_run_recovery_reason: 'feeder_max_run_timeout' });
assert.equal(message.textContent, alertText, 'recovery reason is shown without an alert payload');
render({ last_error_reason: 'unknown_fixture_reason' });
assert.equal(message.textContent, '其他狀態，詳見診斷區');

render({ auto_run_enabled: true, status: 'waiting_feeder' });
assert.equal(message.textContent, '', 'resolved alert and stale action feedback are cleared');
assert.equal(message.dataset.state, '');
feedback('自動運轉已開始');
render({ auto_run_enabled: true, status: 'waiting_feeder' });
assert.equal(message.textContent, '自動運轉已開始');
expireFeedback();
assert.equal(message.textContent, '', 'normal action feedback still expires');
render({ auto_run_disabled_reason: 'camera_not_ready', operator_alert: { reason: 'camera_not_ready' } });
assert.equal(message.textContent, '', 'ordinary readiness hints stay in the operation bar');
console.log('PASS: concise alerts, five-second expiry, polling deduplication, new commands and recovery.');

async function testFinishWaitAction() {
    let approved = false;
    const requests = [];
    context.confirm = text => {
        assert.match(text, /送料已停止.*不會送料/);
        return approved;
    };
    context.fetch = async (url, options) => {
        requests.push({ url, data: JSON.parse(options.body) });
        return { ok: true, json: async () => ({
            hardware_mode: 'hardware', work_mode: 'collection', network_recovery_required: true,
            network_recovery_confirm_disabled_reason: 'network_recovery_in_progress',
            network_recovery: { reason: 'feeder_wait_cancelled', resume_auto_run: false },
        }) };
    };
    render({ ...timeout, work_mode: 'collection', can_finish_feeder_wait: true });
    vm.runInContext('lastState = state', context);
    await context.finishFeederWait();
    assert.equal(requests.length, 0, 'cancel must not send a request');
    approved = true;
    await context.finishFeederWait();
    assert.equal(requests.length, 1);
    assert.equal(requests[0].url, '/api/feeder/finish_wait/');
    assert.deepEqual(requests[0].data, { dataset_mode: 'collection', recovery_confirmed: true });
    assert.equal(element('btn-finish-feeder-wait').hidden, true);
    assert.equal(element('btn-auto-run').disabled, true);
    assert.match(vm.runInContext('autoRunGuidanceText(lastState)', context), /正在結束送料等待.*不會自動送料/);
    console.log('PASS: finish-wait confirmation, request, handshake lock and consistent start guidance.');
}
testFinishWaitAction().catch(error => { console.error(error); process.exitCode = 1; });
