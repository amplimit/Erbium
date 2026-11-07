"use strict";

(() => {
  const NOTIFICATION_LABELS = {
    queued: "My job joins the queue",
    started: "My session starts",
    ending: "My session is about to end",
    finished: "My session reaches its time limit",
    admin: "An administrator stops or removes my job"
  };
  const INSTALL_COMMANDS = {
    windows: "winget install --id Cloudflare.cloudflared",
    macos: "brew install cloudflared",
    linux: "# Debian or Ubuntu; see Cloudflare's docs for other distributions\ncurl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb -o cloudflared.deb\nsudo dpkg -i cloudflared.deb"
  };

  const $ = (id) => document.getElementById(id);
  const esc = erbium.escapeHtml;
  const icon = erbium.icon;
  const state = { me: null, status: null, session: null, telemetry: null, revealed: false, os: detectOs() };
  const TELEMETRY_INTERVAL_MS = 1000;
  const host = erbium.nodeHost() || "<node-host>";
  const jupyterHost = erbium.nodeHost() ? `jupyter-${erbium.nodeHost()}` : null;

  function detectOs() {
    const platform = (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || "";
    if (/mac/i.test(platform)) {
      return "macos";
    }
    if (/linux|x11/i.test(platform)) {
      return "linux";
    }
    return "windows";
  }

  function sshCommand() {
    return `ssh -o ProxyCommand="cloudflared access ssh --hostname %h" access@${host}`;
  }

  function renderConnect() {
    document.querySelectorAll(".os-tabs .tab").forEach((tab) => {
      tab.setAttribute("aria-pressed", String(tab.dataset.os === state.os));
    });
    $("installCommand").innerHTML = erbium.codeBlock(INSTALL_COMMANDS[state.os], "install command");
    $("sshConfig").innerHTML = erbium.codeBlock(
      `Host ${host}\n    User access\n    ProxyCommand cloudflared access ssh --hostname %h`, "SSH config");
    $("sshCommand").innerHTML = erbium.codeBlock(`ssh ${host}`, "SSH command");
    $("scpUpload").innerHTML = erbium.codeBlock(`scp ./data.zip ${host}:/workspace/output/`, "upload command");
    $("scpDownload").innerHTML = erbium.codeBlock(`scp -r ${host}:/workspace/output/results ./`, "download command");
  }

  function renderProfile() {
    const me = state.me;
    $("adminLink").hidden = !me.is_admin;
    $("profileEmail").value = me.email || "";
    $("profileKeys").value = me.public_keys.join("\n");
    $("notificationChecks").innerHTML = Object.entries(NOTIFICATION_LABELS).map(([kind, label]) => `
      <label class="check">
        <input type="checkbox" data-kind="${kind}" ${me.notifications[kind] ? "checked" : ""} />
        <span>${esc(label)}</span>
      </label>
    `).join("");
    $("joinLimits").textContent = `Up to ${erbium.formatHours(me.limits.max_run_time_hrs)} per job, ` +
      `${me.limits.max_jobs_per_user} active job${me.limits.max_jobs_per_user === 1 ? "" : "s"} per person.`;
    $("joinRunTime").max = String(me.limits.max_run_time_hrs);
  }

  function mySession() {
    const session = state.session;
    const job = session.job;
    const total = Math.max(1, job.ends_at - (job.started_at || job.ends_at));
    const password = state.revealed ? esc(session.ssh_password) : "&bull;".repeat(20);
    const jupyterUrl = jupyterHost ? `https://${jupyterHost}/lab?token=${encodeURIComponent(session.jupyter_token)}` : null;
    return `
      <div class="session-top">
        <div>
          <div class="session-title">
            <h2>${esc(job.name)}</h2>
            <span class="badge badge-running">Running</span>
          </div>
          <div class="session-meta">Started ${erbium.formatTime(job.started_at)} · ${erbium.formatHours(job.requested_run_time_hrs)} reserved · ends ${erbium.formatTime(job.ends_at)}</div>
        </div>
        <div class="countdown">
          <div class="countdown-value" id="countdown" data-ends="${job.ends_at}">--</div>
          <div class="countdown-label">remaining</div>
        </div>
      </div>
      <div class="session-progress" aria-hidden="true"><span id="sessionProgress" data-started="${job.started_at || ""}" data-total="${total}"></span></div>
      <div class="session-body">
        <div>
          <div class="code-label">SSH password for user <code>access</code></div>
          <div class="secret">
            <code id="sshPassword">${password}</code>
            <button class="icon-btn" type="button" data-action="reveal" aria-label="${state.revealed ? "Hide password" : "Show password"}">${icon(state.revealed ? "eyeOff" : "eye")}</button>
            <button class="icon-btn" type="button" data-copy="${esc(session.ssh_password)}" aria-label="Copy password">${icon("copy")}</button>
          </div>
          <div class="code-label">Connect</div>
          ${erbium.codeBlock(sshCommand(), "SSH command")}
          <p class="hint">Shorter once your SSH config is set up: <code>ssh ${esc(host)}</code>. <a href="#connect">Setup guide</a></p>
        </div>
        <div>
          <div class="code-label">JupyterLab</div>
          ${jupyterUrl
            ? `<a class="btn" href="${esc(jupyterUrl)}" target="_blank" rel="noopener noreferrer">${icon("notebook")}Open JupyterLab${icon("external")}</a>`
            : '<p class="hint">Open the dashboard through its public hostname to get a JupyterLab link.</p>'}
          <div class="code-label">Jupyter token</div>
          <div class="secret">
            <code>${"&bull;".repeat(20)}</code>
            <button class="icon-btn" type="button" data-copy="${esc(session.jupyter_token)}" aria-label="Copy Jupyter token">${icon("copy")}</button>
          </div>
          <p class="hint">The password and token change with every reservation and stop working when it ends.</p>
        </div>
      </div>
      <div class="session-actions">
        <button class="btn btn-danger" type="button" data-action="stop" data-id="${job.id}">${icon("stop")}Stop my session</button>
      </div>
    `;
  }

  function queuedSession(mine) {
    const items = mine.map(({ job, position }) => `
      <li class="list-item">
        <div class="list-main">
          <div class="list-title"><span class="position">#${position}</span>${esc(job.name)}</div>
          <div class="list-meta">${erbium.formatHours(job.requested_run_time_hrs)} · starts about ${erbium.formatTime(job.estimated_start)} (${erbium.formatRelative(job.estimated_start)})</div>
        </div>
        <button class="btn btn-ghost btn-small" type="button" data-action="leave" data-id="${job.id}">Leave queue</button>
      </li>
    `).join("");
    return `
      <div class="session-top">
        <div>
          <div class="session-title">
            <h2>You are in the queue</h2>
            <span class="badge badge-queued">Waiting</span>
          </div>
          <div class="session-meta">This page shows your SSH password and JupyterLab link as soon as your turn starts.</div>
        </div>
      </div>
      <div class="card-body"><ul class="list">${items}</ul></div>
    `;
  }

  function emptySession() {
    return `
      <div class="empty">
        ${icon("server")}
        <strong>No active reservation</strong>
        <span>Join the queue to reserve the node. Your credentials appear here when it is your turn.</span>
        <a class="btn btn-secondary btn-small" href="#join">Join the queue</a>
      </div>
    `;
  }

  function renderSession() {
    const queue = state.status ? state.status.queue : [];
    const mine = queue.map((job, index) => ({ job, position: index + 1 }))
      .filter(({ job }) => job.username === state.me.username);
    let html;
    if (state.session) {
      html = mySession();
    } else if (mine.length) {
      html = queuedSession(mine);
    } else {
      html = emptySession();
    }
    $("sessionContent").innerHTML = html;
    tick();
  }

  function tick() {
    const countdown = $("countdown");
    if (!countdown) {
      return;
    }
    const remaining = Number(countdown.dataset.ends) - Date.now() / 1000;
    countdown.textContent = erbium.formatDuration(remaining);
    const progress = $("sessionProgress");
    if (progress && progress.dataset.started) {
      const elapsed = Date.now() / 1000 - Number(progress.dataset.started);
      progress.dataset.width = String((100 * elapsed) / Number(progress.dataset.total));
      erbium.applyWidths(progress.parentElement);
    }
  }

  function renderStats() {
    const status = state.status;
    const running = status.running;
    if (state.session) {
      $("statNode").textContent = "Your session";
      $("statNodeHint").textContent = `ends ${erbium.formatRelative(state.session.job.ends_at)}`;
    } else if (running) {
      $("statNode").textContent = "In use";
      $("statNodeHint").textContent = `by ${running.username}, ends ${erbium.formatRelative(running.ends_at)}`;
    } else {
      $("statNode").textContent = "Idle";
      $("statNodeHint").textContent = "ready for the next reservation";
    }
    $("statWait").textContent = status.wait_time_hrs > 0 ? erbium.formatHours(status.wait_time_hrs) : "None";
    $("statQueue").textContent = String(status.queue.length);
    const mineQueued = status.queue.filter((job) => job.username === state.me.username).length;
    $("statQueueHint").textContent = mineQueued ? `${mineQueued} of them yours` : "waiting reservations";
  }

  function renderQueue() {
    const status = state.status;
    const items = [];
    if (status.running) {
      const job = status.running;
      items.push(queueItem(job, "Now", "badge-running", "Running", `ends ${erbium.formatTime(job.ends_at)}`));
    }
    status.queue.forEach((job, index) => {
      items.push(queueItem(job, `#${index + 1}`, "badge-queued", "Queued",
        `starts about ${erbium.formatTime(job.estimated_start)}`));
    });
    $("queueList").innerHTML = items.length
      ? items.join("")
      : '<li class="table-empty">Nothing is running or queued. The node is free.</li>';
  }

  function queueItem(job, position, badgeClass, badge, when) {
    const mine = job.username === state.me.username;
    return `
      <li class="list-item">
        <div class="list-main">
          <div class="list-title"><span class="position">${position}</span>${esc(job.name)}${mine ? '<span class="mine">you</span>' : ""}</div>
          <div class="list-meta">${esc(job.username)} · ${erbium.formatHours(job.requested_run_time_hrs)} · ${when}</div>
        </div>
        <span class="badge ${badgeClass}">${badge}</span>
      </li>
    `;
  }

  function meter(label, percent, detail) {
    const value = Number(percent) || 0;
    return `
      <div class="meter">
        <div class="meter-label"><span>${label}</span><span>${detail}</span></div>
        <div class="meter-track"><span class="meter-fill${value >= 80 ? " high" : ""}" data-width="${value}"></span></div>
      </div>
    `;
  }

  function memoryOf(gpu) {
    const total = Number(gpu.total_memory_gb) || 0;
    const percent = Number(gpu.memory_utilization_percent) || 0;
    return { total, percent, used: (total * percent) / 100 };
  }

  function renderTelemetry() {
    const telemetry = state.telemetry;
    if (!telemetry) {
      return;
    }
    const gpus = telemetry.gpus;
    if (gpus.length) {
      const total = gpus.reduce((sum, gpu) => sum + memoryOf(gpu).total, 0);
      const used = gpus.reduce((sum, gpu) => sum + memoryOf(gpu).used, 0);
      $("statMemory").textContent = total ? `${Math.round((100 * used) / total)}%` : "--";
      $("statMemoryHint").textContent = `${used.toFixed(1)} of ${total.toFixed(0)} GB in use`;
      $("gpuList").innerHTML = gpus.map((gpu) => {
        const memory = memoryOf(gpu);
        const hot = Number(gpu.temperature_c) >= 80;
        return `
          <div class="gpu">
            <div class="gpu-head">
              <span class="gpu-name">${esc(gpu.name)}</span>
              <span class="gpu-id">GPU ${gpu.device_id}</span>
            </div>
            ${meter("Memory", memory.percent, `${Math.round(memory.percent)}% · ${memory.used.toFixed(1)} / ${memory.total.toFixed(0)} GB`)}
            ${meter("Compute", gpu.utilization_percent, erbium.formatNumber(gpu.utilization_percent, "%"))}
            <div class="gpu-facts">
              <span class="${hot ? "hot" : ""}">Temperature ${erbium.formatNumber(gpu.temperature_c, " °C")}</span>
              <span>Power ${erbium.formatNumber(gpu.power_draw_w, " W")}</span>
            </div>
          </div>
        `;
      }).join("");
    } else {
      $("statMemory").textContent = "--";
      $("statMemoryHint").textContent = "no GPU telemetry";
      $("gpuList").innerHTML = '<p class="hint">No GPU telemetry is available.</p>';
    }
    const host = telemetry.host;
    $("hostUsage").innerHTML = `
      <div class="usage-title"><span>Host</span></div>
      ${meter("CPU", host.cpu_percent, erbium.formatNumber(host.cpu_percent, "%"))}
      ${meter("Memory", host.memory_percent, `${Math.round(host.memory_percent)}% · ${host.memory_used_gb.toFixed(1)} / ${host.memory_total_gb.toFixed(0)} GB`)}
    `;
    erbium.applyWidths($("gpuList"));
    erbium.applyWidths($("hostUsage"));
  }

  let telemetryBusy = false;

  async function pollTelemetry() {
    if (document.hidden || telemetryBusy) {
      return;
    }
    telemetryBusy = true;
    const indicator = $("liveIndicator");
    try {
      state.telemetry = await erbium.api("GET", "/api/telemetry");
      renderTelemetry();
      indicator.textContent = "Live";
      indicator.classList.remove("paused");
    } catch (error) {
      indicator.textContent = "Reconnecting";
      indicator.classList.add("paused");
    } finally {
      telemetryBusy = false;
    }
  }

  async function refresh() {
    const [status, session] = await Promise.allSettled([
      erbium.api("GET", "/api/status"),
      erbium.api("GET", "/api/session")
    ]);
    if (session.status === "fulfilled") {
      const value = session.value;
      if (!value || !state.session || value.job.id !== state.session.job.id) {
        state.revealed = false;
      }
      state.session = value;
    }
    if (status.status === "fulfilled") {
      state.status = status.value;
    }
    const failed = status.status !== "fulfilled" || session.status !== "fulfilled";
    $("liveBanner").hidden = !failed;
    const renders = [renderSession];
    if (state.status) {
      renders.push(renderStats, renderQueue);
    }
    for (const render of renders) {
      try {
        render();
      } catch (error) {
        console.error(error);
      }
    }
    if (!failed) {
      $("lastUpdated").textContent = `Updated ${new Date().toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", second: "2-digit" })}`;
    }
  }

  $("sessionContent").addEventListener("click", async (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) {
      return;
    }
    const feedback = $("sessionFeedback");
    const action = button.dataset.action;
    try {
      if (action === "reveal") {
        state.revealed = !state.revealed;
        renderSession();
      } else if (action === "stop") {
        if (!window.confirm("Stop your session now? Every process of the session will be killed.")) {
          return;
        }
        button.disabled = true;
        await erbium.api("POST", `/api/jobs/${button.dataset.id}/stop`);
        erbium.setFeedback(feedback, "success", "Your session is being stopped.");
        await refresh();
      } else if (action === "leave") {
        button.disabled = true;
        await erbium.api("DELETE", `/api/jobs/${button.dataset.id}`);
        erbium.setFeedback(feedback, "success", "You left the queue.");
        await refresh();
      }
    } catch (error) {
      erbium.setFeedback(feedback, "error", error.message);
      button.disabled = false;
    }
  });

  document.querySelector(".os-tabs").addEventListener("click", (event) => {
    const tab = event.target.closest("button[data-os]");
    if (tab) {
      state.os = tab.dataset.os;
      renderConnect();
    }
  });

  erbium.bindCopy(document.body);

  erbium.bindForm($("joinForm"), $("joinButton"), $("joinFeedback"), async () => {
    const job = await erbium.api("POST", "/api/jobs", {
      name: $("joinName").value.trim(),
      requested_run_time_hrs: Number($("joinRunTime").value)
    });
    $("joinForm").reset();
    await refresh();
    return `Queued "${job.name}".`;
  });

  erbium.bindForm($("profileForm"), $("profileButton"), $("profileFeedback"), async () => {
    const notifications = {};
    document.querySelectorAll("#notificationChecks input[data-kind]").forEach((input) => {
      notifications[input.dataset.kind] = input.checked;
    });
    const email = $("profileEmail").value.trim();
    const updated = await erbium.api("PUT", "/api/me", {
      email: email || null,
      notifications,
      public_keys: $("profileKeys").value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean)
    });
    state.me = { ...state.me, ...updated };
    renderProfile();
    return "Settings saved.";
  });

  erbium.bindForm($("passwordForm"), $("passwordButton"), $("passwordFeedback"), async () => {
    await erbium.api("POST", "/api/me/password", {
      current_password: $("currentPassword").value,
      new_password: $("newPassword").value
    });
    window.location.assign("/login");
    return "Password changed.";
  });

  $("logoutButton").addEventListener("click", () => erbium.logout());

  (async () => {
    renderConnect();
    state.me = await erbium.loadMe();
    renderProfile();
    await refresh();
    window.setInterval(refresh, 15000);
    window.setInterval(tick, 1000);
    pollTelemetry();
    window.setInterval(pollTelemetry, TELEMETRY_INTERVAL_MS);
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) {
        $("liveIndicator").textContent = "Paused";
        $("liveIndicator").classList.add("paused");
      } else {
        pollTelemetry();
      }
    });
  })();
})();
