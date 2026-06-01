const state = {
  q: "",
  category: "",
  channel: "",
  tier: "",
  sort: "score",
  featured: false,
  hours: "168",
  limit: 40,
  offset: 0,
  page: 1,
  pages: 1,
  total: 0,
  nextOffset: null,
  prevOffset: null,
  nextPage: null,
  prevPage: null,
  mode: window.location.pathname.startsWith("/daily") ? "daily" : "signals",
  dailyDate: "",
  digestDates: [],
  theme: localStorage.getItem("comfyui-news-theme") || "system",
  read: new Set(JSON.parse(localStorage.getItem("comfyui-news-read") || "[]")),
  collapsedDays: new Set(JSON.parse(localStorage.getItem("comfyui-news-collapsed-days") || "[]")),
};

const els = {
  items: document.querySelector("#items"),
  clusters: document.querySelector("#clusters"),
  sourceWall: document.querySelector("#sourceWall"),
  dailyView: document.querySelector("#dailyView"),
  dailySections: document.querySelector("#dailySections"),
  dailyArchive: document.querySelector("#dailyArchive"),
  dailyDate: document.querySelector("#dailyDateSelect"),
  dailyMarkdown: document.querySelector("#dailyMarkdownLink"),
  dailyRss: document.querySelector("#dailyRssLink"),
  stats: document.querySelector("#stats"),
  runStatus: document.querySelector("#runStatus"),
  meta: document.querySelector("#feedMeta"),
  search: document.querySelector("#searchInput"),
  category: document.querySelector("#categorySelect"),
  channel: document.querySelector("#channelSelect"),
  tier: document.querySelector("#tierSelect"),
  sort: document.querySelector("#sortSelect"),
  theme: document.querySelector("#themeSelect"),
  featured: document.querySelector("#featuredOnly"),
  refresh: document.querySelector("#refreshBtn"),
  prevPage: document.querySelector("#prevPage"),
  nextPage: document.querySelector("#nextPage"),
  pageMeta: document.querySelector("#pageMeta"),
  rangeButtons: [...document.querySelectorAll(".segmented button")],
  sourceForm: document.querySelector("#sourceForm"),
  sourceName: document.querySelector("#sourceName"),
  sourceUrl: document.querySelector("#sourceUrl"),
  sourceReason: document.querySelector("#sourceReason"),
  sourceContact: document.querySelector("#sourceContact"),
  sourceFormStatus: document.querySelector("#sourceFormStatus"),
  feedbackForm: document.querySelector("#feedbackForm"),
  feedbackMessage: document.querySelector("#feedbackMessage"),
  feedbackContact: document.querySelector("#feedbackContact"),
  feedbackStatus: document.querySelector("#feedbackStatus"),
  clearFilters: document.querySelector("#clearFilters"),
  quickChannels: [...document.querySelectorAll(".quick-channels button")],
};

const fmt = new Intl.DateTimeFormat("zh-CN", {
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
});

const rtf = new Intl.RelativeTimeFormat("zh-CN", { numeric: "auto" });

function debounce(fn, delay = 250) {
  let timer = 0;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), delay);
  };
}

function buildParams() {
  const params = new URLSearchParams({ limit: String(state.limit), page: String(state.page) });
  if (state.q) params.set("q", state.q);
  if (state.category) params.set("category", state.category);
  if (state.channel) params.set("channel", state.channel);
  if (state.tier) params.set("tier", state.tier);
  if (state.featured) params.set("featured", "true");
  if (state.hours) params.set("hours", state.hours);
  if (state.sort) params.set("sort", state.sort);
  return params;
}

async function loadItems() {
  els.meta.textContent = "Loading";
  const [itemsRes, clustersRes, wallRes] = await Promise.all([
    fetch(`/api/items?${buildParams()}`),
    fetch(`/api/clusters?${buildParams()}`),
    fetch("/api/source-wall"),
  ]);
  const data = await itemsRes.json();
  const clusterData = await clustersRes.json();
  const wallData = await wallRes.json();
  state.total = data.total || 0;
  state.offset = data.offset || 0;
  state.page = data.page || 1;
  state.pages = data.pages || 1;
  state.nextOffset = data.next_offset;
  state.prevOffset = data.prev_offset;
  state.nextPage = data.next_page;
  state.prevPage = data.prev_page;
  syncUrl();
  renderSourceWall(wallData);
  renderClusters(clusterData.clusters || []);
  renderItems(data.items || []);
  renderPagination();
}

async function loadStats() {
  const res = await fetch("/api/stats");
  const data = await res.json();
  renderStats(data);
}

async function loadDaily() {
  els.meta.textContent = "Loading daily digest";
  const [datesRes, digestRes, wallRes] = await Promise.all([
    fetch("/api/daily/dates?limit=60"),
    fetch(`/api/digest?limit=80${state.dailyDate ? `&day=${encodeURIComponent(state.dailyDate)}` : ""}`),
    fetch("/api/source-wall"),
  ]);
  const datesData = await datesRes.json();
  const digestData = await digestRes.json();
  const wallData = await wallRes.json();
  state.digestDates = datesData.dates || [];
  state.dailyDate = digestData.date || state.dailyDate;
  const archiveData = await fetch("/api/daily/archive?limit=14").then((res) => res.json());
  renderSourceWall(wallData);
  renderDailyDates();
  renderDailyArchive(archiveData.days || []);
  renderDailyDigest(digestData);
  syncUrl();
}

function renderStats(data) {
  els.stats.innerHTML = "";
  const entries = [
    ["Total", data.total ?? 0],
    ["Featured", data.featured ?? 0],
    ["Sources", data.configured_sources?.length ?? 0],
    ["T1", data.tiers?.T1 ?? 0],
  ];
  for (const [label, value] of entries) {
    const node = document.createElement("div");
    node.className = "stat";
    node.innerHTML = `<b>${value}</b><span>${label}</span>`;
    els.stats.appendChild(node);
  }
  renderRunStatus(data.last_collect_result);
}

function renderRunStatus(run) {
  if (!run) {
    els.runStatus.textContent = "No refresh has been recorded yet.";
    return;
  }
  const finished = run.finished_at ? relativeTime(new Date(run.finished_at)) : "unknown";
  const status = run.failed_sources > 0 ? "partial" : "ok";
  const skipped = run.skipped_sources ?? (run.source_results || []).filter((item) => item.status === "skipped").length;
  els.runStatus.innerHTML = `
    <div class="status-line ${status}">
      <span>${status === "ok" ? "OK" : "PARTIAL"}</span>
      <strong>${run.succeeded_sources}/${run.sources}</strong> sources, ${run.fetched} fetched${skipped ? `, ${skipped} skipped` : ""}
    </div>
    <div class="status-note">${run.inserted ?? 0} new, ${run.updated ?? 0} updated, ${run.unchanged ?? 0} unchanged</div>
    <div class="status-note">Last refresh ${finished}</div>
    ${renderSourceProblems(run)}
    ${renderSkippedSources(run)}
  `;
}

function renderSourceProblems(run) {
  const failed = (run.source_results || []).filter((item) => !item.ok && item.status !== "skipped");
  if (!failed.length) return "";
  return `
    <details class="source-problems">
      <summary>${failed.length} source problem${failed.length > 1 ? "s" : ""}</summary>
      ${failed.map((item) => `<p>${escapeHtml(item.name)}: ${escapeHtml(item.error || "unknown error")}</p>`).join("")}
    </details>
  `;
}

function renderSkippedSources(run) {
  const skipped = (run.source_results || []).filter((item) => item.status === "skipped");
  if (!skipped.length) return "";
  return `
    <details class="source-problems skipped-sources">
      <summary>${skipped.length} skipped source${skipped.length > 1 ? "s" : ""}</summary>
      ${skipped.map((item) => `<p>${escapeHtml(item.name)}: ${escapeHtml(item.reason || "skipped")}</p>`).join("")}
    </details>
  `;
}

function renderItems(items) {
  els.items.innerHTML = "";
  const shownStart = state.total ? state.offset + 1 : 0;
  const shownEnd = Math.min(state.offset + items.length, state.total);
  els.meta.textContent = `${shownStart}-${shownEnd} of ${state.total} signals`;
  if (!items.length) {
    els.items.innerHTML = '<div class="empty">No matching data. Click Refresh to fetch the latest ComfyUI signals.</div>';
    return;
  }
  const byDay = groupByDay(items);
  for (const [day, dayItems] of byDay) {
    const section = document.createElement("section");
    section.className = "day-group";
    section.dataset.day = day;
    if (state.collapsedDays.has(day)) section.classList.add("collapsed");
    section.innerHTML = `
      <button class="day-head" type="button" aria-expanded="${state.collapsedDays.has(day) ? "false" : "true"}">
        <h3>${escapeHtml(formatDay(day))}</h3>
        <span>${dayItems.length} signal${dayItems.length > 1 ? "s" : ""}</span>
      </button>
    `;
    section.querySelector(".day-head").addEventListener("click", () => toggleDay(day, section));
    for (const item of dayItems) {
      section.appendChild(renderItem(item));
    }
    els.items.appendChild(section);
  }
}

function renderPagination() {
  els.pageMeta.textContent = `Page ${state.page} / ${state.pages}`;
  els.prevPage.disabled = state.prevPage === null;
  els.nextPage.disabled = state.nextPage === null;
}

function setModeView() {
  const isDaily = state.mode === "daily";
  els.dailyView.hidden = !isDaily;
  els.items.hidden = isDaily;
  els.clusters.hidden = isDaily;
  document.querySelector(".pagination").hidden = isDaily;
  document.querySelector(".segmented").hidden = isDaily;
  document.querySelector(".quick-channels").hidden = isDaily;
}

function renderDailyDates() {
  els.dailyDate.innerHTML = "";
  const dates = state.digestDates.includes(state.dailyDate)
    ? state.digestDates
    : [state.dailyDate, ...state.digestDates].filter(Boolean);
  for (const day of dates) {
    const option = document.createElement("option");
    option.value = day;
    option.textContent = day;
    option.selected = day === state.dailyDate;
    els.dailyDate.appendChild(option);
  }
}

function renderDailyDigest(data) {
  const sectionDefs = [
    ["video_image_models", "Image / Video Models"],
    ["official", "Official / Primary"],
    ["releases", "Releases"],
    ["custom_nodes_workflows", "Custom Nodes / Workflows"],
    ["models", "Models"],
    ["community", "Community"],
  ];
  els.meta.textContent = `${data.date} daily digest / ${data.total} signals`;
  els.dailyMarkdown.href = `/api/export/markdown?day=${encodeURIComponent(data.date)}`;
  els.dailyRss.href = "/rss/daily.xml";
  els.dailySections.innerHTML = `
    <article class="daily-summary">
      <h3>${escapeHtml(data.date)} ComfyUI Daily Digest</h3>
      <p>${escapeHtml(digestCategorySummary(data.categories || {}))}</p>
    </article>
  `;
  const seen = new Set();
  for (const [key, label] of sectionDefs) {
    const items = (data.sections?.[key] || []).filter((item) => {
      if (seen.has(item.guid)) return false;
      seen.add(item.guid);
      return true;
    });
    if (!items.length) continue;
    const section = document.createElement("section");
    section.className = "digest-section";
    section.innerHTML = `<h3>${escapeHtml(label)}</h3>`;
    for (const item of items) {
      section.appendChild(renderItem(item));
    }
    els.dailySections.appendChild(section);
  }
  if (els.dailySections.children.length === 1) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "No daily digest items for this date.";
    els.dailySections.appendChild(empty);
  }
}

function renderDailyArchive(days) {
  els.dailyArchive.innerHTML = "";
  if (!days.length) return;
  els.dailyArchive.innerHTML = `
    <div class="daily-archive-head">
      <h3>Recent digests</h3>
      <a href="/api/public/daily/archive?take=30">JSON</a>
    </div>
    <div class="daily-archive-list">
      ${days
        .map((day) => {
          const active = day.date === state.dailyDate ? "active" : "";
          const topTitle = day.top_item?.title || "No top item";
          return `
            <a class="archive-day ${active}" href="/daily/${encodeURIComponent(day.date)}">
              <span>${escapeHtml(day.date)}</span>
              <b>${day.total}</b>
              <small>${day.featured} featured / top ${day.top_score}</small>
              <em>${escapeHtml(topTitle)}</em>
            </a>
          `;
        })
        .join("")}
    </div>
  `;
}

function digestCategorySummary(categories) {
  const entries = Object.entries(categories);
  if (!entries.length) return "No category summary for this date.";
  return entries.map(([key, value]) => `${key}: ${value}`).join(" / ");
}

function renderItem(item) {
  const node = document.createElement("article");
  const isRead = state.read.has(item.guid);
  node.className = `item ${item.featured ? "featured" : ""} ${isRead ? "read" : ""}`;
  const tags = (item.tags || []).map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("");
  const date = item.published_at ? fmt.format(new Date(item.published_at)) : "";
  const reason = item.reason ? `<p class="reason">${escapeHtml(item.reason)}</p>` : "";
  const breakdown = renderBreakdown(item.score_breakdown || {});
  const sourceType = readableSourceType(item.source_type);
  const scoreLabel = item.score >= 85 ? "High" : item.score >= 65 ? "Solid" : "Scan";
  node.innerHTML = `
    <div class="item-top">
      <div>
        <div class="badges">
          <span class="tier">${escapeHtml(item.source_tier || "T2")}</span>
          <span class="source-chip">${escapeHtml(sourceType)}</span>
          ${item.featured ? '<span class="featured-badge">Featured</span>' : ""}
          ${isRead ? '<span class="read-badge">Read</span>' : ""}
        </div>
        <h3><a href="${escapeAttr(item.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.title)}</a></h3>
      </div>
      <div class="score" title="Signal score"><b>${item.score}</b><span>${scoreLabel}</span></div>
    </div>
    ${reason}
    <p class="summary">${escapeHtml(item.summary || "No summary")}</p>
    ${breakdown}
    <div class="meta">
      <span>${escapeHtml(item.source_name)}</span>
      <span>${escapeHtml(item.category)}</span>
      <span>${date}</span>
      <button class="mark-read" type="button">${isRead ? "Unread" : "Mark read"}</button>
    </div>
    <div class="tags">${tags}</div>
  `;
  node.querySelector("a").addEventListener("click", () => markRead(item.guid));
  node.querySelector(".mark-read").addEventListener("click", () => {
    toggleRead(item.guid);
    loadItems();
  });
  return node;
}

function groupByDay(items) {
  const groups = new Map();
  for (const item of items) {
    const day = item.published_at ? item.published_at.slice(0, 10) : "unknown";
    if (!groups.has(day)) groups.set(day, []);
    groups.get(day).push(item);
  }
  return [...groups.entries()];
}

function formatDay(day) {
  if (day === "unknown") return "Unknown date";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    weekday: "short",
  }).format(new Date(`${day}T00:00:00`));
}

function markRead(guid) {
  state.read.add(guid);
  persistRead();
}

function toggleRead(guid) {
  if (state.read.has(guid)) {
    state.read.delete(guid);
  } else {
    state.read.add(guid);
  }
  persistRead();
}

function persistRead() {
  localStorage.setItem("comfyui-news-read", JSON.stringify([...state.read].slice(-1000)));
}

function toggleDay(day, section) {
  if (state.collapsedDays.has(day)) {
    state.collapsedDays.delete(day);
    section.classList.remove("collapsed");
    section.querySelector(".day-head").setAttribute("aria-expanded", "true");
  } else {
    state.collapsedDays.add(day);
    section.classList.add("collapsed");
    section.querySelector(".day-head").setAttribute("aria-expanded", "false");
  }
  localStorage.setItem("comfyui-news-collapsed-days", JSON.stringify([...state.collapsedDays].slice(-90)));
}

function resetPaging() {
  state.offset = 0;
  state.page = 1;
}

function applyTheme() {
  document.documentElement.dataset.theme = state.theme;
  els.theme.value = state.theme;
  localStorage.setItem("comfyui-news-theme", state.theme);
}

function syncControls() {
  els.search.value = state.q;
  els.category.value = state.category;
  els.channel.value = state.channel;
  els.tier.value = state.tier;
  els.sort.value = state.sort;
  els.featured.checked = state.featured;
  els.rangeButtons.forEach((button) => {
    button.classList.toggle("active", (button.dataset.hours || "") === state.hours);
  });
  els.quickChannels.forEach((button) => {
    button.classList.toggle("active", (button.dataset.channel || "") === state.channel);
  });
}

function readUrlState() {
  const params = new URLSearchParams(window.location.search);
  const pathParts = window.location.pathname.split("/").filter(Boolean);
  if (pathParts[0] === "daily") {
    state.mode = "daily";
    state.dailyDate = pathParts[1] || params.get("day") || "";
  }
  state.q = params.get("q") || "";
  state.category = params.get("category") || "";
  state.channel = params.get("channel") || "";
  state.tier = params.get("tier") || "";
  state.sort = params.get("sort") || "score";
  state.featured = params.has("featured") ? params.get("featured") === "true" : false;
  state.hours = params.has("hours") ? params.get("hours") || "" : "168";
  state.page = Math.max(1, Number.parseInt(params.get("page") || "1", 10) || 1);
  syncControls();
}

function syncUrl() {
  const params = new URLSearchParams();
  if (state.mode === "daily") {
    const nextPath = state.dailyDate ? `/daily/${encodeURIComponent(state.dailyDate)}` : "/daily";
    window.history.replaceState(null, "", nextPath);
    return;
  }
  if (state.q) params.set("q", state.q);
  if (state.category) params.set("category", state.category);
  if (state.channel) params.set("channel", state.channel);
  if (state.tier) params.set("tier", state.tier);
  if (state.sort !== "score") params.set("sort", state.sort);
  if (state.featured) params.set("featured", "true");
  if (state.hours) params.set("hours", state.hours);
  if (state.page > 1) params.set("page", String(state.page));
  const query = params.toString();
  const nextUrl = query ? `${window.location.pathname}?${query}` : window.location.pathname;
  window.history.replaceState(null, "", nextUrl);
}

function renderSourceWall(data) {
  const sources = (data.sources || []).slice(0, 8);
  if (!sources.length) {
    els.sourceWall.innerHTML = "";
    return;
  }
  els.sourceWall.innerHTML = `
    <div class="source-wall-head">
      <div>
        <h3>Source wall</h3>
        <p>${data.configured || 0} active, ${data.pending_submissions || 0} pending suggestions</p>
      </div>
      <a href="/api/source-wall">JSON</a>
    </div>
    <div class="source-wall-list">
      ${sources
        .map(
          (source) => `
            <a href="${escapeAttr(source.url)}" target="_blank" rel="noopener noreferrer" class="source-tile">
              <span>${escapeHtml(source.number)}</span>
              <b>${escapeHtml(source.name)}</b>
              <small>${escapeHtml(source.tier)} / ${escapeHtml(source.category)}</small>
            </a>
          `,
        )
        .join("")}
    </div>
  `;
}

function renderClusters(clusters) {
  els.clusters.innerHTML = "";
  if (!clusters.length) return;
  const top = clusters.slice(0, 6);
  for (const cluster of top) {
    const node = document.createElement("button");
    node.type = "button";
    node.className = "cluster";
    node.title = "Event cluster";
    node.innerHTML = `
      <span>${escapeHtml(cluster.cluster_title || cluster.cluster_key)}</span>
      <b>${cluster.max_score}</b>
      <small>${cluster.item_count} item${cluster.item_count > 1 ? "s" : ""}</small>
    `;
    els.clusters.appendChild(node);
  }
}

function renderBreakdown(breakdown) {
  const entries = Object.entries(breakdown).filter(([, value]) => value !== 0);
  if (!entries.length) return "";
  return `
    <div class="breakdown">
      ${entries.map(([key, value]) => `<span>${escapeHtml(key)} ${value > 0 ? "+" : ""}${value}</span>`).join("")}
    </div>
  `;
}

function readableSourceType(sourceType) {
  const labels = {
    rss: "RSS",
    x_search: "X",
    bilibili_search: "Bilibili",
    youtube_search: "YouTube",
    huggingface_models: "HF",
    civitai_models: "Civitai",
    discord_feed: "Discord",
    forum_json: "Forum",
    json_feed: "JSON",
    github_releases: "Release",
    github_commits: "Commit",
    github_search_repos: "Repo",
    github_issues: "Issue",
  };
  return labels[sourceType] || sourceType || "Source";
}

async function refresh() {
  els.refresh.disabled = true;
  els.refresh.textContent = "Refreshing";
  try {
    await fetch("/api/refresh?wait=true", { method: "POST" });
    await Promise.all([loadItems(), loadStats()]);
  } finally {
    els.refresh.disabled = false;
    els.refresh.textContent = "Refresh";
  }
}

async function submitJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || "Request failed");
  }
  return data;
}

function relativeTime(date) {
  const seconds = Math.round((date.getTime() - Date.now()) / 1000);
  const abs = Math.abs(seconds);
  if (abs < 60) return rtf.format(seconds, "second");
  if (abs < 3600) return rtf.format(Math.round(seconds / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(seconds / 3600), "hour");
  return rtf.format(Math.round(seconds / 86400), "day");
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttr(value) {
  return escapeHtml(value).replaceAll("`", "&#096;");
}

els.search.addEventListener(
  "input",
  debounce((event) => {
    state.q = event.target.value.trim();
    resetPaging();
    loadItems();
  }),
);

els.category.addEventListener("change", (event) => {
  state.category = event.target.value;
  resetPaging();
  loadItems();
});

els.channel.addEventListener("change", (event) => {
  state.channel = event.target.value;
  resetPaging();
  syncControls();
  loadItems();
});

els.tier.addEventListener("change", (event) => {
  state.tier = event.target.value;
  resetPaging();
  loadItems();
});

els.sort.addEventListener("change", (event) => {
  state.sort = event.target.value;
  resetPaging();
  loadItems();
});

els.featured.addEventListener("change", (event) => {
  state.featured = event.target.checked;
  resetPaging();
  loadItems();
});

els.theme.addEventListener("change", (event) => {
  state.theme = event.target.value;
  applyTheme();
});

els.rangeButtons.forEach((button) => {
  button.addEventListener("click", () => {
    state.hours = button.dataset.hours || "";
    resetPaging();
    syncControls();
    loadItems();
  });
});

els.quickChannels.forEach((button) => {
  button.addEventListener("click", () => {
    state.channel = button.dataset.channel || "";
    resetPaging();
    syncControls();
    loadItems();
  });
});

els.clearFilters.addEventListener("click", () => {
  state.q = "";
  state.category = "";
  state.channel = "";
  state.tier = "";
  state.sort = "score";
  state.featured = false;
  state.hours = "168";
  resetPaging();
  syncControls();
  loadItems();
});

els.refresh.addEventListener("click", refresh);

els.dailyDate.addEventListener("change", (event) => {
  state.dailyDate = event.target.value;
  loadDaily();
});

els.dailyArchive.addEventListener("click", (event) => {
  const link = event.target.closest("a.archive-day");
  if (!link) return;
  event.preventDefault();
  const parts = link.getAttribute("href").split("/");
  state.dailyDate = decodeURIComponent(parts[parts.length - 1] || "");
  loadDaily();
});

els.prevPage.addEventListener("click", () => {
  if (state.prevPage === null) return;
  state.page = state.prevPage;
  loadItems();
});

els.nextPage.addEventListener("click", () => {
  if (state.nextPage === null) return;
  state.page = state.nextPage;
  loadItems();
});

els.sourceForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  els.sourceFormStatus.textContent = "Submitting";
  try {
    const data = await submitJson("/api/source-submissions", {
      name: els.sourceName.value.trim(),
      url: els.sourceUrl.value.trim(),
      reason: els.sourceReason.value.trim(),
      contact: els.sourceContact.value.trim(),
    });
    els.sourceForm.reset();
    els.sourceFormStatus.textContent = data.submission?.duplicate
      ? "Already suggested; keeping the existing record."
      : "Source suggestion saved.";
    loadItems();
  } catch (error) {
    els.sourceFormStatus.textContent = error.message;
  }
});

els.feedbackForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  els.feedbackStatus.textContent = "Sending";
  try {
    await submitJson("/api/feedback", {
      message: els.feedbackMessage.value.trim(),
      contact: els.feedbackContact.value.trim(),
    });
    els.feedbackForm.reset();
    els.feedbackStatus.textContent = "Feedback saved.";
  } catch (error) {
    els.feedbackStatus.textContent = error.message;
  }
});

readUrlState();
applyTheme();
setModeView();
Promise.all([state.mode === "daily" ? loadDaily() : loadItems(), loadStats()]);
