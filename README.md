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
**APK** ve/veya **AAB** (Google Play) paketi üretsin.

Bu araç kendi başına bir "Android'e dönüştürme" motoru değildir; kaputun
altında resmi Ren'Py derleme zincirini (RAPT + Gradle) çalıştıran, aktif
olarak bakımı yapılan açık kaynak **renkit** aracını (`renutil` +
`renconstruct`) otomatikleştirir:
👉 https://github.com/kobaltcore/renkit (MIT lisans)

---

## ⚠️ Önce şunu netleştirelim: `renpy-build` bu işi yapmıyor

Sorduğunuz `renpy/renpy-build` reposu gerçek ve önemli bir proje, ama işlevi
sandığınızdan farklı: **tek tek oyunları paketlemiyor.** O repo, Ren'Py
**motorunun kendisini** (Python, SDL, FFmpeg gibi native bağımlılıklarla
birlikte) Windows/Linux/macOS/Android/iOS/Web için **çapraz derleyen** bir
build sistemidir — Ren'Py'nin geliştiricisi PyTom'un, Ren'Py SDK'sının her
sürümünü ve Android/iOS'a gömülen alt bileşenleri üretmek için kullandığı
bir araçtır. Kendisinin bir Gradio/CLI "oyunu yükle, APK al" arayüzü yoktur
ve tek bir oyun projesini derlemek için tasarlanmamıştır.

Bir *oyunu* Android paketine dönüştürmenin gerçek yolu, Ren'Py SDK'sıyla
birlikte gelen **RAPT** (Ren'Py Android Packaging Tool) ve onun kullandığı
Gradle derleme hattıdır. Bu Space de tam olarak bunu, `renkit` üzerinden
otomatikleştirerek yapıyor.

---

## Kurulum (Hugging Face'te bu Space'i oluşturma)

1. Hugging Face'te **yeni bir Space** oluşturun.
2. SDK olarak mutlaka **Docker**'ı seçin ("Gradio" SDK'sını DEĞİL — çünkü
   düz Gradio SDK'sı yalnızca `requirements.txt`'teki Python paketlerini
   kurar; bize Java, Android derleme araçları gibi sistem seviyesi
   bağımlılıklar gerekiyor, bunlar için Dockerfile şart).
3. Bu klasördeki 4 dosyayı (`Dockerfile`, `app.py`, `requirements.txt`,
   `README.md`) Space'inizin kök dizinine ekleyin (Hugging Face web
   arayüzünden tek tek yükleyebilir ya da Space'i yerel makinenize
   `git clone`'layıp dosyaları oraya kopyalayıp `git push` edebilirsiniz).
4. Space otomatik olarak build almaya başlar. **İlk build 10-20+ dakika
   sürebilir** çünkü Java, renkit ve tüm Ren'Py SDK'sı imaja gömülüyor.
5. Build bitince arayüz açılır ve kullanmaya hazırsınız.

### Donanım hakkında

Android/Gradle derlemeleri hem RAM hem disk açısından ağırdır. Ücretsiz/en
düşük donanım katmanında build'ler çok yavaş olabilir ya da bellek
yetersizliğinden başarısız olabilir. Sorun yaşarsanız Space
**Settings → Hardware** kısmından daha güçlü bir CPU katmanına
yükseltmeyi deneyin (güncel seçenekler ve kaynak miktarları için Hugging
Face'in kendi fiyatlandırma sayfasına bakın, burada belirli bir GB/vCPU
rakamı vermiyoruz çünkü bu rakamlar zamanla değişebiliyor).

---

## Kullanım

1. **Proje ZIP'i**: Ren'Py proje klasörünüzü (içinde `game/` klasörü olan
   klasörü) zip'leyip yükleyin. Klasörü doğrudan zip'lemişseniz de
   (`game/` zip'in kökünde), bir üst klasörle birlikte zip'lemişseniz de
   (`projem/game/...`) araç bunu otomatik olarak anlar.
2. **Ren'Py sürümü**: Varsayılan olarak imaja gömülü sürüm önerilir (en
   hızlısı budur). Farklı bir sürüm girerseniz ilk seferde ayrıca indirilir.
3. **APK / AAB**: APK, doğrudan telefona kurmak veya Play dışı mağazalara
   yüklemek içindir. AAB, Google Play Store'a yüklemek içindir.
4. **Paket adı öneki**: Projenizde `build.package` açıkça tanımlı değilse,
   paket adı `önek.oyunadı` şeklinde üretilir (varsayılan önek:
   `com.riaslinkfun` — kendi öneğinizle değiştirebilirsiniz). Aynı oyun
   için bu her zaman aynı sonucu üretir (güncelleme sürekliliği bozulmaz),
   ama projenizde kendi `build.package`'ınız tanımlıysa o kullanılır, bu
   alan yok sayılır.
5. **Gelişmiş → Uygulama İkonu** (isteğe bağlı): Tek bir kare görsel
   yükleyin, Ren'Py'nin istediği iki katmanlı adaptif ikona (bkz. aşağı)
   otomatik dönüştürülür. Boş bırakırsanız, Space'e gömülü bir `icon.png`/
   `icon.jpg` varsa o kullanılır (bkz. "İkon" bölümü), o da yoksa Ren'Py
   varsayılan ikonunu kullanır.
6. **Gelişmiş → Keystore** (isteğe bağlı ama önemli, aşağıya bakın).
7. **"Android Paketini Oluştur"**'a basın; sağdaki günlük penceresinde
   derleme çıktısını canlı olarak izleyebilirsiniz. Bitince dosyalar
   indirme alanında belirir.

---

## 🖼️ Uygulama İkonu

Ren'Py, Android ikonunu projenizin kök dizinindeki **iki** 432×432 PNG
dosyasından üretir (bkz. [resmi belge](https://www.renpy.org/doc/html/android.html#icon-and-presplash-images)):
`android-icon_foreground.png` (şeffaf ön katman) ve
`android-icon_background.png` (opak arka plan) — Google'ın "adaptive icon"
sistemi bu ikisini cihazda birleştirir.

Bu araç, size tek bir görsel yükleme kolaylığı sağlar:

- **Space'e kalıcı olarak eklemek isterseniz:** Bu repoya (Dockerfile'ın
  yanına) `icon.png` veya `icon.jpg` olarak bir dosya ekleyip Space'i
  yeniden build alın. O andan itibaren her derlemede otomatik kullanılır.
- **Sadece bu derlemeye özel bir ikon denemek isterseniz:** Arayüzdeki
  "Gelişmiş → Uygulama İkonu" alanından yükleyin; Space'e gömülü olanın
  yerine geçer.
- Her iki durumda da araç, yüklediğiniz görseli ortalayıp oranlı şekilde
  küçültür, şeffaf bir tuval üzerine yerleştirir (ön katman) ve düz beyaz
  opak bir arka plan üretir (arka plan katmanı) — **yalnızca o derlemenin
  geçici çalışma kopyasına**.
- **Tam kontrol isterseniz:** `android-icon_foreground.png` ve
  `android-icon_background.png` dosyalarını kendiniz hazırlayıp
  projenizin köküne (`game/` ile aynı seviyeye) eklerseniz, araç onlara
  hiç dokunmaz, olduğu gibi kullanılırlar.

---

## 🔑 İmzalama anahtarı (keystore) hakkında ÖNEMLİ uyarı

Bir keystore belirtmezseniz, her derlemede **rastgele/geçici** bir imzalama
anahtarı otomatik üretilir. Bu, hızlı test kurulumları için sorun değildir.
**Ancak:**

- Android, farklı imza anahtarıyla imzalanmış APK'leri **farklı uygulamalar**
  olarak görür. Bugün ürettiğiniz APK'yi cihazınıza kurup yarın anahtarsız
  tekrar derlerseniz, yeni APK eskisinin **üzerine kurulamaz** (önce
  kaldırmanız gerekir).
- Google Play, bir uygulamanın tüm güncellemelerinin **aynı** anahtarla
  imzalanmasını zorunlu kılar. Rastgele üretilen bir anahtarla Play'e ilk
  sürümü yükleyip o anahtarı kaybederseniz, o uygulamayı bir daha
  güncelleyemezsiniz.

**Öneri:** Ciddi bir proje için "Gelişmiş" bölümünden kendi `.keystore`/
`.jks` dosyanızı (alias + şifresiyle birlikte) yükleyin ve bu dosyayı
**güvenli bir yerde saklayın** — kaybederseniz geri döndürülemez.
Keystore dosyanız yalnızca derleme sırasında bellekte/ortam değişkeni
olarak kullanılır, diske düz metin olarak yazılmaz.

---

## Sınırlamalar

- **Her Ren'Py sürümü test edilmedi.** Ren'Py'nin Android derleme zinciri
  (RAPT) yıllar içinde birkaç kez köklü şekilde değişti (Ant → Gradle,
  Python 2 → 3, ayrı RAPT paketi → launcher'a entegre, JDK 8 → 21).
  Bu Dockerfile güncel (8.x, ≥8.2.0) sürümler için Java 21 kurar; çok eski
  bir Ren'Py sürümüyle çalışacaksanız `Dockerfile`'daki
  `eclipse-temurin:21-jdk` satırını `eclipse-temurin:8-jdk` ile
  değiştirmeniz gerekebilir.
- **Uygulama adı / paket adı / sürüm numarası bu arayüzden ayarlanmaz.**
  Bunlar Ren'Py projenizin kendi `game/options.rpy` dosyasındaki
  `build.name`, `build.package` (ya da launcher'ın "Configure" adımında
  girdiğiniz bilgiler) tarafından belirlenir — bu araç sadece mevcut proje
  yapılandırmanızla derlemeyi otomatikleştirir, projenizin kimliğini
  değiştirmez. **İki istisna:**
  1. Ren'Py'nin Android derlemesi, `build.directory_name` (tanımlı değilse
     `build.name`/`config.name`'den türetilir) boşluk/`:`/`;` içerirse
     derlemeyi tamamen reddeder. Bu çok yaygın bir sorun olduğu için araç,
     **yalnızca o derlemenin geçici çalışma kopyasında**
     `game/options.rpy`'yi otomatik düzeltir.
  2. Proje daha önce Ren'Py Launcher üzerinden Android için hiç
     "Configure" edilmemişse (proje kökünde `android.json` yoksa), Ren'Py
     "Run configure before attempting to build the app" diyerek build'i
     reddeder. Araç bu durumda, `options.rpy`'den (varsa `build.name`,
     `build.package`, `config.version`) türetilmiş makul varsayılanlarla
     **yalnızca geçici çalışma kopyasına** bir `android.json` oluşturur.
     Paket adı, aynı oyun için derlemeler arasında **sabit** kalacak
     şekilde (rastgelelik kullanmadan) oyunun adından türetilir — ama
     `com.renpyandroidbuilder.*` biçiminde, gerçek yayın için **uygun
     olmayan** otomatik bir isimdir.

  Her iki durumda da orijinal dosyalarınıza asla dokunulmaz ve ne
  değiştirildiği/oluşturulduğu günlükte açıkça belirtilir. Kalıcı ve
  size ait bir paket adı istiyorsanız (özellikle Play Store için), kendi
  projenizde Ren'Py Launcher > Android > Configure adımını bir kez
  tamamlayıp oluşan `android.json` dosyasını projenizin köküne (`game/`
  ile aynı seviyeye) ekleyip zip'e dahil edin — dosya zaten varsa araç
  ona dokunmaz, olduğu gibi kullanır.
- **İlk Android derlemesi yavaştır.** RAPT, Android SDK bileşenlerini
  (build-tools, platform, gerekirse NDK) ilk gerçek derlemede ayrıca
  indirir; imaja yalnızca Ren'Py SDK'sının kendisi gömülüdür.
- Aynı anda yalnızca **bir** derleme çalışır (kaynak kullanımını
  sınırlamak için); ikinci bir istek, öndeki bitene kadar kuyrukta bekler.
- Bu Space içerik üretmiyor / barındırmıyor — yalnızca **sizin
  yüklediğiniz** projeyi paketliyor. Yüklediğiniz oyunun hakları size ait
  olmalı ya da paketlemeye yetkiniz olmalı.

---

## Sorun Giderme

| Belirti | Olası neden / çözüm |
|---|---|
| Build "OutOfMemory" / Gradle daemon hatasıyla çöküyor | Space donanımınızda yeterli RAM yok; daha güçlü bir donanım katmanına geçin. |
| `renutil install X.Y.Z` sürüm bulamıyor | Sürüm numarasını https://www.renpy.org/release_list.html üzerinden doğrulayın. |
| "'game/' klasörü bulunamadı" hatası | ZIP'i, içinde `game` klasörü **görünecek** şekilde oluşturun (proje klasörünü doğrudan seçip zip'leyin). |
| Java/Gradle sürüm uyuşmazlığı hataları | Kullandığınız Ren'Py sürümüne göre Dockerfile'daki JDK sürümünü (8 ↔ 21) güncelleyin. |
| Günlük penceresi bazen tek satır yerine parça parça güncelleniyor | Bu, altta çalışan araçların kendi tamponlama davranışından kaynaklanır; işlevi etkilemez. |
| Yükleme büyük ZIP dosyasını reddediyor | Kurulu Gradio sürümünün azami dosya boyutu ayarını kontrol edin/artırın. |

---

## Teşekkür / Lisanslar

- [Ren'Py](https://www.renpy.org/) — MIT lisans
- [renkit](https://github.com/kobaltcore/renkit) (kobaltcore) — MIT lisans,
  bu Space'in derleme otomasyonunun temelini oluşturuyor
- Bu Space'in kendi kodu (`app.py`, `Dockerfile`) da MIT lisansıyla
  paylaşılabilir; dilediğiniz gibi değiştirip kullanabilirsiniz.
