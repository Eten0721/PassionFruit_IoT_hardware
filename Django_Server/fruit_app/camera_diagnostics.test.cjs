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
    localStream = { getVideoTracks: () => [{ label: '後置超廣角相機', readyState: 'live', enabled: true, muted: false }] };
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
assert.deepEqual(snapshot.videoTracks, [{ label: '後置超廣角相機', readyState: 'live', enabled: true, muted: false }]);
assert.equal(snapshot.rtcConnectionState, 'failed');
assert.equal(snapshot.captureStatus, '照片已上傳');

vm.runInContext('lastLiveFrameAtMs = 1000; localStream = null;', context);
element('btn-camera-diagnostics').click();
snapshot = JSON.parse(element('camera-diagnostics-output').value);
assert.equal(snapshot.lastLiveFrameAgeMs, 4000);
assert.deepEqual(snapshot.videoTracks, []);

function fakeStream(deviceId) {
    const track = { label: deviceId, stopped: false, stop() { this.stopped = true; }, getSettings: () => ({ deviceId }) };
    return { track, getTracks: () => [track], getVideoTracks: () => [track] };
}
const requests = [];
let failExact = false;
context.navigator.mediaDevices = {
    async getUserMedia(constraints) {
        requests.push(constraints.video);
        const exactId = constraints.video.deviceId?.exact;
        if (exactId && failExact) throw new Error('OverconstrainedError');
        return fakeStream(exactId || 'back-main');
    },
};
context.console = { warn() {} };

// iPhone 17 Pro 的前鏡頭標籤也含「超廣角」，預設不得選到前鏡頭。
const iphoneCameras = [
    { kind: 'videoinput', deviceId: 'front-ultra', label: '前置超廣角相機' },
    { kind: 'videoinput', deviceId: 'back-main', label: '後置相機' },
    { kind: 'videoinput', deviceId: 'back-ultra', label: '後置超廣角相機' },
];
const pickDefaultCamera = vm.runInContext('pickDefaultCamera', context);
assert.equal(pickDefaultCamera(iphoneCameras, '').deviceId, 'back-ultra');
assert.equal(pickDefaultCamera([
    { deviceId: 'f', label: 'Front Ultra Wide Camera' },
    { deviceId: 'b', label: 'Back Ultra Wide Camera' },
], '').deviceId, 'b');
assert.equal(pickDefaultCamera(iphoneCameras, 'back-main').deviceId, 'back-main');
assert.equal(pickDefaultCamera(iphoneCameras, 'stale-id').deviceId, 'back-ultra');
assert.equal(pickDefaultCamera([{ deviceId: 'main', label: 'Back Camera' }], ''), null);

(async () => {
    const switchStream = vm.runInContext('switchStream', context);
    const mainStream = fakeStream('back-main');
    const switched = await switchStream(mainStream, 'back-ultra');
    assert.equal(switched.track.label, 'back-ultra');
    assert.equal(mainStream.track.stopped, true);
    assert.equal(requests.at(-1).deviceId.exact, 'back-ultra');

    const sameStream = fakeStream('back-ultra');
    assert.equal(await switchStream(sameStream, 'back-ultra'), sameStream);
    assert.equal(await switchStream(sameStream, undefined), sameStream);

    failExact = true;
    const fallback = await switchStream(fakeStream('back-ultra'), 'front-ultra');
    assert.equal(fallback.track.label, 'back-main');
    assert.equal(requests.at(-1).facingMode.ideal, 'environment');
    console.log('Camera diagnostics: unavailable, live camera with failed RTC, stale frame, and lens selection checks passed.');
})().catch((error) => { console.error(error); process.exit(1); });
