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

    submitBtn.addEventListener('click', async () => {
        // If your button is type="submit", also prevent the default form post:
        // event?.preventDefault();

        if (!loginForm.checkValidity()) return;

        loginErrorDiv.classList.add('d-none');
        loginErrorDiv.textContent = '';

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
            loginErrorDiv.classList.remove('d-none');

            // Use textContent to avoid injecting HTML from server error pages
            const pre = document.createElement('pre');
            pre.className = 'mb-0';
            pre.style.whiteSpace = 'pre-wrap';
            pre.textContent = String(err?.message || err);
            loginErrorDiv.innerHTML = ''; // clear any prior content
            loginErrorDiv.appendChild(pre);
        }
    });
});
