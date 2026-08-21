package com.riaslink.aerokey

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.ImageDecoder
import android.graphics.LinearGradient
import android.graphics.Paint
import android.graphics.RadialGradient
import android.graphics.Shader
import android.graphics.SweepGradient
import android.graphics.drawable.AnimatedImageDrawable
import android.graphics.drawable.BitmapDrawable
import android.graphics.drawable.Drawable
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.RippleDrawable
import android.graphics.drawable.StateListDrawable
import android.content.res.ColorStateList
import android.os.Build
import android.util.TypedValue
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.view.animation.DecelerateInterpolator
import android.view.animation.LinearInterpolator
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import kotlin.math.cos
import kotlin.math.sin

/**
 * Giriş ekranının görsel dili. Hiçbir XML layout/drawable/tema kaynağı
 * eklemiyoruz — Ren'Py'nin Android şablonunda `res/values/strings.xml` gibi
 * dosyalar HER derlemede yeniden üretildiği için, kaynak klasörüne dosya
 * eklemek kırılgan olurdu. Bunun yerine tüm arayüz koddan çiziliyor.
 */
internal object Palette {
    val bgTop = Color.parseColor("#0B0F1F")
    val bgBottom = Color.parseColor("#12081E")

    val surface = Color.parseColor("#E6141A2E")
    val surfaceBorder = Color.parseColor("#3D8B7CF6")

    val accent = Color.parseColor("#8B7CF6")
    val accentAlt = Color.parseColor("#22D3EE")
    val accentWarm = Color.parseColor("#F472B6")

    val textPrimary = Color.parseColor("#F2F5FF")
    val textSecondary = Color.parseColor("#A6B0D0")
    val textMuted = Color.parseColor("#6F7A9B")

    val success = Color.parseColor("#34D399")
    val danger = Color.parseColor("#FB7185")
    val gold = Color.parseColor("#FBBF24")

    val inputBg = Color.parseColor("#B30D1226")
    val inputBorder = Color.parseColor("#33FFFFFF")
}

internal fun Context.dp(value: Number): Int = TypedValue.applyDimension(
    TypedValue.COMPLEX_UNIT_DIP, value.toFloat(), resources.displayMetrics
).toInt()

/**
 * Ekranın arkasında yavaşça dolaşan renkli ışık kürelerinden oluşan canlı
 * arka plan. Küreler yumuşak radyal gradyanlardan çizildiği için ayrı bir
 * bulanıklaştırma (blur) maliyeti yoktur; düşük donanımda da akıcıdır.
 */
internal class AuroraBackgroundView(context: Context) : View(context) {

    private val basePaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val orbPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private var phase = 0f

    private data class Orb(
        val color: Int,
        val radiusRatio: Float,
        val centerX: Float,
        val centerY: Float,
        val driftX: Float,
        val driftY: Float,
        val speed: Float
    )

    private val orbs = listOf(
        Orb(Palette.accent, 0.75f, 0.18f, 0.16f, 0.10f, 0.07f, 1.00f),
        Orb(Palette.accentAlt, 0.62f, 0.86f, 0.30f, 0.09f, 0.09f, 0.72f),
        Orb(Palette.accentWarm, 0.58f, 0.72f, 0.86f, 0.12f, 0.06f, 0.55f)
    )

    private val animator = ValueAnimator.ofFloat(0f, (Math.PI * 2).toFloat()).apply {
        duration = 22000L
        repeatCount = ValueAnimator.INFINITE
        interpolator = LinearInterpolator()
        addUpdateListener {
            phase = it.animatedValue as Float
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

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(w, h, oldw, oldh)
        if (w > 0 && h > 0) {
            basePaint.shader = LinearGradient(
                0f, 0f, w * 0.35f, h.toFloat(),
                Palette.bgTop, Palette.bgBottom, Shader.TileMode.CLAMP
            )
        }
    }

    override fun onDraw(canvas: Canvas) {
        val w = width.toFloat()
        val h = height.toFloat()
        if (w <= 0f || h <= 0f) return

        canvas.drawRect(0f, 0f, w, h, basePaint)

        val minSide = minOf(w, h)
        for (orb in orbs) {
            val angle = phase * orb.speed
            val cx = (orb.centerX + sin(angle) * orb.driftX) * w
            val cy = (orb.centerY + cos(angle * 0.83f) * orb.driftY) * h
            val radius = minSide * orb.radiusRatio

            orbPaint.shader = RadialGradient(
                cx, cy, radius,
                intArrayOf(
                    withAlpha(orb.color, 0.34f),
                    withAlpha(orb.color, 0.12f),
                    Color.TRANSPARENT
                ),
                floatArrayOf(0f, 0.45f, 1f),
                Shader.TileMode.CLAMP
            )
            canvas.drawCircle(cx, cy, radius, orbPaint)
        }
    }

    private fun withAlpha(color: Int, factor: Float): Int = Color.argb(
        (255 * factor).toInt().coerceIn(0, 255),
        Color.red(color), Color.green(color), Color.blue(color)
    )
}

/** Yükleniyor göstergesi: kendi etrafında dönen, uçları yumuşak bir yay. */
internal class SpinnerView(context: Context) : View(context) {

    private val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.ROUND
        strokeWidth = context.dp(2.5).toFloat()
        color = Palette.accentAlt
    }
    private var sweepStart = 0f

    private val animator = ValueAnimator.ofFloat(0f, 360f).apply {
        duration = 900L
        repeatCount = ValueAnimator.INFINITE
        interpolator = LinearInterpolator()
        addUpdateListener {
            sweepStart = it.animatedValue as Float
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
        val inset = paint.strokeWidth
        canvas.drawArc(
            inset, inset, width - inset, height - inset,
            sweepStart, 260f, false, paint
        )
    }
}

// --- Şekil / arka plan üreticileri --------------------------------------

internal fun roundedFill(color: Int, radiusPx: Int): GradientDrawable =
    GradientDrawable().apply {
        shape = GradientDrawable.RECTANGLE
        cornerRadius = radiusPx.toFloat()
        setColor(color)
    }

internal fun glassCard(context: Context): GradientDrawable =
    GradientDrawable().apply {
        shape = GradientDrawable.RECTANGLE
        cornerRadius = context.dp(24).toFloat()
        setColor(Palette.surface)
        setStroke(context.dp(1), Palette.surfaceBorder)
    }

internal fun gradientPill(context: Context, start: Int, end: Int): GradientDrawable =
    GradientDrawable(
        GradientDrawable.Orientation.LEFT_RIGHT, intArrayOf(start, end)
    ).apply {
        shape = GradientDrawable.RECTANGLE
        cornerRadius = context.dp(14).toFloat()
    }

/** Basılınca hafifçe küçülen, dokunma geri bildirimi veren düğme davranışı. */
internal fun View.addPressFeedback() {
    setOnTouchListener { view, event ->
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN ->
                view.animate().scaleX(0.97f).scaleY(0.97f).setDuration(90).start()
            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL ->
                view.animate().scaleX(1f).scaleY(1f).setDuration(140).start()
        }
        false // dokunuşu tüketmiyoruz; tıklama dinleyicisi normal çalışsın
    }
}

/** Görünümü aşağıdan yukarı süzülerek belirecek şekilde animasyonlar. */
internal fun View.enterWithFade(delayMs: Long) {
    alpha = 0f
    translationY = context.dp(18).toFloat()
    animate()
        .alpha(1f)
        .translationY(0f)
        .setStartDelay(delayMs)
        .setDuration(420)
        .setInterpolator(DecelerateInterpolator())
        .start()
}

// --- Hazır bileşen üreticileri ------------------------------------------

internal fun Context.primaryButton(text: String): Button =
    Button(this).apply {
        this.text = text
        isAllCaps = false
        setTextColor(Color.WHITE)
        textSize = 16f
        typeface = android.graphics.Typeface.DEFAULT_BOLD
        stateListAnimator = null
        background = rippleWrap(
            gradientPill(this@primaryButton, Palette.accent, Palette.accentWarm),
            this@primaryButton
        )
        setPadding(dp(20), dp(15), dp(20), dp(15))
        addPressFeedback()
    }

internal fun Context.secondaryButton(text: String): Button =
    Button(this).apply {
        this.text = text
        isAllCaps = false
        setTextColor(Palette.textPrimary)
        textSize = 15f
        stateListAnimator = null
        background = rippleWrap(
            GradientDrawable().apply {
                cornerRadius = dp(14).toFloat()
                setColor(Palette.inputBg)
                setStroke(dp(1), Palette.inputBorder)
            },
            this@secondaryButton
        )
        setPadding(dp(18), dp(13), dp(18), dp(13))
        addPressFeedback()
    }

internal fun Context.ghostButton(text: String): TextView =
    TextView(this).apply {
        this.text = text
        setTextColor(Palette.textSecondary)
        textSize = 13f
        gravity = android.view.Gravity.CENTER
        isClickable = true
        isFocusable = true
        background = rippleWrap(
            GradientDrawable().apply {
                cornerRadius = dp(12).toFloat()
                setColor(Color.parseColor("#14FFFFFF"))
            },
            this@ghostButton
        )
        setPadding(dp(12), dp(9), dp(12), dp(9))
        addPressFeedback()
    }

internal fun Context.styledInput(hint: String): EditText =
    EditText(this).apply {
        this.hint = hint
        setHintTextColor(Palette.textMuted)
        setTextColor(Palette.textPrimary)
        textSize = 16f
        maxLines = 1
        background = GradientDrawable().apply {
            cornerRadius = dp(14).toFloat()
            setColor(Palette.inputBg)
            setStroke(dp(1), Palette.inputBorder)
        }
        setPadding(dp(16), dp(14), dp(16), dp(14))
    }

internal fun Context.sectionLabel(text: String): TextView =
    TextView(this).apply {
        this.text = text
        setTextColor(Palette.textMuted)
        textSize = 11f
        letterSpacing = 0.14f
        typeface = android.graphics.Typeface.DEFAULT_BOLD
    }

internal fun Context.bodyText(text: String, size: Float = 14f): TextView =
    TextView(this).apply {
        this.text = text
        setTextColor(Palette.textSecondary)
        textSize = size
        setLineSpacing(dp(3).toFloat(), 1f)
    }

/**
 * Android 5+ üzerinde dokunma dalgası (ripple) ekler; daha eskisinde sade
 * arka planı olduğu gibi bırakır.
 */
private fun rippleWrap(base: GradientDrawable, context: Context): android.graphics.drawable.Drawable =
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
        RippleDrawable(
            ColorStateList.valueOf(Color.parseColor("#33FFFFFF")), base, null
        )
    } else {
        StateListDrawable().apply { addState(intArrayOf(), base) }
    }

/**
 * Giriş ekranındaki afiş (banner) görselini yükler.
 *
 * Görsel, APK'nın assets klasörüne paketleyici tarafından konur. GIF ise
 * API 28+ üzerinde HAREKETLİ olarak oynatılır (ImageDecoder ->
 * AnimatedImageDrawable); daha eski sürümlerde animasyon için ek bir
 * kütüphane gerekeceğinden ilk kare durağan olarak gösterilir — bu, sırf
 * afiş için projeye yeni bir bağımlılık eklemekten iyidir.
 *
 * Afiş yoksa ya da çözülemezse null döner ve ekran onsuz çizilir.
 */
internal fun Context.loadBannerDrawable(): Drawable? {
    if (!AeroKeyConfig.HAS_BANNER) return null
    val name = AeroKeyConfig.BANNER_ASSET
    if (name.isBlank()) return null

    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
        try {
            val source = ImageDecoder.createSource(assets, name)
            val drawable = ImageDecoder.decodeDrawable(source)
            (drawable as? AnimatedImageDrawable)?.apply {
                repeatCount = AnimatedImageDrawable.REPEAT_INFINITE
                start()
            }
            return drawable
        } catch (_: Exception) {
            // Aşağıdaki durağan çözümlemeye düşeriz.
        }
    }

    return try {
        assets.open(name).use { stream ->
            BitmapFactory.decodeStream(stream)?.let { BitmapDrawable(resources, it) }
        }
    } catch (_: Exception) {
        null
    }
}

/**
 * Kartın çevresinde yavaşça dönen gradyan bir kenarlık çizer.
 *
 * Giriş ekranını sade bir kutudan çıkarıp canlı hale getiren asıl öğe bu:
 * SweepGradient'i sürekli döndürerek kenarlıkta dolaşan bir ışık etkisi
 * elde ediyoruz.
 */
internal class GlowBorderView(context: Context) : View(context) {

    private val strokeWidthPx = context.dp(1.6).toFloat()
    private val cornerPx = context.dp(24).toFloat()
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = strokeWidthPx
    }
    private val matrix = android.graphics.Matrix()
    private var sweep: SweepGradient? = null
    private var angle = 0f

    private val animator = ValueAnimator.ofFloat(0f, 360f).apply {
        duration = 6000L
        repeatCount = ValueAnimator.INFINITE
        interpolator = LinearInterpolator()
        addUpdateListener {
            angle = it.animatedValue as Float
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

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(w, h, oldw, oldh)
        if (w > 0 && h > 0) {
            sweep = SweepGradient(
                w / 2f, h / 2f,
                intArrayOf(
                    Color.parseColor("#0022D3EE"), Palette.accentAlt, Palette.accent,
                    Palette.accentWarm, Color.parseColor("#0022D3EE")
                ),
                floatArrayOf(0f, 0.22f, 0.45f, 0.68f, 1f)
            )
        }
    }

    override fun onDraw(canvas: Canvas) {
        val shader = sweep ?: return
        matrix.setRotate(angle, width / 2f, height / 2f)
        shader.setLocalMatrix(matrix)
        paint.shader = shader

        val inset = strokeWidthPx / 2f
        canvas.drawRoundRect(
            inset, inset, width - inset, height - inset, cornerPx, cornerPx, paint
        )
    }
}

/** Dikey liste düzeni için kısa yol. */
internal fun Context.column(): LinearLayout = LinearLayout(this).apply {
    orientation = LinearLayout.VERTICAL
    layoutParams = ViewGroup.LayoutParams(
        ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
    )
}

/** Yatay satır düzeni için kısa yol. */
internal fun Context.row(): LinearLayout = LinearLayout(this).apply {
    orientation = LinearLayout.HORIZONTAL
    gravity = android.view.Gravity.CENTER_VERTICAL
    layoutParams = ViewGroup.LayoutParams(
        ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
    )
}

internal fun LinearLayout.addSpace(height: Int) {
    addView(View(context), LinearLayout.LayoutParams(1, height))
}
