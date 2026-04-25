async function toggleApplied(jobId, btn) {
  const res = await fetch(`/toggle/${jobId}`, { method: "POST" });
  const data = await res.json();
  const row = document.getElementById(`row-${jobId}`);
  if (data.applied) {
    row.classList.add("dim");
    btn.classList.replace("pending", "done");
    btn.textContent = "Applied ✓";
  } else {
    row.classList.remove("dim");
    btn.classList.replace("done", "pending");
    btn.textContent = "Mark applied";
  }
  updateStats();
}

async function setProgress(jobId, selectEl) {
  const value = selectEl.value;
  await fetch(`/set_progress/${jobId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ value }),
  });
  // Update colour
  selectEl.className = "progress-select " + value;
}

async function enrichRun(runId, force = false) {
  const btn = force
    ? document.getElementById("re-enrich-btn")
    : document.getElementById("enrich-btn");
  btn.disabled = true;
  btn.textContent = "⏳ Enriching…";
  const url = force ? `/enrich/${runId}?force=1` : `/enrich/${runId}`;
  try {
    const res = await fetch(url, { method: "POST" });
    const data = await res.json();
    if (data.count === -1) {
      btn.textContent = "⚠ Ollama not running";
      btn.disabled = false;
    } else if (data.count > 0) {
      location.reload();
    } else {
      btn.textContent = force ? "↺ Re-enrich" : "✓ All enriched";
      btn.disabled = false;
    }
  } catch (e) {
    btn.textContent = force ? "↺ Re-enrich" : "✨ Enrich";
    btn.disabled = false;
  }
}

function toggleFilter(th, e) {
  e.stopPropagation();
  const dd = th.querySelector(".filter-dropdown");
  document.querySelectorAll(".filter-dropdown.open").forEach((d) => {
    if (d !== dd) d.classList.remove("open");
  });
  dd.classList.toggle("open");
  if (dd.classList.contains("open")) {
    setTimeout(() => {
      const c = dd.querySelector("input,select");
      if (c) c.focus();
    }, 50);
  }
}

document.addEventListener("click", () => {
  document
    .querySelectorAll(".filter-dropdown.open")
    .forEach((d) => d.classList.remove("open"));
});

function filterTable() {
  const rows = document.querySelectorAll("#jobs-table tbody tr");
  const typeFilter = document.querySelector('[data-col="type"]');
  const colFilters = document.querySelectorAll(
    '[data-col]:not([data-col="type"])',
  );
  rows.forEach((row) => {
    let show = true;
    if (
      typeFilter &&
      typeFilter.value &&
      row.dataset.type !== typeFilter.value
    )
      show = false;
    colFilters.forEach((f) => {
      const val = f.value.toLowerCase().trim();
      if (!val) return;
      const cell = row.cells[parseInt(f.dataset.col)];
      if (cell && !cell.textContent.toLowerCase().includes(val))
        show = false;
    });
    row.style.display = show ? "" : "none";
  });
}

function updateStats() {
  const total = document.querySelectorAll("tbody tr").length;
  const dimmed = document.querySelectorAll("tbody tr.dim").length;
  const stats = document.querySelectorAll(".stat strong");
  if (stats.length >= 3) {
    stats[1].textContent = total - dimmed;
    stats[2].textContent = dimmed;
  }
}

// ── Home page column filter ────────────────────────────────
function filterHomeTable() {
  const rows = document.querySelectorAll("#home-table tbody tr");
  const colFilters = document.querySelectorAll("[data-home-col]");
  const progressFilter = document.querySelector("[data-home-progress]");
  rows.forEach(row => {
    let show = true;
    colFilters.forEach(f => {
      const val = f.value.toLowerCase().trim();
      if (!val) return;
      const cell = row.cells[parseInt(f.dataset.homeCol)];
      if (cell && !cell.textContent.toLowerCase().includes(val)) show = false;
    });
    if (progressFilter && progressFilter.value && row.dataset.progress !== progressFilter.value) show = false;
    row.style.display = show ? "" : "none";
  });
}

// ── Manual add modal ───────────────────────────────────────
function openAddModal() {
  document.getElementById("add-modal").classList.add("open");
  document.getElementById("m-title").focus();
}
function closeAddModal() {
  document.getElementById("add-modal").classList.remove("open");
  ["m-title","m-company","m-location","m-url"].forEach(id => document.getElementById(id).value = "");
}
async function submitManual() {
  const title   = document.getElementById("m-title").value.trim();
  const company = document.getElementById("m-company").value.trim();
  if (!title || !company) {
    alert("Title and Company are required.");
    return;
  }
  await fetch("/add_manual", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      title,
      company,
      location: document.getElementById("m-location").value.trim(),
      job_url:  document.getElementById("m-url").value.trim(),
    }),
  });
  location.reload();
}
document.addEventListener("keydown", e => {
  if (e.key === "Escape") closeAddModal();
});

// ── Favourite ─────────────────────────────────────────────
async function toggleFavorite(jobId, heartEl) {
  const res = await fetch(`/toggle_favorite/${jobId}`, { method: "POST" });
  const data = await res.json();
  const row = heartEl.closest("tr");
  if (data.favorite) {
    heartEl.classList.add("fav");
    row.dataset.favorite = "1";
  } else {
    heartEl.classList.remove("fav");
    row.dataset.favorite = "0";
    // hide row if fav filter is active
    const tableId = row.closest("table").id;
    if (activeFavFilter[tableId]) row.style.display = "none";
  }
}

const activeFavFilter = {};
function toggleFavFilter(tableId) {
  const rows = document.querySelectorAll(`#${tableId} tbody tr`);
  const btn  = document.getElementById(`fav-btn-${tableId}`);
  activeFavFilter[tableId] = !activeFavFilter[tableId];
  btn.classList.toggle("active-fav", activeFavFilter[tableId]);
  rows.forEach(row => {
    if (activeFavFilter[tableId]) {
      row.style.display = row.dataset.favorite === "1" ? "" : "none";
    } else {
      row.style.display = "";
    }
  });
}

// ── Home stat filter buttons ───────────────────────────────
let activeStatFilter = null;
function homeStatFilter(mode) {
  const rows = document.querySelectorAll("#home-table tbody tr");
  const statTotal = document.getElementById("stat-total");
  const statInprogress = document.getElementById("stat-inprogress");

  if (activeStatFilter === mode) {
    // Toggle off — show all
    activeStatFilter = null;
    statTotal.classList.remove("active-filter");
    statInprogress.classList.remove("active-filter");
    rows.forEach(r => r.style.display = "");
    return;
  }

  activeStatFilter = mode;
  statTotal.classList.toggle("active-filter", mode === "total");
  statInprogress.classList.toggle("active-filter", mode === "inprogress");

  rows.forEach(row => {
    const progress = row.dataset.progress || "";
    if (mode === "total") {
      row.style.display = "";
    } else if (mode === "inprogress") {
      const active = progress !== "" && progress !== "closed";
      row.style.display = active ? "" : "none";
    }
  });
}
