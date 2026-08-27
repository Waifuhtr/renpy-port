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
| **Otomatik imzalama** | Kalıcı anahtar bir kez üretilir, her derlemede aynısı kullanılır (Space Secret ile de saklanabilir) |
| **`archive.rpa` açma** | Sıkıştırılmış oyun verisi derleme sırasında otomatik açılır |
| **AeroKey lisans ekranı** | Kotlin ile yazılmış, oyundan önce açılan native giriş ekranı |
| **Oyun süresi sayımı** | Oynanan süre sunucuya senkronlanır (Steam tarzı) |
| **Bulut profili** | Ad + avatar cihaz kimliğine bağlanır: silip kursan da, başka oyununu kursan da seni tanır |
| **Profil görselleri** | `aerokey/avatars/` klasörüne GIF koy; seçerken yalnızca seçili olan oynar |
| Otomatik ikon | Tek kare görselden iki katmanlı adaptif ikon üretir |
| Gradle ön belleklemesi | 504 / indirme hatalarını baştan engeller |
| **Ekransız derleme** | Sanal ekran (Xvfb) otomatik başlar; Ren'Py'nin zorunlu grafiksel açılış adımı çökmez |
| **Ücretsiz erişim günleri** | Eklentiden tarih verin, o gün anahtar sorulmaz |
| **Duyuru bildirimleri** | Eklentiden gönderin, oyuncunun bildirim çekmecesine düşsün |
| **Çeviri paketi kurulumu** | Çeviri ZIP'ini ekler ve dili gerçekten seçilebilir yapar |
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
   aerokey/translation.py
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

## 🌍 Çeviri paketi

Çeviri aracınızın ürettiği ZIP'i arayüzdeki **"Çeviri paketi"** bölümünden
olduğu gibi yükleyin — içinden hiçbir şey silmenize gerek yok.

### Sorun neydi?

Çeviri dosyalarını `game/` içine kopyalamak **tek başına yetmiyor**, çünkü:

1. `translate <dil> strings:` blokları yalnızca oyunun dili o dile
   **ayarlandığında** devreye girer. Derlenmiş (`.rpyc`) bir oyunun
   ekranlarına dil seçici ekleyemezsiniz, dolayısıyla dili ayarlayacak
   hiçbir şey olmaz ve çeviri hiç görünmez. Dosyalar `.rpyc`'ye derlenir
   ama hiçbir işe yaramaz — gördüğünüz davranış tam olarak buydu.
2. Üretilen yükleyici betikler JSON eşlemesini düz
   `open(config.gamedir + ...)` ile okur. Bu PC'de çalışır, **Android'de
   çalışmaz**: orada oyun verisi Ren'Py'nin varlık/arşiv katmanından
   okunur. Üstelik hata `except Exception` ile yutulduğu için ortada
   hiçbir uyarı da çıkmaz.

Bu bölüm ikisini de çözer.

### Ne yapıyor?

- `tl/<dil>/` klasörünü ve çeviri betiklerini `game/` içine yerleştirir.
- Kurulum talimatı dosyalarını (`*.txt`, `*.md`) oyuna sokmaz.
- **Kırık yükleyiciyi almaz**, yerine `renpy.file()` kullanan (yani
  Android'de de çalışan) ve dili dikkate alan bir sürüm üretir.
- **Gereksiz JSON'u atar.** Ren'Py belgelerine göre string çevirileri
  "diyalog olarak çevrilmemiş diyalog metinlerine de uygulanır" — yani
  derlenmiş bir oyunda `translate ... strings:` blokları diyaloğu da
  çevirir. Araç, JSON'daki hangi satırların zaten bu bloklarla
  karşılandığını hesaplar ve yalnızca **karşılanmayanlar** için yedek bir
  filtre üretir. Örnek bir pakette 14.684 kaydın tamamı karşılandığı için
  1,7 MB'lık JSON hiç paketlenmedi.
- Dilin seçilebilmesi için gereken kancayı ekler (aşağıya bakın).

### Dil davranışı

| Mod | Ne olur |
|---|---|
| **Açılışta dil sor** (önerilen) | Ana menüden önce bir kez "Dil / Language" ekranı çıkar; seçim kalıcı saklanır |
| **Her zaman çeviri dilinde aç** | `config.language` ile oyun doğrudan o dile açılır |
| **Sadece dosyaları ekle** | Dile hiç dokunulmaz (oyunun kendi ayarlar ekranında zaten seçici varsa) |

**Dil sorma kancası neden `splashscreen`?** `config.overlay_screens` ana
menüde gizlenir (Ren'Py belgeleri), oradan sorulamaz. Belgelenmiş
`splashscreen` etiketi ise "oyun ilk çalıştırıldığında, ana menü
gösterilmeden önce" çağrılır — tam aradığımız yer.

Tek risk, oyunun o etiketi zaten tanımlamış olması: Ren'Py'de aynı etiketi
iki kez tanımlamak oyunu **açılmaz** hale getirir. Bu yüzden araç, derleme
anında oyunun `.rpy` **ve `.rpyc`** dosyalarını (RPYC2 dilimlerini açarak)
tarayıp etiketin boş olduğunu doğrular. Doğrulayamazsa dili sormak yerine
doğrudan uygular ve günlükte bunu açıkça yazar — yani en kötü durumda
çeviri yine çalışır, oyun asla bozulmaz.

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

### 🏷️ Sıralama adı ve profil (bulutta, cihaza bağlı)

Lisans doğrulandıktan sonra, oyuncu **ilk kez** giriyorsa bir ad seçme
ekranı çıkar (3-20 karakter, atlanamaz). Sebebi basit: liderlik tablosu
adlarla çalışıyor ve herkesin `GizemliOyuncu` görünmesi tabloyu anlamsız
kılardı.

**Ad bir kez sorulur — sonsuza kadar.** Oyuncu oyunu silip yeniden kursa
da, senin başka bir oyununu kursa da adı tekrar sorulmaz:

1. Ad seçilince `POST /kimlik` ile **cihaz kimliğine bağlanır**.
2. Sonraki her açılışta, yerelde ad yoksa `GET /kimlik` sorulur.
3. Sunucu bu cihaza bağlı bir ad döndürürse ad ekranı **hiç gösterilmez**;
   bunun yerine kısa bir "Tekrar hoş geldin" ekranı çıkar.

> **Neden `/sync` yetmiyordu:** `/sync`, kullanıcı adını yalnızca gönderilen
> toplam süre sunucudakinden **büyükse** yazıyor. Ad yeni seçildiğinde süre
> genelde eşit kaldığı için ad sunucuya hiç işlenmiyordu. `/kimlik` adı
> koşulsuz yazar ve `/sync`'in süre mantığına hiç dokunmaz.

#### Bu neden tüm oyunlarda çalışıyor?

Kimlik `ANDROID_ID`'ye dayanıyor. Android 8.0+ belgelerine göre bu değer
**"app-signing key, user, and device" üçlüsünün her kombinasyonu için
tekildir"** — paket adı bu üçlüde **yok**. Ayrıca **"imza anahtarı aynı
kaldığı sürece paket kaldırılıp yeniden kurulduğunda değişmez."**

Paketleyici tüm oyunları **tek bir kalıcı imza anahtarıyla** imzaladığı
için (bkz. [İmzalama](#-i̇mzalama)), aynı cihazdaki tüm oyunların cihaz
kimliği aynıdır ve silme/kurma bunu bozmaz.

> ⚠️ **Bunun tek koşulu:** oyunların aynı anahtarla imzalanması. Bir oyun
> için kendi keystore'unu yüklersen o oyun farklı bir kimlik görür.
>
> **En sık karşılaşılan sorun bu:** Space'in kalıcı diski yoksa anahtar her
> yeniden başlatmada yeniden üretilir ve her derleme farklı bir cihaz
> kimliği verir. Çözümü ve anahtarın parmak izini nasıl doğrulayacağınız
> [İmzalama](#-i̇mzalama) bölümünde.

#### 🖼️ Profil görselleri (avatar)

Avatarları depodaki **`aerokey/avatars/`** klasörüne koy:

```
aerokey/avatars/01.gif
aerokey/avatars/02.gif
aerokey/avatars/03.gif
```

* `.gif`, `.png`, `.webp`, `.jpg`, `.jpeg` kabul edilir.
* Sıra **alfabetiktir** — `01`, `02`, `03` gibi adlar kullan.
* Seçim sunucuda sıra numarasıyla saklandığı için **yeni avatarları hep
  sonuna ekle**; araya eklemek eski oyuncuların avatarını kaydırır.
* Kare (1:1) tasarla, avatar daire olarak kırpılıyor. 256x256 yeterli.
* Klasör boşsa avatar adımı hiç çıkmaz; adın ilk harfinden renkli bir
  rozet üretilir.

**Kare hızı:** seçim ekranında aynı anda **yalnızca bir GIF oynar** —
seçili olan. Diğerleri küçültülmüş tek bir durağan kare olarak çizilir
(`inSampleSize` ile bellekte de küçültülür). Bu yüzden çok sayıda avatar
koymak oyunu yavaşlatmaz.

Ardından kısa bir **"İyi oyunlar!"** ekranı gösterilip oyun başlatılır.

### 🎮 Oyun içi menü

Doğrulamadan sonra oyunun **sağ üst köşesinde sabit** bir menü düğmesi belirir.
Oyun yatay çalıştığı için panel de yatay düzende tasarlandı.

- **Dokununca açılır** — düğmenin altından geniş ve alçak bir panel iner:
  - solda **profil avatarınız** (seçmediyseniz adınızın ilk harfinden rozet),
  - başlıkta **hangi oyunda olduğunuz** ve oyuncu adınız,
  - yanında **canlı oynama süresi** (panel açıkken saniye saniye tazelenir),
  - altında **lisans durumu** satırı — hangi hakla oynadığınız ve bitiş
    tarihi: 🎁 Ücretsiz gün / ⭐ VIP üyelik / 🔑 Anahtar etkin / 🔓 Lisans yok.
    Ücretsiz gün diğerlerinin önüne geçer, çünkü o gün lisans hiç
    denetlenmiyor.
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

### ⚠️ Anahtar neden bu kadar kritik?

İmza anahtarı yalnızca "APK'ler birbirinin üzerine kurulsun" meselesi
değil. Android'in `ANDROID_ID` değeri **imza anahtarına bağlıdır**. Anahtar
değişirse:

- her oyun **farklı bir cihaz kimliği** görür,
- **tüm oyuncuların profili** (sıralama adı, avatar, süreler) sıfırlanır,
- daha önce yayınladığınız APK'ler bir daha güncellenemez.

Her derlemenin günlüğünde anahtarın **SHA-256 parmak izi** yazılır. Bu satır
derlemeler arasında **aynı kalmalıdır**; değiştiyse yukarıdakiler olmuş
demektir.

### Anahtarı kalıcı kılmanın iki yolu

**1. Kalıcı disk (en kolay).** Space ayarlarından kalıcı disk açın. Anahtar
`/data` altında saklanır ve yeniden başlatmalardan etkilenmez.

**2. Space Secret (kalıcı disk yoksa TEK güvenilir yol).** Kalıcı disk yoksa
anahtar ev dizinine yazılır ve Space her yeniden başladığında **silinir** —
yenisi üretilir, kimlikler sıfırlanır. Bunu önlemek için:

1. `/api/keystore/auto/secret` adresini açın (ya da "Anahtarı indir" ile
   dosyayı alıp kendiniz base64'e çevirin).
2. Space → **Settings → Variables and secrets** bölümüne üç secret ekleyin:

   | Secret | Değer |
   |---|---|
   | `AEROKEY_KEYSTORE_B64` | anahtarın base64 hâli |
   | `AEROKEY_KEYSTORE_ALIAS` | alias |
   | `AEROKEY_KEYSTORE_PASSWORD` | şifre |

3. Space'i yeniden başlatın. Artık anahtar Space'in kendisinde durur;
   yeniden başlatmalar, imaj yeniden derlemeleri hiçbir şeyi değiştirmez.

Secret tanımlıysa derleme günlüğü bunu açıkça yazar ve dosyadaki anahtar
yok sayılır.

> Üç secret'ın **üçü birden** tanımlı olmalı. Yalnızca biri eksikse derleme
> net bir hatayla durur — sessizce başka bir anahtara düşmez.

- **Yedekleyin:** "Anahtarı indir (yedekle)" düğmesiyle `.keystore`
  dosyasını, "Alias / şifreyi göster" ile de kimlik bilgilerini alın.
- **Kendi anahtarınızı kullanmak isterseniz:** "İkon ve imzalama →
  Bunun yerine kendi anahtarımı kullanmak istiyorum" bölümünden dosya +
  alias + şifre girin. Üçü de dolu değilse otomatik anahtar kullanılır.

---

## 📦 Sıkıştırılmış oyun verisi (`archive.rpa`)

`Build Distributions` çıktısı oyun dosyalarını genelde tek bir
`game/archive.rpa` içinde toplar. Paketleyici bu arşivleri **derleme
sırasında otomatik açar**, dosyaları `game/` altına gerçek klasör yapısıyla
yerleştirir ve arşivi siler.

Sizin yapmanız gereken bir şey yok — ZIP'i olduğu gibi yükleyin.

`archive.rpa` dışındaki adlar da (`images.rpa`, `scripts.rpa` …) bulunur ve
açılır.

### Neden açmak gerekiyor?

Ren'Py arşivi çalışma anında kendisi okuyabilir, yani **paketleme için**
açmak şart değil. Bizim hattımız için şart:

Çeviri kurulumu, kanca etiketinin (`splashscreen` / `before_main_menu`)
oyunda tanımlı **olmadığını** `.rpyc` dosyalarını tarayarak doğruluyor.
Dosyalar arşivin içindeyse tarayıcı hiçbir şey göremez, etiketi "boş" sanır
ve aynı etiketi ikinci kez tanımlar. Ren'Py'de bu, oyunun **hiç
açılmaması** demektir.

### Ayrıntılar

- **Gevşek dosya kazanır.** `game/` altında zaten duran bir dosya, arşivdeki
  kopyayla değiştirilmez — Ren'Py'nin çalışma anındaki davranışı da budur.
- **Arşiv silinir.** Bırakılsaydı aynı veri APK'ya ikinci kez girerdi.
- **Arşiv okunamazsa derleme durur.** Yarım açılmış bir oyunla devam etmek,
  sessizce bozuk bir APK üretmek olurdu.
- **Güvenlik:** arşiv dizini bir `pickle`'dır ve pickle çözmek tasarımı
  gereği kod çalıştırabilir. Yalnızca temel veri kuruculara izin veren
  kısıtlı bir çözücü kullanılıyor; `..` içeren ya da mutlak yol veren
  girdiler hedef klasörün dışına yazamaz.
- **Desteklenen biçimler:** RPA-2.0, RPA-3.0, RPA-3.2. RPA-1.0 (dizini ayrı
  bir `.rpi` dosyasında tutan Ren'Py 6.x öncesi biçim) desteklenmiyor.

---

## 🖼️ Uygulama ikonu

Ren'Py, Android ikonunu proje kökündeki **iki** 432×432 PNG dosyasından
üretir: `android-icon_foreground.png` (şeffaf ön katman) ve
`android-icon_background.png` (opak arka plan).

- **Tam kontrol:** Bu iki dosyayı kendiniz hazırlayıp projenizin köküne
  (`game/` ile aynı seviyeye) ekleyin. Görselin **içeriği korunur**;
  yalnızca kap standartlaştırılır (aşağıya bakın).
- **Kolay yol:** Arayüzden tek bir kare görsel yükleyin; ortalanıp
  oranlı küçültülür, beyaz opak arka planla eşleştirilir.
- **Space'e gömmek:** Bu depoya `icon.png` / `icon.jpg` ekleyip yeniden
  build alın; o andan itibaren her derlemede otomatik kullanılır.

### Neden projenin kendi ikonuna da dokunuyoruz

Bu dosyaları RAPT, `pygame` ile açıp ölçekler
(`rapt/buildlib/rapt/iconmaker.py`): `image.load` + `convert_alpha` +
`smoothscale` — hepsi yerel (native) kod. Bozuk ya da olağandışı büyük bir
PNG orada, Python tarafında **hiçbir iz bırakmadan** çökebilir.

Bu yüzden her katman, derlemeden önce Pillow ile açılıp 432×432 RGBA
PNG olarak yeniden kodlanıyor. Böylece `pygame`'e giden dosya her zaman
küçük, sağlam ve öngörülebilir oluyor.

- Zaten 432×432 olan bir katman **yeniden boyutlandırılmaz** (işlem
  kararlıdır, görsel küçülmez).
- ~64 megapiksel üstü bir kaynak reddedilir.
- Bir katman okunamazsa **derleme durmaz**: o katman atlanır ve Ren'Py
  kendi varsayılanını kullanır; günlüğe sebep yazılır.
- Değişiklik geçici kopyada yapılır — özgün dosyalarınıza dokunulmaz.

---

## 🔌 WordPress eklentisi (v8.9)

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
| **`profil` sütunu** (v8.9) | `aerokey_istatistik` tablosuna eklendi |
| **`GET/POST /kimlik`** (v8.9) | Yeni uç: cihaz kimliğine bağlı ad + avatar |

`/durum`, `/liderlik` ve `/profil` uçları artık avatar bilgisini de
döndürüyor — mevcut alanların hiçbiri kaldırılmadı, yalnızca yeni alan
eklendi, yani eski istemciler etkilenmez.

Kart çevirme, pity, referans, anket, görev anahtarı, VIP, `/sync`,
`/oyun-suresi` ve kısa kod — hepsi olduğu gibi korundu. Özellikle
**`/sync`'in süre mantığına hiç dokunulmadı**; ad yazma işi ayrı bir uca
(`/kimlik`) taşındı.

Yeni tablo ve yeni sütun, eklenti güncellendiğinde `plugins_loaded`
denetimiyle kendiliğinden oluşur; eski kurulumlarda activation hook
yeniden çalışmadığı (ve mevcut tabloda `dbDelta` sütun eklemediği) için bu
denetim gerekli.

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
| `Launch failed (returned -11)` / `KeyError: 'bottom'` / `SDL video driver (dummy)` | **Ekran sunucusu yok.** Aşağıya bakın. |
| `Packaging internal data.` sonrası `Unable to launch Ren'Py: Status 1` (yığın izi YOK) | Süreç bir sinyalle öldürülmüş — genelde **bellek yetersizliği**. Aşağıya bakın. |

### `Launch failed (returned -11)` — ekran sunucusu sorunu

Belirtiler şöyle görünür:

```
Could not get pygame screen: error('OpenGL support is either not configured
in SDL or not available in current SDL video driver (dummy) or platform')
Launch failed (returned -11).
...
KeyError: 'bottom'
```

**Bu hata projenizin kodundan kaynaklanmaz.** Ren'Py, APK üretmeden önce
projeyi bir kez **açıp** kapatır — derleme meta verisini (`build` sözlüğü,
`android_permissions`, `version`) toplamak için. Bu çağrı Ren'Py
Launcher'ın kaynağında koşulsuzdur, atlanamaz. Toplanan veri APK'nın
kendisi için gerekli olduğundan "atlayıp devam etmek" de mümkün değildir.

### Asıl sebep: Ren'Py sürücüyü kendisi "dummy" yapıyor

Sorun yalnızca "ekran sunucusu yok" değildi. Ren'Py'nin kendi kaynağında
(`renpy/arguments.py`) şu var:

```python
register_command("quit", quit)      # uses_display varsayılanı False
...
if not display[command]:
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
```

`quit` komutu **display gerektirmiyor** olarak kayıtlı. Yani Ren'Py, bu
alt süreçte sürücüyü **kendi kararıyla** `dummy` yapar — ortamda geçerli
bir `DISPLAY` olsa bile. Dummy sürücünün OpenGL'i yoktur; `gl2` ve `gles2`
başarısız olur, yazılım (`sw`) render'ına düşülür ve orada segfault gelir
(`-11` = sinyal 11).

Bu, Ren'Py'nin bilinen bir motor hatasıdır ve **gerçek GPU'lu
masaüstlerinde de** görülür — bkz.
[renpy/renpy#4549](https://github.com/renpy/renpy/issues/4549), aynı
belirtiler Docker'sız bir Linux masaüstünde `test` komutuyla.

`KeyError: 'bottom'` **asıl sebep değildir:** alt süreç çökünce Launcher
kendi "Launching the project failed" penceresini çizmeye çalışır, ama
komut satırı kipinde ekran katmanları kurulmadığı için o da çöker. Yani
görünen hata, asıl sebebin üstünü örten **ikincil** bir çökmedir.

### İkinci sebep: Steam entegrasyonu

Ekran sorunu çözüldükten sonra alt süreç **hâlâ** `-11` ile ölüyordu, ama
bu kez ekranla ilgisi yoktu. Ren'Py'nin Steam kapısı
(`renpy/common/00steam.rpy`):

```python
dll_path = os.path.join(os.path.dirname(sys.executable), dll_name)
has_steam = os.path.exists(dll_path)
if not has_steam:
    return
...
if "RENPY_NO_STEAM" in os.environ:
    return
```

`sys.executable` **Ren'Py SDK'sının kendi python'u**, yani aranan
`libsteam_api.so` SDK'nın `lib/` klasöründe. Sonuç: projenin Steam ile
hiçbir ilgisi olmasa bile, bu SDK ile yapılan **her** derlemede Steam'in
yerel (native) kodu yükleniyor ve `InitFlat()` çağrılıyor. Steam
çalışmayan bir konteynerde bu çağrı başarısız oluyor ve süreç bunun
ardından segfault veriyor — Python seviyesinde hiçbir iz bırakmadan.

Günlükte alt sürecin `Running init code` satırına hiç ulaşmadan, tam
Steam mesajlarının ardından öldüğü görülüyor.

**Çözüm:** Ren'Py'nin kendi belgelenmiş kapısı kullanılıyor —
`RENPY_NO_STEAM` tanımlıysa Steam'e hiç dokunulmuyor. Android APK'sında
Steam zaten bulunmadığı için bunu kapatmak yalnızca güvenli değil, doğru
olanı.

### Üçüncü sebep: oyunun kendi init kodu

Steam de kapatıldıktan sonra alt süreç **hâlâ** `-11` ile ölüyordu. Ren'Py'nin
`main.py` dosyasında iki günlük satırı arasındaki kod şu:

```python
log_clock("Loading persistent")
...
for id_, (_prio, node) in enumerate(game.script.initcode):
    node.execute_init()          # <-- OYUNUN kendi init python blokları
...
log_clock("Running init code")
```

Alt süreç `Loading persistent`'ı yazıyor ama `Running init code`'a hiç
ulaşmıyor. Yani çökme **oyunun kendi init kodunun içinde**. Sebebi oyundan
oyuna değişir (yerel kütüphane, yazı tipi, ses aygıtı…) ve dışarıdan
güvenilir biçimde düzeltilemez — çalışan şey oyunun kendi kodudur.

### Çözüm: adımı düzeltmek yerine ona olan bağımlılığı kaldırmak

Bu adımı çalıştırmaya çalışmak yerine, **hiç çalıştırmıyoruz**.

Launcher'ın bu alt süreçten aldığı tek şey `navigation.json` içindeki
`build` sözlüğü ve oradan okuduğu alanlar yalnızca şunlar:

| Alan | Nereden geliyor |
|---|---|
| `google_play_key` | yoksa `None` (isteğe bağlı) |
| `google_play_salt` | yoksa `None` (isteğe bağlı) |
| `destination` | yalnızca GUI kipinde okunur, bizde okunmaz |
| `version` | zaten biliyoruz |
| `android_permissions` | derlenmiş `.rpyc`'den taranıyor |

Paketleyici bu dosyayı derlemeden önce kendisi yazıyor
(`aerokey/build_dump.py`), Launcher da yamalı hâliyle
`update_dump(force=False)` çağırıyor — dosya hazır olduğu için alt süreci
**hiç başlatmıyor**.

İzinler, derlenmiş kodda `android.permission.XXX` düz metin olarak durduğu
için taranarak kurtarılıyor; bulunanlar derleme günlüğüne yazılıyor.

> Dump dosyası yoksa davranış değişmez: Ren'Py yine alt süreci başlatıp
> dump üretir. Yani bu yama hiçbir şeyi bozmaz, yalnızca hazır dump varsa
> ona öncelik verir.

### Çözüm iki katmanlı

**1. Sanal ekran.** İmajda `xvfb` ve `libgl1-mesa-dri` bulunur; uygulama
açılışta bellekte çalışan bir ekran başlatır. Doğrulandı: bu ekran altında
Mesa/llvmpipe ile gerçek bir OpenGL 4.5 bağlamı kuruluyor.

**2. Launcher yaması + `RENPY_NO_STEAM` (asıl düzeltme).** Yalnızca ekran açmak yetmez —
Ren'Py yukarıdaki kodla sürücüyü yine `dummy` yapardı. `patch_rapt.py`,
Launcher'ın alt süreci başlattığı yeri yamalar ve `DISPLAY` varsa
`SDL_VIDEODRIVER=x11` değerini **önceden** ayarlar. Ren'Py `setdefault`
kullandığı için bu değeri ezmez; böylece gerçek bir GL bağlamı kurulur ve
çöken `sw` yoluna hiç düşülmez.

> Yalnızca `DISPLAY` varken devreye girer. Windows/macOS ve gerçek
> masaüstü kullanımı etkilenmez.

Space günlüğünde şu iki satırı görmelisiniz:

```
[ekran] Sanal ekran hazır: :99 (Xvfb başlatıldı (1280x1024x24))
[aerokey] SDK yamalanıyor: /root/.renutil/8.5.3
```

> **Performans:** Xvfb hiçbir şeyi fiziksel olarak çizmez, yalnızca bellekte
> bir kare tamponu tutar. Konteyner başına **bir kez** başlatılır, her
> derlemede değil. Söz konusu adım oyunu yalnızca açıp kapattığı için ek
> yük saniyeler mertebesindedir ve dakikalar süren Gradle aşamasının
> yanında ihmal edilebilir.

---

## `Packaging internal data.` sonrası sessiz ölüm

Derleme şuraya kadar geliyor, sonra hiçbir açıklama olmadan kesiliyorsa:

```
Updating project.
Creating assets directory.
Packaging internal data.
Error: Unable to launch Ren'Py: Status 1
```

### "Status 1" aslında bir çıkış kodu değil

`renutil`'in kaynağında (`renkit/src/renutil.rs`, `launch()`):

```rust
if check_status && !status.success() {
    anyhow::bail!("Unable to launch Ren'Py: Status {}",
                  status.code().unwrap_or(1));
}
```

Rust'ta `status.code()` **yalnızca** süreç bir sinyalle öldürüldüğünde
`None` döner. `.unwrap_or(1)` o durumda yedek olarak `1` basar. Yani
buradaki "Status 1", gerçek bir 1 çıkış kodundan **ayırt edilemez**.

Ayırt edici olan şey başka: `renutil` alt sürecin hem stdout hem stderr'ini
canlı akıtır. Gerçek bir Python hatası olsaydı yığın izi günlükte
görünürdü. **Yığın izi yoksa süreç Python'a hiç uğramadan ölmüştür** —
SIGKILL (bellek bitti) ya da SIGSEGV (yerel kod çöktü).

### Nerede ölüyor

RAPT'ın kaynağında (`rapt/buildlib/rapt/build.py`) iki mesaj arası:

```python
684: iface.info(__("Packaging internal data."))   # <- günlüğün son satırı
...
694: make_tar(iface, private_mp3, private_dirs)
696: with open(private_mp3, "rb") as f:
697:     private_version = hashlib.md5(f.read()).hexdigest()
...
713:     iconmaker.IconMaker(directory, config)
...
737: iface.info(__("I'm using Gradle to build the package."))  # <- ulaşılmıyor
```

**Birinci sebep — `f.read()`.** `private.mp3`, motorun ve dört Android
mimarisi için native kütüphanelerin `tar.gz` arşividir; yüzlerce MB
olabilir. Bu satır dosyanın **tamamını tek seferde belleğe** alır. Bellek
sınırı dar bir konteynerde OOM-killer devreye girer.

**İkinci sebep — `IconMaker`.** Projenin ikon dosyalarını `pygame` ile
açıp ölçekler (`image.load` + `convert_alpha` + `smoothscale`) — hepsi
yerel kod. Bozuk veya olağandışı büyük bir görüntü burada iz bırakmadan
çökebilir.

### Çözüm

| Ne | Nasıl |
|---|---|
| Bellek tepesi | `md5` artık 4 MB'lık parçalar hâlinde hesaplanıyor. Aynı özet, sabit bellek. |
| İkon girdisi | Her ikon katmanı artık **bizim tarafımızda** 432×432 RGBA PNG'ye yeniden kodlanıyor — proje kendi ikonunu sağlasa bile. |
| Görünürlük | Paketleme aşamasının alt adımları günlüğe işaretleniyor. |
| Ön bilgi | Derleme başlarken bellek/disk durumu günlüğe yazılıyor. |

Ölçüm (300 MB'lik bir `private.mp3` ile, `tracemalloc`):

| | Tepe bellek |
|---|---|
| Özgün `f.read()` | 300.0 MB |
| Parçalı okuma | 8.2 MB |

Üretilen `md5` **birebir aynı** — özet fonksiyonu birikimli olduğu için
parçalı beslemek sonucu değiştirmez.

Günlükte artık şunları görürsünüz:

```
Kaynak durumu: bellek 1.4 GB boş / 2.0 GB (cgroup v2), disk(/tmp) 12.1 GB boş
...
[aerokey] adim: private.mp3 arsivi olusturuluyor | bos bellek: 1.20 GB
[aerokey] adim: private.mp3 ozeti (md5) hesaplaniyor | bos bellek: 1.18 GB
[aerokey] adim: sablonlar isleniyor | bos bellek: 1.18 GB
[aerokey] adim: uygulama ikonu uretiliyor | bos bellek: 1.17 GB
```

Yine olursa hangi adımda olduğu doğrudan okunur; artık tahmin gerekmez.

> **Neden `/proc/meminfo` yetmez:** konteyner içinde o dosya HOST makinenin
> belleğini gösterir. 64 GB RAM'li bir sunucuda çalışan 2 GB sınırlı bir
> konteyner "62 GB boş" der ve sınırına çarpıp ölür. Gerçek sınır
> cgroup'ta yazar; önce oraya bakılıyor.

> **Bu yama ölümcül değildir.** Tutunma noktaları bulunamazsa (ileride
> Ren'Py bu satırları değiştirirse) uyarı basılır ve derleme normal şekilde
> sürer. Manifest yaması ise bilinçli olarak ölümcüldür: sessizce atlanırsa
> lisans ekranı olmayan bir APK üretilirdi.

---

## Teşekkür / Lisanslar

- [Ren'Py](https://www.renpy.org/) — MIT lisans
- [renkit](https://github.com/kobaltcore/renkit) (kobaltcore) — MIT lisans,
  bu Space'in derleme otomasyonunun temeli
- AeroKey Lisans Yöneticisi (Riaslink) — bu Space'in konuştuğu WordPress eklentisi
- Bu Space'in kendi kodu da MIT lisansıyla paylaşılabilir.
