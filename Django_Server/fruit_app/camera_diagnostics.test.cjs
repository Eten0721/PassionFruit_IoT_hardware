const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const elements = new Map();
function element(id) {
    if (!elements.has(id)) elements.set(id, {
        textContent: '', value: '', videoWidth: 0, videoHeight: 0,
        addEventListener(event, handler) { this[event] = handler; },
    });
    return elements.get(id);
}
const context = vm.createContext({
    document: { getElementById: element, addEventListener() {} },
    window: {
        isSecureContext: false,
        location: { origin: 'https://192.168.50.10:8000', pathname: '/camera/' },
        setTimeout() {}, clearTimeout() {},
    },
    navigator: { userAgent: 'Test iPhone', onLine: true },
    performance: { now: () => 5000 },
});
vm.runInContext(fs.readFileSync(`${__dirname}/static/fruit_app/js/camera.js`, 'utf8'), context);

// Exercise the same refresh action available before camera permission is granted.
element('btn-camera-diagnostics').click();
let snapshot = JSON.parse(element('camera-diagnostics-output').value);
assert.equal(snapshot.isSecureContext, false);
assert.equal(snapshot.getUserMediaAvailable, false);
assert.equal(snapshot.page, 'https://192.168.50.10:8000/camera/');
assert.deepEqual(snapshot.videoTracks, []);
assert.equal(snapshot.lastLiveFrameAgeMs, null);
assert.equal(snapshot.rtcConnectionState, null);

context.window.isSecureContext = true;
context.navigator.mediaDevices = { getUserMedia() { throw new Error('Must not request permission'); } };
vm.runInContext(`
    localStream = { getVideoTracks: () => [{ readyState: 'live', enabled: true, muted: false }] };
    localVideo.videoWidth = 1920;
    localVideo.videoHeight = 1080;
    lastLiveFrameAtMs = 4800;
    peerConnection = { connectionState: 'failed', iceConnectionState: 'failed' };
    statusEl.textContent = '照片已上傳';
`, context);
element('btn-camera-diagnostics').click();
snapshot = JSON.parse(element('camera-diagnostics-output').value);
assert.equal(snapshot.isSecureContext, true);
assert.equal(snapshot.getUserMediaAvailable, true);
assert.equal(snapshot.lastLiveFrameAgeMs, 200);
assert.deepEqual(snapshot.videoSize, [1920, 1080]);
assert.deepEqual(snapshot.videoTracks, [{ readyState: 'live', enabled: true, muted: false }]);
assert.equal(snapshot.rtcConnectionState, 'failed');
assert.equal(snapshot.captureStatus, '照片已上傳');

vm.runInContext('lastLiveFrameAtMs = 1000; localStream = null;', context);
element('btn-camera-diagnostics').click();
snapshot = JSON.parse(element('camera-diagnostics-output').value);
assert.equal(snapshot.lastLiveFrameAgeMs, 4000);
assert.deepEqual(snapshot.videoTracks, []);
console.log('Camera diagnostics: unavailable, live camera with failed RTC, and stale frame checks passed.');
