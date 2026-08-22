package com.riaslink.aerokey

import android.animation.ValueAnimator
import android.app.Activity
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.LinearGradient
import android.graphics.Paint
import android.graphics.RadialGradient
import android.graphics.Shader
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.util.Log
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.view.animation.OvershootInterpolator
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import java.lang.ref.WeakReference

/**
 * Oyunun üzerinde duran AeroKey menüsü: sağ üst köşede SABİT bir düğme ve
 * ondan açılan yatay panel.
 *
 * NEDEN SABİT, NEDEN BURAYA EKLİ?
 * -------------------------------
 * Bu bileşen iki kez baştan yazıldı; ikisinden de öğrendiğimiz şey burada:
 *
 *  1. İlk sürüm düğmeyi Activity'nin görünüm ağacına ekliyor ve
 *     SÜRÜKLENEBİLİR yapıyordu. Düğme görünüyordu ama sürüklenince
 *     kayboluyor, yalnızca yeniden dokununca geri geliyordu. Sebep: Ren'Py
 *     oyunu bir SurfaceView ile çiziliyor ve SurfaceView pencere yüzeyinde
 *     saydam bir "delik" açıyor; o deliğin üstündeki görünümler yalnızca
 *     yeniden çizilen bölgelerde güvenilir biçimde birleştiriliyor.
 *
 *  2. İkinci sürüm menüyü WindowManager ile AYRI BİR PENCEREYE taşıdı.
 *     Bu, kâğıt üzerinde doğru çözümdü ama bu cihaz/Ren'Py birleşiminde
 *     düğme HİÇ görünmedi.
 *
 * Elimizdeki kanıt net: görünüm ağacı çiziyor, ayrı pencere çizmiyor. O
 * yüzden görünüm ağacına dönüyoruz ve sorunun kaynağını — hareketi —
 * ortadan kaldırıyoruz: düğme artık sağ üstte sabit. Sabit bir görünüm
 * yeniden konumlanmadığı için bayat bölge sorunu da oluşmuyor.
 *
 * Kök katman tıklanabilir değil: düğmenin/panelin dışına yapılan dokunuşlar
 * tüketilmeyip alttaki oyuna geçer.
 */
internal object AeroKeyOverlay {

    private const val TAG = "AeroKey"

    private var hostRef: WeakReference<Activity>? = null
    private var rootRef: WeakReference<FrameLayout>? = null

    private var button: View? = null
    private var panel: View? = null
    private var playtimeLabel: TextView? = null

    private var expanded = false
    private var hidden = false
    private var ticker: Runnable? = null

    // --- Yaşam döngüsü ---------------------------------------------------

    fun attach(activity: Activity) {
        if (!AeroKeyConfig.ENABLED) return
        if (!hasAnyFeature()) return
        if (activity is AeroKeyGateActivity) return

        if (hostRef?.get() === activity && rootRef?.get()?.isAttachedToWindow == true) {
            return
        }
        detach()

        val content = activity.findViewById<FrameLayout>(android.R.id.content)
        if (content == null) {
            // Sessizce vazgeçmek, menünün neden yok olduğunu anlaşılmaz
            // kılardı; en azından logcat'e bırakıyoruz.
            Log.w(TAG, "Menü eklenemedi: android.R.id.content bulunamadı.")
            return
        }

        val root = object : FrameLayout(activity) {
            // Kök katman hiçbir dokunuşu kendiliğinden tüketmez; yalnızca
            // çocukları tüketir. Oyun dokunuşları alt katmana ulaşır.
            override fun onTouchEvent(event: MotionEvent): Boolean = false
        }
        root.isClickable = false

        try {
            content.addView(
                root,
                FrameLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT
                )
            )
        } catch (e: Exception) {
            Log.w(TAG, "Menü kök katmanı eklenemedi.", e)
            return
        }

        hostRef = WeakReference(activity)
        rootRef = WeakReference(root)
        expanded = false
        hidden = AeroKeyPrefs.overlayHidden(activity)

        buildButton(activity, root)
        applyHiddenState(animated = false)
    }

    fun onActivityDestroyed(activity: Activity) {
        if (hostRef?.get() === activity) detach()
    }

    fun detach() {
        stopTicker()
        val root = rootRef?.get()
        (root?.parent as? ViewGroup)?.removeView(root)
        hostRef = null
        rootRef = null
        button = null
        panel = null
        playtimeLabel = null
        headerAvatar?.stopAnimation()
        headerAvatar = null
        expanded = false
    }

    private fun hasAnyFeature(): Boolean =
        AeroKeyConfig.FEATURE_LEADERBOARD || AeroKeyConfig.FEATURE_PROFILE ||
            AeroKeyConfig.FEATURE_SURVEY || AeroKeyConfig.FEATURE_BUG_REPORT

    // --- Sağ üstteki sabit düğme -----------------------------------------

    private fun buildButton(activity: Activity, root: FrameLayout) {
        val size = activity.dp(48)
        val margin = activity.dp(10)

        val view = MenuButtonView(activity).apply {
            isClickable = true
            isFocusable = true
            addPressFeedback()
            setOnClickListener { if (hidden) reveal() else toggle() }
        }

        root.addView(
            view,
            FrameLayout.LayoutParams(size, size, Gravity.TOP or Gravity.END).apply {
                topMargin = margin
                marginEnd = margin
                rightMargin = margin  // marginEnd'i desteklemeyen eski sürümler için
            }
        )
        button = view
    }

    /**
     * Düğmeyi içeriden çizen görünüm: yumuşak dış parıltı, gradyanlı halka,
     * ortada marka işareti. Programatik çizim, kaynak (drawable) dosyası
     * eklemeden zengin görünüm sağlıyor — Ren'Py'nin res/ klasörü her
     * derlemede yeniden üretildiği için oraya dosya koymak kırılgan olurdu.
     */
    private class MenuButtonView(activity: Activity) : View(activity) {
        private val glowPaint = Paint(Paint.ANTI_ALIAS_FLAG)
        private val fillPaint = Paint(Paint.ANTI_ALIAS_FLAG)
        private val ringPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            style = Paint.Style.STROKE
            strokeWidth = activity.dp(2).toFloat()
        }
        private val markPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = Color.WHITE
            textAlign = Paint.Align.CENTER
            textSize = activity.dp(17).toFloat()
            typeface = android.graphics.Typeface.DEFAULT_BOLD
        }
        private var pulse = 0f

        private val animator = ValueAnimator.ofFloat(0f, 1f, 0f).apply {
            duration = 2600L
            repeatCount = ValueAnimator.INFINITE
            addUpdateListener {
                pulse = it.animatedValue as Float
                invalidate()
            }
        }

        override fun onAttachedToWindow() {
            super.onAttachedToWindow()
            if (!animator.isStarted) animator.start()
        }

        override fun onDetachedFromWindow() {
            animator.cancel()
            super.onDetachedFromWindow()
        }

        override fun onDraw(canvas: Canvas) {
            val cx = width / 2f
            val cy = height / 2f
            val radius = minOf(width, height) / 2f

            val glowRadius = radius * (0.92f + pulse * 0.20f)
            glowPaint.shader = RadialGradient(
                cx, cy, glowRadius,
                intArrayOf(
                    Color.argb((70 + pulse * 55).toInt(), 139, 124, 246),
                    Color.TRANSPARENT
                ),
                floatArrayOf(0.55f, 1f),
                Shader.TileMode.CLAMP
            )
            canvas.drawCircle(cx, cy, glowRadius, glowPaint)

            val body = radius * 0.78f
            fillPaint.shader = LinearGradient(
                cx - body, cy - body, cx + body, cy + body,
                Color.parseColor("#F21A2140"), Color.parseColor("#F2241B44"),
                Shader.TileMode.CLAMP
            )
            canvas.drawCircle(cx, cy, body, fillPaint)

            ringPaint.shader = LinearGradient(
                cx - body, cy - body, cx + body, cy + body,
                intArrayOf(Palette.accentAlt, Palette.accent, Palette.accentWarm),
                null, Shader.TileMode.CLAMP
            )
            canvas.drawCircle(cx, cy, body, ringPaint)

            canvas.drawText("▲", cx, cy + markPaint.textSize * 0.36f, markPaint)
        }
    }

    // --- Gizleme ---------------------------------------------------------

    /**
     * "Gizle", düğmeyi yok etmez: küçültüp yarı saydam yapar. Böylece oyunu
     * neredeyse hiç örtmez ama oyuncu menüyü geri getirmenin yolunu da
     * kaybetmez — dokunması yeterlidir.
     */
    private fun applyHiddenState(animated: Boolean) {
        val view = button ?: return
        val targetAlpha = if (hidden) 0.20f else 1f
        val targetScale = if (hidden) 0.6f else 1f
        if (animated) {
            view.animate().alpha(targetAlpha).scaleX(targetScale).scaleY(targetScale)
                .setDuration(240).start()
        } else {
            view.alpha = targetAlpha
            view.scaleX = targetScale
            view.scaleY = targetScale
        }
    }

    private fun hide() {
        collapse()
        hidden = true
        hostRef?.get()?.let { AeroKeyPrefs.setOverlayHidden(it, true) }
        applyHiddenState(animated = true)
    }

    private fun reveal() {
        hidden = false
        hostRef?.get()?.let { AeroKeyPrefs.setOverlayHidden(it, false) }
        applyHiddenState(animated = true)
    }

    // --- Panel -----------------------------------------------------------

    private fun toggle() {
        if (expanded) collapse() else expand()
    }

    private fun expand() {
        if (expanded) return
        val activity = hostRef?.get() ?: return
        val root = rootRef?.get() ?: return
        val anchor = button ?: return

        val view = buildPanel(activity)
        val margin = activity.dp(10)

        // Düğme sağ üstte sabit olduğu için panel de sağ üstten, düğmenin
        // hemen altından açılır.
        root.addView(
            view,
            FrameLayout.LayoutParams(
                panelWidth(activity, root),
                ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.TOP or Gravity.END
            ).apply {
                topMargin = margin + anchor.height + activity.dp(8)
                marginEnd = margin
                rightMargin = margin
            }
        )
        panel = view
        expanded = true

        view.pivotX = view.resources.displayMetrics.widthPixels.toFloat()
        view.pivotY = 0f
        view.scaleX = 0.88f
        view.scaleY = 0.88f
        view.alpha = 0f
        view.animate()
            .scaleX(1f).scaleY(1f).alpha(1f)
            .setDuration(260)
            .setInterpolator(OvershootInterpolator(0.9f))
            .start()

        startTicker()
    }

    private fun panelWidth(activity: Activity, root: FrameLayout): Int {
        // Yatay ekranda tüm genişliği kaplamak oyunu gereksiz yere örterdi.
        val available = if (root.width > 0) root.width
        else activity.resources.displayMetrics.widthPixels
        return (available * 0.60f).toInt()
            .coerceIn(activity.dp(280), activity.dp(560))
    }

    private fun collapse() {
        if (!expanded) return
        expanded = false
        stopTicker()

        val view = panel ?: return
        panel = null
        playtimeLabel = null
        // Avatar GIF'i panel kapanınca kare üretmeye devam etmesin.
        headerAvatar?.stopAnimation()
        headerAvatar = null
        view.animate()
            .scaleX(0.9f).scaleY(0.9f).alpha(0f)
            .setDuration(170)
            .withEndAction { (view.parent as? ViewGroup)?.removeView(view) }
            .start()
    }

    // --- Panel içeriği ---------------------------------------------------

    /** Menü başlığındaki avatar; panel kapanınca GIF'i durdurulur. */
    private var headerAvatar: AvatarView? = null

    private fun buildPanel(activity: Activity): View {
        val card = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            background = panelBackground(activity)
            setPadding(activity.dp(16), activity.dp(14), activity.dp(16), activity.dp(14))
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
                elevation = activity.dp(16).toFloat()
            }
        }
        card.addView(buildPanelHeader(activity))
        card.addSpace(activity.dp(10))
        card.addView(buildLicenseRow(activity))
        card.addSpace(activity.dp(12))
        card.addView(buildActionRow(activity))
        card.addSpace(activity.dp(12))
        card.addView(buildPanelFooter(activity))
        return card
    }

    private fun panelBackground(activity: Activity): GradientDrawable =
        GradientDrawable(
            GradientDrawable.Orientation.TL_BR,
            intArrayOf(Color.parseColor("#F51A2038"), Color.parseColor("#F5150E2B"))
        ).apply {
            cornerRadius = activity.dp(20).toFloat()
            setStroke(activity.dp(1), Color.parseColor("#4D8B7CF6"))
        }

    /**
     * Başlık satırı: hangi oyunda olduğumuzu ve oyuncunun adını yazar,
     * yanında canlı oynama süresini gösterir.
     */
    private fun buildPanelHeader(activity: Activity): View {
        val header = activity.row()

        // Oyuncunun avatarı. Seçilmemişse adın ilk harfinden rozet çizilir,
        // yani burada asla boş bir kutu görünmez.
        val avatar = AvatarView(activity).apply {
            setFallbackLetter(AeroKeyPrefs.username(activity))
            highlighted = true
            val asset = AeroKeyPrefs.avatar(activity)
            if (asset.isNotBlank()) {
                setAvatarDrawable(activity.loadAssetDrawable(asset, animated = true), true)
            }
        }
        headerAvatar = avatar
        header.addView(avatar, LinearLayout.LayoutParams(activity.dp(38), activity.dp(38)))

        val titles = activity.column().apply {
            setPadding(activity.dp(10), 0, activity.dp(10), 0)
        }
        titles.addView(TextView(activity).apply {
            text = AeroKeyConfig.GAME_TITLE
            setTextColor(Palette.textPrimary)
            textSize = 16f
            maxLines = 1
            ellipsize = android.text.TextUtils.TruncateAt.END
            typeface = android.graphics.Typeface.DEFAULT_BOLD
        })
        titles.addView(TextView(activity).apply {
            text = AeroKeyPrefs.username(activity)
            setTextColor(Palette.textMuted)
            textSize = 10.5f
            maxLines = 1
            ellipsize = android.text.TextUtils.TruncateAt.END
        })
        header.addView(
            titles,
            LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        )

        playtimeLabel = TextView(activity).apply {
            setTextColor(Palette.accentAlt)
            textSize = 12.5f
            typeface = android.graphics.Typeface.DEFAULT_BOLD
            background = GradientDrawable().apply {
                cornerRadius = activity.dp(11).toFloat()
                setColor(Color.parseColor("#1F22D3EE"))
                setStroke(activity.dp(1), Color.parseColor("#3322D3EE"))
            }
            setPadding(activity.dp(11), activity.dp(6), activity.dp(11), activity.dp(6))
        }
        header.addView(playtimeLabel)
        updatePlaytime()

        return header
    }

    /**
     * Lisans durumu satırı: oyuncunun hangi hakla oynadığını ve ne zamana
     * kadar geçerli olduğunu gösterir.
     *
     * Sıralama önemli: ücretsiz gün, anahtarın/VIP'in ÖNÜNE geçer, çünkü o
     * gün lisans hiç denetlenmiyor.
     */
    private fun buildLicenseRow(activity: Activity): View {
        val (icon, label, detail, tint) = when {
            AeroKeySession.isFreeAccess() -> Quad(
                "🎁", "Ücretsiz gün", "Bugün anahtar gerekmiyor", Palette.gold
            )
            AeroKeyPrefs.isVip(activity) -> Quad(
                "⭐", "VIP üyelik", expiryText(activity), Palette.gold
            )
            AeroKeyPrefs.licenseKey(activity).isNotBlank() -> Quad(
                "🔑", "Anahtar etkin", expiryText(activity), Palette.success
            )
            else -> Quad(
                "🔓", "Lisans yok", "Doğrulanmış bir erişim bulunamadı", Palette.textMuted
            )
        }

        val rowView = activity.row().apply {
            background = GradientDrawable().apply {
                cornerRadius = activity.dp(13).toFloat()
                setColor(Color.parseColor("#14FFFFFF"))
                setStroke(activity.dp(1), Color.parseColor("#1FFFFFFF"))
            }
            setPadding(activity.dp(11), activity.dp(9), activity.dp(11), activity.dp(9))
        }

        rowView.addView(TextView(activity).apply {
            text = icon
            textSize = 15f
        })

        val texts = activity.column().apply {
            setPadding(activity.dp(9), 0, 0, 0)
        }
        texts.addView(TextView(activity).apply {
            text = label
            setTextColor(tint)
            textSize = 12.5f
            typeface = android.graphics.Typeface.DEFAULT_BOLD
        })
        texts.addView(TextView(activity).apply {
            text = detail
            setTextColor(Palette.textMuted)
            textSize = 10.5f
            maxLines = 1
            ellipsize = android.text.TextUtils.TruncateAt.END
        })
        rowView.addView(
            texts,
            LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        )

        return rowView
    }

    /** Dört alanlı küçük bir taşıyıcı (Kotlin'de hazır Quadruple yok). */
    private data class Quad(
        val icon: String,
        val label: String,
        val detail: String,
        val tint: Int
    )

    private fun expiryText(activity: Activity): String {
        val text = AeroKeyPrefs.expiresText(activity)
        return if (text.isBlank()) "Süre bilgisi yok" else "Bitiş: $text"
    }

    private fun buildActionRow(activity: Activity): View {
        val entries = mutableListOf<Triple<String, String, () -> Unit>>()
        if (AeroKeyConfig.FEATURE_LEADERBOARD) {
            entries.add(Triple("🏆", "Liderlik", { withHost(AeroKeyPanels::showLeaderboard) }))
        }
        if (AeroKeyConfig.FEATURE_PROFILE) {
            entries.add(Triple("👤", "Profilim", { withHost(AeroKeyPanels::showProfile) }))
        }
        if (AeroKeyConfig.FEATURE_SURVEY) {
            entries.add(Triple("📊", "Anket", { withHost(AeroKeyPanels::showSurvey) }))
        }
        if (AeroKeyConfig.FEATURE_BUG_REPORT) {
            entries.add(Triple("🐞", "Hata Bildir", { withHost(AeroKeyPanels::showBugReport) }))
        }

        val row = activity.row().apply { gravity = Gravity.CENTER }
        for ((index, entry) in entries.withIndex()) {
            row.addView(
                buildActionTile(activity, entry.first, entry.second, entry.third),
                LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
                    if (index > 0) leftMargin = activity.dp(8)
                }
            )
        }
        return row
    }

    private fun buildActionTile(
        activity: Activity,
        icon: String,
        label: String,
        action: () -> Unit
    ): View {
        val tile = activity.column().apply {
            gravity = Gravity.CENTER
            background = GradientDrawable().apply {
                cornerRadius = activity.dp(14).toFloat()
                setColor(Color.parseColor("#14FFFFFF"))
                setStroke(activity.dp(1), Color.parseColor("#1FFFFFFF"))
            }
            setPadding(activity.dp(6), activity.dp(11), activity.dp(6), activity.dp(10))
            isClickable = true
            isFocusable = true
            addPressFeedback()
            setOnClickListener {
                collapse()
                action()
            }
        }
        tile.addView(TextView(activity).apply {
            text = icon
            textSize = 19f
            gravity = Gravity.CENTER
        })
        tile.addView(TextView(activity).apply {
            text = label
            setTextColor(Palette.textSecondary)
            textSize = 11.5f
            gravity = Gravity.CENTER
            maxLines = 1
            ellipsize = android.text.TextUtils.TruncateAt.END
        })
        return tile
    }

    private fun buildPanelFooter(activity: Activity): View {
        val footer = activity.row()
        footer.addView(activity.ghostButton("🙈  Menüyü gizle").apply {
            setOnClickListener { hide() }
        })
        footer.addView(View(activity), LinearLayout.LayoutParams(0, 1, 1f))
        footer.addView(activity.ghostButton("Kapat").apply {
            setOnClickListener { collapse() }
        })
        return footer
    }

    private fun withHost(action: (Activity) -> Unit) {
        hostRef?.get()?.let { host ->
            if (!host.isFinishing) action(host)
        }
    }

    // --- Canlı oynama süresi ---------------------------------------------

    private fun updatePlaytime() {
        val activity = hostRef?.get() ?: return
        playtimeLabel?.text = "⏱ " + formatPlaytime(AeroKeySession.currentGameSeconds(activity))
    }

    private fun startTicker() {
        stopTicker()
        val view = playtimeLabel ?: return
        val runnable = object : Runnable {
            override fun run() {
                if (!expanded) return
                updatePlaytime()
                view.postDelayed(this, 1000L)
            }
        }
        ticker = runnable
        view.postDelayed(runnable, 1000L)
    }

    private fun stopTicker() {
        ticker?.let { playtimeLabel?.removeCallbacks(it) }
        ticker = null
    }
}

/** Yatay ekranda daha dar bir diyalog genişliği hesaplar. */
internal fun Activity.dialogWidth(): Int {
    val metrics = resources.displayMetrics
    val landscape = metrics.widthPixels > metrics.heightPixels
    val fraction = if (landscape) 0.62f else 0.94f
    return (metrics.widthPixels * fraction).toInt().coerceAtMost(dp(620))
}

/** Yatay ekranda kaydırılabilir listelerin taşmaması için azami yükseklik. */
internal fun Activity.dialogListHeight(): Int {
    val metrics = resources.displayMetrics
    return (metrics.heightPixels * 0.46f).toInt().coerceAtLeast(dp(160))
}
