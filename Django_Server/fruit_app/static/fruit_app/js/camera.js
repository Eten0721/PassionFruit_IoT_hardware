const localVideo = document.getElementById('local-video');
    const canvas = document.getElementById('capture-canvas');
    const startPanel = document.getElementById('start-panel');
    const startButton = document.getElementById('btn-start-camera');
    const statusEl = document.getElementById('status');
    const diagnosticEl = document.getElementById('diagnostic');

    let localStream = null;
    let peerConnection = null;
    let currentOfferId = 0;
    let knownAnswerId = 0;
    let dashboardIceIndex = 0;
    let lastCaptureRequestKey = '';
    let lastFailedCaptureRequestKey = '';
    let lastCaptureFailureTime = 0;
    let isCapturing = false;
    let capturePollTimer = null;
    let webrtcPollTimer = null;
    const captureRetryDelayMs = 2000;
    const captureIdlePollMs = 250;
    const captureActivePollMs = 50;
    const captureBusyPollMs = 250;
    const captureStateRequestTimeoutMs = 1000;
    const captureStartedTelemetryTimeoutMs = 750;
    const webrtcPollMs = 1000;
    const RTC_CONFIG = {
        iceServers: [{ urls: 'stun:stun.l.google.com:19302' }],
    };

    function setStatus(message) {
        statusEl.textContent = message;
    }

    function updateDiagnostic() {
        if (!window.isSecureContext) {
            diagnosticEl.textContent = '非安全來源：手機瀏覽器通常不會開放相機。請使用 https://電腦IP:8000/camera/，並先接受自簽憑證。';
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
        const response = await fetchWithTimeout('/api/camera/state/', {
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
            setStatus('目前不是安全來源，相機權限不會被瀏覽器開放。');
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
                localStream.getTracks().forEach((track) => track.stop());
            }
            try {
                localStream = await navigator.mediaDevices.getUserMedia({
                    video: {
                        facingMode: { ideal: 'environment' },
                        width: { ideal: 1920 },
                        height: { ideal: 1080 },
                    },
                    audio: false,
                });
            } catch (environmentError) {
                localStream = await navigator.mediaDevices.getUserMedia({
                    video: true,
                    audio: false,
                });
            }

            localVideo.srcObject = localStream;
            startPanel.classList.add('hidden');
            setStatus('相機已開啟，等待 dashboard 連線與拍攝命令。');
            currentOfferId = 0;
            knownAnswerId = 0;
            restartWebrtcPolling(0);
        } catch (error) {
            setStatus(`相機開啟失敗：${error.name || 'Error'}，${error.message || '請檢查瀏覽器權限設定。'}`);
            startButton.disabled = false;
        }
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
                setStatus('dashboard 已送出即時畫面請求，請先開啟相機以回覆 WebRTC answer。');
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
            setStatus('已連線 dashboard 即時畫面。');
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
            await handleWebrtcState(data);
            return data;
        } catch (error) {
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
        setStatus(`串流狀態：${peerConnection.connectionState} / ICE：${peerConnection.iceConnectionState}${debug}${failedHint}`);
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

    updateDiagnostic();
    startButton.addEventListener('click', startCamera);
    restartWebrtcPolling(0);
    restartCapturePolling(0);
