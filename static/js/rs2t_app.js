/* ════════════════════════════════════════════════════════════
   LiveTranslate — rs2t_app.js

   A single flowing transcript where every segment is railed with
   the colour of its detected source language, plus floating
   captions, saved-session browsing and a locally generated
   post-session summary.
════════════════════════════════════════════════════════════ */

document.addEventListener('DOMContentLoaded', () => {

  /* ── WebSocket URL ── */
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const WS_URL = `${proto}//${location.host}/ws/rs2t/`;

  /* ── LANGUAGES ── */
  const LANG_NAMES = {
    en:'English', hi:'Hindi', es:'Español', fr:'Français', de:'Deutsch',
    zh:'Chinese', ja:'Japanese', ar:'Arabic', ru:'Russian', pt:'Português',
    it:'Italian', ko:'Korean', gu:'Gujarati', mr:'Marathi', bn:'Bengali',
    ta:'Tamil', te:'Telugu', kn:'Kannada', ml:'Malayalam', pa:'Punjabi',
    ur:'Urdu', nl:'Dutch', pl:'Polish', sv:'Swedish', da:'Danish',
    fi:'Finnish', no:'Norwegian', cs:'Czech', el:'Greek', hu:'Hungarian',
    ro:'Romanian', uk:'Ukrainian', tr:'Turkish', id:'Indonesian', ms:'Malay',
    th:'Thai', vi:'Vietnamese', tl:'Filipino', he:'Hebrew', fa:'Persian',
    sw:'Swahili'
  };

  /* Voice locales for reading a translation aloud */
  const SPEECH_LOCALES = {
    en:'en-US', hi:'hi-IN', es:'es-ES', fr:'fr-FR', de:'de-DE', zh:'zh-CN',
    ja:'ja-JP', ar:'ar-SA', ru:'ru-RU', pt:'pt-PT', it:'it-IT', ko:'ko-KR',
    gu:'gu-IN', mr:'mr-IN', bn:'bn-IN', ta:'ta-IN', te:'te-IN', kn:'kn-IN',
    ml:'ml-IN', pa:'pa-IN', ur:'ur-PK', nl:'nl-NL', pl:'pl-PL', sv:'sv-SE',
    da:'da-DK', fi:'fi-FI', no:'nb-NO', cs:'cs-CZ', el:'el-GR', hu:'hu-HU',
    ro:'ro-RO', uk:'uk-UA', tr:'tr-TR', id:'id-ID', ms:'ms-MY', th:'th-TH',
    vi:'vi-VN', tl:'fil-PH', he:'he-IL', fa:'fa-IR', sw:'sw-KE'
  };

  const READ_MIN = 15, READ_MAX = 32, READ_DEFAULT = 19;
  const LS = { theme:'lt.theme', read:'lt.readSize' };

  /* Smallest chunk worth sending. One second at 32kbps is ~4KB of audio plus
     a WebM header, so roughly 5-6KB per chunk — anything near 10KB silently
     discards every chunk and the transcript never starts. Real filtering
     happens server-side (MIN_WEBM_BYTES + RMS silence detection); this only
     skips containers with no audio in them at all. */
  const MIN_CHUNK_BYTES = 2000;

  /* If capture runs this long with nothing transcribed, say so rather than
     sitting on "Capturing audio" forever. */
  const SILENT_CAPTURE_WARN_MS = 20000;

  /* ══════════════════════ STATE ══════════════════════ */
  const S = {
    socket: null,
    isCapturing: false,
    targetLang: 'en',
    reconnectAttempts: 0,
    mediaRecorder: null,
    systemStream: null,
    recordingChunks: [],
    /* ms of audio per chunk. Measured against local Whisper on one sentence:
         1s → "The shipment keeps This morning in the drug. Let's war."
         2s → "The shipment cleared customs this This morning when the driver…"
         3s → "The shipment cleared customs this morning and the driver…"  ✓
       Each chunk is transcribed independently (condition_on_previous_text is
       off, which is what stops Whisper looping on noise), so a 1s window cuts
       words in half and has no context to recover them. Longer chunks are also
       *cheaper* — the same audio cost 12.2s of CPU at 1s vs 3.9s at 3s, since
       per-chunk overhead dominates. 3s trades ~2s of extra lag for a usable
       transcript. */
    chunkInterval: 3000,
    detectedLangs: {},        // langName → count
    segments: [],             // [{original, translation, lang, time, el}]
    currentSeg: null,
    totalWords: 0,
    segmentCount: 0,
    sessionStart: null,
    sessionTimer: null,
    // outbound audio accounting — makes "capturing but nothing arrives" visible
    bytesSent: 0,
    chunksSent: 0,
    chunksDropped: 0,
    heardAnything: false,
    silentWatchdog: null,
    throughputTimer: null,
    // lag measurement
    lastSentAt: 0,
    pendingSentAt: 0,
    unitLagged: false,   // lag is recorded once per unit, at its first token
    lags: [],
    // audio metering
    audioCtx: null, analyser: null, levelRaf: null,
    // ui
    autoScroll: true,
    searchTerm: '',
    popupVisible: false,
    popupMinimised: false,
    pipWindow: null,       // the Document PiP window, when one is open
    isDragging: false, dragOffX: 0, dragOffY: 0,
    historyTimer: null,
  };

  /* ══════════════════════ ELEMENTS ══════════════════════ */
  const $ = id => document.getElementById(id);
  const E = {
    app:              $('ltApp'),
    captureBtn:       $('captureBtn'),
    micIcon:          $('micIcon'),
    statusText:       $('statusText'),
    levelMeter:       $('levelMeter'),
    targetLangSelect: $('targetLangSelect'),
    themeBtn:         $('themeBtn'),
    themeIcon:        $('themeIcon'),
    summaryBtn:       $('summaryBtn'),
    clearBtn:         $('clearBtn'),
    discardBtn:       $('discardBtn'),
    popupBtn:         $('popupBtn'),
    helpBtn:          $('helpBtn'),
    moreBtn:          $('moreBtn'),
    actionMenu:       $('actionMenu'),
    themeLabel:       $('themeLabel'),
    sessionTimer:     $('sessionTimer'),
    lagFill:          $('lagFill'),
    lagVal:           $('lagVal'),
    wordCount:        $('wordCount'),
    segmentCount:     $('segmentCount'),
    detectedLangs:    $('detectedLangs'),
    langsEmpty:       $('langsEmpty'),
    fontUpBtn:        $('fontUpBtn'),
    fontDownBtn:      $('fontDownBtn'),
    fontSizeLabel:    $('fontSizeLabel'),
    connPill:         $('connPill'),
    connText:         $('connText'),
    searchInput:      $('searchInput'),
    searchCount:      $('searchCount'),
    autoScrollBtn:    $('autoScrollBtn'),
    liveBar:          $('liveBar'),
    liveBarText:      $('liveBarText'),
    transcriptEl:     $('transcriptEl'),
    emptyState:       $('emptyState'),
    jumpBtn:          $('jumpBtn'),
    captionBar:       $('captionBar'),
    captionOriginal:  $('captionOriginal'),
    captionTranslated:$('captionTranslated'),
    // popup
    ltPopup:          $('ltPopup'),
    popupMinBtn:      $('popupMinBtn'),
    popupCloseBtn:    $('popupCloseBtn'),
    popupDragBar:     $('popupDragBar'),
    popupOrig:        $('popupOrig'),
    popupTrans:       $('popupTrans'),
    popupStatus:      $('popupStatus'),
    // summary
    summaryOverlay:   $('summaryOverlay'),
    summaryCloseBtn:  $('summaryCloseBtn'),
    summaryMeta:      $('summaryMeta'),
    summaryTranscript:$('summaryTranscript'),
    summaryPoints:    $('summaryPoints'),
    copyTranscriptBtn:$('copyTranscriptBtn'),
    exportBtn:        $('exportBtn'),
    exportSrtBtn:     $('exportSrtBtn'),
    // history
    historyBtn:       $('historyBtn'),
    historyOverlay:   $('historyOverlay'),
    historyCloseBtn:  $('historyCloseBtn'),
    historyBody:      $('historyBody'),
    historyList:      $('historyList'),
    historySearch:    $('historySearch'),
    historyCount:     $('historyCount'),
    // help
    helpOverlay:      $('helpOverlay'),
    // glossary
    glossaryBtn:      $('glossaryBtn'),
    glossaryOverlay:  $('glossaryOverlay'),
    glossaryCloseBtn: $('glossaryCloseBtn'),
    glossaryForm:     $('glossaryForm'),
    glossTerm:        $('glossTerm'),
    glossHint:        $('glossHint'),
    glossaryList:     $('glossaryList'),
    glossaryCount:    $('glossaryCount'),
    helpCloseBtn:     $('helpCloseBtn'),
  };

  const CSRF = document.querySelector('meta[name="csrf-token"]')?.content || '';

  /* ══════════════════════ HELPERS ══════════════════════ */
  function escHtml(str) {
    return String(str)
      .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
      .replace(/"/g,'&quot;');
  }

  function escRe(str) { return String(str).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }

  function formatTime(s) {
    const m = Math.floor(s/60), sec = Math.floor(s%60);
    return `${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`;
  }

  /* Stable language → rail colour. The same language always gets the
     same rail, in this session and every future one. */
  function langClass(name) {
    let h = 0;
    for (const ch of String(name || '')) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
    return `lt-lang-${h % 8}`;
  }

  function showToast(msg, tone='ok') {
    const t = document.createElement('div');
    t.className = 'lt-toast';
    const colour = tone === 'bad' ? 'var(--danger)' : tone === 'warn' ? 'var(--warn)' : 'var(--live)';
    t.style.borderLeft = `3px solid ${colour}`;
    t.textContent = msg;
    document.body.appendChild(t);
    requestAnimationFrame(() => t.classList.add('lt-toast--visible'));
    setTimeout(() => {
      t.classList.remove('lt-toast--visible');
      setTimeout(() => t.remove(), 400);
    }, 3600);
  }

  function setConn(text, state) {
    E.connText.textContent = text;
    E.connPill.classList.toggle('is-ok',  state === 'ok');
    E.connPill.classList.toggle('is-bad', state === 'bad');
  }

  /* ══════════════════════ THEME ══════════════════════ */
  function applyTheme(theme) {
    document.documentElement.setAttribute('data-lt-theme', theme);
    const dark = theme !== 'light';
    E.themeIcon.className = dark ? 'fas fa-moon' : 'fas fa-sun';
    const label = dark ? 'Light theme' : 'Dark theme';
    E.themeBtn.title = `Switch to ${label.toLowerCase()} (T)`;
    if (E.themeLabel) E.themeLabel.textContent = label;
    try { localStorage.setItem(LS.theme, theme); } catch(_) {}
    // The PiP window is a separate document — it needs the attribute too.
    if (S.pipWindow) S.pipWindow.document.documentElement.setAttribute('data-lt-theme', theme);
  }

  function toggleTheme() {
    const now = document.documentElement.getAttribute('data-lt-theme') === 'light' ? 'dark' : 'light';
    applyTheme(now);
  }

  /* ══════════════════════ READING SIZE ══════════════════════ */
  function applyReadSize(px) {
    const size = Math.min(READ_MAX, Math.max(READ_MIN, px));
    E.app.style.setProperty('--read-size', size + 'px');
    E.fontSizeLabel.textContent = size + 'px';
    try { localStorage.setItem(LS.read, String(size)); } catch(_) {}
    return size;
  }

  function bumpReadSize(delta) {
    const cur = parseInt(E.fontSizeLabel.textContent, 10) || READ_DEFAULT;
    applyReadSize(cur + delta);
  }

  /* ══════════════════════ WEBSOCKET ══════════════════════ */
  function connectWS() {
    try {
      S.socket = new WebSocket(WS_URL);
      S.socket.onopen = onWsOpen;
      S.socket.onmessage = onWsMessage;
      S.socket.onerror = () => {};   // onclose fires after onerror; no double logging
      S.socket.onclose = onWsClose;
    } catch(e) { scheduleReconnect(); }
  }

  function scheduleReconnect() {
    // Exponential backoff: 2s, 4s, 8s … capped at 30s. Never gives up.
    const delay = Math.min(1000 * Math.pow(2, S.reconnectAttempts), 30000);
    S.reconnectAttempts++;
    setConn(`Reconnecting in ${Math.round(delay/1000)}s (try ${S.reconnectAttempts})`, 'bad');
    setTimeout(connectWS, delay);
  }

  function onWsOpen() {
    S.reconnectAttempts = 0;
    setConn('Connected', 'ok');
    sendWS({ type: 'set_target_language', target_language: S.targetLang });
  }

  function onWsClose(evt) {
    // Code 1000 = normal close (navigated away, cleared session) — don't reconnect
    if (evt.code === 1000) { setConn('Session ended', null); return; }
    if (S.isCapturing) stopCapture();

    // 4401 = the server rejected us as unauthenticated. Reconnecting would
    // loop forever against a sign-in that only a page reload can renew.
    if (evt.code === 4401) {
      setConn('Signed out — reload to sign in again', 'bad');
      showToast('Your session expired. Reload the page to sign in again.', 'bad');
      return;
    }
    scheduleReconnect();
  }

  function onWsMessage(evt) {
    let d;
    try { d = JSON.parse(evt.data); } catch(e) { return; }
    switch (d.type) {
      case 'connection_status':  setConn('Connected', 'ok'); break;
      case 'original_text':      onOriginalText(d); break;
      case 'translation_stream': onTranslationStream(d); break;
      case 'history_data':       renderHistory(d.sessions || []); break;
      case 'session_cleared':    onSessionCleared(d); break;
      case 'language_updated':
        setConn(`Translating into ${LANG_NAMES[d.target_language] || d.target_language}`, 'ok');
        break;
      case 'audio_error':        setConn(d.message, 'bad'); break;
    }
  }

  function sendWS(data) {
    if (S.socket?.readyState === WebSocket.OPEN) S.socket.send(JSON.stringify(data));
  }

  function sendAudio(buf) {
    if (S.socket?.readyState !== WebSocket.OPEN) return;
    S.socket.send(buf);
    S.lastSentAt = performance.now();
  }

  /* Streaming audio out but nothing coming back is the failure mode worth
     naming: the pill shows what actually left the browser. */
  function reportThroughput() {
    if (!S.isCapturing) return;
    const kb = (S.bytesSent / 1024).toFixed(0);
    setConn(`Capturing — ${kb} KB sent, ${S.chunksSent} chunks`, 'ok');
  }

  /* Capture running but nothing transcribed is almost always one of three
     things: no audio in the shared source, a muted tab, or chunks that never
     left. Say which rather than leaving the pill on "Capturing audio". */
  function armSilentWatchdog() {
    clearTimeout(S.silentWatchdog);
    S.silentWatchdog = setTimeout(() => {
      if (!S.isCapturing || S.heardAnything) return;
      if (S.chunksSent === 0) {
        showToast('No audio is reaching the server — is the shared tab playing sound?', 'warn');
        setConn('Capturing, but no audio sent', 'bad');
      } else {
        showToast('Audio is being sent but nothing has been transcribed yet — the source may be silent.', 'warn');
        setConn(`Sent ${(S.bytesSent/1024).toFixed(0)} KB, nothing transcribed`, 'bad');
      }
    }, SILENT_CAPTURE_WARN_MS);
  }

  /* ══════════════════════ LAG GAUGE ══════════════════════ */
  function recordLag(seconds) {
    if (!(seconds > 0) || seconds > 60) return;
    S.lags.push(seconds);
    if (S.lags.length > 12) S.lags.shift();   // rolling window — old chunks say nothing about now
    const avg = S.lags.reduce((a,b) => a+b, 0) / S.lags.length;

    E.lagVal.textContent = avg.toFixed(1) + 's';
    E.lagFill.style.width = Math.min(100, (avg / 6) * 100) + '%';
    E.lagFill.classList.toggle('is-warn', avg >= 2 && avg < 3.5);
    E.lagFill.classList.toggle('is-slow', avg >= 3.5);
  }

  /* ══════════════════════ TRANSCRIPT ══════════════════════ */
  function hideEmpty() {
    if (E.emptyState?.isConnected) E.emptyState.remove();
  }

  function nearBottom() {
    const el = E.transcriptEl;
    return el.scrollHeight - el.scrollTop - el.clientHeight < 120;
  }

  function scrollBottom(force) {
    if (!force && !S.autoScroll) return;
    E.transcriptEl.scrollTop = E.transcriptEl.scrollHeight;
  }

  function setAutoScroll(on) {
    S.autoScroll = on;
    E.autoScrollBtn.classList.toggle('is-on', on);
    E.jumpBtn.classList.toggle('is-shown', !on);
    if (on) scrollBottom(true);
  }

  function onOriginalText(d) {
    hideEmpty();
    S.heardAnything = true;
    S.pendingSentAt = S.lastSentAt;
    S.unitLagged = false;

    if (S.currentSeg) {
      appendToCurrentSeg(d);
      const last = S.segments[S.segments.length - 1];
      if (last) last.original += ' ' + d.text;
    } else {
      createSegment(d);
      S.segments.push({
        original: d.text,
        translation: '',
        lang: d.language,
        time: new Date().toLocaleTimeString('en-GB', {hour:'2-digit', minute:'2-digit', second:'2-digit'}),
        el: S.currentSeg.el,
      });
    }

    updateLiveBar(`${d.language} — translating…`);
    E.captionOriginal.textContent = d.text;
    updatePopupOrig(d.text);

    S.totalWords += d.word_count || 0;
    S.segmentCount++;
    E.wordCount.textContent = S.totalWords;
    E.segmentCount.textContent = S.segmentCount;

    const ln = d.language;
    S.detectedLangs[ln] = (S.detectedLangs[ln] || 0) + 1;
    renderDetectedLangs();
    E.summaryBtn.disabled = false;
  }

  function onTranslationStream(d) {
    if (!S.currentSeg) return;
    const transEl = S.currentSeg.transEl;
    transEl.querySelector('.lt-cursor')?.remove();

    if (d.is_final) {
      if (d.chunk) transEl.insertAdjacentText('beforeend', d.chunk);
      // The server sends the cleaned text on the closing frame; trust it over
      // whatever the token stream painted.
      if (d.text) transEl.textContent = d.text;
      transEl.classList.remove('streaming');

      const full = transEl.textContent;
      updateCaptionTranslated(full);
      updatePopupTrans(full);
      updateLiveBar('Listening…');

      const last = S.segments[S.segments.length - 1];
      if (last) last.translation = full;

      scrollBottom();

      // The server marks the end of a complete thought. Closing the segment
      // here is what gives the transcript sentence structure — without it
      // every window appends to one ever-growing block.
      if (d.end_of_unit) commitCurrentSeg();
      return;
    }

    // Lag is measured to the first visible token: that is the moment the
    // reader can start reading, and it is what the gauge should promise.
    if (!S.unitLagged && S.pendingSentAt) {
      S.unitLagged = true;
      recordLag((performance.now() - S.pendingSentAt) / 1000);
    }

    transEl.classList.add('streaming');
    transEl.insertAdjacentText('beforeend', d.chunk);
    transEl.appendChild(makeCursor());
    updateCaptionTranslated(transEl.textContent + '…');
    scrollBottom();
  }

  function makeCursor() {
    const c = document.createElement('span');
    c.className = 'lt-cursor';
    return c;
  }

  function createSegment(d) {
    const seg = document.createElement('div');
    seg.className = `lt-seg ${langClass(d.language)}`;
    const timeStr = new Date().toLocaleTimeString('en-GB', {hour:'2-digit', minute:'2-digit', second:'2-digit'});

    seg.innerHTML = `
      <div class="lt-seg-meta">
        <span class="lt-seg-lang">${escHtml(d.language || '')}</span>
        <span class="lt-seg-time">${timeStr}</span>
        <span class="lt-seg-acts">
          <button class="lt-seg-act" data-act="copy" title="Copy this segment"><i class="fas fa-copy"></i></button>
          <button class="lt-seg-act" data-act="speak" title="Read the translation aloud"><i class="fas fa-volume-high"></i></button>
        </span>
      </div>
      <div class="lt-seg-original"></div>
      <div class="lt-seg-translation"></div>`;

    seg.querySelector('.lt-seg-original').textContent = d.text;
    seg.querySelector('.lt-seg-translation').appendChild(makeCursor());

    E.transcriptEl.appendChild(seg);
    scrollBottom();

    S.currentSeg = {
      el: seg,
      transEl: seg.querySelector('.lt-seg-translation'),
      origEl: seg.querySelector('.lt-seg-original'),
    };
  }

  /* Same utterance continuing after a short pause — extend the open segment */
  function appendToCurrentSeg(d) {
    S.currentSeg.origEl.textContent += ' ' + d.text;
    const transEl = S.currentSeg.transEl;
    transEl.querySelector('.lt-cursor')?.remove();
    if (transEl.textContent.trim()) transEl.appendChild(document.createTextNode(' '));
    transEl.classList.add('streaming');
    transEl.appendChild(makeCursor());
    scrollBottom();
  }

  function commitCurrentSeg() {
    if (!S.currentSeg) return;
    S.currentSeg.transEl.querySelector('.lt-cursor')?.remove();
    S.currentSeg.transEl.classList.remove('streaming');
    S.currentSeg = null;
  }

  /* ══════════════════════ SEARCH ══════════════════════ */
  function runSearch(term) {
    S.searchTerm = term.trim();
    const segs = [...E.transcriptEl.querySelectorAll('.lt-seg')];

    if (!S.searchTerm) {
      segs.forEach(el => {
        el.classList.remove('is-hit', 'is-dim');
        restorePlain(el);
      });
      E.searchCount.textContent = '';
      return;
    }

    const re = new RegExp(escRe(S.searchTerm), 'gi');
    let hits = 0;

    segs.forEach(el => {
      const orig  = el.querySelector('.lt-seg-original');
      const trans = el.querySelector('.lt-seg-translation');
      const text  = `${orig.textContent} ${trans.textContent}`;
      const match = re.test(text);
      re.lastIndex = 0;

      el.classList.toggle('is-hit', match);
      el.classList.toggle('is-dim', !match);
      if (match) hits++;

      // Don't rewrite the segment still receiving tokens — highlighting it
      // would fight the streaming cursor.
      if (el === S.currentSeg?.el) return;
      highlight(orig, re);
      highlight(trans, re);
    });

    E.searchCount.textContent = hits ? `${hits} of ${segs.length}` : 'no matches';
  }

  function highlight(el, re) {
    const raw = el.dataset.raw ?? el.textContent;
    el.dataset.raw = raw;
    re.lastIndex = 0;
    el.innerHTML = escHtml(raw).replace(re, m => `<mark class="lt-hl">${m}</mark>`);
  }

  function restorePlain(segEl) {
    segEl.querySelectorAll('[data-raw]').forEach(el => {
      el.textContent = el.dataset.raw;
      delete el.dataset.raw;
    });
  }

  /* ══════════════════════ SEGMENT ACTIONS ══════════════════════ */
  E.transcriptEl.addEventListener('click', e => {
    const btn = e.target.closest('.lt-seg-act');
    if (!btn) return;
    const seg = btn.closest('.lt-seg');
    const orig  = seg.querySelector('.lt-seg-original');
    const trans = seg.querySelector('.lt-seg-translation');
    const origText  = orig.dataset.raw  ?? orig.textContent;
    const transText = trans.dataset.raw ?? trans.textContent;

    if (btn.dataset.act === 'copy') {
      navigator.clipboard.writeText(`${origText}\n→ ${transText}`)
        .then(() => showToast('Segment copied'))
        .catch(() => showToast('Copy failed — check clipboard permissions', 'bad'));
    } else if (btn.dataset.act === 'speak') {
      speak(transText);
    }
  });

  function speak(text) {
    if (!('speechSynthesis' in window)) {
      showToast('This browser cannot read text aloud', 'warn');
      return;
    }
    if (!text.trim()) return;
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.lang = SPEECH_LOCALES[S.targetLang] || 'en-US';
    speechSynthesis.speak(u);
  }

  /* ══════════════════════ DETECTED LANGUAGES ══════════════════════ */
  function renderDetectedLangs() {
    const entries = Object.entries(S.detectedLangs).sort((a,b) => b[1]-a[1]);
    E.langsEmpty.style.display = entries.length ? 'none' : '';
    E.detectedLangs.innerHTML = entries
      .map(([l,c]) => `<span class="lt-lang-pill ${langClass(l)}">${escHtml(l)} <small>${c}</small></span>`)
      .join('');
  }

  /* ══════════════════════ AUDIO CAPTURE ══════════════════════ */
  async function startCapture() {
    if (S.socket?.readyState !== WebSocket.OPEN) {
      setConn('Waiting for the connection…', null);
      await waitForWS();
      if (S.socket?.readyState !== WebSocket.OPEN) {
        showToast('Not connected yet — try again in a moment', 'warn');
        return;
      }
    }

    let stream;
    try {
      setConn('Choose the tab or window to listen to', null);
      stream = await navigator.mediaDevices.getDisplayMedia({
        video: { width:1, height:1, frameRate:1 },
        audio: { echoCancellation:false, noiseSuppression:false, autoGainControl:false, sampleRate:16000 }
      });
    } catch(e) {
      if (e.name !== 'NotAllowedError') showToast(`Capture failed: ${e.message}`, 'bad');
      setConn('Connected', 'ok');
      return;
    }

    try {
      stream.getVideoTracks().forEach(t => t.stop());

      let audioTrack = stream.getAudioTracks()[0];
      let audioStream;
      if (audioTrack) {
        audioStream = new MediaStream([audioTrack]);
      } else {
        showToast('That source had no audio — using your microphone instead', 'warn');
        let micStream;
        try {
          micStream = await navigator.mediaDevices.getUserMedia({
            audio: { echoCancellation:true, noiseSuppression:true, autoGainControl:true, sampleRate:16000 }
          });
        } catch(micErr) {
          showToast('No audio available. Tick "Share tab audio", or allow the microphone.', 'bad');
          stream.getTracks().forEach(t => t.stop());
          setConn('Connected', 'ok');
          return;
        }
        audioTrack = micStream.getAudioTracks()[0];
        audioStream = micStream;
      }

      // The track ends when the OS re-routes audio (Bluetooth connects, user
      // stops sharing) — surface it instead of silently capturing nothing.
      audioTrack.addEventListener('ended', () => {
        if (S.isCapturing) {
          stopCapture();
          showToast('Audio source ended — press the mic to start again', 'warn');
        }
      });

      const opts = {};
      if (MediaRecorder.isTypeSupported('audio/webm;codecs=opus')) opts.mimeType = 'audio/webm;codecs=opus';
      else if (MediaRecorder.isTypeSupported('audio/webm')) opts.mimeType = 'audio/webm';
      opts.audioBitsPerSecond = 32000;  // 32kbps is plenty for 16kHz mono speech

      const rec = new MediaRecorder(audioStream, opts);
      S.recordingChunks = [];

      rec.ondataavailable = e => { if (e.data?.size > 0) S.recordingChunks.push(e.data); };
      rec.onerror = () => {
        if (S.isCapturing) { stopCapture(); showToast('Recording stopped unexpectedly', 'bad'); }
      };
      rec.onstop = async () => {
        if (!S.recordingChunks.length) { schedNext(); return; }
        try {
          const blob = new Blob(S.recordingChunks, { type: opts.mimeType || 'audio/webm' });
          const buf  = await blob.arrayBuffer();
          if (buf.byteLength >= MIN_CHUNK_BYTES) {
            sendAudio(buf);
            S.bytesSent += buf.byteLength;
            S.chunksSent++;
          } else {
            S.chunksDropped++;
          }
          S.recordingChunks = [];
        } catch(e) { /* fall through to reschedule */ }
        schedNext();
      };

      function schedNext() {
        if (!S.isCapturing || rec.state !== 'inactive') return;
        setTimeout(() => {
          if (S.isCapturing && rec.state === 'inactive') {
            rec.start();
            setTimeout(() => { if (S.isCapturing && rec.state === 'recording') rec.stop(); }, S.chunkInterval);
          }
        }, 5);
      }

      rec.start();
      setTimeout(() => { if (S.isCapturing && rec.state === 'recording') rec.stop(); }, S.chunkInterval);

      S.mediaRecorder = rec;
      S.systemStream  = stream;
      S.isCapturing   = true;

      startLevelMeter(audioStream);

      E.app.classList.add('is-live');
      E.captureBtn.classList.add('active');
      E.micIcon.className = 'fas fa-stop';
      E.statusText.textContent = `Listening — into ${LANG_NAMES[S.targetLang] || S.targetLang}`;
      E.liveBar.style.display = 'flex';
      updateLiveBar('Listening…');
      E.captionBar.style.display = 'flex';
      setPopupStatus('Recording');
      S.bytesSent = 0; S.chunksSent = 0; S.chunksDropped = 0;
      setConn('Capturing audio', 'ok');
      armSilentWatchdog();

      S.sessionStart = S.sessionStart || Date.now();
      clearInterval(S.sessionTimer);
      S.sessionTimer = setInterval(updateTimer, 1000);
      clearInterval(S.throughputTimer);
      S.throughputTimer = setInterval(reportThroughput, 2000);

    } catch(e) {
      showToast(`Capture failed: ${e.message}`, 'bad');
      stream?.getTracks().forEach(t => t.stop());
    }
  }

  function stopCapture() {
    S.isCapturing = false;
    commitCurrentSeg();
    try { S.mediaRecorder?.stop(); } catch(_) {}
    S.systemStream?.getTracks().forEach(t => t.stop());
    stopLevelMeter();
    clearInterval(S.sessionTimer);
    clearInterval(S.throughputTimer);
    clearTimeout(S.silentWatchdog);
    S.silentWatchdog = null;

    E.app.classList.remove('is-live');
    E.captureBtn.classList.remove('active');
    E.micIcon.className = 'fas fa-microphone';
    E.statusText.textContent = 'Tap to start';
    E.liveBar.style.display = 'none';
    setPopupStatus('Idle');
    setConn('Connected', 'ok');
  }

  async function waitForWS(ms = 5000) {
    const deadline = Date.now() + ms;
    while (Date.now() < deadline) {
      if (S.socket?.readyState === WebSocket.OPEN) return;
      await new Promise(r => setTimeout(r, 100));
    }
  }

  /* ── INPUT LEVEL METER ──
     Shows the room is actually being heard, without waiting for a transcript. */
  const LEVEL_BARS = 9;

  function buildLevelMeter() {
    E.levelMeter.innerHTML = Array.from({length: LEVEL_BARS},
      () => '<span class="lt-level-bar"></span>').join('');
  }

  function startLevelMeter(stream) {
    try {
      S.audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      const src = S.audioCtx.createMediaStreamSource(stream);
      S.analyser = S.audioCtx.createAnalyser();
      S.analyser.fftSize = 512;
      S.analyser.smoothingTimeConstant = 0.75;
      src.connect(S.analyser);

      const data = new Uint8Array(S.analyser.frequencyBinCount);
      const bars = [...E.levelMeter.children];

      const tick = () => {
        S.analyser.getByteTimeDomainData(data);
        let sum = 0;
        for (const v of data) { const d = (v - 128) / 128; sum += d * d; }
        const rms = Math.sqrt(sum / data.length);
        // Perceptual curve — raw RMS barely moves for normal speech
        const level = Math.min(1, Math.pow(rms * 4.2, 0.62));

        E.captureBtn.style.setProperty('--level', level.toFixed(3));
        const lit = Math.round(level * LEVEL_BARS);
        bars.forEach((bar, i) => {
          const on = i < lit;
          bar.style.height = (on ? 5 + i * 2 : 3) + 'px';
          bar.classList.toggle('is-lit', on && i < LEVEL_BARS - 2);
          bar.classList.toggle('is-hot', on && i >= LEVEL_BARS - 2);
        });
        S.levelRaf = requestAnimationFrame(tick);
      };
      tick();
    } catch(e) { /* metering is optional — capture still works without it */ }
  }

  function stopLevelMeter() {
    cancelAnimationFrame(S.levelRaf);
    S.levelRaf = null;
    try { S.audioCtx?.close(); } catch(_) {}
    S.audioCtx = null; S.analyser = null;
    E.captureBtn.style.setProperty('--level', '0');
    [...E.levelMeter.children].forEach(b => {
      b.style.height = '3px';
      b.classList.remove('is-lit', 'is-hot');
    });
  }

  /* ══════════════════════ FLOATING CAPTIONS ══════════════════════
     A position:fixed div lives inside this page, so it can only ever float
     over this page — switch to the meeting tab and it is gone. Document
     Picture-in-Picture opens a real always-on-top OS window that survives tab
     and application switches, which is the whole point of a floating caption.

     The panel's DOM nodes are MOVED into that window rather than cloned, so
     every existing update path keeps writing to the same elements. When the
     window closes they move back.
  ═══════════════════════════════════════════════════════════════ */
  const PIP_SUPPORTED = 'documentPictureInPicture' in window;

  function currentTheme() {
    return document.documentElement.getAttribute('data-lt-theme') || 'dark';
  }

  /* PiP documents inherit nothing, so the stylesheet has to be carried over. */
  function copyStylesInto(win) {
    for (const sheet of document.styleSheets) {
      try {
        const css = [...sheet.cssRules].map(r => r.cssText).join('\n');
        const style = win.document.createElement('style');
        style.textContent = css;
        win.document.head.appendChild(style);
      } catch (e) {
        // Cross-origin sheet: cssRules throws, so link it instead.
        if (!sheet.href) continue;
        const link = win.document.createElement('link');
        link.rel = 'stylesheet';
        link.href = sheet.href;
        win.document.head.appendChild(link);
      }
    }
  }

  async function openFloating() {
    if (!PIP_SUPPORTED) {
      // Firefox and Safari have no Document PiP: fall back to the in-page
      // panel and say plainly what it can and cannot do.
      showPopup();
      showToast('Captions are pinned inside this tab — this browser cannot float them over other windows', 'warn');
      return;
    }
    try {
      const pip = await documentPictureInPicture.requestWindow({ width: 480, height: 232 });
      copyStylesInto(pip);
      pip.document.documentElement.setAttribute('data-lt-theme', currentTheme());
      pip.document.title = 'LiveTranslate captions';
      pip.document.body.style.margin = '0';

      E.ltPopup.classList.add('lt-popup--pip');
      E.ltPopup.classList.remove('minimised');
      E.ltPopup.style.display = 'block';
      pip.document.body.appendChild(E.ltPopup);

      S.pipWindow = pip;
      S.popupVisible = true;
      S.popupMinimised = false;
      E.popupBtn.classList.add('is-on');

      // Fires when the user closes the PiP window from its own chrome.
      pip.addEventListener('pagehide', restoreFromPip, { once: true });
    } catch (e) {
      // Blocked (no user gesture, or the user dismissed it) — degrade quietly.
      showPopup();
    }
  }

  function restoreFromPip() {
    if (!S.pipWindow) return;
    E.ltPopup.classList.remove('lt-popup--pip');
    E.ltPopup.style.display = 'none';
    document.body.appendChild(E.ltPopup);
    S.pipWindow = null;
    S.popupVisible = false;
    E.popupBtn.classList.remove('is-on');
  }

  function closeFloating() {
    if (S.pipWindow) { S.pipWindow.close(); restoreFromPip(); return; }
    hidePopup();
  }

  function toggleFloating() {
    if (S.pipWindow || (S.popupVisible && !PIP_SUPPORTED)) closeFloating();
    else openFloating();
  }

  function showPopup() {
    E.ltPopup.style.display = 'block';
    S.popupVisible = true;
    S.popupMinimised = false;
    E.ltPopup.classList.remove('minimised');
    E.popupBtn.classList.add('is-on');
  }
  function hidePopup() {
    E.ltPopup.style.display = 'none';
    S.popupVisible = false;
    E.popupBtn.classList.remove('is-on');
  }
  function toggleMinimise() {
    S.popupMinimised = !S.popupMinimised;
    E.ltPopup.classList.toggle('minimised', S.popupMinimised);
    E.popupMinBtn.querySelector('i').className = S.popupMinimised ? 'fas fa-plus' : 'fas fa-minus';
  }
  function updatePopupOrig(text) {
    if (!S.popupVisible) return;
    E.popupOrig.textContent = text;
    E.popupTrans.innerHTML = '';
    E.popupTrans.appendChild(makeCursor());
  }
  function updatePopupTrans(text) { if (S.popupVisible) E.popupTrans.textContent = text; }
  function setPopupStatus(msg) { E.popupStatus.textContent = msg; }

  /* Draggable popup (mouse + touch) */
  function dragStart(clientX, clientY) {
    if (S.pipWindow) return;   // the OS window handles its own movement
    S.isDragging = true;
    const rect = E.ltPopup.getBoundingClientRect();
    S.dragOffX = clientX - rect.left;
    S.dragOffY = clientY - rect.top;
  }
  function dragMove(clientX, clientY) {
    if (!S.isDragging) return;
    // Keep the popup on screen however far the pointer travels
    const w = E.ltPopup.offsetWidth, h = E.ltPopup.offsetHeight;
    const x = Math.min(Math.max(clientX - S.dragOffX, 0), window.innerWidth  - w);
    const y = Math.min(Math.max(clientY - S.dragOffY, 0), window.innerHeight - h);
    Object.assign(E.ltPopup.style, { right:'auto', bottom:'auto', left:x+'px', top:y+'px' });
  }
  E.popupDragBar.addEventListener('mousedown', e => { dragStart(e.clientX, e.clientY); e.preventDefault(); });
  document.addEventListener('mousemove', e => dragMove(e.clientX, e.clientY));
  document.addEventListener('mouseup', () => S.isDragging = false);
  E.popupDragBar.addEventListener('touchstart', e => {
    const t = e.touches[0]; dragStart(t.clientX, t.clientY);
  }, {passive:true});
  document.addEventListener('touchmove', e => {
    const t = e.touches[0]; dragMove(t.clientX, t.clientY);
  }, {passive:true});
  document.addEventListener('touchend', () => S.isDragging = false);

  /* ══════════════════════ SUMMARY ══════════════════════ */
  function openSummary() {
    if (!S.segments.length) { showToast('Nothing to summarise yet', 'warn'); return; }

    const duration = S.sessionStart ? formatTime((Date.now() - S.sessionStart)/1000) : '—';
    const langs = Object.keys(S.detectedLangs).join(', ') || '—';
    const avgLag = S.lags.length
      ? (S.lags.reduce((a,b)=>a+b,0) / S.lags.length).toFixed(1) + 's'
      : '—';

    E.summaryMeta.innerHTML = [
      ['Duration', duration],
      ['Segments', S.segments.length],
      ['Words', S.totalWords],
      ['Heard', langs],
      ['Into', LANG_NAMES[S.targetLang] || S.targetLang],
      ['Avg lag', avgLag],
    ].map(([k,v]) => `<span class="lt-meta-chip">${k} <b>${escHtml(v)}</b></span>`).join('');

    E.summaryTranscript.textContent = transcriptText();

    // Longer translated lines carry the substance; the tail is the most
    // recent context, which is what a mid-meeting reader wants.
    const points = S.segments
      .map(s => s.translation)
      .filter(t => t && t.split(/\s+/).length > 5)
      .slice(-8);
    E.summaryPoints.innerHTML = points.length
      ? points.map(t => `<li>${escHtml(t)}</li>`).join('')
      : '<li>Not enough speech yet to pull out key points.</li>';

    E.summaryOverlay.style.display = 'flex';
  }

  function closeSummary() { E.summaryOverlay.style.display = 'none'; }

  function transcriptText() {
    return S.segments
      .map(s => `[${s.time}] ${s.lang}\n${s.original}\n→ ${s.translation || '(translating…)'}`)
      .join('\n\n');
  }

  /* ══════════════════════ EXPORT ══════════════════════ */
  function download(filename, text, mime='text/plain') {
    const blob = new Blob([text], { type: `${mime};charset=utf-8` });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }

  function stamp() { return new Date().toISOString().slice(0,10); }

  function exportTranscript() {
    if (!S.segments.length) { showToast('Nothing to download yet', 'warn'); return; }
    download(`livetranslate-${stamp()}.txt`, transcriptText());
    showToast('Transcript downloaded');
  }

  /* Subtitles for the recording of the same meeting. Cues are spaced by the
     segment's own clock time, so they line up with the source audio. */
  function exportSrt() {
    if (!S.segments.length) { showToast('Nothing to export yet', 'warn'); return; }
    const base = S.sessionStart || Date.now();
    const cues = S.segments.map((s, i) => {
      const start = Math.max(0, (parseClock(s.time, base) ?? i * 4));
      return { start, text: s.translation || s.original };
    }).filter(c => c.text?.trim());

    const srt = cues.map((c, i) => {
      const next = cues[i+1]?.start ?? c.start + 4;
      const end = Math.min(Math.max(next, c.start + 1), c.start + 7);
      return `${i+1}\n${srtTime(c.start)} --> ${srtTime(end)}\n${c.text.trim()}\n`;
    }).join('\n');

    download(`livetranslate-${stamp()}.srt`, srt, 'application/x-subrip');
    showToast('Subtitles downloaded');
  }

  function parseClock(hms, baseMs) {
    const m = /^(\d{2}):(\d{2}):(\d{2})$/.exec(hms || '');
    if (!m) return null;
    const d = new Date(baseMs);
    const secsOfDay = (+m[1])*3600 + (+m[2])*60 + (+m[3]);
    const baseSecs = d.getHours()*3600 + d.getMinutes()*60 + d.getSeconds();
    return secsOfDay - baseSecs;
  }

  function srtTime(sec) {
    const ms = Math.max(0, Math.round(sec * 1000));
    const h = String(Math.floor(ms/3600000)).padStart(2,'0');
    const m = String(Math.floor(ms/60000) % 60).padStart(2,'0');
    const s = String(Math.floor(ms/1000) % 60).padStart(2,'0');
    return `${h}:${m}:${s},${String(ms % 1000).padStart(3,'0')}`;
  }

  function copyTranscript() {
    if (!S.segments.length) { showToast('Nothing to copy yet', 'warn'); return; }
    navigator.clipboard.writeText(transcriptText())
      .then(() => showToast('Transcript copied'))
      .catch(() => showToast('Copy failed — check clipboard permissions', 'bad'));
  }

  /* ══════════════════════ HISTORY ══════════════════════ */
  function openHistory() {
    E.historyOverlay.style.display = 'flex';
    E.historySearch.value = '';
    loadHistory('');
    setTimeout(() => E.historySearch.focus(), 60);
  }

  function closeHistory() { E.historyOverlay.style.display = 'none'; }

  async function loadHistory(q) {
    E.historyList.innerHTML = '<div class="lt-skel"></div><div class="lt-skel"></div><div class="lt-skel"></div>';
    try {
      const resp = await fetch(`/rs2t/history/?q=${encodeURIComponent(q || '')}`);
      if (!resp.ok) throw new Error(resp.statusText);
      const data = await resp.json();
      renderHistory(data.sessions || [], data.quota);
    } catch(e) {
      E.historyList.innerHTML =
        '<div class="lt-history-empty"><i class="fas fa-triangle-exclamation"></i>' +
        '<p>Could not load saved sessions</p><small>Check your connection and try again.</small></div>';
      E.historyCount.textContent = '';
    }
  }

  function renderHistory(sessions, quota) {
    if (quota) E.historyCount.textContent = `${sessions.length} of ${quota} kept`;

    if (!sessions.length) {
      const searching = !!E.historySearch.value.trim();
      E.historyList.innerHTML = searching
        ? `<div class="lt-history-empty"><i class="fas fa-magnifying-glass"></i>
             <p>No sessions match that search</p><small>Try a shorter word.</small></div>`
        : `<div class="lt-history-empty"><i class="fas fa-clock-rotate-left"></i>
             <p>No saved sessions yet</p>
             <small>A session is saved when you clear it or close the tab.</small></div>`;
      return;
    }

    E.historyList.innerHTML = sessions.map(cardHtml).join('');
  }

  function cardHtml(s) {
    const dur   = formatTime(s.duration_seconds || 0);
    const date  = new Date(s.ended_at).toLocaleString('en-GB', {
      day:'2-digit', month:'short', year:'numeric', hour:'2-digit', minute:'2-digit'
    });
    const langs = (s.detected_languages || []).join(', ') || '—';
    const points = (s.key_points || []).length
      ? `<ul class="lt-history-points">${s.key_points.map(p => `<li>${escHtml(p)}</li>`).join('')}</ul>`
      : '';

    return `
      <div class="lt-history-card" data-id="${s.id}">
        <div class="lt-history-hdr">
          <span class="lt-history-title">${escHtml(s.title || 'Untitled session')}</span>
          <span class="lt-history-date">${date}</span>
        </div>
        <div class="lt-history-stats">
          <span><i class="fas fa-stopwatch"></i> ${dur}</span>
          <span><i class="fas fa-font"></i> ${s.word_count || 0} words</span>
          <span><i class="fas fa-layer-group"></i> ${s.segment_count || 0} segments</span>
          <span><i class="fas fa-globe"></i> ${escHtml(langs)}</span>
        </div>
        ${s.summary ? `<p class="lt-history-summary">${escHtml(s.summary)}</p>` : ''}
        ${points}
        <div class="lt-history-actions">
          <button class="lt-btn lt-btn--xs lt-btn--secondary" data-act="open">
            <i class="fas fa-book-open"></i> Read
          </button>
          <span class="lt-spacer"></span>
          <a class="lt-btn lt-btn--xs lt-btn--secondary" href="/rs2t/history/${s.id}/download/?format=txt" download>TXT</a>
          <a class="lt-btn lt-btn--xs lt-btn--secondary" href="/rs2t/history/${s.id}/download/?format=md" download>MD</a>
          <a class="lt-btn lt-btn--xs lt-btn--secondary" href="/rs2t/history/${s.id}/download/?format=srt" download>SRT</a>
          <a class="lt-btn lt-btn--xs lt-btn--secondary" href="/rs2t/history/${s.id}/download/?format=json" download>JSON</a>
          <button class="lt-btn lt-btn--xs lt-btn--danger" data-act="delete">
            <i class="fas fa-trash-can"></i>
          </button>
        </div>
        <div class="lt-history-transcript" hidden></div>
      </div>`;
  }

  /* History card actions, delegated so re-rendered cards keep working */
  E.historyList.addEventListener('click', async e => {
    const btn = e.target.closest('[data-act]');
    if (!btn) return;
    const card = btn.closest('.lt-history-card');
    const id = card.dataset.id;

    if (btn.dataset.act === 'delete') {
      if (!confirm('Delete this session permanently?')) return;
      try {
        const resp = await fetch(`/rs2t/history/${id}/delete/`, {
          method: 'POST', headers: { 'X-CSRFToken': CSRF },
        });
        if (!resp.ok) throw new Error();
        card.remove();
        if (!E.historyList.querySelector('.lt-history-card')) renderHistory([]);
        showToast('Session deleted');
      } catch(_) { showToast('Could not delete that session', 'bad'); }
      return;
    }

    if (btn.dataset.act === 'open') {
      const panel = card.querySelector('.lt-history-transcript');
      const isOpen = !panel.hidden;
      panel.hidden = isOpen;
      card.classList.toggle('is-open', !isOpen);
      btn.innerHTML = isOpen
        ? '<i class="fas fa-book-open"></i> Read'
        : '<i class="fas fa-chevron-up"></i> Hide';
      if (isOpen || panel.dataset.loaded) return;

      panel.innerHTML = '<div class="lt-skel" style="height:56px"></div>';
      try {
        const resp = await fetch(`/rs2t/history/${id}/`);
        if (!resp.ok) throw new Error();
        const data = await resp.json();
        panel.innerHTML = (data.transcript || []).map(t => `
          <div class="lt-ht-row ${langClass(t.language)}">
            <div class="lt-ht-meta">
              <span>${escHtml(String(t.timestamp || '').slice(11,19) || '--:--:--')}</span>
              <span>${escHtml(t.language || '')}</span>
            </div>
            <div class="lt-ht-orig">${escHtml(t.original || '')}</div>
            <div class="lt-ht-trans">${escHtml(t.translation || '')}</div>
          </div>`).join('') || '<p class="lt-empty-note">This session has no transcript lines.</p>';
        panel.dataset.loaded = '1';
      } catch(_) {
        panel.innerHTML = '<p class="lt-empty-note">Could not load this transcript.</p>';
      }
    }
  });

  /* ══════════════════════ CLEAR ══════════════════════ */
  function onSessionCleared(d) {
    if (d.discarded)      showToast('Session discarded — nothing was saved', 'warn');
    else if (d.saved)     showToast('Session saved to your history');
    else                  showToast('Session cleared — there was nothing to save');
    E.clearBtn.disabled = false;
    E.clearBtn.querySelector('i').className = 'fas fa-floppy-disk';
    E.clearBtn.querySelector('span').textContent = 'Save & new';
  }

  function clearSession(save = true) {
    const prompt = save
      ? 'Save this session to your history and start a new one?'
      : 'Discard this session? The transcript will not be saved and cannot be recovered.';
    if (!confirm(prompt)) return;
    if (S.isCapturing) stopCapture();
    commitCurrentSeg();

    E.transcriptEl.innerHTML = `
      <div class="lt-empty" id="emptyState">
        <div class="lt-empty-icon"><i class="fas fa-tower-broadcast"></i></div>
        <h3>Ready to translate</h3>
        <p>Start capture, then pick the tab or window carrying the meeting audio and
           tick <b>Share tab audio</b>. Press <kbd class="lt-kbd">Space</kbd> to start and stop.</p>
      </div>`;
    E.emptyState = $('emptyState');

    E.captionBar.style.display = 'none';
    E.liveBar.style.display = 'none';
    S.segments = [];
    S.detectedLangs = {};
    S.totalWords = 0;
    S.segmentCount = 0;
    S.lags = [];
    S.heardAnything = false;
    S.sessionStart = null;
    clearInterval(S.sessionTimer);

    E.sessionTimer.textContent = '00:00';
    E.wordCount.textContent = '0';
    E.segmentCount.textContent = '0';
    E.lagVal.textContent = '—';
    E.lagFill.style.width = '0%';
    E.summaryBtn.disabled = true;
    E.searchInput.value = '';
    runSearch('');
    renderDetectedLangs();
    setAutoScroll(true);

    // Saving summarises server-side, so show progress on the button that
    // triggered it. Discarding is immediate and needs no spinner.
    if (save) {
      E.clearBtn.disabled = true;
      E.clearBtn.querySelector('i').className = 'fas fa-spinner fa-spin';
      E.clearBtn.querySelector('span').textContent = 'Saving…';
    }
    sendWS({ type: 'clear_session', save });
  }

  /* ══════════════════════ GLOSSARY ══════════════════════ */
  async function openGlossary() {
    E.glossaryOverlay.style.display = 'flex';
    await loadGlossary();
    setTimeout(() => E.glossTerm.focus(), 60);
  }

  function closeGlossary() { E.glossaryOverlay.style.display = 'none'; }

  async function loadGlossary() {
    E.glossaryList.innerHTML = '<div class="lt-skel" style="height:44px"></div>';
    try {
      const resp = await fetch('/rs2t/glossary/');
      if (!resp.ok) throw new Error();
      renderGlossary((await resp.json()).terms || []);
    } catch (_) {
      E.glossaryList.innerHTML = '<p class="lt-empty-note">Could not load the glossary.</p>';
    }
  }

  function renderGlossary(terms) {
    E.glossaryCount.textContent = terms.length ? `${terms.length} term${terms.length === 1 ? '' : 's'}` : '';
    if (!terms.length) {
      E.glossaryList.innerHTML =
        '<p class="lt-empty-note">No terms yet. Add the words this translator keeps mishearing.</p>';
      return;
    }
    E.glossaryList.innerHTML = terms.map(t => `
      <div class="lt-gloss-row" data-id="${t.id}">
        <span class="lt-gloss-term">${escHtml(t.term)}</span>
        <span class="lt-gloss-hint">${escHtml(t.hint || 'keep as written')}</span>
        <button class="lt-icon-btn lt-icon-btn--danger" data-act="del"
                title="Remove ${escHtml(t.term)}"><i class="fas fa-times"></i></button>
      </div>`).join('');
  }

  E.glossaryForm.addEventListener('submit', async e => {
    e.preventDefault();
    const term = E.glossTerm.value.trim();
    if (!term) return;
    const body = new URLSearchParams({ term, hint: E.glossHint.value.trim() });
    try {
      const resp = await fetch('/rs2t/glossary/add/', {
        method: 'POST',
        headers: { 'X-CSRFToken': CSRF, 'Content-Type': 'application/x-www-form-urlencoded' },
        body,
      });
      const data = await resp.json();
      if (!resp.ok) { showToast(data.error || 'Could not add that term', 'bad'); return; }
      E.glossTerm.value = ''; E.glossHint.value = '';
      showToast(data.created ? `Added "${data.term}"` : `Updated "${data.term}"`);
      await loadGlossary();
      // Takes effect on the next window, without reconnecting.
      sendWS({ type: 'reload_glossary' });
    } catch (_) { showToast('Could not add that term', 'bad'); }
  });

  E.glossaryList.addEventListener('click', async e => {
    const btn = e.target.closest('[data-act="del"]');
    if (!btn) return;
    const row = btn.closest('.lt-gloss-row');
    try {
      const resp = await fetch(`/rs2t/glossary/${row.dataset.id}/delete/`, {
        method: 'POST', headers: { 'X-CSRFToken': CSRF },
      });
      if (!resp.ok) throw new Error();
      row.remove();
      if (!E.glossaryList.querySelector('.lt-gloss-row')) renderGlossary([]);
      sendWS({ type: 'reload_glossary' });
    } catch (_) { showToast('Could not remove that term', 'bad'); }
  });

  /* ══════════════════════ ACTION MENU ══════════════════════ */
  /* Below the narrow breakpoint the action row becomes a dropdown. Same
     buttons, same handlers — only the presentation changes, so nothing has to
     be duplicated or kept in sync. */
  function setMenu(open) {
    E.actionMenu.classList.toggle('is-open', open);
    E.moreBtn.classList.toggle('is-open', open);
    E.moreBtn.setAttribute('aria-expanded', String(open));
  }

  function menuOpen() { return E.actionMenu.classList.contains('is-open'); }

  E.moreBtn.addEventListener('click', e => { e.stopPropagation(); setMenu(!menuOpen()); });

  // Any action closes the menu, so the next tap goes to the transcript.
  E.actionMenu.addEventListener('click', e => { if (e.target.closest('.lt-act')) setMenu(false); });

  document.addEventListener('click', e => {
    if (!menuOpen()) return;
    if (!e.target.closest('#actionMenu') && !e.target.closest('#moreBtn')) setMenu(false);
  });

  /* ══════════════════════ SMALL UI ══════════════════════ */
  function updateLiveBar(msg) { E.liveBarText.textContent = msg; }
  function updateCaptionTranslated(text) { E.captionTranslated.textContent = text; }
  function updateTimer() {
    if (!S.sessionStart) return;
    E.sessionTimer.textContent = formatTime((Date.now() - S.sessionStart)/1000);
  }
  function anyModalOpen() {
    return [E.summaryOverlay, E.historyOverlay, E.helpOverlay, E.glossaryOverlay]
      .some(m => m.style.display !== 'none');
  }
  function closeAllModals() {
    closeSummary(); closeHistory(); closeGlossary();
    E.helpOverlay.style.display = 'none';
  }

  /* ══════════════════════ EVENT LISTENERS ══════════════════════ */
  E.captureBtn.addEventListener('click', () => S.isCapturing ? stopCapture() : startCapture());

  E.targetLangSelect.addEventListener('change', e => {
    S.targetLang = e.target.value;
    sendWS({ type:'set_target_language', target_language: S.targetLang });
    if (S.isCapturing) E.statusText.textContent = `Listening — into ${LANG_NAMES[S.targetLang] || S.targetLang}`;
  });

  E.themeBtn.addEventListener('click', toggleTheme);
  E.fontUpBtn.addEventListener('click', () => bumpReadSize(1));
  E.fontDownBtn.addEventListener('click', () => bumpReadSize(-1));

  E.clearBtn.addEventListener('click', () => clearSession(true));
  E.discardBtn.addEventListener('click', () => clearSession(false));
  E.summaryBtn.addEventListener('click', openSummary);
  E.summaryCloseBtn.addEventListener('click', closeSummary);
  E.summaryOverlay.addEventListener('click', e => { if (e.target === E.summaryOverlay) closeSummary(); });

  E.popupBtn.addEventListener('click', toggleFloating);
  E.popupMinBtn.addEventListener('click', toggleMinimise);
  E.popupCloseBtn.addEventListener('click', closeFloating);

  E.historyBtn.addEventListener('click', openHistory);
  E.historyCloseBtn.addEventListener('click', closeHistory);
  E.historyOverlay.addEventListener('click', e => { if (e.target === E.historyOverlay) closeHistory(); });
  E.historySearch.addEventListener('input', e => {
    clearTimeout(S.historyTimer);
    const q = e.target.value;
    S.historyTimer = setTimeout(() => loadHistory(q), 260);
  });

  E.glossaryBtn.addEventListener('click', openGlossary);
  E.glossaryCloseBtn.addEventListener('click', closeGlossary);
  E.glossaryOverlay.addEventListener('click', e => { if (e.target === E.glossaryOverlay) closeGlossary(); });

  E.helpBtn.addEventListener('click', () => E.helpOverlay.style.display = 'flex');
  E.helpCloseBtn.addEventListener('click', () => E.helpOverlay.style.display = 'none');
  E.helpOverlay.addEventListener('click', e => { if (e.target === E.helpOverlay) E.helpOverlay.style.display = 'none'; });

  E.copyTranscriptBtn.addEventListener('click', copyTranscript);
  E.exportBtn.addEventListener('click', exportTranscript);
  E.exportSrtBtn.addEventListener('click', exportSrt);

  E.searchInput.addEventListener('input', e => runSearch(e.target.value));
  E.autoScrollBtn.addEventListener('click', () => setAutoScroll(!S.autoScroll));
  E.jumpBtn.addEventListener('click', () => setAutoScroll(true));

  // Scrolling up hands control to the reader; scrolling back down returns it.
  E.transcriptEl.addEventListener('scroll', () => {
    if (nearBottom()) { if (!S.autoScroll) setAutoScroll(true); }
    else if (S.autoScroll) setAutoScroll(false);
  }, { passive: true });

  /* Keyboard shortcuts */
  document.addEventListener('keydown', e => {
    const typing = e.target.isContentEditable || ['INPUT','SELECT','TEXTAREA'].includes(e.target.tagName);

    if (e.key === 'Escape') {
      if (menuOpen()) { setMenu(false); return; }
      if (typing && e.target === E.searchInput) { E.searchInput.value = ''; runSearch(''); E.searchInput.blur(); return; }
      closeAllModals();
      return;
    }
    if (typing || e.ctrlKey || e.metaKey || e.altKey) return;

    if (e.code === 'Space') { e.preventDefault(); S.isCapturing ? stopCapture() : startCapture(); return; }
    if (e.key === '/')      { e.preventDefault(); E.searchInput.focus(); return; }
    if (e.key === '?')      { E.helpOverlay.style.display = anyModalOpen() ? 'none' : 'flex'; return; }
    if (e.key === '+' || e.key === '=') { bumpReadSize(1); return; }
    if (e.key === '-' || e.key === '_') { bumpReadSize(-1); return; }

    switch (e.key.toLowerCase()) {
      case 'f': setAutoScroll(!S.autoScroll); break;
      case 'p': toggleFloating(); break;
      case 'h': E.historyOverlay.style.display === 'none' ? openHistory() : closeHistory(); break;
      case 's': E.summaryOverlay.style.display === 'none' ? openSummary() : closeSummary(); break;
      case 't': toggleTheme(); break;
      case 'g': E.glossaryOverlay.style.display === 'none' ? openGlossary() : closeGlossary(); break;
    }
  });

  // Don't leave an orphaned caption window behind when this page goes away.
  window.addEventListener('pagehide', () => { try { S.pipWindow?.close(); } catch(_) {} });

  /* ══════════════════════ BOOT ══════════════════════ */
  let savedTheme = 'dark', savedRead = READ_DEFAULT;
  try {
    savedTheme = localStorage.getItem(LS.theme) || 'dark';
    savedRead  = parseInt(localStorage.getItem(LS.read), 10) || READ_DEFAULT;
  } catch(_) {}
  applyTheme(savedTheme);
  applyReadSize(savedRead);
  buildLevelMeter();
  renderDetectedLangs();
  connectWS();
});
