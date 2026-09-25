/* ==========================================================================
   Ren'Py -> Android Paketleyici — arayüz mantığı
   Derleme günlüğü Server-Sent Events ile canlı akar.
   ========================================================================== */

(() => {
  "use strict";

  /**
   * Arayüz sürümü: index.html içindeki `<body data-arayuz="...">` ile
   * AYNI olmak ZORUNDA.
   *
   * NEDEN VAR: bu dosya ile index.html birlikte değişiyor. Space'e
   * yalnızca biri yüklenirse (ör. ZIP klasör yapısı korunmadan açılıp
   * index.html yanlış yere düşerse) JS, HTML'de olmayan bir alanı
   * arıyor ve sessizce patlıyordu — dışarıdan "düğmeye basıyorum hiçbir
   * şey olmuyor" gibi görünüyordu. Artık bu durum sayfanın en üstünde
   * açıkça yazıyor.
   */
  const ARAYUZ_SURUMU = "32";

  const $ = (id) => document.getElementById(id);

  /**
   * Olmayan öğede patlamayan dosya okuyucu.
   *
   * `$("x").files[0]` ifadesi, öğe yoksa TypeError fırlatır ve o anki
   * işleyicinin geri kalanı HİÇ çalışmaz. Bu yardımcı, eksik öğeyi
   * "dosya seçilmemiş" olarak ele alır; eksikliği ayrıca yukarıdaki
   * şerit zaten bildiriyor.
   */
  function secilenDosya(id) {
    const el = $(id);
    if (!el || !el.files) return null;
    return el.files[0] || null;
  }

  /**
   * Sayfanın en üstüne, KAPANMAYAN bir uyarı şeridi koyar.
   *
   * Bildirimler (toast) birkaç saniyede kayboluyor; arayüz/sunucu
   * dosyaları uyumsuzsa bunun ekranda KALMASI gerekiyor.
   */
  function serit(metin) {
    let el = document.getElementById("arayuz-uyarisi");
    if (!el) {
      el = document.createElement("div");
      el.id = "arayuz-uyarisi";
      el.className = "arayuz-uyarisi";
      const kapat = document.createElement("button");
      kapat.type = "button";
      kapat.className = "arayuz-uyarisi-kapat";
      kapat.textContent = "×";
      kapat.addEventListener("click", () => el.remove());
      el.appendChild(document.createElement("span"));
      el.appendChild(kapat);
      document.body.insertBefore(el, document.body.firstChild);
    }
    el.firstChild.textContent = metin;
    return el;
  }

  // Şerit metnini seçmek için: arayüz dosyaları uyumsuz mu?
  // Bilerek genel hata dinleyicilerinden ÖNCE tanımlanıyor: hata
  // kurulumun ilk satırlarında oluşsa bile bu değişkenler hazır olmalı,
  // yoksa hata bildiricinin KENDİSİ "before initialization" ile patlar.
  let arayuzUyumsuz = false;
  let hataBildiriliyor = false;

  /**
   * Öğe yoksa sessizce vazgeçen dinleyici bağlayıcı.
   *
   * `$("x").addEventListener(...)` ifadesi, öğe HTML'de yoksa TypeError
   * fırlatıyor ve bu kurulum kodunun geri kalanını TAMAMEN düşürüyor —
   * yani index.html bir sürüm geride kalırsa sayfadaki her şey ölüyor.
   * Eksikliği zaten `arayuzuDenetle` bildiriyor; burada devam ediyoruz.
   */
  function dinle(id, olay, islev, secenek) {
    const el = $(id);
    if (!el) return false;
    el.addEventListener(olay, islev, secenek);
    return true;
  }

  // Hiçbir JavaScript hatası artık sessiz kalmıyor. Bu dinleyiciler
  // bilerek EN BAŞTA kuruluyor: aşağıdaki kurulum satırlarının kendisi
  // patlasa bile (ör. HTML'de olmayan bir öğe aranırsa) kullanıcı
  // ekranda ne olduğunu görüyor.
  window.addEventListener("error", (e) => {
    bildirBeklenmedik(e.error || e.message);
  });
  window.addEventListener("unhandledrejection", (e) => {
    bildirBeklenmedik(e.reason);
  });

  /**
   * HTML ile bu dosyanın uyumlu olup olmadığını denetler.
   *
   * Uyumsuzluk, gerçekte yaşanmış bir arızanın sebebiydi: Space'e yeni
   * app.js yüklenmiş ama index.html eski kalmıştı; JS, HTML'de olmayan
   * `#fixes` alanını okumaya çalışıp patlıyor ve "Derleme başlat"
   * düğmesi hiçbir tepki vermiyordu. Artık sebep ekranda yazıyor.
   */
  function arayuzuDenetle() {
    const zorunlu = [
      "build-form", "build-btn", "console", "status-pill", "status-text",
      "results", "results-list", "dropzone", "project_zip", "zip-name",
      "cache-field", "cache-list", "cache-size", "upload-progress",
      "upload-bar-fill", "upload-status", "upload-cancel",
      "fixes", "icon", "banner", "translation", "keystore",
      "renpy_version", "package_prefix", "manual_name", "manual_package",
      "manual_version", "keystore_alias", "keystore_password",
      "aerokey_base_url", "aerokey_key_page", "aerokey_game_id",
      "translation_mode", "want_apk", "want_aab", "aerokey_enabled",
      "aerokey_leaderboard", "aerokey_survey", "aerokey_profile",
      "aerokey_bug_report", "aerokey_notifications", "masthead-meta",
      "version-hint", "signing-callout", "keystore-info", "aerokey-body",
      "refresh-game-id", "download-keystore", "show-keystore-info",
      "copy-log", "clear-log",
      "browse-card", "browse-scan", "browse-filter", "browse-info",
      "browse-list", "browse-editor", "browse-file", "browse-blocks",
      "browse-text", "browse-save", "browse-copy", "browse-revert",
      "browse-close", "browse-status", "browse-edits",
    ];
    const eksik = zorunlu.filter((id) => !document.getElementById(id));
    const htmlSurumu = (document.body && document.body.dataset.arayuz) || "";

    if (!eksik.length && htmlSurumu === ARAYUZ_SURUMU) return true;

    arayuzUyumsuz = true;
    let metin =
      "Space'teki arayüz dosyaları birbiriyle uyumsuz: app.js sürüm " +
      ARAYUZ_SURUMU + ", index.html sürüm " + (htmlSurumu || "bilinmiyor") + ".";
    if (eksik.length) {
      metin += " HTML'de bulunamayan alanlar: " + eksik.join(", ") + ".";
    }
    metin +=
      " Son paketteki web/ klasörünü (index.html, app.js, style.css)" +
      " KLASÖR YAPISINI KORUYARAK Space'e yükleyin; aksi halde derleme" +
      " düğmesi tepki vermeyebilir.";
    serit(metin);
    return false;
  }

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
  dinle("console", "scroll", () => {
    const nearBottom =
      consoleEl.scrollHeight - consoleEl.scrollTop - consoleEl.clientHeight < 60;
    autoScroll = nearBottom;
  });

  // --- Başlangıç yapılandırması -----------------------------------------

  async function loadConfig() {
    try {
      const res = await fetch("/api/config");
      const cfg = await res.json();

      // Eksik bir alan yapılandırmanın GERİ KALANINI engellemesin:
      // asıl uyarı şeridi zaten eksikliği söylüyor.
      const yaz = (id, alan, deger) => {
        const el = $(id);
        if (el) el[alan] = deger;
      };
      yaz("renpy_version", "value", cfg.renpy_version);
      yaz("renpy_version", "placeholder", cfg.renpy_version);
      yaz(
        "version-hint", "textContent",
        `İmaja gömülü sürüm: ${cfg.renpy_version} (en hızlısı)`
      );

      yaz("aerokey_base_url", "value", cfg.aerokey_base_url);
      yaz("aerokey_key_page", "value", cfg.aerokey_key_page);
      yaz("aerokey_game_id", "placeholder", cfg.suggested_game_id);

      // Kökte kalmış, sunucunun hiç okumadığı arayüz dosyaları varsa
      // sebebi doğrudan söylüyoruz: en sık yapılan hata, güncelleme
      // ZIP'ini klasör yapısını korumadan açmak.
      const basibos = cfg.stray_web_files || [];
      if (basibos.length) {
        serit(
          "Space'in kökünde, web/ içindekilerden FARKLI şu dosyalar var: " +
          basibos.join(", ") + ". Sunucu yalnızca web/ klasörünü okuyor, " +
          "bu kopyalar HİÇ kullanılmıyor. Güncelleme ZIP'ini klasör " +
          "yapısını koruyarak açın (web/ klasörü web/ olarak kalsın)."
        );
      }

      const meta = $("masthead-meta");
      if (!meta) return;
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

  dinle("upload-cancel", "click", () => {
    if (activeUpload) activeUpload.abort();
  });

  dinle("project_zip", "change", () => {
    const file = zipInput.files && zipInput.files[0];
    if (file) uploadFile(file);
  });

  // --- Önbellek listesi --------------------------------------------------

  function selectUpload(id) {
    const degisti = selectedUploadId !== id;
    selectedUploadId = id;
    store(UPLOAD_KEY, id);
    renderCacheSelection();
    // Başka bir projeye geçildiyse gözatma listesi ARTIK GEÇERSİZ:
    // başka bir projenin dosyalarını göstermek çok yanıltıcı olurdu.
    if (degisti && typeof browseSifirla === "function") browseSifirla();
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
    if (items.length) {
      const omur = data.persistent
        ? "kalıcı disk, Space yeniden başlasa da kalır"
        : "geçici, Space yeniden başlarsa silinir";
      cacheSize.textContent =
        `· ${items.length} dosya, ${humanSize(data.total_bytes)} · ${omur}`;
    } else {
      cacheSize.textContent = "";
    }

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
    dinle("dropzone", evt, (e) => {
      e.preventDefault();
      dropzone.classList.add("dragover");
    })
  );

  ["dragleave", "drop"].forEach((evt) =>
    dinle("dropzone", evt, (e) => {
      e.preventDefault();
      dropzone.classList.remove("dragover");
    })
  );

  dinle("dropzone", "drop", (e) => {
    const files = e.dataTransfer && e.dataTransfer.files;
    if (files && files.length) uploadFile(files[0]);
  });

  // --- AeroKey bölümü ----------------------------------------------------

  const aerokeyToggle = $("aerokey_enabled");
  const aerokeyBody = $("aerokey-body");

  dinle("aerokey_enabled", "change", () => {
    aerokeyBody.hidden = !aerokeyToggle.checked;
  });

  dinle("refresh-game-id", "click", async () => {
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

  dinle("download-keystore", "click", () => {
    window.location.href = "/api/keystore/auto";
  });

  dinle("show-keystore-info", "click", async () => {
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

  dinle("copy-log", "click", async () => {
    if (!logBuffer.length) return toast("Kopyalanacak bir şey yok.");
    try {
      await navigator.clipboard.writeText(logBuffer.join("\n"));
      toast("Günlük panoya kopyalandı.");
    } catch (err) {
      toast("Panoya erişilemedi.", true);
    }
  });

  dinle("clear-log", "click", resetConsole);

  // --- Dosyaları gözat / düzenle -----------------------------------------

  let browseEntries = [];
  let browseEdits = [];
  let browseOpen = null;   // { path, kind, blocks }
  let browseBlock = null;  // seçili blok (.rpyc için)
  let browseScanned = null; // taranmış olan yükleme kimliği

  function browseDurum(metin, kotu = false) {
    const el = $("browse-status");
    if (!el) return;
    el.textContent = metin;
    el.classList.toggle("bad", !!kotu);
  }

  async function browseIstek(yol, secenek) {
    const res = await fetch(yol, secenek);
    if (!res.ok) {
      const hata = await res.json().catch(() => ({}));
      throw new Error(hata.detail || `HTTP ${res.status}`);
    }
    return res.json();
  }

  dinle("browse-scan", "click", async () => {
    if (!selectedUploadId) {
      toast("Önce bir proje ZIP dosyası seçin.", true);
      return;
    }
    const dugme = $("browse-scan");
    dugme.disabled = true;
    $("browse-info").textContent = "Taranıyor… (büyük projelerde bir dakika sürebilir)";
    try {
      const veri = await browseIstek(
        `/api/browse/${selectedUploadId}/scan`, { method: "POST" }
      );
      browseScanned = selectedUploadId;
      browseUygula(veri);
    } catch (err) {
      $("browse-info").textContent = "";
      toast("Tarama başarısız: " + err.message, true);
    } finally {
      dugme.disabled = false;
    }
  });

  function browseUygula(veri) {
    browseEntries = veri.entries || [];
    browseEdits = veri.edits || [];
    const acilabilir = browseEntries.filter((e) => e.openable).length;
    $("browse-info").textContent =
      `${browseEntries.length} dosya · ${acilabilir} tanesi düzenlenebilir` +
      (veri.archives && veri.archives.length
        ? ` · ${veri.archives.length} arşivin içi okundu` : "");
    if (veri.note) appendLine("Gözatma notu:" + veri.note);
    browseListeyiCiz();
    browseDuzenlemeleriCiz();
  }

  function browseListeyiCiz() {
    const liste = $("browse-list");
    const arama = ($("browse-filter").value || "").toLowerCase().trim();
    liste.innerHTML = "";

    const gosterilecek = browseEntries
      .filter((e) => e.openable)
      .filter((e) => !arama || e.path.toLowerCase().includes(arama))
      .slice(0, 300);

    liste.hidden = browseEntries.length === 0;
    if (!gosterilecek.length) {
      liste.innerHTML =
        '<div class="browse-empty">Eşleşen düzenlenebilir dosya yok.</div>';
      return;
    }

    gosterilecek.forEach((e) => {
      const satir = document.createElement("button");
      satir.type = "button";
      satir.className = "browse-item" + (e.edited ? " edited" : "");
      const nereden = e.source.startsWith("rpa:")
        ? e.source.slice(4) + " içinden" : "ZIP";
      satir.innerHTML =
        `<span class="bi-path">${escapeHtml(e.path)}</span>` +
        `<em>${escapeHtml(e.kind)} · ${humanSize(e.size)} · ` +
        `${escapeHtml(nereden)}${e.edited ? " · düzenlendi" : ""}</em>`;
      satir.addEventListener("click", () => browseAc(e.path));
      liste.appendChild(satir);
    });
  }

  dinle("browse-filter", "input", browseListeyiCiz);

  async function browseAc(path) {
    try {
      const veri = await browseIstek(
        `/api/browse/${browseScanned}/file?path=${encodeURIComponent(path)}`
      );
      browseOpen = veri;
      $("browse-editor").hidden = false;
      $("browse-file").textContent = path;
      $("browse-revert").hidden = !veri.edited;
      browseDurum(veri.edited ? "Bu dosyada kayıtlı bir düzenleme var." : "");

      const secici = $("browse-blocks");
      if (veri.kind === "rpyc") {
        const bloklar = veri.blocks || [];
        secici.hidden = false;
        secici.innerHTML = "";
        bloklar.forEach((b, i) => {
          const opt = document.createElement("option");
          opt.value = String(i);
          opt.textContent = b.title;
          secici.appendChild(opt);
        });
        if (!bloklar.length) {
          $("browse-text").value =
            "(Bu derlenmiş betikte düzenlenebilir bir Python kod bloğu yok.)";
          $("browse-text").readOnly = true;
          browseBlock = null;
        } else {
          $("browse-text").readOnly = false;
          browseBlokSec(0);
        }
      } else {
        secici.hidden = true;
        $("browse-text").readOnly = false;
        $("browse-text").value = veri.text || "";
        browseBlock = null;
      }
      $("browse-editor").scrollIntoView({ behavior: "smooth", block: "nearest" });
    } catch (err) {
      toast("Dosya açılamadı: " + err.message, true);
    }
  }

  function browseBlokSec(i) {
    const bloklar = (browseOpen && browseOpen.blocks) || [];
    if (!bloklar[i]) return;
    browseBlock = bloklar[i];
    $("browse-blocks").value = String(i);
    $("browse-text").value = browseBlock.source;
  }

  dinle("browse-blocks", "change", (e) => {
    browseBlokSec(parseInt(e.target.value, 10) || 0);
  });

  dinle("browse-save", "click", async () => {
    if (!browseOpen) return;
    const govde = {
      path: browseOpen.path,
      text: $("browse-text").value,
    };
    if (browseOpen.kind === "rpyc") {
      if (!browseBlock) {
        browseDurum("Kaydedilecek bir kod bloğu seçili değil.", true);
        return;
      }
      govde.block_index = browseBlock.index;
      govde.block_line = browseBlock.line;
    }
    browseDurum("Kaydediliyor…");
    try {
      const veri = await browseIstek(
        `/api/browse/${browseScanned}/file`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(govde),
        }
      );
      browseEdits = veri.edits || [];
      browseEntries = browseEntries.map((e) =>
        e.path === browseOpen.path ? { ...e, edited: true } : e
      );
      $("browse-revert").hidden = false;
      browseDurum("Kaydedildi. Bir sonraki derlemede uygulanacak.");
      browseListeyiCiz();
      browseDuzenlemeleriCiz();
      toast("Düzenleme kaydedildi.");
    } catch (err) {
      browseDurum("Kaydedilemedi: " + err.message, true);
    }
  });

  dinle("browse-copy", "click", async () => {
    try {
      await navigator.clipboard.writeText($("browse-text").value);
      browseDurum("İçerik panoya kopyalandı.");
    } catch (err) {
      browseDurum("Panoya erişilemedi; metni elle seçip kopyalayın.", true);
    }
  });

  dinle("browse-revert", "click", async () => {
    if (!browseOpen) return;
    try {
      const veri = await browseIstek(
        `/api/browse/${browseScanned}/file?path=` +
        encodeURIComponent(browseOpen.path),
        { method: "DELETE" }
      );
      browseEdits = veri.edits || [];
      browseEntries = browseEntries.map((e) =>
        e.path === browseOpen.path ? { ...e, edited: false } : e
      );
      browseListeyiCiz();
      browseDuzenlemeleriCiz();
      const acik = browseOpen.path;
      browseOpen = null;
      browseAc(acik);
      toast("Düzenleme geri alındı.");
    } catch (err) {
      browseDurum("Geri alınamadı: " + err.message, true);
    }
  });

  dinle("browse-close", "click", () => {
    $("browse-editor").hidden = true;
    browseOpen = null;
    browseBlock = null;
  });

  function browseDuzenlemeleriCiz() {
    const kutu = $("browse-edits");
    kutu.hidden = !browseEdits.length;
    if (!browseEdits.length) {
      kutu.innerHTML = "";
      return;
    }
    kutu.innerHTML =
      "<strong>Derlemeye uygulanacak düzenlemeler</strong>" +
      browseEdits
        .map(
          (k) =>
            `<div class="browse-edit"><span>${escapeHtml(k.path)}</span>` +
            `<em>${escapeHtml(k.summary || "")}</em></div>`
        )
        .join("");
  }

  async function browseYukle() {
    if (!selectedUploadId) return;
    try {
      const veri = await browseIstek(`/api/browse/${selectedUploadId}`);
      if (!veri.scanned) {
        browseEntries = [];
        browseEdits = [];
        $("browse-info").textContent =
          "Henüz taranmadı — \u201cTara\u201d düğmesine basın.";
        $("browse-list").hidden = true;
        $("browse-edits").hidden = true;
        return;
      }
      browseScanned = selectedUploadId;
      browseUygula(veri);
    } catch (err) {
      /* proje seçili değilse ya da silinmişse sessiz geç */
    }
  }

  function browseSifirla() {
    browseEntries = [];
    browseEdits = [];
    browseOpen = null;
    browseBlock = null;
    browseScanned = null;
    const editor = $("browse-editor");
    if (editor) editor.hidden = true;
    const liste = $("browse-list");
    if (liste) { liste.hidden = true; liste.innerHTML = ""; }
    const duzenlemeler = $("browse-edits");
    if (duzenlemeler) duzenlemeler.hidden = true;
    const bilgi = $("browse-info");
    if (bilgi) bilgi.textContent = "";
    if ($("browse-card") && $("browse-card").open) browseYukle();
  }

  dinle("browse-card", "toggle", () => {
    if ($("browse-card").open) browseYukle();
  });

  // --- Derleme -----------------------------------------------------------

  /**
   * Derlemeyi başlatır.
   *
   * Dışarıdaki `submit` dinleyicisi bunu try/catch içinde çağırıyor:
   * beklenmedik bir JavaScript hatası olursa kullanıcı bunu GÖRÜYOR.
   * Eskiden böyle bir hata sessizce yutuluyordu ve düğme çalışmıyormuş
   * gibi görünüyordu.
   */
  async function derlemeyiBaslat() {
    if (activeUpload) {
      toast("Dosya hâlâ yükleniyor; bitmesini bekleyin.", true);
      return;
    }
    if (!selectedUploadId) {
      toast("Önce bir Ren'Py proje ZIP dosyası seçin.", true);
      return;
    }
    const apk = $("want_apk");
    const aab = $("want_aab");
    if (!(apk && apk.checked) && !(aab && aab.checked)) {
      toast("En az bir çıktı formatı seçin (APK ve/veya AAB).", true);
      return;
    }

    const data = new FormData();
    // Dosya YENİDEN YÜKLENMİYOR: sunucudaki önbellek kimliği gönderiliyor.
    data.append("cached_zip_id", selectedUploadId);

    // Eksik bir alan yüzünden burada patlanmıyor: eksik alan "boş"
    // sayılıyor, eksikliği de sayfanın üstündeki şerit bildiriyor.
    ["fixes", "icon", "banner", "translation", "keystore"].forEach((id) => {
      const dosya = secilenDosya(id);
      if (dosya) data.append(id, dosya);
    });

    const textFields = [
      "renpy_version", "package_prefix",
      "manual_name", "manual_package", "manual_version",
      "keystore_alias", "keystore_password",
      "aerokey_base_url", "aerokey_key_page", "aerokey_game_id",
      "translation_mode",
    ];
    textFields.forEach((id) => {
      const el = $(id);
      if (el) data.append(id, el.value);
    });

    const boolFields = [
      "want_apk", "want_aab", "aerokey_enabled", "aerokey_leaderboard",
      "aerokey_survey", "aerokey_profile", "aerokey_bug_report",
      "aerokey_notifications",
    ];
    boolFields.forEach((id) => {
      const el = $(id);
      if (el) data.append(id, el.checked ? "true" : "false");
    });

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
  }

  dinle("build-form", "submit", (e) => {
    e.preventDefault();
    let sonuc;
    try {
      sonuc = derlemeyiBaslat();
    } catch (err) {
      bildirBeklenmedik(err);
      return;
    }
    if (sonuc && typeof sonuc.catch === "function") sonuc.catch(bildirBeklenmedik);
  });

  /**
   * Beklenmedik bir hatayı SESSİZ bırakmaz.
   *
   * Bu fonksiyonun kendisi hata fırlatırsa genel `error` dinleyicisi
   * onu tekrar buraya getirir; sonsuz döngüyü önlemek için her adım
   * ayrı ayrı korunuyor ve yeniden girişe karşı bir bayrak var.
   */
  function bildirBeklenmedik(err) {
    if (hataBildiriliyor) return;
    hataBildiriliyor = true;
    try {
      const mesaj =
        (err && (err.message || err.reason || String(err))) || "bilinmeyen hata";

      try {
        setBusy(false);
      } catch (e2) {
        /* düğme bulunamıyorsa şerit zaten bunu söylüyor */
      }
      try {
        setStatus("error", "Arayüz hatası");
        appendLine("Arayüz hatası: " + mesaj);
      } catch (e2) {
        /* günlük alanı yoksa yine şerit ve bildirim var */
      }
      try {
        serit(
          "Arayüzde beklenmedik bir hata oluştu: " + mesaj +
          (arayuzUyumsuz
            ? " — Sebebi büyük olasılıkla yukarıda belirtilen dosya" +
              " uyumsuzluğu."
            : " — Sorun sürerse sayfayı yenileyin; tekrarlıyorsa bu" +
              " mesajı olduğu gibi bildirin.")
        );
      } catch (e2) {
        /* şerit bile kurulamıyorsa yapılacak bir şey kalmadı */
      }
      try {
        toast("Arayüz hatası: " + mesaj, true);
      } catch (e2) {
        /* yoksay */
      }
    } finally {
      hataBildiriliyor = false;
    }
  }

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
        // Derleme durduysa bile indirilecek dosya olabilir: ön denetim,
        // hata veren oyun dosyalarını buraya koyuyor.
        if (payload.files && payload.files.length) {
          showResults(payload.files, jobId, true);
        }
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

  function showResults(files, jobId, hatali = false) {
    if (!files || !files.length) return;
    const baslik = resultsBox.querySelector("h3");
    if (baslik) {
      baslik.textContent = hatali
        ? "Hata veren dosyalar — indirip düzeltin"
        : "Üretilen dosyalar";
    }
    resultsBox.classList.toggle("results-bad", hatali);
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

  arayuzuDenetle();
  selectedUploadId = recall(UPLOAD_KEY);
  loadConfig();
  loadUploads();
  resumeJob();
})();
