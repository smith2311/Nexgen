/* ============================================================================
 * Meril-V — Offline Scan Queue (Row 3.6)
 *
 * The honest gap this closes: every scanning console posts each scan to the
 * server as it happens, so a dropped connection loses whatever was mid-flight
 * — the legacy tool had exactly the same gap (its service worker was
 * network-only, no scan queue; see docs/MERILV_CLIENT_FEEDBACK_REGISTER.md).
 * This is new capability, not a regression fix.
 *
 * Design:
 *   - A queue lives in localStorage, one list per console (queueId keeps
 *     Outbound/Branch Transfer/Inbound/etc. from colliding).
 *   - A scan only queues on a genuine network failure — fetch() rejects with
 *     a TypeError for that (offline, DNS, connection refused); a real 4xx/5xx
 *     resolves normally and must reach the operator as a rejection, not queue
 *     silently.
 *   - Draining replays queued scans through the *exact same* single-scan
 *     endpoint used live, oldest first. If the server answers at all —
 *     accepted, or rejected as a duplicate, wrong batch, whatever — that
 *     scan is done and comes off the queue; only a renewed network failure
 *     keeps it queued.
 *   - Replays are idempotent. Every scan carries a client_ref generated
 *     *before* the first attempt, and the same ref is reused if that attempt
 *     has to be queued. The server records it on the scan row and short-
 *     circuits a second arrival of the same ref, so the case that used to be
 *     this module's known hole — a request accepted by the server whose
 *     response never made it back, replayed, and counted twice for a
 *     non-serialised item — now resolves to a single booking.
 * ========================================================================= */
(function () {
    'use strict';

    var STORAGE_PREFIX = 'merilv.offlineQueue.';

    function keyFor(queueId) { return STORAGE_PREFIX + queueId; }

    function readQueue(queueId) {
        try {
            return JSON.parse(window.localStorage.getItem(keyFor(queueId)) || '[]');
        } catch (err) {
            return [];
        }
    }

    function writeQueue(queueId, items) {
        try { window.localStorage.setItem(keyFor(queueId), JSON.stringify(items)); }
        catch (err) { /* storage full or disabled (private mode) — non-fatal */ }
    }

    function randomRef() {
        return Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
    }

    /** True only for a genuine network failure, never an HTTP error status —
     * fetch() resolves normally for a 4xx/5xx and only rejects (with a
     * TypeError, in every evergreen browser) when the request never reached
     * a server at all. */
    function isNetworkError(err) {
        return err instanceof TypeError;
    }

    function create(queueId, onSizeChange) {
        var notify = onSizeChange || function () {};
        var draining = false;

        function size() { return readQueue(queueId).length; }

        /** ``entry.clientRef`` is honoured when the caller already minted one
         * for its first (failed) attempt — reusing it is what makes the replay
         * idempotent server-side. Only falls back to a fresh ref if absent. */
        function enqueue(entry) {
            var items = readQueue(queueId);
            var stamped = {
                clientRef: entry.clientRef || randomRef(),
                queuedAt: Date.now(),
                url: entry.url,
                fields: entry.fields || {},
            };
            items.push(stamped);
            writeQueue(queueId, items);
            notify(items.length);
            return stamped.clientRef;
        }

        function remove(clientRef) {
            var items = readQueue(queueId).filter(function (e) { return e.clientRef !== clientRef; });
            writeQueue(queueId, items);
            notify(items.length);
        }

        /** Replays every queued entry, oldest first, via `sendFn(entry)` —
         * a caller-supplied function that POSTs entry.fields to entry.url and
         * returns the parsed response. Stops (without throwing) on the first
         * renewed network failure, leaving the rest queued for the next
         * trigger; a defined server answer of any kind is treated as
         * delivered and removed regardless of accepted/rejected. */
        function drain(sendFn) {
            if (draining) { return Promise.resolve({ drained: 0, stillQueued: size() }); }
            draining = true;
            var drained = 0;

            function step() {
                var items = readQueue(queueId);
                if (!items.length) { return Promise.resolve(); }
                var entry = items[0];
                return sendFn(entry).then(function (result) {
                    remove(entry.clientRef);
                    drained++;
                    return step();
                }).catch(function (err) {
                    if (isNetworkError(err)) { return Promise.reject(err); }
                    // A parse error or similar on an otherwise-answered
                    // request — do not spin on it forever.
                    remove(entry.clientRef);
                    drained++;
                    return step();
                });
            }

            return step().catch(function () { /* still offline — swallow, caller sees the count */ })
                .then(function () {
                    draining = false;
                    return { drained: drained, stillQueued: size() };
                });
        }

        return { enqueue: enqueue, remove: remove, size: size, drain: drain, all: function () { return readQueue(queueId); } };
    }

    window.mvOfflineQueue = { create: create, isNetworkError: isNetworkError, newRef: randomRef };
})();
