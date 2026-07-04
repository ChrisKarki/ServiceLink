/* ===========================================================
   ServiceLink — Ticket List  |  tickets.js
   Domain values from RAD FR-2.2: six states, four priorities.
   In-memory data — replace TICKETS with fetch('/api/tickets')
   in Phase 4 implementation.
   =========================================================== */
"use strict";
//
var STATES     = ["New","Assigned","In Progress","Waiting on User","Resolved","Closed"];
var UNRESOLVED = ["New","Assigned","In Progress","Waiting on User"];
var PRIORITY_RANK = { Critical:0, High:1, Medium:2, Low:3 };
var PAGE_SIZE  = 8;

var TICKETS = [
  { id:"INC-1042", subject:"VPN drops every few minutes on remote desktop",   requester:"Dana Whitfield",  priority:"High",     state:"In Progress",     sla:"respond",  agent:"P. Mehta",   created:2  },
  { id:"SR-1041",  subject:"New hire laptop + monitor for onboarding",         requester:"Marcus Bell",     priority:"Medium",   state:"Assigned",        sla:"none",     agent:"L. Tran",    created:1  },
  { id:"INC-1039", subject:"Shared drive permissions reset after migration",   requester:"Priya Nair",      priority:"Critical", state:"New",             sla:"overdue",  agent:"unassigned", created:0  },
  { id:"SR-1037",  subject:"Request access to reporting dashboard",            requester:"Tobias Lund",     priority:"Low",      state:"Waiting on User", sla:"none",     agent:"me",         created:3  },
  { id:"INC-1036", subject:"Email signature not applying for whole team",      requester:"Hannah Cole",     priority:"Medium",   state:"In Progress",     sla:"respond",  agent:"S. Okeke",   created:4  },
  { id:"SR-1034",  subject:"Provision Proxmox VM for QA test bench",          requester:"Wei Zhang",       priority:"High",     state:"Assigned",        sla:"none",     agent:"me",         created:5  },
  { id:"INC-1033", subject:"Printer on 3rd floor showing offline",            requester:"Olivia Grant",    priority:"Low",      state:"New",             sla:"new",      agent:"unassigned", created:1  },
  { id:"INC-1031", subject:"MFA prompt looping on company portal",            requester:"Samuel Reyes",    priority:"Critical", state:"In Progress",     sla:"overdue",  agent:"P. Mehta",   created:0  },
  { id:"SR-1029",  subject:"Software install: screen recording tool",         requester:"Aisha Karim",     priority:"Low",      state:"Resolved",        sla:"resolved", agent:"L. Tran",    created:7  },
  { id:"INC-1028", subject:"Account locked after password change",            requester:"Diego Santos",    priority:"Medium",   state:"Waiting on User", sla:"none",     agent:"S. Okeke",   created:6  },
  { id:"SR-1026",  subject:"VLAN segment request for guest network",          requester:"Fatima Noor",     priority:"High",     state:"New",             sla:"new",      agent:"unassigned", created:2  },
  { id:"INC-1024", subject:"Outlook calendar not syncing on mobile",          requester:"Greg Holloway",   priority:"Low",      state:"Closed",          sla:"resolved", agent:"me",         created:14 }
];

/* ── avatar helpers ────────────────────────────────────────── */
var AV_COLORS = ["#2563EB","#0D9488","#D97706","#7C3AED","#DB2777","#059669","#DC2626"];
function avatarColor(name) {
  var h = 0;
  for (var i = 0; i < name.length; i++) h = name.charCodeAt(i) + ((h << 5) - h);
  return AV_COLORS[Math.abs(h) % AV_COLORS.length];
}
function initials(name) {
  var p = name.trim().split(/\s+/);
  return (p[0][0] + (p[1] ? p[1][0] : "")).toUpperCase();
}

/* ── SLA badge ─────────────────────────────────────────────── */
function slaBadge(sla) {
  switch (sla) {
    case "new":      return '<span class="badge badge-new">New</span>';
    case "respond":  return '<span class="badge badge-high">Response due</span>';
    case "overdue":  return '<span class="badge badge-breach">⚠ Overdue</span>';
    case "resolved": return '<span class="badge badge-resolved">Resolved</span>';
    default:         return '<span style="color:#94A3B8">—</span>';
  }
}

/* ── status dropdown ───────────────────────────────────────── */
function statusOptions(current) {
  return STATES.map(function(s) {
    return '<option value="' + s + '"' + (s === current ? ' selected' : '') + '>' + s + '</option>';
  }).join('');
}

function esc(s) {
  return String(s).replace(/[&<>"]/g, function(c) {
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];
  });
}

/* ── row HTML ──────────────────────────────────────────────── */
function rowHTML(t) {
  var req = esc(t.requester);
  return (
    '<tr data-id="' + esc(t.id) + '">' +
      '<td><span class="ticket-id">' + esc(t.id) + '</span></td>' +
      '<td><span class="ticket-subj">' + esc(t.subject) + '</span></td>' +
      '<td><span class="requester-cell">' +
        '<span class="req-avatar" style="background:' + avatarColor(req) + '">' + initials(req) + '</span>' + req +
      '</span></td>' +
      '<td><span class="priority-cell pri-' + esc(t.priority) + '"><span class="pri-dot"></span>' + esc(t.priority) + '</span></td>' +
      '<td>' + slaBadge(t.sla) + '</td>' +
      '<td><select class="status-select" aria-label="Status for ' + esc(t.id) + '">' + statusOptions(t.state) + '</select></td>' +
    '</tr>'
  );
}

/* ── view state ────────────────────────────────────────────── */
var sortKey = "created", sortAsc = false, searchTerm = "", page = 0;

function readFilters() {
  return {
    state:    document.getElementById("fState").value,
    priority: document.getElementById("fPriority").value,
    agent:    document.getElementById("fAgent").value
  };
}

function filterSort() {
  var f = readFilters();
  var rows = TICKETS.filter(function(t) {
    if (f.state === "all") { if (UNRESOLVED.indexOf(t.state) === -1) return false; }
    else if (t.state !== f.state) return false;
    if (f.priority !== "all" && t.priority !== f.priority) return false;
    if (f.agent === "me"         && t.agent !== "me")         return false;
    if (f.agent === "unassigned" && t.agent !== "unassigned") return false;
    if (f.agent !== "all" && f.agent !== "me" && f.agent !== "unassigned" && t.agent !== f.agent) return false;
    if (searchTerm) {
      var hay = (t.subject + " " + t.id + " " + t.requester).toLowerCase();
      if (hay.indexOf(searchTerm) === -1) return false;
    }
    return true;
  });
  rows.sort(function(a, b) {
    var x, y;
    if      (sortKey === "priority")  { x = PRIORITY_RANK[a.priority]; y = PRIORITY_RANK[b.priority]; }
    else if (sortKey === "created")   { x = a.created;  y = b.created; }
    else if (sortKey === "status")    { x = STATES.indexOf(a.state); y = STATES.indexOf(b.state); }
    else if (sortKey === "sla")       { x = a.sla; y = b.sla; }
    else if (sortKey === "requester") { x = a.requester.toLowerCase(); y = b.requester.toLowerCase(); }
    else if (sortKey === "id")        { x = a.id; y = b.id; }
    else                              { x = a.subject.toLowerCase(); y = b.subject.toLowerCase(); }
    if (x < y) return sortAsc ? -1 : 1;
    if (x > y) return sortAsc ?  1 : -1;
    return 0;
  });
  return rows;
}

/* ── render ────────────────────────────────────────────────── */
function render() {
  var rows    = filterSort();
  var total   = rows.length;
  var maxPage = Math.max(0, Math.ceil(total / PAGE_SIZE) - 1);
  if (page > maxPage) page = maxPage;
  var start = page * PAGE_SIZE;
  var slice = rows.slice(start, start + PAGE_SIZE);

  document.getElementById("ticketBody").innerHTML = slice.map(rowHTML).join("");
  document.getElementById("emptyState").hidden = total > 0;

  var from = total === 0 ? 0 : start + 1;
  var to   = Math.min(start + PAGE_SIZE, total);
  document.getElementById("pagerCount").textContent =
    "Showing " + from + "–" + to + " of " + total + " tickets";

  document.getElementById("prevPage").disabled = page <= 0;
  document.getElementById("nextPage").disabled = page >= maxPage;

  var badge = document.getElementById("openBadge");
  var n = TICKETS.filter(function(t) { return UNRESOLVED.indexOf(t.state) !== -1; }).length;
  if (badge) badge.textContent = n > 0 ? n : "";

  wireRowEvents();
}

function wireRowEvents() {
  document.querySelectorAll(".status-select").forEach(function(sel) {
    sel.addEventListener("change", function() {
      var id = this.closest("tr").getAttribute("data-id");
      var t  = TICKETS.find(function(x) { return x.id === id; });
      if (t) {
        t.state = this.value;
        if (t.state === "Resolved" || t.state === "Closed") t.sla = "resolved";
        render();
      }
    });
  });
}

function wireSortHeaders() {
  document.querySelectorAll("th[data-sort]").forEach(function(th) {
    th.addEventListener("click", function() {
      var key = this.getAttribute("data-sort");
      if (sortKey === key) { sortAsc = !sortAsc; }
      else { sortKey = key; sortAsc = (key === "subject" || key === "requester" || key === "id"); }
      document.querySelectorAll("th[data-sort]").forEach(function(h) { h.classList.remove("is-sorted","asc"); });
      this.classList.add("is-sorted");
      if (sortAsc) this.classList.add("asc");
      page = 0; render();
    });
  });
}

document.addEventListener("DOMContentLoaded", function() {
  wireSortHeaders();

  document.getElementById("ticketSearch").addEventListener("input", function() {
    searchTerm = this.value.trim().toLowerCase(); page = 0; render();
  });
  document.getElementById("applyFilters").addEventListener("click", function() { page = 0; render(); });
  document.getElementById("resetFilters").addEventListener("click", function() {
    document.getElementById("fState").value    = "all";
    document.getElementById("fPriority").value = "all";
    document.getElementById("fAgent").value    = "all";
    searchTerm = ""; document.getElementById("ticketSearch").value = "";
    page = 0; render();
  });
  document.getElementById("clearFromEmpty").addEventListener("click", function() {
    document.getElementById("resetFilters").click();
  });
  document.getElementById("prevPage").addEventListener("click", function() { if (page > 0) { page--; render(); } });
  document.getElementById("nextPage").addEventListener("click", function() { page++; render(); });

  render();
});
