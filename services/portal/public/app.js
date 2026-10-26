"use strict";

// 24x24 stroke icons, all inline so the page makes no external requests.
const ICONS = {
  chart: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
  database: '<ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/>',
  flow: '<circle cx="5" cy="6" r="2"/><circle cx="19" cy="6" r="2"/><circle cx="12" cy="18" r="2"/><path d="M7 6h10M6 8l5 8M18 8l-5 8"/>',
  users: '<circle cx="9" cy="8" r="3"/><path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6M16 5a3 3 0 0 1 0 6M18 14c1.8.7 3 2.5 3 5"/>',
  route: '<circle cx="6" cy="6" r="2"/><circle cx="18" cy="18" r="2"/><path d="M8 6h6a3 3 0 0 1 0 6h-4a3 3 0 0 0 0 6h6"/>',
  key: '<circle cx="8" cy="15" r="4"/><path d="M11 12l9-9M16 7l3 3"/>',
  home: '<path d="M4 11l8-7 8 7M6 10v10h12V10"/>',
};

// portal.srdp.localhost -> <subdomain>.srdp.localhost, so the same image works on any domain.
// With `path` a tool opens straight on a deep page.
function serviceUrl(service) {
  const labels = location.hostname.split(".");
  labels[0] = service.subdomain;
  return `${location.protocol}//${labels.join(".")}${location.port ? ":" + location.port : ""}${service.path || "/"}`;
}

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "text") node.textContent = value;
    else node.setAttribute(key, value);
  }
  for (const child of children) node.append(child);
  return node;
}

function iconSvg(name) {
  return `<svg viewBox="0 0 24 24">${ICONS[name] || ICONS.route}</svg>`;
}

function icon(name) {
  const wrap = el("span", { class: "card-icon", "aria-hidden": "true" });
  wrap.innerHTML = iconSvg(name);
  return wrap;
}

// Tools that refuse to be framed (X-Frame-Options, frame-ancestors) or are not a page open in a new tab.
const isEmbeddable = (service) => service.embed !== false;

function openLink(service, attrs, text) {
  const link = el("a", attrs, text ? [document.createTextNode(text)] : []);
  if (isEmbeddable(service)) {
    link.href = `#${service.id}`;
  } else {
    link.href = serviceUrl(service);
    link.target = "_blank";
    link.rel = "noopener";
  }
  return link;
}

function card(service) {
  const open = openLink(service, { class: "btn", "aria-label": `Open ${service.name}` }, "Open");
  const head = el("div", { class: "card-head" }, [icon(service.icon), el("h3", { text: service.name })]);
  if (!isEmbeddable(service)) head.append(el("span", { class: "badge", text: "New tab" }));
  return el("article", { class: "card" }, [
    head,
    el("p", { text: service.description }),
    el("div", { class: "card-foot" }, [open]),
  ]);
}

function groupedByCategory(config) {
  return config.categories
    .map((category) => ({ category, services: config.services.filter((s) => s.category === category.id) }))
    .filter((group) => group.services.length);
}

function renderHome(config) {
  const groups = document.getElementById("groups");
  for (const { category, services } of groupedByCategory(config)) {
    const heading = el("h2", { text: category.name, id: `cat-${category.id}` });
    const cards = el("div", { class: "cards" }, services.map(card));
    groups.append(el("section", { class: "group", "aria-labelledby": heading.id }, [heading, cards]));
  }
  document.getElementById("empty").hidden = config.services.length > 0;
}

function renderSidebar(config) {
  const nav = document.getElementById("sidebar");
  const withIcon = (name, label) => {
    const wrap = el("span", { "aria-hidden": "true" });
    wrap.innerHTML = iconSvg(name);
    return [wrap, document.createTextNode(label)];
  };
  nav.append(el("a", { href: "#", "data-id": "" }, withIcon("home", "Overview")));
  for (const { category, services } of groupedByCategory(config)) {
    nav.append(el("h2", { text: category.name }));
    for (const service of services) {
      const link = openLink(service, { "data-id": service.id });
      link.replaceChildren(...withIcon(service.icon, service.name));
      if (!isEmbeddable(service)) link.append(el("span", { class: "ext", "aria-label": "opens in a new tab", text: "↗" }));
      nav.append(link);
    }
  }
}

// A tool that does not load in the frame gets a message with options instead of an empty area.
// An unreachable or silent tool is caught here. A tool that refuses framing cannot be told apart from
// the browser, so those carry `"embed": false` in services.json.
const FRAME_TIMEOUT_MS = 20000;
let frameTimer = null;
let fallbackReason = null; // "slow" or "unreachable"

function showFrameFallback(service, url, reason) {
  clearTimeout(frameTimer);
  fallbackReason = reason;
  const messages = {
    unreachable: `${service.name} is not reachable right now. Try again in a few minutes.`,
    slow: `${service.name} is not responding. The page may still load, or the tool has a problem.`,
  };
  document.getElementById("viewer-fallback-title").textContent = `${service.name} cannot be shown here`;
  document.getElementById("viewer-fallback-text").textContent = messages[reason];
  document.getElementById("viewer-fallback-open").href = url;
  document.getElementById("viewer-fallback").hidden = false;
  document.getElementById("frame").hidden = true;
}

function loadFrame(service, url) {
  const frame = document.getElementById("frame");
  clearTimeout(frameTimer);
  document.getElementById("viewer-fallback").hidden = true;
  frame.hidden = false;
  fallbackReason = null;
  frame.onload = () => {
    if (frame.dataset.id !== service.id) return;
    clearTimeout(frameTimer);
    // A tool that loads after the timeout is shown after all. "unreachable" stays: a browser error page also fires load.
    if (fallbackReason === "slow") {
      fallbackReason = null;
      document.getElementById("viewer-fallback").hidden = true;
      frame.hidden = false;
    }
  };
  frame.src = url;
  frameTimer = setTimeout(() => showFrameFallback(service, url, "slow"), FRAME_TIMEOUT_MS);
  // The frame itself cannot tell "unreachable" from a loaded page: a browser error page also fires load,
  // and the page cannot look inside a frame of another origin. So the probe may also fire after a
  // successful load, and then it is right: the tool stopped answering. Do not guard it with a "loaded" flag.
  probe(url).then((reachable) => {
    if (!reachable && frame.dataset.id === service.id) showFrameFallback(service, url, "unreachable");
  });
}

// no-cors gives an opaque answer as soon as the server responds; an error means unreachable.
// One failed request can be a reset on a busy connection, so only two in a row count.
async function probe(url) {
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      await fetch(url, { mode: "no-cors", cache: "no-store" });
      return true;
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
  }
  return false;
}

// Router: #<id> shows that tool in the frame; no hash shows the overview.
function route(config) {
  let id = "";
  try {
    id = decodeURIComponent(location.hash.slice(1));
  } catch {
    // A malformed fragment such as #% shows the overview.
  }
  const service = config.services.find((s) => s.id === id && isEmbeddable(s));
  const frame = document.getElementById("frame");

  if (service) {
    const url = serviceUrl(service);
    if (frame.dataset.id !== service.id) {
      frame.dataset.id = service.id;
      frame.title = service.name;
      loadFrame(service, url);
    }
    document.getElementById("viewer-title").textContent = service.name;
    document.getElementById("viewer-external").href = url;
    document.title = `${service.name} · SRDP`;
  } else {
    delete frame.dataset.id;
    clearTimeout(frameTimer);
    frame.onload = null;
    frame.src = "about:blank"; // also stops websockets of the previous tool
    document.title = "SRDP portal";
  }
  document.getElementById("home").hidden = Boolean(service);
  document.getElementById("viewer").hidden = !service;

  const active = service ? service.id : "";
  for (const link of document.querySelectorAll("#sidebar a[data-id]")) {
    if (link.dataset.id === active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
}

// An expired session sends the frame to the Zitadel login, which refuses framing. So the portal checks
// the session itself now and then and reloads the whole page on a 401, which leads to the login.
async function watchSession() {
  try {
    const response = await fetch("/oauth2/auth", { cache: "no-store" });
    if (response.status === 401) location.reload();
  } catch {
    // Network error: try again later.
  }
}

// The portal sits behind the same sign-in as every tool (oauth2-proxy), so a user is always known.
// If the name cannot be read, the page simply stays anonymous.
async function showUser() {
  try {
    const response = await fetch("/oauth2/userinfo", { cache: "no-store" });
    if (!response.ok) return;
    const info = await response.json();
    const name = info.preferredUsername || info.email || info.user || "";
    if (!name) return;
    const user = document.getElementById("user");
    user.textContent = name;
    user.hidden = false;
    document.getElementById("welcome").textContent = `Welcome, ${name}`;
  } catch {
    // Network error: keep the generic welcome.
  }
}

document.getElementById("viewer-retry").addEventListener("click", () => {
  const frame = document.getElementById("frame");
  delete frame.dataset.id; // forces a fresh load through route()
  window.dispatchEvent(new HashChangeEvent("hashchange"));
});

async function main() {
  showUser();
  setInterval(watchSession, 60000);
  let config;
  try {
    const response = await fetch("/services.json", { cache: "no-cache" });
    if (!response.ok) throw new Error(response.status);
    config = await response.json();
  } catch {
    document.getElementById("empty").hidden = false;
    return;
  }
  renderHome(config);
  renderSidebar(config);
  window.addEventListener("hashchange", () => route(config));
  route(config);
}
main();
