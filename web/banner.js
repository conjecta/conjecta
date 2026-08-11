(function () {
  const phrases = {
    en: [
      "Intuition meets rigor.",
      "A whisper becomes proof.",
      "Conjecture precedes certainty.",
    ],
    zh: ["直觉与严谨相遇。", "低语成证。", "猜想先于确然。"],
  };

  const textEl = document.getElementById("phrase-text");
  const cursorEl = document.querySelector(".phrase-cursor");
  if (!textEl) return;

  const reducedMotion = window.matchMedia(
    "(prefers-reduced-motion: reduce)",
  ).matches;

  let isVisible = !document.hidden;

  const typeSpeed = 42;
  const deleteSpeed = 24;
  const pauseAfterType = 2600;
  const pauseAfterDelete = 420;

  let timer = null;
  let charIndex = 0;
  let deleting = false;
  let phraseIndex = 0;
  let currentPhrases = phrases.en;

  function clearTimer() {
    if (timer !== null) {
      window.clearTimeout(timer);
      timer = null;
    }
  }

  function schedule(fn, delay) {
    clearTimer();
    timer = window.setTimeout(fn, delay);
  }

  function resolveLang() {
    if (window.ConjectaI18n && typeof window.ConjectaI18n.getLang === "function") {
      return window.ConjectaI18n.getLang() === "zh" ? "zh" : "en";
    }
    return "en";
  }

  function activePhrase() {
    return currentPhrases[phraseIndex % currentPhrases.length];
  }

  function tickType() {
    const phrase = activePhrase();

    if (!deleting) {
      charIndex += 1;
      textEl.textContent = phrase.slice(0, charIndex);

      if (charIndex === phrase.length) {
        schedule(() => {
          deleting = true;
          tickType();
        }, pauseAfterType);
        return;
      }

      schedule(tickType, typeSpeed);
      return;
    }

    charIndex -= 1;
    textEl.textContent = phrase.slice(0, charIndex);

    if (charIndex === 0) {
      deleting = false;
      phraseIndex = (phraseIndex + 1) % currentPhrases.length;
      schedule(tickType, pauseAfterDelete);
      return;
    }

    schedule(tickType, deleteSpeed);
  }

  function start(lang) {
    clearTimer();
    currentPhrases = phrases[lang === "zh" ? "zh" : "en"];
    phraseIndex = 0;
    charIndex = 0;
    deleting = false;
    textEl.textContent = "";

    if (reducedMotion) {
      if (cursorEl) cursorEl.style.display = "none";
      textEl.textContent = currentPhrases[0];
      return;
    }

    if (!isVisible) return;

    if (cursorEl) cursorEl.style.display = "";
    schedule(tickType, 500);
  }

  function handleVisibilityChange() {
    if (document.hidden) {
      isVisible = false;
      clearTimer();
    } else {
      isVisible = true;
      if (!reducedMotion && timer === null) {
        schedule(tickType, 420);
      }
    }
  }

  document.addEventListener("visibilitychange", handleVisibilityChange);

  document.addEventListener("conjecta:lang", (event) => {
    const lang =
      event && event.detail && event.detail.lang
        ? event.detail.lang
        : resolveLang();
    start(lang);
  });

  start(resolveLang());
})();
