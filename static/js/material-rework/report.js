/* Every chart and bar below is now optional.
 *
 * The dashboard has three scopes — the overview, all users, and one user — and
 * each renders a different set of canvases. Reaching for an element that is
 * not in the current scope throws, and one uncaught TypeError at the top of
 * this file takes the tabs, the slide-over panels and the delete dialog with
 * it: the page looks fine and nothing works. So each block checks first. */
function mrEl(id) { return document.getElementById(id); }

/* ── Qty bars ── */
(function(){
  const acc = mrEl('qAccBar'), rec = mrEl('qRecBar');
  if (!acc || !rec || typeof QTY === 'undefined') { return; }
  const base = QTY.total || 1;
  acc.style.width = Math.round(QTY.accepted/base*100)+'%';
  rec.style.width = Math.round(QTY.received/base*100)+'%';
})();

/* ── Chart defaults ── */
Chart.defaults.font.family = "'Helvetica Neue',Helvetica,Arial,sans-serif";
Chart.defaults.plugins.legend.position = 'bottom';
Chart.defaults.plugins.legend.labels.boxWidth = 10;
Chart.defaults.plugins.legend.labels.font = {size:10};
const donutOpts = {cutout:'60%', plugins:{tooltip:{callbacks:{label:c=>` ${c.label}: ${c.parsed}`}}}};

if (mrEl('batchDonut')) new Chart(mrEl('batchDonut'), {
  type:'doughnut',
  data:{
    labels:['Pending HOD','HOD Approved','HOD Rejected','Prod. Done','Completed'],
    datasets:[{data:[BC.pending_hod,BC.hod_approved,BC.hod_rejected,BC.production_done,BC.completed],
      backgroundColor:['#fcd34d','#93c5fd','#fca5a5','#c4b5fd','#6ee7b7'],
      borderColor:['#d97706','#1d4ed8','#dc2626','#7c3aed','#059669'],borderWidth:1.5}]
  }, options:donutOpts
});

if (mrEl('itemDonut')) new Chart(mrEl('itemDonut'), {
  type:'doughnut',
  data:{
    labels:['Pend. Prod.','Pend. WH','Accepted','Rejected','Received','HOD Rej.'],
    datasets:[{data:[IC.pending_production,IC.pending_warehouse,IC.accepted,IC.rejected,IC.received,IC.hod_rejected],
      backgroundColor:['#93c5fd','#c4b5fd','#6ee7b7','#fca5a5','#a7f3d0','#fed7aa'],
      borderColor:['#1d4ed8','#7c3aed','#059669','#dc2626','#065f46','#c2410c'],borderWidth:1.5}]
  }, options:donutOpts
});

if (mrEl('monthlyBar')) new Chart(mrEl('monthlyBar'), {
  type:'bar',
  data:{
    labels: MONTHLY.labels,
    datasets:[{label:'Batches Submitted', data:MONTHLY.data,
      backgroundColor:'rgba(0,118,200,.7)', borderColor:'#0076c8', borderWidth:1.5, borderRadius:5}]
  },
  options:{plugins:{legend:{display:false}}, scales:{y:{beginAtZero:true, ticks:{stepSize:1}}}}
});

/* ── Per-user charts ──────────────────────────────────────────────────────
 * Only one scope's canvases exist at a time, so each is guarded the same way
 * as the three above. USERS is supplied by the template and is absent in the
 * overview scope. */
const mrBarOpts = {
  plugins:{legend:{display:false}},
  scales:{y:{beginAtZero:true, ticks:{precision:0}}},
  maintainAspectRatio:false,
};

if (typeof USERS !== 'undefined' && USERS) {
  if (mrEl('usersBar')) {
    new Chart(mrEl('usersBar'), {
      type:'bar',
      data:{
        labels: USERS.people.labels,
        datasets:[{label:'Actions', data:USERS.people.data,
          backgroundColor:'rgba(3,105,161,.72)', borderColor:'#0369a1',
          borderWidth:1.5, borderRadius:5}]
      },
      /* Horizontal bars: names are long and a vertical axis of rotated
         labels is unreadable past about six people. */
      options: Object.assign({indexAxis:'y'}, mrBarOpts, {
        scales:{x:{beginAtZero:true, ticks:{precision:0}}}
      })
    });
  }
  if (mrEl('stageBar')) {
    new Chart(mrEl('stageBar'), {
      type:'bar',
      data:{
        labels: USERS.stages.labels,
        datasets:[{label:'Actions', data:USERS.stages.data,
          backgroundColor:'rgba(124,58,237,.72)', borderColor:'#7c3aed',
          borderWidth:1.5, borderRadius:5}]
      },
      options: mrBarOpts
    });
  }
  if (mrEl('userStageBar')) {
    new Chart(mrEl('userStageBar'), {
      type:'bar',
      data:{
        labels: USERS.stages.labels,
        datasets:[{label:'Actions', data:USERS.stages.data,
          backgroundColor:'rgba(5,150,105,.72)', borderColor:'#059669',
          borderWidth:1.5, borderRadius:5}]
      },
      options: mrBarOpts
    });
  }
}

/* ── Tabs ── */
function switchTab(name, el){
  document.querySelectorAll('.rp-tab-pane').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.rp-tab').forEach(t=>t.classList.remove('active'));
  document.getElementById('tab-'+name).classList.add('active');
  el.classList.add('active');
}

/* ── Item row expand/collapse ── */
function toggleItems(batchPk){
  const rows = document.querySelectorAll(`.item-row[id^="irow-${batchPk}-"]`);
  const expBtn  = document.getElementById('expbtn-'+batchPk);
  const icon = expBtn.querySelector('i');
  const open = icon.classList.contains('fa-chevron-right');
  rows.forEach(r => r.classList.toggle('visible', open));
  icon.classList.toggle('fa-chevron-right', !open);
  icon.classList.toggle('fa-chevron-down', open);
}

/* ── Status colors ── */
const SC = {
  'pending_ppic':      ['#e0f2fe','#0369a1'],
  'ppic_approved':     ['#d1fae5','#065f46'],
  'ppic_rejected':     ['#fee2e2','#991b1b'],
  'pending_hod':       ['#fef3c7','#92400e'],
  'hod_approved':      ['#dbeafe','#1e40af'],
  'hod_rejected':      ['#fee2e2','#991b1b'],
  'production_done':   ['#ede9fe','#5b21b6'],
  'completed':         ['#d1fae5','#065f46'],
  'pending_production':['#dbeafe','#1e40af'],
  'accepted':          ['#d1fae5','#065f46'],
  'rejected':          ['#fee2e2','#991b1b'],
  'pending_warehouse': ['#ede9fe','#5b21b6'],
  'received':          ['#d1fae5','#065f46'],
};

function sBadge(s,l){const c=SC[s]||['#f3f4f6','#374151'];return `<span style="background:${c[0]};color:${c[1]};padding:2px 8px;border-radius:8px;font-size:.72rem;font-weight:700;">${esc(l)}</span>`;}
function tlDot(bg, color, icon){return `<div class="tl-dot" style="background:${bg};color:${color};"><i class="${icon}"></i></div>`;}
function esc(s){ return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

/* ── Batch panel ── */
function openBatchPanel(id,vert,dept,src,status,statusD,upBy,upEmail,upAt,hodAt,hodComm,subject,prodBy,prodEmail,prodAt){
  document.getElementById('panelTitle').innerHTML = `<i class="fas fa-layer-group" style="color:#3730a3;"></i> Batch — ${esc(String(id))}`;
  document.getElementById('panelBody').innerHTML = `
    <div class="ds">
      <div class="ds-title">Batch Info</div>
      <div class="dg">
        <div class="dc"><label>Reference</label><p style="color:#1d4ed8;font-family:monospace;">${esc(String(id))}</p></div>
        <div class="dc"><label>Status</label><p>${sBadge(status,statusD)}</p></div>
        <div class="dc"><label>Vertical</label><p>${esc(vert)}</p></div>
        <div class="dc"><label>Department</label><p>${esc(dept)}</p></div>
        <div class="dc"><label>Requested By</label><p>${src ? `<span style="background:#e0f2fe;color:#0369a1;padding:2px 9px;border-radius:10px;font-size:.73rem;font-weight:700;">${esc(src)}</span>` : '—'}</p></div>
        <div class="dc"><label>Submitted On</label><p>${esc(upAt)}</p></div>
        <div class="dc full"><label>Submitted By</label><p>${esc(upBy)} <small style="font-weight:400;color:#64748b;">${esc(upEmail)}</small></p></div>
        ${subject ? `<div class="dc full"><label>Email Subject</label><p style="color:#0076c8;font-size:.82rem;">${esc(subject)}</p></div>` : ''}
      </div>
    </div>
    ${prodBy ? `
    <div class="ds">
      <div class="ds-title">Production Review</div>
      <div class="dg">
        <div class="dc"><label>Reviewed By</label><p style="font-weight:700;color:#7c3aed;">${esc(prodBy)}</p><small style="color:#64748b;">${esc(prodEmail)}</small></div>
        <div class="dc"><label>Review Date</label><p>${esc(prodAt)}</p></div>
      </div>
    </div>` : ''}
    <div class="ds">
      <div class="ds-title">HOD Review</div>
      <div class="dg">
        <div class="dc full"><label>HOD Reviewed At</label><p>${esc(hodAt)}</p></div>
        ${hodComm ? `<div class="dc full"><label>HOD Comments</label><div class="rmk" style="background:#fffbeb;border-color:#fde68a;color:#92400e;">"${esc(hodComm)}"</div></div>` : ''}
      </div>
    </div>`;
  document.getElementById('slideOverlay').classList.add('open');
}

/* ── Item panel with lifecycle timeline ── */
function openItemPanel(num,mat,batchNo,qty,accQty,pendQty,recvQty,boxNo,remark,status,statusD,prodRem,prodAt,whAt,refNum,materialName,country,mfgDate,expDate,fromLoc,toLoc,uploadedAt){
  document.getElementById('panelTitle').innerHTML = `<i class="fas fa-box" style="color:#1d4ed8;"></i> Item #${num} — ${esc(mat)}`;

  let tl = '';

  tl += `<li class="tl-item">
    ${tlDot('#e0f2fe','#0076c8','fas fa-upload')}
    <div class="tl-body">
      <div class="tl-stage">Uploaded</div>
      <div class="tl-meta">Ref: ${esc(refNum)}</div>
      <div class="tl-note">Qty: <strong>${qty}</strong> | Box: ${esc(boxNo)||'—'} | BatchNo: ${esc(batchNo)||'—'}</div>
    </div>
  </li>`;

  const hodDone = (status !== 'pending_hod' && status !== 'hod_rejected');
  tl += `<li class="tl-item">
    ${hodDone ? tlDot('#d1fae5','#059669','fas fa-check') : (status==='hod_rejected' ? tlDot('#fee2e2','#dc2626','fas fa-times') : tlDot('#fef3c7','#d97706','fas fa-clock'))}
    <div class="tl-body">
      <div class="tl-stage">${status==='hod_rejected' ? 'HOD Rejected' : (hodDone ? 'HOD Approved' : 'Awaiting HOD')}</div>
    </div>
  </li>`;

  if(status !== 'hod_rejected' && status !== 'pending_hod'){
    const prodDone = (prodAt && prodAt !== '—');
    tl += `<li class="tl-item">
      ${prodDone ? tlDot('#ede9fe','#7c3aed','fas fa-industry') : tlDot('#fef3c7','#d97706','fas fa-clock')}
      <div class="tl-body">
        <div class="tl-stage">${prodDone ? 'Production Reviewed' : 'Pending Production'}</div>
        ${prodDone ? `<div class="tl-meta">${esc(prodAt)}</div>` : ''}
        ${prodDone ? `<div class="tl-note">Accepted: <strong style="color:#10b981;">${accQty}</strong>${prodRem ? ' | '+esc(prodRem) : ''}</div>` : ''}
      </div>
    </li>`;
  }

  if(status === 'received' || status === 'accepted' || status === 'pending_warehouse'){
    tl += `<li class="tl-item">
      ${status==='received' || status==='accepted' ? tlDot('#d1fae5','#059669','fas fa-box-open') : tlDot('#ede9fe','#7c3aed','fas fa-warehouse')}
      <div class="tl-body">
        <div class="tl-stage">${status==='received' ? 'Warehouse Received' : (status==='accepted' ? 'No WH Step Required' : 'Pending Warehouse')}</div>
        ${whAt && whAt!=='—' ? `<div class="tl-meta">${esc(whAt)}</div>` : ''}
        ${status==='received' ? `<div class="tl-note">Received Qty: <strong>${recvQty}</strong></div>` : ''}
      </div>
    </li>`;
  }

  if(status === 'rejected'){
    tl += `<li class="tl-item">
      ${tlDot('#fee2e2','#dc2626','fas fa-times')}
      <div class="tl-body">
        <div class="tl-stage">Rejected by Production</div>
        <div class="tl-meta">${esc(prodAt)}</div>
        ${prodRem ? `<div class="tl-note">"${esc(prodRem)}"</div>` : ''}
      </div>
    </li>`;
  }

  document.getElementById('panelBody').innerHTML = `
    <div class="ds">
      <div class="ds-title">Item Details</div>
      <div class="dg">
        <div class="dc"><label>Status</label><p>${sBadge(status,statusD)}</p></div>
        <div class="dc"><label>Item #</label><p>${num}</p></div>
        <div class="dc"><label>Serial No</label><p>${esc(materialName||'—')}</p></div>
        <div class="dc"><label>Country</label><p>${esc(country)||'—'}</p></div>
        <div class="dc full"><label>Code</label><p style="font-size:.95rem;">${esc(mat)}</p></div>
        <div class="dc"><label>Batch No</label><p>${esc(batchNo)||'—'}</p></div>
        <div class="dc"><label>Mfg Date</label><p>${esc(mfgDate)||'—'}</p></div>
        <div class="dc"><label>Exp Date</label><p>${esc(expDate)||'—'}</p></div>
        <div class="dc"><label>From Storage</label><p>${esc(fromLoc)||'—'}</p></div>
        <div class="dc"><label>To Storage</label><p>${esc(toLoc)||'—'}</p></div>
        <div class="dc"><label>Box No</label><p>${esc(boxNo)||'—'}</p></div>
        <div class="dc"><label>Original Qty</label><p style="font-size:1.1rem;font-weight:800;">${qty}</p></div>
        <div class="dc"><label>Prod. Accepted</label><p style="color:#10b981;font-weight:700;">${accQty} <span style="font-size:.75rem;color:#94a3b8;">of ${qty}</span></p></div>
        <div class="dc"><label>WH Received</label><p style="color:#059669;font-weight:700;">${recvQty} <span style="font-size:.75rem;color:#94a3b8;">of ${accQty}</span></p></div>
        <div class="dc"><label>WH Pending</label><p style="color:#7c3aed;font-weight:700;">${pendQty}</p></div>
        <div class="dc"><label>Uploaded At</label><p>${esc(uploadedAt)||'—'}</p></div>
        ${remark ? `<div class="dc full"><label>Remarks</label><div class="rmk">${esc(remark)}</div></div>` : ''}
      </div>
    </div>
    <div class="ds">
      <div class="ds-title">Lifecycle</div>
      <ul class="timeline">${tl}</ul>
    </div>`;
  document.getElementById('slideOverlay').classList.add('open');
}

function closePanel(){ document.getElementById('slideOverlay').classList.remove('open'); }

/* ── Restore active tab from URL ── */
(function(){
  const p = new URLSearchParams(window.location.search);
  if(p.get('tab') === 'report'){
    switchTab('report', document.querySelectorAll('.rp-tab')[1]);
  }
})();

function openDelModal(batchId, refNum){
  document.getElementById('del_ref_display').textContent = refNum;
  document.getElementById('delBatchForm').action = DEL_URL_BASE + batchId + '/';
  document.getElementById('del_next').value = window.location.pathname;
  document.getElementById('delOverlay').classList.add('open');
}
function closeDelModal(){ document.getElementById('delOverlay').classList.remove('open'); }
