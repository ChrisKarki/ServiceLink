/* Shared application shell (topbar + sidebar).
   Each authenticated page contains <header data-shell="topbar"> and
   <nav data-shell="sidebar"> placeholders; this script fills them in.
   When the project moves to Flask in Phase 4, this file maps directly
   to a Jinja base template. */

const NAV_ITEMS = [
    {
        page: 'dashboard', href: 'dashboard.html', label: 'Dashboard',
        icon: 'M3 9l9-7 9 7v11a2 2 0 0 1-2 2h-4 M9 22H5a2 2 0 0 1-2-2V9 M9 22v-10h6v10'
    },
    {
        page: 'tickets', href: 'tickets.html', label: 'Tickets',
        icon: 'M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z'
    },
    {
        page: 'resources', href: 'resources.html', label: 'Resources',
        icon: 'M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 2h10a2 2 0 002-2V8m-9 4h4'
    },
    {
        page: 'knowledge-base', href: 'knowledge-base.html', label: 'Knowledge Base',
        icon: 'M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253'
    },
    {
        page: 'reports', href: 'reports.html', label: 'Reports',
        icon: 'M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z'
    },
    {
        page: 'team', href: 'team.html', label: 'Team',
        icon: 'M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z'
    }
];

/* Which page each secondary screen belongs to, for sidebar highlighting */
const NAV_PARENT = {
    'submit-request': 'tickets',
    'ticket-detail': 'tickets',
    'ticket-edit': 'tickets',
    'ticket-escalate': 'tickets',
    'resource-edit': 'resources',
    'kb-article-edit': 'knowledge-base'
};

function svgIcon(path, size) {
    return '<svg width="' + size + '" height="' + size + '" fill="none" stroke="currentColor" viewBox="0 0 24 24">' +
        '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="' + path + '"></path></svg>';
}

function renderTopbar() {
    return '' +
        '<div class="topbar-left">' +
        '    <div class="logo-toggle-wrapper" onclick="toggleSidebar()" title="Minimize / Expand Sidebar">' +
        '        <img class="logo-img" src="assets/logo.svg" alt="ServiceLink logo">' +
        '        <div class="toggle-icon">' + svgIcon('M11 19l-7-7 7-7m8 14l-7-7 7-7', 18) + '</div>' +
        '    </div>' +
        '    <span class="brand-text">ServiceLink</span>' +
        '    <div class="search-container" style="position: relative; display: flex; align-items: center; width: 400px; margin-left: 10px;">' +
        '        <svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24" style="position: absolute; left: 14px; top: 50%; transform: translateY(-50%); color: var(--text-secondary); pointer-events: none;">' +
        '            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"></path></svg>' +
        '        <input type="text" class="search-input" placeholder="Search tickets, articles, resources..." style="padding-left: 38px; width: 100%;"' +
        '            onfocus="toggleDropdown(\'searchDropdown\')" onblur="setTimeout(()=>toggleDropdown(\'searchDropdown\'), 200)">' +
        '        <div class="dropdown" id="searchDropdown" style="left: 0; right: auto; width: 100%; top: 48px;">' +
        '            <div class="dropdown-header">Recent Searches</div>' +
        '            <div class="dropdown-item">INC-1042 Production Build</div>' +
        '            <div class="dropdown-item">VPN Configuration KB</div>' +
        '            <div class="dropdown-item" style="color: var(--accent-color); text-align: center;">Advanced Search...</div>' +
        '        </div>' +
        '    </div>' +
        '</div>' +
        '<div class="topbar-actions">' +
        '    <button class="icon-btn theme-toggle" onclick="toggleTheme()" title="Toggle dark / light mode">' +
        '        <span class="icon-moon">' + svgIcon('M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z', 20) + '</span>' +
        '        <span class="icon-sun">' + svgIcon('M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z', 20) + '</span>' +
        '    </button>' +
        '    <div style="position: relative;">' +
        '        <button class="icon-btn" onclick="toggleDropdown(\'notifDropdown\')" title="Notifications">' +
        svgIcon('M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9', 22) +
        '            <div class="badge-dot"></div>' +
        '        </button>' +
        '        <div class="dropdown" id="notifDropdown">' +
        '            <div class="dropdown-header">Notifications <span class="badge" style="background: var(--button-secondary-bg); border: 1px solid var(--panel-border);">2</span></div>' +
        '            <div class="dropdown-item">' +
        '                <div class="text-sm" style="color: var(--text-primary); font-weight: 500;">SLA Breach Warning</div>' +
        '                <div class="text-sm" style="font-size: 12px; margin-top: 4px;">INC-1042 is approaching 4hr resolution target.</div>' +
        '            </div>' +
        '            <div class="dropdown-item">' +
        '                <div class="text-sm" style="color: var(--text-primary); font-weight: 500;">Ticket Assigned</div>' +
        '                <div class="text-sm" style="font-size: 12px; margin-top: 4px;">REQ-2055 has been assigned to you.</div>' +
        '            </div>' +
        '            <div class="dropdown-item" style="color: var(--accent-color); text-align: center; padding: 8px;">Mark all as read</div>' +
        '        </div>' +
        '    </div>' +
        '    <div style="position: relative;">' +
        '        <div class="user-profile" onclick="toggleDropdown(\'profileDropdown\')">' +
        '            <div class="avatar">CK</div>' +
        '        </div>' +
        '        <div class="dropdown" id="profileDropdown">' +
        '            <div class="dropdown-item flex-row" style="padding: 16px;">' +
        '                <div class="avatar" style="width: 40px; height: 40px; font-size: 14px;">CK</div>' +
        '                <div>' +
        '                    <div style="color: var(--text-primary); font-weight: 500;">Chris Karki</div>' +
        '                    <div class="text-sm" style="color: var(--accent-color); font-weight: 500;">Manager</div>' +
        '                    <div class="text-sm">chris@company.com</div>' +
        '                </div>' +
        '            </div>' +
        '            <div class="dropdown-item flex-row">' +
        svgIcon('M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z M15 12a3 3 0 11-6 0 3 3 0 016 0z', 16) +
        '                Settings</div>' +
        '            <div class="dropdown-item flex-row" style="color: var(--danger-color);" onclick="doLogout()">' +
        svgIcon('M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1', 16) +
        '                Sign out</div>' +
        '        </div>' +
        '    </div>' +
        '</div>';
}

function renderSidebar(activePage) {
    const items = NAV_ITEMS.map(function (item) {
        const active = item.page === activePage ? ' active' : '';
        return '<li class="nav-item' + active + '" onclick="location.href=\'' + item.href + '\'" title="' + item.label + '">' +
            svgIcon(item.icon, 16) +
            '<span class="nav-label">' + item.label + '</span></li>';
    }).join('');
    return '<ul class="nav-menu">' + items + '</ul>';
}

(function initShell() {
    const currentPage = document.body.dataset.page || '';
    const activePage = NAV_PARENT[currentPage] || currentPage;
    const minimized = localStorage.getItem('sl-sidebar') !== 'expanded';

    const topbar = document.querySelector('[data-shell="topbar"]');
    if (topbar) {
        topbar.className = 'topbar' + (minimized ? ' sidebar-minimized' : '');
        topbar.innerHTML = renderTopbar();
    }

    const sidebar = document.querySelector('[data-shell="sidebar"]');
    if (sidebar) {
        sidebar.className = 'sidebar' + (minimized ? ' minimized' : '');
        sidebar.innerHTML = renderSidebar(activePage);
    }
})();
