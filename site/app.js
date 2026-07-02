  const API_BASE = 'https://yotamjacob--hebrew-video-pipeline-api.modal.run';
  const API      = API_BASE + '/process/';

  const VEO_ENABLED = false;

  // Limits
  const MAX_BYTES    = 500 * 1024 * 1024; // 500 MB
  const WARN_BYTES   = 150 * 1024 * 1024; // 150 MB
  const MAX_SECS     = 20 * 60;            // 20 min
  const WARN_SECS    = 8  * 60;            // 8 min

  const uploadZone   = document.getElementById('uploadZone');
  const fileInput    = document.getElementById('fileInput');
  const fileInfo     = document.getElementById('fileInfo');
  const fileName     = document.getElementById('fileName');
  const fileDetail   = document.getElementById('fileDetail');
  const clearFile    = document.getElementById('clearFile');
  const runBtn       = document.getElementById('runBtn');
  const statusCard   = document.getElementById('statusCard');
  const checklistEl    = document.getElementById('statusChecklist');
  const statusDone     = document.getElementById('statusDone');
  const statusError    = document.getElementById('statusError');
  const errorMsg       = document.getElementById('errorMsg');

  // Checklist step elements
  const checkItems = {
    upload:  document.getElementById('checkUpload'),
    enhance: document.getElementById('checkEnhance'),
    cut:     document.getElementById('checkCut'),
    burn:    document.getElementById('checkBurn'),
  };
  const checkTimeEls = {
    upload:  document.getElementById('checkUploadTime'),
    enhance: document.getElementById('checkEnhanceTime'),
    cut:     document.getElementById('checkCutTime'),
    burn:    document.getElementById('checkBurnTime'),
  };
  let stepTimers = {};         // step name → { start, intervalId }
  let stepEndSecs = {};        // step name → seconds taken (persists across hide/show)
  const downloadBtn  = document.getElementById('downloadBtn');
  const retryBtn     = document.getElementById('retryBtn');
  const advToggle    = document.getElementById('advancedToggle');
  const advPanel     = document.getElementById('advancedPanel');
  const aggrSlider   = document.getElementById('aggrSlider');
  const aggrDesc     = document.getElementById('aggrDesc');
  const AGGR_MAP = [
    { silence: 1.5, padding: 0.35, label: 'Gentle — cuts pauses over 1.5 s' },
    { silence: 0.8, padding: 0.25, label: 'Mild — cuts pauses over 0.8 s' },
    { silence: 0.5, padding: 0.20, label: 'Balanced — cuts pauses over 0.5 s' },
    { silence: 0.3, padding: 0.12, label: 'Aggressive — cuts pauses over 0.3 s' },
    { silence: 0.2, padding: 0.06, label: 'Very aggressive — cuts pauses over 0.2 s' },
  ];
  const noticeBlock  = document.getElementById('noticeBlock');
  const noticeWarn   = document.getElementById('noticeWarn');
  const noticeNet    = document.getElementById('noticeNet');

  let selectedFile = null;
  let videoDuration = null;
  let resultBlob      = null;
  let resultName      = 'edited_video.mp4';
  let uploadTimer     = null;  // drives the upload step's elapsed clock
  let elapsedTimer    = null;  // drives enhance→cut transition timer
  let blocked         = false;
  let videoKey        = null;
  let cutsData        = [];            // gaps removed by silence cutting (from /process result)
  let restoredCuts    = new Set();     // gap indexes the user wants back
  let appliedRestored = new Set();     // gap indexes already applied via /rerender
  let captionsData    = [];
  let cutFilename     = '';
  let pollController  = null;
  let currentCallId   = null;
  const swResolvers   = new Map(); // callId -> resolve, for SW-won polls
  let currentPollInfo = null;       // { callId, pollUrl, deadline } while a poll is active
  let isUploading     = false;
  let runStartTime    = null;
  let captionFont      = 'Heebo';
  let captionMarginPct = 0.08;
  let captionFontSize  = 48;
  let burnMode        = false;
  let currentUploadKey = null;
  let pendingAnalyses        = 0;
  let stockBrollAnalyzed     = false;
  let lastAnalyzedSignature  = '';
  let selectedHookIdx        = -1;
  let hookGenAborted         = false;
  let hookThumbnail          = null;
  let _playerSetupDone       = false;
  let _playerDispW           = 0;   // detected display width (accounts for browser rotation)

  // ── Background-job persistence (survives tab close/reload on mobile) ──
  const JOB_KEY = 'hebpipe_job';
  const JOB_TTL = 45 * 60 * 1000; // 45 min — Modal jobs expire after that

  function saveJob(type, callId, extra) {
    localStorage.setItem(JOB_KEY, JSON.stringify({ type, callId, ts: Date.now(), ...extra }));
  }
  function clearSavedJob() {
    localStorage.removeItem(JOB_KEY);
  }
  function loadSavedJob() {
    try {
      const raw = localStorage.getItem(JOB_KEY);
      if (!raw) return null;
      const job = JSON.parse(raw);
      if (Date.now() - job.ts > JOB_TTL) { clearSavedJob(); return null; }
      return job;
    } catch { clearSavedJob(); return null; }
  }

  async function resumeSavedJob() {
    const job = loadSavedJob();
    document.getElementById('reconnectBanner').style.display = 'none';
    if (!job) return;

    if (job.type === 'process') {
      // Reconnect: can't know which processing step we're in — show enhance as active
      statusCard.classList.add('visible');
      checklistEl.style.display = 'block';
      statusDone.classList.remove('visible');
      statusError.classList.remove('visible');
      _resetChecklist();
      checkItems.upload.className = 'check-item done';
      checkTimeEls.upload.textContent = 'cached';
      _stepActivate('enhance');
      _procStartMs = Date.now();
      try {
        const result = await pollForJSON(`${API_BASE}/process_poll/${job.callId}/`, 900_000, job.callId);
        _stepsDoneProcessing();
        clearSavedJob();
        captionsData = result.captions || [];
        videoKey     = result.video_key;
        cutsData = result.cuts || []; restoredCuts = new Set(); appliedRestored = new Set();
        cutFilename  = (job.filename || 'video').replace(/\.[^/.]+$/, '') + '_cut.mp4';
        if (captionsData.length > 0) {
          document.getElementById('cancelBtn').style.display = 'none';
          showCaptionEditor();
        } else {
          const dlResp = await fetch(`${API_BASE}/download/${videoKey}/?filename=${encodeURIComponent(cutFilename)}`);
          resultBlob = new Blob([await dlResp.arrayBuffer()], { type: 'video/mp4' });
          resultName = cutFilename;
          showDone();
        }
      } catch (err) {
        clearSavedJob();
        if (err.name !== 'AbortError')
          showError('Could not reconnect — job may have expired. Please start again.');
      }

    } else if (job.type === 'burn') {
      statusCard.classList.add('visible');
      checklistEl.style.display = 'block';
      statusDone.classList.remove('visible');
      statusError.classList.remove('visible');
      _resetChecklist();
      checkItems.upload.className = 'check-item done';
      checkItems.enhance.className = 'check-item done';
      checkItems.cut.className = 'check-item done';
      _stepActivate('burn');
      runBtn.style.display = 'block';
      runBtn.disabled = true;
      try {
        const burnResult = await pollForJSON(`${API_BASE}/burn_poll/${job.callId}/`, 600_000, job.callId);
        _stepDone('burn');
        clearSavedJob();
        // Video is ready — show success + schedule card immediately (no download needed)
        checklistEl.style.display = 'none';
        document.getElementById('burnSuccessBanner').style.display = 'flex';
        window._schedCtx = {
          outputKey: burnResult.output_key,
          filename:  job.outputFilename,
          videoKey:  (typeof videoKey !== 'undefined' ? videoKey : null),
        };
        if (typeof revealScheduleCard === 'function') revealScheduleCard();
        // Device download is optional and non-blocking
        try {
          const dlResp = await fetch(
            `${API_BASE}/download/${burnResult.output_key}/?filename=${encodeURIComponent(job.outputFilename)}`
          );
          if (!dlResp.ok) throw new Error(`Download failed (${dlResp.status})`);
          resultBlob = new Blob([await dlResp.arrayBuffer()], { type: 'video/mp4' });
          resultName = job.outputFilename;
          triggerDownload();
        } catch (dlErr) {
          if (dlErr.name !== 'AbortError')
            console.warn('Device download failed (video is still scheduled-ready):', dlErr.message);
        }
      } catch (err) {
        clearSavedJob();
        if (err.name !== 'AbortError')
          showError('Could not reconnect — job may have expired. Please start again.');
      } finally {
        runBtn.disabled = false;
      }
    }
  }

  function bumpPending(delta) {
    pendingAnalyses = Math.max(0, pendingAnalyses + delta);
    validateCaptionTimes();
  }

  function getCaptionsFromEditor() {
    return Array.from(document.querySelectorAll('#captionsList .caption-row')).map(row => ({
      start: parseFloat(row.querySelector('.caption-start').value) || 0,
      end:   parseFloat(row.querySelector('.caption-end').value)   || 0,
      text:  row.querySelector('.caption-input').value,
    }));
  }

  function getEditedCaptions() { return getCaptionsFromEditor(); }

  function getCaptionsSignature() {
    return getCaptionsFromEditor().map(c => c.text).join('|');
  }

  function downloadSRT() {
    const captions = getCaptionsFromEditor();
    if (!captions.length) return;
    function toSrtTime(s) {
      const h   = Math.floor(s / 3600);
      const m   = Math.floor((s % 3600) / 60);
      const sec = Math.floor(s % 60);
      const ms  = Math.round((s % 1) * 1000);
      return `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')},${String(ms).padStart(3,'0')}`;
    }
    const srt = captions.map((c, i) =>
      `${i + 1}\n${toSrtTime(c.start)} --> ${toSrtTime(c.end)}\n${c.text}`
    ).join('\n\n') + '\n';
    const blob = new Blob([srt], { type: 'text/plain;charset=utf-8' });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    const base = selectedFile ? selectedFile.name.replace(/\.[^.]+$/, '') : 'captions';
    a.href = url; a.download = base + '.srt'; a.click();
    URL.revokeObjectURL(url);
  }

  function showConfirmModal(title, body, okText) {
    return new Promise(resolve => {
      document.getElementById('confirmTitle').textContent = title;
      document.getElementById('confirmBody').textContent  = body;
      document.getElementById('confirmOk').textContent    = okText || 'Confirm';
      const overlay = document.getElementById('confirmOverlay');
      overlay.style.display = 'flex';
      function cleanup(result) {
        overlay.style.display = 'none';
        document.getElementById('confirmOk').onclick     = null;
        document.getElementById('confirmCancel').onclick = null;
        resolve(result);
      }
      document.getElementById('confirmOk').onclick     = () => cleanup(true);
      document.getElementById('confirmCancel').onclick = () => cleanup(false);
    });
  }

  function setSetupLocked(locked) {
    ['uploadCard', 'optionsCard'].forEach(id =>
      document.getElementById(id).classList.toggle('setup-locked', locked));
  }

  // ── File selection ──
  fileInput.addEventListener('change', () => handleFile(fileInput.files[0]));
  uploadZone.addEventListener('dragover', e => { e.preventDefault(); uploadZone.classList.add('drag-over'); });
  uploadZone.addEventListener('dragleave', () => uploadZone.classList.remove('drag-over'));
  uploadZone.addEventListener('drop', e => {
    e.preventDefault(); uploadZone.classList.remove('drag-over');
    handleFile(e.dataTransfer.files[0]);
  });

  async function handleFile(file) {
    if (!file) return;
    if (!file.type.startsWith('video/') && !file.name.match(/\.(mp4|mov|mkv|avi|webm)$/i)) {
      showBlockNotice('Unsupported file type', 'Please upload a video file (MP4, MOV, MKV).');
      return;
    }

    selectedFile = file;
    fileName.textContent = file.name;
    fileInfo.classList.add('visible');
    clearNotices();
    resetStatus();

    // Size check (instant)
    if (file.size > MAX_BYTES) {
      fileDetail.textContent = formatSize(file.size) + ' · too large';
      showBlockNotice('File too large', `Max size is 500 MB. This file is ${formatSize(file.size)}. Please trim or compress it first.`);
      blocked = true;
      runBtn.disabled = true;
      return;
    }

    // Read duration from the video element
    fileDetail.textContent = formatSize(file.size) + ' · reading…';
    videoDuration = await getVideoDuration(file);

    if (videoDuration !== null) {
      fileDetail.textContent = formatSize(file.size) + ' · ' + formatDuration(videoDuration);
    } else {
      fileDetail.textContent = formatSize(file.size);
    }

    // Validate duration
    if (videoDuration !== null && videoDuration > MAX_SECS) {
      showBlockNotice(
        'Video too long',
        `Max length is 20 minutes. This video is ${formatDuration(videoDuration)}. Please trim it before uploading.`
      );
      blocked = true;
      runBtn.disabled = true;
      return;
    }

    blocked = false;
    runBtn.disabled = false;

    // Warnings & estimated time
    if (file.size > WARN_BYTES) {
      showWarnNotice('Large file', `${formatSize(file.size)} — the upload itself may take 30–60 seconds depending on your connection.`);
    } else if (videoDuration !== null && videoDuration > WARN_SECS) {
      showWarnNotice('Long video', `${formatDuration(videoDuration)} — processing will take a few minutes. Keep the page open.`);
    }

    updateTimeEstimate();

    checkNetwork();

    // Fire-and-forget GPU warmup so the container is ready by the time the user clicks Run
    fetch(API_BASE + '/warmup/').catch(() => {});
  }

  clearFile.addEventListener('click', () => {
    if (!confirm('Remove the attached video?')) return;
    selectedFile = null; videoDuration = null;
    fileInput.value = '';
    fileInfo.classList.remove('visible');
    clearNotices();
    runBtn.disabled = true;
    resetStatus();
  });

  // ── Duration helper ──
  function getVideoDuration(file) {
    return new Promise(resolve => {
      const video = document.createElement('video');
      video.preload = 'metadata';
      video.onloadedmetadata = () => { URL.revokeObjectURL(video.src); resolve(video.duration); };
      video.onerror = () => resolve(null);
      video.src = URL.createObjectURL(file);
    });
  }

  function estimatedTime(secs) {
    const useEnhance    = document.getElementById('enhanceAudio').checked;
    const useTranscribe = document.getElementById('cutSilences').checked || document.getElementById('burnCaptions').checked;
    const useBroll      = VEO_ENABLED && document.getElementById('suggestBrolls').checked;
    const useStockBroll = document.getElementById('stockBroll').checked;

    let total = 0;
    if (useEnhance || useTranscribe) {
      total += 90;
    } else if (useBroll || useStockBroll) {
      total += 20;
    }
    if (useEnhance)     total += secs * 3.5;   // DeepFilterNet ≈ 3× realtime on T4
    if (useTranscribe)  total += secs / 8;
    if (useEnhance || useTranscribe) total += 60;
    if (useBroll)       total += 60;
    if (useStockBroll)  total += 30;   // Claude moment selection + 2 API searches

    const lo = Math.max(1, Math.round(total / 60));
    const hi = lo + 1;
    return `Estimated ${lo}–${hi} min`;
  }

  function updateTimeEstimate() {
    if (!videoDuration) return;
    document.getElementById('timeEstimateText').textContent = estimatedTime(videoDuration);
    document.getElementById('timeEstimate').style.display = 'flex';
  }

  // ── Advanced ──
  advToggle.addEventListener('click', () => {
    advToggle.classList.toggle('open'); advPanel.classList.toggle('open');
  });

  function checkToolsEnabled() {
    if (!selectedFile || burnMode) return;
    const ids = ['cutSilences', 'burnCaptions', 'enhanceAudio', 'stockBroll'];
    if (VEO_ENABLED) ids.push('suggestBrolls');
    runBtn.disabled = !ids.some(id => document.getElementById(id)?.checked);
  }
  ['cutSilences', 'burnCaptions', 'enhanceAudio', 'suggestBrolls', 'stockBroll'].forEach(id => {
    document.getElementById(id)?.addEventListener('change', () => { checkToolsEnabled(); updateTimeEstimate(); });
  });
  const aggrVal = document.getElementById('aggrVal');
  aggrSlider.addEventListener('input', () => {
    const a = AGGR_MAP[aggrSlider.value - 1];
    aggrDesc.textContent = a.label;
    aggrVal.textContent  = a.silence + ' s';
  });

  // ── Run ──
  runBtn.addEventListener('click', () => { if (burnMode) doBurn(); else run(); });
  retryBtn.addEventListener('click', run);

  document.getElementById('cancelBtn').addEventListener('click', () => {
    if (!confirm('Cancel processing? The job on the server will be stopped.')) return;
    const idToCancel = currentCallId;
    resetStatus();
    runBtn.disabled = false;
    if (idToCancel) {
      fetch(`${API_BASE}/cancel/${idToCancel}/`).catch(() => {});
    }
  });

  window.addEventListener('beforeunload', (e) => {
    const editing = document.getElementById('captionEditorCard').style.display !== 'none';
    const hasBrollWork = selectedBrolls.length > 0 || Object.keys(stockBrollSelections).length > 0;
    if (isUploading || pollController || editing || resultBlob || hasBrollWork) {
      e.preventDefault();
      e.returnValue = '';
    }
  });

  // Phase thresholds calibrated for T4 GPU: deepfilter ~10× realtime, Whisper turbo ~12× realtime
  function getProcMessages(dur) {
    dur = dur || 60;
    const enhanceEnd    = Math.max(25, Math.round(dur * 0.12)); // deepfilter on T4
    const transcribeEnd = Math.max(50, Math.round(enhanceEnd + dur * 0.10)); // whisper turbo
    const renderStart   = transcribeEnd + 10;
    const encodeEnd     = renderStart + Math.max(30, Math.round(dur * 0.22));
    return [
      [0,            'Starting up…'],
      [18,           'Enhancing audio…'],
      [enhanceEnd,   'Transcribing…'],
      [transcribeEnd,'Cutting silences…'],
      [renderStart,  'Rendering…'],
      [encodeEnd,    'Almost there…'],
    ];
  }

  const BURN_MESSAGES = [
    [0,  'Burning captions…'],
    [25, 'Encoding video…'],
    [50, 'Almost there…'],
  ];

  async function run(isRetry = false) {
    if (!selectedFile || blocked) return;

    resultBlob = null;
    showUploadProgress();

    const aggr = AGGR_MAP[aggrSlider.value - 1];
    const brollOn      = VEO_ENABLED && document.getElementById('suggestBrolls').checked;
    const stockBrollOn = document.getElementById('stockBroll').checked;
    const needTranscript = (brollOn || stockBrollOn) && !document.getElementById('burnCaptions').checked;
    const params = new URLSearchParams({
      filename:             selectedFile.name,
      cut_silences:         document.getElementById('cutSilences').checked  ? 'true' : 'false',
      burn_captions:        document.getElementById('burnCaptions').checked ? 'true' : 'false',
      enhance_audio:        document.getElementById('enhanceAudio').checked ? 'true' : 'false',
      transcribe_for_broll: needTranscript ? 'true' : 'false',
      min_silence:          aggr.silence,
      padding:              aggr.padding,
    });

    try {
      // Phase 1: upload video in chunks
      const uploadKey = await chunkedUpload(selectedFile, (pct) => {
        document.getElementById('uploadBarFill').style.width = (pct * 100).toFixed(0) + '%';
        document.getElementById('uploadBarPct').textContent  = (pct * 100).toFixed(0) + '%';
      });
      _stepDone('upload');
      document.getElementById('uploadBarRow').style.display = 'none';

      currentUploadKey = uploadKey;

      // Phase 2: spawn processing job (tiny request — just params, no body)
      runStartTime = Date.now();
      showProcessing(getProcMessages(videoDuration));
      params.set('key', uploadKey);
      const spawnResp = await fetch(`${API_BASE}/process/?${params}`, { method: 'POST' });
      if (spawnResp.status !== 202) {
        const body = await spawnResp.json().catch(() => ({}));
        throw new Error(body.error || `Spawn failed (${spawnResp.status})`);
      }
      const { call_id } = await spawnResp.json();

      // Poll until processing is done — returns JSON {captions, video_key}
      currentCallId = call_id;
      saveJob('process', call_id, { filename: selectedFile.name });
      const pollUrl = `${API_BASE}/process_poll/${call_id}/`;
      const result = await pollForJSON(pollUrl, 900_000, call_id);
      _stepsDoneProcessing();
      clearSavedJob();

      captionsData = result.captions;
      videoKey     = result.video_key;
      cutsData = result.cuts || []; restoredCuts = new Set(); appliedRestored = new Set();
      cutFilename  = (selectedFile.name || 'video').replace(/\.[^/.]+$/, '') + '_cut.mp4';

      const brollActive      = VEO_ENABLED && document.getElementById('suggestBrolls').checked;
      const stockBrollActive = document.getElementById('stockBroll').checked;
      if (captionsData.length > 0 || brollActive) {
        // Keep checklist visible (steps 1-3 done) while user edits captions
        document.getElementById('cancelBtn').style.display = 'none';
        showCaptionEditor();
        startBrollAnalysis();
      } else {
        // No captions, no B-roll — download directly
        const dlResp = await fetch(`${API_BASE}/download/${videoKey}/?filename=${encodeURIComponent(cutFilename)}`);
        resultBlob = new Blob([await dlResp.arrayBuffer()], { type: 'video/mp4' });
        resultName = cutFilename;
        showDone();
      }
    } catch (err) {
      clearInterval(elapsedTimer);
      if (err.name === 'AbortError') return;
      console.error('Process error:', err.message);
      clearSavedJob();
      if (!isRetry && err.message.includes('Network error')) {
        // GPU warmup retry — keep checklist, just update upload step label
        checkItems.upload.className = 'check-item done';
        _stepActivate('enhance');
        await new Promise(r => setTimeout(r, 8000));
        run(true);
      } else {
        showError(err.message);
      }
    }
  }

  async function rerun() {
    if (!currentUploadKey || !selectedFile) return;

    // Hide editor cards and reset to pre-caption state
    burnMode = false;
    captionsData = [];
    videoKey = null;
    cutsData = []; restoredCuts = new Set(); appliedRestored = new Set();
    resultBlob = null;
    ['captionEditorCard', 'hookCard', 'brollCard', 'stockBrollCard'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.style.display = 'none';
    });
    document.getElementById('reprocessBtn').style.display = 'none';
    document.getElementById('burnSuccessBanner').style.display = 'none';
    { const _sc = document.getElementById('scheduleCard'); if (_sc) _sc.style.display = 'none'; }
    setSetupLocked(true);

    const aggr = AGGR_MAP[aggrSlider.value - 1];
    const brollOn        = VEO_ENABLED && document.getElementById('suggestBrolls').checked;
    const stockBrollOn   = document.getElementById('stockBroll').checked;
    const needTranscript = (brollOn || stockBrollOn) && !document.getElementById('burnCaptions').checked;
    const params = new URLSearchParams({
      filename:             selectedFile.name,
      cut_silences:         document.getElementById('cutSilences').checked  ? 'true' : 'false',
      burn_captions:        document.getElementById('burnCaptions').checked ? 'true' : 'false',
      enhance_audio:        document.getElementById('enhanceAudio').checked ? 'true' : 'false',
      transcribe_for_broll: needTranscript ? 'true' : 'false',
      min_silence:          aggr.silence,
      padding:              aggr.padding,
      key:                  currentUploadKey,
    });

    runStartTime = Date.now();
    runBtn.style.display = 'none';

    // Show checklist: upload already done (cached), enhance + cut pending
    statusCard.classList.add('visible');
    checklistEl.style.display = 'block';
    statusDone.classList.remove('visible');
    statusError.classList.remove('visible');
    _resetChecklist();
    checkItems.upload.className = 'check-item done';
    checkTimeEls.upload.textContent = 'cached';
    showProcessing(getProcMessages(videoDuration));

    try {
      const spawnResp = await fetch(`${API_BASE}/process/?${params}`, { method: 'POST' });
      if (spawnResp.status !== 202) {
        const body = await spawnResp.json().catch(() => ({}));
        throw new Error(body.error || `Spawn failed (${spawnResp.status})`);
      }
      const { call_id } = await spawnResp.json();

      currentCallId = call_id;
      saveJob('process', call_id, { filename: selectedFile.name });
      const result = await pollForJSON(`${API_BASE}/process_poll/${call_id}/`, 900_000, call_id);
      _stepsDoneProcessing();
      clearSavedJob();

      captionsData = result.captions;
      videoKey     = result.video_key;
      cutsData = result.cuts || []; restoredCuts = new Set(); appliedRestored = new Set();
      cutFilename  = (selectedFile.name || 'video').replace(/\.[^/.]+$/, '') + '_cut.mp4';

      const brollActive      = VEO_ENABLED && document.getElementById('suggestBrolls').checked;
      const stockBrollActive = document.getElementById('stockBroll').checked;
      if (captionsData.length > 0 || brollActive) {
        document.getElementById('cancelBtn').style.display = 'none';
        showCaptionEditor();
        startBrollAnalysis();
      } else {
        const dlResp = await fetch(`${API_BASE}/download/${videoKey}/?filename=${encodeURIComponent(cutFilename)}`);
        resultBlob = new Blob([await dlResp.arrayBuffer()], { type: 'video/mp4' });
        resultName = cutFilename;
        showDone();
      }
    } catch (err) {
      clearInterval(elapsedTimer);
      if (err.name === 'AbortError') return;
      console.error('Re-process error:', err.message);
      clearSavedJob();
      showError(err.message);
    }
  }

  document.getElementById('reprocessBtn').addEventListener('click', rerun);

  // Upload file in chunks to the Modal ASGI endpoint (streaming body, no 303 redirect issue).
  // Returns the upload key to pass to /process/?key=...
  const CHUNK_SIZE = 5 * 1024 * 1024;
  const UPLOAD_CONCURRENCY = 16;
  async function chunkedUpload(file, onProgress) {
    const key = crypto.randomUUID().replace(/-/g, '');
    const totalChunks = Math.ceil(file.size / CHUNK_SIZE);
    let bytesUploaded = 0;

    async function uploadChunk(i) {
      const start = i * CHUNK_SIZE;
      const end   = Math.min(start + CHUNK_SIZE, file.size);
      const slice = file.slice(start, end);
      const MAX_ATTEMPTS = 4;
      for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt++) {
        if (attempt > 0) await new Promise(r => setTimeout(r, attempt * 1500));
        try {
          const resp = await fetch(
            `${API_BASE}/upload_chunk/?key=${key}&index=${i}`,
            { method: 'POST', headers: {'Content-Type': 'application/octet-stream'}, body: slice }
          );
          if (resp.ok) {
            bytesUploaded += (end - start);
            onProgress(bytesUploaded / file.size);
            return;
          }
          // Hard client errors (not 408/429) — don't retry
          if (resp.status >= 400 && resp.status < 500 && resp.status !== 408 && resp.status !== 429) {
            const body = await resp.json().catch(() => ({}));
            throw new Error(body.error || `Upload failed at chunk ${i} (${resp.status})`);
          }
          // 408, 429, 5xx — fall through to retry
          if (attempt === MAX_ATTEMPTS - 1)
            throw new Error(`Upload failed at chunk ${i} after ${MAX_ATTEMPTS} attempts (${resp.status})`);
        } catch (e) {
          // Re-throw terminal errors immediately
          if (e.message.startsWith('Upload failed') || attempt === MAX_ATTEMPTS - 1) throw e;
          // Network error or CORS-blocked error response — retry
        }
      }
    }

    // Sliding window — always keep UPLOAD_CONCURRENCY requests in-flight.
    // Avoids the batch barrier where the slowest chunk of a batch of N
    // blocks all subsequent chunks from starting.
    let nextChunk = 0;
    const inFlight = new Map(); // index -> promise

    function launchNext() {
      if (nextChunk >= totalChunks) return;
      const i = nextChunk++;
      const p = uploadChunk(i).then(() => inFlight.delete(i), err => { inFlight.delete(i); throw err; });
      inFlight.set(i, p);
    }

    // Fill the window
    while (nextChunk < totalChunks && inFlight.size < UPLOAD_CONCURRENCY) launchNext();

    while (inFlight.size > 0) {
      await Promise.race(inFlight.values());
      while (nextChunk < totalChunks && inFlight.size < UPLOAD_CONCURRENCY) launchNext();
    }

    return key;
  }

  // Upload file via XHR (for progress tracking); returns {callId} from 202 response
  function xhrUpload(url, file, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', url);
      xhr.setRequestHeader('Content-Type', 'application/octet-stream');
      xhr.responseType = 'text';

      xhr.upload.onprogress = e => {
        if (e.lengthComputable) onProgress(e.loaded / e.total);
      };
      xhr.upload.onload = () => onProgress(1);

      xhr.onload = () => {
        if (xhr.status === 202) {
          const json = JSON.parse(xhr.responseText);
          resolve({ callId: json.call_id });
        } else {
          reject(new Error(`Server error (${xhr.status}): ${xhr.responseText.slice(0, 200)}`));
        }
      };
      xhr.onerror = () => reject(new Error('Network error — check your connection and try again.'));
      xhr.ontimeout = () => reject(new Error('Upload timed out.'));
      xhr.timeout = 10 * 60 * 1000; // 10 min for upload only
      xhr.send(file);
    });
  }

  // Poll a URL every 3s until it returns 200 (binary) or throws on 5xx.
  // Retries up to 3 times on transient network errors (e.g. ERR_CONNECTION_RESET).
  async function pollForBinary(pollUrl) {
    pollController = new AbortController();
    const signal = pollController.signal;
    let networkRetries = 0;
    const MAX_RETRIES = 3;
    while (true) {
      try {
        const resp = await fetch(pollUrl, { signal });
        networkRetries = 0;
        if (resp.status === 200) {
          const buffer = await resp.arrayBuffer();
          const cd = resp.headers.get('content-disposition') || '';
          return { buffer, cd };
        }
        if (resp.status === 202) {
          await new Promise((res, rej) => {
            const t = setTimeout(res, 3000);
            signal.addEventListener('abort', () => { clearTimeout(t); rej(new DOMException('aborted', 'AbortError')); });
          });
          continue;
        }
        const text = await resp.text();
        throw new Error(`Server error (${resp.status}): ${text.slice(0, 200)}`);
      } catch (e) {
        if (e.name === 'AbortError') throw e;
        if (++networkRetries <= MAX_RETRIES) {
          console.warn(`Poll network error (retry ${networkRetries}/${MAX_RETRIES}): ${e.message}`);
          await new Promise((res, rej) => {
            const t = setTimeout(res, 2000);
            signal.addEventListener('abort', () => { clearTimeout(t); rej(new DOMException('aborted', 'AbortError')); });
          });
          continue;
        }
        console.error('Poll failed after retries:', e.message);
        throw e;
      }
    }
  }

  async function pollForJSON(pollUrl, timeoutMs = 900_000, callId = null) {
    pollController = new AbortController();
    const signal = pollController.signal;
    const deadline = Date.now() + timeoutMs;

    // Single promise that can be settled by either the page poll loop or the SW
    let _resolve, _reject;
    const done = new Promise((res, rej) => { _resolve = res; _reject = rej; });

    if (callId) {
      swResolvers.set(callId, _resolve);
      // Record poll info so the visibilitychange handler can hand off to SW when hidden
      currentPollInfo = { callId, pollUrl, deadline };
    }

    // Page-side poll — calls _resolve/_reject directly, never leaves a dangling promise
    (async () => {
      let networkRetries = 0;
      const MAX_RETRIES = 3;
      while (Date.now() < deadline) {
        try {
          const resp = await fetch(pollUrl, { signal });
          if (resp.status === 200) { networkRetries = 0; _resolve(await resp.json()); return; }
          if (resp.status === 202) {
            networkRetries = 0;
            await new Promise((res, rej) => {
              const t = setTimeout(res, Math.min(3000, deadline - Date.now()));
              signal.addEventListener('abort', () => { clearTimeout(t); rej(new DOMException('aborted', 'AbortError')); });
            });
            continue;
          }
          const text = await resp.text();
          throw new Error(`Server error (${resp.status}): ${text.slice(0, 200)}`);
        } catch (e) {
          if (e.name === 'AbortError') { _reject(e); return; }
          if (++networkRetries <= MAX_RETRIES) {
            console.warn(`Poll network error (retry ${networkRetries}/${MAX_RETRIES}): ${e.message}`);
            await new Promise((res, rej) => {
              const t = setTimeout(res, Math.min(2000, deadline - Date.now()));
              signal.addEventListener('abort', () => { clearTimeout(t); rej(new DOMException('aborted', 'AbortError')); });
            });
            continue;
          }
          console.error('Poll failed after retries:', e.message);
          _reject(e); return;
        }
      }
      _reject(new Error('Timed out waiting for server result. The job may have failed — check Modal logs.'));
    })();

    try {
      const result = await done;
      if (callId) {
        swResolvers.delete(callId);
        currentPollInfo = null;
        navigator.serviceWorker?.controller?.postMessage({ type: 'POLL_CANCEL', callId });
      }
      return result;
    } catch (e) {
      if (callId) { swResolvers.delete(callId); currentPollInfo = null; }
      throw e;
    }
  }

  downloadBtn.addEventListener('click', triggerDownload);
  document.getElementById('burnDownloadBtn').addEventListener('click', triggerDownload);
  document.getElementById('startOverBtn').addEventListener('click', async () => {
    const confirmed = await showConfirmModal(
      'Start over?',
      'This will clear the current video and all edits. Your downloaded file is safe.',
      'Start over'
    );
    if (confirmed) location.reload();
  });

  // Find B-Roll Moments button
  const findBrollBtn = document.getElementById('findBrollBtn');
  findBrollBtn.addEventListener('click', () => {
    if (!document.getElementById('stockBroll').checked) return;
    triggerStockBroll();
  });

  function updateFindBrollBtn() {
    const on = document.getElementById('stockBroll').checked;
    findBrollBtn.disabled = !on;
    findBrollBtn.title    = on ? '' : 'Enable Stock B-Roll Finder in tool settings';
    findBrollBtn.style.opacity = on ? '1' : '0.45';
    findBrollBtn.style.cursor  = on ? 'pointer' : 'not-allowed';
  }
  document.getElementById('stockBroll').addEventListener('change', updateFindBrollBtn);
  updateFindBrollBtn();

  // Caption preview / burn font size slider
  const fontSizeSlider = document.getElementById('captionFontSizeSlider');

  fontSizeSlider.addEventListener('input', () => {
    captionFontSize = parseInt(fontSizeSlider.value, 10);
    localStorage.setItem('captionFontSize', captionFontSize);
    updatePreviewCaption();
  });

  (function initPreviewFontSize() {
    const saved = parseInt(localStorage.getItem('captionFontSize') || '48', 10);
    captionFontSize = saved;
    fontSizeSlider.value = saved;
  })();

  function triggerDownload() {
    if (!resultBlob) return;
    const url = URL.createObjectURL(resultBlob);
    const a = document.createElement('a');
    a.href = url; a.download = resultName; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  }

  // ── State helpers ──
  // ── Checklist helpers ──────────────────────────────────────────────────────
  function _stepActivate(name) {
    const item = checkItems[name];
    if (!item || item.classList.contains('done')) return;
    item.style.display = '';   // show row if it was hidden (e.g. burn starts hidden)
    item.className = 'check-item active';
    const timeEl = checkTimeEls[name];
    if (timeEl) timeEl.textContent = '0:00';
    const start = Date.now();
    if (stepTimers[name]) clearInterval(stepTimers[name].id);
    stepTimers[name] = {
      start,
      id: setInterval(() => {
        const s = Math.floor((Date.now() - start) / 1000);
        if (timeEl) timeEl.textContent = formatTime(s);
      }, 500)
    };
    document.getElementById('cancelBtn').style.display = 'block';
  }

  function _stepDone(name) {
    if (stepTimers[name]) { clearInterval(stepTimers[name].id); }
    const elapsed = stepTimers[name] ? Math.round((Date.now() - stepTimers[name].start) / 1000) : null;
    stepTimers[name] = null;
    const item = checkItems[name];
    if (!item) return;
    item.className = 'check-item done';
    const timeEl = checkTimeEls[name];
    if (timeEl && elapsed !== null) {
      timeEl.textContent = formatTime(elapsed);
      stepEndSecs[name] = elapsed;
    } else if (timeEl && stepEndSecs[name] != null) {
      timeEl.textContent = formatTime(stepEndSecs[name]);
    }
  }

  function _stepSkip(name) {
    const item = checkItems[name];
    if (item) item.style.display = 'none';
    if (stepTimers[name]) { clearInterval(stepTimers[name].id); stepTimers[name] = null; }
  }

  const HIDDEN_BY_DEFAULT = new Set(['burn']); // rows hidden until triggered

  function _resetChecklist() {
    Object.keys(checkItems).forEach(name => {
      if (stepTimers[name]) { clearInterval(stepTimers[name].id); stepTimers[name] = null; }
      const item = checkItems[name];
      if (item) {
        item.className = 'check-item pending';
        item.style.display = HIDDEN_BY_DEFAULT.has(name) ? 'none' : '';
      }
      const t = checkTimeEls[name];
      if (t) t.textContent = '';
    });
    stepEndSecs = {};
    document.getElementById('cancelBtn').style.display = 'none';
  }

  // ── State display ──────────────────────────────────────────────────────────
  function showUploadProgress() {
    isUploading = true;
    setSetupLocked(true);
    statusCard.classList.add('visible');
    checklistEl.style.display = 'block';
    statusDone.classList.remove('visible');
    statusError.classList.remove('visible');
    _resetChecklist();

    // Show burn step only when burn_captions is enabled
    if (document.getElementById('burnCaptions')?.checked) {
      checkItems.burn.style.display = '';
    }

    // Upload step: active + inline progress bar
    _stepActivate('upload');
    document.getElementById('uploadBarRow').style.display = 'flex';
    document.getElementById('uploadBarFill').style.width = '0%';
    document.getElementById('uploadBarPct').textContent = '0%';

    runBtn.disabled = true;
    runBtn.style.display = 'none';
    window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
  }

  let _procStartMs    = null;
  let _enhanceEndSec  = 9999;
  let _renderStartSec = 9999;

  function showProcessing(msgs) {
    isUploading = false;
    statusCard.classList.add('visible');
    clearInterval(uploadTimer);
    _stepActivate('enhance');
    const messages = msgs || [];
    _enhanceEndSec = 9999;
    _renderStartSec = 9999;
    for (const [t, label] of messages) {
      if (label.includes('Transcrib')) _enhanceEndSec  = t;
      if (label.includes('Rendering')) _renderStartSec = t;
    }
    clearInterval(elapsedTimer);
    _procStartMs = Date.now();
    const burnVisible = checkItems.burn && checkItems.burn.style.display !== 'none';
    let cutActivated = false, burnActivated = false;
    elapsedTimer = setInterval(() => {
      const secs = Math.floor((Date.now() - _procStartMs) / 1000);
      if (!cutActivated && secs >= _enhanceEndSec) {
        cutActivated = true;
        _stepDone('enhance');
        _stepActivate('cut');
      }
      if (burnVisible && cutActivated && !burnActivated && secs >= _renderStartSec) {
        burnActivated = true;
        _stepDone('cut');
        _stepActivate('burn');
      }
    }, 500);
  }

  // Force a step done with an explicit elapsed-seconds value (used when splitting estimated time).
  function _forceDone(name, secs) {
    if (stepTimers[name]) { clearInterval(stepTimers[name].id); stepTimers[name] = null; }
    const item = checkItems[name];
    if (!item) return;
    item.className = 'check-item done';
    const timeEl = checkTimeEls[name];
    if (timeEl) { timeEl.textContent = formatTime(secs); stepEndSecs[name] = secs; }
  }

  // Called when process_poll returns. Marks all remaining active/pending steps done.
  // Handles proportional splits when the process finishes before timer transitions fire.
  function _stepsDoneProcessing() {
    clearInterval(elapsedTimer);
    const burnVisible = checkItems.burn && checkItems.burn.style.display !== 'none';
    const total = Math.max(1, Math.round((Date.now() - _procStartMs) / 1000));

    if (checkItems.enhance.classList.contains('active')) {
      // All steps still pending — split proportionally based on observed GPU timings:
      // enhance ~20%, cut ~25%, burn ~55% (burn includes ffmpeg re-encode, takes longest)
      if (burnVisible) {
        _forceDone('enhance', Math.round(total * 0.20));
        _forceDone('cut',     Math.round(total * 0.25));
        _forceDone('burn',    Math.max(1, total - Math.round(total * 0.20) - Math.round(total * 0.25)));
      } else {
        _forceDone('enhance', Math.min(total - 1, Math.round(total * 0.40)));
        _forceDone('cut',     Math.max(1, total - Math.min(total - 1, Math.round(total * 0.40))));
      }
    } else if (checkItems.cut && checkItems.cut.classList.contains('active')) {
      // enhance done, cut (and possibly burn) still running
      _stepDone('enhance');
      if (burnVisible) {
        const rem = stepTimers.cut ? Math.max(1, Math.round((Date.now() - stepTimers.cut.start) / 1000)) : 1;
        _forceDone('cut',  Math.max(1, Math.round(rem * 0.35)));
        _forceDone('burn', Math.max(1, rem - Math.round(rem * 0.35)));
      } else {
        _stepDone('cut');
      }
    } else {
      // Timer transitions already fired — just close whatever is still open
      _stepDone('enhance');
      _stepDone('cut');
      if (burnVisible) _stepDone('burn');
    }
  }

  function showDone() {
    clearInterval(elapsedTimer);
    checklistEl.style.display = 'none';
    statusDone.classList.add('visible');
    if (!burnMode) runBtn.style.display = 'none';
    const doneTimeEl = document.getElementById('doneTime');
    if (doneTimeEl && runStartTime) {
      const totalSec = Math.round((Date.now() - runStartTime) / 1000);
      doneTimeEl.textContent = `Total processing time: ${formatTime(totalSec)}`;
      doneTimeEl.style.display = 'block';
    }
    triggerDownload();
  }

  function showError(msg) {
    isUploading = false;
    setSetupLocked(false);
    clearInterval(uploadTimer);
    clearInterval(elapsedTimer);
    Object.keys(stepTimers).forEach(k => { if (stepTimers[k]) { clearInterval(stepTimers[k].id); stepTimers[k] = null; } });
    checklistEl.style.display = 'none';
    statusError.classList.add('visible');
    errorMsg.textContent = msg.length > 200 ? msg.slice(0, 200) + '…' : msg;
    runBtn.disabled = false;
    runBtn.style.display = 'block';
  }

  function resetStatus() {
    isUploading = false;
    if (pollController) { pollController.abort(); pollController = null; }
    currentCallId = null;
    clearInterval(uploadTimer);
    clearInterval(elapsedTimer);
    Object.keys(stepTimers).forEach(k => { if (stepTimers[k]) { clearInterval(stepTimers[k].id); stepTimers[k] = null; } });
    runStartTime = null;
    const doneTimeEl = document.getElementById('doneTime');
    if (doneTimeEl) doneTimeEl.style.display = 'none';
    statusCard.classList.remove('visible');
    checklistEl.style.display = 'none';
    _resetChecklist();
    statusDone.classList.remove('visible');
    statusError.classList.remove('visible');
    document.getElementById('captionEditorCard').style.display = 'none';
    document.getElementById('captionsList').innerHTML = '';
    document.getElementById('brollCard').style.display = 'none';
    document.getElementById('brollList').innerHTML = '';
    document.getElementById('stockBrollCard').style.display = 'none';
    document.getElementById('stockBrollList').innerHTML = '';
    stockBrollSelections = {};
    document.getElementById('hookCard').style.display = 'none';
    document.getElementById('hookOptions').style.display = 'none';
    document.getElementById('hookControls').style.display = 'none';
    document.getElementById('hookError').style.display = 'none';
    document.getElementById('generateHookBtn').disabled = true;
    document.getElementById('hookStatus').style.display = 'none';
    selectedHookIdx = -1;
    hookGenAborted = false;
    hookThumbnail = null;
    pendingAnalyses       = 0;
    stockBrollAnalyzed    = false;
    lastAnalyzedSignature = '';
    burnMode = false;
    currentUploadKey = null;
    document.getElementById('reprocessBtn').style.display = 'none';
    setSetupLocked(false);
    document.getElementById('burnSuccessBanner').style.display = 'none';
    { const _sc = document.getElementById('scheduleCard'); if (_sc) _sc.style.display = 'none'; }
    document.getElementById('stockBrollRerunBanner').style.display = 'none';
    document.getElementById('stockCostLimitBanner').style.display  = 'none';
    runBtn.textContent = '▶  Run Pipeline';
    runBtn.style.display = 'block';
    const burnErrorEl = document.getElementById('burnError');
    if (burnErrorEl) burnErrorEl.style.display = 'none';
    selectedBrolls = [];
    captionsData = [];
    videoKey    = null;
    cutsData = []; restoredCuts = new Set(); appliedRestored = new Set();
    cutFilename = '';
    captionFont      = 'Heebo';
    captionMarginPct = 0.08;
    captionFontSize  = 48;
    const _vid = document.getElementById('cutVideo');
    if (_vid) { _vid.pause(); _vid.removeAttribute('src'); _vid.load(); }
    _playerSetupDone = false;
    _playerDispW = 0;
    const _cp = document.getElementById('captionPlayer');
    if (_cp) _cp.style.display = 'none';
    const fs = document.getElementById('fontSelect');
    if (fs) fs.value = 'Heebo';
  }

  // ── Network check ──
  const _conn = navigator.connection || navigator.mozConnection || navigator.webkitConnection;

  function checkNetwork() {
    if (!_conn) return;
    const type = _conn.type;
    const eff  = _conn.effectiveType;
    const isCellular = type === 'cellular';
    const isWired    = type === 'wifi' || type === 'ethernet';
    const isSlow     = !isCellular && !isWired && ['slow-2g', '2g', '3g'].includes(eff);
    if (isCellular || isSlow) {
      document.getElementById('noticeNetTitle').textContent =
        isCellular ? 'Mobile data detected' : 'Slow connection detected';
      document.getElementById('noticeNetBody').textContent =
        isCellular
          ? `Uploading a large video on cellular may use significant data and take longer than expected. Switch to WiFi if possible.`
          : `Your connection looks slow (${eff}). The upload may take a while — stay on this page.`;
      noticeNet.classList.add('visible');
    } else {
      noticeNet.classList.remove('visible');
    }
  }

  if (_conn) _conn.addEventListener('change', checkNetwork);

  // ── Notice helpers ──
  function clearNotices() {
    noticeBlock.classList.remove('visible');
    noticeWarn.classList.remove('visible');
    noticeNet.classList.remove('visible');
  }
  function showBlockNotice(title, body) {
    document.getElementById('noticeBlockTitle').textContent = title;
    document.getElementById('noticeBlockBody').textContent  = body;
    noticeBlock.classList.add('visible');
  }
  function showWarnNotice(title, body) {
    document.getElementById('noticeWarnTitle').textContent = title;
    document.getElementById('noticeWarnBody').textContent  = body;
    noticeWarn.classList.add('visible');
  }

  // ── Utils ──
  function formatSize(bytes) {
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
  }
  function formatTime(s) {
    return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
  }
  function formatDuration(s) {
    const m = Math.floor(s / 60);
    const sec = Math.round(s % 60);
    return m > 0 ? `${m}m ${sec}s` : `${sec}s`;
  }

  // ── Caption editor ──
  function fmtCapTime(secs) {
    const m = Math.floor(secs / 60);
    const s = String(Math.floor(secs % 60)).padStart(2, '0');
    return `${m}:${s}`;
  }

  // ── Caption line-wrapping — mirrors Python _rewrap_cap (char_w = fontSize * 0.60) ──
  function rewrapCaption(text, videoWidth, fontSize) {
    const words = text.replace(/\\N/g, ' ').split(' ').filter(w => w.length);
    if (!words.length) return text;
    const marginH = Math.max(25, Math.floor(videoWidth / 14));
    const avail   = videoWidth - 2 * marginH;
    const charW   = fontSize * 0.50;
    const lines   = [];
    let cur = [], curW = 0;
    for (const word of words) {
      const ww  = word.length * charW;
      const gap = cur.length ? charW : 0;
      if (cur.length && curW + gap + ww > avail) {
        lines.push(cur.join(' '));
        cur = [word]; curW = ww;
      } else {
        cur.push(word); curW += gap + ww;
      }
    }
    if (cur.length) lines.push(cur.join(' '));
    return lines.join('\n');
  }

  // ── Caption preview & positioning ──
  function updatePreviewCaption() {
    // Called by font/size/position sliders — refreshes player caption overlay immediately
    const capEl = document.getElementById('playerCap');
    const vid   = document.getElementById('cutVideo');
    if (!capEl) return;
    capEl.style.fontFamily = `'${captionFont}', sans-serif`;
    capEl.style.bottom     = (captionMarginPct * 100) + '%';
    if (vid && vid.videoWidth) {
      // _playerDispW = detected display width (handles browser-auto-rotated videos where
      // videoWidth reports stream width, not display width)
      const displayW = _playerDispW || vid.videoWidth;
      const scale = vid.clientWidth / displayW;
      capEl.style.fontSize = Math.max(7, captionFontSize * scale) + 'px';
      const t = vid.currentTime;
      const cap = captionsData && captionsData.find(c => t >= c.start && t <= c.end + 0.05);
      if (cap) capEl.textContent = rewrapCaption(cap.text, displayW, captionFontSize);
    }
  }

  function _safePlay(vid) {
    const p = vid.play();
    if (p) p.catch(e => { if (e.name !== 'AbortError') console.error(e); });
  }

  function setupCaptionPlayer() {
    if (_playerSetupDone) return;
    _playerSetupDone = true;

    const vid     = document.getElementById('cutVideo');
    const capEl   = document.getElementById('playerCap');
    const bigPlay = document.getElementById('playerBigPlay');
    const playBtn = document.getElementById('playerPlayBtn');
    const progFill = document.getElementById('playerProgFill');
    const progWrap = document.getElementById('playerProgWrap');
    const timeLbl  = document.getElementById('playerTimeLbl');
    if (!vid) return;

    // Set source — video streams from the already-processed cut file on Modal
    vid.src = `${API_BASE}/download/${videoKey}`;
    document.getElementById('captionPlayer').style.display = 'block';

    vid.addEventListener('loadedmetadata', () => {
      const wrap = document.getElementById('playerWrap');
      if (!wrap) { updatePreviewCaption(); return; }
      const maxW = Math.min(260, window.innerWidth * 0.72);
      wrap.style.width = maxW + 'px';
      wrap.style.aspectRatio = '9 / 16';
      _playerDispW = vid.videoWidth || 1080;
      updatePreviewCaption();
    }, { once: true });

    function fmtT(s) {
      if (!isFinite(s)) return '0:00';
      const m = Math.floor(s / 60), sec = Math.floor(s % 60);
      return `${m}:${sec.toString().padStart(2, '0')}`;
    }

    function togglePlay() {
      if (vid.paused || vid.ended) _safePlay(vid); else vid.pause();
    }

    const playerWrap = document.getElementById('playerWrap');
    if (playerWrap) playerWrap.addEventListener('click', togglePlay);
    if (playBtn) playBtn.addEventListener('click', togglePlay);
    vid.addEventListener('play',  () => { bigPlay.style.opacity = '0'; playBtn.textContent = '⏸'; });
    vid.addEventListener('pause', () => { bigPlay.style.opacity = '1'; playBtn.textContent = '▶'; });
    vid.addEventListener('ended', () => { bigPlay.style.opacity = '1'; playBtn.textContent = '▶'; });

    vid.addEventListener('timeupdate', () => {
      const t = vid.currentTime, dur = vid.duration || 0;
      // Progress bar + thumb
      if (dur > 0 && !scrubbing) {
        const pct = (t / dur * 100) + '%';
        progFill.style.width = pct;
        if (thumb) thumb.style.left = pct;
      }
      timeLbl.textContent = fmtT(t) + ' / ' + fmtT(dur);

      // Caption overlay — same font/position as the preview and the burned video
      const cap = captionsData.find(c => t >= c.start && t <= c.end + 0.05);
      capEl.textContent = cap ? rewrapCaption(cap.text, vid.videoWidth || 1080, captionFontSize) : '';
      if (cap) {
        capEl.style.fontFamily = `'${captionFont}', sans-serif`;
        capEl.style.bottom     = (captionMarginPct * 100) + '%';
        const scale = vid.videoWidth ? vid.clientWidth / vid.videoWidth : vid.clientHeight / (vid.videoHeight || 1920);
        capEl.style.fontSize   = Math.max(7, captionFontSize * scale) + 'px';
        if (vid.videoWidth) {
          const marginH = Math.max(25, Math.floor(vid.videoWidth / 14));
          capEl.style.maxWidth = ((vid.videoWidth - 2 * marginH) / vid.videoWidth * 100).toFixed(2) + '%';
        }
      }

      // Highlight active row — reads timestamps from DOM so it stays correct after edits
      document.querySelectorAll('.caption-row').forEach(row => {
        const rs = parseFloat(row.querySelector('.caption-start')?.value) || 0;
        const re = parseFloat(row.querySelector('.caption-end')?.value)   || 0;
        row.classList.toggle('caption-row-active', t >= rs && t <= re + 0.05);
      });
    });

    // Scrub: click or drag progress bar to seek
    const thumb = document.getElementById('playerProgThumb');
    function seekToX(clientX) {
      if (!isFinite(vid.duration)) return;
      const r = progWrap.getBoundingClientRect();
      const pct = Math.max(0, Math.min(1, (clientX - r.left) / r.width));
      vid.currentTime = pct * vid.duration;
      progFill.style.width = (pct * 100) + '%';
      if (thumb) thumb.style.left = (pct * 100) + '%';
    }
    let scrubbing = false;
    progWrap.addEventListener('mousedown', e => {
      scrubbing = true;
      seekToX(e.clientX);
    });
    document.addEventListener('mousemove', e => { if (scrubbing) seekToX(e.clientX); });
    document.addEventListener('mouseup',   () => { scrubbing = false; });
    progWrap.addEventListener('touchstart', e => { scrubbing = true; seekToX(e.touches[0].clientX); }, { passive: true });
    document.addEventListener('touchmove',  e => { if (scrubbing) seekToX(e.touches[0].clientX); }, { passive: true });
    document.addEventListener('touchend',   () => { scrubbing = false; });
  }

  function initPositionTrack() {
    const track = document.getElementById('posTrack');
    const thumb = document.getElementById('posThumb');
    if (!track || !thumb) return;

    function setFromPct(pct) {
      const MIN_PCT = 0.03; const MAX_PCT = 0.80;
      const clamped = Math.max(MIN_PCT, Math.min(MAX_PCT, pct));
      captionMarginPct = clamped;
      const ratio = 1 - (clamped - MIN_PCT) / (MAX_PCT - MIN_PCT);
      thumb.style.top = (ratio * 100) + '%';
      updatePreviewCaption();
    }

    function onMove(clientY) {
      const rect = track.getBoundingClientRect();
      const ratio = Math.max(0, Math.min(1, (clientY - rect.top) / rect.height));
      const MIN_PCT = 0.03; const MAX_PCT = 0.80;
      setFromPct(MIN_PCT + (1 - ratio) * (MAX_PCT - MIN_PCT));
    }

    thumb.addEventListener('mousedown', e => {
      e.preventDefault();
      const onMove2 = e2 => onMove(e2.clientY);
      const stop = () => { document.removeEventListener('mousemove', onMove2); document.removeEventListener('mouseup', stop); };
      document.addEventListener('mousemove', onMove2);
      document.addEventListener('mouseup', stop);
    });
    thumb.addEventListener('touchstart', e => {
      e.preventDefault();
      const onMove2 = e2 => { e2.preventDefault(); onMove(e2.touches[0].clientY); };
      const stop = () => { document.removeEventListener('touchmove', onMove2); document.removeEventListener('touchend', stop); };
      document.addEventListener('touchmove', onMove2, { passive: false });
      document.addEventListener('touchend', stop);
    }, { passive: false });
    track.addEventListener('click', e => onMove(e.clientY));

    setFromPct(captionMarginPct);
  }

  document.getElementById('fontSelect')?.addEventListener('change', e => {
    captionFont = e.target.value;
    updatePreviewCaption();
  });

  // ── Collapsible cards ──
  function toggleCard(bodyId, headerEl) {
    const body = document.getElementById(bodyId);
    const isCollapsed = headerEl.classList.contains('collapsed');
    headerEl.classList.toggle('collapsed');
    body.style.display = isCollapsed ? 'block' : 'none';
  }

  // ── B-roll ──
  let selectedBrolls = [];
  let stockBrollSelections = {};

  function openLightbox(url) {
    const lb  = document.getElementById('brollLightbox');
    const vid = document.getElementById('brollLightboxVideo');
    vid.src = url;
    vid.play().catch(() => {});
    lb.classList.add('open');
  }
  function closeLightbox() {
    const lb  = document.getElementById('brollLightbox');
    const vid = document.getElementById('brollLightboxVideo');
    lb.classList.remove('open');
    vid.pause();
    vid.src = '';
  }
  document.getElementById('brollLightbox').addEventListener('click', e => {
    if (e.target === e.currentTarget) closeLightbox();
  });

  function updateBurnBtn() {
    if (!burnMode) return;
    const n = selectedBrolls.length + Object.keys(stockBrollSelections).length;
    runBtn.textContent = n > 0
      ? `▶  Burn & Download  (+${n} B-roll${n > 1 ? 's' : ''})`
      : '▶  Burn & Download';
  }
  function validateCaptionTimes() {
    if (!burnMode) return;
    const rows = Array.from(document.querySelectorAll('#captionsList .caption-row'));
    const vid  = document.getElementById('cutVideo');
    const videoDur = (vid && isFinite(vid.duration) && vid.duration > 0) ? vid.duration : Infinity;
    let hasError = false;

    rows.forEach((row, i) => {
      const startInp  = row.querySelector('.caption-start');
      const endInp    = row.querySelector('.caption-end');
      const start     = parseFloat(startInp.value) || 0;
      const end       = parseFloat(endInp.value)   || 0;
      const prevEnd   = i > 0
        ? (parseFloat(rows[i - 1].querySelector('.caption-end').value)   || 0) : 0;
      const nextStart = i < rows.length - 1
        ? (parseFloat(rows[i + 1].querySelector('.caption-start').value) || 0) : Infinity;

      const startBad = start < 0 || start < prevEnd;
      const endBad   = end <= start || end > videoDur || end > nextStart;

      startInp.classList.toggle('time-invalid', startBad);
      endInp.classList.toggle('time-invalid',   endBad);
      startInp.title = startBad
        ? (start < 0 ? 'Start time cannot be negative'
                     : `Start overlaps previous caption (ends at ${prevEnd.toFixed(2)}s)`)
        : 'Click to seek player here';
      endInp.title = endBad
        ? (end <= start   ? 'End must be after start'
         : end > videoDur ? `End exceeds video duration (${videoDur.toFixed(2)}s)`
                          : `End overlaps next caption (starts at ${nextStart.toFixed(2)}s)`)
        : 'Click to seek player here';

      if (startBad || endBad) hasError = true;
    });

    if (burnMode) runBtn.disabled = hasError || pendingAnalyses > 0;
  }

  document.getElementById('suggestBrolls').addEventListener('change', e => {
    if (!VEO_ENABLED) return;
    const show = e.target.checked ? 'block' : 'none';
    document.getElementById('geminiKeyRow').style.display = show;
    document.getElementById('anthropicKeyRow').style.display = show;
    document.getElementById('brollAspectRow').style.display = show;
    if (e.target.checked) {
      const savedGemini = localStorage.getItem('geminiApiKey') || '';
      if (savedGemini) document.getElementById('geminiKey').value = savedGemini;
      const savedAnthropic = localStorage.getItem('anthropicApiKey') || '';
      if (savedAnthropic) document.getElementById('anthropicKey').value = savedAnthropic;
    }
  });
  document.getElementById('geminiKey').addEventListener('change', e => {
    localStorage.setItem('geminiApiKey', e.target.value.trim());
  });
  document.getElementById('anthropicKey').addEventListener('change', e => {
    localStorage.setItem('anthropicApiKey', e.target.value.trim());
  });

  // Stock B-roll toggle
  document.getElementById('stockBroll').addEventListener('change', e => {
    localStorage.setItem('stockBrollEnabled', e.target.checked);
  });

  // Init: check for a saved background job and show reconnect banner
  (function checkSavedJob() {
    const job = loadSavedJob();
    if (job) document.getElementById('reconnectBanner').style.display = 'flex';
  })();

  // Service Worker — takes over polling only when the tab goes to the background
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js')
      .then(() => console.info('[SW] registered'))
      .catch(e => console.warn('[SW] registration failed:', e.message));

    navigator.serviceWorker.addEventListener('message', evt => {
      const { type, callId, result } = evt.data || {};
      console.info(`[SW] message: ${type} callId=${callId}`);
      if (type === 'SW_DONE') {
        const resolve = swResolvers.get(callId);
        if (resolve) { swResolvers.delete(callId); currentPollInfo = null; resolve(result); }
      }
    });

    // Hand off to SW when page is minimized; reclaim when page comes back
    document.addEventListener('visibilitychange', () => {
      const sw = navigator.serviceWorker.controller;
      if (!sw || !currentPollInfo) return;
      if (document.hidden) {
        console.info('[SW] page hidden — handing poll to SW');
        sw.postMessage({ type: 'POLL_START', ...currentPollInfo });
      } else {
        console.info('[SW] page visible — reclaiming poll from SW');
        sw.postMessage({ type: 'POLL_CANCEL', callId: currentPollInfo.callId });
      }
    });
  }

  // Init: apply VEO_ENABLED and restore stock B-roll state from localStorage
  (function initToggles() {
    // Disable Veo toggle if not enabled
    if (!VEO_ENABLED) {
      const veoRow = document.getElementById('suggestBrollsRow');
      if (veoRow) {
        veoRow.classList.add('disabled-feature');
        veoRow.title = 'Veo video generation — maybe later!';
      }
      const veoChk = document.getElementById('suggestBrolls');
      if (veoChk) { veoChk.checked = false; veoChk.disabled = true; }
    }

    // Restore stock B-roll preference
    if (localStorage.getItem('stockBrollEnabled') === 'false') {
      document.getElementById('stockBroll').checked = false;
    }
  })();

  async function startBrollAnalysis() {
    if (!document.getElementById('suggestBrolls').checked) return;
    if (!videoKey) return;
    const geminiKey = document.getElementById('geminiKey').value.trim()
                   || localStorage.getItem('geminiApiKey') || '';
    if (!geminiKey) return;
    const anthropicKey = document.getElementById('anthropicKey').value.trim()
                      || localStorage.getItem('anthropicApiKey') || '';

    bumpPending(+1);
    const card   = document.getElementById('brollCard');
    const status = document.getElementById('brollStatus');
    const list   = document.getElementById('brollList');
    // Ensure broll card body is expanded when showing
    const brollHeader = document.querySelector('#brollCard .card-header');
    if (brollHeader) brollHeader.classList.remove('collapsed');
    const brollBody = document.getElementById('brollBody');
    if (brollBody) brollBody.style.display = 'block';
    card.style.display   = 'block';
    status.style.display = 'flex';
    list.innerHTML       = '';
    window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });

    const aspectRatio = document.querySelector('input[name="brollAspect"]:checked')?.value || '9:16';

    try {
      const resp = await fetch(`${API_BASE}/broll/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ video_key: videoKey, captions: captionsData, gemini_key: geminiKey, aspect_ratio: aspectRatio, anthropic_key: anthropicKey }),
      });
      if (!resp.ok) throw new Error(`Spawn failed: ${resp.status}`);
      const { call_id } = await resp.json();

      const result = await pollBroll(`${API_BASE}/broll_poll/${call_id}/`);
      status.style.display = 'none';
      selectedBrolls = [];
      updateBurnBtn();
      const suggestions = result.suggestions || [];
      if (!suggestions.length) {
        list.innerHTML = '<p style="color:var(--muted);font-size:0.85rem;padding:8px 0">No B-roll suggestions found.</p>';
        return;
      }
      renderBrollList(suggestions);
    } catch (e) {
      console.error('B-roll error:', e.message);
      status.style.display = 'none';
      const isRetryable = /503|UNAVAILABLE|high demand|timeout|timed out/i.test(e.message);
      const msg = isRetryable
        ? 'Analysis timed out or Gemini is overloaded — try again.'
        : `Analysis failed: ${e.message}`;
      list.innerHTML = `<p style="color:var(--red);font-size:0.85rem;padding:8px 0">${msg} <button onclick="startBrollAnalysis()" style="margin-left:8px;font-size:0.8rem;padding:3px 10px;border-radius:6px;border:1px solid var(--red);background:none;color:var(--red);cursor:pointer">Retry</button></p>`;
    } finally {
      bumpPending(-1);
    }
  }

  async function pollBroll(url) {
    let retries = 0;
    while (true) {
      try {
        const resp = await fetch(url);
        if (resp.status === 200) return await resp.json();
        if (resp.status === 202) { await new Promise(r => setTimeout(r, 5000)); continue; }
        const body = await resp.json().catch(() => ({}));
        throw new Error(body.error || `Server error ${resp.status}`);
      } catch (e) {
        if (e.message && !e.message.startsWith('Server error') && ++retries <= 3) {
          await new Promise(r => setTimeout(r, 2000));
          continue;
        }
        throw e;
      }
    }
  }

  // ── Stock B-roll ──

  function triggerStockBroll() {
    document.getElementById('stockBrollRerunBanner').style.display = 'none';
    startStockBrollAnalysis(getEditedCaptions());
  }

  // ── Hook Generator ──────────────────────────────────────────────────────

  function cancelGenerateHook() {
    hookGenAborted = true;
  }

  async function fetchHookThumbnail() {
    if (!videoKey) return;
    await document.fonts.ready;
    try {
      const resp = await fetch(`${API_BASE}/thumbnail/${videoKey}/`);
      if (!resp.ok) return;
      const blob = await resp.blob();
      const img  = new Image();
      img.onload = () => { hookThumbnail = img; drawHookPreview(); };
      img.src = URL.createObjectURL(blob);
    } catch (_) {}
  }

  function drawHookPreview() {
    const canvas = document.getElementById('hookPreviewCanvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const W = canvas.width;
    if (hookThumbnail) {
      const newH = Math.round(W * hookThumbnail.naturalHeight / hookThumbnail.naturalWidth);
      if (canvas.height !== newH) canvas.height = newH;
    }
    const H = canvas.height;

    ctx.clearRect(0, 0, W, H);
    if (hookThumbnail) {
      // Cover-fit: fill canvas, crop excess
      const scale = Math.max(W / hookThumbnail.naturalWidth, H / hookThumbnail.naturalHeight);
      const sw = hookThumbnail.naturalWidth * scale, sh = hookThumbnail.naturalHeight * scale;
      ctx.drawImage(hookThumbnail, (W - sw) / 2, (H - sh) / 2, sw, sh);
    } else {
      ctx.fillStyle = '#1a1a2e';
      ctx.fillRect(0, 0, W, H);
    }

    // Retrieve current settings
    const text = selectedHookIdx >= 0
      ? (document.getElementById(`hookText${selectedHookIdx}`)?.value?.trim() || '')
      : '';
    if (!text) return;

    const fontName   = document.getElementById('hookFont')?.value || 'Heebo';
    const fontColor  = document.getElementById('hookFontColor')?.value || '#FFFFFF';
    const bgColor    = document.getElementById('hookBgColor')?.value || '#000000';
    const bgOpacity  = parseInt(document.getElementById('hookBgOpacity')?.value || '60') / 100;
    const vPos       = parseInt(document.getElementById('hookPosition')?.value || '10') / 100;
    const sizePct    = parseInt(document.getElementById('hookFontSize')?.value || '100') / 100;

    const fontSize = Math.max(12, Math.round(Math.min(W, H) * 0.075 * sizePct));
    ctx.direction   = 'rtl';
    ctx.font        = `bold ${fontSize}px '${fontName}', 'Heebo', sans-serif`;
    ctx.textAlign   = 'center';
    ctx.textBaseline = 'middle';

    const edgePad = fontSize;
    const maxW    = W - 2 * edgePad;          // max text width
    const padH    = Math.round(fontSize * 0.55); // horizontal box padding
    const padV    = Math.round(fontSize * 0.35); // vertical box padding
    const lineH   = Math.round(fontSize * 1.10); // line height — matches ASS renderer (~1.0–1.1×)

    // Word-wrap text into lines that fit within maxW
    const words = text.split(' ').filter(Boolean);
    const lines = [];
    let cur = '';
    for (const w of words) {
      const test = cur ? cur + ' ' + w : w;
      if (ctx.measureText(test).width > maxW && cur) { lines.push(cur); cur = w; }
      else cur = test;
    }
    if (cur) lines.push(cur);

    const blockH  = lines.length * lineH;
    // Center of block, clamped so the box stays inside the canvas
    const rawCY   = edgePad + (H - 2 * edgePad) * vPos;
    const centerY = Math.max(blockH / 2 + padV + 4,
                    Math.min(H - blockH / 2 - padV - 4, rawCY));
    const boxW = maxW + 2 * padH;
    const boxH = blockH + 2 * padV;
    const bx   = W / 2 - boxW / 2;
    const by   = centerY - boxH / 2;

    // Background box (square — matches ASS BorderStyle=3 which has no rounded corners)
    ctx.save();
    ctx.globalAlpha = bgOpacity;
    ctx.fillStyle   = bgColor;
    ctx.fillRect(bx, by, boxW, boxH);
    ctx.restore();

    // Draw each wrapped line — re-assert all text props after restore
    ctx.direction    = 'rtl';
    ctx.font         = `bold ${fontSize}px '${fontName}', 'Heebo', sans-serif`;
    ctx.textAlign    = 'center';
    ctx.textBaseline = 'middle';

    const borderSize  = parseInt(document.getElementById('hookBorderSize')?.value || '0');
    const borderColor = document.getElementById('hookBorderColor')?.value || '#000000';

    if (borderSize > 0) {
      ctx.strokeStyle = borderColor;
      ctx.lineWidth   = borderSize * 2;
      ctx.lineJoin    = 'round';
      lines.forEach((line, i) => {
        ctx.strokeText(line, W / 2, by + padV + (i + 0.5) * lineH);
      });
    }

    ctx.fillStyle = fontColor;
    lines.forEach((line, i) => {
      ctx.fillText(line, W / 2, by + padV + (i + 0.5) * lineH);
    });
  }

  function _hookSettingListeners() {
    ['hookFont','hookFontColor','hookBgColor','hookBgOpacity','hookPosition','hookFontSize','hookBorderColor','hookBorderSize'].forEach(id => {
      const el = document.getElementById(id);
      if (!el) return;
      el.addEventListener('input',  drawHookPreview);
      el.addEventListener('change', drawHookPreview); // color pickers fire 'change' on Safari after picker closes
    });
  }

  // ── Hook design templates (localStorage) ──────────────────────────────────
  function _getHookTemplates() {
    try { return JSON.parse(localStorage.getItem('hookTemplates') || '[]'); } catch { return []; }
  }
  function _saveHookTemplates(list) {
    localStorage.setItem('hookTemplates', JSON.stringify(list));
  }
  function _currentHookDesign() {
    return {
      font:        document.getElementById('hookFont')?.value || 'Heebo',
      fontColor:   document.getElementById('hookFontColor')?.value || '#FFFFFF',
      bgColor:     document.getElementById('hookBgColor')?.value || '#000000',
      bgOpacity:   document.getElementById('hookBgOpacity')?.value || '60',
      fontSize:    document.getElementById('hookFontSize')?.value || '100',
      borderColor: document.getElementById('hookBorderColor')?.value || '#000000',
      borderSize:  document.getElementById('hookBorderSize')?.value || '0',
      position:    document.getElementById('hookPosition')?.value || '10',
    };
  }
  function _applyHookDesign(d) {
    const map = {
      hookFont: d.font, hookFontColor: d.fontColor, hookBgColor: d.bgColor,
      hookBgOpacity: d.bgOpacity, hookFontSize: d.fontSize,
      hookBorderColor: d.borderColor, hookBorderSize: d.borderSize,
      hookPosition: d.position,
    };
    for (const [id, val] of Object.entries(map)) {
      const el = document.getElementById(id);
      if (el) el.value = val;
    }
    const opacityValEl = document.getElementById('hookBgOpacityVal');
    const sizeValEl    = document.getElementById('hookFontSizeVal');
    const borderValEl  = document.getElementById('hookBorderSizeVal');
    if (opacityValEl) opacityValEl.textContent = d.bgOpacity + '%';
    if (sizeValEl)    sizeValEl.textContent    = d.fontSize + '%';
    if (borderValEl)  borderValEl.textContent  = d.borderSize + 'px';
    drawHookPreview();
  }
  function _renderTemplateList() {
    const list = _getHookTemplates();
    const el   = document.getElementById('hookTemplateList');
    if (!el) return;
    if (!list.length) {
      el.innerHTML = '<p style="font-size:0.82rem;color:var(--muted);text-align:center">No saved templates yet.</p>';
      return;
    }
    el.innerHTML = list.map((t, i) => `
      <div style="display:flex;align-items:center;gap:8px;padding:8px 10px;border:1.5px solid var(--purple-100);border-radius:10px;background:var(--purple-50)">
        <span style="flex:1;font-size:0.85rem;font-weight:600;color:var(--purple-800);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${t.name}</span>
        <button data-tidx="${i}" class="hook-tpl-apply"
          style="padding:4px 10px;border-radius:7px;border:none;background:var(--purple-600);color:#fff;font-size:0.78rem;font-weight:700;cursor:pointer;flex-shrink:0">
          Apply
        </button>
        <button data-tidx="${i}" class="hook-tpl-del"
          style="padding:4px 8px;border-radius:7px;border:1.5px solid #FECACA;background:#FEF2F2;color:var(--red);font-size:0.78rem;font-weight:700;cursor:pointer;flex-shrink:0">
          ✕
        </button>
      </div>`).join('');
    el.querySelectorAll('.hook-tpl-apply').forEach(btn => {
      btn.addEventListener('click', () => {
        const i = parseInt(btn.dataset.tidx);
        _applyHookDesign(_getHookTemplates()[i]);
        closeHookTemplateModal();
      });
    });
    el.querySelectorAll('.hook-tpl-del').forEach(btn => {
      btn.addEventListener('click', () => _deleteHookTemplate(parseInt(btn.dataset.tidx)));
    });
  }
  function _deleteHookTemplate(idx) {
    const list = _getHookTemplates();
    list.splice(idx, 1);
    _saveHookTemplates(list);
    _renderTemplateList();
  }
  function saveHookTemplate() {
    const nameEl = document.getElementById('hookTemplateName');
    const name   = nameEl?.value.trim();
    if (!name) { nameEl?.focus(); return; }
    const list = _getHookTemplates();
    list.unshift({ name, ..._currentHookDesign() });
    _saveHookTemplates(list);
    if (nameEl) nameEl.value = '';
    _renderTemplateList();
  }
  function openHookTemplateModal() {
    _renderTemplateList();
    const ov = document.getElementById('hookTemplateOverlay');
    if (ov) ov.style.display = 'flex';
    setTimeout(() => document.getElementById('hookTemplateName')?.focus(), 50);
  }
  function closeHookTemplateModal() {
    const ov = document.getElementById('hookTemplateOverlay');
    if (ov) ov.style.display = 'none';
  }

  async function triggerGenerateHook() {
    if (!videoKey || !captionsData.length) return;
    const captions = getEditedCaptions();

    const btn        = document.getElementById('generateHookBtn');
    const status     = document.getElementById('hookStatus');
    const optionsEl  = document.getElementById('hookOptions');
    const controlsEl = document.getElementById('hookControls');
    const errEl      = document.getElementById('hookError');

    hookGenAborted        = false;
    btn.disabled          = true;
    status.style.display  = 'flex';
    optionsEl.style.display   = 'none';
    controlsEl.style.display  = 'none';
    errEl.style.display   = 'none';
    selectedHookIdx       = -1;

    try {
      const resp = await fetch(`${API_BASE}/generate-hook/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ captions_json: JSON.stringify(captions), video_key: videoKey }),
      });
      if (!resp.ok) {
        const errBody = await resp.json().catch(() => ({}));
        throw new Error(errBody.error || `HTTP ${resp.status}`);
      }
      const { call_id } = await resp.json();

      let retries = 0;
      while (true) {
        if (hookGenAborted) break;
        try {
          const poll = await fetch(`${API_BASE}/generate-hook-poll/${call_id}/`);
          if (poll.status === 200) {
            const result = await poll.json();
            if (!hookGenAborted) renderHookOptions(result.hooks || []);
            break;
          }
          if (poll.status === 202) { await new Promise(r => setTimeout(r, 3000)); retries = 0; continue; }
          throw new Error(`Server error ${poll.status}`);
        } catch (e) {
          if (!e.message.startsWith('Server error') && ++retries <= 3) {
            await new Promise(r => setTimeout(r, 2000)); continue;
          }
          throw e;
        }
      }
    } catch (e) {
      if (!hookGenAborted) {
        errEl.textContent   = `Failed: ${e.message.slice(0, 120)}`;
        errEl.style.display = 'block';
      }
    } finally {
      btn.disabled         = false;
      status.style.display = 'none';
      hookGenAborted       = false;
    }
  }

  function renderHookOptions(hooks) {
    selectedHookIdx = -1;
    const optionsEl  = document.getElementById('hookOptions');
    const controlsEl = document.getElementById('hookControls');

    if (!hooks.length) {
      optionsEl.innerHTML = '<p style="color:var(--muted);font-size:0.82rem">No hooks generated — try again.</p>';
      optionsEl.style.display = 'block';
      return;
    }

    optionsEl.innerHTML = '';
    hooks.forEach((h, i) => {
      const card = document.createElement('div');
      card.id = `hookOption${i}`;
      card.style.cssText = 'border:1.5px solid var(--purple-200);border-radius:12px;padding:12px 14px;margin-bottom:8px;cursor:pointer;transition:background 0.15s,border-color 0.15s';

      // Editable textarea for hook text
      const ta = document.createElement('textarea');
      ta.id = `hookText${i}`;
      ta.value = h.text;
      ta.style.cssText = 'width:100%;border:none;border-bottom:1.5px dashed var(--purple-300);background:transparent;resize:none;overflow:hidden;font-family:inherit;font-size:1.05rem;font-weight:700;direction:rtl;text-align:right;color:var(--text);padding:0 0 2px;margin:0 0 6px;cursor:text;line-height:1.4;display:block;outline:none;transition:border-color 0.15s;';
      ta.rows = 1;
      ta.title = 'לחץ לעריכה';
      ta.addEventListener('focus', () => { ta.style.borderBottomColor = 'var(--purple-500)'; });
      ta.addEventListener('blur',  () => { ta.style.borderBottomColor = 'var(--purple-300)'; });
      ta.addEventListener('input', () => {
        ta.style.height = 'auto';
        ta.style.height = ta.scrollHeight + 'px';
        drawHookPreview();
      });

      const rationale = document.createElement('p');
      rationale.style.cssText = 'font-size:0.76rem;color:var(--muted);margin:0;pointer-events:none;';
      rationale.textContent = h.rationale || '';

      card.appendChild(ta);
      card.appendChild(rationale);

      card.onclick = () => {
        if (selectedHookIdx === i) return; // already selected — don't interrupt editing
        document.querySelectorAll('[id^="hookOption"]').forEach(el => {
          el.style.background  = '';
          el.style.borderColor = 'var(--purple-200)';
        });
        card.style.background  = 'var(--purple-50)';
        card.style.borderColor = 'var(--purple-400)';
        selectedHookIdx = i;
        drawHookPreview();
      };

      optionsEl.appendChild(card);

      // Auto-size textarea once it's in the DOM
      requestAnimationFrame(() => {
        ta.style.height = 'auto';
        ta.style.height = ta.scrollHeight + 'px';
      });
    });

    optionsEl.style.display  = 'block';
    const firstShow = controlsEl.style.display === 'none';
    controlsEl.style.display = 'block';
    if (firstShow) _hookSettingListeners();
    drawHookPreview();
  }

  // ── End Hook Generator ──────────────────────────────────────────────────

  async function startStockBrollAnalysis(captionsOverride) {
    if (!document.getElementById('stockBroll').checked) return;
    const captions = captionsOverride || captionsData;
    if (!captions.length) return;

    const card   = document.getElementById('stockBrollCard');
    const status = document.getElementById('stockBrollStatus');
    const list   = document.getElementById('stockBrollList');

    const cardHeader = card.querySelector('.card-header');
    if (cardHeader) cardHeader.classList.remove('collapsed');
    const cardBody = document.getElementById('stockBrollBody');
    if (cardBody) cardBody.style.display = 'block';
    card.style.display   = 'block';
    status.style.display = 'flex';
    list.innerHTML       = '';
    window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });

    // Lock the caption editor and button while searching
    findBrollBtn.disabled = true;
    findBrollBtn.textContent = '⏳ Searching…';
    document.querySelectorAll('#captionsList .caption-input, #captionsList .caption-time-input, #captionsList .cap-btn').forEach(el => { el.disabled = true; });

    bumpPending(+1);
    const stockElapsedEl = document.getElementById('stockBrollElapsed');
    stockElapsedEl.textContent = '0:00';
    let stockSecs = 0;
    const stockTimer = setInterval(() => { stockElapsedEl.textContent = formatTime(++stockSecs); }, 1000);

    try {
      const resp = await fetch(`${API_BASE}/stock-broll/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ captions_json: JSON.stringify(captions), video_key: videoKey || '' }),
      });
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        throw new Error(body.error || `Spawn failed: ${resp.status}`);
      }
      const { call_id } = await resp.json();

      let netRetries = 0;
      while (true) {
        try {
          const pollResp = await fetch(`${API_BASE}/stock-broll-poll/${call_id}/`);
          if (pollResp.status === 200) {
            const result = await pollResp.json();
            clearInterval(stockTimer);
            status.style.display = 'none';
            const moments = result.moments || [];
            stockBrollAnalyzed    = true;
            lastAnalyzedSignature = getCaptionsSignature();
            // Cost-limit banner
            const costBanner = document.getElementById('stockCostLimitBanner');
            if (result.cost_limit_hit) {
              costBanner.textContent = `Processed top ${result.moments_processed} of ${result.total_moments_identified} moments to stay within budget.`;
              costBanner.style.display = 'block';
            } else {
              costBanner.style.display = 'none';
            }
            if (!moments.length) {
              const fs = result.filter_stats || {};
              let emptyMsg;
              if (!fs.sonnet_moments_raw) {
                emptyMsg = 'No strong moments identified in this video.';
              } else {
                const dropParts = [];
                if (fs.buf_drops)     dropParts.push(`${fs.buf_drops} near video edges`);
                if (fs.spacing_drops) dropParts.push(`${fs.spacing_drops} too close together`);
                const detail = dropParts.length ? ` (${dropParts.join('; ')})` : '';
                const n = fs.sonnet_moments_raw;
                emptyMsg = `${n} moment${n === 1 ? '' : 's'} identified but didn't meet placement rules${detail}.`;
              }
              list.innerHTML = `<p style="color:var(--muted);font-size:0.85rem;padding:8px 0">${emptyMsg}</p>`;
              return;
            }
            renderStockMoments(moments, result.video_context || null);
            return;
          }
          if (pollResp.status === 202) {
            await new Promise(r => setTimeout(r, 5000));
            netRetries = 0;
            continue;
          }
          const body = await pollResp.json().catch(() => ({}));
          throw new Error(body.error || `Server error ${pollResp.status}`);
        } catch (e) {
          if (!e.message.startsWith('Server error') && ++netRetries <= 3) {
            await new Promise(r => setTimeout(r, 2000));
            continue;
          }
          throw e;
        }
      }
    } catch (e) {
      clearInterval(stockTimer);
      console.error('Stock B-roll error:', e.message);
      status.style.display = 'none';
      list.innerHTML = `<p style="color:var(--red);font-size:0.85rem;padding:8px 0">Failed: ${e.message.slice(0, 160)} <button onclick="triggerStockBroll()" style="margin-left:8px;font-size:0.8rem;padding:3px 10px;border-radius:6px;border:1px solid var(--red);background:none;color:var(--red);cursor:pointer">Retry</button></p>`;
    } finally {
      bumpPending(-1);
      // Restore caption editor and button
      document.querySelectorAll('#captionsList .caption-input, #captionsList .caption-time-input, #captionsList .cap-btn').forEach(el => { el.disabled = false; });
      _updateDeleteButtons();
      findBrollBtn.textContent = '🎬 Find B-Roll Moments';
      updateFindBrollBtn();
    }
  }

  function renderStockMoments(moments, videoCtx) {
    const list = document.getElementById('stockBrollList');
    list.innerHTML = '';

    // 2-pass summary row + global debug toggle
    const nEmphasis = moments.filter(m => m.moment_type !== 'coverage').length;
    const nCoverage = moments.filter(m => m.moment_type === 'coverage').length;
    if (moments.length > 0) {
      const summaryRow = document.createElement('div');
      summaryRow.style.cssText = 'display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:4px';

      const summary = document.createElement('div');
      summary.className = 'broll-summary';
      summary.style.margin = '0';
      const parts = [`${moments.length} moment${moments.length !== 1 ? 's' : ''} found`];
      if (nEmphasis > 0) parts.push(`${nEmphasis} emphasis`);
      if (nCoverage > 0) parts.push(`${nCoverage} rhythm/coverage`);
      summary.textContent = parts.join(' · ');

      const debugBtn = document.createElement('button');
      debugBtn.className = 'debug-toggle-btn';
      const debugOn = localStorage.getItem('brollDebugMode') === 'on';
      debugBtn.textContent = debugOn ? '⚙ Debug on' : '⚙ Debug';
      if (debugOn) debugBtn.classList.add('active');
      debugBtn.addEventListener('click', () => {
        const wrapper = list.querySelector('.broll-moments-wrapper');
        if (!wrapper) return;
        const isOn = wrapper.classList.toggle('debug-mode');
        localStorage.setItem('brollDebugMode', isOn ? 'on' : 'off');
        debugBtn.textContent = isOn ? '⚙ Debug on' : '⚙ Debug';
        debugBtn.classList.toggle('active', isOn);
      });

      summaryRow.appendChild(summary);
      summaryRow.appendChild(debugBtn);
      list.appendChild(summaryRow);

      // Video context debug panel — visible only when debug toggle is on
      if (videoCtx && Object.keys(videoCtx).length > 0) {
        const ctxDiv = document.createElement('div');
        ctxDiv.className = 'moment-debug';
        ctxDiv.style.cssText = 'margin-bottom:10px;padding:8px 10px;background:var(--purple-50,#f5f3ff);border-radius:6px;border:1px solid var(--purple-200,#ddd6fe)';
        const rows = [
          ['Genre', videoCtx.video_genre],
          ['Register', videoCtx.speaker_emotional_register],
          ['Setting', videoCtx.setting_description],
          ['Topic', videoCtx.topic_summary],
          ['B-roll style', videoCtx.broll_style_recommendation],
          ['Cultural notes', videoCtx.cultural_context_notes],
          ['Sensitive topics', (videoCtx.sensitive_topics || []).join('; ') || 'none'],
        ];
        ctxDiv.innerHTML = '<strong style="font-size:0.71rem;color:var(--purple-700,#6d28d9)">VIDEO CONTEXT</strong>' +
          rows.filter(([, v]) => v).map(([k, v]) =>
            `<div style="margin-top:3px"><span style="color:var(--muted);font-size:0.68rem">${k}:</span> <span style="font-size:0.71rem">${v}</span></div>`
          ).join('');
        list.appendChild(ctxDiv);
      }
    }

    const wrapper = document.createElement('div');
    wrapper.className = 'broll-moments-wrapper';
    if (moments.length > 6) {
      wrapper.style.cssText = 'max-height:520px;overflow-y:auto;padding-right:4px';
    }
    if (localStorage.getItem('brollDebugMode') === 'on') wrapper.classList.add('debug-mode');
    list.appendChild(wrapper);

    moments.forEach((m, momentIdx) => {
      const isCoverage = m.moment_type === 'coverage';
      const card = document.createElement('div');
      card.className = isCoverage ? 'moment-card coverage' : 'moment-card';

      const momentCtx = {
        momentIdx,
        start: m.start,
        end: m.end,
        broll_duration_seconds: m.broll_duration_seconds || 3.0,
        label: m.label || '',
        reasoning: m.reasoning || '',
        broad_search_prompt: m.broad_search_prompt || m.search_query || '',
        strict_eval_prompt: m.strict_eval_prompt || '',
      };

      // Header: time badge + label + dismiss X
      const header = document.createElement('div');
      header.className = 'moment-header';

      const badge = document.createElement('span');
      badge.className = 'moment-time-badge';
      badge.textContent = fmtCapTime(m.start) + ' – ' + fmtCapTime(m.end);

      const label = document.createElement('span');
      label.className = 'moment-label';
      label.textContent = m.label || '';

      // Dismiss button — for coverage: removes card entirely; for emphasis: skip with restore
      const dismissBtn = document.createElement('button');
      dismissBtn.className = 'moment-dismiss-btn';
      dismissBtn.textContent = '✕';
      dismissBtn.title = isCoverage ? 'Skip this rhythm moment' : 'Skip this moment';
      dismissBtn.addEventListener('click', () => {
        delete stockBrollSelections[momentIdx];
        if (isCoverage) {
          card.remove();
        } else {
          // Collapse card: hide all children except header, show restore button
          Array.from(card.children).forEach(el => { if (el !== header) el.style.display = 'none'; });
          card.classList.add('skipped-emphasis');
          dismissBtn.remove();
          const restoreBtn = document.createElement('button');
          restoreBtn.className = 'moment-restore-btn';
          restoreBtn.textContent = 'Restore';
          restoreBtn.addEventListener('click', () => {
            Array.from(card.children).forEach(el => { el.style.display = ''; });
            card.classList.remove('skipped-emphasis');
            restoreBtn.remove();
            header.appendChild(dismissBtn);
          });
          header.appendChild(restoreBtn);
        }
      });

      header.appendChild(badge);
      if (m.broll_duration_seconds) {
        const durBadge = document.createElement('span');
        durBadge.className = 'moment-dur-badge';
        durBadge.textContent = m.broll_duration_seconds.toFixed(1) + 's';
        header.appendChild(durBadge);
      }
      // Type badge: coverage gets "rhythm" label; other non-concrete types show their type
      if (isCoverage) {
        const rhythmBadge = document.createElement('span');
        rhythmBadge.className = 'moment-badge-rhythm';
        rhythmBadge.title = 'Coverage moment — added for visual rhythm, not an emphasis peak';
        rhythmBadge.textContent = 'rhythm';
        header.appendChild(rhythmBadge);
      } else if (m.moment_type && m.moment_type !== 'concrete') {
        const typeBadge = document.createElement('span');
        typeBadge.className = 'moment-confidence-badge';
        typeBadge.style.cssText = m.moment_type === 'emotional'
          ? 'background:#fce7f3;color:#9d174d;border-color:#f9a8d4'
          : 'background:#ede9fe;color:#6d28d9;border-color:#c4b5fd';
        typeBadge.textContent = m.moment_type;
        header.appendChild(typeBadge);
      }
      if (m.confidence) {
        const confBadge = document.createElement('span');
        confBadge.className = `moment-confidence-badge confidence-${m.confidence}`;
        confBadge.title = m.intensity_score != null ? `intensity ${m.intensity_score}/10` : '';
        confBadge.textContent = m.confidence;
        header.appendChild(confBadge);
      }
      header.appendChild(label);
      header.appendChild(dismissBtn);
      card.appendChild(header);

      // Hebrew excerpt — prefer the backend's transcript_excerpt (built from edited captions)
      const excerptText = m.transcript_excerpt || m.verbatim_quote || (() => {
        const edited = getEditedCaptions();
        return edited.filter(c => c.end >= m.start - 0.5 && c.start <= m.end + 0.5).map(c => c.text).join(' ');
      })();
      if (excerptText) {
        const excerpt = document.createElement('div');
        excerpt.className = 'moment-excerpt';
        excerpt.textContent = excerptText;
        card.appendChild(excerpt);
      }

      // Reasoning (Hebrew)
      if (m.reasoning) {
        const reasoning = document.createElement('div');
        reasoning.className = 'moment-reasoning';
        reasoning.textContent = m.reasoning;
        card.appendChild(reasoning);
      }

      // Clips row
      const clipsContainer = document.createElement('div');
      renderClips(clipsContainer, m.clips || [], m.broad_search_prompt || m.search_query, momentCtx);
      if (m.weak_match) {
        const wm = document.createElement('p');
        wm.className = 'no-clips-msg';
        wm.style.fontStyle = 'italic';
        wm.textContent = 'No stock clips matched this moment — try "Find different clips" with a broader query.';
        clipsContainer.appendChild(wm);
      }
      card.appendChild(clipsContainer);

      // Debug panel — shown only when global debug toggle is active
      const dbg = document.createElement('div');
      dbg.className = 'moment-debug';
      const body = document.createElement('div');
      body.className = 'moment-debug-body';
      const markersStr = (m.intensity_markers && m.intensity_markers.length)
        ? m.intensity_markers.join(', ')
        : null;
      const passLabel = isCoverage
        ? 'Pass 2 — coverage (rhythm)'
        : `Pass 1 — emphasis (${m.moment_type || 'concrete'})`;
      // Per-variant retrieval stats
      const variantStatsStr = (() => {
        const stats = m._variant_stats;
        if (!stats || !stats.length) return null;
        return stats.map(s => `'${s.variant}': ${s.count} clips`).join('\n');
      })();
      // Winner variant
      const winnerClip = m.clips && m.clips[0];
      const winnerVariant = winnerClip && winnerClip._source_variant
        ? `'${winnerClip._source_variant}' (score ${winnerClip.score ?? '?'})` : null;
      const rows = [
        ['Pass', passLabel],
        ['Type', m.moment_type ? `${m.moment_type} · intensity ${m.intensity_score ?? '?'}/10` : null],
        ['Intensity markers', markersStr],
        ['Key insight', m.key_insight],
        ['Visual anchor', m.visual_anchor],
        ['Search variants', m.search_variants && m.search_variants.length
          ? m.search_variants.map((v, i) => `${i+1}. ${v}`).join('\n') : null],
        ['Variant retrieval', variantStatsStr],
        ['Winning variant', winnerVariant],
        ['Scoring target (strict)', m.strict_eval_prompt],
        ['Duration reasoning', m.duration_reasoning],
      ];
      rows.forEach(([label, value]) => {
        if (!value) return;
        const row = document.createElement('div');
        row.className = 'moment-debug-row';
        row.innerHTML = `<strong>${label}:</strong>${value}`;
        body.appendChild(row);
      });
      if (body.childElementCount > 0) {
        dbg.appendChild(body);
        card.appendChild(dbg);
      }

      // Find different clips button
      let clipPage = 2;
      const findBtn = document.createElement('button');
      findBtn.className = 'find-clips-btn';
      findBtn.textContent = '🔄 Find different clips';
      findBtn.addEventListener('click', async () => {
        findBtn.disabled = true;
        findBtn.textContent = 'Scoring clips…';
        delete stockBrollSelections[momentIdx];
        await retryStockMomentClips(momentCtx.broad_search_prompt || m.search_query, clipsContainer, clipPage++, findBtn, momentCtx);
      });
      card.appendChild(findBtn);

      wrapper.appendChild(card);
    });
  }

  function renderClips(container, clips, searchQuery, momentCtx) {
    container.innerHTML = '';
    if (!clips || !clips.length) {
      const msg = document.createElement('p');
      msg.className = 'no-clips-msg';
      msg.textContent = 'No clips found for "' + (searchQuery || 'this moment') + '".';
      container.appendChild(msg);
      return;
    }

    const radioGroup = momentCtx ? `stock-broll-${momentCtx.momentIdx}` : `stock-broll-x`;
    const row = document.createElement('div');
    row.className = 'clips-row';

    clips.forEach(clip => {
      const clipCard = document.createElement('div');
      clipCard.className = 'clip-card';

      const thumbDiv = document.createElement('div');
      thumbDiv.className = 'clip-thumb';
      thumbDiv.addEventListener('click', () => {
        if (clip.page_url) window.open(clip.page_url, '_blank', 'noopener,noreferrer');
      });

      if (clip.thumbnail) {
        const img = document.createElement('img');
        img.src = clip.thumbnail;
        img.alt = clip.author || 'Stock clip';
        img.loading = 'lazy';
        thumbDiv.appendChild(img);
      }

      const playOverlay = document.createElement('div');
      playOverlay.className = 'clip-thumb-play';
      const playIcon = document.createElement('div');
      playIcon.className = 'clip-thumb-play-icon';
      playIcon.textContent = '▶';
      playOverlay.appendChild(playIcon);
      thumbDiv.appendChild(playOverlay);

      const srcBadge = document.createElement('span');
      srcBadge.className = 'clip-source-badge ' + (clip.source || 'pexels');
      srcBadge.textContent = clip.source === 'pixabay' ? 'Pixabay' : 'Pexels';
      thumbDiv.appendChild(srcBadge);

      if (clip.score !== undefined) {
        const level = clip.score >= 8 ? 'high' : clip.score >= 5 ? 'mid' : 'low';
        const scoreBadge = document.createElement('span');
        scoreBadge.className = 'clip-score-badge ' + level;
        scoreBadge.textContent = clip.score + '/10';
        const tooltipParts = [];
        if (clip.frames_observed)    tooltipParts.push('Frames: ' + clip.frames_observed);
        if (clip.score_reason)       tooltipParts.push('Reason: ' + clip.score_reason);
        if (clip.step1_disqualified) tooltipParts.push('⚠ STEP 1 disqualified');
        if (clip.frame_sample_failed) tooltipParts.push('(thumbnail fallback — frame sampling failed)');
        if (clip.title)              tooltipParts.push('Title: ' + clip.title);
        if (clip.tags && clip.tags.length) tooltipParts.push('Tags: ' + clip.tags.slice(0, 5).join(', '));
        if (tooltipParts.length) scoreBadge.title = tooltipParts.join('\n');
        thumbDiv.appendChild(scoreBadge);
      }

      const meta = document.createElement('div');
      meta.className = 'clip-meta';

      if (clip.title) {
        const titleEl = document.createElement('span');
        titleEl.className = 'clip-title';
        titleEl.textContent = clip.title;
        meta.appendChild(titleEl);
      }
      if (clip.tags && clip.tags.length) {
        const tagsEl = document.createElement('span');
        tagsEl.className = 'clip-tags';
        tagsEl.textContent = clip.tags.slice(0, 5).join(', ');
        meta.appendChild(tagsEl);
      }

      const authorLink = document.createElement('a');
      authorLink.className = 'clip-author';
      authorLink.href   = clip.author_url || clip.page_url || '#';
      authorLink.target = '_blank';
      authorLink.rel    = 'noopener noreferrer';
      authorLink.textContent = clip.author ? 'by ' + clip.author : clip.source;

      // Toggle button — "Use for video" (acts like radio but can be deselected)
      const selLabel = document.createElement('label');
      selLabel.className = 'clip-select-label';
      const radio = document.createElement('input');
      radio.type = 'checkbox';
      radio.dataset.group = radioGroup;
      radio.addEventListener('change', () => {
        if (radio.checked) {
          // Deselect all others in this group, then select this one
          row.querySelectorAll(`input[data-group="${radioGroup}"]`).forEach(cb => {
            if (cb !== radio) {
              cb.checked = false;
              cb.closest('.clip-card')?.classList.remove('selected');
              cb.closest('label')?.classList.remove('checked');
            }
          });
          clipCard.classList.add('selected');
          selLabel.classList.add('checked');
          if (momentCtx) {
            stockBrollSelections[momentCtx.momentIdx] = {
              start:        momentCtx.start,
              end:          momentCtx.end,
              download_url: clip.preview_url || '',
              source:       clip.source || 'pexels',
              page_url:     clip.page_url || '',
              clip_use_start_seconds: clip.clip_use_start_seconds != null ? clip.clip_use_start_seconds : 0,
              clip_use_end_seconds:   clip.clip_use_end_seconds   != null ? clip.clip_use_end_seconds   : null,
            };
          }
        } else {
          // Toggled off — remove selection
          clipCard.classList.remove('selected');
          selLabel.classList.remove('checked');
          if (momentCtx) delete stockBrollSelections[momentCtx.momentIdx];
        }
      });
      selLabel.appendChild(radio);
      selLabel.appendChild(document.createTextNode('Use for video'));

      // Small view link
      const viewLink = document.createElement('a');
      viewLink.className = 'clip-view-link';
      viewLink.href   = clip.page_url || '#';
      viewLink.target = '_blank';
      viewLink.rel    = 'noopener noreferrer';
      viewLink.textContent = 'View ↗';

      // Clip window hint (middle/padded strategy)
      if (clip.clip_window_strategy === 'middle' || clip.clip_window_strategy === 'padded') {
        const wn = document.createElement('div');
        wn.className = 'clip-window-note' + (clip.clip_window_strategy === 'padded' ? ' padded' : '');
        const inS  = (clip.clip_use_start_seconds != null ? clip.clip_use_start_seconds : 0).toFixed(1);
        const outS = (clip.clip_use_end_seconds   != null ? clip.clip_use_end_seconds   : 0).toFixed(1);
        wn.textContent = clip.clip_window_strategy === 'padded'
          ? `⚠ clip too short (${outS}s)`
          : `Use ${inS}s → ${outS}s`;
        meta.appendChild(wn);
      }

      meta.appendChild(authorLink);
      meta.appendChild(selLabel);
      meta.appendChild(viewLink);

      clipCard.appendChild(thumbDiv);
      clipCard.appendChild(meta);
      row.appendChild(clipCard);
    });

    container.appendChild(row);
  }

  async function retryStockMomentClips(searchQuery, container, page, btn, momentCtx) {
    try {
      const ctxPayload = momentCtx
        ? JSON.stringify({
            label: momentCtx.label,
            reasoning: momentCtx.reasoning,
            strict_eval_prompt: momentCtx.strict_eval_prompt || '',
            broll_duration_seconds: momentCtx.broll_duration_seconds || 3.0,
          })
        : '';
      const resp = await fetch(`${API_BASE}/stock-broll-clips/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ search_query: searchQuery, page: page || 2, moment_context: ctxPayload }),
      });
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        throw new Error(body.error || `Spawn failed: ${resp.status}`);
      }
      const { call_id } = await resp.json();

      while (true) {
        const poll = await fetch(`${API_BASE}/stock-broll-clips-poll/${call_id}/`);
        if (poll.status === 202) { await new Promise(r => setTimeout(r, 3000)); continue; }
        if (!poll.ok) throw new Error(`Poll error: ${poll.status}`);
        const result = await poll.json();
        renderClips(container, result.clips || [], searchQuery, momentCtx);
        break;
      }
    } catch (e) {
      console.error('Retry clips failed:', e.message);
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.textContent = '🔄 Find different clips';
      }
    }
  }

  async function retryBrollVideo(s, thumbBox, retryBtn) {
    const geminiKey = document.getElementById('geminiKey').value.trim()
                   || localStorage.getItem('geminiApiKey') || '';
    const aspectRatio = document.querySelector('input[name="brollAspect"]:checked')?.value || '9:16';

    retryBtn.disabled = true;
    retryBtn.textContent = '…';

    try {
      const resp = await fetch(`${API_BASE}/broll_image/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ description: s.description, aspect_ratio: aspectRatio, gemini_key: geminiKey }),
      });
      if (!resp.ok) throw new Error(`Spawn failed: ${resp.status}`);
      const { call_id } = await resp.json();

      while (true) {
        const poll = await fetch(`${API_BASE}/broll_image_poll/${call_id}/`);
        if (poll.status === 202) { await new Promise(r => setTimeout(r, 5000)); continue; }
        if (!poll.ok) throw new Error(`Poll error: ${poll.status}`);
        const result = await poll.json();
        if (result.video_key) {
          const videoUrl = `${API_BASE}/download/${result.video_key}/`;
          s.video_key = result.video_key;
          // Update in selectedBrolls if this card was checked
          const sel = selectedBrolls.find(b => b.start === s.start && b.end === s.end);
          if (sel) sel.video_key = result.video_key;
          // Replace thumb content with video
          thumbBox.innerHTML = '';
          thumbBox.classList.add('zoomable');
          const vid = document.createElement('video');
          vid.src = videoUrl;
          vid.autoplay = true;
          vid.loop = true;
          vid.muted = true;
          vid.playsInline = true;
          thumbBox.appendChild(vid);
          thumbBox.onclick = () => openLightbox(videoUrl);
        } else {
          throw new Error(result.video_error || 'Unknown error');
        }
        break;
      }
    } catch (e) {
      console.error('B-roll video retry failed:', e.message);
    } finally {
      retryBtn.disabled = false;
      retryBtn.textContent = '↻ new video';
    }
  }

  function renderBrollList(suggestions) {
    const list = document.getElementById('brollList');
    list.innerHTML = '';
    suggestions.forEach((s, i) => {
      const row = document.createElement('div');
      row.className = 'broll-row';

      // Checkbox — select this B-roll to include in the final video
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.className = 'broll-checkbox';
      checkbox.title = 'Include this B-roll in the final video';
      checkbox.addEventListener('change', () => {
        if (checkbox.checked) {
          selectedBrolls.push({ start: s.start, end: s.end, video_key: s.video_key });
        } else {
          selectedBrolls = selectedBrolls.filter(b => !(b.start === s.start && b.end === s.end));
        }
        updateBurnBtn();
      });

      // Thumbnail + retry button wrapped together
      const thumbWrap = document.createElement('div');
      thumbWrap.className = 'broll-thumb-wrap';

      const thumbBox = document.createElement('div');
      thumbBox.className = 'broll-thumb';
      if (s.video_key) {
        const videoUrl = `${API_BASE}/download/${s.video_key}/`;
        const vid = document.createElement('video');
        vid.src = videoUrl;
        vid.autoplay = true;
        vid.loop = true;
        vid.muted = true;
        vid.playsInline = true;
        thumbBox.appendChild(vid);
        thumbBox.classList.add('zoomable');
        thumbBox.addEventListener('click', () => openLightbox(videoUrl));
      } else {
        const errMsg = s.video_error || 'Video generation unavailable.';
        thumbBox.title = errMsg;
        thumbBox.innerHTML = '<svg class="broll-thumb-placeholder" viewBox="0 0 24 24" fill="none" stroke="var(--amber)" stroke-width="1.5"><path d="M12 9v4m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/></svg>';
      }

      const retryBtn = document.createElement('button');
      retryBtn.className = 'broll-retry';
      retryBtn.textContent = '↻ new video';
      retryBtn.title = 'Coming soon';
      retryBtn.disabled = true;

      thumbWrap.appendChild(thumbBox);
      thumbWrap.appendChild(retryBtn);

      const content = document.createElement('div');
      content.className = 'broll-content';

      const time = document.createElement('div');
      time.className = 'broll-time';
      time.textContent = fmtCapTime(s.start) + ' – ' + fmtCapTime(s.end);

      const label = document.createElement('div');
      label.className = 'broll-label';
      label.textContent = s.label;

      const desc = document.createElement('div');
      desc.className = 'broll-desc';
      desc.textContent = s.description;

      content.appendChild(time);
      content.appendChild(label);
      content.appendChild(desc);

      if (!s.video_key && s.video_error) {
        const vidErr = document.createElement('div');
        vidErr.style.cssText = 'font-size:0.72rem;color:var(--amber);margin-top:4px;';
        vidErr.textContent = `⚠ ${s.video_error}`;
        content.appendChild(vidErr);
      }

      const dismiss = document.createElement('button');
      dismiss.className = 'broll-dismiss';
      dismiss.textContent = '✕';
      dismiss.addEventListener('click', () => {
        if (checkbox.checked) {
          selectedBrolls = selectedBrolls.filter(b => !(b.start === s.start && b.end === s.end));
          updateBurnBtn();
        }
        row.remove();
      });

      row.appendChild(checkbox);
      row.appendChild(thumbWrap);
      row.appendChild(content);
      row.appendChild(dismiss);
      list.appendChild(row);
    });
  }

  function _selectCaption(row, seekSecs) {
    document.querySelectorAll('#captionsList .caption-row-selected')
      .forEach(r => r.classList.remove('caption-row-selected'));
    row.classList.add('caption-row-selected');
    const vid = document.getElementById('cutVideo');
    if (vid && vid.src && isFinite(vid.duration)) {
      vid.currentTime = seekSecs;
      vid.pause();
      // removed scrollIntoView — don't auto-scroll to preview when selecting a caption
    }
  }

  function _updateDeleteButtons() {
    const rows = document.querySelectorAll('#captionsList .caption-row');
    const onlyOne = rows.length === 1;
    rows.forEach(r => { r.querySelector('.cap-btn-del').disabled = onlyOne; });
  }

  function _createCaptionRow(cap) {
    const row = document.createElement('div');
    row.className = 'caption-row';

    // Time controls
    const timeWrap = document.createElement('div');
    timeWrap.className = 'caption-time caption-time-wrap';

    function makeTimeRow(labelText, cls, val) {
      const tr = document.createElement('div');
      tr.className = 'caption-time-row';
      const lbl = document.createElement('label');
      lbl.textContent = labelText;
      const inp = document.createElement('input');
      inp.type      = 'number';
      inp.className = 'caption-time-input ' + cls;
      inp.value     = val.toFixed(2);
      inp.step      = '0.1';
      inp.min       = '0';
      inp.title     = 'Click to seek player here';
      inp.dataset.seek = '1';
      inp.addEventListener('click', e => {
        e.stopPropagation();
        _selectCaption(row, parseFloat(inp.value) || 0);
      });
      inp.addEventListener('input', () => {
        captionsData = getCaptionsFromEditor();
        updatePreviewCaption();
        validateCaptionTimes();
      });
      tr.appendChild(lbl);
      tr.appendChild(inp);
      return { tr, inp };
    }
    const { tr: startRow, inp: startInp } = makeTimeRow('▶', 'caption-start', cap.start);
    const { tr: endRow,   inp: endInp   } = makeTimeRow('■', 'caption-end',   cap.end);
    timeWrap.appendChild(startRow);
    timeWrap.appendChild(endRow);

    // Text input
    const textInp = document.createElement('input');
    textInp.type      = 'text';
    textInp.className = 'caption-input';
    textInp.value     = cap.text;
    textInp.dir       = 'rtl';
    textInp.addEventListener('input', () => {
      captionsData = getCaptionsFromEditor();
      updatePreviewCaption();
      if (stockBrollAnalyzed && getCaptionsSignature() !== lastAnalyzedSignature) {
        document.getElementById('stockBrollRerunBanner').style.display = 'flex';
      }
    });
    textInp.addEventListener('click', e => {
      e.stopPropagation();
      _selectCaption(row, parseFloat(startInp.value) || 0);
    });

    // Action buttons
    const actions = document.createElement('div');
    actions.className = 'caption-actions';

    // Split button ✂
    const splitBtn = document.createElement('button');
    splitBtn.className  = 'cap-btn cap-btn-split';
    splitBtn.textContent = '✂';
    splitBtn.title      = 'Split at cursor';
    splitBtn.addEventListener('click', () => {
      const pos   = textInp.selectionStart ?? textInp.value.length;
      const left  = textInp.value.slice(0, pos).trim();
      const right = textInp.value.slice(pos).trim();
      const s = parseFloat(startInp.value) || 0;
      const e = parseFloat(endInp.value)   || 0;
      const mid = parseFloat(((s + e) / 2).toFixed(2));
      textInp.value = left;
      endInp.value  = mid.toFixed(2);
      const newRow = _createCaptionRow({ start: mid, end: e, text: right });
      row.insertAdjacentElement('afterend', newRow);
      _updateDeleteButtons();
      captionsData = getCaptionsFromEditor();
      updatePreviewCaption();
      validateCaptionTimes();
      newRow.querySelector('.caption-input').focus();
    });

    // Add button +
    const addBtn = document.createElement('button');
    addBtn.className  = 'cap-btn cap-btn-add';
    addBtn.textContent = '+';
    addBtn.title      = 'Add caption line below';
    addBtn.addEventListener('click', () => {
      const e = parseFloat(endInp.value) || 0;
      const nextStart = e;
      const nextRow = row.nextElementSibling;
      const nextEnd = nextRow
        ? (parseFloat(nextRow.querySelector('.caption-start')?.value) || e + 2)
        : e + 2;
      const newRow = _createCaptionRow({ start: nextStart, end: Math.min(nextEnd, nextStart + 2), text: '' });
      row.insertAdjacentElement('afterend', newRow);
      _updateDeleteButtons();
      validateCaptionTimes();
      newRow.querySelector('.caption-input').focus();
    });

    // Delete button −
    const delBtn = document.createElement('button');
    delBtn.className  = 'cap-btn cap-btn-del';
    delBtn.textContent = '−';
    delBtn.title      = 'Remove this line';
    delBtn.addEventListener('click', () => {
      row.remove();
      _updateDeleteButtons();
      captionsData = getCaptionsFromEditor();
      updatePreviewCaption();
      validateCaptionTimes();
    });

    actions.appendChild(splitBtn);
    actions.appendChild(addBtn);
    actions.appendChild(delBtn);

    row.appendChild(timeWrap);
    row.appendChild(textInp);
    row.appendChild(actions);

    // Row click → select + seek to start (buttons handle themselves via stopPropagation)
    row.addEventListener('click', e => {
      if (e.target.closest('.cap-btn')) return;
      _selectCaption(row, parseFloat(startInp.value) || 0);
    });

    return row;
  }

  function renderCutsCard() {
    const card = document.getElementById('cutsCard');
    if (!card) return;
    if (!cutsData.length || !currentUploadKey) { card.style.display = 'none'; return; }
    const secsRemoved = cutsData
      .filter(c => !appliedRestored.has(c.index))
      .reduce((a, c) => a + c.duration, 0);
    document.getElementById('cutsSummary').textContent =
      `· ${cutsData.length} pauses · ${secsRemoved.toFixed(1)}s removed`;
    const list = document.getElementById('cutsList');
    list.innerHTML = '';
    cutsData.forEach(cut => {
      const row = document.createElement('label');
      row.className = 'cut-row' + (restoredCuts.has(cut.index) ? ' restored' : '');
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.className = 'cut-check';
      cb.checked = restoredCuts.has(cut.index);
      cb.addEventListener('change', () => {
        if (cb.checked) restoredCuts.add(cut.index); else restoredCuts.delete(cut.index);
        renderCutsCard();
      });
      const desc = document.createElement('span');
      desc.className = 'cut-desc';
      desc.dir = 'rtl';
      desc.textContent = `\u2026${cut.before}\u2003\u2702\uFE0F ${cut.duration.toFixed(1)}s\u2003${cut.after}\u2026`;
      row.append(cb, desc);
      list.appendChild(row);
    });
    const applyBtn = document.getElementById('applyCutsBtn');
    const dirty = JSON.stringify([...restoredCuts].sort()) !== JSON.stringify([...appliedRestored].sort());
    applyBtn.style.display = dirty ? '' : 'none';
    card.style.display = 'block';
  }

  async function applyCutRestore() {
    const btn = document.getElementById('applyCutsBtn');
    const errBox = document.getElementById('cutsError');
    const restored = [...restoredCuts];
    const orig = btn.textContent;
    btn.disabled = true;
    btn.textContent = '\u23F3 Re-rendering\u2026';
    errBox.style.display = 'none';
    try {
      const resp = await fetch(`${API_BASE}/rerender/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ upload_key: currentUploadKey, restored }),
      });
      if (!resp.ok) throw new Error((await resp.json()).error || `HTTP ${resp.status}`);
      const { call_id } = await resp.json();
      const result = await pollForJSON(`${API_BASE}/rerender_poll/${call_id}/`, 600_000, call_id);
      captionsData    = result.captions || [];
      videoKey        = result.video_key;
      appliedRestored = new Set(restored);
      showCaptionEditor();
    } catch (e) {
      errBox.textContent = 'Re-render failed: ' + (e.message || e);
      errBox.style.display = 'block';
    } finally {
      btn.disabled = false;
      btn.textContent = orig;
      renderCutsCard();
    }
  }

  function showCaptionEditor() {
    renderCutsCard();
    // Ensure card body is expanded (may have been collapsed in a previous burn)
    const captionHeader = document.querySelector('#captionEditorCard .card-header');
    if (captionHeader) captionHeader.classList.remove('collapsed');
    const captionBody = document.getElementById('captionBody');
    if (captionBody) captionBody.style.display = 'block';

    const list = document.getElementById('captionsList');
    list.innerHTML = '';
    captionsData.forEach(cap => list.appendChild(_createCaptionRow(cap)));
    _updateDeleteButtons();
    document.getElementById('captionEditorCard').style.display = 'block';
    document.getElementById('hookCard').style.display = 'block';
    document.getElementById('generateHookBtn').disabled = false;
    fetchHookThumbnail();
    burnMode = true;
    document.getElementById('uploadCard').classList.add('setup-locked');
    document.getElementById('optionsCard').classList.remove('setup-locked');
    if (currentUploadKey) {
      document.getElementById('reprocessBtn').style.display = 'block';
    }
    runBtn.disabled = false;
    runBtn.style.display = 'block';
    updateBurnBtn();
    setupCaptionPlayer();
    setTimeout(() => { initPositionTrack(); updatePreviewCaption(); validateCaptionTimes(); }, 50);
    window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
  }

  async function doBurn() {
    if (!videoKey) return;
    if (captionsData.length === 0 && selectedBrolls.length === 0 && Object.keys(stockBrollSelections).length === 0) return;
    const confirmed = await showConfirmModal(
      'Ready to burn?',
      'Finished editing? Click Burn to generate and download your final video.',
      'Burn & Download'
    );
    if (!confirmed) return;
    document.getElementById('burnSuccessBanner').style.display = 'none';
    { const _sc = document.getElementById('scheduleCard'); if (_sc) _sc.style.display = 'none'; }

    const edited = getCaptionsFromEditor();

    const fname = selectedFile ? selectedFile.name : 'video.mp4';
    const burnUrl = new URL(API_BASE + '/burn/');
    burnUrl.searchParams.set('video_key',  videoKey);
    burnUrl.searchParams.set('filename',   fname);
    burnUrl.searchParams.set('font',       captionFont);
    burnUrl.searchParams.set('margin_v',   captionMarginPct.toFixed(4));
    burnUrl.searchParams.set('font_size',  Math.round(captionFontSize * 1.10));

    const burnErrorEl   = document.getElementById('burnError');
    const reprocessBtn  = document.getElementById('reprocessBtn');
    runBtn.disabled     = true;
    runBtn.textContent  = '⏳ Burning…';
    burnErrorEl.style.display    = 'none';
    if (reprocessBtn) reprocessBtn.disabled = true;
    let burnBtnTimer = null;

    // Lock all editor cards and collapse them while burn is in progress
    const editorIds = ['captionEditorCard', 'hookCard', 'brollCard', 'stockBrollCard'];
    editorIds.forEach(id => document.getElementById(id)?.classList.add('burning'));
    [
      { h: document.querySelector('#captionEditorCard .card-header'), b: document.getElementById('captionBody') },
      { h: document.querySelector('#hookCard .card-header'),          b: document.getElementById('hookBody') },
      { h: document.querySelector('#brollCard .card-header'),         b: document.getElementById('brollBody') },
      { h: document.querySelector('#stockBrollCard .card-header'),    b: document.getElementById('stockBrollBody') },
    ].forEach(({ h, b }) => {
      if (h && !h.classList.contains('collapsed')) { h.classList.add('collapsed'); if (b) b.style.display = 'none'; }
    });
    try {
      // POST captions + selected B-rolls (Veo + stock) → get call_id immediately
      const allBroll = [...selectedBrolls, ...Object.values(stockBrollSelections)];

      // Collect hook settings if an option was selected
      let hookPayload = null;
      if (selectedHookIdx >= 0) {
        const hookTextEl = document.getElementById(`hookText${selectedHookIdx}`);
        const hookText   = hookTextEl ? hookTextEl.value.trim() : '';
        if (hookText) {
          hookPayload = {
            text:              hookText,
            font:              document.getElementById('hookFont')?.value || 'Heebo',
            font_color:        document.getElementById('hookFontColor')?.value || '#FFFFFF',
            bg_color:          document.getElementById('hookBgColor')?.value || '#000000',
            bg_opacity:        parseInt(document.getElementById('hookBgOpacity')?.value || '60') / 100,
            font_size_pct:     parseInt(document.getElementById('hookFontSize')?.value || '100'),
            border_color:      document.getElementById('hookBorderColor')?.value || '#000000',
            border_size:       parseInt(document.getElementById('hookBorderSize')?.value || '0'),
            start_seconds:     parseFloat(document.getElementById('hookStartSec')?.value || '0'),
            duration_seconds:  parseFloat(document.getElementById('hookDurationSec')?.value || '3'),
            vertical_position: parseInt(document.getElementById('hookPosition')?.value || '10'),
          };
        }
      }

      pollController = new AbortController();
      const spawnResp = await fetch(burnUrl.toString(), {
        method: 'POST',
        body: JSON.stringify({ captions: edited, broll: allBroll, ...(hookPayload ? { hook: hookPayload } : {}) }),
        headers: { 'Content-Type': 'application/json' },
        signal: pollController.signal,
      });
      if (!spawnResp.ok) throw new Error(`Burn spawn failed: ${spawnResp.status}`);
      const { call_id } = await spawnResp.json();

      // Poll until burn is done → returns {output_key}
      currentCallId = call_id;
      const outFilename = fname.replace(/\.[^/.]+$/, '') + '_edited.mp4';
      saveJob('burn', call_id, { outputFilename: outFilename });
      const burnResult = await pollForJSON(`${API_BASE}/burn_poll/${call_id}/`, 600_000, call_id);
      clearSavedJob();

      // Video is ready on the server — reveal success + the schedule card NOW.
      // Scheduling uses the server-side video URL, so it never waits on (or
      // requires) the device download that follows.
      const successBanner = document.getElementById('burnSuccessBanner');
      const successTime   = document.getElementById('burnSuccessTime');
      if (runStartTime) {
        const totalSec = Math.round((Date.now() - runStartTime) / 1000);
        successTime.textContent = `Total time: ${formatTime(totalSec)}`;
      }
      successBanner.style.display = 'flex';
      window._schedCtx = { outputKey: burnResult.output_key, filename: outFilename, videoKey: videoKey };
      if (typeof revealScheduleCard === 'function') revealScheduleCard();
      window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });

      // Download to the device (optional — does NOT gate scheduling)
      runBtn.textContent = '⏳ Downloading…';
      try {
        const dlAbort  = new AbortController();
        const dlKillId = setTimeout(() => dlAbort.abort(), 10 * 60 * 1000);
        try {
          const dlResp = await fetch(
            `${API_BASE}/download/${burnResult.output_key}/?filename=${encodeURIComponent(outFilename)}`,
            { signal: dlAbort.signal }
          );
          if (!dlResp.ok) throw new Error(`Download failed (${dlResp.status})`);
          resultBlob = new Blob([await dlResp.arrayBuffer()], { type: 'video/mp4' });
          resultName = outFilename;
          triggerDownload();
        } finally {
          clearTimeout(dlKillId);
        }
      } catch (dlErr) {
        if (dlErr.name !== 'AbortError')
          console.warn('Device download failed (video is still scheduled-ready):', dlErr.message);
      }
      // Re-enable editors for another round of changes on the same video
      editorIds.forEach(id => document.getElementById(id).classList.remove('burning'));
      [
        { h: document.querySelector('#captionEditorCard .card-header'), b: document.getElementById('captionBody') },
        { h: document.querySelector('#hookCard .card-header'),          b: document.getElementById('hookBody') },
        { h: document.querySelector('#brollCard .card-header'),         b: document.getElementById('brollBody') },
        { h: document.querySelector('#stockBrollCard .card-header'),    b: document.getElementById('stockBrollBody') },
      ].forEach(({ h, b }) => {
        if (h) { h.classList.remove('collapsed'); if (b) b.style.display = 'block'; }
      });
    } catch (err) {
      clearInterval(elapsedTimer);
      clearSavedJob();
      if (err.name !== 'AbortError') {
        console.error('Burn error:', err.message);
        editorIds.forEach(id => document.getElementById(id).classList.remove('burning'));
        // Expand editors back so user can retry
        [{ h: captionHeader, b: captionBody }, { h: brollHeader, b: brollBody }, { h: sbHeader, b: sbBody }]
          .forEach(({ h, b }) => {
            if (h) { h.classList.remove('collapsed'); if (b) b.style.display = 'block'; }
          });
        burnErrorEl.textContent = err.message.length > 200 ? err.message.slice(0, 200) + '…' : err.message;
        burnErrorEl.style.display = 'block';
      }
    } finally {
      runBtn.disabled = false;
      if (reprocessBtn) reprocessBtn.disabled = false;
      updateBurnBtn();
    }
  }

/* ── Debug log panel ── */
(function () {
  const lines = [];
  const el = () => document.getElementById('debugLines');
  const lbl = () => document.getElementById('debugLabel');

  function stamp() {
    const d = new Date();
    return d.toTimeString().slice(0,8) + '.' + String(d.getMilliseconds()).padStart(3,'0');
  }

  function push(cls, args) {
    const text = args.map(a => {
      if (typeof a === 'string') return a;
      try { return JSON.stringify(a, null, 0); } catch { return String(a); }
    }).join(' ');
    const entry = `[${stamp()}] ${text}`;
    lines.push({ cls, entry });
    const row = document.createElement('div');
    row.className = 'dl ' + cls;
    row.textContent = entry;
    const container = el();
    if (container) {
      container.appendChild(row);
      container.scrollTop = container.scrollHeight;
    }
    const label = lbl();
    if (label && cls === 'err') label.textContent = '▸ log ⚠';
  }

  const _log   = console.log.bind(console);
  const _warn  = console.warn.bind(console);
  const _error = console.error.bind(console);
  const _info  = console.info.bind(console);

  console.log   = (...a) => { _log(...a);   push('',     a); };
  console.warn  = (...a) => { _warn(...a);  push('warn', a); };
  console.error = (...a) => { _error(...a); push('err',  a); };
  console.info  = (...a) => { _info(...a);  push('info', a); };

  window.addEventListener('error', e => {
    push('err', [`Uncaught: ${e.message} (${e.filename}:${e.lineno})`]);
  });
  window.addEventListener('unhandledrejection', e => {
    push('err', [`UnhandledRejection: ${e.reason}`]);
  });

  window.copyDebugLog = function () {
    const text = lines.map(l => l.entry).join('\n');
    navigator.clipboard.writeText(text).then(() => {
      const btn = document.getElementById('debugCopy');
      if (btn) { btn.textContent = 'copied!'; setTimeout(() => btn.textContent = 'copy', 1500); }
    });
  };

  // Log page load + UA for mobile context
  push('info', [`Page loaded — ${navigator.userAgent}`]);
})();

/* ── Statistics tab ── */
  let _statsLoaded = false;

  function switchTab(which) {
    const views = { pipeline: 'pipelineView', history: 'historyView', stats: 'statsView' };
    const tabs  = { pipeline: 'tabPipeline',  history: 'tabHistory',  stats: 'tabStats' };
    for (const k of Object.keys(views)) {
      document.getElementById(views[k]).style.display = (k === which) ? '' : 'none';
      document.getElementById(tabs[k]).classList.toggle('active', k === which);
      document.getElementById(tabs[k]).setAttribute('aria-selected', String(k === which));
    }
    if (which === 'stats' && !_statsLoaded) loadStats();
    if (which === 'history') loadHistory();
  }

  // ── History tab ──
  async function loadHistory() {
    const list    = document.getElementById('historyList');
    const empty   = document.getElementById('historyEmpty');
    const loading = document.getElementById('historyLoading');
    loading.style.display = '';
    empty.style.display   = 'none';
    list.innerHTML = '';
    try {
      const resp = await fetch(`${API_BASE}/jobs/`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const { jobs } = await resp.json();
      loading.style.display = 'none';
      if (!jobs || !jobs.length) { empty.style.display = ''; return; }
      jobs.forEach(job => list.appendChild(_historyCard(job)));
    } catch (e) {
      loading.style.display = 'none';
      empty.textContent = 'Could not load history \u2014 try again in a moment.';
      empty.style.display = '';
    }
  }

  function _fmtJobDate(ts) {
    if (!ts) return '';
    return new Date(ts * 1000).toLocaleString('en-GB',
      { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
  }

  function _fmtJobDuration(secs) {
    if (!secs) return '';
    const m = Math.floor(secs / 60), sSec = Math.round(secs % 60);
    return `${m}:${String(sSec).padStart(2, '0')}`;
  }

  function _historyCard(job) {
    const card = document.createElement('div');
    card.className = 'history-card';

    const thumb = document.createElement('img');
    thumb.className = 'history-thumb';
    thumb.loading = 'lazy';
    thumb.alt = '';
    thumb.src = `${API_BASE}/thumbnail/${job.key}/`;
    thumb.onerror = () => { thumb.style.visibility = 'hidden'; };

    const info = document.createElement('div');
    info.className = 'history-info';
    const name = document.createElement('div');
    name.className = 'history-name';
    name.textContent = job.name || 'video';
    const meta = document.createElement('div');
    meta.className = 'history-meta';
    const bits = [_fmtJobDate(job.ts)];
    if (job.duration) bits.push(_fmtJobDuration(job.duration));
    if (job.size) bits.push(`${(job.size / 1048576).toFixed(0)} MB`);
    meta.textContent = bits.filter(Boolean).join(' \u00B7 ');
    info.append(name, meta);

    const actions = document.createElement('div');
    actions.className = 'history-actions';
    const dl = document.createElement('button');
    dl.className = 'history-btn';
    dl.textContent = '\u2B07\uFE0F';
    dl.title = 'Download';
    dl.onclick = () => {
      const fname = (job.name || 'video').replace(/\.mp4$/i, '') + '_edited.mp4';
      window.location.href = `${API_BASE}/download/${job.key}/?filename=${encodeURIComponent(fname)}`;
    };
    const del = document.createElement('button');
    del.className = 'history-btn history-btn-danger';
    del.textContent = '\uD83D\uDDD1\uFE0F';
    del.title = 'Delete';
    del.onclick = async () => {
      const ok = await showConfirmModal('Delete this video?',
        `\u201C${job.name}\u201D will be removed from history and can no longer be downloaded.`, 'Delete');
      if (!ok) return;
      try {
        await fetch(`${API_BASE}/jobs/${job.key}/`, { method: 'DELETE' });
      } catch (_) {}
      loadHistory();
    };
    actions.append(dl, del);

    card.append(thumb, info, actions);
    return card;
  }

  const _fmt = n => (n == null ? '—' : Number(n).toLocaleString('en-US'));
  const _monthDay = yyyymmdd => {
    const M = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    return `${M[+yyyymmdd.slice(4,6)-1]} ${+yyyymmdd.slice(6,8)}`;
  };

  function _changeHtml(change) {
    if (change == null || change === 0) return '<span class="metric-change flat">±0</span>';
    const up = change > 0;
    return `<span class="metric-change ${up ? 'up' : 'down'}">${up ? '▲' : '▼'} ${Math.abs(change)}</span>`;
  }

  function _relTime(iso) {
    if (!iso) return '';
    const then = new Date(iso).getTime();
    if (isNaN(then)) return '';
    const mins = Math.round((Date.now() - then) / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins} min ago`;
    const hrs = Math.round(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    const days = Math.round(hrs / 24);
    return `${days}d ago`;
  }

  function renderStats(d) {
    document.getElementById('statsHeadline').textContent = d.headline;
    document.getElementById('statsPeriod').innerHTML =
      `<span>Last 90 days · ${d.period.label}</span>`;

    const rel = _relTime(d.period.generatedAt);
    document.getElementById('statsAsOf').textContent =
      `Stats from ${d.period.generatedAtLabel}${rel ? ' · ' + rel : ''}`;

    // Network cards
    document.getElementById('statsNetworks').innerHTML = d.networks.map(n => {
      const metrics = n.metrics.map(m => `
        <div class="metric">
          <div class="metric-label">${m.label}</div>
          <div class="metric-value">${_fmt(m.value)}${'change' in m ? _changeHtml(m.change) : ''}</div>
        </div>`).join('');
      return `
        <div class="card net-card ${n.status}">
          <div class="net-head">
            <div class="net-badge ${n.key}">${n.icon}</div>
            <div class="net-title">
              <div class="net-name">${n.name}</div>
              <div class="net-handle">${n.handle}</div>
            </div>
            <span class="status-pill ${n.status}">${n.statusLabel}</span>
          </div>
          <div class="net-metrics">${metrics}</div>
          <p class="net-verdict">${n.verdict}</p>
        </div>`;
    }).join('');

    // Best time
    document.getElementById('btHour').textContent = d.bestTime.peakHour;
    document.getElementById('btCity').textContent = d.bestTime.timezone || d.bestTime.city || '';
    document.getElementById('btLead').textContent = `Post on ${d.bestTime.network} around ${d.bestTime.window}`;
    document.getElementById('btNote').textContent = d.bestTime.note;
    document.getElementById('btWhy').innerHTML = d.bestTime.why ? `<b>Why:</b> ${d.bestTime.why}` : '';

    // Top posts
    document.getElementById('statsTopPosts').innerHTML = d.topPosts.map((p, i) => `
      <div class="post-row">
        <div class="post-rank">${i + 1}</div>
        <div class="post-meta">
          <div class="post-net">${p.network} ${p.type}</div>
          <div class="post-sub">${_monthDay(p.date)} · ${p.interactions} interactions</div>
        </div>
        <div class="post-stats">
          <div class="post-imp">${_fmt(p.impressions)}</div>
          <div class="post-eng">reach</div>
        </div>
      </div>`).join('');

    document.getElementById('statsFoot').textContent =
      `Source: Metricool · organic + paid. Numbers are a snapshot generated ${d.period.generatedAtLabel}, not live.`;
  }

  async function loadStats() {
    const loading = document.getElementById('statsLoading');
    const content = document.getElementById('statsContent');
    const errBox  = document.getElementById('statsError');
    try {
      const resp = await fetch('stats.json?v=' + Date.now(), { cache: 'no-store' });
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      const data = await resp.json();
      renderStats(data);
      _statsLoaded = true;
      loading.style.display = 'none';
      errBox.style.display = 'none';
      content.style.display = '';
    } catch (e) {
      if (!_statsLoaded) { loading.style.display = 'none'; errBox.style.display = ''; }
    }
  }

/* ── Schedule this video (Metricool handoff helper) ── */
  const SCHED_PLATFORM_LABELS = { instagram: 'Instagram', facebook: 'Facebook', tiktok: 'TikTok', youtube: 'YouTube' };

  function _hasTranscript() {
    return typeof captionsData !== 'undefined' && Array.isArray(captionsData) && captionsData.length > 0;
  }

  function revealScheduleCard() {
    const card = document.getElementById('scheduleCard');
    if (!card) return;
    card.style.display = 'block';
    const d = new Date();
    const iso = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    const dateEl = document.getElementById('schedDate');
    if (dateEl && !dateEl.value) { dateEl.value = iso; dateEl.min = iso; }
    const sb = document.getElementById('suggestCaptionBtn');
    if (sb) {
      sb.disabled = !_hasTranscript();
      sb.textContent = _hasTranscript() ? '✨ Suggest caption from video' : '✨ Suggest caption (turn captions on to enable)';
    }
    checkMetricoolStatus();
  }

  async function checkMetricoolStatus() {
    const connectEl = document.getElementById('schedConnect');
    const schedBtn = document.getElementById('scheduleBtn');
    try {
      const r = await fetch(`${API_BASE}/oauth/status`, { cache: 'no-store' });
      const { connected } = await r.json();
      connectEl.style.display = connected ? 'none' : 'block';
      schedBtn.style.display = connected ? 'block' : 'none';
    } catch {
      connectEl.style.display = 'block';
      schedBtn.style.display = 'none';
    }
  }

  function connectMetricool() {
    window.open(`${API_BASE}/oauth/start`, '_blank', 'noopener');
  }

  // Show YouTube-only required fields when YouTube is selected
  document.addEventListener('change', (e) => {
    if (e.target && e.target.classList && e.target.classList.contains('sched-platform')) {
      const ytOn = !!document.querySelector('.sched-platform[value="youtube"]:checked');
      const yt = document.getElementById('ytFields');
      if (yt) yt.style.display = ytOn ? 'block' : 'none';
    }
  });

  function _schedPlatforms() {
    return [...document.querySelectorAll('.sched-platform:checked')].map(c => c.value);
  }

  async function suggestCaption() {
    if (!_hasTranscript()) return;
    const btn = document.getElementById('suggestCaptionBtn');
    const ta = document.getElementById('schedCaption');
    const errEl = document.getElementById('schedError');
    const orig = btn.textContent;
    btn.disabled = true; btn.textContent = '✨ Generating…';
    errEl.style.display = 'none';
    try {
      const resp = await fetch(`${API_BASE}/generate-caption/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          captions_json: JSON.stringify(captionsData),
          video_key: (window._schedCtx && window._schedCtx.videoKey) || '',
          platforms: _schedPlatforms().map(p => SCHED_PLATFORM_LABELS[p]).join(', '),
        }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const { call_id } = await resp.json();
      while (true) {
        const poll = await fetch(`${API_BASE}/generate-caption-poll/${call_id}/`);
        if (poll.status === 200) {
          const result = await poll.json();
          if (result.caption) ta.value = result.caption; else throw new Error('empty caption');
          break;
        }
        if (poll.status === 202) { await new Promise(r => setTimeout(r, 3000)); continue; }
        throw new Error(`Server error ${poll.status}`);
      }
    } catch (e) {
      errEl.textContent = `Couldn't generate a caption (${String(e.message).slice(0, 80)}). You can write one manually.`;
      errEl.style.display = 'block';
    } finally {
      btn.disabled = false; btn.textContent = orig;
    }
  }

  async function scheduleNow() {
    const errEl = document.getElementById('schedError');
    const statusEl = document.getElementById('schedStatus');
    const btn = document.getElementById('scheduleBtn');
    errEl.style.display = 'none'; statusEl.style.display = 'none';

    const platforms = _schedPlatforms();
    const caption = document.getElementById('schedCaption').value.trim();
    const date = document.getElementById('schedDate').value;
    const time = document.getElementById('schedTime').value;
    const ctx = window._schedCtx || {};
    const ytOn = platforms.includes('youtube');
    const ytTitle = document.getElementById('ytTitle').value.trim();

    const problems = [];
    if (!platforms.length) problems.push('pick at least one platform');
    if (!caption) problems.push('add a caption');
    if (!date || !time) problems.push('set a publish date and time');
    if (ytOn && !ytTitle) problems.push('add a YouTube title');
    if (!ctx.outputKey) problems.push('burn a video first');
    if (problems.length) {
      errEl.textContent = 'Please ' + problems.join(', ') + '.';
      errEl.style.display = 'block';
      return;
    }

    const payload = {
      platforms,
      caption,
      videoUrl: `${API_BASE}/media/${ctx.outputKey}`,
      dateTime: `${date}T${time}:00`,
      timezone: 'Asia/Jerusalem',
      ytTitle,
      ytPrivacy: document.getElementById('ytPrivacy').value,
      ytKids: document.getElementById('ytKids').checked,
    };

    const orig = btn.textContent;
    btn.disabled = true; btn.textContent = '⏳ Scheduling…';
    statusEl.className = 'sched-status busy'; statusEl.textContent = 'Sending to Metricool…'; statusEl.style.display = 'block';
    try {
      const spawn = await fetch(`${API_BASE}/schedule/`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      });
      if (spawn.status === 400) { checkMetricoolStatus(); throw new Error('Metricool isn\'t connected. Tap Connect Metricool first.'); }
      if (!spawn.ok) throw new Error(`HTTP ${spawn.status}`);
      const { call_id } = await spawn.json();

      let result = null;
      while (true) {
        const poll = await fetch(`${API_BASE}/schedule-poll/${call_id}/`);
        if (poll.status === 200) { result = await poll.json(); break; }
        if (poll.status === 202) { await new Promise(r => setTimeout(r, 2500)); continue; }
        throw new Error(`Server error ${poll.status}`);
      }
      if (result.error) throw new Error(result.error);

      const plannerUrl = result.post && result.post.plannerUrl;
      statusEl.className = 'sched-status ok';
      statusEl.innerHTML = `✅ Scheduled on Metricool for ${date} ${time} (Israel time).` +
        (plannerUrl ? ` <a href="${plannerUrl}" target="_blank" rel="noopener">Open in Metricool ↗</a>` : ' Approve the final publish in Metricool.');
      btn.textContent = '✅ Scheduled';
      setTimeout(() => { btn.textContent = orig; btn.disabled = false; }, 4000);
    } catch (e) {
      statusEl.style.display = 'none';
      errEl.textContent = `Couldn't schedule: ${String(e.message).slice(0, 160)}`;
      errEl.style.display = 'block';
      btn.textContent = orig; btn.disabled = false;
    }
  }
