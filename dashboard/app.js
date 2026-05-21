(() => {
  const WS_URL = "ws://localhost:8000/ws";
  const MAX_ALERTS = 8;
  const MAX_LOGS = 50;

  const BASE_SUBSYSTEMS = [
    { id: "nav", name: "Navigation", match: "navigation", conf: 99.8 },
    { id: "comm", name: "Communications", match: "communications", conf: 99.8 },
    { id: "eng1", name: "Propulsion ENG 1", match: "engine", conf: 97.1 },
    { id: "eng2", name: "Propulsion ENG 2", match: "engine", conf: 98.5 },
    { id: "fuel", name: "Fuel Management", match: "fuel", conf: 91.0 },
    { id: "hyd", name: "Hydraulics", match: "hydraulics", conf: 99.4 },
    { id: "struc", name: "Structural Health", match: "structural", conf: 96.7 },
    { id: "elec", name: "Electrical Bus", match: "electrical", conf: 99.9 },
  ];

  const els = {
    utc: document.getElementById("utc-time"),
    tabCockpit: document.getElementById("tab-cockpit"),
    tabMaint: document.getElementById("tab-maint"),
    cockpitPanel: document.getElementById("cockpit-panel"),
    maintPanel: document.getElementById("maint-panel"),
    subsystemList: document.getElementById("subsystem-list"),
    anomalyPill: document.getElementById("anomaly-pill"),
    alertCount: document.getElementById("alert-count"),
    alertsList: document.getElementById("alerts-list"),
    maintLogBody: document.getElementById("maint-log-body"),
    maintCount: document.getElementById("maint-count"),
    statOpen: document.getElementById("stat-open"),
    statEvents: document.getElementById("stat-events"),
  };

  let alerts = [];
  let maintLogs = [];
  let openAlertKey = null;

  function formatCoord(lat, lon) {
    if (typeof lat !== "number" || typeof lon !== "number") return "-";
    return `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
  }

  function formatTs(ts) {
    if (!ts) return "";
    const parts = ts.split("T");
    if (parts.length < 2) return ts;
    return parts[1].replace("Z", "Z");
  }

  function alertKey(msg) {
    return `${msg.type || ""}|${msg.subsystem || ""}`;
  }

  function buildAlertCard(msg) {
    const gps = msg.gps || {};
    const heading = msg.heading || {};
    const scores = msg.scores || {};
    const detail = [
      { k: "GPS Observed", v: formatCoord(gps.observed_lat_deg, gps.observed_lon_deg) },
      { k: "GPS Expected", v: formatCoord(gps.expected_lat_deg, gps.expected_lon_deg) },
      { k: "Divergence", v: gps.divergence_nm != null ? `${gps.divergence_nm.toFixed(2)} NM` : "-", flag: true },
      { k: "Heading Target", v: heading.target_heading_deg != null ? `${heading.target_heading_deg.toFixed(1)} deg` : "-" },
      { k: "Heading Error", v: heading.error_deg != null ? `${heading.error_deg.toFixed(1)} deg` : "-", flag: true },
      { k: "Mahalanobis", v: scores.mahalanobis != null ? scores.mahalanobis.toFixed(2) : "-" },
      { k: "CUSUM", v: scores.cusum != null ? scores.cusum.toFixed(2) : "-" },
      { k: "GRU Error", v: scores.gru_reconstruction_error != null ? scores.gru_reconstruction_error.toFixed(4) : "-" },
    ];
    const qrh = msg.type === "SPOOF"
      ? "NAV 34.10 - Cross-check IRS / FMS / Radio Navigation"
      : msg.type === "DRIFT"
        ? "MAINT 02.10 - Schedule sensor inspection"
        : "SYS 00.10 - Monitor and cross-check";

    return {
      key: alertKey(msg),
      sev: msg.severity || "ALERT",
      sys: msg.subsystem || "System",
      title: msg.message || msg.type || "Anomaly",
      summary: msg.detail || msg.message || "Anomaly detected",
      detail,
      reasoning: `Scores: mahal=${scores.mahalanobis?.toFixed(2) ?? "-"}, cusum=${scores.cusum?.toFixed(2) ?? "-"}, gru=${scores.gru_reconstruction_error?.toFixed(4) ?? "-"}.`,
      qrh,
      ts: formatTs(msg.timestamp),
      subsystemKey: String(msg.subsystem || "").toLowerCase(),
    };
  }

  function buildLog(msg) {
    return {
      key: alertKey(msg),
      ts: msg.timestamp || "",
      sys: msg.subsystem || "System",
      desc: msg.message || msg.detail || "Maintenance event",
      sev: msg.severity === "MAINTENANCE" ? 2 : 1,
      flight: "LIVE",
    };
  }

  function renderSubsystems() {
    const rows = BASE_SUBSYSTEMS.map((s) => {
      const hasAlert = alerts.some((a) => a.subsystemKey.includes(s.match));
      const warn = hasAlert;
      return `
        <div class="vb-sub-row ${warn ? "warn" : ""}">
          <div class="vb-dot ${warn ? "warn" : ""}"></div>
          <div class="vb-sub-name">${s.name}</div>
          <div class="vb-sub-conf">${s.conf.toFixed(1)}%</div>
        </div>
      `;
    }).join("");
    els.subsystemList.innerHTML = rows;

    if (alerts.length) {
      els.anomalyPill.textContent = `${alerts.length} advisory`;
      els.anomalyPill.classList.remove("ok");
    } else {
      els.anomalyPill.textContent = "all nominal";
      els.anomalyPill.classList.add("ok");
    }
  }

  function renderAlerts() {
    if (!alerts.length) {
      els.alertsList.innerHTML = `
        <div class="vb-alert">
          <div class="vb-alert-hd">
            <span class="vb-badge">OK</span>
            <span class="vb-alert-title">No active anomalies</span>
            <span class="vb-alert-ts"></span>
          </div>
        </div>
      `;
      els.alertCount.textContent = "0 advisory";
      return;
    }

    if (!openAlertKey) {
      openAlertKey = alerts[0].key;
    }

    const cards = alerts.map((a) => {
      const open = a.key === openAlertKey;
      const dlChildren = a.detail.map((m) => {
        const warnClass = m.flag ? "warn" : "";
        return `<dt>${m.k}</dt><dd class="${warnClass}">${m.v}</dd>`;
      }).join("");

      return `
        <div class="vb-alert" data-key="${a.key}">
          <div class="vb-alert-hd">
            <span class="vb-badge">${a.sev}</span>
            <span class="vb-sys-tag">${a.sys}</span>
            <span class="vb-alert-title">${a.title}</span>
            <span class="vb-alert-ts">${a.ts}</span>
            <span class="vb-chev ${open ? "open" : ""}">
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                <path d="M3.5 5L7 8.5L10.5 5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </span>
          </div>
          <div class="vb-alert-bd ${open ? "" : "closed"}">
            <div>
              <div class="vb-block-lbl">Summary</div>
              <div class="vb-summary">${a.summary}</div>

              <div class="vb-block-lbl">ML Reasoning</div>
              <div class="vb-reasoning">${a.reasoning}</div>

              <span class="vb-qrh">${a.qrh}</span>
            </div>

            <div>
              <div class="vb-block-lbl">Twin Comparison</div>
              <div class="vb-twincard">
                <dl class="vb-metrics">${dlChildren}</dl>
              </div>
            </div>
          </div>
        </div>
      `;
    }).join("");

    els.alertsList.innerHTML = cards;
    els.alertCount.textContent = `${alerts.length} advisory`;
  }

  function renderMaintenance() {
    els.statOpen.textContent = String(alerts.length);
    els.statEvents.textContent = String(maintLogs.length);
    if (els.maintCount) {
      els.maintCount.textContent = String(maintLogs.length);
    }

    if (!maintLogs.length) {
      els.maintLogBody.innerHTML = `
        <tr>
          <td class="ts">-</td>
          <td class="sys">-</td>
          <td>No maintenance events yet</td>
          <td><span class="vb-sev vb-sev-1">Info</span></td>
          <td class="fl">-</td>
        </tr>
      `;
      return;
    }

    const rows = maintLogs.map((l) => {
      const sevLabel = l.sev === 1 ? "Info" : "Advisory";
      return `
        <tr>
          <td class="ts">${l.ts}</td>
          <td class="sys">${l.sys}</td>
          <td>${l.desc}</td>
          <td><span class="vb-sev vb-sev-${l.sev}">${sevLabel}</span></td>
          <td class="fl">${l.flight}</td>
        </tr>
      `;
    }).join("");

    els.maintLogBody.innerHTML = rows;
  }

  function updateAll() {
    renderSubsystems();
    renderAlerts();
    renderMaintenance();
  }

  function setTab(tab) {
    if (tab === "maint") {
      els.tabMaint.classList.add("on");
      els.tabCockpit.classList.remove("on");
      els.maintPanel.classList.remove("is-hidden");
      els.cockpitPanel.classList.add("is-hidden");
    } else {
      els.tabCockpit.classList.add("on");
      els.tabMaint.classList.remove("on");
      els.cockpitPanel.classList.remove("is-hidden");
      els.maintPanel.classList.add("is-hidden");
    }
  }

  function connectWs() {
    const ws = new WebSocket(WS_URL);
    ws.onmessage = (event) => {
      let msg;
      try {
        msg = JSON.parse(event.data);
      } catch (e) {
        return;
      }
      if (!msg || msg.event !== "ALERT") return;

      const isMaintenance = msg.severity === "MAINTENANCE" || msg.type === "DRIFT";
      if (isMaintenance) {
        const log = buildLog(msg);
        if (!maintLogs.find((l) => l.key === log.key)) {
          maintLogs.unshift(log);
          if (maintLogs.length > MAX_LOGS) maintLogs = maintLogs.slice(0, MAX_LOGS);
        }
        renderMaintenance();
        return;
      }

      const card = buildAlertCard(msg);
      const idx = alerts.findIndex((a) => a.key === card.key);
      if (idx >= 0) {
        alerts[idx] = card;
      } else {
        alerts.unshift(card);
        if (alerts.length > MAX_ALERTS) alerts = alerts.slice(0, MAX_ALERTS);
      }

      updateAll();
    };

    ws.onclose = () => {
      setTimeout(connectWs, 2000);
    };
  }

  function tickClock() {
    const now = new Date();
    const h = String(now.getUTCHours()).padStart(2, "0");
    const m = String(now.getUTCMinutes()).padStart(2, "0");
    const s = String(now.getUTCSeconds()).padStart(2, "0");
    if (els.utc) {
      els.utc.textContent = `${h}:${m}:${s}Z`;
    }
  }

  els.tabCockpit.addEventListener("click", () => setTab("cockpit"));
  els.tabMaint.addEventListener("click", () => setTab("maint"));

  els.alertsList.addEventListener("click", (evt) => {
    const header = evt.target.closest(".vb-alert-hd");
    if (!header) return;
    const card = header.closest(".vb-alert");
    if (!card) return;
    const key = card.getAttribute("data-key");
    openAlertKey = openAlertKey === key ? null : key;
    renderAlerts();
  });

  tickClock();
  setInterval(tickClock, 1000);
  setTab("cockpit");
  updateAll();
  connectWs();
})();
