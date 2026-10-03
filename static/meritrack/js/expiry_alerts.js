/* Expiry Alerts — uses /meritrack/api/inventory/expiry-list/ (scanned products only) */
const EA_API = '/meritrack/api';
let _eaItems = [];       // full list from API
let _eaLevel = 'all';    // current tier filter

/* ── Bootstrap ────────────────────────────────────────────────────────────── */
(async function init() {
    await loadExpiryAlerts();
})();

/* ── Data load ────────────────────────────────────────────────────────────── */
async function loadExpiryAlerts() {
    try {
        const resp = await fetch(`${EA_API}/inventory/expiry-list/`, { credentials: 'same-origin' });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        _eaItems = data.results || data || [];
        buildTierCounts();
        renderExpiryTable();
    } catch (e) {
        document.getElementById('alertsBody').innerHTML =
            `<tr><td colspan="11" class="ea-loading" style="color:var(--mt-danger);">Failed to load: ${escHtml(e.message)}</td></tr>`;
    }
}

/* ── Tier counts from loaded data ────────────────────────────────────────── */
function buildTierCounts() {
    const counts = { expired: 0, '1w': 0, '1m': 0, '3m': 0, '6m': 0, upcoming: 0 };
    _eaItems.forEach(i => { if (counts[i.expiry_level] !== undefined) counts[i.expiry_level]++; });

    const total = _eaItems.length;
    document.getElementById('cntExpired').textContent = counts.expired;
    document.getElementById('cnt1w').textContent      = counts['1w'];
    document.getElementById('cnt1m').textContent      = counts['1m'];
    document.getElementById('cnt3m').textContent      = counts['3m'];
    document.getElementById('cnt6m').textContent      = counts['6m'];
    document.getElementById('cntAll').textContent     = total;
}

/* ── Filter: tier pill ───────────────────────────────────────────────────── */
function setExpiryFilter(btn, level) {
    _eaLevel = level;
    document.querySelectorAll('.ep-filter').forEach(b => b.classList.remove('active'));
    if (btn) btn.classList.add('active');
    renderExpiryTable();
}

/* ── Filter: tier summary card click ────────────────────────────────────── */
function setTierFilter(card, level) {
    document.querySelectorAll('.ea-tier').forEach(c => c.classList.remove('active'));
    card.classList.add('active');
    // sync pill
    const pill = document.querySelector(`.ep-filter[data-level="${level}"]`);
    document.querySelectorAll('.ep-filter').forEach(b => b.classList.remove('active'));
    if (pill) pill.classList.add('active'); else {
        const allPill = document.querySelector('.ep-filter[data-level="all"]');
        if (allPill) allPill.classList.add('active');
    }
    _eaLevel = level;
    renderExpiryTable();
}

/* ── Render table ────────────────────────────────────────────────────────── */
function renderExpiryTable() {
    const search = (document.getElementById('expirySearch')?.value || '').toLowerCase().trim();
    let items = _eaLevel === 'all' ? _eaItems : _eaItems.filter(i => i.expiry_level === _eaLevel);

    if (search) {
        items = items.filter(i =>
            (i.serial_number  || '').toLowerCase().includes(search) ||
            (i.product_name   || '').toLowerCase().includes(search) ||
            (i.gtin_code      || '').toLowerCase().includes(search) ||
            (i.batch_number   || '').toLowerCase().includes(search) ||
            (i.hospital_name  || '').toLowerCase().includes(search)
        );
    }

    const countLabel = document.getElementById('eaCountLabel');
    const ackPct     = document.getElementById('eaAckPct');

    if (!items.length) {
        countLabel.textContent = '0 products';
        ackPct.style.display = 'none';
        document.getElementById('alertsBody').innerHTML =
            `<tr><td colspan="11" class="ea-empty"><i class="fas fa-check-circle" style="font-size:2em;color:var(--mt-success-dark);display:block;margin-bottom:10px;"></i>No products in this tier.</td></tr>`;
        return;
    }

    const acked = items.filter(i => i.acknowledged).length;
    countLabel.textContent = `${items.length} product${items.length !== 1 ? 's' : ''}`;
    if (acked > 0) {
        ackPct.style.display = '';
        ackPct.textContent = `${acked} acknowledged (${Math.round(acked / items.length * 100)}%)`;
    } else {
        ackPct.style.display = 'none';
    }

    document.getElementById('alertsBody').innerHTML = items.map((p, idx) => {
        const expCls  = getExpiryCls(p.days_to_expiry);
        const tierBadge = getTierBadge(p.expiry_level);
        const daysStr = p.days_to_expiry < 0
            ? `<span class="ea-days expired-days">${p.days_to_expiry}d</span>`
            : `<span class="ea-days ${expCls}">${p.days_to_expiry}d</span>`;

        const ackBtn = p.acknowledged
            ? `<span class="ea-acked"><i class="fas fa-check"></i> Acked</span>`
            : String(p.id).startsWith('sl-')
                ? `<span class="ea-no-ack">—</span>`
                : `<button class="ea-ack-btn" onclick="acknowledgeExpiry(${p.id}, this)"><i class="fas fa-check"></i> Ack</button>`;

        return `<tr class="ea-row ${p.acknowledged ? 'row-acked' : ''}">
            <td class="ea-num">${idx + 1}</td>
            <td><code class="ea-serial">${escHtml(p.serial_number || '—')}</code></td>
            <td class="ea-product">
                <span class="ea-pname">${escHtml(p.product_name || '—')}</span>
                ${tierBadge}
            </td>
            <td class="ea-small">${escHtml(p.gtin_code || '—')}</td>
            <td class="ea-small">${escHtml(p.batch_number || '—')}</td>
            <td>${escHtml(p.hospital_name || '—')}</td>
            <td class="ea-small" style="color:var(--mt-gray-600);">${escHtml(p.department_name || '—')}</td>
            <td><span class="${expCls}" style="font-weight:600;">${p.expiry_date || '—'}</span></td>
            <td>${daysStr}</td>
            <td class="ea-small" style="color:var(--mt-gray-600);">${escHtml(p.placed_by || '—')}</td>
            <td style="text-align:center;">${ackBtn}</td>
        </tr>`;
    }).join('');
}

/* ── Acknowledge ─────────────────────────────────────────────────────────── */
async function acknowledgeExpiry(productId, btn) {
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';
    try {
        const resp = await fetch(`${EA_API}/inventory/${productId}/acknowledge-expiry/`, {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
            body: JSON.stringify({})
        });
        if (resp.ok) {
            const item = _eaItems.find(i => i.id === productId);
            if (item) item.acknowledged = true;
            renderExpiryTable();
        } else {
            const err = await resp.json();
            alert(err.error || 'Failed to acknowledge');
            btn.disabled = false;
            btn.innerHTML = '<i class="fas fa-check"></i> Ack';
        }
    } catch (e) {
        alert('Error: ' + e.message);
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-check"></i> Ack';
    }
}

/* ── Export CSV ──────────────────────────────────────────────────────────── */
function exportExpiryCSV() {
    const search = (document.getElementById('expirySearch')?.value || '').toLowerCase().trim();
    let items = _eaLevel === 'all' ? _eaItems : _eaItems.filter(i => i.expiry_level === _eaLevel);
    if (search) items = items.filter(i =>
        (i.serial_number || '').toLowerCase().includes(search) ||
        (i.product_name  || '').toLowerCase().includes(search) ||
        (i.hospital_name || '').toLowerCase().includes(search)
    );
    if (!items.length) { alert('No data to export'); return; }

    const headers = ['#','Serial No','Product','GTIN','Batch','Hospital','Department','Expiry Date','Days','Tier','Acknowledged','Placed By'];
    const rows = items.map((p, i) => [
        i + 1, p.serial_number, p.product_name, p.gtin_code, p.batch_number,
        p.hospital_name, p.department_name, p.expiry_date, p.days_to_expiry,
        p.expiry_level, p.acknowledged ? 'Yes' : 'No', p.placed_by
    ]);
    const csv = [headers, ...rows].map(r =>
        r.map(v => `"${String(v ?? '').replace(/"/g, '""')}"`).join(',')
    ).join('\n');
    const a = document.createElement('a');
    a.href = 'data:text/csv;charset=utf-8,' + encodeURIComponent(csv);
    a.download = `meritrack_expiry_${new Date().toISOString().slice(0,10)}.csv`;
    a.click();
}

/* ── Helpers ─────────────────────────────────────────────────────────────── */
function getExpiryCls(days) {
    if (days === null || days === undefined) return '';
    if (days < 0)   return 'mtd-exp-expired';
    if (days <= 7)  return 'mtd-exp-1w';
    if (days <= 30) return 'mtd-exp-1m';
    if (days <= 90) return 'mtd-exp-3m';
    if (days <= 180)return 'mtd-exp-6m';
    return 'mtd-exp-upcoming';
}

function getTierBadge(level) {
    const map = {
        expired:  ['ea-badge expired-badge',  'Expired'],
        '1w':     ['ea-badge week-badge',     '≤ 1 Week'],
        '1m':     ['ea-badge month-badge',    '≤ 1 Month'],
        '3m':     ['ea-badge months3-badge',  '≤ 3 Months'],
        '6m':     ['ea-badge months6-badge',  '≤ 6 Months'],
        upcoming: ['ea-badge ok-badge',       '>6 Months'],
    };
    const [cls, label] = map[level] || ['ea-badge', level];
    return `<span class="${cls}">${label}</span>`;
}

function escHtml(s) {
    if (!s) return '';
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
}
