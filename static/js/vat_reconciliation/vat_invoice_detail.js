/* ═══════════════════════════════════════════════════════
   vat_invoice_detail.js — Invoice result / VAT Working Summary
═══════════════════════════════════════════════════════ */
(function () {
  'use strict';

  const ID = DET_CONFIG.invoiceId;
  const API = DET_CONFIG.urls;
  const CSRF = LogiTable.getCookie('csrftoken');
  const escHtml = LogiTable.escHtml;

  function g(id) { return document.getElementById(id); }
  function fmt(v) { return (v === null || v === undefined || v === '') ? '—' : v; }

  // ── Numeric helpers for the country output layouts ──
  function has(v) { return !(v === null || v === undefined || v === ''); }
  function num(v) {
    if (!has(v)) return 0;
    const n = parseFloat(String(v).replace(/,/g, ''));
    return isNaN(n) ? 0 : n;
  }
  // Grouped number with fixed decimals (dp defaults to 2)
  function grp(v, dp) {
    if (!has(v)) return '—';
    const d = (dp === undefined) ? 2 : dp;
    return num(v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
  }
  // First non-zero VAT % from the calc lines, else OCR, else a country default.
  function vatPct(d, fallback) {
    const lines = d.lines || [];
    for (let i = 0; i < lines.length; i++) {
      const p = num(lines[i].vat_percent);
      if (p) return p;
    }
    const oj = d.ocr_json || {};
    return num(oj.vat_percent) || fallback;
  }

  function toast(msg, type) {
    type = type || 'success';
    const el = g('lmToast'); if (!el) return;
    el.className = 'lm-toast ' + type;
    el.querySelector('.toast-msg').textContent = msg;
    el.querySelector('i').className =
      type === 'success' ? 'fas fa-check-circle' : type === 'error' ? 'fas fa-times-circle'
      : type === 'warning' ? 'fas fa-exclamation-triangle' : 'fas fa-info-circle';
    el.classList.add('show'); clearTimeout(el._t);
    el._t = setTimeout(function () { el.classList.remove('show'); }, 3200);
  }

  function statusBadge(s) {
    const el = g('hdrStatus');
    el.textContent = s || '—';
    el.className = 'badge ' + (s === 'MATCHED' ? 'badge-green' : s === 'DISCREPANCY' ? 'badge-red' : 'badge-amber');
    el.style.fontSize = '13px'; el.style.padding = '6px 14px';
  }

  function metaCard(label, value, icon) {
    return '<div class="det-card"><div class="det-card-icon"><i class="fas ' + icon + '"></i></div>' +
      '<div><div class="det-card-val">' + escHtml(String(fmt(value))) + '</div>' +
      '<div class="det-card-lbl">' + label + '</div></div></div>';
  }

  function render(d) {
    g('hdrInvoiceNo').textContent = d.invoice_number || 'Invoice';
    g('hdrSub').textContent = (d.country || '—') + ' · ' + (d.invoice_date || 'no date');
    statusBadge(d.recon_status);

    if (d.process_error) {
      g('errBanner').style.display = 'flex';
      g('errBannerMsg').textContent = d.process_error;
    } else {
      g('errBanner').style.display = 'none';
    }

    // Meta cards
    g('metaCards').innerHTML = [
      metaCard('Country', d.country, 'fa-globe'),
      metaCard('Local Entity', d.local_entity, 'fa-building'),
      metaCard('CHA Partner', d.cha_partner, 'fa-handshake'),
      metaCard('Invoice Currency', d.invoice_currency, 'fa-coins'),
      metaCard('Local Currency', d.local_currency, 'fa-money-bill'),
      metaCard('Exchange Rate', d.exchange_rate, 'fa-exchange-alt'),
      metaCard('Converted Taxable', d.converted_taxable, 'fa-calculator'),
      metaCard('Language', d.language, 'fa-language'),
    ].join('');

    // Country-specific VAT output + the calculation/formula behind it
    const cv = buildCountry(d);
    g('outputTitle').textContent = cv.title;
    const tag = g('outputCountryTag');
    if (tag) { tag.textContent = d.country || ''; tag.style.display = d.country ? '' : 'none'; }
    g('countryOutput').innerHTML = cv.output;
    g('vatFormula').innerHTML = cv.formula;

    // Reconciliation box
    const matched = d.recon_status === 'MATCHED';
    g('reconBox').innerHTML =
      reconItem('Grand Total Amount', fmt(d.taxable_amount)) +
      reconItem('Grand Total Expected VAT', fmt(d.expected_vat)) +
      reconItem('Invoice VAT', fmt(d.charged_vat)) +
      reconItem('Variance', fmt(d.variance), matched ? 'green' : 'red') +
      reconItem('Tolerance Used', fmt(d.tolerance_used)) +
      reconItem('Status', d.recon_status || '—', matched ? 'green' : 'red');

    // OCR raw text
    g('rawText').textContent = d.raw_ocr_text || '—';
    const langEl = g('ocrLang');
    if (langEl) { langEl.textContent = d.language || ''; langEl.style.display = d.language ? '' : 'none'; }

    // Translated English text (from the raw OCR text)
    g('translatedText').textContent = d.translated_text || d.raw_ocr_text || '—';

    loadEmailDraft();
  }

  function reconItem(label, value, color) {
    const cls = color ? ' det-recon-val-' + color : '';
    return '<div class="det-recon-item"><div class="det-recon-lbl">' + label + '</div>' +
      '<div class="det-recon-val' + cls + '">' + escHtml(String(value)) + '</div></div>';
  }

  // ═══════════════════════════════════════════════════════
  //  COUNTRY-SPECIFIC OUTPUT LAYOUTS  (mirror the Excel formats)
  //  Each returns { title, output(html), formula(html) }.
  // ═══════════════════════════════════════════════════════

  function buildCountry(d) {
    const c = (d.country || '').trim().toLowerCase();
    if (c.indexOf('korea') >= 0)    return viewKorea(d);
    if (c.indexOf('australia') >= 0) return viewAustralia(d);
    if (c.indexOf('hungary') >= 0)  return viewHungary(d);
    return viewHsn(d);   // Italy + default
  }

  function lineLabel(l, i) {
    return escHtml(l.hsn_sac_code || l.hsn || l.description || ('Line ' + (i + 1)));
  }

  // ── Italy / default: HSN pivot (Row Labels · Sum of VALUE · Vat · Amount) ──
  function viewHsn(d) {
    const lines = d.lines || [];
    let totVal = 0, totAmt = 0;
    const rows = lines.map(function (l, i) {
      const val = num(l.taxable_amount), p = num(l.vat_percent);
      const amt = has(l.expected_vat) ? num(l.expected_vat) : val * p / 100;
      totVal += val; totAmt += amt;
      return '<tr><td class="xl-l">' + lineLabel(l, i) + '</td>' +
        '<td class="xl-r">' + grp(val) + '</td>' +
        '<td class="xl-c">' + grp(p, 0) + '%</td>' +
        '<td class="xl-r">' + grp(amt) + '</td></tr>';
    }).join('');
    const output =
      '<table class="xl-table">' +
        '<thead><tr><th>Row Labels</th><th>Sum of VALUE</th><th>Vat</th><th>Amount</th></tr></thead>' +
        '<tbody>' + (rows || '<tr><td colspan="4" class="xl-empty">No line items</td></tr>') + '</tbody>' +
        '<tfoot><tr><td class="xl-l">Grand Total</td><td class="xl-r">' + grp(totVal) +
          '</td><td></td><td class="xl-r">' + grp(totAmt) + '</td></tr></tfoot>' +
      '</table>';
    const steps = lines.map(function (l, i) {
      const val = num(l.taxable_amount), p = num(l.vat_percent);
      const amt = has(l.expected_vat) ? num(l.expected_vat) : val * p / 100;
      return '<div class="fx-line"><span class="fx-code">' + lineLabel(l, i) + '</span>' +
        '<span>' + grp(val) + ' &times; ' + grp(p, 0) + '% = <b>' + grp(amt) + '</b></span></div>';
    }).join('');
    const formula =
      '<div class="fx-eq">Expected VAT (per line) = Taxable Value &times; VAT %</div>' +
      (steps || '<div class="fx-line">No line items to calculate.</div>') +
      '<div class="fx-total">Grand Expected VAT = &Sigma; line VAT = <b>' + grp(totAmt) + '</b></div>';
    return { title: 'VAT Working Summary — HSN Based', output: output, formula: formula };
  }

  // ── Korea: flat % on the local-currency converted value + line items ──
  function viewKorea(d) {
    const oj = d.ocr_json || {};
    const invCcy = d.invoice_currency || 'USD';
    const locCcy = d.local_currency || 'KRW';
    const inv = num(d.taxable_amount);
    const rate = num(d.exchange_rate) || num(oj.exchange_rate);
    const local = num(d.converted_taxable) || (inv * rate);
    const p = vatPct(d, 10);
    const vatLocal = local * p / 100;

    let output =
      '<table class="xl-table xl-narrow">' +
        '<thead><tr><th>Invoice Value</th><th>Amt</th></tr></thead><tbody>' +
        '<tr><td class="xl-l">In ' + escHtml(invCcy) + '</td><td class="xl-r">' + grp(inv) + '</td></tr>' +
        '<tr><td class="xl-l">In ' + escHtml(locCcy) + '</td><td class="xl-r">' + grp(local, 0) + '</td></tr>' +
        '<tr><td class="xl-l">VAT ' + grp(p, 0) + '%</td><td class="xl-r">' + grp(vatLocal, 0) + '</td></tr>' +
        '<tr><td class="xl-l">Exchange Rate</td><td class="xl-r">' + grp(rate) + '</td></tr>' +
        '</tbody></table>';

    const lines = d.lines || [];
    if (lines.length) {
      let tot = 0;
      const rows = lines.map(function (l, i) {
        const a = num(l.taxable_amount); tot += a;
        return '<tr><td class="xl-l">' + lineLabel(l, i) + '</td><td class="xl-r">' + grp(a) + '</td></tr>';
      }).join('');
      output += '<table class="xl-table xl-narrow" style="margin-top:14px;">' +
        '<thead><tr><th>Invoice Value</th><th>Amt</th></tr></thead><tbody>' + rows + '</tbody>' +
        '<tfoot><tr><td class="xl-l">Total</td><td class="xl-r">' + grp(tot) + '</td></tr></tfoot></table>';
    }

    const formula =
      '<div class="fx-eq">Amount in ' + escHtml(locCcy) + ' = Invoice (' + escHtml(invCcy) + ') &times; Exchange Rate</div>' +
      '<div class="fx-line"><span>' + grp(inv) + ' &times; ' + grp(rate) + ' = <b>' + grp(local, 0) + ' ' + escHtml(locCcy) + '</b></span></div>' +
      '<div class="fx-eq">Expected VAT = Amount in ' + escHtml(locCcy) + ' &times; VAT %</div>' +
      '<div class="fx-line"><span>' + grp(local, 0) + ' &times; ' + grp(p, 0) + '% = <b>' + grp(vatLocal, 0) + ' ' + escHtml(locCcy) + '</b></span></div>';
    return { title: 'Customs / VAT — Korea', output: output, formula: formula };
  }

  // ── Australia: VOTI-based customs GST + processing charges ──
  function viewAustralia(d) {
    const oj = d.ocr_json || {};
    const invoiceAmt = num(d.taxable_amount);
    const transport = num(oj.transport_insurance);
    const voti = invoiceAmt + transport;
    const p = vatPct(d, 10);
    const gst = voti * p / 100;
    const quarantine = num(oj.quarantine_charge);
    const declaration = num(oj.declaration_charge);
    const totalGov = gst + quarantine + declaration;

    let output =
      '<table class="xl-table xl-duty"><tbody>' +
        '<tr><td class="xl-l">Invoice Amt.</td><td class="xl-r">' + grp(invoiceAmt) + '</td></tr>' +
        '<tr><td class="xl-l">Transport &amp; Insurance</td><td class="xl-r">' + grp(transport) + '</td></tr>' +
        '<tr class="xl-strong"><td class="xl-l">Value of Taxable Import (VOTI)</td><td class="xl-r">' + grp(voti) + '</td></tr>' +
        '<tr><td class="xl-l">Standard GST</td><td class="xl-r">' + grp(p, 0) + '%</td></tr>' +
        '<tr><td class="xl-l">GST Amount</td><td class="xl-r">' + grp(gst) + '</td></tr>' +
        '<tr><td class="xl-l">Quarantine Processing Charge</td><td class="xl-r">' + grp(quarantine, 0) + '</td></tr>' +
        '<tr><td class="xl-l">Declaration Processing Charge</td><td class="xl-r">' + grp(declaration, 0) + '</td></tr>' +
        '<tr class="xl-strong"><td class="xl-l">Total Amt. Government Charges on Imported Goods</td><td class="xl-r">' + grp(totalGov) + '</td></tr>' +
      '</tbody></table>';

    const missing = [];
    if (!has(oj.transport_insurance)) missing.push('Transport & Insurance');
    if (!has(oj.quarantine_charge))   missing.push('Quarantine charge');
    if (!has(oj.declaration_charge))  missing.push('Declaration charge');
    if (missing.length) {
      output += '<div class="xl-note"><i class="fas fa-info-circle"></i> Not found in the document (shown as 0): ' +
        escHtml(missing.join(', ')) + '.</div>';
    }

    const formula =
      '<div class="fx-eq">VOTI = Invoice Amt + Transport &amp; Insurance</div>' +
      '<div class="fx-line"><span>' + grp(invoiceAmt) + ' + ' + grp(transport) + ' = <b>' + grp(voti) + '</b></span></div>' +
      '<div class="fx-eq">GST Amount = VOTI &times; GST %</div>' +
      '<div class="fx-line"><span>' + grp(voti) + ' &times; ' + grp(p, 0) + '% = <b>' + grp(gst) + '</b></span></div>' +
      '<div class="fx-eq">Total Govt Charges = GST + Quarantine + Declaration</div>' +
      '<div class="fx-line"><span>' + grp(gst) + ' + ' + grp(quarantine, 0) + ' + ' + grp(declaration, 0) +
        ' = <b>' + grp(totalGov) + '</b></span></div>';
    return { title: 'Format of Custom Duty — Australia', output: output, formula: formula };
  }

  // ── Hungary: flat % per line shown in invoice + local currency ──
  function viewHungary(d) {
    const oj = d.ocr_json || {};
    const ojLines = oj.lines || [];
    const invCcy = d.invoice_currency || 'EUR';
    const locCcy = d.local_currency || 'HUF';
    const rate = num(d.exchange_rate) || num(oj.exchange_rate);
    const p = vatPct(d, 27);
    const lines = d.lines || [];

    let totVat = 0;
    const rows = lines.map(function (l, i) {
      const eur = num(l.taxable_amount);
      const ojl = ojLines[i] || {};
      const huf = num(ojl.local_amount) || (eur * rate);
      const vat = huf * p / 100; totVat += vat;
      return '<tr><td class="xl-l">' + lineLabel(l, i) + '</td>' +
        '<td class="xl-r">' + grp(eur) + '</td>' +
        '<td class="xl-r">' + grp(huf, 0) + '</td>' +
        '<td class="xl-c">' + grp(p, 0) + '%</td>' +
        '<td class="xl-r">' + grp(vat) + '</td></tr>';
    }).join('');
    const output =
      '<table class="xl-table">' +
        '<thead><tr><th>Custom Invoice</th><th>Amt in ' + escHtml(invCcy) + '</th>' +
          '<th>Amt in ' + escHtml(locCcy) + '</th><th>Vat</th><th>Total</th></tr></thead>' +
        '<tbody>' + (rows || '<tr><td colspan="5" class="xl-empty">No line items</td></tr>') + '</tbody>' +
        '<tfoot><tr><td class="xl-l">Total</td><td></td><td></td><td></td><td class="xl-r">' + grp(totVat) + '</td></tr></tfoot>' +
      '</table>';

    const steps = lines.map(function (l, i) {
      const eur = num(l.taxable_amount);
      const ojl = ojLines[i] || {};
      const huf = num(ojl.local_amount) || (eur * rate);
      const vat = huf * p / 100;
      return '<div class="fx-line"><span class="fx-code">' + lineLabel(l, i) + '</span>' +
        '<span>' + grp(huf, 0) + ' &times; ' + grp(p, 0) + '% = <b>' + grp(vat) + '</b></span></div>';
    }).join('');
    const formula =
      '<div class="fx-eq">Amt in ' + escHtml(locCcy) + ' = Amt in ' + escHtml(invCcy) + ' &times; Exchange Rate (' + grp(rate) + ')</div>' +
      '<div class="fx-eq">VAT (Total) = Amt in ' + escHtml(locCcy) + ' &times; ' + grp(p, 0) + '%</div>' +
      (steps || '<div class="fx-line">No line items to calculate.</div>') +
      '<div class="fx-total">Total VAT = <b>' + grp(totVat) + '</b></div>';
    return { title: 'Format of Custom Duty — Hungary', output: output, formula: formula };
  }

  function load() {
    fetch(API.detail + ID + '/', { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (res) { if (!res.success) throw new Error(res.message || 'Load failed'); render(res.data); })
      .catch(function (e) { toast(e.message, 'error'); });
  }

  function loadEmailDraft() {
    fetch(API.emailDraft + ID + '/', { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (res) {
        if (res.success) { g('emailSubject').value = res.data.subject || ''; g('emailBody').value = res.data.body || ''; }
      })
      .catch(function () {});
  }

  window.reprocess = function (btn) {
    const orig = btn.innerHTML; btn.innerHTML = '<span class="loading-spinner"></span> Processing…'; btn.disabled = true;
    fetch(API.process + ID + '/', { method: 'POST', credentials: 'same-origin', headers: { 'X-CSRFToken': CSRF } })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
      .then(function (res) {
        if (res.data && res.data.data) render(res.data.data);
        toast(res.ok ? 'Re-processed' : (res.data.message || 'Failed'), res.ok ? 'success' : 'error');
      })
      .catch(function (e) { toast(e.message, 'error'); })
      .finally(function () { btn.innerHTML = orig; btn.disabled = false; });
  };

  window.copyEmail = function (btn) {
    const text = 'Subject: ' + g('emailSubject').value + '\n\n' + g('emailBody').value;
    navigator.clipboard.writeText(text).then(function () { toast('Email copied to clipboard', 'success'); })
      .catch(function () { toast('Could not copy', 'error'); });
  };

  /* ── SEND TO CHA ── */
  function setRecipientsHtml(html) { g('chaRecipients').innerHTML = html; }

  window.openSendCha = function () {
    // Prefill subject/body from the reviewed draft fields
    g('chaSubject').value = g('emailSubject').value || '';
    g('chaBody').value = g('emailBody').value || '';
    setRecipientsHtml('<div class="loading-overlay" style="padding:14px"><span class="loading-spinner"></span> Loading CHA contacts…</div>');
    g('sendChaOverlay').classList.add('open');
    document.body.style.overflow = 'hidden';

    fetch(API.chaContacts + ID + '/', { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (res) {
        if (!res.success) throw new Error(res.message || 'Could not load contacts');
        const sub = g('sendChaSub');
        if (sub) sub.textContent = res.country ? ('Country: ' + res.country) : 'Choose recipients';
        const contacts = res.contacts || [];
        if (!contacts.length) {
          setRecipientsHtml('<div style="font-size:13px;color:var(--text-muted)">No CHA contacts found for <strong>' +
            escHtml(res.country || 'this country') +
            '</strong>. Add them in the CHA Email Master first.</div>');
          return;
        }
        setRecipientsHtml(contacts.map(function (c) {
          const label = [c.cha_name, c.poc_name].filter(Boolean).join(' · ');
          return '<label style="display:flex;align-items:center;gap:8px;padding:5px 2px;cursor:pointer;font-size:13px;">' +
            '<input type="checkbox" class="cha-recip" value="' + escHtml(c.email) + '" checked>' +
            '<span><strong>' + escHtml(c.email) + '</strong>' +
            (label ? ' <span style="color:var(--text-muted)">(' + escHtml(label) + ')</span>' : '') +
            '</span></label>';
        }).join(''));
      })
      .catch(function (e) {
        setRecipientsHtml('<div style="font-size:13px;color:var(--red)">' + escHtml(e.message) + '</div>');
      });
  };

  window.closeSendCha = function () {
    g('sendChaOverlay').classList.remove('open');
    document.body.style.overflow = '';
  };

  window.sendCha = function (btn) {
    const recipients = [].slice.call(document.querySelectorAll('.cha-recip:checked'))
      .map(function (c) { return c.value; });
    if (!recipients.length) { toast('Select at least one recipient', 'warning'); return; }
    const subject = g('chaSubject').value.trim();
    const body = g('chaBody').value.trim();
    if (!body) { toast('Email body is empty', 'warning'); return; }
    const orig = btn.innerHTML; btn.innerHTML = '<span class="loading-spinner"></span> Sending…'; btn.disabled = true;
    fetch(API.sendCha + ID + '/', { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
      body: JSON.stringify({ recipients: recipients, subject: subject, body: body }) })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
      .then(function (res) {
        if (!res.ok || !res.data.success) throw new Error((res.data && res.data.message) || 'Send failed');
        toast(res.data.message || 'Email sent', 'success');
        closeSendCha();
      })
      .catch(function (e) { toast(e.message, 'error'); })
      .finally(function () { btn.innerHTML = orig; btn.disabled = false; });
  };

  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('.fi').forEach(function (el) { el.style.opacity = '1'; });
    load();
    const ov = g('sendChaOverlay');
    if (ov) ov.addEventListener('click', function (e) { if (e.target === ov) closeSendCha(); });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeSendCha(); });
  });
})();
