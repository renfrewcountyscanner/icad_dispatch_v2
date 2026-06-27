// Change password page behavior.
(function () {
    "use strict";

    const form = document.getElementById("changePasswordForm");
    if (!form) return;

    const currentPassword = document.getElementById("currentPassword");
    const newPassword = document.getElementById("newPassword");
    const confirmPassword = document.getElementById("confirmPassword");

    // ── Password visibility toggles ───────────────────────────────
    document.querySelectorAll(".pw-toggle").forEach(function (btn) {
        btn.addEventListener("click", function () {
            var input = btn.parentElement.querySelector("input");
            if (!input) return;
            var isPass = input.getAttribute("type") === "password";
            input.setAttribute("type", isPass ? "text" : "password");
            btn.innerHTML = isPass
                ? '<i class="bi bi-eye-slash-fill" style="font-size: 1.40rem"></i>'
                : '<i class="bi bi-eye-fill" style="font-size: 1.40rem"></i>';
        });
    });

    // ── Cancel button ────────────────────────────────────────────
    var cancelBtn = document.getElementById("cancelBtn");
    if (cancelBtn) {
        cancelBtn.addEventListener("click", function () {
            window.history.back();
        });
    }

    // ── Password strength validation ─────────────────────────────
    var passRegex = /^(?=.*[0-9])(?=.*[^A-Za-z0-9]).{8,}$/;

    newPassword.addEventListener("input", function () {
        newPassword.setCustomValidity("");
        confirmPassword.setCustomValidity("");
        if (!passRegex.test(newPassword.value)) {
            newPassword.setCustomValidity("Must be 8+ chars with a number and symbol.");
        }
        if (confirmPassword.value && newPassword.value !== confirmPassword.value) {
            confirmPassword.setCustomValidity("Passwords must match.");
        }
    });

    confirmPassword.addEventListener("input", function () {
        confirmPassword.setCustomValidity("");
        if (confirmPassword.value && newPassword.value !== confirmPassword.value) {
            confirmPassword.setCustomValidity("Passwords must match.");
        }
    });

    // ── Submit ───────────────────────────────────────────────────
    form.addEventListener("submit", async function (event) {
        event.preventDefault();

        // Let the browser do its validation first.
        form.classList.add("was-validated");
        if (!form.checkValidity()) return;

        var btn = document.getElementById("submitChangePasswordBtn");
        var original = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Saving…';

        var formData = new FormData(form);

        try {
            var resp = await fetch(form.getAttribute("action") || "/auth/change_password", {
                method: "POST",
                headers: { "X-Requested-With": "XMLHttpRequest" },
                body: formData,
            });
            var data = await resp.json();

            if (data.success) {
                if (typeof showAlert === "function") showAlert(data.message, "success");
                form.reset();
                form.classList.remove("was-validated");
                currentPassword.focus();
            } else {
                if (typeof showAlert === "function") showAlert(data.message, "danger");
            }
        } catch (err) {
            if (typeof showAlert === "function") showAlert(err.toString(), "danger");
        } finally {
            btn.disabled = false;
            btn.innerHTML = original;
        }
    });
})();
