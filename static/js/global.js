function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function showAlert(message, type) {
    let toast_container = document.getElementById('toastContainer');
    const safeMessage = escapeHtml(String(message));
    const bgClass = {
        success: 'bg-success',
        warning: 'bg-warning',
        info: 'bg-info',
        danger: 'bg-danger'
    };

    const toastHtml = `<div class="toast align-items-center text-white border-0" role="alert" aria-live="assertive" aria-atomic="true" data-bs-autohide="true" data-bs-delay="5000">
                                    <div class="d-flex ${bgClass[type] || 'bg-primary'}">
                                      <div class="toast-body">
                                        ${safeMessage}
                                      </div>
                                      <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>
                                    </div>
                                  </div>`;

    // Insert new toast into the container
    toast_container.insertAdjacentHTML('beforeend', toastHtml);

    // Grab the newly inserted toast element
    const toastEl = toast_container.lastElementChild;
    const toast = new bootstrap.Toast(toastEl);

    // Show the toast
    // The toast will automatically hide after 5 seconds due to data-bs-autohide and data-bs-delay
    toast.show();

    // When it becomes fully hidden, remove it from DOM
    toastEl.addEventListener('hidden.bs.toast', () => {
        // Clean up internal bootstrap event listeners
        toast.dispose();
        // Remove from the document
        toastEl.remove();
    });


}

/**
 * Show a styled confirmation dialog (replaces native confirm()).
 * Returns a Promise<boolean> that resolves true if confirmed.
 *
 * Falls back to native confirm() if the modal element is unavailable.
 *
 * @param {Object} opts
 * @param {string} [opts.title]    Modal title.
 * @param {string} [opts.body]     Body message (plain text, escaped).
 * @param {string} [opts.confirmText] Confirm button label.
 * @param {string} [opts.confirmClass] Confirm button class (e.g. 'btn-danger').
 */
function confirmAction(opts) {
    opts = opts || {};
    const modalEl = document.getElementById('globalConfirmModal');
    if (!modalEl || typeof bootstrap === 'undefined') {
        return Promise.resolve(window.confirm(opts.body || 'Are you sure?'));
    }

    const titleEl = document.getElementById('globalConfirmTitle');
    const bodyEl = document.getElementById('globalConfirmBody');
    const okBtn = document.getElementById('globalConfirmOk');

    titleEl.textContent = opts.title || 'Please Confirm';
    bodyEl.textContent = opts.body || 'Are you sure?';
    okBtn.textContent = opts.confirmText || 'Confirm';
    okBtn.className = 'btn ' + (opts.confirmClass || 'btn-danger');

    const modal = bootstrap.Modal.getOrCreateInstance(modalEl);

    return new Promise((resolve) => {
        let settled = false;

        const onOk = () => {
            settled = true;
            modal.hide();
            resolve(true);
        };
        const onHidden = () => {
            okBtn.removeEventListener('click', onOk);
            modalEl.removeEventListener('hidden.bs.modal', onHidden);
            if (!settled) resolve(false);
        };

        okBtn.addEventListener('click', onOk);
        modalEl.addEventListener('hidden.bs.modal', onHidden);
        modal.show();
    });
}

document.addEventListener('DOMContentLoaded', function () {
    const messages = document.querySelectorAll('.flash-message');
    messages.forEach(message => {
        const category = message.getAttribute('data-category');
        const msg = message.getAttribute('data-message');
        showAlert(msg, category);
    });
});
