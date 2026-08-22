---
title: Renpy Android Paketleyici
emoji: 📱
colorFrom: green
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
license: mit
---

# 🎮 Ren'Py → Android (APK / AAB) Paketleyici

Ren'Py oyun projenizin ZIP dosyasını yükleyin, arayüz sizin için Android
**APK** ve/veya **AAB** (Google Play) paketi üretsin. İsterseniz oyuna,
**AeroKey** WordPress eklentinizle konuşan native (Kotlin) bir **lisans /
giriş ekranı** de ekler.

Kaputun altında resmi Ren'Py derleme zincirini (RAPT + Gradle) çalıştıran
açık kaynak **renkit** aracını (`renutil` + `renconstruct`) otomatikleştirir:
👉 https://github.com/kobaltcore/renkit (MIT lisans)

---

## Neler yapar

| Özellik | Açıklama |
|---|---|
| APK / AAB üretimi | Resmi RAPT + Gradle hattı üzerinden |
| **Derlenmiş proje desteği** | `Build Distributions` çıktısını da (sadece `.rpyc`) paketleyebilir |
| **Otomatik imzalama** | Kalıcı anahtar bir kez üretilir, her derlemede aynısı kullanılır |
| **AeroKey lisans ekranı** | Kotlin ile yazılmış, oyundan önce açılan native giriş ekranı |
| **Oyun süresi sayımı** | Oynanan süre sunucuya senkronlanır (Steam tarzı) |
| Otomatik ikon | Tek kare görselden iki katmanlı adaptif ikon üretir |
| Gradle ön belleklemesi | 504 / indirme hatalarını baştan engeller |
| **Ücretsiz erişim günleri** | Eklentiden tarih verin, o gün anahtar sorulmaz |
| **Duyuru bildirimleri** | Eklentiden gönderin, oyuncunun bildirim çekmecesine düşsün |
| Canlı günlük | Server-Sent Events ile anlık akan derleme çıktısı |

---

## Kurulum (Hugging Face Space)

1. Hugging Face'te **yeni bir Space** oluşturun.
2. SDK olarak mutlaka **Docker**'ı seçin (Gradio SDK'sını DEĞİL — bize Java
   ve Android derleme araçları gibi sistem seviyesi bağımlılıklar gerekiyor).
3. Bu depodaki dosyaları Space'inizin kök dizinine ekleyin:

   ```
   Dockerfile
   app.py
   requirements.txt
   README.md
   icon.placeholder          (silmeyin — Dockerfile'daki COPY icon.* buna dayanıyor)
   banner.placeholder        (silmeyin — COPY banner.* buna dayanıyor)
   web/index.html
   web/style.css
   web/app.js
   aerokey/__init__.py
   aerokey/patch_rapt.py
   aerokey/kotlin/*.kt        (9 dosya)
   ```

4. **Settings → Variables and secrets** kısmından isterseniz şunları
   ayarlayabilirsiniz (hepsi isteğe bağlı):

   | Değişken | Varsayılan | Ne işe yarar |
   |---|---|---|
   | `RENPY_VERSION` | `8.5.3` | İmaja gömülecek Ren'Py sürümü |
   | `AEROKEY_BASE_URL` | `https://riaslink.fun` | WordPress sitenizin kökü |
   | `AEROKEY_KEY_PAGE` | `https://riaslink.fun/bilgi` | "Anahtar Al" düğmesinin açacağı sayfa |
   | `AEROKEY_GAME_ID_PREFIX` | `riaslink_oyun_` | Oyun kimliği öneki |
   | `PORTER_DATA_DIR` | `/data` | Kalıcı veri konumu |

5. **Settings → Persistent storage** açmanız şiddetle önerilir (aşağıya bakın).
6. Space otomatik build alır. **İlk build 15-25 dakika sürebilir** (Java,
   renkit, Ren'Py SDK ve Gradle imaja gömülüyor).

### ⚠️ Kalıcı disk neden önemli?

İmza anahtarı ve oyun kimliği kaydı `/data` altında tutulur. Kalıcı disk
**yoksa** Space her yeniden başladığında:

- imza anahtarı sıfırdan üretilir → yeni APK'ler eskilerin üzerine kurulamaz,
- oyun kimliği sayacı sıfırlanır → `riaslink_oyun_001` yeniden verilir.

Arayüz kalıcı disk yoksa üst köşede sarı bir uyarı gösterir. Kalıcı disk
açamıyorsanız, **"Anahtarı indir (yedekle)"** düğmesiyle imza anahtarınızı
indirip saklayın ve sonraki derlemelerde elle yükleyin.

### Donanım

Android/Gradle derlemeleri hem RAM hem disk açısından ağırdır. Ücretsiz
katmanda build'ler çok yavaş olabilir ya da bellek yetersizliğinden
başarısız olabilir. Sorun yaşarsanız **Settings → Hardware** kısmından daha
güçlü bir CPU katmanına geçmeyi deneyin.

---

## Kullanım

1. **Proje ZIP'i** — Ren'Py proje klasörünüzü (içinde `game/` olan klasörü)
   zip'leyip yükleyin. `game/` zip'in kökünde de olabilir, bir üst klasörle
   birlikte de (`projem/game/...`) — araç ikisini de anlar.
2. **Ren'Py sürümü** — Varsayılan olarak imaja gömülü sürüm en hızlısıdır.
   Farklı bir sürüm girerseniz ilk seferde ayrıca indirilir (ve AeroKey
   yaması o sürüme de otomatik uygulanır).
3. **APK / AAB** — APK doğrudan telefona kurmak için, AAB Google Play için.
4. **Derlenmiş proje bilgileri** — aşağıya bakın.
5. **AeroKey lisans ekranı** — aşağıya bakın.
6. **İkon ve imzalama** — ikon isteğe bağlı; imzalama otomatik.

---

## 📦 Derlenmiş (hazır dağıtım) projeler

Ren'Py Launcher'ın **Build Distributions** çıktısı (örn.
`OyunAdı-1.2.1-pc.zip`) ham proje kaynağından farklıdır: `.rpy` kaynakları
çıkarılıp yalnızca derlenmiş `.rpyc` bırakılır ve `renpy/` + `lib/` (masaüstü
motoru) eklenir.

Bu araç böyle paketleri **paketleyebilir**. Yaptıkları:

- Dağıtım paketi olduğunu otomatik tespit edip günlüğe yazar.
- `renpy/`, `lib/` ve `.exe` gibi masaüstüne özel dosyaları **yalnızca
  geçici çalışma kopyasından** çıkarır (orijinal ZIP'inize dokunmaz).
- Uygulama adı/paket/sürümü elle girdiyseniz onları kullanır.

**Neden elle girmek gerekiyor?** Uygulama adı gibi bilgiler normalde
`game/options.rpy` **metninden** okunur. Derlenmiş pakette bu dosya yoktur
(sadece `.rpyc` bytecode vardır), bu yüzden okunamaz. Arayüzdeki
**"Derlenmiş proje bilgileri"** bölümünü doldurursanız doğru isim/paketle
derlenir; boş bırakırsanız araç klasör adından tahmin eder.

> Elinizde ham kaynak varsa onu yüklemek her zaman daha iyidir.

---

## 🔐 AeroKey lisans ekranı

Açtığınızda, oyunun **açılış ekranı** Ren'Py'nin kendi ekranı yerine Kotlin
ile yazılmış bir lisans geçidi olur. Doğrulama geçilmeden oyun başlamaz.

### Giriş ekranında neler var

- İsteğe bağlı **afiş** (aşağıya bakın)
- **Erişim anahtarı** girişi → `GET /wp-json/lisans/v1/kontrol?anahtar=…`
- **⭐ VIP Üyeyim** → `GET /wp-json/lisans/v1/vip-kontrol?device_id=…`
- **🔑 Anahtar Al** → tarayıcıda `https://riaslink.fun/bilgi` sayfasını açar
- **Cihaz kimliği + Kopyala** → VIP tanımlarken bu kimliği kullanırsınız

Giriş ekranının tek işi doğrulamadır. Liderlik/profil/anket/hata bildirimi
burada değil, aşağıdaki oyun içi menüdedir — oyuncu bunlara oyun sırasında
ihtiyaç duyar, henüz oyuna girmeden değil.

### 🖼️ Giriş ekranı afişi

Giriş ekranının üstünde bir görsel gösterebilirsiniz. Tasarım boyutu
**500×288**; oran korunarak karta sığdırılır.

- **Space'e kalıcı eklemek için:** bu depoya `banner.gif` (ya da
  `banner.png` / `banner.jpg` / `banner.webp`) ekleyip Space'i yeniden build
  alın. `banner.placeholder` dosyasını **silmeyin** — `icon.placeholder` ile
  aynı gerekçe: hiçbir dosyayla eşleşmeyen bir `COPY` deseni Docker build'ini
  başarısız yapar.
- **Sadece bir derleme için:** arayüzdeki "İkon ve imzalama → Giriş ekranı
  afişi" alanından yükleyin; gömülü olanın yerine geçer.
- **GIF** yüklerseniz Android 9 (API 28) ve üzerinde **hareketli** oynatılır.
  Daha eski sürümlerde ilk kare durağan gösterilir — sırf afiş için projeye
  yeni bir GIF kütüphanesi eklemektense bu tercih edildi.
- Afiş yoksa ekran afişsiz çizilir; hiçbir şey bozulmaz.

### 🏷️ Sıralama adı (zorunlu, tek seferlik)

Lisans doğrulandıktan sonra, oyuncu **ilk kez** giriyorsa bir ad seçme
ekranı çıkar (3-20 karakter, atlanamaz). Sebebi basit: liderlik tablosu
adlarla çalışıyor ve herkesin `GizemliOyuncu` görünmesi tabloyu anlamsız
kılardı.

Seçilen ad **cihaz kimliğine bağlı olarak kalıcı** saklanır ve `POST /sync`
ile sunucudaki kayda işlenir; bir daha sorulmaz. Ağ o an kopuksa oyuncu
bekletilmez — ad yerelde durur ve ilk başarılı senkronda sunucuya gider.

Ardından kısa bir **"İyi oyunlar!"** ekranı gösterilip oyun başlatılır.

### 🎮 Oyun içi menü

Doğrulamadan sonra oyunun **sağ üst köşesinde sabit** bir menü düğmesi belirir.
Oyun yatay çalıştığı için panel de yatay düzende tasarlandı.

- **Dokununca açılır** — düğmenin altından geniş ve alçak bir panel iner:
  - başlıkta **hangi oyunda olduğunuz** ve oyuncu adınız,
  - yanında **canlı oynama süresi** (panel açıkken saniye saniye tazelenir),
  - tek sıra hâlinde: 🏆 Liderlik, 👤 Profilim, 📊 Anket, 🐞 Hata Bildir.
- **🙈 Menüyü gizle** — düğmeyi küçültüp neredeyse saydam yapar, yani oyunu
  örtmez. Yok olmaz: tek dokunuşla geri gelir. Durum kalıcı olarak saklanır.
- Menünün **dışına** yapılan dokunuşlar tüketilmez, doğrudan oyuna geçer.

**Neden sürüklenebilir değil?** Denendi ve iki kez başarısız oldu; sonucu
buraya not ediyoruz ki tekrar denenmesin:

1. Düğme Activity'nin görünüm ağacına eklenip sürüklenebilir yapıldığında,
   sürüklenince **kayboluyor** (dokunma çalışmaya devam ediyor, yeniden
   dokununca geri geliyordu). Sebep: Ren'Py oyunu bir `SurfaceView` ile
   çiziliyor ve bu, pencere yüzeyinde saydam bir "delik" açıyor; o deliğin
   üstündeki görünümler yalnızca yeniden çizilen bölgelerde güvenilir şekilde
   birleştiriliyor.
2. Menü `WindowManager` ile ayrı bir pencereye (`TYPE_APPLICATION_PANEL`)
   taşındığında ise **hiç görünmedi**.

Elimizdeki kanıt net: görünüm ağacı çiziyor, ayrı pencere çizmiyor. Bu yüzden
görünüm ağacında kalıp sorunun kaynağını — hareketi — ortadan kaldırdık.
Sabit bir görünüm yeniden konumlanmadığı için bayat bölge sorunu oluşmuyor.

### 🎁 Ücretsiz erişim günleri (eklentiden yönetilir)

Eklenti panelindeki **"Ücretsiz Erişim Günleri"** bölümünden bir tarih
eklerseniz, o gün oyunlar **anahtar sormaz**: açılışta ekranın tam ortasında
"Bugün anahtarlar ücretsiz!" (mesajı değiştirebilirsiniz) gösterilir ve oyun
başlar.

- Gün, **sitenizin saat dilimine** göre hesaplanır — cihazın saatine göre
  değil. Böylece farklı saat dilimlerindeki oyuncular için gün aynı anda
  başlayıp biter.
- Oyun açıkken yapılan düzenli lisans denetimi de ücretsiz günde **atlanır**;
  aksi halde anahtarsız giren oyuncu on dakika sonra oyundan atılırdı.
- Sıralama adı adımı atlanmaz (o anahtar ekranı değil).
- Sunucuya ulaşılamazsa sessizce normal akışa dönülür; ağ sorunu yüzünden
  kimse kapıda kalmaz.

### 🔔 Duyuru bildirimleri (eklentiden gönderilir)

Eklenti panelindeki **"Push Bildirim Gönder"** bölümünden başlık + mesaj
yazıp gönderdiğinizde, duyuru oyuncuların **bildirim çekmecesine** düşer.
Belirli bir oyuna ya da (oyun kimliği boş bırakılırsa) tüm oyunlara
gönderebilirsiniz.

**Bu gerçek "push" (FCM) değil, çekme (polling) yöntemidir.** Gerçek push bir
Firebase projesi, `google-services.json` ve `firebase-messaging` bağımlılığı
isterdi; bu entegrasyonun tamamı bilinçli olarak **sıfır ek Gradle
bağımlılığıyla** yazıldı. Bunun yerine:

- **Oyun açıkken:** düzenli denetimde sorulur (hızlı iletim).
- **Oyun kapalıyken:** `JobScheduler` ile ~15 dakikada bir sorulur
  (Android'in periyodik işler için izin verdiği en kısa aralık).

Kullanıcı açısından sonuç aynı — gerçek bir sistem bildirimi belirir; fark
yalnızca iletim gecikmesinde. Android 13+ için `POST_NOTIFICATIONS` izni
`android.json`'a otomatik eklenir ve ilk açılışta istenir; reddedilirse
yalnızca bildirimler gösterilemez, oyun ve lisans akışı aynen çalışır.

Bir cihaz ilk kez sorduğunda **eski duyurular topluca yağdırılmaz**; imleç
ileri alınır ve yalnızca o andan sonraki duyurular bildirim olur.

### Her zaman etkin olanlar

- **Oyun süresi sayımı** — uygulama ön plandayken saniye sayar; hem bu
  oyunun süresini hem hesabın toplam süresini `POST /sync` ile gönderir.
  Sunucuda daha yüksek bir değer varsa (başka cihazda oynanmışsa) onu alır.
- **Lisans yeniden denetimi** — oyun açıkken 10 dakikada bir sessizce
  doğrular; süre dolduysa oyunu kapatıp giriş ekranına döner.
- **Çevrimdışı tolerans** — internet yoksa, daha önce doğrulanmış lisansla
  devam edilmesine izin verilir. Oyuncuyu geçici bir bağlantı kopukluğu
  yüzünden kendi oyunundan kilitlemek doğru olmaz; yalnızca sunucunun
  açıkça "geçersiz" demesi bağlayıcıdır.

### Oyun kimliği (`oyun_id`)

Oynama süreleri sunucuda oyun kimliğine göre saklanır. Araç, her yeni paket
adına **kullanılmamış en küçük** kimliği verir: `riaslink_oyun_001`,
`riaslink_oyun_002`, … Daha önce bir kimlik verilmiş bir paketi tekrar
derlerseniz **aynı kimlik korunur** (süreler bozulmasın diye). Verilmiş bir
kimlik asla ikinci bir oyuna otomatik atanmaz.

Kimliği elle de girebilirsiniz; başka bir pakete aitse araç uyarır ama
isteğinize uyar.

### Kapsam dışı bırakılanlar

İsteğiniz üzerine **kart çevirme (gacha)** ve **referans/davet** sistemleri
için native arayüz yazılmadı. Eklentideki ilgili uç noktalar duruyor, oyun
bunları çağırmıyor.

### Teknik not: yama nereye uygulanıyor?

Ren'Py, her derlemede `app/AndroidManifest.xml` ve `app/build.gradle` gibi
dosyaları `rapt/templates/` altındaki Jinja2 şablonlarından **yeniden
üretir**. Bu yüzden üretilmiş dosyaları düzenlemek işe yaramaz — bir sonraki
derlemede sessizce geri alınır.

`aerokey/patch_rapt.py` yamayı doğru katmana uygular:

| Ne | Nereye |
|---|---|
| Kotlin kaynakları | `rapt/prototype/renpyandroid/src/main/java/com/riaslink/aerokey/` |
| Kotlin Gradle eklentisi | `rapt/prototype/**/build.gradle` (bunlar üretilmiyor) |
| Launcher değişikliği | `rapt/templates/*AndroidManifest.xml` (üretilenin **kaynağı**) |

Yama Docker imajı kurulurken bir kez çalışır ve o SDK ile derlenen **her**
oyunda geçerli olur. Şablon beklenmedik biçimde değiştiyse betik bilinçli
olarak **hata verip imaj derlemesini durdurur** — sessizce atlanan bir yama,
lisans ekranı olmayan bir APK üretirdi ve bu ancak oyun açıldığında fark
edilirdi.

Ekran **hiçbir yeni Gradle bağımlılığı eklemez**: ağ için `HttpURLConnection`,
JSON için `org.json`, arayüz için programatik Android View'ları kullanır
(hepsi Android'in içinde gelir).

---

## 🔑 İmzalama

**Hiçbir şey yapmanıza gerek yok.** İlk derlemede kalıcı bir imza anahtarı
üretilir ve saklanır; sonraki **tüm** derlemeler aynı anahtarla imzalanır.
Böylece ürettiğiniz APK'ler birbirinin üzerine sorunsuz kurulur ve Play
Store güncellemeleri çalışır.

- **Yedekleyin:** "Anahtarı indir (yedekle)" düğmesiyle `.keystore`
  dosyasını, "Alias / şifreyi göster" ile de kimlik bilgilerini alın.
  Kaybederseniz geri getirilemez ve o uygulamayı bir daha güncelleyemezsiniz.
- **Kendi anahtarınızı kullanmak isterseniz:** "İkon ve imzalama →
  Bunun yerine kendi anahtarımı kullanmak istiyorum" bölümünden dosya +
  alias + şifre girin. Üçü de dolu değilse otomatik anahtar kullanılır.

---

## 🖼️ Uygulama ikonu

Ren'Py, Android ikonunu proje kökündeki **iki** 432×432 PNG dosyasından
üretir: `android-icon_foreground.png` (şeffaf ön katman) ve
`android-icon_background.png` (opak arka plan).

- **Tam kontrol:** Bu iki dosyayı kendiniz hazırlayıp projenizin köküne
  (`game/` ile aynı seviyeye) ekleyin — araç onlara hiç dokunmaz.
- **Kolay yol:** Arayüzden tek bir kare görsel yükleyin; ortalanıp
  oranlı küçültülür, beyaz opak arka planla eşleştirilir.
- **Space'e gömmek:** Bu depoya `icon.png` / `icon.jpg` ekleyip yeniden
  build alın; o andan itibaren her derlemede otomatik kullanılır.

---

## 🔌 WordPress eklentisi (v8.8)

`wordpress/aerokey.php` dosyası, mevcut eklentinizin **güncellenmiş** hâlidir.
Sitenizdeki dosyanın üzerine yazın (ya da eklentiyi yeniden yükleyin).

Eklenen şeyler — **mevcut mekaniklerin hiçbiri değiştirilmedi**, yalnızca
üzerine eklendi:

| Ne | Nerede |
|---|---|
| `aerokey_duyurular` tablosu | Yeni tablo; mevcut dört tablo aynen duruyor |
| `GET /wp-json/lisans/v1/durum` | Yeni uç; mevcut uçların hiçbiri değişmedi |
| "Ücretsiz Erişim Günleri" paneli | Admin sayfasına eklendi |
| "Push Bildirim Gönder" paneli | Admin sayfasına eklendi |

Kart çevirme, pity, referans, anket, görev anahtarı, VIP, `/sync`,
`/oyun-suresi` ve kısa kod — hepsi olduğu gibi korundu.

Yeni tablo, eklenti güncellendiğinde `plugins_loaded` denetimiyle
kendiliğinden oluşur; eski kurulumlarda activation hook yeniden çalışmadığı
için bu denetim gerekli.

---

## Sınırlamalar

- **Aynı anda yalnızca bir derleme çalışır.** Bu bir zarafet tercihi değil:
  RAPT tüm oyunlar için **tek** bir `rapt/project/` çalışma dizini kullanır,
  paralel iki derleme birbirinin dosyalarını bozar. İkinci istek sırada bekler.
- **Her Ren'Py sürümü test edilmedi.** RAPT yıllar içinde köklü şekilde
  değişti (Ant → Gradle, Python 2 → 3, JDK 8 → 21). Bu Dockerfile güncel
  (≥8.2.0) sürümler için Java 21 kurar; çok eski bir sürüm için
  `eclipse-temurin:21-jdk` satırını `eclipse-temurin:8-jdk` yapmanız
  gerekebilir. AeroKey yaması da yalnızca modern (Gradle tabanlı) şablonda
  çalışır.
- **İlk Android derlemesi yavaştır.** RAPT, Android SDK bileşenlerini
  (build-tools, platform, gerekirse NDK) ilk gerçek derlemede indirir.
  Gradle'ın kendisi imaja gömülüdür, o yeniden indirilmez.
- Bu Space içerik üretmiyor / barındırmıyor — yalnızca **sizin yüklediğiniz**
  projeyi paketliyor. Yüklediğiniz oyunun hakları size ait olmalı.

---

## Sorun Giderme

| Belirti | Olası neden / çözüm |
|---|---|
| `Server returned HTTP response code: 504` (Gradle indirme) | Geçici ağ arızası. Araç bunu tanıyıp **3 kez otomatik** yeniden dener ve Gradle imaja önceden gömülüdür. Yine de olursa birkaç dakika sonra tekrar deneyin. |
| Build "OutOfMemory" / Gradle daemon hatası | Space donanımınızda yeterli RAM yok; daha güçlü katmana geçin. |
| `renutil install X.Y.Z` sürüm bulamıyor | Sürümü https://www.renpy.org/release_list.html üzerinden doğrulayın. |
| "'game/' klasörü bulunamadı" | ZIP'i, içinde `game` klasörü **görünecek** şekilde oluşturun. |
| Uygulama adı yanlış çıkıyor | Derlenmiş paket yüklemişsinizdir; "Derlenmiş proje bilgileri" alanlarını doldurun. |
| Lisans ekranı APK'de yok | Günlükte "giriş ekranı Android projesine yerleştirildi" satırını arayın. Yoksa AeroKey anahtarı kapalıdır ya da yama uygulanamamıştır (imaj derleme günlüğüne bakın). |
| APK eskinin üzerine kurulmuyor | Kalıcı disk kapalıysa imza anahtarı değişmiştir. Kalıcı diski açın ya da yedeklediğiniz anahtarı elle yükleyin. |
| Java/Gradle sürüm uyuşmazlığı | Ren'Py sürümünüze göre Dockerfile'daki JDK sürümünü (8 ↔ 21) güncelleyin. |

---

## Teşekkür / Lisanslar

- [Ren'Py](https://www.renpy.org/) — MIT lisans
- [renkit](https://github.com/kobaltcore/renkit) (kobaltcore) — MIT lisans,
  bu Space'in derleme otomasyonunun temeli
- AeroKey Lisans Yöneticisi (Riaslink) — bu Space'in konuştuğu WordPress eklentisi
- Bu Space'in kendi kodu da MIT lisansıyla paylaşılabilir.
