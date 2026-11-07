"use strict";

(() => {
  const form = document.getElementById("loginForm");
  const button = document.getElementById("loginButton");
  const feedback = document.getElementById("loginFeedback");
  const username = document.getElementById("loginUsername");
  const password = document.getElementById("loginPassword");

  username.focus();

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!username.value.trim() || !password.value) {
      erbium.setFeedback(feedback, "error", "Enter your username and password.");
      return;
    }
    button.disabled = true;
    erbium.setFeedback(feedback, "info", "Signing in...");
    try {
      await erbium.api("POST", "/api/login", { username: username.value.trim(), password: password.value });
      window.location.assign("/dash");
    } catch (error) {
      erbium.setFeedback(feedback, "error", error.message);
      password.value = "";
      password.focus();
      button.disabled = false;
    }
  });
})();
