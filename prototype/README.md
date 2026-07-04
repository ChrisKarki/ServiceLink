# ServiceLink — Phase 3 Static Prototype

Multi-page static build of the ServiceLink UI. No backend; all data is placeholder.
Structured so each page maps 1:1 to a Flask route + Jinja template in Phase 4.

## Structure

```
index.html               Login (Design Doc 5.1)
register.html            Self-registration (FR-1.2; linked from login per 5.1)
forgot-password.html     Password reset request (FR-1.2; linked from login per 5.1)
dashboard.html           Dashboard (5.2)
submit-request.html      Submit Request (5.3)
tickets.html             Tickets List (5.4)
ticket-detail.html       Ticket detail w/ comments (FR-2.4, UC-02)
ticket-edit.html         Edit ticket (UC-02)
ticket-escalate.html     Escalation & SLA flow (FR-2.5, UC-02 AF-3)
resources.html           Resources (5.5)
resource-edit.html       Resource Edit (5.8)
knowledge-base.html      KB list/search (FR-4.2, UC-04)
kb-article-edit.html     KB Article Edit (5.9)
reports.html             Reports (5.6)
team.html                Team / user management (5.7)

css/main.css             All styles + light-theme variable overrides
js/theme.js              Applies saved theme before first paint (loaded in <head>)
js/layout.js             Shared topbar + sidebar shell (future Jinja base.html)
js/app.js                Theme toggle, sidebar state, dropdowns, toasts, flash messages
assets/logo.svg          Placeholder logo
```

## Conventions

- **Shell injection:** authenticated pages contain `<header data-shell="topbar">` and
  `<nav data-shell="sidebar">`; `layout.js` fills them. Sidebar highlighting is driven
  by `<body data-page="...">` (secondary screens map to their parent nav item via
  `NAV_PARENT`).
- **Dark/light mode:** toggle button in the topbar (and top-right of auth pages).
  Preference saved in `localStorage` (`sl-theme`); all colors flow through CSS
  variables in `:root` / `[data-theme="light"]`.
- **Cross-page toasts:** `flash(message, type, href)` queues a toast in
  `sessionStorage` and navigates; the destination page shows it on load.
- **Sidebar minimized state** persists across pages (`localStorage: sl-sidebar`).

## Running

Open `index.html` directly or serve with VS Code Live Server. No build step.

## Phase 4 migration notes

- `layout.js` shell → Jinja `base.html` with `{% block content %}`.
- Each `*.html` body `<section class="view-section">` → a page template extending base.
- `flash()` → Flask's `flash()` + message rendering in base template.
- Inline `onclick` navigation → real routes/form posts.
