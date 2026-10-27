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
    const reasonCodesEl = document.getElementById('reason-codes');
    const motorCommandEl = document.getElementById('motor-command');
    const sorterStatusEl = document.getElementById('sorter-status');
    const sorterFruitEl = document.getElementById('sorter-fruit');
    const sorterLabelEl = document.getElementById('sorter-label');
    const sorterErrorEl = document.getElementById('sorter-error');
    const rawStateLabelEl = document.getElementById('raw-state-label');
    const stateBadge = document.getElementById('state-badge');
    const counterInput = document.getElementById('counter-input');
    const noteInput = document.getElementById('note');
    const classifyButtons = Array.from(document.querySelectorAll('.classify-button'));
    const workModeSelect = document.getElementById('work-mode');
    const hardwareModeSelect = document.getElementById('hardware-mode');
    const classificationPanel = document.getElementById('classification-panel');
    const classificationGuidance = document.getElementById('classification-guidance');
    const detectionTitle = document.getElementById('detection-title');
    const detectionResults = document.getElementById('detection-results');
    const detectionSummary = document.getElementById('detection-summary');
    const gradePanel = document.getElementById('grade-panel');
    const detectionGrade = document.getElementById('detection-grade');
    const gradeDetail = document.getElementById('detection-grade-detail');
    const detectionFlowStatus = document.getElementById('detection-flow-status');
    const viewGrades = document.getElementById('detection-view-grades');
    const manualGradePanel = document.getElementById('manual-grade-panel');
    const manualGradeGuidance = document.getElementById('manual-grade-guidance');
    const manualGradeButtons = Array.from(document.querySelectorAll('.manual-grade-button'));
    const manualAbandonButton = document.getElementById('btn-manual-abandon');
    const manualSkipButton = document.getElementById('btn-manual-skip');
    const stageChips = document.getElementById('stage-chips');
    const operationModeSummary = document.getElementById('operation-mode-summary');
    const notificationSummary = document.getElementById('notification-summary-status');
    const notificationToggle = document.getElementById('btn-notification-toggle');
    const notificationModeLabel = document.getElementById('notification-mode-label');
    const notificationStatus = document.getElementById('notification-status');
    const trendAlertForm = document.getElementById('trend-alert-form');
    const trendAlertStatus = document.getElementById('trend-alert-status');
    const trendAlertList = document.getElementById('trend-alert-list');
    const trendAlertSave = document.getElementById('btn-trend-alert-save');
    const trendAlertCategories = document.getElementById('trend-alert-categories');
    const trendAlertAdvancedCategories = document.getElementById('trend-alert-advanced-categories');
    const trendAlertTest = document.getElementById('btn-trend-alert-test');
    const trendAlertFields = [
        ['trend-alert-window', 'window_days'],
        ['trend-alert-min-samples', 'min_samples'],
        ['trend-alert-cooldown', 'cooldown_hours'],
        ['trend-alert-spike-factor', 'spike_factor'],
    ].map(([id, key]) => ({ input: document.getElementById(id), key }));
    // Percentage inputs per category; the API stores them as 0-1.
    const TREND_PERCENT_FIELDS = new Set(['threshold', 'rise']);
    const trendAlertEnabled = document.getElementById('trend-alert-enabled');
    const actionGuidance = document.getElementById('action-guidance');
    const operationDetails = document.getElementById('operation-details');
    const operationToggle = document.getElementById('btn-operation-toggle');
    const optionsGuidance = document.getElementById('options-guidance');
    const autoRunButton = document.getElementById('btn-auto-run');
    const finishFeederWaitButton = document.getElementById('btn-finish-feeder-wait');
    const recaptureButton = document.getElementById('btn-recapture');
    const discardButton = document.getElementById('btn-discard');
    const resetDatasetButton = document.getElementById('btn-reset-dataset');
    const setCounterButton = document.getElementById('btn-set-counter');
    const exportZone = document.getElementById('detection-export');
    const exportButton = document.getElementById('btn-export-detection');
    const exportIncludeResults = document.getElementById('export-include-results');
    let exportInFlight = false;
    const readinessItems = {
        esp32: document.getElementById('readiness-esp32'),
        camera: document.getElementById('readiness-camera'),
        sensor: document.getElementById('readiness-sensor'),
        timing: document.getElementById('readiness-timing'),
    };
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
    const timingSummaryStatus = document.getElementById('timing-summary-status');
    const timingConfigStatus = document.getElementById('timing-config-status');
    const testFeederButton = document.getElementById('btn-test-feeder');
    const runtimeResetButton = document.getElementById('btn-runtime-reset');
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
    let lastBoardManifest = '';
    let lastChipFingerprint = '';
    let lastActionGuidance = '';
    let classificationInFlight = false;
    let controlActionInFlight = null;
    let lastTraceFingerprint = '';
    let messageClearTimer = null;
    let activeStatusMessage = '';
    let lastStatusEventKey = '';
    const messageVisibleMs = 5000;
    const stateIdlePollMs = 1000;
    const stateActivePollMs = 500;
    const webrtcPollMs = 1000;
    const RTC_CONFIG = {
        iceServers: [],
    };

    let networkChoiceSignature = '';
    let previewNetworkIdentity = '';
    let networkSelectionInFlight = false;
    const networkSelect = document.getElementById('network-interface');
    const selectNetworkButton = document.getElementById('btn-select-network');
    const networkSetupButton = document.getElementById('btn-network-setup');
    let networkSetupInFlight = false;

    function renderNetwork(network) {
        const interfaces = network.interfaces || [];
        const signature = JSON.stringify(interfaces);
        if (networkChoiceSignature !== signature) {
            const previous = networkSelect.value;
            networkSelect.replaceChildren(new Option('請選擇網路', ''));
            interfaces.forEach((row) => networkSelect.add(new Option(
                `${row.name}：${row.ipv4}/${row.prefix}`,
                `${row.interface_id}|${row.ipv4}`,
            )));
            networkSelect.value = previous;
            networkChoiceSignature = signature;
        }
        const selected = Boolean(network.selected);
        const summaryStatus = document.getElementById('network-summary-status');
        setTextIfChanged(summaryStatus, selected ? `${network.selection.name}：${network.selection.ipv4}` : '尚未確認');
        summaryStatus.dataset.state = selected ? 'ready' : 'pending';
        networkSelect.disabled = networkSelectionInFlight || !network.selection_allowed;
        selectNetworkButton.disabled = networkSelectionInFlight || !network.selection_allowed || !interfaces.length;
        setTextIfChanged(document.getElementById('network-status'), selected
            ? `網路已選擇：${network.selection.name}，${network.selection.ipv4}`
            : `尚未確認網路：${reasonText(network.reason || 'network_selection_required')}`);
        setTextIfChanged(document.getElementById('server-url'), network.server_url || '尚未確認');
        setTextIfChanged(document.getElementById('camera-url'), network.camera_url || '確認網路後顯示');
        setTextIfChanged(document.getElementById('network-discovery-status'),
            `裝置探索：${statusText(network.discovery?.status)}${network.discovery?.reason ? `，${reasonText(network.discovery.reason)}` : ''}。正式硬體運轉維持停用。`);
        const device = network.esp32 || {};
        setTextIfChanged(document.getElementById('network-device-status'), device.online
            ? `ESP32 診斷往返已完成（${device.source === 'manual' ? '手動填寫網址' : '自動探索'}）。`
            : `ESP32 診斷尚未連線：${reasonText(device.reason || device.status || 'waiting_for_report')}。${device.reason === 'multiple_controllers_found' ? '' : '探索失敗時可使用控制端網址備援。'}`);
        const setup = network.setup || {};
        setTextIfChanged(document.getElementById('network-setup-status'), setup.status === 'allowed'
            ? '可以設定網路：ESP32 與 Django 都處於安全閒置。'
            : `${setup.status === 'blocked' ? '暫時不能設定網路' : '無法確認能否設定網路'}：${reasonText(setup.reason) || statusText('unknown')}`);
        networkSetupButton.disabled = networkSetupInFlight || setup.status !== 'allowed';
        const qr = document.getElementById('camera-qr');
        qr.hidden = !selected;
        if (selected) {
            const url = `${network.qr_url}?run=${network.server_run_id}&revision=${network.network_revision}`;
            if (qr.getAttribute('src') !== url) qr.src = url;
        } else {
            qr.removeAttribute('src');
        }
        const identity = selected ? `${network.server_run_id}:${network.network_revision}` : '';
        if (identity !== previewNetworkIdentity) {
            previewNetworkIdentity = identity;
            if (selected) {
                startWebRTC().catch((error) => {
                    setStreamStatus(`RTC 預覽無法建立：${error.message}；仍可正式拍攝。`);
                    setPreviewState('error', '預覽連線失敗');
                });
            } else {
                if (peerConnection) peerConnection.close();
                peerConnection = null;
                remoteVideo.srcObject = null;
                setRemoteVideoVisible(false);
            }
        }
    }

    document.getElementById('network-form').addEventListener('submit', async (event) => {
        event.preventDefault();
        const [interfaceId, ipv4] = networkSelect.value.split('|');
        if (!interfaceId || !ipv4 || networkSelectionInFlight) return;
        networkSelectionInFlight = true;
        selectNetworkButton.disabled = true;
        try {
            await postJson('/api/network/', { interface_id: interfaceId, ipv4 });
            await refreshState();
        } catch (error) {
            setMessage(`確認網路失敗：${error.message}`);
        } finally {
            networkSelectionInFlight = false;
            await refreshState();
        }
    });

    networkSetupButton.addEventListener('click', async () => {
        if (networkSetupInFlight) return;
        networkSetupInFlight = true;
        networkSetupButton.disabled = true;
        try {
            await postJson('/api/network/setup_request/');
            setMessage('已通知 ESP32 開啟設定熱點；數秒內手機會看到 PF-Setup- 開頭的熱點，再掃描下方 QR Code。');
        } catch (error) {
            setMessage(`無法開啟 ESP32 設定熱點：${error.payload?.reason || error.message}`);
        } finally {
            networkSetupInFlight = false;
            await refreshState();
        }
    });

    function setTextIfChanged(element, value) {
        const nextValue = value == null ? '' : String(value);
        if (element.textContent !== nextValue) {
            element.textContent = nextValue;
        }
    }

    function clearMessage() {
        activeStatusMessage = '';
        setTextIfChanged(messageEl, '');
        messageEl.dataset.state = '';
    }

    // Runtime alerts take priority during their five-second display window.
    function setMessage(message) {
        if (activeStatusMessage) return;
        window.clearTimeout(messageClearTimer);
        setTextIfChanged(messageEl, message || '');
        if (message) {
            messageClearTimer = window.setTimeout(clearMessage, messageVisibleMs);
        }
    }

    function renderStatusMessage(data) {
        const reason = data.last_error_reason || data.auto_run_recovery_reason;
        const eventKey = reason ? JSON.stringify([
            reason, data.operator_alert?.command_id ?? data.last_error_command_id ?? null,
        ]) : '';
        if (eventKey === lastStatusEventKey) return;
        lastStatusEventKey = eventKey;
        if (!reason) {
            if (activeStatusMessage) {
                window.clearTimeout(messageClearTimer);
                clearMessage();
            }
            return;
        }
        activeStatusMessage = reasonText(reason);
        window.clearTimeout(messageClearTimer);
        setTextIfChanged(messageEl, activeStatusMessage);
        messageEl.dataset.state = 'error';
        messageClearTimer = window.setTimeout(clearMessage, messageVisibleMs);
    }

    // Maintainer-only panels; double-clicking the title shows or hides them together.
    const maintenancePanels = [
        document.getElementById('capture-settings'),
        document.getElementById('diagnostics'),
        document.getElementById('trend-alert-advanced'),
        document.getElementById('btn-trend-alert-test'),
    ];
    const diagnosticsStorageKey = 'pf-dashboard-diagnostics-visible';
    function setMaintenanceVisible(visible) {
        maintenancePanels.forEach((panel) => { panel.hidden = !visible; });
    }
    try {
        setMaintenanceVisible(window.localStorage.getItem(diagnosticsStorageKey) === 'true');
    } catch {
        setMaintenanceVisible(false);
    }
    document.getElementById('dashboard-title').addEventListener('dblclick', () => {
        window.getSelection()?.removeAllRanges();
        const visible = maintenancePanels[0].hidden;
        setMaintenanceVisible(visible);
        try {
            window.localStorage.setItem(diagnosticsStorageKey, String(visible));
        } catch {
            // Storage may be blocked; the toggle still works for this page.
        }
        setMessage(visible ? '已顯示維護資訊' : '已隱藏維護資訊');
    });

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
            body: JSON.stringify({ dataset_mode: lastState?.work_mode, ...data }),
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
        if (!lastState?.network?.selected) return;
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
        renderStatusMessage(data);
        renderNetwork(data.network || {});
        const images = data.latest_images || [];
        const status = data.status || 'idle';
        const photoOnly = data.hardware_mode === 'photo_only';
        const detectionMode = data.work_mode === 'detection';
        workModeSelect.value = data.work_mode || 'collection';
        hardwareModeSelect.value = data.hardware_mode || 'hardware';
        workModeSelect.disabled = Boolean(controlActionInFlight) || !data.can_change_options;
        esp32Indicator.hidden = photoOnly;
        feederSensorIndicator.hidden = photoOnly;
        hardwareModeSelect.disabled = Boolean(controlActionInFlight) || !data.can_change_options;
        classificationPanel.hidden = detectionMode;
        gradePanel.hidden = !detectionMode;
        setTextIfChanged(detectionTitle, detectionMode ? '四模型檢測結果' : '三張原圖');
        setTextIfChanged(operationModeSummary,
            `工作模式：${detectionMode ? '自動檢測' : '蒐集模式'}／硬體選項：${photoOnly ? '純拍攝' : '使用硬體'}`);
        setTextIfChanged(optionsGuidance, data.options_disabled_reason
            ? `目前無法切換：${reasonText(data.options_disabled_reason)}` : '開始後，本輪選項固定。');
        setTextIfChanged(activeFruitEl, data.active_fruit_id || '無暫存資料');
        setTextIfChanged(nextFruitEl, data.next_fruit_id || 'fruit_001');
        setTextIfChanged(
            stateLabelEl,
            data.gate3_manual_removal_required
                ? '資料完成，Gate 3 待處理'
                : captureStatusText(status),
        );
        setTextIfChanged(rawStateLabelEl, status);
        stateBadge.dataset.state = data.gate3_manual_removal_required
            ? 'error' : captureStatusTone(status);
        setTextIfChanged(imageCountEl, `${data.image_total ?? images.length} / ${data.image_count || 3}`);

        const diagnosticOnline = Boolean(data.network?.esp32?.online);
        setTextIfChanged(esp32StatusEl, data.esp32_online ? '在線' : diagnosticOnline ? '診斷已連線' : '離線');
        esp32Indicator.dataset.state = data.esp32_online || diagnosticOnline ? 'online' : 'offline';
        const setupSummaryStatus = document.getElementById('network-setup-summary-status');
        setTextIfChanged(setupSummaryStatus, data.esp32_online ? '已連線' : diagnosticOnline ? '已連線（診斷）' : '未連線');
        setupSummaryStatus.dataset.state = data.esp32_online || diagnosticOnline ? 'ready' : 'error';
        setTextIfChanged(cameraReadyStatusEl, data.camera_ready ? '可拍攝' : '未就緒');
        cameraReadyIndicator.dataset.state = data.camera_ready ? 'ready' : 'offline';
        const sensor = sensorStatus(data.feeder_sensor_state);
        setTextIfChanged(feederSensorStateEl, sensor.label);
        feederSensorIndicator.dataset.state = sensor.tone;

        const network = data.network || {};
        const rawReasons = {
            auto_run: data.auto_run_disabled_reason,
            photo_capture: data.photo_capture_disabled_reason,
            options: data.options_disabled_reason,
            classify: data.classify_disabled_reason,
            manual_grade: data.manual_grade_disabled_reason,
            feeder_test: data.feeder_test_disabled_reason,
            recovery: data.network_recovery_confirm_disabled_reason,
            last_error: data.last_error_reason,
            alert: data.operator_alert && data.operator_alert.reason,
            detection: data.detection_error,
            network: network.reason,
            discovery: network.discovery && network.discovery.reason,
            esp32: network.esp32 && network.esp32.reason,
            setup: network.setup && network.setup.reason,
        };
        setTextIfChanged(reasonCodesEl, Object.entries(rawReasons)
            .filter(([, code]) => code)
            .map(([key, code]) => `${key}=${code}`)
            .join('\n') || '無');
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
        // The reason stays reachable on hover; the Gate 3 steps live in the 安全復原 confirm dialog.
        stateBadge.title = errorReasonText(data);

        renderReadiness(data);
        setTextIfChanged(detectionSummary, detectionSummaryText(data));
        renderStageChips(data);
        renderBoard(data);
        renderGradeCard(data);
        renderNotification(data.notification);
        notificationToggle.disabled ||= !data.network?.selected;
        renderTrendAlerts(data.trend_alerts);
        trendAlertSave.disabled ||= !data.network?.selected;
        setTextIfChanged(document.getElementById('dataset-scope'),
            `目前模式：${data.work_mode === 'detection' ? '自動檢測' : '蒐集'}`);
        renderTiming(data.timing || {});
        renderCaptureTiming(data);
        renderTransitionTrace(data);

        classifyButtons.forEach((button) => {
            button.disabled = Boolean(controlActionInFlight)
                || classificationInFlight
                || data.sorter_busy
                || !data.can_classify;
        });
        setTextIfChanged(classificationGuidance, data.classify_disabled_reason
            ? `目前無法分類：${reasonText(data.classify_disabled_reason)}`
            : '確認三張照片後，將目前資料提交到對應等級。');
        discardButton.disabled = Boolean(controlActionInFlight)
            || classificationInFlight
            || !data.can_discard;
        setTextIfChanged(
            discardButton,
            detectionMode && !photoOnly
                ? '跳過當顆（人工移除）'
                : detectionMode && data.discardable_fruit_id && !data.active_fruit_id
                    ? '刪除未保存成功的本輪檢測'
                : '跳過／刪除目前資料',
        );
        finishFeederWaitButton.hidden = photoOnly
            || data.auto_run_recovery_reason !== 'feeder_max_run_timeout' || Boolean(data.active_fruit_id);
        finishFeederWaitButton.disabled = Boolean(controlActionInFlight) || !data.can_finish_feeder_wait;
        finishFeederWaitButton.title = data.can_finish_feeder_wait
            ? '結束等待後可切換模式與網路' : reasonText(data.finish_feeder_wait_disabled_reason);
        if (photoOnly) {
            setTextIfChanged(autoRunButton, '開始拍攝');
            autoRunButton.dataset.mode = 'photo';
            autoRunButton.disabled = Boolean(controlActionInFlight) || !data.can_start_photo_capture;
        } else if (data.auto_run_enabled) {
            setTextIfChanged(autoRunButton, '停止拍攝');
            autoRunButton.dataset.mode = 'pause';
            autoRunButton.disabled = Boolean(controlActionInFlight);
        } else if (data.auto_run_finishing) {
            setTextIfChanged(autoRunButton, waitingForManualGrade(data) ? '等待人工處理' : '正在完成目前果實');
            autoRunButton.dataset.mode = 'finishing';
            autoRunButton.disabled = true;
        } else {
            setTextIfChanged(autoRunButton, data.network_recovery_required ? '確認安全復原' : '開始拍攝');
            autoRunButton.dataset.mode = 'start';
            autoRunButton.disabled = Boolean(controlActionInFlight)
                || !data.can_start_auto_run;
        }
        recaptureButton.disabled = Boolean(controlActionInFlight)
            || data.sorter_busy
            || !data.can_recapture;
        // Detection runs unattended; these only appear when a round needs recovery.
        recaptureButton.hidden = detectionMode && !data.can_recapture;
        discardButton.hidden = detectionMode && !data.can_discard;
        renderActionGuidance(data, photoOnly);
        resetDatasetButton.disabled = Boolean(controlActionInFlight) || !data.can_reset_dataset;
        setCounterButton.disabled = Boolean(controlActionInFlight);
        exportZone.hidden = data.work_mode !== 'detection';
        exportButton.disabled = exportInFlight;
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
            photo_interval: '等待下一張',
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
        ['esp32', 'sensor', 'timing'].forEach((key) => {
            readinessItems[key].hidden = photoOnly;
        });
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
    }

    function timingStatusLabel(status) {
        const labels = {
            applied: '已同步',
            waiting_esp32: '等待 ESP32',
            pending_esp32_apply: '等待套用',
        };
        return labels[status] || '待同步';
    }

    function waitingForManualGrade(data) {
        return data.work_mode === 'detection'
            && data.detection_result?.classification?.status === 'needs_review'
            && Boolean(data.active_fruit_id);
    }

    function autoRunGuidanceText(data) {
        if (data.hardware_mode !== 'photo_only' && data.network_recovery?.resume_auto_run === false) {
            return data.network_recovery_confirm_disabled_reason === 'network_recovery_in_progress'
                ? '正在結束送料等待；ESP32 歸位與清鎖完成後可切換模式或網路，不會自動送料。'
                : '請按「確認安全復原」完成送料等待的清鎖；完成後不會自動送料。';
        }
        if (data.network_recovery && data.network_recovery_confirm_disabled_reason !== 'network_recovery_not_required') {
            const recoveryReason = data.network_recovery_confirm_disabled_reason;
            return recoveryReason
                ? `目前無法確認安全復原：${reasonText(recoveryReason)}`
                : data.hardware_mode === 'photo_only'
                    ? '按「開始拍攝」確認本輪資料已清理，再開始新一輪拍攝。馬達安全鎖仍保留。'
                    : data.network_recovery.reason === 'gate3_manual_removal_required'
                        ? '按「確認安全復原」確認果實已人工移除、手已離開；完成 ESP32 歸位交握後，再按「開始拍攝」。'
                        : '按「確認安全復原」確認人工清理完成；ESP32 歸位交握後，啟動條件成立時才送料。';
        }
        if (data.hardware_mode === 'photo_only') {
            const reason = data.photo_capture_disabled_reason;
            return reason
                ? `目前無法開始：${reasonText(reason)}。本輪完成後請分類或刪除。`
                : '按一次自動拍三張；每張保存成功才拍下一張。果實需自行擺位，三張不保證不同視角。';
        }
        if (waitingForManualGrade(data)) {
            return '本輪等待人工判級；也可按「跳過當顆（人工移除）」，依斷電程序移除果實後完成安全復原。';
        }
        if (data.auto_run_enabled) {
            return data.work_mode === 'detection'
                ? '本輪完成檢測並保存判級後執行分槽；確認分槽完成後才送下一顆。'
                : '目前為連續自動運轉；按「停止拍攝」會讓目前果實完成後停止送入下一顆。';
        }
        if (data.auto_run_finishing) {
            return '正在完成目前果實，完成後不會送入下一顆。';
        }
        const reason = data.auto_run_start_disabled_reason ?? (data.can_start_auto_run ? null : data.auto_run_disabled_reason);
        if (data.can_start_auto_run && data.auto_run_recovery_reason === 'feeder_max_run_timeout') {
            return '可按「開始拍攝」重新送料，或按「結束送料等待」後切換模式與網路。';
        }
        return reason
            ? `目前無法開始：${reasonText(reason)}`
            : '所有啟動條件已就緒，可以開始正式自動運轉。';
    }

    // The operation bar and global status share handling instructions and timing.
    function errorReasonText(data) {
        const alert = data.operator_alert;
        if (!alert) {
            return reasonText(data.last_error_reason || data.auto_run_recovery_reason);
        }
        const feederMetrics = [
            Number.isFinite(alert.feeder_elapsed_ms) ? `實際 ${alert.feeder_elapsed_ms} ms` : '',
            Number.isFinite(alert.feeder_max_run_ms) ? `上限 ${alert.feeder_max_run_ms} ms` : '',
            alert.feeder_stop_reason ? `停止原因：${reasonText(alert.feeder_stop_reason)}` : '',
        ].filter(Boolean).join('｜');
        const reason = reasonText(alert.reason || data.last_error_reason) || statusText('unknown');
        return `${reason}：${alert.instruction || '請排除狀況後重新開始。'}${feederMetrics ? `（${feederMetrics}）` : ''}`;
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

    function viewGradeText(image) {
        const grade = image.grade;
        if (!grade) {
            return '';
        }
        return grade.status === 'ok'
            ? `｜等級：${grade.label}`
            : `｜無法判級：${grade.reason || '未知原因'}`;
    }

    const BOARD_STAGES = [
        { key: 'roi', title: 'ROI', artifact: 'roi_annotated' },
        { key: 'color', title: 'color', artifact: 'masked_roi' },
        { key: 'wrinkle', title: 'wrinkle', artifact: 'wrinkle_gray' },
        { key: 'defect', title: 'defect', artifact: 'defect_annotated' },
    ];
    const PHOTO_FILENAMES = ['img_01.jpg', 'img_02.jpg', 'img_03.jpg'];
    const CAPTURING_STATUSES = ['waiting_camera', 'uploading', 'photo_interval'];
    const REASON_TEXT = JSON.parse(document.getElementById('reason-text').textContent);
    const STATUS_TEXT = JSON.parse(document.getElementById('status-text').textContent);

    // The main screen shows Chinese only; raw codes stay in the diagnostics panel.
    function reasonText(code) {
        if (!code) {
            return '';
        }
        // Codes may carry detail after a colon, e.g. esp32_not_idle:motion or grade_model_failed: ValueError.
        const base = String(code).split(':', 1)[0].trim();
        return REASON_TEXT[base] || '其他狀態，詳見診斷區';
    }

    function statusText(status) {
        return STATUS_TEXT[status] || STATUS_TEXT.unknown;
    }

    function notificationConfigReasonText(configuration) {
        const missing = configuration.config_missing_fields || [];
        return reasonText(configuration.config_reason)
            + (missing.length ? `；缺少欄位：${missing.join('、')}` : '');
    }

    function capturedOriginalUrls(data) {
        const urls = {};
        (data.latest_images || []).forEach((image) => {
            urls[image.filename] = image.url;
        });
        return urls;
    }

    function boardImages(data) {
        const source = data.detection_result || data.detection_progress;
        return (source && source.images) || {};
    }

    function detectionRunning(data) {
        return ['pending', 'running'].includes(data.detection_status);
    }

    function failedStatus(summary) {
        return ['error', 'skipped'].includes(summary && summary.status);
    }

    function roiCell(image, originalUrl, data) {
        const urls = image.artifact_urls || {};
        const roi = image.roi || {};
        const original = urls.original || originalUrl;
        if (urls.roi_annotated) {
            return { state: 'done', url: urls.roi_annotated, caption: detectionJudgement(BOARD_STAGES[0], image) };
        }
        if (failedStatus(roi)) {
            return { state: 'failed', url: original, badge: '原圖', caption: `ROI 不可用：${roi.reason || '未知原因'}` };
        }
        if (original) {
            return {
                state: detectionRunning(data) ? 'running' : 'waiting',
                url: original,
                badge: '原圖',
                caption: detectionRunning(data) ? 'ROI 檢測中' : '已拍攝原圖｜等待 ROI 檢測',
            };
        }
        return { state: 'waiting', placeholder: '等待', caption: '尚未拍攝' };
    }

    function modelCell(stage, image, data, hasResult) {
        const urls = image.artifact_urls || {};
        const model = (image.models || {})[stage.key] || {};
        if (model.status === 'ok' && urls[stage.artifact]) {
            return { state: 'done', url: urls[stage.artifact], caption: detectionJudgement(stage, image) };
        }
        if (model.status === 'ok') {
            return { state: 'failed', placeholder: '影像未保存', caption: detectionJudgement(stage, image) };
        }
        if (failedStatus(model)) {
            return {
                state: 'failed',
                placeholder: model.status === 'skipped' ? '未執行' : '失敗',
                caption: `${model.status === 'skipped' ? '未執行' : '判定失敗'}：${model.reason || '未知原因'}`,
            };
        }
        if (detectionRunning(data)) {
            const roiReady = (image.roi || {}).status === 'ok';
            return { state: 'running', placeholder: '處理中', caption: roiReady ? '模型推論中' : '等待 ROI' };
        }
        if (hasResult) {
            return { state: 'failed', placeholder: '無結果', caption: '未產生結果' };
        }
        return { state: 'waiting', placeholder: '等待', caption: '尚未完成' };
    }

    function detectionBoardModel(data) {
        const images = boardImages(data);
        const originals = capturedOriginalUrls(data);
        const hasResult = Boolean(data.detection_result);
        return PHOTO_FILENAMES.map((filename, index) => {
            const image = images[filename] || {};
            const roi = roiCell(image, originals[filename], data);
            const cells = [roi, ...BOARD_STAGES.slice(1).map(
                (stage) => modelCell(stage, image, data, hasResult),
            )];
            return { filename, index, grade: viewGradeText(image), cells };
        });
    }

    function collectionBoardModel(data) {
        const originals = capturedOriginalUrls(data);
        const capturing = CAPTURING_STATUSES.includes(data.status);
        return PHOTO_FILENAMES.map((filename, index) => {
            const url = originals[filename];
            if (url) {
                return { filename, index, cells: [{ state: 'done', url, caption: filename }] };
            }
            const active = capturing && Number(data.active_station_index) === index + 1;
            return {
                filename,
                index,
                cells: [{
                    state: active ? 'running' : 'waiting',
                    placeholder: active ? '拍攝中' : '等待',
                    caption: active ? '等待手機上傳' : '尚未拍攝',
                }],
            };
        });
    }

    function renderBoard(data) {
        const detectionMode = data.work_mode === 'detection';
        const model = detectionMode ? detectionBoardModel(data) : collectionBoardModel(data);
        const manifest = JSON.stringify([detectionMode, model]);
        if (manifest === lastBoardManifest) {
            return;
        }
        lastBoardManifest = manifest;
        detectionResults.classList.toggle('originals-board', !detectionMode);
        detectionResults.replaceChildren(...model.map((photo) => renderPhotoGroup(photo, detectionMode)));
        if (!model.some((photo) => photo.cells.some((cell) => cell.url))) {
            closeImagePreview();
        }
    }

    function renderPhotoGroup(photo, detectionMode) {
        const group = document.createElement('section');
        group.className = 'detection-group';
        const heading = document.createElement('h3');
        heading.textContent = `照片 ${photo.index + 1}${photo.grade || ''}`;
        group.append(heading);
        const grid = document.createElement('div');
        grid.className = detectionMode ? 'detection-grid' : 'detection-grid originals-grid';
        const titles = detectionMode ? BOARD_STAGES.map((stage) => stage.title) : ['原圖'];
        photo.cells.forEach((cell, cellIndex) => {
            grid.append(renderBoardCell(cell, `照片 ${photo.index + 1} ${titles[cellIndex]}`, titles[cellIndex], detectionMode));
        });
        group.append(grid);
        return group;
    }

    function renderBoardCell(cell, label, title, detectionMode) {
        const card = document.createElement('article');
        card.className = 'detection-card';
        card.dataset.state = cell.state;
        if (detectionMode) {
            const heading = document.createElement('h4');
            heading.textContent = title;
            card.append(heading);
        }
        const media = document.createElement('div');
        media.className = 'detection-media';
        if (cell.url) {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'detection-image-button';
            button.setAttribute('aria-label', `放大檢視 ${label}`);
            const preview = document.createElement('img');
            preview.src = cell.url;
            preview.alt = label;
            button.append(preview);
            button.addEventListener('click', () => openImagePreview({ filename: label, url: cell.url }));
            media.append(button);
        } else {
            media.classList.add('is-empty');
            const placeholder = document.createElement('span');
            placeholder.className = 'detection-placeholder';
            placeholder.textContent = cell.placeholder;
            media.append(placeholder);
        }
        if (cell.badge) {
            const badge = document.createElement('span');
            badge.className = 'detection-badge';
            badge.textContent = cell.badge;
            media.append(badge);
        }
        card.append(media);
        const caption = document.createElement('p');
        caption.className = 'detection-judgement';
        caption.textContent = cell.caption;
        card.append(caption);
        return card;
    }

    function releaseBoardImages() {
        lastBoardManifest = '';
        closeImagePreview();
        detectionResults.querySelectorAll('img').forEach((image) => {
            image.removeAttribute('src');
        });
    }

    function capturedCount(data) {
        const originals = capturedOriginalUrls(data);
        const images = boardImages(data);
        return PHOTO_FILENAMES.filter(
            (filename) => originals[filename] || (images[filename]?.artifact_urls || {}).original,
        ).length;
    }

    function detectionSummaryText(data) {
        const captured = capturedCount(data);
        if (data.status === 'photo_interval') {
            const seconds = (data.photo_detection_interval_ms || 0) / 1000;
            return `拍攝 ${captured}／3 張；照片已保存，等待 ${seconds} 秒後拍攝下一張`;
        }
        if (CAPTURING_STATUSES.includes(data.status)) {
            return `拍攝 ${captured}／3 張；等待第 ${data.active_station_index || captured + 1} 張上傳`;
        }
        if (data.work_mode !== 'detection') {
            return captured === 3 ? '三張原圖已保存，請在操作區人工分類' : '開始後依序保存三張原圖';
        }
        const stageLabels = {
            originals: '原圖已保存',
            roi: 'ROI 已完成',
            color: 'color 已完成',
            wrinkle: 'wrinkle 已完成',
            defect: 'defect 已完成',
        };
        if (detectionRunning(data)) {
            const stage = data.detection_progress?.stage;
            return `四模型檢測中${stage ? `｜${stageLabels[stage] || stage}` : ''}`;
        }
        if (data.detection_status === 'completed') {
            return '檢測與保存完成';
        }
        if (data.detection_status === 'failed') {
            return '檢測未完整完成，不視為整輪成功';
        }
        return '等待本輪三張照片';
    }

    function countViews(images, predicate) {
        return PHOTO_FILENAMES.filter((filename) => predicate(images[filename] || {})).length;
    }

    function stageChipModels(data) {
        const captured = capturedCount(data);
        const capturing = CAPTURING_STATUSES.includes(data.status);
        const chips = [{
            label: `拍攝 ${captured}/3${data.status === 'photo_interval' ? '｜等待下一張' : ''}`,
            state: captured === 3 ? 'done' : capturing ? 'active' : 'waiting',
        }];
        if (data.work_mode !== 'detection') {
            chips.push({
                label: data.status === 'uploaded' ? '待人工分類' : '人工分類',
                state: data.status === 'uploaded' ? 'active' : 'waiting',
            });
            return chips;
        }
        const images = boardImages(data);
        const running = detectionRunning(data);
        const finished = Boolean(data.detection_result) || data.detection_status === 'failed';
        const roiDone = countViews(images, (image) => image.roi?.status === 'ok');
        const roiFailed = countViews(images, (image) => failedStatus(image.roi));
        const modelsDone = countViews(images, (image) => BOARD_STAGES.slice(1).every(
            (stage) => image.models?.[stage.key]?.status === 'ok',
        ));
        const modelsFailed = countViews(images, (image) => BOARD_STAGES.slice(1).some(
            (stage) => failedStatus(image.models?.[stage.key]),
        ));
        chips.push(progressChip('ROI', roiDone, roiFailed, running, finished));
        chips.push(progressChip('四模型', modelsDone, modelsFailed, running, finished));
        const grade = gradeCardModel(data);
        chips.push({
            label: grade.chip || (grade.state === 'graded'
                ? `分類完成：${grade.title}`
                : grade.state === 'review' ? '待人工處理' : '分類'),
            state: grade.state === 'graded' || grade.state === 'manual'
                ? 'done'
                : grade.state === 'review' ? 'failed' : running ? 'active' : 'waiting',
        });
        return chips;
    }

    function progressChip(name, done, failed, running, finished) {
        if (failed) {
            return { label: `${name} 失敗 ${failed} 張`, state: 'failed' };
        }
        if (done === 3) {
            return { label: `${name} 已完成`, state: 'done' };
        }
        if (running) {
            return { label: `${name} 處理中 ${done}/3`, state: 'active' };
        }
        if (finished) {
            return { label: `${name} 未完成 ${done}/3`, state: 'failed' };
        }
        return { label: `${name} 等待`, state: 'waiting' };
    }

    function renderStageChips(data) {
        const chips = stageChipModels(data);
        const fingerprint = JSON.stringify(chips);
        if (fingerprint === lastChipFingerprint) {
            return;
        }
        lastChipFingerprint = fingerprint;
        const marks = { done: '✓', failed: '!', active: '…', waiting: '○' };
        stageChips.replaceChildren(...chips.map((chip) => {
            const item = document.createElement('li');
            item.className = 'stage-chip';
            item.dataset.state = chip.state;
            const mark = document.createElement('span');
            mark.setAttribute('aria-hidden', 'true');
            mark.textContent = marks[chip.state];
            item.append(mark, document.createTextNode(chip.label));
            return item;
        }));
    }

    function gradeCardModel(data) {
        const result = data.detection_result;
        if (!result) {
            if (data.detection_error) {
                return { state: 'review', title: '待人工處理', detail: `檢測未完成：${reasonText(data.detection_error)}` };
            }
            if (detectionRunning(data)) {
                return { state: 'running', title: '檢測中', detail: '三張照片已保存，正在執行四模型檢測與 XGBoost 判級' };
            }
            return { state: 'waiting', title: '等待判定', detail: '三張照片完成後進行檢測與分類' };
        }
        const classification = result.classification || {};
        const storage = result.storage || {};
        if (classification.status === 'abandoned' && classification.source === 'manual') {
            const failure = reasonText(result.reason || (result.model_classification || {}).reason) || '未知原因';
            return {
                state: 'manual',
                title: '已放棄本輪',
                chip: '人工放棄本輪',
                detail: `操作員判定本輪不分級並已保存；照片與原檢測失敗原因保留：${failure}`,
            };
        }
        if (classification.status === 'graded' && classification.source === 'manual') {
            const failure = reasonText(result.reason || (result.model_classification || {}).reason) || '未知原因';
            return {
                state: 'manual',
                title: `${classification.label}（人工判定）`,
                chip: `人工分類：${classification.label}`,
                detail: `模型無法判級，由操作員人工判定並已保存；原檢測失敗原因保留：${failure}`,
            };
        }
        if (storage.status === 'failed') {
            return { state: 'review', title: '待人工處理', detail: `結果保存失敗，不視為完成：${reasonText(storage.reason)}` };
        }
        if (classification.status === 'graded' && result.complete) {
            return { state: 'graded', title: classification.label, detail: '三張照片取最低等級，結果已保存' };
        }
        if (classification.status === 'graded') {
            return {
                state: 'review',
                title: '待人工處理',
                detail: `模型等級 ${classification.label}，但檢測未完整，不視為完成：${reasonText(result.reason) || '未知原因'}`,
            };
        }
        return {
            state: 'review',
            title: '待人工處理',
            detail: `無法判級：${reasonText(classification.reason || result.reason) || '未知原因'}`,
        };
    }

    function viewGradesText(data) {
        const images = (data.detection_result || {}).images || {};
        const parts = PHOTO_FILENAMES.map((filename, index) => {
            const grade = images[filename]?.grade;
            if (!grade) {
                return '';
            }
            return grade.status === 'ok'
                ? `照片 ${index + 1}：${grade.label}`
                : `照片 ${index + 1}：無法判級（${grade.reason || '未知原因'}）`;
        }).filter(Boolean);
        return parts.length ? parts.join('｜') : '尚無視角等級';
    }

    const NOTIFICATION_RESULT_TEXT = {
        pending: '待送',
        sent: '已送出',
        failed: '送出失敗',
        uncertain: '送達不明，請到群組確認',
        skipped: '通知關閉時保存，不排送',
    };

    function renderNotification(notification) {
        if (!notification || notification.error) {
            const error = notification?.error || '通知狀態無法取得';
            setTextIfChanged(notificationSummary, 'LINE 通知：狀態錯誤');
            notificationSummary.dataset.state = 'error';
            setTextIfChanged(notificationStatus, error);
            notificationToggle.disabled = true;
            return;
        }
        const counts = notification.counts || {};
        const enabled = Boolean(notification.enabled);
        const attention = !notification.configured || counts.uncertain || counts.failed;
        const queue = enabled
            ? (counts.pending ? `・待送 ${counts.pending}` : '')
            : (notification.paused ? `・暫停 ${notification.paused}` : '');
        setTextIfChanged(notificationSummary, `LINE 通知：${enabled ? '開啟' : '關閉'}${queue}`);
        notificationSummary.dataset.state = attention ? 'error' : (enabled ? 'ready' : '');
        notificationSummary.classList.toggle('neutral', !enabled && !attention);
        setTextIfChanged(notificationModeLabel,
            notification.mode === 'photo_only' ? '純拍攝：訊息加「測試」' : '使用硬體');
        setTextIfChanged(notificationToggle, enabled ? '關閉通知' : '開啟通知');
        notificationToggle.setAttribute('aria-pressed', String(enabled));
        notificationToggle.disabled = Boolean(controlActionInFlight);
        const current = notification.current;
        const lines = [
            enabled
                ? '只通知最終等級為加工的結果，保存後於背景補送，不影響檢測與分槽。'
                : '已關閉：既有待送暫停，關閉期間的新結果不排送；已交付 LINE 的訊息無法撤回。',
            notification.configured ? null : `尚未完成設定：${notificationConfigReasonText(notification)}`,
            `待送 ${counts.pending || 0}（暫停 ${notification.paused || 0}）・已送出 ${counts.sent || 0}`
                + `・送達不明 ${counts.uncertain || 0}・失敗 ${counts.failed || 0}`,
            current ? `本輪：${NOTIFICATION_RESULT_TEXT[current.status] || current.status}`
                + (current.reason && current.status !== 'sent' ? `（${reasonText(current.reason)}）` : '') : null,
            notification.last_error ? `最近錯誤：${reasonText(notification.last_error)}` : null,
        ];
        setTextIfChanged(notificationStatus, lines.filter(Boolean).join('\n'));
    }

    async function toggleNotification() {
        const enabled = !lastState?.notification?.enabled;
        if (!beginControlAction('notification', notificationToggle)) {
            return;
        }
        try {
            const payload = await postJson('/api/notification/', { enabled });
            lastState = payload;
            renderState(payload);
        } catch (error) {
            setMessage(`通知開關切換失敗：${error.message}`);
        } finally {
            endControlAction(notificationToggle);
        }
    }

    const TREND_DELIVERY_TEXT = {
        pending: 'LINE 待送', sent: 'LINE 已送出', failed: 'LINE 失敗', uncertain: 'LINE 送達不明',
    };

    function trendInputCell(key, field, label) {
        const cell = document.createElement('td');
        const input = document.createElement('input');
        input.type = 'number';
        input.step = '0.1';
        input.dataset.category = key;
        input.dataset.field = field;
        input.setAttribute('aria-label', `${label} ${field === 'threshold' ? '提醒門檻' : '上升百分點'}`);
        cell.appendChild(input);
        return cell;
    }

    function trendCategoryRows(trend) {
        const keys = Object.keys(trend.settings.categories);
        if (trendAlertCategories.dataset.keys === keys.join(',')) return;
        trendAlertCategories.dataset.keys = keys.join(',');
        const header = (label) => {
            const name = document.createElement('th');
            name.scope = 'row';
            name.textContent = label;
            return name;
        };
        trendAlertCategories.replaceChildren(...keys.map((key) => {
            const label = trend.labels[key] || key;
            const row = document.createElement('tr');
            row.append(header(label), trendInputCell(key, 'threshold', label));
            return row;
        }));
        trendAlertAdvancedCategories.replaceChildren(...keys.map((key) => {
            const label = trend.labels[key] || key;
            const row = document.createElement('tr');
            const cell = document.createElement('td');
            const select = document.createElement('select');
            select.dataset.category = key;
            select.dataset.field = 'direction';
            select.setAttribute('aria-label', `${label} 提醒方向`);
            Object.entries(trend.directions).forEach(([value, text]) => select.add(new Option(text, value)));
            cell.appendChild(select);
            row.append(header(label), trendInputCell(key, 'rise', label), cell);
            return row;
        }));
    }

    function trendCategoryInputs() {
        return trendAlertForm.querySelectorAll('[data-category]');
    }

    function renderTrendAlerts(trend) {
        if (!trend || trend.error) {
            setTextIfChanged(trendAlertStatus, trend?.error || '趨勢提醒狀態無法取得');
            trendAlertSave.disabled = true;
            return;
        }
        const settings = trend.settings;
        trendCategoryRows(trend);
        // Polling must not overwrite values the operator is still editing.
        if (!trendAlertForm.dataset.dirty) {
            trendAlertEnabled.checked = Boolean(settings.enabled);
            trendAlertFields.forEach(({ input, key }) => { input.value = String(settings[key]); });
            trendCategoryInputs().forEach((input) => {
                const value = settings.categories[input.dataset.category][input.dataset.field];
                input.value = TREND_PERCENT_FIELDS.has(input.dataset.field)
                    ? String(Math.round(value * 1000) / 10) : value;
            });
        }
        trendAlertSave.disabled = Boolean(controlActionInFlight);
        trendAlertTest.disabled = Boolean(controlActionInFlight);
        const line = trend.line || {};
        const cooling = Object.entries(trend.cooling || {}).map(([key, until]) =>
            `${trend.labels[key] || key}（${until.slice(5, 16).replace('T', ' ')} 後可再提醒）`);
        setTextIfChanged(trendAlertStatus, [
            settings.enabled
                ? `最近 ${settings.window_days} 天至少有 ${settings.min_samples} 顆已分級果實時，各類比例達門檻就提醒；同類 ${settings.cooldown_hours} 小時內不重複。`
                : '已關閉：不檢查也不發送趨勢提醒。',
            cooling.length ? `冷卻中：${cooling.join('、')}` : null,
            line.configured ? null : `LINE 尚未完成設定：${notificationConfigReasonText(line)}（提醒仍會顯示在此）`,
        ].filter(Boolean).join('\n'));
        const recent = trend.recent || [];
        const signature = recent.map((alert) => `${alert.id}:${alert.delivery?.status || ''}`).join('|');
        if (trendAlertList.dataset.signature === signature) return;
        trendAlertList.dataset.signature = signature;
        trendAlertList.replaceChildren(...recent.slice(0, 5).map((alert) => {
            const item = document.createElement('li');
            const delivery = alert.delivery;
            // The full message is on LINE; the Dashboard keeps one short line.
            item.textContent = `${alert.created_at.replace('T', ' ')}・${alert.label}・`
                + `${delivery ? (TREND_DELIVERY_TEXT[delivery.status] || delivery.status) : 'LINE 未排送'}`;
            return item;
        }));
    }

    function trendNumber(input, scale = 1) {
        const value = Number(input.value);
        if (input.value === '' || !Number.isFinite(value)) return null;
        return value / scale;
    }

    async function saveTrendAlerts(event) {
        event.preventDefault();
        const settings = { enabled: trendAlertEnabled.checked, categories: {} };
        for (const { input, key } of trendAlertFields) {
            settings[key] = trendNumber(input);
        }
        trendCategoryInputs().forEach((input) => {
            const params = (settings.categories[input.dataset.category] ||= {});
            params[input.dataset.field] = TREND_PERCENT_FIELDS.has(input.dataset.field)
                ? trendNumber(input, 100) : input.value;
        });
        const values = [...trendAlertFields.map(({ key }) => settings[key]),
            ...Object.values(settings.categories).flatMap((params) => Object.values(params))];
        if (values.includes(null)) {
            setMessage('趨勢提醒設定必須填入數字。');
            return;
        }
        if (!beginControlAction('trend_alerts', trendAlertSave)) {
            return;
        }
        try {
            const payload = await postJson('/api/trend_alerts/', settings);
            delete trendAlertForm.dataset.dirty;
            lastState = payload;
            renderState(payload);
            setMessage('趨勢提醒設定已保存。');
        } catch (error) {
            setMessage(`趨勢提醒設定保存失敗：${error.message}`);
        } finally {
            endControlAction(trendAlertSave);
        }
    }

    async function sendTrendAlertTest() {
        if (!beginControlAction('trend_alert_test', trendAlertTest)) {
            return;
        }
        try {
            const payload = await postJson('/api/trend_alerts/test/', {});
            lastState = payload;
            renderState(payload);
            const labels = payload.trend_alert_test?.labels || [];
            setMessage(`已排入 LINE 測試提醒（${labels.length ? `目前偏高：${labels.join('、')}` : '目前沒有類別達到提醒條件'}）。`);
        } catch (error) {
            setMessage(`測試提醒失敗：${error.message}`);
        } finally {
            endControlAction(trendAlertTest);
        }
    }

    function renderGradeCard(data) {
        const grade = gradeCardModel(data);
        detectionGrade.dataset.state = grade.state;
        gradePanel.dataset.state = grade.state;
        setTextIfChanged(detectionGrade, grade.title);
        setTextIfChanged(gradeDetail, grade.detail);
        setTextIfChanged(detectionFlowStatus, detectionFlowText(data));
        setTextIfChanged(viewGrades, viewGradesText(data));
        renderManualGrade(data, grade);
    }

    function detectionFlowText(data) {
        const result = data.detection_result;
        if (!result) return '等待判級與保存';
        const graded = result.classification?.status === 'graded';
        const saved = result.storage?.status === 'saved';
        const steps = [graded ? '已判級' : '尚未判級', saved ? '已保存' : '保存失敗'];
        const sorting = result.sorting || {};
        const labels = {
            skipped: '純拍攝略過分槽',
            not_started: '尚未分槽，Gate 3 保持關閉',
            pending: '分槽中：等待 ESP32',
            running: '分槽中：控制流程執行中',
            completed: '分槽完成：控制流程已回報',
            failed: '分槽失敗',
            timeout: '分槽失敗：回報逾時',
        };
        steps.push(labels[sorting.status] || '尚未分槽');
        if (sorting.recovery_required) steps.push(`需復原：${reasonText(sorting.reason)}`);
        return steps.join(' → ');
    }

    function renderManualGrade(data, grade) {
        const reason = data.manual_grade_disabled_reason;
        // Shown in the always-visible result card, so collapsing the
        // operation bar never hides a round waiting for the operator.
        manualGradePanel.hidden = grade.state !== 'review'
            || !data.detection_result
            || ['not_detection_mode', 'already_manual_graded', 'not_needs_review'].includes(reason);
        manualGradeButtons.forEach((button) => {
            button.disabled = Boolean(controlActionInFlight) || !data.can_manual_grade;
            button.title = data.can_manual_grade ? '' : `無法人工處理：${reasonText(reason)}`;
        });
        manualAbandonButton.hidden = data.hardware_mode !== 'photo_only';
        manualAbandonButton.disabled = Boolean(controlActionInFlight) || !data.can_manual_abandon;
        manualSkipButton.hidden = data.hardware_mode === 'photo_only';
        manualSkipButton.disabled = Boolean(controlActionInFlight) || !data.can_discard;
        setTextIfChanged(manualGradeGuidance, data.can_manual_grade
            ? data.hardware_mode === 'photo_only'
                ? '檢查三張照片後選級，或放棄本輪；人工結果與模型輸出分開保存。'
                : '檢查三張照片後選級，或跳過當顆；跳過須依斷電程序人工移除果實。人工結果與模型輸出分開保存。'
            : `目前無法人工判級：${reasonText(reason)}`);
    }

    function actionGuidanceItems(data, photoOnly) {
        const items = [autoRunGuidanceText(data)];
        recaptureButton.title = '';
        discardButton.title = '';
        autoRunButton.title = autoRunButton.disabled ? items[0] : '';
        if (recaptureButton.disabled && !recaptureButton.hidden) {
            recaptureButton.title = `重新拍攝：${photoOnly
                ? '純拍攝不提供重新拍攝，請先跳過／刪除本輪再開始'
                : data.active_fruit_id ? '目前流程狀態不允許重新拍攝' : '目前沒有拍攝中的資料'}`;
            items.push(recaptureButton.title);
        }
        if (discardButton.disabled && !discardButton.hidden) {
            discardButton.title = `${discardButton.textContent}：${data.status === 'uploading'
                ? '照片上傳中，完成後才能刪除'
                : data.dataset_operation ? '資料處理中' : '目前沒有可刪除的暫存資料'}`;
            items.push(discardButton.title);
        }
        return items;
    }

    function syncOperationDisclosure() {
        const open = operationDetails.open;
        // Reasons stay as button tooltips; the full list shows only when expanded.
        actionGuidance.hidden = !open;
        operationToggle.setAttribute('aria-expanded', String(open));
        operationToggle.setAttribute('aria-label', `${open ? '收合' : '展開'}模式設定`);
    }

    function renderActionGuidance(data, photoOnly) {
        const items = actionGuidanceItems(data, photoOnly);
        const fingerprint = JSON.stringify(items);
        if (fingerprint === lastActionGuidance) {
            return;
        }
        lastActionGuidance = fingerprint;
        actionGuidance.replaceChildren(...items.map((text) => {
            const item = document.createElement('li');
            item.textContent = text;
            return item;
        }));
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

    function sleep(ms) {
        return new Promise((resolve) => window.setTimeout(resolve, ms));
    }

    async function releaseImagesBeforeFileOperation() {
        releaseBoardImages();
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

    function timingStatusText(data) {
        const revision = data.capture_timing_revision ?? 0;
        switch (data.capture_timing_status) {
            case 'applied':
                return `ESP32 已套用參數 revision ${revision}。`;
            case 'pending_esp32_apply':
                return `參數 revision ${revision} 等待 ESP32 在閒置時自動套用。`;
            case 'waiting_esp32':
                return `參數 revision ${revision} 等待 ESP32 連線後自動套用。`;
            default:
                return '等待 Django 與 ESP32 同步硬體參數。';
        }
    }

    function renderCaptureTiming(data) {
        const timing = data.capture_timing || {};
        timingFirstStationInput.value = timing.first_station_settle_ms ?? '';
        timingServoInput.value = timing.servo_settle_ms ?? '';
        timingFruitInput.value = timing.fruit_settle_ms ?? '';
        timingFinalReturnInput.value = timing.final_gate_return_delay_ms ?? '';
        timingIdleCommandPollInput.value = timing.idle_command_poll_interval_ms ?? '';
        timingFeederStopInput.value = timing.feeder_stop_us ?? '';
        timingFeederDriveInput.value = timing.feeder_drive_us ?? '';
        timingFeederMaxRunInput.value = timing.feeder_max_run_ms ?? '';
        testFeederButton.disabled = Boolean(controlActionInFlight) || !data.can_test_feeder;
        testFeederButton.title = data.can_test_feeder
            ? ''
            : `目前無法測試送料：${reasonText(data.feeder_test_disabled_reason || 'system_not_idle')}`;
        setTextIfChanged(timingConfigStatus, timingStatusText(data));
        const timingApplied = data.capture_timing_status === 'applied';
        timingSummaryStatus.dataset.state = timingApplied ? 'ready' : 'pending';
        setTextIfChanged(timingSummaryStatus, timingApplied ? '已同步' : '待同步');
    }

    async function testFeederOnce(event) {
        // The button sits inside <summary>; clicking it must not fold the panel.
        event.preventDefault();
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

    async function resetRuntime(event) {
        // The button sits inside <summary>; clicking it must not fold the panel.
        event.preventDefault();
        if (!confirm('將清空 Django 所有執行狀態並取消已選網路；照片與 dataset 會保留。'
            + '請先確認機台停止、Gate 3 與分類器沒有果實，再繼續。')) {
            return;
        }
        if (!beginControlAction('runtime-reset', runtimeResetButton)) {
            return;
        }
        try {
            const payload = await postJson('/api/runtime/reset/');
            lastState = payload;
            renderState(payload);
            setMessage(payload.message || 'Django 狀態已完全重置。');
        } catch (error) {
            setMessage(`完全重置失敗：${error.message}`);
        } finally {
            endControlAction(runtimeResetButton);
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
            photo_capture_requested: '純拍攝已要求下一張照片',
            photo_interval_started: '照片已保存，等待間隔後拍攝下一張',
            detection_stage_saved: '檢測階段產物已保存',
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

    async function finishFeederWait() {
        if (!lastState?.can_finish_feeder_wait || !confirm(
            '是否確認送料已停止、本輪已清空且手已離開？確認後 ESP32 會歸位並結束等待，不會送料。',
        )) return;
        if (!beginControlAction('finish-feeder-wait', finishFeederWaitButton)) return;
        try {
            const payload = await postJson('/api/feeder/finish_wait/', { recovery_confirmed: true });
            lastState = payload;
            renderState(payload);
        } catch (error) {
            setMessage(`結束送料等待失敗：${error.message}`);
        } finally {
            endControlAction(finishFeederWaitButton);
        }
    }

    async function toggleAutoRun() {
        if (!beginControlAction('auto-run', autoRunButton)) {
            return;
        }
        try {
            if (lastState && lastState.hardware_mode === 'photo_only') {
                const recoveryRequired = Boolean(lastState.network_recovery)
                    && lastState.network_recovery_confirm_disabled_reason !== 'network_recovery_not_required';
                const recoveryConfirmed = recoveryRequired && confirm(
                    '是否已刪除本輪暫存資料並完成安全清理？確認後將開始新一輪拍攝。',
                );
                if (recoveryRequired && !recoveryConfirmed) return;
                const payload = await postJson('/api/photo_capture/', {recovery_confirmed: recoveryConfirmed});
                lastState = payload;
                renderState(payload);
                return;
            }
            const enabled = !Boolean(lastState && lastState.auto_run_enabled);
            let recoveryConfirmed = false;
            if (enabled && lastState && (
                lastState.sorter_recovery_required
                || lastState.network_recovery
                || lastState.auto_run_recovery_reason === 'gate3_manual_removal_required'
            )) {
                recoveryConfirmed = confirm(
                    lastState.network_recovery
                        ? '是否已刪除或隔離本輪資料，依斷電程序移除果實，確認 Gate 3 與分類器安全且手已離開？確認後會讓馬達歸位。'
                        : '請先依主畫面提示確認 Gate 3、分類器與果實位置安全；是否已完成檢查並人工復原？',
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
            setMessage(payload.recovery_pending
                ? payload.network_recovery?.reason === 'gate3_manual_removal_required'
                    ? '已確認人工復原，等待 ESP32 安全歸位與清鎖交握；完成後請再按「開始拍攝」。'
                    : '已確認人工復原，等待 ESP32 安全歸位與清鎖交握。'
                : enabled
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
            await releaseImagesBeforeFileOperation();
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

    async function manualGrade(label) {
        // ``label === null`` abandons the round without a grade.
        const actionButton = label === null
            ? manualAbandonButton
            : manualGradeButtons.find((button) => button.dataset.label === label);
        const result = lastState && lastState.detection_result;
        if (!result) {
            return;
        }
        if (label === null && !window.confirm(
            `確定放棄 ${result.fruit_id}？本輪不分級，照片與失敗原因仍保留在檢測紀錄。`,
        )) {
            return;
        }
        if (!beginControlAction('manual_grade', actionButton)) {
            return;
        }
        const decision = label === null ? '放棄本輪' : `人工判定「${label}」`;
        try {
            const payload = await postJson('/api/detection/manual_grade/', {
                ...(label === null ? { abandon: true } : { label }),
                fruit_id: result.fruit_id,
                result_id: result.result_id,
            });
            lastState = payload;
            renderState(payload);
            setMessage(payload.duplicate
                ? `${payload.fruit_id} 已是${decision}，未重複寫入紀錄。`
                : `${payload.fruit_id} 已${decision}並更新同顆檢測紀錄。`);
        } catch (error) {
            setMessage(`${decision}失敗：${error.message}`);
        } finally {
            endControlAction(actionButton);
            await refreshState();
        }
    }

    async function discardCurrent() {
        const fruitId = lastState && (
            lastState.discardable_fruit_id || lastState.active_fruit_id
        );
        if (!fruitId) {
            return;
        }
        const gate3Recovery = lastState.gate3_manual_removal_required
            || (lastState.work_mode === 'detection' && lastState.hardware_mode !== 'photo_only');
        const prompt = gate3Recovery
            ? `跳過當顆（人工移除）\n\n${fruitId} 已保存的檢測紀錄會保留；只清理本輪暫存資料，不自動開啟 Gate 3 或送料。\n\n`
                + '請切斷伺服電源，確認不會動作後人工移除果實；手離開機構並恢復供電後，按「確認安全復原」讓 ESP32 歸位。交握完成後，再按「開始拍攝」。\n\n'
                + '是否跳過當顆並進入人工安全復原？'
            : `確定刪除 ${fruitId} 的暫存照片嗎？`;
        if (!confirm(prompt)) {
            return;
        }
        if (!beginControlAction('discard', discardButton)) {
            return;
        }
        try {
            await releaseImagesBeforeFileOperation();
            const payload = await postJson('/api/discard/', {
                fruit_id: fruitId,
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
        const datasetMode = lastState?.work_mode;
        const modeName = datasetMode === 'detection' ? '自動檢測' : '蒐集';
        if (!confirm(`確定要重置${modeName}資料區嗎？這會刪除此模式的暫存、照片與結果並重置編號，另一模式及舊資料保留。`)) {
            return;
        }
        if (!confirm('再次確認：此動作無法復原，下一筆 ID 會回到 fruit_001。')) {
            return;
        }
        if (!beginControlAction('reset-dataset', resetDatasetButton)) {
            return;
        }
        try {
            await releaseImagesBeforeFileOperation();
            const payload = await postJson('/api/reset_dataset/', { dataset_mode: datasetMode });
            lastState = payload;
            renderState(payload);
            setMessage(`${modeName}資料區已重置，下一筆資料將從 fruit_001 開始。${warningText(payload)}`);
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
        if (!lastState?.work_mode) {
            setMessage('尚未取得目前資料區，請稍候再開啟資料夾。');
            return;
        }
        try {
            const params = new URLSearchParams({ dataset_mode: lastState.work_mode });
            const response = await fetch(`/api/open_dataset_folder/?${params}`);
            const payload = await response.json();
            if (!response.ok) throw new Error(payload.message || `HTTP ${response.status}`);
            if (!payload.opened) {
                setMessage(`資料夾位置：${payload.path}`);
            }
        } catch (error) {
            setMessage(`開啟資料夾失敗：${error.message}`);
        }
    }

    async function exportDetection() {
        if (!lastState?.work_mode) {
            setMessage('尚未取得目前資料區，請稍候再匯出。');
            return;
        }
        exportInFlight = true;
        exportButton.disabled = true;
        exportButton.textContent = '匯出中…';
        try {
            const params = new URLSearchParams({
                dataset_mode: lastState.work_mode,
                include_results: exportIncludeResults.checked ? '1' : '0',
            });
            const response = await fetch(`/api/export_detection/?${params}`);
            if (!response.ok) {
                const payload = await response.json().catch(() => ({}));
                throw new Error(payload.message || `HTTP ${response.status}`);
            }
            const disposition = response.headers.get('Content-Disposition') || '';
            const filename = /filename="?([^";]+)"?/.exec(disposition)?.[1] || 'detection_export.zip';
            const url = URL.createObjectURL(await response.blob());
            const link = document.createElement('a');
            link.href = url;
            link.download = filename;
            link.click();
            setTimeout(() => URL.revokeObjectURL(url), 0);
            setMessage(`已匯出 ${filename}。`);
        } catch (error) {
            setMessage(`匯出檢測報告失敗：${error.message}`);
        } finally {
            exportInFlight = false;
            exportButton.disabled = false;
            exportButton.textContent = '匯出檢測報告';
        }
    }

    document.getElementById('btn-set-counter').addEventListener('click', setCounter);
    exportButton.addEventListener('click', exportDetection);
    testFeederButton.addEventListener('click', testFeederOnce);
    runtimeResetButton.addEventListener('click', resetRuntime);
    autoRunButton.addEventListener('click', toggleAutoRun);
    operationToggle.addEventListener('click', () => {
        operationDetails.open = !operationDetails.open;
    });
    operationDetails.addEventListener('toggle', syncOperationDisclosure);
    workModeSelect.addEventListener('change', async () => {
        const workMode = workModeSelect.value;
        const hardwareMode = hardwareModeSelect.value;
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
    notificationToggle.addEventListener('click', toggleNotification);
    trendAlertForm.addEventListener('submit', saveTrendAlerts);
    trendAlertTest.addEventListener('click', sendTrendAlertTest);
    trendAlertForm.addEventListener('input', () => { trendAlertForm.dataset.dirty = 'true'; });
    recaptureButton.addEventListener('click', recaptureCurrent);
    document.getElementById('btn-open-folder').addEventListener('click', openDatasetFolder);
    finishFeederWaitButton.addEventListener('click', finishFeederWait);
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
    manualGradeButtons.forEach((button) => {
        button.addEventListener('click', () => manualGrade(button.dataset.label));
    });
    manualAbandonButton.addEventListener('click', () => manualGrade(null));
    manualSkipButton.addEventListener('click', discardCurrent);
    classifyButtons.forEach((button) => {
        button.addEventListener('click', () => classify(button.dataset.label));
    });

    restartStatePolling(0);
    restartWebRTCPolling(0);
