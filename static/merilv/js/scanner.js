/* ============================================================================
 * Meril-V — Outbound Scanner Console
 *
 * The screen a picker stares at for 8 hours a day. Every scan produces an
 * unmistakable visual + audio signal, the scan input stays focused through
 * everything, and the delivery cards on the right reconcile every 2s against
 * the server so a second gun on the same session is reflected promptly.
 *
 * Reused across Branch Transfer and ZVA05 — both are the same console with a
 * different scan/state/focus/finish endpoint set and a couple of feature
 * flags, passed in via the config object rather than forked into new files.
 * ========================================================================= */
(function () {
    'use strict';

    function init(root) {
        if (!root) { return; }
        var cfg = JSON.parse(root.dataset.mvScanner || "{}");
        var ui = window.mvUI || { toast: function () {}, csrfHeaders: function (h) { return h || {}; } };

        var els = {
            input: root.querySelector('[data-mv="scan-input"]'),
            status: root.querySelector('[data-mv="scan-status"]'),
            // One log, inside the scan frame. It was two — a cut-down runtime
            // log on the console and the real filterable one inside a modal —
            // which put "did that land" and "what has been refused" in two
            // different places, one of them behind a click. Still queried from
            // `document` rather than `root`: the partial is included inside the
            // console today, but this has never depended on that.
            scanLog: document.querySelector('[data-mv="scan-log"]'),
            timer: root.querySelector('[data-mv="timer"]'),
            deliveries: root.querySelector('[data-mv="deliveries"]'),
            logToggle: document.querySelector('[data-mv="log-toggle"]'),
            logToggleLabel: document.querySelector('[data-mv="log-toggle-label"]'),
            qtyRequired: root.querySelector('[data-mv="qty-required"]'),
            qtyScanned: root.querySelector('[data-mv="qty-scanned"]'),
            qtyPending: root.querySelector('[data-mv="qty-pending"]'),
            errorAction: root.querySelector('[data-mv="error-action"]'),
            progressPct: root.querySelector('[data-mv="progress-pct"]'),
            progressFill: root.querySelector('[data-mv="progress-fill"]'),
            deliveryFilter: root.querySelector('[data-mv="delivery-filter"]'),
            totals: root.querySelector('[data-mv="totals"]'),
            pollSpinner: root.querySelector('[data-mv="poll-spinner"]'),
            undoBtn: root.querySelector('[data-mv="undo-btn"]'),
            clearScansBtn: root.querySelector('[data-mv="clear-scans-btn"]'),
            // document-scoped, like scanLog above: both of these render in
            // the page header's actions block, which is a sibling of
            // .mv-page-body and so is NOT inside .mv-console (root). Queried
            // from root, the toggle resolved to null and clicking it did
            // nothing at all — no mute, no label change — which is its own
            // reason to conclude the scan sounds are broken.
            soundSettings: document.querySelector('[data-mv="sound-settings"]'),
            soundLabel: document.querySelector('[data-mv="sound-label"]'),
            focusBanner: root.querySelector('[data-mv="focus-banner"]'),
            focusPrompt: root.querySelector('[data-mv="focus-prompt"]'),
            scopeBar: root.querySelector('[data-mv="scope-bar"]'),
            tabScan: root.querySelector('[data-mv="tab-scan"]'),
            tabBulk: root.querySelector('[data-mv="tab-bulk"]'),
            scanPanel: root.querySelector('[data-mv="scan-panel"]'),
            bulkPanel: root.querySelector('[data-mv="bulk-panel"]'),
            bulkInput: root.querySelector('[data-mv="bulk-input"]'),
            bulkStatus: root.querySelector('[data-mv="bulk-status"]'),
            bulkSubmit: root.querySelector('[data-mv="bulk-submit"]'),
            result: root.querySelector('[data-mv="scan-result"]'),
            // document-scoped: the popup hangs off the page, not the console.
            errorPop: document.querySelector('[data-mv="error-pop"]'),
            errorTitle: document.querySelector('[data-mv="error-title"]'),
            errorMessage: document.querySelector('[data-mv="error-message"]'),
            errorSerial: document.querySelector('[data-mv="error-serial"]'),
            errorDetail: document.querySelector('[data-mv="error-detail"]'),
            errorHint: document.querySelector('[data-mv="error-hint"]'),
            errorSkip: document.querySelector('[data-mv="error-skip"]'),
            // Header actions render outside .mv-console, like the sound button.
            invoiceBtn: document.querySelector('[data-mv="invoice-export"]')
        };

        var state = {
            posting: false,
            /* Scans that arrived while a request was in flight.
               A barcode gun does not wait for a network round trip before
               firing its next keystroke burst: with a bare `if (posting)
               return`, a stress run of 100 rapid scans reached the server 80
               times and lost the other 20 silently. They queue here instead,
               and drain in order as each request settles (see submitScan's
               .finally). */
            pendingScans: [],
            polling: false,
            soundEnabled: true,
            audioUnlocked: false,
            /* The last rejected scan, held until the picker resolves it.
               null when there is nothing outstanding. */
            failedScan: null,
            logRowCount: 0,
            /* Which delivery view is showing: card | list | table | kanban.
             * Overridden from localStorage further down if a choice was saved. */
            view: 'card',
            /* Row 2.4 — bulk-finish selection, Table view only. A Set survives
             * across re-renders (poll ticks rebuild every row), so a picker's
             * selection doesn't vanish the moment the state payload refreshes. */
            selectedDeliveries: new Set(),
            /* Bulk Paste is showing. The scan input is not the keyboard target
               while it is, so the refocus rules have to know. */
            bulkMode: false,
            /* A finish is in flight. Finishing twice updates the existing row
               rather than creating a second one (see _finish_one_delivery), so
               a double press is not destructive — but it is confusing, and the
               button says so by going quiet. */
            finishing: false,
            /* Whether the last paint had scanning blocked for want of a focused
               delivery. Only used to decide when to hand the keyboard back:
               re-running that on every 2s poll would fight the operator for the
               caret. */
            blockedLastPaint: false,
            /* Row-level permissions. The server re-checks every one of them on
               every request — these only decide which controls are DRAWN.

                 canEdit    correct an assigned serial (operate)
                 canBatch   change a line's batch (Batch Correction)
                 canDelete  remove one recorded scan (Delete A Scan Row)
                 canClear   clear a whole delivery's log (Clear Scan Log)

               A picker gets the first two and not the last two: undo is their
               tool and reaches the last scan, while removing a specific row
               found an hour later — or wiping a delivery's whole log — is a
               supervisor's call. Each is a capability an admin can move between
               tiers under Master Data -> Roles & Access, so these are no longer
               hard-wired to "is admin".

               canBatch and canClear fall back to their neighbours when the
               template predates them, so an older cached page keeps working
               rather than silently losing its controls. */
            canEdit: !!cfg.canEdit,
            canBatch: cfg.canBatch === undefined ? !!cfg.canEdit : !!cfg.canBatch,
            canDelete: !!cfg.canDelete,
            canClear: cfg.canClear === undefined ? !!cfg.canDelete : !!cfg.canClear,
            startedAt: cfg.startedAt ? new Date(cfg.startedAt).getTime() : Date.now()
        };

        /* ── Audio ─────────────────────────────────────────────────────────
         * A distinct sound per outcome family (PRD FR7) is produced by WebAudio
         * tones. Recorded mp3s are supported but OFF by default, because they
         * have never been recorded/licensed and static/merilv/audio/ does not
         * exist: preloading them unconditionally fired four 404s on every load
         * of every scanner screen, which buried real errors in the console
         * during debugging for no gain — the tones were what played either way.
         *
         * To switch to real files: drop {ok,warn,danger,neutral}.mp3 into
         * static/merilv/audio/ and set `useAudioFiles: true` in the page config
         * beside `audioBase`. A file that then fails to load still degrades to
         * its tone, per family, so a missing one is never silent.
         *
         * Autoplay can be blocked until the first user gesture, so scans before
         * that show the "sound off" indicator rather than throwing. */
        var audioCtx = null;
        var audioFiles = {};
        /* Each cue is a shape, not just a pitch.
         *
         * These were four sine waves differing only in frequency, which over
         * warehouse noise is one beep. An operator has to be able to tell
         * "took it" from "wrong item" without looking at the screen, so:
         *
         *   ok       two rising notes  — the "accepted" chirp
         *   danger   two low buzzes    — unmistakably not the ok sound
         *   warn     one wobbling note — a duplicate, needs a look
         *   neutral  one flat note     — queued/nothing happened
         *
         * `steps` are [frequency, startSeconds, durationSeconds]. */
        /* Peak amplitude for a cue that does not set its own. Raised from
         * 0.20 on the floor's report that the cues were too quiet over
         * warehouse noise. Cue notes barely overlap — the longest overlap in
         * the table is 5ms — so nothing here sums close to clipping even at
         * the loudest cue's level. */
        var CUE_GAIN = 0.30;

        /* Hard ceiling on one note's amplitude, whatever the operator asks
         * for. WebAudio gain is linear and 1.0 is full scale, so a value that
         * sums past it distorts instead of getting louder. No two notes in
         * CUE overlap (a test pins that), so one note is the whole signal and
         * this is the only limit that matters. */
        var MAX_CUE_PEAK = 0.60;

        /* The operator's own level, as a multiplier on each cue's gain. One
         * fixed volume cannot suit both a quiet packing bench and a floor next
         * to a conveyor, and the answer to "make it louder" should not be
         * another commit. Remembered per browser, like the delivery view and
         * the page size. */
        var VOLUME_KEY = 'merilv.outbound.soundVolume';
        var VOLUME_MIN = 0.25;
        var VOLUME_MAX = 2;
        state.volume = (function () {
            try {
                var saved = parseFloat(window.localStorage.getItem(VOLUME_KEY));
                if (isFinite(saved) && saved >= VOLUME_MIN && saved <= VOLUME_MAX) { return saved; }
            } catch (err) { /* private mode — fall through to the default */ }
            return 1;
        }());

        function setVolume(value) {
            state.volume = Math.min(Math.max(value, VOLUME_MIN), VOLUME_MAX);
            try { window.localStorage.setItem(VOLUME_KEY, String(state.volume)); }
            catch (err) { /* non-fatal: the level just will not persist */ }
        }

        /* Every sound the console can make, keyed by VARIANT id.
         *
         * The default variant of each family is keyed by the family's own name
         * — `ok`, `danger`, `mismatch` — so an outcome with no stored choice
         * resolves to exactly the cue it always had, and everything that reads
         * this table by family name still finds what it expects. The rest are
         * alternatives an operator picks in the sound panel.
         *
         * Why alternatives at all: a cue that carries across one warehouse
         * disappears in another. A bench next to a compressor loses the sine
         * cues; a quiet packing room finds the square ones abrasive over eight
         * hours. Rather than guess a set that suits every floor, each outcome
         * offers four and the floor picks.
         *
         * What the alternatives may NOT do is blur the families together: the
         * point of seven cues is that "took it", "already had it", "wrong
         * item", "wrong delivery" and "nothing focused" are five different
         * things to an operator who is not looking at the screen. So each
         * family's variants keep that family's identifying character — ok
         * rises, mismatch falls, wrong_delivery repeats a pip, blocked buzzes
         * slowly — and vary pitch, length and timbre within it.
         *
         * `steps` are [frequency, startSeconds, durationSeconds], and no two
         * notes in one cue may overlap: WebAudio gain is linear, so notes
         * sounding together sum, and the single per-note ceiling that makes an
         * operator-controlled volume safe rests on one note being the whole
         * signal. A test walks every entry here and enforces it. */
        var CUE = {
            /* ok — accepted and assigned. Rising, and short: it plays on every
               good scan, hundreds of times a shift. */
            ok:             { type: 'sine',     steps: [[784, 0, 0.09], [1175, 0.09, 0.13]] },
            ok_triple:      { type: 'sine',     steps: [[659, 0, 0.06], [880, 0.06, 0.12], [1175, 0.12, 0.19]] },
            ok_chime:       { type: 'sine',     steps: [[1047, 0, 0.22]] },
            ok_ping:        { type: 'triangle', steps: [[1319, 0, 0.10]] },

            /* warn — duplicate. One note that is not steady, so it reads as
               "hold on" rather than as a failure. */
            warn:           { type: 'triangle', steps: [[554, 0, 0.30]], wobble: 34 },
            warn_double:    { type: 'triangle', steps: [[554, 0, 0.09], [554, 0.13, 0.22]] },
            warn_low:       { type: 'triangle', steps: [[415, 0, 0.30]], wobble: 26 },
            warn_buzz:      { type: 'square',   gain: 0.26, steps: [[466, 0, 0.18]] },

            /* mismatch — wrong material or batch. Falls where ok rises, which
               is what makes it read as ok's opposite. */
            mismatch:       { type: 'triangle', steps: [[880, 0, 0.10], [587, 0.10, 0.24]] },
            mismatch_three: { type: 'triangle', steps: [[880, 0, 0.07], [740, 0.07, 0.14], [587, 0.14, 0.26]] },
            mismatch_low:   { type: 'triangle', steps: [[660, 0, 0.10], [440, 0.10, 0.26]] },
            mismatch_sharp: { type: 'square',   gain: 0.30, steps: [[880, 0, 0.08], [494, 0.08, 0.22]] },

            /* wrong_delivery — right item, wrong place. Told apart by RHYTHM
               before pitch, because rhythm survives warehouse noise. */
            wrong_delivery:      { type: 'square', gain: 0.34, steps: [[440, 0, 0.07], [440, 0.10, 0.17], [440, 0.20, 0.27]] },
            wrong_delivery_two:  { type: 'square', gain: 0.34, steps: [[440, 0, 0.08], [440, 0.12, 0.20]] },
            wrong_delivery_fast: { type: 'square', gain: 0.32, steps: [[523, 0, 0.05], [523, 0.07, 0.12], [523, 0.14, 0.19], [523, 0.21, 0.26]] },
            wrong_delivery_low:  { type: 'square', gain: 0.34, steps: [[330, 0, 0.07], [330, 0.10, 0.17], [330, 0.20, 0.27]] },

            /* blocked — nothing focused, so the scan was never looked at. A
               door that will not open: slow, equal, and in a timbre nothing
               else uses. */
            blocked:        { type: 'sawtooth', gain: 0.32, steps: [[330, 0, 0.14], [330, 0.22, 0.36]] },
            blocked_long:   { type: 'sawtooth', gain: 0.30, steps: [[294, 0, 0.34]] },
            blocked_three:  { type: 'sawtooth', gain: 0.30, steps: [[330, 0, 0.10], [330, 0.15, 0.25], [330, 0.30, 0.40]] },
            blocked_deep:   { type: 'sawtooth', gain: 0.32, steps: [[247, 0, 0.14], [247, 0.22, 0.36]] },

            /* danger — expired, over-scanned, already finished. Low, and
               unmistakably not the ok sound. */
            danger:         { type: 'square',   gain: 0.40, steps: [[196, 0, 0.16], [147, 0.20, 0.26]] },
            danger_three:   { type: 'square',   gain: 0.38, steps: [[262, 0, 0.10], [196, 0.12, 0.22], [147, 0.24, 0.34]] },
            danger_long:    { type: 'square',   gain: 0.38, steps: [[165, 0, 0.34]] },
            danger_harsh:   { type: 'sawtooth', gain: 0.36, steps: [[175, 0, 0.14], [131, 0.18, 0.32]] },

            /* neutral — queued offline, nothing decided either way. */
            neutral:        { type: 'sine', steps: [[660, 0, 0.16]] },
            neutral_blip:   { type: 'sine', steps: [[880, 0, 0.08]] },
            neutral_tick:   { type: 'sine', steps: [[440, 0, 0.09]] },
            neutral_two:    { type: 'sine', steps: [[660, 0, 0.07], [660, 0.10, 0.17]] }
        };

        /* What the sound panel offers per outcome, in the order it offers it.
         * The first of each is the default and shares the family's name. */
        var CUE_CHOICES = {
            ok: [
                ['ok', 'Two rising notes'],
                ['ok_triple', 'Three rising notes'],
                ['ok_chime', 'Soft chime'],
                ['ok_ping', 'Bright ping']
            ],
            warn: [
                ['warn', 'Wobbling note'],
                ['warn_double', 'Double tap'],
                ['warn_low', 'Low wobble'],
                ['warn_buzz', 'Short buzz']
            ],
            mismatch: [
                ['mismatch', 'Two falling notes'],
                ['mismatch_three', 'Three falling notes'],
                ['mismatch_low', 'Low fall'],
                ['mismatch_sharp', 'Sharp fall']
            ],
            wrong_delivery: [
                ['wrong_delivery', 'Three pips'],
                ['wrong_delivery_two', 'Two pips'],
                ['wrong_delivery_fast', 'Four fast pips'],
                ['wrong_delivery_low', 'Three low pips']
            ],
            blocked: [
                ['blocked', 'Two slow buzzes'],
                ['blocked_long', 'One long buzz'],
                ['blocked_three', 'Three buzzes'],
                ['blocked_deep', 'Two deep buzzes']
            ],
            danger: [
                ['danger', 'Two low buzzes'],
                ['danger_three', 'Three descending'],
                ['danger_long', 'One long low note'],
                ['danger_harsh', 'Harsh double']
            ],
            neutral: [
                ['neutral', 'One flat note'],
                ['neutral_blip', 'High blip'],
                ['neutral_tick', 'Low tick'],
                ['neutral_two', 'Two ticks']
            ]
        };

        /* The operator's pick per family, remembered on this machine like the
         * volume, the delivery view and the page size. Validated on the way in:
         * a stored id from a variant that no longer exists falls back to the
         * family default rather than silencing that outcome. */
        var CUE_CHOICE_KEY = 'merilv.outbound.soundCues';
        state.cueChoice = (function () {
            try {
                var saved = JSON.parse(window.localStorage.getItem(CUE_CHOICE_KEY) || '{}');
                var clean = {};
                Object.keys(CUE_CHOICES).forEach(function (family) {
                    if (CUE[saved[family]]) { clean[family] = saved[family]; }
                });
                return clean;
            } catch (err) { return {}; }      /* private mode, or corrupt JSON */
        }());

        function chosenCue(family) {
            var pick = state.cueChoice[family];
            return CUE[pick] ? pick : family;
        }

        function setCueChoice(family, variant) {
            if (!CUE[variant]) { return; }
            state.cueChoice[family] = variant;
            try { window.localStorage.setItem(CUE_CHOICE_KEY, JSON.stringify(state.cueChoice)); }
            catch (err) { /* non-fatal: the choice just will not persist */ }
        }

        /* Which sound an outcome plays. Separate from the colour family below:
         * the log row has four colours, the speaker has six sounds, and
         * collapsing the two is what silenced the distinction. */
        var CUE_FOR_OUTCOME = {
            match: 'ok',
            duplicate: 'warn',
            mismatch: 'mismatch',
            wrong_delivery: 'wrong_delivery',
            no_focus: 'blocked',
            error: 'danger'
        };

        function cueOf(outcome) {
            return CUE_FOR_OUTCOME[outcome && outcome.audio_cue]
                || (outcome && outcome.accepted ? 'ok' : 'neutral');
        }

        /* The one cue a finished bulk paste plays: anything accepted is a
         * success, otherwise the most serious rejection in the batch. */
        function bulkCue(summary, results) {
            if ((summary && summary.accepted_count) > 0) { return 'ok'; }
            var rows = results || [];
            var order = ['error', 'wrong_delivery', 'mismatch', 'duplicate'];
            for (var i = 0; i < order.length; i++) {
                for (var j = 0; j < rows.length; j++) {
                    if (rows[j].audio_cue === order[i]) { return CUE_FOR_OUTCOME[order[i]]; }
                }
            }
            return 'neutral';
        }

        function ensureAudioCtx() {
            if (audioCtx) { return audioCtx; }
            var AC = window.AudioContext || window.webkitAudioContext;
            audioCtx = AC ? new AC() : null;
            return audioCtx;
        }

        if (cfg.useAudioFiles && cfg.audioBase) {
            (cfg.audioFamilies || ['ok', 'warn', 'danger', 'neutral']).forEach(function (fam) {
                var a = new Audio(cfg.audioBase + fam + '.mp3');
                a.preload = 'auto';
                a.addEventListener('error', function () { audioFiles[fam] = null; }, { once: true });
                audioFiles[fam] = a;
            });
        }

        /* `family` is the outcome; the SOUND is whichever variant of it the
         * operator picked. Callers still pass a family and never need to know
         * a variant exists — playCue, the bulk summary and the panel's own
         * previews all speak in outcomes. */
        function playTone(family) {
            var ctx = ensureAudioCtx();
            if (!ctx) { return; }
            /* CUE[family] is the fallback rather than the lookup: passing a
               variant id straight in is what the panel's preview does. */
            var cue = CUE[chosenCue(family)] || CUE[family] || CUE.neutral;
            var now = ctx.currentTime;

            cue.steps.forEach(function (step) {
                var freq = step[0], at = now + step[1], until = now + step[2];
                var osc = ctx.createOscillator();
                var gain = ctx.createGain();
                osc.type = cue.type || 'sine';
                osc.frequency.setValueAtTime(freq, at);

                /* A slow frequency sweep, so "duplicate" wobbles rather than
                   sitting on one pitch — that is what makes it recognisable
                   without being louder than the others. */
                if (cue.wobble) {
                    osc.frequency.linearRampToValueAtTime(freq + cue.wobble, at + (until - at) / 2);
                    osc.frequency.linearRampToValueAtTime(freq, until);
                }

                /* Ramped, never switched: setting gain straight to a value
                   clicks, and a click on every scan is its own annoyance. */
                gain.gain.setValueAtTime(0.0001, at);
                /* exponentialRampToValueAtTime throws on zero, so the floor
                   is a hair above silence rather than silence itself. */
                var peak = Math.max(
                    Math.min((cue.gain || CUE_GAIN) * state.volume, MAX_CUE_PEAK), 0.0002);
                gain.gain.exponentialRampToValueAtTime(peak, at + 0.012);
                gain.gain.exponentialRampToValueAtTime(0.0001, until);
                osc.connect(gain).connect(ctx.destination);
                osc.start(at);
                osc.stop(until + 0.02);
            });
        }

        function playCue(family) {
            if (!state.soundEnabled) { return; }
            var file = audioFiles[family];
            if (file && file.error === null) {
                var p = file.cloneNode();
                p.play().catch(function () { playTone(family); });
            } else {
                playTone(family);
            }
        }

        function unlockAudio() {
            if (state.audioUnlocked) { return; }
            state.audioUnlocked = true;
            var ctx = ensureAudioCtx();
            if (ctx && ctx.state === 'suspended') { ctx.resume(); }
        }
        document.addEventListener('pointerdown', unlockAudio, { once: true });
        document.addEventListener('keydown', unlockAudio, { once: true });

        /* The header button reports the state; the panel changes it. Kept in
         * one function so the button, its label and the panel's own switch can
         * never disagree about whether sound is on. */
        function paintSoundState() {
            if (els.soundSettings) {
                els.soundSettings.classList.toggle('is-muted', !state.soundEnabled);
            }
            if (els.soundLabel) {
                els.soundLabel.textContent = state.soundEnabled ? 'Sound on' : 'Sound off';
            }
            var box = document.querySelector('[data-snd="mute"]');
            if (box) { box.checked = !state.soundEnabled; }
            var note = document.querySelector('[data-snd="note"]');
            if (note) {
                note.textContent = state.soundEnabled
                    ? 'Scan audio is on.'
                    : 'Scan audio is muted — the previews below still play.';
            }
        }

        function setSoundEnabled(on) {
            state.soundEnabled = !!on;
            paintSoundState();
        }
        paintSoundState();

        /* ── Sound test ────────────────────────────────────────────────────
         * Every outcome has its own cue, but the only way to hear one was to
         * make the outcome happen, so nobody could confirm on the floor that
         * a wrong batch and an expired item sound different. Each row plays
         * its cue on demand, muted or not — the point of the panel is to hear
         * it, so it overrides the mute rather than obeying it. */
        var SOUND_TESTS = [
            ['ok', 'Scanned', 'Accepted and assigned — two rising notes'],
            ['warn', 'Duplicate', 'Already packed, or already dispatched — one wobbling note'],
            ['mismatch', 'Wrong item', 'Wrong material or batch, or the line is full — two falling notes'],
            ['wrong_delivery', 'Wrong delivery', 'Not required on the focused delivery — three quick pips'],
            ['blocked', 'No delivery focused', 'Nothing was scanned — pick a delivery first — two slow buzzes'],
            ['danger', 'Rejected', 'Expired, over-scanned or already finished — two low buzzes'],
            ['neutral', 'Queued', 'Held offline, nothing decided — one flat note']
        ];

        function closeSoundPanel() {
            var open = document.querySelector('[data-mv="sound-panel"]');
            if (open && open.parentNode) { open.parentNode.removeChild(open); }
            if (els.soundSettings) { els.soundSettings.setAttribute('aria-expanded', 'false'); }
        }

        function openSoundPanel() {
            closeSoundPanel();
            var scrim = document.createElement('div');
            scrim.className = 'mv-modal-scrim is-open';
            scrim.setAttribute('data-mv', 'sound-panel');
            scrim.innerHTML =
                '<div class="mv-modal" role="dialog" aria-modal="true" aria-labelledby="mvSndTitle">' +
                '  <div class="mv-modal__head">' +
                '    <div><h2 class="mv-t-h2" id="mvSndTitle">Scan sounds</h2>' +
                '    <div class="mv-t-caption">One cue per outcome, four to choose from. ' +
                '      Pressing one plays it and keeps it on this machine.</div></div>' +
                '    <button type="button" class="mv-btn mv-btn--ghost mv-btn--icon" data-snd="close" ' +
                '      aria-label="Close"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" ' +
                '      stroke="currentColor" stroke-width="1.5" stroke-linecap="round">' +
                '      <path d="M6 6l12 12M18 6 6 18"/></svg></button>' +
                '  </div>' +
                '  <div class="mv-modal__body">' +
                '    <label class="mv-check mv-soundmute">' +
                '      <input type="checkbox" data-snd="mute">' +
                '      <span><span class="mv-t-body mv-t-strong">Mute scan sounds</span><br>' +
                '        <span class="mv-t-caption">Stops the cue after each scan. The banner and ' +
                '        the toast still appear, and the previews below still play.</span></span>' +
                '    </label>' +
                '    <div class="mv-field mv-soundvol">' +
                '      <label class="mv-label" for="mvSndVol">Volume</label>' +
                '      <div class="mv-soundvol__row">' +
                '        <input type="range" id="mvSndVol" data-snd="volume" ' +
                '          min="25" max="200" step="5">' +
                '        <output class="mv-mono" data-snd="volume-value"></output>' +
                '      </div>' +
                '      <span class="mv-hint">Applies to every cue and is remembered on this ' +
                '        machine. The top of the range is capped so the tone cannot distort.</span>' +
                '    </div>' +
                '    <div class="mv-soundlist" data-snd="list"></div>' +
                '  </div>' +
                '  <div class="mv-modal__foot">' +
                '    <span class="mv-t-caption" data-snd="note" style="margin-right:auto;"></span>' +
                '    <button type="button" class="mv-btn" data-snd="close">Close</button>' +
                '  </div>' +
                '</div>';

            var slider = scrim.querySelector('[data-snd="volume"]');
            var readout = scrim.querySelector('[data-snd="volume-value"]');
            function paintVolume() {
                readout.textContent = Math.round(state.volume * 100) + '%';
            }
            slider.value = String(Math.round(state.volume * 100));
            paintVolume();
            /* input repaints the number while dragging; change plays the cue
               once the operator lets go, so a drag is not a burst of beeps. */
            slider.addEventListener('input', function () {
                setVolume(parseInt(slider.value, 10) / 100);
                paintVolume();
            });
            slider.addEventListener('change', function () {
                unlockAudio();
                playTone('ok');
            });

            var list = scrim.querySelector('[data-snd="list"]');
            SOUND_TESTS.forEach(function (entry) {
                var family = entry[0];
                var row = document.createElement('div');
                row.className = 'mv-soundrow mv-soundrow--' + family;
                row.innerHTML =
                    '<span class="mv-reason mv-reason--' + (
                        family === 'ok' ? 'ok'
                        : family === 'warn' ? 'warn'
                        : family === 'neutral' ? 'neutral' : 'danger'
                    ) + '"></span>' +
                    '<div class="mv-soundrow__body"><div class="mv-t-strong"></div>' +
                    '<div class="mv-t-caption"></div>' +
                    '<div class="mv-soundpick" role="group"></div></div>';
                row.querySelector('.mv-reason').textContent = entry[1].toUpperCase();
                row.querySelector('.mv-t-strong').textContent = entry[1];
                row.querySelector('.mv-t-caption').textContent = entry[2];

                /* One button per alternative. Pressing it selects AND plays —
                 * choosing a sound you cannot hear at the moment of choosing is
                 * not choosing, and a separate preview button per option would
                 * be eight controls on a row that has to stay readable. */
                var picker = row.querySelector('.mv-soundpick');
                picker.setAttribute('aria-label', entry[1] + ' sound');
                (CUE_CHOICES[family] || []).forEach(function (choice) {
                    var id = choice[0], label = choice[1];
                    var button = document.createElement('button');
                    button.type = 'button';
                    button.className = 'mv-soundpick__opt';
                    button.dataset.snd = 'pick';
                    button.dataset.cue = id;
                    button.textContent = label;
                    button.setAttribute('aria-pressed', String(chosenCue(family) === id));
                    button.addEventListener('click', function () {
                        unlockAudio();
                        setCueChoice(family, id);
                        Array.prototype.forEach.call(
                            picker.querySelectorAll('[data-snd="pick"]'),
                            function (other) {
                                other.setAttribute('aria-pressed',
                                    String(other.dataset.cue === id));
                            }
                        );
                        /* The variant id, not the family: the choice has just
                           been stored, but hearing it must not depend on that
                           having worked — private mode swallows the write. */
                        playTone(id);
                    });
                    picker.appendChild(button);
                });
                list.appendChild(row);
            });

            scrim.querySelector('[data-snd="mute"]').addEventListener('change', function (e) {
                setSoundEnabled(!e.target.checked);
                if (state.soundEnabled) { unlockAudio(); playTone('ok'); }
            });
            /* Paints the switch and the footer note from the live state, so the
               panel opens showing the truth rather than a default. */
            paintSoundState();

            scrim.addEventListener('click', function (e) {
                if (e.target === scrim || e.target.closest('[data-snd="close"]')) { closeSoundPanel(); }
            });
            document.addEventListener('keydown', function esc_(e) {
                if (e.key !== 'Escape') { return; }
                document.removeEventListener('keydown', esc_);
                closeSoundPanel();
                refocusScanInput();
            });

            (document.querySelector('.mv') || document.body).appendChild(scrim);
            if (els.soundSettings) { els.soundSettings.setAttribute('aria-expanded', 'true'); }
            /* The mute switch, not the first sound button: opening the panel
               should not park the keyboard on a control that plays a noise the
               moment it is pressed by someone feeling their way with Enter. */
            var first = scrim.querySelector('[data-snd="mute"]');
            if (first) { first.focus(); }
        }

        if (els.soundSettings) {
            els.soundSettings.addEventListener('click', function () {
                if (document.querySelector('[data-mv="sound-panel"]')) { closeSoundPanel(); }
                else { openSoundPanel(); }
            });
        }

        /* ── Timer ───────────────────────────────────────────────────────── */
        function tickTimer() {
            if (!els.timer) { return; }
            var s = Math.max(0, Math.floor((Date.now() - state.startedAt) / 1000));
            var hh = Math.floor(s / 3600), mm = Math.floor((s % 3600) / 60), ss = s % 60;
            var pad = function (n) { return (n < 10 ? '0' : '') + n; };
            els.timer.textContent = (hh > 0 ? pad(hh) + ':' : '') + pad(mm) + ':' + pad(ss);
        }
        setInterval(tickTimer, 1000);
        tickTimer();

        /* ── Scan input focus ────────────────────────────────────────────
         * The one control that must never lose focus: a picker holding a gun
         * has no free hand for a mouse, so every path that moves focus away —
         * clicking a delivery card, switching view, closing a modal, or the
         * two-second repaint destroying the element that happened to have it —
         * has to give it back.
         *
         * Three cases are deliberately left alone: an open modal owns the
         * keyboard until it closes; a field the operator is deliberately
         * typing in is not interrupted; and in Bulk Paste mode the textarea
         * IS the scan target, so that is what gets focused instead. */
        function focusTarget() {
            if (state.bulkMode && els.bulkInput && els.bulkPanel && !els.bulkPanel.hidden) {
                return els.bulkInput;
            }
            return els.input;
        }

        function refocusScanInput() {
            var target = focusTarget();
            if (!target) { return; }
            if (document.querySelector('.mv-modal-scrim.is-open')) { return; }
            var active = document.activeElement;
            if (active === target) { return; }
            if (active && active !== document.body &&
                /^(INPUT|TEXTAREA|SELECT)$/.test(active.tagName)) { return; }
            /* preventScroll, or the keyboard watchdog becomes a scroll
               watchdog. This runs every second whenever nothing holds focus,
               and after every 2s repaint of the delivery panel — and a plain
               focus() scrolls its target into view. So an operator who scrolled
               down to read the delivery list was yanked back to the scan input
               a second later, over and over, which is the "it keeps jumping to
               the top" report. The keyboard still lands here; only the
               viewport is left alone.

               Not supported everywhere forever — Safari added it late — so the
               call is guarded and falls back to a plain focus() rather than
               throwing and leaving a gun typing into <body>. */
            try {
                target.focus({ preventScroll: true });
            } catch (err) {
                target.focus();
            }
            /* Caret at the end, not wherever focus() left it — a gun that
               fires mid-repaint would otherwise interleave its characters
               into the middle of a partly-typed code. */
            if (target.setSelectionRange && target.value) {
                try { target.setSelectionRange(target.value.length, target.value.length); }
                catch (err) { /* input types with no selection API */ }
            }
        }

        document.addEventListener('keydown', function (e) {
            if (e.target === els.input) { return; }
            if (document.querySelector('.mv-modal-scrim.is-open')) { return; }
            if (/^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) { return; }
            if (e.key.length === 1 || e.key === 'Backspace') { refocusScanInput(); }
        });

        /* Focus watchdog. A repaint of the delivery panel rebuilds whatever
           element the operator was on, dropping focus to <body>, where a
           scanned code goes nowhere at all and is simply lost. Only ever acts
           when nothing has focus, so it cannot steal the keyboard from a
           filter box or the finish form. */
        setInterval(function () {
            if (!document.activeElement || document.activeElement === document.body) {
                refocusScanInput();
            }
        }, 1000);
        /* Coming back from another tab or window lands on <body> too. */
        window.addEventListener('focus', refocusScanInput);
        document.addEventListener('mv:modal-closed', refocusScanInput);
        refocusScanInput();

        function setStatus(text) { if (els.status) { els.status.textContent = text; } }

        /* ── Result banner ───────────────────────────────────────────────────
         * The outcome of the last scan, in type large enough to read from the
         * far side of a packing bench. A toast is a corner of the screen and a
         * log row is one line of many; neither answers "did that go through?"
         * at arm's length, which is the question the operator is asking every
         * two seconds.
         *
         * Colour is never the only signal — each state also carries its own
         * word (SCANNED / DUPLICATE / REJECTED) and its own sound. */
        /* The word across the banner, which is what an operator reads from the
         * far side of the bench. `blocked` is its own word for its own reason:
         * it used to fall through to `warn`, so a scan refused for want of a
         * focused delivery announced itself as DUPLICATE. */
        var RESULT_LABEL = {
            ok: 'SCANNED', warn: 'DUPLICATE', danger: 'REJECTED',
            blocked: 'NOT SCANNED', neutral: 'QUEUED'
        };

        function showResult(family, headline, detail, serial) {
            if (!els.result) { return; }
            els.result.hidden = false;
            els.result.className = 'mv-scanresult mv-scanresult--' + family;
            els.result.querySelector('[data-mv="result-state"]').textContent =
                RESULT_LABEL[family] || RESULT_LABEL.neutral;
            els.result.querySelector('[data-mv="result-headline"]').textContent = headline || '';
            els.result.querySelector('[data-mv="result-detail"]').textContent = detail || '';
            var serialEl = els.result.querySelector('[data-mv="result-serial"]');
            serialEl.textContent = serial || '';
            serialEl.hidden = !serial;
            /* Re-trigger the flash on a repeat of the same outcome, which is
               otherwise indistinguishable from nothing having happened. */
            els.result.classList.remove('is-flash');
            void els.result.offsetWidth;
            els.result.classList.add('is-flash');
        }

        /* ── Scan error popup ──────────────────────────────────────────────
         * Every rejection gets one, and there is only ever ONE of them.
         *
         * The element is in the page once (see outbound_session.html) and each
         * error rewrites it, so a 2,000-code paste with 400 rejections cannot
         * open 400 popups — which is the crash this is guarding against. The
         * same rule makes a burst of single scans safe: the fifth rejection in
         * five seconds replaces the fourth rather than stacking on it.
         *
         * It does not take the keyboard. A real modal would — refocusScanInput
         * stands down while one is open — and a picker holding a gun would have
         * to put it down and click to carry on scanning.
         */
        function hideErrorPop() {
            if (els.errorPop) { els.errorPop.hidden = true; }
        }

        /* `detail` is the scrollable half: a list of {label, items} groups. One
         * scan passes a single group; a paste passes one per error type, which
         * is what folds "50 duplicates and 20 wrong deliveries" into one popup
         * with two headings instead of 70 popups.
         *
         * An item is either a plain code or {code, serial}. A single scan has
         * its serial on the line above (opts.serial) where it is unmissable; a
         * paste has one serial per rejected code and they belong beside the
         * codes, in the part that scrolls. */
        function showErrorPop(title, message, detail, hint, opts) {
            if (!els.errorPop) { return; }
            els.errorTitle.textContent = title || 'Scan Rejected';
            els.errorMessage.textContent = message || '';
            /* The serial the picker is holding. Hidden rather than emptied
               when there is none, so the popup does not keep a blank line
               where the last error's serial was. */
            if (els.errorSerial) {
                var serial = (opts && opts.serial) || '';
                els.errorSerial.hidden = !serial;
                els.errorSerial.textContent = serial ? ('Serial ' + serial) : '';
            }
            /* "Skip this one" appears on the errors that put the session on
               hold, which is every single-scan rejection: the same code cannot
               be rescanned until it is corrected or deliberately left out, and
               the way out of that belongs with the error that caused it rather
               than in a banner somewhere else on the page. */
            if (els.errorSkip) { els.errorSkip.hidden = !(opts && opts.skippable); }

            var groups = detail || [];
            els.errorDetail.innerHTML = '';
            els.errorDetail.hidden = !groups.length;
            groups.forEach(function (group) {
                var wrap = document.createElement('div');
                wrap.className = 'mv-errorpop__group';
                if (group.label) {
                    var head = document.createElement('div');
                    head.className = 'mv-errorpop__grouphead';
                    head.textContent = group.label +
                        (group.items && group.items.length > 1 ? ' (' + group.items.length + ')' : '');
                    wrap.appendChild(head);
                }
                (group.items || []).forEach(function (item) {
                    var line = document.createElement('div');
                    line.className = 'mv-errorpop__item mv-mono';
                    line.textContent = (item && item.code !== undefined) ? item.code : item;
                    if (item && item.serial) {
                        /* The serial under its own code, not appended to it:
                           a barcode wraps across several lines at this width,
                           and a serial tacked onto the end of one is lost in
                           the middle of a block of digits. */
                        var ser = document.createElement('span');
                        ser.className = 'mv-errorpop__itemserial';
                        ser.textContent = 'Serial ' + item.serial;
                        line.appendChild(ser);
                    }
                    wrap.appendChild(line);
                });
                els.errorDetail.appendChild(wrap);
            });

            if (els.errorHint) {
                els.errorHint.textContent = hint || 'Scan the next item to dismiss.';
            }
            /* The way out, when the rejection has one.
             *
             * Only SERIAL_IN_ANOTHER_SESSION offers this today: it tells the
             * operator to undo the serial in the session holding it, and the
             * landing page lists only the twenty newest sessions — so the one
             * they were sent to was usually unreachable. Opened in a new tab
             * deliberately: this session is mid-pick and must not be navigated
             * away from to go and fix another one. */
            if (els.errorAction) {
                var action = opts && opts.action;
                els.errorAction.hidden = !action;
                if (action) {
                    els.errorAction.textContent = action.label;
                    els.errorAction.href = action.href;
                }
            }
            els.errorPop.hidden = false;
            /* Re-run the entrance on a repeat of the same error, which is
               otherwise indistinguishable from the popup never having moved. */
            els.errorPop.classList.remove('is-in');
            void els.errorPop.offsetWidth;
            els.errorPop.classList.add('is-in');
        }

        document.addEventListener('click', function (e) {
            if (e.target.closest('[data-mv="error-skip"]')) {
                skipFailedScan();
                return;
            }
            if (e.target.closest('[data-mv="error-close"], [data-mv="error-dismiss"]')) {
                /* Dismiss hides the popup; it does not clear the hold. The
                   picker has read the error, not resolved it — and rescanning
                   the same code brings the popup straight back, so the hold is
                   never active with nothing on screen to explain it. */
                hideErrorPop();
                refocusScanInput();
            }
        });
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && els.errorPop && !els.errorPop.hidden) { hideErrorPop(); }
        });

        /* ── Scan log ──────────────────────────────────────────────────────
         * One target now. There were two — the console's runtime log and the
         * modal's filterable one — and every row had to be built twice and
         * kept in step. The "Check Logs" button that opened the modal, and the
         * enable-on-first-row dance behind it, went with it: a log that is
         * always on screen has nothing to open. */
        function scanLogTargets() {
            return els.scanLog ? [els.scanLog] : [];
        }

        function clearLogEmptyState(target) {
            var empty = target && target.querySelector('[data-mv-empty]');
            if (empty) { empty.remove(); }
        }

        /* Server time, not browser time.
         *
         * A live row used to be stamped with the picker's own clock while the
         * server-rendered rows carried the database's, so the same session
         * showed two different times for the same scan whenever a workstation
         * clock drifted — and after a refresh the row simply changed time.
         * The scan response carries the recorded timestamp; this is only the
         * fallback for rows that have none (undo, skip, grouped summaries). */
        function localStamp() {
            var now = new Date();
            var d = String(now.getDate()).padStart(2, '0');
            var m = now.toLocaleString('en-GB', { month: 'short' });
            return d + ' ' + m + ' ' + now.getFullYear() + ' ' +
                now.toLocaleTimeString('en-GB', { hour12: false });
        }

        function prependLogRow(entry) {
            var targets = scanLogTargets();
            if (!targets.length) { return; }
            var time = entry.time || localStamp();

            function buildRow() {
                var row = document.createElement('div');
                row.className = 'mv-scanrow mv-scanrow--' + entry.family + ' mv-scanrow--enter';
                // Same contract as the server-rendered rows, so the status filter in
                // datatable.js treats live scans identically. "accepted" tracks
                // whether the code is assigned right now, which is why an undo
                // prepends its own row rather than editing this one.
                row.dataset.status = entry.accepted ? 'accepted' : 'rejected';
                row.dataset.code = entry.raw || entry.code || '';
                /* Which delivery this row belongs to, so the log can be
                 * narrowed to the delivery being picked (applyLogScope). An
                 * accepted scan carries the line it landed on; anything else
                 * belongs to whatever was focused when it happened, which —
                 * since nothing may be scanned unfocused — is the delivery the
                 * operator was on. Matches the data-delivery the server writes
                 * onto the seeded rows. */
                row.dataset.delivery = entry.deliveryNo || focusedDelivery(lastPayload) || '';
                /* data-serial is the REPOINT key, not the display value:
                 * correcting a serial rewrites it wherever it appears
                 * (repointSerialRows). Only an assigned scan has one — a
                 * duplicate rejection names the serial it collided with and
                 * must keep naming it, because renaming the live scan does not
                 * change what was refused at the time. Matches the rule the
                 * server-rendered rows follow (_shell/scan_log.html). */
                if (entry.accepted && entry.serial) { row.dataset.serial = entry.serial; }
                row.innerHTML =
                    '<div class="mv-scanrow__time mv-mono">' + esc(time) + '</div>' +
                    '<div class="mv-scanrow__badge"></div>' +
                    '<div class="mv-scanrow__body">' +
                    '  <div class="mv-scanrow__code mv-mono"></div>' +
                    '  <div class="mv-scanrow__msg"></div>' +
                    '  <div class="mv-scanrow__serial mv-mono" hidden></div>' +
                    '</div>' +
                    '<div class="mv-scanrow__actions">' +
                    /* Two different questions, both of which have to be yes.
                       `entry.canEdit` is about the ROW — an accepted scan has a
                       serial to correct, a rejection has nothing. `state.canEdit`
                       is about the USER. Gating on the row alone is what drew
                       edit controls for a viewer, whose click answered with a
                       login redirect. */
                    (entry.canEdit && state.canEdit
                        ? '<button type="button" class="mv-btn mv-btn--ghost mv-btn--icon mv-scanrow__edit" ' +
                          'data-mv="edit-scan" data-serial="' + esc(entry.serial || '') + '" aria-label="Edit serial">' +
                          '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" ' +
                          'stroke-linecap="round" stroke-linejoin="round"><path d="M4 20h4L20 8l-4-4L4 16z"/></svg>' +
                          '</button>'
                        : '') +
                    (entry.canEdit && state.canDelete
                        ? '<button type="button" class="mv-btn mv-btn--ghost mv-btn--icon mv-scanrow__edit" ' +
                          'data-mv="delete-scan" data-serial="' + esc(entry.serial || '') + '" aria-label="Delete scan">' +
                          '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" ' +
                          'stroke-linecap="round" stroke-linejoin="round">' +
                          '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/></svg>' +
                          '</button>'
                        : '') +
                    '</div>';
                row.querySelector('.mv-scanrow__badge').innerHTML =
                    '<span class="mv-reason mv-reason--' + entry.family + '">' + esc(entry.reasonLabel) + '</span>';
                row.querySelector('.mv-scanrow__code').textContent = entry.code || '—';
                row.querySelector('.mv-scanrow__msg').textContent = entry.message || '';
                // The serial, spelled out on the row that produced it — "what
                // serial did this scan get?" was previously answerable only by
                // opening the line's serial list.
                //
                // `shownSerial` rather than `serial` because a REJECTION has
                // one too: nothing was assigned, but the label still named a
                // unit and that is what a picker looking at a refused row
                // needs. `serial` stays the assigned serial, which is the edit
                // key above, and the two are the same string on an accept.
                var serialEl = row.querySelector('.mv-scanrow__serial');
                var shown = entry.shownSerial || entry.serial;
                if (shown) {
                    serialEl.hidden = false;
                    serialEl.textContent = 'Serial ' + shown +
                        (entry.deliveryNo ? ('  ·  Delivery ' + entry.deliveryNo) : '');
                }
                return row;
            }

            targets.forEach(function (target) {
                var row = buildRow();
                clearLogEmptyState(target);
                target.insertBefore(row, target.firstChild);
                requestAnimationFrame(function () { row.classList.remove('mv-scanrow--enter'); });
            });

            state.logRowCount++;
            // No row cap here any more. It used to hide everything past the 50th
            // behind a "Show all" button, but the status filter also drives
            // row.hidden — two owners of one property meant a filtered view
            // could un-hide capped rows and vice versa. The server already caps
            // the feed, and filtering is a better answer to volume than
            // truncation, so the cap and its button are gone.
            if (ui.applyScanFilters) { ui.applyScanFilters(); }
        }

        /* One collapsed row standing in for many "wrong delivery" results from
         * a single bulk paste. Pasting a multi-delivery fixture while focused
         * on one delivery is expected to skip most of it — that is not the
         * same kind of problem as an expired item or a wrong batch, so it
         * gets a calmer colour and one line instead of a wall of identical
         * red rows repeating "not required on delivery X". */
        function prependGroupedMismatchRow(results, focusLabel) {
            var time = new Date().toLocaleTimeString([], { hour12: false });
            var targets = scanLogTargets();
            if (!targets.length || !results.length) { return; }
            var count = results.length;

            function buildRow() {
                var row = document.createElement('div');
                row.className = 'mv-scanrow mv-scanrow--neutral mv-scanrow--enter mv-scanrow--group';
                row.dataset.status = 'rejected';
                row.dataset.code = results.map(function (r) { return r.raw_scan || ''; }).join(' ');
                // Belongs to the delivery the paste was made against — it is
                // the summary of what that delivery would not take.
                row.dataset.delivery = focusedDelivery(lastPayload) || '';
                row.innerHTML =
                    '<div class="mv-scanrow__time mv-mono">' + esc(time) + '</div>' +
                    '<div class="mv-scanrow__badge"><span class="mv-reason mv-reason--neutral">NOT ON THIS DELIVERY</span></div>' +
                    '<div class="mv-scanrow__body">' +
                    '  <div class="mv-scanrow__code">' + count + ' scanned code' + (count === 1 ? '' : 's') + '</div>' +
                    '  <div class="mv-scanrow__msg">Not required on ' + esc(focusLabel || 'the focused delivery') +
                    '     - probably meant for a different delivery in this paste. ' +
                    '     <button type="button" class="mv-link-btn mv-t-caption" data-mv="toggle-group">Show codes</button>' +
                    '  </div>' +
                    '  <ul class="mv-scanrow__grouplist" hidden></ul>' +
                    '</div>';
                var list = row.querySelector('.mv-scanrow__grouplist');
                results.forEach(function (r) {
                    var li = document.createElement('li');
                    li.className = 'mv-mono';
                    li.textContent = r.raw_scan || '';
                    list.appendChild(li);
                });
                var toggle = row.querySelector('[data-mv="toggle-group"]');
                toggle.addEventListener('click', function () {
                    var open = !list.hidden;
                    list.hidden = open;
                    toggle.textContent = open ? 'Show codes' : 'Hide codes';
                });
                return row;
            }

            targets.forEach(function (target) {
                var row = buildRow();
                clearLogEmptyState(target);
                target.insertBefore(row, target.firstChild);
                requestAnimationFrame(function () { row.classList.remove('mv-scanrow--enter'); });
            });
            state.logRowCount++;
            if (ui.applyScanFilters) { ui.applyScanFilters(); }
        }

        /* ── Focus, the picking lock ───────────────────────────────────────
         * Nothing may be scanned or assigned until the operator has said which
         * delivery they are on. The server enforces it (views._focus_gate); the
         * console's job is to make that visible before a picker fires a gun
         * into a refusal, and to narrow everything on screen — the delivery
         * list, the line table and the scan log — to the delivery they named.
         *
         * `can_scan` comes from the server rather than being re-derived here,
         * because customer focus counts only for entities configured to allow
         * it, and two readings of that rule would eventually disagree. A null
         * payload means the first poll has not landed yet: let the request
         * through and let the server answer, rather than refusing on a state we
         * do not have. */
        function canScan() {
            return !lastPayload || !!lastPayload.can_scan;
        }

        /* The delivery everything on screen is narrowed to, or '' for none.
         * Customer focus deliberately does not narrow to a delivery — it names
         * a customer, which is usually several — so it scopes the delivery list
         * (see scopeToFocus) but leaves the per-delivery log unscoped. */
        function focusedDelivery(payload) {
            return (payload && payload.focus_delivery) || '';
        }

        /* Clear Scans acts on the focused delivery, so it is only live while
         * one is focused and has something to clear. Left disabled otherwise
         * rather than hidden, so the control does not move around under the
         * operator between ticks. */
        function renderClearScansBtn(payload) {
            if (!els.clearScansBtn) { return; }
            var delivery = focusedDelivery(payload);
            var scanned = 0;
            var list = (payload && payload.deliveries) || [];
            for (var i = 0; i < list.length; i++) {
                if (list[i].delivery_no === delivery) { scanned = Number(list[i].scanned) || 0; }
            }
            var finished = false;
            for (var j = 0; j < list.length; j++) {
                if (list[j].delivery_no === delivery) { finished = !!list[j].is_finished; }
            }
            /* Without the capability the control is not merely disabled —
               it is not a control this user has, so hiding it is the honest
               rendering and matches what the server will answer. */
            els.clearScansBtn.hidden = !state.canClear;
            els.clearScansBtn.disabled = !delivery || scanned <= 0 || finished;
            els.clearScansBtn.title = !delivery
                ? 'Focus a delivery to clear its scans'
                : (finished
                    ? 'Delivery ' + delivery + ' is finished — its scans are locked'
                    : (scanned <= 0
                        ? 'Delivery ' + delivery + ' has no scans'
                        : 'Clear all ' + scanned + ' scan(s) on ' + delivery));
        }

        /* ── State reconciliation (deliveries, totals, focus) ─────────────── */
        function renderState(payload) {
            if (!payload) { return; }
            /* What each closed delivery actually recorded, published for the
               Finish modal. It reads the delivery LIST off the cards' data
               attributes (see focusable), which is the right shape for four
               scalars and the wrong one for a dozen paperwork fields and six
               box counts — so this one thing comes across as an object. */
            window.mvFinishedPaperwork = payload.finished_detail || {};
            if (els.totals) {
                var all = payload.deliveries || [];
                var shown = scopeToFocus(all, payload);
                /* Both numbers, when they differ. The footer counted the whole
                   session while the list beside it can now show one delivery,
                   and a footer reading "24" over a list of one is read as the
                   list having lost something. */
                els.totals.textContent =
                    'Total Deliveries: ' + all.length +
                    '   Completed: ' + all.filter(function (d) { return d.complete; }).length +
                    (shown.length === all.length
                        ? ''
                        : '   ·   Showing ' + shown.length + ' (focused)');
            }
            renderProgress(payload);
            if (els.deliveries) { renderDeliveries(payload.deliveries || [], payload); }
            if (els.focusBanner) { renderFocusBanner(payload); }
            renderFocusPrompt(payload);
            renderScopeBar(payload);
            renderInvoiceBtn(payload);
            renderClearScansBtn(payload);
            applyLogScope(payload);
            /* The serials modal outlives the table that opened it (it hangs off
             * <body>, while the table is rebuilt every tick), so it has to be
             * repainted or it quietly shows a two-second-old list. */
            paintSerialsModal(payload);
        }

        /* Total / Scanned / Pending, on the rail, auto-updating.
         *
         * `payload.totals` has been computed server-side since the console was
         * written (_session_payload in merilv/views.py) and nothing displayed
         * it: the only running number on the screen was a count of DELIVERIES
         * in the footer, which answers a different question from "how many
         * units are left to pick". The template renders the same three numbers
         * for first paint, so they are right before this ever runs rather than
         * flashing zeroes on load.
         *
         * Whole units where they are whole — a pick list counts pieces, and
         * "12.00 pending" reads as a rounding error rather than twelve
         * cartons — but a genuine fraction is never hidden. */
        /* `unitText`, not `qtyText`: the delivery cards already own a
           qtyText() further down, and two function declarations of one name in
           this scope silently resolve to the later of them. */
        function unitText(value) {
            var n = Number(value || 0);
            if (!isFinite(n)) { return '0'; }
            return n % 1 === 0 ? String(n) : n.toFixed(2);
        }

        function renderProgress(payload) {
            var t = (payload && payload.totals) || {};
            if (els.qtyRequired) { els.qtyRequired.textContent = unitText(t.required); }
            if (els.qtyScanned) { els.qtyScanned.textContent = unitText(t.scanned); }
            if (els.qtyPending) { els.qtyPending.textContent = unitText(t.pending); }
            var pct = Math.max(0, Math.min(100, Number(t.percent || 0)));
            if (els.progressPct) { els.progressPct.textContent = Math.round(pct) + '%'; }
            if (els.progressFill) { els.progressFill.style.width = pct + '%'; }
        }

        /* The invoice control, which is only ever about the focused delivery.
         *
         * Its whole content — docket, box count, weight, vehicle — is read off
         * the FinishedDelivery row, so before a delivery is finished there is
         * no thinner sheet to give, only no sheet. Rather than let it download
         * fourteen empty columns, the control goes quiet and says which of the
         * two steps is outstanding when it is pressed.
         *
         * Pressed, not ignored: a link that does nothing on click reads as
         * broken, and "not yet, because X" is the useful half of the answer. */
        function renderInvoiceBtn(payload) {
            var btn = els.invoiceBtn;
            if (!btn) { return; }
            var focused = focusedDelivery(payload);
            var delivery = focused
                ? (payload.deliveries || []).filter(function (d) {
                    return d.delivery_no === focused;
                })[0]
                : null;
            var ready = !!(delivery && delivery.invoiceable);

            btn.classList.toggle('is-off', !ready);
            btn.setAttribute('aria-disabled', String(!ready));
            btn.textContent = '';
            btn.appendChild(document.createTextNode(focused ? ('Invoice ' + focused) : 'Invoice'));
            /* The href stays correct whatever the state: a middle-click or a
               copied link should still reach the endpoint, which does its own
               checking and answers the same way. */
            btn.href = cfg.invoiceUrl + (focused ? ('?delivery_no=' + encodeURIComponent(focused)) : '');
            btn.dataset.mvInvoiceOff = ready
                ? ''
                : (!focused ? 'nofocus' : (delivery && delivery.finished ? 'pending_courier' : 'unfinished'));
            btn.title = ready
                ? ('Invoice sheet for delivery ' + focused)
                : 'Not available yet — press for the reason.';
        }

        /* Why it is off, in the operator's terms. The server says the same
         * thing when the endpoint is reached directly; this saves the trip. */
        var INVOICE_NOT_READY = {
            nofocus: ['No delivery focused',
                      'Focus the delivery you want the invoice for.'],
            unfinished: ['Delivery not finished',
                         'The invoice sheet is the dispatch record — the docket, the box ' +
                         'count, the weight and the vehicle. None of it exists until the ' +
                         'delivery is finished.'],
            pending_courier: ['Courier details outstanding',
                              'This delivery is finished but its dispatch paperwork is not ' +
                              'in. An invoice with no docket, vehicle or weight is a sheet ' +
                              'SAP will reject.']
        };

        if (els.invoiceBtn) {
            els.invoiceBtn.addEventListener('click', function (e) {
                var why = els.invoiceBtn.dataset.mvInvoiceOff;
                if (!why) { return; }          /* ready — let the download run */
                e.preventDefault();
                var said = INVOICE_NOT_READY[why] || INVOICE_NOT_READY.unfinished;
                playCue('blocked');
                showErrorPop(said[0], said[1], [], 'Finish the delivery to enable it.');
            });
        }

        /* Say, on the screen itself, that nothing can be scanned yet.
         *
         * Without this the only feedback is the refusal that comes back after
         * the gun has already fired, which reads as a broken scanner rather
         * than a step not taken.
         *
         * The input stays ENABLED while blocked, deliberately. A disabled box
         * swallows a gun's keystrokes into <body> and answers a scan with
         * nothing at all, which is the exact shape of "the scanner is broken";
         * an enabled one takes the code, and Enter answers with the banner, the
         * toast and the sound that name the missing step (see
         * refuseUnfocused). The placeholder and the tint say so before that. */
        function renderFocusPrompt(payload) {
            var blocked = !(payload && payload.can_scan);
            if (els.focusPrompt) { els.focusPrompt.hidden = !blocked; }
            if (els.input) {
                els.input.placeholder = blocked ? 'SELECT A DELIVERY FIRST' : 'SCAN BARCODE';
                els.input.classList.toggle('is-blocked', blocked);
            }
            if (blocked) {
                setStatus('Select a delivery on the right, then scan.');
            } else if (state.blockedLastPaint) {
                setStatus('Ready to scan. Keyboard input is focused.');
                refocusScanInput();
            }
            state.blockedLastPaint = blocked;
        }

        /* Narrow the scan log to the focused delivery.
         *
         * The scope is written onto the log container and read back by the
         * filter that already owns `row.hidden` (wireScanFilter in
         * datatable.js) — two owners of one property is exactly what made the
         * status filter and the old row cap fight each other, so this sets the
         * attribute and lets that one filter act on it. */
        function applyLogScope(payload) {
            if (els.scanLog) {
                els.scanLog.dataset.mvScopeDelivery = focusedDelivery(payload);
            }
            if (ui.applyScanFilters) { ui.applyScanFilters(); }
        }

        function renderFocusBanner(payload) {
            var key = payload.focus_delivery || payload.focus_customer;
            if (!key) { els.focusBanner.hidden = true; return; }
            els.focusBanner.hidden = false;
            var kind = payload.focus_delivery ? 'Delivery' : 'Customer';
            els.focusBanner.querySelector('[data-mv="focus-key"]').textContent = kind + ': ' + key;

            /* Row 2.4 — "Focus Pending", scoped to the focused delivery (or
             * every delivery under the focused customer), not the session
             * total — the legacy screen's own reading of "how much is left
             * on THIS thing I'm working on right now". */
            var pendingEl = els.focusBanner.querySelector('[data-mv="focus-pending"]');
            if (pendingEl) {
                var deliveries = payload.deliveries || [];
                var scoped = payload.focus_delivery
                    ? deliveries.filter(function (d) { return d.delivery_no === payload.focus_delivery; })
                    : deliveries.filter(function (d) { return d.customer_code === payload.focus_customer; });
                if (scoped.length) {
                    var pending = scoped.reduce(function (sum, d) { return sum + Number(d.pending || 0); }, 0);
                    var required = scoped.reduce(function (sum, d) { return sum + Number(d.required || 0); }, 0);
                    pendingEl.hidden = false;
                    pendingEl.textContent = 'Focus Pending: ' + Math.round(pending) + ' / ' + Math.round(required);
                } else {
                    pendingEl.hidden = true;
                }
            }
        }

        function ringMarkup(pct, complete, id) {
            var r = 20, c = 2 * Math.PI * r;
            var off = c - (Math.max(0, Math.min(100, pct)) / 100) * c;
            return (
                '<div class="mv-ring' + (complete ? ' is-complete' : '') + '">' +
                '  <svg viewBox="0 0 48 48">' +
                '    <circle class="mv-ring__track" cx="24" cy="24" r="' + r + '" fill="none" stroke-width="4"/>' +
                '    <circle class="mv-ring__bar" cx="24" cy="24" r="' + r + '" fill="none" stroke-width="4" ' +
                '      stroke-linecap="round" stroke-dasharray="' + c + '" stroke-dashoffset="' + off + '"/>' +
                '  </svg>' +
                '  <div class="mv-ring__text">' + Math.round(pct) + '%</div>' +
                '</div>'
            );
        }

        function progressBar(pct, complete) {
            var w = Math.max(0, Math.min(100, pct || 0));
            return '<div class="mv-progress"><div class="mv-progress__bar'
                + (complete ? ' mv-progress__bar--ok' : '') + '" style="width:' + w + '%"></div></div>';
        }

        /* Every view marks the focused delivery and dims completed ones, and
         * every row/card is a <button> that focuses on click — the view only
         * changes density, never what the thing does. */
        function stateOf(d, payload) {
            var focused = payload.focus_delivery === d.delivery_no;
            /* FINISHED outranks the rest, and is not the same as COMPLETE.
             * Complete says every line reached its quantity; finished says the
             * paperwork is recorded and the delivery refuses further scans. A
             * delivery can be complete and not finished, and — shipped short on
             * purpose — finished without being complete.
             *
             * It matters more now that finishing keeps the operator on this
             * screen: the card is the only thing that says the one they just
             * closed is closed. */
            var finished = !!d.finished;
            return {
                focused: focused,
                finished: finished,
                complete: !!d.complete,
                started: Number(d.scanned) > 0,
                pillFamily: finished ? 'ok' : (d.complete ? 'ok' : (focused ? 'primary' : 'neutral')),
                pillText: finished
                    ? 'FINISHED'
                    : (d.complete ? 'COMPLETE' : (focused ? 'FOCUSED' : 'PENDING'))
            };
        }

        function focusable(tag, cls, d) {
            var node = document.createElement(tag);
            if (tag === 'button') { node.type = 'button'; }
            /* A card is a <div role="button"> rather than a real <button>
               because it now contains its own controls (Lines, Export), and a
               button inside a button is invalid markup that browsers silently
               reflow. The keyboard contract is kept by hand below, and the
               element position is unchanged so the delivery list's nth-child
               accent colours still land on the same cards. */
            if (tag === 'div') {
                node.setAttribute('role', 'button');
                node.tabIndex = 0;
                node.addEventListener('keydown', function (e) {
                    if (e.key !== 'Enter' && e.key !== ' ') { return; }
                    if (e.target !== node) { return; }
                    e.preventDefault();
                    postFocus(d.delivery_no);
                });
            }
            node.className = cls;
            /* finish_modal.html's own 2s tick reads delivery state off these
             * elements instead of making a second request. It reads these DATA
             * ATTRIBUTES, never the rendered text — the text layout differs per
             * view (card/list/table/kanban), so scraping it would break the
             * Finish Delivery dropdown the moment the view changed. Every view's
             * root element must therefore carry all four. */
            node.setAttribute('data-mv', 'delivery-card');
            node.dataset.deliveryNo = d.delivery_no;
            node.dataset.scanned = d.scanned;
            node.dataset.required = d.required;
            node.dataset.complete = d.complete ? '1' : '0';
            /* Closed. The Finish modal greys its form for one of these and
               fills it from what was recorded, rather than offering an empty
               form that reads as an invitation to finish it a second time. */
            node.dataset.finished = d.finished ? '1' : '0';
            /* The courier this delivery resolves to, so Finish Delivery can
               pre-fill it. Carried here rather than fetched by the modal for
               the same reason as the four above: the console already polls
               this payload, and the modal reads these attributes. */
            node.dataset.courier = d.courier || '';
            node.dataset.courierNumber = d.courier_number || '';
            /* Vehicle and type ride along too: courier names are not unique
               across plants, so the modal cannot safely look the row up by
               name in the datalist. */
            node.dataset.courierVehicle = d.courier_vehicle || '';
            node.dataset.courierVehicleType = d.courier_vehicle_type || '';
            node.dataset.courierSource = d.courier_source || '';
            /* Mode of transit for this delivery's carrier, so Finish Delivery
               can preselect it instead of defaulting every dispatch to Road. */
            node.dataset.courierMode = d.courier_mode || '';
            /* Customer code and name, so the Finish Delivery dropdown can read
               "8001234567 - CUST001 - ABC Healthcare" without a second
               request — it already scrapes these attributes every 2s. */
            node.dataset.customerCode = d.customer_code || '';
            node.dataset.customerName = d.customer_name || '';
            node.addEventListener('click', function (e) {
                /* A control drawn inside the card acts for itself. */
                if (e.target.closest('[data-mv-card-action]')) { return; }
                postFocus(d.delivery_no);
            });
            return node;
        }

        /* The per-delivery controls that sit on a card: open just this
         * delivery's lines, and export just this delivery's serial sheet.
         * Both are scoped to one delivery — the whole point of the request. */
        function deliveryActions(d) {
            var wrap = document.createElement('div');
            wrap.className = 'mv-delivery-card__actions';

            var lines = document.createElement('button');
            lines.type = 'button';
            lines.className = 'mv-btn mv-btn--sm';
            lines.setAttribute('data-mv-card-action', 'lines');
            lines.textContent = 'Lines' + (d.line_count ? ' (' + d.line_count + ')' : '');
            lines.title = 'Show only this delivery\u2019s line items';
            lines.addEventListener('click', function () { openDeliveryLines(d.delivery_no); });
            wrap.appendChild(lines);

            if (cfg.exportUrl) {
                var exp = document.createElement('a');
                exp.className = 'mv-btn mv-btn--sm';
                exp.setAttribute('data-mv-card-action', 'export');
                exp.href = cfg.exportUrl + '?delivery_no=' + encodeURIComponent(d.delivery_no);
                exp.textContent = 'Export';
                exp.title = 'Download the serial sheet for delivery ' + d.delivery_no + ' only';
                /* The browser navigates to a download; the console must not
                   lose the keyboard when it comes back. */
                exp.addEventListener('click', function () { setTimeout(refocusScanInput, 400); });
                wrap.appendChild(exp);
            }
            if (cfg.deliveryDeleteUrl && state.canClear && !d.finished) {
                var del = document.createElement('button');
                del.type = 'button';
                del.className = 'mv-btn mv-btn--sm mv-btn--icon mv-btn--danger';
                del.setAttribute('data-mv-card-action', 'delete-delivery');
                del.setAttribute('aria-label', 'Delete delivery ' + d.delivery_no + ' from this session');
                del.innerHTML =
                    '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" ' +
                    'stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">' +
                    '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/></svg>';
                del.title = 'Remove delivery ' + d.delivery_no + ' from this session';
                del.addEventListener('click', function () { confirmDeleteDelivery(d); });
                wrap.appendChild(del);
            }
            return wrap;
        }

        /* Focus the delivery and switch to the per-line view, which is what
         * "open this delivery" means on this screen: the Lines view shows only
         * the focused delivery's items (see renderLinesView). */
        function openDeliveryLines(deliveryNo) {
            state.view = 'lines';
            try { window.localStorage.setItem(VIEW_KEY, 'lines'); } catch (err) { /* non-fatal */ }
            applyView();
            resetPaging();
            postFocus(deliveryNo, { keepIfSame: true });
        }

        function qtyText(d) { return d.scanned + ' / ' + d.required + ' scanned'; }

        /* "CUST001 - ABC Healthcare", or whichever half exists. A delivery
         * number on its own does not tell a picker whose stock they are
         * packing, which is what the customer-details request is about. */
        function customerLabel(d) {
            return [d.customer_code, d.customer_name].filter(Boolean).join(' - ');
        }

        function renderCardView(list, payload) {
            els.deliveries.className = 'mv-delivery-list';
            list.forEach(function (d) {
                var s = stateOf(d, payload);
                var card = focusable('div', 'mv-delivery-card'
                    + (s.focused ? ' is-focused' : '') + (s.complete ? ' is-complete' : '') + (s.finished ? ' is-finished' : ''), d);
                card.innerHTML =
                    ringMarkup(d.percent || 0, s.complete) +
                    '<div class="mv-delivery-card__body">' +
                    '  <div class="mv-delivery-card__head">' +
                    '    <span class="mv-mono mv-t-strong"></span>' +
                    '    <span class="mv-badge mv-badge--' + s.pillFamily + '">' + s.pillText + '</span>' +
                    '  </div>' +
                    '  <div class="mv-delivery-card__customer"></div>' +
                    '  <div class="mv-delivery-card__qty mv-t-caption"></div>' +
                    '</div>' +
                    '<svg class="mv-delivery-card__chevron" viewBox="0 0 24 24" width="16" height="16" ' +
                    'fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" ' +
                    'stroke-linejoin="round"><path d="M9 6l6 6-6 6"/></svg>';
                card.querySelector('.mv-delivery-card__head .mv-mono').textContent = d.delivery_no;
                card.querySelector('.mv-delivery-card__customer').textContent = d.customer_name || '';
                // Plant belongs on the qty line rather than in its own row: it is
                // the second thing a picker checks after the delivery number, and
                // the card has no spare vertical space at this density.
                card.querySelector('.mv-delivery-card__qty').textContent =
                    d.plant ? (d.plant + ' · ' + qtyText(d)) : qtyText(d);
                // "Delivery — customer code — customer name", the same shape
                // the Finish Delivery dropdown uses, so the two screens name a
                // delivery identically.
                card.querySelector('.mv-delivery-card__customer').textContent = customerLabel(d);
                /* Clicking the customer filters the list to every delivery
                 * they have. A customer with several orders is picked as one
                 * job, and finding the rest by eye against the dump is what
                 * this replaces. data-mv-card-action keeps the click off the
                 * card's own focus handler. */
                if (d.customer_code) {
                    var cust = card.querySelector('.mv-delivery-card__customer');
                    cust.setAttribute('data-mv-card-action', 'customer');
                    cust.setAttribute('role', 'button');
                    cust.tabIndex = 0;
                    cust.title = 'Show every delivery for ' + d.customer_code;
                    cust.style.cursor = 'pointer';
                    cust.addEventListener('click', function () { filterByCustomer(d.customer_code); });
                    cust.addEventListener('keydown', function (e) {
                        if (e.key !== 'Enter' && e.key !== ' ') { return; }
                        e.preventDefault();
                        filterByCustomer(d.customer_code);
                    });
                }
                card.querySelector('.mv-delivery-card__body').appendChild(deliveryActions(d));
                els.deliveries.appendChild(card);
            });
        }

        function renderTableView(list, payload) {
            els.deliveries.className = 'mv-table-wrap mv-table-wrap--paged';
            var table = document.createElement('table');
            table.className = 'mv-table mv-table--dense';
            table.innerHTML =
                '<thead><tr>' +
                '<th style="width:32px;"></th><th>Delivery</th><th>Customer</th><th>Plant</th>' +
                '<th class="mv-num">Required</th><th class="mv-num">Scanned</th><th class="mv-num">Pending</th>' +
                '<th class="mv-num">Progress</th><th>Status</th><th></th>' +
                '</tr></thead><tbody></tbody>';
            var tbody = table.querySelector('tbody');
            var meta = paginate(list, 'table');
            meta.rows.forEach(function (d) {
                var s = stateOf(d, payload);
                var tr = focusable('tr', (s.focused ? 'is-focused' : ''), d);
                tr.style.cursor = 'pointer';
                // Row 2.4 — select for bulk finish. One innerHTML assignment
                // for the whole row, built as a single string: an interim
                // appendChild() followed by `+=` would serialise-and-reparse
                // the row, discarding the checkbox's own listener before it
                // was ever wired up.
                tr.innerHTML =
                    '<td style="width:32px;"><input type="checkbox" class="mv-chk" data-mv-bulk-select></td>' +
                    '<td class="mv-mono mv-t-strong"></td><td></td><td class="mv-mono"></td>' +
                    '<td class="mv-num"></td><td class="mv-num"></td><td class="mv-num"></td>' +
                    '<td class="mv-num mv-mono"></td>' +
                    '<td><span class="mv-badge mv-badge--' + s.pillFamily + '">' + s.pillText + '</span></td>' +
                    '<td class="mv-cell-actions"></td>';
                var c = tr.children;
                c[1].textContent = d.delivery_no;
                c[2].textContent = customerLabel(d) || '—';
                c[3].textContent = d.plant || '—';
                c[4].textContent = d.required;
                c[5].textContent = d.scanned;
                c[6].textContent = d.pending;
                c[7].textContent = Math.round(d.percent || 0) + '%';

                // Same per-delivery controls as a card: this view is the one a
                // supervisor works from, and "export just this one" has to be
                // reachable from whichever view is open.
                c[9].appendChild(deliveryActions(d));
                c[9].addEventListener('click', function (e) { e.stopPropagation(); });

                var checkbox = c[0].querySelector('input');
                checkbox.checked = state.selectedDeliveries.has(d.delivery_no);
                c[0].addEventListener('click', function (e) { e.stopPropagation(); });
                checkbox.addEventListener('change', function () {
                    if (checkbox.checked) { state.selectedDeliveries.add(d.delivery_no); }
                    else { state.selectedDeliveries.delete(d.delivery_no); }
                    renderBulkFinishBar();
                });
                tbody.appendChild(tr);
            });
            mountTable(table);
            appendPager(meta, 'table', 'deliveries');
        }

        function renderKanbanView(list, payload) {
            els.deliveries.className = 'mv-kanban';
            /* Grouped by how far along the pick is — the question a supervisor
             * actually asks of this board ("what hasn't been started?"). */
            var cols = [
                { key: 'pending',  title: 'Not started', mod: 'pending',  items: [] },
                { key: 'progress', title: 'In progress', mod: 'progress', items: [] },
                { key: 'complete', title: 'Complete',    mod: 'complete', items: [] }
            ];
            list.forEach(function (d) {
                var s = stateOf(d, payload);
                if (s.complete) { cols[2].items.push(d); }
                else if (s.started) { cols[1].items.push(d); }
                else { cols[0].items.push(d); }
            });

            cols.forEach(function (col) {
                var wrap = document.createElement('div');
                wrap.className = 'mv-kanban__col mv-kanban__col--' + col.mod;
                wrap.innerHTML =
                    '<div class="mv-kanban__head">' +
                    '  <span class="mv-kanban__title"></span>' +
                    '  <span class="mv-badge mv-badge--neutral">' + col.items.length + '</span>' +
                    '</div><div class="mv-kanban__body"></div>';
                wrap.querySelector('.mv-kanban__title').textContent = col.title;
                var body = wrap.querySelector('.mv-kanban__body');

                if (!col.items.length) {
                    var empty = document.createElement('div');
                    empty.className = 'mv-kanban__empty';
                    empty.textContent = 'Nothing here.';
                    body.appendChild(empty);
                }
                col.items.forEach(function (d) {
                    var s = stateOf(d, payload);
                    var card = focusable('button', 'mv-kancard'
                        + (s.focused ? ' is-focused' : '') + (s.complete ? ' is-complete' : '') + (s.finished ? ' is-finished' : ''), d);
                    card.innerHTML =
                        '<div class="mv-kancard__top">' +
                        '  <span class="mv-mono mv-t-strong"></span>' +
                        (s.focused ? '<span class="mv-badge mv-badge--primary">FOCUSED</span>' : '') +
                        '</div>' +
                        '<div class="mv-kancard__cust"></div>' +
                        progressBar(d.percent, s.complete) +
                        '<div class="mv-kancard__foot"><span class="mv-mono"></span><span></span></div>';
                    card.querySelector('.mv-kancard__top .mv-mono').textContent = d.delivery_no;
                    // Customer and plant share this line — a kanban card is the
                    // densest of the four views and has no room for another row.
                    var cust = customerLabel(d);
                    card.querySelector('.mv-kancard__cust').textContent =
                        d.plant ? ((cust || '—') + ' · ' + d.plant) : cust;
                    var foot = card.querySelectorAll('.mv-kancard__foot span');
                    foot[0].textContent = d.scanned + '/' + d.required;
                    foot[1].textContent = Math.round(d.percent || 0) + '%';
                    body.appendChild(card);
                });
                els.deliveries.appendChild(wrap);
            });
        }

        /* The table gets its own scroll box rather than riding the page's:
         * that is what lets the header stick to the top of the rows and the
         * pager stay parked at the bottom of the panel, instead of both
         * scrolling off while the operator is still reading rows. */
        function mountTable(table) {
            var scroller = document.createElement('div');
            scroller.className = 'mv-table-scroll';
            scroller.appendChild(table);
            els.deliveries.appendChild(scroller);
            return scroller;
        }

        /* -- Paging, shared by the Table and Lines views ---------------------
         * Lines has one row per material line, so a single 40-line delivery
         * already fills the screen and a full session runs into the hundreds.
         * The whole table used to render at once, which made the last row
         * reachable only by a very long scroll and pushed the session totals
         * off the bottom of the page.
         *
         * Page size is remembered because it is a property of the operator's
         * monitor rather than of the session. */
        var PAGE_SIZE_KEY = 'merilv.outbound.pageSize';
        var PAGE_SIZES = [25, 50, 100, 200];
        state.pageSize = (function () {
            try {
                var saved = parseInt(window.localStorage.getItem(PAGE_SIZE_KEY), 10);
                return PAGE_SIZES.indexOf(saved) === -1 ? 25 : saved;
            } catch (err) { return 25; }
        }());
        state.page = { table: 1, lines: 1 };

        /* Clamps as well as slices. A poll tick that removes rows -- or a
         * tightened filter -- can leave state.page past the end, which would
         * otherwise paint an empty table with no control left to get back. */
        function paginate(rows, viewKey) {
            var total = rows.length;
            var pages = Math.max(1, Math.ceil(total / state.pageSize));
            var page = Math.min(Math.max(1, state.page[viewKey] || 1), pages);
            state.page[viewKey] = page;
            var from = (page - 1) * state.pageSize;
            var to = Math.min(from + state.pageSize, total);
            return {
                rows: rows.slice(from, to), page: page, pages: pages,
                total: total, from: total ? from + 1 : 0, to: to
            };
        }

        /* One page of rows still gets the bar: "is this everything?" is the
         * first question any truncated table raises, so the count shows even
         * when the arrows would be dead. */
        function appendPager(meta, viewKey, noun) {
            var pager = document.createElement('div');
            pager.className = 'mv-pager';
            /* An empty result already says so in the table body; repeating
             * it as "Showing 0-0 of 0 lines" is just noise. */
            var info = meta.total
                ? ('Showing <strong>' + meta.from + '–' + meta.to +
                   '</strong> of <strong>' + meta.total + '</strong> ' + noun)
                : ('No ' + noun + ' to show');
            pager.innerHTML =
                '<span class="mv-pager__info">' + info + '</span>' +
                (meta.pages > 1
                    ? '<span class="mv-pager__nav">' +
                      '<button type="button" class="mv-pager__btn" data-pg="first" aria-label="First page">«</button>' +
                      '<button type="button" class="mv-pager__btn" data-pg="prev" aria-label="Previous page">‹</button>' +
                      '<span class="mv-pager__count">Page <strong>' + meta.page + '</strong> / ' + meta.pages + '</span>' +
                      '<button type="button" class="mv-pager__btn" data-pg="next" aria-label="Next page">›</button>' +
                      '<button type="button" class="mv-pager__btn" data-pg="last" aria-label="Last page">»</button>' +
                      '</span>'
                    : '') +
                '<label class="mv-pager__size">Rows' +
                '<select data-pg="size" aria-label="Rows per page">' +
                PAGE_SIZES.map(function (n) {
                    return '<option value="' + n + '"' + (n === state.pageSize ? ' selected' : '') + '>' + n + '</option>';
                }).join('') +
                '</select></label>';

            /* Clamped, not raw: an unclamped prev on page 1 is 0 and an
             * unclamped next on the last page is pages+1, neither of which
             * equals meta.page — so the disabled check below let both ends
             * stay live and clickable while doing nothing. */
            var jumps = {
                first: 1,
                prev: Math.max(1, meta.page - 1),
                next: Math.min(meta.pages, meta.page + 1),
                last: meta.pages
            };
            Array.prototype.forEach.call(pager.querySelectorAll('[data-pg]'), function (el) {
                var kind = el.dataset.pg;
                if (kind === 'size') {
                    el.addEventListener('change', function () {
                        state.pageSize = parseInt(el.value, 10) || 25;
                        try { window.localStorage.setItem(PAGE_SIZE_KEY, String(state.pageSize)); }
                        catch (err) { /* non-fatal */ }
                        state.page[viewKey] = 1;
                        renderDeliveries(lastPayload && lastPayload.deliveries, lastPayload);
                    });
                    return;
                }
                if (jumps[kind] === meta.page) { el.disabled = true; return; }
                el.addEventListener('click', function () {
                    state.page[viewKey] = jumps[kind];
                    renderDeliveries(lastPayload && lastPayload.deliveries, lastPayload);
                    /* The table scrolls its own overflow box, not the window,
                     * so page 2 would otherwise open mid-table. */
                    var box = els.deliveries && els.deliveries.querySelector('.mv-table-scroll');
                    if (box) { box.scrollTop = 0; }
                });
            });
            els.deliveries.appendChild(pager);
        }

        /* Any control that changes WHICH rows are in the set sends both views
         * back to page 1 -- staying on page 7 of a result that now has two
         * rows reads as "no results". */
        function resetPaging() { state.page.table = 1; state.page.lines = 1; }

        /* -- Assigned-serials detail -----------------------------------------
         * A line can carry 40+ serials. Rendered inline they wrapped into a
         * paragraph that pushed every other column out of view, and the
         * "+34 more" that capped them was dead text -- there was no way at all
         * to reach the rest. "Which serials went into this line?" is a real
         * question (a recall, a customer dispute, a short-shipment claim), so
         * the full list gets its own searchable surface.
         *
         * Keyed on delivery + item rather than an index, because the modal
         * stays open across poll ticks and re-reads its line from each fresh
         * payload -- serials scanned while it is open appear in it instead of
         * going stale behind the operator's back. */
        /* tokens.css scopes every --mv-* property to `.mv`, not :root, so an
         * overlay appended to <body> renders with NO tokens at all -- a
         * transparent dialog over a dimmed page. `.mv` sets no transform,
         * filter or contain, so position:fixed inside it is still relative to
         * the viewport. */
        function mvRoot() { return document.querySelector('.mv') || document.body; }

        var openSerialsKey = null;

        function lineKey(l) { return l.delivery_no + '' + (l.item_no || ''); }

        function findLine(payload, key) {
            var lines = (payload && payload.lines) || [];
            for (var i = 0; i < lines.length; i++) {
                if (lineKey(lines[i]) === key) { return lines[i]; }
            }
            return null;
        }

        /* By primary key, from the payload as it stands right now.
         *
         * The table is rebuilt every two seconds, so a row element captured
         * when a control was drawn is a stale object by the time it is clicked
         * — and the batch lock is exactly the thing that can have changed in
         * between, because another gun scanning this line is what sets it. */
        function findLineById(id) {
            var lines = (lastPayload && lastPayload.lines) || [];
            for (var i = 0; i < lines.length; i++) {
                if (String(lines[i].id) === String(id)) { return lines[i]; }
            }
            return null;
        }

        function closeSerialsModal() {
            openSerialsKey = null;
            var scrim = document.querySelector('[data-mv="serials-modal"]');
            if (scrim && scrim.parentNode) { scrim.parentNode.removeChild(scrim); }
            refocusScanInput();
        }

        function openSerialsModal(key) {
            closeSerialsModal();
            openSerialsKey = key;
            var scrim = document.createElement('div');
            scrim.className = 'mv-modal-scrim is-open';
            scrim.setAttribute('data-mv', 'serials-modal');
            scrim.innerHTML =
                '<div class="mv-modal mv-modal--wide" role="dialog" aria-modal="true" aria-labelledby="mvSerTitle">' +
                '  <div class="mv-modal__head">' +
                '    <div><h2 class="mv-t-h2" id="mvSerTitle">Assigned serials</h2>' +
                '    <div class="mv-t-caption" data-ser="meta"></div></div>' +
                '    <button type="button" class="mv-btn mv-btn--ghost mv-btn--icon" data-ser="close" aria-label="Close">' +
                '      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" ' +
                '        stroke-width="1.5" stroke-linecap="round"><path d="M6 6l12 12M18 6 6 18"/></svg>' +
                '    </button>' +
                '  </div>' +
                '  <div class="mv-modal__body">' +
                '    <div class="mv-serials__tools">' +
                '      <div class="mv-search"><input type="text" class="mv-input" data-ser="find" ' +
                '        placeholder="Find a serial..." aria-label="Find a serial" autocomplete="off"></div>' +
                '      <button type="button" class="mv-btn mv-btn--sm" data-ser="copy">Copy all</button>' +
                '    </div>' +
                '    <div class="mv-serials" data-ser="grid"></div>' +
                '  </div>' +
                '  <div class="mv-modal__foot">' +
                '    <span class="mv-t-caption" data-ser="count" style="margin-right:auto;"></span>' +
                '    <button type="button" class="mv-btn" data-ser="close">Close</button>' +
                '  </div>' +
                '</div>';

            scrim.addEventListener('click', function (e) {
                if (e.target === scrim || e.target.closest('[data-ser="close"]')) { closeSerialsModal(); }
            });
            document.addEventListener('keydown', function esc_(e) {
                if (e.key !== 'Escape') { return; }
                document.removeEventListener('keydown', esc_);
                closeSerialsModal();
            });
            scrim.querySelector('[data-ser="find"]').addEventListener('input', function () {
                paintSerialsModal(lastPayload);
            });
            scrim.querySelector('[data-ser="copy"]').addEventListener('click', function () {
                var line = findLine(lastPayload, openSerialsKey);
                var text = ((line && line.serials) || []).join('\n');

                function fallbackCopy() {
                    var ta = document.createElement('textarea');
                    ta.value = text;
                    ta.style.position = 'fixed';
                    ta.style.opacity = '0';
                    document.body.appendChild(ta);
                    ta.select();
                    try { document.execCommand('copy'); ui.toast('ok', 'Copied', 'Serials on the clipboard.'); }
                    catch (err) { ui.toast('danger', 'Could not copy', 'Select the list and copy manually.'); }
                    document.body.removeChild(ta);
                }

                /* navigator.clipboard is https-only; this console is served over
                 * plain http on the warehouse LAN, so the textarea fallback is
                 * the branch that actually runs there, not a legacy path. */
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    navigator.clipboard.writeText(text).then(function () {
                        ui.toast('ok', 'Copied', text.split('\n').length + ' serials on the clipboard.');
                    }, fallbackCopy);
                } else { fallbackCopy(); }
            });

            mvRoot().appendChild(scrim);
            paintSerialsModal(lastPayload);
            scrim.querySelector('[data-ser="find"]').focus();
        }

        function paintSerialsModal(payload) {
            var scrim = document.querySelector('[data-mv="serials-modal"]');
            if (!scrim || !openSerialsKey) { return; }
            var line = findLine(payload, openSerialsKey);
            if (!line) {
                /* The line left the payload -- an operator on another gun
                 * removed the delivery, or the session was re-uploaded. */
                scrim.querySelector('[data-ser="grid"]').innerHTML =
                    '<p class="mv-t-caption" style="margin:0;">This line is no longer part of the session.</p>';
                return;
            }
            var serials = line.serials || [];
            var q = (scrim.querySelector('[data-ser="find"]').value || '').trim().toLowerCase();
            var hits = serials.map(function (s, i) { return { s: s, n: i + 1 }; });
            if (q) {
                hits = hits.filter(function (h) {
                    return String(h.s).toLowerCase().indexOf(q) !== -1;
                });
            }

            scrim.querySelector('[data-ser="meta"]').textContent =
                'Delivery ' + line.delivery_no + '  ·  Item ' + (line.item_no || '—') +
                '  ·  ' + line.material + (line.batch ? ('  ·  Batch ' + line.batch) : '');

            var grid = scrim.querySelector('[data-ser="grid"]');
            if (!hits.length) {
                grid.innerHTML = '<p class="mv-t-caption" style="margin:0;">' +
                    (serials.length ? 'No serial matches "' + esc(q) + '".'
                                    : 'Nothing scanned against this line yet.') + '</p>';
            } else {
                /* The ordinal is what makes a 200-serial list navigable: an
                 * operator checking a box against a pick list counts position,
                 * not identity. Carried on the item so it survives filtering
                 * -- a match's number is its number in the FULL list. */
                grid.innerHTML = hits.map(function (h) {
                    return '<span class="mv-serial"><i>' + h.n + '</i><code>' + esc(h.s) + '</code>' +
                        /* Correcting a serial is a picker's job as much as a
                           supervisor's — a mistyped unit is found by whoever is
                           packing it. Deleting the scan outright is not. */
                        (state.canEdit
                        ? '<button type="button" class="mv-serial__act" data-mv="edit-scan" ' +
                        '  data-serial="' + esc(h.s) + '" title="Correct this serial" ' +
                        '  aria-label="Correct serial ' + esc(h.s) + '">' +
                        '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" ' +
                        'stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">' +
                        '<path d="M4 20h4L20 8l-4-4L4 16z"/></svg></button>'
                        : '') +
                        (state.canDelete
                            ? '<button type="button" class="mv-serial__act mv-serial__act--danger" ' +
                              '  data-mv="delete-scan" data-serial="' + esc(h.s) + '" ' +
                              '  title="Delete this scan" aria-label="Delete scan ' + esc(h.s) + '">' +
                              '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" ' +
                              'stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">' +
                              '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/></svg></button>'
                            : '') +
                        '</span>';
                }).join('');
            }
            scrim.querySelector('[data-ser="count"]').textContent = q
                ? (hits.length + ' of ' + serials.length + ' serials match')
                : (serials.length + ' serial' + (serials.length === 1 ? '' : 's') +
                   '  ·  ' + line.scanned + ' of ' + line.required + ' scanned');
        }

        /* The banner above a narrowed console: what is showing, for whom, and
         * the way back to everything. Without it "only one delivery" and "the
         * screen is broken" look identical.
         *
         * Above the delivery list rather than inside it, and in every view
         * rather than only the Lines table: focus narrows all four views now,
         * so the explanation has to be somewhere all four can see it.
         * .mv-delivery-list is a four-column grid colouring its cards by
         * :nth-child, which a banner dropped in as the first child would both
         * take a cell from and recolour. */
        function renderScopeBar(payload) {
            var bar = els.scopeBar;
            if (!bar) { return; }
            var deliveryNo = focusedDelivery(payload);
            var customer = (payload && payload.focus_customer) || '';
            if (!deliveryNo && !customer) { bar.hidden = true; bar.innerHTML = ''; return; }

            var rows = scopeToFocus((payload && payload.deliveries) || [], payload);
            var scanned = rows.reduce(function (sum, d) { return sum + Number(d.scanned || 0); }, 0);
            var required = rows.reduce(function (sum, d) { return sum + Number(d.required || 0); }, 0);
            var delivery = rows[0] || {};

            bar.hidden = false;
            bar.innerHTML =
                '<div class="mv-lines-scope__body">' +
                '  <strong class="mv-mono" data-sc="no"></strong>' +
                '  <span data-sc="cust"></span>' +
                '  <div class="mv-t-caption" data-sc="meta"></div>' +
                '</div>';
            bar.querySelector('[data-sc="no"]').textContent = deliveryNo || customer;
            bar.querySelector('[data-sc="cust"]').textContent =
                (deliveryNo && customerLabel(delivery)) ? ('  ·  ' + customerLabel(delivery)) : '';
            /* A search widens the list past the focus — it is how the next
               delivery gets found — so the bar stops describing a scope it is
               no longer showing and says what is still true instead: the focus
               holds, and scanning still goes to it. */
            var searching = !!(els.deliveryFilter && els.deliveryFilter.value.trim());
            bar.querySelector('[data-sc="meta"]').textContent = searching
                ? ('Searching every delivery in this session. Scanning still goes to ' +
                   (deliveryNo || customer) + '.')
                : ((deliveryNo
                        ? 'Showing this delivery only'
                        : 'Showing this customer only — ' + rows.length +
                          ' deliver' + (rows.length === 1 ? 'y' : 'ies')) +
                   ' — ' + Math.round(scanned) + ' of ' + Math.round(required) +
                   ' units scanned. Scanning is locked to it.');

            var clear = document.createElement('button');
            clear.type = 'button';
            clear.className = 'mv-btn mv-btn--sm';
            if (searching) {
                /* Back to the delivery being picked, without touching the lock. */
                clear.textContent = 'Back to ' + (deliveryNo || customer);
                clear.title = 'Clears the search. The focus is unchanged.';
                clear.addEventListener('click', function () {
                    if (els.deliveryFilter) { els.deliveryFilter.value = ''; }
                    resetPaging();
                    renderDeliveries(lastPayload && lastPayload.deliveries, lastPayload);
                    refocusScanInput();
                });
            } else {
                clear.textContent = 'Show all deliveries';
                /* Clearing the focus also stops scanning — that is the whole
                   point of the lock — so the button says what it costs rather
                   than looking like a view toggle. */
                clear.title = 'Clears the focus. Scanning stays blocked until another delivery is focused.';
                clear.addEventListener('click', function () { postFocus(''); });
            }
            bar.appendChild(clear);
        }

        /* The Lines view's counterpart to renderScopeBar, for when a status
         * filter is what narrowed the table. Only reached with nothing focused
         * — a focused console is explained by the scope bar above the list, in
         * every view — so this names the filter and how much it matched. */
        function linesStatusBar(status, deliveries, shown) {
            var label = { pending: 'Pending', scanned: 'Scanned' }[status] || status;
            var bar = document.createElement('div');
            bar.className = 'mv-alert mv-alert--info mv-lines-scope';
            bar.innerHTML =
                '<div class="mv-lines-scope__body">' +
                '  <strong data-sc="label"></strong>' +
                '  <div class="mv-t-caption" data-sc="meta"></div>' +
                '</div>';
            bar.querySelector('[data-sc="label"]').textContent = label + ' deliveries';
            bar.querySelector('[data-sc="meta"]').textContent =
                deliveries + ' deliver' + (deliveries === 1 ? 'y' : 'ies') + ', ' +
                shown + ' line' + (shown === 1 ? '' : 's') + '.';

            var clear = document.createElement('button');
            clear.type = 'button';
            clear.className = 'mv-btn mv-btn--sm';
            clear.textContent = 'Show all';
            clear.addEventListener('click', function () {
                var all = root.querySelector('[data-mv-delivery-status="all"]');
                if (all) { all.click(); }
            });
            bar.appendChild(clear);
            return bar;
        }

        /* Material + assigned-serials detail per delivery line -- the client's
         * "material details + assigned serials not shown per line" complaint
         * (Row 2.1). payload.lines already carries this (see
         * merilv/views.py:_session_payload). */
        function renderLinesView(list, payload) {
            els.deliveries.className = 'mv-table-wrap mv-table-wrap--paged';
            var allowed = {};
            list.forEach(function (d) { allowed[d.delivery_no] = true; });

            /* Delivery isolation.
             *
             * With several deliveries loaded, this table showed every
             * delivery's lines interleaved — so opening one delivery told you
             * about all of them, and a picker reading a row had no way to know
             * which delivery it belonged to unless it happened to be a group
             * head.
             *
             * `list` is the delivery list this view was handed, already
             * narrowed to the focused delivery and then to the search box and
             * the status buttons (see renderDeliveries / scopeToFocus), so
             * following it is all the isolation this needs — focus no longer
             * has to be re-applied here, and the status filter no longer has to
             * override it to stay usable. Unfocused, `list` is the whole
             * session, which is the overview the Lines view was originally
             * for. */
            /* Two filters, and they are about different things.
             *
             * `allowed` is the delivery scope this view was handed — focus,
             * then the search box. The status filter is the second one, and
             * while a delivery is focused it applies HERE rather than to the
             * delivery list: All/Pending/Scanned then mean twelve entries, four
             * of them picked, which is the question worth asking about one
             * delivery. Unfocused it has already narrowed the deliveries
             * upstream, so applying it again to their lines would hide the
             * un-picked lines of a delivery that was let through for having
             * been started. */
            var byEntry = statusMode(payload, !!(els.deliveryFilter && els.deliveryFilter.value.trim()))
                === 'lines';
            var lines = (payload.lines || []).filter(function (l) {
                if (!allowed[l.delivery_no]) { return false; }
                return byEntry ? matchesStatus(l, state.deliveryStatus) : true;
            });
            var meta = paginate(lines, 'lines');

            /* The focused case is explained by the scope bar above the list,
             * in every view. This says which question the table is answering
             * when a status filter rather than focus is what narrowed it —
             * without it, a filtered view is indistinguishable from a session
             * that genuinely has only these lines. */
            if (!focusedDelivery(payload) && state.deliveryStatus !== 'all') {
                els.deliveries.appendChild(
                    linesStatusBar(state.deliveryStatus, list.length, meta.total));
            }

            var table = document.createElement('table');
            table.className = 'mv-table mv-table--dense mv-table--lines';
            table.innerHTML =
                '<thead><tr><th>Delivery</th><th class="mv-num">Item</th><th>Material</th>' +
                '<th>Description</th><th>Batch</th><th>Plant</th>' +
                '<th class="mv-num">Req</th><th class="mv-num">Scanned</th><th class="mv-num">Pending</th>' +
                '<th>Assigned serials</th></tr></thead><tbody></tbody>';
            var tbody = table.querySelector('tbody');

            if (!meta.total) {
                var empty = document.createElement('tr');
                empty.innerHTML = '<td colspan="10" class="mv-t-caption" style="text-align:center;padding:var(--mv-5);">No lines to show.</td>';
                tbody.appendChild(empty);
            }

            /* One delivery contributes dozens of consecutive rows, and
             * repeating its number down every one of them was most of what
             * made this table unreadable. Printed once per run, with a rule
             * above the run so the group boundary stays visible. */
            var previousDelivery = null;

            meta.rows.forEach(function (l) {
                var serials = l.serials || [];
                var newGroup = l.delivery_no !== previousDelivery;
                previousDelivery = l.delivery_no;

                var tr = document.createElement('tr');
                if (newGroup) { tr.className = 'is-group-start'; }
                tr.innerHTML =
                    '<td class="mv-mono mv-t-strong"></td><td class="mv-num"></td>' +
                    '<td class="mv-mono mv-t-strong"></td><td class="mv-cell-desc"></td>' +
                    '<td class="mv-mono"></td>' +
                    '<td class="mv-mono"></td>' +
                    '<td class="mv-num"></td><td class="mv-num"></td><td class="mv-num"></td>' +
                    '<td class="mv-cell-serials"></td>';
                var c = tr.children;
                c[0].textContent = newGroup ? l.delivery_no : '';
                /* The number prints once per run, so a row scrolled away from
                 * its group head shows nothing in this column; the title puts
                 * the answer one hover away rather than one scroll back. */
                tr.title = 'Delivery ' + l.delivery_no;
                c[1].textContent = l.item_no || '—';
                c[2].textContent = l.material;
                /* Material description from the master. Display only — it is
                 * deliberately absent from the SAP upload sheet, whose column
                 * set is matched by name. */
                c[3].textContent = l.material_description || '—';
                c[3].title = l.material_description || '';
                /* ── Batch: the row's one editable field ───────────────────
                 * A button for whoever may change it, plain text for everyone
                 * else, and plain text with a reason once the line has scans on
                 * it — the server locks the override at that point, so offering
                 * a control that can only be refused would be worse than not
                 * offering one.
                 *
                 * An overridden batch shows what the dump originally said
                 * underneath: a batch that silently differs from the paperwork
                 * is the kind of thing nobody notices until dispatch. */
                c[4].textContent = '';
                if (state.canBatch && !l.batch_locked) {
                    var batchBtn = document.createElement('button');
                    batchBtn.type = 'button';
                    batchBtn.className = 'mv-batchbtn mv-mono';
                    batchBtn.dataset.mv = 'edit-batch';
                    batchBtn.dataset.lineId = l.id;
                    batchBtn.title = 'Change this line\'s batch';
                    batchBtn.setAttribute('aria-label',
                        'Change batch for delivery ' + l.delivery_no + ' item ' + (l.item_no || ''));
                    batchBtn.textContent = l.batch || '—';
                    c[4].appendChild(batchBtn);
                } else {
                    var batchText = document.createElement('span');
                    batchText.textContent = l.batch || '—';
                    if (state.canBatch && l.batch_locked) {
                        batchText.title = 'Batch change is locked — undo this line\'s scans first.';
                        batchText.className = 'mv-batch-locked';
                    }
                    c[4].appendChild(batchText);
                }
                if (l.batch_overridden && l.original_batch) {
                    var was = document.createElement('span');
                    was.className = 'mv-batch-was mv-t-caption';
                    was.textContent = 'was ' + l.original_batch;
                    was.title = 'The dump named ' + l.original_batch +
                        '; it was overridden to ' + l.batch + '.';
                    c[4].appendChild(was);
                }
                c[5].textContent = l.plant || '—';
                c[6].textContent = l.required;
                c[7].textContent = l.scanned;
                c[8].textContent = l.pending;
                /* A pending of 0 is the good case and shouldn't compete for
                 * attention with the ones that still owe stock. */
                if (Number(l.pending) === 0) { c[8].classList.add('is-clear'); }

                /* The cell is one button rather than a chip strip plus a
                 * separate link: the only thing it does is open the list, and
                 * a 39-serial cell has no room for two affordances. */
                if (!serials.length) {
                    /* Say WHICH kind of empty this is.
                     *
                     * An entity whose barcodes carry no serial (MLSIPL is
                     * configured that way) will never have anything here, so a
                     * bare dash reads as a fault rather than as "there are no
                     * serials to show" — which is what prompted "no serial
                     * codes assigned in the column". */
                    var note = document.createElement('span');
                    note.className = 'mv-t-caption';
                    if (payload && payload.supports_serial === false) {
                        note.textContent = 'none yet';
                        note.title = 'Batch-managed stock: where the label prints a unit '
                            + 'number it is used, and otherwise each scan is given a running '
                            + 'serial of BATCH + 001. Nothing scanned against this line yet.';
                    } else {
                        note.textContent = '—';
                        note.title = 'No serial scanned against this line yet.';
                    }
                    c[9].appendChild(note);
                } else {
                    var btn = document.createElement('button');
                    btn.type = 'button';
                    btn.className = 'mv-serialbtn';
                    btn.setAttribute('aria-label',
                        'View all ' + serials.length + ' serials for delivery ' + l.delivery_no +
                        ' item ' + (l.item_no || ''));
                    btn.innerHTML =
                        '<span class="mv-serialbtn__n"></span>' +
                        '<span class="mv-serialbtn__peek mv-mono"></span>' +
                        '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" ' +
                        'stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">' +
                        '<path d="M9 6l6 6-6 6"/></svg>';
                    btn.querySelector('.mv-serialbtn__n').textContent = serials.length;
                    btn.querySelector('.mv-serialbtn__peek').textContent = serials.slice(0, 3).join('  ');
                    var key = lineKey(l);
                    btn.addEventListener('click', function () { openSerialsModal(key); });
                    c[9].appendChild(btn);
                }
                tbody.appendChild(tr);
            });
            mountTable(table);
            appendPager(meta, 'lines', 'lines');
        }

        /* What emptied the list, and the click that undoes it. `scoped` is the
         * list after focus but before the search box and the status buttons, so
         * it is what separates "this delivery has nothing matching" from
         * "nothing is in scope at all". */
        function emptyDeliveryState(query, scoped, payload) {
            var wrap = document.createElement('div');
            wrap.className = 'mv-empty';
            /* Whichever focus is in force, named the way the operator set it —
               a customer focus narrows the list too, and saying "delivery"
               about it would be describing a different lock. */
            var scope = focusedDelivery(payload)
                ? ('delivery ' + payload.focus_delivery)
                : ((payload && payload.focus_customer)
                    ? ('customer ' + payload.focus_customer)
                    : '');
            var status = state.deliveryStatus === 'pending' ? 'pending' : 'scanned';
            var title = document.createElement('div');
            title.className = 'mv-empty__title';
            var text = document.createElement('p');
            text.className = 'mv-empty__text';

            if (!scoped.length && scope) {
                title.textContent = 'Nothing in this session for ' + scope;
                text.textContent = 'The focused target is not part of this dump. Clear the focus to see the rest.';
            } else if (query) {
                title.textContent = 'No delivery matches “' + query + '”';
                text.textContent = scope
                    ? ('Only ' + scope + ' is in scope while it is focused.')
                    : 'Try a different delivery number or customer name.';
            } else {
                title.textContent = 'No delivery is ' + status;
                text.textContent = scope
                    ? ('Only ' + scope + ' is in scope, and nothing in it is ' + status + '.')
                    : 'Switch back to All to see every delivery.';
            }
            wrap.appendChild(title);
            wrap.appendChild(text);

            var back = document.createElement('button');
            back.type = 'button';
            back.className = 'mv-btn mv-btn--sm';
            if (query) {
                back.textContent = 'Clear the search';
                back.addEventListener('click', function () {
                    if (els.deliveryFilter) { els.deliveryFilter.value = ''; }
                    resetPaging();
                    renderDeliveries(lastPayload && lastPayload.deliveries, lastPayload);
                });
            } else {
                back.textContent = 'Show all';
                back.addEventListener('click', function () {
                    var all = root.querySelector('[data-mv-delivery-status="all"]');
                    if (all) { all.click(); }
                });
            }
            wrap.appendChild(back);
            return wrap;
        }

        var RENDERERS = {
            card: renderCardView,
            table: renderTableView,
            kanban: renderKanbanView,
            lines: renderLinesView
        };

        /* Narrow the delivery list to what is focused.
         *
         * Focus is the picking lock: with a delivery focused nothing else can
         * be scanned, so showing every other delivery's numbers beside the one
         * being picked is at best noise and at worst read as progress on the
         * wrong delivery. Everything downstream — the search box, the
         * All/Pending/Scanned buttons, their counts, and every one of the four
         * views — works within this scope rather than around it.
         *
         * Customer focus scopes to that customer's deliveries, which is the
         * same promise at the width the operator asked for. */
        function scopeToFocus(list, payload) {
            var rows = list || [];
            if (!payload) { return rows; }
            if (payload.focus_delivery) {
                return rows.filter(function (d) { return d.delivery_no === payload.focus_delivery; });
            }
            if (payload.focus_customer) {
                return rows.filter(function (d) { return d.customer_code === payload.focus_customer; });
            }
            return rows;
        }

        /* What All/Pending/Scanned are counting and filtering right now.
         *
         *   'lines'       a delivery is focused — they are about ITS entries
         *   'deliveries'  nothing focused, or a search is running — the session's
         *
         * Focused, the delivery list is one row, so counting deliveries gave
         * "All 1 / Pending 1 / Scanned 0" and a filter that could only ever
         * hide the one thing on screen. The useful question at that point is
         * about the delivery's lines — twelve entries, four of them picked —
         * which is what the buttons mean instead. */
        function statusMode(payload, searching) {
            return (!searching && focusedDelivery(payload)) ? 'lines' : 'deliveries';
        }

        function linesOf(payload, deliveryNo) {
            return ((payload && payload.lines) || []).filter(function (l) {
                return l.delivery_no === deliveryNo;
            });
        }

        /* Is there still work outstanding on this row?
         *
         * "Pending" used to mean "nothing scanned at all", so a delivery at
         * 179 of 180 counted as Scanned and the Pending chip read 0 while a
         * unit was still missing — reported from the floor against delivery
         * 8070271541. The question the chip is asked is "what still needs
         * picking", not "what has nobody touched", and a part-picked line is
         * the most urgent answer to it, not the least.
         *
         * Deliveries carry `complete` from the server; lines do not, so the
         * quantities decide there. The epsilon is because both are floats —
         * required and scanned are decimal quantities, and an exact `<` on
         * 179.99999 would leave a finished line pending forever. */
        function isOutstanding(row) {
            if (row.complete !== undefined && row.complete !== null) {
                return !row.complete;
            }
            var required = Number(row.required) || 0;
            var scanned = Number(row.scanned) || 0;
            return required - scanned > 1e-6;
        }

        function matchesStatus(row, status) {
            if (status === 'pending') { return isOutstanding(row); }
            if (status === 'scanned') { return !isOutstanding(row); }
            return true;
        }

        /* Put a customer code into the delivery filter and repaint, so every
         * order that customer has is listed together. The scanning focus is
         * deliberately untouched: this narrows what is SHOWN, and a search
         * already spans the whole session regardless of the lock. */
        function filterByCustomer(code) {
            if (!els.deliveryFilter || !code) { return; }
            if (lastPayload && lastPayload.allow_customer_focus) {
                els.deliveryFilter.value = '';
                postCustomerFocus(code, { keepIfSame: true });
                return;
            }
            els.deliveryFilter.value = code;
            resetPaging();
            renderDeliveries(lastPayload && lastPayload.deliveries, lastPayload);
            ui.toast('ok', 'Filtered to ' + code, 'Showing every delivery for this customer.');
            refocusScanInput();
        }

        function renderDeliveries(list, payload) {
            if (!els.deliveries) { return; }
            var q = (els.deliveryFilter && els.deliveryFilter.value || '').trim().toLowerCase();

            /* A search looks at the WHOLE session, focused or not.
             *
             * It is how an operator finds the next delivery to work — and since
             * nothing can be scanned without a focus, they are always focused,
             * so a search confined to the focus could only ever return the
             * delivery they are already on. The scope bar says the focus still
             * holds for scanning while a search is showing the rest. */
            var searching = !!q;
            var scoped = searching ? (list || []) : scopeToFocus(list, payload);
            var mode = statusMode(payload, searching);

            /* Counts describe whatever the buttons act on, always — a badge
               that counts something other than what the click filters is how
               "the filter does nothing" gets reported. */
            updateStatusCounts(
                mode === 'lines' ? linesOf(payload, focusedDelivery(payload)) : scoped,
                mode === 'lines' ? 'entries' : 'deliveries'
            );

            var filtered = scoped.filter(function (d) {
                /* Customer CODE is searchable as well as the name and the
                 * delivery number. A customer with several orders is the case
                 * this exists for: typing their code brings every one of their
                 * deliveries into the list at once, which is what the floor
                 * was doing by eye against the dump. */
                if (q && (d.delivery_no + ' ' + (d.customer_code || '') + ' ' + (d.customer_name || ''))
                        .toLowerCase().indexOf(q) === -1) { return false; }
                /* In 'lines' mode the status belongs to the entries, not to
                   this list: the one delivery here IS the scope, and hiding it
                   would empty the panel to say something about its lines. */
                if (mode === 'lines') { return true; }
                /* "Scanned" means "has been scanned", not "is finished".
                 *
                 * Both buttons used to test d.complete, which is true only
                 * when a delivery is 100% picked. On a real session that is
                 * almost nothing: with 374 of 413 units scanned on one
                 * delivery and 23 others untouched, Pending returned all 24
                 * and Scanned returned zero — so the two buttons showed the
                 * same list as All and an empty list respectively, and the
                 * filter looked broken because nothing it did was visible.
                 *
                 * Split on whether any scanning has happened instead. The two
                 * still partition the list, but now each side has something in
                 * it while a session is in progress, which is the whole time
                 * anyone is looking at this screen. */
                return matchesStatus(d, state.deliveryStatus);
            });
            /* A poll tick rebuilds this whole panel every 2s. The two table
             * views now scroll inside their own box rather than riding the
             * page, so without carrying the offset across the rebuild an
             * operator reading row 40 is thrown back to row 1 thirty times a
             * minute. Horizontal too — Lines has nine columns and a laptop
             * cannot show them all at once. */
            var priorBox = els.deliveries.querySelector('.mv-table-scroll');
            var offset = priorBox ? { top: priorBox.scrollTop, left: priorBox.scrollLeft } : null;

            els.deliveries.innerHTML = '';
            if (!filtered.length && (scoped.length || (list || []).length)) {
                /* An empty panel is the one outcome a filter must never produce
                   silently — it is indistinguishable from a session that lost
                   its deliveries, which is how "the filter does nothing" gets
                   reported. Name what emptied it and offer the way back. */
                els.deliveries.className = '';
                els.deliveries.appendChild(emptyDeliveryState(q, scoped, payload));
            } else {
                (RENDERERS[state.view] || renderCardView)(filtered, payload || {});
            }

            if (offset) {
                var freshBox = els.deliveries.querySelector('.mv-table-scroll');
                if (freshBox) {
                    freshBox.scrollTop = offset.top;
                    freshBox.scrollLeft = offset.left;
                }
            }
        }

        /* ── Status filter (All / Pending / Scanned) — Row 2.4 ──────────────── */
        /* Each button carries its own count.
         *
         * Without it the filter gives no feedback at all: clicking Pending on
         * a session where everything is pending changes nothing on screen, and
         * that is indistinguishable from a broken button. The number says what
         * the click will do before it is clicked. */
        function updateStatusCounts(list, noun) {
            var rows = list || [];
            var scanned = 0;
            /* Same rule as matchesStatus, or the badge and the filtered list
               disagree — which is worse than either being wrong alone. */
            rows.forEach(function (d) { if (!isOutstanding(d)) { scanned++; } });
            var counts = {
                all: rows.length,
                pending: rows.length - scanned,
                scanned: scanned
            };
            /* What the number counts, spelled out on hover. The buttons keep
               one set of words while what they act on changes underneath —
               deliveries across the session, entries within a focused one — and
               a bare "12" does not say which of those it is. */
            var unit = noun || 'deliveries';
            var says = {
                all: 'every ' + unit.replace(/ies$/, 'y').replace(/s$/, '') + ' in scope',
                pending: unit + ' still short of their required quantity',
                scanned: unit + ' picked in full'
            };
            Array.prototype.forEach.call(
                root.querySelectorAll('[data-mv-delivery-status]'),
                function (btn) {
                    var key = btn.dataset.mvDeliveryStatus;
                    if (!(key in counts)) { return; }
                    var badge = btn.querySelector('[data-mv="status-count"]');
                    if (!badge) {
                        badge = document.createElement('span');
                        badge.dataset.mv = 'status-count';
                        badge.className = 'mv-count-pill';
                        btn.appendChild(badge);
                    }
                    badge.textContent = counts[key];
                    btn.title = counts[key] + ' ' + says[key];
                }
            );
        }

        state.deliveryStatus = 'all';

        /* One writer for the filter, so the buttons' aria-pressed can never
         * disagree with what the list is actually filtered by — the two used
         * to be set in the same handler and nowhere else, which left no way
         * for anything but a click to change the filter. */
        function setDeliveryStatus(status, opts) {
            if (!status || status === state.deliveryStatus) { return false; }
            state.deliveryStatus = status;
            resetPaging();
            Array.prototype.forEach.call(
                root.querySelectorAll('[data-mv-delivery-status]'),
                function (b) {
                    b.setAttribute('aria-pressed', String(b.dataset.mvDeliveryStatus === status));
                }
            );
            if (!(opts && opts.silent)) {
                renderDeliveries(lastPayload && lastPayload.deliveries, lastPayload);
            }
            return true;
        }

        Array.prototype.forEach.call(root.querySelectorAll('[data-mv-delivery-status]'), function (btn) {
            btn.addEventListener('click', function () {
                /* Re-rendered even when the status has not changed: the click
                   is also how "Show all" in the Lines bar gets back to an
                   unfiltered table, and that must repaint. */
                if (!setDeliveryStatus(btn.dataset.mvDeliveryStatus)) {
                    renderDeliveries(lastPayload && lastPayload.deliveries, lastPayload);
                }
                refocusScanInput();
            });
        });

        /* ── How much of the log to show ─────────────────────────────────────
         * Partial by default, full on request.
         *
         * Collapsed is the unmodified state — the class is added, never
         * removed, to reach the expanded one — so the server-rendered page is
         * already the default and there is no expand-then-snap-back on load.
         *
         * It sizes the whole working row, not just the log: the scan frame and
         * the delivery panel share that row at equal heights, so giving the log
         * more room gives the list beside it the same. Only the LIST resizes
         * inside the frame — the input, the mode tabs, the outcome banner and
         * the filter chips keep their natural height in both states, because a
         * collapse that could hide the input would take the one thing a picker
         * cannot lose.
         *
         * Remembered per browser like the delivery view, the page size and the
         * sound level — an operator who works expanded should not have to say
         * so at the start of every shift. */
        var LOG_KEY = 'merilv.outbound.logExpanded';
        state.logExpanded = (function () {
            try { return window.localStorage.getItem(LOG_KEY) === '1'; }
            catch (err) { return false; }   /* private mode — keep the default */
        }());

        function applyLogSize() {
            /* On the console, not on the frame. The scan frame and the delivery
               panel are siblings on one grid row and the class sizes that row,
               so it has to sit on their shared parent — a class on the frame
               could only ever resize the frame, and the two would stop being
               equal the moment it was used. */
            root.classList.toggle('is-log-expanded', state.logExpanded);
            if (els.logToggle) {
                els.logToggle.setAttribute('aria-expanded', String(state.logExpanded));
                els.logToggle.title = state.logExpanded
                    ? 'Show a few rows and give the space back to the deliveries'
                    : 'Give the log the rest of the screen';
            }
            if (els.logToggleLabel) {
                els.logToggleLabel.textContent = state.logExpanded ? 'Collapse' : 'Expand';
            }
        }
        applyLogSize();

        if (els.logToggle) {
            els.logToggle.addEventListener('click', function () {
                state.logExpanded = !state.logExpanded;
                try { window.localStorage.setItem(LOG_KEY, state.logExpanded ? '1' : '0'); }
                catch (err) { /* non-fatal: the choice just will not persist */ }
                applyLogSize();
                /* The keyboard goes straight back to the gun, and without
                   moving the page — the operator resized a panel, they did not
                   ask to be taken anywhere. */
                refocusScanInput();
            });
        }

        /* ── View switcher ───────────────────────────────────────────────────
         * Remembered across sessions, because a picker who wants the table
         * wants it every shift, not once. */
        var VIEW_KEY = 'merilv.outbound.deliveryView';
        try {
            var saved = window.localStorage.getItem(VIEW_KEY);
            /* Validated against RENDERERS, so a stale value from a view that no
             * longer exists (the old 'list' mode) falls back to cards instead
             * of rendering nothing. */
            if (saved && RENDERERS[saved]) { state.view = saved; }
        } catch (err) { /* private mode / storage disabled — keep the default */ }

        function applyView() {
            Array.prototype.forEach.call(root.querySelectorAll('[data-mv-view]'), function (btn) {
                btn.setAttribute('aria-pressed', String(btn.dataset.mvView === state.view));
            });
        }

        Array.prototype.forEach.call(root.querySelectorAll('[data-mv-view]'), function (btn) {
            btn.addEventListener('click', function () {
                var next = btn.dataset.mvView;
                if (!RENDERERS[next] || next === state.view) { return; }
                state.view = next;
                try { window.localStorage.setItem(VIEW_KEY, next); } catch (err) { /* non-fatal */ }
                applyView();
                renderDeliveries(lastPayload && lastPayload.deliveries, lastPayload);
                refocusScanInput();
            });
        });
        applyView();

        /* ── Scan / Bulk Paste tabs ──────────────────────────────────────── */
        function showBulkPanel(show) {
            if (!els.scanPanel || !els.bulkPanel) { return; }
            els.scanPanel.hidden = show;
            els.bulkPanel.hidden = !show;
            state.bulkMode = !!show;
            if (els.tabScan) { els.tabScan.setAttribute('aria-pressed', String(!show)); }
            if (els.tabBulk) { els.tabBulk.setAttribute('aria-pressed', String(show)); }
            refocusScanInput();
        }
        if (els.tabScan) { els.tabScan.addEventListener('click', function () { showBulkPanel(false); }); }
        if (els.tabBulk) { els.tabBulk.addEventListener('click', function () { showBulkPanel(true); }); }

        function submitBulk() {
            if (!els.bulkInput || state.posting) { return; }
            /* A paste is one delivery's codes, so the focus requirement costs
               it nothing and buys the same guarantee as a hand scan. Checked
               before the text, because "focus a delivery" is the step to take
               whether or not anything has been pasted yet. */
            if (!canScan()) {
                refuseUnfocused('paste');
                return;
            }
            var text = els.bulkInput.value;
            if (!text || !text.trim()) {
                ui.toast('warn', 'Nothing to process', 'Paste or scan some codes first.');
                els.bulkInput.focus();
                return;
            }
            state.posting = true;
            var previousLabel = els.bulkSubmit ? els.bulkSubmit.innerHTML : '';
            if (els.bulkSubmit) { els.bulkSubmit.disabled = true; }
            if (els.bulkStatus) { els.bulkStatus.textContent = 'Processing…'; }
            var fd = new FormData();
            fd.append('raw_text', text);
            post(cfg.bulkUrl, fd).then(function (res) {
                var o = res.body || {};
                if (!res.ok) {
                    if (els.bulkStatus) { els.bulkStatus.textContent = o.message || 'Failed.'; }
                    if (o.reason_code === 'NO_FOCUS') { refuseUnfocused('paste'); return; }
                    showErrorPop(
                        o.error_title || 'Bulk paste failed',
                        o.message || 'Could not process the pasted codes.',
                        [],
                        'Nothing was recorded.'
                    );
                    return;
                }
                var results = o.results || [];
                var sum = o.summary || {};
                // "Not required on this delivery/customer" is the expected,
                // routine outcome for most of a multi-delivery fixture pasted
                // while focused on one — not a defect worth one alarming row
                // per code. Everything else (accepted, duplicate, expired,
                // wrong batch for THIS target, overscan…) is still itemised.
                var mismatches = results.filter(function (r) { return r.reason_code === 'TARGET_MISMATCH'; });
                results.forEach(function (r) {
                    if (r.reason_code === 'TARGET_MISMATCH') { return; }
                    var family = familyOf(r);
                    prependLogRow({
                        family: family,
                        reasonLabel: (r.reason_code || 'UNKNOWN').replace(/_/g, ' '),
                        code: r.raw_scan,
                        raw: r.raw_scan,
                        accepted: !!r.accepted,
                        message: r.message,
                        // A bulk row carries its assigned serial and can be
                        // corrected or removed exactly like a single scan —
                        // there is no reason for the two to differ.
                        canEdit: !!r.accepted,
                        serial: r.serial_only || '',
                        shownSerial: r.display_serial || '',
                        deliveryNo: r.delivery_no || ''
                    });
                });
                if (mismatches.length) {
                    var focusLabel = o.state && o.state.focus_delivery ? 'delivery ' + o.state.focus_delivery
                        : (o.state && o.state.focus_customer ? 'customer ' + o.state.focus_customer : '');
                    prependGroupedMismatchRow(mismatches, focusLabel);
                }
                /* One cue for the whole paste — 500 codes must not be 500
                   beeps — but the cue names the worst thing in it rather than
                   only whether anything landed, so a paste that took nothing
                   because every code was a duplicate does not sound the same
                   as one that took nothing because the box was empty. */
                playCue(bulkCue(o, results));
                if (o.state) { lastPayload = o.state; renderState(o.state); }
                /* One line per outcome an operator can act on, rather than a
                   single "N rejected" that says nothing about what to do. */
                var parts = [(sum.accepted || 0) + ' scanned'];
                if (sum.duplicate) { parts.push(sum.duplicate + ' duplicate'); }
                if (sum.unparsed) { parts.push(sum.unparsed + ' unreadable'); }
                if (sum.unmatched) { parts.push(sum.unmatched + ' not matched'); }
                if (mismatches.length) { parts.push(mismatches.length + ' for another delivery'); }
                var summary = parts.join(', ') + '.';
                if (els.bulkStatus) { els.bulkStatus.textContent = summary; }
                showResult(o.accepted_count > 0 ? 'ok' : 'warn',
                    o.accepted_count + ' of ' + (o.accepted_count + o.rejected_count) + ' codes scanned',
                    summary, '');
                ui.toast(o.accepted_count > 0 ? 'ok' : 'warn', 'Bulk paste complete', summary);
                /* ONE popup for the whole paste, grouped by error type.
                   A popup per rejection would be hundreds of them on a real
                   fixture — the thing the request calls a potential crash — and
                   a popup per type would still be five or six stacked. This is
                   one node with a scrollable body, whatever the paste
                   contained. */
                showBulkErrorPop(results);
                els.bulkInput.value = '';
            }).catch(function () {
                ui.toast('danger', 'Bulk paste failed', 'Check your connection and try again.');
                if (els.bulkStatus) { els.bulkStatus.textContent = 'Network error.'; }
            }).finally(function () {
                state.posting = false;
                if (els.bulkSubmit) { els.bulkSubmit.disabled = false; els.bulkSubmit.innerHTML = previousLabel; }
                refocusScanInput();
            });
        }
        /* Group a paste's rejections by error type: one heading per type, the
         * codes under it, all inside the popup's scrollable body.
         *
         * Grouped on the server's `error_title` rather than on the reason code,
         * because that is what the operator is being shown — three codes that
         * all read "Duplicate" belong under one heading even though
         * DUPLICATE_SERIAL, SERIAL_ALREADY_DISPATCHED and
         * SERIAL_IN_ANOTHER_SESSION are three different gates. */
        function showBulkErrorPop(results) {
            var order = [];
            var byTitle = {};
            var blockingSession = null;
            (results || []).forEach(function (r) {
                if (r.accepted) { return; }
                if (!blockingSession && r.blocking_session_id) {
                    blockingSession = r.blocking_session_id;
                }
                var title = r.error_title || (r.reason_code || 'REJECTED').replace(/_/g, ' ');
                if (!byTitle[title]) { byTitle[title] = []; order.push(title); }
                byTitle[title].push({
                    code: r.raw_scan || '—',
                    // Every rejected line names its unit. A paste of 400 is
                    // reconciled against cartons on a bench, and a list of
                    // barcodes is not something anyone can match to one.
                    serial: r.display_serial || ''
                });
            });
            if (!order.length) { hideErrorPop(); return; }

            var total = order.reduce(function (n, t) { return n + byTitle[t].length; }, 0);
            showErrorPop(
                total + (total === 1 ? ' code rejected' : ' codes rejected'),
                order.length === 1
                    ? order[0]
                    : order.length + ' kinds of problem in this paste.',
                order.map(function (title) { return { label: title, items: byTitle[title] }; }),
                'Nothing was recorded for these. Correct them and paste again.',
                {
                    action: blockingSession
                        ? {
                            label: 'Open session #' + blockingSession,
                            href: '/meril-v/outbound/' + blockingSession + '/'
                        }
                        : null
                }
            );
        }

        if (els.bulkSubmit) { els.bulkSubmit.addEventListener('click', submitBulk); }
        if (els.bulkInput) {
            /* Ctrl/Cmd+Enter submits. A plain Enter has to stay a newline —
               a gun firing into this box sends one after every code, and
               submitting on the first of them would process one code and throw
               the rest away. Keyboard-first either way: paste, Ctrl+Enter. */
            els.bulkInput.addEventListener('keydown', function (e) {
                if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
                    e.preventDefault();
                    e.stopPropagation();
                    submitBulk();
                }
            });
        }

        /* ── Bulk finish (Row 2.4) — Table view's checkboxes ─────────────────
         * The bar and its count live inside `root`; the modal is a sibling
         * include outside it (same as finish_modal.html), so it's reached via
         * document rather than root. */
        var bulkFinishBar = root.querySelector('[data-mv="bulk-finish-bar"]');
        var bulkFinishCount = root.querySelector('[data-mv="bulk-finish-count"]');
        var bulkFinishClear = root.querySelector('[data-mv="bulk-finish-clear"]');
        var bulkFinishModal = document.getElementById('mvBulkFinishModal');
        var bulkFinishModalCount = bulkFinishModal && bulkFinishModal.querySelector('[data-mv="bulk-finish-modal-count"]');
        var bulkFinishSubmit = document.getElementById('mvBulkFinishSubmit');

        function renderBulkFinishBar() {
            var n = state.selectedDeliveries.size;
            if (bulkFinishBar) { bulkFinishBar.hidden = n === 0; }
            if (bulkFinishCount) { bulkFinishCount.textContent = n + ' deliver' + (n === 1 ? 'y' : 'ies') + ' selected'; }
            if (bulkFinishModalCount) { bulkFinishModalCount.textContent = String(n); }
        }
        if (bulkFinishClear) {
            bulkFinishClear.addEventListener('click', function () {
                state.selectedDeliveries.clear();
                renderBulkFinishBar();
                renderDeliveries(lastPayload && lastPayload.deliveries, lastPayload);
            });
        }
        if (bulkFinishSubmit) {
            bulkFinishSubmit.addEventListener('click', function () {
                var deliveryNumbers = Array.from(state.selectedDeliveries);
                if (!deliveryNumbers.length) { return; }
                bulkFinishSubmit.disabled = true;
                var fd = new FormData();
                deliveryNumbers.forEach(function (no) { fd.append('delivery_no', no); });
                var field = function (id) { var el = document.getElementById(id); return el ? el.value.trim() : ''; };
                fd.append('courier_partner', field('mvBulkFinishCourier'));
                fd.append('courier_number', field('mvBulkFinishCourierNo'));
                fd.append('docket_no', field('mvBulkFinishDocket'));
                fd.append('vehicle_number', field('mvBulkFinishVehicle'));
                var proceedEl = document.getElementById('mvBulkFinishProceed');
                if (proceedEl && proceedEl.checked) { fd.append('proceed_anyway', '1'); }

                post(cfg.bulkFinishUrl, fd).then(function (res) {
                    var o = res.body || {};
                    if (!res.ok) {
                        ui.toast('danger', 'Bulk finish failed', o.message || 'Could not finish the selected deliveries.');
                        return;
                    }
                    var summary = o.finished_count + ' finished';
                    if (o.skipped && o.skipped.length) { summary += ', ' + o.skipped.length + ' skipped'; }
                    ui.toast(o.finished_count > 0 ? 'ok' : 'warn', 'Bulk finish complete', summary + '.');
                    if (o.skipped && o.skipped.length) {
                        o.skipped.forEach(function (s) {
                            ui.toast('warn', s.delivery_no + ' skipped', s.reason);
                        });
                    }
                    state.selectedDeliveries.clear();
                    renderBulkFinishBar();
                    if (o.state) { lastPayload = o.state; renderState(o.state); }
                    if (bulkFinishModal) { bulkFinishModal.classList.remove('is-open'); }
                }).catch(function () {
                    ui.toast('danger', 'Bulk finish failed', 'Check your connection and try again.');
                }).finally(function () {
                    bulkFinishSubmit.disabled = false;
                });
            });
        }

        if (els.deliveryFilter) {
            els.deliveryFilter.addEventListener('input', function () {
                resetPaging();
                if (lastPayload) { renderDeliveries(lastPayload.deliveries || [], lastPayload); }
            });
        }

        /* ── Network ─────────────────────────────────────────────────────── */
        var lastPayload = null;

        function post(url, body) {
            return fetch(url, {
                method: 'POST',
                headers: ui.csrfHeaders({ 'X-Requested-With': 'XMLHttpRequest' }),
                body: body
            }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, status: r.status, body: j }; }); });
        }

        /* ── Offline queue (Row 3.6) ─────────────────────────────────────────
         * The honest gap: this console posts each scan as it happens, so a
         * dropped connection loses whatever was mid-flight — same gap the
         * legacy tool had (its service worker was network-only, no queue).
         * See static/merilv/js/offline_queue.js for the full design notes,
         * including the one case it cannot fully close (a response lost
         * after the server already accepted a non-serialised item). */
        var offlineQueue = window.mvOfflineQueue ? window.mvOfflineQueue.create(cfg.scanUrl, onQueueSizeChange) : null;

        function onQueueSizeChange(count) {
            if (count > 0) {
                setStatus(count + ' scan(s) queued offline — sending automatically once the connection returns.');
            }
        }

        /* ── Failed-scan hold ──────────────────────────────────────────────
         * A rejected scan holds the session: the same code cannot be rescanned
         * until it is corrected or deliberately left out. The hold has no UI of
         * its own — the error popup is its UI, and carries "Skip this one".
         *
         * It used to build a banner and insert it above the runtime scan log.
         * That log no longer exists, so the insert fell through to its
         * `root.appendChild` fallback and the banner appeared at the END of the
         * console, under the delivery list — a block of its own repeating what
         * the popup already said, in the last place a picker looks. The
         * fallback's own comment called that "a poor place for this"; it was
         * right, and the answer is not a better place but one fewer element. */
        function clearFailedScan() {
            state.failedScan = null;
        }

        function skipFailedScan() {
            var failed = state.failedScan;
            if (!failed) { return; }
            state.failedScan = null;
            hideErrorPop();
            /* Recorded in the log, not just dismissed. "We left one out" is a
               fact about the session that the packing list has to be able to
               explain later. */
            prependLogRow({
                family: 'warn',
                reasonLabel: 'SKIPPED',
                code: failed.raw,
                raw: failed.raw,
                accepted: false,
                message: 'Left out deliberately after: ' + (failed.message || failed.reason),
                canEdit: false
            });
            setStatus('Skipped. Ready to scan.');
            refocusScanInput();
        }

        /* Put the hold's error back on screen. Dismissing the popup does not
         * resolve anything, so rescanning the held code has to say why it is
         * still refused rather than failing silently. */
        function reshowHold() {
            var failed = state.failedScan;
            if (!failed) { return; }
            showErrorPop(
                failed.title || failed.reason,
                failed.message || '',
                failed.raw ? [{ label: 'Scanned', items: [failed.raw] }] : [],
                'Scan the corrected item, or skip this one.',
                { skippable: true, serial: failed.serial || '' }
            );
        }

        function familyOf(o) {
            if (o.audio_cue === 'match') { return 'ok'; }
            if (o.audio_cue === 'duplicate') { return 'warn'; }
            if (o.audio_cue === 'no_focus') { return 'blocked'; }
            if (o.audio_cue === 'error' || o.audio_cue === 'mismatch' ||
                o.audio_cue === 'wrong_delivery') { return 'danger'; }
            return o.accepted ? 'ok' : 'neutral';
        }

        function renderScanResult(o, raw) {
            /* The server's focus refusal is a workflow gate, not a verdict on
               the item: no audit row is written for it, so none is drawn here
               either, and it must not arm the failed-scan hold — that hold
               blocks a repeat of the same code, and repeating the same code is
               exactly what the picker should do once they have focused a
               delivery. */
            if (o.reason_code === 'NO_FOCUS') {
                refuseUnfocused('scan');
                return;
            }
            var family = familyOf(o);
            var reasonLabel = (o.reason_code || 'UNKNOWN').replace(/_/g, ' ');
            /* cueOf, not familyOf: the colour has four values and the sound has
               six, so a wrong batch and an expired item share a red row but not
               a beep. */
            playCue(cueOf(o));
            prependLogRow({
                family: family,
                reasonLabel: reasonLabel,
                code: (o.parsed && (o.parsed.material || o.parsed.batch)) || raw,
                raw: raw,
                accepted: !!o.accepted,
                message: o.message,
                canEdit: !!o.accepted,
                // The stored serial (BATCH + 3-digit serial) rather than the
                // engine's internal identity: they are the same string now,
                // but the serial is what the operator and the export both use.
                serial: o.serial_only || o.serial_identity || '',
                // What the row prints. Set on rejections as well, where there
                // is no assigned serial to print but the label named a unit.
                shownSerial: o.display_serial || '',
                deliveryNo: o.delivery_no || '',
                time: o.scanned_at || ''
            });

            /* Three unmistakable outcomes: took it, already had it, would not
               take it. Banner (readable across the bench), toast (persists in
               the corner), and a distinct sound — never sound alone. */
            if (o.accepted) {
                /* A good scan is the answer to whatever the popup was
                   complaining about, so it goes rather than being clicked
                   away. */
                hideErrorPop();
                showResult('ok',
                    o.delivery_no ? ('Delivery ' + o.delivery_no) : 'Scanned',
                    o.message || '', o.serial_only || '');
                ui.toast('ok', 'Scanned', o.serial_only
                    ? ('Serial ' + o.serial_only + (o.delivery_no ? ' → ' + o.delivery_no : ''))
                    : (o.message || ''));
            } else {
                /* The popup, not a toast. Both at once is two reports of one
                   rejection in two corners of the screen — and a toast is the
                   thing that queues, so a paste's worth of them would arrive
                   one every four seconds long after the operator had moved on.
                   The banner still carries it in the frame; this is the part
                   that demands to be read. */
                showResult(family === 'warn' ? 'warn' : 'danger', reasonLabel,
                           o.message || '', o.serial_only || o.serial_identity || '');
                showErrorPop(
                    o.error_title || reasonLabel,
                    o.message || '',
                    raw ? [{ label: 'Scanned', items: [raw] }] : [],
                    'Scan the corrected item, or skip this one.',
                    {
                        skippable: true,
                        serial: o.display_serial || '',
                        action: o.blocking_session_id
                            ? {
                                label: 'Open session #' + o.blocking_session_id,
                                href: '/meril-v/outbound/' + o.blocking_session_id + '/'
                            }
                            : null
                    }
                );
            }

            if (o.state) { lastPayload = o.state; renderState(o.state); }

            /* Hold on a rejection, release on an acceptance. Doing it here
               rather than in submitScan covers the offline queue drain too,
               which renders through this same function. The popup raised above
               is the hold's only UI, so there is nothing further to paint —
               the title is kept so rescanning the held code can say the same
               thing again. */
            if (o.accepted) {
                clearFailedScan();
            } else {
                state.failedScan = {
                    raw: raw,
                    reason: reasonLabel,
                    title: o.error_title || reasonLabel,
                    message: o.message || '',
                    // Kept so reshowHold puts the same serial back on screen.
                    // The hold's popup is the only UI it has; a second showing
                    // that dropped the serial would read as a different error.
                    serial: o.display_serial || ''
                };
            }
        }

        function drainOfflineQueue() {
            if (!offlineQueue || !offlineQueue.size()) { return; }
            offlineQueue.drain(function (entry) {
                var fd = new FormData();
                Object.keys(entry.fields).forEach(function (key) { fd.append(key, entry.fields[key]); });
                return post(entry.url, fd).then(function (res) {
                    renderScanResult(res.body || {}, entry.fields.raw_scan);
                });
            }).then(function (result) {
                if (result && result.drained > 0) {
                    ui.toast('ok', 'Queued scans sent', result.drained + ' scan(s) delivered.');
                }
                if (offlineQueue.size() === 0) { setStatus('Ready to scan. Keyboard input is focused.'); }
            });
        }
        if (offlineQueue) {
            window.addEventListener('online', drainOfflineQueue);
            setInterval(drainOfflineQueue, 15000);
            drainOfflineQueue(); // scans queued in a previous page load, if any
        }

        /* One refusal for every path that would assign stock with nothing
         * focused. Says which step is missing and where it is taken — in the
         * banner, in a toast and as a sound, because a picker who is looking at
         * the bench rather than the screen still has to know the scan did not
         * land. Nothing is logged and nothing is held: the same code is meant
         * to be rescanned the moment a delivery is focused. */
        function refuseUnfocused(where) {
            playCue('blocked');
            setStatus('Select a delivery, then ' + where + '.');
            showResult('blocked', 'No delivery focused',
                       'Nothing is scanned or assigned until a delivery is focused.', '');
            showErrorPop(
                'No Delivery Focused',
                'Click a delivery in the list to focus it, then ' + where + '.',
                [],
                'Focus a delivery to dismiss.'
            );
            if (els.scopeBar) { els.scopeBar.scrollIntoView({ block: 'nearest' }); }
        }

        function submitScan(raw) {
            if (!raw) { return; }
            if (state.posting) {
                /* Queued, never dropped. It is submitted the moment the
                   current request settles — back through this same function,
                   so the focus gate, the failed-scan hold and the offline
                   fallback all still apply to it then. */
                state.pendingScans.push(raw);
                return;
            }

            /* The focus rule, checked before the round trip. The server refuses
               this too (views._focus_gate) and is the authority; this is so a
               picker finds out at the gun rather than after it. */
            if (!canScan()) {
                refuseUnfocused('scan');
                refocusScanInput();
                return;
            }

            /* The same code that just failed cannot succeed now — the delivery
               data has not changed — so it is refused here instead of spending
               a round trip to be told the same thing. Anything else goes
               through: a corrected scan is exactly how the hold is meant to be
               cleared. */
            if (state.failedScan && raw === state.failedScan.raw) {
                playCue('danger');
                setStatus('Same code as the failed scan. Scan the correct item, or press Skip.');
                /* The popup, not a toast — and the SAME popup, with the
                   original reason. Dismissing it did not resolve anything, so
                   firing the held code again has to say why it is still
                   refused instead of failing quietly. */
                reshowHold();
                refocusScanInput();
                return;
            }

            state.posting = true;
            setStatus('Submitting…');
            // Minted before the request, not inside the catch: if this attempt
            // reaches the server and is accepted but the response is lost, the
            // queued retry carries the same ref and the server recognises it
            // as the same scan instead of booking it twice.
            var clientRef = window.mvOfflineQueue ? window.mvOfflineQueue.newRef() : '';
            var fd = new FormData();
            fd.append('raw_scan', raw);
            if (clientRef) { fd.append('client_ref', clientRef); }
            post(cfg.scanUrl, fd).then(function (res) {
                renderScanResult(res.body || {}, raw);
                setStatus((res.body || {}).accepted ? 'Ready to scan. Keyboard input is focused.' : 'Rejected — see log below.');
            }).catch(function (err) {
                if (offlineQueue && window.mvOfflineQueue.isNetworkError(err)) {
                    offlineQueue.enqueue({
                        url: cfg.scanUrl,
                        clientRef: clientRef,
                        fields: { raw_scan: raw, client_ref: clientRef },
                    });
                    playCue('neutral');
                    setStatus(offlineQueue.size() + ' scan(s) queued offline — sending automatically once the connection returns.');
                    ui.toast('warn', 'Scan queued offline', 'It will send automatically when the connection returns.');
                    return;
                }
                playCue('danger');
                setStatus('Network error — the scan may not have been recorded. Try again.');
                ui.toast('danger', 'Scan failed to submit', 'Check your connection and rescan if needed.');
            }).finally(function () {
                state.posting = false;
                /* The box is deliberately NOT cleared here. It was emptied on
                   the Enter that submitted this code, so anything in it now is
                   the next code part-typed — clearing it would wipe that. */
                refocusScanInput();
                /* One queued scan per settled request, recursing rather than
                   looping so each goes through the identical path a typed one
                   does. */
                if (state.pendingScans.length) { submitScan(state.pendingScans.shift()); }
            });
        }

        /* ── Clear every scan on the focused delivery ─────────────────────
         * Undo walks back one scan at a time; correcting a whole delivery that
         * way is what drove the floor to delete the upload and re-import the
         * Excel instead. Confirmed first, and named — the dialog says which
         * delivery and how many scans go, because "Clear" on its own has
         * already been read as "clear the focus" on this screen.
         */
        function confirmClearScans() {
            var delivery = focusedDelivery(lastPayload);
            if (!delivery) {
                ui.toast('warn', 'No delivery focused',
                         'Focus the delivery you want to clear first.');
                return;
            }
            var scanned = 0;
            var list = (lastPayload && lastPayload.deliveries) || [];
            for (var i = 0; i < list.length; i++) {
                if (list[i].delivery_no === delivery) { scanned = Number(list[i].scanned) || 0; }
            }
            if (!scanned) {
                ui.toast('warn', 'Nothing to clear', 'Delivery ' + delivery + ' has no scans.');
                return;
            }
            ui.confirm(
                {
                    title: 'Clear scans on ' + delivery + '?',
                    message: scanned + ' scan' + (scanned === 1 ? '' : 's') + ' on delivery '
                        + delivery + ' will be removed and their units returned to pending. '
                        + 'Every removal is recorded in the scan log. This cannot be undone.',
                    okLabel: 'Clear ' + scanned + ' scan' + (scanned === 1 ? '' : 's')
                },
                function () { postClearScans(delivery); }
            );
        }

        function postClearScans(delivery) {
            var fd = new FormData();
            fd.append('delivery_no', delivery);
            post(cfg.clearUrl, fd).then(function (res) {
                var body = res.body || {};
                if (!res.ok || !body.ok) {
                    ui.toast('danger', 'Could not clear',
                             body.message || 'The scans were not cleared.');
                    return;
                }
                prependLogRow({
                    family: 'warn',
                    reasonLabel: 'CLEARED',
                    code: 'Delivery ' + body.delivery_no,
                    accepted: false,
                    message: body.cleared_count + ' scan'
                        + (body.cleared_count === 1 ? '' : 's') + ' cleared — returned to pending.',
                    canEdit: false
                });
                lastPayload = body.state;
                renderState(body.state);
                ui.toast('ok', 'Scans cleared',
                         body.cleared_count + ' removed from ' + body.delivery_no);
            }).catch(function () {
                ui.toast('danger', 'Could not clear',
                         'Check your connection, then check the log before trying again.');
            });
        }

        function confirmDeleteDelivery(d) {
            if (!d || !d.delivery_no) { return; }
            if (!state.canClear || !cfg.deliveryDeleteUrl) {
                ui.toast('warn', 'Not permitted', 'You do not have permission to remove a delivery.');
                return;
            }
            var scans = Number(d.scanned) || 0;
            ui.confirm(
                {
                    title: 'Delete delivery ' + d.delivery_no + '?',
                    message: 'This removes ' + (d.line_count || 0) + ' line'
                        + (Number(d.line_count) === 1 ? '' : 's') + ' from this session'
                        + (scans ? (' and releases ' + scans + ' scan' + (scans === 1 ? '' : 's')) : '')
                        + '. Use this when the delivery should move to another session. This cannot be undone.',
                    okLabel: 'Delete delivery'
                },
                function () { postDeleteDelivery(d.delivery_no); }
            );
        }

        function postDeleteDelivery(delivery) {
            var fd = new FormData();
            fd.append('delivery_no', delivery);
            post(cfg.deliveryDeleteUrl, fd).then(function (res) {
                var body = res.body || {};
                if (!res.ok || !body.ok) {
                    ui.toast('danger', 'Could not delete delivery',
                             body.message || 'The delivery was not removed.');
                    return;
                }
                prependLogRow({
                    family: 'warn',
                    reasonLabel: 'DELIVERY DELETED',
                    code: 'Delivery ' + body.delivery_no,
                    accepted: false,
                    message: body.line_count + ' line'
                        + (body.line_count === 1 ? '' : 's') + ' removed from this session.',
                    canEdit: false
                });
                state.selectedDeliveries.delete(body.delivery_no);
                lastPayload = body.state;
                renderState(body.state);
                ui.toast('ok', 'Delivery deleted',
                         body.delivery_no + ' removed from this session.');
            }).catch(function () {
                ui.toast('danger', 'Could not delete delivery',
                         'Check your connection, then try again.');
            }).finally(refocusScanInput);
        }

        function postUndo() {
            post(cfg.undoUrl, new FormData()).then(function (res) {
                if (res.body && res.body.ok) {
                    // Prepend the reversal rather than deleting the top row. The
                    // server appends an UNDONE entry to the audit log, and now
                    // that rejections appear in the feed too, "remove the first
                    // row" would usually have deleted an unrelated rejection.
                    prependLogRow({
                        family: 'warn',
                        reasonLabel: 'UNDONE',
                        code: res.body.removed || '—',
                        raw: res.body.raw_scan || '',
                        accepted: false,
                        message: 'Scan reversed — returned to pending.',
                        canEdit: false
                    });
                    lastPayload = res.body.state;
                    renderState(res.body.state);
                    ui.toast('ok', 'Last scan undone', res.body.removed);
                } else {
                    ui.toast('warn', 'Nothing to undo',
                             (res.body && res.body.message) || '');
                }
            }).catch(function () {
                /* Silent before this. The undo either happened or it did not,
                   and a button that answers a dropped connection with nothing
                   leaves an operator pressing it again — which, if the first
                   one did land, undoes a second scan they meant to keep. */
                ui.toast('danger', 'Could not undo',
                         'Check your connection, then check the log before pressing it again.');
            });
        }

        function postFocus(deliveryNo, opts) {
            /* The endpoint TOGGLES: posting the delivery that is already
               focused clears the focus, which is what a second click on a card
               should do. "Open this delivery's lines" is not a toggle, so it
               asks to be left focused if it already is. */
            /* Focusing a delivery clears an active status filter. Focus now
               narrows every view to that delivery, and a leftover
               Pending/Scanned filter can only subtract from a list of one — so
               clicking a delivery that has already been scanned while
               "Pending" was selected would focus it and show nothing. The
               picker asked for this delivery; the narrower question they asked
               earlier gets dropped.

               Above the early return below, not after it, so "open this
               delivery's lines" on the delivery that is ALREADY focused clears
               the filter too; that path repaints from the cached payload and
               would otherwise be the one click that still did nothing.
               `silent` because both paths repaint straight after. */
            if (deliveryNo) { setDeliveryStatus('all', { silent: true }); }

            if (opts && opts.keepIfSame && lastPayload && lastPayload.focus_delivery === deliveryNo) {
                renderState(lastPayload);
                refocusScanInput();
                return;
            }
            var fd = new FormData();
            fd.append('delivery_no', deliveryNo);
            post(cfg.focusUrl, fd).then(function (res) {
                if (res.body && res.body.state) { lastPayload = res.body.state; renderState(res.body.state); }
                // The "no delivery focused" popup, if it is up, has just been
                // answered — by the click that is running right now.
                if (deliveryNo) { hideErrorPop(); }
            }).finally(function () {
                /* Clicking a card moves focus to the card. Give the keyboard
                   straight back — this is the single most common way an
                   operator ends up scanning into nothing. */
                refocusScanInput();
            });
        }

        function postCustomerFocus(customerCode, opts) {
            if (opts && opts.keepIfSame && lastPayload && lastPayload.focus_customer === customerCode) {
                renderState(lastPayload);
                refocusScanInput();
                return;
            }
            var fd = new FormData();
            fd.append('customer_code', customerCode);
            post(cfg.focusUrl, fd).then(function (res) {
                if (res.body && res.body.state) { lastPayload = res.body.state; renderState(res.body.state); }
            }).finally(function () {
                refocusScanInput();
            });
        }

        function poll() {
            if (state.polling) { return; }             // skip this tick if a POST is in flight
            state.polling = true;
            if (els.pollSpinner) { els.pollSpinner.classList.add('is-active'); }
            fetch(cfg.stateUrl, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
                .then(function (r) { return r.json(); })
                .then(function (payload) { lastPayload = payload; renderState(payload); })
                .catch(function () {})
                .finally(function () {
                    state.polling = false;
                    if (els.pollSpinner) { els.pollSpinner.classList.remove('is-active'); }
                });
        }
        setInterval(function () { if (!state.posting) { poll(); } }, 2000);

        /* ── Wiring ──────────────────────────────────────────────────────── */
        if (els.input) {
            els.input.addEventListener('keydown', function (e) {
                if (e.key !== 'Enter') { return; }
                e.preventDefault();
                /* Emptied here, synchronously, on the Enter itself — never
                   when the request settles. A gun does not wait for a network
                   round trip before firing its next burst, and it appends its
                   keystrokes to whatever the box still holds rather than
                   clearing first. Clear on the response instead and the next
                   code lands on the end of this one: "A1" then "A2" reaching
                   the server as "A1A2", and the box going on collecting every
                   code after that.

                   This handler runs to completion before the browser delivers
                   the next keystroke, so this is the only point at which the
                   box is guaranteed empty for the code that follows.

                   submitScan therefore never touches the box: by the time it,
                   or its round trip, gets there, anything in the box is the
                   NEXT code being typed and not this one. */
                var raw = els.input.value.trim();
                els.input.value = '';
                submitScan(raw);
            });
        }
        if (els.undoBtn) { els.undoBtn.addEventListener('click', postUndo); }
        if (els.clearScansBtn) {
            els.clearScansBtn.addEventListener('click', confirmClearScans);
        }

        /* Edit and delete are wired on `document`, not on `root`: both controls
         * also appear inside the Check Logs modal and the serials modal, which
         * are siblings of the console rather than descendants of it. Bound to
         * root, neither worked from there. */
        document.addEventListener('click', function (e) {
            var batchBtn = e.target.closest('[data-mv="edit-batch"]');
            if (batchBtn) {
                openBatchModal(findLineById(batchBtn.dataset.lineId));
                return;
            }
            var editBtn = e.target.closest('[data-mv="edit-scan"]');
            if (editBtn) { openEditScanModal(editBtn.dataset.serial); return; }
            var delBtn = e.target.closest('[data-mv="delete-scan"]');
            if (delBtn) { confirmDeleteScan(delBtn.dataset.serial); return; }
            var clearFocus = e.target.closest('[data-mv="clear-focus"]');
            if (clearFocus) { postFocus(''); }
        });

        /* ── Correct an assigned serial ──────────────────────────────────────
         * This was a stub: it opened a confirm dialog with no field in it and
         * a comment saying the real one lived elsewhere. It did not, so the
         * edit control on every accepted scan row did nothing, and a
         * mis-scanned serial could not be corrected at all — which is what
         * "the serial is missing in the edit view" describes.
         *
         * The endpoint rebuilds the identity as BATCH + serial, so the field
         * asks for the serial half only and shows the batch beside it. */
        /* ── Finish a delivery, in place ───────────────────────────────────
         * A session holds a whole dump — often twenty-odd deliveries — and a
         * picker works through them one after another. Finishing used to
         * navigate to the finished delivery's detail page, so closing each one
         * threw the operator out of the session and they came back in to start
         * the next; a shortfall did the same through a full discrepancy page.
         * The data saved either way, but between twenty deliveries that is
         * twenty round trips away from the scan input.
         *
         * The form is still a real <form> and still posts to the same endpoint
         * without JavaScript. This intercepts the submit and asks the same
         * view over fetch, which answers an XHR with JSON instead of a
         * redirect. */
        var finishForm = document.querySelector('#mvFinishModal form');

        function finishShortEl(name) {
            return document.querySelector('[data-mv="finish-' + name + '"]');
        }

        function clearFinishShort() {
            var box = finishShortEl('short');
            if (box) { box.hidden = true; }
        }

        /* The refused shortfall, in the modal that asked for it. */
        function showFinishShort(body) {
            var box = finishShortEl('short');
            if (!box) {
                ui.toast('warn', 'Delivery is short', body.message || '');
                return;
            }
            finishShortEl('short-title').textContent =
                body.message || 'This delivery is short.';
            var list = finishShortEl('short-list');
            list.innerHTML = '';
            (body.gaps || []).forEach(function (gap) {
                var row = document.createElement('div');
                row.className = 'mv-finish-short__row';
                row.innerHTML =
                    '<span class="mv-mono" data-g="item"></span>' +
                    '<span class="mv-mono" data-g="material"></span>' +
                    '<span data-g="qty"></span>';
                row.querySelector('[data-g="item"]').textContent = gap.item_no || '—';
                row.querySelector('[data-g="material"]').textContent = gap.material || '';
                row.querySelector('[data-g="qty"]').textContent =
                    unitText(gap.scanned) + ' of ' + unitText(gap.required) +
                    '  ·  ' + unitText(gap.pending) + ' short';
                list.appendChild(row);
            });
            box.hidden = false;
            /* The override box is NOT ticked here.
             *
             * It used to be — "tick it for them, the second press is what
             * makes it a decision" — but a second press is not a decision, it
             * is the same press again. An operator who clicks Finish twice,
             * which is what anyone does when a button appears not to have
             * worked, dispatched a delivery whose material had never been
             * scanned and never saw the box that allowed it. That is the
             * "warning bypassable through repeated clicks" this refuses.
             *
             * Ticking it is now the operator's own act, and it is what the
             * shortfall panel asks them for. */
            var proceed = finishForm && finishForm.querySelector('[name="proceed_anyway"]');
            if (proceed) { proceed.checked = false; }
            box.scrollIntoView({ block: 'nearest' });
        }

        /* The paperwork a dispatch cannot leave without.
         *
         * These three are the SAP invoice sheet: courier number is its CURR
         * column and the docket its DOC column, and a sheet missing either is
         * rejected at the other end — after the van has gone. The form marks
         * them with a * and this is what that * means.
         *
         * Checked here rather than with the `required` attribute because this
         * form is also the admin's edit form and is submitted over fetch; a
         * native validation bubble on a field inside a scrolled modal is also
         * routinely missed on a warehouse laptop, where a toast is not. The
         * server still accepts a blank — the no-JavaScript fallback posts the
         * same form — so this is a guard on the console, not a schema change.
         */
        var FINISH_REQUIRED = [
            ['courier_partner', 'Courier'],
            ['courier_number', 'Courier Number'],
            ['docket_no', 'Docket Number']
        ];

        function missingFinishPaperwork() {
            var missing = [];
            FINISH_REQUIRED.forEach(function (pair) {
                var el = finishForm.querySelector('[name="' + pair[0] + '"]');
                if (el && !(el.value || '').trim()) { missing.push(pair[1]); }
            });
            return missing;
        }

        function submitFinish() {
            if (!finishForm || state.finishing) { return; }

            var missing = missingFinishPaperwork();
            if (missing.length) {
                ui.toast('warn', 'Fill in the marked fields',
                         missing.join(', ') + ' ' +
                         (missing.length > 1 ? 'are' : 'is') + ' required to close a delivery.');
                var first = finishForm.querySelector(
                    '[name="' + FINISH_REQUIRED.filter(function (p) {
                        return missing.indexOf(p[1]) !== -1;
                    })[0][0] + '"]');
                if (first) { first.focus(); }
                return;
            }

            state.finishing = true;
            var button = finishForm.querySelector('[data-mv="finish-submit"]');
            var label = button ? button.innerHTML : '';
            if (button) { button.disabled = true; }
            clearFinishShort();

            post(finishForm.action, new FormData(finishForm)).then(function (res) {
                var body = res.body || {};
                if (res.status === 409 && body.short) { showFinishShort(body); return; }
                if (res.status === 409 && body.already_finished) {
                    /* Not a failure to report and move on from: the modal is
                       open on a delivery that is already closed, so repaint it
                       into its read-only state and let the operator read what
                       was recorded. The state comes on the next poll tick. */
                    ui.toast('warn', 'Already finished', body.message || '');
                    if (lastPayload) { renderState(lastPayload); }
                    document.dispatchEvent(new CustomEvent('mv:finish-refused', {
                        detail: { delivery_no: body.delivery_no }
                    }));
                    return;
                }
                if (!res.ok || !body.ok) {
                    ui.toast('danger', 'Could not finish',
                             body.message || 'The delivery was not closed.');
                    return;
                }
                if (body.state) { lastPayload = body.state; renderState(body.state); }
                /* The paperwork is offered, not navigated to — the operator
                   decides whether to look at it now or carry on picking. */
                ui.toast(body.level === 'warn' ? 'warn' : 'ok',
                         'Delivery ' + body.delivery_no + ' finished', body.message || '');
                setStatus('Delivery ' + body.delivery_no + ' finished. Focus the next one to carry on.');
                closeFinishModal();
                /* Its focus is spent: the delivery is closed and refuses
                   further scans, so leaving it locked would answer the next
                   scan with a rejection. */
                if (lastPayload && lastPayload.focus_delivery === body.delivery_no) {
                    postFocus('');
                }
            }).catch(function () {
                ui.toast('danger', 'Could not finish', 'Check your connection and try again.');
            }).finally(function () {
                state.finishing = false;
                if (button) { button.disabled = false; button.innerHTML = label; }
            });
        }

        function closeFinishModal() {
            var scrim = document.getElementById('mvFinishModal');
            if (scrim) { scrim.classList.remove('is-open'); }
            document.dispatchEvent(new CustomEvent('mv:modal-closed'));
        }

        if (finishForm) {
            finishForm.addEventListener('submit', function (e) {
                e.preventDefault();
                submitFinish();
            });
        }

        /* ── Change a line's batch ─────────────────────────────────────────
         * SAP dumps sometimes name a batch the warehouse cannot supply and the
         * picker substitutes another, so this is a picker's control as much as
         * a supervisor's — the substitution is decided at the shelf.
         *
         * The endpoint existed and was tested from the day it was written, and
         * nothing on this screen ever called it: Branch Transfer grew a control
         * for its own line-batch endpoint and Outbound never did. So the only
         * way to override a batch here was to post to the URL by hand.
         *
         * Locked once anything is scanned onto the line — repointing an
         * already-scanned unit at a different batch corrupts the dispatch
         * record — and outbound_line_batch re-checks that under a row lock. The
         * cell says so rather than silently refusing.
         */
        function openBatchModal(line) {
            if (!line) { return; }
            if (!state.canEdit) {
                ui.toast('warn', 'Not permitted', 'You may not change a batch on this session.');
                return;
            }
            var scrim = document.createElement('div');
            scrim.className = 'mv-modal-scrim is-open';
            scrim.setAttribute('data-mv', 'edit-batch-modal');
            scrim.innerHTML =
                '<div class="mv-modal" role="dialog" aria-modal="true" aria-labelledby="mvBatchTitle">' +
                '  <div class="mv-modal__head">' +
                '    <h2 class="mv-t-h2" id="mvBatchTitle">Change batch</h2>' +
                '    <button type="button" class="mv-btn mv-btn--ghost mv-btn--icon" data-bt="cancel" aria-label="Close">' +
                '      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" ' +
                '        stroke-width="1.5" stroke-linecap="round"><path d="M6 6l12 12M18 6 6 18"/></svg>' +
                '    </button>' +
                '  </div>' +
                '  <div class="mv-modal__body">' +
                '    <div class="mv-field">' +
                '      <div class="mv-label">Line</div>' +
                '      <div class="mv-t-strong" data-bt="line"></div>' +
                '    </div>' +
                '    <div class="mv-field">' +
                '      <div class="mv-label">Batch on the dump</div>' +
                '      <code class="mv-mono mv-t-strong" data-bt="original"></code>' +
                '    </div>' +
                '    <div class="mv-field">' +
                '      <label class="mv-label" for="mvBatchValue">Replacement batch</label>' +
                '      <input class="mv-input mv-input--mono" id="mvBatchValue" autocomplete="off" data-mv-no-nav>' +
                '      <span class="mv-hint">Stored uppercased. The number the dump carried is kept ' +
                '        alongside it, so the substitution stays visible at dispatch.</span>' +
                '    </div>' +
                '    <div class="mv-alert mv-alert--warn" data-bt="error" hidden></div>' +
                '  </div>' +
                '  <div class="mv-modal__foot">' +
                '    <button type="button" class="mv-btn" data-bt="cancel">Cancel</button>' +
                '    <button type="button" class="mv-btn mv-btn--primary" data-bt="save">Save batch</button>' +
                '  </div>' +
                '</div>';
            scrim.querySelector('[data-bt="line"]').textContent =
                'Delivery ' + line.delivery_no + '  ·  item ' + (line.item_no || '—') +
                '  ·  ' + line.material;
            scrim.querySelector('[data-bt="original"]').textContent =
                line.original_batch || line.batch || '—';
            scrim.querySelector('#mvBatchValue').value = line.batch || '';

            function close() {
                if (scrim.parentNode) { scrim.parentNode.removeChild(scrim); }
                refocusScanInput();
            }
            function fail(message) {
                var box = scrim.querySelector('[data-bt="error"]');
                box.hidden = false;
                box.textContent = message;
            }
            function save() {
                var field = scrim.querySelector('#mvBatchValue');
                var value = (field.value || '').trim();
                if (!value) { fail('Enter a batch number.'); field.focus(); return; }
                var fd = new FormData();
                fd.append('line_id', line.id);
                fd.append('batch', value);
                post(cfg.batchUrl, fd).then(function (res) {
                    var body = res.body || {};
                    if (!res.ok || !body.ok) {
                        fail(body.message || 'That batch could not be saved.');
                        return;
                    }
                    ui.toast('ok', 'Batch changed', (line.batch || '—') + ' → ' + value.toUpperCase());
                    if (body.state) { lastPayload = body.state; renderState(body.state); }
                    close();
                }).catch(function () { fail('Network error — the batch was not changed.'); });
            }

            scrim.addEventListener('click', function (e) {
                if (e.target === scrim || e.target.closest('[data-bt="cancel"]')) { close(); }
                if (e.target.closest('[data-bt="save"]')) { save(); }
            });
            scrim.addEventListener('keydown', function (e) {
                if (e.key === 'Escape') { close(); }
                if (e.key === 'Enter') { e.preventDefault(); save(); }
            });
            mvRoot().appendChild(scrim);
            scrim.querySelector('#mvBatchValue').focus();
            scrim.querySelector('#mvBatchValue').select();
        }

        function openEditScanModal(serial) {
            if (!serial) {
                ui.toast('warn', 'Nothing to edit', 'This row has no assigned serial.');
                return;
            }
            var scrim = document.createElement('div');
            scrim.className = 'mv-modal-scrim is-open';
            scrim.setAttribute('data-mv', 'edit-scan-modal');
            scrim.innerHTML =
                '<div class="mv-modal" role="dialog" aria-modal="true" aria-labelledby="mvEditSerTitle">' +
                '  <div class="mv-modal__head">' +
                '    <h2 class="mv-t-h2" id="mvEditSerTitle">Correct assigned serial</h2>' +
                '    <button type="button" class="mv-btn mv-btn--ghost mv-btn--icon" data-ed="cancel" aria-label="Close">' +
                '      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" ' +
                '        stroke-width="1.5" stroke-linecap="round"><path d="M6 6l12 12M18 6 6 18"/></svg>' +
                '    </button>' +
                '  </div>' +
                '  <div class="mv-modal__body">' +
                '    <div class="mv-field">' +
                '      <div class="mv-label">Currently assigned</div>' +
                '      <code class="mv-mono mv-t-strong" data-ed="current"></code>' +
                '    </div>' +
                '    <div class="mv-field">' +
                '      <label class="mv-label" for="mvEditSerial">Corrected serial</label>' +
                '      <input class="mv-input mv-input--mono" id="mvEditSerial" autocomplete="off" data-mv-no-nav>' +
                '      <span class="mv-hint">The batch is kept and the serial replaced. A number shorter ' +
                '        than three digits is padded — 7 becomes 007.</span>' +
                '    </div>' +
                '    <div class="mv-alert mv-alert--warn" data-ed="error" hidden></div>' +
                '  </div>' +
                '  <div class="mv-modal__foot">' +
                '    <button type="button" class="mv-btn" data-ed="cancel">Cancel</button>' +
                '    <button type="button" class="mv-btn mv-btn--primary" data-ed="save">Save serial</button>' +
                '  </div>' +
                '</div>';
            scrim.querySelector('[data-ed="current"]').textContent = serial;

            function close() {
                if (scrim.parentNode) { scrim.parentNode.removeChild(scrim); }
                refocusScanInput();
            }
            function fail(message) {
                var box = scrim.querySelector('[data-ed="error"]');
                box.hidden = false;
                box.textContent = message;
            }
            function save() {
                var field = scrim.querySelector('#mvEditSerial');
                var value = (field.value || '').trim();
                if (!value) { fail('Enter the corrected serial.'); field.focus(); return; }
                var fd = new FormData();
                fd.append('serial_identity', serial);
                fd.append('serial', value);
                post(cfg.editUrl, fd).then(function (res) {
                    var body = res.body || {};
                    if (!res.ok || !body.ok) {
                        fail(body.message || 'That serial could not be saved.');
                        return;
                    }
                    ui.toast('ok', 'Serial corrected', serial + ' → ' + body.serial_only);
                    if (body.state) { lastPayload = body.state; renderState(body.state); }
                    /* The old serial is still printed on whichever log rows
                       carry it; repoint them rather than leaving a control that
                       edits a serial that no longer exists. */
                    repointSerialRows(serial, body.serial_only);
                    close();
                }).catch(function () { fail('Network error — the serial was not changed.'); });
            }

            scrim.addEventListener('click', function (e) {
                if (e.target === scrim || e.target.closest('[data-ed="cancel"]')) { close(); }
                if (e.target.closest('[data-ed="save"]')) { save(); }
            });
            scrim.addEventListener('keydown', function (e) {
                if (e.key === 'Enter') { e.preventDefault(); save(); }
                if (e.key === 'Escape') { close(); }
            });
            mvRoot().appendChild(scrim);
            scrim.querySelector('#mvEditSerial').focus();
        }

        function repointSerialRows(oldSerial, newSerial) {
            Array.prototype.forEach.call(
                document.querySelectorAll('[data-mv="edit-scan"], [data-mv="delete-scan"]'),
                function (btn) { if (btn.dataset.serial === oldSerial) { btn.dataset.serial = newSerial; } });
            Array.prototype.forEach.call(document.querySelectorAll('.mv-scanrow'), function (row) {
                if (row.dataset.serial !== oldSerial) { return; }
                row.dataset.serial = newSerial;
                var label = row.querySelector('.mv-scanrow__serial');
                if (label) { label.textContent = label.textContent.replace(oldSerial, newSerial); }
            });
        }

        /* ── Delete a recorded scan (admins) ─────────────────────────────────
         * Undo only reaches the last scan; removing a specific one found later
         * needs a supervisor. Confirmed first, and confirmed again on the
         * server — the button is only drawn for an admin, but that is a
         * convenience, not the boundary (views.outbound_scan_delete). */
        function confirmDeleteScan(serial) {
            if (!serial) { return; }
            if (!state.canDelete) {
                ui.toast('warn', 'Not permitted', 'Only a Meril-V admin can delete a recorded scan.');
                return;
            }
            ui.confirm(
                {
                    title: 'Delete this scan?',
                    message: 'Serial ' + serial + ' will be removed and its unit returned to '
                        + 'pending. The deletion is recorded in the scan log. This cannot be undone.',
                    okLabel: 'Delete scan'
                },
                function () { deleteScan(serial); }
            );
        }

        function deleteScan(serial) {
            var fd = new FormData();
            fd.append('serial_identity', serial);
            post(cfg.deleteUrl, fd).then(function (res) {
                var body = res.body || {};
                if (!res.ok || !body.ok) {
                    ui.toast('danger', 'Could not delete', body.message || 'The scan was not deleted.');
                    return;
                }
                prependLogRow({
                    family: 'warn',
                    reasonLabel: 'DELETED',
                    code: body.removed || serial,
                    raw: body.raw_scan || '',
                    accepted: false,
                    message: 'Deleted by an admin — returned to pending.',
                    canEdit: false
                });
                ui.toast('ok', 'Scan deleted', body.removed +
                    (body.delivery_no ? (' — ' + body.delivery_no + ' updated') : ''));
                if (body.state) { lastPayload = body.state; renderState(body.state); }
            }).catch(function () {
                ui.toast('danger', 'Could not delete', 'Check your connection and try again.');
            }).finally(refocusScanInput);
        }

        /* ── Keyboard shortcuts ────────────────────────────────────────────
         * Esc: clear focus. Ctrl+Z: undo. Ctrl+Enter: open Finish Delivery.
         * /: focus the delivery filter. Tab: default browser behaviour. */
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && document.activeElement === els.input) { postFocus(''); return; }
            if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') { e.preventDefault(); postUndo(); return; }
            if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
                e.preventDefault();
                var finishBtn = root.querySelector('[data-mv="finish-btn"]');
                if (finishBtn) { finishBtn.click(); }
                return;
            }
            if (e.key === '/' && document.activeElement !== els.input &&
                !/^(INPUT|TEXTAREA)$/.test(document.activeElement.tagName)) {
                e.preventDefault();
                if (els.deliveryFilter) { els.deliveryFilter.focus(); }
            }
        });

        function esc(s) {
            return String(s == null ? '' : s)
                .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        }

        /* First paint. */
        poll();
    }

    document.addEventListener('DOMContentLoaded', function () {
        Array.prototype.forEach.call(
            document.querySelectorAll('[data-mv-scanner]'), init);
    });
})();
