"use strict";

(() => {
  const form = document.getElementById("loginForm");
  const button = document.getElementById("loginButton");
  const feedback = document.getElementById("loginFeedback");

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    button.disabled = true;
    erbium.setFeedback(feedback, "info", "Signing in...");
    try {
      await erbium.api("POST", "/api/login", {
        username: document.getElementById("loginUsername").value.trim(),
        password: document.getElementById("loginPassword").value
      });
      window.location.assign("/dash");
    } catch (error) {
      erbium.setFeedback(feedback, "error", error.message);
      button.disabled = false;
    }
  });
})();
