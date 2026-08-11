(function () {
  const form = document.getElementById("contact-form");
  if (!form) return;

  const statusEl = document.getElementById("contact-status");
  const submitBtn = document.getElementById("contact-submit");
  const submitLabel = submitBtn ? submitBtn.querySelector("span") : null;
  const nameInput = form.querySelector('input[name="name"]');

  function t(key, fallback) {
    if (window.ConjectaI18n && typeof window.ConjectaI18n.t === "function") {
      return window.ConjectaI18n.t(key);
    }
    return fallback;
  }

  function setStatus(message, kind) {
    if (!statusEl) return;
    statusEl.textContent = message || "";
    statusEl.dataset.kind = kind || "";
  }

  function focusContactForm() {
    if (window.location.hash !== "#contact") return;
    window.requestAnimationFrame(() => {
      if (nameInput && typeof nameInput.focus === "function") {
        nameInput.focus({ preventScroll: true });
      }
    });
  }

  window.addEventListener("hashchange", focusContactForm);
  focusContactForm();

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = new FormData(form);
    const payload = {
      name: String(data.get("name") || "").trim(),
      email: String(data.get("email") || "").trim(),
      message: String(data.get("message") || "").trim(),
    };
    if (!payload.name || !payload.email || !payload.message) {
      setStatus(t("home.contact.error", "Could not send right now. Please try again."), "error");
      return;
    }

    const previousLabel = submitLabel ? submitLabel.textContent : "";
    if (submitBtn) submitBtn.disabled = true;
    if (submitLabel) {
      submitLabel.textContent = t("home.contact.sending", "Sending…");
    }
    setStatus("", "");

    try {
      const res = await fetch("/api/contact", {
        method: "POST",
        headers: { Accept: "application/json", "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        throw new Error("request failed");
      }
      form.reset();
      setStatus(t("home.contact.success", "Thanks — your message was sent."), "success");
    } catch (_) {
      setStatus(t("home.contact.error", "Could not send right now. Please try again."), "error");
    } finally {
      if (submitBtn) submitBtn.disabled = false;
      if (submitLabel) {
        submitLabel.textContent =
          previousLabel || t("home.contact.send", "Send message");
      }
    }
  });
})();
