/* Shared page behaviour: theme toggle, sidebar state, dropdowns,
   toast notifications, and cross-page flash messages. */

// ---------- Theme ----------
function toggleTheme() {
    const root = document.documentElement;
    const light = root.getAttribute('data-theme') === 'light';
    if (light) {
        root.removeAttribute('data-theme');
        localStorage.setItem('sl-theme', 'dark');
    } else {
        root.setAttribute('data-theme', 'light');
        localStorage.setItem('sl-theme', 'light');
    }
}

// ---------- Sidebar ----------
function toggleSidebar() {
    const sidebar = document.querySelector('.sidebar');
    const topbar = document.querySelector('.topbar');
    if (!sidebar) return;
    sidebar.classList.toggle('minimized');
    if (topbar) topbar.classList.toggle('sidebar-minimized');
    localStorage.setItem('sl-sidebar', sidebar.classList.contains('minimized') ? 'minimized' : 'expanded');
}

// ---------- Dropdowns ----------
function toggleDropdown(id) {
    const dropdown = document.getElementById(id);
    if (!dropdown) return;
    document.querySelectorAll('.dropdown').forEach(function (d) {
        if (d.id !== id) d.classList.remove('active');
    });
    dropdown.classList.toggle('active');
}

document.addEventListener('click', function (e) {
    if (!e.target.closest('.search-container') && !e.target.closest('.topbar-actions')) {
        document.querySelectorAll('.dropdown').forEach(function (d) {
            d.classList.remove('active');
        });
    }
});

// ---------- Toasts ----------
function showToast(message, type = 'info') {
    const container = document.getElementById('toastContainer');
    if (!container) return;
    const toast = document.createElement('div');
    toast.className = 'toast ' + type;

    const icon = type === 'success'
        ? '<svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path></svg>'
        : '<svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>';

    toast.innerHTML = icon + message;
    container.appendChild(toast);

    setTimeout(function () { toast.classList.add('show'); }, 10);
    setTimeout(function () {
        toast.classList.remove('show');
        setTimeout(function () { toast.remove(); }, 300);
    }, 3000);
}

/* Queue a toast, then navigate. The destination page shows it on load. */
function flash(message, type, href) {
    sessionStorage.setItem('sl-flash', JSON.stringify({ message: message, type: type }));
    location.href = href;
}

document.addEventListener('DOMContentLoaded', function () {
    const pending = sessionStorage.getItem('sl-flash');
    if (pending) {
        sessionStorage.removeItem('sl-flash');
        const data = JSON.parse(pending);
        showToast(data.message, data.type);
    }
});

// ---------- Auth (static prototype: no backend yet) ----------
function doLogin() {
    flash('Logged in successfully', 'success', 'dashboard.html');
}

function doLogout() {
    flash('Signed out', 'info', 'index.html');
}

function doRegister() {
    flash('Registration submitted for Administrator approval', 'success', 'index.html');
}

function doPasswordReset() {
    flash('Password reset link sent to your email', 'success', 'index.html');
}

// ---------- Form helpers ----------
function selectFilter(element) {
    Array.from(element.parentElement.children).forEach(function (child) {
        child.classList.remove('active');
    });
    element.classList.add('active');
}

function selectCategory(element) {
    selectFilter(element);
}
