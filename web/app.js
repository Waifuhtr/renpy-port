/* ==========================================================================
   Ren'Py -> Android Paketleyici — arayüz mantığı
   Derleme günlüğü Server-Sent Events ile canlı akar.
   ========================================================================== */

(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);

  const form = $("build-form");
  const buildBtn = $("build-btn");
  const consoleEl = $("console");
  const statusPill = $("status-pill");
  const statusText = $("status-text");
  const resultsBox = $("results");
  const resultsList = $("results-list");
  const dropzone = $("dropzone");
  const zipInput = $("project_zip");
  const zipName = $("zip-name");

  let eventSource = null;
  let logBuffer = [];
  let autoScroll = true;

  // Derleme günlüğü binlerce satıra çıkabiliyor (özellikle Gradle/Android
  // derlemesinin sonunda, çok sayıda satır kısa sürede birden gelince).
  // Her satırı geldiği anda tek tek DOM'a eklemek — özellikle her seferinde
  // scrollTop okuyup yazmak — tarayıcıyı sayfa genelinde donma hissi
  // verecek kadar yorabiliyor. Bunun yerine satırları bir kuyrukta
  // biriktirip tek bir animasyon karesinde toplu halde ekliyoruz, ve
  // DOM'daki satır sayısını sınırlı tutuyoruz. Tam günlük (kopyalama ve
  // sunucudan indirme için) her zaman logBuffer'da / sunucuda eksiksiz
  // kalır — yalnızca EKRANDA GÖRÜNEN satır sayısı sınırlanıyor.
  const MAX_RENDERED_LINES = 2500;
  const BURST_ANIMATION_THRESHOLD = 40;
  let pendingLines = [];
  let flushHandle = null;
  let trimmedCount = 0;
  let trimNoticeEl = null;
  let consoleHasContent = false;

  // --- Küçük yardımcılar -------------------------------------------------

  function toast(message, isBad = false) {
    const el = document.createElement("div");
    el.className = "toast" + (isBad ? " bad" : "");
    el.textContent = message;
    document.body.appendChild(el);
    requestAnimationFrame(() => el.classList.add("show"));
    setTimeout(() => {
      el.classList.remove("show");
      setTimeout(() => el.remove(), 300);
    }, 2600);
  }

  function setStatus(state, text) {
    statusPill.dataset.state = state;
    statusText.textContent = text;
  }

  /** Satırın önem derecesine göre renk sınıfı seçer. */
  function lineClass(line) {
    const lower = line.toLowerCase();
    if (/^hata:|hata ile sonuçlandı|\berror\b|exception|failed|başarısız/.test(lower)) {
      return "l-error";
    }
    if (/^uyarı|warning|uyari/.test(lower)) return "l-warn";
    if (/bitti!|başarılı|onaylandı|tamamlandı|üretildi/.test(lower)) return "l-good";
    if (/^otomatik|^bilgi:|^temizlik:|^proje bulundu|aerokey|imzalama:/.test(lower)) {
      return "l-info";
    }
    return "";
  }

  /** Satırı kuyruğa ekler; gerçek DOM güncellemesi flushLines()'da olur. */
  function appendLine(line) {
    logBuffer.push(line);
    pendingLines.push(line);
    if (flushHandle === null) {
      flushHandle = requestAnimationFrame(flushLines);
    }
  }

  /**
   * Kuyruktaki tüm satırları TEK seferde DOM'a yazar.
   *
   * Bir DocumentFragment kullanmak, her satır için ayrı ayrı appendChild
   * çağırmaktan (ve dolayısıyla ayrı ayrı reflow tetiklemekten) kaçınır.
   * scrollTop da yalnızca bu toplu işlemin SONUNDA bir kez okunup yazılır —
   * bu, çoğu jank'in asıl kaynağıdır (her satırda scrollTop okumak,
   * tarayıcıyı senkron bir layout hesabına zorlar).
   */
  function flushLines() {
    flushHandle = null;
    if (!pendingLines.length) return;

    if (!consoleHasContent) {
      consoleEl.innerHTML = "";
      consoleHasContent = true;
    }

    // Çok sayıda satır tek karede birden gelmişse (derleme sonunda tipik),
    // her birine giriş animasyonu oynatmak başlı başına bir performans
    // yüküdür; böyle bir patlamada animasyonu atlayıp anında gösteriyoruz.
    const skipAnimation = pendingLines.length > BURST_ANIMATION_THRESHOLD;

    const fragment = document.createDocumentFragment();
    for (const line of pendingLines) {
      const span = document.createElement("span");
      span.className = "line " + lineClass(line) + (skipAnimation ? " no-anim" : "");
      span.textContent = line || " ";
      fragment.appendChild(span);
    }
    pendingLines = [];
    consoleEl.appendChild(fragment);

    trimRenderedLines();

    if (autoScroll) consoleEl.scrollTop = consoleEl.scrollHeight;
  }

  /**
   * Görünen satır sayısını sınırlı tutar; eskiler DOM'dan atılır (veri
   * kaybı yok — tam günlük logBuffer'da ve sunucuda duruyor, "Kopyala" ve
   * "Aç" bu sınırdan etkilenmez).
   */
  function trimRenderedLines() {
    if (consoleEl.childElementCount <= MAX_RENDERED_LINES) return;

    if (!trimNoticeEl) {
      trimNoticeEl = document.createElement("span");
      trimNoticeEl.className = "line l-trim-notice";
      consoleEl.insertBefore(trimNoticeEl, consoleEl.firstChild);
    }

    // Bildirim satırının hemen ardından gelen (yani en eski) satırları
    // silip bildirimin kendisini hiç dokunmadan başta tutuyoruz.
    while (consoleEl.childElementCount > MAX_RENDERED_LINES) {
      const victim = trimNoticeEl.nextElementSibling;
      if (!victim) break;
      consoleEl.removeChild(victim);
      trimmedCount += 1;
    }

    trimNoticeEl.textContent =
      `— performans için ilk ${trimmedCount} satır ekrandan gizlendi ` +
      `(tam günlük "Kopyala" ya da "Aç" ile eksiksiz) —`;
  }

  function resetConsole() {
    if (flushHandle !== null) {
      cancelAnimationFrame(flushHandle);
      flushHandle = null;
    }
    logBuffer = [];
    pendingLines = [];
    trimmedCount = 0;
    trimNoticeEl = null;
    consoleHasContent = false;
    consoleEl.innerHTML =
      '<span class="console-empty">Derleme günlüğü burada canlı olarak akacak.</span>';
    resultsBox.hidden = true;
    resultsList.innerHTML = "";
  }

  // Kullanıcı yukarı kaydırdıysa otomatik kaydırmayı bırak — uzun bir
  // günlüğü incelerken alta zıplamak sinir bozucudur.
  consoleEl.addEventListener("scroll", () => {
    const nearBottom =
      consoleEl.scrollHeight - consoleEl.scrollTop - consoleEl.clientHeight < 60;
    autoScroll = nearBottom;
  });

  // --- Başlangıç yapılandırması -----------------------------------------

  async function loadConfig() {
    try {
      const res = await fetch("/api/config");
      const cfg = await res.json();

      $("renpy_version").value = cfg.renpy_version;
      $("renpy_version").placeholder = cfg.renpy_version;
      $("version-hint").textContent = `İmaja gömülü sürüm: ${cfg.renpy_version} (en hızlısı)`;

      $("aerokey_base_url").value = cfg.aerokey_base_url;
      $("aerokey_key_page").value = cfg.aerokey_key_page;
      $("aerokey_game_id").placeholder = cfg.suggested_game_id;

      const meta = $("masthead-meta");
      meta.innerHTML = "";
      meta.appendChild(chip(`Ren'Py <b>${cfg.renpy_version}</b>`));
      meta.appendChild(chip(`Sonraki kimlik <b>${cfg.suggested_game_id}</b>`));
      meta.appendChild(
        cfg.persistent_storage
          ? chip("Kalıcı disk <b>açık</b>")
          : chip("Kalıcı disk <b>yok</b> — anahtarı yedekleyin", true)
      );
    } catch (err) {
      toast("Yapılandırma okunamadı.", true);
    }
  }

  function chip(html, warn = false) {
    const el = document.createElement("span");
    el.className = "meta-chip" + (warn ? " warn" : "");
    el.innerHTML = html;
    return el;
  }

  // --- Dosya seçimi ------------------------------------------------------

  function showZipName() {
    const file = zipInput.files && zipInput.files[0];
    if (file) {
      const mb = (file.size / (1024 * 1024)).toFixed(1);
      zipName.textContent = `${file.name} · ${mb} MB`;
      dropzone.classList.add("filled");
    } else {
      zipName.textContent = "Dosyayı sürükleyin ya da seçin";
      dropzone.classList.remove("filled");
    }
  }

  zipInput.addEventListener("change", showZipName);

  ["dragenter", "dragover"].forEach((evt) =>
    dropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      dropzone.classList.add("dragover");
    })
  );

  ["dragleave", "drop"].forEach((evt) =>
    dropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      dropzone.classList.remove("dragover");
    })
  );

  dropzone.addEventListener("drop", (e) => {
    const files = e.dataTransfer && e.dataTransfer.files;
    if (files && files.length) {
      zipInput.files = files;
      showZipName();
    }
  });

  // --- AeroKey bölümü ----------------------------------------------------

  const aerokeyToggle = $("aerokey_enabled");
  const aerokeyBody = $("aerokey-body");

  aerokeyToggle.addEventListener("change", () => {
    aerokeyBody.hidden = !aerokeyToggle.checked;
  });

  $("refresh-game-id").addEventListener("click", async () => {
    const pkg = $("manual_package").value.trim();
    try {
      const res = await fetch(`/api/game-id?package=${encodeURIComponent(pkg)}`);
      const data = await res.json();
      $("aerokey_game_id").value = data.game_id;
      toast(
        data.reused
          ? `Bu pakete daha önce ${data.game_id} atanmıştı.`
          : `Sıradaki boş kimlik: ${data.game_id}`
      );
    } catch (err) {
      toast("Kimlik alınamadı.", true);
    }
  });

  // --- İmza anahtarı -----------------------------------------------------

  $("download-keystore").addEventListener("click", () => {
    window.location.href = "/api/keystore/auto";
  });

  $("show-keystore-info").addEventListener("click", async () => {
    const box = $("keystore-info");
    if (!box.hidden) {
      box.hidden = true;
      return;
    }
    try {
      const res = await fetch("/api/keystore/auto/info");
      const info = await res.json();
      box.innerHTML =
        `<span>alias:</span> ${escapeHtml(info.alias)}<br>` +
        `<span>şifre:</span> ${escapeHtml(info.password)}`;
      box.hidden = false;
    } catch (err) {
      toast("Anahtar bilgisi alınamadı.", true);
    }
  });

  function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  // --- Günlük araçları ---------------------------------------------------

  $("copy-log").addEventListener("click", async () => {
    if (!logBuffer.length) return toast("Kopyalanacak bir şey yok.");
    try {
      await navigator.clipboard.writeText(logBuffer.join("\n"));
      toast("Günlük panoya kopyalandı.");
    } catch (err) {
      toast("Panoya erişilemedi.", true);
    }
  });

  $("clear-log").addEventListener("click", resetConsole);

  // --- Derleme -----------------------------------------------------------

  form.addEventListener("submit", async (e) => {
    e.preventDefault();

    if (!zipInput.files || !zipInput.files.length) {
      toast("Önce bir Ren'Py proje ZIP dosyası seçin.", true);
      return;
    }
    if (!$("want_apk").checked && !$("want_aab").checked) {
      toast("En az bir çıktı formatı seçin (APK ve/veya AAB).", true);
      return;
    }

    const data = new FormData();
    data.append("project_zip", zipInput.files[0]);

    if ($("icon").files[0]) data.append("icon", $("icon").files[0]);
    if ($("banner").files[0]) data.append("banner", $("banner").files[0]);
    if ($("keystore").files[0]) data.append("keystore", $("keystore").files[0]);

    const textFields = [
      "renpy_version", "package_prefix",
      "manual_name", "manual_package", "manual_version",
      "keystore_alias", "keystore_password",
      "aerokey_base_url", "aerokey_key_page", "aerokey_game_id",
    ];
    textFields.forEach((id) => data.append(id, $(id).value));

    const boolFields = [
      "want_apk", "want_aab", "aerokey_enabled", "aerokey_leaderboard",
      "aerokey_survey", "aerokey_profile", "aerokey_bug_report",
      "aerokey_notifications",
    ];
    boolFields.forEach((id) => data.append(id, $(id).checked ? "true" : "false"));

    setBusy(true);
    resetConsole();
    setStatus("running", "Yükleniyor…");
    appendLine("Proje sunucuya yükleniyor…");

    try {
      const res = await fetch("/api/build", { method: "POST", body: data });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: "Bilinmeyen hata" }));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const { job_id } = await res.json();
      listen(job_id);
    } catch (err) {
      appendLine("Hata: " + err.message);
      setStatus("error", "Başlatılamadı");
      setBusy(false);
    }
  });

  function listen(jobId) {
    setStatus("running", "Derleniyor…");
    autoScroll = true;

    eventSource = new EventSource(`/api/jobs/${jobId}/stream`);

    eventSource.addEventListener("log", (e) => {
      appendLine(JSON.parse(e.data));
    });

    eventSource.addEventListener("done", (e) => {
      const payload = JSON.parse(e.data);
      closeStream();
      setBusy(false);

      if (payload.status === "success") {
        setStatus("success", "Tamamlandı");
        showResults(payload.files, jobId);
        toast("Derleme tamamlandı.");
      } else {
        setStatus("error", "Hata");
        toast("Derleme başarısız oldu.", true);
      }
    });

    eventSource.onerror = () => {
      // Tarayıcı bağlantıyı kendiliğinden yeniden kurmaya çalışır; ancak
      // iş çoktan bittiyse akış kapanmış olabilir. Kullanıcıyı belirsizlikte
      // bırakmamak için durumu bir kez bildiriyoruz.
      if (eventSource && eventSource.readyState === EventSource.CLOSED) {
        closeStream();
        setBusy(false);
        setStatus("error", "Bağlantı koptu");
        appendLine("Uyarı: Sunucuyla canlı bağlantı koptu. Derleme arka planda sürüyor olabilir.");
      }
    };
  }

  function closeStream() {
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
  }

  function showResults(files, jobId) {
    if (!files || !files.length) return;
    resultsList.innerHTML = "";
    files.forEach((file) => {
      const item = document.createElement("div");
      item.className = "result-item";

      const name = document.createElement("span");
      name.className = "name";
      name.textContent = file.name;

      const link = document.createElement("a");
      link.href = file.url;
      link.textContent = "İndir";
      link.setAttribute("download", file.name);

      item.append(name, link);
      resultsList.appendChild(item);
    });

    const logLink = document.createElement("div");
    logLink.className = "result-item";
    logLink.innerHTML =
      '<span class="name">derleme-gunlugu.txt</span>' +
      `<a href="/api/jobs/${jobId}/log" target="_blank" rel="noopener">Aç</a>`;
    resultsList.appendChild(logLink);

    resultsBox.hidden = false;
  }

  function setBusy(busy) {
    buildBtn.disabled = busy;
    buildBtn.classList.toggle("busy", busy);
    buildBtn.querySelector(".btn-label").textContent = busy
      ? "Derleniyor…"
      : "Android Paketini Oluştur";
  }

  // Derleme sürerken sekmeyi kapatmaya çalışırsa uyar.
  window.addEventListener("beforeunload", (e) => {
    if (eventSource) {
      e.preventDefault();
      e.returnValue = "";
    }
  });

  loadConfig();
})();
