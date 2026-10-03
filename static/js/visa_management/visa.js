'use strict';
/* visa.js — shared JS for all visa_management pages */

document.addEventListener('DOMContentLoaded', function () {

  // Wire up shared Modal component (lmodal-overlay click-outside + Escape)
  if (window.Modal) Modal.wire();

  // Auto-dismiss alert-banner messages after 5 s
  var alerts = document.querySelectorAll('.alert-banner');
  if (alerts.length) {
    setTimeout(function () {
      alerts.forEach(function (a) {
        a.style.transition = 'opacity 0.4s';
        a.style.opacity = '0';
        setTimeout(function () { a.style.display = 'none'; }, 420);
      });
    }, 5000);
  }

});
