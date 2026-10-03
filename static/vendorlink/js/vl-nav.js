/* ═══════════════════════════════════════════════════════════════════════
   vl-nav.js — make the header back link return to where you actually came from

   Every VendorLink page carries a `.lp-back` link pointing at its canonical
   parent (Raise a Request -> Requests, Rate Comparison -> Commercial Database,
   and so on). That is right when you walked down the hierarchy, but most of
   these pages have several entry points — you can reach "Raise a Request" from
   the VendorLink home, and "Request Detail" from the requests list, the
   Commercial Database or a rate comparison. In those cases the fixed parent is
   not where you were, so "back" threw you somewhere unexpected.

   THE RULE: back may only ever move UP. A referrer is used only when it sits
   strictly shallower in the hierarchy than the page you are on.

   That is what stops the link ping-ponging. Without it, walking
   Admin Console -> Users -> back lands you on Admin Console whose referrer is
   now Users, so its own back link would be rewritten to point back down at
   Users and the two pages bounce off each other forever. Because depth must
   strictly decrease, every click is guaranteed to make progress toward the
   module root, and same-depth siblings simply keep their canonical parent.

   Pure progressive enhancement: with JS off, the original link still works.
   ══════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  var MODULE = '/vendorlink/';

  /* [path pattern, label, depth]
     Depth is the page's real place in the module hierarchy, not its number of
     URL segments — Registrations lives at /vendorlink/registrations/ but sits
     a level below Vendor Master and the Admin Console, so it is depth 2. */
  var PAGES = [
    [/^\/vendorlink\/$/,                                       'VendorLink',         0],

    [/^\/vendorlink\/vendor-master\/$/,                        'Vendor Master',      1],
    [/^\/vendorlink\/requests\/$/,                             'Requests',           1],
    [/^\/vendorlink\/bids\/$/,                                 'Commercial Database', 1],
    [/^\/vendorlink\/admin\/$/,                                'Admin Console',      1],

    [/^\/vendorlink\/vendor-master\/vendors\/$/,               'Vendor Directory',   2],
    [/^\/vendorlink\/vendor-master\/capability-matrix\/$/,     'Capability Matrix',  2],
    [/^\/vendorlink\/registrations\/$/,                        'Registrations',      2],
    [/^\/vendorlink\/admin\/users\/$/,                         'Users',              2],
    [/^\/vendorlink\/admin\/masters\/$/,                       'Masters',            2],
    [/^\/vendorlink\/admin\/masters\/capabilities\/$/,         'Capability Master',  2],
    [/^\/vendorlink\/requests\/new\/$/,                        'Raise a Request',    2],
    [/^\/vendorlink\/requests\/\d+\/$/,                        'Request',            2],

    [/^\/vendorlink\/vendor-master\/vendors\/new\/$/,          'Add Vendor',         3],
    [/^\/vendorlink\/vendor-master\/vendors\/\d+\/$/,          'Vendor',             3],
    [/^\/vendorlink\/admin\/users\/new\/$/,                    'New Admin',          3],
    [/^\/vendorlink\/admin\/users\/\d+\/$/,                    'User',               3],
    [/^\/vendorlink\/requests\/\d+\/compare\/$/,               'Rate Comparison',    3],

    [/^\/vendorlink\/vendor-master\/vendors\/\d+\/edit\/$/,    'Edit Vendor',        4]
  ];

  function lookup(path) {
    for (var i = 0; i < PAGES.length; i++) {
      if (PAGES[i][0].test(path)) return { label: PAGES[i][1], depth: PAGES[i][2] };
    }
    return null;
  }

  /* The label sits as a bare text node next to the icon span, so swap that
     rather than rebuilding the element (keeps the icon and its transitions). */
  function setLabel(link, text) {
    var nodes = link.childNodes;
    for (var i = nodes.length - 1; i >= 0; i--) {
      if (nodes[i].nodeType === 3 && nodes[i].textContent.trim()) {
        nodes[i].textContent = ' ' + text;
        return;
      }
    }
    link.appendChild(document.createTextNode(' ' + text));
  }

  function apply(link) {
    var ref = document.referrer;
    if (!ref) return;                                        // typed / bookmarked

    var from;
    try { from = new URL(ref); } catch (e) { return; }

    if (from.origin !== window.location.origin) return;      // came from off-site
    if (from.pathname.indexOf(MODULE) !== 0) return;         // outside VendorLink
    if (from.pathname === window.location.pathname) return;  // re-render of this page

    var here = lookup(window.location.pathname);
    var there = lookup(from.pathname);
    if (!here || !there) return;                             // unmapped — leave alone

    // Back only ever goes up. Equal or deeper referrers keep the canonical
    // parent, which is what prevents two pages bouncing off each other.
    if (there.depth >= here.depth) return;

    // Already pointing where we came from — nothing to change.
    var current = link.getAttribute('href');
    if (current && current.split('?')[0] === from.pathname) return;

    link.setAttribute('href', from.pathname + from.search);

    // Only the header back link is renamed. Opted-in controls (a form's
    // Cancel button) keep their own wording — just their destination changes.
    if (link.classList.contains('lp-back')) {
      setLabel(link, there.label);
    }
  }

  function init() {
    var links = document.querySelectorAll('.lp-back, [data-vl-back]');
    Array.prototype.forEach.call(links, apply);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
