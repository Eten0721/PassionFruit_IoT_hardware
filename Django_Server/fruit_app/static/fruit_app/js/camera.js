const localVideo = document.getElementById('local-video');
    const canvas = document.getElementById('capture-canvas');
    const startPanel = document.getElementById('start-panel');
    const startButton = document.getElementById('btn-start-camera');
    const statusEl = document.getElementById('status');
    const diagnosticEl = document.getElementById('diagnostic');
    const rtcStatusEl = document.getElementById('rtc-status');
    const lensBar = document.getElementById('lens-bar');
    const lensSelect = document.getElementById('camera-lens');

    let localStream = null;
    let peerConnection = null;
    let currentOfferId = 0;
    let knownAnswerId = 0;
    let dashboardIceIndex = 0;
    let lastCaptureRequestKey = '';
    let lastFailedCaptureRequestKey = '';
    let lastCaptureFailureTime = 0;
    let isCapturing = false;
    let lastLiveFrameAtMs = null;
    let capturePollTimer = null;
    let webrtcPollTimer = null;
    let watchingLiveFrames = false;
    const captureRetryDelayMs = 2000;
    const captureIdlePollMs = 250;
    const captureActivePollMs = 50;
    const captureBusyPollMs = 250;
    const captureStateRequestTimeoutMs = 1000;
    const captureStartedTelemetryTimeoutMs = 750;
    const webrtcPollMs = 1000;
    const CAPTURE_SIZE = { width: { ideal: 1920 }, height: { ideal: 1080 } };
    const ULTRA_WIDE_LABEL = /ultra\s*wide|超廣角|超广角|超広角/i;
    const FRONT_LABEL = /front|前置|前鏡頭|前镜头/i;
    const LENS_STORAGE_KEY = 'pf-camera-device-id';
    const RTC_CONFIG = {
        iceServers: [],
    };

    function setStatus(message) {
        statusEl.textContent = message;
    }

    function updateDiagnostic() {
        if (!window.isSecureContext) {
            diagnosticEl.textContent = 'camera_secure_context_required：isSecureContext=false。請用控制台所選網路的 HTTPS 相機網址；接受憑證警告後仍須確認此檢查通過。';
            return;
        }
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            diagnosticEl.textContent = '此瀏覽器不支援 mediaDevices.getUserMedia，相機無法開啟。';
            return;
        }
        diagnosticEl.textContent = '安全來源已通過。點擊按鈕後，瀏覽器應會詢問相機權限。';
    }

    async function fetchWithTimeout(url, options, timeoutMs) {
        const controller = new AbortController();
        const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);
        try {
            return await fetch(url, {
                ...options,
                signal: controller.signal,
            });
        } catch (error) {
            if (error.name === 'AbortError') {
                const timeoutError = new Error(`請求逾時（${timeoutMs} ms）：${url}`);
                timeoutError.code = 'request_timeout';
                throw timeoutError;
            }
            throw error;
        } finally {
            window.clearTimeout(timeoutId);
        }
    }

    async function postJson(url, data, { timeoutMs = null } = {}) {
        const options = {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        };
        const response = timeoutMs === null
            ? await fetch(url, options)
            : await fetchWithTimeout(url, options, timeoutMs);
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            throwHttpError(payload, response.status);
        }
        return payload;
    }

    function throwHttpError(payload, status) {
        const reason = payload.reason ? ` (${payload.reason})` : '';
        const error = new Error(`${payload.message || `HTTP ${status}`}${reason}`);
        error.payload = payload;
        error.status = status;
        throw error;
    }

    async function fetchCaptureState() {
        const hasFreshLiveFrame = (
            lastLiveFrameAtMs !== null
            && performance.now() - lastLiveFrameAtMs <= 1000
        );
        const url = hasFreshLiveFrame
            ? '/api/camera/state/?camera_ready=1'
            : '/api/camera/state/';
        const response = await fetchWithTimeout(url, {
            cache: 'no-store',
            headers: { 'Cache-Control': 'no-cache' },
        }, captureStateRequestTimeoutMs);
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            throwHttpError(payload, response.status);
        }
        return payload;
    }

    function captureRequestFromState(data) {
        const capture = data.capture || {};
        return {
            revision: data.revision ?? 0,
            fruitId: capture.fruit_id ?? data.fruit_id ?? data.active_fruit_id ?? '',
            captureToken: capture.token ?? data.capture_token ?? 0,
            stationIndex: Number(capture.station_index ?? data.station_index ?? data.active_station_index ?? 0),
            captureRequested: Boolean(capture.requested ?? data.capture_requested),
        };
    }

    async function startCamera() {
        updateDiagnostic();
        if (!window.isSecureContext) {
            setStatus('camera_secure_context_required：目前不是安全來源，相機權限不會被瀏覽器開放。');
            return;
        }
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            setStatus('此瀏覽器不支援相機 API。');
            return;
        }

        startButton.disabled = true;
        setStatus('正在向瀏覽器請求相機權限。');
        try {
            if (localStream) {
                stopStream(localStream);
            }
            const rearStream = await openRearCamera();
            const cameras = await listCameras();
            const preferred = pickDefaultCamera(cameras, readSavedDeviceId());
            attachStream(await switchStream(rearStream, preferred?.deviceId));
            renderLensOptions(cameras);
            startPanel.classList.add('hidden');
            setStatus(`相機已開啟（${activeLensLabel()}），等待正式拍攝請求；RTC 預覽另行連線。`);
            currentOfferId = 0;
            knownAnswerId = 0;
            restartWebrtcPolling(0);
        } catch (error) {
            setStatus(`相機開啟失敗：${error.name || 'Error'}，${error.message || '請檢查瀏覽器權限設定。'}`);
            startButton.disabled = false;
        }
    }

    async function openRearCamera() {
        try {
            return await navigator.mediaDevices.getUserMedia({
                video: { facingMode: { ideal: 'environment' }, ...CAPTURE_SIZE },
                audio: false,
            });
        } catch (environmentError) {
            return navigator.mediaDevices.getUserMedia({ video: true, audio: false });
        }
    }

    async function listCameras() {
        const devices = await navigator.mediaDevices.enumerateDevices().catch(() => []);
        return devices.filter((device) => device.kind === 'videoinput' && device.deviceId);
    }

    // 操作員選過的鏡頭優先；否則選後置超廣角（三站間距 19 cm 時主鏡頭視角不足），前鏡頭不自動選。
    function pickDefaultCamera(cameras, savedDeviceId) {
        const saved = cameras.find((camera) => camera.deviceId === savedDeviceId);
        if (saved) {
            return saved;
        }
        return cameras.find((camera) => (
            ULTRA_WIDE_LABEL.test(camera.label || '') && !FRONT_LABEL.test(camera.label || '')
        )) || null;
    }

    async function switchStream(stream, deviceId) {
        if (!deviceId || deviceIdOf(stream) === deviceId) {
            return stream;
        }
        // iOS 同時只能開一顆鏡頭，須先停止舊串流。
        stopStream(stream);
        try {
            return await navigator.mediaDevices.getUserMedia({
                video: { deviceId: { exact: deviceId }, ...CAPTURE_SIZE },
                audio: false,
            });
        } catch (switchError) {
            console.warn('指定鏡頭開啟失敗，改用預設後鏡頭', switchError);
            return openRearCamera();
        }
    }

    function attachStream(stream) {
        localStream = stream;
        localVideo.srcObject = stream;
        lastLiveFrameAtMs = null;
        watchLiveFrames();
        const track = stream.getVideoTracks()[0];
        const sender = peerConnection?.getSenders().find((item) => item.track?.kind === 'video');
        if (sender && track) {
            sender.replaceTrack(track).catch((error) => console.warn('RTC 預覽切換鏡頭失敗', error));
        }
    }

    function renderLensOptions(cameras) {
        const options = cameras.map((camera, index) => {
            const option = document.createElement('option');
            option.value = camera.deviceId;
            option.textContent = camera.label || `鏡頭 ${index + 1}`;
            return option;
        });
        lensSelect.replaceChildren(...options);
        lensSelect.value = deviceIdOf(localStream);
        lensBar.classList.toggle('hidden', cameras.length < 2);
    }

    async function selectLens() {
        const deviceId = lensSelect.value;
        if (!localStream || isCapturing) {
            lensSelect.value = deviceIdOf(localStream);
            setStatus('拍攝中無法切換鏡頭，請稍後再試。');
            return;
        }
        lensSelect.disabled = true;
        try {
            attachStream(await switchStream(localStream, deviceId));
            saveDeviceId(deviceIdOf(localStream));
            lensSelect.value = deviceIdOf(localStream);
            setStatus(`已切換鏡頭（${activeLensLabel()}），等待正式拍攝請求。`);
        } catch (error) {
            localStream = null;
            lensBar.classList.add('hidden');
            startPanel.classList.remove('hidden');
            startButton.disabled = false;
            setStatus(`切換鏡頭失敗：${error.name || 'Error'}，請重新開啟相機。`);
        } finally {
            lensSelect.disabled = false;
        }
    }

    function deviceIdOf(stream) {
        return stream?.getVideoTracks()[0]?.getSettings?.().deviceId || '';
    }

    function stopStream(stream) {
        stream.getTracks().forEach((track) => track.stop());
    }

    function readSavedDeviceId() {
        try {
            return window.localStorage.getItem(LENS_STORAGE_KEY) || '';
        } catch (storageError) {
            return '';
        }
    }

    function saveDeviceId(deviceId) {
        try {
            window.localStorage.setItem(LENS_STORAGE_KEY, deviceId);
        } catch (storageError) {
            console.warn('無法記住鏡頭選擇', storageError);
        }
    }

    function activeLensLabel() {
        return localStream?.getVideoTracks()[0]?.label || '未知鏡頭';
    }

    function createPeerConnection() {
        if (peerConnection) {
            peerConnection.close();
        }
        dashboardIceIndex = 0;
        peerConnection = new RTCPeerConnection(RTC_CONFIG);
        localStream.getTracks().forEach((track) => {
            peerConnection.addTrack(track, localStream);
        });
        peerConnection.onicecandidate = (event) => {
            if (event.candidate) {
                postJson('/api/webrtc/ice', {
                    role: 'camera',
                    candidate: event.candidate.toJSON(),
                }).catch((error) => console.warn('ICE candidate 上傳失敗', error));
            }
        };
        peerConnection.onconnectionstatechange = () => {
            renderWebRTCStatus();
        };
        peerConnection.oniceconnectionstatechange = () => {
            renderWebRTCStatus();
        };
    }

    async function handleWebrtcState(data) {
        const offerId = Number(data.offer_id || 0);
        if (!localStream) {
            if (data.offer_present) {
                rtcStatusEl.textContent = 'RTC 預覽：請先開啟相機。';
            }
            return;
        }

        if (data.offer) {
            currentOfferId = offerId;
            knownAnswerId = Number(data.answer_id || 0);
            createPeerConnection();
            await peerConnection.setRemoteDescription(data.offer);
            const answer = await peerConnection.createAnswer();
            await peerConnection.setLocalDescription(answer);
            const answerPayload = await postJson('/api/webrtc/answer', {
                answer: {
                    type: peerConnection.localDescription.type,
                    sdp: peerConnection.localDescription.sdp,
                },
            });
            knownAnswerId = Number(answerPayload.answer_id || knownAnswerId);
            rtcStatusEl.textContent = 'RTC 預覽已連線。';
        } else if (offerId && offerId !== currentOfferId) {
            currentOfferId = 0;
            restartWebrtcPolling(0);
            return;
        }

        const dashboardIce = data.dashboard_ice || [];
        for (const candidate of dashboardIce) {
            if (!peerConnection) {
                break;
            }
            try {
                await peerConnection.addIceCandidate(candidate);
            } catch (error) {
                console.warn('加入 dashboard ICE candidate 失敗', error);
            }
        }
        dashboardIceIndex = typeof data.dashboard_ice_total === 'number'
            ? data.dashboard_ice_total
            : dashboardIceIndex + dashboardIce.length;
        renderWebRTCStatus(data);
    }

    async function pollWebrtcState() {
        try {
            const params = new URLSearchParams({
                dashboard_ice_from: String(dashboardIceIndex),
                known_offer_id: String(currentOfferId),
                known_answer_id: String(knownAnswerId),
            });
            const response = await fetch(`/api/webrtc/state?${params.toString()}`, { cache: 'no-store' });
            const data = await response.json();
            if (!response.ok) throwHttpError(data, response.status);
            await handleWebrtcState(data);
            return data;
        } catch (error) {
            rtcStatusEl.textContent = `RTC 預覽同步失敗：${error.message}；正式照片採集獨立運作。`;
            console.warn('WebRTC signaling 同步失敗', error);
            return null;
        }
    }

    function renderWebRTCStatus(data = null) {
        if (!peerConnection) {
            return;
        }
        const debug = data
            ? `，offer:${data.offer_present ? '有' : '無'} #${data.offer_id || 0} answer:${data.answer_present ? '有' : '無'} #${data.answer_id || 0} ICE:${data.dashboard_ice_total || 0}/${data.camera_ice_total || 0}`
            : '';
        const failedHint = peerConnection.connectionState === 'failed' || peerConnection.iceConnectionState === 'failed'
            ? '，請回 dashboard 按重新連線串流'
            : '';
        rtcStatusEl.textContent = `RTC 預覽：${peerConnection.connectionState} / ICE：${peerConnection.iceConnectionState}${debug}${failedHint}；正式照片採集獨立運作。`;
    }

    async function pollCaptureState() {
        try {
            const data = await fetchCaptureState();
            if (data.status === 'error') {
                setStatus(data.message || 'Django 拍攝流程發生錯誤。');
                return data;
            }
            const capture = captureRequestFromState(data);
            const captureIdentity = `${capture.fruitId}:${capture.captureToken}:${capture.stationIndex}`;
            const requestKey = `${capture.revision}:${captureIdentity}`;
            if (
                capture.captureRequested &&
                capture.fruitId &&
                capture.stationIndex > 0 &&
                requestKey !== lastCaptureRequestKey &&
                !isCapturing
            ) {
                if (
                    captureIdentity === lastFailedCaptureRequestKey &&
                    Date.now() - lastCaptureFailureTime < captureRetryDelayMs
                ) {
                    return;
                }
                const completed = await captureAndUpload(
                    capture.fruitId,
                    capture.captureToken,
                    capture.stationIndex,
                    capture.revision
                );
                if (completed) {
                    lastCaptureRequestKey = requestKey;
                    lastFailedCaptureRequestKey = '';
                    lastCaptureFailureTime = 0;
                } else {
                    lastFailedCaptureRequestKey = captureIdentity;
                    lastCaptureFailureTime = Date.now();
                }
            }
            return data;
        } catch (error) {
            console.warn('拍攝狀態同步失敗', error);
            return null;
        }
    }

    function capturePollDelay(data) {
        if (isCapturing) {
            return captureBusyPollMs;
        }
        const capture = data ? captureRequestFromState(data) : null;
        if (capture && (capture.captureRequested || capture.fruitId)) {
            return captureActivePollMs;
        }
        return captureIdlePollMs;
    }

    function restartCapturePolling(delayMs = 0) {
        if (capturePollTimer !== null) {
            window.clearTimeout(capturePollTimer);
        }
        capturePollTimer = window.setTimeout(runCapturePollLoop, delayMs);
    }

    async function runCapturePollLoop() {
        capturePollTimer = null;
        if (document.hidden) {
            return;
        }
        const data = await pollCaptureState();
        if (document.hidden) {
            return;
        }
        restartCapturePolling(capturePollDelay(data));
    }

    function restartWebrtcPolling(delayMs = 0) {
        if (webrtcPollTimer !== null) {
            window.clearTimeout(webrtcPollTimer);
        }
        webrtcPollTimer = window.setTimeout(runWebrtcPollLoop, delayMs);
    }

    async function runWebrtcPollLoop() {
        webrtcPollTimer = null;
        if (document.hidden) {
            return;
        }
        await pollWebrtcState();
        if (document.hidden) {
            return;
        }
        restartWebrtcPolling(webrtcPollMs);
    }

    document.addEventListener('visibilitychange', () => {
        if (document.hidden) {
            if (capturePollTimer !== null) {
                window.clearTimeout(capturePollTimer);
                capturePollTimer = null;
            }
            if (webrtcPollTimer !== null) {
                window.clearTimeout(webrtcPollTimer);
                webrtcPollTimer = null;
            }
            return;
        }
        restartCapturePolling(0);
        restartWebrtcPolling(0);
    });

    function roundTimingMs(value) {
        return Number(value.toFixed(1));
    }

    async function captureAndUpload(fruitId, captureToken, stationIndex, requestRevision) {
        if (!localStream) {
            setStatus('尚未開啟相機，無法拍攝。');
            return false;
        }
        isCapturing = true;
        const requestReceivedAtMs = performance.now();
        setStatus(`收到 ${fruitId} 第 ${stationIndex} 站拍攝請求，準備拍攝單張照片。`);

        try {
            await waitForVideoReady();
            const videoReadyAtMs = performance.now();
            const captureStartedSentAtMs = performance.now();
            notifyCaptureStarted(fruitId, captureToken, stationIndex, captureStartedSentAtMs)
                .catch((error) => console.warn('開始拍攝 telemetry 回報失敗，不影響照片上傳', error));

            const formData = new FormData();
            formData.append('fruit_id', fruitId);
            formData.append('capture_token', captureToken);
            formData.append('station_index', stationIndex);
            const capturedFrame = await captureFrameBlob();
            const uploadStartedAtMs = performance.now();
            formData.append('image', capturedFrame.blob, `img_${String(stationIndex).padStart(2, '0')}.jpg`);
            setStatus(`${fruitId} 第 ${stationIndex} 站照片已拍攝，正在上傳。`);

            formData.append('capture_meta', JSON.stringify({
                schema_version: 2,
                capture_token: captureToken,
                station_index: stationIndex,
                client_timing: {
                    clock: 'performance.now',
                    request_revision: requestRevision,
                    request_received_at_ms: roundTimingMs(requestReceivedAtMs),
                    video_ready_at_ms: roundTimingMs(videoReadyAtMs),
                    capture_started_sent_at_ms: roundTimingMs(captureStartedSentAtMs),
                    frame_drawn_at_ms: roundTimingMs(capturedFrame.frameDrawnAtMs),
                    blob_ready_at_ms: roundTimingMs(capturedFrame.blobReadyAtMs),
                    upload_started_at_ms: roundTimingMs(uploadStartedAtMs),
                    request_to_video_ready_ms: roundTimingMs(videoReadyAtMs - requestReceivedAtMs),
                    request_to_frame_drawn_ms: roundTimingMs(capturedFrame.frameDrawnAtMs - requestReceivedAtMs),
                    frame_to_blob_ready_ms: roundTimingMs(capturedFrame.blobReadyAtMs - capturedFrame.frameDrawnAtMs),
                    request_to_upload_started_ms: roundTimingMs(uploadStartedAtMs - requestReceivedAtMs),
                },
            }));

            const response = await fetch('/api/upload_images/', {
                method: 'POST',
                body: formData,
            });
            if (!response.ok) {
                const payload = await response.json().catch(() => ({}));
                throwHttpError(payload, response.status);
            }
            setStatus(`${fruitId} 第 ${stationIndex} 站照片已上傳完成。`);
            return true;
        } catch (error) {
            setStatus(`拍攝或上傳失敗：${error.message}`);
            return false;
        } finally {
            isCapturing = false;
        }
    }

    function notifyCaptureStarted(fruitId, captureToken, stationIndex, sentAtMs) {
        return postJson('/api/capture_started/', {
            fruit_id: fruitId,
            capture_token: captureToken,
            station_index: stationIndex,
            client_timing: {
                clock: 'performance.now',
                capture_started_sent_at_ms: roundTimingMs(sentAtMs),
            },
        }, { timeoutMs: captureStartedTelemetryTimeoutMs });
    }

    function waitForVideoReady() {
        if (localVideo.videoWidth > 0 && localVideo.videoHeight > 0) {
            return Promise.resolve();
        }
        return new Promise((resolve) => {
            localVideo.addEventListener('loadedmetadata', resolve, { once: true });
        });
    }

    function captureFrameBlob() {
        canvas.width = localVideo.videoWidth;
        canvas.height = localVideo.videoHeight;
        const context = canvas.getContext('2d');
        context.drawImage(localVideo, 0, 0, canvas.width, canvas.height);
        const frameDrawnAtMs = performance.now();
        return new Promise((resolve, reject) => {
            canvas.toBlob((blob) => {
                if (blob) {
                    resolve({
                        blob,
                        frameDrawnAtMs,
                        blobReadyAtMs: performance.now(),
                    });
                } else {
                    reject(new Error('瀏覽器無法產生照片 blob。'));
                }
            }, 'image/jpeg', 0.92);
        });
    }

    function watchLiveFrames() {
        lastLiveFrameAtMs = null;
        if (watchingLiveFrames) {
            return;
        }
        watchingLiveFrames = true;
        if (typeof localVideo.requestVideoFrameCallback !== 'function') {
            localVideo.addEventListener('timeupdate', markLiveFrame);
            return;
        }
        localVideo.requestVideoFrameCallback(function onLiveFrame() {
            markLiveFrame();
            localVideo.requestVideoFrameCallback(onLiveFrame);
        });
    }

    function markLiveFrame() {
        if (localVideo.videoWidth > 0 && localVideo.videoHeight > 0) {
            lastLiveFrameAtMs = performance.now();
        }
    }

    function updateAcceptanceDiagnostics() {
        const snapshot = {
            recordedAt: new Date().toISOString(),
            page: window.location.origin + window.location.pathname,
            userAgent: navigator.userAgent,
            isSecureContext: window.isSecureContext,
            getUserMediaAvailable: Boolean(navigator.mediaDevices?.getUserMedia),
            browserOnline: navigator.onLine,
            videoSize: [localVideo.videoWidth, localVideo.videoHeight],
            videoTracks: (localStream?.getVideoTracks() || []).map((track) => ({
                label: track.label, readyState: track.readyState, enabled: track.enabled, muted: track.muted,
            })),
            lastLiveFrameAgeMs: lastLiveFrameAtMs === null
                ? null : Math.round(performance.now() - lastLiveFrameAtMs),
            captureStatus: statusEl.textContent,
            rtcConnectionState: peerConnection?.connectionState ?? null,
            rtcIceConnectionState: peerConnection?.iceConnectionState ?? null,
            rtcStatus: rtcStatusEl.textContent,
        };
        document.getElementById('camera-diagnostics-output').value = JSON.stringify(snapshot, null, 2);
    }

    document.getElementById('btn-camera-diagnostics').addEventListener('click', updateAcceptanceDiagnostics);
    updateDiagnostic();
    startButton.addEventListener('click', startCamera);
    lensSelect.addEventListener('change', selectLens);
    restartWebrtcPolling(0);
    restartCapturePolling(0);
