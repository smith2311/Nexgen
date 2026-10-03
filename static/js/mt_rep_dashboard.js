/* mt_rep_dashboard.js
 * MeriTrack Rep Performance Dashboard — all client-side logic.
 * Page config injected by template via #page-config (JSON).
 */
(function () {
  'use strict';

  // ── Config ────────────────────────────────────────────────────────────────
  var _cfg               = JSON.parse(document.getElementById('page-config').textContent);
  var IS_MANAGER         = _cfg.isManager;
  var IS_REP             = _cfg.isRep;
  var CURRENT_USER_EMAIL = _cfg.currentUserEmail;
  var SELECTED_REP_INIT  = _cfg.selectedRepEmailInit;

  var API_PERF     = '/meritrack/api/sp-performance/';
  var API_SESSIONS = '/meritrack/api/sessions/';

  // ── State ─────────────────────────────────────────────────────────────────
  var currentYear       = new Date().getFullYear();
  var currentMonth      = new Date().getMonth() + 1;
  var currentRepEmail   = SELECTED_REP_INIT || (IS_MANAGER ? '' : CURRENT_USER_EMAIL);
  var currentMonthData  = null;
  var historyLoaded       = false;
  var overviewLoaded      = false;
  var batchStatusLoaded   = false;
  var _myScansData        = [];
  var _myScansFilter      = 'all';
  var _myScansLoaded      = false;
  var _myScansGrouped   = {};
  var _myScansHospitals = [];

  var EXPIRY_CFG = {
    'expired': { bg:'#fee2e2', color:'#dc2626', label:'Expired'    },
    '1w':      { bg:'#fecdd3', color:'#be123c', label:'≤ 1 Week'   },
    '1m':      { bg:'#fef3c7', color:'#d97706', label:'≤ 1 Month'  },
    '3m':      { bg:'#fef9c3', color:'#a16207', label:'≤ 3 Months' },
    '6m':      { bg:'#dcfce7', color:'#16a34a', label:'≤ 6 Months' },
    'ok':      { bg:'#f0fdf4', color:'#15803d', label:'OK'          },
  };

  // ── Init ──────────────────────────────────────────────────────────────────
  document.addEventListener('DOMContentLoaded', function () {
    updateMonthLabel();
    // Always default to the "This Month" tab. For managers with no rep selected,
    // current-month aggregates across all reps; overview loads lazily when its tab is opened.
    switchTabById('tab-hospitals');
    loadCurrentMonth();
    var modal = document.getElementById('sessionModal');
    if (modal) {
      modal.addEventListener('click', function (e) { if (e.target === this) closeSessionModal(); });
    }
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeSessionModal(); });
  });

  // ── Month navigation ──────────────────────────────────────────────────────
  function changeMonth(delta) {
    currentMonth += delta;
    if (currentMonth > 12) { currentMonth = 1; currentYear++; }
    if (currentMonth < 1)  { currentMonth = 12; currentYear--; }
    updateMonthLabel();
    historyLoaded     = false;
    overviewLoaded    = false;
    batchStatusLoaded = false;
    if (IS_MANAGER && !currentRepEmail) {
      loadOverview();
      loadCurrentMonth();
    } else {
      loadCurrentMonth();
    }
  }

  function updateMonthLabel() {
    var names = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    document.getElementById('monthLabel').textContent = names[currentMonth - 1] + ' ' + currentYear;
  }

  // ── Rep change (manager) ──────────────────────────────────────────────────
  function onRepChange() {
    currentRepEmail   = document.getElementById('repSelect').value;
    historyLoaded     = false;
    overviewLoaded    = false;
    batchStatusLoaded = false;
    _myScansLoaded    = false;
    // Stay on the "This Month" tab regardless of rep selection.
    switchTabById('tab-hospitals');
    loadCurrentMonth();
  }

  // ── Refresh ───────────────────────────────────────────────────────────────
  function refreshDashboard() {
    historyLoaded     = false;
    overviewLoaded    = false;
    batchStatusLoaded = false;
    _myScansLoaded    = false;
    if (IS_MANAGER && !currentRepEmail) {
      loadOverview();
      loadCurrentMonth();
    } else {
      loadCurrentMonth();
      var active = document.querySelector('.sp-tab-content.active');
      if (active) {
        if (active.id === 'tab-history')      loadHistory();
        if (active.id === 'tab-my-scans')     loadMyScans();
        if (active.id === 'tab-batch-status') loadBatchStatus();
      }
    }
  }

  // ── API helper ────────────────────────────────────────────────────────────
  function apiFetch(url) {
    return fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      });
  }

  // ── Load current month ────────────────────────────────────────────────────
  function loadCurrentMonth() {
    showHospitalsLoading(true);
    var url = API_PERF + 'current-month/?year=' + currentYear + '&month=' + currentMonth;
    if (currentRepEmail) url += '&rep_email=' + encodeURIComponent(currentRepEmail);

    apiFetch(url)
      .then(function (data) {
        currentMonthData = data;
        renderStats(data);
        renderHospitals(data.hospitals || []);
        renderPending(data.hospitals  || []);
      })
      .catch(function (err) {
        showHospitalsLoading(false);
        console.error('Failed to load current month:', err);
      });
  }

  // ── Stats ─────────────────────────────────────────────────────────────────
  function renderStats(data) {
    document.getElementById('statAssigned').textContent = data.assigned_hospitals != null ? data.assigned_hospitals : '—';
    document.getElementById('statVisited').textContent  = data.visited_hospitals  != null ? data.visited_hospitals  : '—';
    document.getElementById('statPending').textContent  = data.pending_hospitals  != null ? data.pending_hospitals  : '—';
    document.getElementById('statSessions').textContent = data.total_sessions     != null ? data.total_sessions     : '—';
    document.getElementById('statScans').textContent    = data.total_scans        != null ? data.total_scans        : '—';
    document.getElementById('statRate').textContent     = data.visit_rate         != null ? data.visit_rate + '%'   : '—';

    var badge = document.getElementById('pendingTabBadge');
    badge.textContent = data.pending_hospitals > 0 ? data.pending_hospitals : '';

    var rate = data.visit_rate || 0;
    var fill = document.getElementById('progressFill');
    fill.style.width = rate + '%';
    fill.className   = 'progress-bar-fill' + (rate >= 80 ? '' : rate >= 50 ? ' warn' : ' danger');
    document.getElementById('progressLeft').textContent  = (data.visited_hospitals || 0) + ' of ' + (data.assigned_hospitals || 0) + ' hospitals visited';
    document.getElementById('progressRight').textContent = rate + '% coverage';
  }

  // ── Hospital table ────────────────────────────────────────────────────────
  function renderHospitals(hospitals) {
    var wrap  = document.getElementById('hospitalsTableWrap');
    var tbody = document.getElementById('hospitalsTableBody');
    var empty = document.getElementById('hospitalsEmpty');
    tbody.innerHTML = '';
    if (!hospitals.length) {
      wrap.style.display  = 'none';
      empty.style.display = 'block';
      showHospitalsLoading(false);
      return;
    }
    hospitals.forEach(function (h, i) { tbody.appendChild(buildHospRow(h, i + 1)); });
    wrap.style.display  = '';
    empty.style.display = 'none';
    showHospitalsLoading(false);
  }

  function renderPending(hospitals) {
    var pending = hospitals.filter(function (h) { return h.status === 'pending'; });
    var wrap    = document.getElementById('pendingTableWrap');
    var tbody   = document.getElementById('pendingTableBody');
    var empty   = document.getElementById('pendingEmpty');
    tbody.innerHTML = '';
    if (!pending.length) {
      wrap.style.display  = 'none';
      empty.style.display = 'block';
      return;
    }
    pending.forEach(function (h, i) { tbody.appendChild(buildHospRow(h, i + 1)); });
    wrap.style.display  = '';
    empty.style.display = 'none';
  }

  function buildHospRow(h, idx) {
    var statusLabel = { pending: '⏳ Pending', in_progress: '🔄 In Progress', completed: '✅ Visited' };
    var badgeClass  = { pending: 'badge-red',  in_progress: 'badge-amber',    completed: 'badge-green' };
    var dotClass    = { pending: 'dot-pending', in_progress: 'dot-in_progress', completed: 'dot-completed' };
    var tr = document.createElement('tr');
    tr.innerHTML =
      '<td style="color:var(--sp-muted);font-size:.8rem;">' + idx + '</td>' +
      '<td class="hosp-name-cell">' + escHtml(h.name) + '</td>' +
      '<td style="font-size:.82rem;color:var(--sp-muted);">' + (h.sap_code  ? escHtml(h.sap_code)  : '—') + '</td>' +
      '<td style="font-size:.82rem;color:var(--sp-muted);">' + (h.province  ? escHtml(h.province)  : '—') + '</td>' +
      '<td><span class="badge ' + (badgeClass[h.status] || 'badge-gray') + '">' +
        '<span class="status-dot ' + (dotClass[h.status] || '') + '"></span>' +
        (statusLabel[h.status] || h.status) +
      '</span></td>' +
      '<td style="text-align:center;">' + (h.sessions_count
        ? '<span class="badge badge-blue">📋 ' + h.sessions_count + '</span>'
        : '<span style="color:var(--sp-muted);">—</span>') + '</td>' +
      '<td style="text-align:center;">' + (h.scans_count
        ? '<span class="badge badge-gray">📦 ' + h.scans_count + '</span>'
        : '<span style="color:var(--sp-muted);">—</span>') + '</td>' +
      '<td style="font-size:.82rem;color:var(--sp-muted);">' + (h.last_visit ? escHtml(h.last_visit) : '—') + '</td>' +
      '<td>' + (h.sessions_count
        ? '<button class="link-btn" data-id="' + h.id + '" data-name="' + escHtml(h.name) + '" onclick="mtRepDash.openSessionModal(+this.dataset.id, this.dataset.name)">🔍 View Sessions</button>'
        : '<span style="color:#cbd5e1;font-size:.8rem;">—</span>') + '</td>';
    return tr;
  }

  // ── Session modal ─────────────────────────────────────────────────────────
  function openSessionModal(hospitalId, hospitalName) {
    var modal = document.getElementById('sessionModal');
    document.getElementById('sessionModalTitle').textContent    = hospitalName;
    document.getElementById('sessionModalSubtitle').textContent =
      'Sessions in ' + ['','Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][currentMonth] + ' ' + currentYear;
    document.getElementById('sessionModalBody').innerHTML =
      '<div class="session-modal-loading"><div class="loading-spinner" style="display:inline-block;margin-right:8px;"></div> Loading sessions…</div>';
    modal.classList.add('open');
    document.body.style.overflow = 'hidden';

    var url = API_PERF + 'hospital-sessions/?hospital_id=' + hospitalId +
              '&year=' + currentYear + '&month=' + currentMonth;
    if (currentRepEmail) url += '&rep_email=' + encodeURIComponent(currentRepEmail);

    apiFetch(url)
      .then(function (data) { renderSessionModal(data); })
      .catch(function (err) {
        document.getElementById('sessionModalBody').innerHTML =
          '<div class="empty-state"><div class="icon">⚠️</div><p>Failed to load sessions: ' + escHtml(String(err)) + '</p></div>';
      });
  }

  function closeSessionModal() {
    var modal = document.getElementById('sessionModal');
    if (modal) { modal.classList.remove('open'); }
    document.body.style.overflow = '';
  }

  function renderSessionModal(data) {
    var body     = document.getElementById('sessionModalBody');
    var sessions = data.sessions || [];
    if (!sessions.length) {
      body.innerHTML = '<div class="empty-state"><div class="icon">📋</div><p>No sessions recorded for this month.</p></div>';
      return;
    }
    var statusBadge = {
      active:    '<span class="badge badge-amber">🔄 Active</span>',
      completed: '<span class="badge badge-blue">📋 Submitted</span>',
      closed:    '<span class="badge badge-green">✅ Confirmed</span>',
      cancelled: '<span class="badge badge-gray">❌ Cancelled</span>',
    };
    body.innerHTML = sessions.map(function (s) {
      return '<div class="session-row">' +
        '<div class="session-row-left">' +
          '<div class="session-row-title">📋 ' + escHtml(s.batch_id) +
            (s.session_type === 'Hospital Transfer' ? '<span class="badge badge-blue" style="margin-left:6px;">H2H</span>' : '') +
          '</div>' +
          '<div class="session-row-meta">' +
            '<span>🕒 ' + escHtml(s.started_at || '—') + '</span>' +
            (s.completed_at ? '<span>✅ Done: ' + escHtml(s.completed_at) + '</span>' : '') +
            (s.scanned_by   ? '<span>👤 ' + escHtml(s.scanned_by) + '</span>' : '') +
            '<span>📦 ' + (s.scans_count || 0) + ' scans</span>' +
          '</div>' +
        '</div>' +
        '<div class="session-row-right">' +
          (statusBadge[s.status] || '<span class="badge badge-gray">' + escHtml(s.status) + '</span>') +
          '<a href="' + escHtml(s.detail_url) + '" target="_blank" class="link-btn">🔗 Open Session</a>' +
        '</div>' +
      '</div>';
    }).join('');
  }

  // ── History tab ───────────────────────────────────────────────────────────
  function loadHistory() {
    if (historyLoaded) return;
    document.getElementById('historyLoading').style.display = 'flex';
    var wrap  = document.getElementById('historyTableWrap');
    var empty = document.getElementById('historyEmpty');
    if (wrap)  wrap.style.display  = 'none';
    if (empty) empty.style.display = 'none';

    var url = API_PERF + 'detail/?months=12';
    if (currentRepEmail) url += '&rep_email=' + encodeURIComponent(currentRepEmail);

    apiFetch(url)
      .then(function (data) {
        var tbody   = document.getElementById('historyBody');
        var history = data.history || [];
        tbody.innerHTML = '';
        if (!history.length) {
          document.getElementById('historyLoading').style.display = 'none';
          document.getElementById('historyEmpty').style.display   = 'block';
          return;
        }
        var now = new Date();
        history.forEach(function (m) {
          var isCurrentMonth = m.year === now.getFullYear() && m.month === now.getMonth() + 1;
          var rateColor = m.visit_rate >= 80 ? '#10b981' : m.visit_rate >= 50 ? '#f59e0b' : '#ef4444';
          var tr = document.createElement('tr');
          if (isCurrentMonth) tr.style.background = '#eff6ff';
          tr.innerHTML =
            '<td style="font-weight:' + (isCurrentMonth ? '700' : '400') + '">' +
              escHtml(m.month_label) + (isCurrentMonth ? ' <span class="badge badge-blue" style="font-size:.7rem;">Current</span>' : '') +
            '</td>' +
            '<td>' + m.assigned_hospitals + '</td>' +
            '<td><span style="color:#10b981;font-weight:600;">' + m.visited_hospitals + '</span></td>' +
            '<td><span style="color:' + (m.pending_hospitals > 0 ? '#ef4444' : '#10b981') + ';font-weight:600;">' + m.pending_hospitals + '</span></td>' +
            '<td>' + m.total_sessions + '</td>' +
            '<td>' + m.total_scans + '</td>' +
            '<td class="rate-cell"><span style="color:' + rateColor + '">' + m.visit_rate + '%</span></td>';
          tbody.appendChild(tr);
        });
        document.getElementById('historyLoading').style.display = 'none';
        if (wrap) wrap.style.display = '';
        historyLoaded = true;
      })
      .catch(function (err) {
        document.getElementById('historyLoading').style.display = 'none';
        console.error('History load failed:', err);
      });
  }

  // ── Overview tab (manager) ────────────────────────────────────────────────
  function loadOverview() {
    if (!IS_MANAGER || overviewLoaded) return;
    document.getElementById('overviewLoading').style.display = 'flex';
    document.getElementById('overviewCards').innerHTML       = '';
    document.getElementById('overviewEmpty').style.display   = 'none';

    var url = API_PERF + 'overview/?year=' + currentYear + '&month=' + currentMonth;

    apiFetch(url)
      .then(function (data) {
        var cards = document.getElementById('overviewCards');
        var reps  = data.salespersons || data.reps || [];
        if (!reps.length) {
          document.getElementById('overviewLoading').style.display = 'none';
          document.getElementById('overviewEmpty').style.display   = 'block';
          return;
        }
        // Aggregate totals for the stats row
        var agg = { assigned: 0, visited: 0, pending: 0, sessions: 0, scans: 0 };
        reps.forEach(function (rep) {
          agg.assigned  += rep.assigned_hospitals  || 0;
          agg.visited   += rep.visited_hospitals   || 0;
          agg.pending   += rep.pending_hospitals   || 0;
          agg.sessions  += rep.total_sessions      || 0;
          agg.scans     += rep.total_scans         || 0;
        });
        var aggRate = agg.assigned > 0 ? Math.round(agg.visited / agg.assigned * 100) : 0;
        renderStats({
          assigned_hospitals: agg.assigned,
          visited_hospitals:  agg.visited,
          pending_hospitals:  agg.pending,
          total_sessions:     agg.sessions,
          total_scans:        agg.scans,
          visit_rate:         aggRate,
        });

        reps.forEach(function (rep) {
          var visitRate = rep.visit_rate != null ? rep.visit_rate : 0;
          var visitColor = visitRate >= 80 ? '#10b981' : visitRate >= 50 ? '#f59e0b' : '#ef4444';
          var card = document.createElement('div');
          card.className = 'sp-card';
          card.innerHTML =
            '<div class="sp-card-name">👤 ' + escHtml(rep.name || rep.salesperson_name || rep.email) + '</div>' +
            '<div class="sp-card-email">' + escHtml(rep.email || rep.salesperson_email) + '</div>' +
            '<div class="sp-card-stats">' +
              '<div class="sp-card-stat"><div class="sp-card-stat-val" style="color:#6366f1">' + (rep.assigned_hospitals || 0) + '</div><div class="sp-card-stat-lbl">Assigned</div></div>' +
              '<div class="sp-card-stat"><div class="sp-card-stat-val" style="color:#10b981">' + (rep.visited_hospitals || 0) + '</div><div class="sp-card-stat-lbl">Visited</div></div>' +
              '<div class="sp-card-stat"><div class="sp-card-stat-val" style="color:#f59e0b">' + (rep.pending_hospitals || 0) + '</div><div class="sp-card-stat-lbl">Pending</div></div>' +
              '<div class="sp-card-stat"><div class="sp-card-stat-val" style="color:' + visitColor + '">' + visitRate + '%</div><div class="sp-card-stat-lbl">Coverage</div></div>' +
              '<div class="sp-card-stat"><div class="sp-card-stat-val" style="color:#8b5cf6">' + (rep.total_sessions || 0) + '</div><div class="sp-card-stat-lbl">Sessions</div></div>' +
              '<div class="sp-card-stat"><div class="sp-card-stat-val" style="color:#0ea5e9">' + (rep.total_scans || 0) + '</div><div class="sp-card-stat-lbl">Scans</div></div>' +
            '</div>' +
            '<button class="sp-card-view-btn" onclick="mtRepDash.viewRep(\'' + escHtml(rep.email || rep.salesperson_email) + '\')">📊 View Detail</button>';
          cards.appendChild(card);
        });
        document.getElementById('overviewLoading').style.display = 'none';
        overviewLoaded = true;
      })
      .catch(function (err) {
        document.getElementById('overviewLoading').style.display = 'none';
        console.error('Overview load failed:', err);
      });
  }

  function viewRep(email) {
    var sel = document.getElementById('repSelect');
    if (sel) sel.value = email;
    currentRepEmail   = email;
    historyLoaded     = false;
    overviewLoaded    = false;
    batchStatusLoaded = false;
    _myScansLoaded    = false;
    loadCurrentMonth();
    switchTabById('tab-hospitals');
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  // ── Tab switching ─────────────────────────────────────────────────────────
  function switchTab(e, tabId) {
    document.querySelectorAll('.sp-tab-btn').forEach(function (b) { b.classList.remove('active'); });
    document.querySelectorAll('.sp-tab-content').forEach(function (c) { c.classList.remove('active'); });
    e.currentTarget.classList.add('active');
    document.getElementById(tabId).classList.add('active');
    if (tabId === 'tab-history')      loadHistory();
    if (tabId === 'tab-overview')     loadOverview();
    if (tabId === 'tab-my-scans')     loadMyScans();
    if (tabId === 'tab-batch-status') loadBatchStatus();
  }

  function switchTabById(tabId) {
    document.querySelectorAll('.sp-tab-btn').forEach(function (b) { b.classList.remove('active'); });
    document.querySelectorAll('.sp-tab-content').forEach(function (c) { c.classList.remove('active'); });
    var btn = document.querySelector('[onclick*="' + tabId + '"]');
    if (btn) btn.classList.add('active');
    var tab = document.getElementById(tabId);
    if (tab) tab.classList.add('active');
    // Trigger data load for tabs that need it
    if (tabId === 'tab-history')      loadHistory();
    if (tabId === 'tab-overview')     loadOverview();
    if (tabId === 'tab-my-scans')     loadMyScans();
    if (tabId === 'tab-batch-status') loadBatchStatus();
  }

  // ── Batch Status tab ──────────────────────────────────────────────────────
  function loadBatchStatus() {
    if (batchStatusLoaded) return;
    var loading = document.getElementById('batchStatusLoading');
    var list    = document.getElementById('batchStatusList');
    loading.style.display = 'flex';
    list.innerHTML = '';
    var url = API_SESSIONS + 'my_batches/';
    if (currentRepEmail) url += '?rep_email=' + encodeURIComponent(currentRepEmail);

    apiFetch(url)
      .then(function (data) {
        renderBatchTracker(data.sessions || data.results || []);
        batchStatusLoaded = true;
      })
      .catch(function (err) {
        console.error('Batch status load failed:', err);
        list.innerHTML = '<p style="color:#dc2626;padding:2rem;text-align:center;">Failed to load batch data.</p>';
      })
      .finally(function () { loading.style.display = 'none'; });
  }

  function renderBatchTracker(sessions) {
    var list = document.getElementById('batchStatusList');
    sessions = sessions.filter(function (s) { return s.status !== 'cancelled' && s.scans_count > 0; });
    if (!sessions.length) {
      list.innerHTML = '<p style="color:#94a3b8;text-align:center;padding:2rem;">No batches found.</p>';
      return;
    }

    var STEPS = [
      { key: 'active',    label: 'Scanning'  },
      { key: 'completed', label: 'Submitted' },
      { key: 'closed',    label: 'Confirmed' },
    ];

    var grouped  = {};
    sessions.forEach(function (s) {
      var h = s.hospital_name || 'Unknown Hospital';
      if (!grouped[h]) grouped[h] = [];
      grouped[h].push(s);
    });
    var hospitals = Object.keys(grouped).sort();

    function buildCard(s) {
      var step = s.status_step;
      var DOT_ICONS = ['🔵','📤','✅'];
      var stepsHtml = STEPS.map(function (st, i) {
        var dc = i + 1 < step ? 'done' : i + 1 === step ? 'current' : 'pending';
        var lc = i + 1 < step ? 'done-label' : i + 1 === step ? 'active-label' : '';
        var lineHtml = i > 0 ? '<div class="tracker-line ' + (i < step ? 'done' : '') + '"></div>' : '';
        return lineHtml +
          '<div class="tracker-step"><div class="tracker-dot ' + dc + '">' + DOT_ICONS[i] + '</div>' +
          '<div class="tracker-label ' + lc + '">' + st.label + '</div></div>';
      }).join('');

      var remarkBadge = '';
      if (s.has_warehouse_remark) {
        remarkBadge = '<span onclick="mtRepDash.openBatchRemarksModal(\'' + escHtml(s.session_key) + '\')" title="Click to view remarks" style="display:inline-flex;align-items:center;gap:4px;background:#f5f3ff;color:#7c3aed;border:1px solid #ddd6fe;border-radius:20px;padding:2px 10px;font-size:.75rem;font-weight:700;margin-left:8px;cursor:pointer;">💬 ' + s.total_remarks + ' remark' + (s.total_remarks !== 1 ? 's' : '') + ' · WH replied</span>';
      } else if (s.total_remarks > 0) {
        remarkBadge = '<span onclick="mtRepDash.openBatchRemarksModal(\'' + escHtml(s.session_key) + '\')" title="Click to view remarks" style="display:inline-flex;align-items:center;gap:4px;background:#eff6ff;color:#2563eb;border:1px solid #bfdbfe;border-radius:20px;padding:2px 10px;font-size:.75rem;font-weight:700;margin-left:8px;cursor:pointer;">💬 ' + s.total_remarks + ' remark' + (s.total_remarks !== 1 ? 's' : '') + '</span>';
      }

      var cardBorder = s.has_warehouse_remark
        ? 'border-left:4px solid #7c3aed;background:linear-gradient(90deg,#faf5ff 0%,#fff 60%);'
        : s.total_remarks > 0
        ? 'border-left:4px solid #2563eb;background:linear-gradient(90deg,#eff6ff 0%,#fff 60%);'
        : '';

      return '<div class="batch-tracker" style="' + cardBorder + '">' +
        '<div class="batch-tracker-header"><div>' +
          '<div class="batch-tracker-title"></div>' +
          '<div class="batch-tracker-meta">Session:&nbsp;<b>' + escHtml(s.batch_id || s.session_key || '—') +
            '</b>&nbsp;·&nbsp;<b>' + (s.scans_count || 0) + '</b>&nbsp;scans' +
            (s.rep_name ? '&nbsp;·&nbsp;👤&nbsp;' + escHtml(s.rep_name) : '') +
            remarkBadge +
          '</div>' +
        '</div>' +
        '<a href="' + escHtml(s.detail_url || '#') + '" target="_blank" class="link-btn" style="flex-shrink:0;">🔗 Open</a>' +
        '</div>' +
        '<div class="tracker-steps">' + stepsHtml + '</div>' +
      '</div>';
    }

    list.innerHTML = hospitals.map(function (hospName) {
      return '<div class="batch-hosp-header">' +
        '<span>🏥</span><span class="batch-hosp-title">' + escHtml(hospName) + '</span>' +
        '</div>' +
        grouped[hospName].map(function (s) { return buildCard(s); }).join('');
    }).join('');
  }

  // ── My Scans tab ──────────────────────────────────────────────────────────
  function loadMyScans() {
    if (_myScansLoaded) { renderMyScansGrouped(); return; }
    var loading = document.getElementById('myScansLoading');
    loading.style.display = 'flex';
    var url = API_SESSIONS + 'my_scans/';
    if (currentRepEmail) url += '?rep_email=' + encodeURIComponent(currentRepEmail);

    apiFetch(url)
      .then(function (data) {
        _myScansData   = data.results || data.scans || [];
        _myScansLoaded = true;
        renderMyScansGrouped();
      })
      .catch(function (err) {
        console.error('My Scans load failed:', err);
        document.getElementById('myScansHospitalSections').innerHTML =
          '<p style="color:#dc2626;text-align:center;padding:2rem;">Failed to load data.</p>';
      })
      .finally(function () { loading.style.display = 'none'; });
  }

  function setMyScansFilter(btn, level) {
    _myScansFilter = level;
    document.querySelectorAll('.ms-filter').forEach(function (b) { b.classList.remove('active'); });
    btn.classList.add('active');
    renderMyScansGrouped();
  }

  function renderMyScansGrouped() {
    var rows = _myScansData;
    if (_myScansFilter !== 'all') {
      rows = rows.filter(function (r) { return r.expiry_level === _myScansFilter; });
    }
    var container = document.getElementById('myScansHospitalSections');
    if (!rows.length) {
      container.innerHTML = '<div class="empty-state"><div class="icon">📭</div><p>No products found for this filter.</p></div>';
      return;
    }

    _myScansGrouped = {};
    rows.forEach(function (r) {
      var h = r.hospital_name || 'Unknown Hospital';
      if (!_myScansGrouped[h]) _myScansGrouped[h] = [];
      _myScansGrouped[h].push(r);
    });
    _myScansHospitals = Object.keys(_myScansGrouped).sort();

    container.innerHTML = _myScansHospitals.map(function (hospName, hIdx) {
      var hospRows  = _myScansGrouped[hospName];
      var tableRows = hospRows.map(function (r) {
        var cfg   = EXPIRY_CFG[r.expiry_level];
        var rowBg = cfg && r.expiry_level !== 'ok' ? cfg.bg : '';
        var tdBg  = rowBg ? 'background:' + rowBg + ';' : '';
        var sn    = (r.serial_number && r.serial_number.indexOf('NSN-') !== 0) ? r.serial_number : '—';
        var scAt  = r.scanned_at ? r.scanned_at.replace('T', ' ').slice(0, 16) : '—';
        return '<tr>' +
          '<td class="col-product"  style="' + tdBg + '" title="' + escHtml(r.product_name  || '—') + '">' + escHtml(r.product_name  || '—') + '</td>' +
          '<td class="col-serial"   style="' + tdBg + '"><code style="font-size:.75rem;">' + escHtml(sn) + '</code></td>' +
          '<td class="col-gtin"     style="' + tdBg + '">' + escHtml(r.gtin_code     || '—') + '</td>' +
          '<td class="col-lot"      style="' + tdBg + '">' + escHtml(r.batch_number  || '—') + '</td>' +
          '<td class="col-material" style="' + tdBg + '">' + escHtml(r.material_code || '—') + '</td>' +
          '<td class="col-expiry"   style="' + tdBg + '">' + expiryBadge(r.expiry_date, r.expiry_level) + '</td>' +
          '<td class="col-batchid"  style="' + tdBg + '">' + escHtml(r.batch_id      || '—') + '</td>' +
          '<td class="col-session"  style="' + tdBg + '">' + sessionStatusBadge(r.session_status) + '</td>' +
          '<td class="col-scanned"  style="' + tdBg + '">' + escHtml(scAt) + '</td>' +
        '</tr>';
      }).join('');

      return '<div class="hosp-scan-section">' +
        '<div class="hosp-scan-header" onclick="mtRepDash.toggleHospSection(' + hIdx + ')">' +
          '<div style="display:flex;align-items:center;gap:10px;">' +
            '<span class="hosp-scan-chevron" id="hss-chev-' + hIdx + '">▼</span>' +
            '<span class="hosp-scan-name">🏥 ' + escHtml(hospName) + '</span>' +
            '<span class="badge badge-blue" style="font-size:.73rem;">' + hospRows.length + ' scan' + (hospRows.length !== 1 ? 's' : '') + '</span>' +
          '</div>' +
          '<button onclick="event.stopPropagation();mtRepDash.downloadHospitalCSV(' + hIdx + ')" ' +
            'style="background:#0771F4;color:#fff;border:none;border-radius:7px;padding:5px 14px;font-size:.78rem;font-weight:600;cursor:pointer;">⬇ CSV</button>' +
        '</div>' +
        '<div class="hosp-scan-body" id="hss-body-' + hIdx + '">' +
          '<div style="overflow-x:auto;-webkit-overflow-scrolling:touch;">' +
            '<table class="ms-scan-tbl" style="min-width:1000px;">' +
              '<thead><tr>' +
                '<th class="col-product">Product</th>' +
                '<th class="col-serial">Serial</th>' +
                '<th class="col-gtin">GTIN</th>' +
                '<th class="col-lot">Lot / Batch</th>' +
                '<th class="col-material">Material</th>' +
                '<th class="col-expiry">Expiry</th>' +
                '<th class="col-batchid">Session</th>' +
                '<th class="col-session">Status</th>' +
                '<th class="col-scanned">Scanned At</th>' +
              '</tr></thead>' +
              '<tbody>' + tableRows + '</tbody>' +
            '</table>' +
          '</div>' +
        '</div>' +
      '</div>';
    }).join('');
  }

  function toggleHospSection(idx) {
    var body = document.getElementById('hss-body-' + idx);
    var chev = document.getElementById('hss-chev-' + idx);
    if (!body) return;
    var isOpen = body.style.display !== 'none';
    body.style.display = isOpen ? 'none' : '';
    chev.textContent   = isOpen ? '▶' : '▼';
  }

  function downloadHospitalCSV(idx) {
    var hospName = _myScansHospitals[idx];
    if (!hospName) return;
    exportScansToCSV(_myScansGrouped[hospName] || [], hospName.replace(/[^a-z0-9]/gi, '_') + '_scans');
  }

  function downloadMyScansExcel() {
    var rows = _myScansData;
    if (_myScansFilter !== 'all') rows = rows.filter(function (r) { return r.expiry_level === _myScansFilter; });
    exportScansToCSV(rows, 'mt_scans_all');
  }

  function exportScansToCSV(rows, filename) {
    var statusMap = { active: 'Active', completed: 'Submitted', closed: 'Confirmed', cancelled: 'Cancelled' };
    function escCSV(v) { var s = String(v || '').replace(/"/g, '""'); return '"' + s + '"'; }
    var csv = '\uFEFF';
    csv += 'Hospital,Product,Serial Number,GTIN,Lot/Batch,Material Code,Expiry Date,Expiry Status,Session Key,Session Status,Scanned At\n';
    rows.forEach(function (r) {
      var sn       = (r.serial_number && r.serial_number.indexOf('NSN-') !== 0) ? r.serial_number : '';
      var scAt     = r.scanned_at ? r.scanned_at.replace('T', ' ').slice(0, 16) : '';
      var expiryLbl = (EXPIRY_CFG[r.expiry_level] || {}).label || '';
      var sesLbl   = statusMap[r.session_status] || (r.session_status || '');
      csv += [
        escCSV(r.hospital_name), escCSV(r.product_name), escCSV(sn),
        escCSV(r.gtin_code), escCSV(r.batch_number), escCSV(r.material_code),
        escCSV(r.expiry_date), escCSV(expiryLbl),
        escCSV(r.batch_id || r.session_key), escCSV(sesLbl), escCSV(scAt)
      ].join(',') + '\n';
    });
    var blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    var url  = URL.createObjectURL(blob);
    var a    = document.createElement('a');
    a.href = url; a.download = filename + '_' + new Date().toISOString().slice(0, 10) + '.csv';
    document.body.appendChild(a); a.click();
    URL.revokeObjectURL(url); document.body.removeChild(a);
  }

  // ── Helpers ───────────────────────────────────────────────────────────────
  function showHospitalsLoading(show) {
    var loader = document.getElementById('hospitalsLoading');
    var wrap   = document.getElementById('hospitalsTableWrap');
    loader.style.display = show ? 'flex' : 'none';
    if (show) wrap.style.display = 'none';
  }

  function escHtml(str) {
    if (!str) return '';
    return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  function expiryBadge(dateStr, level) {
    var c = EXPIRY_CFG[level] || { bg:'#f1f5f9', color:'#64748b', label:'—' };
    var d = dateStr ? escHtml(dateStr) : '—';
    return '<span style="display:inline-flex;flex-direction:column;align-items:center;background:' + c.bg + ';color:' + c.color + ';border-radius:6px;padding:2px 7px;font-size:.75rem;font-weight:600;line-height:1.3;white-space:nowrap;">' +
           d + '<small style="font-size:.68rem;font-weight:500;">' + c.label + '</small></span>';
  }

  function sessionStatusBadge(s) {
    var map = {
      active:    { bg:'#dbeafe', color:'#1d4ed8', label:'Active'    },
      completed: { bg:'#fef3c7', color:'#92400e', label:'Submitted' },
      closed:    { bg:'#d1fae5', color:'#065f46', label:'Confirmed' },
      cancelled: { bg:'#fee2e2', color:'#991b1b', label:'Cancelled' },
    };
    var c = map[s] || { bg:'#f1f5f9', color:'#475569', label: (s || '—') };
    return '<span style="background:' + c.bg + ';color:' + c.color + ';border-radius:12px;padding:2px 9px;font-size:.75rem;font-weight:700;">' + c.label + '</span>';
  }

  // ── Batch Remarks Modal ───────────────────────────────────────────────────
  async function openBatchRemarksModal(sessionKey) {
    var html =
      '<div id="mtBatchRemarkModal" style="position:fixed;inset:0;background:rgba(15,23,42,.55);z-index:9999;display:flex;align-items:flex-end;justify-content:center;" onclick="if(event.target===this)this.remove()">' +
        '<div style="background:#fff;border-radius:20px 20px 0 0;width:min(620px,100%);max-height:80vh;overflow-y:auto;">' +
          '<div style="text-align:center;padding:10px 0 4px;"><div style="width:36px;height:4px;background:#e2e8f0;border-radius:2px;display:inline-block;"></div></div>' +
          '<div style="display:flex;justify-content:space-between;align-items:center;padding:6px 18px 12px;border-bottom:1px solid #f1f5f9;">' +
            '<div style="font-weight:800;font-size:1em;color:#0f172a;">💬 Batch Remarks</div>' +
            '<button onclick="document.getElementById(\'mtBatchRemarkModal\').remove()" style="background:#f1f5f9;color:#64748b;border:none;width:32px;height:32px;border-radius:50%;font-size:1.1em;cursor:pointer;">✕</button>' +
          '</div>' +
          '<div id="mtBatchRemarkBody" style="padding:16px 18px;"><div style="text-align:center;padding:20px;color:#94a3b8;">Loading…</div></div>' +
        '</div>' +
      '</div>';
    document.body.insertAdjacentHTML('beforeend', html);
    var body = document.getElementById('mtBatchRemarkBody');
    try {
      var sessData = await apiFetch(API_SESSIONS.replace('/sessions/', '/session-remarks/?session=') + encodeURIComponent(sessionKey));
      var prodData = await apiFetch(API_SESSIONS.replace('/sessions/', '/product-remarks/?session=') + encodeURIComponent(sessionKey));
      var sessRemarks = Array.isArray(sessData) ? sessData : (sessData.results || []);
      var prodRemarks = Array.isArray(prodData) ? prodData : (prodData.results || []);
      var all = sessRemarks.map(function(r){ return Object.assign({}, r, {_type:'batch'}); })
        .concat(prodRemarks.map(function(r){ return Object.assign({}, r, {_type:'product'}); }))
        .sort(function(a, b){ return new Date(a.created_at) - new Date(b.created_at); });
      if (!all.length) {
        body.innerHTML = '<p style="color:#94a3b8;font-style:italic;text-align:center;padding:16px;">No remarks found.</p>';
        return;
      }
      body.innerHTML = all.map(function(r) {
        var isWH = r.role === 'warehouse';
        var typeTag = r._type === 'product'
          ? '<span style="background:#fef3c7;color:#92400e;border-radius:10px;padding:1px 7px;font-size:.68rem;font-weight:700;margin-left:6px;">Product</span>'
          : '<span style="background:#dcfce7;color:#166534;border-radius:10px;padding:1px 7px;font-size:.68rem;font-weight:700;margin-left:6px;">Batch</span>';
        var dt = r.created_at ? new Date(r.created_at).toLocaleString() : '';
        return '<div style="padding:10px 12px;border-radius:8px;background:' + (isWH?'#faf5ff':'#f8fafc') + ';border:1px solid ' + (isWH?'#ddd6fe':'#e2e8f0') + ';margin-bottom:8px;">' +
          '<div style="display:flex;align-items:center;flex-wrap:wrap;gap:4px;margin-bottom:4px;">' +
            '<span style="font-weight:700;font-size:.8em;color:' + (isWH?'#7c3aed':'#2563eb') + ';">' + escHtml(r.created_by_name || '') + '</span>' +
            '<span style="font-size:.72em;color:#94a3b8;">(' + (isWH?'Warehouse':'Sales Rep') + ')</span>' +
            typeTag +
            '<span style="font-size:.72em;color:#cbd5e1;margin-left:auto;">' + escHtml(dt) + '</span>' +
          '</div>' +
          '<div style="font-size:.88em;color:#1e293b;line-height:1.5;">' + escHtml(r.remark) + '</div>' +
        '</div>';
      }).join('');
    } catch(e) {
      body.innerHTML = '<p style="color:#dc2626;">Failed to load remarks.</p>';
    }
  }

  // ── Public API (referenced from HTML attributes) ──────────────────────────
  window.mtRepDash = {
    openSessionModal:  openSessionModal,
    closeSessionModal: closeSessionModal,
    toggleHospSection: toggleHospSection,
    downloadHospitalCSV: downloadHospitalCSV,
    downloadMyScansExcel: downloadMyScansExcel,
    setMyScansFilter:  setMyScansFilter,
    viewRep:           viewRep,
    openBatchRemarksModal: openBatchRemarksModal,
  };

  // Expose top-level functions called from HTML onclick attributes
  window.changeMonth       = changeMonth;
  window.onRepChange       = onRepChange;
  window.refreshDashboard  = refreshDashboard;
  window.switchTab         = switchTab;

}());
