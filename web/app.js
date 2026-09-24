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
  const cacheField = $("cache-field");
  const cacheList = $("cache-list");
  const cacheSize = $("cache-size");
  const uploadProgress = $("upload-progress");
  const uploadBarFill = $("upload-bar-fill");
  const uploadStatus = $("upload-status");
  const uploadCancel = $("upload-cancel");

  let eventSource = null;
  let logBuffer = [];
  let autoScroll = true;

  // Seçili önbellek girdisi. Derleme bunu gönderiyor; dolayısıyla dosya
  // YALNIZCA BİR KEZ yükleniyor, sonraki derlemeler anında başlıyor.
  let selectedUploadId = null;
  let activeUpload = null; // süren XHR
  const JOB_KEY = "renpy-porter-job";
  const UPLOAD_KEY = "renpy-porter-upload";

  function store(key, value) {
    try {
      if (value === null) localStorage.removeItem(key);
      else localStorage.setItem(key, value);
    } catch (err) {
      /* gizli sekmede localStorage kapalı olabilir; sorun değil */
    }
  }

  function recall(key) {
    try {
      return localStorage.getItem(key);
    } catch (err) {
      return null;
    }
  }

  function humanSize(bytes) {
    if (!bytes && bytes !== 0) return "";
    if (bytes < 1024) return bytes + " B";
    const kb = bytes / 1024;
    if (kb < 1024) return kb.toFixed(0) + " KB";
    const mb = kb / 1024;
    if (mb < 1024) return mb.toFixed(1) + " MB";
    return (mb / 1024).toFixed(2) + " GB";
  }

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

  /**
   * Seçilen dosyayı HEMEN yükler ve sunucudaki önbelleğe aldırır.
   *
   * Yükleme derlemeden ayrıldığı için: dosya bir kez yükleniyor, sonraki
   * derlemeler aynı dosyayı yeniden yüklemeden kullanıyor. XHR
   * kullanılıyor çünkü fetch() yükleme ilerlemesi bildirmiyor.
   */
  function uploadFile(file) {
    if (!file) return;
    if (activeUpload) activeUpload.abort();

    const data = new FormData();
    data.append("project_zip", file);

    const xhr = new XMLHttpRequest();
    activeUpload = xhr;

    uploadProgress.hidden = false;
    uploadCancel.hidden = false;
    uploadBarFill.style.width = "0%";
    uploadStatus.textContent = `${file.name} · ${humanSize(file.size)} · yükleniyor…`;
    dropzone.classList.remove("filled");
    zipName.textContent = file.name;

    xhr.upload.addEventListener("progress", (e) => {
      if (!e.lengthComputable) return;
      const pct = Math.round((e.loaded / e.total) * 100);
      uploadBarFill.style.width = pct + "%";
      uploadStatus.textContent =
        `${file.name} · ${humanSize(e.loaded)} / ${humanSize(e.total)} (%${pct})`;
    });

    xhr.addEventListener("load", () => {
      activeUpload = null;
      let payload = null;
      try {
        payload = JSON.parse(xhr.responseText);
      } catch (err) {
        payload = null;
      }

      if (xhr.status !== 200 || !payload || !payload.upload) {
        uploadProgress.hidden = true;
        const detail = (payload && payload.detail) || `HTTP ${xhr.status}`;
        toast("Yükleme başarısız: " + detail, true);
        zipName.textContent = "Dosyayı sürükleyin ya da seçin";
        return;
      }

      uploadBarFill.style.width = "100%";
      uploadCancel.hidden = true;
      uploadStatus.textContent = "Yüklendi — önbellekte, tekrar yüklemeye gerek yok.";
      setTimeout(() => {
        uploadProgress.hidden = true;
      }, 2200);

      selectUpload(payload.upload.id);
      zipInput.value = "";
      loadUploads();
      toast("Dosya yüklendi ve önbelleğe alındı.");
    });

    xhr.addEventListener("error", () => {
      activeUpload = null;
      uploadProgress.hidden = true;
      toast("Yükleme sırasında bağlantı koptu.", true);
    });

    xhr.addEventListener("abort", () => {
      activeUpload = null;
      uploadProgress.hidden = true;
    });

    xhr.open("POST", "/api/uploads");
    xhr.send(data);
  }

  uploadCancel.addEventListener("click", () => {
    if (activeUpload) activeUpload.abort();
  });

  zipInput.addEventListener("change", () => {
    const file = zipInput.files && zipInput.files[0];
    if (file) uploadFile(file);
  });

  // --- Önbellek listesi --------------------------------------------------

  function selectUpload(id) {
    selectedUploadId = id;
    store(UPLOAD_KEY, id);
    renderCacheSelection();
  }

  function renderCacheSelection() {
    let secili = null;
    cacheList.querySelectorAll(".cache-item").forEach((el) => {
      const aktif = el.dataset.id === selectedUploadId;
      el.classList.toggle("active", aktif);
      if (aktif) secili = el.dataset.name;
    });

    if (secili) {
      zipName.textContent = secili + " · önbellekten";
      dropzone.classList.add("filled");
    } else {
      zipName.textContent = "Dosyayı sürükleyin ya da seçin";
      dropzone.classList.remove("filled");
    }
  }

  async function loadUploads() {
    let data;
    try {
      const res = await fetch("/api/uploads");
      data = await res.json();
    } catch (err) {
      return;
    }

    const items = data.uploads || [];
    cacheField.hidden = items.length === 0;
    cacheSize.textContent = items.length
      ? `· ${items.length} dosya, ${humanSize(data.total_bytes)}`
      : "";

    // Seçili girdi artık yoksa (Space yeniden başlamış olabilir) seçimi bırak.
    if (selectedUploadId && !items.some((u) => u.id === selectedUploadId)) {
      selectedUploadId = null;
      store(UPLOAD_KEY, null);
    }
    // Hiç seçim yoksa en son kullanılanı seç: en sık istenen davranış bu.
    if (!selectedUploadId && items.length) {
      selectedUploadId = items[0].id;
      store(UPLOAD_KEY, selectedUploadId);
    }

    cacheList.innerHTML = "";
    items.forEach((u) => {
      const row = document.createElement("div");
      row.className = "cache-item";
      row.dataset.id = u.id;
      row.dataset.name = u.name;

      const name = document.createElement("div");
      name.className = "cache-name";
      const kullanim = u.uses ? ` · ${u.uses} kez derlendi` : "";
      name.innerHTML =
        `${escapeHtml(u.name)}<em>${humanSize(u.size)}${escapeHtml(kullanim)}</em>`;

      const drop = document.createElement("button");
      drop.type = "button";
      drop.className = "cache-drop";
      drop.textContent = "Sil";
      drop.addEventListener("click", async (e) => {
        e.stopPropagation();
        try {
          const res = await fetch(`/api/uploads/${u.id}`, { method: "DELETE" });
          if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || `HTTP ${res.status}`);
          }
          if (selectedUploadId === u.id) {
            selectedUploadId = null;
            store(UPLOAD_KEY, null);
          }
          loadUploads();
        } catch (err) {
          toast(err.message, true);
        }
      });

      row.addEventListener("click", () => selectUpload(u.id));
      row.append(name, drop);
      cacheList.appendChild(row);
    });

    renderCacheSelection();
  }

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
    if (files && files.length) uploadFile(files[0]);
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

    if (activeUpload) {
      toast("Dosya hâlâ yükleniyor; bitmesini bekleyin.", true);
      return;
    }
    if (!selectedUploadId) {
      toast("Önce bir Ren'Py proje ZIP dosyası seçin.", true);
      return;
    }
    if (!$("want_apk").checked && !$("want_aab").checked) {
      toast("En az bir çıktı formatı seçin (APK ve/veya AAB).", true);
      return;
    }

    const data = new FormData();
    // Dosya YENİDEN YÜKLENMİYOR: sunucudaki önbellek kimliği gönderiliyor.
    data.append("cached_zip_id", selectedUploadId);

    if ($("icon").files[0]) data.append("icon", $("icon").files[0]);
    if ($("banner").files[0]) data.append("banner", $("banner").files[0]);
    if ($("translation").files[0]) data.append("translation", $("translation").files[0]);
    if ($("keystore").files[0]) data.append("keystore", $("keystore").files[0]);

    const textFields = [
      "renpy_version", "package_prefix",
      "manual_name", "manual_package", "manual_version",
      "keystore_alias", "keystore_password",
      "aerokey_base_url", "aerokey_key_page", "aerokey_game_id",
      "translation_mode",
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
    setStatus("running", "Başlatılıyor…");
    appendLine("Derleme başlatılıyor (proje önbellekten alınıyor, yükleme yok)…");

    try {
      const res = await fetch("/api/build", { method: "POST", body: data });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: "Bilinmeyen hata" }));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const { job_id } = await res.json();
      listen(job_id);
      loadUploads();
    } catch (err) {
      appendLine("Hata: " + err.message);
      setStatus("error", "Başlatılamadı");
      setBusy(false);
    }
  });

  function listen(jobId, reconnected = false) {
    setStatus("running", "Derleniyor…");
    autoScroll = true;
    store(JOB_KEY, jobId);

    if (reconnected) {
      resetConsole();
      appendLine("Süren derlemeye yeniden bağlanıldı; günlük baştan yükleniyor…");
      setBusy(true);
    }

    eventSource = new EventSource(`/api/jobs/${jobId}/stream`);

    eventSource.addEventListener("log", (e) => {
      appendLine(JSON.parse(e.data));
    });

    eventSource.addEventListener("done", (e) => {
      const payload = JSON.parse(e.data);
      closeStream();
      setBusy(false);
      store(JOB_KEY, null);

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
        appendLine(
          "Uyarı: Sunucuyla canlı bağlantı koptu. Derleme arka planda SÜRÜYOR — " +
          "sayfayı yenilerseniz kaldığı yerden bağlanılır."
        );
      }
    };
  }

  /**
   * Sayfa açılışında, arka planda süren bir derlemeye yeniden bağlanır.
   *
   * Derleme sunucuda ayrı bir iş parçacığında çalışıyor; tarayıcının açık
   * olması gerekmiyor. Günlük akışı da her zaman BAŞTAN gönderildiği için
   * hiçbir satır kaybolmuyor.
   */
  async function resumeJob() {
    const jobId = recall(JOB_KEY);
    if (!jobId) return;

    let data;
    try {
      const res = await fetch("/api/jobs");
      data = await res.json();
    } catch (err) {
      return;
    }

    const job = (data.jobs || []).find((j) => j.id === jobId);
    if (!job) {
      store(JOB_KEY, null);
      return;
    }
    if (job.status === "success" || job.status === "error") {
      // Bitmiş: günlüğü göstermek için yine de bağlanıyoruz (akış, biten
      // işte tüm satırları gönderip "done" diyerek kapanıyor).
      listen(jobId, true);
      toast("Önceki derleme siz yokken tamamlanmış.");
      return;
    }

    listen(jobId, true);
    toast("Arka planda süren derlemeye bağlanıldı.");
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

  // Yalnızca YÜKLEME sürerken uyarıyoruz. Derleme sunucuda arka planda
  // çalıştığı için sekmeyi kapatmak onu durdurmaz; yükleme ise tarayıcı
  // kapanınca yarıda kalır (bir HTTP isteği sekmeyle birlikte ölür).
  window.addEventListener("beforeunload", (e) => {
    if (activeUpload) {
      e.preventDefault();
      e.returnValue = "";
    }
  });

  selectedUploadId = recall(UPLOAD_KEY);
  loadConfig();
  loadUploads();
  resumeJob();
})();
