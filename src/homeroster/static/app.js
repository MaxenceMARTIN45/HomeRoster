/* HomeRoster: small progressive enhancements (no external library, CSP-friendly). */
(function () {
  "use strict";
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));

  /* --- Translated strings, rendered by the server in <script type="application/json" id="i18n"> -------- */
  let I18N = {};
  try { I18N = JSON.parse(($("#i18n") || {}).textContent || "{}"); } catch (e) { I18N = {}; }
  const t = (key, fallback) => I18N[key] || fallback || key;

  /* --- Show / hide a password ----------------------------------------------------------------------- */
  $$("[data-toggle-password]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const input = document.getElementById(btn.dataset.togglePassword);
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      btn.querySelector("use").setAttribute("href", "/static/icons.svg#" + (show ? "eye-off" : "eye"));
      btn.setAttribute("aria-label", show ? t("hide_password") : t("show_password"));
    });
  });

  /* --- Selects that apply right away (language of the setup page) ------------------------------------ */
  $$("select[data-autosubmit]").forEach((select) => {
    select.addEventListener("change", () => select.form.submit());
  });

  /* --- A select that shows the matching group of fields (Settings > Services) -------------------------- */
  $$("form[data-switch]").forEach((form) => {
    const select = $("[data-switch-select]", form);
    const update = () => $$("[data-switch-panel]", form).forEach((panel) => {
      const on = panel.dataset.switchPanel === select.value;
      panel.hidden = !on;
      panel.disabled = !on;
    });
    select.addEventListener("change", update);
    update();
  });

  /* --- Generate a password that is easy to read out loud ---------------------------------------------- */
  const WORDS = t("password_words", "tiger lamp orchard cliff storm peony moon compass").split(/\s+/);
  const rand = (n) => { const a = new Uint32Array(1); crypto.getRandomValues(a); return a[0] % n; };
  const cap = (w) => w.charAt(0).toUpperCase() + w.slice(1);
  const word = () => cap(WORDS[rand(WORDS.length)]);
  const generate = () => `${word()}-${word()}-${rand(90) + 10}-${word()}`;

  $$("[data-generate]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const pw = generate();
      btn.dataset.generate.split(",").forEach((id) => {
        const input = document.getElementById(id.trim());
        if (input) { input.value = pw; input.type = "text"; input.dispatchEvent(new Event("input")); }
      });
    });
  });

  /* --- Strength meter ---------------------------------------------------------------------------------- */
  const score = (pw) => {
    if (!pw) return 0;
    let s = Math.min(pw.length, 20) * 3;
    if (/[a-z]/.test(pw)) s += 8;
    if (/[A-Z]/.test(pw)) s += 8;
    if (/\d/.test(pw)) s += 8;
    if (/[^A-Za-z0-9]/.test(pw)) s += 10;
    if (new Set(pw).size < 5) s -= 30;
    if (pw.length < 10) s = Math.min(s, 30);
    return Math.max(0, Math.min(100, s));
  };
  $$("[data-strength]").forEach((bar) => {
    const input = document.getElementById(bar.dataset.strength);
    const fill = bar.querySelector("span");
    const label = document.getElementById(bar.dataset.strength + "-label");
    const update = () => {
      const s = score(input.value);
      fill.style.width = s + "%";
      fill.style.background = s < 40 ? "var(--err)" : s < 70 ? "var(--warn)" : "var(--ok)";
      if (label) label.textContent = !input.value ? "" : (s < 40 ? t("strength_weak") : s < 70 ? t("strength_fair") : t("strength_strong")) + " · ";
    };
    input.addEventListener("input", update);
    update();
  });

  /* --- Confirmation must match --------------------------------------------------------------------------- */
  $$("[data-match]").forEach((input) => {
    const other = document.getElementById(input.dataset.match);
    const msg = document.getElementById(input.id + "-msg");
    const check = () => {
      const bad = input.value && other.value !== input.value;
      input.classList.toggle("invalid", !!bad);
      if (msg) msg.classList.toggle("hidden", !bad);
    };
    input.addEventListener("input", check);
    other.addEventListener("input", check);
  });

  /* --- Live preview of the id and device name ------------------------------------------------------------ */
  const slugify = (s) => (s || "").normalize("NFKD").replace(/[̀-ͯ]/g, "")
    .replace(/[^a-zA-Z0-9]+/g, "_").replace(/^_+|_+$/g, "").replace(/_+/g, "_").toLowerCase().slice(0, 32);
  const nameInput = $("#display_name"), slugInput = $("#slug"), deviceInput = $("#device");
  if (nameInput && slugInput) {
    let touched = !!slugInput.value;
    const preview = () => {
      if (!touched) slugInput.value = slugify(nameInput.value);
      const s = slugInput.value || t("slug_placeholder", "name");
      const d = slugify((deviceInput && deviceInput.value) || "") || "phone";
      $$("[data-peer-preview]").forEach((el) => { el.textContent = `${s} - ${d}`; });
    };
    nameInput.addEventListener("input", preview);
    slugInput.addEventListener("input", () => { touched = true; slugInput.value = slugify(slugInput.value); preview(); });
    if (deviceInput) deviceInput.addEventListener("input", preview);
    preview();
  }

  /* --- Search in the people list ---------------------------------------------------------------------------- */
  const search = $("#search");
  if (search) {
    search.addEventListener("input", () => {
      const q = slugify(search.value);
      $$("[data-search]").forEach((row) => { row.classList.toggle("hidden", !!q && !row.dataset.search.includes(q)); });
    });
  }

  /* --- QR code dialog ---------------------------------------------------------------------------------------- */
  const dlg = $("#qr-dialog");
  const openQr = async (url, title, confUrl) => {
    if (!dlg) return;
    $("#qr-title", dlg).textContent = title || t("qr_title", "QR code");
    const box = $("#qr-content", dlg);
    box.innerHTML = '<div class="spinner"></div>';
    const dl = $("#qr-download", dlg);
    if (dl) dl.href = confUrl || "#";
    dlg.classList.remove("fullscreen");
    dlg.showModal();
    try {
      const r = await fetch(url, { credentials: "same-origin" });
      box.innerHTML = await r.text();
    } catch (e) {
      box.innerHTML = `<p class="qr-error">${t("qr_error")}</p>`;
    }
  };
  $$("[data-qr]").forEach((btn) => btn.addEventListener("click", () => openQr(btn.dataset.qr, btn.dataset.qrTitle, btn.dataset.qrConf)));
  if (dlg) {
    $$("[data-close]", dlg).forEach((b) => b.addEventListener("click", () => dlg.close()));
    const big = $("#qr-big", dlg);
    if (big) big.addEventListener("click", () => {
      const on = dlg.classList.toggle("fullscreen");
      big.querySelector("span").textContent = on ? t("qr_shrink") : t("qr_enlarge");
    });
    dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
    const auto = document.body.dataset.openQr;
    if (auto) { const btn = $(`[data-peer-id="${CSS.escape(auto)}"]`); if (btn) btn.click(); }
  }

  /* --- Simple confirmations ------------------------------------------------------------------------------------ */
  $$("form[data-confirm]").forEach((f) => f.addEventListener("submit", (e) => { if (!confirm(f.dataset.confirm)) e.preventDefault(); }));

  /* --- "Busy" state on submit ------------------------------------------------------------------------------------ */
  $$("form").forEach((f) => f.addEventListener("submit", (e) => {
    if (e.defaultPrevented) return;
    const b = f.querySelector("button[type=submit]");
    if (b) { b.setAttribute("aria-busy", "true"); setTimeout(() => { b.disabled = true; }, 0); }
  }));

  /* --- 6-digit code: submit automatically ------------------------------------------------------------------------- */
  $$("[data-autosubmit]").forEach((input) => {
    input.addEventListener("input", () => {
      input.value = input.value.replace(/\D/g, "").slice(0, 6);
      if (input.value.length === 6) input.form.requestSubmit();
    });
  });

  /* --- Flash messages ------------------------------------------------------------------------------------------------ */
  $$(".flash .close").forEach((b) => b.addEventListener("click", () => b.closest(".flash").remove()));
  if (window.history && location.search.includes("flash=")) {
    const u = new URL(location.href); u.searchParams.delete("flash"); u.searchParams.delete("kind");
    history.replaceState(null, "", u.pathname + (u.search || ""));
  }

  /* --- Live creation: follow the steps --------------------------------------------------------------------------------- */
  const job = $("#job");
  if (job) {
    const icons = { ok: "check", error: "x", skip: "x", pending: "", running: "" };
    const render = (steps) => {
      steps.forEach((s) => {
        const row = $(`[data-step="${s.key}"]`, job);
        if (!row) return;
        row.className = "step " + s.status;
        const bullet = $(".bullet", row);
        bullet.innerHTML = s.status === "running" ? '<span class="spinner"></span>'
          : icons[s.status] ? `<svg class="icon"><use href="/static/icons.svg#${icons[s.status]}"></use></svg>` : "";
        $(".msg", row).textContent = s.message || (s.status === "pending" ? t("pending") : s.status === "running" ? t("running") : "");
      });
    };
    const poll = async () => {
      try {
        const r = await fetch(job.dataset.api, { credentials: "same-origin" });
        if (r.ok) {
          const data = await r.json();
          render(data.steps);
          if (data.status !== "running") {
            $("#job-running").classList.add("hidden");
            $(data.status === "done" ? "#job-done" : "#job-partial").classList.remove("hidden");
            const pid = data.result.person_id, device = data.result.device_id;
            $$("[data-person-link]").forEach((a) => { a.href = `/people/${pid}`; });
            if (device) {
              $("#job-qr").classList.remove("hidden");
              const btn = $("#job-qr-open");
              btn.dataset.qr = `/people/${pid}/devices/qr?device=${encodeURIComponent(device)}`;
              btn.dataset.qrConf = `/people/${pid}/devices/conf?device=${encodeURIComponent(device)}`;
              btn.dataset.qrTitle = data.result.device_name || t("qr_title");
              const r2 = await fetch(btn.dataset.qr, { credentials: "same-origin" });
              $("#job-qr-inline").innerHTML = await r2.text();
              $("#job-qr-name").textContent = data.result.device_name || "";
              btn.addEventListener("click", () => openQr(btn.dataset.qr, btn.dataset.qrTitle, btn.dataset.qrConf));
              $("#job-qr-download").href = btn.dataset.qrConf;
            }
            return;
          }
        }
      } catch (e) { /* retry below */ }
      setTimeout(poll, 800);
    };
    poll();
  }
})();
