/* ═══════════════════════════════════════════════════════════════════════
   grocery_document_form.js — create / view a GRN or a Stock Issue.

   Driven by GS_DOC, built from DOCUMENT_SPECS in grocery_stock/pages_txn.py.

   ON THE LIVE TOTALS
   The panel shows line taxable / tax / total and the header grand total,
   because that arithmetic is per-line and unambiguous. It deliberately does
   NOT preview the apportioned landed cost: spreading header charges is the
   part of section 8 with rounding rules (the heaviest line absorbs the
   residue), and reimplementing that here would give two answers that drift
   apart the first time either changes. Landed cost is shown after posting,
   straight from the server.
═══════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  if (typeof GS_DOC === 'undefined') return;

  const U = GSUtil;
  const cfg = GS_DOC;

  const state = {
    id: cfg.documentId,
    doc: null,
    lines: [],          // in-memory rows while editing
    options: {},        // endpoint -> options
    stockByItem: {},    // item id -> available, for the issue screen
    dirty: false,
    posted: false,
  };

  let lineSeq = 0;

  const isPosted = () => state.doc && state.doc.status !== 'DRAFT';
  const editable = () => cfg.canWrite && !isPosted();

  /* ═══ HEADER FORM ═══════════════════════════════════════════════════ */

  function buildHeader() {
    U.$('gsHeaderGrid').innerHTML = cfg.headerFields.map((field) => {
      const parts = [];
      if (field.section) {
        parts.push(`<div class="gs-form-section span-2">${U.esc(field.section)}</div>`);
      }

      const id = 'gsh_' + field.name;
      const span = field.span === 2 || field.type === 'textarea' ? ' span-2' : '';
      const req = field.required ? '<span class="req">*</span>' : '';
      const help = field.help ? `<div class="form-hint">${U.esc(field.help)}</div>` : '';

      let control;
      switch (field.type) {
        case 'textarea':
          control = `<textarea class="form-textarea" id="${id}"></textarea>`; break;
        case 'date':
          control = `<input type="date" class="form-input" id="${id}">`; break;
        case 'decimal':
        case 'number':
          control = `<input type="number" class="form-input" id="${id}"
                       step="${U.esc(field.step || '1')}">`; break;
        case 'fk':
          control = `<select class="form-select" id="${id}"
                       data-source="${U.esc(field.source)}"
                       data-label="${U.esc(field.source_label)}">
                       <option value="">Loading…</option></select>`; break;
        default:
          control = `<input type="text" class="form-input" id="${id}"
                       ${field.maxlength ? `maxlength="${field.maxlength}"` : ''}
                       autocomplete="off">`;
      }

      parts.push(`<div class="form-field${span}">
        <label class="form-label" for="${id}">${U.esc(field.label)}${req}</label>
        ${control}${help}
        <div class="form-error" data-err="${U.esc(field.name)}"></div></div>`);
      return parts.join('');
    }).join('');

    cfg.headerFields.forEach((field) => {
      const el = U.$('gsh_' + field.name);
      if (!el) return;
      el.addEventListener('change', () => { state.dirty = true; });
      if (field.type === 'date' && field.default === 'today' && !state.id) {
        el.value = U.today();
      } else if (field.default !== undefined && field.default !== 'today' && !state.id) {
        el.value = field.default;
      }
    });
  }

  async function loadHeaderOptions() {
    await Promise.all(cfg.headerFields
      .filter((f) => f.type === 'fk')
      .map(async (field) => {
        const select = U.$('gsh_' + field.name);
        try {
          const options = await U.loadOptions(cfg.lookupBase, field.source,
                                              field.source_label);
          state.options[field.source] = options;
          const keep = select.dataset.pending || select.value || '';
          select.innerHTML = '<option value="">— select —</option>' +
            options.map((o) => `<option value="${o.id}">${U.esc(o.label)}</option>`).join('');
          if (keep) select.value = keep;
        } catch (e) {
          select.innerHTML = '<option value="">Could not load</option>';
        }
      }));
  }

  function fillHeader(doc) {
    cfg.headerFields.forEach((field) => {
      const el = U.$('gsh_' + field.name);
      if (!el) return;
      const value = doc[field.name];
      if (field.type === 'fk') {
        const v = value === null || value === undefined ? '' : String(value);
        if (el.options.length <= 1) el.dataset.pending = v; else el.value = v;
      } else {
        el.value = value === null || value === undefined ? '' : value;
      }
    });
  }

  function collectHeader() {
    const payload = {};
    cfg.headerFields.forEach((field) => {
      const el = U.$('gsh_' + field.name);
      if (!el) return;
      const raw = el.value.trim();
      if (raw === '') {
        // Blank fk / number must be null — DRF rejects '' for both.
        payload[field.name] = (field.type === 'fk' || field.type === 'number'
                               || field.type === 'decimal' || field.type === 'date')
          ? null : '';
      } else {
        payload[field.name] = raw;
      }
    });
    return payload;
  }

  /* ═══ LINES ═════════════════════════════════════════════════════════ */

  function blankLine() {
    const line = { _key: 'L' + (++lineSeq) };
    cfg.lineColumns.forEach((col) => {
      line[col.name] = col.type === 'decimal' || col.type === 'number' ? '' : '';
    });
    return line;
  }

  function renderLinesHead() {
    const cells = cfg.lineColumns.map((col) => {
      const width = col.width ? ` style="width:${col.width}"` : '';
      const req = col.required ? '<span class="req">*</span>' : '';
      return `<th${width}>${U.esc(col.label)}${req}</th>`;
    }).join('');

    // Live per-line arithmetic while editing; server figures once posted.
    const extra = isPosted()
      ? cfg.lineResults.map((c) => `<th class="gs-cell-num">${U.esc(c.label)}</th>`).join('')
      : (cfg.slug === 'receipts' ? '<th class="gs-cell-num" style="width:120px">Line Total</th>' : '');

    const avail = cfg.showAvailability && !isPosted()
      ? `<th style="width:150px">${U.esc(cfg.availabilityLabel || 'Available')}</th>`
      : '';

    U.$('gsLinesHead').innerHTML =
      `<tr>${cells}${avail}${extra}<th style="width:46px"></th></tr>`;
  }

  function lineTotal(line) {
    const qty = Number(line.accepted_quantity || 0);
    const rate = Number(line.unit_price || 0);
    const disc = Number(line.discount_amount || 0);
    const tax = Number(line.tax_percent || 0);
    const taxable = Math.max(qty * rate - disc, 0);
    return taxable + taxable * tax / 100;
  }

  /**
   * The advisory quantity shown beside a line.
   *
   * An issue asks "is there enough?" — so the badge turns red when the line
   * exceeds stock. An adjustment asks "what is the variance?" — over-count and
   * under-count are both legitimate, so it shows the book figure plus a signed
   * delta rather than an error.
   *
   * Either way this is guidance only. The server recomputes from the locked
   * row at posting, so a stale figure here cannot corrupt anything.
   */
  function availabilityCell(line) {
    const known = state.stockByItem[line.item];
    if (known === undefined) return '<span class="gs-cell-muted">—</span>';

    const entered = Number(line[cfg.quantityField] || 0);

    if (cfg.slug === 'adjustments') {
      const delta = entered - known;
      const tone = delta === 0 ? 'gs-badge-off'
                 : delta > 0 ? 'gs-badge-on' : 'gs-badge-warn';
      const sign = delta > 0 ? '+' : '';
      return `<span class="gs-badge gs-badge-off">${U.qty(known)}</span>`
           + (line[cfg.quantityField] === '' || line[cfg.quantityField] === undefined
              ? ''
              : ` <span class="gs-badge ${tone}">${sign}${U.qty(delta)}</span>`);
    }

    const short = entered > known;
    return `<span class="gs-badge ${short ? 'gs-badge-danger' : 'gs-badge-on'}">`
         + `${U.qty(known)}</span>`;
  }

  function renderLines() {
    const body = U.$('gsLinesBody');
    const empty = U.$('gsLinesEmpty');

    if (!state.lines.length) {
      body.innerHTML = '';
      empty.hidden = false;
      renderTotals();
      return;
    }
    empty.hidden = true;

    body.innerHTML = state.lines.map((line) => {
      const cells = cfg.lineColumns.map((col) => {
        const value = line[col.name];
        if (!editable()) {
          // Read-only view: show the resolved label, not the raw id.
          let shown = value;
          if (col.type === 'fk') {
            const opt = (state.options[col.source] || [])
              .find((o) => String(o.id) === String(value));
            shown = opt ? opt.label : value;
          } else if (col.type === 'date') {
            shown = U.dateText(value);
          }
          return `<td>${shown ? U.esc(shown) : '<span class="gs-cell-muted">—</span>'}</td>`;
        }

        const attrs = `data-key="${line._key}" data-name="${U.esc(col.name)}"`;
        if (col.type === 'fk') {
          return `<td><select class="form-select gs-line-input" ${attrs}
                    data-source="${U.esc(col.source)}"><option value="">—</option>
                  </select></td>`;
        }
        if (col.type === 'date') {
          return `<td><input type="date" class="form-input gs-line-input" ${attrs}
                    value="${U.esc(value || '')}"></td>`;
        }
        if (col.type === 'decimal' || col.type === 'number') {
          return `<td><input type="number" class="form-input gs-line-input gs-cell-num"
                    ${attrs} step="${U.esc(col.step || '1')}"
                    value="${U.esc(value || '')}"></td>`;
        }
        return `<td><input type="text" class="form-input gs-line-input" ${attrs}
                  value="${U.esc(value || '')}" autocomplete="off"></td>`;
      }).join('');

      const avail = cfg.showAvailability && !isPosted()
        ? `<td>${availabilityCell(line)}</td>` : '';

      let extra = '';
      if (isPosted()) {
        extra = cfg.lineResults.map((c) =>
          `<td class="gs-cell-num">${U.cell(line, c)}</td>`).join('');
      } else if (cfg.slug === 'receipts') {
        extra = `<td class="gs-cell-num">${U.money(lineTotal(line))}</td>`;
      }

      const remove = editable()
        ? `<td><button type="button" class="gs-icon-btn gs-danger gs-line-del"
             data-key="${line._key}" title="Remove line">
             <i class="fas fa-xmark"></i></button></td>`
        : '<td></td>';

      return `<tr>${cells}${avail}${extra}${remove}</tr>`;
    }).join('');

    if (editable()) wireLineInputs();
    renderTotals();
  }

  function wireLineInputs() {
    const body = U.$('gsLinesBody');

    // Populate the fk selects, preserving each row's current value.
    body.querySelectorAll('select.gs-line-input').forEach((select) => {
      const col = cfg.lineColumns.find((c) => c.name === select.dataset.name);
      const options = state.options[col.source] || [];
      const line = state.lines.find((l) => l._key === select.dataset.key);
      select.innerHTML = '<option value="">— select —</option>' +
        options.map((o) => `<option value="${o.id}">${U.esc(o.label)}</option>`).join('');
      if (line && line[col.name]) select.value = line[col.name];
    });

    body.querySelectorAll('.gs-line-input').forEach((input) => {
      input.addEventListener('input', onLineInput);
      input.addEventListener('change', onLineInput);
    });

    body.querySelectorAll('.gs-line-del').forEach((btn) => {
      btn.addEventListener('click', () => {
        state.lines = state.lines.filter((l) => l._key !== btn.dataset.key);
        state.dirty = true;
        renderLines();
      });
    });
  }

  function onLineInput(e) {
    const el = e.target;
    const line = state.lines.find((l) => l._key === el.dataset.key);
    if (!line) return;

    line[el.dataset.name] = el.value;
    state.dirty = true;

    if (el.dataset.name === 'item' && cfg.showAvailability) {
      fetchAvailability(el.value);
    }
    // Re-rendering on every keystroke would steal focus, so only the derived
    // cells are refreshed in place.
    refreshDerivedCells();
  }

  function refreshDerivedCells() {
    const rows = U.$('gsLinesBody').querySelectorAll('tr');
    state.lines.forEach((line, i) => {
      const row = rows[i];
      if (!row) return;

      if (cfg.showAvailability && !isPosted()) {
        const td = row.children[cfg.lineColumns.length];
        if (td) td.innerHTML = availabilityCell(line);
      }
      if (cfg.slug === 'receipts' && !isPosted()) {
        const td = row.children[cfg.lineColumns.length];
        if (td) td.textContent = U.money(lineTotal(line));
      }
    });
    renderTotals();
  }

  async function fetchAvailability(itemId) {
    if (!itemId || state.stockByItem[itemId] !== undefined) return;
    const warehouse = U.$('gsh_warehouse') ? U.$('gsh_warehouse').value : '';
    if (!warehouse) return;
    try {
      const data = await U.api(
        `${cfg.lookupBase}stock/?item=${itemId}&warehouse=${warehouse}`);
      const total = (data.results || [])
        .reduce((sum, r) => sum + Number(r.quantity_available || 0), 0);
      state.stockByItem[itemId] = total;
      refreshDerivedCells();
    } catch (e) { /* availability is advisory; posting is authoritative */ }
  }

  /* ═══ TOTALS ════════════════════════════════════════════════════════ */

  function renderTotals() {
    const box = U.$('gsTotals');

    if (isPosted()) {
      const d = state.doc;
      const rows = cfg.slug === 'receipts'
        ? [['Subtotal', d.subtotal_amount], ['Tax', d.tax_amount],
           ['Invoice Total', d.total_amount],
           ['Value added to stock', d.inventory_value, true]]
        : [['Total issued value', d.total_value, true]];
      box.innerHTML = rows.map(([label, value, strong]) =>
        `<div class="gs-total-row${strong ? ' gs-total-strong' : ''}">
           <span>${U.esc(label)}</span><span>${U.money(value)}</span></div>`).join('');
      return;
    }

    if (cfg.slug !== 'receipts') { box.innerHTML = ''; return; }

    const lineSum = state.lines.reduce((s, l) => s + lineTotal(l), 0);
    const charges = ['freight_amount', 'loading_amount', 'insurance_amount',
                     'other_charges']
      .reduce((s, name) => s + Number((U.$('gsh_' + name) || {}).value || 0), 0);

    box.innerHTML = `
      <div class="gs-total-row"><span>Lines total</span>
        <span>${U.money(lineSum)}</span></div>
      <div class="gs-total-row"><span>Charges to apportion</span>
        <span>${U.money(charges)}</span></div>
      <div class="gs-total-row gs-total-strong"><span>Invoice total</span>
        <span>${U.money(lineSum + charges)}</span></div>
      <div class="gs-total-note">
        <i class="fas fa-circle-info"></i>
        Landed cost per unit is calculated by the server when you post — GST is
        excluded for items marked GST-creditable.
      </div>`;
  }

  /* ═══ LOAD / SAVE / POST ════════════════════════════════════════════ */

  function applyDocument(doc) {
    state.doc = doc;
    state.lines = (doc.lines || []).map((l) =>
      Object.assign({ _key: 'L' + (++lineSeq) }, l));

    fillHeader(doc);

    const number = doc[cfg.numberField];
    U.$('gsDocTitle').textContent = number || `${cfg.title} (draft)`;

    const chip = U.$('gsStatusChip');
    chip.hidden = false;
    chip.textContent = doc.status_display || doc.status;
    chip.className = 'gs-status-chip gs-chip-' + (doc.status || '').toLowerCase();

    if (isPosted()) {
      U.$('gsReadOnlyBanner').hidden = false;
      U.$('gsReadOnlyText').textContent =
        `This ${cfg.title.toLowerCase()} is ${(doc.status_display || '').toLowerCase()} ` +
        `and can no longer be edited. Its stock movements are part of the ledger.`;
      document.querySelectorAll('#gsHeaderGrid input, #gsHeaderGrid select, ' +
                                '#gsHeaderGrid textarea')
        .forEach((el) => { el.disabled = true; });
      U.$('gsAddLineBtn').hidden = true;
    }

    updateButtons();
    renderLinesHead();
    renderLines();
  }

  function updateButtons() {
    U.$('gsSaveDraftBtn').hidden = !editable();
    U.$('gsPostBtn').hidden = !editable() || !state.id;
    U.$('gsAddLineBtn').hidden = !editable();
  }

  function collectPayload() {
    const payload = collectHeader();
    payload.lines = state.lines.map((line) => {
      const out = {};
      cfg.lineColumns.forEach((col) => {
        const raw = (line[col.name] === undefined || line[col.name] === null)
          ? '' : String(line[col.name]).trim();
        if (raw === '') {
          out[col.name] = (col.type === 'fk' || col.type === 'date') ? null
            : (col.type === 'decimal' || col.type === 'number') ? '0' : '';
        } else {
          out[col.name] = raw;
        }
      });
      if (line.id) out.id = line.id;
      return out;
    });
    return payload;
  }

  function showError(message) {
    const box = U.$('gsFormError');
    // Text goes in the inner span so the banner keeps its icon.
    U.$('gsFormErrorText').textContent = message;
    box.hidden = false;
    box.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }

  function clearError() {
    U.$('gsFormError').hidden = true;
    document.querySelectorAll('[data-err]').forEach((el) => { el.textContent = ''; });
  }

  async function saveDraft(silent) {
    clearError();

    if (!state.lines.length) {
      showError('Add at least one line before saving.');
      return false;
    }

    const btn = U.$('gsSaveDraftBtn');
    const original = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="gs-spinner"></span> Saving…';

    try {
      const payload = collectPayload();
      const doc = state.id
        ? await U.api(cfg.apiBase + state.id + '/',
                      { method: 'PATCH', body: JSON.stringify(payload) })
        : await U.api(cfg.apiBase, { method: 'POST', body: JSON.stringify(payload) });

      const isNew = !state.id;
      state.id = doc.id;
      state.dirty = false;
      applyDocument(doc);

      if (!silent) U.toast('Draft saved');
      if (isNew) {
        // Put the id in the URL so a refresh reopens the same draft.
        window.history.replaceState({}, '',
          cfg.detailUrlTemplate.replace(/0\/$/, doc.id + '/'));
      }
      return true;
    } catch (err) {
      showError(U.errorMessage(err, 'Could not save the draft.'));
      return false;
    } finally {
      btn.disabled = false;
      btn.innerHTML = original;
    }
  }

  function openPostConfirm() {
    const lines = state.lines.length;
    const rows = [['Lines', lines]];
    if (cfg.slug === 'receipts') {
      rows.push(['Invoice total',
                 U.money(state.lines.reduce((s, l) => s + lineTotal(l), 0))]);
    }
    U.$('gsPostSummary').innerHTML = rows.map(([k, v]) =>
      `<div class="lmodal-record-row">
         <span class="lmodal-record-lbl">${U.esc(k)}</span>
         <span class="lmodal-record-val">${U.esc(v)}</span></div>`).join('');
    Modal.open('gsPostOverlay');
  }

  async function doPost() {
    const btn = U.$('gsPostConfirm');
    const original = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="gs-spinner"></span> Posting…';

    try {
      // Always flush pending edits first, so what posts is what is on screen.
      if (state.dirty && !(await saveDraft(true))) {
        Modal.close('gsPostOverlay');
        return;
      }
      const doc = await U.api(`${cfg.apiBase}${state.id}/post_document/`,
                              { method: 'POST', body: '{}' });
      Modal.close('gsPostOverlay');
      applyDocument(doc);
      U.toast(`Posted ${doc[cfg.numberField]}`);
    } catch (err) {
      Modal.close('gsPostOverlay');
      showError(U.errorMessage(err, 'Could not post this document.'));
    } finally {
      btn.disabled = false;
      btn.innerHTML = original;
    }
  }

  /* ═══ INIT ══════════════════════════════════════════════════════════ */

  document.addEventListener('DOMContentLoaded', async function () {
    Modal.wire();
    buildHeader();
    renderLinesHead();

    // Line dropdowns need their masters loaded before any row renders.
    await Promise.all([
      loadHeaderOptions(),
      ...cfg.lineColumns.filter((c) => c.type === 'fk').map(async (col) => {
        state.options[col.source] =
          await U.loadOptions(cfg.lookupBase, col.source, col.source_label);
      }),
    ]);

    if (state.id) {
      try {
        applyDocument(await U.api(cfg.apiBase + state.id + '/'));
      } catch (err) {
        showError(U.errorMessage(err, 'Could not load this document.'));
      }
    } else {
      updateButtons();
      state.lines = [blankLine()];
      renderLines();
    }

    U.$('gsAddLineBtn').addEventListener('click', () => {
      state.lines.push(blankLine());
      state.dirty = true;
      renderLines();
    });

    U.$('gsSaveDraftBtn').addEventListener('click', () => saveDraft(false));
    U.$('gsPostBtn').addEventListener('click', openPostConfirm);
    U.$('gsPostCancel').addEventListener('click', () => Modal.close('gsPostOverlay'));
    U.$('gsPostConfirm').addEventListener('click', doPost);

    // Header charges feed the totals panel.
    ['freight_amount', 'loading_amount', 'insurance_amount', 'other_charges']
      .forEach((name) => {
        const el = U.$('gsh_' + name);
        if (el) el.addEventListener('input', renderTotals);
      });

    // Changing warehouse invalidates every cached availability figure.
    const warehouse = U.$('gsh_warehouse');
    if (warehouse && cfg.showAvailability) {
      warehouse.addEventListener('change', () => {
        state.stockByItem = {};
        state.lines.forEach((l) => { if (l.item) fetchAvailability(l.item); });
      });
    }

    window.addEventListener('beforeunload', (e) => {
      if (state.dirty && editable()) { e.preventDefault(); e.returnValue = ''; }
    });
  });
})();
