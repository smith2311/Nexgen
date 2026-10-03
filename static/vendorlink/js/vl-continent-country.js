/* Continent → country cascade.
 *
 * Picking a continent narrows the country select next to it. Done in the
 * browser rather than over a round-trip because the pair appears mid-form on
 * the vendor, registration and RFI forms — a reload there would throw away
 * everything else the person had typed.
 *
 * Markup contract:
 *
 *   <script type="application/json" id="vl-continent-map">{"AS":["India",…],…}</script>
 *   <select data-vl-continent="country">…</select>
 *   <select name="country" data-vl-country="1">…</select>
 *
 * The continent select's value names the country select it drives — matched
 * first by `name`, then by id, within the nearest form (or the document, for a
 * filter bar that is not in one). Several independent pairs on one page work.
 *
 * Filter bars that resubmit on change carry their own inline `onchange`
 * handler; this script only narrows, and never submits anything. On those
 * screens the server narrows the list too, so the rebuild is belt-and-braces
 * for the moment between the change and the reload.
 *
 * Two rules that are easy to get wrong and matter:
 *
 *  - The country select's ORIGINAL options are captured on init and are the
 *    only source of what may be offered. Filter bars list only the countries
 *    present in the data, so rebuilding from the full geonames map would offer
 *    countries that return nothing. The map is used to decide which of the
 *    existing options survive, never to add new ones.
 *
 *  - A country already selected on load is kept, and the continent is
 *    back-filled from it. Editing an existing vendor must not silently clear
 *    the country it already holds just because the continent box starts empty.
 */
(function () {
    "use strict";

    var MAP = null;

    function continentMap() {
        if (MAP !== null) { return MAP; }
        MAP = {};
        var node = document.getElementById("vl-continent-map");
        if (node) {
            try {
                MAP = JSON.parse(node.textContent || "{}");
            } catch (e) {
                // A malformed map must not take the form down: with an empty
                // map every option survives every filter, which degrades to
                // the single-level picker this replaced.
                MAP = {};
            }
        }
        return MAP;
    }

    /* continent code -> Set of its country names, for O(1) membership. */
    var SETS = {};
    function countriesIn(code) {
        if (!code) { return null; }
        if (!SETS[code]) {
            SETS[code] = new Set(continentMap()[code] || []);
        }
        return SETS[code];
    }

    /* Which continent a country name belongs to. Linear over ~7 continents. */
    function continentOf(name) {
        if (!name) { return ""; }
        var map = continentMap();
        for (var code in map) {
            if (Object.prototype.hasOwnProperty.call(map, code)) {
                if (countriesIn(code).has(name)) { return code; }
            }
        }
        return "";
    }

    function findCountrySelect(continentSelect) {
        var target = (continentSelect.getAttribute("data-vl-continent") || "").trim();
        var scope = continentSelect.form || document;
        if (target) {
            var byName = scope.querySelector('[name="' + target + '"]');
            if (byName) { return byName; }
            var byId = document.getElementById(target);
            if (byId) { return byId; }
        }
        return scope.querySelector("[data-vl-country]");
    }

    function apply(continentSelect, countrySelect, options, preserve) {
        var code = continentSelect.value;
        var allowed = countriesIn(code);
        var keep = preserve ? countrySelect.value : "";

        // Rebuilt rather than toggling `hidden` on options: hidden options stay
        // keyboard-selectable in some browsers, which would let someone pick a
        // country outside the continent they chose and then be rejected by the
        // form's own cross-check.
        countrySelect.innerHTML = "";
        var stillThere = false;
        options.forEach(function (opt) {
            // The blank prompt/"All countries" option has no value and always
            // survives — it is how the filter is cleared.
            if (opt.value && allowed && !allowed.has(opt.value)) { return; }
            countrySelect.appendChild(opt.cloneNode(true));
            if (opt.value && opt.value === keep) { stillThere = true; }
        });
        countrySelect.value = stillThere ? keep : "";

        // vl-geoselect.js wraps some of these selects with a searchable panel
        // built from the options as they were at construction. Without this it
        // would keep offering the countries we just removed, and the two would
        // disagree about what is selected.
        countrySelect.dispatchEvent(new CustomEvent("vl:options-changed", { bubbles: true }));
    }

    function init(continentSelect) {
        if (continentSelect.dataset.vlContinentReady === "1") { return; }
        var countrySelect = findCountrySelect(continentSelect);
        if (!countrySelect) { return; }
        continentSelect.dataset.vlContinentReady = "1";

        var options = Array.prototype.map.call(countrySelect.options, function (opt) {
            return opt.cloneNode(true);
        });

        // Back-fill the continent from a country already selected, so an edit
        // form opens with the pair consistent.
        if (!continentSelect.value && countrySelect.value) {
            var derived = continentOf(countrySelect.value);
            if (derived) { continentSelect.value = derived; }
        }
        if (continentSelect.value) {
            apply(continentSelect, countrySelect, options, true);
        }

        continentSelect.addEventListener("change", function () {
            // preserve=false: changing the continent deliberately abandons a
            // country from the old one. Keeping it would leave "Europe /
            // India" on screen, which the form then rejects.
            apply(continentSelect, countrySelect, options, false);
        });
    }

    function scan(root) {
        var nodes = (root || document).querySelectorAll("[data-vl-continent]");
        Array.prototype.forEach.call(nodes, init);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", function () { scan(document); });
    } else {
        scan(document);
    }

    // Exposed so a drawer or modal loaded over AJAX can wire its own pair.
    window.vlContinentCountry = { scan: scan };
})();
