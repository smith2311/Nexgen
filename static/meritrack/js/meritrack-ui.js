/* ================================================================
   MERITRACK UI LAYER
   Progressive-enhancement behaviours shared by every MeriTrack page.

   Nothing here is required for a page to function — if this file
   fails to load the pages keep working, they just lose the polish.

   Provides
     • scroll reveals with stagger              (.mt-reveal / .is-in)
     • animated counters for stat tiles
     • pointer ripples on buttons
     • horizontal scroll containers for wide tables
     • "row → card" stacking for tables marked data-stack
     • modal niceties: Esc, scroll-lock, animated close, focus return
     • window.mtToast() notification stack
     • mobile bottom-nav wiring + safe-area padding
     • back-to-top button and top progress bar
   ================================================================ */
(function () {
    'use strict';

    if (window.__meritrackUI) return;
    window.__meritrackUI = true;

    var REDUCED = false;
    try {
        REDUCED = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    } catch (e) { /* very old browser */ }

    var HAS_IO = typeof window.IntersectionObserver === 'function';
    var HAS_MO = typeof window.MutationObserver === 'function';

    function $(sel, root) { return (root || document).querySelector(sel); }
    function $$(sel, root) {
        return Array.prototype.slice.call((root || document).querySelectorAll(sel));
    }
    function isPhone() { return window.matchMedia('(max-width: 768px)').matches; }
    function onIdle(fn) {
        if (typeof window.requestIdleCallback === 'function') {
            window.requestIdleCallback(fn, { timeout: 600 });
        } else {
            setTimeout(fn, 60);
        }
    }

    /* =============================================================
       1. SCROLL REVEALS
       ============================================================= */
    var REVEAL_TARGETS = [
        '.mt-page > .mt-card',
        '.mt-page > .card',
        '.mt-page > .section-title',
        '.mt-page > .chart-grid > .chart-card',
        '.mt-page > .mt-grid-2 > *',
        '.mt-page > .mt-grid-3 > *',
        '.mtd-stats > .mtd-stat-card',
        '.mt-stats > .stat-card',
        '.stats-row > .stat-card',
        '.recon-stats > .stat-card',
        '.sp-stats > .sp-stat',
        '.ea-tier-row > .ea-tier',
        '.tier-summary > .tier-card',
        '.detail-meta > .meta-card',
        '.mt-page > .progress-card',
        '.mt-page > .analytics-cta',
        /* Static content only. A step's primary control (Start, Next, Save) is
           also a direct child here, and a reveal that never fires leaves it at
           opacity:0 — an invisible button with nothing on screen to explain it. */
        '.mt-scanner .mt-body > .mt-section.active > *:not(button):not(a):not(.mt-btn)'
    ].join(',');

    var revealObserver = null;

    function initRevealObserver() {
        if (!HAS_IO || REDUCED) return null;
        return new IntersectionObserver(function (entries) {
            entries.forEach(function (entry) {
                if (!entry.isIntersecting) return;
                var el = entry.target;
                el.classList.add('is-in');
                revealObserver.unobserve(el);
                // Drop the transition-delay once played so later
                // interactions (hover, re-render) feel immediate.
                setTimeout(function () { el.classList.add('is-done'); }, 900);
            });
        }, { rootMargin: '0px 0px -8% 0px', threshold: 0.06 });
    }

    function reveal(root) {
        var nodes = $$(REVEAL_TARGETS, root && root !== document ? root : document);
        if (!nodes.length) return;

        if (!HAS_IO || REDUCED) {
            nodes.forEach(function (el) { el.classList.add('mt-reveal', 'is-in', 'is-done'); });
            return;
        }
        if (!revealObserver) revealObserver = initRevealObserver();
        scheduleRevealSweep();

        // Stagger index resets per parent so each row cascades on its own.
        var seen = new Map();
        nodes.forEach(function (el) {
            if (el.classList.contains('mt-reveal')) return;
            var parent = el.parentNode;
            var i = seen.get(parent) || 0;
            seen.set(parent, i + 1);
            el.style.setProperty('--mt-i', Math.min(i, 8));
            el.classList.add('mt-reveal');
            revealObserver.observe(el);
        });
    }

    /* The observer can measure at a moment that lies: an ancestor still
       display:none, or a section mid-entrance-animation whose transform is
       briefly the containing block for a position:fixed child. It records
       "not intersecting" for something the user can plainly see, and .is-in
       never arrives — leaving the node stuck at opacity:0 forever.
       So sweep once after things settle: anything still un-revealed while
       inside the viewport gets shown. Genuinely off-screen nodes are left
       alone and keep their scroll reveal. */
    var sweepTimer = null;

    function sweepStuckReveals() {
        $$('.mt-reveal:not(.is-in)').forEach(function (el) {
            var r = el.getBoundingClientRect();
            if (!r.width && !r.height) return;                       // still hidden
            if (r.top >= window.innerHeight || r.bottom <= 0) return; // truly off-screen
            el.classList.add('is-in', 'is-done');
            if (revealObserver) revealObserver.unobserve(el);
        });
    }

    function scheduleRevealSweep() {
        if (!HAS_IO || REDUCED) return;
        clearTimeout(sweepTimer);
        sweepTimer = setTimeout(sweepStuckReveals, 1200);
    }

    /* Cards injected by page JS (session lists, rep cards …) get a
       one-shot entrance instead of a scroll reveal. */
    var ENTER_PARENTS = '.mtd-sessions-grid, .sp-cards, #batchStatusList, #myScansHospitalSections, #inventoryTree, .order-cards';

    function animateInjected(parent) {
        if (REDUCED) return;
        var kids = Array.prototype.filter.call(parent.children, function (n) {
            return n.nodeType === 1 && !n.classList.contains('mt-enter');
        });
        kids.slice(0, 24).forEach(function (el, i) {
            el.style.setProperty('--mt-i', i);
            el.classList.add('mt-enter');
        });
    }

    /* =============================================================
       2. ANIMATED COUNTERS
       ============================================================= */
    var COUNTER_SELECTOR = [
        '.mtd-stat-val',
        '.stat-card .stat-value',
        '.stat-card .num',
        '.sp-stat-val',
        '.tier-count',
        '.ea-tier-val',
        '.mtd-tab-badge',
        '#totalRecons', '#completedRecons', '#inProgressRecons'
    ].join(',');

    var NUM_RE = /^\s*([+-]?[\d.,]+)\s*(%|€|\$)?\s*$/;

    function parseNum(text) {
        var m = NUM_RE.exec(text || '');
        if (!m) return null;
        var raw = m[1];
        // Treat "1,234" as a thousands separator but "1,5" as decimal.
        var normalised = /,\d{3}(\D|$)/.test(raw + ' ') || raw.indexOf('.') > -1
            ? raw.replace(/,/g, '')
            : raw.replace(',', '.');
        var n = parseFloat(normalised);
        return isFinite(n) ? { value: n, suffix: m[2] || '', hadGrouping: /,/.test(raw) && !/,\d{1,2}$/.test(raw) } : null;
    }

    function formatNum(n, decimals, grouped) {
        var v = decimals ? n.toFixed(decimals) : String(Math.round(n));
        if (grouped && !decimals) v = v.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
        return v;
    }

    /* Write through here, never straight to textContent. Recording the exact
       string we wrote lets the MutationObserver tell our own writes apart from
       the page's. A boolean flag cannot: observers fire on a later microtask,
       by which time a synchronously-cleared flag is already false — every
       animation frame would then start another animation. */
    function writeNum(el, str) {
        el.__mtWritten = str;
        el.textContent = str;
    }

    function countUp(el, target, suffix, decimals, grouped) {
        var from = el.__mtLast != null ? el.__mtLast : 0;
        el.__mtLast = target;

        if (REDUCED || Math.abs(target - from) < 0.005) {
            if (el.__mtRaf) { cancelAnimationFrame(el.__mtRaf); el.__mtRaf = null; }
            el.classList.remove('mt-num-running');
            writeNum(el, formatNum(target, decimals, grouped) + suffix);
            return;
        }

        if (el.__mtRaf) cancelAnimationFrame(el.__mtRaf);
        var duration = Math.min(900, 260 + Math.abs(target - from) * 9);
        var start = null;
        el.classList.add('mt-num-running');
        el.classList.remove('mt-num-settled');

        function step(ts) {
            if (start === null) start = ts;
            var p = Math.min(1, (ts - start) / duration);
            // easeOutExpo
            var eased = p === 1 ? 1 : 1 - Math.pow(2, -10 * p);
            writeNum(el, formatNum(from + (target - from) * eased, decimals, grouped) + suffix);
            if (p < 1) {
                el.__mtRaf = requestAnimationFrame(step);
            } else {
                el.__mtRaf = null;
                el.classList.remove('mt-num-running');
                el.classList.add('mt-num-settled');
                setTimeout(function () { el.classList.remove('mt-num-settled'); }, 420);
            }
        }
        el.__mtRaf = requestAnimationFrame(step);
    }

    function tryCount(el) {
        var text = el.textContent;
        // Our own animation frames must not feed back into a new animation.
        if (el.__mtWritten != null && text === el.__mtWritten) return;
        var parsed = parseNum(text);
        if (!parsed) { el.__mtLast = null; return; }
        var decimals = /\.\d/.test(text) ? 1 : 0;
        countUp(el, parsed.value, parsed.suffix, decimals, parsed.hadGrouping);
    }

    function watchCounters(root) {
        if (!HAS_MO) return;
        $$(COUNTER_SELECTOR, root || document).forEach(function (el) {
            if (el.__mtCounted) return;
            el.__mtCounted = true;
            tryCount(el);
            new MutationObserver(function () { tryCount(el); })
                .observe(el, { childList: true, characterData: true, subtree: true });
        });
    }

    /* =============================================================
       3. RIPPLES
       ============================================================= */
    var RIPPLE_SELECTOR = '.btn-mt, .mt-btn, .mtd-btn, .hero-btn, .filter-tab,' +
        '.mtd-filter-btn, .ep-filter, .ms-filter, .mt-pill-tab, .mtd-action-btn,' +
        '.mt-action-btn, .btn-header, .sp-tab-btn, .mtd-tab, .dash-tab, .tab-btn,' +
        '.month-nav-btn, .mt-back-btn, .mtd-btn-ack, .link-btn,' +
        /* dashboard view switcher + filter-sheet chips */
        '.mtd-vt-btn, .mtd-chip';

    function ripple(ev) {
        if (REDUCED) return;
        var btn = ev.target.closest ? ev.target.closest(RIPPLE_SELECTOR) : null;
        if (!btn || btn.disabled || btn.dataset.noRipple != null) return;

        var rect = btn.getBoundingClientRect();
        if (!rect.width) return;
        var size = Math.max(rect.width, rect.height);
        var x = (ev.clientX != null ? ev.clientX : rect.left + rect.width / 2) - rect.left;
        var y = (ev.clientY != null ? ev.clientY : rect.top + rect.height / 2) - rect.top;

        var span = document.createElement('span');
        span.className = 'mt-ripple';
        span.style.width = span.style.height = size + 'px';
        span.style.left = (x - size / 2) + 'px';
        span.style.top = (y - size / 2) + 'px';
        btn.appendChild(span);
        setTimeout(function () { if (span.parentNode) span.parentNode.removeChild(span); }, 620);
    }

    /* =============================================================
       4. TABLES — scroll containers + stacked card mode
       ============================================================= */
    var SKIP_WRAP_ANCESTORS = '.mt-tscroll, .table-responsive-wrap, .table-scroll-wrap,' +
        '.hosp-tbl-wrap, .dataTables_wrapper, .dataTables_scrollBody, .dt-container,' +
        '#expiryDtTableWrap, .hosp-scan-body, [data-no-tscroll]';

    function ancestorScrolls(el) {
        var node = el.parentNode;
        var depth = 0;
        while (node && node.nodeType === 1 && depth++ < 6) {
            if (node.classList && (node.classList.contains('mt-page') ||
                node.classList.contains('mt-body'))) return false;
            var ox;
            try { ox = getComputedStyle(node).overflowX; } catch (e) { ox = ''; }
            if (ox === 'auto' || ox === 'scroll') return true;
            node = node.parentNode;
        }
        return false;
    }

    function wrapTable(table) {
        if (table.__mtWrapped) return;
        table.__mtWrapped = true;
        if (table.closest(SKIP_WRAP_ANCESTORS)) return;
        if (ancestorScrolls(table)) return;

        var wrap = document.createElement('div');
        wrap.className = 'mt-tscroll';
        table.parentNode.insertBefore(wrap, table);
        wrap.appendChild(table);
        hintScroll(wrap);
    }

    /* Copy <thead> labels onto each <td> so CSS can render a row as a
       titled card on phones. Re-runs whenever the tbody is repainted. */
    function stampLabels(table) {
        var heads = $$('thead th', table).map(function (th) {
            // strip icons/badges, keep the readable label
            return (th.textContent || '').replace(/\s+/g, ' ').trim();
        });
        if (!heads.length) return false;

        $$('tbody tr', table).forEach(function (tr) {
            var cells = tr.children;
            for (var i = 0; i < cells.length; i++) {
                var td = cells[i];
                if (td.tagName !== 'TD') continue;
                if (td.hasAttribute('colspan')) continue;
                var label = heads[i] != null ? heads[i] : '';
                if (label) {
                    td.setAttribute('data-label', label);
                } else {
                    td.removeAttribute('data-label');
                    // header-less trailing column is almost always actions
                    if (i === cells.length - 1 && td.querySelector('a,button')) {
                        td.classList.add('mt-cell-actions');
                    }
                }
            }
        });
        return true;
    }

    function setupStackedTable(table) {
        if (!stampLabels(table)) return;
        table.classList.add('mt-stacked');

        if (!HAS_MO || table.__mtStackObserved) return;
        table.__mtStackObserved = true;
        var tbody = table.tBodies && table.tBodies[0];
        if (!tbody) return;

        var pending = false;
        new MutationObserver(function () {
            if (pending) return;
            pending = true;
            requestAnimationFrame(function () {
                pending = false;
                stampLabels(table);
                animateRows(tbody);
            });
        }).observe(tbody, { childList: true });
    }

    function animateRows(tbody) {
        if (REDUCED) return;
        tbody.classList.remove('mt-rows-in');
        var rows = tbody.rows;
        var n = Math.min(rows.length, 18);
        for (var i = 0; i < n; i++) rows[i].style.setProperty('--mt-i', i);
        // force a reflow so the animation restarts
        void tbody.offsetWidth;
        tbody.classList.add('mt-rows-in');
    }

    function hintScroll(box) {
        if (!box || box.__mtHinted) return;
        box.__mtHinted = true;
        function check() {
            var over = box.scrollWidth > box.clientWidth + 2;
            box.classList.toggle('can-scroll', over);
            box.classList.toggle('scroll-end', box.scrollLeft + box.clientWidth >= box.scrollWidth - 8);
        }
        box.addEventListener('scroll', check, { passive: true });
        window.addEventListener('resize', check, { passive: true });
        if (HAS_MO) {
            new MutationObserver(function () { requestAnimationFrame(check); })
                .observe(box, { childList: true, subtree: true });
        }
        check();
    }

    function initTables(root) {
        var scope = root || document;
        // Label-stamp opted-in tables first so the CSS can card them up.
        $$('table[data-stack]', scope).forEach(setupStackedTable);
        // Everything still gets a scroll box: stacking only kicks in at ≤640px,
        // so between 641px and 768px these tables need somewhere to scroll.
        $$('.mt-page table, .mt-scanner table', scope).forEach(wrapTable);
        $$('.mt-tscroll, .table-responsive-wrap, .table-scroll-wrap, .hosp-tbl-wrap', scope)
            .forEach(hintScroll);
    }

    /* =============================================================
       5. MODALS
       ============================================================= */
    var scrollLocks = 0;
    var savedScrollY = 0;

    function lockScroll() {
        if (scrollLocks++ > 0) return;
        savedScrollY = window.scrollY || 0;
        document.body.style.overflow = 'hidden';
        document.body.style.touchAction = 'none';
        /* Modals live inside .mt-page, which the motion layer gives its own
           stacking context (a filling opacity animation). Their z-index is
           therefore scoped to .mt-page, which sits below the body-level
           bottom nav — so on phones the bar painted over the sheet's action
           row and buried Save / Update / Cancel. Retire the bar while a
           modal is open; it is unreachable behind the scrim anyway. */
        document.body.classList.add('mt-modal-open');
    }
    function unlockScroll() {
        if (--scrollLocks > 0) return;
        scrollLocks = 0;
        document.body.style.overflow = '';
        document.body.style.touchAction = '';
        document.body.classList.remove('mt-modal-open');
    }

    function isOpen(overlay) {
        if (!overlay) return false;
        if (overlay.classList.contains('active') || overlay.classList.contains('open')) return true;
        var d = overlay.style.display;
        return d === 'flex' || d === 'block';
    }

    function watchModals() {
        if (!HAS_MO) return;
        var overlays = $$('.modal-overlay, #sessionModal, #productRemarkModal, .mt-serial-overlay');
        overlays.forEach(function (overlay) {
            if (overlay.__mtModal) return;
            overlay.__mtModal = true;
            var wasOpen = isOpen(overlay);

            function sync() {
                var open = isOpen(overlay);
                if (open === wasOpen) return;
                wasOpen = open;
                if (open) {
                    lockScroll();
                    overlay.classList.remove('mt-closing');
                    openStack.push(overlay);
                    // focus the first sensible control for keyboard users
                    var target = overlay.querySelector('input:not([type=hidden]), select, textarea, .modal-close, button');
                    if (target && !isPhone()) { try { target.focus({ preventScroll: true }); } catch (e) { /* noop */ } }
                } else {
                    unlockScroll();
                    var i = openStack.indexOf(overlay);
                    if (i > -1) openStack.splice(i, 1);
                }
            }
            new MutationObserver(sync).observe(overlay, { attributes: true, attributeFilter: ['class', 'style'] });
            if (wasOpen) { lockScroll(); openStack.push(overlay); }
        });
    }

    var openStack = [];

    function closeTop() {
        var overlay = openStack[openStack.length - 1];
        if (!overlay) return false;
        // Prefer the page's own close handler so its state stays consistent.
        var closer = overlay.querySelector('.modal-close, .session-modal-close, [data-modal-close]');
        if (closer) { closer.click(); return true; }
        overlay.classList.remove('active', 'open');
        overlay.style.display = 'none';
        return true;
    }

    /* =============================================================
       6. TOASTS
       ============================================================= */
    var toastHost = null;
    var ICONS = {
        success: 'fa-circle-check',
        error: 'fa-circle-exclamation',
        warning: 'fa-triangle-exclamation',
        info: 'fa-circle-info'
    };

    function mtToast(message, type, ms) {
        if (!message) return;
        type = type || 'info';
        if (!toastHost) {
            toastHost = document.createElement('div');
            toastHost.className = 'mt-toast-host';
            toastHost.setAttribute('role', 'status');
            toastHost.setAttribute('aria-live', 'polite');
            document.body.appendChild(toastHost);
        }
        var el = document.createElement('div');
        el.className = 'mt-toast mt-toast-' + type;
        var icon = document.createElement('i');
        icon.className = 'fas ' + (ICONS[type] || ICONS.info);
        icon.setAttribute('aria-hidden', 'true');
        var text = document.createElement('span');
        text.textContent = message;
        var close = document.createElement('button');
        close.className = 'mt-toast-close';
        close.type = 'button';
        close.setAttribute('aria-label', 'Dismiss');
        close.innerHTML = '&#x2715;';
        el.appendChild(icon);
        el.appendChild(text);
        el.appendChild(close);
        toastHost.appendChild(el);

        var timer = setTimeout(dismiss, ms || (type === 'error' ? 6000 : 3800));
        function dismiss() {
            clearTimeout(timer);
            el.classList.add('mt-toast-out');
            setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, 260);
        }
        close.addEventListener('click', dismiss);
        // keep at most 4 on screen
        while (toastHost.children.length > 4) toastHost.removeChild(toastHost.firstChild);
        return dismiss;
    }

    /* =============================================================
       7. BOTTOM NAV
       ============================================================= */
    var navEl = null;
    var navHome = null;

    function centreActiveTab(nav, smooth) {
        var active = nav.querySelector('a.active');
        if (!active || nav.scrollWidth <= nav.clientWidth + 2) return;
        var left = active.offsetLeft - (nav.clientWidth - active.offsetWidth) / 2;
        left = Math.max(0, left);
        try {
            nav.scrollTo({ left: left, behavior: (smooth && !REDUCED) ? 'smooth' : 'auto' });
        } catch (e) { nav.scrollLeft = left; }
    }

    /* Below 768px the nav is lifted out of `.mtd-header` (overflow:hidden,
       animated background) and mounted on <body> as a fixed bottom bar. */
    function placeNav() {
        if (!navEl || !navHome) return;
        if (isPhone()) {
            if (navEl.parentNode !== document.body) {
                document.body.appendChild(navEl);
                navEl.classList.add('mt-nav-fixed');
                document.body.classList.add('mt-nav-present');
                centreActiveTab(navEl, false);
            }
        } else if (navEl.parentNode !== navHome) {
            navHome.appendChild(navEl);
            navEl.classList.remove('mt-nav-fixed');
            document.body.classList.remove('mt-nav-present');
            navEl.scrollLeft = 0;
        }
    }

    function initNav() {
        if (navEl) { placeNav(); return; }
        navEl = $('.mt-nav');
        if (!navEl) {
            document.body.classList.remove('mt-nav-present');
            return;
        }
        navHome = navEl.parentNode;
        placeNav();
        hintScroll(navEl);
        centreActiveTab(navEl, false);

        try {
            var mq = window.matchMedia('(max-width: 768px)');
            if (typeof mq.addEventListener === 'function') mq.addEventListener('change', placeNav);
            else if (typeof mq.addListener === 'function') mq.addListener(placeNav);
        } catch (e) {
            window.addEventListener('resize', placeNav, { passive: true });
        }
    }

    /* =============================================================
       8. BACK TO TOP + HEADER SHRINK + PROGRESS BAR
       ============================================================= */
    function initScrollUI() {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'mt-to-top';
        btn.setAttribute('aria-label', 'Back to top');
        btn.innerHTML = '<i class="fas fa-arrow-up" aria-hidden="true"></i>';
        btn.addEventListener('click', function () {
            try {
                window.scrollTo({ top: 0, behavior: REDUCED ? 'auto' : 'smooth' });
            } catch (e) { window.scrollTo(0, 0); }
        });
        document.body.appendChild(btn);

        var header = $('.mtd-header') || $('.mt-hero-header');
        var ticking = false;

        function onScroll() {
            if (ticking) return;
            ticking = true;
            requestAnimationFrame(function () {
                ticking = false;
                var y = window.scrollY || document.documentElement.scrollTop || 0;
                btn.classList.toggle('is-on', y > 420);
                if (header) header.classList.toggle('mt-scrolled', y > 120);
            });
        }
        window.addEventListener('scroll', onScroll, { passive: true });
        onScroll();
    }

    var progressEl = null;
    var inflight = 0;

    function progress(on) {
        if (!progressEl) {
            progressEl = document.createElement('div');
            progressEl.className = 'mt-progress-top';
            document.body.appendChild(progressEl);
        }
        inflight = Math.max(0, inflight + (on ? 1 : -1));
        progressEl.classList.toggle('is-on', inflight > 0);
    }

    function instrumentFetch() {
        if (typeof window.fetch !== 'function' || window.fetch.__mtWrapped) return;
        var original = window.fetch;
        var wrapped = function () {
            var showing = false;
            var timer = setTimeout(function () { showing = true; progress(true); }, 220);
            function done() {
                clearTimeout(timer);
                if (showing) progress(false);
            }
            var p;
            try {
                p = original.apply(this, arguments);
            } catch (err) {
                done();
                throw err;
            }
            if (p && typeof p.then === 'function') {
                p.then(done, done);
                return p;
            }
            done();
            return p;
        };
        wrapped.__mtWrapped = true;
        window.fetch = wrapped;
    }

    /* =============================================================
       9. TAB / ACCORDION MOTION HOOKS
       ============================================================= */
    var TAB_SELECTOR = '.mtd-tab, .sp-tab-btn, .mt-pill-tab, .dash-tab, .tab-btn, .filter-tab, .mtd-filter-btn, .ep-filter, .ms-filter';

    function onTabClick(ev) {
        var tab = ev.target.closest ? ev.target.closest(TAB_SELECTOR) : null;
        if (!tab) return;
        // Let the page's own handler set .active first, then re-run the
        // enhancements against whatever it revealed.
        setTimeout(function () {
            var panel = $('.mtd-tab-content.active') || $('.sp-tab-content.active') || $('.tab-content.active');
            if (panel) {
                initTables(panel);
                watchCounters(panel);
                reveal(panel);
            }
            var nav = tab.parentNode;
            if (nav && nav.scrollWidth > nav.clientWidth) {
                var left = tab.offsetLeft - (nav.clientWidth - tab.offsetWidth) / 2;
                try { nav.scrollTo({ left: Math.max(0, left), behavior: REDUCED ? 'auto' : 'smooth' }); } catch (e) { /* noop */ }
            }
        }, 60);
    }

    /* =============================================================
       10. GLOBAL OBSERVER — keep enhancements applied to new DOM
       ============================================================= */
    function watchDom() {
        if (!HAS_MO) return;
        var root = $('.mt-page') || $('.mt-scanner') || document.body;
        var queued = false;
        var addedParents = new Set();

        new MutationObserver(function (records) {
            var relevant = false;
            records.forEach(function (r) {
                for (var i = 0; i < r.addedNodes.length; i++) {
                    var n = r.addedNodes[i];
                    if (n.nodeType !== 1) continue;
                    relevant = true;
                    if (r.target && r.target.matches && r.target.matches(ENTER_PARENTS)) {
                        addedParents.add(r.target);
                    }
                }
            });
            if (!relevant || queued) return;
            queued = true;
            requestAnimationFrame(function () {
                queued = false;
                addedParents.forEach(animateInjected);
                addedParents.clear();
                onIdle(function () {
                    initTables(document);
                    watchCounters(document);
                    watchModals();
                });
            });
        }).observe(root, { childList: true, subtree: true });
    }

    /* =============================================================
       11. BOOT
       ============================================================= */
    function boot() {
        try { initNav(); } catch (e) { /* noop */ }
        try { reveal(document); } catch (e) { /* noop */ }
        try { watchCounters(document); } catch (e) { /* noop */ }
        try { watchModals(); } catch (e) { /* noop */ }
        try { initScrollUI(); } catch (e) { /* noop */ }
        try { instrumentFetch(); } catch (e) { /* noop */ }

        document.addEventListener('pointerdown', ripple, { passive: true });
        document.addEventListener('click', onTabClick, true);
        document.addEventListener('keydown', function (ev) {
            if (ev.key === 'Escape' && openStack.length) {
                if (closeTop()) ev.stopPropagation();
            }
        });

        // Tables run at load so DataTables has already built its wrappers.
        function afterLoad() {
            try { initTables(document); } catch (e) { /* noop */ }
            try { watchDom(); } catch (e) { /* noop */ }
            try { sweepStuckReveals(); } catch (e) { /* noop */ }
            $$(ENTER_PARENTS).forEach(function (p) { if (p.children.length) animateInjected(p); });
        }
        if (document.readyState === 'complete') onIdle(afterLoad);
        else window.addEventListener('load', function () { onIdle(afterLoad); });

        window.addEventListener('orientationchange', function () {
            setTimeout(function () { try { initNav(); } catch (e) { /* noop */ } }, 250);
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }

    /* =============================================================
       12. PUBLIC API
       ============================================================= */
    window.mtToast = mtToast;
    window.MeriTrackUI = {
        toast: mtToast,
        reveal: reveal,
        tables: initTables,
        counters: watchCounters,
        modals: watchModals,
        progress: progress,
        animateRows: animateRows,
        animateInjected: animateInjected,
        reducedMotion: function () { return REDUCED; },
        /* Flash a table row green — handy after an inline save. */
        flashRow: function (row) {
            if (!row || REDUCED) return;
            row.classList.remove('mt-row-flash');
            void row.offsetWidth;
            row.classList.add('mt-row-flash');
        },
        /* Mark a button busy while an async action runs. */
        busy: function (btn, on) {
            if (!btn) return;
            btn.classList.toggle('is-busy', on !== false);
            if (on === false) btn.removeAttribute('aria-busy');
            else btn.setAttribute('aria-busy', 'true');
        },
        shake: function (el) {
            if (!el || REDUCED) return;
            el.classList.remove('mt-shake');
            void el.offsetWidth;
            el.classList.add('mt-shake');
            setTimeout(function () { el.classList.remove('mt-shake'); }, 420);
        }
    };
})();
