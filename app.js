const categories = [
  { id: "all", label: "全部" }, { id: "专业学习", label: "专业学习" },
  { id: "校园机会", label: "校园机会" }, { id: "竞赛科研", label: "竞赛科研" },
  { id: "升学就业", label: "专业就业" }, { id: "政策行业", label: "政策行业" },
  { id: "科技前沿", label: "科技前沿" }, { id: "四川热点", label: "四川热点" }
];
const hostedMode = location.protocol === "https:" || location.protocol === "file:" || location.hostname.endsWith(".github.io");

const state = {
  items: [], category: "all", query: "", sort: "relevance", visibleCount: 10,
  importantOnly: localStorage.getItem("important-only") === "true",
  hideRead: localStorage.getItem("hide-read") === "true",
  readIds: new Set(JSON.parse(localStorage.getItem("read-ids") || "[]")),
  savedIds: new Set(JSON.parse(localStorage.getItem("saved-ids") || "[]")),
  refreshInterval: Number(localStorage.getItem("refresh-interval") || 60000), timer: null
};

const elements = {
  tabs: document.querySelector("#category-tabs"), briefing: document.querySelector("#briefing-list"),
  news: document.querySelector("#news-list"), search: document.querySelector("#search-input"),
  sort: document.querySelector("#sort-select"), loadMore: document.querySelector("#load-more"),
  refresh: document.querySelector("#refresh-button"), liveState: document.querySelector("#live-state"),
  lastUpdated: document.querySelector("#last-updated"), heroTotal: document.querySelector("#hero-total"),
  heroImportant: document.querySelector("#hero-important"), heroSources: document.querySelector("#hero-sources"),
  settingsButton: document.querySelector("#settings-button"), settingsDialog: document.querySelector("#settings-dialog"),
  importantOnly: document.querySelector("#important-only"), hideRead: document.querySelector("#hide-read"),
  refreshInterval: document.querySelector("#refresh-interval"), toast: document.querySelector("#toast")
};

function escapeHtml(value = "") {
  return value.replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" })[char]);
}

function relativeTime(iso) {
  if (!iso) return "时间待确认";
  const date = new Date(iso);
  const minutes = Math.max(0, Math.floor((Date.now() - date.getTime()) / 60000));
  if (minutes < 2) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)} 小时前`;
  return `${Math.floor(minutes / 1440)} 天前`;
}

function showToast(message) {
  elements.toast.textContent = message;
  elements.toast.classList.add("is-visible");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => elements.toast.classList.remove("is-visible"), 2200);
}

function renderTabs() {
  elements.tabs.innerHTML = categories.map((category) => `
    <button class="category-tab ${state.category === category.id ? "is-active" : ""}" type="button" role="tab"
      aria-selected="${state.category === category.id}" data-category="${escapeHtml(category.id)}">${escapeHtml(category.label)}</button>
  `).join("");
}

function filteredItems() {
  const query = state.query.trim().toLowerCase();
  const filtered = state.items
    .filter((item) => state.category === "all" || item.category === state.category)
    .filter((item) => !state.importantOnly || item.important)
    .filter((item) => !state.hideRead || !state.readIds.has(item.id))
    .filter((item) => !query || [item.title, item.summary, item.source, item.category].join(" ").toLowerCase().includes(query));
  const sorted = [...filtered].sort((a, b) => {
    if (state.sort === "newest") return new Date(b.published_at) - new Date(a.published_at);
    if (state.sort === "source") return String(b.source_type).localeCompare(String(a.source_type));
    return (b.score || 0) - (a.score || 0);
  });
  if (state.sort !== "relevance" || state.category !== "all") return sorted;
  const grouped = new Map();
  sorted.forEach((item) => {
    if (!grouped.has(item.category)) grouped.set(item.category, []);
    grouped.get(item.category).push(item);
  });
  const orderedGroups = [...grouped.values()].sort((a, b) => (b[0].score || 0) - (a[0].score || 0));
  const result = [];
  let added = true;
  while (added) {
    added = false;
    orderedGroups.forEach((group) => {
      const item = group.shift();
      if (item && result.length < sorted.length) {
        result.push(item);
        added = true;
      }
    });
  }
  return result;
}

function itemMeta(item) {
  return `<div class="meta-line"><span class="category-label">${escapeHtml(item.category)}</span><span>${escapeHtml(item.source)}</span><span>·</span><time datetime="${escapeHtml(item.published_at || "")}">${escapeHtml(item.time_label || relativeTime(item.published_at))}</time>${item.important ? '<span class="priority-label">● 值得关注</span>' : ""}</div>`;
}

function bookmarkButton(item) {
  const saved = state.savedIds.has(item.id);
  return `<button class="bookmark-button ${saved ? "is-saved" : ""}" type="button" data-save-id="${escapeHtml(item.id)}" aria-label="${saved ? "取消收藏" : "收藏"}" title="${saved ? "取消收藏" : "收藏"}">${saved ? "★" : "☆"}</button>`;
}

function renderBriefing(items) {
  const topItems = items.slice(0, 4);
  if (!topItems.length) {
    elements.briefing.innerHTML = '<div class="empty-state"><div><strong>暂时没有匹配内容</strong><span>换个关键词或分类试试，也可以点击“刷新”重新同步。</span></div></div>';
    return;
  }
  elements.briefing.innerHTML = topItems.map((item, index) => `
    <article class="briefing-item"><div class="briefing-rank">${String(index + 1).padStart(2, "0")}</div>
      <div class="briefing-copy"><h3><a href="${escapeHtml(item.link)}" target="_blank" rel="noreferrer" data-read-id="${escapeHtml(item.id)}">${escapeHtml(item.title)}</a></h3>
      <p>${escapeHtml(item.summary)}</p>${itemMeta(item)}</div><div class="item-actions">${bookmarkButton(item)}</div></article>
  `).join("");
}

function renderNews(items) {
  const visibleItems = items.slice(0, state.visibleCount);
  if (!visibleItems.length) {
    elements.news.innerHTML = '<div class="empty-state"><div><strong>没有找到相关资讯</strong><span>试试更短的关键词，或返回“全部”分类。</span></div></div>';
    elements.loadMore.hidden = true;
    return;
  }
  elements.news.innerHTML = visibleItems.map((item) => `
    <article class="news-item ${state.readIds.has(item.id) ? "is-read" : ""}">
      <div class="news-thumb">${item.image ? `<img src="${escapeHtml(item.image)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.parentElement.classList.add('is-broken'); this.remove();">` : `<div class="thumb-fallback" aria-hidden="true">${escapeHtml((item.category || "讯").slice(0, 1))}</div>`}</div>
      <div class="news-copy"><h3><a href="${escapeHtml(item.link)}" target="_blank" rel="noreferrer" data-read-id="${escapeHtml(item.id)}">${escapeHtml(item.title)}</a></h3><p>${escapeHtml(item.summary)}</p>${itemMeta(item)}</div>
      <div class="item-actions">${bookmarkButton(item)}</div>
    </article>
  `).join("");
  elements.loadMore.hidden = visibleItems.length >= items.length;
}

function render() {
  renderTabs();
  const items = filteredItems();
  renderBriefing(items);
  renderNews(items);
  const todayItems = state.items.filter((item) => new Date(item.published_at).toDateString() === new Date().toDateString());
  elements.heroTotal.textContent = todayItems.length || state.items.length;
  elements.heroImportant.textContent = state.items.filter((item) => item.important).length;
  elements.heroSources.textContent = new Set(state.items.map((item) => item.source)).size;
}

async function loadNews(showMessage = false) {
  elements.refresh.classList.add("is-loading");
  elements.liveState.classList.remove("is-error");
  elements.liveState.lastChild.textContent = "正在同步";
  try {
    const candidates = ["/api/news", new URL("./data/news_cache.json", document.baseURI).href];
    let payload = null;
    let lastError = null;
    for (const candidate of candidates) {
      try {
        const response = await fetch(candidate, { cache: "no-store" });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const parsed = await response.json();
        if (Array.isArray(parsed.items)) {
          payload = parsed;
          break;
        }
      } catch (error) {
        lastError = error;
      }
    }
    if (!payload) throw lastError || new Error("news cache unavailable");
    state.items = payload.items || [];
    elements.lastUpdated.textContent = payload.last_updated ? `最后同步：${new Date(payload.last_updated).toLocaleString("zh-CN", { hour12: false })}` : "尚未完成同步";
    if (payload.status === "empty") {
      elements.liveState.classList.add("is-error");
      elements.liveState.lastChild.textContent = "数据源待恢复";
      elements.lastUpdated.textContent = "聚合服务已运行，但当前公开信息源未返回内容";
    } else {
      elements.liveState.lastChild.textContent = hostedMode ? "每日更新" : "实时更新";
    }
    if (showMessage) showToast(`已同步 ${state.items.length} 条资讯`);
    render();
  } catch (error) {
    elements.liveState.classList.add("is-error");
    elements.liveState.lastChild.textContent = "同步失败";
    elements.lastUpdated.textContent = hostedMode ? "无法读取公开版资讯缓存" : "无法连接本地聚合服务，请运行 run.cmd";
    if (showMessage) showToast(hostedMode ? "公开版缓存读取失败" : "同步失败，请确认本地服务正在运行");
  } finally {
    elements.refresh.classList.remove("is-loading");
  }
}

function persistSet(key, value) { localStorage.setItem(key, JSON.stringify([...value])); }
function setAutoRefresh() { clearInterval(state.timer); state.timer = setInterval(() => loadNews(false), state.refreshInterval); }

elements.tabs.addEventListener("click", (event) => {
  const button = event.target.closest("[data-category]");
  if (!button) return;
  state.category = button.dataset.category;
  state.visibleCount = 10;
  render();
});
elements.search.addEventListener("input", () => { state.query = elements.search.value; state.visibleCount = 10; render(); });
elements.sort.addEventListener("change", () => { state.sort = elements.sort.value; render(); });
elements.loadMore.addEventListener("click", () => { state.visibleCount += 10; render(); });
elements.refresh.addEventListener("click", async () => {
  elements.refresh.classList.add("is-loading");
  if (hostedMode) {
    await loadNews(false);
    showToast("已读取公开版缓存；每日 08:00 自动更新");
    elements.refresh.classList.remove("is-loading");
    return;
  }
  try {
    const response = await fetch("/api/refresh", { cache: "no-store" });
    const payload = await response.json();
    await loadNews(false);
    showToast(payload.message || "资讯已更新");
  } catch (error) {
    await loadNews(false);
    showToast("暂时无法连接新的信息源，已保留最近缓存");
  } finally {
    elements.refresh.classList.remove("is-loading");
  }
});

document.addEventListener("click", (event) => {
  const readLink = event.target.closest("[data-read-id]");
  if (readLink) { state.readIds.add(readLink.dataset.readId); persistSet("read-ids", state.readIds); }
  const saveButton = event.target.closest("[data-save-id]");
  if (!saveButton) return;
  const id = saveButton.dataset.saveId;
  if (state.savedIds.has(id)) { state.savedIds.delete(id); showToast("已取消收藏"); }
  else { state.savedIds.add(id); showToast("已加入收藏"); }
  persistSet("saved-ids", state.savedIds);
  render();
});

elements.settingsButton.addEventListener("click", () => {
  elements.importantOnly.checked = state.importantOnly;
  elements.hideRead.checked = state.hideRead;
  elements.refreshInterval.value = String(state.refreshInterval);
  elements.settingsDialog.showModal();
});
elements.settingsDialog.addEventListener("close", () => {
  if (elements.settingsDialog.returnValue === "cancel") return;
  state.importantOnly = elements.importantOnly.checked;
  state.hideRead = elements.hideRead.checked;
  state.refreshInterval = Number(elements.refreshInterval.value);
  localStorage.setItem("important-only", String(state.importantOnly));
  localStorage.setItem("hide-read", String(state.hideRead));
  localStorage.setItem("refresh-interval", String(state.refreshInterval));
  setAutoRefresh();
  render();
  showToast("偏好已保存");
});

setAutoRefresh();
loadNews(false);
