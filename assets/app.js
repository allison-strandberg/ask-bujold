/* Ask the Author: Lois McMaster Bujold — archive front end.
   All data is static JSON; search runs client-side with MiniSearch. */

const state = {
  query: "",
  tags: new Set(),
  sort: "newest",
  shown: 50,
  hideSpoilers: true,
  tagPanelOpen: false,
};
const PAGE = 50;
const revealed = new Set(); // spoiler cards the user has clicked open

let questions = [];
let byId = new Map();
let tagsById = {};
let tagList = [];
let mini = null;
let renderedCount = 0; // questions rendered (excludes year dividers)
let lastYear = null;   // last year divider emitted

const UNTAGGED = "__untagged";

const $ = (sel) => document.querySelector(sel);

async function loadJSON(path, fallback) {
  try {
    const r = await fetch(path);
    if (!r.ok) throw new Error(r.status);
    return await r.json();
  } catch {
    return fallback;
  }
}

const stripHTML = (html) =>
  html.replace(/<br\s*\/?>/g, " ").replace(/<[^>]+>/g, "").replace(/\s+/g, " ").trim();

const esc = (s) =>
  s.replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* ---------- search highlighting ---------- */

function hlRegex() {
  const tokens = state.query
    .split(/\s+/)
    .filter((t) => t.length >= 2)
    .map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  if (!tokens.length) return null;
  return new RegExp(`\\b(${tokens.join("|")})\\w*`, "gi");
}

function highlightIn(root, regex) {
  if (!regex) return;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  for (const node of nodes) {
    regex.lastIndex = 0;
    if (!regex.test(node.nodeValue)) continue;
    const frag = document.createDocumentFragment();
    let last = 0;
    regex.lastIndex = 0;
    for (const m of node.nodeValue.matchAll(regex)) {
      frag.append(node.nodeValue.slice(last, m.index));
      const mark = document.createElement("mark");
      mark.textContent = m[0];
      frag.append(mark);
      last = m.index + m[0].length;
    }
    frag.append(node.nodeValue.slice(last));
    node.replaceWith(frag);
  }
}

function fmtDate(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return d.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
}

const DATE_SOURCES = {
  first_seen: "dated by when the archive first saw this question",
  comment: "estimated from the first comment's timestamp",
  page: "estimated from the answer's age shown on Goodreads",
  interpolated: "interpolated from neighboring questions' dates",
};

function answeredLabel(d) {
  if (!d) return "";
  const dt = new Date(d.est + "T12:00:00");
  if (isNaN(dt)) return "";
  let when;
  if (d.precision_days <= 10) {
    when = dt.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
  } else if (d.precision_days <= 92) {
    when = dt.toLocaleDateString("en-US", { year: "numeric", month: "short" });
  } else {
    when = String(dt.getFullYear());
  }
  const title = `Approximate — ${DATE_SOURCES[d.source] || "estimated"}`;
  return `<span class="answered" title="${title}">answered ~${when}</span>`;
}

async function init() {
  const [qs, tags, taglist, meta, dates] = await Promise.all([
    loadJSON("data/questions.json", []),
    loadJSON("data/tags.json", {}),
    loadJSON("data/taglist.json", []),
    loadJSON("data/meta.json", null),
    loadJSON("data/dates.json", {}),
  ]);
  questions = qs;
  tagsById = tags;
  tagList = taglist;

  for (const q of questions) {
    q._date = dates[String(q.id)] || null;
    q._plain = stripHTML(q.answer);
    q._commentsPlain = q.comments.map((c) => stripHTML(c.text)).join(" ");
    q._tags = tagsById[String(q.id)] || [];
    byId.set(q.id, q);
  }

  if (meta) {
    const n = questions.length.toLocaleString("en-US");
    const when = meta.scraped_at ? fmtDate(meta.scraped_at) : "";
    $("#archive-meta").textContent = `${n} answered questions · archive updated ${when}`;
  }

  mini = new MiniSearch({
    fields: ["question", "answer", "comments"],
    searchOptions: { boost: { question: 2 }, prefix: true, fuzzy: 0.15, combineWith: "AND" },
  });
  mini.addAll(
    questions.map((q) => ({
      id: q.id,
      question: q.question,
      answer: q._plain,
      comments: q._commentsPlain,
    }))
  );

  buildTagPanel();

  let debounce = null;
  $("#search").addEventListener("input", (e) => {
    clearTimeout(debounce);
    debounce = setTimeout(() => {
      const wasEmpty = state.query === "";
      state.query = e.target.value.trim();
      if (wasEmpty && state.query && state.sort === "newest") {
        state.sort = "relevance";
        $("#sort").value = "relevance";
      }
      if (!state.query && state.sort === "relevance") {
        state.sort = "newest";
        $("#sort").value = "newest";
      }
      state.shown = PAGE;
      render();
    }, 150);
  });

  $("#sort").addEventListener("change", (e) => {
    state.sort = e.target.value;
    state.shown = PAGE;
    render();
  });

  $("#show-spoilers").addEventListener("change", (e) => {
    state.hideSpoilers = !e.target.checked;
    if (state.hideSpoilers) revealed.clear();
    render();
  });

  $("#tag-toggle").addEventListener("click", () => {
    state.tagPanelOpen = !state.tagPanelOpen;
    $("#tag-panel").hidden = !state.tagPanelOpen;
    updateTagToggle();
  });

  $("#more").addEventListener("click", () => {
    state.shown += PAGE;
    render(true);
  });

  window.addEventListener("hashchange", openFromHash);

  render();
  document.body.classList.remove("loading");
  openFromHash();
}

/* ---------- tags ---------- */

function updateTagToggle() {
  const n = state.tags.size;
  $("#tag-toggle").textContent =
    `Filter by tag (tags contain spoilers!)${n ? ` (${n})` : ""} ${state.tagPanelOpen ? "▾" : "▸"}`;
}

function buildTagPanel() {
  if (!tagList.length) return;
  const counts = {};
  for (const q of questions) for (const t of q._tags) counts[t] = (counts[t] || 0) + 1;

  const groups = new Map();
  for (const t of tagList) {
    if (!counts[t.name]) continue;
    if (!groups.has(t.group)) groups.set(t.group, []);
    groups.get(t.group).push(t);
  }
  const groupNames = {
    vorkosigan: "The Vorkosigan Saga",
    fivegods: "World of the Five Gods",
    sharingknife: "The Sharing Knife",
    other: "Other works",
    topic: "Topics",
  };
  const container = $("#tag-groups");
  container.innerHTML = "";
  for (const [group, tags] of groups) {
    const div = document.createElement("div");
    div.className = "tag-group";
    const name = document.createElement("div");
    name.className = "tag-group-name";
    name.textContent = groupNames[group] || group;
    const chips = document.createElement("div");
    chips.className = "chips";
    tags.sort((a, b) => counts[b.name] - counts[a.name]);
    for (const t of tags) {
      const chip = document.createElement("button");
      chip.className = `chip g-${t.group}`;
      chip.dataset.tag = t.name;
      chip.innerHTML = `${esc(t.name)}<span class="count">${counts[t.name]}</span>`;
      chip.addEventListener("click", () => toggleTag(t.name));
      chips.appendChild(chip);
    }
    div.append(name, chips);
    container.appendChild(div);
  }

  // Curation helper: filter to questions with no tags yet.
  const untaggedCount = questions.filter((q) => q._tags.length === 0).length;
  if (untaggedCount) {
    const div = document.createElement("div");
    div.className = "tag-group";
    div.innerHTML = `<div class="tag-group-name">Curation</div>`;
    const chips = document.createElement("div");
    chips.className = "chips";
    const chip = document.createElement("button");
    chip.className = "chip g-topic";
    chip.dataset.tag = UNTAGGED;
    chip.innerHTML = `Untagged<span class="count">${untaggedCount}</span>`;
    chip.addEventListener("click", () => toggleTag(UNTAGGED));
    chips.appendChild(chip);
    div.appendChild(chips);
    container.appendChild(div);
  }

  $("#tag-toggle").hidden = false;
  updateTagToggle();
}

function toggleTag(tag) {
  if (state.tags.has(tag)) state.tags.delete(tag);
  else state.tags.add(tag);
  state.shown = PAGE;
  document.querySelectorAll(".chip").forEach((c) => {
    c.classList.toggle("active", state.tags.has(c.dataset.tag));
  });
  updateTagToggle();
  render();
}

/* ---------- results ---------- */

function currentList() {
  let list;
  if (state.query) {
    list = mini
      .search(state.query)
      .map((r) => byId.get(r.id))
      .filter(Boolean);
  } else {
    list = [...questions];
  }
  if (state.tags.size) {
    list = list.filter((q) =>
      [...state.tags].every((t) =>
        t === UNTAGGED ? q._tags.length === 0 : q._tags.includes(t)
      )
    );
  }
  switch (state.sort) {
    case "likes":
      list.sort((a, b) => b.likes - a.likes);
      break;
    case "newest":
      list.sort((a, b) => a.list_position - b.list_position);
      break;
    case "oldest":
      list.sort((a, b) => b.list_position - a.list_position);
      break;
    case "relevance":
      if (!state.query) list.sort((a, b) => a.list_position - b.list_position);
      break;
  }
  return list;
}

function render(append = false) {
  const list = currentList();
  const n = list.length.toLocaleString("en-US");
  $("#result-count").textContent =
    state.query || state.tags.size ? `${n} matching answers` : `${n} answers`;

  const results = $("#results");
  if (!append) {
    results.innerHTML = "";
    renderedCount = 0;
    lastYear = null;
  }
  const slice = list.slice(renderedCount, state.shown);
  renderedCount += slice.length;
  const timeline = state.sort === "newest" || state.sort === "oldest";
  for (const q of slice) {
    if (timeline && q._date) {
      const year = q._date.est.slice(0, 4);
      if (year !== lastYear) {
        const div = document.createElement("div");
        div.className = "year-divider";
        div.innerHTML = `<span>${year}</span>`;
        results.appendChild(div);
        lastYear = year;
      }
    }
    results.appendChild(card(q));
  }
  const remaining = list.length - state.shown;
  $("#more").hidden = remaining <= 0;
  if (remaining > 0) $("#more").textContent = `Show more (${remaining.toLocaleString("en-US")} remaining)`;
}

function card(q) {
  const el = document.createElement("article");
  el.className = "card";
  el.id = `card-${q.id}`;

  const chips = q._tags
    .map((t) => `<button class="minichip" data-tag="${esc(t)}">${esc(t)}</button>`)
    .join("");
  const masked = q.spoiler && state.hideSpoilers && !revealed.has(q.id);
  const nc = q.comment_count;
  const commentsCtl = nc
    ? `<button class="comments-toggle">${nc} comment${nc === 1 ? "" : "s"} ▸</button>`
    : `<span class="comments-n">no comments</span>`;

  el.innerHTML = `
    <div class="spoiler-shield${masked ? " masked" : ""}"${
      masked ? ' role="button" tabindex="0" aria-label="Spoilers hidden — press Enter to reveal"' : ""
    }>
      <div class="shield-content">
        <h2 class="question">${esc(q.question)}</h2>
        <div class="answer-label">Lois's answer</div>
        <div class="answer">${q.answer}</div>
      </div>
    </div>
    <div class="comments" hidden></div>
    <div class="foot">
      <span class="likes">♥ ${q.likes}</span>
      ${answeredLabel(q._date)}
      ${commentsCtl}
      ${chips ? `<span class="minichips">${chips}</span>` : ""}
      <a class="gr-link" href="${esc(q.url)}" target="_blank" rel="noopener">View on Goodreads ↗</a>
      <a class="permalink" href="#q/${q.id}" title="Link to this question">§</a>
    </div>`;

  const shield = el.querySelector(".spoiler-shield");
  if (masked) {
    const reveal = () => {
      revealed.add(q.id);
      shield.classList.remove("masked");
      shield.removeAttribute("role");
      shield.removeAttribute("tabindex");
      shield.removeAttribute("aria-label");
    };
    shield.addEventListener("click", reveal, { once: true });
    shield.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        reveal();
      }
    });
  }
  const btn = el.querySelector(".comments-toggle");
  if (btn) btn.addEventListener("click", () => toggleComments(el, q));
  el.querySelectorAll(".minichip").forEach((c) =>
    c.addEventListener("click", () => {
      toggleTag(c.dataset.tag);
    })
  );

  const regex = state.query ? hlRegex() : null;
  if (regex) {
    highlightIn(el.querySelector(".shield-content"), regex);
    // If the match lives only in the comments, open them so it's visible.
    regex.lastIndex = 0;
    const inQA = regex.test(q.question) || (regex.lastIndex = 0, regex.test(q._plain));
    regex.lastIndex = 0;
    if (!inQA && q.comment_count && regex.test(q._commentsPlain)) {
      toggleComments(el, q);
    }
  }
  return el;
}

function toggleComments(el, q) {
  const box = el.querySelector(".comments");
  if (!box.dataset.rendered) {
    box.innerHTML = renderComments(q);
    box.dataset.rendered = "1";
    if (state.query) highlightIn(box, hlRegex());
  }
  box.hidden = !box.hidden;
  el.classList.toggle("expanded", !box.hidden);
  const btn = el.querySelector(".comments-toggle");
  const nc = q.comment_count;
  btn.setAttribute("aria-expanded", String(!box.hidden));
  btn.textContent = box.hidden
    ? `${nc} comment${nc === 1 ? "" : "s"} ▸`
    : `Hide comments ▾`;
}

function renderComments(q) {
  let html = "";
  for (const c of q.comments) {
    const isLois = /Lois McMaster Bujold/i.test(c.author);
    const author = c.author_url
      ? `<a href="${esc(c.author_url)}" target="_blank" rel="noopener">${esc(c.author)}</a>`
      : esc(c.author);
    html += `
      <div class="comment${isLois ? " by-author" : ""}">
        <div class="comment-head">${author} · ${fmtDate(c.posted_at)}</div>
        <div class="comment-text">${c.text}</div>
      </div>`;
  }
  return html;
}

/* ---------- permalinks ---------- */

function openFromHash() {
  const m = location.hash.match(/^#q\/(\d+)$/);
  if (!m) return;
  const id = Number(m[1]);
  const q = byId.get(id);
  if (!q) return;

  let el = document.getElementById(`card-${id}`);
  if (!el) {
    // Not visible under current filters/pagination: clear filters and show it.
    state.query = "";
    $("#search").value = "";
    state.tags.clear();
    document.querySelectorAll(".chip.active").forEach((c) => c.classList.remove("active"));
    updateTagToggle();
    const list = currentList();
    const idx = list.findIndex((x) => x.id === id);
    state.shown = Math.max(PAGE, idx + 1);
    render();
    el = document.getElementById(`card-${id}`);
  }
  if (el && q.comment_count && el.querySelector(".comments").hidden) toggleComments(el, q);
  if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
}

// Assembled at runtime so address-harvesting scrapers reading raw HTML miss it.
(() => {
  const a = ["bujold", "asktheauthor", "archive"].join("");
  const addr = `${a}@${"gmail"}.com`;
  const link = document.getElementById("contact-link");
  link.href = `mailto:${addr}?subject=${encodeURIComponent("Ask the Author archive feedback")}`;
  link.textContent = addr;
})();

init();
