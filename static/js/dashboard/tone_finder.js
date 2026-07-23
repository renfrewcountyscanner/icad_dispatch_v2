/*--------------------------------------------------------------------
  tone_finder.js
  Front-end logic for the Tone-Finder “Hits” page.

  ✦ Scope
    - Systems/Calls table management (DataTables)
    - Call-details modal rendering
    - Trigger resolution helpers (fuzzy/exact)
    - Bulk/single delete flows
    - Footer “Now Playing” custom audio player wiring
    - Auto-refresh and UI state handling

  ✦ Important
    - **No logic changes** in this refactor — formatting, ordering,
      structure, and documentation only.
    - All functions remain function-declarations (hoisted) to preserve
      behavior despite re-ordering.
--------------------------------------------------------------------*/


/* ====================================================================
   0) GLOBAL STATE & CONSTANTS
   ==================================================================== */

let table;                                  // DataTable instance
const els = {};                             // Centralized DOM refs (populated in init)
let availableRadioSystems = [];
let callIdToDelete = null;                  // Pending delete id
let refreshTimer = null;                    // Auto-refresh handle
let currentPlayingId = null;                // Call-id currently playing

const callMeta = new Map();                 // call_id -> { src, label }
const selectedIds = new Set();              // Selected call ids (string)

const COL = {                               // DataTable column indices
    SEL: 0, TIME: 1, TYPE: 2, SYSTEM: 3,
    TONES_TRIG: 4, TONES_COUNT: 5, TG: 6, DUR: 7,
    ACTIONS: 8, ID: 9, EPOCH: 10
};

const toneCache = new Map();                // call_id -> [{s,e,type,fa,fb,triggerId,fired}, ...]
let modalCallId = null;                     // Call id currently shown in modal
let modalSegments = [];                     // [{ tr, s, e, type, fa, fb, isTone, triggerId, fired }]

let lastActiveRow = null;                   // Last highlighted <tr> inside modal
let activeTriggerRow = null;                // <tr> that spawned the “create trigger” modal
let pendingTriggerPayload = null;           // Built trigger body from a tone row
let reopenDetailsAfterTrigger = false;      // Reopen call modal after adding a trigger?

// Triggers index for current system (exact/wildcard/fuzzy lookup)
let systemTriggerIndex = null;              // { keys:Set, raw:[], byKey:Map, byId:Map, systemId }

// Tone → Trigger UI mapping in modal (for synchronized highlighting)
let modalTriggerLis = new Map();            // triggerId -> <li>
let lastActiveTriggerId = null;             // Currently highlighted trigger-id in modal
let modalTriggerNames = new Map();          // triggerId -> display name
let transcriptModel = null;
let lastActiveWordEl = null;
let lastActiveSegEl = null;

/* ====================================================================
   1) GENERIC UTILS
   ==================================================================== */

/**
 * Ensure URL is absolute (adds https://host/ if missing).
 * Handles:
 *  1) https://codeholio.icaddispatch.com/static/audio/...
 *  2) codeholio.icaddispatch.com/static/audio/...  (legacy, no scheme)
 *  3) static/audio/...                             (relative path)
 */
function absUrl(u) {
    if (!u) return "";

    u = u.trim();
    if (!u) return "";

    // 1) Already absolute
    if (/^https?:\/\//i.test(u)) return u;

    // 2) Protocol-relative: //host/path
    if (u.startsWith("//")) return `${location.protocol}${u}`;

    // 3) Legacy: host/path (no scheme)
    //    e.g. "codeholio.icaddispatch.com/static/audio/..."
    if (u.startsWith(location.host + "/")) {
        return `${location.protocol}//${u}`;
    }

    // 4) Pure relative: "static/..." or "/static/..."
    const path = u.replace(/^\/+/, "");
    return `${location.origin}/${path}`;
}

/**
 * Escape HTML entities for attribute/text contexts.
 * @param {string} s
 * @returns {string}
 */
function esc(s) {
    return String(s)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}

/** Read CSRF token from a hidden input (if present). */
function getCsrf() {
    return document.getElementById("csrfToken")?.value || "";
}

/**
 * Resolve the correct audio URL for a call/list row.
 * Tries several common field names and normalizes to absolute.
 * Works for both table rows (flat object) and call-detail payloads (data.call).
 */
function resolveCallAudioUrl(obj) {
    if (!obj) return "";

    // Sometimes the call is nested (detail payload), sometimes it's flat (list row)
    const call = obj.call || obj;

    const candidates = [
        call.audio_url,
    ];

    const raw = candidates.find(v => typeof v === "string" && v.trim() !== "");
    return absUrl(raw || "");
}

/** Fully stop/reset custom player highlights and state. */
function stopAndResetPlayer() {
    window.hideNowPlaying?.();
    currentPlayingId = null;
    highlightPlayingRow(); // clears any “playing” highlight
}

/** Normalize value to Number (or null if NaN/Infinite). */
function num(v) {
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
}

/** Clear bulk selection and reset header checkbox. */
function clearMainSelection() {
    selectedIds.clear();
    updateBulkUI();
    if (els.selAll) {
        els.selAll.checked = false;
        els.selAll.indeterminate = false;
    }
}

/** Update bulk UI count/button enabled state. */
function updateBulkUI() {
    const n = selectedIds.size;
    els.bulkCount.textContent = String(n);
    els.bulkBtn.disabled = n === 0;
}

/** Build checkbox HTML for a row. */
function checkboxCell(id) {
    const checked = selectedIds.has(String(id)) ? "checked" : "";
    return `<input type="checkbox" class="form-check-input js-rowchk" data-id="${id}" ${checked} aria-label="Select row">`;
}

function sanitizeDtmf(s) {
    const raw = (s ?? "").toString().trim().toUpperCase().replace(/\s+/g, "");
    const seq = raw.replace(/[^0-9ABCD*#]/g, "");
    return seq;
}

function updateDashboardBanner({ systemLabel = "All Systems", systemCount = null, hitCount = null } = {}) {
    const systemBadge = document.getElementById("dashboardSystemBadge");
    const hitBadge = document.getElementById("dashboardHitCountBadge");

    if (systemBadge) {
        systemBadge.textContent = systemCount == null
            ? `System ${systemLabel}`
            : `${systemCount} systems`;
    }
    if (hitBadge) {
        hitBadge.textContent = hitCount == null
            ? "Calls -"
            : `${hitCount} calls`;
    }
}

/* ====================================================================
   2) TRIGGER MATCHING HELPERS
   ==================================================================== */

/**
 * Get a trigger label by ID prioritizing:
 *  (1) Modal’s trigger names for this call
 *  (2) System trigger index
 *  (3) Fallback "Trigger <id>"
 */
function getTriggerLabelById(id) {
    if (id == null) return null;
    const key = String(id);

    if (modalTriggerNames?.has(key)) return modalTriggerNames.get(key);

    const tr = systemTriggerIndex?.byId?.get(Number(id));
    if (tr?.alert_trigger_name) return tr.alert_trigger_name;

    return `Trigger ${id}`;
}

/**
 * Build POST body for a new trigger based on the selected tone row.
 * - Seeds legacy flat columns for indexing/fuzzy match
 * - Adds child-set arrays matching the new schema
 * - Applies “minus-a-bit” defaults for required NOT NULLs
 */
function buildTriggerPayloadForRow(row) {
    const type = row.dataset.type;
    if (!type || type === "voice") return null;

    const fa = parseFloat(row.dataset.fa);
    const fb = parseFloat(row.dataset.fb);
    const alts = parseInt(row.dataset.alts);
    const cycles = parseInt(row.dataset.cycles);
    const len = parseFloat(row.dataset.len);
    const aLen = parseFloat(row.dataset.aLen);
    const bLen = parseFloat(row.dataset.bLen);
    const onMs = parseInt(row.dataset.onMs);
    const offMs = parseInt(row.dataset.offMs);

    const body = {};

    switch (type) {
        case "two_tone": {
            if (!Number.isFinite(fa) || !Number.isFinite(fb)) return null;

            // defaults (server-friendly)
            const minA = Number.isFinite(aLen) ? Math.max(0, aLen - 0.2) : 0.8;
            const minB = Number.isFinite(bLen) ? Math.max(0, bLen - 0.5) : 2.8;

            // legacy seeding
            body.alert_trigger_two_tone_a = fa;
            body.alert_trigger_two_tone_b = fb;
            body.alert_trigger_two_tone_a_length = minA;
            body.alert_trigger_two_tone_b_length = minB;

            // child-set
            body.two_tone_sets = [{
                freq_a_hz: fa,
                min_len_a_s: minA,
                freq_b_hz: fb,
                min_len_b_s: minB
            }];
            break;
        }

        case "hi_low": {
            if (!Number.isFinite(fa) || !Number.isFinite(fb)) return null;

            const minAlts = Number.isFinite(alts) ? Math.max(1, alts - 2) : 4;

            // legacy seeding
            body.alert_trigger_hi_low_tone_a = fa;
            body.alert_trigger_hi_low_tone_b = fb;
            body.alert_trigger_hi_low_alternations = minAlts;

            // child-set
            body.hi_low_sets = [{
                hi_freq_a_hz: fa,
                hi_freq_b_hz: fb,
                min_alternations: minAlts
            }];
            break;
        }

        case "pulsed": {
            if (!Number.isFinite(fa)) return null;

            const minCycles = Number.isFinite(cycles) ? Math.max(1, cycles - 2) : 6;

            // legacy seeding
            body.alert_trigger_pulsed_tone = fa;
            body.alert_trigger_pulsed_min_cycles = minCycles;

            // child-set
            const rule = {center_hz: fa, min_cycles: minCycles};
            if (Number.isFinite(onMs)) rule.min_on_ms = onMs;
            if (Number.isFinite(offMs)) rule.min_off_ms = offMs;
            body.pulsed_sets = [rule];
            break;
        }

        case "long":
        case "long_tone": {
            if (!Number.isFinite(fa)) return null;

            const minLen = Number.isFinite(len) ? Math.max(0, len - 0.5) : 3.8;

            // legacy seeding
            body.alert_trigger_long_tone = fa;
            body.alert_trigger_long_tone_length = minLen;

            // child-set
            body.long_tone_sets = [{freq_hz: fa, min_len_s: minLen}];
            break;
        }

        case "dtmf": {
            const seq = sanitizeDtmf(row.dataset.dtmf);
            if (!seq) return null;
            // child-set (no legacy fields for dtmf)
            body.dtmf_sequences = [{sequence: seq}];
            break;
        }

        default:
            return null; // (MDC, etc., not supported for triggers)
    }

    return {type, body};
}

/** Generate a default trigger name for the “create trigger” modal from a tone row. */
function defaultTriggerNameFromRow(row) {
    const type = row.dataset.type;
    const fa = Number(row.dataset.fa);
    const fb = Number(row.dataset.fb);
    const tag = (x) => Number.isFinite(x) ? x.toFixed(1) : "—";

    switch (type) {
        case "two_tone":
            return `Auto: 2T ${tag(fa)}→${tag(fb)}`;
        case "hi_low":
            return `Auto: Hi/Low ${tag(fa)}/${tag(fb)}`;
        case "pulsed":
            return `Auto: Pulsed ~${tag(fa)}`;
        case "long":
        case "long_tone":
            return `Auto: Long ${tag(fa)}`;
        default:
            return "Auto trigger";
    }
}

/** Trigger endpoints require radio_system_id, unlike the system-decimal filter. */
function getTriggerSystemId() {
    return els.sysSel?.selectedOptions?.[0]?.dataset.radioSystemId
        || els.dPlayPauseBtn?.dataset.radioSystemId
        || "";
}

function scoreMatchForSet(row, typeKey, setObj, tolPct) {
    const n = (x) => {
        const f = Number(x);
        return Number.isFinite(f) ? f : null;
    };
    const pctOk = (det, target) =>
        Number.isFinite(det) && Number.isFinite(target) && target !== 0 &&
        Math.abs(det - target) / Math.abs(target) * 100.0 <= tolPct;

    const fa = n(row.dataset.fa);
    const fb = n(row.dataset.fb);
    const aLen = n(row.dataset.aLen);
    const bLen = n(row.dataset.bLen);
    const alts = n(row.dataset.alts);
    const cyc = n(row.dataset.cycles);
    const len = n(row.dataset.len);

    switch (typeKey) {
        case "two_tone": {
            const ta = n(setObj.freq_a_hz), tb = n(setObj.freq_b_hz);
            const minA = n(setObj.min_len_a_s), minB = n(setObj.min_len_b_s);
            if (!pctOk(fa, ta) || !pctOk(fb, tb)) return null;
            if (Number.isFinite(minA) && !(Number.isFinite(aLen) && aLen >= minA)) return null;
            if (Number.isFinite(minB) && !(Number.isFinite(bLen) && bLen >= minB)) return null;
            const dA = Math.abs(fa - ta) || 0, dB = Math.abs(fb - tb) || 0;
            return dA + dB;
        }
        case "hi_low": {
            const ha = n(setObj.hi_freq_a_hz), hb = n(setObj.hi_freq_b_hz);
            const minAlts = n(setObj.min_alternations);
            if (!pctOk(fa, ha) || !pctOk(fb, hb)) return null;
            if (Number.isFinite(minAlts) && !(Number.isFinite(alts) && alts >= minAlts)) return null;
            const dA = Math.abs(fa - ha) || 0, dB = Math.abs(fb - hb) || 0;
            return dA + dB;
        }
        case "pulsed": {
            const c = n(setObj.center_hz);
            const minCycles = n(setObj.min_cycles);
            const minOn = n(setObj.min_on_ms);
            const minOff = n(setObj.min_off_ms);
            if (!pctOk(fa, c)) return null;
            if (Number.isFinite(minCycles) && !(Number.isFinite(cyc) && cyc >= minCycles)) return null;
            if (Number.isFinite(minOn) && !(Number.isFinite(n(row.dataset.onMs)) && n(row.dataset.onMs) >= minOn)) return null;
            if (Number.isFinite(minOff) && !(Number.isFinite(n(row.dataset.offMs)) && n(row.dataset.offMs) >= minOff)) return null;
            return Math.abs(fa - c) || 0;
        }
        case "long_tone": {
            const f = n(setObj.freq_hz);
            const need = n(setObj.min_len_s);
            if (!pctOk(fa, f)) return null;
            if (Number.isFinite(need) && !(Number.isFinite(len) && len >= need)) return null;
            return Math.abs(fa - f) || 0;
        }
        case "dtmf": {
            const seq = sanitizeDtmf(row.dataset.dtmf);
            const want = (setObj.sequence || "").toString().trim().toUpperCase();
            return (seq && want && seq === want) ? 0 : null;
        }
        default:
            return null;
    }
}

/** Enumerate all triggers that would match this row (respecting TG gate), with scores. */
function getAllMatchingTriggersForRow(row, {restrictToIds = null} = {}) {
    if (!systemTriggerIndex?.raw?.length) return [];

    const type = (row.dataset.type || "").toLowerCase();
    const typeKey = (type === "long") ? "long_tone" : type;
    const rowTG = Number.isFinite(Number(row.dataset.tg)) ? Number(row.dataset.tg) : null;

    const out = [];
    for (const tr of systemTriggerIndex.raw) {
        const id = Number(tr.alert_trigger_id);
        if (restrictToIds && restrictToIds.size && !restrictToIds.has(id)) continue;

        // TG gate: trigger TG must match row TG when set; empty TG == wildcard
        const trigTG = tr.alert_trigger_talkgroup ?? null;
        if (trigTG != null && trigTG !== "" && rowTG != null && Number(trigTG) !== rowTG) continue;

        const tolPct = Number(tr.alert_trigger_tone_tolerance) || 2.0;
        let scores = [];

        if (typeKey === "two_tone" && Array.isArray(tr.two_tone_sets)) {
            scores = tr.two_tone_sets.map(s => scoreMatchForSet(row, "two_tone", s, tolPct)).filter(v => v != null);
        } else if (typeKey === "hi_low" && Array.isArray(tr.hi_low_sets)) {
            scores = tr.hi_low_sets.map(s => scoreMatchForSet(row, "hi_low", s, tolPct)).filter(v => v != null);
        } else if (typeKey === "pulsed" && Array.isArray(tr.pulsed_sets)) {
            scores = tr.pulsed_sets.map(s => scoreMatchForSet(row, "pulsed", s, tolPct)).filter(v => v != null);
        } else if (typeKey === "long_tone" && Array.isArray(tr.long_tone_sets)) {
            scores = tr.long_tone_sets.map(s => scoreMatchForSet(row, "long_tone", s, tolPct)).filter(v => v != null);
        } else if (typeKey === "dtmf" && Array.isArray(tr.dtmf_sequences)) {
            scores = tr.dtmf_sequences.map(s => scoreMatchForSet(row, "dtmf", s, tolPct)).filter(v => v != null);
        }

        if (scores.length) out.push({tr, score: Math.min(...scores)});
    }

    out.sort((a, b) => a.score - b.score || (Number(a.tr.alert_trigger_id) - Number(b.tr.alert_trigger_id)));
    return out;
}

function serverWouldFire(row, tr) {
    const matches = getAllMatchingTriggersForRow(row, {restrictToIds: null})
        .filter(m => Number(m.tr.alert_trigger_id) === Number(tr.alert_trigger_id));
    return matches.length > 0;
}

function fuzzyMatchTriggerForRow(row, {restrictToIds = null} = {}) {
    const matches = getAllMatchingTriggersForRow(row, {restrictToIds});
    return matches.length ? matches[0].tr : null;
}


/* ====================================================================
   3) SEGMENT & TONE KEY HELPERS
   ==================================================================== */

/**
 * Prefer server-provided VAD segments; otherwise derive voice windows
 * from tone gaps across [0, duration].
 * @param {object} args
 * @returns {Array<{s:number,e:number,type:"voice",fa:null,fb:null,isTone:false}>}
 */
function getVoiceSegments({duration, toneWindows = [], apiVoice = null, minLen = 2.50}) {
    const out = [];

    // Prefer backend-provided VAD (robust key handling)
    const vad = Array.isArray(apiVoice) ? apiVoice :
        (Array.isArray(apiVoice?.segments) ? apiVoice.segments : null);

    if (vad && vad.length) {
        vad.forEach(seg => {
            const s = Number(seg.start_s ?? seg.start ?? seg.s);
            const e = Number(seg.end_s ?? seg.end ?? seg.e);
            if (Number.isFinite(s) && Number.isFinite(e) && (e - s) >= 0.05) {
                out.push({s, e, type: "voice", fa: null, fb: null, isTone: false});
            }
        });
        return out.sort((a, b) => a.s - b.s || a.e - b.e);
    }

    // Fallback: compute from tone windows
    if (!Number.isFinite(duration) || !toneWindows.length) {
        if (Number.isFinite(duration) && duration >= minLen) {
            out.push({s: 0, e: duration, type: "voice", fa: null, fb: null, isTone: false});
        }
        return out;
    }

    const merged = [];
    toneWindows.sort((a, b) => a.s - b.s || a.e - b.e);
    for (const w of toneWindows) {
        if (!merged.length || w.s > merged[merged.length - 1].e) merged.push({s: w.s, e: w.e});
        else merged[merged.length - 1].e = Math.max(merged[merged.length - 1].e, w.e);
    }

    const pushVoice = (vs, ve) => {
        const len = Math.max(0, ve - vs);
        if (len >= minLen) out.push({s: vs, e: ve, type: "voice", fa: null, fb: null, isTone: false});
    };

    let t0 = 0.0;
    for (const w of merged) {
        if (w.s > t0) pushVoice(t0, w.s);
        t0 = Math.max(t0, w.e);
    }
    if (duration > t0) pushVoice(t0, duration);

    return out.sort((a, b) => a.s - b.s || a.e - b.e);
}

/** Format number to 1 decimal (or empty). */
function n1(x) {
    return Number.isFinite(x) ? Number(x).toFixed(1) : "";
}

/** Build a stable key from a *tone row* (for trigger byKey lookup). */
function toneKeyFromRow(row) {
    const type = (row.dataset.type || "").toLowerCase();
    const tg = row.dataset.tg || "";
    const fa = n1(Number(row.dataset.fa));
    const fb = n1(Number(row.dataset.fb));
    const aLen = n1(Number(row.dataset.aLen));
    const bLen = n1(Number(row.dataset.bLen));
    const alts = row.dataset.alts || "";
    const cyc = row.dataset.cycles || "";
    const len = n1(Number(row.dataset.len));

    switch (type) {
        case "two_tone":
            return `two_tone|tg:${tg}|fa:${fa}|fb:${fb}|a:${aLen}|b:${bLen}`;
        case "hi_low":
            return `hi_low|tg:${tg}|fa:${fa}|fb:${fb}|alts:${alts}`;
        case "pulsed":
            return `pulsed|tg:${tg}|fa:${fa}|cycles:${cyc}`;
        case "long":
        case "long_tone":
            return `long|tg:${tg}|fa:${fa}|len:${len}`;
        default:
            return "";
    }
}

/** Build a key from a *trigger row* (for byKey index). */
function toneKeyFromTrigger(tr) {
    const type = (() => {
        if (tr.alert_trigger_two_tone_a || tr.alert_trigger_two_tone_b) return "two_tone";
        if (tr.alert_trigger_hi_low_tone_a || tr.alert_trigger_hi_low_tone_b) return "hi_low";
        if (tr.alert_trigger_pulsed_tone) return "pulsed";
        if (tr.alert_trigger_long_tone) return "long";
        return "";
    })();

    const tg = tr.alert_trigger_talkgroup ?? "";
    const fa = n1(tr.alert_trigger_two_tone_a ?? tr.alert_trigger_hi_low_tone_a ?? tr.alert_trigger_pulsed_tone ?? tr.alert_trigger_long_tone);
    const fb = n1(tr.alert_trigger_two_tone_b ?? tr.alert_trigger_hi_low_tone_b);
    const aLen = n1(tr.alert_trigger_two_tone_a_length);
    const bLen = n1(tr.alert_trigger_two_tone_b_length);
    const alts = tr.alert_trigger_hi_low_alternations ?? "";
    const cyc = tr.alert_trigger_pulsed_min_cycles ?? "";
    const len = n1(tr.alert_trigger_long_tone_length);

    switch (type) {
        case "two_tone":
            return `two_tone|tg:${tg}|fa:${fa}|fb:${fb}|a:${aLen}|b:${bLen}`;
        case "hi_low":
            return `hi_low|tg:${tg}|fa:${fa}|fb:${fb}|alts:${alts}`;
        case "pulsed":
            return `pulsed|tg:${tg}|fa:${fa}|cycles:${cyc}`;
        case "long":
            return `long|tg:${tg}|fa:${fa}|len:${len}`;
        default:
            return "";
    }
}

/**
 * Resolve which trigger most likely corresponds to a tone row, preferring:
 *  baked > fired (single/multi) > exact key > wildcard > fuzzy > null.
 * @param {HTMLTableRowElement} row
 * @param {Set<string|number>}  firedIdSet
 * @returns {{tr: object|null, source: "baked"|"fired"|"exact"|"wildcard"|"fuzzy"|null}}
 */
function resolveTriggerForToneRow(row, firedIdSet) {
    // 1) Baked id/name on the row (if present)
    const bakedId = row.dataset.matchedTriggerId;
    if (bakedId != null) {
        const tr = systemTriggerIndex?.byId?.get(Number(bakedId)) || null;
        if (tr) return {tr, source: "baked"};
    }

    // 2) Fired triggers → choose best (set-aware)
    const firedIds = firedIdSet && firedIdSet.size ? [...firedIdSet].map(Number).filter(Number.isFinite) : [];
    if (firedIds.length) {
        const bestFired = getAllMatchingTriggersForRow(row, {restrictToIds: new Set(firedIds)});
        if (bestFired.length) return {tr: bestFired[0].tr, source: "fired"};
    }

    // 3) Exact key, then wildcard (legacy single-set keys)
    const exactKey = toneKeyFromRow(row);
    if (exactKey) {
        const exact = systemTriggerIndex?.byKey?.get(exactKey) || null;
        if (exact) return {tr: exact, source: "exact"};
        const wildcardKey = exactKey.replace(/(\|tg:)[^|]*/, '$1');
        const wild = systemTriggerIndex?.byKey?.get(wildcardKey) || null;
        if (wild) return {tr: wild, source: "wildcard"};
    }

    // 4) Best set-aware fuzzy
    const fuzzy = fuzzyMatchTriggerForRow(row);
    if (fuzzy) return {tr: fuzzy, source: "fuzzy"};

    return {tr: null, source: null};
}


/* ====================================================================
   4) REMOTE DATA & INDEX BUILDERS
   ==================================================================== */

/**
 * Populate systemTriggerIndex for the selected system.
 * Builds exact and wildcard (no-TG) keys for fast lookup.
 */
async function refreshSystemTriggers(force = false, radioSystemId = "") {
    const sysId = radioSystemId || getTriggerSystemId();
    if (!sysId) {
        systemTriggerIndex = {keys: new Set(), raw: [], byKey: new Map(), byId: new Map(), systemId: null};
        return;
    }

    if (!force &&
        systemTriggerIndex?.systemId === sysId &&
        Array.isArray(systemTriggerIndex?.raw) &&
        systemTriggerIndex.raw.length > 0) {
        return; // already loaded
    }

    const r = await fetch(`/api/systems/${sysId}/triggers?full=1`);
    const js = await r.json().catch(() => ({success: false, result: []}));
    if (!js.success) {
        systemTriggerIndex = {keys: new Set(), raw: [], byKey: new Map(), byId: new Map(), systemId: null};
        return;
    }

    const keys = new Set();
    const byKey = new Map();
    const byId = new Map();

    (js.result || []).forEach(tr => {
        const id = tr.alert_trigger_id;
        if (id != null) byId.set(Number(id), tr);

        // Build keys from multi-set arrays when present; else fall back to legacy.
        const ks = toneKeysFromTrigger(tr);
        ks.forEach(k => {
            keys.add(k);
            if (!byKey.has(k)) byKey.set(k, tr);
        });
    });

    systemTriggerIndex = {keys, raw: js.result || [], byKey, byId, systemId: sysId};
}

function toneKeysFromTrigger(tr) {
    const out = [];
    const tg = tr.alert_trigger_talkgroup ?? "";
    const n1 = v => {
        const f = Number(v);
        return Number.isFinite(f) ? f.toFixed(1) : "";
    };
    const add = key => {
        if (!key) return;
        out.push(key);
        // wildcard (no TG) variant used by resolveTriggerForToneRow
        out.push(key.replace(/(\|tg:)[^|]*/, '$1'));
    };

    const haveAnySets =
        Array.isArray(tr.two_tone_sets) ||
        Array.isArray(tr.long_tone_sets) ||
        Array.isArray(tr.hi_low_sets) ||
        Array.isArray(tr.pulsed_sets) ||
        Array.isArray(tr.dtmf_sequences);

    if (Array.isArray(tr.two_tone_sets)) {
        tr.two_tone_sets.forEach(s => add(
            `two_tone|tg:${tg}|fa:${n1(s.freq_a_hz)}|fb:${n1(s.freq_b_hz)}|a:${n1(s.min_len_a_s)}|b:${n1(s.min_len_b_s)}`
        ));
    }
    if (Array.isArray(tr.long_tone_sets)) {
        tr.long_tone_sets.forEach(s => add(
            `long|tg:${tg}|fa:${n1(s.freq_hz)}|len:${n1(s.min_len_s)}`
        ));
    }
    if (Array.isArray(tr.hi_low_sets)) {
        tr.hi_low_sets.forEach(s => add(
            `hi_low|tg:${tg}|fa:${n1(s.hi_freq_a_hz)}|fb:${n1(s.hi_freq_b_hz)}|alts:${s.min_alternations ?? ""}`
        ));
    }
    if (Array.isArray(tr.pulsed_sets)) {
        tr.pulsed_sets.forEach(s => add(
            `pulsed|tg:${tg}|fa:${n1(s.center_hz)}|cycles:${s.min_cycles ?? ""}`
        ));
    }
    if (Array.isArray(tr.dtmf_sequences)) {
        tr.dtmf_sequences.forEach(s => {
            const seq = (s.sequence || "").toString().trim().toUpperCase();
            if (seq) add(`dtmf|tg:${tg}|seq:${seq}`);
        });
    }

    // Fall back to legacy single-row key only if no arrays were present
    if (!haveAnySets) {
        const k = toneKeyFromTrigger(tr); // your existing legacy helper
        if (k) add(k);
    }

    return out;
}

/** Fetch systems list → populate selector. */
async function fetchSystems() {
    try {
        els.loader.style.display = "flex";

        const resp = await fetch("/api/systems");
        const {success, result, message} = await resp.json();
        if (!success) throw new Error(message || "API error");

        els.sysSel.innerHTML = '<option value="">All Systems</option>';
        result.forEach(sys => {
            const opt = document.createElement("option");
            opt.value = sys.system_decimal;
            opt.dataset.radioSystemId = sys.radio_system_id;
            opt.textContent = sys.system_name || `ID ${sys.system_decimal}`;
            els.sysSel.appendChild(opt);
        });

        availableRadioSystems = result;

        updateDashboardBanner({
            systemLabel: "All Systems",
            systemCount: result.length
        });

        // Also populate trigger modal system dropdown
        if (els.trigSystemId) {
            els.trigSystemId.innerHTML = '<option value="">Select a radio system</option>';
            result.forEach(sys => {
                const opt = document.createElement("option");
                opt.value = sys.radio_system_id;
                opt.textContent = sys.system_name || `ID ${sys.system_decimal}`;
                els.trigSystemId.appendChild(opt);
            });
        }
    } catch (err) {
        console.error(err);
        showAlert("Failed to load systems list", "danger");
    } finally {
        els.loader.style.display = "none";
    }
}

/** Fetch triggers list → populate trigger selector. */
async function fetchTriggers() {
    try {
        const resp = await fetch("/api/trigger-calls?limit=1000");
        const data = await resp.json();
        if (!data.success) return;

        const triggers = data.result || [];
        if (els.triggerSel) {
            els.triggerSel.innerHTML = '<option value="">All Triggers</option>';
            triggers.forEach(t => {
                const opt = document.createElement("option");
                opt.value = t.alert_trigger_id;
                opt.textContent = t.alert_trigger_name || `ID ${t.alert_trigger_id}`;
                els.triggerSel.appendChild(opt);
            });
        }
    } catch (err) {
        console.error('Failed to load triggers:', err);
    }
}


/* ====================================================================
   5) TABLE SELECTION, DELETE & STATUS HELPERS
   ==================================================================== */

/** Toggle checkbox selection for all visible rows on the current page. */
function toggleSelectAllVisible(checked) {
    const rows = table.rows({page: 'current'}).nodes().to$();
    rows.each((_, tr) => {
        const cb = tr.querySelector('input.js-rowchk');
        if (!cb) return;
        cb.checked = checked;
        const id = cb.dataset.id;
        if (checked) selectedIds.add(String(id)); else selectedIds.delete(String(id));
    });
    updateBulkUI();
}

/** Reflect header checkbox tri-state based on visible row checkboxes. */
function syncHeaderCheckbox() {
    const rows = table.rows({page: 'current'}).nodes().to$();
    const cbs = rows.map((_, tr) => tr.querySelector('input.js-rowchk')).get();
    if (!cbs.length) {
        els.selAll.checked = false;
        els.selAll.indeterminate = false;
        return;
    }

    let checked = 0;
    cbs.forEach(cb => {
        if (cb && cb.checked) checked++;
    });
    els.selAll.checked = checked === cbs.length;
    els.selAll.indeterminate = checked > 0 && checked < cbs.length;
}

/** Build little icon string for row status (🚨 fired / 🔊 tones present). */
function buildStatusIcons(row) {
    const icons = [];
    if (row.has_trigger) icons.push(`<span title="Trigger fired" aria-label="Trigger fired">🚨</span>`);
    if (row.tone_count > 0) icons.push(`<span title="Tone(s) detected" aria-label="Tone(s) detected">🔊</span>`);
    return icons.join(" ");
}

/** Confirm single-call delete via API then update DataTable. */
async function handleDeleteCall() {
    if (!callIdToDelete) return;
    const id = callIdToDelete;
    callIdToDelete = null;

    try {
        const resp = await fetch(`/api/tone-finder/calls/${id}`, {
            method: "DELETE",
            headers: {"Accept": "application/json", "X-CSRFToken": getCsrf()},
            credentials: "same-origin"
        });
        const js = await resp.json();
        if (!js.success) throw new Error(js.message || "API error");

        // Remove row
        const rowIdx = table.rows().indexes()
            .filter(i => String(table.row(i).data()?.[COL.ID]) === String(id))[0];

        selectedIds.delete(String(id));
        updateBulkUI();
        syncHeaderCheckbox();
        if (rowIdx !== undefined) table.row(rowIdx).remove().draw(false);

        stopIfDeleted(id);
        bootstrap.Modal.getInstance(els.delModal).hide();
    } catch (err) {
        console.error(err);
        showAlert("Failed to delete call", "danger");
        bootstrap.Modal.getInstance(els.delModal).hide();
    }
}

/** Bulk delete selected calls then refresh DataTable (without full reload). */
async function handleBulkDeleteConfirm() {
    const ids = Array.from(selectedIds).map(Number);
    if (!ids.length) return;

    try {
        els.bulkConfirmBtn.disabled = true;
        const resp = await fetch(`/api/tone-finder/calls/bulk-delete`, {
            method: "POST",
            headers: {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-CSRFToken": getCsrf()
            },
            body: JSON.stringify({call_ids: ids})
        });
        const js = await resp.json();
        if (!js.success) throw new Error(js.message || "API error");

        const deleted = new Set((js.result?.deleted_ids || []).map(String));

        table.rows().every(function () {
            const data = this.data();
            const id = String(data[COL.ID]);
            if (deleted.has(id)) this.remove();
        });
        table.draw(false);

        deleted.forEach(id => selectedIds.delete(id));
        updateBulkUI();
        if (currentPlayingId && deleted.has(String(currentPlayingId))) stopIfDeleted(currentPlayingId);
        syncHeaderCheckbox();

        bootstrap.Modal.getInstance(els.bulkModal).hide();

        await loadCalls();

    } catch (err) {
        console.error(err);
        showAlert("Bulk delete failed", "failed");
    } finally {
        els.bulkConfirmBtn.disabled = false;
    }
}


/* ====================================================================
   6) LOADING & RENDERING CALLS TABLE
   ==================================================================== */

/**
 * Fetch matching calls and bind to DataTable according to current filters.
 * Keeps playing highlight if the playing row still exists.
 * @param {Event} [ev]
 */
async function loadCalls(ev) {
    if (ev) ev.preventDefault();
    if (!table) return;
    const systemId = els.sysSel.value;

    const params = new URLSearchParams({ limit: "1000", offset: "0" });
    if (systemId) params.append("system", systemId);
    
    const selectedToneType = els.toneSel.value;
    const triggerId = els.triggerSel?.value;
    const incidentType = els.incidentSel?.value;
    const dateFrom = els.dateFrom?.value;
    const dateTo = els.dateTo?.value;
    const triggerOnly = els.trigChk.checked;
    
    if (selectedToneType) params.append("tone_type", selectedToneType);
    if (triggerId) params.append("trigger_id", triggerId);
    if (incidentType) params.append("incident", incidentType);
    if (dateFrom) params.append("date_from", dateFrom);
    if (dateTo) params.append("date_to", dateTo);
    if (triggerOnly) params.append("trigger_only", "1");

    try {
        const resp = await fetch(`/api/tone-finder/calls?${params}`);
        const data = await resp.json();
        if (!data.success) throw new Error(data.message || "API error");
        callMeta.clear();

        const rows = data.result.map(row => {
            const callId = row.call_id;
            const audioSrc = resolveCallAudioUrl(row);
            const label = row.talkgroup ?? "Unknown TG";
            const labelAttr = String(label).replace(/"/g, "&quot;");
            const startLocal = new Date(row.start_epoch * 1000).toLocaleString();
            const talkgroup = String(row.talkgroup ?? "");
            const systemName = row.system_name || "";
            const toneCount = row.tone_count ?? 0;
            const durSec = Number(row.duration_s ?? 0).toFixed(1) + "s";

            // Type cell: incident type badge
            const incidentColors = {
                Fire: "#dc3545", Medical: "#0d6efd", Traffic: "#ffc107",
                Rescue: "#20c997", HazMat: "#d4a017", Utilities: "#343a40", Other: "#6c757d"
            };
            const inc = row.incident;
            const typeCell = inc
                ? `<span class="badge" style="background:${incidentColors[inc] || "#6c757d"};font-size:0.7rem">${esc(inc)}</span>`
                : "";

            // Radio System badge
            const systemCell = systemName
                ? `<span class="badge" style="background:#2c3e50;color:#ccc;font-size:0.7rem;font-weight:500">${esc(systemName)}</span>`
                : "";

            // Tones Triggered: fired trigger pills only (count is its own column)
            const trigPills = (row.fired_triggers || []).slice(0, 5)
                .map(t => `<div class="trigger-pill" title="${esc(t.trigger_name)}">${esc(t.trigger_name)}</div>`)
                .join("");

            // Narrow numeric cells
            const tonesCountCell = `<span style="font-size:0.8rem;font-variant-numeric:tabular-nums">${toneCount}</span>`;
            const talkgroupCell  = `<span style="font-size:0.8rem;font-variant-numeric:tabular-nums">${esc(talkgroup)}</span>`;
            const durationCell   = `<span style="font-size:0.8rem;font-variant-numeric:tabular-nums">${durSec}</span>`;

            callMeta.set(String(callId), {src: audioSrc, label});

            return [
                checkboxCell(callId),
                startLocal,
                typeCell,
                systemCell,
                trigPills,
                tonesCountCell,
                talkgroupCell,
                durationCell,
                `<div class="btn-group btn-group-sm" role="group">
                  <button class="btn btn-success js-play"
                          data-id="${callId}" data-src="${audioSrc}" data-label="${labelAttr}" title="Play">
                    <i class="bi bi-play-fill"></i>
                  </button>
                  <button class="btn btn-warning js-reprocess" data-id="${callId}" title="Reprocess Tones">
                    <i class="bi bi-arrow-repeat"></i>
                  </button>
                  <button class="btn btn-danger js-del" data-id="${callId}" title="Delete">
                    <i class="bi bi-trash"></i>
                  </button>
                </div>`,
                callId,
                row.start_epoch
            ];
        });

        table.clear();
        if (rows.length) table.rows.add(rows);
        table.draw(false);

        // Update stats from current filtered data
        const allRows = data.result;
        const totalCalls = allRows.length;
        const triggered = allRows.filter(r => r.has_trigger).length;
        const transcribed = allRows.filter(r => r.has_transcript).length;
        const avgDur = allRows.length
            ? (allRows.reduce((s, r) => s + (Number(r.duration_s) || 0), 0) / allRows.length).toFixed(1) + " s"
            : "-";
        const incCounts = {};
        allRows.forEach(r => { if (r.incident) incCounts[r.incident] = (incCounts[r.incident] || 0) + 1; });

        const elTotalCalls = document.getElementById("statTotalCalls");
        const elTriggered  = document.getElementById("statTriggered");
        const elTranscribed = document.getElementById("statTranscribed");
        const elAvgDur     = document.getElementById("statAvgDuration");
        if (elTotalCalls)  elTotalCalls.textContent  = totalCalls;
        if (elTriggered)   elTriggered.textContent   = triggered;
        if (elTranscribed) elTranscribed.textContent = transcribed;
        if (elAvgDur)      elAvgDur.textContent      = avgDur;

        updateDashboardBanner({
            systemLabel: els.sysSel.value
                ? (els.sysSel.options[els.sysSel.selectedIndex]?.textContent || "Selected system")
                : "All Systems",
            hitCount: totalCalls
        });

        ["Fire","Medical","Traffic","Rescue","Utilities","HazMat","Other"].forEach(type => {
            const el = document.querySelector(`.badge-${type.toLowerCase()}`);
            if (el) el.textContent = `${type}: ${incCounts[type] || 0}`;
        });

        syncHeaderCheckbox();
        updateBulkUI();

        if (currentPlayingId) {
            const stillPresent = table.rows().data().toArray()
                .some(r => String(r[COL.ID]) === String(currentPlayingId));
            if (!stillPresent) stopIfDeleted(currentPlayingId);
            else highlightPlayingRow();
        }
    } catch (err) {
        console.error(err);
        showAlert("Failed to load calls", "danger");
    }
}

/**
 * Row click handler → fetch call detail → open modal.
 * Ignores clicks on play/delete buttons and checkbox.
 * @param {MouseEvent} e
 */
async function onRowClick(e) {
    if (e.target.closest(".js-del") || e.target.closest(".js-play")) return;

    const tr = e.target.closest("tr");
    if (!tr) return;

    const rowData = table.row(tr).data();
    if (!rowData) return;

    const callId = rowData[COL.ID];

    if (currentPlayingId && String(currentPlayingId) !== String(callId) && window.npHasSrc?.()) {
        stopAndResetPlayer();
    }

    try {
        const r = await fetch(`/api/tone-finder/calls/${callId}`);
        const js = await r.json();
        if (!js.success) throw new Error(js.message || "API error");

        await refreshSystemTriggers(true, js.result.call.radio_system_id);

        renderCallDetails(js.result);
        bootstrap.Modal.getOrCreateInstance(els.callModal).show();
    } catch (err) {
        console.error(err);
        showAlert("Failed to load call details", "danger");
    }
}


/* ====================================================================
   7) PAGE-LEVEL ORCHESTRATION (filters, timers, validation)
   ==================================================================== */

/** Load calls for selected system (or all if none selected). */
function maybeLoad() {
    if (!table) return;
    loadCalls(); // Load all systems if no selection
    applyRefreshInterval();
}

/** (Re)start the auto-refresh timer based on dropdown value. */
function applyRefreshInterval() {
    if (refreshTimer) {
        clearInterval(refreshTimer);
        refreshTimer = null;
    }
    const ms = Number(els.autoRef.value) || 0;
    if (ms > 0) {
        refreshTimer = setInterval(() => {
            loadCalls(); // Works with all systems too
        }, ms);
    }
}

/** Enable Bootstrap validation styling for any .needs-validation forms. */
function initValidation() {
    const forms = document.querySelectorAll(".needs-validation");
    forms.forEach(f => {
        f.addEventListener("submit", ev => {
            if (!f.checkValidity()) {
                ev.preventDefault();
                ev.stopPropagation();
            }
            f.classList.add("was-validated");
        });
    });
}


/* ====================================================================
   8) PAGE INIT (DataTable, listeners, tooltips, initial fetch)
   ==================================================================== */

/**
 * Initialize globals, create DataTable, attach listeners, fetch systems, etc.
 * Runs once on DOMContentLoaded (see bottom).
 */
function initToneFinderPage() {
    Object.assign(els, {
        loader: document.querySelector(".page-loader"),
        sysSel: document.getElementById("systemSelect"),
        toneSel: document.getElementById("toneType"),
        triggerSel: document.getElementById("triggerSelect"),
        incidentSel: document.getElementById("incidentSelect"),
        dateFrom: document.getElementById("dateFrom"),
        dateTo: document.getElementById("dateTo"),
        trigChk: document.getElementById("triggerOnly"),
        clearFilters: document.getElementById("clearFilters"),
        reprocessAllBtn: document.getElementById("reprocessAllBtn"),
        refreshBtn: document.getElementById("refreshBtn"),

        callModal: document.getElementById("callModal"),
        delModal: document.getElementById("deleteModal"),
        delConfirmBtn: document.getElementById("deleteCallConfirmBtn"),

        autoRef: document.getElementById("autoRefresh"),

        dPlayPauseBtn: document.getElementById("dPlayPauseBtn"),
        dStopBtn: document.getElementById("dStopBtn"),
        dDeleteFromModalBtn: document.getElementById("dDeleteFromModalBtn"),

        dCallId: document.getElementById("dCallId"),
        dTG: document.getElementById("dTG"),
        dStart: document.getElementById("dStart"),
        dDur: document.getElementById("dDur"),
        dMerged: document.getElementById("dMerged"),
        dTrigList: document.getElementById("dTrigList"),
        dNoTrig: document.getElementById("dNoTrig"),
        dTonesTable: document.getElementById("dTonesTable"),

        selAll: document.getElementById("selectAllRows"),
        bulkBtn: document.getElementById("bulkDeleteBtn"),
        bulkCount: document.getElementById("bulkSelectedCount"),
        bulkModal: document.getElementById("bulkDeleteModal"),
        bulkCountInModal: document.getElementById("bulkCountInModal"),
        bulkConfirmBtn: document.getElementById("bulkDeleteConfirmBtn"),
        dashboardSystemBadge: document.getElementById("dashboardSystemBadge"),
        dashboardHitCountBadge: document.getElementById("dashboardHitCountBadge"),

        // “Create Trigger” modal (lightweight)
        triggerModal: document.getElementById("triggerModal"),
        triggerForm: document.getElementById("triggerForm"),
        trigSystemId: document.getElementById("modalSystemId"),
        trigTalkgroup: document.getElementById("modalTriggerTalkgroup"),
        trigId: document.getElementById("modalTriggerId"),
        trigName: document.getElementById("modalTriggerName"),
        trigEnabled: document.getElementById("modalTriggerEnabled"),
        trigType: document.getElementById("modalTriggerType"),
        trigSaveBtn: document.getElementById("modalSaveBtn"),
        trigTitle: document.getElementById("triggerModalLabel"),

        dTranscriptWrap: document.getElementById("dTranscriptWrap"),
        dTranscriptStatus: document.getElementById("dTranscriptStatus"),
        dTranscriptBody: document.getElementById("dTranscriptBody"),
        copyTranscriptBtn: document.getElementById("copyTranscriptBtn"),
        expandTranscriptBtn: document.getElementById("expandTranscriptBtn"),

        dIncidentWrap: document.getElementById("dIncidentWrap"),
        dIncidentTypeBadge: document.getElementById("dIncidentTypeBadge"),
        dIncidentSeverityBadge: document.getElementById("dIncidentSeverityBadge"),
        dIncidentMeta: document.getElementById("dIncidentMeta"),
        dIncidentSummary: document.getElementById("dIncidentSummary"),

        dAddressWrap: document.getElementById("dAddressWrap"),
        dAddrExtracted: document.getElementById("dAddrExtracted"),
        dAddrGeocoded: document.getElementById("dAddrGeocoded"),
        dAddrMapLink: document.getElementById("dAddrMapLink"),

    });
    // Tone actions use the call's routed operational system automatically.
    document.getElementById("triggerModalSystemPicker")?.classList.add("d-none");

    if (els.autoRef) {
        if (!els.autoRef.value || els.autoRef.value === "0") {
            els.autoRef.value = "15000"; // ms
        }
    }

    table = new DataTable("#callsTable", {
        responsive: { details: false },
        autoWidth: false,
        order: [[COL.EPOCH, "desc"]],
        pageLength: 100,
        lengthMenu: [25, 50, 100, 250, 500, 1000],
        columnDefs: [
            // ── widths ───────────────────────────────────────────────
            { targets: COL.SEL,         width: "28px",  orderable: false, searchable: false },
            { targets: COL.TIME,        width: "120px" },
            { targets: COL.TYPE,        width: "70px",  className: "text-center" },
            { targets: COL.SYSTEM,      width: "120px" },
            { targets: COL.TONES_TRIG,  width: "240px" },
            { targets: COL.TONES_COUNT, width: "50px",  className: "text-end" },
            { targets: COL.TG,          width: "80px",  className: "text-end" },
            { targets: COL.DUR,         width: "60px",  className: "text-end" },
            { targets: COL.ACTIONS,     width: "100px", orderable: false, searchable: false },
            { targets: [COL.ID, COL.EPOCH], visible: false },

            // ── responsive: lower = kept visible longer ───────────
            { targets: [COL.TIME, COL.TONES_TRIG, COL.ACTIONS], responsivePriority: 1 },
            { targets: [COL.SYSTEM],                             responsivePriority: 2 },
            { targets: [COL.TYPE, COL.TG],                       responsivePriority: 3 },
            { targets: [COL.TONES_COUNT, COL.DUR],               responsivePriority: 4 },
            { targets: [COL.SEL],                                responsivePriority: 5 }
        ]
    });

    /* ---------- Listeners: filters & controls ---------- */
    els.sysSel.addEventListener("change", async () => {
        clearMainSelection();
        stopAndResetPlayer();
        await refreshSystemTriggers();
        maybeLoad();
    });

    els.toneSel.addEventListener("change", () => {
        clearMainSelection();
        stopAndResetPlayer();
        maybeLoad();
    });

    els.trigChk.addEventListener("change", () => {
        clearMainSelection();
        stopAndResetPlayer();
        maybeLoad();
    });

    els.autoRef.addEventListener("change", applyRefreshInterval);

    // New filter event listeners
    if (els.triggerSel) {
        els.triggerSel.addEventListener("change", () => {
            clearMainSelection();
            stopAndResetPlayer();
            maybeLoad();
        });
    }
    if (els.incidentSel) {
        els.incidentSel.addEventListener("change", () => {
            clearMainSelection();
            stopAndResetPlayer();
            maybeLoad();
        });
    }
    if (els.dateFrom) {
        els.dateFrom.addEventListener("change", () => {
            clearMainSelection();
            stopAndResetPlayer();
            maybeLoad();
        });
    }
    if (els.dateTo) {
        els.dateTo.addEventListener("change", () => {
            clearMainSelection();
            stopAndResetPlayer();
            maybeLoad();
        });
    }
    if (els.clearFilters) {
        els.clearFilters.addEventListener("click", () => {
            if (els.sysSel) els.sysSel.value = "";
            if (els.toneSel) els.toneSel.value = "";
            if (els.triggerSel) els.triggerSel.value = "";
            if (els.incidentSel) els.incidentSel.value = "";
            if (els.dateFrom) els.dateFrom.value = "";
            if (els.dateTo) els.dateTo.value = "";
            if (els.trigChk) els.trigChk.checked = false;
            clearMainSelection();
            stopAndResetPlayer();
            maybeLoad();
        });
    }
    if (els.refreshBtn) {
        els.refreshBtn.addEventListener("click", () => {
            clearMainSelection();
            stopAndResetPlayer();
            maybeLoad();
        });
    }

    if (els.reprocessAllBtn) {
        els.reprocessAllBtn.addEventListener("click", async () => {
            const btn = els.reprocessAllBtn;
            const rsid = els.sysSel?.value || null;

            const scope = rsid ? "the selected system" : "ALL systems";
            const confirmed = await confirmAction({
                title: "Reprocess All Triggers",
                body: `This will re-evaluate every stored tone event for ${scope} against all current triggers. On large datasets this may take a while. Continue?`,
                confirmText: "Reprocess",
                confirmClass: "btn-warning",
            });
            if (!confirmed) return;

            btn.disabled = true;
            btn.innerHTML = '<i class="bi bi-hourglass-split"></i> Reprocessing…';
            try {
                const body = rsid ? JSON.stringify({ radio_system_id: rsid }) : "{}";
                const resp = await fetch("/api/tone-finder/reprocess-triggers", {
                    method: "POST",
                    headers: { "Content-Type": "application/json", "X-CSRFToken": getCsrf() },
                    body
                });
                const data = await resp.json();
                if (data.success) {
                    showAlert(`Reprocessed ${data.result.updated} calls (${data.result.errors} errors)`, "success");
                    maybeLoad();
                } else {
                    showAlert("Reprocess failed: " + (data.message || "unknown error"), "danger");
                }
            } catch (err) {
                console.error("[Reprocess] Error:", err);
                showAlert("Reprocess request failed: " + err.message, "danger");
            } finally {
                btn.disabled = false;
                btn.innerHTML = '<i class="bi bi-arrow-repeat"></i> Reprocess All';
            }
        });
    }

    const tbody = document.querySelector("#callsTable tbody");

    // Checkbox delegate on body
    tbody.addEventListener("change", (e) => {
        const cb = e.target.closest("input.js-rowchk");
        if (!cb) return;
        const id = String(cb.dataset.id);
        if (cb.checked) selectedIds.add(id); else selectedIds.delete(id);
        updateBulkUI();
        syncHeaderCheckbox();
        e.stopPropagation();
    });

    // Row click → modal (ignore on interactive controls)
    tbody.addEventListener("click", (e) => {
        const td = e.target.closest("td");
        if (td && td.querySelector('input.js-rowchk')) return;

        if (e.target.closest("input,button,.btn")) return;
        onRowClick(e);
    });

    // Play button
    tbody.addEventListener("click", (e) => {
        const btn = e.target.closest(".js-play");
        if (!btn) return;
        e.stopPropagation();
        startPlayback(btn.dataset.id, btn.dataset.src, btn.dataset.label);
    });

    // Single delete button
    tbody.addEventListener("click", (e) => {
        const btn = e.target.closest(".js-del");
        if (!btn) return;
        e.stopPropagation();
        callIdToDelete = btn.dataset.id;
        bootstrap.Modal.getOrCreateInstance(els.delModal).show();
    });

    // Reprocess tones button
    tbody.addEventListener("click", async (e) => {
        const btn = e.target.closest(".js-reprocess");
        if (!btn) return;
        e.stopPropagation();
        const callId = btn.dataset.id;
        
        try {
            const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content;
            const resp = await fetch(`/api/call-upload/reprocess/${callId}`, {
                method: 'POST',
                headers: {'X-CSRF-Token': csrfToken || ''}
            });
            const data = await resp.json();
            if (resp.ok) {
                showAlert('Tones reprocessed successfully', 'success');
                maybeLoad();
            } else {
                showAlert('Failed to reprocess: ' + (data.error || 'Unknown error'), 'danger');
            }
        } catch (err) {
            showAlert('Failed to reprocess tones: ' + err.message, 'danger');
        }
    });

    // Delegate: click the pencil to toggle the drawer
    tbody.addEventListener("click", (e) => {
        const btn = e.target.closest(".js-edit");
        if (!btn) return;

        e.stopPropagation();
        const tr = btn.closest("tr");
        const rowApi = table.row(tr);
        const data = rowApi.data();
        const callId = data?.[COL.ID];
        if (callId == null) return;

        const expanded = rowApi.child.isShown();
        btn.setAttribute("aria-expanded", expanded ? "false" : "true");

        if (expanded) {
            rowApi.child.hide();
            tr.classList.remove("shown");
            openDrawers.delete(String(callId));
        } else {
            openDrawerForRow(rowApi, tr, callId);
        }
    });

    els.copyTranscriptBtn?.addEventListener("click", async () => {
        let txt = "";

        if (transcriptModel && Array.isArray(transcriptModel.segments)) {
            txt = transcriptModel.segments.map(s => s.text || "").join(" ").trim();
        }

        if (!txt && transcriptModel?.meta?.text_full) {
            txt = String(transcriptModel.meta.text_full).trim();
        }

        // Fallback: raw text from DOM if nothing in model
        if (!txt && els.dTranscriptBody) {
            txt = els.dTranscriptBody.innerText.trim();
        }

        if (!txt) return;

        await navigator.clipboard.writeText(txt);
        showAlert("Transcript copied.", "success");
    });
    els.expandTranscriptBtn?.addEventListener("click", () => {
        const v = els.dTranscriptBody.getAttribute("data-collapsed") === "1" ? "0" : "1";
        els.dTranscriptBody.setAttribute("data-collapsed", v);
        els.expandTranscriptBtn.textContent = (v === "1") ? "Expand" : "Collapse";
    });

    // Header “select all visible”
    els.selAll.addEventListener("change", () => {
        toggleSelectAllVisible(els.selAll.checked);
    });

    // Keep header checkbox sane on redraw
    $('#callsTable').on('draw.dt', () => {
        syncHeaderCheckbox();
    });

    // Bulk delete button → modal
    els.bulkBtn.addEventListener("click", () => {
        els.bulkCountInModal.textContent = String(selectedIds.size);
        bootstrap.Modal.getOrCreateInstance(els.bulkModal).show();
    });

    // Bulk confirm delete
    els.bulkConfirmBtn.addEventListener("click", handleBulkDeleteConfirm);

    /* ---------- Modal transport & actions ---------- */
    els.dPlayPauseBtn.addEventListener("click", async () => {
        const id = els.dPlayPauseBtn.dataset.id;
        const src = els.dPlayPauseBtn.dataset.src;
        const label = els.dPlayPauseBtn.dataset.label || `Call ${id}`;

        const sameCall = String(currentPlayingId) === String(id) && window.npHasSrc?.();

        if (!sameCall) {
            currentPlayingId = String(id);
            highlightPlayingRow();
            await window.playFrom?.({src, title: label, start: 0});
        } else {
            if (window.npIsPaused?.()) {
                await window.npPlay?.();
            } else {
                window.npPause?.();
            }
        }
    });

    els.dStopBtn.addEventListener("click", () => {
        window.npStop?.();
    });

    els.dDeleteFromModalBtn.addEventListener("click", () => {
        callIdToDelete = modalCallId;
        bootstrap.Modal.getOrCreateInstance(els.delModal).show();
    });

    // Single delete confirm
    els.delConfirmBtn.addEventListener("click", handleDeleteCall);

    /* ---------- Tones table interactions in modal ---------- */
    els.dTonesTable.addEventListener("click", async (e) => {
        const editLink = e.target.closest("a.js-edit-trigger-link");
        if (editLink) { e.stopPropagation(); return; }

        const ddHit = e.target.closest(".dropdown-menu, .dropdown-toggle, .dropdown-item");
        if (ddHit) { e.stopPropagation(); return; }

        const addBtn = e.target.closest(".js-create-trigger");
        if (addBtn) {
            e.preventDefault();
            e.stopPropagation();
            const row = addBtn.closest("tr.segment-row");
            if (!row) return;

            const sysId = getTriggerSystemId();
            if (!sysId) { alert("Select a system first."); return; }

            ensureAddOrAttachModal();
            openAddOrAttachForRow(row);
            return;
        }

        const toggle = e.target.closest(".js-toggle-drawer");
        if (toggle) {
            e.preventDefault();
            e.stopPropagation();

            const tr = toggle.closest("tr.segment-row");
            if (!tr) return;

            // If a drawer is already open right after this row, close it
            const next = tr.nextElementSibling;
            if (next && next.classList.contains("tone-drawer")) {
                next.remove();
                toggle.setAttribute("aria-expanded", "false");
                return;
            }

            const sysId = getTriggerSystemId();
            const matches = getAllMatchingTriggersForRow(tr); // uses your existing helper

            const list = Array.isArray(matches) ? matches : [];

            const items = list.map(({ tr: trig }) => {
                const id = trig.alert_trigger_id;
                const name = esc(trig.alert_trigger_name || `Trigger ${id}`);
                const href = `/dashboard/triggers?system=${encodeURIComponent(sysId)}&trigger=${encodeURIComponent(id)}`;
                return `<li class="list-group-item d-flex justify-content-between align-items-center">
                        <div class="fw-semibold">${name}</div>
                        <a class="btn btn-sm btn-secondary" href="${href}" aria-label="Edit ${name}">
                            <i class="bi bi-pencil-square"></i> Edit
                        </a>
                    </li>`;
            }).join("");

            const html = items
                ? `<ul class="list-group list-group-flush">${items}</ul>`
                : `<div class="text-muted small">No matching triggers for this tone.</div>`;

            const drawer = document.createElement("tr");
            drawer.className = "tone-drawer";
            const td = document.createElement("td");
            td.colSpan = els.dTonesTable.tHead.rows[0].cells.length;
            td.innerHTML = `<div class="p-2">${html}</div>`;
            drawer.appendChild(td);
            tr.after(drawer);

            toggle.setAttribute("aria-expanded", "true");
            return;
        }

        // ====== SEEK-AND-PLAY on row click (any segment type, any cell except Actions) ======
        const row = e.target.closest("tbody tr.segment-row");
        const cell = e.target.closest("td");
        if (!row || !cell) return;

        // Ignore clicks on the Actions cell (last column)
        const isActionsCell = (cell.cellIndex === row.cells.length - 1);
        if (isActionsCell) return;

        e.stopPropagation();

        const start = parseFloat(row.dataset.start);
        if (!Number.isFinite(start)) return;

        const id = els.dPlayPauseBtn.dataset.id;
        const src = els.dPlayPauseBtn.dataset.src;
        const label = els.dPlayPauseBtn.dataset.label || `Call ${id}`;

        const sameCall = String(currentPlayingId) === String(id) && window.npHasSrc?.();

        if (!sameCall) {
            currentPlayingId = String(id);
            highlightPlayingRow();
            await window.playFrom?.({ src, title: label, start });
        } else {
            // Always seek + ensure playback (no pause toggle)
            window.seekNowPlaying?.(start);
            if (window.npIsPaused?.()) { await window.npPlay?.(); }
        }

        // Update Now Playing subtitle/meta
        const type = row.dataset.type;
        const fa = parseFloat(row.dataset.fa);
        const fb = parseFloat(row.dataset.fb);
        const toneLabel = (type === "voice") ? "Voice segment" : formatToneLabel(type, fa, fb);

        const tid = row.dataset.resolvedTriggerId != null ? Number(row.dataset.resolvedTriggerId) : null;
        const trigLabel = (tid != null) ? getTriggerLabelById(tid) : null;

        const sub = trigLabel ? `${toneLabel} — Trigger: ${trigLabel}` : toneLabel;
        const metaClass = (row.classList.contains("voice-row"))
            ? "voice-meta"
            : (row.classList.contains("trigger-row") ? "trigger-meta" : "tone-meta");
        window.updateNowPlayingMeta?.({ subtitle: sub, metaClass });
    });


    // Create Trigger modal submit
    els.triggerForm.addEventListener("submit", async (ev) => {
        ev.preventDefault();
        if (!pendingTriggerPayload || !activeTriggerRow) return;
        if (ev.submitter && ev.submitter.id !== "modalSaveBtn") return;

        const sysId = els.trigSystemId.value || getTriggerSystemId();
        if (!sysId) { alert("The call has no operational radio system."); return; }

        const body = {
            ...pendingTriggerPayload.body,
            alert_trigger_name: els.trigName.value?.trim() || defaultTriggerNameFromRow(activeTriggerRow),
            alert_trigger_enabled: els.trigEnabled.value,
            alert_trigger_type: els.trigType.value,
        };

        const tgRaw = (els.trigTalkgroup.value || "").trim();
        if (tgRaw !== "") {
            const tgInt = parseInt(tgRaw, 10);
            if (Number.isFinite(tgInt)) body.alert_trigger_talkgroup = tgInt;
        }

        els.trigSaveBtn.disabled = true;

        try {
            const resp = await fetch(`/api/systems/${sysId}/triggers`, {
                method: "POST",
                headers: {
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "X-CSRFToken": getCsrf(),
                },
                body: JSON.stringify(body),
            });
            const js = await resp.json().catch(() => ({}));
            if (!resp.ok || !js.success) throw new Error(js.message || "Failed to create trigger.");

            bootstrap.Modal.getInstance(els.triggerModal)?.hide();

            const newId = js.result?.alert_trigger_id ?? null;

            const nameFromForm = (els.trigName?.value || "").trim();
            const parsedName = (js.message || "").match(/Trigger ['“](.+?)['”] created/i)?.[1] || "";
            const trigName = nameFromForm || parsedName;
            const label = trigName ? `“${trigName}”` : (newId != null ? `#${newId}` : "");

            const sysIdForLink = els.trigSystemId.value;
            const editMarkup =
                `<a href="/dashboard/triggers?system=${encodeURIComponent(sysIdForLink)}${newId ? `&trigger=${encodeURIComponent(newId)}` : ""}"
           class="btn btn-sm btn-secondary js-edit-trigger-link"
           data-bs-toggle="tooltip" data-bs-placement="top"
           data-bs-title="Edit"
           aria-label="Edit">
           <i class="bi bi-pencil-square"></i>
         </a>`;

            const cell = activeTriggerRow?.querySelector("td:last-child");
            if (cell) {
                const existingAdd = cell.querySelector('.js-create-trigger')?.outerHTML || "";
                cell.innerHTML = `
                   <div class="btn-group" role="group">
                     ${existingAdd}
                     ${editMarkup.replace(/data-bs-title="[^"]*"/, 'data-bs-title="Edit"')}
                   </div>`;
                cell.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(el => {
                    bootstrap.Tooltip.getOrCreateInstance(el, {container: els.callModal});
                });
            }

            try {
                const b = pendingTriggerPayload?.body || {};
                const trLike = {
                    alert_trigger_id: newId,
                    alert_trigger_name: els.trigName.value?.trim() || null,
                    alert_trigger_talkgroup: b.alert_trigger_talkgroup ?? null,
                    alert_trigger_two_tone_a: b.alert_trigger_two_tone_a,
                    alert_trigger_two_tone_a_length: b.alert_trigger_two_tone_a_length,
                    alert_trigger_two_tone_b: b.alert_trigger_two_tone_b,
                    alert_trigger_two_tone_b_length: b.alert_trigger_two_tone_b_length,
                    alert_trigger_long_tone: b.alert_trigger_long_tone,
                    alert_trigger_long_tone_length: b.alert_trigger_long_tone_length,
                    alert_trigger_hi_low_tone_a: b.alert_trigger_hi_low_tone_a,
                    alert_trigger_hi_low_tone_b: b.alert_trigger_hi_low_tone_b,
                    alert_trigger_hi_low_alternations: b.alert_trigger_hi_low_alternations,
                    alert_trigger_pulsed_tone: b.alert_trigger_pulsed_tone,
                    alert_trigger_pulsed_min_cycles: b.alert_trigger_pulsed_min_cycles,
                };

                const k = toneKeyFromTrigger(trLike);
                const kw = k ? k.replace(/(\|tg:)[^|]*/, '$1') : null;

                systemTriggerIndex.byId.set(Number(newId), trLike);
                if (k && !systemTriggerIndex.byKey.has(k)) systemTriggerIndex.byKey.set(k, trLike);
                if (kw && !systemTriggerIndex.byKey.has(kw)) systemTriggerIndex.byKey.set(kw, trLike);
                systemTriggerIndex.keys.add(k);
                systemTriggerIndex.raw.push(trLike);
            } catch { /* non-fatal */ }

            refreshSystemTriggers().catch(() => {});

            activeTriggerRow = null;
            pendingTriggerPayload = null;

            showAlert(`Trigger ${label ? label + " " : ""}created.`, "success");
        } catch (err) {
            console.error(err);
            showAlert(err.message || "Failed to create trigger.", "danger");
        } finally {
            els.trigSaveBtn.disabled = false;
        }
    });

    // After closing “create trigger” modal, optionally re-open details
    els.triggerModal.addEventListener("hidden.bs.modal", () => {
        activeTriggerRow = null;
        pendingTriggerPayload = null;
        if (reopenDetailsAfterTrigger) {
            reopenDetailsAfterTrigger = false;
            bootstrap.Modal.getOrCreateInstance(els.callModal).show();
        }
    });

    // Keep modal transport UI in sync with player
    els.callModal.addEventListener('shown.bs.modal', () => {
        const t = window.npGetState?.()?.currentTime;
        if (Number.isFinite(t)) syncTranscriptInlineHighlight(t);
    });
    els.callModal.addEventListener('hidden.bs.modal', () => {
        syncModalTransportUI();
        if (!reopenDetailsAfterTrigger) stopAndResetPlayer();
    });

    // Player events → update modal transport and tone highlight
    window.addEventListener('np:state', syncModalTransportUI);
    window.addEventListener('np:time', () => {
        syncModalTransportUI();
        const t = window.npGetState?.()?.currentTime;
        if (Number.isFinite(t)) {
            syncModalToneHighlight(t);      // existing
            syncTranscriptInlineHighlight(t); // new (header transcript)
        }
    });

    initValidation();

    // Initial data
    fetchSystems().then(async () => {
        await refreshSystemTriggers();
        applyRefreshInterval();
        maybeLoad();
    });

    // Tooltips (lazy delegate)
    new bootstrap.Tooltip(document.body, {
        selector: '[data-bs-toggle="tooltip"]',
        container: 'body',
        boundary: 'window'
    });
}

function buildTranscriptModelFromCallData(data) {
    const meta = (data?.transcript) || {};
    let segs = [];

    // 1) Top-level: transcript_segments
    if (Array.isArray(data?.transcript_segments)) {
        segs = data.transcript_segments;
    }
    // 2) Nested under transcript.segments
    else if (Array.isArray(meta.segments)) {
        segs = meta.segments;
    }
    // 3) Fallback: top-level "segments" (just in case)
    else if (Array.isArray(data?.segments)) {
        segs = data.segments;
    }

    // Full-text fallback (for highlight + copy)
    const fullText =
        meta.text_full ||
        meta.text ||
        data?.transcript_text ||
        null;

    // If we have **no** segments but do have full text -> make a single segment
    if (!segs.length && fullText) {
        const dur = Number(data?.call?.duration_s);
        const s = 0;
        const e = Number.isFinite(dur) && dur > 0 ? dur : 0;

        return {
            segments: [{ s, e, text: fullText, words: null }],
            meta: { ...meta, text_full: fullText },
        };
    }

    // Normalise segment timing + text
    const norm = segs
        .map(s => {
            const start = Number(
                s.start_s ?? s.s ?? s.start
            );
            const end = Number(
                s.end_s ?? s.e ?? s.end
            );

            if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) {
                return null;
            }

            return {
                s: start,
                e: end,
                text: (s.text || "").trim(),
                words: Array.isArray(s.words) ? s.words : null,
            };
        })
        .filter(Boolean)
        .sort((a, b) => a.s - b.s || a.e - b.e);

    return { segments: norm, meta };
}

function renderAddressBlockFromCallData(data) {
    if (!els.dAddressWrap || !els.dAddrExtracted || !els.dAddrGeocoded || !els.dAddrMapLink) return;

    const call = data?.call || {};
    const transcript = data?.transcript || call?.transcript || {};

    const firstNonEmpty = (list) => {
        for (const v of list) {
            if (v == null) continue;
            if (typeof v === "string" && v.trim() === "") continue;
            return v;
        }
        return null;
    };

    const extractedRaw = firstNonEmpty([
        transcript.address_extracted,
        transcript.address_extracted_json,
    ]);

    const geocodedRaw = firstNonEmpty([
        transcript.address_geocoded,
        transcript.address_geocoded_json,
    ]);

    const parseMaybeJson = (addr) => {
        if (typeof addr !== "string") return addr;
        const s = addr.trim();
        if (!s) return null;
        try {
            const parsed = JSON.parse(s);
            if (parsed && typeof parsed === "object") return parsed;
        } catch {
            // fall through: treat as plain string
        }
        return s;
    };

    // LLM extracted address → "rough draft"
    const normalizeExtracted = (addr) => {
        if (!addr) return null;
        addr = parseMaybeJson(addr);

        // If backend gave a plain string, just use it
        if (typeof addr === "string") {
            const s = addr.trim();
            return s ? { text: s, mapsUrl: null } : null;
        }

        if (typeof addr !== "object") return null;

        // Prefer the LLM's own raw_text verbatim
        let text =
            addr.raw_text ||
            addr.formatted ||
            addr.full ||
            null;

        const street =
            addr.street ||
            addr.address_line1 ||
            addr.address1 ||
            addr.line1 ||
            null;

        const city =
            addr.city ||
            addr.town ||
            addr.locality ||
            null;

        const state =
            addr.state ||
            addr.region ||
            addr.province ||
            null;

        const postal =
            addr.postal_code ||
            addr.zip ||
            addr.zipcode ||
            null;

        const county = addr.county || null;
        const country = addr.country || null;

        if (!text) {
            if (street || city || state || postal) {
                // LLM extracted full-ish address
                text = [street, city, state, postal].filter(Boolean).join(", ");
            } else if (city || state || county || country) {
                // Minimal fallback
                text = [city, county, state, country].filter(Boolean).join(", ");
            }
        }

        if (!text) return null;
        return { text, mapsUrl: null }; // extracted path does NOT care about mapsUrl
    };

    // Geocoded address → "final product" (Google result)
    const normalizeGeocoded = (addr) => {
        if (!addr) return null;
        addr = parseMaybeJson(addr);

        if (typeof addr === "string") {
            const s = addr.trim();
            return s ? { text: s, mapsUrl: null } : null;
        }

        if (typeof addr !== "object") return null;

        let text =
            addr.formatted_address ||   // typical Google field
            addr.formatted ||
            addr.full ||
            addr.raw_text ||
            null;

        const street =
            addr.street ||
            addr.address_line1 ||
            addr.address1 ||
            addr.line1 ||
            null;

        const city =
            addr.city ||
            addr.town ||
            addr.locality ||
            null;

        const state =
            addr.state ||
            addr.region ||
            addr.province ||
            null;

        const postal =
            addr.postal_code ||
            addr.zip ||
            addr.zipcode ||
            null;

        const countyRaw = addr.county || null;
        const country = addr.country || null;

        if (!text) {
            if (street || city || state || postal) {
                text = [street, city, state, postal].filter(Boolean).join(", ");
            } else if (countyRaw || city || state || country) {
                let county = countyRaw;
                if (county && !/county$/i.test(county)) {
                    county += " County";
                }
                // This matches your "Bradford County" style
                text = [county || city, state, country].filter(Boolean).join(", ");
            }
        }

        let extra = addr.extra || {};
        if (typeof extra === "string") {
            try { extra = JSON.parse(extra); } catch { extra = {}; }
        }

        let mapsUrl =
            addr.google_maps_url ||
            addr.maps_url ||
            extra.google_maps_url ||
            extra.maps_url ||
            null;

        const lat = addr.lat ?? addr.latitude ?? extra.lat ?? extra.latitude;
        const lng = addr.lng ?? addr.longitude ?? extra.lng ?? extra.longitude;

        if (!mapsUrl && lat != null && lng != null) {
            mapsUrl = `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(String(lat))},${encodeURIComponent(String(lng))}`;
        }

        if (!text && !mapsUrl) return null;
        return { text: text || "", mapsUrl };
    };

    const extracted = normalizeExtracted(extractedRaw);
    const geocoded = normalizeGeocoded(geocodedRaw);

    if (!extracted && !geocoded) {
        els.dAddressWrap.classList.add("d-none");
        els.dAddrExtracted.textContent = "—";
        els.dAddrGeocoded.textContent = "—";
        els.dAddrMapLink.href = "#";
        els.dAddrMapLink.classList.add("d-none");
        return;
    }

    els.dAddressWrap.classList.remove("d-none");

    // ✅ Two distinct addresses:
    // - Extracted: LLM string (raw_text)
    // - Geocoded: Google-normalized address / county
    els.dAddrExtracted.textContent = extracted?.text || "—";
    els.dAddrGeocoded.textContent = geocoded?.text || "—";

    const mapUrl = geocoded?.mapsUrl || extracted?.mapsUrl;
    if (mapUrl) {
        els.dAddrMapLink.href = mapUrl;
        els.dAddrMapLink.classList.remove("d-none");
        els.dAddrMapLink.target = "_blank";
        els.dAddrMapLink.rel = "noopener noreferrer";
    } else {
        els.dAddrMapLink.href = "#";
        els.dAddrMapLink.classList.add("d-none");
    }
}



/** Render the transcript inline header block */
function renderTranscriptInline(model) {
    transcriptModel = model;
    lastActiveWordEl = null;
    lastActiveSegEl = null;

    els.dTranscriptStatus.textContent = "";
    els.dTranscriptBody.innerHTML = "";

    if (!model || !Array.isArray(model.segments) || !model.segments.length) {
        els.dTranscriptStatus.innerHTML = `<span class="empty">No transcript available for this call.</span>`;
        return;
    }

    // Default collapsed for long texts
    els.dTranscriptBody.setAttribute("data-collapsed", "1");
    els.expandTranscriptBtn.textContent = "Expand";

    const frag = document.createDocumentFragment();

    model.segments.forEach((seg, idx) => {
        // NOTE: segments use s/e, not start/end
        const start = Number(seg.s);
        const end   = Number(seg.e);

        const segEl = document.createElement("div");
        segEl.className = "seg";
        segEl.dataset.segIdx = String(idx);   // <-- used by syncTranscriptInlineHighlight
        segEl.dataset.start = start;
        segEl.dataset.end   = end;

        // time chip (click → seek)
        const chip = document.createElement("span");
        chip.className = "timechip";
        chip.textContent = `↦ ${start.toFixed(2)}s`;
        chip.title = "Play from here";
        chip.addEventListener("click", async (e) => {
            e.stopPropagation();
            const id    = els.dPlayPauseBtn.dataset.id;
            const src   = els.dPlayPauseBtn.dataset.src;
            const label = els.dPlayPauseBtn.dataset.label || `Call ${id}`;
            const same  = String(currentPlayingId) === String(id) && window.npHasSrc?.();

            if (!same) {
                currentPlayingId = String(id);
                highlightPlayingRow();
                await window.playFrom?.({ src, title: label, start });
            } else {
                window.seekNowPlaying?.(start);
                if (window.npIsPaused?.()) await window.npPlay?.();
            }
        });
        segEl.appendChild(chip);

        // words (if provided) otherwise whole segment clickable
        if (Array.isArray(seg.words) && seg.words.length) {
            seg.words.forEach(w => {
                const wEl = document.createElement("span");
                wEl.className = "w";
                const ws = Number(w.start);
                const we = Number(w.end);
                wEl.dataset.start = Number.isFinite(ws) ? ws : start;
                wEl.dataset.end   = Number.isFinite(we) ? we : end;
                wEl.textContent   = (w.word || "").replace(/\s+/g, " ");
                wEl.addEventListener("click", async (e) => {
                    e.stopPropagation();
                    const tStart = Number(e.currentTarget.dataset.start) || start;
                    const id     = els.dPlayPauseBtn.dataset.id;
                    const src    = els.dPlayPauseBtn.dataset.src;
                    const label  = els.dPlayPauseBtn.dataset.label || `Call ${id}`;
                    const same   = String(currentPlayingId) === String(id) && window.npHasSrc?.();

                    if (!same) {
                        currentPlayingId = String(id);
                        highlightPlayingRow();
                        await window.playFrom?.({ src, title: label, start: tStart });
                    } else {
                        window.seekNowPlaying?.(tStart);
                        if (window.npIsPaused?.()) await window.npPlay?.();
                    }
                });
                segEl.appendChild(wEl);
                segEl.append(" ");
            });
        } else {
            const tEl = document.createElement("span");
            tEl.className = "w";
            tEl.dataset.start = start;
            tEl.dataset.end   = end;
            tEl.textContent   = seg.text || "";
            tEl.addEventListener("click", () => chip.click());
            segEl.appendChild(tEl);
        }

        frag.appendChild(segEl);
    });

    els.dTranscriptBody.appendChild(frag);
}

/** Keep header transcript highlight in sync with the player */
function syncTranscriptInlineHighlight(currentTime) {
    if (!els.dTranscriptBody) return;
    if (!transcriptModel || !Array.isArray(transcriptModel.segments)) return;

    const segments = transcriptModel.segments;

    // Find active segment index
    let activeIdx = -1;
    for (let i = 0; i < segments.length; i++) {
        const seg = segments[i];
        const s = (
            typeof seg.s === "number" ? seg.s :
                typeof seg.start === "number" ? seg.start :
                    null
        );
        const e = (
            typeof seg.e === "number" ? seg.e :
                typeof seg.end === "number" ? seg.end :
                    null
        );
        if (s == null || e == null) continue;
        if (currentTime >= s && currentTime < e) {
            activeIdx = i;
            break;
        }
    }

    const container = els.dTranscriptBody;

    // Clear previous
    if (lastActiveSegEl) {
        lastActiveSegEl.classList.remove("is-active", "active");
        lastActiveSegEl = null;
    }

    if (activeIdx === -1) return;

    // Highlight the current segment
    const segEl = container.querySelector(`.seg[data-seg-idx="${activeIdx}"]`);
    if (!segEl) return;

    segEl.classList.add("is-active", "active");
    lastActiveSegEl = segEl;

    // Auto-scroll
    const scroller   = container.closest(".transcript-scroll") || container;
    const parentRect = scroller.getBoundingClientRect();
    const elRect     = segEl.getBoundingClientRect();
    const relTop     = elRect.top - parentRect.top;

    if (relTop < 0 || relTop > scroller.clientHeight * 0.7) {
        scroller.scrollTop += relTop - scroller.clientHeight * 0.3;
    }
}



/* ====================================================================
   9) CALL DETAILS RENDERING & PLAYBACK SYNC
   ==================================================================== */

function ensureToneExtraHeaders() {
    const tableEl = els.dTonesTable;
    const hr = tableEl?.tHead?.rows?.[0];
    if (!hr) return;

    const labels = Array.from(hr.cells).map(th => th.textContent.trim().toLowerCase());
    const hasDTMF = labels.some(l => l.includes('dtmf'));
    const hasMDC = labels.some(l => l.includes('mdc') || l.includes('unitid') || l.includes('unit id'));

    // Find "Freq B" and insert immediately after it
    const freqBIdx = labels.findIndex(l => /freq\s*b/.test(l));
    const insertAfter = (freqBIdx >= 0 ? freqBIdx + 1 : 4); // default to index 4 (after Freq B)

    const mk = (txt) => {
        const th = document.createElement('th');
        th.scope = 'col';
        th.textContent = txt;
        return th;
    };

    if (!hasDTMF) hr.insertBefore(mk('DTMF Digit'), hr.cells[insertAfter] || null);
    // if we added DTMF, the MDC insertion index shifts by +1
    const insertAfter2 = insertAfter + (hasDTMF ? 0 : 1);
    if (!hasMDC) hr.insertBefore(mk('MDC UnitID'), hr.cells[insertAfter2] || null);
}

/**
 * Ensure tone/voice segments for a call are present in toneCache.
 * Used to drive the footer-player subtitle/metadata when modal is closed.
 */
async function ensureToneMetaLoaded(callId) {
    if (toneCache.has(String(callId))) return;
    try {
        const r = await fetch(`/api/tone-finder/calls/${callId}`);
        const js = await r.json();
        if (!js.success) return;

        const {call, tones = []} = js.result || {};
        const dur = num(call?.duration_s);

        const triggers = js.result?.triggers || [];
        const rawFiredIds = (triggers || [])
            .map(t => t.alert_trigger_id)
            .filter(id => id != null);
        const firedIdSet = new Set([
            ...rawFiredIds.map(Number).filter(Number.isFinite),
            ...rawFiredIds.map(String)
        ]);

        const safeJson = (v) => (v && typeof v === "object") ? v : (() => {
            try {
                return JSON.parse(v);
            } catch {
                return {};
            }
        })();
        const numOrNull = (v) => {
            const n = Number(v);
            return Number.isFinite(n) ? n : null;
        };

        // Tone segments
        const toneSegs = tones.map(t => {
            const jp = safeJson(t.payload || t.json_payload);
            const s = numOrNull(t.start_s) ?? numOrNull(jp.start ?? jp.start_s);
            const e = numOrNull(t.end_s) ?? numOrNull(jp.end ?? jp.end_s);
            const type = String(t.tone_type || "").toLowerCase();

            let fa = numOrNull(t.freq_a) ?? numOrNull(jp.freq_a);
            let fb = numOrNull(t.freq_b) ?? numOrNull(jp.freq_b);
            if (fa == null || fb == null) {
                if (Array.isArray(jp?.detected)) {
                    fa ??= numOrNull(jp.detected[0]);
                    fb ??= numOrNull(jp.detected[1]);
                } else {
                    fa ??= numOrNull(jp?.detected);
                }
            }
            if (s == null || e == null) return null;

            // Resolve matching trigger (like the modal)
            const fakeRow = document.createElement('tr');
            fakeRow.dataset.type = type;
            fakeRow.dataset.start = String(s);
            fakeRow.dataset.end = String(e);
            if (Number.isFinite(Number(call?.talkgroup))) fakeRow.dataset.tg = String(Number(call.talkgroup));
            if (fa != null) fakeRow.dataset.fa = String(fa);
            if (fb != null) fakeRow.dataset.fb = String(fb);
            const la = numOrNull(t.length_a_s) ?? numOrNull(jp.tone_a_length);
            const lb = numOrNull(t.length_b_s) ?? numOrNull(jp.tone_b_length);
            if (la != null) fakeRow.dataset.aLen = String(la);
            if (lb != null) fakeRow.dataset.bLen = String(lb);
            const alts = jp.alternations ?? t.alternations;
            const cyc = jp.cycles ?? t.cycles;
            if (Number.isFinite(alts)) fakeRow.dataset.alts = String(alts);
            if (Number.isFinite(cyc)) fakeRow.dataset.cycles = String(cyc);

            const {tr: matchedTrig} = resolveTriggerForToneRow(fakeRow, firedIdSet);
            const triggerId = matchedTrig?.alert_trigger_id ?? null;
            const fired = (triggerId != null) &&
                (firedIdSet.has(Number(triggerId)) || firedIdSet.has(String(triggerId)));

            return {s, e, type, fa, fb, triggerId, fired};
        }).filter(Boolean).sort((a, b) => a.s - b.s || a.e - b.e);

        // Voice segments (prefer server-provided VAD)
        const toneWindows = toneSegs.map(w => ({s: w.s, e: w.e}));
        const apiVoice = js.result?.vad_segments || js.result?.voice || null;

        const voiceSegs = getVoiceSegments({
            duration: dur,
            toneWindows,
            apiVoice,
            minLen: 0.05
        });

        const segs = [...toneSegs, ...voiceSegs].sort((a, b) => a.s - b.s || a.e - b.e);
        toneCache.set(String(callId), segs);
    } catch {
    }
}

/**
 * Start playback from a table row or modal, update highlights and metadata.
 * @param {string|number} callId
 * @param {string} src
 * @param {string} label
 */
async function startPlayback(callId, src, label) {
    currentPlayingId = String(callId);

    let finalSrc = (src || "").trim();
    if (!finalSrc) {
        const meta = callMeta.get(String(callId));
        if (meta?.src) finalSrc = meta.src;
    }

    if (!finalSrc) {
        console.warn("No audio source for call", callId);
        showAlert("No audio file available for this call.", "warning");
        return;
    }

    // Wait for audio player to initialize if needed
    if (!window.nowPlaying) {
        console.warn("Audio player not yet initialized, waiting...");
        setTimeout(() => startPlayback(callId, finalSrc, label), 100);
        return;
    }

    window.nowPlaying({
        src: finalSrc,
        title: label || `Call ${callId}`,
        autoplay: true
    });

    highlightPlayingRow();

    // Preload segments to set a subtitle even if the call modal isn't open
    ensureToneMetaLoaded(callId).then(() => {
        const rows = toneCache.get(String(callId)) || (typeof modalSegments !== "undefined" ? modalSegments : []);
        if (!rows || !rows.length) return;

        const firstTone = rows.find(r => r.type && r.type !== "voice");
        const seg = firstTone || rows[0];

        const sub = (seg.type === "voice") ? "Voice segment" : formatToneLabel(seg.type, seg.fa, seg.fb);
        const metaClass = (seg.type === "voice") ? "voice-meta" : (seg.fired ? "trigger-meta" : "tone-meta");
        window.updateNowPlayingMeta?.({subtitle: sub, metaClass});
    });
}

/** Add/remove “playing” class on the current row in the DataTable. */
function highlightPlayingRow() {
    if (!table) return;
    table.rows().every(function () {
        const data = this.data();
        const tr = this.node();
        if (!data || !tr) return;
        const id = String(data[COL.ID]);
        tr.classList.toggle("playing", currentPlayingId && id === currentPlayingId);
    });
}

/** If the currently-playing call was deleted, reset the footer player UI. */
function stopIfDeleted(deletedId) {
    if (String(deletedId) !== String(currentPlayingId)) return;
    window.hideNowPlaying?.();
    currentPlayingId = null;
    highlightPlayingRow();
}

/** Human readable tone label for subtitle/tooltips. */
function formatToneLabel(type, fa, fb) {
    const hz = (x) => Number.isFinite(x) ? `${x.toFixed(1)} Hz` : "";
    switch (type) {
        case "two_tone":
            return `Two-tone: ${hz(fa)} → ${hz(fb)}`.trim();
        case "hi_low":
            return `Hi/Low: ${hz(fa)} / ${hz(fb)}`.trim();
        case "pulsed":
            return `Pulsed ~ ${hz(fa)}`.trim();
        case "long":
        case "long_tone":
            return `Long: ${hz(fa)}`.trim();
        default:
            return hz(fa);
    }
}

/**
 * Sync highlighted tone row (and trigger list) to the footer player time.
 * @param {number} cur currentTime (sec)
 */
function syncModalToneHighlight(cur) {
    if (!currentPlayingId) return;
    const sameCall = String(currentPlayingId) === String(modalCallId);

    const rows = (sameCall && modalSegments.length)
        ? modalSegments
        : (toneCache.get(String(currentPlayingId)) || []);

    if (!rows.length) return;

    // Find active segment
    let active = null;
    for (const r of rows) {
        if (cur >= r.s && cur < r.e) {
            active = r;
            break;
        }
    }

    // Highlight in modal (if open & same call)
    if (els.callModal.classList.contains("show") && sameCall) {
        for (const seg of modalSegments) seg.tr.classList.toggle("active-tone", active && seg === active);
        if (active && lastActiveRow !== active.tr) {
            lastActiveRow = active.tr;
            active.tr.scrollIntoView({block: "nearest", behavior: "smooth"});
        }
    }

    if (active) {
        const toneLabel = (active.type === "voice")
            ? "Voice segment"
            : formatToneLabel(active.type, active.fa, active.fb);

        const trigLabel = (active.triggerId != null) ? getTriggerLabelById(active.triggerId) : null;

        const sub = trigLabel ? `${toneLabel} — Trigger: ${trigLabel}` : toneLabel;
        const metaClass = (active.type === "voice")
            ? "voice-meta"
            : (active.fired ? "trigger-meta" : "tone-meta");
        window.updateNowPlayingMeta?.({subtitle: sub, metaClass});
    }

    // Sync trigger list highlight
    const tid = (active && active.triggerId != null) ? String(active.triggerId) : null;
    if (tid !== lastActiveTriggerId) {
        modalTriggerLis.forEach(li => li.classList.remove('active'));
        if (tid && modalTriggerLis.has(tid)) {
            const li = modalTriggerLis.get(tid);
            li.classList.add('active');
            li.scrollIntoView({block: 'nearest'});
        }
        lastActiveTriggerId = tid;
    }
}

/** Sync modal transport buttons to footer-player state. */
function syncModalTransportUI() {
    if (!els.dPlayPauseBtn) return;
    const sameCall = String(currentPlayingId) === String(modalCallId);
    const st = window.npGetState?.() || {hasSrc: false, paused: true};

    const canControl = sameCall && st.hasSrc;
    els.dStopBtn.disabled = !canControl;

    const icon = els.dPlayPauseBtn.querySelector("i");
    if (canControl && !st.paused) {
        els.dPlayPauseBtn.title = "Pause";
        icon.className = "bi bi-pause-fill";
    } else {
        els.dPlayPauseBtn.title = "Play";
        icon.className = "bi bi-play-fill";
    }
}

/**
 * Render call header + tones/voice lists into the modal.
 * @param {object} data Payload from GET /api/tone-finder/calls/<id>
 */
function renderCallDetails(data) {
    const {call, tones = [], triggers = []} = data;

    // call detail data loaded
    const audioSrc = resolveCallAudioUrl(call);

    // Header/summary
    els.dPlayPauseBtn.dataset.systemId = els.sysSel.value || "";
    els.dPlayPauseBtn.dataset.radioSystemId = call.radio_system_id || "";
    els.dCallId.textContent = call.call_id;
    els.dTG.textContent = call.talkgroup ?? "—";
    els.dPlayPauseBtn.dataset.id = call.call_id;
    els.dPlayPauseBtn.dataset.src = audioSrc;
    els.dPlayPauseBtn.dataset.label = call.talkgroup ?? `Call ${call.call_id}`;
    els.dStart.textContent = new Date(call.start_epoch * 1000).toLocaleString();
    els.dDur.textContent = Number(call.duration_s ?? 0).toFixed(1) + " s";
    els.dMerged.textContent = call.merged_from_stub ? "Yes" : "No";
    els.dTranscriptStatus.textContent = "Loading…";
    els.dTranscriptBody.innerHTML = "";

    renderAddressBlockFromCallData(data);
    renderIncidentBlockFromCallData(data);

    modalSegments = [];
    modalCallId = String(call.call_id);
    lastActiveTriggerId = null;
    lastActiveRow = null;

    // ---- TRIGGERS (sorted earliest → latest) ----
    els.dTrigList.innerHTML = "";
    modalTriggerLis = new Map();
    modalTriggerNames = new Map();

    // Prefer transcript bundled with this call payload
    const tModel = buildTranscriptModelFromCallData(data);
    if (tModel && (tModel.segments?.length || (tModel.meta?.text_full||"").trim())) {
        renderTranscriptInline(tModel);
    } else {
        els.dTranscriptStatus.innerHTML = `<span class="empty">No transcript available for this call.</span>`;
    }

    const triggersSorted = triggers.slice().sort((a, b) => {
        const ta = Number.isFinite(a.fired_at_epoch_s) ? a.fired_at_epoch_s : 0;
        const tb = Number.isFinite(b.fired_at_epoch_s) ? b.fired_at_epoch_s : 0;
        return ta - tb;
    });

    if (triggersSorted.length) {
        triggersSorted.forEach(t => {
            const id = t.alert_trigger_id;
            const name = t.alert_trigger_name ?? (id != null ? `Trigger ${id}` : "Trigger");

            const li = document.createElement("li");
            li.className = "list-group-item d-flex justify-content-between align-items-center";
            li.innerHTML =
                `<span>${name}</span>
         <span class="badge bg-danger-subtle text-danger fw-normal">
           ${new Date((t.fired_at_epoch_s ?? 0) * 1000).toLocaleTimeString()}
         </span>`;

            els.dTrigList.appendChild(li);

            if (id != null) {
                modalTriggerLis.set(String(id), li);
                modalTriggerNames.set(String(id), name);
            }
        });
        els.dTrigList.classList.remove("d-none");
        els.dNoTrig.classList.add("d-none");
    } else {
        els.dTrigList.classList.add("d-none");
        els.dNoTrig.classList.remove("d-none");
    }

    // Helpers
    const fmtNum = (n, d = 2) => (Number.isFinite(n) ? Number(n).toFixed(d) : "—");
    const fmtHz = (n) => (Number.isFinite(n) ? `${Number(n).toFixed(1)} Hz` : "—");
    const safeJson = (v) => {
        if (!v) return {};
        if (typeof v === "object") return v;
        try {
            return JSON.parse(v);
        } catch {
            return {};
        }
    };
    const getStart = (t) => {
        if (Number.isFinite(t.start_s)) return t.start_s;
        const jp = safeJson(t.payload || t.json_payload);
        const s = Number(jp.start ?? jp.start_s);
        return Number.isFinite(s) ? s : Infinity;
    };
    const getEnd = (t) => {
        if (Number.isFinite(t.end_s)) return t.end_s;
        const jp = safeJson(t.payload || t.json_payload);
        const e = Number(jp.end ?? jp.end_s);
        return Number.isFinite(e) ? e : Infinity;
    };
    const calcLen = (t) => {
        const la = num(t.length_a_s);
        const lb = num(t.length_b_s);
        if (la != null && lb != null) return la + lb;
        if (la != null) return la;
        const s = num(t.start_s);
        const e = num(t.end_s);
        if (s != null && e != null) return Math.max(0, e - s);
        return null;
    };

    const tbody = els.dTonesTable.querySelector("tbody");
    tbody.innerHTML = "";

    ensureToneExtraHeaders();

    // ---- TONES (collect → append) ----
    const tonesSorted = tones.slice().sort((a, b) => {
        const sa = getStart(a), sb = getStart(b);
        if (sa !== sb) return sa - sb;
        return getEnd(a) - getEnd(b);
    });

    const rawFiredIds = (triggersSorted || [])
        .map(t => t.alert_trigger_id)
        .filter(id => id != null);
    const firedIdSet = new Set([
        ...rawFiredIds.map(Number).filter(Number.isFinite),
        ...rawFiredIds.map(String)
    ]);

    const toneWindows = [];
    const toneSegs = [];

    tonesSorted.forEach(t => {
        const jp = safeJson(t.payload || t.json_payload);
        const type = String(t.tone_type || "").toLowerCase();

        const setId = t.tone_set_id ?? "—";
        const fa = num(t.freq_a) ?? num(jp.freq_a);
        const fb = num(t.freq_b) ?? num(jp.freq_b);
        const start = num(t.start_s) ?? num(jp.start ?? jp.start_s);
        const end = num(t.end_s) ?? num(jp.end ?? jp.end_s);
        const len = calcLen(t);
        if (start == null || end == null) return;

        // subtype formatting
        let aLen = "—", bLen = "—", alts = "—", cycles = "—", onMs = "—", offMs = "—";
        if (type === "two_tone") {
            const la = num(t.length_a_s) ?? num(jp.tone_a_length);
            const lb = num(t.length_b_s) ?? num(jp.tone_b_length);
            aLen = fmtNum(la, 2);
            bLen = fmtNum(lb, 2);
        } else if (type === "hi_low") {
            const a = jp.alternations ?? t.alternations;
            alts = Number.isFinite(a) ? String(a) : "—";
        } else if (type === "pulsed") {
            const cyc = jp.cycles ?? t.cycles;
            const on = jp.on_ms;
            const off = jp.off_ms;
            cycles = Number.isFinite(cyc) ? String(cyc) : "—";
            onMs = Number.isFinite(on) ? String(on) : "—";
            offMs = Number.isFinite(off) ? String(off) : "—";
        }

        // Build a row element and set datasets for downstream matching/seeking
        const tr = document.createElement("tr");
        tr.classList.add("segment-row", "tone-row");
        tr.dataset.start = String(start);
        tr.dataset.end = String(end);
        tr.dataset.type = type;

        let faNum = fa, fbNum = fb;
        if (faNum == null || fbNum == null) {
            if (Array.isArray(jp?.detected)) {
                faNum ??= num(jp.detected[0]);
                fbNum ??= num(jp.detected[1]);
            } else {
                faNum ??= num(jp?.detected);
            }
        }
        tr.dataset.fa = faNum != null ? String(faNum) : "";
        tr.dataset.fb = fbNum != null ? String(fbNum) : "";

        tr.dataset.len = len != null ? String(len) : "";
        tr.dataset.aLen = aLen !== "—" ? aLen : "";
        tr.dataset.bLen = bLen !== "—" ? bLen : "";
        tr.dataset.alts = alts !== "—" ? alts : "";
        tr.dataset.cycles = cycles !== "—" ? cycles : "";

        const tgNumeric = Number(call.talkgroup);
        if (Number.isFinite(tgNumeric)) tr.dataset.tg = String(tgNumeric);

        // Baked trigger hints (optional, multiple fallbacks)
        const bakedIdFromArray =
            Array.isArray(t.trigger_ids) && t.trigger_ids.length ? t.trigger_ids[0] : null;

        const bakedId =
            bakedIdFromArray ??
            t.matches_trigger_id ??
            t.alert_trigger_id ??
            t.configured_trigger_id ??
            (t.alert_trigger && t.alert_trigger.alert_trigger_id) ??
            null;

        const bakedName =
            t.alert_trigger_name ??
            (t.alert_trigger && t.alert_trigger.alert_trigger_name) ??
            null;

        if (bakedId != null) tr.dataset.matchedTriggerId = String(bakedId);
        if (bakedName) tr.dataset.matchedTriggerName = bakedName;

        const {tr: matchedTrig} = resolveTriggerForToneRow(tr, firedIdSet);
        const matchedFired = Boolean(t.matches_trigger ?? t.matched_trigger ?? t.trigger_fired ?? 0);
        if (matchedFired) tr.classList.add('trigger-row'); else tr.classList.remove('trigger-row');

        const matchedTriggerId = matchedTrig?.alert_trigger_id ?? null;
        const matchedTriggerName = matchedTrig?.alert_trigger_name ?? null;
        if (matchedTriggerId != null) tr.dataset.resolvedTriggerId = String(matchedTriggerId);

// ---- derive per-type flags/derived values BEFORE using them
        const isDTMF = (type === "dtmf");
        const isMDC = (type === "mdc");

// Pull DTMF digit or MDC unitID from payload
        const dtmfDigit = isDTMF ? (jp?.digit ?? t?.payload?.digit ?? "—") : "—";
        const mdcUnitID = isMDC ? (jp?.unitID ?? jp?.unit_id ?? t?.payload?.unitID ?? t?.payload?.unit_id ?? "—") : "—";

// Seed datasets used by “attach to existing trigger” flow
        if (isDTMF && dtmfDigit !== "—") tr.dataset.dtmf = String(dtmfDigit);
        if (isMDC && mdcUnitID !== "—") tr.dataset.mdc = String(mdcUnitID);
// For pulsed, capture on/off if present so we can attach exact rule later
        if (type === "pulsed") {
            const on = jp.on_ms, off = jp.off_ms;
            if (Number.isFinite(on)) tr.dataset.onMs = String(on);
            if (Number.isFinite(off)) tr.dataset.offMs = String(off);
        }

        // Cells (hide bogus Hz for DTMF/MDC)
        const faCell = (isDTMF || isMDC) ? "—" : fmtHz(fa);
        const fbCell = (type === "two_tone" || type === "hi_low") ? fmtHz(fb) : "—";

        // Status / tooltip / editor href
        const statusParts = [];
        if (matchedFired) statusParts.push(`<span title="Trigger fired">🚨</span>`);
        statusParts.push(`<span title="Tone detected">🔊</span>`);
        const statusMarkup = statusParts.join(" ");

        const sysId = getTriggerSystemId();
        const editControl = buildEditControlForRow(tr, sysId, firedIdSet);

        // Label used in “Add trigger” tooltip
        const toneLabelForBtn =
            (isDTMF && dtmfDigit !== "—") ? `DTMF ${dtmfDigit}` :
                (isMDC && mdcUnitID !== "—") ? `MDC ${mdcUnitID}` :
                    formatToneLabel(type, faNum, fbNum);

        const addControl = (isMDC
            ? `<span class="text-muted small" data-bs-toggle="tooltip" data-bs-title="MDC can’t be used for triggers">Not supported</span>`
            : `<button type="button" class="btn btn-sm btn-primary js-create-trigger"
       data-bs-toggle="tooltip" data-bs-placement="top" data-bs-html="true"
       data-bs-title="Add trigger for ${esc(toneLabelForBtn)}${Number.isFinite(Number(call.talkgroup)) ? ` (TG ${call.talkgroup})` : ""}"
       aria-label="Add trigger">
       <i class="bi bi-plus-circle"></i>
     </button>`);

        // Action: edit if matched, else show “create/attach” — but block MDC
        const actionMarkup = `
             <div class="btn-group" role="group">
               ${addControl}
               ${editControl || ""}
             </div>`;

        // Finally render the row
        tr.innerHTML = `
          <td>${type}</td>
          <td>${setId}</td>
          <td>${faCell}</td>
          <td>${fbCell}</td>
          <td>${dtmfDigit}</td>
          <td>${mdcUnitID}</td>
          <td>${fmtNum(start, 2)}</td>
          <td>${fmtNum(end, 2)}</td>
          <td>${fmtNum(len, 2)}</td>
          <td>${aLen}</td>
          <td>${bLen}</td>
          <td>${alts}</td>
          <td>${cycles}</td>
          <td>${onMs}</td>
          <td>${offMs}</td>
          <td class="align-middle">${statusMarkup}</td>
          <td class="align-middle">${actionMarkup}</td>
        `;

        toneSegs.push({
            tr, s: start, e: end, type,
            fa: faNum ?? null, fb: fbNum ?? null,
            isTone: true,
            triggerId: (matchedTriggerId != null ? Number(matchedTriggerId) : null),
            fired: matchedFired === true
        });
        toneWindows.push({s: start, e: end});
    });

    // ---- VOICE: prefer backend VAD segments, else tone-gaps ----
    const voiceSegs = [];
    const dur = num(call?.duration_s);

    const apiVoice =
        data.voice_segments ||
        data.vad_segments ||
        data.voice?.segments ||
        data.voice || null;

    const MIN_VOICE = 0.5;
    const pushVoiceRow = (vs, ve) => {
        const len = Math.max(0, ve - vs);
        if (len < MIN_VOICE) return;

        const tr = document.createElement("tr");
        tr.classList.add("segment-row", "voice-row");

        // Keep datasets consistent with tone rows
        tr.dataset.start = String(vs);
        tr.dataset.end = String(ve);
        tr.dataset.type = "voice";
        tr.dataset.fa = "";   // voice has no frequencies
        tr.dataset.fb = "";

        // Column order (17 cols total):
        // Type, Set ID, Freq A, Freq B, DTMF, MDC, Start, End, Len,
        // A Len, B Len, Alternations, Cycles, ON ms, OFF ms, Trigger?, Action
        tr.innerHTML = `
            <td>voice</td>
            <td>—</td>
            <td>—</td>
            <td>—</td>
            <td>—</td>  <!-- DTMF Digit -->
            <td>—</td>  <!-- MDC UnitID  -->
            <td>${fmtNum(vs, 2)}</td>
            <td>${fmtNum(ve, 2)}</td>
            <td>${fmtNum(len, 2)}</td>
            <td>—</td>
            <td>—</td>
            <td>—</td>
            <td>—</td>
            <td>—</td>
            <td>—</td>
            <td></td>
            <td></td>
          `;

        voiceSegs.push({tr, s: vs, e: ve, type: "voice", fa: null, fb: null, isTone: false});
    };


    if (Array.isArray(apiVoice)) {
        for (const seg of apiVoice) {
            const s = Number(seg.start_s ?? seg.start ?? seg.s);
            const e = Number(seg.end_s ?? seg.end ?? seg.e);
            if (Number.isFinite(s) && Number.isFinite(e)) pushVoiceRow(s, e);
        }
    } else if (Array.isArray(apiVoice?.segments)) {
        for (const seg of apiVoice.segments) {
            const s = Number(seg.start_s ?? seg.start ?? seg.s);
            const e = Number(seg.end_s ?? seg.end ?? seg.e);
            if (Number.isFinite(s) && Number.isFinite(e)) pushVoiceRow(s, e);
        }
    } else if (Number.isFinite(dur)) {
        const merged = [];
        toneWindows.sort((a, b) => a.s - b.s || a.e - b.e);
        for (const w of toneWindows) {
            if (!merged.length || w.s > merged[merged.length - 1].e) merged.push({...w});
            else merged[merged.length - 1].e = Math.max(merged[merged.length - 1].e, w.e);
        }
        let t0 = 0.0;
        for (const w of merged) {
            if (w.s > t0) pushVoiceRow(t0, w.s);
            t0 = Math.max(t0, w.e);
        }
        if (dur > t0) pushVoiceRow(t0, dur);
    }

    // Append to table (sorted)
    const segments = [...toneSegs, ...voiceSegs]
        .sort((a, b) => (a.s - b.s) || (a.e - b.e) || ((a.isTone === b.isTone) ? 0 : (a.isTone ? -1 : 1)));

    segments.forEach(seg => tbody.appendChild(seg.tr));
    modalSegments = segments;

    // Cache for footer player
    toneCache.set(
        String(call.call_id),
        segments.map(({s, e, type, fa, fb, triggerId, fired}) => ({s, e, type, fa, fb, triggerId, fired}))
    );

    // Hide per-type columns that are all empty (“—”)
    autoHideEmptyColumns(els.dTonesTable, [4, 5, 9, 10, 11, 12, 13, 14]);

    syncModalTransportUI();
}

/**
 * Hide (th/td) columns when all cells in that column are “—” or empty.
 * @param {HTMLTableElement} tableEl
 * @param {number[]} colIdxs 0-based column indices
 */
function autoHideEmptyColumns(tableEl, colIdxs) {
    if (!tableEl) return;
    const theadCells = Array.from(tableEl.tHead?.rows?.[0]?.cells ?? []);
    const rows = Array.from(tableEl.tBodies?.[0]?.rows ?? []);

    colIdxs.forEach(idx => {
        if (!theadCells[idx]) return;
        const anyData = rows.some(r => {
            const c = r.cells[idx];
            if (!c) return false;
            const txt = c.textContent.trim();
            return txt !== "—" && txt !== "";
        });
        const display = anyData ? "" : "none";
        theadCells[idx].style.display = display;
        rows.forEach(r => {
            if (r.cells[idx]) r.cells[idx].style.display = display;
        });
    });
}

/* ====================================================================
   10) ADD/ATTACH TRIGGER HELPERS
   ==================================================================== */

/** Lazy-create the small “Add or Attach” chooser modal. */
function ensureAddOrAttachModal() {
    if (document.getElementById('addOrAttachModal')) return;

    const html = `
  <div class="modal fade" id="addOrAttachModal" tabindex="-1" aria-hidden="true">
    <div class="modal-dialog">
      <form class="modal-content" id="addOrAttachForm">
        <div class="modal-header">
          <h5 class="modal-title">Use this tone…</h5>
          <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
        </div>

        <div class="modal-body">
          <div class="alert alert-secondary small mb-3">
            <div id="aoaTonePreview" class="fw-semibold"></div>
            <div class="text-muted">Choose to create a new trigger or add this tone as another rule to an existing trigger.</div>
          </div>

          <div class="form-check mb-2">
            <input class="form-check-input" type="radio" name="aoaMode" id="aoaNew" value="new" checked>
            <label class="form-check-label" for="aoaNew">Create a <strong>new</strong> trigger</label>
          </div>

          <div class="form-check mb-2">
            <input class="form-check-input" type="radio" name="aoaMode" id="aoaExisting" value="existing">
            <label class="form-check-label" for="aoaExisting">Add to an <strong>existing</strong> trigger</label>
          </div>

          <div class="mt-2 ps-4" id="aoaExistingWrap" style="display:none;">
            <label for="aoaExistingSelect" class="form-label">Target trigger</label>
            <select id="aoaExistingSelect" class="form-select form-select-sm">
              <option value="">Select trigger…</option>
            </select>
            <div class="form-text">List is filtered to this system. The closest matching trigger (if any) is preselected.</div>
          </div>
        </div>

        <div class="modal-footer">
          <button type="button" class="btn btn-outline-secondary" data-bs-dismiss="modal">Cancel</button>
          <button type="submit" id="aoaSubmitBtn" class="btn btn-primary">
            <i class="bi bi-check2"></i> Continue
          </button>
        </div>
      </form>
    </div>
  </div>`;
    document.body.insertAdjacentHTML('beforeend', html);

    Object.assign(els, {
        aoaModal: document.getElementById('addOrAttachModal'),
        aoaForm: document.getElementById('addOrAttachForm'),
        aoaNew: document.getElementById('aoaNew'),
        aoaExisting: document.getElementById('aoaExisting'),
        aoaWrap: document.getElementById('aoaExistingWrap'),
        aoaSelect: document.getElementById('aoaExistingSelect'),
        aoaSubmit: document.getElementById('aoaSubmitBtn'),
        aoaTonePreview: document.getElementById('aoaTonePreview'),
    });

    // Toggle select visibility
    const toggle = () => {
        els.aoaWrap.style.display = els.aoaExisting.checked ? '' : 'none';
    };
    els.aoaNew.addEventListener('change', toggle);
    els.aoaExisting.addEventListener('change', toggle);
    // Submit handler: branch to new vs existing
    els.aoaForm.addEventListener('submit', async (ev) => {
        ev.preventDefault();
        if (!activeTriggerRow || !pendingTriggerPayload) return;

        const sysId = getTriggerSystemId();
        if (!sysId) {
            alert("Select a radio system first.");
            return;
        }

        if (els.aoaNew.checked) {
            // Open your existing Create Trigger modal — prefilled exactly as before
            bootstrap.Modal.getInstance(els.aoaModal).hide();
            openCreateTriggerFromRow(activeTriggerRow);
            return;
        }

        // Attach to existing trigger
        const trg = Number(els.aoaSelect.value);
        if (!Number.isFinite(trg)) {
            alert("Choose a target trigger.");
            return;
        }

        els.aoaSubmit.disabled = true;
        try {
            await attachRuleToExistingTrigger({sysId, triggerId: trg, row: activeTriggerRow});
            bootstrap.Modal.getInstance(els.aoaModal).hide();
        } catch (e) {
            console.error(e);
            showAlert(e.message || "Failed to add rule to trigger.", "danger");
        } finally {
            els.aoaSubmit.disabled = false;
        }
    });

    // Rebuild select on-open
    els.aoaModal.addEventListener('show.bs.modal', () => {
        populateExistingTriggerSelect(activeTriggerRow);
    });

    ensureAddOrAttachModal();
}

/** Open the chooser for a given tone-row. */
function openAddOrAttachForRow(row) {
    if ((row.dataset.type || "").toLowerCase() === "mdc") {
        alert("MDC can’t be used for triggers (not supported).");
        return;
    }
    activeTriggerRow = row;
    pendingTriggerPayload = buildTriggerPayloadForRow(row);
    if (!pendingTriggerPayload) {
        alert("Unsupported or invalid tone row.");
        return;
    }

    // Preview label
    const type = row.dataset.type;
    const fa = Number(row.dataset.fa);
    const fb = Number(row.dataset.fb);
    els.aoaTonePreview.textContent = (type === "voice")
        ? "Voice segment"
        : formatToneLabel(type, fa, fb);

    els.aoaNew.checked = true;
    els.aoaExisting.checked = false;
    els.aoaWrap.style.display = 'none';

    bootstrap.Modal.getOrCreateInstance(els.aoaModal).show();
}

/** Build a compact "rule" object for the multi-set format from a tone row. */
/**
 * Build a compact rule for the multi-set endpoint.
 * Returns null if required values are missing.
 */
function buildRuleFromRow(row) {
    const type = (row.dataset.type || "").toLowerCase();
    const n = (x) => {
        const f = Number(x);
        return Number.isFinite(f) ? f : undefined;
    };
    const minus = (v, d, floor = 0) => {
        const f = Number(v);
        return Number.isFinite(f) ? Math.max(floor, f - d) : undefined;
    };

    switch (type) {
        case "two_tone": {
            const A = n(row.dataset.fa);
            const B = n(row.dataset.fb);
            if (A == null || B == null) return null;
            const aLen = minus(row.dataset.aLen, 0.2) ?? 0.8;
            const bLen = minus(row.dataset.bLen, 0.5) ?? 2.8;
            return {
                type_key: "two_tone",
                rule: {freq_a_hz: A, min_len_a_s: aLen, freq_b_hz: B, min_len_b_s: bLen}
            };
        }

        case "hi_low": {
            const A = n(row.dataset.fa);
            const B = n(row.dataset.fb);
            if (A == null || B == null) return null;
            const alts = n(row.dataset.alts);
            const minAlts = (alts != null) ? Math.max(1, alts - 2) : 4;
            return {
                type_key: "hi_low",
                rule: {hi_freq_a_hz: A, hi_freq_b_hz: B, min_alternations: minAlts}
            };
        }

        case "pulsed": {
            const F = n(row.dataset.fa);
            if (F == null) return null;
            const c = n(row.dataset.cycles);
            const minCycles = (c != null) ? Math.max(1, c - 2) : 6;
            const rule = {center_hz: F, min_cycles: minCycles};
            const on = n(row.dataset.onMs);
            const off = n(row.dataset.offMs);
            if (on != null) rule.min_on_ms = on;
            if (off != null) rule.min_off_ms = off;
            return {type_key: "pulsed", rule};
        }

        case "long":
        case "long_tone": {
            const F = n(row.dataset.fa);
            if (F == null) return null;
            const L = n(row.dataset.len);
            const minLen = (L != null) ? Math.max(0, L - 0.5) : 3.8;
            return {type_key: "long_tone", rule: {freq_hz: F, min_len_s: minLen}};
        }

        case "dtmf": {
            const seq = sanitizeDtmf(row.dataset.dtmf);
            if (!seq) return null;
            return {type_key: "dtmf", rule: {sequence: seq}};
        }

        case "mdc":
            // unchanged (not part of SQL-backed sets, but keep for future UI)
            return {
                type_key: "mdc",
                rule: {unit_id: (row.dataset.mdc || "").toString().trim() || undefined}
            };

        default:
            return null;
    }
}

/**
 * POST a new rule to an existing trigger (multi-set).
 * Expected backend: POST /api/systems/:sysId/triggers/:triggerId/rules
 * Body: { type_key: "two_tone"|..., rule: {...} }
 * Falls back to opening the editor if endpoint isn’t present.
 */
async function attachRuleToExistingTrigger({sysId, triggerId, row}) {
    const payload = buildRuleFromRow(row);
    if (!payload) throw new Error("Unsupported tone type.");

    const url = `/api/systems/${encodeURIComponent(sysId)}/triggers/${encodeURIComponent(triggerId)}/rules`;
    const r = await fetch(url, {
        method: "POST",
        headers: {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-CSRFToken": getCsrf(),
        },
        body: JSON.stringify(payload)
    });

    let js = {};
    try {
        js = await r.json();
    } catch {
    }

    // If the endpoint doesn’t exist yet — bounce to editor with a seed param
    if (r.status === 404 || r.status === 405) {
        const seed = btoa(encodeURIComponent(JSON.stringify(payload)).replace(/%([0-9A-F]{2})/g, (_, p1) => String.fromCharCode('0x' + p1)));
        const href = `/dashboard/triggers?system=${encodeURIComponent(sysId)}&trigger=${encodeURIComponent(triggerId)}&seed_rule=${encodeURIComponent(seed)}`;
        window.location.href = href;
        return;
    }

    if (!r.ok || !js.success) {
        throw new Error(js.message || "API error while adding rule.");
    }

    // Success: convert the row’s Action cell into an “Edit” link and toast
    try {
        const cell = row.querySelector("td:last-child");
        const name = (systemTriggerIndex?.byId?.get(Number(triggerId))?.alert_trigger_name) || `#${triggerId}`;
        const editorHref = `/dashboard/triggers?system=${encodeURIComponent(sysId)}&trigger=${encodeURIComponent(triggerId)}`;
        const editOnly = `
                                    <a href="${editorHref}" class="btn btn-sm btn-secondary js-edit-trigger-link"
                                       data-bs-toggle="tooltip" data-bs-placement="top" data-bs-title="Edit" aria-label="Edit">
                                      <i class="bi bi-pencil-square"></i>
                                    </a>`;
        const existingAdd = cell.querySelector('.js-create-trigger')?.outerHTML || "";
        cell.innerHTML = `<div class="btn-group" role="group">${existingAdd}${editOnly}</div>`;
        cell.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(el => {
            bootstrap.Tooltip.getOrCreateInstance(el, {container: els.callModal});
        });
    } catch {
    }

    // Update local index and UI
    await refreshSystemTriggers().catch(() => {
    });
    showAlert("Added rule to trigger.", "success");
}

/** Fill the existing-triggers <select>, prefer a fuzzy/exact match preselected. */
function populateExistingTriggerSelect(row) {
    const sysId = getTriggerSystemId();
    els.aoaSelect.innerHTML = `<option value="">Select trigger…</option>`;

    // Prefer sorted by name, then id
    const list = (systemTriggerIndex?.raw || []).slice().sort((a, b) => {
        const an = (a.alert_trigger_name || "").toLowerCase();
        const bn = (b.alert_trigger_name || "").toLowerCase();
        return an.localeCompare(bn) || (Number(a.alert_trigger_id) - Number(b.alert_trigger_id));
    });

    for (const tr of list) {
        const id = tr.alert_trigger_id;
        const name = tr.alert_trigger_name || `Trigger ${id}`;
        const tg = tr.alert_trigger_talkgroup;
        const opt = document.createElement('option');
        opt.value = String(id);
        opt.textContent = `${name} (ID ${id})${(tg != null && tg !== "") ? ` — TG ${tg}` : ""}`;
        els.aoaSelect.appendChild(opt);
    }

    // Preselect best match for convenience
    try {
        const {tr: best} = resolveTriggerForToneRow(row, null);
        if (best?.alert_trigger_id) els.aoaSelect.value = String(best.alert_trigger_id);
    } catch {
    }
}

/** Factor existing “create new trigger from row” into a function we can call. */
function openCreateTriggerFromRow(row) {
    const built = buildTriggerPayloadForRow(row);
    if (!built) {
        alert("Unsupported or invalid tone row.");
        return;
    }

    activeTriggerRow = row;
    pendingTriggerPayload = built;

    // (This block is your existing prefill; kept verbatim.)
    els.trigTitle.textContent = "Add Trigger";
    els.trigSystemId.value = getTriggerSystemId();
    els.trigId.value = "";
    els.trigTalkgroup.value = "";
    const hintTg = Number.isFinite(Number(els.dTG.textContent)) ? els.dTG.textContent : "";
    els.trigTalkgroup.placeholder = hintTg ? `e.g., ${hintTg} (optional)` : "Any talkgroup";
    els.trigName.value = defaultTriggerNameFromRow(row);
    els.trigEnabled.value = "1";
    els.trigType.value = "AND";

    const callModal = bootstrap.Modal.getInstance(els.callModal) || bootstrap.Modal.getOrCreateInstance(els.callModal);
    const openTriggerModal = () => {
        const trig = bootstrap.Modal.getOrCreateInstance(els.triggerModal);
        els.triggerModal.addEventListener("shown.bs.modal", () => {
            els.trigName?.focus();
            els.trigName?.select();
        }, {once: true});
        trig.show();
    };

    reopenDetailsAfterTrigger = true;
    if (els.callModal.classList.contains("show")) {
        els.callModal.addEventListener("hidden.bs.modal", openTriggerModal, {once: true});
        callModal.hide();
    } else {
        openTriggerModal();
    }
}

function buildEditControlForRow(row, sysId, firedIdSet = null) {
    // Gather matches: fired ones first, then the rest (deduped)
    const seen = new Set();
    const addUniq = (arr, m) => {
        const id = Number(m.tr.alert_trigger_id);
        if (!seen.has(id)) {
            seen.add(id);
            arr.push(m);
        }
    };

    const matches = [];
    if (firedIdSet && firedIdSet.size) {
        const restrict = new Set([...firedIdSet].map(Number).filter(Number.isFinite));
        (getAllMatchingTriggersForRow(row, {restrictToIds: restrict}) || []).forEach(m => addUniq(matches, m));
    }
    (getAllMatchingTriggersForRow(row) || []).forEach(m => addUniq(matches, m));

    if (!matches.length) return "";

    // One button toggles a drawer right under this row
    const count = matches.length;
    return `
    <button type="button"
            class="btn btn-sm btn-secondary js-toggle-drawer"
            data-count="${count}"
            title="Show matching triggers"
            aria-expanded="false">
      <i class="bi bi-pencil-square"></i>
      Edit ${count}
    </button>`;
}

function renderIncidentBlockFromCallData(data) {
    const wrap = document.getElementById("dIncidentWrap");
    const typeBadge = document.getElementById("dIncidentTypeBadge");
    const meta = document.getElementById("dIncidentMeta");
    if (!wrap || !typeBadge || !meta) return;

    // reset
    wrap.classList.add("d-none");
    typeBadge.textContent = "";
    meta.textContent = "";
    resetBadgeBg(typeBadge);

    // show only if we have something meaningful
    const incident = data.incident || null;

    if (!incident) return;

    // text
    typeBadge.textContent = incident.incident_type;

    // color by category (and show category + confidence in meta)
    const cat = (incident.category || "").trim();
    if (cat) typeBadge.classList.add(...getCategoryBadgeClasses(cat));

    const confTxt = formatConfidence(incident.confidence);
    meta.textContent = [cat || null, confTxt || null].filter(Boolean).join(" • ");

    wrap.classList.remove("d-none");
}

function formatConfidence(conf) {
    if (conf === null || conf === undefined) return "";
    const n = Number(conf);
    if (!Number.isFinite(n) || n <= 0) return "";

    // Accept either 0..1 or 0..100
    const pct = (n <= 1) ? Math.round(n * 100) : Math.round(n);
    return `${pct}%`;
}

function resetBadgeBg(el) {
    el.classList.remove(
        "bg-primary","bg-secondary","bg-success","bg-danger",
        "bg-warning","bg-info","bg-dark","bg-light",
        "text-dark"
    );
}

function getCategoryBadgeClasses(category) {
    switch (category.toLowerCase()) {
        case "fire":             return ["bg-danger"];
        case "medical":          return ["bg-success"];
        case "traffic":          return ["bg-warning", "text-dark"];
        case "rescue":           return ["bg-info", "text-dark"];
        case "law enforcement":  return ["bg-primary"];
        case "hazmat":           return ["bg-dark"];
        case "utilities":        return ["bg-secondary"];
        default:                 return ["bg-secondary"];
    }
}

/* ====================================================================
   11) FOOTER “NOW PLAYING” PLAYER (custom; global API)
   ==================================================================== */

document.addEventListener('DOMContentLoaded', () => {
    (function () {
        const P = {
            bar: document.getElementById('nowPlayingBar'),
            title: document.getElementById('npTitle'),
            subtitle: document.getElementById('npSubtitle'),
            playPause: document.getElementById('npPlayPause'),
            stop: document.getElementById('npStop'),
            seek: document.getElementById('npSeek'),
            time: document.getElementById('npTime'),
            volume: document.getElementById('npVolume'),
            icon: () => document.querySelector('#npPlayPause i'),
        };

        // If the markup isn't on this page, bail quietly.
        if (!P.bar || !P.playPause || !P.seek || !P.time) return;

        P.volIcon = document.getElementById('npVolIcon') ||
            P.bar.querySelector('.bi-volume-up, .bi-volume-down, .bi-volume-mute, .bi-volume-off');

        const audio = new Audio();
        audio.preload = 'metadata';
        // audio.crossOrigin = 'anonymous'; // if you need CORS for remote audio
        let seeking = false;

        function setPlayerCSSHeight() {
            const h = P.bar?.offsetHeight || 0;
            document.documentElement.style.setProperty('--np-height', `${h}px`);
        }

        function updateVolumeUI() {
            const vol = audio.muted ? 0 : (audio.volume ?? 1);
            const pct = Math.max(0, Math.min(1, vol)) * 100;
            P.volume.style.setProperty('--vol', pct.toFixed(2) + '%');

            if (!P.volIcon) return;
            if (audio.muted || vol === 0) {
                P.volIcon.className = 'bi bi-volume-mute';
                P.volume.setAttribute('aria-label', 'Volume (muted)');
            } else if (vol <= 0.5) {
                P.volIcon.className = 'bi bi-volume-down';
                P.volume.setAttribute('aria-label', `Volume ${Math.round(pct)} percent`);
            } else {
                P.volIcon.className = 'bi bi-volume-up';
                P.volume.setAttribute('aria-label', `Volume ${Math.round(pct)} percent`);
            }
        }

        function fmtTime(sec) {
            if (!isFinite(sec) || sec < 0) sec = 0;
            const m = Math.floor(sec / 60);
            const s = Math.floor(sec % 60);
            return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
        }

        function calcBufferedEnd() {
            try {
                const t = audio.currentTime;
                for (let i = 0; i < audio.buffered.length; i++) {
                    const start = audio.buffered.start(i);
                    const end = audio.buffered.end(i);
                    if (t >= start && t <= end) return end;
                }
                if (audio.buffered.length) {
                    return audio.buffered.end(audio.buffered.length - 1);
                }
            } catch { /* Safari sometimes throws */
            }
            return 0;
        }

        function setSeekProgress(playedPct, bufferedPct) {
            const bufferedSafe = Math.max(bufferedPct, playedPct);
            P.seek.style.setProperty('--played', playedPct.toFixed(2) + '%');
            P.seek.style.setProperty('--buffered', bufferedSafe.toFixed(2) + '%');
        }

        function updateTimeUI() {
            const cur = audio.currentTime || 0;
            const dur = isFinite(audio.duration) ? audio.duration : 0;

            window.dispatchEvent(new CustomEvent('np:time', {
                detail: {currentTime: cur, duration: dur}
            }));

            P.time.textContent = `${fmtTime(cur)} / ${fmtTime(dur)}`;

            if (!seeking && dur > 0) {
                P.seek.max = String(dur);
                P.seek.value = String(cur);
            }

            const playedPct = dur > 0 ? (cur / dur) * 100 : 0;
            const bufferedEnd = calcBufferedEnd();
            const bufferedPct = dur > 0 ? (bufferedEnd / dur) * 100 : 0;
            setSeekProgress(playedPct, bufferedPct);
        }

        function setPlayingUI(isPlaying) {
            P.playPause.setAttribute('aria-label', isPlaying ? 'Pause' : 'Play');
            P.icon().className = isPlaying ? 'bi bi-pause-fill' : 'bi bi-play-fill';
        }

        audio.addEventListener('loadedmetadata', updateTimeUI);
        audio.addEventListener('timeupdate', updateTimeUI);
        audio.addEventListener('durationchange', updateTimeUI);
        audio.addEventListener('progress', updateTimeUI);

        audio.addEventListener('play', () => setPlayingUI(true));
        audio.addEventListener('pause', () => setPlayingUI(false));
        audio.addEventListener('ended', () => setPlayingUI(false));

        // Broadcast state to the rest of the app
        const emitState = () => {
            window.dispatchEvent(new CustomEvent('np:state', {detail: window.npGetState?.()}));
        };
        audio.addEventListener('play', emitState);
        audio.addEventListener('pause', emitState);
        audio.addEventListener('ended', emitState);
        audio.addEventListener('loadedmetadata', emitState);
        audio.addEventListener('durationchange', emitState);

        // Controls
        P.playPause.addEventListener('click', async () => {
            if (!audio.src) return;
            if (audio.paused) {
                try {
                    await audio.play();
                } catch {
                }
            } else audio.pause();
        });

        P.stop.addEventListener('click', () => {
            audio.pause();
            audio.currentTime = 0;
            updateTimeUI();
        });

        let seekingDrag = false;

        P.seek.addEventListener('input', () => {
            seekingDrag = true;
            const val = parseFloat(P.seek.value);
            const dur = isFinite(audio.duration) ? audio.duration : 0;
            P.time.textContent = `${fmtTime(val)} / ${fmtTime(dur)}`;

            const playedPct = dur > 0 ? (val / dur) * 100 : 0;
            const bufferedEnd = calcBufferedEnd();
            const bufferedPct = dur > 0 ? (bufferedEnd / dur) * 100 : 0;
            setSeekProgress(playedPct, bufferedPct);
        });

        P.seek.addEventListener('change', () => {
            const val = parseFloat(P.seek.value);
            audio.currentTime = isFinite(val) ? val : 0;
            seekingDrag = false;
        });

        P.volume.addEventListener('input', () => {
            const v = parseFloat(P.volume.value);
            audio.muted = false;
            audio.volume = isFinite(v) ? Math.max(0, Math.min(1, v)) : 1;
            updateVolumeUI();
        });

        audio.addEventListener('volumechange', updateVolumeUI);

        P.volIcon?.addEventListener('click', () => {
            audio.muted = !audio.muted;
            updateVolumeUI();
        });

        P.volume.addEventListener('dblclick', () => {
            audio.muted = !audio.muted;
            updateVolumeUI();
        });

        // Public API
        window.nowPlaying = async ({src, title = '', subtitle = '', autoplay = true}) => {
            P.bar.classList.remove('d-none');
            document.body.classList.add('has-player');
            setPlayerCSSHeight();
            updateVolumeUI();

            P.title.textContent = title || (src ? src.split('/').pop() : 'Unknown');
            P.subtitle.textContent = subtitle || '';

            if (src) {
                const same = (audio.currentSrc === src || audio.src === src);
                audio.src = src;
                try {
                    audio.load();
                    updateTimeUI();
                    if (autoplay || !same) await audio.play();
                } catch {
                }
            }
            emitState();
        };

        window.seekNowPlaying = (sec) => {
            if (!Number.isFinite(sec)) return;
            const dur = Number.isFinite(audio.duration) ? audio.duration : 0;
            audio.currentTime = Math.max(0, dur ? Math.min(sec, dur) : sec);
        };

        window.updateNowPlayingMeta = ({title, subtitle, metaClass} = {}) => {
            if (typeof title === "string") P.title.textContent = title;
            if (typeof subtitle === "string") P.subtitle.textContent = subtitle;
            if (metaClass) {
                P.subtitle.classList.remove("voice-meta", "tone-meta", "trigger-meta");
                P.subtitle.classList.add(metaClass);
            }
        };

        window.hideNowPlaying = () => {
            audio.pause();
            audio.removeAttribute('src');
            P.bar.classList.add('d-none');
            document.body.classList.remove('has-player');
            setPlayerCSSHeight();
            updateTimeUI();
        };

        window.addEventListener("np:time", (ev) => {
            const t = ev?.detail?.currentTime;
            if (Number.isFinite(t)) syncModalToneHighlight(t);
        });

        window.npHasSrc = () => Boolean(audio.currentSrc || audio.src);
        window.npIsPaused = () => audio.paused;
        window.npPlay = () => {
            try {
                audio.play();
            } catch {
            }
        };
        window.npPause = () => {
            try {
                audio.pause();
            } catch {
            }
        };
        window.npStop = () => {
            audio.pause();
            audio.currentTime = 0;
            updateTimeUI();
            emitState();
        };
        window.npGetState = () => ({
            hasSrc: Boolean(audio.currentSrc || audio.src),
            paused: audio.paused,
            currentTime: audio.currentTime || 0,
            duration: isFinite(audio.duration) ? audio.duration : NaN,
            src: audio.currentSrc || audio.src
        });

        window.playFrom = async ({src, title = '', subtitle = '', start = 0}) => {
            await window.nowPlaying({src, title, subtitle, autoplay: false});
            const jumpAndPlay = () => {
                if (Number.isFinite(start)) {
                    try {
                        const dur = Number.isFinite(audio.duration) ? audio.duration : NaN;
                        audio.currentTime = Number.isFinite(dur)
                            ? Math.max(0, Math.min(start, dur))
                            : start;
                    } catch {
                    }
                }
                window.npPlay();
            };
            if (Number.isFinite(audio.duration)) jumpAndPlay();
            else audio.addEventListener('loadedmetadata', jumpAndPlay, {once: true});
            emitState();
        };

        let _npResizeTimer = null;
        window.addEventListener('resize', () => {
            clearTimeout(_npResizeTimer);
            _npResizeTimer = setTimeout(setPlayerCSSHeight, 100);
        });

    })();
});

/* ====================================================================
   12) BOOTSTRAP: PAGE INIT
   ==================================================================== */

document.addEventListener("DOMContentLoaded", initToneFinderPage);
