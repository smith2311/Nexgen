/* analytics.js — MeriTrack Analytics
 * Sections:
 *   1. Inventory Overview  (ConsignedProduct status, hospital stock, top products, country)
 *   2. Expiry Analysis     (urgency tiers, at-risk by hospital)
 *   3. Scan Activity       (sessions by type, recent sessions)
 * All data comes from /meritrack/api/dashboard/* endpoints.
 */

const API = '/meritrack/api';

// ── Helpers ──────────────────────────────────────────────────────────────────

function fmtNum(n) { return (n || 0).toLocaleString(); }

function escHtml(s) {
    if (!s) return '';
    const d = document.createElement('div'); d.textContent = s; return d.innerHTML;
}

// alias for internal use
const esc = escHtml;

function hospitalId() {
    return document.getElementById('hospitalFilter')?.value || '';
}

function qs(params = {}) {
    const hid = hospitalId();
    if (hid) params.hospital_id = hid;
    const str = Object.entries(params).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&');
    return str ? '?' + str : '';
}

function spinner(id) {
    document.getElementById(id).innerHTML =
        '<div class="loading-spinner"><i class="fas fa-spinner fa-spin"></i></div>';
}

function errMsg(id) {
    document.getElementById(id).innerHTML =
        '<p style="color:var(--mt-danger);padding:16px;text-align:center;">Failed to load.</p>';
}

function noData(id) {
    document.getElementById(id).innerHTML =
        '<p style="color:var(--mt-gray-400);text-align:center;padding:24px;">No data available.</p>';
}

async function apiFetch(path) {
    const r = await fetch(path, { credentials: 'same-origin' });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
}

// ── Horizontal bar chart ──────────────────────────────────────────────────────
function renderBarChart(containerId, rows, { labelKey, valueKey, colorBase = 200, limit = 10 } = {}) {
    const el = document.getElementById(containerId);
    if (!rows || !rows.length) { noData(containerId); return; }
    const top = rows.slice(0, limit);
    const max = Math.max(...top.map(r => r[valueKey] || 0), 1);
    el.innerHTML = '<div class="bar-chart">' + top.map((r, i) => {
        const pct = ((r[valueKey] || 0) / max * 100).toFixed(1);
        const hue = (colorBase) + i * 14;
        const label = esc(String(r[labelKey] || '—'));
        const val = fmtNum(r[valueKey]);
        return `<div class="bar-row">
            <span class="bar-label" title="${label}">${label}</span>
            <div class="bar-track">
                <div class="bar-fill" style="width:${pct}%;background:hsl(${hue},62%,46%);">
                    ${r[valueKey] > max * 0.18 ? val : ''}
                </div>
            </div>
            <span class="bar-count">${val}</span>
        </div>`;
    }).join('') + '</div>';
}

// ── Stacked legend bar ───────────────────────────────────────────────────────
function renderStackedBar(containerId, rows, { labelKey, valueKey, colorKey } = {}) {
    const el = document.getElementById(containerId);
    if (!rows || !rows.length) { noData(containerId); return; }
    const total = rows.reduce((s, r) => s + (r[valueKey] || 0), 0) || 1;

    const barSegments = rows
        .filter(r => r[valueKey] > 0)
        .map(r => `<div style="flex:${r[valueKey]};background:${r[colorKey] || 'var(--mt-gray-400)'};height:100%;min-width:2px;" title="${esc(r[labelKey])}: ${fmtNum(r[valueKey])}"></div>`)
        .join('');

    const legend = rows.map(r => {
        const pct = ((r[valueKey] || 0) / total * 100).toFixed(1);
        return `<div style="display:flex;align-items:center;gap:8px;flex-wrap:nowrap;">
            <span style="min-width:80px;max-width:130px;font-size:0.82em;font-weight:600;
                         color:${r[colorKey]||'var(--mt-gray-400)'};overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">
                ${esc(r[labelKey])}
            </span>
            <div style="flex:1;background:var(--mt-gray-100);border-radius:6px;height:20px;overflow:hidden;min-width:60px;">
                <div style="width:${pct}%;background:${r[colorKey]||'var(--mt-gray-400)'};height:100%;border-radius:6px;
                             display:flex;align-items:center;padding-left:6px;font-size:0.7em;color:#fff;font-weight:600;min-width:24px;">
                    ${parseFloat(pct) > 5 ? pct + '%' : ''}
                </div>
            </div>
            <span style="min-width:40px;text-align:right;font-weight:700;font-size:0.85em;">${fmtNum(r[valueKey])}</span>
        </div>`;
    }).join('');

    el.innerHTML = `
        <div style="height:28px;border-radius:8px;overflow:hidden;display:flex;margin-bottom:16px;">${barSegments}</div>
        <div style="display:flex;flex-direction:column;gap:8px;">${legend}</div>
        <div style="text-align:center;margin-top:10px;font-size:0.8em;color:var(--mt-gray-600);">
            Total: <strong>${fmtNum(total)}</strong> units
        </div>`;
}

// ─────────────────────────────────────────────────────────────────────────────
// SECTION 1 — INVENTORY OVERVIEW
// ─────────────────────────────────────────────────────────────────────────────

async function loadStatusBreakdown() {
    spinner('statusBreakdown');
    try {
        const statusColors = {
            on_shelf: 'var(--mt-success)', used: 'var(--mt-purple)', returned: 'var(--mt-warning)',
            expired_removed: 'var(--mt-danger)', recalled: 'var(--mt-gray-600)', transferred: 'var(--mt-primary)',
        };
        const statusLabels = {
            on_shelf: 'On Shelf', used: 'Used on Patient', returned: 'Returned',
            expired_removed: 'Expired Removed', recalled: 'Recalled', transferred: 'Transferred',
        };
        const raw = await apiFetch(`${API}/dashboard/status-breakdown/${qs()}`);
        const rows = raw.map(r => ({
            ...r,
            label: statusLabels[r.status] || r.status,
            color: statusColors[r.status] || 'var(--mt-gray-400)',
        }));
        renderStackedBar('statusBreakdown', rows, { labelKey: 'label', valueKey: 'count', colorKey: 'color' });
    } catch (e) { errMsg('statusBreakdown'); }
}

async function loadHospitalInventory() {
    spinner('hospitalInventory');
    try {
        const data = await apiFetch(`${API}/dashboard/hospital-inventory/${qs()}`);
        if (!data.length) { noData('hospitalInventory'); return; }
        document.getElementById('hospitalInventory').innerHTML =
            '<div class="table-scroll-wrap"><table class="mt-table"><thead><tr>' +
            '<th>Hospital</th><th style="text-align:right">On Shelf</th>' +
            '<th style="text-align:right">Used</th><th style="text-align:right">Returned</th>' +
            '</tr></thead><tbody>' +
            data.slice(0, 25).map(d => `<tr>
                <td style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;"
                    title="${esc(d.hospital_name)}">${esc(d.hospital_name)}</td>
                <td style="text-align:right"><span class="badge badge-green">${fmtNum(d.on_shelf)}</span></td>
                <td style="text-align:right"><span class="badge badge-purple">${fmtNum(d.used)}</span></td>
                <td style="text-align:right"><span class="badge badge-yellow">${fmtNum(d.returned)}</span></td>
            </tr>`).join('') + '</tbody></table></div>';
    } catch (e) { errMsg('hospitalInventory'); }
}

async function loadTopProducts() {
    spinner('topProducts');
    try {
        const hid = hospitalId();
        const data = await apiFetch(`${API}/dashboard/top-products/?limit=12${hid ? '&hospital_id=' + hid : ''}`);
        renderBarChart('topProducts', data.map(d => ({
            ...d, label: d.product_name || d.material_code || '?'
        })), { labelKey: 'label', valueKey: 'count', colorBase: 160 });
    } catch (e) { errMsg('topProducts'); }
}

async function loadCountryBreakdown() {
    spinner('countryBreakdown');
    try {
        const data = await apiFetch(`${API}/dashboard/country-breakdown/`);
        const colors = ['var(--mt-primary)', 'var(--mt-success)', 'var(--mt-warning)', 'var(--mt-purple)', 'var(--mt-danger)', 'var(--mt-purple)', 'var(--mt-teal)'];
        const rows = data.map((r, i) => ({
            ...r,
            label: r.country || 'Unknown',
            color: colors[i % colors.length],
        }));
        renderStackedBar('countryBreakdown', rows, { labelKey: 'label', valueKey: 'count', colorKey: 'color' });
    } catch (e) { errMsg('countryBreakdown'); }
}

// ─────────────────────────────────────────────────────────────────────────────
// SECTION 2 — EXPIRY ANALYSIS
// ─────────────────────────────────────────────────────────────────────────────

async function loadExpiryBreakdown() {
    spinner('expiryBreakdown');
    try {
        const data = await apiFetch(`${API}/dashboard/expiry-breakdown/${qs()}`);
        renderStackedBar('expiryBreakdown', data, { labelKey: 'label', valueKey: 'count', colorKey: 'color' });
    } catch (e) { errMsg('expiryBreakdown'); }
}

async function loadExpiryByHospital() {
    spinner('expiryByHospital');
    try {
        const data = await apiFetch(`${API}/dashboard/expiry-by-hospital/${qs()}`);
        if (!data.length) {
            document.getElementById('expiryByHospital').innerHTML =
                '<p style="color:var(--mt-success);text-align:center;padding:24px;font-weight:600;">' +
                '<i class="fas fa-check-circle"></i> No products expiring within 90 days.</p>';
            return;
        }
        document.getElementById('expiryByHospital').innerHTML =
            '<div class="table-scroll-wrap"><table class="mt-table"><thead><tr>' +
            '<th>Hospital</th>' +
            '<th style="text-align:right;color:var(--mt-danger)">Expired</th>' +
            '<th style="text-align:right;color:var(--mt-orange)">≤7d</th>' +
            '<th style="text-align:right;color:var(--mt-warning)">≤30d</th>' +
            '<th style="text-align:right;color:var(--mt-info)">≤90d</th>' +
            '</tr></thead><tbody>' +
            data.map(d => `<tr>
                <td style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;"
                    title="${esc(d.hospital_name)}">${esc(d.hospital_name)}</td>
                <td style="text-align:right">${d.expired > 0 ? `<span class="badge badge-red">${fmtNum(d.expired)}</span>` : '<span style="color:var(--mt-gray-300)">—</span>'}</td>
                <td style="text-align:right">${d.expiring_7d > 0 ? `<span class="badge badge-orange">${fmtNum(d.expiring_7d)}</span>` : '<span style="color:var(--mt-gray-300)">—</span>'}</td>
                <td style="text-align:right">${d.expiring_30d > 0 ? `<span class="badge badge-yellow">${fmtNum(d.expiring_30d)}</span>` : '<span style="color:var(--mt-gray-300)">—</span>'}</td>
                <td style="text-align:right">${d.expiring_90d > 0 ? `<span class="badge badge-blue">${fmtNum(d.expiring_90d)}</span>` : '<span style="color:var(--mt-gray-300)">—</span>'}</td>
            </tr>`).join('') + '</tbody></table></div>';
    } catch (e) { errMsg('expiryByHospital'); }
}

// ─────────────────────────────────────────────────────────────────────────────
// SECTION 3 — SCAN ACTIVITY
// ─────────────────────────────────────────────────────────────────────────────

async function loadScanActivity() {
    spinner('scanByType');
    spinner('recentSessions');
    try {
        const data = await apiFetch(`${API}/dashboard/scan-activity/?days=30`);

        // Sessions by type
        const typeColors = { audit: 'var(--mt-primary)', replenish: 'var(--mt-success)', removal: 'var(--mt-danger)', transfer: 'var(--mt-warning)' };
        const typeIcon   = { audit: 'fa-search', replenish: 'fa-plus-circle', removal: 'fa-trash-alt', transfer: 'fa-exchange-alt' };

        if (!data.by_type.length) {
            noData('scanByType');
        } else {
            const maxT = Math.max(...data.by_type.map(r => r.total), 1);
            document.getElementById('scanByType').innerHTML =
                `<div style="margin-bottom:12px;font-size:0.82em;color:var(--mt-gray-600);">
                    <strong>${fmtNum(data.total_sessions)}</strong> total sessions &nbsp;·&nbsp;
                    <span style="color:var(--mt-success)"><strong>${fmtNum(data.completed_30d)}</strong> completed last 30d</span> &nbsp;·&nbsp;
                    <span style="color:var(--mt-warning)"><strong>${fmtNum(data.active_sessions)}</strong> active now</span>
                 </div>` +
                '<div class="bar-chart">' +
                data.by_type.map(r => {
                    const pct = (r.total / maxT * 100).toFixed(1);
                    const color = typeColors[r.session_type] || 'var(--mt-gray-400)';
                    return `<div class="bar-row">
                        <span class="bar-label" title="${esc(r.label)}">
                            <i class="fas ${typeIcon[r.session_type]||'fa-circle'}" style="color:${color};margin-right:4px;font-size:0.85em;"></i>${esc(r.label)}
                        </span>
                        <div class="bar-track">
                            <div class="bar-fill" style="width:${pct}%;background:${color};">
                                ${r.total > maxT * 0.18 ? fmtNum(r.total) : ''}
                            </div>
                        </div>
                        <span class="bar-count">${fmtNum(r.total)}</span>
                    </div>
                    <div style="font-size:0.72em;color:var(--mt-gray-400);margin-left:168px;margin-top:-4px;margin-bottom:4px;">
                        ${fmtNum(r.completed)} completed
                    </div>`;
                }).join('') + '</div>';
        }

        // Recent sessions table
        if (!data.recent.length) {
            document.getElementById('recentSessions').innerHTML =
                '<p style="color:var(--mt-gray-400);text-align:center;padding:24px;">No scan sessions in the last 30 days.</p>';
        } else {
            const statusBadge = {
                active: 'badge-blue', completed: 'badge-green',
                closed: 'badge-gray', cancelled: 'badge-red',
            };
            document.getElementById('recentSessions').innerHTML =
                '<div class="table-scroll-wrap"><table class="mt-table"><thead><tr>' +
                '<th>Type</th><th>Hospital</th><th>Rep</th><th>Status</th><th>Date</th>' +
                '</tr></thead><tbody>' +
                data.recent.map(s => `<tr>
                    <td><i class="fas ${typeIcon[s.session_type]||'fa-circle'}" style="color:var(--mt-gray-600);margin-right:5px;"></i>${esc(s.type_label)}</td>
                    <td style="max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${esc(s.hospital)}">${esc(s.hospital)}</td>
                    <td style="font-size:0.85em;color:var(--mt-gray-600);">${esc(s.rep)}</td>
                    <td><span class="badge ${statusBadge[s.status]||'badge-gray'}">${esc(s.status)}</span></td>
                    <td style="font-size:0.8em;color:var(--mt-gray-400);white-space:nowrap;">${esc(s.started_at)}</td>
                </tr>`).join('') + '</tbody></table></div>';
        }
    } catch (e) {
        errMsg('scanByType');
        errMsg('recentSessions');
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// BOOT
// ─────────────────────────────────────────────────────────────────────────────

function reloadAll() {
    loadStatusBreakdown();
    loadHospitalInventory();
    loadTopProducts();
    loadCountryBreakdown();
    loadExpiryBreakdown();
    loadExpiryByHospital();
    loadScanActivity();
}

document.addEventListener('DOMContentLoaded', reloadAll);

