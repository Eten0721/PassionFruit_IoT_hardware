const remoteVideo = document.getElementById('remote-video');
    const remoteVideoWrap = document.getElementById('remote-video-wrap');
    const streamStatus = document.getElementById('stream-status');
    const messageEl = document.getElementById('message');
    const activeFruitEl = document.getElementById('active-fruit');
    const nextFruitEl = document.getElementById('next-fruit');
    const stateLabelEl = document.getElementById('state-label');
    const imageCountEl = document.getElementById('image-count');
    const esp32StatusEl = document.getElementById('esp32-status');
    const motorCommandEl = document.getElementById('motor-command');
    const stationStatusEls = [
        document.getElementById('station-1-status'),
        document.getElementById('station-2-status'),
        document.getElementById('station-3-status'),
    ];
    const errorReasonEl = document.getElementById('error-reason');
    const thumbnailGrid = document.getElementById('thumbnail-grid');
    const thumbnailEmpty = document.getElementById('thumbnail-empty');
    const counterInput = document.getElementById('counter-input');
    const noteInput = document.getElementById('note');
    const classifyButtons = Array.from(document.querySelectorAll('.classify-button'));
    const manualCaptureButton = document.getElementById('btn-manual-capture');
    const recaptureButton = document.getElementById('btn-recapture');
    const discardButton = document.getElementById('btn-discard');
    const resetDatasetButton = document.getElementById('btn-reset-dataset');
    const timingCommandDelay = document.getElementById('timing-command-delay');
    const timingWaitStarted = document.getElementById('timing-wait-started');
    const timingUploadReceived = document.getElementById('timing-upload-received');
    const transitionTraceEl = document.getElementById('transition-trace');
    const timingFirstStationInput = document.getElementById('timing-first-station');
    const timingServoInput = document.getElementById('timing-servo');
    const timingFruitInput = document.getElementById('timing-fruit');
    const timingFinalReturnInput = document.getElementById('timing-final-return');
    const timingInputs = [
        timingFirstStationInput,
        timingServoInput,
        timingFruitInput,
        timingFinalReturnInput,
    ];
    const timingConfigStatus = document.getElementById('timing-config-status');
    const applyTimingButton = document.getElementById('btn-apply-timing');
    const resetTimingButton = document.getElementById('btn-reset-timing');
    const imageDialog = document.getElementById('image-dialog');
    const imageDialogImage = document.getElementById('image-dialog-image');
    const imageDialogTitle = document.getElementById('image-dialog-title');
    const closeImageDialogButton = document.getElementById('btn-close-image-dialog');

    let peerConnection = null;
    let answerApplied = false;
    let cameraIceIndex = 0;
    let currentOfferId = 0;
    let knownAnswerId = 0;
    let statePollTimer = null;
    let webrtcPollTimer = null;
    let lastState = null;
    let lastThumbnailManifest = '';
    let timingInputsDirty = false;
    let timingUpdateInFlight = false;
    const stateIdlePollMs = 1000;
    const stateActivePollMs = 500;
    const webrtcPollMs = 1000;
    const RTC_CONFIG = {
        iceServers: [{ urls: 'stun:stun.l.google.com:19302' }],
    };

    document.getElementById('camera-url').textContent = `${window.location.origin}/camera/`;

    function setMessage(message) {
        messageEl.textContent = message || '';
    }

    function setStreamStatus(message) {
        streamStatus.textContent = message;
    }

    function setRemoteVideoVisible(visible) {
        remoteVideoWrap.hidden = !visible;
        remoteVideoWrap.classList.toggle('is-streaming', visible);
    }

    async function postJson(url, data = {}) {
        const response = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            const error = new Error(payload.message || `HTTP ${response.status}`);
            error.payload = payload;
            throw error;
        }
        return payload;
    }

    async function startWebRTC() {
        if (peerConnection) {
            peerConnection.close();
        }
        answerApplied = false;
        cameraIceIndex = 0;
        currentOfferId = 0;
        knownAnswerId = 0;
        remoteVideo.srcObject = null;
        setRemoteVideoVisible(false);
        peerConnection = new RTCPeerConnection(RTC_CONFIG);
        peerConnection.addTransceiver('video', { direction: 'recvonly' });

        peerConnection.ontrack = (event) => {
            const [stream] = event.streams;
            if (stream) {
                remoteVideo.srcObject = stream;
                setRemoteVideoVisible(stream.getVideoTracks().length > 0);
                stream.addEventListener('removetrack', () => {
                    if (!stream.getVideoTracks().length) {
                        setRemoteVideoVisible(false);
                    }
                });
                setStreamStatus('手機即時畫面已連線。');
            }
        };
        peerConnection.onicecandidate = (event) => {
            if (event.candidate) {
                postJson('/api/webrtc/ice', {
                    role: 'dashboard',
                    candidate: event.candidate.toJSON(),
                }).catch((error) => console.warn('ICE candidate 上傳失敗', error));
            }
        };
        peerConnection.onconnectionstatechange = () => {
            if (['failed', 'closed', 'disconnected'].includes(peerConnection.connectionState)) {
                setRemoteVideoVisible(false);
            }
            renderWebRTCStatus();
        };
        peerConnection.oniceconnectionstatechange = () => {
            renderWebRTCStatus();
        };

        const offer = await peerConnection.createOffer();
        await peerConnection.setLocalDescription(offer);
        const offerPayload = await postJson('/api/webrtc/offer', { offer: peerConnection.localDescription });
        currentOfferId = Number(offerPayload.offer_id || 0);
        setStreamStatus('已送出 WebRTC offer，請確認手機頁正在開啟。');
        restartWebRTCPolling(0);
    }

    async function pollWebRTCState() {
        if (!peerConnection) {
            return;
        }
        try {
            const params = new URLSearchParams({
                camera_ice_from: String(cameraIceIndex),
                known_offer_id: String(currentOfferId),
                known_answer_id: String(knownAnswerId),
            });
            const response = await fetch(`/api/webrtc/state?${params.toString()}`, { cache: 'no-store' });
            const data = await response.json();
            if (data.answer && !answerApplied) {
                await peerConnection.setRemoteDescription(data.answer);
                answerApplied = true;
                knownAnswerId = Number(data.answer_id || knownAnswerId);
            } else if (typeof data.answer_id === 'number') {
                knownAnswerId = data.answer_id;
            }

            const cameraIce = data.camera_ice || [];
            for (const candidate of cameraIce) {
                try {
                    await peerConnection.addIceCandidate(candidate);
                } catch (error) {
                    console.warn('加入 camera ICE candidate 失敗', error);
                }
            }
            cameraIceIndex = typeof data.camera_ice_total === 'number'
                ? data.camera_ice_total
                : cameraIceIndex + cameraIce.length;
            renderWebRTCStatus(data);
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
            ? '，請按重新連線串流'
            : '';
        setStreamStatus(`WebRTC 狀態：${peerConnection.connectionState} / ICE：${peerConnection.iceConnectionState}${debug}${failedHint}`);
    }

    async function refreshState() {
        try {
            const response = await fetch('/api/state/');
            const data = await response.json();
            lastState = data;
            renderState(data);
            return data;
        } catch (error) {
            setMessage(`狀態同步失敗：${error.message}`);
            return null;
        }
    }

    function statePollDelay(data) {
        if (data && data.active_fruit_id) {
            return stateActivePollMs;
        }
        return stateIdlePollMs;
    }

    function restartStatePolling(delayMs = 0) {
        if (statePollTimer !== null) {
            window.clearTimeout(statePollTimer);
        }
        statePollTimer = window.setTimeout(runStatePollLoop, delayMs);
    }

    async function runStatePollLoop() {
        statePollTimer = null;
        if (document.hidden) {
            return;
        }
        const data = await refreshState();
        restartStatePolling(statePollDelay(data));
    }

    function restartWebRTCPolling(delayMs = 0) {
        if (webrtcPollTimer !== null) {
            window.clearTimeout(webrtcPollTimer);
        }
        webrtcPollTimer = window.setTimeout(runWebRTCPollLoop, delayMs);
    }

    async function runWebRTCPollLoop() {
        webrtcPollTimer = null;
        if (document.hidden) {
            return;
        }
        await pollWebRTCState();
        restartWebRTCPolling(webrtcPollMs);
    }

    document.addEventListener('visibilitychange', () => {
        if (document.hidden) {
            if (statePollTimer !== null) {
                window.clearTimeout(statePollTimer);
                statePollTimer = null;
            }
            if (webrtcPollTimer !== null) {
                window.clearTimeout(webrtcPollTimer);
                webrtcPollTimer = null;
            }
            return;
        }
        restartStatePolling(0);
        restartWebRTCPolling(0);
    });

    function renderState(data) {
        const images = data.latest_images || [];
        activeFruitEl.textContent = data.active_fruit_id || '無暫存資料';
        nextFruitEl.textContent = data.next_fruit_id || 'fruit_001';
        stateLabelEl.textContent = data.status || 'idle';
        imageCountEl.textContent = `${data.image_total ?? images.length} / ${data.image_count || 3}`;
        esp32StatusEl.textContent = data.esp32_online ? '在線' : '離線';
        const motorCommand = data.motor_command || {};
        motorCommandEl.textContent = motorCommand.command && motorCommand.command !== 'none'
            ? `${motorCommand.command} #${motorCommand.command_id || 0}`
            : 'none';
        renderStationStatuses(data.station_statuses || {});
        errorReasonEl.textContent = data.last_error_reason || '無';
        setMessage(data.message || '');
        renderThumbnails(images);
        renderTiming(data.timing || {});
        renderCaptureTiming(data);
        renderTransitionTrace(data);

        classifyButtons.forEach((button) => {
            button.disabled = !data.can_classify;
        });
        discardButton.disabled = !data.can_discard;
        manualCaptureButton.disabled = !data.can_manual_capture;
        recaptureButton.disabled = !data.can_recapture;
    }

    function renderStationStatuses(stationStatuses) {
        stationStatusEls.forEach((element, index) => {
            element.textContent = stationStatuses[String(index + 1)] || 'pending';
        });
    }

    function renderThumbnails(images) {
        const manifest = JSON.stringify(images.map((image) => [image.filename, image.url]));
        if (manifest === lastThumbnailManifest) {
            return;
        }
        lastThumbnailManifest = manifest;
        thumbnailGrid.innerHTML = '';
        thumbnailEmpty.style.display = images.length ? 'none' : 'grid';
        if (!images.length) {
            closeImagePreview();
        }
        images.forEach((image) => {
            const item = document.createElement('button');
            item.className = 'thumb';
            item.type = 'button';
            item.setAttribute('aria-label', `預覽 ${image.filename}`);

            const media = document.createElement('div');
            media.className = 'thumb-media';
            const img = document.createElement('img');
            img.src = image.url;
            img.alt = image.filename;

            const label = document.createElement('span');
            label.textContent = image.filename;

            media.appendChild(img);
            item.appendChild(media);
            item.appendChild(label);
            item.addEventListener('click', () => openImagePreview(image));
            thumbnailGrid.appendChild(item);
        });
    }

    function openImagePreview(image) {
        imageDialogTitle.textContent = image.filename || '照片預覽';
        imageDialogImage.src = image.url;
        imageDialogImage.alt = image.filename || '照片預覽';
        if (!imageDialog.open) {
            imageDialog.showModal();
        }
    }

    function closeImagePreview() {
        if (imageDialog.open) {
            imageDialog.close();
            return;
        }
        imageDialogImage.removeAttribute('src');
        imageDialogImage.src = '';
    }

    function releaseThumbnailFileHandles() {
        lastThumbnailManifest = '';
        closeImagePreview();
        thumbnailGrid.querySelectorAll('img').forEach((img) => {
            img.removeAttribute('src');
            img.src = '';
        });
        thumbnailGrid.innerHTML = '';
        thumbnailEmpty.style.display = 'grid';
    }

    function sleep(ms) {
        return new Promise((resolve) => window.setTimeout(resolve, ms));
    }

    async function releaseThumbnailsBeforeFileOperation() {
        releaseThumbnailFileHandles();
        await sleep(120);
    }

    function warningText(payload) {
        const warnings = (payload && payload.delete_warnings) || [];
        return warnings.length ? `；注意：部分舊資料已移到 _delete_pending 或標記忽略：${warnings.join('；')}` : '';
    }

    function discardResultText(payload) {
        const fruitId = payload.discarded_fruit_id || '目前資料';
        switch (payload.discard_mode) {
            case 'quarantined':
                return `${fruitId} 已隔離等待背景清理，可立即拍攝下一顆。`;
            case 'deferred_cleanup':
                return `${fruitId} 仍被占用，已跳過並等待背景清理；下一筆為 ${payload.next_fruit_id || '新的 fruit ID'}。`;
            default:
                return `${fruitId} 已刪除。`;
        }
    }

    function renderTiming(timing) {
        const delay = timing.command_to_phone_start_ms;
        timingCommandDelay.textContent = typeof delay === 'number' ? `${delay} ms` : '尚無資料';
        timingWaitStarted.textContent = timing.wait_started_at || '尚無資料';
        timingUploadReceived.textContent = timing.upload_received_at || '尚無資料';
    }

    function recommendedCaptureTiming(data = lastState || {}) {
        return data.capture_timing_recommended || {
            first_station_settle_ms: 300,
            servo_settle_ms: 200,
            fruit_settle_ms: 350,
            final_gate_return_delay_ms: 300,
        };
    }

    function setTimingInputs(timing) {
        timingFirstStationInput.value = timing.first_station_settle_ms ?? 300;
        timingServoInput.value = timing.servo_settle_ms ?? 200;
        timingFruitInput.value = timing.fruit_settle_ms ?? 350;
        timingFinalReturnInput.value = timing.final_gate_return_delay_ms ?? 300;
    }

    function timingStatusText(data) {
        const revision = data.capture_timing_revision ?? 0;
        const appliedRevision = data.capture_timing_applied_revision ?? 0;
        switch (data.capture_timing_status) {
            case 'applied':
                return `ESP32 已套用 revision ${revision}。`;
            case 'pending_esp32_apply':
                return `設定 revision ${revision} 已儲存，等待 ESP32 在閒置時套用（目前已套用 ${appliedRevision}）。`;
            case 'waiting_esp32':
                return `設定 revision ${revision} 已儲存，等待 ESP32 連線並套用。`;
            default:
                return '等待 Django 與 ESP32 同步拍攝停穩設定。';
        }
    }

    function renderCaptureTiming(data) {
        const timing = data.capture_timing || recommendedCaptureTiming(data);
        if (!timingInputsDirty && !timingUpdateInFlight) {
            setTimingInputs(timing);
        }
        const motorCommand = data.motor_command || {};
        const hasPendingMotorCommand = motorCommand.command && motorCommand.command !== 'none';
        const editable = data.status === 'idle' && !data.active_fruit_id && !hasPendingMotorCommand;
        timingInputs.forEach((input) => {
            input.disabled = !editable || timingUpdateInFlight;
        });
        applyTimingButton.disabled = !editable || timingUpdateInFlight;
        resetTimingButton.disabled = !editable || timingUpdateInFlight;
        timingConfigStatus.textContent = timingStatusText(data);
    }

    function readTimingInput(input, key, minimum) {
        const value = Number(input.value);
        if (!Number.isInteger(value) || value < minimum || value > 3000 || value % 50 !== 0) {
            throw new Error(`${key} 必須介於 ${minimum} 到 3000 ms，且以 50 ms 為間距。`);
        }
        return value;
    }

    function captureTimingPayloadFromInputs() {
        return {
            first_station_settle_ms: readTimingInput(timingFirstStationInput, '第 1 站停穩時間', 50),
            servo_settle_ms: readTimingInput(timingServoInput, '伺服穩定時間', 50),
            fruit_settle_ms: readTimingInput(timingFruitInput, '到站停穩時間', 50),
            final_gate_return_delay_ms: readTimingInput(timingFinalReturnInput, '最終歸位延遲', 0),
        };
    }

    function restoreRecommendedTiming() {
        setTimingInputs(recommendedCaptureTiming());
        timingInputsDirty = true;
        setMessage('已填入校正預設 300／200／350／300 ms，按下「套用停穩設定」後才會儲存。');
    }

    async function applyCaptureTiming() {
        let timing;
        try {
            timing = captureTimingPayloadFromInputs();
        } catch (error) {
            setMessage(`停穩設定無效：${error.message}`);
            return;
        }

        timingUpdateInFlight = true;
        if (lastState) {
            renderCaptureTiming(lastState);
        }
        try {
            const payload = await postJson('/api/capture_timing/', timing);
            timingInputsDirty = false;
            lastState = payload;
            renderState(payload);
            setMessage(payload.capture_timing_unchanged
                ? '停穩設定沒有變更。'
                : '停穩設定已儲存，等待 ESP32 閒置時套用。');
        } catch (error) {
            if (error.payload) {
                lastState = error.payload;
                renderState(error.payload);
            }
            setMessage(`套用停穩設定失敗：${error.message}`);
        } finally {
            timingUpdateInFlight = false;
            if (lastState) {
                renderCaptureTiming(lastState);
            }
        }
    }

    function transitionTraceFromState(data) {
        const candidates = [
            data.transition_trace,
            data.trace,
            data.timing && data.timing.transition_trace,
            data.timing && data.timing.trace,
        ];
        return candidates.find((candidate) => Array.isArray(candidate)) || [];
    }

    function traceEventLabel(eventName) {
        const labels = {
            hcsr04_trigger: 'HC-SR04 觸發',
            hcsr04_trigger_received: 'Django 已收到 HC-SR04 觸發',
            hcsr04_station_1_ready: 'HC-SR04 首站捷徑就緒',
            hcsr04_station_1_ready_received: 'Django 已收到首站捷徑就緒',
            hcsr04_station_1_ready_duplicate: '首站捷徑重送已去重',
            fast_path_station_1_capture_requested: '首站捷徑已開放手機拍攝',
            fast_path_fallback_to_legacy: '首站捷徑回退標準流程',
            start_sequence: '三站流程啟動',
            capture_session_created: '拍攝工作階段已建立',
            motor_command_issued: 'Django 已發出馬達命令',
            station_1_ready: '第 1 站已就緒',
            station_2_ready: '第 2 站已就緒',
            station_3_ready: '第 3 站已就緒',
            capture_requested: '手機拍攝請求已開放',
            station_capture_requested: '站點已開放手機拍攝',
            phone_capture_started: '手機開始拍攝',
            client_timing_received: '手機端 timing 已收到',
            upload_received: '照片已收到',
            image_saved: '照片已原子保存',
            station_image_saved: '站點照片已原子保存',
            release_gate_1: '放行 Gate 1',
            release_gate_2: '放行 Gate 2',
            release_gate_3: '放行 Gate 3',
            capture_sequence_finished: '三站流程完成',
            capture_timing_updated: '拍攝停穩設定已更新',
            timing_config_applied: 'ESP32 已套用拍攝停穩設定',
            fruit_discarded: '暫存資料已跳過／刪除',
        };
        return labels[eventName] || eventName || '未命名事件';
    }

    function traceMetaText(entry) {
        const parts = [];
        if (entry.at) {
            parts.push(entry.at);
        }
        if (entry.fruit_id) {
            parts.push(entry.fruit_id);
        }
        if (entry.station_index) {
            parts.push(`第 ${entry.station_index} 站`);
        }
        if (entry.command_id) {
            parts.push(`command #${entry.command_id}`);
        }
        if (entry.trigger_id) {
            parts.push(`trigger ${entry.trigger_id}`);
        }
        if (entry.details && typeof entry.details === 'object') {
            const detailText = Object.entries(entry.details)
                .filter(([, value]) => ['string', 'number', 'boolean'].includes(typeof value))
                .slice(0, 3)
                .map(([key, value]) => `${key}=${value}`)
                .join('，');
            if (detailText) {
                parts.push(detailText);
            }
        } else if (typeof entry.details === 'string' && entry.details) {
            parts.push(entry.details);
        }
        return parts.length ? parts.join('｜') : '尚無附加資料';
    }

    function renderTransitionTrace(data) {
        const trace = transitionTraceFromState(data);
        transitionTraceEl.replaceChildren();
        if (!trace.length) {
            const empty = document.createElement('li');
            empty.className = 'trace-empty';
            empty.textContent = '尚無流程事件；舊版後端不提供 trace 時會維持此顯示。';
            transitionTraceEl.appendChild(empty);
            return;
        }

        trace.slice(-12).reverse().forEach((rawEntry) => {
            const entry = rawEntry && typeof rawEntry === 'object'
                ? rawEntry
                : { event: String(rawEntry || '未命名事件') };
            const item = document.createElement('li');
            item.className = 'trace-entry';

            const event = document.createElement('strong');
            event.className = 'trace-event';
            event.textContent = traceEventLabel(entry.event || entry.name);

            const meta = document.createElement('span');
            meta.className = 'trace-meta';
            meta.textContent = traceMetaText(entry);

            item.append(event, meta);
            transitionTraceEl.appendChild(item);
        });
    }

    async function setCounter() {
        const value = counterInput.value.trim();
        if (!value) {
            setMessage('請先輸入起始 ID。');
            return;
        }
        try {
            const payload = await postJson('/api/set_counter/', { start_id: value });
            counterInput.value = '';
            setMessage(`起始 ID 已設定，下一筆為 ${payload.next_fruit_id}。`);
            await refreshState();
        } catch (error) {
            setMessage(`設定失敗：${error.message}`);
        }
    }

    async function manualCapture() {
        try {
            const payload = await postJson('/api/manual_capture/');
            setMessage(`已建立 ${payload.fruit_id}，等待 ESP32 啟動三站流程。`);
            await refreshState();
        } catch (error) {
            setMessage(`拍攝命令失敗：${error.message}`);
        }
    }

    async function recaptureCurrent() {
        if (!lastState || !lastState.active_fruit_id) {
            return;
        }
        try {
            const payload = await postJson('/api/recapture/');
            lastState = payload;
            renderState(payload);
            setMessage(`已重新啟動 ${payload.active_fruit_id || '目前 fruit'} 三站流程。`);
            await refreshState();
        } catch (error) {
            setMessage(`重新拍攝失敗：${error.message}`);
        }
    }

    async function classify(label) {
        try {
            await releaseThumbnailsBeforeFileOperation();
            const payload = await postJson('/api/classify/', {
                label,
                note: noteInput.value.trim(),
            });
            noteInput.value = '';
            setMessage(`${payload.fruit_id} 已分類到 ${payload.path}。${warningText(payload)}`);
            await refreshState();
        } catch (error) {
            if (error.payload) {
                lastState = error.payload;
                renderState(error.payload);
            }
            setMessage(`分類失敗：${error.message}`);
        }
    }

    async function discardCurrent() {
        if (!lastState || !lastState.active_fruit_id) {
            return;
        }
        if (!confirm(`確定刪除 ${lastState.active_fruit_id} 的暫存照片嗎？`)) {
            return;
        }
        try {
            await releaseThumbnailsBeforeFileOperation();
            const payload = await postJson('/api/discard/');
            lastState = payload;
            renderState(payload);
            setMessage(`${discardResultText(payload)}${warningText(payload)}`);
            await refreshState();
        } catch (error) {
            if (error.payload) {
                lastState = error.payload;
                renderState(error.payload);
            } else {
                await refreshState();
            }
            setMessage(`刪除失敗：${error.message}`);
        }
    }

    async function resetDataset() {
        if (!confirm('確定要重置整個 dataset 嗎？這會刪除 temp 與所有分類資料夾內的照片，並清空 metadata.csv。')) {
            return;
        }
        if (!confirm('再次確認：此動作無法復原，下一筆 ID 會回到 fruit_001。')) {
            return;
        }
        try {
            await releaseThumbnailsBeforeFileOperation();
            const payload = await postJson('/api/reset_dataset/');
            lastState = payload;
            renderState(payload);
            setMessage(`dataset 已重置，下一筆資料將從 fruit_001 開始。${warningText(payload)}`);
            await refreshState();
        } catch (error) {
            if (error.payload) {
                lastState = error.payload;
                renderState(error.payload);
            }
            setMessage(`重置 dataset 失敗：${error.message}`);
        }
    }

    async function openDatasetFolder() {
        try {
            const response = await fetch('/api/open_dataset_folder/');
            const payload = await response.json();
            if (payload.opened) {
                setMessage(`已開啟資料夾：${payload.path}`);
            } else {
                setMessage(`資料夾位置：${payload.path}`);
            }
        } catch (error) {
            setMessage(`開啟資料夾失敗：${error.message}`);
        }
    }

    document.getElementById('btn-set-counter').addEventListener('click', setCounter);
    timingInputs.forEach((input) => {
        input.addEventListener('input', () => {
            timingInputsDirty = true;
        });
    });
    resetTimingButton.addEventListener('click', restoreRecommendedTiming);
    applyTimingButton.addEventListener('click', applyCaptureTiming);
    manualCaptureButton.addEventListener('click', manualCapture);
    recaptureButton.addEventListener('click', recaptureCurrent);
    document.getElementById('btn-open-folder').addEventListener('click', openDatasetFolder);
    discardButton.addEventListener('click', discardCurrent);
    resetDatasetButton.addEventListener('click', resetDataset);
    document.getElementById('btn-reconnect').addEventListener('click', () => {
        startWebRTC().catch((error) => setStreamStatus(`串流重連失敗：${error.message}`));
    });
    closeImageDialogButton.addEventListener('click', closeImagePreview);
    imageDialog.addEventListener('click', (event) => {
        if (event.target === imageDialog) {
            closeImagePreview();
        }
    });
    imageDialog.addEventListener('close', () => {
        imageDialogImage.removeAttribute('src');
        imageDialogImage.src = '';
    });
    classifyButtons.forEach((button) => {
        button.addEventListener('click', () => classify(button.dataset.label));
    });

    startWebRTC().catch((error) => setStreamStatus(`WebRTC 初始化失敗：${error.message}`));
    restartStatePolling(0);
    restartWebRTCPolling(0);
