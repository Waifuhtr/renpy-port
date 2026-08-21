package com.riaslink.aerokey

import android.app.Activity
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.graphics.LinearGradient
import android.graphics.Shader
import android.net.Uri
import android.os.Bundle
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.view.inputmethod.InputMethodManager
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast

/**
 * Oyunun açılış (launcher) ekranı: AeroKey lisans geçidi.
 *
 * Ren'Py'nin kendi `PythonSDLActivity` ekranı, yalnızca buradan geçilince
 * başlatılır. Lisans doğrulaması başarılıysa bu ekran kapanır ve oyuna
 * geçilir; oyun açıkken süre sayımı ve düzenli lisans denetimi
 * [AeroKeySession] tarafından sürdürülür.
 *
 * AeroKeyConfig.ENABLED = false ise ekran hiç çizilmez, doğrudan oyuna
 * geçilir — böylece entegrasyonu kapatmak tek bir bayrak meselesidir.
 */
class AeroKeyGateActivity : Activity() {

    companion object {
        /** Süre dolduğunda oturum yöneticisinin ilettiği açıklama. */
        const val EXTRA_EXPIRED_MESSAGE = "aerokey_expired_message"

        private const val GAME_ACTIVITY = "org.renpy.android.PythonSDLActivity"
    }

    private lateinit var root: FrameLayout
    private lateinit var card: LinearLayout
    private lateinit var statusText: TextView
    private lateinit var keyInput: EditText
    private lateinit var verifyButton: Button
    private lateinit var vipButton: Button
    private lateinit var busyOverlay: View

    private var busy = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // Entegrasyon kapalıysa hiçbir şey çizmeden oyuna geç.
        if (!AeroKeyConfig.ENABLED) {
            launchGame()
            return
        }

        window.setFlags(
            WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
            WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS
        )
        window.setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE)

        buildUi()

        val expiredMessage = intent?.getStringExtra(EXTRA_EXPIRED_MESSAGE)
        if (!expiredMessage.isNullOrBlank()) {
            showStatus(expiredMessage, Palette.danger)
        } else {
            // Elimizde daha önce doğrulanmış bir lisans varsa, kullanıcıyı
            // hiç uğraştırmadan sessizce tazeleyip oyuna geçmeyi deneriz.
            tryAutoLogin()
        }

        AeroKeySession.primeAchievements(this)
    }

    // --- Arayüz kurulumu -------------------------------------------------

    private fun buildUi() {
        root = FrameLayout(this)
        root.addView(
            AuroraBackgroundView(this),
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT
            )
        )

        val scroll = ScrollView(this).apply {
            isVerticalScrollBarEnabled = false
            isFillViewport = true
            clipToPadding = false
            setPadding(dp(20), dp(48), dp(20), dp(32))
        }

        val holder = column().apply { gravity = Gravity.CENTER_HORIZONTAL }

        holder.addView(buildHeader())
        holder.addSpace(dp(26))

        card = column().apply {
            background = glassCard(this@AeroKeyGateActivity)
            setPadding(dp(24), dp(26), dp(24), dp(26))
        }
        buildCardContent(card)
        holder.addView(
            card,
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
            )
        )

        holder.addSpace(dp(18))
        holder.addView(buildDeviceIdChip())

        // Liderlik / profil / anket / hata bildirimi burada DEĞİL: bunlar
        // oyun içinde açılan yüzen menüde yaşıyor (bkz. AeroKeyOverlay).
        // Giriş ekranının tek işi doğrulama; oyuncu daha oyuna girmeden
        // liderlik tablosuna bakmak istemez.

        holder.addSpace(dp(16))
        holder.addView(bodyText("AeroKey ile korunmaktadır", 11f).apply {
            setTextColor(Palette.textMuted)
            gravity = Gravity.CENTER
        })

        scroll.addView(holder)
        root.addView(scroll)

        busyOverlay = buildBusyOverlay()
        root.addView(busyOverlay)

        setContentView(root)

        // Giriş animasyonu: parçalar sırayla süzülerek belirir.
        var delay = 60L
        for (i in 0 until holder.childCount) {
            holder.getChildAt(i).enterWithFade(delay)
            delay += 70L
        }
    }

    private fun buildHeader(): View {
        val header = column().apply { gravity = Gravity.CENTER_HORIZONTAL }

        header.addView(TextView(this).apply {
            text = AeroKeyConfig.GAME_TITLE
            textSize = 27f
            typeface = android.graphics.Typeface.DEFAULT_BOLD
            gravity = Gravity.CENTER
            setTextColor(Palette.textPrimary)
            // Başlığa yatay bir renk geçişi uygula.
            post {
                if (width > 0) {
                    paint.shader = LinearGradient(
                        0f, 0f, width.toFloat(), 0f,
                        intArrayOf(Palette.accentAlt, Palette.accent, Palette.accentWarm),
                        null, Shader.TileMode.CLAMP
                    )
                    invalidate()
                }
            }
        })
        header.addSpace(dp(8))
        header.addView(bodyText("Devam etmek için erişim anahtarını doğrula", 13f).apply {
            gravity = Gravity.CENTER
        })
        return header
    }

    private fun buildCardContent(container: LinearLayout) {
        container.addView(sectionLabel("ERİŞİM ANAHTARI"))
        container.addSpace(dp(10))

        keyInput = styledInput("RIASKEY-XXXXXXXX").apply {
            inputType = InputType.TYPE_CLASS_TEXT or
                InputType.TYPE_TEXT_FLAG_CAP_CHARACTERS or
                InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
            setText(AeroKeyPrefs.licenseKey(this@AeroKeyGateActivity))
        }
        container.addView(
            keyInput,
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
            )
        )
        container.addSpace(dp(14))

        verifyButton = primaryButton("Doğrula ve Başlat").apply {
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
            )
            setOnClickListener { verifyKey() }
        }
        container.addView(verifyButton)
        container.addSpace(dp(10))

        val buttonRow = row()
        vipButton = secondaryButton("⭐ VIP Üyeyim").apply {
            setOnClickListener { verifyVip() }
        }
        buttonRow.addView(
            vipButton,
            LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
                .apply { rightMargin = dp(8) }
        )
        buttonRow.addView(
            secondaryButton("🔑 Anahtar Al").apply { setOnClickListener { openKeyPage() } },
            LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        )
        container.addView(buttonRow)

        container.addSpace(dp(14))
        statusText = bodyText("").apply {
            gravity = Gravity.CENTER
            visibility = View.GONE
        }
        container.addView(statusText)
    }

    /**
     * Cihaz kimliği kartı. VIP tanımlaması bu kimliğe göre yapıldığı için,
     * kullanıcının kimliği kolayca kopyalayıp iletebilmesi gerekiyor.
     */
    private fun buildDeviceIdChip(): View {
        val deviceId = AeroKeyPrefs.deviceId(this)

        val chip = row().apply {
            background = GradientDrawableCompat.pill(this@AeroKeyGateActivity)
            setPadding(dp(16), dp(12), dp(12), dp(12))
        }

        val texts = column()
        texts.addView(sectionLabel("CİHAZ KİMLİĞİN"))
        texts.addSpace(dp(4))
        texts.addView(TextView(this).apply {
            text = deviceId
            setTextColor(Palette.textPrimary)
            textSize = 13f
            maxLines = 1
            ellipsize = android.text.TextUtils.TruncateAt.MIDDLE
            typeface = android.graphics.Typeface.MONOSPACE
        })
        chip.addView(
            texts,
            LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        )

        chip.addView(ghostButton("Kopyala").apply {
            setOnClickListener { copyDeviceId(deviceId, this) }
        })
        return chip
    }


    private fun buildBusyOverlay(): View {
        val overlay = FrameLayout(this).apply {
            setBackgroundColor(Color.parseColor("#99070A14"))
            visibility = View.GONE
            isClickable = true // arkadaki düğmelere dokunmayı engelle
        }
        overlay.addView(
            SpinnerView(this),
            FrameLayout.LayoutParams(dp(46), dp(46), Gravity.CENTER)
        )
        return overlay
    }

    // --- Eylemler --------------------------------------------------------

    private fun tryAutoLogin() {
        if (!AeroKeyPrefs.hasStoredLicense(this)) return

        setBusy(true)
        showStatus("Kayıtlı lisansın denetleniyor…", Palette.textSecondary)

        val vip = AeroKeyPrefs.isVip(this)
        val key = AeroKeyPrefs.licenseKey(this)
        val deviceId = AeroKeyPrefs.deviceId(this)

        AeroKeyAsync.run({
            if (vip) AeroKeyApi.checkVip(deviceId) else AeroKeyApi.checkKey(key)
        }) { state ->
            setBusy(false)
            when {
                state.valid -> {
                    AeroKeyPrefs.saveLicense(this, key, vip, state.expiresText)
                    onAccessGranted(state.expiresText)
                }
                // Ağ yoksa, daha önce doğrulanmış lisansla çevrimdışı devam
                // etmesine izin veriyoruz: oyuncuyu internet kesintisi
                // yüzünden kendi oyunundan kilitlemek doğru olmaz.
                state.message.contains("ulaşılamadı") -> {
                    showStatus("Çevrimdışı mod: kayıtlı lisansınla devam ediliyor.", Palette.gold)
                    onAccessGranted(AeroKeyPrefs.expiresText(this))
                }
                else -> {
                    AeroKeyPrefs.clearLicense(this)
                    showStatus(state.message, Palette.danger)
                }
            }
        }
    }

    private fun verifyKey() {
        if (busy) return
        val key = keyInput.text.toString().trim()
        if (key.isEmpty()) {
            showStatus("Önce erişim anahtarını gir.", Palette.danger)
            shake(keyInput)
            return
        }

        hideKeyboard()
        setBusy(true)
        showStatus("Anahtar doğrulanıyor…", Palette.textSecondary)

        AeroKeyAsync.run({ AeroKeyApi.checkKey(key) }) { state ->
            setBusy(false)
            if (state.valid) {
                AeroKeyPrefs.saveLicense(this, key, false, state.expiresText)
                onAccessGranted(state.expiresText)
            } else {
                showStatus(state.message, Palette.danger)
                shake(card)
            }
        }
    }

    private fun verifyVip() {
        if (busy) return
        hideKeyboard()
        setBusy(true)
        showStatus("VIP kaydın sorgulanıyor…", Palette.textSecondary)

        val deviceId = AeroKeyPrefs.deviceId(this)
        AeroKeyAsync.run({ AeroKeyApi.checkVip(deviceId) }) { state ->
            setBusy(false)
            if (state.valid) {
                AeroKeyPrefs.saveLicense(this, "", true, state.expiresText)
                onAccessGranted(state.expiresText)
            } else {
                showStatus(
                    state.message.ifBlank {
                        "Bu cihaz VIP olarak tanımlı değil. Cihaz kimliğini " +
                            "kopyalayıp geliştiriciye iletebilirsin."
                    },
                    Palette.danger
                )
                shake(card)
            }
        }
    }

    private fun openKeyPage() {
        try {
            startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(AeroKeyConfig.KEY_PAGE_URL)))
        } catch (_: Exception) {
            Toast.makeText(this, "Tarayıcı açılamadı.", Toast.LENGTH_SHORT).show()
        }
    }

    private fun copyDeviceId(deviceId: String, anchor: View) {
        try {
            val clipboard = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
            clipboard.setPrimaryClip(ClipData.newPlainText("AeroKey Cihaz ID", deviceId))
            (anchor as? TextView)?.let {
                val original = it.text
                it.text = "Kopyalandı ✓"
                it.setTextColor(Palette.success)
                it.postDelayed({
                    it.text = original
                    it.setTextColor(Palette.textSecondary)
                }, 1600)
            }
        } catch (_: Exception) {
            Toast.makeText(this, "Panoya kopyalanamadı.", Toast.LENGTH_SHORT).show()
        }
    }

    /** Doğrulama başarılı: kısa bir onay animasyonundan sonra oyuna geç. */
    private fun onAccessGranted(expiresText: String) {
        AeroKeySession.onLicenseVerified()

        val message = if (expiresText.isBlank()) {
            "Erişim onaylandı. İyi oyunlar!"
        } else {
            "Erişim onaylandı • $expiresText"
        }
        showStatus(message, Palette.success)

        card.animate()
            .scaleX(1.02f).scaleY(1.02f)
            .setDuration(180)
            .withEndAction {
                card.animate().scaleX(1f).scaleY(1f).setDuration(160).start()
            }
            .start()

        root.postDelayed({ launchGame() }, 700)
    }

    private fun launchGame() {
        try {
            AeroKeySession.install(this)
            val gameClass = Class.forName(GAME_ACTIVITY)
            val intent = Intent(this, gameClass)
            // Oyun ekranı, geçidin üzerine değil onun YERİNE açılmalı;
            // aksi halde geri tuşu lisans ekranına düşer.
            intent.addFlags(Intent.FLAG_ACTIVITY_NO_ANIMATION)
            startActivity(intent)
            overridePendingTransition(0, 0)
            finish()
        } catch (e: ClassNotFoundException) {
            // Bu yalnızca paketleme hatalıysa olur; oyuncuyu boş ekranda
            // bırakmamak için açıkça bildiriyoruz.
            Toast.makeText(
                this, "Oyun başlatılamadı: $GAME_ACTIVITY bulunamadı.", Toast.LENGTH_LONG
            ).show()
        }
    }

    // --- Küçük yardımcılar -----------------------------------------------

    private fun setBusy(value: Boolean) {
        busy = value
        verifyButton.isEnabled = !value
        vipButton.isEnabled = !value
        if (value) {
            busyOverlay.visibility = View.VISIBLE
            busyOverlay.alpha = 0f
            busyOverlay.animate().alpha(1f).setDuration(160).start()
        } else {
            busyOverlay.animate().alpha(0f).setDuration(160).withEndAction {
                busyOverlay.visibility = View.GONE
            }.start()
        }
    }

    private fun showStatus(message: String, color: Int) {
        statusText.setTextColor(color)
        statusText.text = message
        if (statusText.visibility != View.VISIBLE) {
            statusText.visibility = View.VISIBLE
            statusText.alpha = 0f
        }
        statusText.animate().alpha(1f).setDuration(220).start()
    }

    /** Hatalı girişte kartı/alanı hafifçe sallayan geri bildirim. */
    private fun shake(view: View) {
        val distance = dp(9).toFloat()
        android.animation.ObjectAnimator.ofFloat(
            view, "translationX",
            0f, -distance, distance, -distance * 0.6f, distance * 0.6f, 0f
        ).apply {
            duration = 420
            start()
        }
    }

    private fun hideKeyboard() {
        try {
            val imm = getSystemService(Context.INPUT_METHOD_SERVICE) as InputMethodManager
            imm.hideSoftInputFromWindow(root.windowToken, 0)
        } catch (_: Exception) {
            // Klavye zaten kapalıysa önemsiz.
        }
    }

    override fun onBackPressed() {
        // Lisans ekranından geri tuşuyla oyuna sızılamaz; uygulama kapanır.
        finishAffinity()
    }
}

/** Cihaz kimliği kartının arka planı için küçük bir yardımcı. */
internal object GradientDrawableCompat {
    fun pill(context: Context): android.graphics.drawable.GradientDrawable =
        android.graphics.drawable.GradientDrawable().apply {
            cornerRadius = context.dp(16).toFloat()
            setColor(Color.parseColor("#66101528"))
            setStroke(context.dp(1), Color.parseColor("#26FFFFFF"))
        }
}
