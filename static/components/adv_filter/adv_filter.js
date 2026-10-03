/* ═══════════════════════════════════════════════════════
   adv_filter.js — Advanced Filter Panel (shared)
   Include AFTER logi_datatable.js on any list page.

   Usage:
     const af = new AdvFilter({
       fields:       [...],       // field definitions
       searchWrapId: 'mySearchWrap', // id of .search-wrap to inject button after
       chipsId:      'myChips',  // id of chips container div
       onApply:      () => { state.page=1; applyFilters(); }
     });

     // In applyFilters():
     let filtered = state.allRecords;
     filtered = af.filterData(filtered);  // <-- add this line
     ...rest of filters...
═══════════════════════════════════════════════════════ */

(function (global) {
  'use strict';

  /* ── Operator sets per field type ─────────────────── */
  var OPS = {
    text: [
      { v: 'contains',     l: 'Contains' },
      { v: 'not_contains', l: 'Does Not Contain' },
      { v: 'equals',       l: 'Equals' },
      { v: 'not_equals',   l: 'Not Equals' },
      { v: 'starts_with',  l: 'Starts With' },
      { v: 'ends_with',    l: 'Ends With' },
      { v: 'is_empty',     l: 'Is Empty' },
      { v: 'is_not_empty', l: 'Is Not Empty' },
    ],
    number: [
      { v: 'eq',  l: '= Equals' },
      { v: 'neq', l: '≠ Not Equals' },
      { v: 'gt',  l: '> Greater Than' },
      { v: 'lt',  l: '< Less Than' },
      { v: 'gte', l: '≥ Greater or Equal' },
      { v: 'lte', l: '≤ Less or Equal' },
    ],
    date: [
      { v: 'is',         l: 'Is' },
      { v: 'is_not',     l: 'Is Not' },
      { v: 'is_before',  l: 'Is Before' },
      { v: 'is_after',   l: 'Is After' },
      { v: 'is_between', l: 'Is Between' },
    ],
    select: [
      { v: 'is',     l: 'Is' },
      { v: 'is_not', l: 'Is Not' },
    ],
  };

  /* ── Test a single condition on a raw value ── */
  function test(raw, op, fv, fv2) {
    var val = String(raw == null ? '' : raw).toLowerCase().trim();
    var v1  = String(fv  == null ? '' : fv ).toLowerCase().trim();
    var v2  = String(fv2 == null ? '' : fv2).toLowerCase().trim();
    switch (op) {
      case 'contains':     return val.indexOf(v1) !== -1;
      case 'not_contains': return val.indexOf(v1) === -1;
      case 'equals':       return val === v1;
      case 'not_equals':   return val !== v1;
      case 'starts_with':  return val.indexOf(v1) === 0;
      case 'ends_with':    return val.slice(-v1.length) === v1;
      case 'is_empty':     return val === '' || raw == null;
      case 'is_not_empty': return val !== '' && raw != null;
      case 'eq':           return parseFloat(raw) === parseFloat(fv);
      case 'neq':          return parseFloat(raw) !== parseFloat(fv);
      case 'gt':           return parseFloat(raw)  >  parseFloat(fv);
      case 'lt':           return parseFloat(raw)  <  parseFloat(fv);
      case 'gte':          return parseFloat(raw)  >= parseFloat(fv);
      case 'lte':          return parseFloat(raw)  <= parseFloat(fv);
      case 'is':           return val === v1;
      case 'is_not':       return val !== v1;
      case 'is_before':    return raw && fv  && new Date(raw) < new Date(fv);
      case 'is_after':     return raw && fv  && new Date(raw) > new Date(fv);
      case 'is_between':   return raw && fv && fv2 &&
                                  new Date(raw) >= new Date(fv) &&
                                  new Date(raw) <= new Date(fv2);
      default: return true;
    }
  }

  /* ═══════════════════════════════════════════════════
     AdvFilter constructor
  ═══════════════════════════════════════════════════ */
  function AdvFilter(cfg) {
    this.fields         = cfg.fields         || [];
    this.onApply        = cfg.onApply        || function(){};
    this.searchWrapId   = cfg.searchWrapId   || '';  // inject btn AFTER this element
    this.btnContainerId = cfg.btnContainerId || '';  // inject btn INSIDE this container
    this.chipsId        = cfg.chipsId        || '';
    this._rows          = [];
    this._uid           = 'af' + Date.now().toString(36);
    this._init();
  }

  AdvFilter.prototype._init = function () {
    this._buildOverlay();
    this._buildPanel();
    this._injectBtn();
    this._chipsEl = document.getElementById(this.chipsId) || null;
    // expose on window for inline handlers
    window['_af_' + this._uid] = this;
  };

  /* ── Overlay ── */
  AdvFilter.prototype._buildOverlay = function () {
    var ov = document.createElement('div');
    ov.className = 'af-overlay';
    var self = this;
    ov.addEventListener('click', function () { self.close(); });
    document.body.appendChild(ov);
    this._overlay = ov;
  };

  /* ── Panel ── */
  AdvFilter.prototype._buildPanel = function () {
    var uid   = this._uid;
    var panel = document.createElement('div');
    panel.className = 'af-panel';
    panel.innerHTML =
      '<div class="af-panel-header">' +
        '<h3><i class="fas fa-sliders-h"></i> Advanced Filters</h3>' +
        '<button class="af-panel-close" onclick="window[\'_af_' + uid + '\'].close()">' +
          '<i class="fas fa-times"></i>' +
        '</button>' +
      '</div>' +
      '<div class="af-panel-body" id="' + uid + '_body"></div>' +
      '<div class="af-panel-footer">' +
        '<button class="af-clear-btn" onclick="window[\'_af_' + uid + '\'].clear()">' +
          '<i class="fas fa-times"></i> Clear All' +
        '</button>' +
        '<button class="af-apply-btn" onclick="window[\'_af_' + uid + '\'].apply()">' +
          '<i class="fas fa-check"></i> Apply Filters' +
        '</button>' +
      '</div>';
    document.body.appendChild(panel);
    this._panel = panel;
    this._body  = document.getElementById(uid + '_body');
  };

  /* ── Trigger button — injected next to searchWrapId OR inside btnContainerId ── */
  AdvFilter.prototype._injectBtn = function () {
    var btn = document.createElement('button');
    btn.type      = 'button';
    btn.className = 'af-btn';
    btn.innerHTML =
      '<i class="fas fa-sliders-h"></i> Filters ' +
      '<span class="af-btn-badge" style="display:none">0</span>';
    var self = this;
    btn.addEventListener('click', function () { self.open(); });

    if (this.btnContainerId) {
      // Place button INSIDE a dedicated container div
      var container = document.getElementById(this.btnContainerId);
      if (container) container.appendChild(btn);
      else return;
    } else if (this.searchWrapId) {
      // Place button AFTER the search-wrap element
      var wrap = document.getElementById(this.searchWrapId);
      if (wrap) wrap.parentNode.insertBefore(btn, wrap.nextSibling);
      else return;
    } else {
      return;
    }
    this._btn = btn;
  };

  /* ── Open / Close ── */
  AdvFilter.prototype.open = function () {
    this._renderBody();
    this._panel.classList.add('open');
    this._overlay.classList.add('open');
    document.body.style.overflow = 'hidden';
  };

  AdvFilter.prototype.close = function () {
    this._panel.classList.remove('open');
    this._overlay.classList.remove('open');
    document.body.style.overflow = '';
  };

  /* ── Add / Remove rows ── */
  AdvFilter.prototype._addRow = function () {
    var f0   = this.fields[0] || {};
    var type = f0.type || 'text';
    var ops  = OPS[type] || OPS.text;
    this._rows.push({ field: f0.key || '', op: ops[0].v, val: '', val2: '' });
    this._renderBody();
  };

  AdvFilter.prototype._removeRow = function (idx) {
    this._rows.splice(idx, 1);
    this._renderBody();
  };

  /* ── Field/op/val change handlers ── */
  AdvFilter.prototype._changeField = function (idx, key) {
    var field = this._fieldByKey(key) || {};
    var type  = field.type || 'text';
    this._rows[idx] = { field: key, op: (OPS[type] || OPS.text)[0].v, val: '', val2: '' };
    this._renderBody();
  };

  AdvFilter.prototype._changeOp = function (idx, op) {
    this._rows[idx].op   = op;
    this._rows[idx].val  = '';
    this._rows[idx].val2 = '';
    this._renderBody();
  };

  AdvFilter.prototype._changeVal = function (idx, val, isSecond) {
    if (isSecond) this._rows[idx].val2 = val;
    else          this._rows[idx].val  = val;
  };

  AdvFilter.prototype._fieldByKey = function (key) {
    for (var i = 0; i < this.fields.length; i++) {
      if (this.fields[i].key === key) return this.fields[i];
    }
    return null;
  };

  /* ── Render panel body ── */
  AdvFilter.prototype._renderBody = function () {
    var body = this._body;
    if (!body) return;
    body.innerHTML = '';

    var self = this;
    var uid  = this._uid;

    if (!this._rows.length) {
      body.innerHTML =
        '<div class="af-no-rows">' +
          '<i class="fas fa-filter"></i>' +
          'No filters yet.<br>Click <strong>"Add Filter"</strong> below to start.' +
        '</div>';
    }

    this._rows.forEach(function (row, idx) {
      if (idx > 0) {
        var and = document.createElement('div');
        and.className = 'af-and-badge';
        and.textContent = 'AND';
        body.appendChild(and);
      }

      var field   = self._fieldByKey(row.field) || self.fields[0] || {};
      var type    = field.type || 'text';
      var ops     = OPS[type] || OPS.text;
      var noVal   = (row.op === 'is_empty' || row.op === 'is_not_empty');
      var isBetween = (row.op === 'is_between');

      /* Field dropdown */
      var fieldOpts = self.fields.map(function (f) {
        return '<option value="' + esc(f.key) + '"' + (f.key === row.field ? ' selected' : '') + '>' + esc(f.label) + '</option>';
      }).join('');

      /* Operator dropdown */
      var opOpts = ops.map(function (o) {
        return '<option value="' + esc(o.v) + '"' + (o.v === row.op ? ' selected' : '') + '>' + esc(o.l) + '</option>';
      }).join('');

      /* Value field */
      var valHtml = '';
      if (noVal) {
        valHtml = '<input type="text" disabled placeholder="—" style="opacity:0.35">';
      } else if (type === 'select') {
        var selOpts = (field.options || []).map(function (o) {
          return '<option value="' + esc(o.v) + '"' + (o.v === row.val ? ' selected' : '') + '>' + esc(o.l) + '</option>';
        }).join('');
        valHtml = '<select onchange="window[\'_af_' + uid + '\']._changeVal(' + idx + ',this.value,false)">' +
                    '<option value="">— Select —</option>' + selOpts +
                  '</select>';
      } else if (type === 'date') {
        valHtml = '<input type="date" value="' + esc(row.val) + '" ' +
                    'onchange="window[\'_af_' + uid + '\']._changeVal(' + idx + ',this.value,false)">';
        if (isBetween) {
          valHtml += '<div class="af-val2-wrap">' +
                       '<div class="af-val2-lbl">TO</div>' +
                       '<input type="date" value="' + esc(row.val2) + '" ' +
                         'onchange="window[\'_af_' + uid + '\']._changeVal(' + idx + ',this.value,true)">' +
                     '</div>';
        }
      } else if (type === 'number') {
        valHtml = '<input type="number" value="' + esc(row.val) + '" placeholder="Enter value…" ' +
                    'onchange="window[\'_af_' + uid + '\']._changeVal(' + idx + ',this.value,false)">';
      } else {
        valHtml = '<input type="text" value="' + esc(row.val) + '" placeholder="Type value…" ' +
                    'oninput="window[\'_af_' + uid + '\']._changeVal(' + idx + ',this.value,false)">';
      }

      var rowEl = document.createElement('div');
      rowEl.className = 'af-row';
      rowEl.innerHTML =
        '<div class="af-row-grid">' +
          '<div class="af-row-field">' +
            '<div class="af-row-label">Field</div>' +
            '<select onchange="window[\'_af_' + uid + '\']._changeField(' + idx + ',this.value)">' +
              fieldOpts +
            '</select>' +
          '</div>' +
          '<div class="af-row-field">' +
            '<div class="af-row-label">Condition</div>' +
            '<select onchange="window[\'_af_' + uid + '\']._changeOp(' + idx + ',this.value)">' +
              opOpts +
            '</select>' +
          '</div>' +
          '<div class="af-row-field">' +
            '<div class="af-row-label">Value</div>' +
            valHtml +
          '</div>' +
          '<button class="af-row-remove" title="Remove filter" ' +
            'onclick="window[\'_af_' + uid + '\']._removeRow(' + idx + ')">' +
            '<i class="fas fa-times"></i>' +
          '</button>' +
        '</div>';
      body.appendChild(rowEl);
    });

    /* Add Filter button */
    var addBtn = document.createElement('button');
    addBtn.type      = 'button';
    addBtn.className = 'af-add-row';
    addBtn.innerHTML = '<i class="fas fa-plus"></i> Add Filter';
    addBtn.addEventListener('click', function () { self._addRow(); });
    body.appendChild(addBtn);
  };

  /* ── Apply ── */
  AdvFilter.prototype.apply = function () {
    this.close();
    this._renderChips();
    this._updateBtn();
    this.onApply();
  };

  AdvFilter.prototype.clear = function () {
    this._rows = [];
    this._renderBody();
    this._renderChips();
    this._updateBtn();
    this.onApply();
  };

  /* ── Filter data array (call from applyFilters()) ── */
  AdvFilter.prototype.filterData = function (data) {
    var active = this._activeRows();
    if (!active.length) return data;
    var self = this;
    return data.filter(function (item) {
      return active.every(function (row) {
        var rawVal = item[row.field];
        return test(rawVal, row.op, row.val, row.val2);
      });
    });
  };

  AdvFilter.prototype._activeRows = function () {
    return this._rows.filter(function (r) {
      return r.field && (r.val !== '' || r.op === 'is_empty' || r.op === 'is_not_empty');
    });
  };

  /* ── Chips ── */
  AdvFilter.prototype._renderChips = function () {
    var el = this._chipsEl;
    if (!el) return;
    var active = this._activeRows();
    if (!active.length) { el.innerHTML = ''; return; }

    var self = this;
    var uid  = this._uid;
    el.innerHTML = active.map(function (row) {
      var field   = self._fieldByKey(row.field) || {};
      var ops     = OPS[field.type || 'text'] || OPS.text;
      var opDef   = ops.filter(function (o) { return o.v === row.op; })[0] || { l: row.op };
      var valDisp = row.val || '';
      if (field.type === 'select' && field.options) {
        var opt = field.options.filter(function (o) { return o.v === row.val; })[0];
        if (opt) valDisp = opt.l;
      }
      var v2part  = (row.val2 && row.op === 'is_between') ? ' → ' + row.val2 : '';
      var valPart = (row.op !== 'is_empty' && row.op !== 'is_not_empty' && valDisp)
                    ? ' <em>' + esc(valDisp + v2part) + '</em>' : '';
      var realIdx = self._rows.indexOf(row);
      return '<div class="af-chip">' +
               '<strong>' + esc(field.label || row.field) + '</strong> ' +
               esc(opDef.l) + valPart +
               '<button class="af-chip-remove" title="Remove" ' +
                 'onclick="window[\'_af_' + uid + '\']._removeChip(' + realIdx + ')">' +
                 '<i class="fas fa-times"></i>' +
               '</button>' +
             '</div>';
    }).join('');
  };

  AdvFilter.prototype._removeChip = function (idx) {
    this._rows.splice(idx, 1);
    this._renderChips();
    this._updateBtn();
    this.onApply();
  };

  AdvFilter.prototype._updateBtn = function () {
    var btn = this._btn;
    if (!btn) return;
    var count = this._activeRows().length;
    var badge = btn.querySelector('.af-btn-badge');
    if (badge) {
      badge.style.display = count ? 'inline-flex' : 'none';
      badge.textContent   = count;
    }
    if (count) btn.classList.add('has-filters');
    else       btn.classList.remove('has-filters');
  };

  /* ── tiny html escaper ── */
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g,'&amp;').replace(/</g,'&lt;')
      .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  global.AdvFilter = AdvFilter;

}(window));
