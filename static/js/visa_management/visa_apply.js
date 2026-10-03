'use strict';
/* visa_apply.js — application form logic */

document.addEventListener('DOMContentLoaded', function () {

  var countrySelect  = document.getElementById('countrySelect');
  var startDateInput = document.querySelector('[name="visit_start_date"]');
  var endDateInput   = document.querySelector('[name="visit_end_date"]');
  var expiryInput    = document.getElementById('passportExpiry');
  var expiryWarn     = document.getElementById('expiryWarn');

  // ── Country → default entry type ─────────────────────────────────────────
  if (countrySelect) {
    countrySelect.addEventListener('change', function () {
      var opt = this.options[this.selectedIndex];
      var entryType = opt.getAttribute('data-entry');
      if (!entryType || entryType === 'both') return;
      var radios = document.querySelectorAll('[name="entry_type"]');
      radios.forEach(function (r) {
        r.checked = (r.value === entryType);
      });
    });
  }

  // ── Passport expiry warning ───────────────────────────────────────────────
  function checkExpiry() {
    if (!expiryInput || !expiryWarn || !endDateInput) return;
    var expiry = expiryInput.value ? new Date(expiryInput.value) : null;
    var end    = endDateInput.value ? new Date(endDateInput.value) : null;
    if (!expiry || !end) { expiryWarn.style.display = 'none'; return; }
    var threshold = new Date(end);
    threshold.setMonth(threshold.getMonth() + 6);
    expiryWarn.style.display = expiry < threshold ? 'block' : 'none';
  }

  if (expiryInput)  expiryInput.addEventListener('change', checkExpiry);
  if (endDateInput) endDateInput.addEventListener('change', checkExpiry);
  checkExpiry(); // run once on load (edit mode pre-filled values)

  // ── Past dates disabled on forward-looking date fields ───────────────────
  // Travel dates and passport expiry must be today or later, so the native
  // picker greys out everything before today.
  //
  // Date of Birth, Date of Joining and passport Date of Issue are deliberately
  // NOT restricted: all three are inherently past dates (you joined the company
  // before today, and a passport you already hold was issued before today), so
  // a minimum of today would make those required fields impossible to fill.
  var now = new Date();
  var todayStr = now.getFullYear() + '-' +
                 String(now.getMonth() + 1).padStart(2, '0') + '-' +
                 String(now.getDate()).padStart(2, '0');

  ['visit_start_date', 'visit_end_date', 'passport_expiry_date'].forEach(function (name) {
    var el = document.querySelector('[name="' + name + '"]');
    if (el) el.min = todayStr;
  });

  // ── End date must be after start date ────────────────────────────────────
  if (startDateInput && endDateInput) {
    startDateInput.addEventListener('change', function () {
      if (endDateInput.value && endDateInput.value < this.value) {
        endDateInput.value = this.value;
      }
      // Tighten to the start date, but never relax back below today.
      endDateInput.min = this.value > todayStr ? this.value : todayStr;
    });
  }

  // ── Passport number → uppercase on blur ──────────────────────────────────
  var ppNum = document.querySelector('[name="passport_number"]');
  if (ppNum) {
    ppNum.addEventListener('blur', function () {
      this.value = this.value.toUpperCase();
    });
  }

  // ── Nationality → uppercase on blur ──────────────────────────────────────
  var natInput = document.querySelector('[name="nationality"]');
  if (natInput) {
    natInput.addEventListener('blur', function () {
      this.value = this.value.toUpperCase();
    });
  }

  var ppName = document.querySelector('[name="passport_name"]');
  if (ppName) {
    ppName.addEventListener('blur', function () {
      this.value = this.value.toLowerCase().replace(/\b[a-z]/g, function (ch) {
        return ch.toUpperCase();
      });
    });
  }

  // ── Self-HOD guard ────────────────────────────────────────────────────────
  var hodEmailInput = document.querySelector('[name="hod_email"]');

  // The real check happens server-side; this is just a UX hint
  if (hodEmailInput) {
    hodEmailInput.addEventListener('blur', function () {
      var selfEmail = document.querySelector('[name="applicant_email"]');
      if (!selfEmail) return;
      if (this.value && this.value.toLowerCase() === selfEmail.value.toLowerCase()) {
        this.setCustomValidity('You cannot be your own reporting head.');
        this.reportValidity();
      } else {
        this.setCustomValidity('');
      }
    });
  }

  // ── Submit button: confirm only for "submit" action ──────────────────────
  document.querySelectorAll('[name="action"][value="submit"]').forEach(function (btn) {
    btn.addEventListener('click', function (e) {
      if (!confirm('Submit this application for HOD approval?\nAn approval request will be sent to your reporting head.')) {
        e.preventDefault();
      }
    });
  });

});
