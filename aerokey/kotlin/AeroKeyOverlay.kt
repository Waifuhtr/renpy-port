package com.riaslink.aerokey

import android.animation.ValueAnimator
import android.app.Activity
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.LinearGradient
import android.graphics.Paint
import android.graphics.PixelFormat
import android.graphics.RadialGradient
import android.graphics.Shader
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.ViewConfiguration
import android.view.ViewGroup
import android.view.WindowManager
import android.view.animation.DecelerateInterpolator
import android.view.animation.OvershootInterpolator
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import java.lang.ref.WeakReference
import kotlin.math.hypot
import kotlin.math.roundToInt

/**
 * Oyunun ÜZERİNDE duran yüzen AeroKey menüsü.
 *
 * NEDEN KENDİ PENCERESİ VAR?
 * --------------------------
 * İlk sürümde menü, oyunun Activity'sinin görünüm ağacına (android.R.id.content)
 * ekleniyordu. Baloncuk duruyorken görünüyordu ama SÜRÜKLENİNCE kayboluyor,
 * yalnızca yeniden dokunulunca geri geliyordu: Ren'Py oyunu bir SurfaceView
 * ile çiziliyor ve SurfaceView, pencere yüzeyinde saydam bir "delik" açıyor.
 * O deliğin üstünde duran görünümler yalnızca pencere yüzeyinin kirli
 * (yeniden çizilen) bölgelerinde doğru şekilde birleştiriliyor; görünümü
 * sürükleyince ortaya çıkan yeni bölge güvenilir biçimde tazelenmiyordu.
 *
 * Çözüm, sorunu tamamen ortadan kaldıran katmanı değiştirmek: menü artık
 * WindowManager üzerinden AYRI BİR PENCERE. Pencereyi taşımak bir
 * birleştirici (compositor) işlemidir, oyunun yüzeyinden bağımsızdır; bu
 * yüzden sürüklerken kaybolma diye bir durum kalmaz.
 *
 * İZİN GEREKMİYOR: TYPE_APPLICATION_PANEL, kendi Activity'mizin penceresine
 * bağlı bir ALT PENCEREDİR (token ile). "Diğer uygulamaların üzerinde
 * göster" (SYSTEM_ALERT_WINDOW) izni yalnızca TYPE_APPLICATION_OVERLAY için
 * gerekir; onu kullanmıyoruz.
 *
 * DOKUNUŞ GEÇİRGENLİĞİ: Pencereler tam olarak içerikleri kadar büyük ve
 * FLAG_NOT_TOUCH_MODAL taşıyor, yani pencerelerin DIŞINDAKİ dokunuşlar
 * doğrudan oyuna gidiyor — menü oyun kontrollerini engellemiyor.
 */
internal object AeroKeyOverlay {

    private var hostRef: WeakReference<Activity>? = null
    private var windowManager: WindowManager? = null

    private var bubbleView: View? = null
    private var bubbleParams: WindowManager.LayoutParams? = null

    private var panelView: View? = null
    private var playtimeLabel: TextView? = null

    private var expanded = false
    private var hidden = false
    private var ticker: Runnable? = null

    private var bubbleSizePx = 0

    // --- Yaşam döngüsü ---------------------------------------------------

    fun attach(activity: Activity) {
        if (!AeroKeyConfig.ENABLED) return
        if (!hasAnyFeature()) return
        if (activity is AeroKeyGateActivity) return
        if (hostRef?.get() === activity && bubbleView != null) return

        detach()

        val token = activity.window?.decorView?.windowToken ?: return
        val wm = activity.windowManager ?: return

        hostRef = WeakReference(activity)
        windowManager = wm
        hidden = AeroKeyPrefs.overlayHidden(activity)
        expanded = false
        bubbleSizePx = activity.dp(54)

        val view = BubbleView(activity)
        val params = WindowManager.LayoutParams(
            bubbleSizePx,
            bubbleSizePx,
            WindowManager.LayoutParams.TYPE_APPLICATION_PANEL,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL or
                WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
            PixelFormat.TRANSLUCENT
        ).apply {
            this.token = token
            gravity = Gravity.TOP or Gravity.START
        }

        val (px, py) = restoredPosition(activity)
        params.x = px
        params.y = py

        attachDragBehavior(activity, view, params)

        try {
            wm.addView(view, params)
        } catch (_: Exception) {
            // Activity penceresi bu arada gittiyse sessizce vazgeç.
            hostRef = null
            windowManager = null
            return
        }

        bubbleView = view
        bubbleParams = params
        applyHiddenState(animated = false)
    }

    fun onActivityDestroyed(activity: Activity) {
        if (hostRef?.get() === activity) detach()
    }

    fun detach() {
        stopTicker()
        collapseImmediate()
        bubbleView?.let { removeWindow(it) }
        bubbleView = null
        bubbleParams = null
        playtimeLabel = null
        windowManager = null
        hostRef = null
        expanded = false
    }

    private fun removeWindow(view: View) {
        try {
            windowManager?.removeViewImmediate(view)
        } catch (_: Exception) {
            // Zaten kaldırılmışsa önemsiz.
        }
    }

    private fun hasAnyFeature(): Boolean =
        AeroKeyConfig.FEATURE_LEADERBOARD || AeroKeyConfig.FEATURE_PROFILE ||
            AeroKeyConfig.FEATURE_SURVEY || AeroKeyConfig.FEATURE_BUG_REPORT

    // --- Ekran ölçüleri --------------------------------------------------

    private fun screenWidth(activity: Activity) = activity.resources.displayMetrics.widthPixels
    private fun screenHeight(activity: Activity) = activity.resources.displayMetrics.heightPixels

    private fun restoredPosition(activity: Activity): Pair<Int, Int> {
        val (fx, fy) = AeroKeyPrefs.overlayPosition(activity)
        val maxX = (screenWidth(activity) - bubbleSizePx).coerceAtLeast(0)
        val maxY = (screenHeight(activity) - bubbleSizePx).coerceAtLeast(0)
        return Pair((fx * maxX).roundToInt(), (fy * maxY).roundToInt())
    }

    private fun savePosition(activity: Activity, params: WindowManager.LayoutParams) {
        val maxX = (screenWidth(activity) - bubbleSizePx).coerceAtLeast(1)
        val maxY = (screenHeight(activity) - bubbleSizePx).coerceAtLeast(1)
        AeroKeyPrefs.saveOverlayPosition(
            activity, params.x.toFloat() / maxX, params.y.toFloat() / maxY
        )
    }

    // --- Baloncuk --------------------------------------------------------

    /**
     * Baloncuğu içeriden çizen görünüm: yumuşak dış parıltı, gradyanlı halka
     * ve ortada marka işareti. Programatik çizim, kaynak (drawable) dosyası
     * eklemeden zengin görünüm sağlıyor — Ren'Py'nin res/ klasörü her
     * derlemede yeniden üretildiği için oraya dosya koymak kırılgan olurdu.
     */
    private class BubbleView(activity: Activity) : View(activity) {
        private val glowPaint = Paint(Paint.ANTI_ALIAS_FLAG)
        private val fillPaint = Paint(Paint.ANTI_ALIAS_FLAG)
        private val ringPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            style = Paint.Style.STROKE
            strokeWidth = activity.dp(2).toFloat()
        }
        private val markPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = Color.WHITE
            textAlign = Paint.Align.CENTER
            textSize = activity.dp(19).toFloat()
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

            val body = radius * 0.76f
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

    // --- Sürükleme -------------------------------------------------------

    private fun attachDragBehavior(
        activity: Activity,
        view: View,
        params: WindowManager.LayoutParams
    ) {
        val slop = ViewConfiguration.get(activity).scaledTouchSlop
        var downRawX = 0f
        var downRawY = 0f
        var startX = 0
        var startY = 0
        var dragging = false

        view.setOnTouchListener { v, event ->
            when (event.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    downRawX = event.rawX
                    downRawY = event.rawY
                    startX = params.x
                    startY = params.y
                    dragging = false
                    v.animate().scaleX(1.12f).scaleY(1.12f).setDuration(120).start()
                    true
                }

                MotionEvent.ACTION_MOVE -> {
                    val dx = event.rawX - downRawX
                    val dy = event.rawY - downRawY
                    if (!dragging && hypot(dx, dy) > slop) {
                        dragging = true
                        collapse()
                    }
                    if (dragging) {
                        // Pencereyi taşıyoruz; görünümü değil. Bu, oyunun
                        // SurfaceView'ı üzerinde güvenilir biçimde çalışır.
                        params.x = (startX + dx).roundToInt()
                            .coerceIn(0, screenWidth(activity) - bubbleSizePx)
                        params.y = (startY + dy).roundToInt()
                            .coerceIn(0, screenHeight(activity) - bubbleSizePx)
                        updateBubbleLayout()
                    }
                    true
                }

                MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                    v.animate().scaleX(1f).scaleY(1f).setDuration(150).start()
                    if (dragging) {
                        snapToEdge(activity, params)
                    } else if (event.actionMasked == MotionEvent.ACTION_UP) {
                        if (hidden) reveal() else toggle()
                    }
                    true
                }

                else -> false
            }
        }
    }

    private fun updateBubbleLayout() {
        val view = bubbleView ?: return
        val params = bubbleParams ?: return
        try {
            windowManager?.updateViewLayout(view, params)
        } catch (_: Exception) {
            // Pencere bu arada kaldırıldıysa önemsiz.
        }
    }

    /** Baloncuğu en yakın yan kenara yaslar ve konumu kalıcı olarak saklar. */
    private fun snapToEdge(activity: Activity, params: WindowManager.LayoutParams) {
        val margin = activity.dp(8)
        val maxX = screenWidth(activity) - bubbleSizePx
        val maxY = screenHeight(activity) - bubbleSizePx
        val centerX = params.x + bubbleSizePx / 2

        val targetX = if (centerX < screenWidth(activity) / 2) margin else maxX - margin
        val targetY = params.y.coerceIn(margin, (maxY - margin).coerceAtLeast(margin))

        val fromX = params.x
        val fromY = params.y
        ValueAnimator.ofFloat(0f, 1f).apply {
            duration = 240
            interpolator = DecelerateInterpolator()
            addUpdateListener { anim ->
                val t = anim.animatedValue as Float
                params.x = (fromX + (targetX - fromX) * t).roundToInt()
                params.y = (fromY + (targetY - fromY) * t).roundToInt()
                updateBubbleLayout()
            }
            addListener(object : android.animation.AnimatorListenerAdapter() {
                override fun onAnimationEnd(animation: android.animation.Animator) {
                    savePosition(activity, params)
                }
            })
            start()
        }
    }

    // --- Gizleme ---------------------------------------------------------

    /**
     * "Gizle", baloncuğu yok etmez: küçültüp yarı saydam yapar. Böylece
     * oyunu neredeyse hiç örtmez ama oyuncu menüyü geri getirmenin yolunu
     * da kaybetmez — dokunması yeterlidir.
     */
    private fun applyHiddenState(animated: Boolean) {
        val view = bubbleView ?: return
        val targetAlpha = if (hidden) 0.22f else 1f
        val targetScale = if (hidden) 0.62f else 1f
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

    // --- Panel penceresi -------------------------------------------------

    private fun toggle() {
        if (expanded) collapse() else expand()
    }

    private fun expand() {
        if (expanded) return
        val activity = hostRef?.get() ?: return
        val wm = windowManager ?: return
        val bubbleLp = bubbleParams ?: return
        val token = activity.window?.decorView?.windowToken ?: return

        val content = buildPanel(activity)
        val width = panelWidth(activity)

        // Yüksekliği önceden bilmemiz gerekiyor (konumlandırma için), bu
        // yüzden paneli genişliğe göre ölçüyoruz.
        content.measure(
            View.MeasureSpec.makeMeasureSpec(width, View.MeasureSpec.EXACTLY),
            View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED)
        )
        val height = content.measuredHeight

        val gap = activity.dp(10)
        val margin = activity.dp(8)
        val bubbleCenterX = bubbleLp.x + bubbleSizePx / 2
        val openLeft = bubbleCenterX > screenWidth(activity) / 2

        var x = if (openLeft) bubbleLp.x - gap - width else bubbleLp.x + bubbleSizePx + gap
        x = x.coerceIn(margin, (screenWidth(activity) - width - margin).coerceAtLeast(margin))

        var y = bubbleLp.y + bubbleSizePx / 2 - height / 2
        y = y.coerceIn(margin, (screenHeight(activity) - height - margin).coerceAtLeast(margin))

        val params = WindowManager.LayoutParams(
            width,
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.TYPE_APPLICATION_PANEL,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL or
                WindowManager.LayoutParams.FLAG_WATCH_OUTSIDE_TOUCH or
                WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
            PixelFormat.TRANSLUCENT
        ).apply {
            this.token = token
            gravity = Gravity.TOP or Gravity.START
            this.x = x
            this.y = y
        }

        // Panelin DIŞINA yapılan dokunuş paneli kapatır. FLAG_NOT_TOUCH_MODAL
        // sayesinde aynı dokunuş oyuna da ulaşır; panel yolu tıkamaz.
        content.setOnTouchListener { _, event ->
            if (event.actionMasked == MotionEvent.ACTION_OUTSIDE) {
                collapse()
                true
            } else {
                false
            }
        }

        try {
            wm.addView(content, params)
        } catch (_: Exception) {
            return
        }

        panelView = content
        expanded = true

        content.pivotX = if (openLeft) width.toFloat() else 0f
        content.pivotY = height / 2f
        content.scaleX = 0.86f
        content.scaleY = 0.86f
        content.alpha = 0f
        content.animate()
            .scaleX(1f).scaleY(1f).alpha(1f)
            .setDuration(260)
            .setInterpolator(OvershootInterpolator(0.9f))
            .start()

        startTicker()
    }

    private fun panelWidth(activity: Activity): Int {
        // Yatay ekranda tüm genişliği kaplamak oyunu gereksiz yere örterdi.
        val max = activity.dp(560)
        val preferred = (screenWidth(activity) * 0.62f).toInt()
        return preferred.coerceIn(activity.dp(280), max)
    }

    private fun collapse() {
        if (!expanded) return
        expanded = false
        stopTicker()

        val view = panelView ?: return
        panelView = null
        playtimeLabel = null
        view.animate()
            .scaleX(0.88f).scaleY(0.88f).alpha(0f)
            .setDuration(170)
            .withEndAction { removeWindow(view) }
            .start()
    }

    /** Animasyon beklemeden kapatır (detach sırasında kullanılır). */
    private fun collapseImmediate() {
        expanded = false
        panelView?.let { removeWindow(it) }
        panelView = null
    }

    // --- Panel içeriği ---------------------------------------------------

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
        card.addSpace(activity.dp(14))
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
     * Başlık satırı: hangi oyunda olduğumuzu yazar ve canlı oynama süresini
     * gösterir.
     */
    private fun buildPanelHeader(activity: Activity): View {
        val header = activity.row()

        val marker = View(activity).apply {
            background = GradientDrawable(
                GradientDrawable.Orientation.TOP_BOTTOM,
                intArrayOf(Palette.accentAlt, Palette.accentWarm)
            ).apply { cornerRadius = activity.dp(2).toFloat() }
        }
        header.addView(marker, LinearLayout.LayoutParams(activity.dp(3), activity.dp(30)))

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

    /** Eylem kutucukları: yatay ekran için tek sıra, ikon üstte etiket altta. */
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
