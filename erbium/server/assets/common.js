"use strict";

const erbium = (() => {
  let csrfToken = "";

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

  function formatNumber(value, suffix) {
    return value === null || value === undefined || !Number.isFinite(Number(value))
      ? "--"
      : `${Number(value).toFixed(0)}${suffix}`;
  }

  function formatHours(value) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) {
      return "--";
    }
    const hours = Number(value);
    if (hours < 1) {
      return `${Math.round(hours * 60)} min`;
    }
    return `${hours.toFixed(hours >= 24 ? 1 : 2)} h`;
  }

  function formatTime(unixSeconds) {
    if (unixSeconds === null || unixSeconds === undefined || !Number.isFinite(Number(unixSeconds))) {
      return "--";
    }
    return new Date(Number(unixSeconds) * 1000).toLocaleString([], {
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
    if (!hostname || hostname === "localhost" || hostname === "127.0.0.1") {
      return null;
    }
    return hostname.replace(/^node-/, "");
  }

  async function copy(text, feedback) {
    try {
      await navigator.clipboard.writeText(text);
      if (feedback) {
        setFeedback(feedback, "success", "Copied to clipboard.");
      }
    } catch (error) {
      if (feedback) {
        setFeedback(feedback, "error", "Copy failed. Select the text and copy it manually.");
      }
    }
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

  return { api, loadMe, logout, escapeHtml, formatNumber, formatHours, formatTime, setFeedback, nodeHost, copy, bindForm };
})();
