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
        append() {},
        appendChild() {},
        querySelector: (selector) => element(`${id} ${selector}`),
        querySelectorAll: () => [],
        addEventListener(event, handler) { this[event] = handler; },
    });
    return elements.get(id);
}
const buttons = ['high', 'medium', 'low', 'processing'].map(element);
element('reason-text').textContent = JSON.stringify({
    fixture_reason: '頁面提供的原因',
    line_credentials_missing: '測試缺檔說明',
    line_credentials_unreadable: '測試讀取說明',
    line_credentials_invalid: '測試格式說明',
    line_credentials_incomplete: '測試缺欄位說明',
});
const dashboardScript = fs.readFileSync(`${__dirname}/static/fruit_app/js/dashboard.js`, 'utf8');
function loadDashboard(storage = { getItem() { return null; }, setItem() {} }) {
    const context = vm.createContext({
        document: {
            getElementById: element,
            createElement: (tag) => element(Symbol(tag)),
            createTextNode: (text) => ({ textContent: text }),
            querySelectorAll: (selector) => selector === '.manual-grade-button' ? buttons : [],
            addEventListener() {},
        },
        window: {
            localStorage: storage, timers: [], selectionCleared: false,
            setTimeout(handler, delay) { this.timers.push({ handler, delay }); }, clearTimeout() {},
            getSelection() { return { removeAllRanges: () => { this.selectionCleared = true; } }; },
        },
        Option: function(text, value) { return { textContent: text, value }; },
    });
    vm.runInContext(dashboardScript, context);
    return context;
}

const savedSettings = new Map();
const storage = {
    getItem: (key) => savedSettings.get(key) ?? null,
    setItem: (key, value) => savedSettings.set(key, value),
};
let diagnosticsContext = loadDashboard(storage);
assert.equal(element('diagnostics').hidden, true);
element('dashboard-title').dblclick();
assert.equal(element('diagnostics').hidden, false);
assert.equal(element('message').textContent, '已顯示診斷資訊');
assert.equal(diagnosticsContext.window.selectionCleared, true);
assert.equal(diagnosticsContext.window.timers.at(-1).delay, 10000);
diagnosticsContext.window.timers.at(-1).handler();
assert.equal(element('message').textContent, '');
diagnosticsContext = loadDashboard(storage);
assert.equal(element('diagnostics').hidden, false);
element('dashboard-title').dblclick();
assert.equal(element('diagnostics').hidden, true);
assert.equal(element('message').textContent, '已隱藏診斷資訊');
loadDashboard(storage);
assert.equal(element('diagnostics').hidden, true);
element('diagnostics').hidden = false;
loadDashboard({
    getItem() { throw new Error('storage blocked'); },
    setItem() { throw new Error('storage blocked'); },
});
assert.equal(element('diagnostics').hidden, true);
assert.doesNotThrow(() => element('dashboard-title').dblclick());
assert.equal(element('diagnostics').hidden, false);
assert.equal(element('message').textContent, '已顯示診斷資訊');
assert.doesNotThrow(() => element('dashboard-title').dblclick());
assert.equal(element('diagnostics').hidden, true);
console.log('PASS: hidden diagnostics toggle, reload persistence, selection cleanup and blocked storage.');

const context = loadDashboard();
assert.equal(vm.runInContext("reasonText('fixture_reason')", context), '頁面提供的原因（fixture_reason）');
assert.equal(vm.runInContext("reasonText('unknown_reason')", context), 'unknown_reason');
assert.equal(vm.runInContext("reasonText('')", context), '');
element('message').textContent = '';

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

context.dashboardState = {
    work_mode: 'detection', hardware_mode: 'photo_only', status: 'idle',
    dataset_path: 'D:/Dataset/detection', legacy_dataset_path: 'D:/Dataset',
    legacy_detection_path: 'D:/detection_results',
};
function renderDashboard(overrides = {}) {
    Object.assign(context.dashboardState, overrides);
    vm.runInContext('renderState(dashboardState)', context);
    return element('network-setup-summary-status');
}
let badge = renderDashboard();
assert.equal(badge.textContent, '未連線');
assert.equal(badge.dataset.state, 'error');
assert.equal(element('dataset-scope').textContent, '目前模式：自動檢測');
assert.equal(element('legacy-dataset-location').textContent, '');
badge = renderDashboard({ esp32_online: true });
assert.equal(badge.textContent, '已連線');
assert.equal(badge.dataset.state, 'ready');
badge = renderDashboard({ esp32_online: false, network: { esp32: { online: true } } });
assert.equal(badge.textContent, '已連線（診斷）');
assert.equal(badge.dataset.state, 'ready');
badge = renderDashboard({ network: { esp32: { online: false } }, last_error_reason: 'motor_failed' });
assert.equal(badge.textContent, '未連線');
assert.equal(badge.dataset.state, 'error');
// The reason moved from the removed #error-reason line to the state badge tooltip.
assert.equal(element('state-badge').title, 'motor_failed');
renderDashboard({ work_mode: 'collection', last_error_reason: '' });
assert.equal(element('dataset-scope').textContent, '目前模式：蒐集');
assert.equal(element('state-badge').title, '');
vm.runInContext("setStreamStatus('RTC 預覽無法建立：rtc_failed；仍可正式拍攝。')", context);
assert.equal(element('stream-status').textContent, 'RTC 預覽無法建立：rtc_failed；仍可正式拍攝。');
console.log('PASS: Dashboard connection badges, path-free modes and visible failure reasons.');

function renderNotification(notification) {
    context.notificationState = notification;
    vm.runInContext('renderNotification(notificationState)', context);
    return element('notification-status').textContent;
}
const counts = { pending: 0, sent: 0, failed: 0, uncertain: 0 };
let notificationText = renderNotification({
    mode: 'photo_only', enabled: false, configured: true, counts, paused: 0, current: null,
});
assert.equal(element('notification-summary-status').textContent, 'LINE 通知：關閉');
assert.equal(element('btn-notification-toggle').textContent, '開啟通知');
assert.equal(element('notification-mode-label').textContent, '純拍攝：訊息加「測試」');
assert.match(notificationText, /關閉期間的新結果不排送/);
notificationText = renderNotification({
    mode: 'hardware', enabled: false, configured: true, counts: { ...counts, pending: 2 },
    paused: 2, current: { status: 'pending', reason: null },
});
assert.equal(element('notification-summary-status').textContent, 'LINE 通知：關閉・暫停 2');
assert.match(notificationText, /本輪：待送/);
notificationText = renderNotification({
    mode: 'hardware', enabled: true, configured: false,
    config_reason: 'line_credentials_missing', config_missing_fields: [],
    counts: { ...counts, pending: 1, uncertain: 1 }, paused: 0,
    current: { status: 'uncertain', reason: '無法確認是否已送達' },
    last_error: 'LINE 連線中斷，送達狀態待確認',
});
assert.equal(element('notification-summary-status').textContent, 'LINE 通知：開啟・待送 1');
assert.equal(element('notification-summary-status').dataset.state, 'error');
assert.equal(element('btn-notification-toggle').textContent, '關閉通知');
assert.equal(element('btn-notification-toggle')['aria-pressed'], 'true');
assert.match(notificationText, /尚未完成設定：測試缺檔說明（line_credentials_missing）/);
assert.match(notificationText, /本輪：送達不明，請到群組確認（無法確認是否已送達）/);
assert.match(notificationText, /最近錯誤：LINE 連線中斷/);
for (const [reason, description] of Object.entries(JSON.parse(element('reason-text').textContent))
    .filter(([reason]) => reason.startsWith('line_credentials_'))) {
    const missing = reason === 'line_credentials_incomplete' ? ['line_group_id', 'cloudinary_api_key'] : [];
    const configuration = { configured: false, config_reason: reason, config_missing_fields: missing };
    const expected = `${description}（${reason}）` + (missing.length ? '；缺少欄位：line_group_id、cloudinary_api_key' : '');
    notificationText = renderNotification({ mode: 'hardware', counts, ...configuration });
    assert.ok(notificationText.includes(`尚未完成設定：${expected}`));
    context.trendState = { settings: { enabled: false, categories: {} }, line: configuration };
    vm.runInContext('renderTrendAlerts(trendState)', context);
    assert.ok(element('trend-alert-status').textContent.includes(`LINE 尚未完成設定：${expected}`));
    assert.doesNotMatch(notificationText + element('trend-alert-status').textContent,
                        /notification_credentials\.json|line-token-SECRET|C0123456789abcdef/);
}
renderNotification({ mode: 'hardware', enabled: false, error: '通知待送檔無法讀取：bad json' });
assert.equal(element('btn-notification-toggle').disabled, true);
assert.equal(element('notification-status').textContent, '通知待送檔無法讀取：bad json');
console.log('PASS: LINE notification switch, paused queue, setup reason and unknown delivery text.');

async function testDatasetFolderFeedback() {
    context.URLSearchParams = URLSearchParams;
    vm.runInContext("lastState = { work_mode: 'detection' };", context);
    let requestedUrl;
    let response = { ok: true, json: async () => ({ opened: true, path: 'D:/Dataset/detection' }) };
    context.fetch = async (url) => { requestedUrl = url; return response; };
    await element('btn-open-folder').click();
    assert.equal(requestedUrl, '/api/open_dataset_folder/?dataset_mode=detection');
    assert.equal(element('message').textContent, '');

    response = { ok: false, json: async () => ({ message: 'folder_open_failed' }) };
    await element('btn-open-folder').click();
    assert.equal(element('message').textContent, '開啟資料夾失敗：folder_open_failed');
    response = { ok: true, json: async () => ({ opened: true }) };
    await element('btn-open-folder').click();
    assert.equal(element('message').textContent, '開啟資料夾失敗：folder_open_failed');

    response = { ok: true, json: async () => ({ opened: false, path: 'D:/Dataset/detection' }) };
    await element('btn-open-folder').click();
    assert.equal(element('message').textContent, '資料夾位置：D:/Dataset/detection');
    console.log('PASS: silent folder success preserves errors and folder fallback reminders.');
}
testDatasetFolderFeedback().catch((error) => { console.error(error); process.exitCode = 1; });
