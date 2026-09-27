const remoteVideo = document.getElementById('remote-video');
    const remoteVideoWrap = document.getElementById('remote-video-wrap');
    const videoPlaceholder = document.getElementById('video-placeholder');
    const streamStatus = document.getElementById('stream-status');
    const previewStatus = document.getElementById('preview-status');
    const previewIndicator = document.getElementById('preview-indicator');
    const messageEl = document.getElementById('message');
    const activeFruitEl = document.getElementById('active-fruit');
    const nextFruitEl = document.getElementById('next-fruit');
    const stateLabelEl = document.getElementById('state-label');
    const imageCountEl = document.getElementById('image-count');
    const esp32StatusEl = document.getElementById('esp32-status');
    const esp32Indicator = document.getElementById('esp32-indicator');
    const cameraReadyStatusEl = document.getElementById('camera-ready-status');
    const cameraReadyIndicator = document.getElementById('camera-ready-indicator');
    const feederSensorStateEl = document.getElementById('feeder-sensor-state');
    const feederSensorIndicator = document.getElementById('feeder-sensor-indicator');
    const feederTestResultEl = document.getElementById('feeder-test-result');
    const motorCommandEl = document.getElementById('motor-command');
    const sorterStatusEl = document.getElementById('sorter-status');
    const sorterFruitEl = document.getElementById('sorter-fruit');
    const sorterLabelEl = document.getElementById('sorter-label');
    const sorterErrorEl = document.getElementById('sorter-error');
    const stationStatusEls = [
        document.getElementById('station-1-status'),
        document.getElementById('station-2-status'),
        document.getElementById('station-3-status'),
    ];
    const stationCards = [1, 2, 3].map((index) => document.getElementById(`station-card-${index}`));
    const stationImages = [1, 2, 3].map((index) => document.getElementById(`station-image-${index}`));
    const stationFilenames = [1, 2, 3].map((index) => document.getElementById(`station-filename-${index}`));
    const stationProgressLabel = document.getElementById('station-progress-label');
    const errorReasonEl = document.getElementById('error-reason');
    const rawStateLabelEl = document.getElementById('raw-state-label');
    const stateBadge = document.getElementById('state-badge');
    const thumbnailEmpty = document.getElementById('thumbnail-empty');
    const counterInput = document.getElementById('counter-input');
    const noteInput = document.getElementById('note');
    const classifyButtons = Array.from(document.querySelectorAll('.classify-button'));
    const workModeSelect = document.getElementById('work-mode');
    const hardwareModeSelect = document.getElementById('hardware-mode');
    const classificationPanel = document.getElementById('classification-panel');
    const detectionPanel = document.getElementById('detection-panel');
    const detectionResults = document.getElementById('detection-results');
    const detectionSummary = document.getElementById('detection-summary');
    const optionsGuidance = document.getElementById('options-guidance');
    const autoRunButton = document.getElementById('btn-auto-run');
    const autoRunGuidance = document.getElementById('auto-run-guidance');
    const recaptureButton = document.getElementById('btn-recapture');
    const discardButton = document.getElementById('btn-discard');
    const resetDatasetButton = document.getElementById('btn-reset-dataset');
    const setCounterButton = document.getElementById('btn-set-counter');
    const readinessItems = {
        esp32: document.getElementById('readiness-esp32'),
        camera: document.getElementById('readiness-camera'),
        sensor: document.getElementById('readiness-sensor'),
        timing: document.getElementById('readiness-timing'),
        calibration: document.getElementById('readiness-calibration'),
    };
    const operatorAlert = document.getElementById('operator-alert');
    const operatorAlertReason = document.getElementById('operator-alert-reason');
    const operatorAlertLocation = document.getElementById('operator-alert-location');
    const operatorAlertFruit = document.getElementById('operator-alert-fruit');
    const operatorAlertCommand = document.getElementById('operator-alert-command');
    const operatorAlertFeederWrap = document.getElementById('operator-alert-feeder-wrap');
    const operatorAlertFeeder = document.getElementById('operator-alert-feeder');
    const operatorAlertInstruction = document.getElementById('operator-alert-instruction');
    const timingCommandDelay = document.getElementById('timing-command-delay');
    const timingWaitStarted = document.getElementById('timing-wait-started');
    const timingUploadReceived = document.getElementById('timing-upload-received');
    const transitionTraceEl = document.getElementById('transition-trace');
    const timingFirstStationInput = document.getElementById('timing-first-station');
    const timingServoInput = document.getElementById('timing-servo');
    const timingFruitInput = document.getElementById('timing-fruit');
    const timingFinalReturnInput = document.getElementById('timing-final-return');
    const timingIdleCommandPollInput = document.getElementById('timing-idle-command-poll');
    const timingFeederStopInput = document.getElementById('timing-feeder-stop');
    const timingFeederDriveInput = document.getElementById('timing-feeder-drive');
    const timingFeederMaxRunInput = document.getElementById('timing-feeder-max-run');
    const feederCalibratedInput = document.getElementById('feeder-calibrated');
    const feederCalibrationStatus = document.getElementById('feeder-calibration-status');
    const feederHelp = document.getElementById('feeder-help');
    const feederHelpButton = document.getElementById('feeder-help-button');
    const captureTimingWarning = document.getElementById('capture-timing-warning');
    const timingSummaryStatus = document.getElementById('timing-summary-status');
    const calibrationSummaryStatus = document.getElementById('calibration-summary-status');
    const timingInputs = [
        timingFirstStationInput,
        timingServoInput,
        timingFruitInput,
        timingFinalReturnInput,
        timingIdleCommandPollInput,
        timingFeederStopInput,
        timingFeederDriveInput,
        timingFeederMaxRunInput,
    ];
    const timingConfigStatus = document.getElementById('timing-config-status');
    const applyTimingButton = document.getElementById('btn-apply-timing');
    const resetTimingButton = document.getElementById('btn-reset-timing');
    const testFeederButton = document.getElementById('btn-test-feeder');
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
    let lastDetectionManifest = '';
    let timingInputsDirty = false;
    let feederSettingsDirty = false;
    let timingUpdateInFlight = false;
    let feederConfirmationInFlight = false;
    let classificationInFlight = false;
    let controlActionInFlight = null;
    let lastAlertFingerprint = '';
    let lastTraceFingerprint = '';
    const stateIdlePollMs = 1000;
    const stateActivePollMs = 500;
    const webrtcPollMs = 1000;
    const RTC_CONFIG = {
        iceServers: [{ urls: 'stun:stun.l.google.com:19302' }],
    };

    document.getElementById('camera-url').textContent = `${window.location.origin}/camera/`;

    function setTextIfChanged(element, value) {
        const nextValue = value == null ? '' : String(value);
        if (element.textContent !== nextValue) {
            element.textContent = nextValue;
        }
    }

    function setMessage(message) {
        setTextIfChanged(messageEl, message || '');
    }

    function setStreamStatus(message) {
        setTextIfChanged(streamStatus, message);
    }

    function setPreviewState(state, label) {
        previewIndicator.dataset.state = state;
        setTextIfChanged(previewStatus, label);
    }

    function setRemoteVideoVisible(visible) {
        remoteVideoWrap.hidden = !visible;
        remoteVideoWrap.classList.toggle('is-streaming', visible);
        videoPlaceholder.hidden = visible;
        setPreviewState(visible ? 'ready' : 'pending', visible ? '預覽已連線' : '等待連線');
    }

    function setElementBusy(element, busy) {
        element.setAttribute('aria-busy', String(busy));
        element.disabled = busy;
    }

    function beginControlAction(name, element) {
        if (controlActionInFlight) {
            return false;
        }
        controlActionInFlight = name;
        setElementBusy(element, true);
        return true;
    }

    function endControlAction(element) {
        controlActionInFlight = null;
        setElementBusy(element, false);
        if (lastState) {
            renderState(lastState);
        }
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
        setPreviewState('pending', '等待手機回覆');
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
        const connected = peerConnection.connectionState === 'connected';
        const failed = ['failed', 'closed', 'disconnected'].includes(peerConnection.connectionState)
            || peerConnection.iceConnectionState === 'failed';
        if (connected) {
            setPreviewState('ready', '預覽已連線');
        } else if (failed) {
            setPreviewState('error', '預覽已中斷');
        } else {
            setPreviewState('pending', '正在連線');
        }
        const debug = data
            ? `，offer:${data.offer_present ? '有' : '無'} #${data.offer_id || 0} answer:${data.answer_present ? '有' : '無'} #${data.answer_id || 0} ICE:${data.dashboard_ice_total || 0}/${data.camera_ice_total || 0}`
            : '';
        const failedHint = failed
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
        if (data && (data.active_fruit_id || data.sorter_busy)) {
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
        const status = data.status || 'idle';
        const photoOnly = data.hardware_mode === 'photo_only';
        const detectionMode = data.work_mode === 'detection';
        workModeSelect.value = data.work_mode || 'collection';
        hardwareModeSelect.value = data.hardware_mode || 'hardware';
        workModeSelect.disabled = Boolean(controlActionInFlight) || !data.can_change_options;
        esp32Indicator.hidden = photoOnly;
        feederSensorIndicator.hidden = photoOnly;
        stationCards.forEach((card, index) => {
            setTextIfChanged(card.querySelector('.station-card-heading span'),
                `第 ${index + 1} ${photoOnly ? '張' : '站'}`);
        });
        hardwareModeSelect.disabled = Boolean(controlActionInFlight) || !data.can_change_options;
        hardwareModeSelect.querySelector('option[value="hardware"]').disabled = detectionMode;
        classificationPanel.hidden = detectionMode;
        detectionPanel.hidden = !detectionMode;
        setTextIfChanged(optionsGuidance, data.options_disabled_reason
            ? `目前無法切換：${data.options_disabled_reason}` : '開始後，本輪選項固定。');
        recaptureButton.hidden = photoOnly;
        setTextIfChanged(activeFruitEl, data.active_fruit_id || '無暫存資料');
        setTextIfChanged(nextFruitEl, data.next_fruit_id || 'fruit_001');
        setTextIfChanged(stateLabelEl, captureStatusText(status));
        setTextIfChanged(rawStateLabelEl, status);
        stateBadge.dataset.state = captureStatusTone(status);
        setTextIfChanged(imageCountEl, `${data.image_total ?? images.length} / ${data.image_count || 3}`);

        setTextIfChanged(esp32StatusEl, data.esp32_online ? '在線' : '離線');
        esp32Indicator.dataset.state = data.esp32_online ? 'online' : 'offline';
        setTextIfChanged(cameraReadyStatusEl, data.camera_ready ? '可拍攝' : '未就緒');
        cameraReadyIndicator.dataset.state = data.camera_ready ? 'ready' : 'offline';
        const sensor = sensorStatus(data.feeder_sensor_state);
        setTextIfChanged(feederSensorStateEl, sensor.label);
        feederSensorIndicator.dataset.state = sensor.tone;

        const feederResult = data.feeder_test_result;
        setTextIfChanged(feederTestResultEl, feederResult
            ? `${feederResult.elapsed_ms} / ${feederResult.max_run_ms} ms｜${feederResult.stop_reason}`
            : '尚無資料');
        const motorCommand = data.motor_command || {};
        setTextIfChanged(motorCommandEl, motorCommand.command && motorCommand.command !== 'none'
            ? `${motorCommand.command} #${motorCommand.command_id || 0}`
            : 'none');
        setTextIfChanged(sorterStatusEl, sorterStatusText(data.sorter_status));
        setTextIfChanged(sorterFruitEl, data.sorter_fruit_id || '無');
        setTextIfChanged(sorterLabelEl, data.sorter_label || '無');
        setTextIfChanged(sorterErrorEl, data.sorter_error || '無');
        setTextIfChanged(errorReasonEl, data.last_error_reason || '無');

        renderReadiness(data);
        renderOperatorAlert(data.operator_alert);
        setMessage(data.message || '');
        renderStationStatuses(data.station_statuses || {}, data.active_station_index, status);
        setTextIfChanged(thumbnailEmpty, photoOnly
            ? '手機相機就緒後，按「拍攝本輪三張」自動保存三張原圖。'
            : '開始自動運轉後，HC-SR04 會啟動三閘門流程，手機每站上傳 1 張照片。');
        renderThumbnails(images, photoOnly);
        renderDetection(data);
        renderTiming(data.timing || {});
        renderCaptureTiming(data);
        renderTransitionTrace(data);

        classifyButtons.forEach((button) => {
            button.disabled = Boolean(controlActionInFlight)
                || classificationInFlight
                || data.sorter_busy
                || !data.can_classify;
        });
        discardButton.disabled = Boolean(controlActionInFlight)
            || classificationInFlight
            || !data.can_discard;
        if (photoOnly) {
            setTextIfChanged(autoRunButton, '拍攝本輪三張');
            autoRunButton.dataset.mode = 'photo';
            autoRunButton.disabled = Boolean(controlActionInFlight) || !data.can_start_photo_capture;
        } else if (data.auto_run_enabled) {
            setTextIfChanged(autoRunButton, '優雅暫停');
            autoRunButton.dataset.mode = 'pause';
            autoRunButton.disabled = Boolean(controlActionInFlight);
        } else if (data.auto_run_finishing) {
            setTextIfChanged(autoRunButton, '正在完成目前果實');
            autoRunButton.dataset.mode = 'finishing';
            autoRunButton.disabled = true;
        } else {
            setTextIfChanged(autoRunButton, '開始執行');
            autoRunButton.dataset.mode = 'start';
            autoRunButton.disabled = Boolean(controlActionInFlight)
                || !data.can_start_auto_run
                || timingInputsDirty;
        }
        setTextIfChanged(autoRunGuidance, autoRunGuidanceText(data));
        recaptureButton.disabled = Boolean(controlActionInFlight)
            || data.sorter_busy
            || !data.can_recapture;
        resetDatasetButton.disabled = Boolean(controlActionInFlight) || !data.can_reset_dataset;
        setCounterButton.disabled = Boolean(controlActionInFlight);
    }

    function captureStatusText(status) {
        const labels = {
            idle: '閒置',
            waiting_feeder: '等待送料',
            waiting_fruit: '等待果實',
            waiting_esp32_start: '等待 ESP32',
            waiting_station_ready: '等待站點',
            waiting_camera: '等待手機拍攝',
            uploading: '上傳中',
            waiting_motor: '等待閘門',
            uploaded: '拍攝完成',
            classified: '分類完成',
            detection_pending: '等待檢測',
            detection_running: '檢測中',
            detection_completed: '檢測完成',
            detection_failed: '檢測未完整',
            incomplete: '照片不完整',
            multiple_temp: '暫存衝突',
            error: '需要處理',
        };
        return labels[status] || '未知狀態';
    }

    function captureStatusTone(status) {
        if (['uploaded', 'classified', 'detection_completed'].includes(status)) {
            return 'completed';
        }
        if (['error', 'incomplete', 'multiple_temp', 'detection_failed'].includes(status)) {
            return 'error';
        }
        if (status === 'idle') {
            return 'ready';
        }
        return 'running';
    }

    function sensorStatus(status) {
        const labels = {
            clear: { label: '淨空', tone: 'ready' },
            blocked: { label: '感測區有果實', tone: 'blocked' },
            unavailable: { label: '無有效回音', tone: 'error' },
        };
        return labels[status] || labels.unavailable;
    }

    function setReadinessState(item, ready, readyText, blockedText) {
        item.dataset.state = ready ? 'ready' : 'blocked';
        setTextIfChanged(item.querySelector('strong'), ready ? readyText : blockedText);
    }

    function renderReadiness(data) {
        const photoOnly = data.hardware_mode === 'photo_only';
        ['esp32', 'sensor', 'timing', 'calibration'].forEach((key) => {
            readinessItems[key].hidden = photoOnly;
        });
        const timing = data.capture_timing || {};
        const missingEsp32Capability = !data.esp32_feeder_capable
            ? '缺少 feeder_v1'
            : '缺少 gate3_sorter_v1';
        setReadinessState(
            readinessItems.esp32,
            Boolean(data.esp32_online && data.esp32_feeder_capable && data.esp32_sorter_capable),
            '在線且支援 feeder_v1／gate3_sorter_v1',
            data.esp32_online ? missingEsp32Capability : 'ESP32 離線',
        );
        setReadinessState(
            readinessItems.camera,
            Boolean(data.camera_ready),
            '可拍攝',
            '手機拍攝端未就緒',
        );
        setReadinessState(
            readinessItems.sensor,
            data.feeder_sensor_state === 'clear',
            '感測區淨空',
            sensorStatus(data.feeder_sensor_state).label,
        );
        setReadinessState(
            readinessItems.timing,
            data.capture_timing_status === 'applied',
            '已同步',
            timingStatusLabel(data.capture_timing_status),
        );
        setReadinessState(
            readinessItems.calibration,
            Boolean(timing.feeder_calibrated),
            '已確認單顆送料',
            '尚未完成校正',
        );
    }

    function timingStatusLabel(status) {
        const labels = {
            applied: '已同步',
            waiting_esp32: '等待 ESP32',
            pending_esp32_apply: '等待套用',
        };
        return labels[status] || '待同步';
    }

    function autoRunGuidanceText(data) {
        if (data.hardware_mode === 'photo_only') {
            const reason = data.photo_capture_disabled_reason;
            return reason
                ? `目前無法開始：${reason}。本輪完成後請分類或刪除。`
                : '按一次自動拍三張；每張保存成功才拍下一張。果實需自行擺位，三張不保證不同視角。';
        }
        if (data.auto_run_enabled) {
            return '目前為連續自動運轉；優雅暫停會讓目前果實完成後停止送入下一顆。';
        }
        if (data.auto_run_finishing) {
            return '正在完成目前果實，完成後不會送入下一顆。';
        }
        if (timingInputsDirty) {
            return '停穩設定尚未套用，請先在進階設定保存數值。';
        }
        const reason = data.auto_run_disabled_reason;
        return reason
            ? `目前無法開始：${reason}`
            : '所有啟動條件已就緒，可以開始正式自動運轉。';
    }

    function renderOperatorAlert(alert) {
        const fingerprint = JSON.stringify(alert || null);
        if (fingerprint === lastAlertFingerprint) {
            return;
        }
        lastAlertFingerprint = fingerprint;
        operatorAlert.hidden = !alert;
        if (!alert) {
            return;
        }
        const feederMetrics = [
            Number.isFinite(alert.feeder_elapsed_ms) ? `實際 ${alert.feeder_elapsed_ms} ms` : '',
            Number.isFinite(alert.feeder_max_run_ms) ? `上限 ${alert.feeder_max_run_ms} ms` : '',
            alert.feeder_stop_reason ? `停止原因 ${alert.feeder_stop_reason}` : '',
        ].filter(Boolean).join('｜');
        setTextIfChanged(operatorAlertReason, alert.reason || 'unknown');
        setTextIfChanged(operatorAlertLocation, alert.location || 'unknown');
        setTextIfChanged(operatorAlertFruit, alert.fruit_id || '無');
        setTextIfChanged(
            operatorAlertCommand,
            `${alert.command || 'none'} #${alert.command_id || 0}`,
        );
        operatorAlertFeederWrap.hidden = !feederMetrics;
        setTextIfChanged(operatorAlertFeeder, feederMetrics || '無');
        setTextIfChanged(
            operatorAlertInstruction,
            alert.instruction
                || '暫停 → 排除／重新拍攝／刪除 → 開始執行（請自行暫停、排除狀況後重新開始）',
        );
    }

    function sorterStatusText(status) {
        const labels = {
            idle: '閒置',
            pending: '等待 ESP32',
            running: 'MG996R 執行中',
            completed: '控制流程完成',
            failed: '硬體分類器失敗',
            timeout: '硬體分類器逾時',
        };
        return labels[status] || status || '閒置';
    }

    function renderStationStatuses(stationStatuses, activeStationIndex, status) {
        let completed = 0;
        stationStatusEls.forEach((element, index) => {
            const stationIndex = index + 1;
            const captured = stationStatuses[String(stationIndex)] === 'captured';
            const active = !captured
                && Number(activeStationIndex) === stationIndex
                && !['idle', 'uploaded', 'classified'].includes(status);
            const label = captured ? '已完成' : active ? '進行中' : '等待';
            const tone = captured ? 'completed' : active ? 'active' : 'pending';
            completed += captured ? 1 : 0;
            setTextIfChanged(element, label);
            stationCards[index].dataset.state = tone;
        });
        setTextIfChanged(stationProgressLabel, `${completed}／3 完成`);
    }

    function renderThumbnails(images, photoOnly = false) {
        const captureUnit = photoOnly ? '張' : '站';
        const manifest = JSON.stringify(images.map((image) => [image.filename, image.url]));
        if (manifest === lastThumbnailManifest) {
            return;
        }
        lastThumbnailManifest = manifest;
        stationCards.forEach((card, index) => {
            const imageElement = stationImages[index];
            imageElement.removeAttribute('src');
            imageElement.src = '';
            imageElement.alt = '';
            imageElement.hidden = true;
            card.querySelector('.station-placeholder').hidden = false;
            card.disabled = true;
            card.onclick = null;
            card.setAttribute('aria-label', `第 ${index + 1} ${captureUnit}尚未拍攝`);
            setTextIfChanged(stationFilenames[index], '等待照片');
        });
        thumbnailEmpty.hidden = Boolean(images.length);
        if (!images.length) {
            closeImagePreview();
        }
        images.forEach((image, imageIndex) => {
            const filenameMatch = String(image.filename || '').match(/img_0?([1-3])/i);
            const stationIndex = filenameMatch ? Number(filenameMatch[1]) - 1 : imageIndex;
            if (stationIndex < 0 || stationIndex >= stationCards.length) {
                return;
            }
            const card = stationCards[stationIndex];
            const imageElement = stationImages[stationIndex];
            imageElement.src = image.url;
            imageElement.alt = `第 ${stationIndex + 1} ${captureUnit}照片：${image.filename}`;
            imageElement.hidden = false;
            card.querySelector('.station-placeholder').hidden = true;
            card.disabled = false;
            card.setAttribute('aria-label', `預覽第 ${stationIndex + 1} ${captureUnit}照片 ${image.filename}`);
            card.onclick = () => openImagePreview(image);
            setTextIfChanged(stationFilenames[stationIndex], image.filename);
        });
    }

    function confidenceText(value) {
        return Number.isFinite(Number(value))
            ? `｜信心值 ${(Number(value) * 100).toFixed(1)}%`
            : '';
    }

    function detectionJudgement(stage, image) {
        if (stage.key === 'roi') {
            const roi = image.roi || {};
            return roi.status === 'ok'
                ? `已偵測 ROI${confidenceText(roi.confidence)}`
                : `ROI 不可用：${roi.reason || '尚未產生'}`;
        }
        const model = (image.models || {})[stage.key] || {};
        if (model.status !== 'ok') {
            return `判定失敗：${model.reason || '尚未執行'}`;
        }
        if (stage.key !== 'defect') {
            return `${model.display_label || model.class_name || '未知'}${confidenceText(model.confidence)}`;
        }
        const detections = model.detections || [];
        if (!detections.length) {
            return '未偵測到局部瑕疵｜面積比例 0%';
        }
        return detections.map((item) => {
            const ratio = Number.isFinite(Number(item.mask_area_ratio))
                ? `｜面積 ${(Number(item.mask_area_ratio) * 100).toFixed(2)}%`
                : '';
            return `${item.class_name || '未知瑕疵'}${confidenceText(item.confidence)}${ratio}`;
        }).join('；');
    }

    function renderDetection(data) {
        const result = data.detection_result;
        const manifest = JSON.stringify([
            data.detection_status, data.detection_error, result,
        ]);
        if (manifest === lastDetectionManifest) {
            return;
        }
        lastDetectionManifest = manifest;
        detectionResults.replaceChildren();

        const statusLabels = {
            idle: '等待本輪三張照片',
            capturing: '拍攝中',
            pending: '等待批次檢測',
            running: '四模型檢測中',
            completed: '檢測與保存完成',
            failed: '檢測未完整完成',
        };
        setTextIfChanged(
            detectionSummary,
            statusLabels[data.detection_status] || data.detection_status || '等待',
        );
        const stages = [
            { key: 'roi', title: 'ROI', artifact: 'roi_annotated' },
            { key: 'color', title: 'color', artifact: 'masked_roi' },
            { key: 'wrinkle', title: 'wrinkle', artifact: 'wrinkle_gray' },
            { key: 'defect', title: 'defect', artifact: 'defect_annotated' },
        ];
        const pendingText = data.detection_error
            ? `未產生結果：${data.detection_error}`
            : (statusLabels[data.detection_status] || '等待檢測');
        const images = result?.images || Object.fromEntries(
            [1, 2, 3].map((index) => [`img_0${index}.jpg`, {}]),
        );
        Object.entries(images).forEach(
            ([filename, image], imageIndex) => {
                const group = document.createElement('section');
                group.className = 'detection-group';
                const heading = document.createElement('h3');
                heading.textContent = `照片 ${imageIndex + 1}｜${filename}`;
                group.append(heading);
                const grid = document.createElement('div');
                grid.className = 'detection-grid';

                stages.forEach((stage) => {
                    const card = document.createElement('article');
                    card.className = 'detection-card';
                    const title = document.createElement('h4');
                    title.textContent = stage.title;
                    card.append(title);
                    const media = document.createElement('div');
                    media.className = 'detection-media';
                    const urls = image.artifact_urls || {};
                    const url = urls[stage.artifact]
                        || (stage.key === 'roi' ? urls.original : null);
                    if (url) {
                        const button = document.createElement('button');
                        button.type = 'button';
                        button.className = 'detection-image-button';
                        const preview = document.createElement('img');
                        preview.src = url;
                        preview.alt = `照片 ${imageIndex + 1} ${stage.title}：${filename}`;
                        button.append(preview);
                        button.addEventListener('click', () => openImagePreview({
                            filename: `${filename}｜${stage.title}`,
                            url,
                        }));
                        media.append(button);
                    } else {
                        const placeholder = document.createElement('span');
                        placeholder.className = 'detection-placeholder';
                        placeholder.textContent = result ? '影像不可用' : pendingText;
                        media.append(placeholder);
                    }
                    card.append(media);
                    const judgement = document.createElement('p');
                    judgement.className = 'detection-judgement';
                    judgement.textContent = result
                        ? detectionJudgement(stage, image) : pendingText;
                    card.append(judgement);
                    grid.append(card);
                });
                group.append(grid);
                detectionResults.append(group);
            },
        );
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
        const captureUnit = lastState && lastState.hardware_mode === 'photo_only' ? '張' : '站';
        lastThumbnailManifest = '';
        closeImagePreview();
        stationImages.forEach((imageElement, index) => {
            imageElement.removeAttribute('src');
            imageElement.src = '';
            imageElement.alt = '';
            imageElement.hidden = true;
            stationCards[index].querySelector('.station-placeholder').hidden = false;
            stationCards[index].disabled = true;
            stationCards[index].onclick = null;
            stationCards[index].setAttribute('aria-label', `第 ${index + 1} ${captureUnit}尚未拍攝`);
            setTextIfChanged(stationFilenames[index], '等待照片');
        });
        thumbnailEmpty.hidden = false;
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
        setTextIfChanged(timingCommandDelay, typeof delay === 'number' ? `${delay} ms` : '尚無資料');
        setTextIfChanged(timingWaitStarted, timing.wait_started_at || '尚無資料');
        setTextIfChanged(timingUploadReceived, timing.upload_received_at || '尚無資料');
    }

    function recommendedCaptureTiming(data = lastState || {}) {
        return data.capture_timing_recommended || {
            first_station_settle_ms: 300,
            servo_settle_ms: 200,
            fruit_settle_ms: 350,
            final_gate_return_delay_ms: 300,
            idle_command_poll_interval_ms: 250,
            feeder_stop_us: 1500,
            feeder_drive_us: 1300,
            feeder_max_run_ms: 5000,
            feeder_calibrated: false,
        };
    }

    function setTimingInputs(timing) {
        timingFirstStationInput.value = timing.first_station_settle_ms ?? 300;
        timingServoInput.value = timing.servo_settle_ms ?? 200;
        timingFruitInput.value = timing.fruit_settle_ms ?? 350;
        timingFinalReturnInput.value = timing.final_gate_return_delay_ms ?? 300;
        timingIdleCommandPollInput.value = timing.idle_command_poll_interval_ms ?? 250;
        timingFeederStopInput.value = timing.feeder_stop_us ?? 1500;
        timingFeederDriveInput.value = timing.feeder_drive_us ?? 1300;
        timingFeederMaxRunInput.value = timing.feeder_max_run_ms ?? 5000;
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
        if (!feederConfirmationInFlight) {
            feederCalibratedInput.checked = Boolean(
                timing.feeder_calibrated && !feederSettingsDirty,
            );
        }
        const motorCommand = data.motor_command || {};
        const hasPendingMotorCommand = motorCommand.command && motorCommand.command !== 'none';
        const editable = (
            data.status === 'idle'
            && !data.active_fruit_id
            && !data.sorter_busy
            && !data.auto_run_enabled
            && !hasPendingMotorCommand
        );
        timingInputs.forEach((input) => {
            input.disabled = !editable || timingUpdateInFlight;
        });
        applyTimingButton.disabled = !editable || timingUpdateInFlight;
        resetTimingButton.disabled = !editable || timingUpdateInFlight;
        testFeederButton.disabled = (
            !data.can_test_feeder
            || timingInputsDirty
            || timingUpdateInFlight
            || feederConfirmationInFlight
        );
        feederCalibratedInput.disabled = (
            !editable
            || timingUpdateInFlight
            || feederConfirmationInFlight
            || timingInputsDirty
            || timing.feeder_calibrated
            || !data.can_confirm_feeder_calibration
        );
        setTextIfChanged(captureTimingWarning, data.capture_timing_warning || '');
        setTextIfChanged(timingConfigStatus, timingStatusText(data));
        setTextIfChanged(feederCalibrationStatus, feederCalibrationStatusText(data, timing));
        const timingApplied = data.capture_timing_status === 'applied' && !timingInputsDirty;
        timingSummaryStatus.dataset.state = timingApplied ? 'ready' : 'pending';
        setTextIfChanged(timingSummaryStatus, timingApplied ? '已同步' : '待同步');
        const calibrated = Boolean(timing.feeder_calibrated && !feederSettingsDirty);
        calibrationSummaryStatus.dataset.state = calibrated ? 'ready' : 'pending';
        setTextIfChanged(calibrationSummaryStatus, calibrated ? '已校正' : '未校正');
    }

    function feederCalibrationStatusText(data, timing) {
        const revision = data.capture_timing_revision ?? 0;
        if (timingInputsDirty) {
            if (timing.feeder_calibrated && !feederSettingsDirty) {
                return '送料校正仍有效；目前修改尚未套用，請先保存數值。';
            }
            return '步驟 1／3：設定尚未套用；請先保存數值。';
        }
        if (timing.feeder_calibrated) {
            return `已完成：revision ${revision} 的送料確認已保存，可以直接開始執行。`;
        }
        if (data.capture_timing_status !== 'applied') {
            return `步驟 1／3：等待 ESP32 套用 revision ${revision}。`;
        }
        if (data.status === 'waiting_feeder') {
            return '步驟 2／3：送料測試進行中，等待 HC-SR04 停止馬達。';
        }
        if (data.can_confirm_feeder_calibration) {
            return '步驟 3／3：測試已通過；目視確認恰好送出一顆後勾選。';
        }
        const result = data.feeder_test_result || {};
        if (result.event && !result.ok) {
            return `測試未通過（${result.stop_reason || result.event}）；排除原因後請重新測試。`;
        }
        if (data.can_test_feeder) {
            return '步驟 2／3：設定已套用，請執行一次送料測試。';
        }
        return `暫時無法測試送料（${data.feeder_test_disabled_reason || '系統尚未就緒'}）。`;
    }

    function readTimingInput(input, key, minimum, maximum = 3000) {
        const value = Number(input.value);
        if (!Number.isInteger(value) || value < minimum || value > maximum || value % 50 !== 0) {
            throw new Error(`${key} 必須介於 ${minimum} 到 ${maximum} ms，且以 50 ms 為間距。`);
        }
        return value;
    }

    function readSteppedInput(input, key, minimum, maximum, step, unit) {
        const value = Number(input.value);
        if (!Number.isInteger(value) || value < minimum || value > maximum || value % step !== 0) {
            throw new Error(`${key} 必須介於 ${minimum} 到 ${maximum} ${unit}，且以 ${step} ${unit} 為間距。`);
        }
        return value;
    }

    function captureTimingPayloadFromInputs() {
        const payload = {
            first_station_settle_ms: readTimingInput(timingFirstStationInput, '第 1 站停穩時間', 50),
            servo_settle_ms: readTimingInput(timingServoInput, '伺服穩定時間', 50),
            fruit_settle_ms: readTimingInput(timingFruitInput, '到站停穩時間', 50),
            final_gate_return_delay_ms: readTimingInput(timingFinalReturnInput, '最終歸位延遲', 0),
            idle_command_poll_interval_ms: readTimingInput(
                timingIdleCommandPollInput,
                'ESP32 閒置命令輪詢間隔',
                100,
                5000,
            ),
            feeder_stop_us: readSteppedInput(timingFeederStopInput, '送料停止脈波', 1400, 1600, 5, 'us'),
            feeder_drive_us: readSteppedInput(timingFeederDriveInput, '送料驅動脈波', 1000, 2000, 10, 'us'),
            feeder_max_run_ms: readSteppedInput(
                timingFeederMaxRunInput,
                '送料最長運轉時間',
                1000,
                20000,
                500,
                'ms',
            ),
        };
        if (payload.feeder_drive_us === payload.feeder_stop_us) {
            throw new Error('送料驅動脈波不得與停止脈波相同。');
        }
        return payload;
    }

    function restoreRecommendedTiming() {
        setTimingInputs(recommendedCaptureTiming());
        timingInputsDirty = true;
        feederSettingsDirty = true;
        feederCalibratedInput.checked = false;
        setMessage('已填入校正預設 300／200／350／300／250 ms，按下「套用停穩設定」後才會儲存。');
        if (lastState) {
            renderCaptureTiming(lastState);
        }
    }

    async function applyCaptureTiming() {
        let timing;
        try {
            timing = captureTimingPayloadFromInputs();
        } catch (error) {
            setMessage(`停穩設定無效：${error.message}`);
            return;
        }
        if (!beginControlAction('capture-timing', applyTimingButton)) {
            return;
        }

        timingUpdateInFlight = true;
        if (lastState) {
            renderCaptureTiming(lastState);
        }
        try {
            const payload = await postJson('/api/capture_timing/', timing);
            timingInputsDirty = false;
            feederSettingsDirty = false;
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
            endControlAction(applyTimingButton);
        }
    }

    async function testFeederOnce() {
        if (!confirm('請確認 HC-SR04 區域淨空，並在送料入口放置一顆百香果，再執行測試送料。')) {
            return;
        }
        if (!beginControlAction('feeder-test', testFeederButton)) {
            return;
        }
        try {
            const payload = await postJson('/api/feeder/test/');
            lastState = payload;
            renderState(payload);
            setMessage('送料測試已送出；ESP32 會在本機計時到期後停止。');
        } catch (error) {
            if (error.payload) {
                lastState = error.payload;
                renderState(error.payload);
            }
            setMessage(`送料測試失敗：${error.message}`);
        } finally {
            endControlAction(testFeederButton);
        }
    }

    async function confirmFeederCalibration() {
        if (!feederCalibratedInput.checked || !lastState || feederConfirmationInFlight) {
            return;
        }
        if (!beginControlAction('feeder-confirmation', feederCalibratedInput)) {
            return;
        }
        feederConfirmationInFlight = true;
        renderCaptureTiming(lastState);
        try {
            const payload = await postJson('/api/feeder/calibration/confirm/', {
                timing_revision: Number(lastState.capture_timing_revision),
            });
            lastState = payload;
            renderState(payload);
            setMessage('送料校正確認已保存，不用再次測試或套用設定。');
        } catch (error) {
            feederCalibratedInput.checked = false;
            if (error.payload && error.payload.state) {
                lastState = error.payload.state;
                renderState(error.payload.state);
            }
            const reason = error.payload && error.payload.reason
                ? `（${error.payload.reason}）`
                : '';
            setMessage(`保存送料校正確認失敗：${error.message}${reason}`);
        } finally {
            feederConfirmationInFlight = false;
            endControlAction(feederCalibratedInput);
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
        const fingerprint = JSON.stringify(trace);
        if (fingerprint === lastTraceFingerprint) {
            return;
        }
        lastTraceFingerprint = fingerprint;
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
        if (!beginControlAction('set-counter', setCounterButton)) {
            return;
        }
        try {
            const payload = await postJson('/api/set_counter/', { start_id: value });
            counterInput.value = '';
            setMessage(`起始 ID 已設定，下一筆為 ${payload.next_fruit_id}。`);
            await refreshState();
        } catch (error) {
            setMessage(`設定失敗：${error.message}`);
        } finally {
            endControlAction(setCounterButton);
        }
    }

    async function toggleAutoRun() {
        if (!beginControlAction('auto-run', autoRunButton)) {
            return;
        }
        try {
            if (lastState && lastState.hardware_mode === 'photo_only') {
                const payload = await postJson('/api/photo_capture/');
                lastState = payload;
                renderState(payload);
                return;
            }
            const enabled = !Boolean(lastState && lastState.auto_run_enabled);
            let recoveryConfirmed = false;
            if (enabled && lastState && lastState.sorter_recovery_required) {
                recoveryConfirmed = confirm(
                    '分類器結果因 Django 重啟而不確定。請先確認 Gate 3、分類器與果實位置安全；是否已完成檢查並復原？',
                );
                if (!recoveryConfirmed) {
                    return;
                }
            }
            const payload = await postJson('/api/auto_run/', {
                enabled,
                recovery_confirmed: recoveryConfirmed,
            });
            lastState = payload;
            renderState(payload);
            setMessage(enabled
                ? '自動運轉已開始，正在送入第一顆百香果。'
                : '已要求暫停；目前百香果完成後不會送入下一顆。');
            await refreshState();
        } catch (error) {
            setMessage(`自動運轉切換失敗：${error.message}`);
        } finally {
            endControlAction(autoRunButton);
        }
    }

    async function recaptureCurrent() {
        if (!lastState || !lastState.active_fruit_id) {
            return;
        }
        if (!beginControlAction('recapture', recaptureButton)) {
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
        } finally {
            endControlAction(recaptureButton);
        }
    }

    async function classify(label) {
        const actionButton = classifyButtons.find((button) => button.dataset.label === label);
        if (classificationInFlight) {
            return;
        }
        if (!beginControlAction('classify', actionButton)) {
            return;
        }
        classificationInFlight = true;
        if (lastState) {
            renderState(lastState);
        }
        try {
            await releaseThumbnailsBeforeFileOperation();
            const payload = await postJson('/api/classify/', {
                label,
                note: noteInput.value.trim(),
                fruit_id: lastState.active_fruit_id,
                capture_token: lastState.capture_token,
            });
            noteInput.value = '';
            if (payload.hardware_skipped) {
                setMessage(`${payload.fruit_id} 已分類保存到 ${payload.path}，本輪結束。`);
            } else if (payload.sorter_command_queued) {
                setMessage(`${payload.fruit_id} 已分類到 ${payload.path}，等待 ESP32 執行硬體分類器。${warningText(payload)}`);
            } else {
                setMessage(`${payload.fruit_id} 資料分類已完成，但硬體分類命令建立失敗：${payload.sorter_error || 'sorter_command_queue_failed'}。`);
            }
            await refreshState();
        } catch (error) {
            if (error.payload) {
                lastState = error.payload;
                renderState(error.payload);
            }
            setMessage(`分類失敗：${error.message}`);
        } finally {
            classificationInFlight = false;
            await refreshState();
            endControlAction(actionButton);
        }
    }

    async function discardCurrent() {
        if (!lastState || !lastState.active_fruit_id) {
            return;
        }
        if (!confirm(`確定刪除 ${lastState.active_fruit_id} 的暫存照片嗎？`)) {
            return;
        }
        if (!beginControlAction('discard', discardButton)) {
            return;
        }
        try {
            await releaseThumbnailsBeforeFileOperation();
            const payload = await postJson('/api/discard/', {
                fruit_id: lastState.active_fruit_id,
                capture_token: lastState.capture_token,
            });
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
        } finally {
            endControlAction(discardButton);
        }
    }

    async function resetDataset() {
        if (!confirm('確定要重置整個 dataset 嗎？這會刪除 temp 與所有分類資料夾內的照片，並清空 metadata.csv。')) {
            return;
        }
        if (!confirm('再次確認：此動作無法復原，下一筆 ID 會回到 fruit_001。')) {
            return;
        }
        if (!beginControlAction('reset-dataset', resetDatasetButton)) {
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
        } finally {
            endControlAction(resetDatasetButton);
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
            autoRunButton.disabled = true;
            if ([
                timingFeederStopInput,
                timingFeederDriveInput,
                timingFeederMaxRunInput,
            ].includes(input)) {
                feederSettingsDirty = true;
                feederCalibratedInput.checked = false;
            }
            if (lastState) {
                renderCaptureTiming(lastState);
            }
        });
    });
    resetTimingButton.addEventListener('click', restoreRecommendedTiming);
    applyTimingButton.addEventListener('click', applyCaptureTiming);
    testFeederButton.addEventListener('click', testFeederOnce);
    feederCalibratedInput.addEventListener('change', confirmFeederCalibration);
    feederHelpButton.addEventListener('click', () => {
        const isOpen = feederHelp.classList.toggle('is-open');
        feederHelpButton.setAttribute('aria-expanded', String(isOpen));
    });
    document.addEventListener('click', (event) => {
        if (!feederHelp.contains(event.target)) {
            feederHelp.classList.remove('is-open');
            feederHelpButton.setAttribute('aria-expanded', 'false');
        }
    });
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            feederHelp.classList.remove('is-open');
            feederHelpButton.setAttribute('aria-expanded', 'false');
        }
    });
    autoRunButton.addEventListener('click', toggleAutoRun);
    workModeSelect.addEventListener('change', async () => {
        const workMode = workModeSelect.value;
        const hardwareMode = workMode === 'detection'
            ? 'photo_only' : hardwareModeSelect.value;
        if (!beginControlAction('collection-options', workModeSelect)) {
            return;
        }
        try {
            const payload = await postJson('/api/collection_options/', {
                work_mode: workMode, hardware_mode: hardwareMode,
            });
            lastState = payload;
            renderState(payload);
        } catch (error) {
            await refreshState();
            setMessage(`選項切換失敗：${error.message}`);
        } finally {
            endControlAction(workModeSelect);
        }
    });
    hardwareModeSelect.addEventListener('change', async () => {
        const hardwareMode = hardwareModeSelect.value;
        if (!beginControlAction('collection-options', hardwareModeSelect)) {
            return;
        }
        try {
            const payload = await postJson('/api/collection_options/', {
                work_mode: workModeSelect.value,
                hardware_mode: hardwareMode,
            });
            lastState = payload;
            renderState(payload);
        } catch (error) {
            await refreshState();
            setMessage(`選項切換失敗：${error.message}`);
        } finally {
            endControlAction(hardwareModeSelect);
        }
    });
    recaptureButton.addEventListener('click', recaptureCurrent);
    document.getElementById('btn-open-folder').addEventListener('click', openDatasetFolder);
    discardButton.addEventListener('click', discardCurrent);
    resetDatasetButton.addEventListener('click', resetDataset);
    document.getElementById('btn-reconnect').addEventListener('click', () => {
        startWebRTC().catch((error) => {
            setStreamStatus(`串流重連失敗：${error.message}`);
            setPreviewState('error', '預覽連線失敗');
        });
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

    startWebRTC().catch((error) => {
        setStreamStatus(`WebRTC 初始化失敗：${error.message}`);
        setPreviewState('error', '預覽連線失敗');
    });
    restartStatePolling(0);
    restartWebRTCPolling(0);
