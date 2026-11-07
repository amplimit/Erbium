"use strict";

const erbium = (() => {
  let csrfToken = "";

  const ICON_PATHS = {
    copy: '<rect width="14" height="14" x="8" y="8" rx="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>',
    check: '<path d="M20 6 9 17l-5-5"/>',
    eye: '<path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/>',
    eyeOff: '<path d="M9.88 9.88a3 3 0 1 0 4.24 4.24"/><path d="M10.73 5.08A10.43 10.43 0 0 1 12 5c7 0 10 7 10 7a13.16 13.16 0 0 1-1.67 2.68"/><path d="M6.61 6.61A13.53 13.53 0 0 0 2 12s3 7 10 7a9.74 9.74 0 0 0 5.39-1.61"/><path d="M2 2l20 20"/>',
    external: '<path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
    stop: '<rect width="14" height="14" x="5" y="5" rx="2"/>',
    up: '<path d="m18 15-6-6-6 6"/>',
    down: '<path d="m6 9 6 6 6-6"/>',
    close: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    trash: '<path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/>',
    terminal: '<path d="m4 17 6-6-6-6"/><path d="M12 19h8"/>',
    server: '<rect width="20" height="8" x="2" y="2" rx="2"/><rect width="20" height="8" x="2" y="14" rx="2"/><path d="M6 6h.01"/><path d="M6 18h.01"/>',
    notebook: '<path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>',
    clock: '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>'
  };

  function icon(name) {
    return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICON_PATHS[name]}</svg>`;
  }

  async function api(method, url, body) {
    const headers = { Accept: "application/json" };
    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
    }
    if (method !== "GET") {
      headers["X-CSRF-Token"] = csrfToken;
    }
    const response = await fetch(url, {
      method,
      headers,
      credentials: "same-origin",
      body: body === undefined ? undefined : JSON.stringify(body)
    });
    const text = await response.text();
    let payload = null;
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch (error) {
        payload = text;
      }
    }
    if (response.status === 401 && url !== "/api/login") {
      window.location.assign("/login");
      throw new Error("Not signed in");
    }
    if (!response.ok) {
      throw new Error(describeError(payload, response.status));
    }
    return payload;
  }

  function describeError(payload, status) {
    if (payload && typeof payload === "object" && payload.detail) {
      if (Array.isArray(payload.detail)) {
        return payload.detail.map((item) => item.msg || String(item)).join("; ");
      }
      return String(payload.detail);
    }
    return `Request failed with status ${status}`;
  }

  async function loadMe() {
    const me = await api("GET", "/api/me");
    csrfToken = me.csrf_token;
    const name = document.getElementById("userName");
    const avatar = document.getElementById("userAvatar");
    if (name) {
      name.textContent = me.username;
    }
    if (avatar) {
      avatar.textContent = me.username.slice(0, 1);
    }
    return me;
  }

  async function logout() {
    try {
      await api("POST", "/api/logout");
    } finally {
      window.location.assign("/login");
    }
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function isNumber(value) {
    return value !== null && value !== undefined && Number.isFinite(Number(value));
  }

  function formatNumber(value, suffix, digits = 0) {
    return isNumber(value) ? `${Number(value).toFixed(digits)}${suffix}` : "--";
  }

  function formatHours(value) {
    if (!isNumber(value)) {
      return "--";
    }
    const hours = Number(value);
    if (hours < 1) {
      return `${Math.round(hours * 60)} min`;
    }
    const rounded = Math.round(hours * 100) / 100;
    return `${rounded} h`;
  }

  function formatDuration(seconds) {
    if (!isNumber(seconds)) {
      return "--";
    }
    const total = Math.max(0, Math.floor(Number(seconds)));
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const rest = total % 60;
    const pad = (value) => String(value).padStart(2, "0");
    return `${hours}:${pad(minutes)}:${pad(rest)}`;
  }

  function formatRelative(unixSeconds) {
    if (!isNumber(unixSeconds)) {
      return "--";
    }
    const delta = Number(unixSeconds) - Date.now() / 1000;
    const minutes = Math.round(Math.abs(delta) / 60);
    if (minutes < 1) {
      return "now";
    }
    const text = minutes < 60 ? `${minutes} min` : `${Math.round(minutes / 6) / 10} h`;
    return delta > 0 ? `in ${text}` : `${text} ago`;
  }

  function formatTime(unixSeconds) {
    if (!isNumber(unixSeconds)) {
      return "--";
    }
    return new Date(Number(unixSeconds) * 1000).toLocaleString("en-US", {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit"
    });
  }

  function setFeedback(target, type, message) {
    target.className = `feedback ${type}`;
    target.textContent = message;
  }

  function nodeHost() {
    const hostname = window.location.hostname;
    if (!hostname || hostname === "localhost" || hostname === "127.0.0.1" || !hostname.startsWith("node-")) {
      return null;
    }
    return hostname.slice("node-".length);
  }

  function flash(button, iconName, label) {
    const original = { html: button.innerHTML, label: button.getAttribute("aria-label"), title: button.title };
    button.innerHTML = icon(iconName);
    button.setAttribute("aria-label", label);
    button.title = label;
    window.setTimeout(() => {
      button.innerHTML = original.html;
      button.setAttribute("aria-label", original.label || "Copy");
      button.title = original.title;
    }, 1600);
  }

  async function copy(text, button) {
    try {
      await navigator.clipboard.writeText(text);
      if (button) {
        flash(button, "check", "Copied");
      }
      return true;
    } catch (error) {
      if (button) {
        flash(button, "close", "Copy failed; select the text and copy it manually");
      }
      return false;
    }
  }

  function codeBlock(text, label) {
    return `
      <div class="code">
        <pre>${escapeHtml(text)}</pre>
        <button class="icon-btn" type="button" data-copy="${escapeHtml(text)}" aria-label="Copy${label ? ` ${escapeHtml(label)}` : ""}">${icon("copy")}</button>
      </div>
    `;
  }

  function bindCopy(root) {
    root.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-copy]");
      if (button) {
        copy(button.dataset.copy, button);
      }
    });
  }

  function applyWidths(root) {
    root.querySelectorAll("[data-width]").forEach((element) => {
      const value = Math.max(0, Math.min(100, Number(element.dataset.width) || 0));
      element.style.width = `${value}%`;
    });
  }

  function bindForm(form, button, feedback, handler) {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      button.disabled = true;
      setFeedback(feedback, "info", "Working...");
      try {
        const message = await handler();
        setFeedback(feedback, "success", message || "Done.");
      } catch (error) {
        setFeedback(feedback, "error", error.message);
      } finally {
        button.disabled = false;
      }
    });
  }

  return {
    api, loadMe, logout, escapeHtml, formatNumber, formatHours, formatDuration, formatRelative, formatTime, setFeedback,
    nodeHost, copy, codeBlock, bindCopy, applyWidths, bindForm, icon
  };
})();
