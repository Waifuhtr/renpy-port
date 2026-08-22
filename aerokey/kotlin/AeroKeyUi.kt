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
    SheenButton(this).apply {
        this.text = text
        isAllCaps = false
        setTextColor(Color.WHITE)
        textSize = 16f
        typeface = android.graphics.Typeface.DEFAULT_BOLD
        stateListAnimator = null
        // Üç renkli geçiş, iki renkliye göre gözle görülür şekilde daha
        // canlı; ışıltı bandı da bunun üzerinden geçiyor.
        background = rippleWrap(
            GradientDrawable(
                GradientDrawable.Orientation.LEFT_RIGHT,
                intArrayOf(Palette.accentAlt, Palette.accent, Palette.accentWarm)
            ).apply { cornerRadius = dp(14).toFloat() },
            this@primaryButton
        )
        setPadding(dp(20), dp(15), dp(20), dp(15))
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            elevation = dp(6).toFloat()
        }
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
            GradientDrawable(
                GradientDrawable.Orientation.TOP_BOTTOM,
                intArrayOf(Color.parseColor("#1FFFFFFF"), Color.parseColor("#0AFFFFFF"))
            ).apply {
                cornerRadius = dp(14).toFloat()
                setStroke(dp(1), Color.parseColor("#3D8B7CF6"))
            },
            this@secondaryButton
        )
        setPadding(dp(18), dp(13), dp(18), dp(13))
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            elevation = dp(2).toFloat()
        }
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
    return loadAssetDrawable(name, animated = true)
}

/**
 * assets içindeki bir görseli çözer.
 *
 * `animated = true` ise ve cihaz API 28+ ise GIF hareketli olarak döner
 * (AnimatedImageDrawable) ve OYNATILMAZ — başlatmak çağırana bırakılır.
 * Bu bilinçli: avatar ızgarasında aynı anda onlarca GIF oynatmak kare
 * hızını düşürür, bu yüzden yalnızca seçili olan başlatılır.
 *
 * `animated = false` ise yalnızca ilk kare çözülür; istenirse
 * `maxSizePx` ile küçültülerek belleğe alınır.
 */
internal fun Context.loadAssetDrawable(
    name: String,
    animated: Boolean,
    maxSizePx: Int = 0
): Drawable? {
    if (name.isBlank()) return null

    if (animated && Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
        try {
            val source = ImageDecoder.createSource(assets, name)
            return ImageDecoder.decodeDrawable(source)
        } catch (_: Exception) {
            // Aşağıdaki durağan çözümlemeye düşeriz.
        }
    }

    return try {
        decodeAssetBitmap(name, maxSizePx)?.let { BitmapDrawable(resources, it) }
    } catch (_: Exception) {
        null
    }
}

/**
 * Bir varlığı, hedef boyuta göre KÜÇÜLTEREK çözer.
 *
 * Avatar ızgarasında 500x500'lük GIF'leri tam boyutta belleğe almak
 * gereksiz; inSampleSize ile karesel olarak küçültüp yalnızca ilk kareyi
 * alıyoruz. Bu, "hepsi oynarsa fps düşer" endişesinin bellek tarafındaki
 * karşılığı.
 */
internal fun Context.decodeAssetBitmap(name: String, maxSizePx: Int): android.graphics.Bitmap? {
    if (maxSizePx <= 0) {
        return assets.open(name).use { BitmapFactory.decodeStream(it) }
    }

    val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
    assets.open(name).use { BitmapFactory.decodeStream(it, null, bounds) }

    var sample = 1
    val largest = maxOf(bounds.outWidth, bounds.outHeight)
    while (largest > 0 && largest / sample > maxSizePx * 2) sample *= 2

    val options = BitmapFactory.Options().apply { inSampleSize = sample }
    return assets.open(name).use { BitmapFactory.decodeStream(it, null, options) }
}

/** AeroKeyConfig'teki virgülle ayrılmış avatar listesini ayrıştırır. */
internal fun avatarAssets(): List<String> =
    AeroKeyConfig.AVATARS.split(',')
        .map { it.trim() }
        .filter { it.isNotEmpty() }

/**
 * Çevresinde yavaşça dönen gradyan bir kenarlık çizen KAPSAYICI.
 *
 * Kenarlığı ayrı bir `View` olarak üstüne bindirmiyoruz; kapsayıcının
 * kendisi çiziyor. Bunun sebebi somut bir hata:
 *
 * `wrap_content` yükseklikli bir FrameLayout içine `match_parent` bir
 * bindirme koyunca, ScrollView'in `fillViewport`'u (içerik ekrandan
 * kısaysa) düzeni `EXACTLY(ekran)` ile yeniden ölçer. O zincirde
 * `EXACTLY(H) -> AT_MOST(H) -> AT_MOST(H)` olur ve sade bir View
 * `AT_MOST` için spec boyunu döndürdüğü için bindirme H piksele şişer;
 * kapsayıcı da onun boyuna sarılıp TÜM EKRANI kaplar. Sonuç: kartın
 * altında uzun boş bir bant, afişte ise ekranın tamamını yiyen boş bir
 * kutu.
 *
 * Kenarlığı `dispatchDraw` içinde, çocukların ÜSTÜNE çizerek bu sınıfı
 * ölçüm denkleminden tamamen çıkarıyoruz.
 */
internal class GlowBorderFrame(context: Context) : android.widget.FrameLayout(context) {

    private val strokeWidthPx = context.dp(1.6).toFloat()
    private val cornerPx = context.dp(24).toFloat()
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = strokeWidthPx
    }
    private val matrix = android.graphics.Matrix()
    private var sweep: SweepGradient? = null
    private var angle = 0f

    /** Köşe yarıçapı; afiş ve kart farklı yuvarlaklıkta olabiliyor. */
    var cornerRadiusPx: Float = cornerPx

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

    override fun dispatchDraw(canvas: Canvas) {
        super.dispatchDraw(canvas)

        val shader = sweep ?: return
        matrix.setRotate(angle, width / 2f, height / 2f)
        shader.setLocalMatrix(matrix)
        paint.shader = shader

        val inset = strokeWidthPx / 2f
        canvas.drawRoundRect(
            inset, inset, width - inset, height - inset,
            cornerRadiusPx, cornerRadiusPx, paint
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

// --- Afiş: kutuyu TAMAMEN dolduran görsel -------------------------------

/**
 * Genişliğe göre sabit bir en–boy oranıyla ölçülen görsel.
 *
 * Afişin kutuyu tam doldurması için gereken şey buydu: eskiden
 * `adjustViewBounds + FIT_CENTER` kullanılıyordu, yani görselin kendi oranı
 * kutununkinden farklıysa kenarlarda boşluk (letterbox) kalıyordu. Burada
 * yüksekliği oranla biz belirliyor, ölçeklemeyi CENTER_CROP'a bırakıyoruz;
 * böylece görsel taşan kenarından kırpılır ama boşluk KALMAZ.
 */
internal class AspectImageView(
    context: Context,
    private val widthOverHeight: Float
) : android.widget.ImageView(context) {

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        val w = MeasureSpec.getSize(widthMeasureSpec)
        if (w > 0 && widthOverHeight > 0f) {
            setMeasuredDimension(w, (w / widthOverHeight).toInt())
        } else {
            super.onMeasure(widthMeasureSpec, heightMeasureSpec)
        }
    }
}

// --- Işıltılı düğme ------------------------------------------------------

/**
 * Üzerinden düzenli aralıklarla ışık bandı geçen birincil düğme.
 *
 * Bant, düğmenin yuvarlatılmış dikdörtgenine çizildiği için ayrıca kırpma
 * (clipPath) gerekmez — tek bir drawRoundRect hem bandı çizer hem sınırda
 * tutar. Animasyon yalnızca görünürken çalışır; pencereden ayrılınca
 * durdurulur, yani arka planda pil yakmaz.
 */
internal class SheenButton(context: Context) : Button(context) {

    private val sheenPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val matrix = android.graphics.Matrix()
    private var shader: LinearGradient? = null
    private var bandWidth = 0f
    private var progress = -1f
    private val corner = context.dp(14).toFloat()

    /**
     * Bandın döngünün NE KADARINDA geçtiği. Kalan kısım bekleme süresidir:
     * ışıltı bir geçip sonra bir süre durur. (ValueAnimator'da tekrarlar
     * arasına gecikme koyan bir özellik yok, o yüzden bekleme döngünün
     * kendi içine gömülü.)
     */
    private val sweepDuty = 0.32f

    private val animator = ValueAnimator.ofFloat(0f, 1f).apply {
        duration = 3400L
        startDelay = 700L
        repeatCount = ValueAnimator.INFINITE
        interpolator = LinearInterpolator()
        addUpdateListener {
            progress = it.animatedValue as Float
            // Yalnızca bandın görünür olduğu evrede yeniden çiziyoruz;
            // bekleme evresinde boşuna kare üretmiyoruz.
            if (progress <= sweepDuty) invalidate()
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
        if (w <= 0) return
        bandWidth = w * 0.30f
        shader = LinearGradient(
            0f, 0f, bandWidth, 0f,
            intArrayOf(
                Color.parseColor("#00FFFFFF"),
                Color.parseColor("#4DFFFFFF"),
                Color.parseColor("#00FFFFFF")
            ),
            floatArrayOf(0f, 0.5f, 1f),
            Shader.TileMode.CLAMP
        )
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)

        val s = shader ?: return
        if (progress < 0f || progress > sweepDuty) return

        // Döngünün ışıltı evresini 0..1 aralığına ölçekle ve yumuşat.
        val raw = (progress / sweepDuty).coerceIn(0f, 1f)
        val eased = (1f - cos(raw * Math.PI.toFloat())) / 2f

        val travel = width + bandWidth
        matrix.setTranslate(eased * travel - bandWidth, 0f)
        // Hafif eğim, bandı dikey yerine diyagonal gösterir.
        matrix.postSkew(-0.30f, 0f, 0f, height / 2f)
        s.setLocalMatrix(matrix)
        sheenPaint.shader = s

        canvas.drawRoundRect(
            0f, 0f, width.toFloat(), height.toFloat(), corner, corner, sheenPaint
        )
    }
}

// --- Avatar --------------------------------------------------------------

/**
 * Yuvarlak avatar görüntüleyici.
 *
 * Avatar seçilmemişse (ya da varlık çözülemezse) adın ilk harfinden
 * renkli bir rozet çizer; böylece boş bir kutu asla görünmez.
 */
internal class AvatarView(context: Context) : View(context) {

    private val ringPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = context.dp(2).toFloat()
    }
    private val fillPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val letterPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Palette.textPrimary
        textAlign = Paint.Align.CENTER
        typeface = android.graphics.Typeface.DEFAULT_BOLD
    }

    private var drawable: Drawable? = null
    private var letter: String = "?"

    /**
     * Seçili halka vurgusu.
     *
     * Adı bilerek `selected` DEĞİL: View sınıfında zaten bir
     * `setSelected(boolean)` var ve aynı isim JVM'de imza çakışmasına yol
     * açıyor.
     */
    var highlighted: Boolean = false
        set(value) {
            field = value
            invalidate()
        }

    fun setFallbackLetter(name: String) {
        letter = name.trim().take(1).uppercase().ifBlank { "?" }
        invalidate()
    }

    /**
     * Gösterilecek görseli değiştirir.
     *
     * `animate = true` ise ve görsel hareketliyse oynatılır. Izgarada
     * yalnızca SEÇİLİ olan için true veriyoruz: onlarca GIF'i aynı anda
     * oynatmak kare hızını gözle görülür şekilde düşürür.
     */
    fun setAvatarDrawable(value: Drawable?, animate: Boolean) {
        // Önceki hareketli görseli durdur; aksi halde görünmese bile
        // kare üretmeye devam eder.
        stopAnimation()

        drawable = value
        value?.callback = object : Drawable.Callback {
            override fun invalidateDrawable(who: Drawable) = invalidate()
            override fun scheduleDrawable(who: Drawable, what: Runnable, whenMs: Long) {
                postDelayed(what, whenMs - android.os.SystemClock.uptimeMillis())
            }
            override fun unscheduleDrawable(who: Drawable, what: Runnable) {
                removeCallbacks(what)
            }
        }

        if (animate && Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            (value as? AnimatedImageDrawable)?.apply {
                repeatCount = AnimatedImageDrawable.REPEAT_INFINITE
                start()
            }
        }
        invalidate()
    }

    fun stopAnimation() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            (drawable as? AnimatedImageDrawable)?.stop()
        }
    }

    override fun onDetachedFromWindow() {
        stopAnimation()
        super.onDetachedFromWindow()
    }

    override fun onDraw(canvas: Canvas) {
        val size = minOf(width, height).toFloat()
        if (size <= 0f) return
        val cx = width / 2f
        val cy = height / 2f
        val radius = size / 2f - ringPaint.strokeWidth

        val art = drawable
        if (art != null) {
            // Görseli daireye kırpıp çiziyoruz.
            val save = canvas.save()
            val path = android.graphics.Path().apply {
                addCircle(cx, cy, radius, android.graphics.Path.Direction.CW)
            }
            canvas.clipPath(path)
            art.setBounds(
                (cx - radius).toInt(), (cy - radius).toInt(),
                (cx + radius).toInt(), (cy + radius).toInt()
            )
            art.draw(canvas)
            canvas.restoreToCount(save)
        } else {
            fillPaint.shader = LinearGradient(
                0f, 0f, size, size,
                intArrayOf(Palette.accent, Palette.accentWarm),
                null, Shader.TileMode.CLAMP
            )
            canvas.drawCircle(cx, cy, radius, fillPaint)
            letterPaint.textSize = radius * 1.05f
            val baseline = cy - (letterPaint.descent() + letterPaint.ascent()) / 2f
            canvas.drawText(letter, cx, baseline, letterPaint)
        }

        ringPaint.color = if (highlighted) Palette.accentAlt else Color.parseColor("#33FFFFFF")
        ringPaint.strokeWidth = context.dp(if (highlighted) 2.5 else 1.2).toFloat()
        canvas.drawCircle(cx, cy, radius, ringPaint)
    }
}

/**
 * Yatay avatar seçici.
 *
 * KARE HIZI KURALI: aynı anda YALNIZCA bir GIF oynar — seçili olan.
 * Diğerleri, küçültülmüş tek bir durağan kare olarak çizilir. Oyuncu başka
 * bir avatara dokunduğunda önceki durdurulup durağana çevrilir, yenisi
 * hareketli olarak yüklenir. Onlarca GIF'i birden oynatmak düşük donanımlı
 * telefonlarda gözle görülür takılmaya yol açtığı için bu davranış
 * bilinçli.
 */
internal class AvatarPicker(
    context: Context,
    private val assetNames: List<String>,
    initialSelection: String,
    private val fallbackLetter: String,
    private val onSelected: (String) -> Unit
) : android.widget.HorizontalScrollView(context) {

    private val tiles = mutableListOf<Pair<String, AvatarView>>()
    private val tileSize = context.dp(62)
    private var current: String = initialSelection

    /** Şu an seçili avatarın varlık adı (hiçbiri seçilmediyse boş). */
    val selection: String get() = current

    init {
        isHorizontalScrollBarEnabled = false
        overScrollMode = OVER_SCROLL_NEVER
        clipToPadding = false
        setPadding(0, context.dp(2), 0, context.dp(2))

        val row = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = android.view.Gravity.CENTER_VERTICAL
        }

        for ((index, name) in assetNames.withIndex()) {
            val view = AvatarView(context).apply {
                setFallbackLetter(fallbackLetter)
                isClickable = true
                isFocusable = true
                addPressFeedback()
            }
            view.setOnClickListener { select(name) }

            row.addView(
                view,
                LinearLayout.LayoutParams(tileSize, tileSize).apply {
                    if (index > 0) leftMargin = context.dp(10)
                }
            )
            tiles.add(name to view)
        }

        addView(
            row,
            ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT
            )
        )

        // İlk çizim: hepsi durağan, seçili olan hareketli.
        for ((name, view) in tiles) {
            view.highlighted = (name == current)
            loadInto(view, name, animate = name == current)
        }
    }

    private fun select(name: String) {
        if (name == current) return

        // Önce eskisini durdurup durağan kareye indir.
        for ((otherName, view) in tiles) {
            if (otherName == current) {
                view.highlighted = false
                loadInto(view, otherName, animate = false)
            }
        }

        current = name
        for ((otherName, view) in tiles) {
            if (otherName == name) {
                view.highlighted = true
                loadInto(view, otherName, animate = true)
                view.animate().scaleX(1.12f).scaleY(1.12f).setDuration(120)
                    .withEndAction {
                        view.animate().scaleX(1f).scaleY(1f).setDuration(140).start()
                    }.start()
            }
        }
        onSelected(name)
    }

    private fun loadInto(view: AvatarView, name: String, animate: Boolean) {
        val drawable = context.loadAssetDrawable(
            name,
            animated = animate,
            maxSizePx = if (animate) 0 else tileSize
        )
        view.setAvatarDrawable(drawable, animate)
    }

    /** Ekran kapanırken oynayan GIF'i serbest bırakır. */
    fun release() {
        for ((_, view) in tiles) view.stopAnimation()
    }
}
