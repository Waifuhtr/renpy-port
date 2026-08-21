package com.riaslink.aerokey

import android.app.Activity
import android.app.Application
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.util.Log
import java.lang.ref.WeakReference
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Oyun açıkken arka planda çalışan oturum yöneticisi. İki işi vardır:
 *
 *  1. OYNAMA SÜRESİ: Uygulama ön plandayken saniyeleri sayar, belirli
 *     aralıklarla AeroKey sunucusuna gönderir (`/sync`). Sunucu daha yüksek
 *     bir toplam tutuyorsa (kullanıcı başka bir cihazda oynamışsa) o değeri
 *     alıp yerelde günceller.
 *  2. LİSANS DENETİMİ: Belirli aralıklarla lisansı yeniden doğrular; süre
 *     dolmuşsa oyunu kapatıp giriş ekranına döner.
 *
 * Kendi `Application` sınıfımızı tanımlamıyoruz — bu, Ren'Py'nin manifest
 * şablonuna fazladan müdahale gerektirirdi. Bunun yerine giriş ekranı,
 * oyunu başlatmadan hemen önce buradaki [install] fonksiyonunu çağırır ve
 * yaşam döngüsü geri çağrıları uygulamanın kendi Application nesnesine
 * kaydedilir; süreç yaşadığı sürece çalışır.
 */
internal object AeroKeySession {

    private const val TAG = "AeroKey"

    private val installed = AtomicBoolean(false)

    private var appContext: Context? = null
    private var ticker: Thread? = null

    /** Ön planda olan Activity sayısı; 0 ise oyun arkada demektir. */
    @Volatile private var foregroundCount = 0

    /** Süre dolduğunda kapatmamız gereken, o an ekranda olan oyun ekranı. */
    private var currentActivity: WeakReference<Activity>? = null

    /** Aynı anda birden fazla senkron/denetim isteği çıkmasını engeller. */
    private val syncInFlight = AtomicBoolean(false)
    private val recheckInFlight = AtomicBoolean(false)

    /** Süre dolmuş uyarısını yalnızca bir kez göstermek için. */
    private val expiryHandled = AtomicBoolean(false)

    @Volatile private var totalSeconds = 0L
    @Volatile private var gameSeconds = 0L
    @Volatile private var secondsSinceSync = 0
    @Volatile private var secondsSinceRecheck = 0

    /** Sunucudan gelen son duyuru metni (giriş ekranında gösterilir). */
    @Volatile var announcement: String = ""
        private set

    /** Son senkronda bildirilen çevrimiçi oyuncu sayısı. */
    @Volatile var onlineCount: Int = 0
        private set

    fun install(activity: Activity) {
        if (!AeroKeyConfig.ENABLED) return
        if (!installed.compareAndSet(false, true)) return

        val app = activity.application ?: return
        val context = activity.applicationContext
        appContext = context

        totalSeconds = AeroKeyPrefs.totalSeconds(context)
        gameSeconds = AeroKeyPrefs.gameSeconds(context)

        app.registerActivityLifecycleCallbacks(object : Application.ActivityLifecycleCallbacks {
            override fun onActivityResumed(a: Activity) {
                foregroundCount++
                if (a !is AeroKeyGateActivity) currentActivity = WeakReference(a)
            }

            override fun onActivityPaused(a: Activity) {
                foregroundCount = (foregroundCount - 1).coerceAtLeast(0)
                // Oyundan çıkarken/arka plana alırken biriken süreyi hemen
                // yollayalım ki süreç öldürülürse veri kaybolmasın.
                if (foregroundCount == 0) {
                    persist()
                    pushSync()
                }
            }

            override fun onActivityCreated(a: Activity, s: Bundle?) = Unit
            override fun onActivityStarted(a: Activity) = Unit
            override fun onActivityStopped(a: Activity) = Unit
            override fun onActivitySaveInstanceState(a: Activity, s: Bundle) = Unit
            override fun onActivityDestroyed(a: Activity) = Unit
        })

        startTicker()
    }

    /** Giriş ekranı, doğrulama başarılı olduğunda süre sayacını sıfırlar. */
    fun onLicenseVerified() {
        expiryHandled.set(false)
        secondsSinceRecheck = 0
    }

    private fun startTicker() {
        if (ticker != null) return
        ticker = Thread {
            while (true) {
                try {
                    Thread.sleep(1000L)
                } catch (_: InterruptedException) {
                    return@Thread
                }

                if (foregroundCount <= 0) continue

                totalSeconds++
                gameSeconds++
                secondsSinceSync++
                secondsSinceRecheck++

                if (secondsSinceSync >= AeroKeyConfig.SYNC_INTERVAL_SECONDS) {
                    secondsSinceSync = 0
                    persist()
                    pushSync()
                }

                if (secondsSinceRecheck >= AeroKeyConfig.LICENSE_RECHECK_SECONDS) {
                    secondsSinceRecheck = 0
                    recheckLicense()
                }
            }
        }.apply {
            isDaemon = true
            name = "aerokey-session"
            start()
        }
    }

    private fun persist() {
        val context = appContext ?: return
        AeroKeyPrefs.saveSeconds(context, totalSeconds, gameSeconds)
    }

    /**
     * Biriken süreyi sunucuya gönderir. Sunucu daha büyük bir değer
     * tutuyorsa (`buluttan_yukle`) onu benimseriz — böylece kullanıcı
     * cihaz değiştirdiğinde süresi geri gelir.
     */
    fun pushSync() {
        val context = appContext ?: return
        if (!AeroKeyConfig.ENABLED) return
        if (!syncInFlight.compareAndSet(false, true)) return

        val deviceId = AeroKeyPrefs.deviceId(context)
        val username = AeroKeyPrefs.username(context)
        val achievements = AeroKeyPrefs.achievementsRaw(context)
        val sentTotal = totalSeconds
        val sentGame = gameSeconds

        AeroKeyAsync.run({
            AeroKeyApi.sync(
                deviceId, username, sentTotal,
                AeroKeyConfig.GAME_ID, sentGame, achievements
            )
        }) { result ->
            syncInFlight.set(false)
            if (result !is AeroKeyApi.Result.Ok) return@run

            val body = result.body
            announcement = body.optString("duyuru", announcement)
            onlineCount = body.optInt("canli", onlineCount)

            if (body.optString("durum") == "buluttan_yukle") {
                val cloudTotal = body.optLong("saniye", 0L)
                val cloudGame = body.optLong("oyun_saniye", 0L)
                // Yalnızca yukarı doğru düzeltiyoruz: bu sırada oyuncu
                // oynamaya devam ettiyse yerelde biriken saniyeleri de
                // koruyalım, aksi halde süre geriye sıçramış gibi görünür.
                if (cloudTotal > totalSeconds) {
                    totalSeconds = cloudTotal + (totalSeconds - sentTotal)
                }
                if (cloudGame > gameSeconds) {
                    gameSeconds = cloudGame + (gameSeconds - sentGame)
                }
                val cloudName = body.optString("kullanici_adi", "")
                if (cloudName.isNotBlank() && cloudName != AeroKeyPrefs.DEFAULT_USERNAME) {
                    AeroKeyPrefs.setUsername(context, cloudName)
                }
                persist()
            }
        }
    }

    /**
     * Sunucudaki başarım listesini bir kez okuyup saklar.
     *
     * `/sync` bu alanın üzerine her seferinde yazdığı için, kullanıcının
     * AeroKey hesabında başka bir uygulamadan gelen başarımlar varsa onları
     * ezmemek adına mevcut değeri alıp aynen geri gönderiyoruz.
     */
    fun primeAchievements(context: Context) {
        if (!AeroKeyConfig.ENABLED) return
        val username = AeroKeyPrefs.username(context)
        if (username.isBlank()) return

        AeroKeyAsync.run({ AeroKeyApi.profile(username) }) { result ->
            if (result is AeroKeyApi.Result.Ok &&
                result.body.optString("durum") == "basarili"
            ) {
                val raw = result.body.opt("basarimlar")
                if (raw != null && raw.toString().isNotBlank() && raw.toString() != "null") {
                    AeroKeyPrefs.setAchievementsRaw(context, raw.toString())
                }
            }
        }
    }

    /**
     * Lisansı sessizce yeniden doğrular. Süresi dolmuşsa oyunu kapatıp
     * giriş ekranını "süre doldu" mesajıyla öne getirir.
     *
     * Ağ hatası burada KASITLI olarak yok sayılır: geçici bir bağlantı
     * kopukluğu yüzünden oyuncuyu oyundan atmak istemeyiz, yalnızca
     * sunucunun açıkça "geçersiz" demesi bizim için bağlayıcıdır.
     */
    private fun recheckLicense() {
        val context = appContext ?: return
        if (!AeroKeyPrefs.hasStoredLicense(context)) return
        if (!recheckInFlight.compareAndSet(false, true)) return

        val vip = AeroKeyPrefs.isVip(context)
        val key = AeroKeyPrefs.licenseKey(context)
        val deviceId = AeroKeyPrefs.deviceId(context)

        AeroKeyAsync.run({
            if (vip) AeroKeyApi.checkVip(deviceId) else AeroKeyApi.checkKey(key)
        }) { state ->
            recheckInFlight.set(false)
            if (state.valid) {
                AeroKeyPrefs.saveLicense(context, key, vip, state.expiresText)
                return@run
            }
            // Sunucuya ulaşılamadıysa oyunu bölmüyoruz.
            if (state.message.contains("ulaşılamadı")) {
                Log.i(TAG, "Lisans denetimi ertelendi: bağlantı yok.")
                return@run
            }
            handleExpiry(context, state.message)
        }
    }

    private fun handleExpiry(context: Context, message: String) {
        if (!expiryHandled.compareAndSet(false, true)) return

        AeroKeyPrefs.clearLicense(context)
        persist()
        pushSync()

        val activity = currentActivity?.get()
        val intent = Intent(context, AeroKeyGateActivity::class.java).apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
            putExtra(AeroKeyGateActivity.EXTRA_EXPIRED_MESSAGE, message)
        }
        context.startActivity(intent)
        // Oyun ekranını da kapatıyoruz ki geri tuşuyla lisanssız
        // devam edilemesin.
        activity?.finish()
    }

    // --- Giriş ekranının okuduğu anlık değerler --------------------------

    fun currentTotalSeconds(context: Context): Long =
        if (installed.get()) totalSeconds else AeroKeyPrefs.totalSeconds(context)

    fun currentGameSeconds(context: Context): Long =
        if (installed.get()) gameSeconds else AeroKeyPrefs.gameSeconds(context)
}
