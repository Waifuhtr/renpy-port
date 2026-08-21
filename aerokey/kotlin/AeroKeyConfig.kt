package com.riaslink.aerokey

/**
 * Derleme başına ÜRETİLEN yapılandırma.
 *
 * Bu dosyanın depodaki hali yalnızca güvenli bir varsayılandır: Ren'Py
 * Android Paketleyici (app.py), her derleme için bu dosyayı kullanıcının
 * arayüzde girdiği değerlerle YENİDEN YAZAR. Yani buradaki sabitleri elle
 * değiştirmenin bir anlamı yoktur; arayüzdeki "AeroKey Lisans Entegrasyonu"
 * bölümünü kullanın.
 *
 * ENABLED = false olduğunda giriş ekranı hiç çizilmez, uygulama açılır
 * açılmaz doğrudan oyuna geçer (ölçülebilir bir gecikme oluşturmaz).
 */
internal object AeroKeyConfig {

    /** Lisans/giriş ekranı tamamen devrede mi? */
    const val ENABLED: Boolean = false

    /** WordPress sitesinin kökü, sonda eğik çizgi OLMADAN. */
    const val BASE_URL: String = "https://riaslink.fun"

    /** "Anahtar Al" düğmesinin açacağı sayfa. */
    const val KEY_PAGE_URL: String = "https://riaslink.fun/bilgi"

    /** Bu oyunu sunucudaki istatistiklerde tekil olarak tanımlayan kimlik. */
    const val GAME_ID: String = "riaslink_oyun_001"

    /** Giriş ekranında gösterilecek oyun adı. */
    const val GAME_TITLE: String = "Ren'Py Game"

    // --- İsteğe bağlı paneller ------------------------------------------
    const val FEATURE_LEADERBOARD: Boolean = false
    const val FEATURE_SURVEY: Boolean = false
    const val FEATURE_PROFILE: Boolean = false
    const val FEATURE_BUG_REPORT: Boolean = false

    // --- Zamanlama ------------------------------------------------------
    /** Oynama süresinin sunucuya kaç saniyede bir gönderileceği. */
    const val SYNC_INTERVAL_SECONDS: Int = 60

    /** Oyun açıkken lisansın kaç saniyede bir yeniden doğrulanacağı. */
    const val LICENSE_RECHECK_SECONDS: Int = 600

    /** Ağ isteklerinin zaman aşımı (milisaniye). */
    const val NETWORK_TIMEOUT_MS: Int = 15000
}
