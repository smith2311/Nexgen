/* ═══════════════════════════════════════════════════════
   modal.js — Shared Modal Component helper
   Logistics Management Design System

   Usage:
     Modal.open('editModalOverlay');   // show
     Modal.close('editModalOverlay');   // hide
     Modal.closeAll();                  // hide every open modal
     Modal.wire();                      // once on DOMContentLoaded:
                                        //   click-on-overlay + Escape close modals
   Works with the markup:
     <div class="lmodal-overlay" id="..."> <div class="modal"> … </div> </div>
═══════════════════════════════════════════════════════ */

window.Modal = (function () {
  'use strict';

  function el(x) { return typeof x === 'string' ? document.getElementById(x) : x; }

  function open(x) {
    const e = el(x);
    if (e) { e.classList.add('open'); document.body.style.overflow = 'hidden'; }
  }

  function close(x) {
    const e = el(x);
    if (e) { e.classList.remove('open'); document.body.style.overflow = ''; }
  }

  function closeAll() {
    document.querySelectorAll('.lmodal-overlay.open').forEach(function (e) {
      e.classList.remove('open');
    });
    document.body.style.overflow = '';
  }

  function wire() {
    document.addEventListener('click', function (e) {
      if (e.target && e.target.classList && e.target.classList.contains('lmodal-overlay')) {
        close(e.target);
      }
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') closeAll();
    });
  }

  return { open: open, close: close, closeAll: closeAll, wire: wire };
})();
