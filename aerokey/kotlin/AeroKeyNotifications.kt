package com.riaslink.aerokey

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.job.JobInfo
import android.app.job.JobParameters
import android.app.job.JobScheduler
import android.app.job.JobService
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.os.Build
import android.util.Log
import org.json.JSONArray

/**
 * Eklentiden gelen önemli duyuruları cihazın bildirim çekmecesine düşürür.
 *
 * GERÇEK "PUSH" DEĞİL, ÇEKME (POLLING) — NEDEN?
 * ---------------------------------------------
 * Gerçek push (FCM) bir Firebase projesi, `google-services.json` dosyası ve
 * `firebase-messaging` bağımlılığı ister; bu da her oyun için ayrı bir
 * Firebase kurulumu demek. Bu entegrasyonun tamamı bilinçli olarak SIFIR ek
 * Gradle bağımlılığıyla yazıldı, o yüzden bildirimleri çekerek alıyoruz:
 *
 *   - Oyun açıkken: [AeroKeySession] düzenli aralıkla sorar (hızlı iletim).
 *   - Oyun kapalıyken: JobScheduler ile yaklaşık 15 dakikada bir sorulur
 *     (Android'in izin verdiği en sık periyot).
 *
 * Kullanıcı açısından sonuç aynı: bildirim çekmecesinde gerçek bir sistem
 * bildirimi belirir. Fark yalnızca iletim gecikmesinde.
 */
internal object AeroKeyNotifications {

    private const val TAG = "AeroKey"
    private const val CHANNEL_ID = "aerokey_duyuru"
    private const val JOB_ID = 41287

    /** Aynı anda çok sayıda bildirim yığmamak için üst sınır. */
    private const val MAX_PER_CHECK = 5

    // --- Kanal -----------------------------------------------------------

    private fun ensureChannel(context: Context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val manager = context.getSystemService(Context.NOTIFICATION_SERVICE)
            as? NotificationManager ?: return
        if (manager.getNotificationChannel(CHANNEL_ID) != null) return

        val channel = NotificationChannel(
            CHANNEL_ID, "Duyurular", NotificationManager.IMPORTANCE_DEFAULT
        ).apply {
            description = "Oyun ve anahtarlarla ilgili önemli duyurular"
        }
        manager.createNotificationChannel(channel)
    }

    // --- Sorgulama -------------------------------------------------------

    /**
     * Sunucuya sorar; yeni duyuru varsa bildirim olarak gösterir.
     *
     * ARKA PLAN İŞ PARÇACIĞINDAN çağrılmalıdır (ağ erişimi yapar).
     * Dönen değer: "ücretsiz gün" bilgisi, çağıranın kullanabilmesi için.
     */
    fun checkNow(context: Context): FreeAccess {
        if (!AeroKeyConfig.ENABLED) return FreeAccess.none()

        val app = context.applicationContext
        val lastId = AeroKeyPrefs.lastNoticeId(app)
        val result = AeroKeyApi.status(
            AeroKeyConfig.GAME_ID, lastId, AeroKeyPrefs.deviceId(app)
        )
        if (result !is AeroKeyApi.Result.Ok) return FreeAccess.none()

        val body = result.body
        showNewNotices(app, body.optJSONArray("duyurular"))

        val highest = body.optLong("son_duyuru_id", lastId)
        if (highest > lastId) AeroKeyPrefs.setLastNoticeId(app, highest)

        return FreeAccess(
            active = body.optBoolean("serbest", false),
            message = body.optString("serbest_mesaj", "").ifBlank {
                "Bugün anahtarlar ücretsiz!"
            }
        )
    }

    private fun showNewNotices(context: Context, notices: JSONArray?) {
        if (notices == null || notices.length() == 0) return
        ensureChannel(context)

        val manager = context.getSystemService(Context.NOTIFICATION_SERVICE)
            as? NotificationManager ?: return

        val count = minOf(notices.length(), MAX_PER_CHECK)
        for (i in 0 until count) {
            val notice = notices.optJSONObject(i) ?: continue
            val id = notice.optLong("id", System.currentTimeMillis())
            val title = notice.optString("baslik", "").ifBlank { AeroKeyConfig.GAME_TITLE }
            val message = notice.optString("mesaj", "")
            if (message.isBlank()) continue

            try {
                manager.notify(id.toInt(), build(context, title, message))
            } catch (e: Exception) {
                // Android 13+ üzerinde POST_NOTIFICATIONS izni verilmemişse
                // buraya düşeriz; bildirim gösterilemez ama oyun etkilenmez.
                Log.i(TAG, "Bildirim gösterilemedi (izin yok olabilir).", e)
                return
            }
        }
    }

    private fun build(context: Context, title: String, message: String): Notification {
        val launch = context.packageManager
            .getLaunchIntentForPackage(context.packageName)
            ?.apply { addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP) }

        var flags = PendingIntent.FLAG_UPDATE_CURRENT
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            flags = flags or PendingIntent.FLAG_IMMUTABLE
        }
        val pending = if (launch != null) {
            PendingIntent.getActivity(context, 0, launch, flags)
        } else {
            null
        }

        val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            Notification.Builder(context, CHANNEL_ID)
        } else {
            @Suppress("DEPRECATION")
            Notification.Builder(context)
        }

        return builder
            .setContentTitle(title)
            .setContentText(message)
            .setStyle(Notification.BigTextStyle().bigText(message))
            // Uygulamanın kendi ikonunu kullanıyoruz: kaynak klasörüne yeni
            // bir simge eklemek gerekmiyor (o klasör her derlemede yeniden
            // üretiliyor ve eklediğimiz dosya kaybolurdu).
            .setSmallIcon(context.applicationInfo.icon)
            .setAutoCancel(true)
            .apply { if (pending != null) setContentIntent(pending) }
            .build()
    }

    /** "Ücretsiz gün" bilgisinin taşıyıcısı. */
    data class FreeAccess(val active: Boolean, val message: String) {
        companion object {
            fun none() = FreeAccess(false, "")
        }
    }

    // --- Arka plan denetimi ----------------------------------------------

    /**
     * Oyun kapalıyken de duyuru alabilmek için düzenli bir iş planlar.
     *
     * Android, periyodik işler için en az 15 dakika dayatır; daha sık
     * istemek sessizce 15 dakikaya yuvarlanır. İş zaten planlıysa yeniden
     * planlamıyoruz, aksi halde her açılışta sayaç sıfırlanırdı.
     */
    fun scheduleBackgroundChecks(context: Context) {
        if (!AeroKeyConfig.ENABLED) return
        if (!AeroKeyConfig.NOTIFICATIONS_ENABLED) return

        val scheduler = context.getSystemService(Context.JOB_SCHEDULER_SERVICE)
            as? JobScheduler ?: return

        val alreadyScheduled = try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
                scheduler.getPendingJob(JOB_ID) != null
            } else {
                scheduler.allPendingJobs.any { it.id == JOB_ID }
            }
        } catch (_: Exception) {
            false
        }
        if (alreadyScheduled) return

        val job = JobInfo.Builder(
            JOB_ID, ComponentName(context, AeroKeyNotificationJobService::class.java)
        )
            .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
            .setPersisted(true)
            .setPeriodic(15 * 60 * 1000L)
            .build()

        try {
            scheduler.schedule(job)
        } catch (e: Exception) {
            Log.i(TAG, "Arka plan duyuru denetimi planlanamadı.", e)
        }
    }
}

/**
 * Oyun kapalıyken duyuruları kontrol eden arka plan işi.
 *
 * Manifest'e kaydı `patch_rapt.py` tarafından yapılır (BIND_JOB_SERVICE
 * izniyle, dışa kapalı).
 */
class AeroKeyNotificationJobService : JobService() {

    @Volatile private var worker: Thread? = null

    override fun onStartJob(params: JobParameters?): Boolean {
        val thread = Thread {
            try {
                AeroKeyNotifications.checkNow(applicationContext)
            } catch (_: Exception) {
                // Ağ yoksa ya da sunucu yanıt vermiyorsa sessizce geçiyoruz;
                // bir sonraki periyotta yeniden denenecek.
            } finally {
                worker = null
                jobFinished(params, false)
            }
        }
        thread.isDaemon = true
        worker = thread
        thread.start()
        // true: iş arka planda sürüyor, bittiğinde jobFinished çağıracağız.
        return true
    }

    override fun onStopJob(params: JobParameters?): Boolean {
        worker?.interrupt()
        worker = null
        // true: sistem işi daha sonra yeniden denesin.
        return true
    }
}
