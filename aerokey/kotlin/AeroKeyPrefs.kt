package com.riaslink.aerokey

import android.annotation.SuppressLint
import android.content.Context
import android.content.SharedPreferences
import android.provider.Settings
import java.util.UUID

/**
 * Cihaz üzerinde saklanan AeroKey durumu (lisans, kullanıcı adı, biriken
 * oynama süresi). Tümü uygulamaya özel SharedPreferences içindedir;
 * uygulama silinince gider — sunucudaki kayıt cihaz kimliğine bağlı olduğu
 * için yeniden kurulumda süreler buluttan geri yüklenir.
 */
internal object AeroKeyPrefs {

    private const val FILE = "aerokey_state"

    private const val K_LICENSE = "license_key"
    private const val K_IS_VIP = "is_vip"
    private const val K_EXPIRES_TEXT = "expires_text"
    private const val K_LAST_OK_AT = "last_verified_at"
    private const val K_USERNAME = "username"
    private const val K_TOTAL_SECONDS = "total_seconds"
    private const val K_GAME_SECONDS = "game_seconds"
    private const val K_ACHIEVEMENTS = "achievements_raw"
    private const val K_FALLBACK_ID = "fallback_device_id"

    const val DEFAULT_USERNAME = "GizemliOyuncu"

    private fun prefs(context: Context): SharedPreferences =
        context.applicationContext.getSharedPreferences(FILE, Context.MODE_PRIVATE)

    // --- Cihaz kimliği ---------------------------------------------------

    /**
     * Sunucunun VIP eşleştirmesinde ve istatistik kaydında kullandığı kimlik.
     *
     * ANDROID_ID, cihaz + uygulama imzası başına sabittir ve kurulum silinip
     * yeniden kurulsa bile (aynı imzayla) korunur; bu yüzden VIP tanımlamak
     * için doğru seçimdir. Emülatör gibi bazı ortamlarda boş/null gelebildiği
     * için, o durumda bir kez üretilip saklanan yedek bir kimliğe düşeriz.
     */
    @SuppressLint("HardwareIds")
    fun deviceId(context: Context): String {
        val androidId = try {
            Settings.Secure.getString(
                context.applicationContext.contentResolver,
                Settings.Secure.ANDROID_ID
            )
        } catch (_: Exception) {
            null
        }
        if (!androidId.isNullOrBlank() && androidId != "9774d56d682e549c") {
            return androidId
        }

        val p = prefs(context)
        val stored = p.getString(K_FALLBACK_ID, null)
        if (!stored.isNullOrBlank()) return stored

        val generated = UUID.randomUUID().toString().replace("-", "").take(16)
        p.edit().putString(K_FALLBACK_ID, generated).apply()
        return generated
    }

    // --- Lisans ----------------------------------------------------------

    fun licenseKey(context: Context): String =
        prefs(context).getString(K_LICENSE, "") ?: ""

    fun isVip(context: Context): Boolean =
        prefs(context).getBoolean(K_IS_VIP, false)

    fun expiresText(context: Context): String =
        prefs(context).getString(K_EXPIRES_TEXT, "") ?: ""

    fun lastVerifiedAt(context: Context): Long =
        prefs(context).getLong(K_LAST_OK_AT, 0L)

    fun saveLicense(context: Context, key: String, vip: Boolean, expiresText: String) {
        prefs(context).edit()
            .putString(K_LICENSE, key)
            .putBoolean(K_IS_VIP, vip)
            .putString(K_EXPIRES_TEXT, expiresText)
            .putLong(K_LAST_OK_AT, System.currentTimeMillis())
            .apply()
    }

    fun clearLicense(context: Context) {
        prefs(context).edit()
            .remove(K_LICENSE)
            .remove(K_IS_VIP)
            .remove(K_EXPIRES_TEXT)
            .remove(K_LAST_OK_AT)
            .apply()
    }

    /** Daha önce doğrulanmış bir lisans var mı (çevrimdışı açılış için)? */
    fun hasStoredLicense(context: Context): Boolean =
        isVip(context) || licenseKey(context).isNotBlank()

    // --- Kullanıcı adı ---------------------------------------------------

    fun username(context: Context): String =
        prefs(context).getString(K_USERNAME, DEFAULT_USERNAME) ?: DEFAULT_USERNAME

    fun setUsername(context: Context, name: String) {
        val clean = name.trim().take(50).ifBlank { DEFAULT_USERNAME }
        prefs(context).edit().putString(K_USERNAME, clean).apply()
    }

    private const val K_USERNAME_CHOSEN = "username_chosen"

    /**
     * Oyuncu sıralama adını KENDİSİ seçti mi?
     *
     * Ayrı bir bayrak tutuyoruz; "ad varsayılandan farklı mı" diye bakmak
     * yeterli değil, çünkü ad buluttan da gelebiliyor (/sync yanıtı) ve o
     * durumda oyuncuya hiç sorulmamış oluyor. Bir kez seçildikten sonra
     * cihaz kimliğine bağlı olarak kalıcıdır, bir daha sorulmaz.
     */
    fun usernameChosen(context: Context): Boolean =
        prefs(context).getBoolean(K_USERNAME_CHOSEN, false)

    fun markUsernameChosen(context: Context) {
        prefs(context).edit().putBoolean(K_USERNAME_CHOSEN, true).apply()
    }

    // --- Oynama süreleri -------------------------------------------------

    fun totalSeconds(context: Context): Long =
        prefs(context).getLong(K_TOTAL_SECONDS, 0L)

    fun gameSeconds(context: Context): Long =
        prefs(context).getLong(K_GAME_SECONDS, 0L)

    fun saveSeconds(context: Context, total: Long, game: Long) {
        prefs(context).edit()
            .putLong(K_TOTAL_SECONDS, total)
            .putLong(K_GAME_SECONDS, game)
            .apply()
    }

    // --- Başarımlar ------------------------------------------------------

    /**
     * Sunucudaki `basarimlar` alanının ham JSON metni.
     *
     * Bu port başarım üretmez; ama /sync uç noktası bu alanı her çağrıda
     * yeniden yazdığı için, kullanıcının AeroKey hesabında başka bir
     * uygulamadan gelen başarımlar varsa onları SİLMEMEK adına mevcut
     * değeri bir kez okuyup her senkronda aynen geri gönderiyoruz.
     */
    fun achievementsRaw(context: Context): String =
        prefs(context).getString(K_ACHIEVEMENTS, "[]") ?: "[]"

    fun setAchievementsRaw(context: Context, raw: String) {
        prefs(context).edit().putString(K_ACHIEVEMENTS, raw).apply()
    }

    // --- Duyurular --------------------------------------------------------

    private const val K_LAST_NOTICE = "last_notice_id"

    /**
     * Gösterilmiş en yüksek duyuru kimliği.
     *
     * Sunucudan yalnızca bundan YENİ duyuruları istiyoruz; böylece aynı
     * duyuru her denetimde yeniden bildirim olarak düşmüyor.
     */
    fun lastNoticeId(context: Context): Long =
        prefs(context).getLong(K_LAST_NOTICE, 0L)

    fun setLastNoticeId(context: Context, id: Long) {
        prefs(context).edit().putLong(K_LAST_NOTICE, id).apply()
    }

    // --- Oyun içi yüzen menü ---------------------------------------------

    private const val K_OVERLAY_X = "overlay_x"
    private const val K_OVERLAY_Y = "overlay_y"
    private const val K_OVERLAY_HIDDEN = "overlay_hidden"

    /**
     * Baloncuğun konumu, ekran boyutunun ORANI olarak (0..1) saklanır.
     * Piksel saklamak, ekran döndüğünde ya da farklı bir cihazda menünün
     * ekran dışında kalmasına yol açardı.
     *
     * Varsayılan: sağ kenar, dikeyde ortanın biraz üstü — yatay oyunlarda
     * genelde en az iş yapan bölge burasıdır.
     */
    fun overlayPosition(context: Context): Pair<Float, Float> {
        val p = prefs(context)
        return Pair(
            p.getFloat(K_OVERLAY_X, 1f).coerceIn(0f, 1f),
            p.getFloat(K_OVERLAY_Y, 0.32f).coerceIn(0f, 1f)
        )
    }

    fun saveOverlayPosition(context: Context, fractionX: Float, fractionY: Float) {
        prefs(context).edit()
            .putFloat(K_OVERLAY_X, fractionX.coerceIn(0f, 1f))
            .putFloat(K_OVERLAY_Y, fractionY.coerceIn(0f, 1f))
            .apply()
    }

    fun overlayHidden(context: Context): Boolean =
        prefs(context).getBoolean(K_OVERLAY_HIDDEN, false)

    fun setOverlayHidden(context: Context, hidden: Boolean) {
        prefs(context).edit().putBoolean(K_OVERLAY_HIDDEN, hidden).apply()
    }
}
