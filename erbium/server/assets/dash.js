"use strict";

(() => {
  const NOTIFICATION_LABELS = {
    queued: "My job joined the queue",
    started: "My job started (my turn)",
    ending: "My job is about to end",
    finished: "My job reached its time limit",
    admin: "An administrator stopped or removed my job"
  };
  const PROXY = 'ProxyCommand="cloudflared access ssh --hostname %h"';

  const state = { me: null, status: null, session: null, revealed: false };
  const $ = (id) => document.getElementById(id);
  const host = erbium.nodeHost() || "<node-host>";

  function renderCommands() {
    $("sshCommand").textContent = `ssh -o ${PROXY} access@${host}`;
    $("scpUploadCommand").textContent = `scp -o ${PROXY} ./local-file access@${host}:/workspace/output/`;
    $("scpDownloadCommand").textContent = `scp -o ${PROXY} access@${host}:/workspace/output/remote-file ./`;
  }

  function renderProfile() {
    const me = state.me;
    $("signedInAs").textContent = `Signed in as ${me.username}`;
    $("adminLink").hidden = !me.is_admin;
    $("profileEmail").value = me.email || "";
    $("profileKeys").value = me.public_keys.join("\n");
    $("notificationChecks").innerHTML = Object.entries(NOTIFICATION_LABELS).map(([kind, label]) => `
      <label class="check-row">
        <input type="checkbox" data-kind="${kind}" ${me.notifications[kind] ? "checked" : ""} />
        <span>${erbium.escapeHtml(label)}</span>
      </label>
    `).join("");
    $("joinLimits").textContent = `Limits: at most ${erbium.formatHours(me.limits.max_run_time_hrs)} per job and ` +
      `${me.limits.max_jobs_per_user} active job(s) per person.`;
    $("joinRunTime").max = String(me.limits.max_run_time_hrs);
  }

  function renderSession() {
    const summary = $("sessionSummary");
    const parts = [];
    const session = state.session;
    if (session) {
      const job = session.job;
      const jupyterUrl = erbium.nodeHost()
        ? `https://jupyter-${erbium.nodeHost()}/lab?token=${encodeURIComponent(session.jupyter_token)}`
        : null;
      const password = state.revealed ? erbium.escapeHtml(session.ssh_password) : "&bull;".repeat(16);
      parts.push(`
        <div class="summary-row">
          <div class="summary-label">Running now</div>
          <div class="summary-value">${erbium.escapeHtml(job.name)} &middot; ends ${erbium.formatTime(job.ends_at)}</div>
        </div>
        <div class="summary-row">
          <div class="summary-label">SSH password for user access</div>
          <div class="secret-row">
            <code>${password}</code>
            <button class="small-button ghost-button" type="button" data-action="reveal">${state.revealed ? "Hide" : "Show"}</button>
            <button class="small-button ghost-button" type="button" data-action="copy-password">Copy</button>
          </div>
        </div>
        <div class="summary-row">
          <div class="summary-label">Jupyter</div>
          <div class="button-row">
            ${jupyterUrl ? `<a class="small-button" href="${erbium.escapeHtml(jupyterUrl)}" target="_blank" rel="noopener noreferrer">Open JupyterLab</a>` : ""}
            <button class="small-button ghost-button" type="button" data-action="copy-token">Copy token</button>
          </div>
        </div>
        <div class="button-row">
          <button class="small-button danger-button" type="button" data-action="stop" data-id="${job.id}">Stop my job now</button>
        </div>
      `);
    }
    const mine = (state.status ? state.status.queue : []).filter((job) => job.username === state.me.username);
    mine.forEach((job) => {
      parts.push(`
        <div class="summary-row">
          <div class="summary-label">Queued</div>
          <div class="summary-value">${erbium.escapeHtml(job.name)} &middot; ${erbium.formatHours(job.requested_run_time_hrs)} &middot; starts about ${erbium.formatTime(job.estimated_start)}</div>
          <div class="button-row">
            <button class="small-button ghost-button" type="button" data-action="leave" data-id="${job.id}">Leave queue</button>
          </div>
        </div>
      `);
    });
    summary.innerHTML = parts.length ? parts.join("") : '<div class="summary-empty">You have no running or queued jobs.</div>';
  }

  function renderStatus() {
    const status = state.status;
    if (!status) {
      return;
    }
    const available = status.gpus.filter((device) => device.available).length;
    $("heroWaitTime").textContent = erbium.formatHours(status.wait_time_hrs);
    $("heroQueueDepth").textContent = String(status.queue.length);
    $("heroAvailableGpus").textContent = status.gpus.length ? `${available}/${status.gpus.length}` : "--";
    $("lastUpdated").textContent = `Last updated ${new Date().toLocaleTimeString()}`;

    $("gpuList").innerHTML = status.gpus.length ? status.gpus.map((device) => `
      <article class="gpu-chip">
        <span class="gpu-id">GPU ${device.device_id}</span>
        <div class="gpu-meta">
          <strong>${erbium.escapeHtml(device.name)}</strong>
          <span>${erbium.formatNumber(device.total_memory_gb, " GB")} &middot; ${erbium.formatNumber(device.utilization_percent, "% compute")} &middot; ${erbium.formatNumber(device.memory_utilization_percent, "% memory")} &middot; ${erbium.formatNumber(device.power_draw_w, " W")}</span>
        </div>
        <span class="gpu-status ${device.available ? "available" : "busy"}">${device.available ? "Idle" : "Busy"}</span>
      </article>
    `).join("") : '<div class="help-line">No GPU telemetry is available.</div>';

    const rows = [];
    if (status.running) {
      rows.push(queueRow("Running", status.running, status.running.ends_at, "available", "Running"));
    }
    status.queue.forEach((job, index) => rows.push(queueRow(String(index + 1), job, job.estimated_start, "busy", "Queued")));
    $("queueBody").innerHTML = rows.join("");
    $("queueEmpty").textContent = rows.length ? "" : "Nothing is running or queued.";
  }

  function queueRow(position, job, time, badgeClass, badge) {
    return `
      <tr>
        <td>${position}</td>
        <td class="queue-name">${erbium.escapeHtml(job.name)}</td>
        <td>${erbium.escapeHtml(job.username)}</td>
        <td>${erbium.formatHours(job.requested_run_time_hrs)}</td>
        <td>${erbium.formatTime(time)}</td>
        <td><span class="badge ${badgeClass}">${badge}</span></td>
      </tr>
    `;
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
    for (const render of [renderSession, renderStatus]) {
      try {
        render();
      } catch (error) {
        console.error(error);
      }
    }
    if (status.status !== "fulfilled" || session.status !== "fulfilled") {
      $("lastUpdated").textContent = "Live data unavailable. Retrying automatically.";
    }
  }

  $("sessionSummary").addEventListener("click", async (event) => {
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
      } else if (action === "copy-password") {
        await erbium.copy(state.session.ssh_password, feedback);
      } else if (action === "copy-token") {
        await erbium.copy(state.session.jupyter_token, feedback);
      } else if (action === "stop") {
        if (!window.confirm("Stop your running job now? All of its processes will be killed.")) {
          return;
        }
        await erbium.api("POST", `/api/jobs/${button.dataset.id}/stop`);
        erbium.setFeedback(feedback, "success", "Your job is being stopped.");
        await refresh();
      } else if (action === "leave") {
        await erbium.api("DELETE", `/api/jobs/${button.dataset.id}`);
        erbium.setFeedback(feedback, "success", "Removed from the queue.");
        await refresh();
      }
    } catch (error) {
      erbium.setFeedback(feedback, "error", error.message);
    }
  });

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
    renderCommands();
    state.me = await erbium.loadMe();
    renderProfile();
    await refresh();
    window.setInterval(refresh, 15000);
  })();
})();
