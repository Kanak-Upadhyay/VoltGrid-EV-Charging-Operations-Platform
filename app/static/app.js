const state = {
  token: sessionStorage.getItem("vg_token") || "",
  role: sessionStorage.getItem("vg_role") || "",
  name: sessionStorage.getItem("vg_name") || "",
  sites: [],
  session: null,
  history: [],
  analytics: null,
  error: "",
  city: "Mumbai",
};

const cities = {
  Mumbai: { lat: 19.09, lng: 72.87, radius_km: 12 },
  Delhi: { lat: 28.6315, lng: 77.2167, radius_km: 8 },
  Bengaluru: { lat: 12.9784, lng: 77.6408, radius_km: 8 },
  Hyderabad: { lat: 17.4474, lng: 78.3762, radius_km: 8 },
};

const accounts = [
  ["driver@voltgrid.local", "Driver"],
  ["operator@voltgrid.local", "Operator"],
  ["admin@voltgrid.local", "Admin"],
];

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[ch]));
}

function money(value) {
  return `INR ${Number(value).toFixed(2)}`;
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body) headers["Content-Type"] = "application/json";
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const response = await fetch(path, { ...options, headers });
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (response.status === 401 && path !== "/auth/login") {
    logout();
    throw new Error("Session expired");
  }
  if (!response.ok) {
    throw new Error(data?.message || "Request failed");
  }
  return data;
}

function logout() {
  state.token = "";
  state.role = "";
  state.name = "";
  sessionStorage.clear();
  render();
}

async function login(email, password) {
  state.error = "";
  const data = await api("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  state.token = data.access_token;
  state.role = data.role;
  state.name = data.full_name;
  sessionStorage.setItem("vg_token", state.token);
  sessionStorage.setItem("vg_role", state.role);
  sessionStorage.setItem("vg_name", state.name);
  await refresh();
}

async function refresh() {
  const city = cities[state.city];
  const params = new URLSearchParams({
    lat: city.lat,
    lng: city.lng,
    radius_km: city.radius_km,
  });
  state.sites = await api(`/sites/nearby?${params}`);
  state.history = await api("/sessions?limit=8");
  state.session = state.history.find((item) => item.status === "active") || state.session;
  if (state.role === "operator" || state.role === "admin") {
    state.analytics = await api("/analytics/overview");
  } else {
    state.analytics = null;
  }
  render();
}

async function startCharge(connectorId) {
  state.error = "";
  state.session = await api("/sessions", {
    method: "POST",
    body: JSON.stringify({ connector_id: connectorId }),
  });
  await refresh();
}

async function stopCharge() {
  const duration = Number(document.getElementById("duration").value);
  const idle = document.getElementById("idle").checked;
  const body = { simulate: true, duration_minutes: duration };
  if (idle) {
    body.unplugged_at = new Date(Date.now() + 20 * 60 * 1000).toISOString();
  }
  state.session = await api(`/sessions/${state.session.id}/stop`, {
    method: "POST",
    body: JSON.stringify(body),
  });
  await refresh();
}

async function pay(invoiceId) {
  await api(`/invoices/${invoiceId}/pay`, {
    method: "POST",
    body: JSON.stringify({ method: "upi", reference: `UPI${Date.now()}` }),
  });
  await refresh();
}

function renderLogin() {
  document.body.dataset.screen = "login";
  document.getElementById("who").textContent = "India network";
  document.getElementById("app").innerHTML = `
    <section class="hero">
      <div class="copy">
        <p class="eyebrow">Electric vehicle charging</p>
        <h1>Plug in.<br>Get moving.</h1>
        <p class="lede">Find a fast charger, start a session, and walk away with a GST invoice. Mumbai to Hyderabad, on one network.</p>
        <ul class="statrow">
          <li><strong>5</strong><span>live sites</span></li>
          <li><strong>120 kW</strong><span>fastest plug</span></li>
          <li><strong>18%</strong><span>GST on the bill</span></li>
        </ul>
      </div>
      <form class="panel" id="login-form">
        <p class="eyebrow">Sign in</p>
        <h2>Open the console</h2>
        <label for="email">Email</label>
        <input id="email" type="email" value="driver@voltgrid.local" required />
        <label for="password">Password</label>
        <input id="password" type="password" value="VoltGrid#2026" required />
        <div class="row"><button class="primary" type="submit">Enter VoltGrid</button></div>
        <div class="accounts">
          ${accounts.map(([email, label]) => `<button type="button" class="chip" data-email="${email}">${label}</button>`).join("")}
        </div>
        <p class="meta">Demo password: VoltGrid#2026</p>
        ${state.error ? `<div class="banner">${esc(state.error)}</div>` : ""}
      </form>
    </section>`;
  document.getElementById("login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await login(document.getElementById("email").value, document.getElementById("password").value);
    } catch (error) {
      state.error = error.message;
      render();
    }
  });
  document.querySelectorAll("[data-email]").forEach((button) => {
    button.addEventListener("click", async () => {
      try {
        await login(button.dataset.email, "VoltGrid#2026");
      } catch (error) {
        state.error = error.message;
        render();
      }
    });
  });
}

function plugs(site) {
  return site.chargers.map((charger) => charger.connectors.map((connector) => {
    const width = Math.max(12, Math.min(100, (Number(connector.power_kw) / 120) * 100));
    const ready = connector.status === "available" && charger.status === "online";
    return `
      <div class="plug">
        <div>
          <div class="plug-name"><span class="dot ${esc(connector.status)}"></span>${esc(connector.connector_type)} · ${Number(connector.power_kw)} kW</div>
          <div class="plug-sub">${esc(charger.serial_number)} · ${esc(charger.status)}</div>
          <div class="bar"><span style="--w:${width}%"></span></div>
        </div>
        ${ready ? `<button class="primary" data-start="${connector.id}" type="button">Start</button>` : ""}
      </div>`;
  }).join("")).join("");
}

function invoiceBlock(session) {
  const invoice = session?.invoice;
  if (!invoice) return "";
  const paid = invoice.status === "paid";
  return `
    <article class="receipt">
      <p class="eyebrow" style="color:#9a6b16">Latest invoice ${paid ? `<span class="paid">PAID</span>` : ""}</p>
      <div class="bill">
        <span>Energy</span><strong>${money(invoice.energy_amount)}</strong>
        <span>Time</span><strong>${money(invoice.time_amount)}</strong>
        <span>Idle fee</span><strong>${money(invoice.idle_amount)}</strong>
        <span>GST</span><strong>${money(invoice.gst_amount)}</strong>
      </div>
      <p class="total">${money(invoice.total)}</p>
      <p>${esc(session.energy_kwh)} kWh · ${session.estimated ? "estimated meter" : "meter reading"}</p>
      ${invoice.status === "issued" ? `<button class="primary" id="pay" type="button">Pay with UPI</button>` : ""}
    </article>`;
}

function renderApp() {
  document.body.dataset.screen = "app";
  document.getElementById("who").textContent = `${state.name} · ${state.role}`;
  const active = state.session && state.session.status === "active" ? state.session : null;
  const latestBill = state.history.find((item) => item.invoice);
  document.getElementById("app").innerHTML = `
    <section class="dash">
      <aside class="card">
        <p class="eyebrow">${esc(state.city)}</p>
        <h2>Find a plug</h2>
        <div class="cities">
          ${Object.keys(cities).map((city) => `<button type="button" class="city ${city === state.city ? "on" : ""}" data-city="${city}">${city}</button>`).join("")}
        </div>
        ${state.analytics ? `<div class="network">
          <p class="eyebrow">Network</p>
          <p class="total">${money(state.analytics.revenue_inr)}</p>
          <p class="meta">${esc(state.analytics.sessions)} sessions · ${esc(state.analytics.energy_kwh)} kWh</p>
        </div>` : ""}
        <div class="row"><button class="ghost" id="logout" type="button">Sign out</button></div>
        ${state.error ? `<div class="banner">${esc(state.error)}</div>` : ""}
      </aside>
      <section>
        ${active ? `<article class="card live-card rise">
          <div class="live">
            <div class="orb"></div>
            <div>
              <p class="eyebrow">Live session</p>
              <h2>Charging now</h2>
              <p class="meta">Connector ${esc(active.connector_id)}</p>
            </div>
          </div>
          <label for="duration">Demo length</label>
          <select id="duration">
            <option value="20">20 minutes</option>
            <option value="40" selected>40 minutes</option>
            <option value="60">60 minutes</option>
          </select>
          <label class="check"><input id="idle" type="checkbox" /> Cable stayed in for 20 minutes after stop</label>
          <div class="row"><button class="danger" id="stop" type="button">Stop and bill</button></div>
        </article>` : `<div class="sites">${state.sites.map((site, index) => `
          <article class="card site-card rise" style="animation-delay:${index * 90}ms">
            <p class="eyebrow">${esc(site.distance_km)} km away</p>
            <h3>${esc(site.name)}</h3>
            <p class="meta">${esc(site.address)}</p>
            ${plugs(site)}
          </article>`).join("") || `<article class="card"><p class="meta">No sites in this radius.</p></article>`}</div>`}
        ${invoiceBlock(active ? null : latestBill) || `<p class="meta">Stop a charge to issue an invoice.</p>`}
      </section>
    </section>`;

  document.getElementById("logout").addEventListener("click", logout);
  document.querySelectorAll("[data-city]").forEach((button) => {
    button.addEventListener("click", async () => {
      state.city = button.dataset.city;
      state.error = "";
      try { await refresh(); } catch (error) { state.error = error.message; render(); }
    });
  });
  document.querySelectorAll("[data-start]").forEach((button) => {
    button.addEventListener("click", async () => {
      try { await startCharge(Number(button.dataset.start)); }
      catch (error) { state.error = error.message; render(); }
    });
  });
  const stop = document.getElementById("stop");
  if (stop) {
    stop.addEventListener("click", async () => {
      try { await stopCharge(); }
      catch (error) { state.error = error.message; render(); }
    });
  }
  const payButton = document.getElementById("pay");
  if (payButton && latestBill?.invoice) {
    payButton.addEventListener("click", async () => {
      try { await pay(latestBill.invoice.id); }
      catch (error) { state.error = error.message; render(); }
    });
  }
}

function render() {
  if (!state.token) renderLogin();
  else renderApp();
}

render();
if (state.token) {
  refresh().catch((error) => {
    state.error = error.message;
    render();
  });
}
