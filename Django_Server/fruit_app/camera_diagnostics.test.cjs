const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { test } = require('node:test');

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

function reportingCamera({ cameraError = null, failFirst = false, reportError = null, reportOk = true } = {}) {
    const nodes = new Map();
    const reports = [];
    const warnings = [];
    const connections = [];
    let cameraRequests = 0;
    const track = {
        kind: 'video', label: 'private camera label', readyState: 'live', muted: false,
        getSettings: () => ({ deviceId: 'rear' }), stop() {},
        addEventListener(event, handler) { this[event] = handler; },
    };
    const stream = { getTracks: () => [track], getVideoTracks: () => [track] };
    function node(id) {
        if (!nodes.has(id)) nodes.set(id, {
            videoWidth: 1920, videoHeight: 1080, textContent: '', value: '',
            classList: { add() {}, remove() {}, toggle() {} }, replaceChildren() {},
            addEventListener(event, handler) { this[event] = handler; },
        });
        return nodes.get(id);
    }
    const sandbox = vm.createContext({
        document: { getElementById: node, addEventListener() {}, createElement: () => ({}) },
        window: {
            isSecureContext: true, setTimeout() {}, clearTimeout() {},
            location: { origin: 'https://private.example', pathname: '/camera/' },
        },
        navigator: {
            userAgent: 'private userAgent', mediaDevices: {
                async getUserMedia() {
                    cameraRequests += 1;
                    if (cameraError && (!failFirst || cameraRequests === 1)) throw cameraError;
                    return stream;
                },
                async enumerateDevices() { return []; },
            },
        },
        performance: { now: () => 5000 },
        console: { warn: (...args) => warnings.push(args) },
        async fetch(url, options) {
            assert.equal(url, '/api/camera/diagnostics/');
            assert.equal(options.method, 'POST');
            assert.equal(options.headers['Content-Type'], 'application/json');
            reports.push(JSON.parse(options.body));
            if (reportError) throw reportError;
            return { ok: reportOk, status: reportOk ? 200 : 400, json: async () => ({ ok: reportOk }) };
        },
        RTCPeerConnection: class {
            constructor() {
                this.connectionState = 'new'; this.iceConnectionState = 'new';
                connections.push(this);
            }
            addTrack() {}
            getSenders() { return []; }
            close() { this.connectionState = 'closed'; }
        },
    });
    vm.runInContext(fs.readFileSync(`${__dirname}/static/fruit_app/js/camera.js`, 'utf8'), sandbox);
    return { sandbox, node, reports, warnings, track, connections };
}

const reportKeys = [
    'event', 'secure_context', 'get_user_media_available', 'track_muted', 'permission',
    'video_track_state', 'rtc_connection_state', 'rtc_ice_connection_state',
    'video_width', 'video_height', 'last_live_frame_age_ms',
].sort();

test('camera start, track end and both RTC state events report only whitelisted fields', async () => {
    const camera = reportingCamera();
    assert.equal(camera.reports.length, 0);
    await camera.node('btn-start-camera').click();
    assert.deepEqual(camera.reports.map((body) => body.event), ['camera_started']);
    assert.equal(camera.reports[0].permission, 'granted');
    assert.equal(camera.reports[0].video_track_state, 'live');
    assert.equal(camera.node('btn-start-camera').disabled, true);

    camera.track.readyState = 'ended';
    camera.track.ended();
    assert.equal(camera.reports.at(-1).event, 'track_ended');
    assert.equal(camera.reports.at(-1).video_track_state, 'ended');

    vm.runInContext('createPeerConnection()', camera.sandbox);
    const rtc = camera.connections[0];
    rtc.connectionState = 'connected';
    rtc.onconnectionstatechange();
    assert.equal(camera.reports.at(-1).rtc_connection_state, 'connected');
    rtc.iceConnectionState = 'failed';
    rtc.oniceconnectionstatechange();
    assert.equal(camera.reports.at(-1).rtc_ice_connection_state, 'failed');
    assert.deepEqual(camera.reports.map((body) => body.event), [
        'camera_started', 'track_ended', 'rtc_state_changed', 'rtc_state_changed',
    ]);
    for (const body of camera.reports) {
        assert.deepEqual(Object.keys(body).sort(), reportKeys);
        assert.equal(JSON.stringify(body).includes('private'), false);
        assert.equal(Object.hasOwn(body, 'userAgent'), false);
    }
});

test('camera failure reports the permission error name and drops free text', async () => {
    for (const name of ['NotAllowedError', 'NotFoundError', 'NotReadableError', 'OverconstrainedError',
        'SecurityError', 'AbortError', 'TypeError', 'arbitrary private name']) {
        const camera = reportingCamera({ cameraError: { name, message: 'private error message' } });
        await camera.node('btn-start-camera').click();
        assert.equal(camera.reports.length, 1);
        for (const body of camera.reports) {
            assert.equal(body.event, 'camera_failed');
            assert.equal(body.permission, name.startsWith('arbitrary') ? 'unknown' : name);
            assert.deepEqual(Object.keys(body).sort(), reportKeys);
            assert.equal(JSON.stringify(body).includes('private'), false);
        }
        assert.equal(camera.node('btn-start-camera').disabled, false);
    }
});

test('getUserMedia fallback success reports only camera_started', async () => {
    const camera = reportingCamera({ cameraError: { name: 'OverconstrainedError' }, failFirst: true });
    await camera.node('btn-start-camera').click();
    assert.deepEqual(camera.reports.map((body) => [body.event, body.permission]), [
        ['camera_started', 'granted'],
    ]);
    assert.match(camera.node('status').textContent, /相機已開啟/);
});

test('lens fallback success reports only camera_started', async () => {
    const camera = reportingCamera();
    await camera.node('btn-start-camera').click();
    camera.reports.length = 0;
    let attempts = 0;
    camera.sandbox.navigator.mediaDevices.getUserMedia = async () => {
        if (++attempts === 1) throw { name: 'NotReadableError', message: 'private error' };
        return { getTracks: () => [camera.track], getVideoTracks: () => [camera.track] };
    };
    camera.node('camera-lens').value = 'other';
    await camera.node('camera-lens').change();
    assert.deepEqual(camera.reports.map((body) => [body.event, body.permission]), [
        ['camera_started', 'granted'],
    ]);
    assert.match(camera.node('status').textContent, /已切換鏡頭/);
});

test('camera start and lens switch report one failure with the final fallback error name', async () => {
    for (const switching of [false, true]) {
        const camera = reportingCamera();
        if (switching) {
            await camera.node('btn-start-camera').click();
            camera.reports.length = 0;
        }
        const failures = switching
            ? ['NotReadableError', 'OverconstrainedError', 'AbortError']
            : ['OverconstrainedError', 'AbortError'];
        camera.sandbox.navigator.mediaDevices.getUserMedia = async () => {
            throw { name: failures.shift(), message: 'private error' };
        };
        if (switching) {
            camera.node('camera-lens').value = 'other';
            await camera.node('camera-lens').change();
        } else {
            await camera.node('btn-start-camera').click();
        }
        assert.equal(failures.length, 0);
        assert.deepEqual(camera.reports.map((body) => [body.event, body.permission]), [
            ['camera_failed', 'AbortError'],
        ]);
        assert.equal(camera.node('btn-start-camera').disabled, false);
    }
});

test('diagnostic network and HTTP failures are warnings and leave camera start successful', async () => {
    for (const options of [{ reportError: new Error('offline') }, { reportOk: false }]) {
        const camera = reportingCamera(options);
        await assert.doesNotReject(camera.node('btn-start-camera').click());
        await new Promise(setImmediate);
        assert.equal(camera.reports.length, 1);
        assert.equal(camera.node('btn-start-camera').disabled, true);
        assert.match(camera.node('status').textContent, /相機已開啟/);
        assert.equal(camera.warnings.length, 1);
        assert.equal(await vm.runInContext("reportCameraDiagnostics('rtc_state_changed')", camera.sandbox), undefined);
    }
});
