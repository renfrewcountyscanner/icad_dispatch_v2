/* public_map/static/js/help.js */
/* Help modal for the Public Live Emergency Map */

let helpModalOpen = false;

function toggleHelpModal() {
    helpModalOpen = !helpModalOpen;
    const modal = document.getElementById('helpModal');
    if (helpModalOpen) {
        modal.classList.add('open');
        // Default to first tab
        switchHelpTab('overview');
    } else {
        modal.classList.remove('open');
    }
}

function closeHelpModal() {
    helpModalOpen = false;
    document.getElementById('helpModal').classList.remove('open');
}

function switchHelpTab(tabId) {
    // Update tab buttons
    document.querySelectorAll('.help-tab-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.tab === tabId);
    });
    // Update tab content
    document.querySelectorAll('.help-tab-content').forEach(content => {
        content.classList.toggle('active', content.dataset.tab === tabId);
    });
}

function initHelpModal() {
    // Tab button clicks
    document.querySelectorAll('.help-tab-btn').forEach(btn => {
        btn.addEventListener('click', () => switchHelpTab(btn.dataset.tab));
    });

    // Close on backdrop click
    document.getElementById('helpModal').addEventListener('click', (e) => {
        if (e.target.id === 'helpModal') closeHelpModal();
    });
}

document.addEventListener('DOMContentLoaded', initHelpModal);
