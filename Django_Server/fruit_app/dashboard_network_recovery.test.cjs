const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync(`${__dirname}/static/fruit_app/js/dashboard.js`, 'utf8');
const start = source.indexOf('    function autoRunGuidanceText(data) {');
const end = source.indexOf('    // The operation bar', start);
assert.ok(start >= 0 && end > start);
const context = vm.createContext({reasonText: reason => reason, timingInputsDirty: false});
vm.runInContext(source.slice(start, end), context);
const guidance = context.autoRunGuidanceText;

assert.match(guidance({hardware_mode: 'hardware', network_recovery: {},
    network_recovery_confirm_disabled_reason: null, auto_run_disabled_reason: 'camera_not_ready'}), /確認人工安全復原/);
assert.match(guidance({hardware_mode: 'hardware', network_recovery: {},
    network_recovery_confirm_disabled_reason: 'esp32_offline', auto_run_disabled_reason: 'system_not_idle'}), /esp32_offline/);
assert.match(guidance({hardware_mode: 'hardware', network_recovery: null,
    auto_run_disabled_reason: 'camera_not_ready'}), /camera_not_ready/);
assert.match(guidance({hardware_mode: 'photo_only', network_recovery: {},
    network_recovery_confirm_disabled_reason: 'network_recovery_not_required', photo_capture_disabled_reason: null}), /按一次自動拍三張/);
assert.match(guidance({hardware_mode: 'photo_only', network_recovery: {},
    network_recovery_confirm_disabled_reason: 'temp_fruit_exists', photo_capture_disabled_reason: null}), /temp_fruit_exists/);
console.log('PASS: 5 independent recovery / production guidance cases.');
