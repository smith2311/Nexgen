/* MeriTrack Warehouse Dashboard */
'use strict';

const MTD_API = '/meritrack/api';

// ── Config ────────────────────────────────────────────────────────────────────
const _cfg = (() => {
    try { return JSON.parse(document.getElementById('mtd-config').textContent); }
    catch { return {}; }
})();

// ── State ─────────────────────────────────────────────────────────────────────
let _sessions     = [];
let _expiryItems  = [];
let _stockItems   = [];
let _typeFilter   = 'all';
let _expiryLevel  = 'all';
let _expiryLoaded = false;   // fetch has been kicked off
let _expiryReady  = false;   // …and has landed, so the alternate views can paint
let _stockLoaded  = false;
let _stockReady   = false;
let _sessionsLoaded = false;   // keeps the server-rendered skeletons up until the fetch lands

// Presentation. Every tab belongs to a view group, and each group remembers
// its own Cards / Table / Kanban choice independently — switching to Table on
// All Scans must not flip the session board too.
//
// Kanban needs a column axis. Sessions column up by status, so their board
// merges the three status tabs into a dedicated pane. Expiry columns up by
// tier, and paints inside its own pane. Scans have no such axis, so that
// group is Cards + Table only and the Kanban button hides there.
const SESSION_TABS = ['tab-active', 'tab-awaiting', 'tab-closed'];
const VIEW_GROUPS = {
    'tab-active': 'session', 'tab-awaiting': 'session', 'tab-closed': 'session',
    'tab-expiry': 'expiry',
    'tab-stock':  'stock',
};
const GROUP_VIEWS = {
    session: ['cards', 'table', 'kanban'],
    expiry:  ['cards', 'table', 'kanban'],
    stock:   ['cards', 'table'],
};
const VIEW_KEYS     = { session: 'mtd.sessionView', expiry: 'mtd.expiryView', stock: 'mtd.stockView' };
const VIEW_DEFAULTS = { session: 'cards',           expiry: 'table',          stock: 'table' };

let _activeTab = 'tab-active';
const _views = Object.fromEntries(Object.keys(VIEW_KEYS).map(g => {
    let saved = null;
    try { saved = localStorage.getItem(VIEW_KEYS[g]); } catch { /* private mode */ }
    return [g, GROUP_VIEWS[g].includes(saved) ? saved : VIEW_DEFAULTS[g]];
}));

function currentGroup() { return VIEW_GROUPS[_activeTab] || 'session'; }

// Advanced session filters
let _sessionFilters = { year: '', month: '', date: '', salesperson: '', hospital: '' };

// ── Helpers ───────────────────────────────────────────────────────────────────
async function fetchJSON(url) {
    const r = await fetch(url, { credentials: 'same-origin' });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
}

async function postJSON(url, body) {
    const csrfToken = document.cookie.match(/csrftoken=([^;]+)/)?.[1] || '';
    const r = await fetch(url, {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
        body: JSON.stringify(body),
    });
    return r;
}

function fmtNum(n) { return (n ?? 0).toLocaleString(); }

function relativeDate(iso) {
    if (!iso) return '—';
    const d = new Date(iso), now = new Date();
    const diff = Math.round((now - d) / 60000);
    if (diff < 2)  return 'just now';
    if (diff < 60) return `${diff}m ago`;
    if (diff < 1440) return `${Math.round(diff/60)}h ago`;
    return d.toLocaleDateString('en-IN', { day:'2-digit', month:'short' });
}

function showAlert(msg, type='danger') {
    const el = document.getElementById('alertContainer');
    if (!el) return;
    const a = document.createElement('div');
    a.className = `mtd-alert mtd-alert-${type}`;
    a.textContent = msg;
    el.appendChild(a);
    setTimeout(() => a.remove(), 5000);
}

// ── Status/Type labels ────────────────────────────────────────────────────────
const STATUS_LABELS = {
    active:    { label: 'Active',     cls: 'status-active' },
    completed: { label: 'Awaiting',   cls: 'status-awaiting' },
    on_hold:   { label: 'On Hold',    cls: 'status-onhold' },
    closed:    { label: 'Confirmed',  cls: 'status-closed' },
    terminated:{ label: 'Terminated', cls: 'status-terminated' },
    cancelled: { label: 'Cancelled',  cls: 'status-terminated' },
};

const TYPE_ICONS = {
    audit: '🔍', replenish: '📦', removal: '🗑️', transfer: '🔄',
};

function getStatusBadge(s) {
    const m = STATUS_LABELS[s] || { label: s, cls: '' };
    return `<span class="mtd-status-badge ${m.cls}">${m.label}</span>`;
}

function getTypeBadge(t, displayName) {
    const icon = TYPE_ICONS[t] || '📋';
    return `<span class="mtd-type-badge mtd-type-${t}">${icon} ${displayName || t}</span>`;
}

function getExpiryClass(days) {
    if (days < 0) return 'mtd-exp-expired';
    if (days <= 7) return 'mtd-exp-1w';
    if (days <= 30) return 'mtd-exp-1m';
    if (days <= 90) return 'mtd-exp-3m';
    if (days <= 180) return 'mtd-exp-6m';
    return 'mtd-exp-upcoming';
}

const EP_CFG = {
    'expired': { bg:'var(--mt-danger-light)', color:'var(--mt-danger)', label:'Expired'    },
    '1w':      { bg:'var(--mt-danger-border)', color:'var(--mt-danger-dark)', label:'≤ 1 Week'   },
    '1m':      { bg:'var(--mt-warning-light)', color:'var(--mt-warning-dark)', label:'≤ 1 Month'  },
    '3m':      { bg:'var(--mt-warning-light)', color:'var(--mt-warning-text)', label:'≤ 3 Months' },
    '6m':      { bg:'var(--mt-success-light)', color:'var(--mt-success-dark)', label:'≤ 6 Months' },
    // The API also emits 'upcoming' for anything beyond 6 months. It has no
    // tier pill, but the kanban needs a column for it — same neutral treatment
    // the badge fallback already gave it, just with a real label.
    'upcoming':{ bg:'var(--mt-gray-100)',      color:'var(--mt-gray-600)',     label:'> 6 Months' },
};

function epBadge(dateStr, level) {
    const c = EP_CFG[level] || { bg:'var(--mt-gray-100)', color:'var(--mt-gray-600)', label:'—' };
    return `<span style="display:inline-flex;flex-direction:column;align-items:center;background:${c.bg};color:${c.color};border-radius:6px;padding:2px 8px;font-size:.75rem;font-weight:700;line-height:1.35;white-space:nowrap;">`
         + `${dateStr || '—'}<small style="font-size:.68rem;font-weight:500;">${c.label}</small></span>`;
}

let _expiryDt = null;

// ── Tab switching ─────────────────────────────────────────────────────────────
function switchTab(evt, tabId) {
    _activeTab = tabId;
    document.querySelectorAll('.mtd-tab').forEach(b => b.classList.remove('active'));
    if (evt && evt.currentTarget) evt.currentTarget.classList.add('active');
    showActivePane();

    // Kick off the fetch on first visit, then paint — the render is safe before
    // the data lands and puts the pane into the group's saved view either way.
    if (tabId === 'tab-expiry') {
        if (!_expiryLoaded) loadExpiryList();
        renderExpiry();
    }
    if (tabId === 'tab-stock') {
        if (!_stockLoaded) loadAllStock();
        renderAllStock();
    }
}

/** Reveal the pane the current tab + view combination calls for.
 *  The session kanban shows every status at once, so on a session tab it takes
 *  over the content area; the tab underline stays put as the "you are here"
 *  marker. The product tabs keep their own pane in every view. */
function showActivePane() {
    const onSessionTab = currentGroup() === 'session';
    const paneId = (onSessionTab && _views.session === 'kanban') ? 'tab-kanban' : _activeTab;

    document.querySelectorAll('.mtd-tab-content').forEach(c => {
        c.classList.toggle('active', c.id === paneId);
    });

    // The switcher follows every tab; the session filters only mean anything
    // on a session tab, so they step out of the bar elsewhere.
    paintViewToggle();
    const dropdown = document.getElementById('sfDropdown');
    if (dropdown) dropdown.hidden = !onSessionTab;
    if (!onSessionTab && isFilterPanelOpen()) closeFilterPanel();
    const chips = document.getElementById('activeChips');
    if (chips && !onSessionTab) chips.hidden = true;
    else renderActiveChips();
}

// ── View switching ────────────────────────────────────────────────────────────
/** Sync the switcher to the active tab: drop the views this group doesn't
 *  offer, and light up the one it is currently using. */
function paintViewToggle() {
    const group   = currentGroup();
    const allowed = GROUP_VIEWS[group];
    const view    = _views[group];
    document.querySelectorAll('.mtd-vt-btn').forEach(b => {
        const v = b.dataset.view;
        b.hidden = !allowed.includes(v);
        const on = v === view;
        b.classList.toggle('active', on);
        b.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
}

function setView(view) {
    const group = currentGroup();
    if (!GROUP_VIEWS[group].includes(view)) return;
    _views[group] = view;
    try { localStorage.setItem(VIEW_KEYS[group], view); } catch { /* private mode */ }

    paintViewToggle();
    if (group === 'session') { renderAllSessionTabs(); showActivePane(); }
    else if (group === 'expiry') renderExpiry();
    else renderAllStock();
}

/** Show/hide one of a pane's alternate view hosts. */
function showHost(id, on) {
    const el = document.getElementById(id);
    if (el) el.hidden = !on;
}

function loadingHost(label) {
    return `<div class="mtd-pc-loading"><div class="mtd-spinner"></div>${escHtml(label)}</div>`;
}

// ── Type filter ───────────────────────────────────────────────────────────────
function setTypeFilter(type, btn) {
    _typeFilter = type;
    document.querySelectorAll('#typeChips .mtd-chip').forEach(b => {
        b.classList.toggle('active', b === btn || b.dataset.type === type);
    });
    refreshFilterState();
}

// ── Filter panel ──────────────────────────────────────────────────────────────
function isFilterPanelOpen() {
    return !document.getElementById('sessionFilterPanel')?.hidden;
}

function toggleFilterPanel(e) {
    if (e) e.stopPropagation();
    isFilterPanelOpen() ? closeFilterPanel() : openFilterPanel();
}

function openFilterPanel() {
    const panel = document.getElementById('sessionFilterPanel');
    if (!panel) return;
    panel.hidden = false;
    const backdrop = document.getElementById('sfBackdrop');
    if (backdrop) backdrop.hidden = false;
    const trigger = document.getElementById('sfTrigger');
    if (trigger) { trigger.classList.add('active'); trigger.setAttribute('aria-expanded', 'true'); }
    // Stop the page scrolling behind the mobile sheet.
    document.body.style.overflow = 'hidden';
}

function closeFilterPanel() {
    const panel = document.getElementById('sessionFilterPanel');
    if (panel) panel.hidden = true;
    const backdrop = document.getElementById('sfBackdrop');
    if (backdrop) backdrop.hidden = true;
    document.body.style.overflow = '';
    const trigger = document.getElementById('sfTrigger');
    if (trigger) {
        trigger.setAttribute('aria-expanded', 'false');
        // Stay highlighted while filters are still narrowing the list.
        trigger.classList.toggle('active', activeFilterCount() > 0);
    }
}

function activeFilterCount() {
    return Object.values(_sessionFilters).filter(v => v !== '').length
         + (_typeFilter !== 'all' ? 1 : 0);
}

document.addEventListener('click', function (e) {
    if (!isFilterPanelOpen()) return;
    if (e.target?.id === 'sfBackdrop') { closeFilterPanel(); return; }
    const dropdown = document.getElementById('sfDropdown');
    if (dropdown && !dropdown.contains(e.target)) closeFilterPanel();
});

document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && isFilterPanelOpen()) closeFilterPanel();
});

function populateFilterDropdowns() {
    // Years from session data
    const years = [...new Set(_sessions.map(s => new Date(s.started_at).getFullYear()))].sort((a, b) => b - a);
    const yearSel = document.getElementById('filterYear');
    if (yearSel) {
        yearSel.innerHTML = '<option value="">Year</option>' +
            years.map(y => `<option value="${y}">${y}</option>`).join('');
        if (_sessionFilters.year) yearSel.value = _sessionFilters.year;
    }

    // Reps from session rep_details
    const repMap = {};
    _sessions.forEach(s => {
        const email = s.rep_email || (s.rep_details && s.rep_details.email) || '';
        if (email && !repMap[email.toLowerCase()]) {
            const full = s.rep_name || (s.rep_details
                ? ((s.rep_details.first_name || '') + ' ' + (s.rep_details.last_name || '')).trim() || s.rep_details.username
                : '') || email;
            repMap[email.toLowerCase()] = full;
        }
    });
    const spSel = document.getElementById('filterSalesperson');
    if (spSel) {
        const sorted = Object.entries(repMap).sort((a, b) => a[1].localeCompare(b[1]));
        spSel.innerHTML = '<option value="">Any Rep</option>' +
            sorted.map(([email, name]) => `<option value="${email}">${name}</option>`).join('');
        if (_sessionFilters.salesperson) spSel.value = _sessionFilters.salesperson;
    }

    // Hospitals from sessions
    const hospitals = [...new Set(_sessions.map(s => s.hospital_name).filter(Boolean))].sort();
    const hospSel = document.getElementById('filterHospital');
    if (hospSel) {
        hospSel.innerHTML = '<option value="">Hospital</option>' +
            hospitals.map(h => `<option value="${h}">${h}</option>`).join('');
        if (_sessionFilters.hospital) hospSel.value = _sessionFilters.hospital;
    }
}

function applySessionFilter() {
    _sessionFilters.year        = document.getElementById('filterYear')?.value        || '';
    _sessionFilters.month       = document.getElementById('filterMonth')?.value       || '';
    _sessionFilters.date        = document.getElementById('filterDate')?.value        || '';
    _sessionFilters.salesperson = document.getElementById('filterSalesperson')?.value || '';
    _sessionFilters.hospital    = document.getElementById('filterHospital')?.value    || '';
    refreshFilterState();
}

/** Re-sync the badge, the chip bar and every list after a filter changes. */
function refreshFilterState() {
    const count   = activeFilterCount();
    const badge   = document.getElementById('sfBadge');
    const trigger = document.getElementById('sfTrigger');
    if (badge)   { badge.textContent = count; badge.hidden = count === 0; }
    if (trigger) { trigger.classList.toggle('active', count > 0 || isFilterPanelOpen()); }

    renderActiveChips();
    renderAllSessionTabs();
    updateAwaitingBadge();
}

const TYPE_LABELS = {
    audit: '🔍 Audit', replenish: '📦 Replenish',
    removal: '🗑️ Removal', transfer: '🔄 Transfer',
};
const MONTH_NAMES = ['January','February','March','April','May','June',
                     'July','August','September','October','November','December'];

/** Chips summarising what's applied — the panel is shut most of the time,
 *  so without these there's no way to see why a list looks short. */
function renderActiveChips() {
    const bar = document.getElementById('activeChips');
    if (!bar) return;
    if (!SESSION_TABS.includes(_activeTab)) { bar.hidden = true; return; }

    const chips = [];
    if (_typeFilter !== 'all') {
        chips.push({ key: 'type', label: TYPE_LABELS[_typeFilter] || _typeFilter });
    }
    if (_sessionFilters.year)  chips.push({ key: 'year',  label: _sessionFilters.year });
    if (_sessionFilters.month) chips.push({ key: 'month', label: MONTH_NAMES[+_sessionFilters.month - 1] || _sessionFilters.month });
    if (_sessionFilters.date)  chips.push({ key: 'date',  label: _sessionFilters.date });
    if (_sessionFilters.salesperson) {
        const sel = document.getElementById('filterSalesperson');
        const opt = sel?.querySelector(`option[value="${cssEsc(_sessionFilters.salesperson)}"]`);
        chips.push({ key: 'salesperson', label: opt?.textContent || _sessionFilters.salesperson });
    }
    if (_sessionFilters.hospital) chips.push({ key: 'hospital', label: _sessionFilters.hospital });

    if (!chips.length) { bar.hidden = true; bar.innerHTML = ''; return; }

    bar.hidden = false;
    bar.innerHTML = chips.map(c =>
        `<span class="mtd-achip">${escHtml(c.label)}` +
        `<button type="button" aria-label="Remove ${escHtml(c.label)} filter" onclick="removeFilter('${c.key}')">×</button></span>`
    ).join('') + `<button type="button" class="mtd-achip-clear" onclick="clearAllFilters()">Clear all</button>`;
}

function removeFilter(key) {
    if (key === 'type') {
        _typeFilter = 'all';
        document.querySelectorAll('#typeChips .mtd-chip').forEach(b =>
            b.classList.toggle('active', b.dataset.type === 'all'));
    } else {
        _sessionFilters[key] = '';
        const id = 'filter' + key.charAt(0).toUpperCase() + key.slice(1);
        const el = document.getElementById(id);
        if (el) el.value = '';
    }
    refreshFilterState();
}

function clearAllFilters() {
    _sessionFilters = { year: '', month: '', date: '', salesperson: '', hospital: '' };
    ['filterYear','filterMonth','filterDate','filterSalesperson','filterHospital'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.value = '';
    });
    _typeFilter = 'all';
    document.querySelectorAll('#typeChips .mtd-chip').forEach(b =>
        b.classList.toggle('active', b.dataset.type === 'all'));
    refreshFilterState();
}

// ── Load stats ────────────────────────────────────────────────────────────────
async function loadStats() {
    try {
        const s = await fetchJSON(`${MTD_API}/dashboard/`);
        setEl('statActive',   fmtNum(s.active_sessions  ?? s.sessions_active ?? 0));
        setEl('statAwaiting', fmtNum(s.awaiting_sessions ?? s.sessions_awaiting ?? 0));
        setEl('statClosed',   fmtNum(s.closed_sessions  ?? s.sessions_closed ?? 0));
        setEl('statProducts', fmtNum(s.total_on_shelf   ?? s.total_consigned ?? 0));
        setEl('statHospitals',fmtNum(s.total_hospitals  ?? 0));
    } catch(e) { console.error('Stats error', e); }
    try {
        const ex = await fetchJSON(`${MTD_API}/inventory/expiry-summary/`);
        setEl('statExpired',   fmtNum(ex.expired ?? 0));
        setEl('statExpiring30',fmtNum((ex['1w'] ?? 0) + (ex['1m'] ?? 0)));
        const critical = (ex.expired ?? 0) + (ex['1w'] ?? 0) + (ex['1m'] ?? 0) + (ex['3m'] ?? 0) + (ex['6m'] ?? 0);
        const badge = document.getElementById('expiryBadge');
        if (badge && critical > 0) badge.textContent = critical;
    } catch(e) { console.error('Expiry summary error', e); }

    // Both fetches are in; the summary now has real numbers to fold down to.
    refreshStatsSummary();
    armStatsAutoCollapse();
}

function setEl(id, val) {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
}

// ── Stats strip: fold away on phones ──────────────────────────────────────────
// Seven tiles cost roughly a third of a phone screen before any content shows.
// They stay up long enough to read, then collapse into a one-line summary.
const STATS_PIN_KEY = 'mtd.statsPinned';
const STATS_MQ = typeof window.matchMedia === 'function' ? window.matchMedia('(max-width: 640px)') : null;
let _statsTimer = null;

function statsPinned() {
    try { return localStorage.getItem(STATS_PIN_KEY) === '1'; } catch { return false; }
}

function setStatsCollapsed(on) {
    const wrap = document.getElementById('statWrap');
    if (!wrap) return;
    wrap.classList.toggle('is-collapsed', on);
    const btn = document.getElementById('statSummary');
    if (btn) btn.setAttribute('aria-expanded', on ? 'false' : 'true');
}

/** Opening the strip by hand means "leave it alone" — remember that and stop
 *  auto-folding. Closing it by hand is just closing it. */
function toggleStats() {
    const wrap = document.getElementById('statWrap');
    if (!wrap) return;
    const collapsing = !wrap.classList.contains('is-collapsed');
    clearTimeout(_statsTimer);
    setStatsCollapsed(collapsing);
    try { localStorage.setItem(STATS_PIN_KEY, collapsing ? '0' : '1'); } catch { /* private mode */ }
}

/** The numbers worth keeping in view once the tiles are gone. Zeroes for
 *  awaiting and expired are not news, so they drop out. */
function refreshStatsSummary() {
    const el = document.getElementById('statSummaryText');
    if (!el) return;
    const txt = id => (document.getElementById(id)?.textContent || '').trim() || '0';
    const num = id => parseInt(txt(id).replace(/\D/g, ''), 10) || 0;

    const parts = [`${escHtml(txt('statActive'))} active`];
    if (num('statAwaiting')) parts.push(`${escHtml(txt('statAwaiting'))} awaiting`);
    if (num('statExpired'))  parts.push(`<b class="mtd-ss-warn">${escHtml(txt('statExpired'))} expired</b>`);
    parts.push(`${escHtml(txt('statProducts'))} on shelf`);
    el.innerHTML = parts.join(' <span class="mtd-ss-dot">·</span> ');
}

/** Fold 5s after the numbers land — but only at the top of the page, so it can
 *  never yank content out from under someone who has already scrolled on. */
function armStatsAutoCollapse() {
    if (!STATS_MQ || !STATS_MQ.matches || statsPinned()) return;
    clearTimeout(_statsTimer);
    _statsTimer = setTimeout(() => {
        if (statsPinned()) return;
        if (window.scrollY < 40) { setStatsCollapsed(true); return; }
        const onScroll = () => {
            if (statsPinned()) { window.removeEventListener('scroll', onScroll); return; }
            if (window.scrollY >= 40) return;
            window.removeEventListener('scroll', onScroll);
            setStatsCollapsed(true);
        };
        window.addEventListener('scroll', onScroll, { passive: true });
    }, 5000);
}

/** Rotating a phone to landscape, or resizing on desktop, should not leave the
 *  strip folded away where there is room for it. */
function watchStatsBreakpoint() {
    if (!STATS_MQ) return;
    const onChange = e => {
        if (!e.matches) { clearTimeout(_statsTimer); setStatsCollapsed(false); }
        else armStatsAutoCollapse();
    };
    if (STATS_MQ.addEventListener) STATS_MQ.addEventListener('change', onChange);
    else STATS_MQ.addListener(onChange);
}

// ── Load all sessions ─────────────────────────────────────────────────────────
async function loadSessions() {
    try {
        const data = await fetchJSON(`${MTD_API}/sessions/dashboard-list/`);
        _sessions = data.results || [];
        _sessionsLoaded = true;
        populateFilterDropdowns();
        renderAllSessionTabs();
        updateAwaitingBadge();
    } catch(e) {
        console.error('Sessions error', e);
        showAlert('Failed to load sessions: ' + e.message);
    }
}

function updateAwaitingBadge() {
    const awaitingCount = applyAdvancedFilters(
        _sessions.filter(s => s.status === 'completed' || s.status === 'on_hold')
    ).length;
    const badge = document.getElementById('awaitingBadge');
    if (badge) badge.textContent = awaitingCount > 0 ? awaitingCount : '';
}

function applyAdvancedFilters(list) {
    let out = list;
    if (_sessionFilters.year) {
        const y = parseInt(_sessionFilters.year);
        out = out.filter(s => new Date(s.started_at).getFullYear() === y);
    }
    if (_sessionFilters.month) {
        const m = parseInt(_sessionFilters.month);
        out = out.filter(s => (new Date(s.started_at).getMonth() + 1) === m);
    }
    if (_sessionFilters.date) {
        out = out.filter(s => new Date(s.started_at).toISOString().split('T')[0] === _sessionFilters.date);
    }
    if (_sessionFilters.salesperson) {
        const sp = _sessionFilters.salesperson.toLowerCase();
        out = out.filter(s => {
            const email = (s.rep_email || (s.rep_details && s.rep_details.email) || '').toLowerCase();
            return email === sp;
        });
    }
    if (_sessionFilters.hospital) {
        out = out.filter(s => (s.hospital_name || '') === _sessionFilters.hospital);
    }
    return out;
}

function filterSessions(statusFilter) {
    let list = _sessions;
    if (statusFilter === 'awaiting') {
        list = list.filter(s => s.status === 'completed' || s.status === 'on_hold');
    } else if (statusFilter) {
        list = list.filter(s => s.status === statusFilter);
    }
    if (_typeFilter && _typeFilter !== 'all') {
        list = list.filter(s => s.session_type === _typeFilter);
    }
    return applyAdvancedFilters(list);
}

function renderAllSessionTabs() {
    // Before the first fetch resolves, leave the skeleton placeholders alone.
    if (!_sessionsLoaded) return;
    renderSessionList('activeList',   filterSessions('active'));
    renderSessionList('awaitingList', filterSessions('awaiting'));
    renderSessionList('closedList',   filterSessions('closed'));
    if (_views.session === 'kanban') renderKanban();
}

function renderSessionList(containerId, sessions) {
    const el = document.getElementById(containerId);
    if (!el) return;

    if (!sessions.length) {
        el.className = 'mtd-sessions-grid';
        el.innerHTML = emptyState();
        return;
    }

    if (_views.session === 'table') {
        el.className = 'mtd-sessions-tablewrap';
        el.innerHTML = renderSessionTable(sessions);
        // Hand the fresh table to meritrack-ui so it stacks into cards ≤640px.
        try { window.MeriTrackUI?.tables?.(el); } catch { /* progressive enhancement */ }
    } else {
        el.className = 'mtd-sessions-grid';
        el.innerHTML = sessions.map(renderSessionCard).join('');
    }
}

function emptyState(msg) {
    return `<div class="mtd-empty-state" style="grid-column:1/-1;">
        <div class="mtd-empty-icon">📋</div>
        <p>${escHtml(msg || 'No sessions found')}</p>
    </div>`;
}

// ── Table view ────────────────────────────────────────────────────────────────
function renderSessionTable(sessions) {
    const rows = sessions.map(s => {
        const toHosp = s.to_hospital_name ? ` → ${escHtml(s.to_hospital_name)}` : '';
        const dept   = s.department_name ? `<span class="mtd-tbl-sub">📍 ${escHtml(s.department_name)}</span>` : '';
        const acts   = sessionActionButtons(s);
        return `<tr>
            <td><span class="mtd-tbl-hosp">${escHtml(s.hospital_name || '—')}${toHosp}</span>${dept}</td>
            <td>${getTypeBadge(s.session_type, s.session_type_display)}</td>
            <td>${getStatusBadge(s.status)}</td>
            <td>${escHtml(s.rep_name || '—')}</td>
            <td style="font-weight:700;">${s.total_scans ?? 0}</td>
            <td style="white-space:nowrap;">${relativeDate(s.started_at)}</td>
            <td><div class="mtd-tbl-actions">
                <a href="${escHtml(s.detail_url)}" class="mtd-card-link">View →</a>${acts}
            </div></td>
        </tr>`;
    }).join('');

    return `<div class="table-responsive-wrap" data-hint="← scroll for more →">
        <table class="mtd-session-tbl" data-stack style="min-width:820px;">
            <thead><tr>
                <th>Hospital</th><th>Type</th><th>Status</th>
                <th>Rep</th><th>Scans</th><th>Started</th><th>Actions</th>
            </tr></thead>
            <tbody>${rows}</tbody>
        </table>
    </div>`;
}

// ── Kanban view ───────────────────────────────────────────────────────────────
const KANBAN_COLUMNS = [
    { key: 'active',   title: 'Active',    cls: 'kan-active', dot: 'var(--mt-warning)' },
    { key: 'awaiting', title: 'Awaiting',  cls: 'kan-await',  dot: 'var(--mt-info)' },
    { key: 'closed',   title: 'Confirmed', cls: 'kan-closed', dot: 'var(--mt-success-dark)' },
];

function renderKanban() {
    const board = document.getElementById('kanbanBoard');
    if (!board) return;
    board.innerHTML = KANBAN_COLUMNS.map(col => {
        const items = filterSessions(col.key);
        const body = items.length
            ? items.map(s => renderKanbanCard(s, col.cls)).join('')
            : `<div class="mtd-kan-empty">Nothing here</div>`;
        return `<section class="mtd-kan-col">
            <header class="mtd-kan-head">
                <span class="mtd-kan-dot" style="background:${col.dot};"></span>
                <span class="mtd-kan-title">${col.title}</span>
                <span class="mtd-kan-count">${items.length}</span>
            </header>
            <div class="mtd-kan-body">${body}</div>
        </section>`;
    }).join('');
}

function renderKanbanCard(s, cls) {
    const toHosp = s.to_hospital_name ? ` → ${escHtml(s.to_hospital_name)}` : '';
    const remark = s.total_remarks > 0
        ? `<span class="mtd-remark-btn${s.has_warehouse_remark ? ' has-wh' : ''}">💬 ${s.total_remarks}</span>` : '';
    return `<article class="mtd-kan-card ${cls}">
        <div class="mtd-card-top">
            <div class="mtd-card-badges">${getTypeBadge(s.session_type, s.session_type_display)}</div>
            <div class="mtd-card-meta-right">${s.has_attachment ? '<span class="mtd-att-badge">📎</span>' : ''}${remark}</div>
        </div>
        <div class="mtd-kan-hosp">${escHtml(s.hospital_name || '—')}${toHosp}</div>
        <div class="mtd-kan-meta">
            <span>👤 ${escHtml(s.rep_name || '—')}</span>
            <span>📦 ${s.total_scans ?? 0} scans</span>
            <span>🕒 ${relativeDate(s.started_at)}</span>
        </div>
        <div class="mtd-kan-foot">
            <a href="${escHtml(s.detail_url)}" class="mtd-kan-link">View Details →</a>
            <div class="mtd-card-actions">${sessionActionButtons(s)}</div>
        </div>
    </article>`;
}

/** Status-appropriate manager actions — shared by every view so the three
 *  stay in step when the workflow changes. */
function sessionActionButtons(s) {
    if (s.status === 'active') {
        return `<button class="mtd-action-btn mtd-action-force" onclick="forceComplete('${s.session_key}', this)">✅ Force Complete</button>
                <button class="mtd-action-btn mtd-action-term"  onclick="terminateSession('${s.session_key}', this)">🚫 Terminate</button>`;
    }
    if (s.status === 'completed') {
        return `<button class="mtd-action-btn mtd-action-close"  onclick="closeSession('${s.session_key}', this)">🔒 Close</button>
                <button class="mtd-action-btn mtd-action-reject" onclick="rejectSession('${s.session_key}', this)">↩ Reject</button>
                <button class="mtd-action-btn mtd-action-hold"   onclick="holdSession('${s.session_key}', this)">⏸ Hold</button>`;
    }
    if (s.status === 'on_hold') {
        return `<button class="mtd-action-btn mtd-action-resume" onclick="resumeSession('${s.session_key}', this)">▶ Resume</button>`;
    }
    return '';
}

function renderSessionCard(s) {
    const statusBadge = getStatusBadge(s.status);
    const typeBadge   = getTypeBadge(s.session_type, s.session_type_display);
    const remarkBadge = s.total_remarks > 0
        ? `<span class="mtd-remark-btn${s.has_warehouse_remark ? ' has-wh' : ''}" title="${s.total_remarks} remark(s)">💬 ${s.total_remarks}</span>`
        : '';
    const attBadge = s.has_attachment
        ? `<span class="mtd-att-badge" title="${s.attachment_name || 'Attachment'}">📎</span>` : '';
    const toHospStr = s.to_hospital_name ? ` → ${s.to_hospital_name}` : '';

    const actionBtns = sessionActionButtons(s);

    const managerActions = actionBtns
        ? `<div class="mtd-card-actions">${actionBtns}</div>`
        : '';

    const deptStr = s.department_name ? `<div class="mtd-card-dept">📍 ${s.department_name}</div>` : '';

    return `
<div class="mtd-session-card" data-status="${s.status}">
  <div class="mtd-card-top">
    <div class="mtd-card-badges">${typeBadge}${statusBadge}</div>
    <div class="mtd-card-meta-right">${attBadge}${remarkBadge}</div>
  </div>
  <div class="mtd-card-hospital">${s.hospital_name || '—'}${toHospStr}</div>
  ${deptStr}
  <div class="mtd-card-grid">
    <div><span class="mtd-card-key">Rep</span><span>${s.rep_name || '—'}</span></div>
    <div><span class="mtd-card-key">Scans</span><span>${s.total_scans}</span></div>
    <div><span class="mtd-card-key">Started</span><span>${relativeDate(s.started_at)}</span></div>
    ${s.completed_at ? `<div><span class="mtd-card-key">Done</span><span>${relativeDate(s.completed_at)}</span></div>` : ''}
  </div>
  <div class="mtd-card-footer">
    <a href="${s.detail_url}" class="mtd-card-link">View Details →</a>
    ${managerActions}
  </div>
</div>`;
}

// ── Product card ──────────────────────────────────────────────────────────────
/** One product card, shared by the expiry and scans grids. `rail` colours the
 *  left edge; `rows` is a list of [label, htmlValue] pairs — values arrive as
 *  HTML, so callers escape their own text. */
function productCard({ rail, badges, right, title, serial, rows, foot }) {
    const cells = rows.filter(Boolean).map(([k, v]) =>
        `<div><span class="mtd-card-key">${escHtml(k)}</span><span>${v}</span></div>`).join('');
    return `<article class="mtd-pc${rail ? ' ' + rail : ''}">
  <div class="mtd-card-top">
    <div class="mtd-card-badges">${badges || ''}</div>
    <div class="mtd-card-meta-right">${right || ''}</div>
  </div>
  <div class="mtd-pc-title">${escHtml(title || '—')}</div>
  ${serial ? `<div class="mtd-pc-sn">${serial}</div>` : ''}
  <div class="mtd-card-grid">${cells}</div>
  ${foot ? `<div class="mtd-pc-foot">${foot}</div>` : ''}
</article>`;
}

/** Serial numbers are synthesised as NSN-… when the scan had none. */
function serialTag(sn) {
    return (sn && !String(sn).startsWith('NSN-'))
        ? `<code class="mtd-pc-code">${escHtml(sn)}</code>`
        : '<em class="mtd-pc-nosn">No Serial</em>';
}

function daysTag(days) {
    if (days === null || days === undefined) return '—';
    const cls = getExpiryClass(days);
    return days < 0
        ? `<span class="mtd-pc-days ${cls}">${Math.abs(days)}d ago</span>`
        : `<span class="mtd-pc-days ${cls}">in ${days}d</span>`;
}

/** Scan-log rows have no ConsignedProduct behind them, so there is nothing to
 *  acknowledge against — they get a dash in every view. */
function ackControl(p) {
    if (p.acknowledged) return '<span class="mtd-pc-acked">✓ Acked</span>';
    if (String(p.id).startsWith('sl-')) return '<span class="mtd-pc-noack">—</span>';
    return `<button class="mtd-btn-ack" onclick="acknowledgeExpiry(${p.id}, this)">Ack</button>`;
}

// ── Expiry tab ────────────────────────────────────────────────────────────────
async function loadExpiryList() {
    _expiryLoaded = true;
    try {
        const data = await fetchJSON(`${MTD_API}/inventory/expiry-list/`);
        _expiryItems = data.results || [];
        _expiryReady = true;
        renderExpiry();
    } catch(e) {
        console.error('Expiry list error', e);
        showAlert('Failed to load expiry data: ' + e.message);
    }
}

function setExpiryFilter(btn, level) {
    _expiryLevel = level;
    document.querySelectorAll('.ep-filter').forEach(b => b.classList.remove('active'));
    if (btn) btn.classList.add('active');
    renderExpiry();
}

function expiryItems() {
    return _expiryLevel === 'all'
        ? _expiryItems
        : _expiryItems.filter(i => i.expiry_level === _expiryLevel);
}

/** Paint whichever of the three views the expiry group is set to. Tier is the
 *  kanban axis, so the tier pills stand down while that board is up — the same
 *  trade the status tabs make for the session board. */
function renderExpiry() {
    const view = _views.expiry;

    // DataTables owns the table's DOM while it lives; tear it down before any
    // re-render so a later init isn't fighting a stale instance.
    if (_expiryDt) { _expiryDt.destroy(); _expiryDt = null; }

    const pills = document.getElementById('expiryFilterBar');
    if (pills) pills.hidden = view === 'kanban';

    showHost('expiryTableWrap', view === 'table');
    showHost('expiryCards',     view === 'cards');
    showHost('expiryKanban',    view === 'kanban');

    if (!_expiryReady) {
        // Fetch still in flight — the table keeps its server-rendered skeleton.
        if (view !== 'table') {
            const host = document.getElementById(view === 'cards' ? 'expiryCards' : 'expiryKanban');
            if (host) host.innerHTML = loadingHost('Loading expiry data…');
        }
        return;
    }

    if (view === 'table') renderExpiryTable();
    else if (view === 'cards') renderExpiryCards();
    else renderExpiryKanban();
}

function renderExpiryTable() {
    const tbody = document.getElementById('expiryTableBody');
    if (!tbody) return;

    const items = expiryItems();
    if (!items.length) {
        tbody.innerHTML = `<tr><td colspan="10" style="text-align:center;padding:40px;color:var(--mt-gray-400);">No products in this tier.</td></tr>`;
        return;
    }

    tbody.innerHTML = items.map(p => {
        const c = EP_CFG[p.expiry_level];
        const days = p.days_to_expiry;
        const daysLabel = days !== null && days !== undefined
            ? (days < 0 ? `<span class="ep-days" style="color:var(--mt-danger);font-weight:700;">${Math.abs(days)}d ago</span>`
                        : `<span class="ep-days" style="color:${c ? c.color : 'var(--mt-gray-600)'};font-weight:600;">in ${days}d</span>`)
            : '—';
        const sn = p.serial_number && !String(p.serial_number).startsWith('NSN-')
            ? `<code style="font-size:.8em;background:rgba(0,0,0,.04);padding:2px 5px;border-radius:4px;">${p.serial_number}</code>`
            : '<em style="color:var(--mt-gray-400);font-size:.82em;">No Serial</em>';
        return `<tr class="ep-row ep-${p.expiry_level || 'upcoming'}">
            <td>${sn}</td>
            <td style="font-weight:600;">${p.product_name || '—'}</td>
            <td style="font-size:.8em;color:var(--mt-gray-600);">${p.gtin_code || '—'}</td>
            <td style="font-size:.8em;">${p.batch_number || '—'}</td>
            <td>${p.hospital_name || '—'}</td>
            <td style="font-size:.85em;">${p.department_name || '—'}</td>
            <td data-order="${p.expiry_date || ''}">${epBadge(p.expiry_date, p.expiry_level)}</td>
            <td data-order="${days ?? 99999}" style="white-space:nowrap;">${daysLabel}</td>
            <td style="font-size:.8em;color:var(--mt-gray-600);">${p.placed_by || '—'}</td>
            <td style="text-align:center;">${ackControl(p)}</td>
        </tr>`;
    }).join('');

    _expiryDt = new DataTable('#expiryTable', {
        paging: true, pageLength: 25,
        ordering: true, order: [[7, 'asc']],
        searching: true, info: true,
        autoWidth: false,
        language: { search: 'Search:', emptyTable: 'No products in this tier.' },
        columnDefs: [{ orderable: false, targets: [9] }],
    });
}

function expiryCard(p) {
    return productCard({
        rail:   `pc-exp-${p.expiry_level}`,
        badges: epBadge(p.expiry_date, p.expiry_level),
        right:  daysTag(p.days_to_expiry),
        title:  p.product_name,
        serial: serialTag(p.serial_number),
        rows: [
            ['Hospital',   escHtml(p.hospital_name   || '—')],
            ['Department', escHtml(p.department_name || '—')],
            ['Lot/Batch',  escHtml(p.batch_number    || '—')],
            ['GTIN',       escHtml(p.gtin_code       || '—')],
        ],
        foot: `<span class="mtd-pc-footkey">Placed by ${escHtml(p.placed_by || '—')}</span>${ackControl(p)}`,
    });
}

function renderExpiryCards() {
    const host = document.getElementById('expiryCards');
    if (!host) return;
    const items = expiryItems();
    host.innerHTML = items.length
        ? items.map(expiryCard).join('')
        : `<div class="mtd-empty-state" style="grid-column:1/-1;">
               <div class="mtd-empty-icon">✅</div><p>No products in this tier.</p>
           </div>`;
}

// Ordered soonest-to-expire first, which is also how the API sorts.
const EXPIRY_COLUMNS = ['expired', '1w', '1m', '3m', '6m', 'upcoming'];

/** Tier board. Ignores the pill filter by design — the pills are hidden in
 *  this view because the columns already are the tier breakdown. */
function renderExpiryKanban() {
    const board = document.getElementById('expiryKanban');
    if (!board) return;
    board.innerHTML = EXPIRY_COLUMNS.map(level => {
        const c = EP_CFG[level] || {};
        const items = _expiryItems.filter(i => i.expiry_level === level);
        const body = items.length
            ? items.map(p => expiryKanbanCard(p, level)).join('')
            : '<div class="mtd-kan-empty">Nothing here</div>';
        return `<section class="mtd-kan-col">
            <header class="mtd-kan-head">
                <span class="mtd-kan-dot" style="background:${c.color || 'var(--mt-gray-300)'};"></span>
                <span class="mtd-kan-title">${escHtml(c.label || level)}</span>
                <span class="mtd-kan-count">${items.length}</span>
            </header>
            <div class="mtd-kan-body">${body}</div>
        </section>`;
    }).join('');
}

function expiryKanbanCard(p, level) {
    return `<article class="mtd-kan-card pc-exp-${level}">
        <div class="mtd-card-top">
            <div class="mtd-card-badges">${serialTag(p.serial_number)}</div>
            <div class="mtd-card-meta-right">${daysTag(p.days_to_expiry)}</div>
        </div>
        <div class="mtd-kan-hosp">${escHtml(p.product_name || '—')}</div>
        <div class="mtd-kan-meta">
            <span>🏥 ${escHtml(p.hospital_name || '—')}</span>
            ${p.batch_number ? `<span>🔖 ${escHtml(p.batch_number)}</span>` : ''}
            <span>📅 ${escHtml(p.expiry_date || '—')}</span>
        </div>
        <div class="mtd-kan-foot">
            <span class="mtd-pc-footkey">${escHtml(p.department_name || '—')}</span>
            ${ackControl(p)}
        </div>
    </article>`;
}

async function acknowledgeExpiry(productId, btn) {
    btn.disabled = true; btn.textContent = '…';
    try {
        const r = await postJSON(`${MTD_API}/inventory/${productId}/acknowledge-expiry/`, {});
        if (r.ok) {
            const item = _expiryItems.find(i => i.id === productId);
            if (item) item.acknowledged = true;
            renderExpiry();
            showAlert('Acknowledged', 'success');
        } else {
            const err = await r.json();
            showAlert(err.error || 'Failed to acknowledge');
            btn.disabled = false; btn.textContent = 'Ack';
        }
    } catch(e) { showAlert('Error: ' + e.message); btn.disabled = false; btn.textContent = 'Ack'; }
}

function exportExpiryCSV() {
    const items = _expiryLevel === 'all' ? _expiryItems : _expiryItems.filter(i => i.expiry_level === _expiryLevel);
    if (!items.length) { showAlert('No data to export', 'info'); return; }
    const headers = ['Serial No','Product','GTIN','Batch','Hospital','Department','Expiry Date','Days','Tier','Acknowledged','Placed By'];
    const rows = items.map(p => [p.serial_number, p.product_name, p.gtin_code, p.batch_number,
        p.hospital_name, p.department_name, p.expiry_date, p.days_to_expiry,
        p.expiry_level, p.acknowledged ? 'Yes' : 'No', p.placed_by]);
    downloadCSV([headers, ...rows], 'meritrack_expiry_' + today());
}

// ── All Scans tab ─────────────────────────────────────────────────────────────
async function loadAllStock() {
    _stockLoaded = true;
    document.getElementById('stockLoadingMsg').innerHTML =
        '<div class="mtd-spinner" style="margin-bottom:10px;"></div>Loading all scans…';
    try {
        const data = await fetchJSON(`${MTD_API}/sessions/my_scans/`);
        _stockItems = data.results || data.scans || [];
        _stockReady = true;
        document.getElementById('stockLoadingMsg').style.display = 'none';
        renderAllStock();
    } catch(e) {
        document.getElementById('stockLoadingMsg').textContent = 'Failed: ' + e.message;
        showAlert('Failed to load scans: ' + e.message);
    }
}

function stockItems() {
    const search = ((document.getElementById('stockSearch') || {}).value || '').toLowerCase().trim();
    if (!search) return _stockItems;
    return _stockItems.filter(i =>
        (i.serial_number || '').toLowerCase().includes(search) ||
        (i.product_name  || '').toLowerCase().includes(search) ||
        (i.gtin_code     || '').toLowerCase().includes(search) ||
        (i.batch_number  || '').toLowerCase().includes(search) ||
        (i.hospital_name || '').toLowerCase().includes(search));
}

/** Cards or table — scans have no status axis worth columning up, so this
 *  group offers no kanban. */
function renderAllStock() {
    const view  = _views.stock;
    const empty = document.getElementById('stockEmpty');

    if (!_stockReady) {
        // The loading skeleton is still up; don't claim "no scans" over it.
        showHost('stockTableWrap', false);
        showHost('stockCards', false);
        if (empty) empty.style.display = 'none';
        return;
    }

    const items = stockItems();
    showHost('stockTableWrap', view === 'table' && items.length > 0);
    showHost('stockCards',     view === 'cards' && items.length > 0);
    if (empty) empty.style.display = items.length ? 'none' : '';
    if (!items.length) return;

    if (view === 'cards') renderStockCards(items);
    else renderStockTable(items);
}

function renderStockCards(items) {
    const host = document.getElementById('stockCards');
    if (!host) return;
    host.innerHTML = items.map(p => {
        const days = p.expiry_date ? daysLeft(p.expiry_date) : null;
        return productCard({
            rail:   p.expiry_date ? `pc-exp-${expiryTier(days)}` : '',
            badges: p.session_key ? `<span class="mtd-pc-session">${escHtml(p.session_key)}</span>` : '',
            right:  p.expiry_date ? daysTag(days) : '',
            title:  p.product_name,
            serial: serialTag(p.serial_number),
            rows: [
                ['Hospital',  escHtml(p.hospital_name || '—')],
                ['Scanned By', escHtml(p.rep_name     || '—')],
                ['Lot/Batch', escHtml(p.batch_number  || '—')],
                ['Material',  escHtml(p.material_code || '—')],
                ['GTIN',      escHtml(p.gtin_code     || '—')],
                ['Expiry',    escHtml(p.expiry_date   || '—')],
            ],
            foot: `<span class="mtd-pc-footkey">Scanned ${escHtml(p.scanned_at ? new Date(p.scanned_at).toLocaleDateString() : '—')}</span>`,
        });
    }).join('');
}

/** Same tier boundaries the expiry endpoint uses, so a card's left rail means
 *  the same thing on both tabs. */
function expiryTier(days) {
    if (days < 0)   return 'expired';
    if (days <= 7)  return '1w';
    if (days <= 30) return '1m';
    if (days <= 90) return '3m';
    if (days <= 180) return '6m';
    return 'upcoming';
}

function renderStockTable(items) {
    const tbody = document.getElementById('stockTableBody');
    if (!tbody) return;
    tbody.innerHTML = items.map(p => {
        const sn = (p.serial_number && !p.serial_number.startsWith('NSN-'))
            ? `<code style="font-size:.8em;">${p.serial_number}</code>`
            : '<em style="color:var(--mt-gray-400);font-size:.8em;">No Serial</em>';
        const expStr = p.expiry_date ? `<span class="${getExpiryClass(daysLeft(p.expiry_date))}">${p.expiry_date}</span>` : '—';
        const scannedAt = p.scanned_at ? new Date(p.scanned_at).toLocaleDateString() : '—';
        return `<tr>
            <td>${sn}</td>
            <td>${p.product_name || '—'}</td>
            <td style="font-size:.8em;color:var(--mt-gray-600);">${p.gtin_code || '—'}</td>
            <td style="font-size:.8em;">${p.batch_number || '—'}</td>
            <td style="font-size:.8em;">${p.material_code || '—'}</td>
            <td style="font-size:.8em;color:var(--mt-primary);font-weight:600;">${p.session_key || '—'}</td>
            <td style="font-size:.85em;">${p.hospital_name || '—'}</td>
            <td style="font-size:.8em;color:var(--mt-gray-600);">${p.rep_name || '—'}</td>
            <td>${expStr}</td>
            <td style="font-size:.8em;color:var(--mt-gray-600);">${scannedAt}</td>
        </tr>`;
    }).join('');
}

function exportStockCSV() {
    if (!_stockItems.length) { showAlert('Load scans first', 'info'); return; }
    const headers = ['Serial No','Product','GTIN','Batch','Material','Session','Hospital','Scanned By','Expiry','Scanned At'];
    const rows = _stockItems.map(p => [
        p.serial_number, p.product_name, p.gtin_code, p.batch_number,
        p.material_code, p.session_key, p.hospital_name, p.rep_name,
        p.expiry_date, p.scanned_at ? new Date(p.scanned_at).toLocaleDateString() : ''
    ]);
    downloadCSV([headers, ...rows], 'meritrack_scans_' + today());
}

// ── Manager actions ───────────────────────────────────────────────────────────
async function forceComplete(sessionKey, btn) {
    if (!confirm('Force complete this active session?')) return;
    btn.disabled = true;
    try {
        const r = await postJSON(`${MTD_API}/sessions/force_complete_session/`, { session_key: sessionKey });
        if (r.ok) { showAlert('Session force-completed', 'success'); await loadSessions(); }
        else { const e = await r.json(); showAlert(e.error || e.detail || 'Failed'); btn.disabled = false; }
    } catch(e) { showAlert('Error: ' + e.message); btn.disabled = false; }
}

async function terminateSession(sessionKey, btn) {
    if (!confirm('Terminate this session? This cannot be undone.')) return;
    btn.disabled = true;
    try {
        const r = await postJSON(`${MTD_API}/sessions/terminate_session/`, { session_key: sessionKey });
        if (r.ok) { showAlert('Session terminated', 'success'); await loadSessions(); }
        else { const e = await r.json(); showAlert(e.error || e.detail || 'Failed'); btn.disabled = false; }
    } catch(e) { showAlert('Error: ' + e.message); btn.disabled = false; }
}

async function closeSession(sessionKey, btn) {
    if (!confirm('Close and confirm this session?')) return;
    btn.disabled = true;
    try {
        const r = await postJSON(`${MTD_API}/sessions/close_session/`, { session_key: sessionKey });
        if (r.ok) { showAlert('Session closed', 'success'); await loadSessions(); }
        else { const e = await r.json(); showAlert(e.error || e.detail || 'Failed'); btn.disabled = false; }
    } catch(e) { showAlert('Error: ' + e.message); btn.disabled = false; }
}

async function rejectSession(sessionKey, btn) {
    const reason = prompt('Reason for rejecting this session (will be sent back to rep):');
    if (reason === null) return; // cancelled
    btn.disabled = true;
    try {
        const r = await postJSON(`${MTD_API}/sessions/reject_session/`, {
            session_key: sessionKey,
            reason: reason || 'Rejected by manager'
        });
        if (r.ok) { showAlert('Session rejected — sent back to rep', 'success'); await loadSessions(); }
        else { const e = await r.json(); showAlert(e.error || e.detail || 'Failed to reject'); btn.disabled = false; }
    } catch(e) { showAlert('Error: ' + e.message); btn.disabled = false; }
}

async function holdSession(sessionKey, btn) {
    if (!confirm('Put this session on hold? (Pauses follow-up notifications)')) return;
    btn.disabled = true;
    try {
        const r = await postJSON(`${MTD_API}/sessions/hold_session/`, { session_key: sessionKey });
        if (r.ok) { showAlert('Session placed on hold', 'success'); await loadSessions(); }
        else { const e = await r.json(); showAlert(e.error || e.detail || 'Failed to hold session'); btn.disabled = false; }
    } catch(e) { showAlert('Error: ' + e.message); btn.disabled = false; }
}

async function resumeSession(sessionKey, btn) {
    if (!confirm('Resume this session from on hold?')) return;
    btn.disabled = true;
    try {
        const r = await postJSON(`${MTD_API}/sessions/resume_session/`, { session_key: sessionKey });
        if (r.ok) { showAlert('Session resumed', 'success'); await loadSessions(); }
        else { const e = await r.json(); showAlert(e.error || e.detail || 'Failed to resume session'); btn.disabled = false; }
    } catch(e) { showAlert('Error: ' + e.message); btn.disabled = false; }
}

// ── Utils ─────────────────────────────────────────────────────────────────────
function escHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => (
        { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
    ));
}

/** Escape a value for use inside an attribute selector. */
function cssEsc(v) {
    return (window.CSS && CSS.escape) ? CSS.escape(v) : String(v).replace(/["\\]/g, '\\$&');
}

function today() { return new Date().toISOString().slice(0,10).replace(/-/g,''); }

function daysLeft(dateStr) {
    if (!dateStr) return 9999;
    return Math.round((new Date(dateStr) - new Date()) / 86400000);
}

function downloadCSV(rows, filename) {
    const esc = v => `"${String(v ?? '').replace(/"/g,'""')}"`;
    const csv = rows.map(r => r.map(esc).join(',')).join('\n');
    const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8;' });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href = url; a.download = filename + '.csv'; a.click();
    URL.revokeObjectURL(url);
}

// ── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    showActivePane();   // restores the opening tab's saved view and paints the switcher
    watchStatsBreakpoint();
    loadStats();
    loadSessions();
});
