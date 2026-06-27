// Admin database import page behavior.
(function () {
    "use strict";

    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
    const form = document.getElementById("importForm");
    const importBtn = document.getElementById("importBtn");
    const resultContainer = document.getElementById("resultContainer");
    const progressWrap = document.getElementById("importProgressWrap");
    const progressText = document.getElementById("importProgressText");

    if (!form) return;

    form.addEventListener("submit", async (e) => {
        e.preventDefault();

        const file = document.getElementById("dbFile").files[0];
        if (!file) {
            showResult("error", "No file selected");
            return;
        }

        const formData = new FormData();
        formData.append("db_file", file);

        importBtn.disabled = true;
        importBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Importing…';
        progressWrap.classList.remove("d-none");
        progressText.classList.remove("d-none");
        resultContainer.innerHTML = "";

        try {
            const resp = await fetch("/admin/import-db", {
                method: "POST",
                headers: { "X-CSRFToken": csrfToken },
                body: formData,
            });
            const data = await resp.json();
            if (data.success) {
                showResult("success", data.result);
                form.reset();
            } else {
                showResult("error", data.message || "Import failed");
            }
        } catch (err) {
            showResult("error", "Network error: " + err.message);
        } finally {
            importBtn.disabled = false;
            importBtn.innerHTML = '<i class="bi bi-upload"></i> Import Database';
            progressWrap.classList.add("d-none");
            progressText.classList.add("d-none");
        }
    });

    function showResult(type, data) {
        resultContainer.innerHTML = "";

        if (typeof data === "string") {
            const div = document.createElement("div");
            div.className = `import-result ${type}`;
            div.innerHTML =
                `<strong>${type === "success" ? "\u2713 Success" : "\u2717 Error"}</strong>` +
                `<div>${escapeHtml(data)}</div>`;
            resultContainer.appendChild(div);
            return;
        }

        const stats = data;
        const rows = [
            ["Calls imported", stats.calls_imported],
            ["Calls skipped (duplicates)", stats.calls_skipped],
            ["Tone events imported", stats.tones_imported],
            ["Transcripts imported", stats.transcripts_imported],
            ["VAD segments imported", stats.vad_segments_imported],
            ["Trigger fires imported", stats.trigger_fires_imported],
        ];
        let html = '<div class="import-result success"><strong>\u2713 Import Complete</strong><div class="mt-2">';
        rows.forEach(([label, value]) => {
            html += `<div class="stat-row"><span class="stat-label">${label}:</span><span class="stat-value">${value}</span></div>`;
        });
        if (stats.errors > 0) {
            html += `<div class="stat-row" style="color:#d87d7d;"><span class="stat-label">Errors:</span><span class="stat-value">${stats.errors}</span></div>`;
        }
        html += "</div></div>";
        resultContainer.innerHTML = html;
    }

    // Fallback escapeHtml if global.js hasn't defined one.
    function escapeHtml(text) {
        if (typeof window.escapeHtml === "function" && window.escapeHtml !== escapeHtml) {
            return window.escapeHtml(text);
        }
        const div = document.createElement("div");
        div.textContent = text;
        return div.innerHTML;
    }
})();
