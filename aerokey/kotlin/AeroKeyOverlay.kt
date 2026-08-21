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
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.ViewConfiguration
import android.view.ViewGroup
import android.view.animation.DecelerateInterpolator
import android.view.animation.OvershootInterpolator
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import java.lang.ref.WeakReference
import kotlin.math.hypot

/**
 * Oyunun ÜZERİNDE duran yüzen AeroKey menüsü.
 *
 * Giriş ekranındaki ek düğmeler (liderlik, profil, anket, hata bildirimi)
 * buraya taşındı: oyuncu bunlara oyun sırasında ihtiyaç duyar, giriş
 * yaparken değil.
 *
 * NASIL ÇALIŞIYOR
 * ---------------
 * Sistem seviyesinde bir pencere (TYPE_APPLICATION_OVERLAY) KULLANMIYORUZ;
 * o, kullanıcıdan "diğer uygulamaların üzerinde göster" izni istemeyi
 * gerektirirdi. Bunun yerine görünümü doğrudan oyunun kendi Activity'sinin
 * içerik köküne (android.R.id.content) ekliyoruz. Böylece hiçbir izin
 * gerekmiyor ve menü yalnızca oyun ekranındayken görünüyor.
 *
 * Kök katman TIKLANABİLİR DEĞİL: baloncuğun/panelin dışına yapılan
 * dokunuşlar tüketilmeyip altta duran oyuna geçer, yani menü oyunu
 * engellemez.
 *
 * Oyun yatay çalıştığı için panel de yatay düzende tasarlandı: geniş ve
 * alçak, eylemler tek sıra halinde.
 */
internal object AeroKeyOverlay {

    private var hostRef: WeakReference<Activity>? = null
    private var rootRef: WeakReference<FrameLayout>? = null

    private var bubble: View? = null
    private var panel: View? = null
    private var scrim: View? = null
    private var playtimeLabel: TextView? = null

    private var expanded = false
    private var hidden = false
    private var ticker: Runnable? = null

    // --- Yaşam döngüsü ---------------------------------------------------

    /**
     * Menüyü verilen Activity'ye bağlar. Aynı Activity için tekrar
     * çağrılması zararsızdır; farklı bir Activity gelirse (örn. ekran
     * döndürme sonrası yeniden oluşturma) eski bağlantı bırakılıp yenisi
     * kurulur.
     */
    fun attach(activity: Activity) {
        if (!AeroKeyConfig.ENABLED) return
        if (!hasAnyFeature()) return
        if (activity is AeroKeyGateActivity) return

        if (hostRef?.get() === activity && rootRef?.get()?.isAttachedToWindow == true) {
            return
        }
        detach()

        val content = activity.findViewById<FrameLayout>(android.R.id.content) ?: return

        val root = object : FrameLayout(activity) {
            // Kök katman hiçbir dokunuşu kendiliğinden tüketmez; yalnızca
            // çocukları (baloncuk, panel, perde) tüketir. Bu sayede oyun
            // dokunuşları normal şekilde alt katmana ulaşır.
            override fun onTouchEvent(event: MotionEvent): Boolean = false
        }
        root.isClickable = false
        root.fitsSystemWindows = false

        content.addView(
            root,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT
            )
        )

        hostRef = WeakReference(activity)
        rootRef = WeakReference(root)
        expanded = false
        hidden = AeroKeyPrefs.overlayHidden(activity)

        buildScrim(activity, root)
        buildBubble(activity, root)
        applyHiddenState(animated = false)

        // Konumu ancak düzen ölçüldükten sonra uygulayabiliriz (kök
        // genişlik/yüksekliği lazım). Bir OnLayoutChangeListener, hem ilk
        // ölçümü hem de sonraki boyut değişimlerini (ekran döndürme,
        // çok pencereli mod) tek yerden karşılar.
        root.addOnLayoutChangeListener { _, left, top, right, bottom,
                                         oldLeft, oldTop, oldRight, oldBottom ->
            val sizeChanged = (right - left) != (oldRight - oldLeft) ||
                (bottom - top) != (oldBottom - oldTop)
            if (sizeChanged) {
                if (expanded) collapse()
                restorePosition(activity, root)
            }
        }
    }

    /** Bağlı olduğumuz Activity yok edildiyse referansları bırakırız. */
    fun onActivityDestroyed(activity: Activity) {
        if (hostRef?.get() === activity) detach()
    }

    fun detach() {
        stopTicker()
        val root = rootRef?.get()
        (root?.parent as? ViewGroup)?.removeView(root)
        hostRef = null
        rootRef = null
        bubble = null
        panel = null
        scrim = null
        playtimeLabel = null
        expanded = false
    }

    private fun hasAnyFeature(): Boolean =
        AeroKeyConfig.FEATURE_LEADERBOARD || AeroKeyConfig.FEATURE_PROFILE ||
            AeroKeyConfig.FEATURE_SURVEY || AeroKeyConfig.FEATURE_BUG_REPORT

    // --- Baloncuk --------------------------------------------------------

    private fun buildBubble(activity: Activity, root: FrameLayout) {
        val size = activity.dp(54)

        val view = BubbleView(activity)
        root.addView(view, FrameLayout.LayoutParams(size, size))
        bubble = view

        attachDragBehavior(activity, root, view)
    }

    /**
     * Baloncuğu içeriden çizen görünüm: yumuşak bir dış parıltı, gradyanlı
     * bir halka ve ortada marka işareti. Programatik çizim, kaynak dosyası
     * (drawable) eklemeden zengin bir görünüm elde etmemizi sağlıyor —
     * Ren'Py'nin res/ klasörü her derlemede yeniden üretildiği için oraya
     * dosya koymak kırılgan olurdu.
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

            // Nefes alan dış parıltı
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

            // Gövde
            val body = radius * 0.76f
            fillPaint.shader = LinearGradient(
                cx - body, cy - body, cx + body, cy + body,
                Color.parseColor("#F21A2140"), Color.parseColor("#F2241B44"),
                Shader.TileMode.CLAMP
            )
            canvas.drawCircle(cx, cy, body, fillPaint)

            // Gradyanlı halka
            ringPaint.shader = LinearGradient(
                cx - body, cy - body, cx + body, cy + body,
                intArrayOf(Palette.accentAlt, Palette.accent, Palette.accentWarm),
                null, Shader.TileMode.CLAMP
            )
            canvas.drawCircle(cx, cy, body, ringPaint)

            canvas.drawText("▲", cx, cy + markPaint.textSize * 0.36f, markPaint)
        }
    }

    // --- Sürükleme + kenara yapışma --------------------------------------

    private fun attachDragBehavior(activity: Activity, root: FrameLayout, view: View) {
        val slop = ViewConfiguration.get(activity).scaledTouchSlop
        var downRawX = 0f
        var downRawY = 0f
        var startX = 0f
        var startY = 0f
        var dragging = false

        view.setOnTouchListener { v, event ->
            when (event.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    downRawX = event.rawX
                    downRawY = event.rawY
                    startX = v.x
                    startY = v.y
                    dragging = false
                    v.animate().scaleX(1.12f).scaleY(1.12f).setDuration(120).start()
                    true
                }

                MotionEvent.ACTION_MOVE -> {
                    val dx = event.rawX - downRawX
                    val dy = event.rawY - downRawY
                    if (!dragging && hypot(dx, dy) > slop) {
                        dragging = true
                        collapse()  // sürüklerken panel açık kalmasın
                    }
                    if (dragging) {
                        v.x = clamp(startX + dx, 0f, (root.width - v.width).toFloat())
                        v.y = clamp(startY + dy, 0f, (root.height - v.height).toFloat())
                    }
                    true
                }

                MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                    v.animate().scaleX(1f).scaleY(1f).setDuration(150).start()
                    if (dragging) {
                        snapToEdge(activity, root, v)
                    } else if (event.actionMasked == MotionEvent.ACTION_UP) {
                        if (hidden) reveal() else toggle()
                    }
                    true
                }

                else -> false
            }
        }
    }

    /** Baloncuğu en yakın yan kenara yaslar ve konumu kalıcı olarak saklar. */
    private fun snapToEdge(activity: Activity, root: FrameLayout, view: View) {
        val margin = activity.dp(10).toFloat()
        val centerX = view.x + view.width / 2f
        val targetX =
            if (centerX < root.width / 2f) margin
            else root.width - view.width - margin
        val targetY = clamp(view.y, margin, root.height - view.height - margin)

        view.animate()
            .x(targetX).y(targetY)
            .setDuration(260)
            .setInterpolator(DecelerateInterpolator())
            .withEndAction { savePosition(activity, root, view) }
            .start()
    }

    private fun savePosition(activity: Activity, root: FrameLayout, view: View) {
        // Oranla saklıyoruz: ekran döndüğünde ya da farklı çözünürlükte
        // baloncuk göreli olarak aynı yerde kalsın.
        val maxX = (root.width - view.width).coerceAtLeast(1)
        val maxY = (root.height - view.height).coerceAtLeast(1)
        AeroKeyPrefs.saveOverlayPosition(activity, view.x / maxX, view.y / maxY)
    }

    private fun restorePosition(activity: Activity, root: FrameLayout) {
        val view = bubble ?: return
        if (root.width == 0 || root.height == 0) return
        val maxX = (root.width - view.width).coerceAtLeast(1)
        val maxY = (root.height - view.height).coerceAtLeast(1)
        val (fx, fy) = AeroKeyPrefs.overlayPosition(activity)
        view.x = clamp(fx * maxX, 0f, maxX.toFloat())
        view.y = clamp(fy * maxY, 0f, maxY.toFloat())
    }

    private fun clamp(value: Float, min: Float, max: Float): Float =
        if (max < min) min else value.coerceIn(min, max)

    // --- Gizleme ---------------------------------------------------------

    /**
     * "Gizle", baloncuğu tamamen yok etmez: küçültüp yarı saydam hale
     * getirir. Böylece oyunu neredeyse hiç kapatmaz ama oyuncu menüyü
     * geri getirmenin yolunu da kaybetmez — dokunması yeterlidir.
     */
    private fun applyHiddenState(animated: Boolean) {
        val view = bubble ?: return
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

    // --- Panel -----------------------------------------------------------

    private fun toggle() {
        if (expanded) collapse() else expand()
    }

    private fun buildScrim(activity: Activity, root: FrameLayout) {
        val view = View(activity).apply {
            setBackgroundColor(Color.parseColor("#66050813"))
            alpha = 0f
            visibility = View.GONE
            setOnClickListener { collapse() }
        }
        root.addView(
            view,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT
            )
        )
        scrim = view
    }

    private fun expand() {
        val activity = hostRef?.get() ?: return
        val root = rootRef?.get() ?: return
        val anchor = bubble ?: return
        if (expanded) return

        val view = buildPanel(activity)
        panel = view

        val params = FrameLayout.LayoutParams(
            panelWidth(activity, root), ViewGroup.LayoutParams.WRAP_CONTENT
        )
        root.addView(view, params)

        scrim?.apply {
            visibility = View.VISIBLE
            animate().alpha(1f).setDuration(180).start()
        }

        view.visibility = View.INVISIBLE
        view.post { positionPanel(activity, root, anchor, view) }

        expanded = true
        startTicker()
    }

    private fun panelWidth(activity: Activity, root: FrameLayout): Int {
        // Yatay ekranda tüm genişliği kaplamak oyunu gereksiz yere
        // örterdi; ekranın bir kısmıyla sınırlıyoruz.
        val max = activity.dp(600)
        val preferred = (root.width * 0.68f).toInt()
        return preferred.coerceIn(activity.dp(300), max)
    }

    /** Paneli baloncuğun yanına, ekran dışına taşmayacak şekilde yerleştirir. */
    private fun positionPanel(
        activity: Activity,
        root: FrameLayout,
        anchor: View,
        view: View
    ) {
        val gap = activity.dp(12)
        val margin = activity.dp(10)

        val anchorCenterX = anchor.x + anchor.width / 2f
        val openLeft = anchorCenterX > root.width / 2f

        var x = if (openLeft) anchor.x - gap - view.width else anchor.x + anchor.width + gap
        x = clamp(x, margin.toFloat(), (root.width - view.width - margin).toFloat())

        var y = anchor.y + anchor.height / 2f - view.height / 2f
        y = clamp(y, margin.toFloat(), (root.height - view.height - margin).toFloat())

        view.x = x
        view.y = y
        view.visibility = View.VISIBLE

        // Baloncuktan doğuyormuş gibi açılsın.
        view.pivotX = if (openLeft) view.width.toFloat() else 0f
        view.pivotY = view.height / 2f
        view.scaleX = 0.86f
        view.scaleY = 0.86f
        view.alpha = 0f
        view.animate()
            .scaleX(1f).scaleY(1f).alpha(1f)
            .setDuration(280)
            .setInterpolator(OvershootInterpolator(0.9f))
            .start()
    }

    private fun collapse() {
        if (!expanded) return
        expanded = false
        stopTicker()

        scrim?.animate()?.alpha(0f)?.setDuration(160)?.withEndAction {
            scrim?.visibility = View.GONE
        }?.start()

        val view = panel ?: return
        panel = null
        playtimeLabel = null
        view.animate()
            .scaleX(0.88f).scaleY(0.88f).alpha(0f)
            .setDuration(170)
            .withEndAction { (view.parent as? ViewGroup)?.removeView(view) }
            .start()
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

    /** Panelin arka planı: koyu cam + ince gradyan kenarlık hissi. */
    private fun panelBackground(activity: Activity): GradientDrawable =
        GradientDrawable(
            GradientDrawable.Orientation.TL_BR,
            intArrayOf(Color.parseColor("#F51A2038"), Color.parseColor("#F5150E2B"))
        ).apply {
            cornerRadius = activity.dp(20).toFloat()
            setStroke(activity.dp(1), Color.parseColor("#4D8B7CF6"))
        }

    /**
     * Başlık satırı: hangi oyunda olduğumuzu yazar (kullanıcının istediği
     * gibi) ve yanında canlı oynama süresini gösterir.
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
            text = "AeroKey menüsü"
            setTextColor(Palette.textMuted)
            textSize = 10.5f
            letterSpacing = 0.09f
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
            val tile = buildActionTile(activity, entry.first, entry.second, entry.third)
            row.addView(
                tile,
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

        footer.addView(
            View(activity),
            LinearLayout.LayoutParams(0, 1, 1f)
        )

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

    /** Panel açıkken süreyi saniye başına tazeler. */
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
