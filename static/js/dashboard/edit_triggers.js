/*********************************************************************
 *  Edit-Triggers page script  (Bootstrap 5.3 + DataTables v2)
 *  © iCAD Dispatch
 *
 *  Highlights
 *  - Autosave for Tone Rules: add/edit/delete and drag-to-reorder
 *  - DataTables per tone-type (Responsive + RowReorder)
 *  - Hides each tone table when it has 0 rows; shows it when rows exist
 *  - Busy UI while saving (tiny "Saving…" badge + disabled action buttons)
 *********************************************************************/

/* ===================================================================
   0) GLOBALS / ELEMENT REFS
   ===================================================================*/

/** System/Trigger selectors + editor cards */
const sysSel   = document.getElementById("triggerSystemSelect");
const trigGrp  = document.getElementById("triggerSelectGroup");
const trigSel  = document.getElementById("triggerSelect");
const noCard   = document.getElementById("noTriggerCard");
const editCard = document.getElementById("triggerEditCard");

/** Config + state */
const AUTOSAVE_RULES = true;       // master switch for tone-rule autosave
let systemToneSettings = null;     // { tone_tolerance_pct?: number }
let triggerDefaultTolPct = null;   // number | null
let lastSavedToneState = null;     // snapshot used for rollback
let isSaving = false;              // saving-in-flight flag (UI hint)

/** Current selected IDs */
let curSys  = null;
let curTrig = null;

/** Hide trigger picklist until a system is chosen */
trigGrp.classList.add("d-none");


/* ===================================================================
   1) SMALL UTILITIES (alerts, csrf, fetch, debounce, waitFor)
   ===================================================================*/

/**
 * Show a quick, themed message to the user.
 * If a global showAlert() exists, that will be used; otherwise uses alert().
 * @param {string} m   Message text
 * @param {"info"|"success"|"warning"|"danger"} [t="info"]
 */
if (typeof showAlert !== "function") {
    window.showAlert = (m, t = "info") => alert(`${t.toUpperCase()}: ${m}`);
}

/** @returns {string} Best-effort CSRF token from page */
function getCsrfToken() {
    return (
        document.querySelector('input[name="_csrf_token"]')?.value ||
        document.querySelector('meta[name="csrf-token"]')?.content ||
        window.CSRF_TOKEN ||
        ""
    );
}

function statusSuffix(enabled) {
    return (+enabled ? "" : " [off]");
}

function pickNum(obj, keys) {
    for (const k of keys) {
        const v = obj?.[k];
        if (v != null && !Number.isNaN(+v)) return parseFloat(v);
    }
    return null;
}

/**
 * Deep clone a plain object/array.
 * @template T
 * @param {T} obj
 * @returns {T}
 */
function deepClone(obj) { return JSON.parse(JSON.stringify(obj)); }

/**
 * Return a debounced wrapper around fn.
 * @param {Function} fn
 * @param {number} [wait=600] milliseconds
 */
function debounce(fn, wait = 600) {
    let t;
    return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), wait); };
}

/**
 * Lightweight JSON fetch with CSRF header + automatic JSON body.
 * Returns parsed JSON (never throws), attaches http_status if !ok.
 * @param {string} url
 * @param {{method?: string, body?: any, headers?: Record<string,string>}} [opts]
 * @returns {Promise<object>}
 */
async function apiJson(url, { method = "GET", body = null, headers = {} } = {}) {
    const token = getCsrfToken();
    const opts = { method, credentials: "same-origin", headers: { ...headers } };

    // Always send CSRF header on state-changing calls
    if (!["GET", "HEAD", "OPTIONS"].includes(method.toUpperCase()) && token) {
        opts.headers["X-CSRFToken"]  = token;
        opts.headers["X-CSRF-Token"] = token;
    }

    if (body !== null) {
        opts.headers["Content-Type"] = "application/json";
        if (token && body._csrf_token == null) body = { _csrf_token: token, ...body };
        opts.body = JSON.stringify(body);
    }

    const r = await fetch(url, opts);
    let data;
    try { data = await r.json(); }
    catch { data = { success: false, message: "Bad response", result: [] }; }
    if (!r.ok) data.http_status = r.status;
    return data;
}

/**
 * Polls a test function until it becomes true or times out.
 * @param {() => boolean} testFn
 * @param {number} [timeoutMs=3000]
 * @param {number} [intervalMs=50]
 * @returns {Promise<boolean>}
 */
function waitFor(testFn, timeoutMs = 3000, intervalMs = 50) {
    return new Promise(resolve => {
        const t0 = Date.now();
        const timer = setInterval(() => {
            if (testFn()) { clearInterval(timer); resolve(true); }
            else if (Date.now() - t0 > timeoutMs) { clearInterval(timer); resolve(false); }
        }, intervalMs);
    });
}


/* ===================================================================
   2) DEFAULTS / TOLERANCE RESOLUTION
   ===================================================================*/

/**
 * Find the default tolerance to apply when a rule’s tol_pct is blank:
 * prefer trigger-level, then fall back to system-level.
 * @returns {{tol: number|null, source: "trigger"|"system"|null}}
 */
function getDefaultTolPct() {
    if (triggerDefaultTolPct != null && !Number.isNaN(triggerDefaultTolPct)) {
        return { tol: triggerDefaultTolPct, source: "trigger" };
    }
    const sysTol = systemToneSettings?.tone_tolerance_pct;
    if (sysTol != null && !Number.isNaN(sysTol)) {
        return { tol: parseFloat(sysTol), source: "system" };
    }
    return { tol: null, source: null };
}

/**
 * Resolve tolerance for display (rule > trigger > system).
 * @param {any} ruleTol
 * @returns {{tol: number|null, source: "rule"|"trigger"|"system"|null}}
 */
function resolveTolForDisplay(ruleTol) {
    if (ruleTol != null && ruleTol !== "") {
        const n = parseFloat(ruleTol);
        return { tol: Number.isNaN(n) ? null : n, source: "rule" };
    }
    return getDefaultTolPct();
}


/* ===================================================================
   3) STARTUP (DOMContentLoaded) + SYSTEM/TRIGGER LOADING
   ===================================================================*/

document.addEventListener("DOMContentLoaded", async () => {
    await loadSystems();
    wirePatchForms();
    wireTriggerEnabledAutosave();
    wireNotifierOverrideAutosave();
    await preselectFromURL();
});

/** Autosave channel overrides when their selects change */
function wireNotifierOverrideAutosave() {
    const form = document.getElementById("updateTriggerOverridesForm");
    if (!form) return;

    form.addEventListener("change", async (e) => {
        const target = e.target;
        if (!(target instanceof HTMLSelectElement)) return;
        if (!target.name?.startsWith("alert_trigger_enable_")) return;
        if (!curSys || !curTrig) return;

        target.disabled = true;
        const rsp = await apiJson(`/api/systems/${curSys}/triggers/${curTrig}`, {
            method: "PATCH",
            body: { [target.name]: target.value }
        });
        target.disabled = false;

        showAlert(
            rsp.message || (rsp.success ? "Saved." : "Failed to save."),
            rsp.success ? "success" : "danger"
        );
    });
}


/**
 * Load system-level tone settings from a few likely endpoints.
 * @param {string|number} systemId
 */
async function loadSystemToneSettings(systemId) {
    systemToneSettings = null;
    const candidates = [
        `/api/systems/${systemId}/tone_settings`,
        `/api/systems/${systemId}?include=tone_settings`,
        `/api/systems/${systemId}`
    ];

    for (const url of candidates) {
        try {
            const rsp = await apiJson(url);
            if (!rsp?.success) continue;

            const root = rsp.result || rsp.results || rsp.system || rsp;
            const ts   = root?.tone_settings || root?.system_tone_settings || root;
            if (!ts) continue;

            const tol =
                ts?.tone_tolerance_pct ??
                ts?.default_tone_tolerance ??
                ts?.tolerance_pct ??
                null;

            const tolNum = (tol != null && !Number.isNaN(+tol)) ? parseFloat(tol) : null;

            // build the per-type defaults
            const sysDefaults = {
                two_tone: {
                    min_len_a_s: pickNum(ts, ["two_tone_a_min_s","two_tone_min_len_a_s","min_len_a_s"]),
                    min_len_b_s: pickNum(ts, ["two_tone_b_min_s","two_tone_min_len_b_s","min_len_b_s"]),
                },
                long_tone: {
                    min_len_s:   pickNum(ts, ["long_min_s","long_tone_min_len_s","min_len_s"]),
                },
                hi_low: {
                    min_alternations: pickNum(ts, ["hi_low_min_alternations","hi_low_min_alts","min_alternations"]),
                    interval_s:       pickNum(ts, ["hi_low_interval_s","interval_s"]),
                },
                pulsed: {
                    min_cycles:  pickNum(ts, ["pulsed_min_cycles","min_cycles"]),
                    min_on_ms:   pickNum(ts, ["pulsed_min_on_ms","min_on_ms","on_min_ms"]),
                    max_on_ms:   pickNum(ts, ["pulsed_max_on_ms","max_on_ms","on_max_ms"]),
                    min_off_ms:  pickNum(ts, ["pulsed_min_off_ms","min_off_ms","off_min_ms"]),
                    max_off_ms:  pickNum(ts, ["pulsed_max_off_ms","max_off_ms","off_max_ms"]),
                }
            };

            systemToneSettings = {
                tone_tolerance_pct: tolNum,
                defaults: sysDefaults
            };
            break; // ✅ break only after we've set both tol + defaults
        } catch { /* noop */ }
    }
}

/** Populate the Systems select */
async function loadSystems() {
    const rsp = await apiJson("/api/systems");
    if (!rsp.success) { showAlert(rsp.message, "danger"); return; }

    sysSel.innerHTML = `<option value="">Select System</option>`;
    rsp.result.forEach(s =>
        sysSel.insertAdjacentHTML(
            "beforeend",
            `<option value="${s.radio_system_id}">${s.system_name}</option>`
        )
    );
}

/** Preselect system/trigger from URL params (?system=...&trigger=...) */
async function preselectFromURL() {
    const params = new URLSearchParams(location.search);
    const sys  = params.get("system");
    const trig = params.get("trigger");
    if (!sys) return;

    sysSel.value = sys;
    await populateTriggersForSystem(sys);

    if (trig) {
        const ok = await waitFor(() =>
            Array.from(trigSel.options).some(o => o.value === String(trig)), 3000);
        if (ok) {
            trigSel.value = String(trig);
            trigSel.dispatchEvent(new Event("change"));
        }
    }
}

/**
 * Load triggers for a system and reveal the trigger picklist._fetch_system_tone_settings_row
 * Editor is hidden until a trigger is chosen.
 * @param {string|number|null} systemId
 */
async function populateTriggersForSystem(systemId) {
    curSys  = systemId || null;
    curTrig = null;

    if (curSys) await loadSystemToneSettings(curSys);

    trigSel.innerHTML = `<option value="">Select Trigger</option>`;
    trigGrp.classList.add("d-none");
    toggleEditor(false);

    if (!curSys) return;

    const rsp = await apiJson(`/api/systems/${curSys}/triggers`);
    if (!rsp.success) { showAlert(rsp.message, "danger"); return; }

    rsp.result.forEach(t =>
        trigSel.insertAdjacentHTML(
            "beforeend",
            `<option value="${t.alert_trigger_id}" data-enabled="${t.alert_trigger_enabled ?? 1}">
      ${t.alert_trigger_name}${
                t.alert_trigger_talkgroup != null ? ` (TG ${t.alert_trigger_talkgroup})` : ""
            }${statusSuffix(t.alert_trigger_enabled ?? 1)}
    </option>`
        )
    );
    trigGrp.classList.remove("d-none");
}

/** System selector change → refresh triggers */
sysSel.addEventListener("change", async () => {
    await populateTriggersForSystem(sysSel.value || null);
});

/** Trigger selector change → load trigger payload + show editor */
trigSel.addEventListener("change", async () => {
    curTrig = trigSel.value || null;
    if (!curTrig) { toggleEditor(false); return; }

    const rsp = await apiJson(`/api/systems/${curSys}/triggers/${curTrig}?full=1`);
    if (!rsp.success) { showAlert(rsp.message, "danger"); toggleEditor(false); return; }

    populateEditor(rsp.result);
    toggleEditor(true);
});

/**
 * Toggle the editor vs empty-card state.
 * @param {boolean} show
 */
function toggleEditor(show) {
    editCard.classList.toggle("d-none", !show);
    noCard .classList.toggle("d-none",  show);
}


/* ===================================================================
   4) EDITOR POPULATION (general tab, legacy fields, notifiers)
   ===================================================================*/

/**
 * Fill the whole editor from a trigger payload.
 * @param {object} t
 */
function populateEditor(t) {
    triggerDefaultTolPct = (t?.alert_trigger_tone_tolerance !== undefined && t?.alert_trigger_tone_tolerance !== null && t?.alert_trigger_tone_tolerance !== "")
        ? parseFloat(t.alert_trigger_tone_tolerance)
        : null;

    // General
    document.getElementById("updateTriggerId").value        = t.alert_trigger_id;
    document.getElementById("updateTriggerName").value      = t.alert_trigger_name;
    document.getElementById("updateTriggerEnabled").value   = t.alert_trigger_enabled;
    document.getElementById("updateTriggerType").value      = t.alert_trigger_type;
    document.getElementById("updateTriggerIgnore").value    = t.alert_trigger_ignore_time;
    document.getElementById("updateTriggerTol").value       = t.alert_trigger_tone_tolerance;
    document.getElementById("updateTriggerTalkgroup").value = t.alert_trigger_talkgroup ?? "";

    // Legacy single-value tone fields (kept for compatibility)
    const map = {
        alert_trigger_two_tone_a:         "alert_trigger_two_tone_a",
        alert_trigger_two_tone_a_length:  "alert_trigger_two_tone_a_length",
        alert_trigger_two_tone_b:         "alert_trigger_two_tone_b",
        alert_trigger_two_tone_b_length:  "alert_trigger_two_tone_b_length",
        alert_trigger_long_tone:          "alert_trigger_long_tone",
        alert_trigger_long_tone_length:   "alert_trigger_long_tone_length",
        alert_trigger_hi_low_tone_a:      "alert_trigger_hi_low_tone_a",
        alert_trigger_hi_low_tone_b:      "alert_trigger_hi_low_tone_b",
        alert_trigger_hi_low_alternations:"alert_trigger_hi_low_alternations",
        alert_trigger_pulsed_tone:        "alert_trigger_pulsed_tone",
        alert_trigger_pulsed_min_cycles:  "alert_trigger_pulsed_min_cycles",
    };
    for (const col of Object.keys(map)) {
        const el = document.querySelector(`[name="${col}"]`);
        if (el) el.value = t[col] ?? "";
    }

    // Tone Rules (DataTables)
    populateToneRules(t);

    // Notifier overrides
    ["discord","make","telegram","ntfy"].forEach(ch => {
        const sel = document.querySelector(`[name="alert_trigger_enable_${ch}"]`);
        if (sel) sel.value = t[`alert_trigger_enable_${ch}`] ?? 0;
    });

    // Ntfy topic override
    const ntfyTopicEl = document.querySelector(`[name="alert_trigger_ntfy_topic"]`);
    if (ntfyTopicEl) ntfyTopicEl.value = t.alert_trigger_ntfy_topic ?? "";

    // Pushover child row
    loadPushoverTab();
}

/**
 * Refresh the trigger <select> options and optionally re-select an ID.
 * @param {string|number|null} [selectId]
 */
async function reloadTriggerSelect(selectId = null) {
    trigSel.innerHTML = `<option value="">Select Trigger</option>`;
    const rsp = await apiJson(`/api/systems/${curSys}/triggers`);
    if (!rsp.success) return;

    rsp.result.forEach(t =>
        trigSel.insertAdjacentHTML(
            "beforeend",
            `<option value="${t.alert_trigger_id}" data-enabled="${t.alert_trigger_enabled ?? 1}">
      ${t.alert_trigger_name}${
                t.alert_trigger_talkgroup != null ? ` (TG ${t.alert_trigger_talkgroup})` : ""
            }${statusSuffix(t.alert_trigger_enabled ?? 1)}
    </option>`
        )
    );
    if (selectId) {
        trigSel.value = selectId;
        trigSel.dispatchEvent(new Event("change"));
    }
}


/* ===================================================================
   5) TRIGGER ADD/EDIT/DELETE MODALS (non-rule-level)
   ===================================================================*/

const modalEl   = document.getElementById("triggerModal");
const modalForm = document.getElementById("triggerForm");

/** Open Add Trigger modal (prefill system) */
document.getElementById("addTriggerBtn").addEventListener("click", () => {
    if (!curSys) { showAlert("Select a system first.", "warning"); return; }
    modalForm.reset();
    modalForm.modalSystemId.value  = curSys;
    modalForm.modalTriggerId.value = "";
    document.getElementById("triggerModalLabel").textContent = "Add Trigger";
});

/** Create or update a trigger */
modalForm.addEventListener("submit", async ev => {
    ev.preventDefault();
    const data  = Object.fromEntries(new FormData(modalForm).entries());
    const isNew = !data.alert_trigger_id;
    const url   = isNew
        ? `/api/systems/${curSys}/triggers`
        : `/api/systems/${curSys}/triggers/${data.alert_trigger_id}`;

    const rsp = await apiJson(url, { method: isNew ? "POST" : "PATCH", body: data });
    if (!rsp.success) { showAlert(rsp.message, "danger"); return; }

    bootstrap.Modal.getInstance(modalEl).hide();
    await reloadTriggerSelect(rsp.result.alert_trigger_id);
    showAlert(rsp.message, "success");
});

/* ----- Delete modal ----- */
const delModal = document.getElementById("triggerDeleteModal");
const delForm  = document.getElementById("triggerDeleteForm");

/** Preconfirm delete text */
document.getElementById("deleteTriggerBtn").addEventListener("click", () => {
    delForm.deleteTriggerId.value = curTrig;
    document.getElementById("deleteTriggerQuestion").textContent =
        `Delete trigger “${trigSel.selectedOptions[0].textContent}”?`;
});

/** Delete a trigger */
delForm.addEventListener("submit", async ev => {
    ev.preventDefault();
    const csrf = delForm._csrf_token.value;
    const rsp  = await apiJson(`/api/systems/${curSys}/triggers/${curTrig}`,
        { method: "DELETE", body: { _csrf_token: csrf } });
    if (!rsp.success) { showAlert(rsp.message, "danger"); return; }

    bootstrap.Modal.getInstance(delModal).hide();
    await reloadTriggerSelect();
    toggleEditor(false);
    showAlert(rsp.message, "success");
});


/* ===================================================================
   6) IN-CARD PATCH FORMS (General, Overrides, Pushover)
   ===================================================================*/

/** Attach PATCH handlers for on-card forms */
function wirePatchForms() {
    attachPatch("updateTriggerGeneralForm");
    attachPatch("updateTriggerTwoToneForm");    // back-compat if present
    attachPatch("updateTriggerOverridesForm");

    // Pushover child row
    document.getElementById("updateTriggerPushoverForm")
        ?.addEventListener("submit", async ev => {
            ev.preventDefault();
            const data = Object.fromEntries(new FormData(ev.target).entries());
            const url  = `/api/systems/${curSys}/triggers/${curTrig}/pushover/settings`;
            const rsp  = await apiJson(url, { method: "PATCH", body: data });
            showAlert(rsp.message, rsp.success ? "success" : "danger");
        });
}

/**
 * Generic PATCH binder for a simple form that updates the trigger row.
 * @param {string} formId
 */
function attachPatch(formId) {
    const f = document.getElementById(formId);
    if (!f) return;
    f.addEventListener("submit", async ev => {
        ev.preventDefault();
        const data = Object.fromEntries(new FormData(f).entries());
        const rsp  = await apiJson(
            `/api/systems/${curSys}/triggers/${curTrig}`,
            { method: "PATCH", body: data }
        );
        showAlert(rsp.message, rsp.success ? "success" : "danger");
    });
}

/** Load Pushover child-row into form fields */
async function loadPushoverTab() {
    const rsp = await apiJson(
        `/api/systems/${curSys}/triggers/${curTrig}/pushover/settings`
    );
    if (!rsp.success) return;

    const map = {
        enable_pushover:       "enable_pushover",
        pushover_group_token:  "pushover_group_token",
        pushover_app_token:    "pushover_app_token",
        pushover_subject:      "pushover_subject",
        pushover_body:         "pushover_body",
        pushover_sound:        "pushover_sound"
    };
    for (const [col] of Object.entries(map)) {
        const el = document.querySelector(`[name="${col}"]`);
        if (el) el.value = rsp.result[col] ?? "";
    }
}


/* ===================================================================
   7) TONE RULES: SCHEMA + STATE + RENDER HELPERS
   ===================================================================*/

function getSystemDefault(typeKey, fieldName) {
    return systemToneSettings?.defaults?.[typeKey]?.[fieldName] ??
        RULE_TYPES?.[typeKey]?.defaults?.[fieldName] ??
        null;
}

const FIELD_ALIASES = {
    two_tone: {
        freq_a_hz:      ["tone_a_hz", "two_tone_a", "tone_a", "freqAHz"],
        min_len_a_s:    ["two_tone_a_length", "tone_a_length", "a_min_s", "tone_a_min_s"],
        freq_b_hz:      ["tone_b_hz", "two_tone_b", "tone_b", "freqBHz"],
        min_len_b_s:    ["two_tone_b_length", "tone_b_length", "b_min_s", "tone_b_min_s"],
        tol_pct:        ["tolerance_pct", "tolerance", "tol", "tone_tolerance_pct"]
    },
    long_tone: {
        freq_hz:        ["long_tone_hz", "long_tone", "tone_hz", "frequency_hz"],
        min_len_s:      ["long_tone_length", "length_s", "tone_length_s"],
        tol_pct:        ["tolerance_pct", "tolerance", "tol", "tone_tolerance_pct"]
    },
    hi_low: {
        hi_freq_a_hz:   ["hi_hz", "high_hz", "hi_low_tone_a", "hi_freq_hz_a"],
        hi_freq_b_hz:   ["low_hz", "lo_hz", "hi_low_tone_b", "hi_freq_hz_b"],
        min_alternations:["alternations", "hi_low_alternations"],
        interval_s:     ["hi_low_interval_s", "interval"],
        tol_pct:        ["tolerance_pct", "tolerance", "tol", "tone_tolerance_pct"]
    },
    pulsed: {
        center_hz:      ["pulsed_center_hz", "pulsed_tone", "center"],
        min_cycles:     ["pulsed_min_cycles", "mincycles"],
        min_on_ms:      ["on_min_ms", "min_on"],
        max_on_ms:      ["on_max_ms", "max_on"],
        min_off_ms:     ["off_min_ms", "min_off"],
        max_off_ms:     ["off_max_ms", "max_off"],
        tol_pct:        ["tolerance_pct", "tolerance", "tol", "tone_tolerance_pct"]
    },
    dtmf: {
        sequence:       ["dtmf_sequence", "seq"]
    }
};

/** Server-side defaults used when building a new rule row in the UI */
const SCHEMA_DEFAULTS = {
    TWO_TONE_A_MIN_S  : 0.6,
    TWO_TONE_B_MIN_S  : 2.5,
    LONG_MIN_S        : 3.8,
    HI_LOW_MIN_ALTS   : 4,
    HI_LOW_INTERVAL_S : 0.2,
    PULSED_MIN_CYCLES : 6,
};

/** Spec per tone rule type (UI + wire format) */
const RULE_TYPES = {
    two_tone: {
        title: "Two-Tone",
        listId: "twoToneList",
        paneId: "trPaneTwoTone",
        emptyId: "twoToneEmpty",
        countId: "twoToneCount",
        key: "two_tone_sets",
        fields: [
            { name:"freq_a_hz",   label:"Tone A",   type:"number", step:"0.1", num:"float", suffix:"Hz" },
            { name:"min_len_a_s", label:"A min",    type:"number", step:"0.1", num:"float", suffix:"s"  },
            { name:"freq_b_hz",   label:"Tone B",   type:"number", step:"0.1", num:"float", suffix:"Hz" },
            { name:"min_len_b_s", label:"B min",    type:"number", step:"0.1", num:"float", suffix:"s"  },
            { name:"tol_pct",     label:"±Tol",     type:"number", step:"0.1", num:"float", suffix:"%", optional:true },
        ],
        layout: [["freq_a_hz","min_len_a_s"], ["freq_b_hz","min_len_b_s"], ["tol_pct"]],
        defaults: { min_len_a_s: SCHEMA_DEFAULTS.TWO_TONE_A_MIN_S, min_len_b_s: SCHEMA_DEFAULTS.TWO_TONE_B_MIN_S },
    },

    long_tone: {
        title: "Long Tone",
        listId: "longToneList",
        paneId: "trPaneLongTone",
        emptyId: "longToneEmpty",
        countId: "longToneCount",
        key: "long_tone_sets",
        fields: [
            { name:"freq_hz",   label:"Frequency", type:"number", step:"0.1", num:"float", suffix:"Hz" },
            { name:"min_len_s", label:"Min length",type:"number", step:"0.1", num:"float", suffix:"s"  },
            { name:"tol_pct",   label:"±Tol",      type:"number", step:"0.1", num:"float", suffix:"%", optional:true },
        ],
        layout: [["freq_hz","min_len_s"], ["tol_pct"]],
        defaults: { min_len_s: SCHEMA_DEFAULTS.LONG_MIN_S },
    },

    hi_low: {
        title: "Hi / Low",
        listId: "hiLowList",
        paneId: "trPaneHiLow",
        emptyId: "hiLowEmpty",
        countId: "hiLowCount",
        key: "hi_low_sets",
        fields: [
            { name:"hi_freq_a_hz",    label:"Hi",            type:"number", step:"0.1",  num:"float", suffix:"Hz" },
            { name:"hi_freq_b_hz",    label:"Low",           type:"number", step:"0.1",  num:"float", suffix:"Hz" },
            { name:"min_alternations",label:"Alternations",  type:"number", step:"1",    num:"int"                },
            { name:"interval_s",      label:"Interval",      type:"number", step:"0.05", num:"float", suffix:"s"  },
            { name:"tol_pct",         label:"±Tol",          type:"number", step:"0.1",  num:"float", suffix:"%", optional:true },
        ],
        layout: [["hi_freq_a_hz","hi_freq_b_hz"], ["min_alternations","interval_s"], ["tol_pct"]],
        defaults: { min_alternations: SCHEMA_DEFAULTS.HI_LOW_MIN_ALTS, interval_s: SCHEMA_DEFAULTS.HI_LOW_INTERVAL_S },
    },

    pulsed: {
        title: "Pulsed",
        listId: "pulsedList",
        paneId: "trPanePulsed",
        emptyId: "pulsedEmpty",
        countId: "pulsedCount",
        key: "pulsed_sets",
        fields: [
            { name:"center_hz",  label:"Center",     type:"number", step:"0.1", num:"float", suffix:"Hz" },
            { name:"min_cycles", label:"Min cycles", type:"number", step:"1",   num:"int"                },
            { name:"min_on_ms",  label:"Min ON",     type:"number", step:"1",   num:"int", suffix:"ms", optional:true },
            { name:"max_on_ms",  label:"Max ON",     type:"number", step:"1",   num:"int", suffix:"ms", optional:true },
            { name:"min_off_ms", label:"Min OFF",    type:"number", step:"1",   num:"int", suffix:"ms", optional:true },
            { name:"max_off_ms", label:"Max OFF",    type:"number", step:"1",   num:"int", suffix:"ms", optional:true },
            { name:"tol_pct",    label:"±Tol",       type:"number", step:"0.1", num:"float", suffix:"%", optional:true },
        ],
        layout: [["center_hz","min_cycles"], ["min_on_ms","max_on_ms"], ["min_off_ms","max_off_ms"], ["tol_pct"]],
        defaults: { min_cycles: SCHEMA_DEFAULTS.PULSED_MIN_CYCLES },
    },

    dtmf: {
        title: "DTMF",
        listId: "dtmfList",
        paneId: "trPaneDTMF",
        emptyId: "dtmfEmpty",
        countId: "dtmfCount",
        key: "dtmf_sequences",
        fields: [
            { name:"sequence",   label:"Sequence",  type:"text" },
        ],
        layout: [["sequence"]],
        defaults: {},
    },
};

/** In-memory state mirroring server arrays */
let toneState = {
    two_tone_sets  : [],
    long_tone_sets : [],
    hi_low_sets    : [],
    pulsed_sets    : [],
    dtmf_sequences : [],
};

/** Generate a short, stable UID for rule rows */
function uid() {
    return [...crypto.getRandomValues(new Uint8Array(8))]
        .map(b => b.toString(16).padStart(2, "0")).join("");
}

/** Simple formatters used by DataTables cell render */
const fmtHz  = v => v == null || v === "" ? "—" : `${Number(v).toFixed(1)} Hz`;
const fmtSec = v => v == null || v === "" ? "—" : `${Number(v).toFixed(2)} s`;


/* ===================================================================
   8) TONE RULES: DATATABLES RENDERING + ROW EVENTS
   ===================================================================*/

/** DataTable instances by typeKey */
const toneTables = {};

/** Back-compat wrapper for old callers */
function renderToneLists() { renderToneTables(); }

/**
 * Create (once) or retrieve the DataTable instance for a given type.
 * Adds:
 *  - Leading "drag" column as RowReorder handle
 *  - Field columns (formatted values)
 *  - Trailing Actions column (Edit/Delete)
 * @param {"two_tone"|"long_tone"|"hi_low"|"pulsed"|"dtmf"} typeKey
 * @returns {DataTable|null}
 */
function ensureToneTable(typeKey) {
    const spec = RULE_TYPES[typeKey];
    const host = document.getElementById(spec.listId);
    if (!host) return null;

    // Upgrade static list container to a table host (once)
    if (!host.dataset.upgraded) {
        host.dataset.upgraded = "1";
        host.className = "table-responsive border rounded dt-theme";
        host.innerHTML = `
    <table id="${spec.listId}_tbl"
           class="table table-sm table-striped table-hover align-middle w-100"></table>`;
    }
    if (toneTables[typeKey]) return toneTables[typeKey];

    const tableEl = host.querySelector("table");

    const columns = [
        { title: "", data: "_drag", orderable: false, searchable: false, className: "reorder-handle text-secondary", width: "1%" },
        ...spec.fields.map((f, i) => ({
            title: f.label,
            data: f.name,
            responsivePriority: i < 2 ? 1 : (i < 4 ? 2 : 5)
        })),
        { title: "Actions", data: "_actions", orderable: false, searchable: false, className: "text-nowrap", responsivePriority: 1 }
    ];

    const dt = new DataTable(tableEl, {
        data: [],
        columns,
        paging: false,
        searching: false,
        info: false,
        autoWidth: false,
        order: [],
        responsive: true,
        rowReorder: { selector: "td.reorder-handle" },
        language: { emptyTable: `No ${spec.title} rules` },
        layout: { topStart: null, topEnd: null, bottomStart: null, bottomEnd: null }
    });

    // Row-level action buttons (edit/delete)
    tableEl.addEventListener("click", (e) => {
        const btn = e.target.closest("[data-edit-rule],[data-del-rule]");
        if (!btn) return;
        e.preventDefault();
        const uid = btn.dataset.uid;
        if (btn.hasAttribute("data-edit-rule")) openRuleModal(typeKey, uid);
        else deleteRule(typeKey, uid);
    });

    // Keep toneState order in sync after drag + debounced autosave
    dt.on("row-reorder", () => {
        const arr = toneState[spec.key] || [];
        const ordered = dt.rows().data().toArray()
            .map(row => arr.find(r => r.rule_uid === row._uid))
            .filter(Boolean);
        toneState[spec.key] = ordered;
        debouncedAutosave();
    });

    toneTables[typeKey] = dt;
    return dt;
}

/**
 * Resolve a value for a table field from a rule object, trying:
 *  - canonical name,
 *  - known aliases for this tone type,
 *  - generic tolerance fallbacks,
 *  - tolerant case/underscore differences.
 * Returns undefined only if not found anywhere.
 * @param {"two_tone"|"long_tone"|"hi_low"|"pulsed"|"dtmf"} typeKey
 * @param {string} fieldName
 * @param {object} rule
 */
function getRuleFieldValue(typeKey, fieldName, rule) {
    if (!rule) return undefined;

    // 1) Exact
    if (rule[fieldName] !== undefined) return rule[fieldName];

    // 2) Aliases
    const aliases = FIELD_ALIASES[typeKey]?.[fieldName] || [];
    for (const a of aliases) {
        if (rule[a] !== undefined) return rule[a];
    }

    // 3) Generic tolerance fallbacks for any type
    if (fieldName === "tol_pct") {
        const tolKeys = ["tolerance_pct", "tolerance", "tol", "tone_tolerance_pct"];
        for (const k of tolKeys) if (rule[k] !== undefined) return rule[k];
    }

    // 4) Loose key match (case/underscore insensitive)
    const norm = s => String(s).toLowerCase().replace(/[^a-z0-9]/g, "");
    const want = norm(fieldName);
    const entries = Object.entries(rule);
    for (const [k, v] of entries) {
        if (norm(k) === want) return v;
    }

    return undefined;
}

/**
 * Format one value according to field meta for display in the table.
 * Applies unit formatting and tol_pct inheritance display.
 * @param {object} f field spec
 * @param {any} v raw value
 * @returns {string}
 */
function formatCell(f, v) {
    // Show inherited tolerance if per-rule tol is null/blank
    if (f.name === "tol_pct") {
        const { tol } = resolveTolForDisplay(v);
        return tol == null ? "—" : `±${Number(tol).toFixed(1)} %`;
    }

    if (v == null || v === "") return "—";

    const asInt   = x => Number.isFinite(+x) ? parseInt(x, 10) : null;
    const asFloat = x => Number.isFinite(+x) ? parseFloat(x)  : null;

    if (f.type === "select")   return String(v).toUpperCase();
    if (f.name === "sequence") return String(v).toUpperCase();

    if (f.type === "number") {
        const num = (f.num === "int") ? asInt(v) : asFloat(v);
        if (num == null) return "—";
        const suf = (f.suffix || "").toLowerCase();
        if (suf.includes("hz")) return `${num.toFixed(1)} Hz`;
        if (suf === "s")        return `${num.toFixed(2)} s`;
        if (suf === "ms")       return `${Math.round(num)} ms`;
        if (suf === "%")        return `${num.toFixed(1)} %`;
        return String(num);
    }
    return String(v);
}

/**
 * Build a DataTables row object from an internal rule object.
 * @param {"two_tone"|"long_tone"|"hi_low"|"pulsed"|"dtmf"} typeKey
 * @param {object} rule
 */
function buildRowObject(typeKey, rule) {
    const spec = RULE_TYPES[typeKey];
    const obj  = { _uid: rule.rule_uid };

    obj._drag = `<span class="d-inline-flex align-items-center" title="Drag to reorder">
    <i class="bi bi-grip-vertical"></i>
  </span>`;

    spec.fields.forEach(f => {
        const raw = getRuleFieldValue ? getRuleFieldValue(typeKey, f.name, rule) : rule[f.name];
        obj[f.name] = formatCell(f, raw);
    });

    obj._actions = `
    <div class="btn-group btn-group-sm" role="group">
      <button type="button" class="btn btn-outline-secondary"
              data-edit-rule data-uid="${rule.rule_uid}">
        <i class="bi bi-pencil"></i> Edit
      </button>
      <button type="button" class="btn btn-outline-danger"
              data-del-rule data-uid="${rule.rule_uid}">
        <i class="bi bi-trash"></i> Delete
      </button>
    </div>`;
    return obj;
}

/**
 * Render all tone tables from toneState.
 * If a type has 0 rows, its container is hidden and "empty" helper is shown.
 */
function renderToneTables() {
    for (const [typeKey, spec] of Object.entries(RULE_TYPES)) {
        const host  = document.getElementById(spec.listId);
        const empty = document.getElementById(spec.emptyId);
        const badge = document.getElementById(spec.countId);
        if (!host) continue;

        const arr = toneState[spec.key] || [];
        if (badge) badge.textContent = String(arr.length);

        if (arr.length === 0) {
            host.classList.add("d-none");
            empty?.classList.remove("d-none");

            const maybeDt = toneTables[typeKey];
            if (maybeDt) maybeDt.clear().draw(false);
            continue;
        }

        host.classList.remove("d-none");
        empty?.classList.add("d-none");

        const dt   = ensureToneTable(typeKey);
        const data = arr.map(r => buildRowObject(typeKey, r));
        dt.clear();
        dt.rows.add(data);
        dt.draw(false);
    }
}


/* ===================================================================
   9) TONE RULES: MODAL ADD/EDIT FLOW (+ AUTOSAVE)
   ===================================================================*/

document.querySelectorAll("[data-rule-add]").forEach(btn => {
    btn.addEventListener("click", (e) => {
        e.stopPropagation();             // don't toggle the accordion
        openRuleModal(btn.dataset.ruleAdd, null);
    });
});

const ruleModalEl   = document.getElementById("toneRuleModal");
const ruleModal     = new bootstrap.Modal(ruleModalEl);
const ruleForm      = document.getElementById("toneRuleForm");
const ruleFieldsBox = document.getElementById("toneRuleFields");
const ruleUidInput  = document.getElementById("toneRuleUid");
const typeKeyInput  = document.getElementById("toneRuleTypeKey");
const ruleTitle     = document.getElementById("toneRuleModalLabel");

/**
 * Open the tone rule modal for add/edit of a single rule row.
 * Renders inputs per RULE_TYPES[typeKey].layout with units + placeholders.
 * @param {"two_tone"|"long_tone"|"hi_low"|"pulsed"|"dtmf"} typeKey
 * @param {string|null} [existingUid]
 */
function openRuleModal(typeKey, existingUid = null) {
    const spec = RULE_TYPES[typeKey];
    typeKeyInput.value = typeKey;

    let current = null;
    if (existingUid) {
        current = (toneState[spec.key] || []).find(r => r.rule_uid === existingUid) || null;
        ruleUidInput.value = existingUid;
        ruleTitle.textContent = `Edit ${spec.title} rule`;
    } else {
        ruleUidInput.value = "";
        ruleTitle.textContent = `Add ${spec.title} rule`;
    }

    const buildControl = (f, id) => {
        const label = `<label class="form-label" for="${id}">${f.label}${f.optional ? ' <span class="text-muted">(optional)</span>' : ''}</label>`;
        if (f.type === "select") {
            const opts = (f.options||[]).map(o => `<option value="${o}">${o}</option>`).join("");
            return `${label}
        <select id="${id}" class="form-select form-select-sm" name="${f.name}">${opts}</select>`;
        }
        const input = `<input id="${id}" class="form-control form-control-sm" type="${f.type}" name="${f.name}" ${f.step ? `step="${f.step}"` : ""}>`;
        if (f.suffix) {
            return `${label}
        <div class="input-group input-group-sm">
          ${input}
          <span class="input-group-text">${f.suffix}</span>
        </div>`;
        }
        return `${label}${input}`;
    };

    // Render fields in rows
    ruleFieldsBox.innerHTML = "";
    const rows = spec.layout && spec.layout.length ? spec.layout : [spec.fields.map(f => f.name)];

    rows.forEach(row => {
        const cols = row.length;
        const colWidth = Math.max(12 / cols, 3);
        const rowEl = document.createElement("div");
        rowEl.className = "row g-3 align-items-end";

        row.forEach(fname => {
            const f = spec.fields.find(x => x.name === fname);
            if (!f) return;
            const col = document.createElement("div");
            col.className = `col-12 col-md-${colWidth}`;
            const id = `fld_${typeKey}_${f.name}`;

            col.innerHTML = buildControl(f, id);
            rowEl.appendChild(col);

            // Initial value + helpful tolerance placeholder
            const el = col.querySelector(`[name="${f.name}"]`);
            if (f.name === "tol_pct") {
                const { tol, source } = getDefaultTolPct();
                if (tol != null) {
                    el.placeholder = `${tol.toFixed(1)}`;
                    el.title = `Leave blank to use ${tol.toFixed(1)}% from ${source} defaults`;
                } else {
                    el.placeholder = "";
                    el.title = "Optional per-rule tolerance in %";
                }
            }

            let val;
            if (current && current[f.name] != null) {
                val = current[f.name];
            } else {
                // prefer radio_system default, then type defaults, then select-first
                const sysDef = getSystemDefault(typeKey, f.name);
                if (sysDef != null && f.name !== "tol_pct") {
                    val = sysDef;
                } else if (f.name in (spec.defaults||{})) {
                    val = spec.defaults[f.name];
                } else if (f.type === "select") {
                    val = (f.options && f.options[0]) || "";
                } else {
                    val = "";
                }
            }
            if (f.type === "select")      el.value = (val || "").toString().toUpperCase();
            else if (f.type === "number") el.value = (val === null || val === undefined) ? "" : val;
            else                           el.value = val ?? "";

            /* ========= INSERT: system-default placeholder hint (optional) ======== */
            if ((val == null || val === "") && f.name !== "tol_pct") {
                const sysDef = getSystemDefault(typeKey, f.name);
                if (sysDef != null) {
                    el.placeholder = String(sysDef);
                    el.title = "Defaults to radio system setting";
                }
            }
        });

        ruleFieldsBox.appendChild(rowEl);
    });

    ruleModal.show();
}

/**
 * Add/Update a rule locally, re-render the table, and autosave to server.
 * Rolls back UI if save fails (handled in saveRulesToServer()).
 */
ruleForm.addEventListener("submit", async ev => {
    ev.preventDefault();
    const typeKey = typeKeyInput.value;
    const spec    = RULE_TYPES[typeKey];
    const uidVal  = ruleUidInput.value || uid();

    const data = { rule_uid: uidVal };
    spec.fields.forEach(f => {
        const el = ruleForm.querySelector(`[name="${f.name}"]`);
        let v = el ? el.value : null;

        if (f.type === "number") {
            if (v === "") v = null;
            else v = (f.num === "int") ? parseInt(v, 10) : parseFloat(v);
            if (Number.isNaN(v)) v = null;
        } else if (f.type === "select") {
            v = (v || "").toString().toUpperCase();
        } else {
            v = (v || "").trim();
        }
        data[f.name] = v;
    });

    spec.fields.forEach(f => {
        if (f.name === "tol_pct") return; // keep inheritance model for tolerance
        const v = data[f.name];
        if (v == null || v === "") {
            const sysDef = getSystemDefault(typeKey, f.name);
            if (sysDef != null) data[f.name] = sysDef;
        }
    });

    // Upsert locally
    const arr = toneState[spec.key] || (toneState[spec.key] = []);
    const i = arr.findIndex(r => r.rule_uid === uidVal);
    if (i >= 0) arr[i] = { ...arr[i], ...data };
    else arr.push({ ...spec.defaults, ...data });

    // Auto-open accordion if this is the first row for that type
    if ((toneState[spec.key] || []).length === 1) {
        const pane = document.getElementById(spec.paneId);
        if (pane) new bootstrap.Collapse(pane, { toggle: false }).show();
    }

    ruleModal.hide();
    renderToneTables();

    // Persist immediately (autosave)
    setSavingUI(true);
    try {
        await saveRulesToServer({ silent: false });
    } finally {
        setSavingUI(false);
    }
});


/* ===================================================================
   10) TONE RULES: DELETE + RENDER (AUTOSAVE)
   ===================================================================*/

/**
 * Delete a rule row, re-render, then persist immediately.
 * @param {"two_tone"|"long_tone"|"hi_low"|"pulsed"|"dtmf"} typeKey
 * @param {string} ruleUid
 */
async function deleteRule(typeKey, ruleUid) {
    const spec = RULE_TYPES[typeKey];
    const arr  = toneState[spec.key] || [];
    toneState[spec.key] = arr.filter(r => r.rule_uid !== ruleUid);
    renderToneLists(); // may hide table if now empty

    setSavingUI(true);
    try {
        await saveRulesToServer({ silent: false });
    } finally {
        setSavingUI(false);
    }
}


/* ===================================================================
   11) TONE RULES: STATE <-> SERVER + AUTOSAVE PLUMBING
   ===================================================================*/

/**
 * Replace in-memory toneState from a trigger payload and render.
 * @param {object} trigger
 */
function populateToneRules(trigger) {
    toneState = {
        two_tone_sets  : (trigger.two_tone_sets  || []).map(c => ({...c})),
        long_tone_sets : (trigger.long_tone_sets || []).map(c => ({...c})),
        hi_low_sets    : (trigger.hi_low_sets    || []).map(c => ({...c})),
        pulsed_sets    : (trigger.pulsed_sets    || []).map(c => ({...c})),
        dtmf_sequences : (trigger.dtmf_sequences || []).map(c => ({...c})),
    };
    renderToneLists();
    lastSavedToneState = deepClone(toneState);
}

/** Manual Save (kept as a safety valve) */
document.getElementById("saveRulesBtn").addEventListener("click", async ev => {
    ev.preventDefault();
    if (!curSys || !curTrig) return;

    setSavingUI(true);
    try {
        const rsp = await apiJson(`/api/systems/${curSys}/triggers/${curTrig}?full=1`, {
            method: "PATCH",
            body: buildRulesPayload()
        });

        showAlert(
            rsp.message || (rsp.success ? "Rules saved." : "Save failed"),
            rsp.success ? "success" : "danger"
        );

        if (rsp.success) {
            const ref = await apiJson(`/api/systems/${curSys}/triggers/${curTrig}?full=1`);
            if (ref.success) populateToneRules(ref.result);
        }
    } finally {
        setSavingUI(false);
    }
});

/** Reset to server state */
document.getElementById("resetRulesBtn").addEventListener("click", ev => {
    ev.preventDefault();
    trigSel.dispatchEvent(new Event("change"));
});

/**
 * Build the PATCH payload from toneState, adding sort_order based on UI order.
 * @returns {object}
 */
function buildRulesPayload() {
    const payload = {};
    for (const spec of Object.values(RULE_TYPES)) {
        const arr = toneState[spec.key] || [];
        payload[spec.key] = arr.map((r, idx) => ({ ...r, sort_order: idx }));
    }
    return payload;
}

/**
 * Toggle saving UI: disable action buttons and show a small "Saving…" badge.
 * @param {boolean} [saving=false]
 */
function setSavingUI(saving = false) {
    isSaving = !!saving;

    // Disable common buttons while saving to prevent double taps
    document.querySelectorAll('#saveRulesBtn, [data-rule-add], [data-edit-rule], [data-del-rule]')
        .forEach(btn => { if (btn) btn.disabled = isSaving; });

    // Show a tiny "Saving..." pill in the Tone Rules tab header
    let badge = document.getElementById('toneSavingBadge');
    if (!badge) {
        const tabBtn = document.querySelector('button[data-bs-target="#trig-tone"]');
        if (tabBtn) {
            badge = document.createElement('span');
            badge.id = 'toneSavingBadge';
            badge.className = 'badge bg-secondary ms-2 align-middle';
            badge.style.display = 'none';
            tabBtn.appendChild(badge);
        }
    }
    if (badge) {
        badge.textContent = 'Saving…';
        badge.style.display = isSaving ? '' : 'none';
    }
}

/** Debounced saver for reorder drags or bursts of quick edits */
const debouncedAutosave = debounce(() => saveRulesToServer({ silent: true }), 800);

/**
 * Persist current toneState to server.
 * Rolls back UI on failure so the user never ends up “saved visually but not really”.
 * @param {{silent?: boolean}} [opts]
 */
async function saveRulesToServer({ silent = false } = {}) {
    if (!AUTOSAVE_RULES || !curSys || !curTrig) {
        if (!silent) console.warn("Autosave skipped: missing curSys/curTrig or disabled.");
        return;
    }

    const prevSnapshot = deepClone(toneState);
    const payload = buildRulesPayload();

    let rsp;
    try {
        rsp = await apiJson(`/api/systems/${curSys}/triggers/${curTrig}?full=1`, {
            method: "PATCH",
            body: payload
        });
    } catch (e) {
        rsp = { success: false, message: e?.message || "Network error" };
    }

    if (!rsp.success) {
        // rollback
        toneState = prevSnapshot;
        renderToneTables();
        if (!silent) showAlert(rsp.message || "Failed to save tone rules.", "danger");
        return;
    }

    // Re-fetch authoritative state to keep client exactly in sync
    const ref = await apiJson(`/api/systems/${curSys}/triggers/${curTrig}?full=1`);
    if (ref.success) {
        populateToneRules(ref.result);
        lastSavedToneState = deepClone(toneState);
    }
    if (!silent) showAlert(rsp.message || "Rules saved.", "success");
}

function wireTriggerEnabledAutosave() {
    const sel = document.getElementById("updateTriggerEnabled");
    if (!sel) return;

    // remember last good value for rollback on failure
    sel.dataset.prev = sel.value;

    sel.addEventListener("change", async () => {
        if (!curSys || !curTrig) return;
        const newVal = sel.value; // "1" or "0"

        // optimistic UI: disable while saving
        sel.disabled = true;
        const rsp = await apiJson(`/api/systems/${curSys}/triggers/${curTrig}`, {
            method: "PATCH",
            body: { alert_trigger_enabled: newVal }
        });
        sel.disabled = false;

        if (!rsp.success) {
            showAlert(rsp.message || "Failed to update status.", "danger");
            // rollback
            sel.value = sel.dataset.prev ?? "1";
            return;
        }

        sel.dataset.prev = newVal;
        showAlert("Trigger status updated.", "success");
        // refresh the <select> labels so the “[off]” suffix reflects the new state
        await reloadTriggerSelect(curTrig);
    });
}
