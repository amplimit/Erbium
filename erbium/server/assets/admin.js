"use strict";

(() => {
  const REASONS = {
    expired: "Time limit reached",
    stopped: "Stopped by owner",
    left: "Left the queue",
    admin_stopped: "Stopped by admin",
    admin_removed: "Removed by admin"
  };
  const state = { me: null, overview: null };
  const $ = (id) => document.getElementById(id);
  const esc = erbium.escapeHtml;

  function jobRow(job, position, time, isRunning, index, count) {
    const actions = isRunning
      ? `<button class="small-button danger-button" type="button" data-action="stop" data-id="${job.id}">Stop</button>`
      : `
        <button class="small-button ghost-button" type="button" data-action="move" data-offset="-1" data-id="${job.id}" ${index === 0 ? "disabled" : ""}>Up</button>
        <button class="small-button ghost-button" type="button" data-action="move" data-offset="1" data-id="${job.id}" ${index === count - 1 ? "disabled" : ""}>Down</button>
        <button class="small-button danger-button" type="button" data-action="remove" data-id="${job.id}">Remove</button>
      `;
    return `
      <tr>
        <td>${position}</td>
        <td class="queue-name">${esc(job.name)}</td>
        <td>${esc(job.username)}</td>
        <td>${erbium.formatTime(time)}</td>
        <td>
          <div class="button-row">
            <input type="number" min="0.25" step="0.25" value="${job.requested_run_time_hrs}" data-hours-for="${job.id}" aria-label="Run time in hours" />
            <button class="small-button ghost-button" type="button" data-action="hours" data-id="${job.id}">Set</button>
          </div>
        </td>
        <td><div class="button-row">${actions}</div></td>
      </tr>
    `;
  }

  function renderJobs() {
    const { running, queue, history } = state.overview;
    const rows = [];
    if (running) {
      rows.push(jobRow(running, "Running", running.ends_at, true, 0, 1));
    }
    queue.forEach((job, index) => rows.push(jobRow(job, String(index + 1), job.estimated_start, false, index, queue.length)));
    $("jobsBody").innerHTML = rows.join("");
    $("jobsEmpty").textContent = rows.length ? "" : "Nothing is running or queued.";
    $("historyBody").innerHTML = history.map((job) => `
      <tr>
        <td class="queue-name">${esc(job.name)}</td>
        <td>${esc(job.username)}</td>
        <td>${erbium.formatTime(job.started_at)}</td>
        <td>${erbium.formatTime(job.ended_at)}</td>
        <td>${esc(REASONS[job.end_reason] || job.end_reason || "--")}</td>
      </tr>
    `).join("");
  }

  function renderUsers() {
    const users = state.overview.users;
    $("usersBody").innerHTML = users.map((user) => `
      <tr>
        <td class="queue-name">${esc(user.username)}</td>
        <td>${esc(user.email || "--")}</td>
        <td>${user.is_admin ? "Administrator" : "Member"}</td>
        <td>
          ${user.username === state.me.username ? "" : `<button class="small-button danger-button" type="button" data-action="delete-user" data-username="${esc(user.username)}">Delete</button>`}
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
  }

  $("jobsBody").addEventListener("click", async (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) {
      return;
    }
    const id = button.dataset.id;
    const feedback = $("jobsFeedback");
    try {
      if (button.dataset.action === "stop") {
        if (!window.confirm("Stop the running job? All of its processes will be killed.")) {
          return;
        }
        await erbium.api("POST", `/api/admin/jobs/${id}/stop`);
        erbium.setFeedback(feedback, "success", "The running job is being stopped.");
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
    const button = event.target.closest("button[data-action='delete-user']");
    if (!button) {
      return;
    }
    const username = button.dataset.username;
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
    await erbium.api("PATCH", `/api/admin/users/${encodeURIComponent(username)}`, {
      email: email || null,
      is_admin: $("editUserAdmin").checked
    });
    await refresh();
    return `Saved ${username}.`;
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
    state.me = await erbium.loadMe();
    $("signedInAs").textContent = `Signed in as ${state.me.username}`;
    await refresh({ settings: true });
    window.setInterval(() => {
      if (document.activeElement && document.activeElement.closest("form, #jobsBody")) {
        return;
      }
      refresh().catch(() => undefined);
    }, 15000);
  })();
})();
