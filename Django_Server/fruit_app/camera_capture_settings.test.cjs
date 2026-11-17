const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { test } = require('node:test');

// 相機頁的解析度／JPEG 品質選單：toml 為預設，手機 localStorage 優先。
function loadCamera({ stored = {}, dataset = { captureWidth: '1920', captureHeight: '1080', jpegQuality: '0.92' } } = {}) {
    const nodes = new Map();
    const requests = [];
    const qualities = [];
    const storage = { ...stored };
    function trackFor(video) {
        return {
            kind: 'video', label: 'Back Ultra Wide Camera', readyState: 'live', muted: false, stopped: false,
            stop() { this.stopped = true; },
            getSettings: () => ({ deviceId: 'rear-ultra', width: video.width?.ideal, height: video.height?.ideal }),
            addEventListener() {},
        };
    }
    function node(id) {
        if (!nodes.has(id)) nodes.set(id, {
            id, videoWidth: 0, videoHeight: 0, textContent: '', value: '', disabled: false,
            classList: { add() {}, remove() {}, toggle() {} },
            replaceChildren(...children) { this.children = children; },
            addEventListener(event, handler) { this[event] = handler; },
            getContext: () => ({ drawImage() {} }),
            toBlob(callback, type, quality) { qualities.push(quality); callback({ type }); },
        });
        return nodes.get(id);
    }
    node('capture-canvas').dataset = dataset;
    const sandbox = vm.createContext({
        document: { getElementById: node, addEventListener() {}, createElement: () => ({}) },
        window: {
            isSecureContext: true, setTimeout() {}, clearTimeout() {},
            location: { origin: 'https://lab.example', pathname: '/camera/' },
            localStorage: {
                getItem: (key) => storage[key] ?? null,
                setItem: (key, value) => { storage[key] = String(value); },
            },
        },
        navigator: {
            userAgent: 'Test iPhone',
            mediaDevices: {
                async getUserMedia(constraints) {
                    requests.push(constraints.video);
                    const track = trackFor(constraints.video);
                    return { getTracks: () => [track], getVideoTracks: () => [track] };
                },
                async enumerateDevices() { return []; },
            },
        },
        performance: { now: () => 5000 },
        console: { warn() {} },
        async fetch() { return { ok: true, status: 200, json: async () => ({ ok: true }) }; },
    });
    vm.runInContext(fs.readFileSync(`${__dirname}/static/fruit_app/js/camera.js`, 'utf8'), sandbox);
    return { sandbox, node, requests, qualities, storage };
}

test('toml default is used until the phone saves its own choice', async () => {
    const camera = loadCamera({ dataset: { captureWidth: '3840', captureHeight: '2160', jpegQuality: '0.9' } });
    await camera.node('btn-start-camera').click();
    assert.equal(camera.requests[0].width.ideal, 3840);
    assert.equal(camera.node('camera-resolution').value, '3840x2160');
    assert.equal(camera.node('camera-quality').value, '0.9');
    assert.equal(camera.node('actual-resolution').textContent, '實際 3840×2160');
});

test('saved choice overrides the toml default and invalid saved values are ignored', async () => {
    const saved = loadCamera({ stored: {
        'pf-camera-capture-resolution': '2560x1440', 'pf-camera-jpeg-quality': '0.85',
    } });
    await saved.node('btn-start-camera').click();
    assert.equal(saved.requests[0].width.ideal, 2560);
    assert.equal(saved.requests[0].height.ideal, 1440);
    assert.equal(vm.runInContext('jpegQuality', saved.sandbox), 0.85);

    const invalid = loadCamera({ stored: {
        'pf-camera-capture-resolution': '4000x3000', 'pf-camera-jpeg-quality': '0.3',
    } });
    await invalid.node('btn-start-camera').click();
    assert.equal(invalid.requests[0].width.ideal, 1920);
    assert.equal(vm.runInContext('jpegQuality', invalid.sandbox), 0.92);
});

test('resolution change restarts the same lens with new constraints, never during capture', async () => {
    const camera = loadCamera();
    await camera.node('btn-start-camera').click();
    const first = vm.runInContext('localStream', camera.sandbox);

    vm.runInContext('isCapturing = true', camera.sandbox);
    camera.node('camera-resolution').value = '3840x2160';
    await camera.node('camera-resolution').change();
    assert.equal(camera.requests.length, 1);
    assert.equal(camera.node('camera-resolution').value, '1920x1080');
    assert.match(camera.node('status').textContent, /拍攝中無法切換解析度/);

    vm.runInContext('isCapturing = false', camera.sandbox);
    camera.node('camera-resolution').value = '3840x2160';
    await camera.node('camera-resolution').change();
    assert.equal(first.getVideoTracks()[0].stopped, true);
    assert.equal(camera.requests.at(-1).deviceId.exact, 'rear-ultra');
    assert.equal(camera.requests.at(-1).width.ideal, 3840);
    assert.equal(camera.requests.at(-1).height.ideal, 2160);
    assert.equal(camera.storage['pf-camera-capture-resolution'], '3840x2160');
    assert.equal(camera.node('actual-resolution').textContent, '實際 3840×2160');
});

test('quality change is used by the next toBlob without restarting the stream', async () => {
    const camera = loadCamera();
    await camera.node('btn-start-camera').click();
    camera.node('camera-quality').value = '0.97';
    camera.node('camera-quality').change();
    await vm.runInContext('captureFrameBlob()', camera.sandbox);
    assert.deepEqual(camera.qualities, [0.97]);
    assert.equal(camera.requests.length, 1);
    assert.equal(camera.storage['pf-camera-jpeg-quality'], '0.97');
});
