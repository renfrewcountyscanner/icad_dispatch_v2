document.addEventListener('DOMContentLoaded', function () {
    const loginForm = document.getElementById('loginForm');
    const submitBtn = document.getElementById('submitLoginBtn');
    const loginErrorDiv = document.getElementById('loginError');
    const passwordInput = document.getElementById('loginPassword');
    const togglePasswordButton = document.getElementById('togglePassword');

    togglePasswordButton?.addEventListener('click', function () {
        const type = passwordInput.getAttribute('type') === 'password' ? 'text' : 'password';
        passwordInput.setAttribute('type', type);
        togglePasswordButton.innerHTML = type === 'password'
            ? '<i class="bi bi-eye-fill" style="font-size: 1.40rem"></i>'
            : '<i class="bi bi-eye-slash-fill" style="font-size: 1.40rem"></i>';
    });

    loginForm.addEventListener('submit', async (event) => {
        event.preventDefault();

        if (!loginForm.checkValidity()) {
            loginForm.classList.add('was-validated');
            return;
        }

        loginErrorDiv.classList.remove('show');
        loginErrorDiv.textContent = '';

        // Show loading state on the button.
        submitBtn.classList.add('loading');
        submitBtn.disabled = true;

        const formData = new FormData(loginForm);
        const token = formData.get('_csrf_token');

        try {
            const resp = await fetch('/auth/login', {
                method: 'POST',
                body: formData,
                headers: {
                    'Accept': 'application/json',
                    'X-Requested-With': 'XMLHttpRequest',
                    ...(token ? { "X-CSRFToken": token } : {})
                },
                credentials: 'same-origin'
            });

            // If server redirected, just go there
            if (resp.redirected) {
                window.location.assign(resp.url);
                return;
            }

            // Try to parse JSON first
            const ct = resp.headers.get('content-type') || '';
            let data = null, gotJSON = false;
            if (ct.includes('application/json')) {
                try {
                    data = await resp.json();
                    gotJSON = true;
                } catch { /* fall through to text */ }
            }

            if (gotJSON) {
                if (resp.ok && data?.success) {
                    window.location.href = '/dashboard';
                    return;
                }
                throw new Error(data?.message || `HTTP ${resp.status} ${resp.statusText}`);
            }

            // Fallback: show raw text (first few hundred chars)
            const text = await resp.text();
            const snippet = text.slice(0, 800); // avoid dumping megabytes
            throw new Error(`HTTP ${resp.status} ${resp.statusText}\n${snippet}`);

        } catch (err) {
            console.error(err);
            loginErrorDiv.classList.add('show');
            loginErrorDiv.textContent = String(err?.message || err);
        } finally {
            // Restore the button unless we've already navigated away.
            submitBtn.classList.remove('loading');
            submitBtn.disabled = false;
        }
    });
});
