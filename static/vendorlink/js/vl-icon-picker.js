/* Capability icon picker.
 *
 * Drives the palette in templates/vendorlink/admin/_capability_icon_picker.html:
 * the form's `icon` field is a hidden input, this sets it, and the trigger shows
 * what is currently chosen.
 *
 * Deliberately small and dependency-free — it runs on two admin screens and the
 * whole palette is already in the DOM, so there is nothing to fetch and nothing
 * to build.
 *
 * Each picker wires itself from its own root element, so more than one on a page
 * works without them fighting over a shared id.
 */
(function () {
    "use strict";

    function init(root) {
        if (root.dataset.vlIconpickReady === "1") { return; }
        root.dataset.vlIconpickReady = "1";

        var hidden  = root.querySelector('input[type="hidden"]');
        var trigger = root.querySelector("[data-vl-iconpick-trigger]");
        var drawer  = root.querySelector("[data-vl-iconpick-drawer]");
        var preview = root.querySelector("[data-vl-iconpick-preview] i");
        var nameEl  = root.querySelector("[data-vl-iconpick-name]");
        var search  = root.querySelector("[data-vl-iconpick-search]");
        var closeEl = root.querySelector("[data-vl-iconpick-close]");
        var empty   = root.querySelector("[data-vl-iconpick-empty]");
        var options = Array.prototype.slice.call(root.querySelectorAll("[data-vl-iconpick-opt]"));
        var groups  = Array.prototype.slice.call(root.querySelectorAll("[data-vl-iconpick-group]"));

        if (!hidden || !trigger || !drawer) { return; }

        function markSelected(value) {
            options.forEach(function (opt) {
                var on = opt.dataset.vlIconpickOpt === value;
                opt.classList.toggle("is-selected", on);
                opt.setAttribute("aria-pressed", on ? "true" : "false");
            });
        }

        function reflect(value, label) {
            hidden.value = value;
            if (preview) {
                // Only the fa-* class is swapped; "fas" stays so the glyph keeps
                // its weight.
                preview.className = "fas " + value;
            }
            if (nameEl) { nameEl.textContent = label || value.replace(/^fa-/, "").replace(/-/g, " "); }
            markSelected(value);
        }

        function open() {
            drawer.hidden = false;
            trigger.setAttribute("aria-expanded", "true");
            if (search) { search.value = ""; filter(""); search.focus(); }
            var selected = root.querySelector(".vl-iconpick__opt.is-selected");
            if (selected) { selected.scrollIntoView({ block: "nearest" }); }
        }

        function close() {
            drawer.hidden = true;
            trigger.setAttribute("aria-expanded", "false");
        }

        function filter(term) {
            var needle = (term || "").trim().toLowerCase();
            var anyVisible = false;
            options.forEach(function (opt) {
                var hit = !needle || (opt.dataset.vlIconpickTerms || "").indexOf(needle) !== -1;
                opt.hidden = !hit;
                if (hit) { anyVisible = true; }
            });
            // Hide a group heading whose icons all filtered out, or the heading
            // sits above nothing.
            groups.forEach(function (group) {
                var visible = group.querySelectorAll("[data-vl-iconpick-opt]:not([hidden])").length;
                group.hidden = visible === 0;
            });
            if (empty) { empty.hidden = anyVisible; }
        }

        trigger.addEventListener("click", function () {
            if (drawer.hidden) { open(); } else { close(); }
        });
        if (closeEl) { closeEl.addEventListener("click", close); }
        if (search) { search.addEventListener("input", function () { filter(this.value); }); }

        options.forEach(function (opt) {
            opt.addEventListener("click", function () {
                var label = opt.querySelector("span");
                reflect(opt.dataset.vlIconpickOpt, label ? label.textContent.trim() : "");
                close();
                trigger.focus();
            });
        });

        document.addEventListener("click", function (event) {
            if (!drawer.hidden && !root.contains(event.target)) { close(); }
        });
        document.addEventListener("keydown", function (event) {
            if (event.key === "Escape" && !drawer.hidden) { close(); trigger.focus(); }
        });

        // Show whatever the form already holds — an edit form opens on the
        // capability's real icon, not on the placeholder.
        var current = hidden.value || "fa-circle-check";
        var match = options.filter(function (o) { return o.dataset.vlIconpickOpt === current; })[0];
        var matchLabel = match && match.querySelector("span");
        reflect(current, matchLabel ? matchLabel.textContent.trim() : "");
    }

    function scan(scope) {
        var nodes = (scope || document).querySelectorAll("[data-vl-iconpick]");
        Array.prototype.forEach.call(nodes, init);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", function () { scan(document); });
    } else {
        scan(document);
    }

    window.vlIconPicker = { scan: scan };
})();
