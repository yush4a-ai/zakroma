/* Закрома — интерфейс. Без сборки: один файл, работает и в браузере, и в программе для Windows. */
(() => {
"use strict";

const API = window.ZAKROMA_API || "";
const TAURI = window.__TAURI__ || null;
const IS_QUICK = location.hash.startsWith("#quick");
const $app = document.getElementById("app");

const LOGO = `<svg viewBox="0 0 32 28" aria-hidden="true"><path fill="#8f87f5" d="M2 26V11C2 4 10 1.5 13.4 8.2L16 13.6l2.6-5.4C22 1.5 30 4 30 11v15h-6V12.5l-5.2 9.5h-5.6L8 12.5V26z"/></svg>`;
const KIND = {
  reel:    { icon: "clapperboard", name: "Рилс" },
  youtube: { icon: "youtube",      name: "YouTube" },
  link:    { icon: "file-text",    name: "Статья" },
  note:    { icon: "lightbulb",    name: "Заметка" },
  voice:   { icon: "mic",          name: "Голосовое" },
  photo:   { icon: "image",        name: "Скриншот" },
  video:   { icon: "film",         name: "Видео" },
};
const KIND_FILTERS = [["", "Все"], ["reel", "Рилсы"], ["youtube", "YouTube"], ["link", "Статьи"], ["note", "Заметки"]];
const KIND_GROUP = { reel: "reel", youtube: "youtube", link: "link", note: "note", voice: "note", photo: "media", video: "media" };
const TAG_ICONS = [
  [/нейро|ai|ии|gpt|claude|нейрон/, "brain"], [/монтаж|premiere|davinci|видео/, "clapperboard"],
  [/unity|unreal|движ/, "box"], [/стрим|twitch|эфир/, "radio"], [/иде/, "lightbulb"],
  [/игр|геймдев|game/, "gamepad-2"], [/граф|дизайн|арт|blender|3d/, "palette"],
  [/музык|звук|аудио/, "music"], [/код|программ|python|js/, "code-xml"], [/инструмент|сервис/, "wrench"],
];
const tagIcon = t => (TAG_ICONS.find(([re]) => re.test(t)) || [0, "hash"])[1];

/* ---------- состояние ---------- */
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch {} },
};
const S = {
  token: store.get("zk_token"),
  me: null, stats: null, tags: [],
  route: { view: "all" },
  kind: "", sort: "new", layout: store.get("zk_layout") || "grid",
  items: [], offset: 0, more: false, loading: false,
  q: "",
};

/* ---------- утилиты ---------- */
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const ico = (name, cls = "") => `<i data-lucide="${name}" class="${cls}"></i>`;
const icons = () => window.lucide && lucide.createIcons({ attrs: { "stroke-width": 1.8 } });
const plural = (n, a, b, c) => { const m = n % 10, h = n % 100; return m === 1 && h !== 11 ? a : m >= 2 && m <= 4 && (h < 10 || h >= 20) ? b : c; };
const MONTHS = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];
const fmtDate = iso => { if (!iso) return ""; const d = new Date(iso); return `${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}`; };
const host = url => { try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return ""; } };
const initials = s => (s || "?").trim().split(/\s+/).map(w => w[0]).slice(0, 2).join("").toUpperCase();
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

function highlight(text, words) {
  let out = esc(text);
  (words || []).filter(w => w.length > 1).sort((a, b) => b.length - a.length).forEach(w => {
    out = out.replace(new RegExp(`(${w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "gi"), "<mark>$1</mark>");
  });
  return out.replace(/<\/mark>(\s+)<mark>/g, "$1");
}

function toast(text, action) {
  const el = document.createElement("div");
  el.className = "toast";
  el.innerHTML = `<span>${esc(text)}</span>${action ? `<button>${esc(action.label)}</button>` : ""}`;
  if (action) el.querySelector("button").onclick = () => { action.run(); el.remove(); };
  document.getElementById("toasts").appendChild(el);
  setTimeout(() => el.remove(), action ? 6000 : 3200);
}

async function api(path, opts = {}) {
  const res = await fetch(API + path, {
    method: opts.method || "GET",
    headers: { "Content-Type": "application/json", ...(S.token ? { Authorization: "Bearer " + S.token } : {}) },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (res.status === 401 && !path.startsWith("/api/login")) { logout(); throw new Error("auth"); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || "Сервер не ответил");
  return data;
}

function openUrl(url) {
  if (!url) return;
  if (TAURI?.core) TAURI.core.invoke("plugin:opener|open_url", { url }).catch(() => window.open(url, "_blank"));
  else window.open(url, "_blank", "noopener");
}

function copy(text) {
  (navigator.clipboard?.writeText(text) || Promise.reject()).then(
    () => toast("Ссылка скопирована"), () => toast("Не получилось скопировать"));
}

/* ---------- маршруты ---------- */
function parseRoute() {
  const h = decodeURIComponent(location.hash.replace(/^#\/?/, ""));
  const [path, query] = h.split("?");
  const parts = path.split("/").filter(Boolean);
  const params = new URLSearchParams(query || "");
  const r = { view: parts[0] || "all" };
  if (r.view === "tag") r.tag = parts.slice(1).join("/");
  if (r.view === "kind") r.kindPath = parts[1];
  if (r.view === "search") r.q = params.get("q") || "";
  r.item = params.get("item");
  return r;
}
const go = hash => { if (location.hash !== hash) location.hash = hash; else route(); };

/* ---------- вход ---------- */
function logout() {
  S.token = null; store.set("zk_token", null);
  renderLogin();
}

const FLOATS = ["#нейросети", "#монтаж", "#unity", "#стрим", "#идеи", "#игры", "#графика", "#туториал", "#blender", "#звук", "#инструменты", "#рилсы"];
function renderLogin(state = "start", url = "") {
  stopLoginPoll();
  // теги раскладываем по краям сетки 4×4, не залезая под карточку и за край экрана
  const SPOTS = [[6,8],[30,5],[62,9],[84,6],[4,34],[86,30],[8,60],[83,57],[5,86],[28,90],[72,95],[86,84],[18,22],[74,20],[16,74],[76,72]];
  const floats = FLOATS.concat(["#ассеты", "#озвучка", "#шейдеры", "#ai"]).slice(0, SPOTS.length).map((t, i) => {
    const [x, y] = SPOTS[i], r = ((i * 13) % 12) - 6;
    return `<span style="left:${x}%;top:${y}%;transform:translate(-${x}%,-${y}%) rotate(${r}deg)">${t}</span>`;
  }).join("");
  const kinds = [["clapperboard", "Рилсы"], ["youtube", "YouTube"], ["file-text", "Статьи"], ["lightbulb", "Заметки"]]
    .map(([i, n]) => `<div>${ico(i)}<span>${n}</span></div>`).join("");
  const body = state === "wait" ? `
      <div class="wait-ring">${ico("send")}</div>
      <h2>Ждём подтверждения<br>в Telegram…</h2>
      <p class="sub">Открой бота «Закрома» в Telegram<br>и нажми «Подтвердить вход».</p>
      <button class="btn big" id="reopen">${ico("external-link")} Открыть Telegram ещё раз</button>
      <button class="btn ghost sm" id="back">Отмена</button>` : `
      <p class="lead">Всё полезное — в одном месте</p>
      <div class="kinds">${kinds}</div>
      <button class="btn accent big" id="tg">${ico("send")} Войти через Telegram <span class="arr">${ico("arrow-right")}</span></button>
      <div class="or">или с телефона</div>
      <div class="qr" id="qr" aria-label="QR-код для входа"></div>
      <p class="sub" style="margin:10px 0 0">Наведи камеру и нажми «Старт» в боте</p>`;
  $app.innerHTML = `<div class="login"><div class="bar-top">${LOGO}Закрома</div><div class="floats">${floats}</div>
    <div class="login-card"><div class="mark">${LOGO}</div><h1>Закрома</h1>${body}
    <small>Входить нужно один раз на каждом устройстве.</small></div></div>`;
  icons();

  if (state === "wait") {
    document.getElementById("reopen").onclick = () => openUrl(url);
    document.getElementById("back").onclick = () => renderLogin();
    return;
  }
  startLogin().then(sess => {
    if (!sess) return;
    const qrEl = document.getElementById("qr");
    if (qrEl && window.qrcode) {
      const q = qrcode(0, "M"); q.addData(sess.url); q.make();
      qrEl.innerHTML = q.createSvgTag({ cellSize: 4, margin: 0, scalable: true }) + `<span class="tgb">${ico("send")}</span>`;
      icons();
    }
    const btn = document.getElementById("tg");
    if (btn) btn.onclick = () => { openUrl(sess.url); renderLogin("wait", sess.url); pollLogin(sess.key); };
    pollLogin(sess.key);
  });
}

let loginTimer = null;
const stopLoginPoll = () => { clearTimeout(loginTimer); loginTimer = null; };
async function startLogin() {
  try { return await api("/api/login/start", { method: "POST" }); }
  catch { toast("Сервер Закромов не отвечает. Проверь интернет."); return null; }
}
function pollLogin(key) {
  stopLoginPoll();
  const tick = async () => {
    try {
      const r = await api("/api/login/poll?key=" + encodeURIComponent(key));
      if (r.token) { S.token = r.token; store.set("zk_token", r.token); boot(); return; }
    } catch {}
    loginTimer = setTimeout(tick, 2000);
  };
  loginTimer = setTimeout(tick, 1500);
}

/* ---------- каркас ---------- */
function shell() {
  $app.innerHTML = `
  <div class="shell">
    <aside class="side" id="side"></aside>
    <main class="main">
      <div class="top">
        <button class="btn icon burger" id="burger" aria-label="Меню">${ico("menu")}</button>
        <label class="search">${ico("search")}
          <span class="sr">Поиск</span>
          <input id="q" placeholder="Найти по смыслу: нейронка для субтитров" autocomplete="off" spellcheck="false">
          <button class="clear" id="qclear" hidden aria-label="Очистить">${ico("x")}</button>
        </label>
        <button class="btn big triage-btn" id="triageBtn">${ico("inbox")}<span>Разобрать (<b class="num" id="newN">0</b>)</span></button>
      </div>
      <div class="content" id="content"></div>
    </main>
  </div>`;
  icons();
  const q = document.getElementById("q");
  const clearBtn = document.getElementById("qclear");
  const doSearch = debounce(v => go(v.trim() ? "#/search?q=" + encodeURIComponent(v.trim()) : "#/all"), 350);
  q.addEventListener("input", () => { clearBtn.hidden = !q.value; doSearch(q.value); });
  q.addEventListener("keydown", e => { if (e.key === "Escape") { q.value = ""; clearBtn.hidden = true; go("#/all"); q.blur(); } });
  clearBtn.onclick = () => { q.value = ""; clearBtn.hidden = true; go("#/all"); q.focus(); };
  document.getElementById("triageBtn").onclick = () => go("#/new");
  document.getElementById("burger").onclick = () => document.getElementById("side").classList.toggle("open");
}

function renderSide() {
  const s = S.stats || {};
  const r = S.route;
  const cur = r.view === "tag" ? "tag/" + r.tag : r.view === "kind" ? "kind/" + r.kindPath : r.view;
  const link = (hash, icon, name, n) =>
    `<a href="#/${esc(hash)}" class="${cur === hash ? "on" : ""}">${ico(icon)}<span>${esc(name)}</span>${n != null ? `<span class="n">${n}</span>` : ""}</a>`;
  const KIND_WORDS = /^(статьи|рилсы|youtube|заметки|видео)$/;
  const tags = S.tags.filter(t => !KIND_WORDS.test(t.tag)).slice(0, 7).map(t => link("tag/" + t.tag, tagIcon(t.tag), t.tag[0].toUpperCase() + t.tag.slice(1).replace(/_/g, " "), t.count)).join("");
  const kinds = [["link", "file-text", "Статьи"], ["reel", "clapperboard", "Рилсы"], ["youtube", "youtube", "YouTube"], ["note", "sticky-note", "Заметки"], ["media", "image", "Скриншоты и видео"]]
    .filter(([k]) => s[k]).map(([k, i, n]) => link("kind/" + k, i, n, s[k])).join("");
  document.getElementById("side").innerHTML = `
    <a class="logo" href="#/all">${LOGO}<span>Закрома</span></a>
    <nav class="nav">
      ${link("all", "layout-grid", "Все", s.total ?? "")}
      ${link("favorite", "heart", "Избранное", s.favorite ?? "")}
      ${link("try", "play", "Попробовать", s.try ?? "")}
      ${link("used", "check-check", "Использовал", s.used ?? "")}
      ${link("new", "inbox", "Разобрать", s.new ?? "")}
    </nav>
    ${tags ? `<div class="nav-sep"></div><nav class="nav">${tags}</nav>` : ""}
    ${kinds ? `<div class="nav-sep"></div><nav class="nav">${kinds}</nav>` : ""}
    <div class="nav-sep"></div>
    <nav class="nav">${link("trash", "trash-2", "Корзина", s.trash ?? "")}</nav>
    <div class="side-foot nav">${link("settings", "settings", "Настройки")}</div>`;
  const n = document.getElementById("newN"); if (n) n.textContent = s.new ?? 0;
  icons();
}

async function refreshMeta() {
  try {
    const [st, tg] = await Promise.all([api("/api/stats"), api("/api/tags")]);
    S.stats = st; S.tags = tg.tags; renderSide();
  } catch {}
}

/* ---------- лента ---------- */
const VIEWS = {
  all:      { title: "Все", sub: "" },
  favorite: { title: "Избранное", sub: "Материалы, которые ты отметил как важные" },
  try:      { title: "Попробовать", sub: "То, что хочется применить на деле" },
  used:     { title: "Использовал", sub: "Уже пригодилось" },
  trash:    { title: "Корзина", sub: "Удалённое хранится 30 дней, потом исчезает" },
};

function listQuery(offset = 0) {
  const r = S.route, p = new URLSearchParams();
  if (r.view === "search") p.set("q", r.q);
  else if (r.view === "tag") p.set("tag", r.tag);
  else if (r.view === "kind") p.set("kind", r.kindPath);
  else p.set("view", r.view);
  if (S.kind && r.view !== "kind") p.set("kind", S.kind);
  p.set("sort", S.sort);
  if (offset) p.set("offset", offset);
  return "/api/items?" + p;
}

async function loadList(append = false) {
  S.loading = true;
  const offset = append ? S.items.length : 0;
  if (!append) renderFeed(true);
  try {
    const { items } = await api(listQuery(offset));
    S.items = append ? S.items.concat(items) : items;
    S.more = S.route.view !== "search" && items.length === 60;
  } catch (e) { if (e.message !== "auth") toast(e.message); }
  S.loading = false;
  renderFeed();
}

function headHtml() {
  const r = S.route, s = S.stats || {};
  if (r.view === "all") {
    const chips = S.tags.slice(0, 4).map(t =>
      `<a class="chip" href="#/tag/${encodeURIComponent(t.tag)}">#${esc(t.tag)} <span class="n">${t.count}</span></a>`).join("");
    return `<div class="bar">${chips}<span class="grow"></span>${filtersHtml()}</div>`;
  }
  let title, sub, count;
  if (r.view === "search") {
    title = "Результаты поиска";
    sub = S.loading ? "Ищу…" : `Найдено ${S.items.length} ${plural(S.items.length, "материал", "материала", "материалов")} по запросу «${esc(r.q)}»`;
  } else if (r.view === "tag") {
    title = "#" + esc(r.tag); count = (S.tags.find(t => t.tag === r.tag) || {}).count;
    sub = "Всё с этим тегом";
  } else if (r.view === "kind") {
    title = { reel: "Рилсы", youtube: "YouTube", link: "Статьи", note: "Заметки", media: "Скриншоты и видео" }[r.kindPath] || "";
    count = s[r.kindPath];
  } else {
    title = VIEWS[r.view].title; sub = VIEWS[r.view].sub; count = s[r.view];
  }
  return `<div class="head"><div><h1>${title}${count != null ? ` <span class="count num">${count}</span>` : ""}</h1>${sub ? `<p>${sub}</p>` : ""}</div>
    <span class="grow"></span>${filtersHtml()}</div>`;
}

function filtersHtml() {
  const r = S.route;
  const isSearch = r.view === "search";
  const counts = {};
  if (isSearch) S.items.forEach(i => { const g = KIND_GROUP[i.kind]; counts[g] = (counts[g] || 0) + 1; });
  const pills = r.view === "kind" ? "" : KIND_FILTERS.map(([k, n]) => {
    const c = isSearch ? (k ? counts[k] || 0 : S.items.length) : null;
    if (isSearch && k && !c) return "";
    return `<button class="pill ${S.kind === k ? "on" : ""}" data-kind="${k}">${n}${c != null ? ` <span class="n num">${c}</span>` : ""}</button>`;
  }).join("");
  const sort = isSearch ? `<span class="select">${ico("arrow-down-up")}По релевантности</span>` :
    `<label class="select">${ico("arrow-down-up")}<span class="sr">Сортировка</span><select id="sort">
      <option value="new" ${S.sort === "new" ? "selected" : ""}>Сначала новые</option>
      <option value="old" ${S.sort === "old" ? "selected" : ""}>Сначала старые</option></select>${ico("chevron-down", "chev")}</label>`;
  const lay = isSearch ? "list" : S.layout;
  return `<div class="bar" style="padding:0">${pills}<span style="width:8px"></span>${sort}
    <div class="seg" role="group" aria-label="Вид"><button data-lay="grid" class="${lay === "grid" ? "on" : ""}" aria-label="Плиткой">${ico("layout-grid")}</button>
    <button data-lay="list" class="${lay === "list" ? "on" : ""}" aria-label="Списком">${ico("list")}</button></div></div>`;
}

function actionsHtml(it, compact) {
  const r = S.route.view;
  const primary = it.status === "trash"
    ? `<button class="btn sm accent" data-act="restore">${ico("undo-2")} Вернуть</button>`
    : (r === "all" || r === "tag" || r === "kind" || r === "new") && it.status !== "try"
      ? `<button class="btn sm accent" data-act="try">${ico("play")} Попробовать</button>`
      : `<button class="btn sm accent" data-act="open">${ico("play")} Открыть</button>`;
  const used = `<button class="btn sm ${it.status === "used" ? "on" : ""}" data-act="used">${ico("check")} Использовал</button>`;
  const trash = it.status === "trash" ? "" : `<button class="btn sm trash" data-act="trash" title="В мусор">${ico("trash-2")}<span class="lbl">В мусор</span></button>`;
  return compact ? primary + used + trash : `<div class="acts">${primary}${used}${trash}</div>`;
}

function cardHtml(it) {
  const k = KIND[it.kind] || KIND.link;
  const note = it.kind === "note" || it.kind === "voice";
  const done = it.status === "used" && S.route.view !== "used" ? " done" : "";
  const tags = it.tags.length ? `<div class="tags">${it.tags.slice(0, 3).map(t => `<span class="tag">#${esc(t)}</span>`).join("")}</div>` : "";
  const sum = it.summary || (note ? it.note : "") || "";
  const star = it.favorite ? `<span class="star">${ico("star")}</span>` : "";
  if (it.image && !note) {
    return `<article class="card${done}" data-id="${it.id}" tabindex="0">
      <div class="thumb ${it.kind === "reel" && S.route.view === "all" ? "tall" : ""}"><img src="${esc(it.image)}" alt="" loading="lazy" referrerpolicy="no-referrer" data-fallback>
        <span class="kind">${ico(k.icon)}</span>${star}<button class="dots more" data-act="menu" aria-label="Ещё">${ico("ellipsis-vertical")}</button></div>
      <div class="card-body"><div class="card-title"><h3>${esc(it.title)}</h3></div>
        ${sum ? `<p class="sum">${esc(sum)}</p>` : ""}${tags}${actionsHtml(it)}</div></article>`;
  }
  return `<article class="card textonly${note ? " note" : ""}${done}" data-id="${it.id}" tabindex="0">
    <div class="card-body"><div class="head-row"><span class="kind-flat">${ico(k.icon)}</span>
      <span style="display:flex;gap:2px;align-items:center">${it.favorite ? `<span style="color:var(--star)">${ico("star")}</span>` : ""}<button class="dots" data-act="menu" aria-label="Ещё">${ico("ellipsis-vertical")}</button></span></div>
      <div class="card-title"><h3>${esc(it.title)}</h3></div>
      ${sum ? `<p class="sum">${esc(sum)}</p>` : ""}
      ${!note && it.url ? `<span class="tag" style="align-self:flex-start">${ico("link")} ${esc(host(it.url))}</span>` : ""}
      ${tags}${actionsHtml(it)}</div></article>`;
}

function rowHtml(it) {
  const k = KIND[it.kind] || KIND.link;
  const tags = `<div class="tags">${it.tags.slice(0, 3).map(t => `<span class="tag">#${esc(t)}</span>`).join("")}</div>`;
  const thumb = it.image
    ? `<div class="thumb"><img src="${esc(it.image)}" alt="" loading="lazy" referrerpolicy="no-referrer" data-fallback><span class="kind">${ico(k.icon)}</span></div>`
    : `<div class="thumb empty">${ico(k.icon)}</div>`;
  const match = it.match != null ? `<div class="match"><span class="why">${ico("sparkles")} ${it.match_kind === "meaning" ? "Совпадение по смыслу" : "Совпадение по словам"}</span>
      <p>${highlight(it.match, it.highlight)}</p></div>` :
    `<div class="match"><span class="why">${ico("calendar")} Сохранено ${fmtDate(it.created_at)}</span><p>${esc(it.note || host(it.url) || "")}</p></div>`;
  const favBtn = `<button class="btn sm ${it.favorite ? "on" : ""}" data-act="fav">${ico("star")} ${it.favorite ? "В избранном" : "В избранное"}</button>`;
  return `<article class="row" data-id="${it.id}" tabindex="0">${thumb}
    <div class="info"><h3>${esc(it.title)}</h3><p>${esc(it.summary || it.note || "")}</p>${tags}</div>${match}
    <div class="side-acts"><div class="c">${actionsHtml(it, true)}</div><div class="c"><button class="dots" data-act="menu" aria-label="Ещё">${ico("ellipsis-vertical")}</button>${favBtn}</div></div></article>`;
}

function masonry(items) {
  const w = document.getElementById("content").clientWidth - 40;
  const n = Math.max(1, Math.min(5, Math.floor((w + 14) / 300)));
  const cols = Array.from({ length: n }, () => ({ h: 0, html: [] }));
  items.forEach(it => {
    const est = (it.image && it.kind !== "note" ? (it.kind === "reel" ? 360 : 170) : 40) + 120 +
      Math.min(3, Math.ceil((it.summary || it.note || "").length / 45)) * 20;
    const c = cols.reduce((a, b) => (b.h < a.h ? b : a));
    c.html.push(cardHtml(it)); c.h += est;
  });
  return `<div class="masonry">${cols.map(c => `<div class="col">${c.html.join("")}</div>`).join("")}</div>`;
}

function emptyHtml() {
  const r = S.route.view;
  const [icon, h, p] =
    r === "search" ? ["search-x", "Ничего не нашлось", "Попробуй описать другими словами — поиск понимает смысл, а не только точные совпадения."] :
    r === "favorite" ? ["star", "Пока пусто", "Отмечай важное звёздочкой в карточке — оно соберётся здесь."] :
    r === "trash" ? ["trash-2", "Корзина пуста", "Удалённое попадает сюда и хранится 30 дней."] :
    r === "try" ? ["play", "Нечего пробовать", "Нажимай «Попробовать» на карточках — здесь будет список дел."] :
    ["inbox", "Здесь пока пусто", `Кинь ссылку или заметку боту @${esc(S.me?.bot || "vzakroma_bot")} — она появится здесь.`];
  return `<div class="empty-state">${ico(icon)}<h2>${h}</h2><p>${p}</p></div>`;
}

function renderFeed(skeleton = false) {
  const el = document.getElementById("content");
  if (!el) return;
  const list = S.route.view === "search" || S.layout === "list";
  let body;
  if (skeleton) body = `<div class="masonry">${[0, 1, 2, 3].map(i => `<div class="col">${[260, 190, 300].map(h => `<div class="skel" style="height:${h - i * 12}px"></div>`).join("")}</div>`).join("")}</div>`;
  else if (!S.items.length) body = emptyHtml();
  else body = list ? `<div class="rows">${S.items.map(rowHtml).join("")}</div>`
    : S.route.view === "all" ? masonry(S.items) : `<div class="grid">${S.items.map(cardHtml).join("")}</div>`;
  const more = !skeleton && S.more ? `<div class="more-wrap"><button class="btn" id="more">Показать ещё</button></div>` : "";
  el.innerHTML = headHtml() + body + more;
  icons();
  el.querySelectorAll("img[data-fallback]").forEach(img => img.addEventListener("error", () => {
    const t = img.closest(".thumb"); if (!t) return;
    if (t.closest(".card")) { t.closest(".card").classList.add("textonly"); t.remove(); }
    else { t.classList.add("empty"); img.remove(); }
  }, { once: true }));
  const m = document.getElementById("more"); if (m) m.onclick = () => loadList(true);
  const sort = document.getElementById("sort"); if (sort) sort.onchange = () => { S.sort = sort.value; loadList(); };
  el.querySelectorAll("[data-kind]").forEach(b => b.onclick = () => {
    S.kind = b.dataset.kind;
    if (S.route.view === "search") renderFeed(); else loadList();
  });
  if (S.route.view === "search" && S.kind) el.querySelectorAll(".row").forEach(row => {
    const it = S.items.find(i => i.id == row.dataset.id); if (KIND_GROUP[it.kind] !== S.kind) row.remove();
  });
  el.querySelectorAll("[data-lay]").forEach(b => b.onclick = () => {
    if (S.route.view === "search") return;
    S.layout = b.dataset.lay; store.set("zk_layout", S.layout); renderFeed();
  });
}

const relayout = debounce(() => { if (["all", "favorite", "try", "used", "trash", "tag", "kind"].includes(S.route.view) && S.layout === "grid" && S.items.length) renderFeed(); }, 150);
window.addEventListener("resize", relayout);

/* ---------- действия с записями ---------- */
const byId = id => S.items.find(i => i.id == id);

async function patch(it, body, msg, undo) {
  const before = { ...it };
  Object.assign(it, body);
  const leaves = (S.route.view === "favorite" && body.favorite === false) ||
    (body.status && ["try", "used", "new", "trash"].includes(S.route.view) && body.status !== S.route.view) ||
    (body.status === "trash" && S.route.view !== "trash");
  if (leaves) S.items = S.items.filter(i => i !== it);
  renderFeed();
  try {
    await api("/api/items/" + it.id, { method: "PATCH", body });
    if (msg) toast(msg, undo ? { label: "Отменить", run: () => restore(before, body) } : null);
    refreshMeta();
  } catch (e) { toast(e.message); Object.assign(it, before); loadList(); }
}
async function restore(before, body) {
  const back = {}; Object.keys(body).forEach(k => back[k] = before[k]);
  await api("/api/items/" + before.id, { method: "PATCH", body: back });
  loadList(); refreshMeta();
}

function act(name, it, anchor) {
  if (name === "try") patch(it, { status: "try" }, "Добавлено в «Попробовать»");
  else if (name === "used") patch(it, { status: it.status === "used" ? "keep" : "used" }, it.status === "used" ? null : "Отмечено: использовал");
  else if (name === "trash") patch(it, { status: "trash" }, "Перенесено в корзину", true);
  else if (name === "restore") patch(it, { status: "keep" }, "Возвращено из корзины");
  else if (name === "fav") patch(it, { favorite: !it.favorite }, it.favorite ? "Убрано из избранного" : "Добавлено в избранное");
  else if (name === "open") openUrl(it.url) || (!it.url && openItem(it.id));
  else if (name === "menu") showMenu(it, anchor);
}

function showMenu(it, anchor) {
  closeMenu();
  const m = document.createElement("div");
  m.className = "menu"; m.id = "menu";
  m.innerHTML = `
    <button data-m="detail">${ico("panel-right-open")} Подробнее</button>
    ${it.url ? `<button data-m="open">${ico("external-link")} Открыть оригинал</button><button data-m="copy">${ico("copy")} Скопировать ссылку</button>` : ""}
    <button data-m="fav">${ico("star")} ${it.favorite ? "Убрать из избранного" : "В избранное"}</button>
    ${it.status === "trash" ? `<button data-m="restore">${ico("undo-2")} Вернуть</button>` : `<button data-m="trash" class="danger">${ico("trash-2")} В мусор</button>`}`;
  document.body.appendChild(m);
  icons();
  const r = anchor.getBoundingClientRect();
  m.style.top = Math.min(r.bottom + 6, innerHeight - m.offsetHeight - 10) + "px";
  m.style.left = Math.max(10, Math.min(r.right - m.offsetWidth, innerWidth - m.offsetWidth - 10)) + "px";
  m.onclick = e => {
    const b = e.target.closest("[data-m]"); if (!b) return;
    closeMenu();
    const k = b.dataset.m;
    if (k === "detail") openItem(it.id);
    else if (k === "open") openUrl(it.url);
    else if (k === "copy") copy(it.url);
    else act(k, it);
  };
  setTimeout(() => document.addEventListener("click", closeMenu, { once: true }), 0);
}
const closeMenu = () => document.getElementById("menu")?.remove();

document.addEventListener("click", e => {
  const el = e.target.closest("[data-id]");
  if (!el || !document.getElementById("content")?.contains(el)) return;
  const it = byId(el.dataset.id); if (!it) return;
  const b = e.target.closest("[data-act]");
  if (b) { e.stopPropagation(); act(b.dataset.act, it, b); return; }
  if (e.target.closest("a")) return;
  openItem(it.id);
});
document.addEventListener("keydown", e => {
  if (e.key === "Enter" && e.target.matches?.("[data-id]")) openItem(e.target.dataset.id);
});

/* ---------- панель записи ---------- */
async function openItem(id) {
  closeDrawer();
  let it;
  try { it = await api("/api/items/" + id); } catch (e) { return toast(e.message); }
  const k = KIND[it.kind] || KIND.link;
  const listItem = byId(id);
  const isVideo = ["reel", "youtube", "video"].includes(it.kind);
  const wrap = document.createElement("div");
  wrap.id = "drawer";
  wrap.innerHTML = `<div class="scrim"></div>
  <aside class="drawer" role="dialog" aria-modal="true" aria-label="${esc(it.title)}">
    <button class="btn icon ghost x" data-d="close" aria-label="Закрыть">${ico("x")}</button>
    <button class="dots dmenu" data-d="menu" aria-label="Ещё">${ico("ellipsis-vertical")}</button>
    <div class="scroll">
      ${it.image ? `<div class="hero"><img src="${esc(it.image)}" alt="" referrerpolicy="no-referrer" onerror="this.parentNode.remove()">
        ${it.url ? `<button class="btn sm open" data-d="open">Открыть оригинал ${ico("external-link")}</button>` : ""}
        ${isVideo && it.url ? `<button class="play" data-d="open" aria-label="Смотреть">${ico("play")}</button>` : ""}</div>` : ""}
      <h2>${esc(it.title)}</h2>
      <div class="meta">
        <div class="who"><span class="avatar">${it.source ? esc(initials(it.source)) : ico(k.icon)}</span>
          <div><b>${esc(it.source || host(it.url) || k.name)}</b><span>${esc(k.name)}${it.url ? " · " + esc(host(it.url)) : ""}</span></div></div>
        <span class="saved">${ico("calendar")} Сохранено ${fmtDate(it.created_at)}</span>
      </div>
      ${it.summary ? `<section class="sec"><h4>${ico("sparkles")} Выжимка от ИИ</h4><div class="box"><p>${esc(it.summary)}</p></div></section>` : ""}
      ${it.speech ? `<section class="sec"><h4>${ico("audio-lines")} Что говорят в видео</h4><div class="box speech" id="speech"><p>${esc(it.speech)}</p>
        <button class="btn sm more-btn" data-d="speech">Показать полностью ${ico("chevron-down")}</button></div></section>` : ""}
      <section class="sec"><h4>${ico("pencil-line")} Моя пометка</h4>
        <textarea class="note-edit" id="noteEdit" placeholder="Зачем сохранил, что хочешь попробовать…">${esc(it.note || "")}</textarea></section>
      <section class="sec"><h4>${ico("tags")} Теги</h4><div class="tag-edit" id="tagEdit"></div></section>
    </div>
    <div class="foot">
      <button class="btn ${it.status === "try" ? "on" : "accent"}" data-d="try">${ico("play")} ${it.status === "try" ? "В планах" : "Попробовать"}</button>
      <button class="btn ${it.status === "used" ? "on" : ""}" data-d="used">${ico("check")} Использовал</button>
      <button class="btn ${it.favorite ? "on" : ""}" data-d="fav">${ico("heart")} ${it.favorite ? "В избранном" : "В избранное"}</button>
      <button class="btn" data-d="trash">${ico("trash-2")} ${it.status === "trash" ? "Вернуть" : "В мусор"}</button>
    </div>
  </aside>`;
  document.body.appendChild(wrap);
  icons();

  const save = async (body, msg) => {
    try {
      await api("/api/items/" + it.id, { method: "PATCH", body });
      Object.assign(it, body); if (listItem) Object.assign(listItem, body);
      if (msg) toast(msg);
      refreshMeta();
    } catch (e) { toast(e.message); }
  };
  const tagBox = wrap.querySelector("#tagEdit");
  const drawTags = () => {
    tagBox.innerHTML = it.tags.map((t, i) => `<span class="tag">#${esc(t)}<button data-rm="${i}" aria-label="Убрать тег ${esc(t)}">${ico("x")}</button></span>`).join("") +
      `<button class="add" data-d="addtag">${ico("plus")} Добавить тег</button>`;
    icons();
  };
  drawTags();
  tagBox.onclick = e => {
    const rm = e.target.closest("[data-rm]");
    if (rm) { it.tags.splice(+rm.dataset.rm, 1); drawTags(); save({ tags: it.tags }); return; }
    if (e.target.closest("[data-d=addtag]")) {
      const add = tagBox.querySelector(".add");
      const inp = document.createElement("input"); inp.placeholder = "новый тег"; add.replaceWith(inp); inp.focus();
      const commit = () => {
        const v = inp.value.trim().replace(/^#/, "").toLowerCase().replace(/\s+/g, "_");
        if (v && !it.tags.includes(v)) { it.tags.push(v); save({ tags: it.tags }); }
        drawTags();
      };
      inp.onkeydown = ev => { if (ev.key === "Enter") commit(); if (ev.key === "Escape") drawTags(); };
      inp.onblur = commit;
    }
  };
  const noteEl = wrap.querySelector("#noteEdit");
  noteEl.addEventListener("input", debounce(() => save({ note: noteEl.value }), 700));

  wrap.addEventListener("click", e => {
    if (e.target.classList.contains("scrim")) return closeDrawer();
    const b = e.target.closest("[data-d]"); if (!b) return;
    const d = b.dataset.d;
    if (d === "close") closeDrawer();
    else if (d === "menu") showMenu(listItem || it, b);
    else if (d === "open") openUrl(it.url);
    else if (d === "speech") { const s = wrap.querySelector("#speech"); s.classList.toggle("open"); b.innerHTML = s.classList.contains("open") ? `Свернуть ${ico("chevron-up")}` : `Показать полностью ${ico("chevron-down")}`; icons(); }
    else if (d === "try") { save({ status: "try" }, "Добавлено в «Попробовать»"); closeDrawer(); loadList(); }
    else if (d === "used") { save({ status: "used" }, "Отмечено: использовал"); closeDrawer(); loadList(); }
    else if (d === "fav") { save({ favorite: !it.favorite }, it.favorite ? "Убрано из избранного" : "Добавлено в избранное"); closeDrawer(); loadList(); }
    else if (d === "trash") { save({ status: it.status === "trash" ? "keep" : "trash" }, it.status === "trash" ? "Возвращено" : "Перенесено в корзину"); closeDrawer(); loadList(); }
  });
  wrap.querySelector(".drawer").focus?.();
}
function closeDrawer() { document.getElementById("drawer")?.remove(); }

/* ---------- разбор ---------- */
const T = { queue: [], total: 0, pos: 0 };
async function renderTriage() {
  const el = document.getElementById("content");
  el.innerHTML = `<div class="loading">Загружаю…</div>`;
  try {
    const [{ items }, back] = await Promise.all([api("/api/items?view=new&sort=new"), api("/api/items?view=all").catch(() => ({ items: [] }))]);
    T.queue = items; T.total = S.stats?.new || items.length; T.pos = 0;
    T.bg = back.items.length ? `<div class="grid">${back.items.slice(0, 12).map(i => cardHtml(i)).join("")}</div>` : "";
  } catch (e) { if (e.message !== "auth") toast(e.message); return; }
  drawTriage();
}
function drawTriage() {
  const el = document.getElementById("content");
  if (S.route.view !== "new" || !el) return;
  const it = T.queue[0];
  const done = Math.min(T.pos + 1, T.total);
  const pct = T.total ? Math.round((T.pos / T.total) * 100) : 100;
  const head = `<div class="triage-head"><div class="t"><h1>Разбираем сохранённое</h1>
      <p>Быстро просматривай и решай, что оставить, попробовать или удалить</p></div>
    <div class="progress"><b>${it ? `${done} из ${T.total}` : "Всё разобрано"}</b><div class="track"><div class="fill" style="width:${it ? pct : 100}%"></div></div></div>
    <button class="btn big" id="finish">${ico("x")} Завершить</button></div>`;
  if (!it) {
    el.innerHTML = `<div class="triage">${head}<div class="done-state">${ico("party-popper")}<h2>Завалов больше нет</h2>
      <p>Всё сохранённое разобрано. Новое из бота появится здесь само.</p><a class="btn accent big" href="#/all">К ленте</a></div></div>`;
    icons(); document.getElementById("finish").onclick = () => go("#/all"); return;
  }
  const k = KIND[it.kind] || KIND.link;
  const note = it.kind === "note" || it.kind === "voice";
  const bg = T.bg || "";
  el.innerHTML = `<div class="triage"><div class="triage-bg" aria-hidden="true">${bg}</div>${head}
    <article class="tcard ${note ? "note-card" : ""}" id="tcard">
      ${it.image && !note ? `<div class="thumb"><img src="${esc(it.image)}" alt="" referrerpolicy="no-referrer" onerror="this.parentNode.remove()">
        <span class="kind">${ico(k.icon)}</span>${it.url ? `<button class="btn sm open" id="topen">Открыть оригинал ${ico("external-link")}</button>` : ""}
        <button class="dots" id="tmenu" aria-label="Ещё">${ico("ellipsis-vertical")}</button></div>` : ""}
      <div class="body">
        <h2>${esc(it.title)}</h2>
        <div class="meta"><div class="who"><span class="avatar">${it.source ? esc(initials(it.source)) : ico(k.icon)}</span>
          <div><b>${esc(it.source || host(it.url) || k.name)}</b><span>${esc(k.name)}</span></div></div>
          <span class="saved">${ico("calendar")} Сохранено ${fmtDate(it.created_at)}</span></div>
        ${it.summary || it.note ? `<section class="sec"><h4>${ico("sparkles")} ${it.summary ? "Выжимка от ИИ" : "Заметка"}</h4><div class="box"><p>${esc(it.summary || it.note)}</p></div></section>` : ""}
        <div class="tag-edit" id="ttags">${it.tags.map(t => `<span class="tag">#${esc(t)}</span>`).join("")}<button class="add" id="taddtag">${ico("plus")} Добавить тег</button></div>
        ${!it.image && it.url ? `<button class="btn sm" id="topen" style="align-self:flex-start">${ico("external-link")} Открыть оригинал</button>` : ""}
      </div></article>
    <div class="tactions">
      <button class="tbtn" data-t="keep">${ico("bookmark")}<span>Оставить</span><kbd>1</kbd></button>
      <button class="tbtn accent" data-t="try">${ico("play")}<span>Попробовать</span><kbd>2</kbd></button>
      <button class="tbtn" data-t="used">${ico("check")}<span>Уже использовал</span><kbd>3</kbd></button>
      <button class="tbtn danger" data-t="trash">${ico("trash-2")}<span>В мусор</span><kbd>Del</kbd></button>
      <button class="tbtn" data-t="skip">${ico("skip-forward")}<span>Пропустить</span><kbd>${ico("arrow-right")}</kbd></button>
    </div></div>`;
  icons();
  document.getElementById("finish").onclick = () => go("#/all");
  const o = document.getElementById("topen"); if (o) o.onclick = () => openUrl(it.url);
  const tm = document.getElementById("tmenu"); if (tm) tm.onclick = () => showMenu(it, tm);
  const ta = document.getElementById("taddtag");
  if (ta) ta.onclick = () => {
    const inp = document.createElement("input"); inp.placeholder = "новый тег"; ta.replaceWith(inp); inp.focus();
    const commit = () => {
      const v = inp.value.trim().replace(/^#/, "").toLowerCase().replace(/\s+/g, "_");
      if (v && !it.tags.includes(v)) { it.tags.push(v); api("/api/items/" + it.id, { method: "PATCH", body: { tags: it.tags } }).catch(e => toast(e.message)); }
      drawTriage();
    };
    inp.onkeydown = ev => { ev.stopPropagation(); if (ev.key === "Enter") commit(); if (ev.key === "Escape") drawTriage(); };
    inp.onblur = commit;
  };
  el.querySelectorAll("[data-t]").forEach(b => b.onclick = () => triage(b.dataset.t));
}
let triageBusy = false;
async function triage(what) {
  const it = T.queue[0]; if (!it || triageBusy) return;
  triageBusy = true;
  document.querySelector(`[data-t="${what}"]`)?.classList.add("hit");
  document.getElementById("tcard")?.classList.add("leave");
  if (what === "skip") T.queue.push(T.queue.shift());
  else {
    T.queue.shift(); T.pos++;
    api("/api/items/" + it.id, { method: "PATCH", body: { status: what } })
      .then(() => { if (S.stats) { S.stats.new = Math.max(0, S.stats.new - 1); if (what !== "keep") S.stats[what] = (S.stats[what] || 0) + 1; renderSide(); } })
      .catch(e => toast(e.message));
  }
  await new Promise(r => setTimeout(r, 200));
  triageBusy = false;
  drawTriage();
}
document.addEventListener("keydown", e => {
  if (S.route.view !== "new" || e.target.matches("input, textarea") || document.getElementById("drawer")) return;
  const map = { "1": "keep", "2": "try", "3": "used", Delete: "trash", Backspace: "trash", ArrowRight: "skip" };
  if (map[e.key]) { e.preventDefault(); triage(map[e.key]); }
});

/* ---------- настройки ---------- */
async function renderSettings() {
  const el = document.getElementById("content");
  const me = S.me || {}, st = S.stats || {}, imp = st.import;
  const botUrl = `https://t.me/${me.bot || "vzakroma_bot"}`;
  const keys = [["Быстрый поиск", ["Ctrl", "Shift", "Z"]], ["Сохранить из буфера обмена", ["Ctrl", "Shift", "S"]],
    ["Разобрать сохранённое", ["Ctrl", "Shift", "D"]], ["Открыть приложение", ["Ctrl", "Alt", "Z"]]];
  const impBar = imp ? `<div class="import-progress"><span>Импортирую…</span><span class="num">${imp.done} из ${imp.total}</span>
    <div class="track"><div class="fill" style="width:${Math.round(imp.done / imp.total * 100)}%"></div></div><span class="num">${Math.round(imp.done / imp.total * 100)}%</span></div>` : "";
  let auto = false;
  if (TAURI?.core) { try { auto = await TAURI.core.invoke("plugin:autostart|is_enabled"); } catch {} }
  el.innerHTML = `
  <div class="head"><div><h1>Настройки</h1><p>Настрой приложение под себя</p></div></div>
  <div class="settings">
    <div class="stack">
      <section class="panel">
        <div class="panel-h"><span class="ico">${ico("user-round")}</span><div><h3>Аккаунт</h3><p>Вход через Telegram-бота</p></div></div>
        <div class="acc"><span class="avatar">${esc(initials(me.name || me.username))}</span>
          <div><b>${esc(me.name || me.username || "Ты")}</b>${me.username ? `<span>@${esc(me.username)}</span>` : ""}</div>
          <span class="status-ok"><i></i>Подключено</span></div>
        <div class="btn-row"><button class="btn accent big" data-s="bot">${ico("send")} Открыть бота в Telegram ${ico("external-link")}</button>
          <button class="btn big" data-s="logout">${ico("log-out")} Выйти</button></div>
      </section>
      <section class="panel">
        <div class="panel-h"><span class="ico">${ico("download")}</span><div><h3>Импорт</h3><p>Перенеси старые завалы в Закрома</p></div></div>
        <div class="list">
          <div class="li"><span class="brand tg">${ico("send")}</span><div class="t"><b>Избранное из Telegram</b>
            <span>Telegram Desktop → Избранное → меню «Ещё» → Экспорт истории чата → формат JSON. Файл result.json отправь боту.</span></div>
            <button class="btn accent" data-s="bot">Открыть бота</button></div>
          ${impBar}
          <div class="li"><span class="brand ig">${ico("instagram")}</span><div class="t"><b>Переписка из Instagram</b>
            <span>Центр аккаунтов → Ваша информация → Скачать информацию → Сообщения, JSON. Архив отправь боту.</span></div>
            <button class="btn" data-s="bot">Открыть бота</button></div>
        </div>
      </section>
      <section class="panel">
        <div class="panel-h"><span class="ico">${ico("trash-2")}</span><div><h3>Корзина</h3><p>Удалённое хранится 30 дней, потом исчезает навсегда</p></div></div>
        <div class="list"><a class="li link" href="#/trash">${ico("archive")}<div class="t"><b>Открыть корзину</b></div>
          <span class="num" style="color:var(--text-2)">${st.trash || 0}</span>${ico("chevron-right", "chev")}</a></div>
      </section>
    </div>
    <div class="stack">
      <section class="panel">
        <div class="panel-h"><span class="ico">${ico("keyboard")}</span><div><h3>Горячие клавиши</h3><p>${TAURI ? "Работают из любой программы" : "Работают в программе для Windows"}</p></div></div>
        <div class="list">${keys.map(([n, k]) => `<div class="li"><div class="t">${n}</div><div class="keys">${k.map(x => `<kbd>${x}</kbd>`).join("")}</div></div>`).join("")}</div>
        ${TAURI ? "" : `<p class="hint">В браузере быстрый поиск открывается по Ctrl+K.</p>`}
      </section>
      <section class="panel">
        <div class="panel-h"><span class="ico">${ico("hash")}</span><div><h3>Теги</h3><p>ИИ ставит их сам, но иногда стоит прибраться</p></div></div>
        <div class="tags">${S.tags.slice(0, 10).map(t => `<a class="chip" href="#/tag/${encodeURIComponent(t.tag)}">#${esc(t.tag)} <span class="n">${t.count}</span></a>`).join("")}</div>
        <div class="list"><button class="li link" data-s="clean">${ico("sparkles")}<div class="t" style="text-align:left"><b>Навести порядок в тегах</b>
          <span>ИИ предложит объединить похожие теги — применишь, если понравится</span></div>${ico("chevron-right", "chev")}</button></div>
        <div id="tagmap"></div>
      </section>
      <section class="panel">
        <div class="panel-h"><span class="ico">${ico("monitor")}</span><div><h3>Приложение</h3><p>${TAURI ? "Программа для Windows" : "Ты в браузере"}</p></div></div>
        <div class="list">
          ${TAURI ? `<div class="li"><div class="t"><b>Запускать вместе с Windows</b><span>Закрома тихо живут в трее и ждут горячую клавишу</span></div>
            <button class="switch ${auto ? "on" : ""}" data-s="auto" role="switch" aria-checked="${auto}" aria-label="Запускать вместе с Windows"></button></div>` :
          `<a class="li link" href="https://github.com/yush4a-ai/zakroma/releases/latest" target="_blank" rel="noopener">${ico("monitor-down")}<div class="t"><b>Скачать программу для Windows</b>
            <span>Горячие клавиши, быстрый поиск поверх всех окон, иконка в трее</span></div>${ico("external-link", "chev")}</a>`}
        </div>
      </section>
    </div>
  </div>`;
  icons();
  el.querySelectorAll("[data-s]").forEach(b => b.onclick = async () => {
    const s = b.dataset.s;
    if (s === "bot") openUrl(botUrl);
    else if (s === "logout") logout();
    else if (s === "auto") {
      const on = !b.classList.contains("on");
      try { await TAURI.core.invoke(on ? "plugin:autostart|enable" : "plugin:autostart|disable"); b.classList.toggle("on", on); b.setAttribute("aria-checked", on); }
      catch { toast("Не получилось изменить автозапуск"); }
    } else if (s === "clean") cleanTags();
  });
}

async function cleanTags() {
  const box = document.getElementById("tagmap");
  box.innerHTML = `<p class="hint">Думаю, что можно объединить…</p>`;
  try {
    const { groups } = await api("/api/tags/clean", { method: "POST" });
    if (!groups.length) { box.innerHTML = `<p class="hint">Теги и так в порядке.</p>`; return; }
    box.innerHTML = `<div class="tagmap">${groups.map(g => `<div class="g">${g.from.map(f => `<span class="tag">#${esc(f)}</span>`).join("")}
      ${ico("arrow-right")}<span class="tag to">#${esc(g.to)}</span></div>`).join("")}</div>
      <div class="btn-row" style="margin-top:12px"><button class="btn accent" id="tmApply">${ico("check")} Объединить</button><button class="btn" id="tmCancel">Отмена</button></div>`;
    icons();
    document.getElementById("tmCancel").onclick = () => box.innerHTML = "";
    document.getElementById("tmApply").onclick = async () => {
      const r = await api("/api/tags/clean/apply", { method: "POST" });
      toast(`Готово, обновлено ${r.updated} ${plural(r.updated, "запись", "записи", "записей")}`);
      await refreshMeta(); renderSettings();
    };
  } catch (e) { box.innerHTML = `<p class="hint">${esc(e.message)}</p>`; }
}

/* ---------- быстрый поиск ---------- */
const P = { items: [], sel: 0 };
function openPalette() {
  if (document.getElementById("palette")) return document.getElementById("pq").focus();
  const wrap = document.createElement("div");
  wrap.className = "palette-scrim"; wrap.id = "palette";
  wrap.innerHTML = `<div class="palette" role="dialog" aria-label="Быстрый поиск">
    <div class="ph"><span class="logo-s">${LOGO}Закрома</span><span class="grow"></span>
      ${IS_QUICK ? `<button class="lnk" id="papp">Открыть приложение</button><span style="color:var(--line-2)">|</span>` : ""}<kbd>Esc</kbd></div>
    <label class="pin">${ico("search")}<input id="pq" placeholder="Что ищем?" autocomplete="off" spellcheck="false"><button class="clear" id="pclear" aria-label="Очистить">${ico("x")}</button></label>
    <div class="res" id="pres"><div class="empty">Начни печатать — поиск понимает смысл</div></div>
    <div class="pf"><kbd>Enter</kbd> — открыть <span style="width:14px"></span><kbd>Ctrl</kbd>+<kbd>C</kbd> — скопировать ссылку
      <span class="grow"></span><kbd>${ico("arrow-up")}</kbd><kbd>${ico("arrow-down")}</kbd> — выбрать</div></div>`;
  document.body.appendChild(wrap);
  icons();
  const inp = document.getElementById("pq");
  inp.focus();
  const run = debounce(async v => {
    if (!v.trim()) { P.items = []; drawPalette(); return; }
    try { P.items = (await api("/api/items?q=" + encodeURIComponent(v.trim()))).items.slice(0, 8); P.sel = 0; drawPalette(); } catch {}
  }, 220);
  inp.oninput = () => run(inp.value);
  document.getElementById("pclear").onclick = () => { inp.value = ""; P.items = []; drawPalette(); inp.focus(); };
  wrap.addEventListener("mousedown", e => { if (e.target === wrap) closePalette(); });
  const pa = document.getElementById("papp"); if (pa) pa.onclick = () => focusMain();
  wrap.addEventListener("keydown", e => {
    if (e.key === "Escape") { e.preventDefault(); closePalette(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); P.sel = Math.min(P.items.length - 1, P.sel + 1); drawPalette(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); P.sel = Math.max(0, P.sel - 1); drawPalette(); }
    else if (e.key === "Enter" && P.items[P.sel]) { e.preventDefault(); pickPalette(P.items[P.sel]); }
    else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "c" && !getSelection().toString() && P.items[P.sel]?.url) { e.preventDefault(); copy(P.items[P.sel].url); }
  });
}
function drawPalette() {
  const res = document.getElementById("pres"); if (!res) return;
  if (!P.items.length) { res.innerHTML = `<div class="empty">${document.getElementById("pq").value.trim() ? "Ничего похожего" : "Начни печатать — поиск понимает смысл"}</div>`; return; }
  res.innerHTML = P.items.map((it, i) => {
    const k = KIND[it.kind] || KIND.link;
    return `<div class="hit ${i === P.sel ? "sel" : ""}" data-i="${i}">
      ${it.image ? `<div class="thumb"><img src="${esc(it.image)}" alt="" referrerpolicy="no-referrer" onerror="this.parentNode.classList.add('empty');this.remove()"></div>` : `<div class="thumb empty">${ico(k.icon)}</div>`}
      <div style="min-width:0"><h4>${esc(it.title)}</h4><p>${highlight(it.match || it.summary || "", it.highlight)}</p>
        <div class="tags">${it.tags.slice(0, 2).map(t => `<span class="tag">#${esc(t)}</span>`).join("")}</div></div>
      <span class="d">${fmtDate(it.created_at)}</span></div>`;
  }).join("");
  icons();
  res.querySelectorAll(".hit").forEach(h => {
    h.onmouseenter = () => { P.sel = +h.dataset.i; res.querySelectorAll(".hit").forEach(x => x.classList.toggle("sel", x === h)); };
    h.onclick = () => pickPalette(P.items[+h.dataset.i]);
  });
  res.querySelector(".sel")?.scrollIntoView({ block: "nearest" });
}
async function pickPalette(it) {
  if (IS_QUICK && TAURI) {
    try { await TAURI.event.emitTo("main", "open-item", it.id); } catch {}
    focusMain();
  } else { closePalette(); openItem(it.id); }
}
async function focusMain() {
  if (!TAURI) return;
  try {
    const W = TAURI.window, main = await W.Window.getByLabel("main");
    await main.show(); await main.unminimize(); await main.setFocus();
    await W.getCurrentWindow().hide();
  } catch {}
}
function closePalette() {
  if (IS_QUICK && TAURI) { TAURI.window.getCurrentWindow().hide().catch(() => {}); return; }
  document.getElementById("palette")?.remove();
}
document.addEventListener("keydown", e => {
  if (IS_QUICK) return;
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openPalette(); }
  if (e.key === "Escape") { if (document.getElementById("menu")) closeMenu(); else if (document.getElementById("palette")) closePalette(); else if (document.getElementById("drawer")) closeDrawer(); }
  if (e.key === "/" && !e.target.matches("input, textarea")) { e.preventDefault(); document.getElementById("q")?.focus(); }
});

/* ---------- программа для Windows ---------- */
function tauriHooks() {
  if (!TAURI?.event) return;
  const L = TAURI.event.listen;
  if (IS_QUICK) {
    L("quick-open", () => { const i = document.getElementById("pq"); if (i) { i.select(); i.focus(); } });
    return;
  }
  L("open-item", e => openItem(e.payload));
  L("open-triage", () => go("#/new"));
  L("quick-save", async e => {
    const text = String(e.payload || "").trim();
    if (!text) return toast("В буфере обмена пусто — скопируй ссылку или текст");
    toast("Сохраняю в Закрома…");
    try {
      const r = await api("/api/items", { method: "POST", body: { text } });
      if (r.saved.length) toast(`Сохранено: ${r.saved[0].title}`);
      else if (r.duplicates.length) toast("Это уже есть в Закромах");
      refreshMeta(); if (["all", "new"].includes(S.route.view)) route();
    } catch (err) { toast(err.message); }
  });
}

/* ---------- запуск ---------- */
async function route() {
  closeMenu();
  const prev = S.route;
  S.route = parseRoute();
  const r = S.route;
  if (prev.view !== r.view || prev.tag !== r.tag || prev.kindPath !== r.kindPath) S.kind = "";
  document.getElementById("side")?.classList.remove("open");
  const q = document.getElementById("q");
  if (q && r.view !== "search" && document.activeElement !== q) { q.value = ""; document.getElementById("qclear").hidden = true; }
  if (q && r.view === "search" && !q.value) { q.value = r.q; document.getElementById("qclear").hidden = false; }
  renderSide();
  if (r.view === "settings") renderSettings();
  else if (r.view === "new") renderTriage();
  else if (VIEWS[r.view] || ["tag", "kind", "search"].includes(r.view)) loadList();
  else go("#/all");
  if (r.item) openItem(r.item);
}

async function boot() {
  stopLoginPoll();
  if (IS_QUICK) document.body.classList.add("quick");
  if (!S.token) {
    if (IS_QUICK) { $app.innerHTML = ""; openPalette(); document.getElementById("pres").innerHTML = `<div class="empty">Сначала войди в главном окне Закромов</div>`; return; }
    return renderLogin();
  }
  try { S.me = await api("/api/me"); } catch (e) { if (e.message === "auth") return; }
  tauriHooks();
  if (IS_QUICK) { $app.innerHTML = ""; openPalette(); return; }
  shell();
  await refreshMeta();
  window.onhashchange = route;
  route();
  setInterval(refreshMeta, 60000);
}

boot();
})();
