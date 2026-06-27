// Audio upload test page behavior.
(function () {
    "use strict";

    const form = document.getElementById("uploadForm");
    if (!form) return;

    const submitBtn = document.getElementById("uploadSubmitBtn");
    const progressBar = document.getElementById("uploadProgress");
    const resultDiv = document.getElementById("uploadResult");
    const detailsDiv = document.getElementById("resultDetails");

    form.addEventListener("submit", async function (e) {
        e.preventDefault();

        const formData = new FormData(this);
        const alertDiv = resultDiv.querySelector(".alert");

        // Show loading state
        submitBtn.disabled = true;
        submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Processing…';
        progressBar.classList.remove("d-none");
        resultDiv.classList.remove("d-none");
        alertDiv.className = "alert alert-info";
        alertDiv.textContent = "Uploading and processing audio…";
        detailsDiv.innerHTML = "";

        try {
            const response = await fetch("/api/call-upload", {
                method: "POST",
                body: formData,
            });

            let data;
            try {
                data = await response.json();
            } catch (_) {
                data = null;
            }

            if (response.ok) {
                alertDiv.className = "alert alert-success";
                alertDiv.textContent = data?.message || "Upload successful!";

                const result = data?.result || {};
                let html = '<div class="row"><div class="col-md-6">';
                html += '<h6 class="text-muted mb-2">Call Details</h6>';
                html += '<dl class="row small">';

                if (result.call_id) {
                    html += '<dt class="col-sm-4">Call ID:</dt><dd class="col-sm-8"><strong>' + escapeHtml(String(result.call_id)) + '</strong></dd>';
                }
                if (result.system_name) {
                    html += '<dt class="col-sm-4">System:</dt><dd class="col-sm-8">' + escapeHtml(result.system_name) + '</dd>';
                }
                if (result.talkgroup) {
                    html += '<dt class="col-sm-4">Talkgroup:</dt><dd class="col-sm-8">' + escapeHtml(String(result.talkgroup)) + '</dd>';
                }
                if (result.duration_s !== undefined) {
                    html += '<dt class="col-sm-4">Duration:</dt><dd class="col-sm-8">' + Number(result.duration_s).toFixed(1) + 's</dd>';
                }
                if (result.merged !== undefined) {
                    html += '<dt class="col-sm-4">Merged:</dt><dd class="col-sm-8">' + (result.merged ? "Yes" : "No") + '</dd>';
                }

                html += '</dl></div><div class="col-md-6">';
                html += '<h6 class="text-muted mb-2">Detection Results</h6>';
                html += '<dl class="row small">';

                if (result.tones_detected !== undefined) {
                    html += '<dt class="col-sm-4">Tones:</dt><dd class="col-sm-8">' + escapeHtml(String(result.tones_detected)) + '</dd>';
                }

                if (result.tone_types && Object.keys(result.tone_types).length > 0) {
                    html += '<dt class="col-sm-4">Types:</dt><dd class="col-sm-8">';
                    html += Object.entries(result.tone_types)
                        .map(function (kv) { return escapeHtml(kv[0]) + ": " + escapeHtml(String(kv[1])); })
                        .join(", ");
                    html += '</dd>';
                }

                if (result.triggers_fired && result.triggers_fired.length > 0) {
                    html += '<dt class="col-sm-4">Triggers:</dt><dd class="col-sm-8">';
                    html += result.triggers_fired.map(function (t) { return '<span class="badge bg-info">' + escapeHtml(t) + '</span>'; }).join(" ");
                    html += '</dd>';
                } else {
                    html += '<dt class="col-sm-4">Triggers:</dt><dd class="col-sm-8"><em class="text-muted">None</em></dd>';
                }

                if (result.transcript) {
                    html += '<dt class="col-sm-4">Transcript:</dt><dd class="col-sm-8"><small>' + escapeHtml(result.transcript) + '...</small></dd>';
                }

                html += '</dl></div></div>';
                detailsDiv.innerHTML = html;
            } else {
                alertDiv.className = "alert alert-danger";
                var errorMsg = "Unknown error";
                if (data && typeof data === "object") {
                    errorMsg = data.message || data.error || JSON.stringify(data);
                } else if (typeof data === "string") {
                    errorMsg = data;
                }
                alertDiv.textContent = "Error: " + errorMsg;
                detailsDiv.innerHTML = "";
            }
        } catch (err) {
            alertDiv.className = "alert alert-danger";
            alertDiv.textContent = "Upload failed: " + err.message;
        } finally {
            submitBtn.disabled = false;
            submitBtn.innerHTML = '<i class="bi bi-upload"></i> Upload &amp; Process';
            progressBar.classList.add("d-none");
        }
    });

    // Local escapeHtml fallback.
    function escapeHtml(text) {
        if (typeof window.escapeHtml === "function" && window.escapeHtml !== escapeHtml) {
            return window.escapeHtml(text);
        }
        var div = document.createElement("div");
        div.textContent = text;
        return div.innerHTML;
    }
})();
