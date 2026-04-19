/*****************************************************
 *               GLOBAL / HTML ELEMENTS
 *****************************************************/
const systemSelect = document.getElementById('systemSelect');
const addSystemForm = document.getElementById('addSystemForm');
const deleteSystemForm = document.getElementById('deleteSystemForm');
const submitAddSystemButton = document.getElementById("submitAddSystem");
const submitDeleteSystem = document.getElementById("submitDeleteSystem");

const systemEditCard = document.getElementById("systemEditCard");
const noSystemCard = document.getElementById('noSystemCard');

const systemDataCache = {};

let currentSystemId  = null;
let currentDiscordSettingId = null;
let currentDiscordFields    = [];

let currentMakeSettingId = null;   // PK from radio_system_make_settings
let currentMakeFields    = [];     // [{payload_field_id, key, value_template, …}]

let currentEmailAddresses = [];

let currentAddressExtractionSettings = null;
let currentGeocodingRegions = [];

let currentStorageSettings = null;

let currentIncidentClassificationSettings = null;

const GEO_LABELS_BY_COUNTRY = {
    US: {
        stateLabel: "State",
        statePlaceholder: "State code (e.g. PA)",
        countyLabel: "County / Region",
        regionsHelp: "Regions are tried in order when geocoding. Put your most common state/county pairs at the top."
    },
    CA: {
        stateLabel: "Province",
        statePlaceholder: "Province code (e.g. ON)",
        countyLabel: "Admin Area / Region",
        regionsHelp: "Regions are tried in order when geocoding. Put your most common province/admin-area pairs at the top."
    }
};

// Bootstrap's getInstance/getOrCreateInstance expect an Element, not a selector string.
// These helpers safely show/hide by CSS selector.
function _modalInstanceBySelector(sel) {
    const el = document.querySelector(sel);
    if (!el) return null;
    // Prefer existing instance, otherwise create
    return bootstrap.Modal.getInstance(el) || bootstrap.Modal.getOrCreateInstance(el);
}
function hideModalById(sel) {
    const inst = _modalInstanceBySelector(sel);
    if (inst) inst.hide();
}
function showModalById(sel) {
    const inst = _modalInstanceBySelector(sel);
    if (inst) inst.show();
}


/*****************************************************
 *               On Loaded Event
 *****************************************************/

/**
 * initialize page and event listeners.
 **/
document.addEventListener("DOMContentLoaded", function () {
    initFormListeners();
    initEventListeners();
    initTooltips();          // enable [data-bs-toggle="tooltip"]
    wireSmtpPasswordToggle();// SMTP password eye toggle
    wireIntegrationTestButtons();
    fetchSystems();
});


function initTooltips() {
    document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(el => {
        bootstrap.Tooltip.getOrCreateInstance(el);
    });
}

function wireSmtpPasswordToggle(){
    const pw  = document.getElementById("smtpPassword");
    const btn = document.getElementById("smtpPasswordToggle") || document.querySelector('#smtpPassword + .btn');
    if (pw && btn) {
        btn.addEventListener("click", () => {
            pw.type = (pw.type === "password") ? "text" : "password";
            btn.classList.toggle("btn-outline-success");
            btn.classList.toggle("btn-outline-secondary");
        });
    }
}

/*****************************************************
 *               Event Listeners
 *****************************************************/

function initEventListeners() {

    // System Select Event -> Select a system
    systemSelect.addEventListener("change", (e) => {
        const selectedId = systemSelect.value;

        currentSystemId = selectedId;

        /* toggle visibility */
        const editing = !!selectedId;
        systemEditCard.classList.toggle("d-none", !editing);
        noSystemCard.classList.toggle("d-none", editing);

        if (!editing) return;                // nothing selected; stop here

        systemEditCard.classList.remove("d-none");

        fetch(`/api/systems?radio_system_id=${selectedId}&include_config=1`)
            .then(r => r.json())
            .then(({result}) => {
                const system_data = result[0];

                // Populate Delete modal
                populateDeleteSystem(system_data);

                // Populate each tab’s form with system_data fields
                populateUpdateGeneral(system_data);
                populateUpdateUpload(system_data);
                refreshStorageSettings(system_data.radio_system_id);
                populateUpdateTone(system_data);
                populateUpdateTelegram(system_data);
                populateUpdateDiscord(system_data);
                populateUpdateMake(system_data);
                populateUpdatePushover(system_data);
                populateUpdateEmail(system_data);
                populateUpdateTranscribe(system_data);
                populateUpdateAddressExtraction(system_data);
                populateUpdateIncidentClassification(system_data);
                populateUpdateN8n(system_data);

            });
    });
}


/*****************************************************
 *               FETCH SYSTEMS HANDLER
 *****************************************************/

/**
 * Fetches available systems from the server and populates `systemSelect`.
 **/

function fetchSystems() {
    const loader = document.querySelector('.page-loader');
    if (loader) loader.style.display = 'flex';

    // remember previous selection
    const prev = currentSystemId || systemSelect.value || "";

    return fetch("/api/systems")
        .then(response => response.json())
        .then(result => {
            const systems = result.result || [];

            systemSelect.textContent = '';

            // Populate System Select

            // Create default Option
            const defaultOption = document.createElement("option");
            defaultOption.value = "";
            defaultOption.dataset.sysName = "";
            defaultOption.textContent = "Select System";
            systemSelect.appendChild(defaultOption);

            // Populate Select
            systems.forEach(system => {
                const option = document.createElement("option");
                option.value = String(system.radio_system_id);
                option.dataset.sysName = system.system_name;
                option.textContent = system.system_name;
                systemSelect.appendChild(option);
            });

            // restore selection if still available
            if (prev && systems.some(s => String(s.radio_system_id) === String(prev))) {
                systemSelect.value = String(prev);
                currentSystemId = String(prev);
            } else {
                systemSelect.value = "";
                currentSystemId = null;
            }

            // fire change to refresh the edit panel
            systemSelect.dispatchEvent(new Event("change"));

            return systems;
        })
        .catch(error => {
            console.error(`Error Fetching Systems: ${error}`);
            showAlert(`Error Fetching Systems: ${error}`, "danger");
            throw error;
        })
        .finally(() => {
            if (loader) loader.style.display = 'none';
        });
}


/*****************************************************
 *               Form Functions
 *****************************************************/

/**
 * Initialize the listeners on the various page forms
 *
 */
function initFormListeners() {
    // ---------------------------------------------------------------------
    // Helpers
    // ---------------------------------------------------------------------
    const on = (id, evt, fn) => document.getElementById(id)?.addEventListener(evt, fn);
    const mustHaveSystem = () => {
        if (!currentSystemId) { showAlert("Select a system first.", "warning"); return false; }
        return true;
    };
    const refreshAfterSystemCrud = async () => {
        await fetchSystems();
        closeAllModals();
        clearAllForms();
        systemSelect.dispatchEvent(new Event("change"));
    };

    // ---------------------------------------------------------------------
    // SYSTEM CRUD (Add / Delete)
    // ---------------------------------------------------------------------
    submitAddSystemButton.addEventListener("click", async (event) => {
        event.preventDefault();
        await handlePostFormSubmission("/api/systems", "addSystemForm", "addSystem");
        await refreshAfterSystemCrud();
    });

    submitDeleteSystem.addEventListener("click", async (event) => {
        event.preventDefault();
        const deleteSystemId = document.getElementById("deleteSystemId").value;
        await handleDeleteFormSubmission(`/api/systems/${deleteSystemId}`, "deleteSystemForm", "deleteSystem");
        await refreshAfterSystemCrud();
    });

    // ---------------------------------------------------------------------
    // GENERAL TAB (system name / decimal / stream url / api key)
    // ---------------------------------------------------------------------
    on("submitUpdateSystemGeneral", "click", async (ev) => {
        ev.preventDefault();
        await saveGeneralSettings();
    });

    on("regenerateApiKeyBtn", "click", async () => {
        if (!mustHaveSystem()) return;
        if (!confirm("Generate a NEW key?")) return;

        const csrf = document.querySelector("#updateSystemGeneralForm [name=_csrf_token]")?.value ?? "";
        const rsp = await apiJson(`/api/systems/${currentSystemId}/apikey`, {
            method: "POST",
            body: { _csrf_token: csrf }
        });

        if (!rsp.success) { showAlert(rsp.message, "danger"); return; }

        const apiKeyEl = document.getElementById("updateSystemApiKey");
        apiKeyEl.value = rsp.result.api_key;
        apiKeyEl.readOnly = true;
        apiKeyEl.classList.remove("is-invalid");

        showAlert("New API key generated.", "success");
    });

    // ---------------------------------------------------------------------
    // TONE TAB
    // ---------------------------------------------------------------------
    on("updateSystemToneForm", "submit", (ev) => {
        ev.preventDefault();
        saveToneSettings();
    });

    // ---------------------------------------------------------------------
    // TELEGRAM TAB
    // ---------------------------------------------------------------------
    on("updateSystemTelegramForm", "submit", (ev) => {
        ev.preventDefault();
        saveTelegramSettings();
    });

    // ---------------------------------------------------------------------
    // DISCORD TAB + Discord Field Modals
    // ---------------------------------------------------------------------
    on("updateSystemDiscordForm", "submit", async (ev) => {
        ev.preventDefault();
        await saveDiscordSettings();
    });

    on("discordFieldAddBtn", "click", (ev) => {
        ev.preventDefault();
        openDiscordFieldModalForAdd();
    });

    on("discordFieldForm", "submit", async (ev) => {
        ev.preventDefault();
        await saveDiscordFieldFromModal(ev.target);
    });

    // ---------------------------------------------------------------------
    // MAKE TAB + Make Field Modals
    // ---------------------------------------------------------------------
    on("makeFieldAddBtn", "click", (ev) => {
        ev.preventDefault();
        openMakeFieldModalForAdd();
    });

    on("updateSystemMakeForm", "submit", async (ev) => {
        ev.preventDefault();
        await saveMakeSettings();
    });

    on("makeFieldForm", "submit", async (ev) => {
        ev.preventDefault();
        await saveMakeField(ev.target);
    });

    on("makeFieldDeleteForm", "submit", async (ev) => {
        ev.preventDefault();
        if (!mustHaveSystem()) return;

        const id   = document.getElementById("makeFieldDeleteId").value;
        const csrf = ev.target.querySelector('[name=_csrf_token]')?.value ?? "";

        const resp = await apiJson(
            `/api/systems/${currentSystemId}/make/fields/${id}`,
            { method: "DELETE", body: { _csrf_token: csrf } }
        );

        if (!resp.success) { showAlert(resp.message, "danger"); return; }

        hideModalById("#makeFieldDeleteModal");
        showAlert(resp.message, "success");
        await refreshMake();
    });

    // ---------------------------------------------------------------------
    // PUSHOVER TAB
    // ---------------------------------------------------------------------
    on("updateSystemPushoverForm", "submit", (ev) => {
        ev.preventDefault();
        savePushoverSettings();
    });

    // ---------------------------------------------------------------------
    // EMAIL / SMTP TAB + Email Address Modals
    // ---------------------------------------------------------------------
    on("updateSystemSmtpForm", "submit", async (ev) => {
        ev.preventDefault();
        await saveEmailSettings();
    });

    on("emailAddressAddBtn", "click", (ev) => {
        ev.preventDefault();
        openEmailAddressModalForAdd();
    });

    on("emailAddressForm", "submit", async (ev) => {
        ev.preventDefault();
        await saveEmailAddress(ev.target);
    });

    on("emailAddressDeleteForm", "submit", async (ev) => {
        ev.preventDefault();
        await deleteEmailAddress();
    });

    // ---------------------------------------------------------------------
    // TRANSCRIBE TAB
    // ---------------------------------------------------------------------
    on("updateSystemTranscribeForm", "submit", (ev) => {
        ev.preventDefault();
        saveTranscribeSettings();
    });

    // ---------------------------------------------------------------------
    // UPLOAD / SPLIT TAB
    // ---------------------------------------------------------------------
    on("submitUpdateSystemUpload", "click", (ev) => {
        ev.preventDefault();
        saveUploadSettings();
    });

    // ---------------------------------------------------------------------
    // N8N TAB
    // ---------------------------------------------------------------------
    on("updateSystemN8nForm", "submit", async (ev) => {
        ev.preventDefault();
        await saveN8nSettings();
    });

    on("n8nSecretToggle", "click", (ev) => {
        ev.preventDefault();
        const i = document.getElementById("n8nJwtSecret");
        if (!i) return;
        i.type = (i.type === "password") ? "text" : "password";
    });

    on("n8nSecretGenerate", "click", (ev) => {
        ev.preventDefault();
        const i = document.getElementById("n8nJwtSecret");
        if (!i) return;
        i.value = cryptoRandomHex(64);
    });

    // ---------------------------------------------------------------------
    // ADDRESS EXTRACTION TAB + Region Modals
    // ---------------------------------------------------------------------
    on("updateSystemAddressExtractionForm", "submit", async (ev) => {
        ev.preventDefault();
        await saveAddressExtractionSettings();
    });

    on("addressRegionAddBtn", "click", (ev) => {
        ev.preventDefault();
        openAddressRegionModalForAdd();
    });

    on("addressRegionForm", "submit", async (ev) => {
        ev.preventDefault();
        await saveAddressRegionFromModal(ev.target);
    });

    on("addressRegionDeleteForm", "submit", async (ev) => {
        ev.preventDefault();
        await deleteAddressRegion();
    });

    // country selection affects labels/placeholders
    on("addressGeocodeCountry", "change", (e) => {
        applyGeocodeCountryLabels(e.target.value);
    });

    // ---------------------------------------------------------------------
    // STORAGE TAB
    // ---------------------------------------------------------------------
    on("updateSystemStorageForm", "submit", async (ev) => {
        ev.preventDefault();
        await saveStorageSettings();
    });

    on("storageType", "change", () => {
        updateStorageTypeVisibility();
    });

    on("sftpClearKeyBtn", "click", (ev) => {
        ev.preventDefault();
        onSftpClearKeyClick(ev);
    });

    // ---------------------------------------------------------------------
    // INCIDENT CLASSIFICATION TAB
    // ---------------------------------------------------------------------
    on("incidentSettingsSaveBtn", "click", async (ev) => {
        ev.preventDefault();
        await saveIncidentClassificationSettings();
    });
}

/*****************************************************
 *               Form Populate Functions
 *****************************************************/

/**
 * Populate Delete System Form
 */
function populateDeleteSystem(system_data) {
    const deleteSystemQuestion = document.getElementById("deleteSystemQuestion");
    const deleteSystemId = document.getElementById("deleteSystemId");
    const deleteSystemName = document.getElementById("deleteSystemName");

    deleteSystemQuestion.textContent = `Delete System ${system_data.system_name}?`;
    deleteSystemId.value = system_data.radio_system_id;
    deleteSystemName.value = system_data.system_name;

}

/**
 * Populate The Update General System Form
 */
function populateUpdateGeneral(system_data) {
    const generalTabTitle = document.getElementById("generalTabTitle");
    const updateSystemId = document.getElementById("updateSystemId");
    const updateSystemDecimal = document.getElementById("updateSystemDecimal");
    const updateSystemName = document.getElementById("updateSystemName");
    const updateSystemStreamURL = document.getElementById("updateSystemStreamURL");
    const updatePostToneDelay = document.getElementById("updatePostToneDelay");
    const apiKeyEl = document.getElementById("updateSystemApiKey");

    generalTabTitle.innerHTML = `General Settings - ${system_data.system_name}`;
    apiKeyEl.value = system_data.api_key || "";
    apiKeyEl.readOnly = true;
    apiKeyEl.classList.remove("is-invalid");

    updateSystemId.value = system_data.radio_system_id;
    updateSystemDecimal.value = system_data.system_decimal;
    updateSystemName.value = system_data.system_name;
    updateSystemStreamURL.value = system_data.stream_url;
    updatePostToneDelay.value = system_data.post_tone_delay || 0;
}

function gatherGeneralFormData () {
    const f = document.getElementById("updateSystemGeneralForm");
    const q = sel => f.querySelector(sel)?.value ?? "";
    const postToneDelayEl = document.getElementById("updatePostToneDelay");
    console.log("Input element value:", postToneDelayEl.value);
    console.log("Input element raw:", document.querySelector("[name=post_tone_delay]")?.value);
    return {
        _csrf_token     : q("[name=_csrf_token]"),
        radio_system_id : Number(q("#updateSystemId")),
        system_decimal  : Number(q("#updateSystemDecimal")),
        system_name     : q("#updateSystemName").trim(),
        stream_url      : q("#updateSystemStreamURL").trim(),
        api_key         : q("#updateSystemApiKey").trim(),
        post_tone_delay : Number(document.getElementById("updatePostToneDelay").value) || 0,
    };
}

async function saveGeneralSettings () {
    const data = gatherGeneralFormData();
    console.log("Sending post_tone_delay:", data.post_tone_delay);
    const url  = `/api/systems/${data.radio_system_id}`;
    const resp = await apiJson(url, {method:"PATCH", body:data});

    if (!resp.success) { showAlert(resp.message,"danger"); return; }

    /* update the drop-down label immediately */
    const opt = systemSelect.querySelector(`option[value="${data.radio_system_id}"]`);
    if (opt) opt.textContent = data.system_name;

    /* re-render form with canonical data from server */
    populateUpdateGeneral(resp.result);

    showAlert(resp.message,"success");
}

function populateUpdateUpload (system_data){
    const up = system_data.upload ?? {};

    document.getElementById("uploadTabTitle").textContent =
        `Upload / Split Settings – ${system_data.system_name}`;

    document.getElementById("updateUploadSystemId").value   = system_data.radio_system_id;
    document.getElementById("updateUploadSystemName").value = system_data.system_name;

    /* master switch */
    document.getElementById("uploadSplitEnabled").checked =
        Number(up.split_enabled) === 1;

    /* knobs */
    document.getElementById("uploadMinVoice")    .value = up.audio_min_length    ?? 2.0;
    document.getElementById("tailMinVoice")    .value = up.tail_min_voice_sec    ?? 3.0;
    document.getElementById("vadMinSpeech")    .value = up.vad_min_speech_ratio  ?? 0.15;
    document.getElementById("voiceRmsDbfs")    .value = up.voice_rms_dbfs        ?? -35.0;
    document.getElementById("maxSplitInterval").value = up.max_split_interval    ?? 30.0;
    document.getElementById("maxSplitLength")  .value = up.max_split_length      ?? 30.0;
}

function gatherUploadSettingsFormData (){
    const f = document.getElementById("updateSystemUploadForm");
    const q = sel=>f.querySelector(sel)?.value ?? "";

    return {
        _csrf_token          : q('[name=_csrf_token]'),
        radio_system_id      : Number(q("#updateUploadSystemId")),
        split_enabled        : document.getElementById("uploadSplitEnabled").checked ? 1 : 0,
        audio_min_length     : parseFloat(q("#uploadMinVoice")),
        tail_min_voice_sec   : parseFloat(q("#tailMinVoice")),
        vad_min_speech_ratio : parseFloat(q("#vadMinSpeech")),
        voice_rms_dbfs       : parseFloat(q("#voiceRmsDbfs")),
        max_split_interval   : parseFloat(q("#maxSplitInterval")),
        max_split_length     : parseFloat(q("#maxSplitLength"))
    };
}

async function saveUploadSettings (){
    const data = gatherUploadSettingsFormData();
    const url  = `/api/systems/${data.radio_system_id}/upload/settings`;

    const resp = await apiJson(url, {method:"PATCH", body:data});
    if (!resp.success){
        showAlert(resp.message,"danger");
        return;
    }

    /* re-render with canonical values */
    populateUpdateUpload({
        radio_system_id : data.radio_system_id,
        system_name     : document.getElementById("updateUploadSystemName").value,
        upload          : resp.result
    });
    showAlert(resp.message,"success");
}

function updateStorageTypeVisibility() {
    const type = document.getElementById("storageType")?.value || "LOCAL";
    const sftpSection = document.getElementById("storageSftpSection");
    const s3Section   = document.getElementById("storageS3Section");

    if (sftpSection) {
        sftpSection.classList.toggle("d-none", type !== "SFTP");
    }
    if (s3Section) {
        s3Section.classList.toggle("d-none", type !== "S3");
    }

}

async function onSftpClearKeyClick(evt) {
    evt.preventDefault();

    const textarea = document.getElementById("sftpSshKey");
    const help     = document.getElementById("sftpSshKeyHelp");
    const btn      = document.getElementById("sftpClearKeyBtn");
    const form     = document.getElementById("updateSystemStorageForm");

    if (!textarea || !form) return;

    if (!confirm("Clear the stored SSH key for this system? This cannot be undone.")) {
        return;
    }

    const sysIdInput = document.getElementById("updateStorageSystemId");
    const radioSystemId = Number(sysIdInput?.value || currentStorageSettings?.radio_system_id || 0);
    if (!radioSystemId) {
        showAlert("Missing system ID; cannot clear SSH key.", "danger");
        return;
    }

    const csrfToken = form.querySelector('[name="_csrf_token"]')?.value || "";

    // Disable button while request in flight
    if (btn) {
        btn.disabled = true;
        btn.classList.add("disabled");
    }

    try {
        const url = `/api/systems/${radioSystemId}/storage/settings`;
        const resp = await apiJson(url, {
            method: "PATCH",
            body: {
                _csrf_token       : csrfToken,
                sftp_clear_ssh_key: 1
            }
        });

        if (!resp.success) {
            showAlert(resp.message || "Failed to clear SSH key.", "danger");
            if (btn) {
                btn.disabled = false;
                btn.classList.remove("disabled");
            }
            return;
        }

        // Refresh UI from server-normalized config
        if (resp.result) {
            populateUpdateStorage(resp.result);
        } else {
            // Fallback: just locally clear UI
            textarea.value = "";
            textarea.placeholder = "";

            if (help) {
                help.textContent =
                    "No SSH key exists yet. Paste a PEM private key here to create one. If left blank, nothing is changed.";
                help.classList.add("text-muted");
                help.classList.remove("text-warning");
            }

            if (btn) {
                btn.disabled = true;
                btn.classList.add("disabled");
            }
        }

        showAlert(resp.message || "SSH key cleared.", "success");
    } catch (err) {
        console.error("Error clearing SSH key:", err);
        showAlert("Unexpected error while clearing SSH key.", "danger");
        if (btn) {
            btn.disabled = false;
            btn.classList.remove("disabled");
        }
    }
}

/**
 * Populate the Storage tab from a storage config object
 * returned by /api/systems/<id>/storage/settings.
 */
function populateUpdateStorage(storage) {
    if (!storage) {
        return;
    }

    currentStorageSettings = storage;

    const titleEl = document.getElementById("storageTabTitle");
    if (titleEl) {
        titleEl.textContent = `Storage Settings – ${storage.system_name || ""}`;
    }

    const sysIdEl   = document.getElementById("updateStorageSystemId");
    const sysNameEl = document.getElementById("updateStorageSystemName");
    if (sysIdEl)   sysIdEl.value   = storage.radio_system_id ?? "";
    if (sysNameEl) sysNameEl.value = storage.system_name     ?? "";

    // Storage type + path
    const typeSelect = document.getElementById("storageType");
    if (typeSelect) {
        typeSelect.value = storage.storage_type || "LOCAL";
    }

    const pathInput = document.getElementById("storagePathPattern");
    if (pathInput) {
        pathInput.value = storage.path_pattern || "%Y/%m/%d";
    }

    // SFTP block
    const sftp = storage.sftp || {};
    const sftpHost          = document.getElementById("sftpHost");
    const sftpPort          = document.getElementById("sftpPort");
    const sftpTimeout       = document.getElementById("sftpTimeout");
    const sftpUsername      = document.getElementById("sftpUsername");
    const sftpPassword      = document.getElementById("sftpPassword");
    const sftpUseServerKey = document.getElementById("sftpUseServerKey");
    const sftpSshKey        = document.getElementById("sftpSshKey");
    const sftpSshKeyHelp    = document.getElementById("sftpSshKeyHelp");
    const sftpRemotePath    = document.getElementById("sftpRemotePath");
    const sftpBaseUrl       = document.getElementById("sftpBaseUrl");
    const sftpMaxRetries    = document.getElementById("sftpMaxRetries");
    const sftpClearKeyBtn   = document.getElementById("sftpClearKeyBtn");

    if (sftpHost)       sftpHost.value       = sftp.host        ?? "";
    if (sftpPort)       sftpPort.value       = sftp.port        ?? "";
    if (sftpTimeout)    sftpTimeout.value    = sftp.timeout_s   ?? "";
    if (sftpUsername)   sftpUsername.value   = sftp.username    ?? "";
    if (sftpPassword)   sftpPassword.value   = sftp.password    ?? "";
    if (sftpUseServerKey) sftpUseServerKey.checked = !!sftp.use_ssh_key;

    const hasServerKey = !!sftp.ssh_key_exists;
    if (sftpSshKey) {
        sftpSshKey.value = "";

        // Optional cosmetic: hint via placeholder
        if (hasServerKey) {
            sftpSshKey.placeholder = "SSH key present; pasting a new PEM here will overwrite it.";
        } else {
            sftpSshKey.placeholder = "";
        }
    }

    if (sftpSshKeyHelp) {
        if (hasServerKey) {
            sftpSshKeyHelp.textContent =
                "A SSH key already exists for this system. Pasting a new PEM here will overwrite the existing key.";
            sftpSshKeyHelp.classList.remove("text-muted");
            sftpSshKeyHelp.classList.add("text-warning");
        } else {
            sftpSshKeyHelp.textContent =
                "No SSH key exists yet. Paste a PEM private key here to create one. If left blank, nothing is changed.";
            sftpSshKeyHelp.classList.add("text-muted");
            sftpSshKeyHelp.classList.remove("text-warning");
        }
    }

    if (sftpClearKeyBtn) {
        sftpClearKeyBtn.disabled = !hasServerKey;
        sftpClearKeyBtn.classList.toggle("disabled", !hasServerKey);
    }

    if (sftpRemotePath) sftpRemotePath.value = sftp.remote_path ?? "";
    if (sftpBaseUrl)    sftpBaseUrl.value    = sftp.base_url    ?? "";
    if (sftpMaxRetries) sftpMaxRetries.value = sftp.max_retries ?? "";



    // S3 block
    const s3 = storage.s3 || {};
    const s3Bucket          = document.getElementById("s3Bucket");
    const s3Region          = document.getElementById("s3Region");
    const s3AccessKeyId     = document.getElementById("s3AccessKeyId");
    const s3SecretAccessKey = document.getElementById("s3SecretAccessKey");
    const s3EndpointUrl     = document.getElementById("s3EndpointUrl");
    const s3ObjectKeyPrefix = document.getElementById("s3ObjectKeyPrefix");

    if (s3Bucket)          s3Bucket.value          = s3.bucket            ?? "";
    if (s3Region)          s3Region.value          = s3.region            ?? "";
    if (s3AccessKeyId)     s3AccessKeyId.value     = s3.access_key_id     ?? "";
    if (s3SecretAccessKey) s3SecretAccessKey.value = s3.secret_access_key ?? "";
    if (s3EndpointUrl)     s3EndpointUrl.value     = s3.endpoint_url      ?? "";
    if (s3ObjectKeyPrefix) s3ObjectKeyPrefix.value = s3.object_key_prefix ?? "";

    // Show/hide sections appropriately
    updateStorageTypeVisibility();
}

function gatherStorageSettingsFormData() {
    const form = document.getElementById("updateSystemStorageForm");
    if (!form) return null;
    const q = sel => form.querySelector(sel)?.value ?? "";

    const sshKeyEl      = form.querySelector("#sftpSshKey");
    const sshKeyRaw     = sshKeyEl ? sshKeyEl.value : "";
    const useServerKeyEl = form.querySelector("#sftpUseServerKey");

    const data = {
        _csrf_token          : q('[name="_csrf_token"]'),
        radio_system_id      : Number(q("#updateStorageSystemId")) || null,
        storage_type         : q("#storageType"),

        path_pattern         : q("#storagePathPattern").trim(),

        // SFTP
        sftp_host            : q("#sftpHost").trim(),
        sftp_port            : q("#sftpPort").trim(),
        sftp_username        : q("#sftpUsername").trim(),
        sftp_password        : q("#sftpPassword"),
        sftp_remote_path     : q("#sftpRemotePath").trim(),
        sftp_base_url        : q("#sftpBaseUrl").trim(),
        sftp_timeout_s       : q("#sftpTimeout").trim(),
        sftp_max_retries     : q("#sftpMaxRetries").trim(),
        sftp_use_ssh_key     : useServerKeyEl && useServerKeyEl.checked ? 1 : 0,
        sftp_ssh_key         : sshKeyRaw.trim(),

        sftp_clear_ssh_key   : 0,

        // S3
        s3_bucket            : q("#s3Bucket").trim(),
        s3_region            : q("#s3Region").trim(),
        s3_access_key_id     : q("#s3AccessKeyId").trim(),
        s3_secret_access_key : q("#s3SecretAccessKey"),
        s3_endpoint_url      : q("#s3EndpointUrl").trim(),
        s3_object_key_prefix : q("#s3ObjectKeyPrefix").trim()
    };

    return data;

}

async function saveStorageSettings() {
    const data = gatherStorageSettingsFormData();
    if (!data || !data.radio_system_id) {
        showAlert("Missing system ID; cannot save storage settings.", "danger");
        return;
    }

    const url  = `/api/systems/${data.radio_system_id}/storage/settings`;
    const resp = await apiJson(url, {method: "PATCH", body: data});

    if (!resp.success) {
        showAlert(resp.message || "Failed to save storage settings.", "danger");
        return;
    }

    // resp.result should be the normalized config from _fetch_storage_settings_obj
    if (resp.result) {
        populateUpdateStorage(resp.result);
    }

    showAlert(resp.message || "Storage settings saved.", "success");
}

async function refreshStorageSettings(systemId) {
    const id = systemId || currentSystemId;
    if (!id) return;

    const resp = await apiJson(`/api/systems/${id}/storage/settings`, {method: "GET"});
    if (!resp.success) {
        showAlert(resp.message || "Failed to fetch storage settings.", "danger");
        return;
    }

    if (resp.result) {
        populateUpdateStorage(resp.result);
    }
}

function populateUpdateTone(system_data) {
    // ✅ correct title element id
    const toneTabTitle = document.getElementById("toneTabTitle");

    const updateToneSystemId   = document.getElementById("updateToneSystemId");
    const updateToneSystemName = document.getElementById("updateToneSystemName");

    const toneFinderSelect     = document.getElementById("toneFinderSelect");
    const toneMatchThreshold   = document.getElementById("toneMatchThreshold");
    const toneSNRThreshold     = document.getElementById("toneSNRThreshold");
    const toneAMin             = document.getElementById("toneAMin");
    const toneBMin             = document.getElementById("toneBMin");
    const toneSeparationHZ     = document.getElementById("toneSeparationHZ");
    const toneHiLowInterval    = document.getElementById("toneHiLowInterval");
    const toneHiLowAlternations= document.getElementById("toneHiLowAlternations");
    const toneLongMin          = document.getElementById("toneLongMin");

    // pulsed
    const pulsedMinCycles = document.getElementById("pulsedMinCycles");
    const pulsedMinOn     = document.getElementById("pulsedMinOn");
    const pulsedMaxOn     = document.getElementById("pulsedMaxOn");
    const pulsedMinOff    = document.getElementById("pulsedMinOff");
    const pulsedMaxOff    = document.getElementById("pulsedMaxOff");

    //dtmf
    const dtmfMinMs        = document.getElementById("dtmfMinMs");
    const dtmfMergeMs      = document.getElementById("dtmfMergeMs");
    const dtmfStartOffset  = document.getElementById("dtmfStartOffsetMs");
    const dtmfEndOffset    = document.getElementById("dtmfEndOffsetMs");
    const dtmfSequenceGapS = document.getElementById("dtmfSequenceGapS");


    const tone = system_data.tone ?? {};

    toneTabTitle.innerText      = `Tone Settings - ${system_data.system_name}`;
    updateToneSystemId.value    = system_data.radio_system_id;
    updateToneSystemName.value  = system_data.system_name;

    // on/off select expects "1"/"0" strings
    toneFinderSelect.value      = Number(tone.enabled) === 1 ? "1" : "0";

    // existing knobs
    toneMatchThreshold.value    = tone.matching_threshold ?? 2.0;
    toneSNRThreshold.value      = tone.fe_snr_above_noise_db ?? 1.0;
    toneAMin.value              = tone.tone_a_min_length ?? 0.7;
    toneBMin.value              = tone.tone_b_min_length ?? 2.6;
    toneSeparationHZ.value      = tone.two_tone_min_pair_separation_hz ?? 10;
    toneHiLowInterval.value     = tone.hi_low_interval ?? 0.2;
    toneHiLowAlternations.value = tone.hi_low_min_alternations ?? 6;
    toneLongMin.value           = tone.long_tone_min_length ?? 1.8;

    pulsedMinCycles.value       = tone.pulsed_min_cycles  ?? 6;
    pulsedMinOn.value           = tone.pulsed_min_on_ms   ?? 120;
    pulsedMaxOn.value           = tone.pulsed_max_on_ms   ?? 900;
    pulsedMinOff.value          = tone.pulsed_min_off_ms  ?? 25;
    pulsedMaxOff.value          = tone.pulsed_max_off_ms  ?? 350;

    dtmfMinMs.value       = tone.dtmf_min_ms          ?? 100;
    dtmfMergeMs.value     = tone.dtmf_merge_ms        ?? 75;
    dtmfStartOffset.value = tone.dtmf_start_offset_ms ?? -20;
    dtmfEndOffset.value   = tone.dtmf_end_offset_ms   ?? 20;
    dtmfSequenceGapS.value = tone.dtmf_sequence_gap_s ?? 0.3

}

function gatherToneSettingsFormData() {
    const form = document.getElementById("updateSystemToneForm");
    const q = sel => form.querySelector(sel)?.value ?? "";

    return {
        _csrf_token             : q("[name=_csrf_token]"),
        radio_system_id         : Number(q("#updateToneSystemId")),
        tone_finder_enabled     : Number(q("#toneFinderSelect")),
        matching_threshold      : parseFloat(q("#toneMatchThreshold")),
        fe_snr_above_noise_db   : parseFloat(q("#toneSNRThreshold")),
        tone_a_min_length       : parseFloat(q("#toneAMin")),
        tone_b_min_length       : parseFloat(q("#toneBMin")),
        two_tone_min_pair_separation_hz : parseInt(q("#toneSeparationHZ")),
        hi_low_interval         : parseFloat(q("#toneHiLowInterval")),
        hi_low_min_alternations : parseInt(q("#toneHiLowAlternations"), 10),
        long_tone_min_length    : parseFloat(q("#toneLongMin")),

        pulsed_min_cycles       : parseInt(q("#pulsedMinCycles"), 10),
        pulsed_min_on_ms        : parseInt(q("#pulsedMinOn"), 10),
        pulsed_max_on_ms        : parseInt(q("#pulsedMaxOn"), 10),
        pulsed_min_off_ms       : parseInt(q("#pulsedMinOff"), 10),
        pulsed_max_off_ms       : parseInt(q("#pulsedMaxOff"), 10),
        dtmf_min_ms          : parseInt(q("#dtmfMinMs"), 10),
        dtmf_merge_ms        : parseInt(q("#dtmfMergeMs"), 10),
        dtmf_start_offset_ms : parseInt(q("#dtmfStartOffsetMs"), 10),
        dtmf_end_offset_ms   : parseInt(q("#dtmfEndOffsetMs"), 10),
        dtmf_sequence_gap_s  : parseFloat(q("#dtmfSequenceGapS")),
    };
}

async function saveToneSettings() {
    const data = gatherToneSettingsFormData();
    const url  = `/api/systems/${data.radio_system_id}/tone/settings`;
    const resp = await apiJson(url, {method:"PATCH", body:data});

    if (!resp.success) { showAlert(resp.message,"danger"); return; }

    populateUpdateTone({
        radio_system_id : data.radio_system_id,
        system_name     : document.getElementById("updateToneSystemName").value,
        tone            : resp.result
    });
    showAlert(resp.message,"success");
}

function populateUpdateTelegram(system_data) {
    const telegramTabTitle = document.getElementById("telegramTabTitle");
    const updateTelegramSystemId = document.getElementById("updateTelegramSystemId");
    const updateTelegramSystemName = document.getElementById("updateTelegramSystemName");
    const telegramSelect = document.getElementById("telegramSelect");
    const telegramChannelId = document.getElementById("telegramChannelId");
    const telegramBotToken = document.getElementById("telegramBotToken");
    const telegramAlertBody = document.getElementById("telegramAlertBody");

    telegramTabTitle.innerText = `Telegram Settings - ${system_data.system_name}`;
    updateTelegramSystemId.value = system_data.radio_system_id;
    updateTelegramSystemName.value = system_data.system_name;

    telegramSelect.value = Number(system_data.telegram.enabled) === 1 ? "1" : "0";

    telegramChannelId.value = system_data.telegram.channel_id;
    telegramBotToken.value = system_data.telegram.bot_token;
    telegramAlertBody.value = system_data.telegram.message_body;

    updateIntegrationTestButtonsState();

}

function populateUpdateDiscord(system_data) {
    if (!system_data) return;
    const discord = system_data.discord ?? {};

    // capture state
    currentDiscordSettingId = discord.discord_setting_id ?? null;
    currentDiscordFields    = Array.isArray(discord.fields) ? discord.fields.slice() : [];

    // hidden identifiers
    const updateDiscordSystemId   = document.getElementById("updateDiscordSystemId");
    const updateDiscordSystemName = document.getElementById("updateDiscordSystemName");
    if (updateDiscordSystemId)   updateDiscordSystemId.value   = system_data.radio_system_id ?? "";
    if (updateDiscordSystemName) updateDiscordSystemName.value = system_data.system_name ?? "";

    // enabled select
    const discordEnabledEl = document.getElementById("discordSelect");
    if (discordEnabledEl) {
        discordEnabledEl.value = Number(discord.enabled) === 1 ? "1" : "0";
    }

    // webhook URL
    const discordWebhookURL = document.getElementById("discordWebhookURL");
    if (discordWebhookURL) discordWebhookURL.value = discord.webhook_url ?? "";

    // embed title
    const discordEmbedTitle = document.getElementById("discordEmbedTitle");
    if (discordEmbedTitle) discordEmbedTitle.value = discord.embed_title;

    // embed color (picker only)
    const discordEmbedColor = document.getElementById("discordEmbedColor");
    if (discordEmbedColor) {

        discordEmbedColor.value = discord.embed_color || "#F04646";

    }

    // toggle map render
    const discordRenderMap = document.getElementById("discordRenderMap");
    if (discordRenderMap) {
        // support either backend naming: render_map OR discord_render_map
        const v = (discord.render_map ?? 0);
        discordRenderMap.checked = Number(v) === 1;
    }

    // toggle attach audio
    const discordAttachAudio = document.getElementById("discordAttachAudio");
    if (discordAttachAudio) {
        const v = (discord.attach_audio ?? 0);
        discordAttachAudio.checked = Number(v) === 1;
    }

    // embed footer
    const discordEmbedFooter = document.getElementById("discordEmbedFooter");
    if (discordEmbedFooter) discordEmbedFooter.value = discord.embed_footer;

    // render table w/ action handlers
    populateDiscordFieldsTable(currentDiscordFields, {
        onEdit:   openDiscordFieldModalForEdit,
        onDelete: confirmDeleteDiscordField,
        onMove:   reorderDiscordField
    });

    updateIntegrationTestButtonsState();
}

function gatherTelegramSettingsFormData() {
    const form = document.getElementById("updateSystemTelegramForm");
    const q = sel => form.querySelector(sel)?.value ?? "";
    return {
        _csrf_token          : q('[name=_csrf_token]'),
        radio_system_id      : Number(q("#updateTelegramSystemId")),
        telegram_enabled     : Number(q("#telegramSelect")),
        telegram_channel_id  : q("#telegramChannelId").trim() || null,
        telegram_bot_token   : q("#telegramBotToken").trim()  || null,
        telegram_message_body: q("#telegramAlertBody").trim()
    };
}

async function saveTelegramSettings() {
    const data = gatherTelegramSettingsFormData();
    const url  = `/api/systems/${data.radio_system_id}/telegram/settings`;
    const resp = await apiJson(url, {method:"PATCH", body:data});

    if (!resp.success) { showAlert(resp.message,"danger"); return; }

    populateUpdateTelegram({
        radio_system_id : data.radio_system_id,
        system_name     : document.getElementById("updateTelegramSystemName").value,
        telegram        : resp.result
    });
    showAlert(resp.message,"success");
}

function populateDiscordFieldsTable(fields, opts = {}) {
    const {
        showActions = true,
        onEdit     = () => {},
        onDelete   = () => {},
        onMove     = () => {},
    } = opts;

    const tbody = document.querySelector("#discordFieldsTable tbody");
    if (!tbody) return;
    tbody.innerHTML = "";

    if (!Array.isArray(fields) || fields.length === 0) {
        tbody.innerHTML = `
      <tr>
        <td colspan="7" class="text-center text-body-secondary py-3">
          No embed fields defined. Click "Add Field" to create one.
        </td>
      </tr>`;
        return;
    }

    const sorted = [...fields].sort((a,b)=>(a.sort_order ?? 0) - (b.sort_order ?? 0));

    sorted.forEach((f, idx) => {
        const row = document.createElement("tr");
        const enabled = !!f.field_enabled;
        const inline  = !!f.field_inline;
        if (!enabled) row.classList.add("table-secondary","opacity-75");

        let actionsHtml = "";
        if (showActions) {
            const upDisabled   = idx === 0          ? "disabled" : "";
            const downDisabled = idx === sorted.length-1 ? "disabled" : "";
            actionsHtml = `
      <div class="btn-group btn-group-sm" role="group">
        <button type="button" class="btn btn-outline-secondary js-df-move-up"   data-id="${f.embed_field_id}" ${upDisabled}   title="Move up"><i class="bi bi-arrow-up"></i></button>
        <button type="button" class="btn btn-outline-secondary js-df-move-down" data-id="${f.embed_field_id}" ${downDisabled} title="Move down"><i class="bi bi-arrow-down"></i></button>
        <button type="button" class="btn btn-outline-primary  js-df-edit"      data-id="${f.embed_field_id}" title="Edit"><i class="bi bi-pencil"></i></button>
        <button type="button" class="btn btn-outline-danger   js-df-del"       data-id="${f.embed_field_id}" title="Delete"><i class="bi bi-trash"></i></button>
      </div>`;
        }

        row.innerHTML = `
      <td>${idx+1}</td>
      <td>${escapeHtml(f.field_label ?? "")}</td>
      <td class="d-none d-md-table-cell"><code>${escapeHtml(f.field_key ?? "")}</code></td>
      <td class="d-none d-lg-table-cell small text-break">${escapeHtml(f.field_template ?? "")}</td>
      <td>${inline ? "Yes" : "No"}</td>
      <td>${enabled ? "Yes" : "No"}</td>
      <td>${actionsHtml}</td>
    `;
        tbody.appendChild(row);
    });

    if (!showActions) return;
    tbody.querySelectorAll(".js-df-edit").forEach(btn   => btn.addEventListener("click", () => onEdit(btn.dataset.id)));
    tbody.querySelectorAll(".js-df-del").forEach(btn    => btn.addEventListener("click", () => onDelete(btn.dataset.id)));
    tbody.querySelectorAll(".js-df-move-up").forEach(btn=> btn.addEventListener("click", () => onMove(btn.dataset.id, -1)));
    tbody.querySelectorAll(".js-df-move-down").forEach(btn=> btn.addEventListener("click", () => onMove(btn.dataset.id, +1)));
}

function escapeHtml(str) {
    if (str == null) return "";
    return String(str).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

function gatherDiscordSettingsFormData() {
    const form = document.getElementById("updateSystemDiscordForm");
    const csrf = form.querySelector('[name="_csrf_token"]')?.value ?? "";
    const sysId = Number(document.getElementById("updateDiscordSystemId")?.value) || null;

    return {
        _csrf_token:          csrf,
        radio_system_id:      sysId,
        discord_enabled:      Number(document.getElementById("discordSelect")?.value) || 0,
        discord_webhook_url:  ((document.getElementById("discordWebhookURL")?.value) ?? "").trim() || null,
        discord_embed_title:  ((document.getElementById("discordEmbedTitle")?.value) ?? "").trim() || null,
        discord_embed_color:  document.getElementById("discordEmbedColor")?.value || null,
        discord_embed_footer: ((document.getElementById("discordEmbedFooter")?.value) ?? "").trim() || null,
        discord_render_map:   document.getElementById("discordRenderMap").checked ? 1 : 0,
        discord_attach_audio: document.getElementById("discordAttachAudio").checked ? 1 : 0,
    };
}

async function saveDiscordSettings() {
    const data = gatherDiscordSettingsFormData();
    if (!data.radio_system_id) {
        showAlert("Missing system ID; cannot save Discord settings.", "danger");
        return;
    }

    const url = `/api/systems/${data.radio_system_id}/discord/settings`;
    const resp = await apiJson(url, {method:"PATCH", body:data});

    if (!resp.success) {
        showAlert(resp.message || "Failed to save Discord settings.", "danger");
        return;
    }

    // Update UI from response
    const sysObj = {
        radio_system_id: data.radio_system_id,
        system_name: document.getElementById("updateDiscordSystemName")?.value ?? "",
        discord: resp.result
    };
    populateUpdateDiscord(sysObj);

    showAlert(resp.message || "Discord settings saved.", "success");
}

function openDiscordFieldModalForAdd() {
    const form = document.getElementById("discordFieldForm");
    if (!form) return;

    document.getElementById("discordFieldModalLabel").textContent = "Add Discord Field";

    form.reset();
    form.querySelector("#discordFieldId").value        = "";
    form.querySelector("#discordFieldSystemId").value  = currentSystemId ?? "";
    form.querySelector("#discordFieldSettingId").value = currentDiscordSettingId ?? "";
    form.querySelector("#discordFieldEnabled").value   = "1";
    form.querySelector("#discordFieldInline").value    = "0";

    const maxSort = currentDiscordFields.reduce((m,f)=>Math.max(m, f.sort_order ?? 0), 0);
    form.querySelector("#discordFieldSortOrder").value = maxSort + 10;
}

function openDiscordFieldModalForEdit(embed_field_id) {
    const f = currentDiscordFields.find(x => String(x.embed_field_id) === String(embed_field_id));
    if (!f) { showAlert("Could not locate field to edit.", "warning"); return; }

    const form = document.getElementById("discordFieldForm");
    if (!form) return;

    document.getElementById("discordFieldModalLabel").textContent = `Edit Field: ${f.field_label}`;

    form.querySelector("#discordFieldId").value        = f.embed_field_id;
    form.querySelector("#discordFieldSystemId").value  = currentSystemId ?? "";
    form.querySelector("#discordFieldSettingId").value = currentDiscordSettingId ?? "";
    form.querySelector("#discordFieldSortOrder").value = f.sort_order ?? 0;
    form.querySelector("#discordFieldEnabled").value   = f.field_enabled ? "1" : "0";
    form.querySelector("#discordFieldInline").value    = f.field_inline  ? "1" : "0";
    form.querySelector("#discordFieldLabel").value     = f.field_label   ?? "";
    form.querySelector("#discordFieldKey").value       = f.field_key     ?? "";
    form.querySelector("#discordFieldTemplate").value  = f.field_template?? "";

    const modalEl = document.getElementById("discordFieldModal");
    if (modalEl && window.bootstrap) {
        bootstrap.Modal.getOrCreateInstance(modalEl).show();
    }
}

function gatherDiscordFieldFormData(formEl) {
    const form = formEl || document.getElementById("discordFieldForm");
    const q = (sel) => form?.querySelector(sel)?.value ?? "";

    const efid  = q("#discordFieldId").trim();
    const dsid  = q("#discordFieldSettingId").trim();
    const rsid  = q("#discordFieldSystemId").trim();
    const key   = q("#discordFieldKey").trim();
    const label = q("#discordFieldLabel").trim();
    const tpl   = q("#discordFieldTemplate").trim();
    const inline  = Number(q("#discordFieldInline"))   || 0;
    const enabled = Number(q("#discordFieldEnabled"))  || 0;
    const sort    = Number(q("#discordFieldSortOrder"))|| 0;

    return {
        _csrf_token:        q('[name="_csrf_token"]'),
        embed_field_id:     efid  || null,
        discord_setting_id: dsid  || null,
        radio_system_id:    rsid  || null,
        field_key:          key,
        field_label:        label,
        field_template:     tpl,
        field_inline:       inline,
        field_enabled:      enabled,
        sort_order:         sort,
    };
}

async function saveDiscordFieldFromModal(formEl) {
    const payload = gatherDiscordFieldFormData(formEl);

    let resp;
    if (payload.embed_field_id) {
        // update existing field
        resp = await apiJson(`/api/systems/${payload.radio_system_id}/discord/fields/${payload.embed_field_id}`, {
            method: "PATCH",
            body: payload
        });
    } else {
        // create new field; parent system in URL
        resp = await apiJson(`/api/systems/${payload.radio_system_id}/discord/fields`, {
            method: "POST",
            body: payload
        });
    }

    if (!resp.success) {
        showAlert(resp.message || "Failed to save Discord field.", "danger");
        return;
    }

    // Close modal
    const modalEl = document.getElementById("discordFieldModal");
    if (modalEl && window.bootstrap) {
        bootstrap.Modal.getOrCreateInstance(modalEl).hide();
    }

    showAlert(resp.message || "Discord field saved.", "success");

    // Refresh data (settings + fields)
    await refreshCurrentSystemDiscord();
}

async function confirmDeleteDiscordField(embed_field_id) {
    if (!window.confirm("Delete this Discord field? This cannot be undone.")) return;

    // pick a CSRF token from either field modal or settings form
    const csrf =
        document.querySelector('#discordFieldForm [name="_csrf_token"]')?.value ||
        document.querySelector('#updateSystemDiscordForm [name="_csrf_token"]')?.value || "";

    const resp = await apiJson(`/api/systems/${currentSystemId}/discord/fields/${embed_field_id}`, {
        method: "DELETE",
        body: {_csrf_token: csrf}
    });

    if (!resp.success) {
        showAlert(resp.message || "Failed to delete Discord field.", "danger");
        return;
    }

    showAlert(resp.message || "Discord field deleted.", "success");
    await refreshCurrentSystemDiscord();
}

function reorderDiscordField(embed_field_id, delta) {
    // clone and sort current list
    const sorted = [...currentDiscordFields].sort((a,b)=>(a.sort_order??0)-(b.sort_order??0));
    const idx = sorted.findIndex(f => String(f.embed_field_id) === String(embed_field_id));
    if (idx < 0) return;

    const newIdx = idx + delta;
    if (newIdx < 0 || newIdx >= sorted.length) return; // out of bounds

    // swap
    const tmp = sorted[idx];
    sorted[idx] = sorted[newIdx];
    sorted[newIdx] = tmp;

    // renumber locally (multiples of 10 keep headroom)
    sorted.forEach((f,i)=> f.sort_order = (i+1)*10);
    currentDiscordFields = sorted;

    // optimistic update
    populateDiscordFieldsTable(currentDiscordFields, {
        onEdit:   openDiscordFieldModalForEdit,
        onDelete: confirmDeleteDiscordField,
        onMove:   reorderDiscordField,
    });

    // persist order to server (async)
    persistDiscordFieldOrder(sorted.map(f => f.embed_field_id));
}

async function persistDiscordFieldOrder(orderedIds) {
    if (!currentSystemId) return;

    const csrf = document.querySelector('#updateSystemDiscordForm [name="_csrf_token"]')?.value || "";

    const resp = await apiJson(
        `/api/systems/${currentSystemId}/discord/fields/reorder`,
        {method:"POST", body:{_csrf_token:csrf, order:orderedIds}}
    );

    if (!resp.success) {
        showAlert(resp.message || "Failed to reorder Discord fields.", "danger");
        return;
    }

    // server returns authoritative list (sorted)
    currentDiscordFields = Array.isArray(resp.result) ? resp.result : currentDiscordFields;

    populateDiscordFieldsTable(currentDiscordFields, {
        onEdit:   openDiscordFieldModalForEdit,
        onDelete: confirmDeleteDiscordField,
        onMove:   reorderDiscordField,
    });

    showAlert(resp.message || "Field order saved.", "success");
}

async function refreshCurrentSystemDiscord() {
    if (!currentSystemId) return;

    const resp = await apiJson(
        `/api/systems/${currentSystemId}/discord/settings`,
        {method:"GET"}
    );

    if (!resp.success) {
        showAlert(resp.message || "Failed to refresh Discord settings.", "danger");
        return;
    }

    const sysObj = {
        radio_system_id: currentSystemId,
        system_name: document.getElementById("updateDiscordSystemName")?.value ?? "",
        discord: resp.result
    };

    populateUpdateDiscord(sysObj);
}

function populateUpdateMake(system_data) {
    const make = system_data.make ?? {};

    currentMakeSettingId = make.make_setting_id ?? null;
    currentMakeFields    = Array.isArray(make.fields) ? [...make.fields] : [];

    /* identifiers */
    document.getElementById("updateMakeSystemId").value   = system_data.radio_system_id;
    document.getElementById("updateMakeSystemName").value = system_data.system_name;

    /* basic settings */
    document.getElementById("makeSelect").value      = Number(make.enabled) === 1 ? "1" : "0";
    document.getElementById("makeWebhookURL").value  = make.webhook_url  ?? "";
    document.getElementById("makeApiKey").value      = make.api_key      ?? "";

    /* table */
    populateMakeFieldsTable(currentMakeFields, {
        onEdit  : openMakeFieldModalForEdit,
        onDelete: confirmDeleteMakeField
    });

    updateIntegrationTestButtonsState();
}

function populateMakeFieldsTable(fields, {showActions=true, onEdit, onDelete} = {}) {
    const tbody = document.querySelector("#makeFieldsTable tbody");
    tbody.innerHTML = "";

    if (!fields?.length) {
        tbody.innerHTML = `<tr><td colspan="5" class="text-center py-3 text-body-secondary">
        No keys defined. Click “Add Key”.
      </td></tr>`;
        return;
    }

    fields.forEach((f, idx) => {
        const enabled = !!f.field_enabled;
        const row = document.createElement("tr");
        if (!enabled) row.classList.add("table-secondary","opacity-75");

        row.innerHTML = `
      <td>${idx+1}</td>
      <td class="d-none d-md-table-cell">${escapeHtml(f.field_key)}</td>
      <td class="d-none d-lg-table-cell small text-break">${escapeHtml(f.field_value)}</td>
      <td>${enabled ? "Yes" : "No"}</td>
      <td>
        <div class="btn-group btn-group-sm">
          <button class="btn btn-outline-primary js-mf-edit" data-id="${f.payload_field_id}">
            <i class="bi bi-pencil"></i>
          </button>
          <button class="btn btn-outline-danger  js-mf-del"  data-id="${f.payload_field_id}">
            <i class="bi bi-trash"></i>
          </button>
        </div>
      </td>`;
        tbody.appendChild(row);
    });

    /* wire buttons */
    if (showActions && !tbody._mfDelegated) {
        tbody.addEventListener("click", (e)=>{
            if (e.target.closest(".js-mf-edit")) {
                onEdit(e.target.closest(".js-mf-edit").dataset.id);
            }
            if (e.target.closest(".js-mf-del")) {
                onDelete(e.target.closest(".js-mf-del").dataset.id);
            }
        });
        tbody._mfDelegated = true;          // flag so we only add once
    }

}

function openMakeFieldModalForAdd() {
    const form = document.getElementById("makeFieldForm");

    if (!form) return;

    form.reset();

    form.querySelector("#makeFieldId").value        = "";
    form.querySelector("#makeFieldSystemId").value  = currentSystemId ?? "";
    form.querySelector("#makeFieldSettingId").value = currentMakeSettingId ?? "";
    form.querySelector("#makeFieldEnabled").value   = "1";

}

function openMakeFieldModalForEdit(id) {
    const field = currentMakeFields.find(x=>String(x.payload_field_id)===String(id));
    if (!field) { showAlert("Field not found.","warning"); return; }

    const form = document.getElementById("makeFieldForm");
    form.querySelector("#makeFieldModalLabel").textContent = `Edit: ${field.field_key}`;
    form.querySelector("#makeFieldId").value        = field.payload_field_id;
    form.querySelector("#makeFieldSystemId").value  = currentSystemId ?? "";
    form.querySelector("#makeFieldSettingId").value = currentMakeSettingId ?? "";
    form.querySelector("#makeFieldKey").value       = field.field_key;
    form.querySelector("#makeFieldTemplate").value  = field.field_value;
    form.querySelector("#makeFieldEnabled").value   = field.field_enabled ? "1":"0";

    const modalEl = document.getElementById("makeFieldModal");
    if (modalEl && window.bootstrap) {
        bootstrap.Modal.getOrCreateInstance(modalEl).show();
    }

}

function gatherMakeSettingsFormData() {
    return {
        _csrf_token      : document.querySelector("#updateSystemMakeForm [name=_csrf_token]").value,
        radio_system_id  : Number(document.getElementById("updateMakeSystemId").value),
        make_enabled     : Number(document.getElementById("makeSelect").value),
        make_webhook_url : document.getElementById("makeWebhookURL").value.trim(),
        make_api_key     : document.getElementById("makeApiKey").value.trim()
    };
}

async function saveMakeSettings() {
    const data = gatherMakeSettingsFormData();
    const resp = await apiJson(`/api/systems/${data.radio_system_id}/make/settings`,
        {method:"PATCH", body:data});

    if (!resp.success) { showAlert(resp.message,"danger"); return; }
    showAlert(resp.message,"success");
    /* refresh */
    populateUpdateMake({radio_system_id:data.radio_system_id, make:resp.result});
}

function gatherMakeFieldFormData(form) {
    const q = sel=>form.querySelector(sel)?.value ?? "";
    return {
        _csrf_token          : q('[name=_csrf_token]'),
        payload_field_id     : q("#makeFieldId")        || null,
        make_setting_id      : q("#makeFieldSettingId") || null,
        radio_system_id      : q("#makeFieldSystemId")  || null,
        field_key            : q("#makeFieldKey").trim(),
        field_value : q("#makeFieldTemplate").trim(),
        field_enabled        : Number(q("#makeFieldEnabled"))
    };
}

async function saveMakeField(formEl){
    const data = gatherMakeFieldFormData(formEl);
    const url = data.payload_field_id
        ? `/api/systems/${data.radio_system_id}/make/fields/${data.payload_field_id}`
        : `/api/systems/${data.radio_system_id}/make/fields`;
    const method = data.payload_field_id ? "PATCH" : "POST";
    const resp = await apiJson(url,{method, body:data});

    if (!resp.success){ showAlert(resp.message,"danger"); return; }

    hideModalById("#makeFieldModal");

    showAlert(resp.message,"success");
    await refreshMake();               // pull fresh list & re-render
}

async function confirmDeleteMakeField(id){
    /* show mini confirm modal */
    document.getElementById("makeFieldDeleteQuestion").textContent =
        "Delete key permanently?";
    document.getElementById("makeFieldDeleteId").value = id;

    showModalById("#makeFieldDeleteModal");

}

async function refreshMake(){
    if (!currentSystemId) return;
    const resp = await apiJson(`/api/systems/${currentSystemId}/make/settings`);
    if (!resp.success){ showAlert(resp.message,"danger"); return; }
    populateUpdateMake({radio_system_id:currentSystemId, make:resp.result});
}

function populateUpdatePushover(system_data) {
    const pushover = system_data.pushover ?? {};

    document.getElementById("updatePushoverSystemId").value   = system_data.radio_system_id;
    document.getElementById("updatePushoverSystemName").value = system_data.system_name;

    document.getElementById("pushoverSelect").value      = Number(pushover.enabled) === 1 ? "1" : "0";
    document.getElementById("pushoverGroupToken").value  = pushover.group_token ?? "";
    document.getElementById("pushoverAppToken").value    = pushover.app_token   ?? "";
    document.getElementById("pushoverSound").value       = pushover.sound       ?? "pushover";
    document.getElementById("pushoverSubject").value     = pushover.subject     ?? "Dispatch Alert";
    document.getElementById("pushoverAlertBody").value   = pushover.body        ?? "";

    updateIntegrationTestButtonsState();
}

function gatherPushoverSettingsFormData() {
    const form = document.getElementById("updateSystemPushoverForm");
    const q = sel => form.querySelector(sel)?.value ?? "";
    return {
        _csrf_token        : q('[name=_csrf_token]'),
        radio_system_id    : Number(q("#updatePushoverSystemId")),
        pushover_enabled   : Number(q("#pushoverSelect")),
        pushover_group_token : q("#pushoverGroupToken").trim() || null,
        pushover_app_token   : q("#pushoverAppToken").trim()   || null,
        pushover_sound       : q("#pushoverSound").trim()      || "pushover",
        pushover_subject     : q("#pushoverSubject").trim()    || "Dispatch Alert",
        pushover_body        : q("#pushoverAlertBody").trim()
    };
}

async function savePushoverSettings() {
    const data  = gatherPushoverSettingsFormData();
    const url   = `/api/systems/${data.radio_system_id}/pushover/settings`;
    const resp  = await apiJson(url, {method:"PATCH", body:data});
    if (!resp.success) { showAlert(resp.message,"danger"); return; }

    // re-hydrate current tab
    populateUpdatePushover({radio_system_id:data.radio_system_id,
        system_name     : document.getElementById("updatePushoverSystemName").value,
        pushover        : resp.result});
    showAlert(resp.message,"success");
}


function populateUpdateEmail(system_data) {
    const email = system_data.email ?? {};

    /* identifiers */
    document.getElementById("updateSmtpSystemId").value   = system_data.radio_system_id;
    document.getElementById("updateSmtpSystemName").value = system_data.system_name;

    /* basic flags / smtp fields */
    document.getElementById("smtpSelect").value        = Number(email.enabled) === 1 ? "1" : "0";
    document.getElementById("smtpHost").value          = email.smtp_hostname   ?? "";
    document.getElementById("smtpPort").value          = email.smtp_port       ?? "";
    document.getElementById("smtpUser").value          = email.smtp_username   ?? "";
    document.getElementById("smtpPassword").value      = email.smtp_password   ?? "";
    document.getElementById("smtpEmailFrom").value     = email.email_address_from ?? "";
    document.getElementById("smtpEmailTextFrom").value = email.email_text_from ?? "";
    document.getElementById("emailAlertSubject").value = email.email_alert_subject ?? "";
    document.getElementById("emailAlertBody").value    = email.email_alert_body ?? "";

    /* addresses */
    currentEmailAddresses = Array.isArray(email.recipients) ? [...email.recipients] : [];
    populateEmailAddressesTable(currentEmailAddresses, {
        onEdit  : openEmailAddressModalForEdit,
        onDelete: confirmDeleteEmailAddress
    });

    updateIntegrationTestButtonsState();

}

function populateEmailAddressesTable(list, {showActions=true, onEdit, onDelete} = {}) {
    const tbody = document.querySelector("#emailAddressesTable tbody");
    if (!tbody) return;
    tbody.innerHTML = "";

    if (!list.length) {
        tbody.innerHTML = `<tr><td colspan="4" class="text-center py-3 text-body-secondary">
        No addresses defined. Click “Add”.
      </td></tr>`;
        return;
    }

    list.forEach((addr, idx) => {
        const row = document.createElement("tr");
        if (!addr.enabled) row.classList.add("table-secondary","opacity-75");

        row.innerHTML = `
      <td>${idx+1}</td>
      <td>${escapeHtml(addr.email_address)}</td>
      <td>${addr.enabled ? "Yes":"No"}</td>
      <td>
        <div class="btn-group btn-group-sm">
          <button class="btn btn-outline-primary js-em-edit" data-id="${addr.email_id}">
            <i class="bi bi-pencil"></i>
          </button>
          <button class="btn btn-outline-danger  js-em-del"  data-id="${addr.email_id}">
            <i class="bi bi-trash"></i>
          </button>
        </div>
      </td>`;
        tbody.appendChild(row);
    });

    if (showActions) {
        tbody.querySelectorAll(".js-em-edit")
            .forEach(btn=>btn.addEventListener("click",()=>onEdit(btn.dataset.id)));
        tbody.querySelectorAll(".js-em-del")
            .forEach(btn=>btn.addEventListener("click",()=>onDelete(btn.dataset.id)));
    }
}

function gatherEmailSettingsFormData(){
    return {
        _csrf_token        : document.querySelector("#updateSystemSmtpForm [name=_csrf_token]").value,
        radio_system_id    : Number(document.getElementById("updateSmtpSystemId").value),
        email_enabled      : Number(document.getElementById("smtpSelect").value),
        smtp_hostname      : document.getElementById("smtpHost").value.trim(),
        smtp_port          : document.getElementById("smtpPort").value.trim(),
        smtp_username      : document.getElementById("smtpUser").value.trim(),
        smtp_password      : document.getElementById("smtpPassword").value,
        email_address_from : document.getElementById("smtpEmailFrom").value.trim(),
        email_text_from    : document.getElementById("smtpEmailTextFrom").value.trim(),
        email_alert_subject: document.getElementById("emailAlertSubject").value.trim(),
        email_alert_body   : document.getElementById("emailAlertBody").value.trim()
    };
}

async function saveEmailSettings(){
    const data = gatherEmailSettingsFormData();
    const resp = await apiJson(`/api/systems/${data.radio_system_id}/email/settings`,
        {method:"PATCH", body:data});
    if (!resp.success){ showAlert(resp.message,"danger"); return; }
    showAlert(resp.message,"success");
    /* refresh list/fields so we have canonical data */
    populateUpdateEmail({radio_system_id:data.radio_system_id, email:resp.result});
}

function openEmailAddressModalForAdd(){
    const form = document.getElementById("emailAddressForm");
    form.reset();
    form.querySelector("#emailFieldId").value        = "";
    form.querySelector("#emailFieldSystemId").value  = currentSystemId ?? "";
    form.querySelector("#emailFieldEnabled").value   = "1";
    document.getElementById("emailAddressModalLabel").textContent = "Add Email Address";
    // (open via data-bs-target on button or call:)
    // showModalById("#emailAddressModal");
}

function openEmailAddressModalForEdit(id){
    const rec = currentEmailAddresses.find(r=>String(r.email_id)===String(id));
    if (!rec){ showAlert("Address not found.","warning"); return; }

    const form = document.getElementById("emailAddressForm");
    document.getElementById("emailAddressModalLabel").textContent = `Edit: ${rec.email_address}`;
    form.querySelector("#emailFieldId").value        = rec.email_id;
    form.querySelector("#emailFieldSystemId").value  = currentSystemId ?? "";
    form.querySelector("#emailFieldAddress").value   = rec.email_address;
    form.querySelector("#emailFieldEnabled").value   = rec.enabled ? "1" : "0";

    showModalById("#emailAddressModal");

}

/* gather + save */
function gatherEmailAddressFormData(form){
    const q = sel=>form.querySelector(sel)?.value ?? "";
    return {
        _csrf_token      : q('[name=_csrf_token]'),
        email_id         : q("#emailFieldId")       || null,
        radio_system_id  : q("#emailFieldSystemId") || null,
        email_address    : q("#emailFieldAddress").trim(),
        enabled          : Number(q("#emailFieldEnabled"))
    };
}

async function saveEmailAddress(formEl){
    const data   = gatherEmailAddressFormData(formEl);
    const url    = data.email_id
        ? `/api/systems/${data.radio_system_id}/emails/${data.email_id}`
        : `/api/systems/${data.radio_system_id}/emails`;
    const method = data.email_id ? "PATCH" : "POST";

    const resp = await apiJson(url,{method, body:data});
    if (!resp.success){ showAlert(resp.message,"danger"); return; }

    hideModalById("#emailAddressModal");
    showAlert(resp.message,"success");
    await refreshEmailData();
}

/* delete workflow */
function confirmDeleteEmailAddress(id){
    document.getElementById("emailDeleteId").value = id;
    document.getElementById("emailDeleteQuestion").textContent =
        "Delete this address permanently?";
    // Open via data-bs-target on button or call:
    showModalById("#emailAddressDeleteModal");
}

async function deleteEmailAddress(){
    const id   = document.getElementById("emailDeleteId").value;
    const csrf = document.querySelector("#emailAddressDeleteForm [name=_csrf_token]").value;
    const resp = await apiJson(`/api/systems/${currentSystemId}/emails/${id}`,
        {method:"DELETE", body:{_csrf_token:csrf}});
    if (!resp.success){ showAlert(resp.message,"danger"); return; }
    hideModalById("#emailAddressDeleteModal");
    showAlert(resp.message,"success");
    await refreshEmailData();
}

/* pull fresh settings + addresses */
async function refreshEmailData(){
    if (!currentSystemId) return;
    const resp = await apiJson(`/api/systems/${currentSystemId}/email/settings`);
    if (!resp.success){ showAlert(resp.message,"danger"); return; }
    populateUpdateEmail({radio_system_id:currentSystemId, email:resp.result});
}

function populateUpdateTranscribe(system_data) {
    const t = system_data.transcribe ?? {};

    document.getElementById("transcribeTabTitle").innerText =
        `Transcribe Settings - ${system_data.system_name}`;

    document.getElementById("updateTranscribeSystemId").value   = system_data.radio_system_id ?? "";
    document.getElementById("updateTranscribeSystemName").value = system_data.system_name ?? "";

    // on/off select expects "1"/"0"
    document.getElementById("transcribeSelect").value = Number(t.enabled) === 1 ? "1" : "0";

    // basic
    document.getElementById("transcribeURL").value     = t.url     ?? "";
    document.getElementById("transcribeApiKey").value  = t.api_key ?? "";

    // NEW
    document.getElementById("transcribeModel").value    = t.model    ?? "";
    document.getElementById("transcribeLanguage").value = t.language ?? "";
    document.getElementById("transcribePrompt").value   = t.prompt   ?? "";
}

function gatherTranscribeSettingsFormData() {
    const f = document.getElementById("updateSystemTranscribeForm");
    const q = sel => f.querySelector(sel)?.value ?? "";
    return {
        _csrf_token         : q('[name=_csrf_token]'),
        radio_system_id     : Number(q("#updateTranscribeSystemId")),
        transcribe_enabled  : Number(q("#transcribeSelect")),
        transcribe_url      : q("#transcribeURL").trim()     || null,
        transcribe_api_key  : q("#transcribeApiKey").trim()  || null,
        transcribe_model    : q("#transcribeModel").trim()    || null,
        transcribe_language : q("#transcribeLanguage").trim() || null,
        transcribe_prompt   : q("#transcribePrompt").trim()   || null
    };
}

async function saveTranscribeSettings() {
    const data = gatherTranscribeSettingsFormData();
    const url  = `/api/systems/${data.radio_system_id}/transcribe/settings`;
    const resp = await apiJson(url, {method:"PATCH", body:data});

    if (!resp.success) { showAlert(resp.message,"danger"); return; }

    populateUpdateTranscribe({
        radio_system_id : data.radio_system_id,
        system_name     : document.getElementById("updateTranscribeSystemName").value,
        transcribe      : resp.result
    });
    updateIntegrationTestButtonsState();
    showAlert(resp.message,"success");
}

/*****************************************************
 *  Address Extraction SETTINGS
 *****************************************************/

function applyGeocodeCountryLabels(countryCodeRaw) {
    const code = String(countryCodeRaw || "US").toUpperCase();
    const cfg = GEO_LABELS_BY_COUNTRY[code] || GEO_LABELS_BY_COUNTRY.US;

    // Default region labels
    const stateLabelEl = document.getElementById("addressGeocodeStateLabel");
    const stateInputEl = document.getElementById("addressGeocodeState");
    if (stateLabelEl) stateLabelEl.textContent = cfg.stateLabel;
    if (stateInputEl) stateInputEl.placeholder = cfg.statePlaceholder;

    // Regions table headers + help text
    const thState  = document.getElementById("addressRegionsStateHeader");
    const thCounty = document.getElementById("addressRegionsCountyHeader");
    const helpText = document.getElementById("addressRegionsHelpText");
    if (thState)  thState.textContent  = cfg.stateLabel;
    if (thCounty) thCounty.textContent = cfg.countyLabel;
    if (helpText) helpText.textContent = cfg.regionsHelp;

    // Region modal labels (optional but nice)
    const modalStateLbl  = document.getElementById("addressRegionStateLabel");
    const modalCountyLbl = document.getElementById("addressRegionCountyLabel");
    if (modalStateLbl)  modalStateLbl.textContent  = cfg.stateLabel;
    if (modalCountyLbl) modalCountyLbl.textContent = cfg.countyLabel;
}

function populateUpdateAddressExtraction(system_data) {
    const s = system_data.address_extraction ?? {};

    currentAddressExtractionSettings = s;
    currentGeocodingRegions = Array.isArray(s.regions) ? [...s.regions] : [];

    const tabTitle = document.getElementById("addressTabTitle");
    const sysIdEl  = document.getElementById("updateAddressSystemId");
    const sysNameEl= document.getElementById("updateAddressSystemName");

    if (tabTitle)  tabTitle.textContent = `Address Extraction – ${system_data.system_name}`;
    if (sysIdEl)   sysIdEl.value        = system_data.radio_system_id;
    if (sysNameEl) sysNameEl.value      = system_data.system_name;

    // Enabled select (note: backend field is "enabled")
    const enabledSel = document.getElementById("addressExtractionEnabled");
    if (enabledSel) {
        enabledSel.value = Number(s.enabled) === 1 ? "1" : "0";
    }

    // OpenAI model (safe to show)
    const modelEl = document.getElementById("addressOpenAiModel");
    if (modelEl) {
        modelEl.value = s.openai_model ?? "";
    }

    // API keys: DON'T show actual key; just hint that one is stored
    const openKeyEl   = document.getElementById("addressOpenAiKey");
    const googleKeyEl = document.getElementById("addressGoogleKey");

    if (openKeyEl) {
        openKeyEl.value = s.openai_api_key;
    }
    if (googleKeyEl) {
        googleKeyEl.value = s.google_maps_api_key;
    }

    // Default region
    const countryEl = document.getElementById("addressGeocodeCountry");
    const stateEl   = document.getElementById("addressGeocodeState");
    const cityEl    = document.getElementById("addressGeocodeCity");

    if (countryEl) countryEl.value = s.geocode_country ?? "";
    if (stateEl)   stateEl.value   = s.geocode_state   ?? "";
    if (cityEl)    cityEl.value    = s.geocode_city    ?? "";

    applyGeocodeCountryLabels(countryEl?.value || s.geocode_country || "US");

    populateGeocodingRegionsTable(currentGeocodingRegions, {
        onEdit:   openAddressRegionModalForEdit,
        onDelete: confirmDeleteAddressRegion,
        onMove:   reorderAddressRegion
    });
}


async function refreshAddressExtraction() {
    if (!currentSystemId) return;

    const resp = await apiJson(
        `/api/systems/${currentSystemId}/address_extraction/settings`,
        { method: "GET" }
    );

    if (!resp.success) {
        // only complain if we actually have a message
        if (resp.message) {
            showAlert(resp.message, "danger");
        }
        return;
    }

    const s = resp.result || {};
    currentAddressExtractionSettings = s;
    currentGeocodingRegions = Array.isArray(s.regions) ? [...s.regions] : [];

    const en  = byId("addressExtractionEnabled");
    const oai = byId("addressOpenAiKey");
    const mdl = byId("addressOpenAiModel");
    const gk  = byId("addressGoogleKey");
    const cty = byId("addressGeocodeCountry");
    const st  = byId("addressGeocodeState");
    const ci  = byId("addressGeocodeCity");

    if (en) en.value = Number(s.enabled) === 1 ? "1" : "0";
    if (oai) oai.value = s.openai_api_key       || "";
    if (mdl) mdl.value = s.openai_model         || "";
    if (gk)  gk.value  = s.google_maps_api_key  || "";
    if (cty) cty.value = s.geocode_country      || "";
    if (st)  st.value  = s.geocode_state        || "";
    if (ci)  ci.value  = s.geocode_city         || "";

    applyGeocodeCountryLabels(cty?.value || s.geocode_country || "US");

    populateGeocodingRegionsTable(currentGeocodingRegions, {
        onEdit:   openAddressRegionModalForEdit,
        onDelete: confirmDeleteAddressRegion,
        onMove:   reorderAddressRegion
    });
}

function gatherAddressExtractionSettingsFormData() {
    const form = document.getElementById("updateSystemAddressExtractionForm");
    if (!form) return null;
    const q = sel => form.querySelector(sel)?.value ?? "";

    const radio_system_id = Number(q("#updateAddressSystemId"));

    // Get the raw select value as string "1" or "0"
    const enabledRaw = q("#addressExtractionEnabled").trim();
    // Normalize to integer 1 or 0
    const enabled = enabledRaw === "1" ? 1 : 0;

    const data = {
        _csrf_token      : q('[name="_csrf_token"]'),
        radio_system_id,
        // send *always*, even when 0
        enabled,
        country          : q("#addressGeocodeCountry").trim() || null,
        state            : q("#addressGeocodeState").trim()   || null,
        city             : q("#addressGeocodeCity").trim()    || null,
        openai_model     : q("#addressOpenAiModel").trim()    || null,
    };

    const openKey = q("#addressOpenAiKey").trim();
    if (openKey) {
        data.openai_key = openKey;
    }

    const googleKey = q("#addressGoogleKey").trim();
    if (googleKey) {
        data.google_key = googleKey;
    }

    // Debug: see exactly what goes over the wire
    console.debug("AddressExtraction PATCH payload:", data);

    return data;
}

async function saveAddressExtractionSettings() {
    const data = gatherAddressExtractionSettingsFormData();
    if (!data) return;

    if (!data.radio_system_id) {
        showAlert("No system selected; cannot save address settings.", "danger");
        return;
    }

    const url  = `/api/systems/${data.radio_system_id}/address_extraction/settings`;
    const resp = await apiJson(url, { method: "PATCH", body: data });

    if (!resp.success) {
        showAlert(resp.message || "Failed to save address settings.", "danger");
        return;
    }

    const settings = resp.result || {};
    currentAddressExtractionSettings = settings;
    currentGeocodingRegions = Array.isArray(settings.regions) ? settings.regions : [];

    // Re-render tab with canonical values from server
    populateUpdateAddressExtraction({
        radio_system_id : data.radio_system_id,
        system_name     : document.getElementById("updateAddressSystemName")?.value ?? "",
        address_extraction: settings
    });

    showAlert(resp.message || "Address extraction settings saved.", "success");
}

/*****************************************************
 *  Address Extraction – Regions table + modals
 *****************************************************/

function populateGeocodingRegionsTable(regions, opts = {}) {
    const {
        showActions = true,
        onEdit      = () => {},
        onDelete    = () => {},
        onMove      = () => {}
    } = opts;

    const tbody = document.querySelector("#addressRegionsTable tbody");
    if (!tbody) return;
    tbody.innerHTML = "";

    if (!Array.isArray(regions) || regions.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="5" class="text-center text-body-secondary py-3">
                    No regions defined. Click “Add Region” to create one.
                </td>
            </tr>`;
        return;
    }

    const sorted = [...regions].sort(
        (a, b) => (a.priority ?? 0) - (b.priority ?? 0)
    );

    sorted.forEach((r, idx) => {
        const row = document.createElement("tr");

        const upDisabled   = idx === 0 ? "disabled" : "";
        const downDisabled = idx === sorted.length - 1 ? "disabled" : "";

        let actionsHtml = "";
        if (showActions) {
            actionsHtml = `
                <div class="btn-group btn-group-sm" role="group">
                    <button type="button"
                            class="btn btn-outline-secondary js-ar-move-up"
                            data-id="${r.region_id}" ${upDisabled}
                            title="Move up">
                        <i class="bi bi-arrow-up"></i>
                    </button>
                    <button type="button"
                            class="btn btn-outline-secondary js-ar-move-down"
                            data-id="${r.region_id}" ${downDisabled}
                            title="Move down">
                        <i class="bi bi-arrow-down"></i>
                    </button>
                    <button type="button"
                            class="btn btn-outline-primary js-ar-edit"
                            data-id="${r.region_id}"
                            title="Edit">
                        <i class="bi bi-pencil"></i>
                    </button>
                    <button type="button"
                            class="btn btn-outline-danger js-ar-del"
                            data-id="${r.region_id}"
                            title="Delete">
                        <i class="bi bi-trash"></i>
                    </button>
                </div>`;
        }

        row.innerHTML = `
            <td>${idx + 1}</td>
            <td>${escapeHtml(r.state_code   ?? "")}</td>
            <td>${escapeHtml(r.county_name  ?? "")}</td>
            <td>${r.priority ?? ""}</td>
            <td>${actionsHtml}</td>
        `;
        tbody.appendChild(row);
    });

    if (!showActions) return;

    tbody.querySelectorAll(".js-ar-edit")
        .forEach(btn => btn.addEventListener("click",
            () => onEdit(btn.dataset.id)));
    tbody.querySelectorAll(".js-ar-del")
        .forEach(btn => btn.addEventListener("click",
            () => onDelete(btn.dataset.id)));
    tbody.querySelectorAll(".js-ar-move-up")
        .forEach(btn => btn.addEventListener("click",
            () => onMove(btn.dataset.id, -1)));
    tbody.querySelectorAll(".js-ar-move-down")
        .forEach(btn => btn.addEventListener("click",
            () => onMove(btn.dataset.id, +1)));
}

function openAddressRegionModalForAdd() {
    const form = document.getElementById("addressRegionForm");
    if (!form) return;

    form.reset();

    form.querySelector("#addressRegionId").value       = "";
    form.querySelector("#addressRegionSystemId").value = currentSystemId ?? "";

    const maxPriority = currentGeocodingRegions.reduce(
        (m, r) => Math.max(m, r.priority ?? 0),
        0
    );
    form.querySelector("#addressRegionPriority").value = maxPriority + 10;

    const labelEl = byId("addressRegionModalLabel");
    if (labelEl) labelEl.textContent = "Add Region";
    // Modal opening handled by data-bs-toggle on the button
}

function openAddressRegionModalForEdit(region_id) {
    const r = currentGeocodingRegions.find(
        x => String(x.region_id) === String(region_id)
    );
    if (!r) {
        showAlert("Region not found.", "warning");
        return;
    }

    const form = document.getElementById("addressRegionForm");
    if (!form) return;

    const labelEl = byId("addressRegionModalLabel");
    if (labelEl) {
        labelEl.textContent =
            `Edit Region: ${r.state_code || ""} – ${r.county_name || ""}`;
    }

    form.querySelector("#addressRegionId").value        = r.region_id;
    form.querySelector("#addressRegionSystemId").value  = currentSystemId ?? "";
    form.querySelector("#addressRegionStateCode").value = r.state_code   ?? "";
    form.querySelector("#addressRegionCountyName").value= r.county_name  ?? "";
    form.querySelector("#addressRegionPriority").value  = r.priority     ?? 10;

    showModalById("#addressRegionModal");
}

function gatherAddressRegionFormData(form) {
    const q = sel => form.querySelector(sel)?.value ?? "";

    return {
        _csrf_token     : q('[name="_csrf_token"]'),
        region_id       : q("#addressRegionId") || null,
        radio_system_id : Number(q("#addressRegionSystemId")),
        state_code      : q("#addressRegionStateCode").trim()  || null,
        county_name     : q("#addressRegionCountyName").trim() || null,
        priority        : parseInt(q("#addressRegionPriority"), 10) || null
    };
}

async function saveAddressRegionFromModal(formEl) {
    const data = gatherAddressRegionFormData(formEl);
    const sysId = data.radio_system_id || currentSystemId;
    if (!sysId) {
        showAlert("Missing system ID for region.", "danger");
        return;
    }

    let url, method;
    if (data.region_id) {
        url    = `/api/systems/${sysId}/address_extraction/regions/${data.region_id}`;
        method = "PATCH";
    } else {
        url    = `/api/systems/${sysId}/address_extraction/regions`;
        method = "POST";
    }

    const resp = await apiJson(url, { method, body: data });
    if (!resp.success) {
        showAlert(resp.message || "Failed to save region.", "danger");
        return;
    }

    hideModalById("#addressRegionModal");
    showAlert(resp.message || "Region saved.", "success");

    await refreshAddressExtraction();
}

function confirmDeleteAddressRegion(region_id) {
    const r = currentGeocodingRegions.find(
        x => String(x.region_id) === String(region_id)
    );

    const questionEl = byId("addressRegionDeleteQuestion");
    if (questionEl) {
        questionEl.textContent = r
            ? `Delete region ${r.state_code || ""} – ${r.county_name || ""}?`
            : "Delete this region permanently?";
    }

    byId("addressRegionDeleteId").value = region_id;
    showModalById("#addressRegionDeleteModal");
}

async function deleteAddressRegion() {
    const regionId = byId("addressRegionDeleteId").value;
    const csrf = document.querySelector("#addressRegionDeleteForm [name=_csrf_token]")?.value || "";

    const resp = await apiJson(
        `/api/systems/${currentSystemId}/address_extraction/regions/${regionId}`,
        {
            method: "DELETE",
            body  : { _csrf_token: csrf }
        }
    );

    if (!resp.success) {
        showAlert(resp.message || "Failed to delete region.", "danger");
        return;
    }

    hideModalById("#addressRegionDeleteModal");
    showAlert(resp.message || "Region deleted.", "success");

    await refreshAddressExtraction();
}

function reorderAddressRegion(region_id, delta) {
    const sorted = [...currentGeocodingRegions].sort(
        (a, b) => (a.priority ?? 0) - (b.priority ?? 0)
    );
    const idx = sorted.findIndex(
        r => String(r.region_id) === String(region_id)
    );
    if (idx < 0) return;

    const newIdx = idx + delta;
    if (newIdx < 0 || newIdx >= sorted.length) return;

    const tmp = sorted[idx];
    sorted[idx] = sorted[newIdx];
    sorted[newIdx] = tmp;

    // Renumber priorities 10, 20, 30 ...
    sorted.forEach((r, i) => r.priority = (i + 1) * 10);
    currentGeocodingRegions = sorted;

    populateGeocodingRegionsTable(currentGeocodingRegions, {
        onEdit:   openAddressRegionModalForEdit,
        onDelete: confirmDeleteAddressRegion,
        onMove:   reorderAddressRegion
    });

    // Persist to server
    persistAddressRegionOrder(sorted.map(r => r.region_id));
}

async function persistAddressRegionOrder(orderedIds) {
    if (!currentSystemId) return;

    const csrf = document.querySelector("#updateSystemAddressExtractionForm [name=_csrf_token]")?.value || "";

    const resp = await apiJson(
        `/api/systems/${currentSystemId}/address_extraction/regions/reorder`,
        {
            method: "POST",
            body  : { _csrf_token: csrf, order: orderedIds }
        }
    );

    if (!resp.success) {
        showAlert(resp.message || "Failed to reorder regions.", "danger");
        return;
    }

    currentGeocodingRegions = Array.isArray(resp.result)
        ? resp.result
        : currentGeocodingRegions;

    populateGeocodingRegionsTable(currentGeocodingRegions, {
        onEdit:   openAddressRegionModalForEdit,
        onDelete: confirmDeleteAddressRegion,
        onMove:   reorderAddressRegion
    });

    showAlert(resp.message || "Region order saved.", "success");
}

/*****************************************************
 *  Incident Classification SETTINGS
 *****************************************************/

function populateUpdateIncidentClassification(system_data) {
    const s = system_data.incident_classification || {};
    currentIncidentClassificationSettings = s;

    const titleEl = document.getElementById("incidentTabTitle");
    if (titleEl) titleEl.textContent = `Incident Classification`;

    const sysIdEl   = document.getElementById("updateIncidentSystemId");
    const sysNameEl = document.getElementById("updateIncidentSystemName");
    if (sysIdEl)   sysIdEl.value   = system_data.radio_system_id ?? "";
    if (sysNameEl) sysNameEl.value = system_data.system_name ?? "";

    const enabledEl = document.getElementById("incidentClassificationEnabled");
    if (enabledEl) enabledEl.value = Number(s.enabled || 0) === 1 ? "1" : "0";

    const keyEl = document.getElementById("incidentOpenAiKey");
    if (keyEl) keyEl.value = s.openai_api_key ?? "";

    const modelEl = document.getElementById("incidentOpenAiModel");
    if (modelEl) modelEl.value = s.openai_model ?? "";

    const minEl = document.getElementById("incidentMinConfidence");
    if (minEl) minEl.value = (s.min_confidence ?? "") === null ? "" : (s.min_confidence ?? "");
}

function gatherIncidentClassificationFormData() {
    const form = document.getElementById("updateSystemIncidentClassificationForm");
    if (!form) return null;

    const q = sel => form.querySelector(sel)?.value ?? "";

    const sysId = Number(q("#updateIncidentSystemId")) || null;
    const enabledRaw = q("#incidentClassificationEnabled").trim();
    const enabled = (enabledRaw === "1") ? 1 : 0;

    const minRaw = q("#incidentMinConfidence").trim();
    const minVal = (minRaw === "") ? null : Number(minRaw);
    const min_confidence = (minVal === null || Number.isFinite(minVal)) ? minVal : null;

    return {
        _csrf_token: q('[name="_csrf_token"]'),
        radio_system_id: sysId,
        incident_classification_enabled: enabled,
        openai_api_key: (q("#incidentOpenAiKey").trim() || null),   // blank => clear
        openai_model: (q("#incidentOpenAiModel").trim() || null),
        min_confidence
    };
}

async function saveIncidentClassificationSettings() {
    const data = gatherIncidentClassificationFormData();
    if (!data) return;

    if (!data.radio_system_id) {
        showAlert("No system selected; cannot save incident settings.", "danger");
        return;
    }

    const url = `/api/systems/${data.radio_system_id}/incident_classification/settings`;
    const resp = await apiJson(url, { method: "PATCH", body: data });

    if (!resp.success) {
        showAlert(resp.message || "Failed to save incident settings.", "danger");
        return;
    }

    // Re-render using canonical server payload
    populateUpdateIncidentClassification({
        radio_system_id: data.radio_system_id,
        system_name: document.getElementById("updateIncidentSystemName")?.value ?? "",
        incident_classification: resp.result
    });

    showAlert(resp.message || "Incident settings saved.", "success");
}

/*****************************************************
 *               n8n TAB
 *****************************************************/
// Everything below is additive and safely no-ops if your page lacks n8n markup.

function populateUpdateN8n(system_data) {
    const n = (system_data && system_data.n8n) ? system_data.n8n : {};

    // ids
    const sidEl = document.getElementById("updateN8nSystemId");
    const sNmEl = document.getElementById("updateN8nSystemName");
    if (sidEl) sidEl.value = system_data.radio_system_id ?? "";
    if (sNmEl) sNmEl.value = system_data.system_name ?? "";

    // fields (STANDARD keys)
    const en   = document.getElementById("n8nEnabled");
    const url  = document.getElementById("n8nWebhookURL");
    const to   = document.getElementById("n8nTimeoutS");
    const iss  = document.getElementById("n8nJwtIssuer");
    const aud  = document.getElementById("n8nJwtAudience");
    const sub  = document.getElementById("n8nJwtSubjectTemplate");
    const ttl  = document.getElementById("n8nJwtTtlS");
    const sec  = document.getElementById("n8nJwtSecret");

    if (en)  en.value  = Number(n.enabled) === 1 ? "1" : "0";
    if (url) url.value = n.webhook_url ?? "";
    if (to)  to.value  = (n.timeout_s ?? 10);
    if (iss) iss.value = n.jwt_issuer ?? "";
    if (aud) aud.value = n.jwt_audience ?? "";
    if (sub) sub.value = n.jwt_subject_template ?? "";
    if (ttl) ttl.value = (n.jwt_ttl_s ?? 300);

    if (sec) sec.value = (n.jwt_passphrase ?? "");

    updateIntegrationTestButtonsState();
}

function gatherN8nSettingsFormData() {
    const f = document.getElementById("updateSystemN8nForm");
    if (!f) return null;

    const q = sel => f.querySelector(sel)?.value ?? "";

    const jwtSecret = (q("#n8nJwtSecret").trim());

    // STANDARD payload
    const payload = {
        _csrf_token          : q('[name=_csrf_token]'),
        radio_system_id      : Number(q("#updateN8nSystemId")),
        n8n_enabled              : Number(q("#n8nEnabled")),
        n8n_webhook_url          : q("#n8nWebhookURL").trim() || null,
        n8n_timeout_s            : parseInt(q("#n8nTimeoutS"), 10) || 10,
        jwt_issuer           : q("#n8nJwtIssuer").trim() || null,
        jwt_audience         : q("#n8nJwtAudience").trim() || null,
        jwt_subject_template : q("#n8nJwtSubjectTemplate").trim() || null,
        jwt_ttl_s            : parseInt(q("#n8nJwtTtlS"), 10) || 300,
    };

    // IMPORTANT: only send passphrase if user typed it
    if (jwtSecret.length > 0) {
        payload.jwt_passphrase = jwtSecret;
    }

    return payload;
}

async function saveN8nSettings() {
    const data = gatherN8nSettingsFormData();
    if (!data) return;

    const url  = `/api/systems/${data.radio_system_id}/n8n/settings`;
    const resp = await apiJson(url, { method: "PATCH", body: data });

    if (!resp.success) {
        showAlert(resp.message || "Failed to save n8n settings.", "danger");
        return;
    }

    // resp.result is STANDARD system.n8n
    populateUpdateN8n({
        radio_system_id : data.radio_system_id,
        system_name     : document.getElementById("updateN8nSystemName")?.value || "",
        n8n             : resp.result
    });

    showAlert(resp.message || "n8n settings saved.", "success");
}

function cryptoRandomHex(len = 64) {
    if (window.crypto?.getRandomValues) {
        const bytes = new Uint8Array(len / 2);
        window.crypto.getRandomValues(bytes);
        return Array.from(bytes).map(b => b.toString(16).padStart(2,'0')).join('');
    }
    return Array.from({length:len}, () => Math.floor(Math.random()*16).toString(16)).join('');
}

/*****************************************************
 *  Integration “Send Test” buttons
 *  - One config table
 *  - All buttons just POST to their existing endpoint
 *****************************************************/

const INTEGRATION_TESTS = [
    {
        key: "telegram",
        btn: "#telegramSendTestBtn",
        form: "#updateSystemTelegramForm",
        endpoint: (id) => `/api/systems/${id}/telegram/test`,
        canRun: () => !!(val("#telegramBotToken") && val("#telegramChannelId")),
        watch: ["#telegramBotToken", "#telegramChannelId"]
    },
    {
        key: "discord",
        btn: "#discordSendTestBtn",
        form: "#updateSystemDiscordForm",
        endpoint: (id) => `/api/systems/${id}/discord/test`,
        canRun: () => !!val("#discordWebhookURL"),
        watch: ["#discordWebhookURL"]
    },
    {
        key: "make",
        btn: "#makeSendTestBtn",
        form: "#updateSystemMakeForm",
        endpoint: (id) => `/api/systems/${id}/make/test`,
        canRun: () => !!(val("#makeWebhookURL") && val("#makeApiKey")),
        watch: ["#makeWebhookURL", "#makeApiKey"]
    },
    {
        key: "pushover",
        btn: "#pushoverSendTestBtn",
        form: "#updateSystemPushoverForm",
        endpoint: (id) => `/api/systems/${id}/pushover/test`,
        canRun: () => !!(val("#pushoverGroupToken") && val("#pushoverAppToken")),
        watch: ["#pushoverGroupToken", "#pushoverAppToken"]
    },
    {
        key: "email",
        btn: "#emailSendTestBtn",
        form: "#updateSystemSmtpForm",
        endpoint: (id) => `/api/systems/${id}/email/test`,
        canRun: () => {
            const host = val("#smtpHost");
            const port = val("#smtpPort");
            const from = val("#smtpEmailFrom");
            const haveRecipient = Array.isArray(currentEmailAddresses) && currentEmailAddresses.length > 0;
            return !!(host && port && from && haveRecipient);
        },
        watch: ["#smtpHost", "#smtpPort", "#smtpEmailFrom"]
        // recipients changes come from your add/delete flows which already call populate/refresh -> updateIntegrationTestButtonsState()
    },
    {
        key: "transcribe",
        btn: "#transcribeSendTestBtn",
        form: "#updateSystemTranscribeForm",
        endpoint: (id) => `/api/systems/${id}/transcribe/test`,
        canRun: () => !!(val("#transcribeURL") && val("#transcribeApiKey")),
        watch: ["#transcribeURL", "#transcribeApiKey"]
    },
    {
        key: "n8n",
        btn: "#n8nSendTestBtn",
        form: "#updateSystemN8nForm",
        endpoint: (id) => `/api/systems/${id}/n8n/test`,
        canRun: () => {
            const url = val("#n8nWebhookURL");
            const secret = val("#n8nJwtSecret"); // from your HTML
            return !!(url && secret);
        },
        watch: ["#n8nWebhookURL", "#n8nJwtSecret"]
    }

];

function wireIntegrationTestButtons() {
    // Wire click handlers
    INTEGRATION_TESTS.forEach(cfg => {
        const btn = document.querySelector(cfg.btn);
        if (!btn) return;

        btn.addEventListener("click", async (ev) => {
            ev.preventDefault();
            await runIntegrationTest(cfg);
        });
    });

    // One delegated watcher for all inputs we care about
    const watched = new Set(INTEGRATION_TESTS.flatMap(t => t.watch || []));
    document.addEventListener("input", (e) => {
        const id = e.target?.id ? `#${e.target.id}` : "";
        if (!id) return;
        if (watched.has(id)) updateIntegrationTestButtonsState();
    });

    // Initial state
    updateIntegrationTestButtonsState();
}

function updateIntegrationTestButtonsState() {
    INTEGRATION_TESTS.forEach(cfg => {
        const btn = document.querySelector(cfg.btn);
        if (!btn) return;

        const ok = !!(currentSystemId && cfg.canRun());
        toggleBtn(cfg.btn, ok);
    });
}

async function runIntegrationTest(cfg) {
    if (!currentSystemId) {
        showAlert("Select a system first.", "warning");
        return;
    }
    if (!cfg.canRun()) {
        showAlert("Missing required settings for this test.", "warning");
        updateIntegrationTestButtonsState();
        return;
    }

    const btn  = document.querySelector(cfg.btn);
    const form = document.querySelector(cfg.form);

    const csrf =
        form?.querySelector('[name=_csrf_token]')?.value ||
        document.querySelector('[name=_csrf_token]')?.value ||
        "";

    const endpoint = cfg.endpoint(currentSystemId);

    try {
        setBusy(btn, true);
        const resp = await apiJson(endpoint, { method: "POST", body: { _csrf_token: csrf } });

        if (!resp.success) {
            showAlert(resp.message || "Test failed.", "danger");
            return;
        }
        showAlert(resp.message || "Test sent.", "success");
    } catch (e) {
        showAlert(`Test failed: ${e}`, "danger");
    } finally {
        setBusy(btn, false);
        updateIntegrationTestButtonsState();
    }
}

/* ── tiny helpers (keep these if you already have them) ── */
function val(sel){ return document.querySelector(sel)?.value?.trim() || ""; }
function toggleBtn(sel, enabled){
    const el = document.querySelector(sel);
    if (!el) return;
    el.disabled = !enabled || el.dataset.busy === "1";
}
function setBusy(btn, busy){
    if (!btn) return;
    btn.dataset.busy = busy ? "1" : "0";
    btn.disabled = !!busy;
    btn.classList.toggle("disabled", !!busy);
}

/*****************************************************
 *               Form Submission Functions
 *****************************************************/

async function apiJson(url, {method="GET", body=null, headers={}} = {}) {
    const opts = {method, headers:{...headers}};
    if (body !== null) {
        opts.headers["Content-Type"] = "application/json";
        opts.body = JSON.stringify(body);
    }
    const resp = await fetch(url, opts);
    let data;
    try { data = await resp.json(); }
    catch { data = {success:false, message:"Invalid JSON response", result:[]}; }
    if (!resp.ok) data.http_status = resp.status;
    return data;
}

/**
 * Post a Form and its data to a given endpoint
 *
 */
function handlePostFormSubmission(endPoint, formId, formType) {
    let url = endPoint;

    const formElement = document.getElementById(formId);
    const formData = new FormData(formElement);

    return fetch(url, {method: "POST", body: formData})
        .then(response => response.json())
        .then(result => {
            if (result.success) {
                console.log("Submission Success:", result.message);
                showAlert(result.message, "success");
            } else {
                console.log("Submission Error:", result.message);
                showAlert(result.message, "danger");
            }
            return result;
        })
        .catch(error => {
            console.error(`Error POSTing submission form ${formId}: ${error}`);
            showAlert(`Error POSTing submission form ${formId}: ${error}`, "danger");
            throw error;
        });

}

/**
 * Handle Delete Form and its data to a given endpoint
 *
 */
function handleDeleteFormSubmission(endPoint, formId, formType) {

    const token = document.querySelector(`#${formId} [name="_csrf_token"]`)?.value || "";

    return fetch(endPoint, {
        method: "DELETE",
        headers: {
            "X-CSRFToken": token,
            "Accept": "application/json"
        },
        credentials: "same-origin"
    })
        .then(response => response.json())
        .then(result => {
            if (result.success) {
                console.log("Delete Submission Success:", result.message);
                showAlert(result.message, "success");
            } else {
                console.log("Delete Submission Error:", result.message);
                showAlert(result.message, "danger");
            }
            return result;
        })
        .catch(error => {
            console.error(`Error DELETEing submission form ${formId}: ${error}`);
            showAlert(`Error DELETEing submission form ${formId}: ${error}`, "danger");
            throw error;
        });

}

/*****************************************************
 *               Page Reset Functions
 *****************************************************/

function closeAllModals() {
    // Select all currently visible modals
    const openModals = document.querySelectorAll('.modal.show');
    openModals.forEach(modalEl => {
        // Get the Bootstrap Modal instance for each .modal element
        const modalInstance = bootstrap.Modal.getInstance(modalEl);
        if (modalInstance) {
            modalInstance.hide();
        }
    });
}

function clearAllForms() {
    const forms = document.querySelectorAll('.input-form');
    forms.forEach(form => {
        // Resets every input/select/textarea in the form to its initial value
        // If there's no initial/default value, it becomes empty/unchecked
        form.reset();
    });
}

(function initApiKeyEditor(){
    const apiKeyEl = document.getElementById("updateSystemApiKey");
    const toggleBtn = document.getElementById("toggleApiKeyEditBtn");
    const regenBtn  = document.getElementById("regenerateApiKeyBtn");

    function setEditable(on) {
        apiKeyEl.readOnly = !on;
        apiKeyEl.classList.toggle("bg-body-secondary", !on);
        toggleBtn.innerHTML = on ? '<i class="bi bi-lock"></i>' : '<i class="bi bi-pencil"></i>';
        toggleBtn.title = on ? "Lock API key" : "Edit API key";
    }

    // default locked
    setEditable(false);

    toggleBtn.addEventListener("click", () => {
        const turningOn = apiKeyEl.readOnly; // currently locked -> turning edit on
        if (turningOn) {
            // optional: warn before enabling edit
            if (!confirm("Enable editing for the API key?")) return;
            setEditable(true);
            apiKeyEl.focus();
            apiKeyEl.select();
        } else {
            // lock it back
            setEditable(false);
        }
    });

    // simple client-side check (server will enforce too)
    apiKeyEl.addEventListener("input", () => {
        const v = apiKeyEl.value.trim();
        const ok = v.length >= 16 && v.length <= 128 && /^[A-Za-z0-9_\-\.~]+$/.test(v);
        apiKeyEl.classList.toggle("is-invalid", v !== "" && !ok);
    });

    // If they regenerate, force lock again
    regenBtn.addEventListener("click", () => setEditable(false));
})();
