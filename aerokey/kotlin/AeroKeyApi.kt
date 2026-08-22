package com.riaslink.aerokey

import android.util.Log
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.OutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

/**
 * AeroKey WordPress eklentisinin `lisans/v1` REST uçlarına konuşan ince bir
 * istemci. Bilinçli olarak hiçbir üçüncü parti kütüphane kullanmaz
 * (HttpURLConnection + org.json, ikisi de Android'in içinde gelir) — böylece
 * Ren'Py'nin Gradle projesine tek bir yeni bağımlılık bile eklemiyoruz ve
 * derleme sırasında indirilecek ekstra bir şey oluşmuyor.
 *
 * TÜM fonksiyonlar ağ işi yapar; ASLA ana iş parçacığından çağırmayın
 * (AeroKeyAsync.run yardımcı fonksiyonunu kullanın).
 */
internal object AeroKeyApi {

    private const val TAG = "AeroKey"
    private const val ROOT = "/wp-json/lisans/v1"

    /** Ağ katmanının döndürdüğü sonuç: ya gövde JSON'u ya da hata sebebi. */
    sealed class Result {
        data class Ok(val body: JSONObject) : Result()
        data class Failed(val reason: String, val offline: Boolean) : Result()
    }

    /** Lisans doğrulama sonucunun uygulama seviyesindeki karşılığı. */
    data class LicenseState(
        val valid: Boolean,
        val expiresText: String,
        val message: String
    )

    // --- Uçlar -----------------------------------------------------------

    /** GET /kontrol?anahtar=... — normal (anahtarlı) lisans doğrulama. */
    fun checkKey(key: String): LicenseState {
        val res = get("/kontrol?anahtar=" + enc(key))
        return toLicenseState(res, "Anahtar doğrulanamadı.")
    }

    /** GET /vip-kontrol?device_id=... — cihaza tanımlı VIP erişimi. */
    fun checkVip(deviceId: String): LicenseState {
        val res = get("/vip-kontrol?device_id=" + enc(deviceId))
        return toLicenseState(res, "Bu cihaz için VIP kaydı bulunamadı.")
    }

    /**
     * POST /sync — oynama süresini gönderir, sunucu daha yüksek bir değer
     * tutuyorsa onu geri verir.
     *
     * Sunucu üç durumdan birini döner:
     *  - `guncellendi` : bizim gönderdiğimiz süre kaydedildi
     *  - `esit`        : değişiklik yok
     *  - `buluttan_yukle`: sunucudaki değer daha yüksek, istemci onu almalı
     */
    fun sync(
        deviceId: String,
        username: String,
        totalSeconds: Long,
        gameId: String,
        gameSeconds: Long,
        achievementsRaw: String
    ): Result {
        val body = JSONObject()
        body.put("cihaz_id", deviceId)
        body.put("kullanici_adi", username)
        body.put("toplam_saniye", totalSeconds)
        body.put("oyun_id", gameId)
        body.put("oyun_saniye", gameSeconds)
        // Eklenti bu alanı json_encode'dan geçirdiği için dizi/nesne olarak
        // göndermek gerekiyor; ham metni ayrıştıramazsak boş dizi yollarız.
        body.put("basarimlar", parseAchievements(achievementsRaw))
        return post("/sync", body)
    }

    /**
     * GET /durum — sunucunun anlık durumu: bugün "ücretsiz gün" mü, ve
     * oyuncunun henüz görmediği duyurular neler.
     *
     * Tek bir uç noktada topladık: hem ücretsiz gün hem bildirimler düzenli
     * aralıkla sorgulanıyor, ikisini ayrı çağrılara bölmek gereksiz trafik
     * olurdu.
     *
     * Eklentinin ESKİ sürümlerinde bu uç nokta yoktur; o durumda sunucu 404
     * döner ve çağıran taraf bunu "ücretsiz gün yok, duyuru yok" olarak
     * yorumlar — yani eski eklentiyle de çalışmaya devam eder.
     */
    fun status(gameId: String, lastNoticeId: Long, deviceId: String): Result =
        get(
            "/durum?oyun_id=" + enc(gameId) +
                "&son_duyuru=" + lastNoticeId +
                "&cihaz_id=" + enc(deviceId)
        )

    /**
     * Cihaza bağlı bulut kimliği: sıralama adı + avatar.
     *
     * Oyuncunun adını neden /sync ile değil de ayrı bir uçla kaydediyoruz:
     * /sync, adı yalnızca gönderilen süre sunucudakinden BÜYÜKSE yazıyor.
     * Ad yeni seçildiğinde süre genelde eşit kaldığı için ad sunucuya hiç
     * işlenmiyordu — silip yeniden kuran oyuncuya adı tekrar sorulmasının
     * asıl sebebi buydu.
     */
    data class Identity(
        val registered: Boolean,
        val username: String,
        val avatar: String
    )

    /** GET /kimlik?cihaz_id=... — bu cihaza daha önce bağlanmış profil. */
    fun identity(deviceId: String): Identity {
        val res = get("/kimlik?cihaz_id=" + enc(deviceId))
        return toIdentity(res)
    }

    /** POST /kimlik — adı ve avatarı cihaz kimliğine kalıcı olarak bağlar. */
    fun saveIdentity(deviceId: String, username: String, avatar: String): Identity {
        val body = JSONObject()
        body.put("cihaz_id", deviceId)
        body.put("kullanici_adi", username)
        body.put("profil", avatar)
        return toIdentity(post("/kimlik", body))
    }

    /**
     * Kimlik yanıtını çözer.
     *
     * Eklentinin ESKİ sürümlerinde /kimlik yoktur; 404 gelir ve burası
     * "kayıtlı değil" döner. Yani eski eklentiyle de akış kırılmaz, sadece
     * ad bir kez daha sorulur.
     */
    private fun toIdentity(res: Result): Identity = when (res) {
        is Result.Failed -> Identity(false, "", "")
        is Result.Ok -> {
            val body = res.body
            val name = body.optString("kullanici_adi", "").trim()
            Identity(
                registered = body.optBoolean("kayitli", false) && name.isNotEmpty(),
                username = name,
                avatar = body.optString("profil", "").trim()
            )
        }
    }

    /** GET /liderlik — en çok oynayan ilk 10 kişi. */
    fun leaderboard(): Result = get("/liderlik")

    /** GET /profil?kullanici_adi=... — tek bir oyuncunun özeti. */
    fun profile(username: String): Result =
        get("/profil?kullanici_adi=" + enc(username))

    /** GET /anket — yayında olan anket (yoksa durum=hata döner). */
    fun survey(): Result = get("/anket")

    /** POST /anket — ankete oy verir (oy: 1 veya 2). */
    fun vote(surveyId: Int, choice: Int): Result {
        val body = JSONObject()
        body.put("anket_id", surveyId)
        body.put("oy", choice)
        return post("/anket", body)
    }

    /** POST /hata-bildir — oyuncudan gelen hata/geri bildirim mesajı. */
    fun reportBug(deviceId: String, username: String, message: String): Result {
        val body = JSONObject()
        body.put("cihaz_id", deviceId)
        body.put("kullanici_adi", username)
        body.put("mesaj", message)
        return post("/hata-bildir", body)
    }

    // --- Yardımcılar -----------------------------------------------------

    /**
     * Sunucudan gelen ham `basarimlar` metnini eklentiye geri gönderilebilir
     * bir JSON değerine çevirir. Bozuk/boş ise veri kaybetmemek adına boş
     * dizi döneriz (eklenti her senkronda bu alanın üzerine yazıyor).
     */
    private fun parseAchievements(raw: String): Any {
        val text = raw.trim()
        if (text.isEmpty()) return JSONArray()
        return try {
            when (text.first()) {
                '[' -> JSONArray(text)
                '{' -> JSONObject(text)
                else -> JSONArray()
            }
        } catch (_: Exception) {
            JSONArray()
        }
    }

    private fun toLicenseState(res: Result, fallbackMessage: String): LicenseState =
        when (res) {
            is Result.Failed -> LicenseState(false, "", res.reason)
            is Result.Ok -> {
                val body = res.body
                if (body.optString("durum") == "basarili") {
                    LicenseState(true, body.optString("bitis", ""), "")
                } else {
                    val serverMessage = body.optString("mesaj", "")
                    val text = when {
                        serverMessage.equals("Suresi dolmus", true) ->
                            "Bu lisansın süresi dolmuş."
                        serverMessage.isNotBlank() -> serverMessage
                        else -> fallbackMessage
                    }
                    LicenseState(false, "", text)
                }
            }
        }

    private fun enc(value: String): String =
        try {
            URLEncoder.encode(value, "UTF-8")
        } catch (_: Exception) {
            value
        }

    private fun get(path: String): Result = request("GET", path, null)

    private fun post(path: String, body: JSONObject): Result =
        request("POST", path, body)

    private fun request(method: String, path: String, body: JSONObject?): Result {
        val url = AeroKeyConfig.BASE_URL.trimEnd('/') + ROOT + path
        var conn: HttpURLConnection? = null
        try {
            conn = (URL(url).openConnection() as HttpURLConnection).apply {
                requestMethod = method
                connectTimeout = AeroKeyConfig.NETWORK_TIMEOUT_MS
                readTimeout = AeroKeyConfig.NETWORK_TIMEOUT_MS
                useCaches = false
                setRequestProperty("Accept", "application/json")
                setRequestProperty("User-Agent", "AeroKey-Android/1.0")
            }

            if (body != null) {
                conn.doOutput = true
                conn.setRequestProperty("Content-Type", "application/json; charset=utf-8")
                val payload = body.toString().toByteArray(Charsets.UTF_8)
                conn.setFixedLengthStreamingMode(payload.size)
                val out: OutputStream = conn.outputStream
                out.write(payload)
                out.flush()
                out.close()
            }

            val code = conn.responseCode
            val stream = if (code in 200..299) conn.inputStream else conn.errorStream
            val text = stream?.let { readAll(it) } ?: ""

            if (text.isBlank()) {
                return Result.Failed("Sunucu boş yanıt döndü (HTTP $code).", false)
            }

            return try {
                Result.Ok(JSONObject(text))
            } catch (_: Exception) {
                // Genelde WordPress'in HTML hata sayfası ya da bir güvenlik
                // duvarı sayfası döndüğünde buraya düşeriz.
                Result.Failed("Sunucu beklenmeyen bir yanıt döndü (HTTP $code).", false)
            }
        } catch (e: Exception) {
            Log.w(TAG, "İstek başarısız: $url", e)
            return Result.Failed("Sunucuya ulaşılamadı. İnternet bağlantınızı kontrol edin.", true)
        } finally {
            conn?.disconnect()
        }
    }

    private fun readAll(stream: java.io.InputStream): String {
        BufferedReader(InputStreamReader(stream, Charsets.UTF_8)).use { reader ->
            val sb = StringBuilder()
            var line = reader.readLine()
            while (line != null) {
                sb.append(line)
                line = reader.readLine()
            }
            return sb.toString()
        }
    }
}

/**
 * Ağ çağrılarını arka planda çalıştırıp sonucu ana iş parçacığına taşıyan
 * minik yardımcı. Coroutine/AsyncTask kullanmıyoruz: ilki yeni bir Gradle
 * bağımlılığı gerektirir, ikincisi kullanımdan kaldırıldı.
 */
internal object AeroKeyAsync {

    private val mainHandler = android.os.Handler(android.os.Looper.getMainLooper())

    fun <T> run(work: () -> T, onDone: (T) -> Unit) {
        Thread {
            val result = work()
            mainHandler.post { onDone(result) }
        }.apply {
            isDaemon = true
            name = "aerokey-net"
            start()
        }
    }

    fun onMain(action: () -> Unit) = mainHandler.post(action)
}

/** Saniyeyi "3 sa 12 dk" gibi okunur bir metne çevirir. */
internal fun formatPlaytime(seconds: Long): String {
    if (seconds <= 0) return "0 dk"
    val hours = seconds / 3600
    val minutes = (seconds % 3600) / 60
    return when {
        hours > 0 && minutes > 0 -> "$hours sa $minutes dk"
        hours > 0 -> "$hours sa"
        else -> "${minutes.coerceAtLeast(1)} dk"
    }
}
