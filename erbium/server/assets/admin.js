"use strict";

(() => {
  const REASONS = {
    expired: "Time limit reached",
    stopped: "Stopped by owner",
    left: "Left the queue",
    admin_stopped: "Stopped by admin",
    admin_removed: "Removed by admin"
  };
  const TABS = ["jobs", "users", "settings", "packages"];
  const state = { me: null, overview: null };
  const $ = (id) => document.getElementById(id);
  const esc = erbium.escapeHtml;
  const icon = erbium.icon;

  function selectTab(name, focus) {
    const active = TABS.includes(name) ? name : "jobs";
    TABS.forEach((tab) => {
      const button = $(`tab-${tab}`);
      button.setAttribute("aria-selected", String(tab === active));
      button.tabIndex = tab === active ? 0 : -1;
      $(`panel-${tab}`).hidden = tab !== active;
    });
    if (focus) {
      $(`tab-${active}`).focus();
    }
    if (window.location.hash !== `#${active}`) {
      window.history.replaceState(null, "", `#${active}`);
    }
  }

  function jobRow(job, position, time, isRunning, index, count) {
    const actions = isRunning
      ? `<button class="btn btn-danger btn-small" type="button" data-action="stop" data-id="${job.id}">${icon("stop")}Stop</button>`
      : `
        <button class="icon-btn" type="button" data-action="move" data-offset="-1" data-id="${job.id}" aria-label="Move ${esc(job.name)} up" ${index === 0 ? "disabled" : ""}>${icon("up")}</button>
        <button class="icon-btn" type="button" data-action="move" data-offset="1" data-id="${job.id}" aria-label="Move ${esc(job.name)} down" ${index === count - 1 ? "disabled" : ""}>${icon("down")}</button>
        <button class="btn btn-ghost btn-small" type="button" data-action="remove" data-id="${job.id}">Remove</button>
      `;
    return `
      <tr>
        <td>${isRunning ? '<span class="badge badge-running">Running</span>' : `<span class="position">#${position}</span>`}</td>
        <td class="strong">${esc(job.name)}</td>
        <td>${esc(job.username)}</td>
        <td class="muted">${erbium.formatTime(time)}</td>
        <td>
          <div class="inline-input">
            <input type="number" min="0.25" step="0.25" value="${job.requested_run_time_hrs}" data-hours-for="${job.id}" aria-label="Hours for ${esc(job.name)}" />
            <button class="btn btn-secondary btn-small" type="button" data-action="hours" data-id="${job.id}">Set</button>
          </div>
        </td>
        <td><div class="row-actions">${actions}</div></td>
      </tr>
    `;
  }

  function renderJobs() {
    const { running, queue, history } = state.overview;
    const rows = [];
    if (running) {
      rows.push(jobRow(running, 0, running.ends_at, true, 0, 1));
    }
    queue.forEach((job, index) => rows.push(jobRow(job, index + 1, job.estimated_start, false, index, queue.length)));
    $("jobsBody").innerHTML = rows.join("");
    $("jobsEmpty").hidden = rows.length > 0;
    $("historyBody").innerHTML = history.map((job) => `
      <tr>
        <td class="strong">${esc(job.name)}</td>
        <td>${esc(job.username)}</td>
        <td class="muted">${erbium.formatTime(job.started_at)}</td>
        <td class="muted">${erbium.formatTime(job.ended_at)}</td>
        <td>${esc(REASONS[job.end_reason] || job.end_reason || "--")}</td>
      </tr>
    `).join("");
    $("historyEmpty").hidden = history.length > 0;
  }

  function renderUsers() {
    const users = state.overview.users;
    $("usersBody").innerHTML = users.map((user) => `
      <tr>
        <td class="strong">${esc(user.username)}${user.username === state.me.username ? ' <span class="mine">you</span>' : ""}</td>
        <td class="muted">${esc(user.email || "--")}</td>
        <td>${user.is_admin ? '<span class="badge badge-queued">Administrator</span>' : '<span class="badge">Member</span>'}</td>
        <td class="muted">${erbium.formatTime(user.created_at)}</td>
        <td>
          <div class="row-actions">
            <button class="icon-btn" type="button" data-action="edit-user" data-username="${esc(user.username)}" aria-label="Edit ${esc(user.username)}">${icon("pencil")}</button>
            ${user.username === state.me.username ? "" : `<button class="icon-btn" type="button" data-action="delete-user" data-username="${esc(user.username)}" aria-label="Delete ${esc(user.username)}">${icon("trash")}</button>`}
          </div>
        </td>
      </tr>
    `).join("");
    const editing = $("editUsername").value;
    document.querySelectorAll(".user-select").forEach((select) => {
      const selected = select.value;
      select.innerHTML = users.map((user) => `<option value="${esc(user.username)}">${esc(user.username)}</option>`).join("");
      if (users.some((user) => user.username === selected)) {
        select.value = selected;
      }
    });
    if ($("editUsername").value !== editing) {
      fillEditForm();
    }
  }

  function fillEditForm() {
    const user = state.overview.users.find((candidate) => candidate.username === $("editUsername").value);
    $("editUserNewName").value = user ? user.username : "";
    $("editUserEmail").value = user && user.email ? user.email : "";
    $("editUserAdmin").checked = Boolean(user && user.is_admin);
  }

  function renderSettings() {
    const settings = state.overview.settings;
    $("maxRunTime").value = settings.max_run_time_hrs;
    $("maxJobsPerUser").value = settings.max_jobs_per_user;
    $("maxQueueLength").value = settings.max_queue_length;
    $("endingNotice").value = settings.ending_notice_minutes;
  }

  async function refresh({ settings = false } = {}) {
    state.overview = await erbium.api("GET", "/api/admin/overview");
    renderJobs();
    renderUsers();
    if (settings) {
      renderSettings();
    }
    $("lastUpdated").textContent = `Updated ${new Date().toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", second: "2-digit" })}`;
  }

  document.querySelector(".tabs").addEventListener("click", (event) => {
    const tab = event.target.closest("button[data-tab]");
    if (tab) {
      selectTab(tab.dataset.tab, false);
    }
  });

  document.querySelector(".tabs").addEventListener("keydown", (event) => {
    if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") {
      return;
    }
    const current = TABS.findIndex((tab) => $(`tab-${tab}`).getAttribute("aria-selected") === "true");
    const step = event.key === "ArrowRight" ? 1 : -1;
    selectTab(TABS[(current + step + TABS.length) % TABS.length], true);
    event.preventDefault();
  });

  window.addEventListener("hashchange", () => selectTab(window.location.hash.slice(1), false));

  $("jobsBody").addEventListener("click", async (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) {
      return;
    }
    const id = button.dataset.id;
    const feedback = $("jobsFeedback");
    try {
      if (button.dataset.action === "stop") {
        if (!window.confirm("Stop the running session? All of its processes will be killed.")) {
          return;
        }
        await erbium.api("POST", `/api/admin/jobs/${id}/stop`);
        erbium.setFeedback(feedback, "success", "The running session is being stopped.");
      } else if (button.dataset.action === "remove") {
        await erbium.api("DELETE", `/api/admin/jobs/${id}`);
        erbium.setFeedback(feedback, "success", "Job removed from the queue.");
      } else if (button.dataset.action === "move") {
        await erbium.api("POST", `/api/admin/jobs/${id}/move`, { offset: Number(button.dataset.offset) });
        erbium.setFeedback(feedback, "success", "Queue reordered.");
      } else if (button.dataset.action === "hours") {
        const hours = Number(document.querySelector(`input[data-hours-for="${id}"]`).value);
        await erbium.api("PUT", `/api/admin/jobs/${id}/run_time`, { requested_run_time_hrs: hours });
        erbium.setFeedback(feedback, "success", "Run time updated.");
      }
      await refresh();
    } catch (error) {
      erbium.setFeedback(feedback, "error", error.message);
    }
  });

  $("usersBody").addEventListener("click", async (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) {
      return;
    }
    const username = button.dataset.username;
    if (button.dataset.action === "edit-user") {
      $("editUsername").value = username;
      $("resetUsername").value = username;
      fillEditForm();
      $("editUserForm").scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
      $("editUserNewName").focus({ preventScroll: true });
      return;
    }
    if (!window.confirm(`Delete ${username}? Their jobs are removed and a running session is stopped.`)) {
      return;
    }
    try {
      await erbium.api("DELETE", `/api/admin/users/${encodeURIComponent(username)}`);
      erbium.setFeedback($("usersFeedback"), "success", `Deleted ${username}.`);
      await refresh();
    } catch (error) {
      erbium.setFeedback($("usersFeedback"), "error", error.message);
    }
  });

  $("editUsername").addEventListener("change", fillEditForm);

  erbium.bindForm($("createUserForm"), $("createUserButton"), $("createUserFeedback"), async () => {
    const email = $("newUserEmail").value.trim();
    const user = await erbium.api("POST", "/api/admin/users", {
      username: $("newUsername").value.trim(),
      password: $("newUserPassword").value,
      email: email || null,
      is_admin: $("newUserAdmin").checked
    });
    $("createUserForm").reset();
    await refresh();
    return `Created ${user.username}.`;
  });

  erbium.bindForm($("editUserForm"), $("editUserButton"), $("editUserFeedback"), async () => {
    const username = $("editUsername").value;
    const email = $("editUserEmail").value.trim();
    const user = await erbium.api("PATCH", `/api/admin/users/${encodeURIComponent(username)}`, {
      username: $("editUserNewName").value.trim(),
      email: email || null,
      is_admin: $("editUserAdmin").checked
    });
    if (username === state.me.username) {
      state.me = await erbium.loadMe();
    }
    await refresh();
    $("editUsername").value = user.username;
    $("resetUsername").value = user.username;
    fillEditForm();
    return user.username === username ? `Saved ${username}.` : `Renamed ${username} to ${user.username}.`;
  });

  erbium.bindForm($("resetPasswordForm"), $("resetPasswordButton"), $("resetPasswordFeedback"), async () => {
    const username = $("resetUsername").value;
    await erbium.api("POST", `/api/admin/users/${encodeURIComponent(username)}/password`, {
      password: $("resetPassword").value
    });
    $("resetPassword").value = "";
    if (username === state.me.username) {
      window.location.assign("/login");
    }
    return `Password reset for ${username}.`;
  });

  erbium.bindForm($("settingsForm"), $("settingsButton"), $("settingsFeedback"), async () => {
    await erbium.api("PUT", "/api/admin/settings", {
      max_run_time_hrs: Number($("maxRunTime").value),
      max_jobs_per_user: Number($("maxJobsPerUser").value),
      max_queue_length: Number($("maxQueueLength").value),
      ending_notice_minutes: Number($("endingNotice").value)
    });
    await refresh({ settings: true });
    return "Limits saved.";
  });

  erbium.bindForm($("aptForm"), $("aptButton"), $("aptFeedback"), async () => {
    const packages = $("aptPackages").value.split(/[\s,]+/).map((name) => name.trim()).filter(Boolean);
    const result = await erbium.api("POST", "/api/admin/apt_install", { packages });
    $("aptForm").reset();
    return `Installed: ${result.installed.join(", ")}.`;
  });

  $("logoutButton").addEventListener("click", () => erbium.logout());

  (async () => {
    selectTab(window.location.hash.slice(1), false);
    state.me = await erbium.loadMe();
    await refresh({ settings: true });
    window.setInterval(() => {
      if (document.activeElement && document.activeElement.closest("form, #jobsBody")) {
        return;
      }
      refresh().catch(() => undefined);
    }, 15000);
  })();
})();
