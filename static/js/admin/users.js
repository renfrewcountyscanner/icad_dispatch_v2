// User management page behavior: create, edit, delete users.
(function () {
    "use strict";

    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";

    function notify(message, type) {
        if (typeof showAlert === "function") {
            showAlert(message, type);
        } else {
            alert(message);
        }
    }

    // ── Create user ────────────────────────────────────────────────
    const createForm = document.getElementById("createUserForm");
    if (createForm) {
        createForm.addEventListener("submit", async (e) => {
            e.preventDefault();
            const btn = document.getElementById("createUserSubmit");
            const original = btn.innerHTML;
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Creating…';

            const formData = new FormData(createForm);
            try {
                const resp = await fetch("/admin/users", {
                    method: "POST",
                    headers: { "X-CSRFToken": csrfToken },
                    body: formData,
                });
                const data = await resp.json();
                if (data.success) {
                    notify("User created successfully", "success");
                    setTimeout(() => window.location.reload(), 800);
                } else {
                    notify(data.message || "Failed to create user", "danger");
                    btn.disabled = false;
                    btn.innerHTML = original;
                }
            } catch (err) {
                notify("Network error: " + err.message, "danger");
                btn.disabled = false;
                btn.innerHTML = original;
            }
        });
    }

    // ── Edit user ──────────────────────────────────────────────────
    const editModalEl = document.getElementById("editUserModal");
    const editModal = editModalEl ? new bootstrap.Modal(editModalEl) : null;

    document.querySelectorAll(".btn-edit").forEach((btn) => {
        btn.addEventListener("click", () => {
            const row = btn.closest("tr");
            const userId = row.dataset.userId;
            const username = row.dataset.username;
            const isAdmin = row.dataset.isAdmin === "1";
            const isActive = row.dataset.isActive === "1";
            let systems = {};
            try {
                systems = JSON.parse(row.dataset.systems || "{}");
            } catch (_) {
                systems = {};
            }

            document.getElementById("editUserId").value = userId;
            document.getElementById("editUsername").value = username;
            document.getElementById("editPassword").value = "";
            document.getElementById("editIsAdmin").checked = isAdmin;
            document.getElementById("editIsActive").checked = isActive;

            // Root user (id 1) cannot lose admin/active status.
            const lockRoot = userId === "1";
            document.getElementById("editIsAdmin").disabled = lockRoot;
            document.getElementById("editIsActive").disabled = lockRoot;

            document.querySelectorAll(".edit-sys-chk").forEach((chk) => {
                const sysId = chk.value;
                const assigned = Object.prototype.hasOwnProperty.call(systems, sysId);
                chk.checked = assigned;
                const sel = document.querySelector(`.edit-sys-perm[data-sys-id="${sysId}"]`);
                if (sel) sel.value = assigned ? systems[sysId] : "read";
            });

            if (editModal) editModal.show();
        });
    });

    const editForm = document.getElementById("editUserForm");
    if (editForm) {
        editForm.addEventListener("submit", async (e) => {
            e.preventDefault();
            const userId = document.getElementById("editUserId").value;
            const btn = document.getElementById("editUserSubmit");
            const original = btn.innerHTML;

            const systems = {};
            document.querySelectorAll(".edit-sys-chk").forEach((chk) => {
                if (chk.checked) {
                    const sel = document.querySelector(`.edit-sys-perm[data-sys-id="${chk.value}"]`);
                    systems[chk.value] = sel ? sel.value : "read";
                }
            });

            const payload = {
                username: document.getElementById("editUsername").value.trim(),
                is_admin: document.getElementById("editIsAdmin").checked,
                is_active: document.getElementById("editIsActive").checked,
                systems: systems,
            };
            const newPw = document.getElementById("editPassword").value;
            if (newPw) payload.password = newPw;

            btn.disabled = true;
            btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Saving…';

            try {
                const resp = await fetch(`/admin/users/${userId}`, {
                    method: "PATCH",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": csrfToken,
                    },
                    body: JSON.stringify(payload),
                });
                const data = await resp.json();
                if (data.success) {
                    notify("User updated successfully", "success");
                    setTimeout(() => window.location.reload(), 800);
                } else {
                    notify(data.message || "Failed to update user", "danger");
                    btn.disabled = false;
                    btn.innerHTML = original;
                }
            } catch (err) {
                notify("Network error: " + err.message, "danger");
                btn.disabled = false;
                btn.innerHTML = original;
            }
        });
    }

    // ── Delete user ────────────────────────────────────────────────
    const deleteModalEl = document.getElementById("deleteUserModal");
    const deleteModal = deleteModalEl ? new bootstrap.Modal(deleteModalEl) : null;
    let pendingDeleteId = null;

    document.querySelectorAll(".btn-delete").forEach((btn) => {
        btn.addEventListener("click", () => {
            const row = btn.closest("tr");
            pendingDeleteId = row.dataset.userId;
            document.getElementById("deleteUserName").textContent = row.dataset.username;
            if (deleteModal) deleteModal.show();
        });
    });

    const deleteConfirm = document.getElementById("deleteUserConfirm");
    if (deleteConfirm) {
        deleteConfirm.addEventListener("click", async () => {
            if (!pendingDeleteId) return;
            deleteConfirm.disabled = true;
            try {
                const resp = await fetch(`/admin/users/${pendingDeleteId}`, {
                    method: "DELETE",
                    headers: { "X-CSRFToken": csrfToken },
                });
                const data = await resp.json();
                if (data.success) {
                    notify("User deleted successfully", "success");
                    setTimeout(() => window.location.reload(), 800);
                } else {
                    notify(data.message || "Failed to delete user", "danger");
                    deleteConfirm.disabled = false;
                }
            } catch (err) {
                notify("Network error: " + err.message, "danger");
                deleteConfirm.disabled = false;
            }
        });
    }
})();
