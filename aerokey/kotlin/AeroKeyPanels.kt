package com.riaslink.aerokey

import android.app.Activity
import android.app.Dialog
import android.graphics.Color
import android.graphics.drawable.ColorDrawable
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.Window
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import org.json.JSONArray

/**
 * Giriş ekranının altındaki isteğe bağlı paneller: liderlik tablosu, anket,
 * profil ve hata bildirimi. Her biri, giriş ekranıyla aynı görsel dili
 * kullanan yalın bir alt sayfa (bottom sheet benzeri diyalog) olarak açılır.
 *
 * Bu paneller Ren'Py Android Paketleyici arayüzünden tek tek açılıp
 * kapatılabilir; kapalıysa ilgili düğme hiç çizilmez.
 */
internal object AeroKeyPanels {

    /** Ortak diyalog iskeleti: başlık + içerik + kapat düğmesi. */
    private fun sheet(activity: Activity, title: String): Pair<Dialog, LinearLayout> {
        val dialog = Dialog(activity)
        dialog.requestWindowFeature(Window.FEATURE_NO_TITLE)
        dialog.window?.setBackgroundDrawable(ColorDrawable(Color.TRANSPARENT))

        val outer = activity.column().apply {
            setPadding(activity.dp(16), activity.dp(16), activity.dp(16), activity.dp(16))
        }

        val card = activity.column().apply {
            background = glassCard(activity)
            setPadding(activity.dp(22), activity.dp(20), activity.dp(22), activity.dp(20))
        }

        val header = activity.row()
        header.addView(
            TextView(activity).apply {
                text = title
                setTextColor(Palette.textPrimary)
                textSize = 19f
                typeface = android.graphics.Typeface.DEFAULT_BOLD
            },
            LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        )
        header.addView(activity.ghostButton("Kapat").apply {
            setOnClickListener { dialog.dismiss() }
        })
        card.addView(header)
        card.addSpace(activity.dp(16))

        val content = activity.column()
        card.addView(content)

        outer.addView(card)
        dialog.setContentView(outer)
        // Oyun yatay çalıştığı için diyalogu tüm genişliğe yaymıyoruz;
        // yatayda ekranın bir kısmıyla sınırlı, dikeyde neredeyse tam
        // genişlik kullanıyoruz (bkz. Activity.dialogWidth).
        dialog.window?.setLayout(
            activity.dialogWidth(),
            ViewGroup.LayoutParams.WRAP_CONTENT
        )
        dialog.window?.setGravity(Gravity.CENTER)
        card.enterWithFade(0)
        return dialog to content
    }

    private fun loadingRow(activity: Activity): View {
        val row = activity.row().apply { gravity = Gravity.CENTER }
        row.addView(
            SpinnerView(activity),
            LinearLayout.LayoutParams(activity.dp(22), activity.dp(22))
        )
        row.addView(activity.bodyText("  Yükleniyor…"))
        return row
    }

    private fun replaceContent(container: LinearLayout, view: View) {
        container.removeAllViews()
        container.addView(view)
        view.enterWithFade(0)
    }

    // --- Liderlik --------------------------------------------------------

    fun showLeaderboard(activity: Activity) {
        val (dialog, content) = sheet(activity, "🏆 En Çok Oynayanlar")
        content.addView(loadingRow(activity))
        dialog.show()

        AeroKeyAsync.run({ AeroKeyApi.leaderboard() }) { result ->
            if (!activity.isFinishing) {
                replaceContent(content, buildLeaderboard(activity, result))
            }
        }
    }

    private fun buildLeaderboard(activity: Activity, result: AeroKeyApi.Result): View {
        if (result !is AeroKeyApi.Result.Ok) {
            return activity.bodyText((result as AeroKeyApi.Result.Failed).reason)
        }
        val list: JSONArray = result.body.optJSONArray("liste") ?: JSONArray()
        if (list.length() == 0) {
            return activity.bodyText("Henüz kimse listeye girmemiş. İlk sen ol!")
        }

        val myName = AeroKeyPrefs.username(activity)
        val column = activity.column()

        for (i in 0 until list.length()) {
            val entry = list.optJSONObject(i) ?: continue
            val name = entry.optString("kullanici_adi", "—")
            val seconds = entry.optLong("toplam_saniye", 0L)
            val isMe = name == myName

            val row = activity.row().apply {
                background = roundedFill(
                    if (isMe) Color.parseColor("#268B7CF6") else Color.parseColor("#0DFFFFFF"),
                    activity.dp(12)
                )
                setPadding(activity.dp(12), activity.dp(10), activity.dp(12), activity.dp(10))
            }

            val medal = when (i) {
                0 -> "🥇"; 1 -> "🥈"; 2 -> "🥉"; else -> "${i + 1}."
            }
            row.addView(TextView(activity).apply {
                text = medal
                setTextColor(if (i < 3) Palette.gold else Palette.textMuted)
                textSize = 15f
                typeface = android.graphics.Typeface.DEFAULT_BOLD
                width = activity.dp(38)
            })
            row.addView(
                TextView(activity).apply {
                    text = name
                    setTextColor(if (isMe) Palette.accentAlt else Palette.textPrimary)
                    textSize = 15f
                    maxLines = 1
                    ellipsize = android.text.TextUtils.TruncateAt.END
                    if (isMe) typeface = android.graphics.Typeface.DEFAULT_BOLD
                },
                LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
            )
            row.addView(TextView(activity).apply {
                text = formatPlaytime(seconds)
                setTextColor(Palette.textSecondary)
                textSize = 13f
            })

            column.addView(
                row,
                LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT
                ).apply { bottomMargin = activity.dp(8) }
            )
        }

        return ScrollView(activity).apply {
            isVerticalScrollBarEnabled = false
            addView(column)
            // Sabit bir yükseklik yatay ekranda diyalogu ekran dışına
            // taşırırdı; ekran yüksekliğinin bir oranını kullanıyoruz.
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, activity.dialogListHeight()
            )
        }
    }

    // --- Anket -----------------------------------------------------------

    fun showSurvey(activity: Activity) {
        val (dialog, content) = sheet(activity, "📊 Topluluk Anketi")
        content.addView(loadingRow(activity))
        dialog.show()

        AeroKeyAsync.run({ AeroKeyApi.survey() }) { result ->
            if (activity.isFinishing) return@run
            if (result !is AeroKeyApi.Result.Ok ||
                result.body.optString("durum") != "basarili"
            ) {
                replaceContent(content, activity.bodyText("Şu an yayında bir anket yok."))
                return@run
            }
            replaceContent(content, buildSurvey(activity, content, result.body))
        }
    }

    private fun buildSurvey(
        activity: Activity,
        container: LinearLayout,
        body: org.json.JSONObject
    ): View {
        val surveyId = body.optInt("id", 0)
        val column = activity.column()

        column.addView(TextView(activity).apply {
            text = body.optString("soru", "")
            setTextColor(Palette.textPrimary)
            textSize = 17f
            typeface = android.graphics.Typeface.DEFAULT_BOLD
        })
        column.addSpace(activity.dp(18))

        fun castVote(choice: Int) {
            replaceContent(container, loadingRow(activity))
            AeroKeyAsync.run({ AeroKeyApi.vote(surveyId, choice) }) {
                if (activity.isFinishing) return@run
                val total = body.optInt("oy1", 0) + body.optInt("oy2", 0) + 1
                val mine1 = body.optInt("oy1", 0) + if (choice == 1) 1 else 0
                val mine2 = body.optInt("oy2", 0) + if (choice == 2) 1 else 0
                val results = activity.column()
                results.addView(activity.bodyText("Oyun kaydedildi, teşekkürler!", 15f).apply {
                    setTextColor(Palette.success)
                })
                results.addSpace(activity.dp(14))
                results.addView(
                    resultBar(activity, body.optString("secenek1"), mine1, total, choice == 1)
                )
                results.addSpace(activity.dp(10))
                results.addView(
                    resultBar(activity, body.optString("secenek2"), mine2, total, choice == 2)
                )
                replaceContent(container, results)
            }
        }

        column.addView(activity.secondaryButton(body.optString("secenek1", "Seçenek 1")).apply {
            setOnClickListener { castVote(1) }
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
            )
        })
        column.addSpace(activity.dp(10))
        column.addView(activity.secondaryButton(body.optString("secenek2", "Seçenek 2")).apply {
            setOnClickListener { castVote(2) }
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
            )
        })
        return column
    }

    /** Oy oranını gösteren, dolan bir çubuk. */
    private fun resultBar(
        activity: Activity,
        label: String,
        votes: Int,
        total: Int,
        mine: Boolean
    ): View {
        val percent = if (total <= 0) 0 else (votes * 100 / total)
        val holder = activity.column()

        val head = activity.row()
        head.addView(
            TextView(activity).apply {
                text = if (mine) "$label  ✓" else label
                setTextColor(if (mine) Palette.accentAlt else Palette.textSecondary)
                textSize = 14f
            },
            LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        )
        head.addView(TextView(activity).apply {
            text = "%$percent"
            setTextColor(Palette.textPrimary)
            textSize = 14f
            typeface = android.graphics.Typeface.DEFAULT_BOLD
        })
        holder.addView(head)
        holder.addSpace(activity.dp(6))

        val track = LinearLayout(activity).apply {
            background = roundedFill(Color.parseColor("#1AFFFFFF"), activity.dp(6))
        }
        val fill = View(activity).apply {
            background = gradientPill(activity, Palette.accent, Palette.accentAlt)
        }
        track.addView(fill, LinearLayout.LayoutParams(0, activity.dp(10)))
        holder.addView(
            track,
            LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, activity.dp(10))
        )

        // Çubuğu, genişlik ölçüldükten sonra animasyonla doldur.
        track.post {
            val target = (track.width * percent / 100).coerceAtLeast(activity.dp(4))
            android.animation.ValueAnimator.ofInt(0, target).apply {
                duration = 620
                addUpdateListener { anim ->
                    fill.layoutParams = LinearLayout.LayoutParams(
                        anim.animatedValue as Int, activity.dp(10)
                    )
                }
                start()
            }
        }
        return holder
    }

    // --- Profil ----------------------------------------------------------

    fun showProfile(activity: Activity) {
        val (dialog, content) = sheet(activity, "👤 Profilim")
        content.addView(loadingRow(activity))
        dialog.show()

        val username = AeroKeyPrefs.username(activity)
        AeroKeyAsync.run({ AeroKeyApi.profile(username) }) { result ->
            if (activity.isFinishing) return@run

            val column = activity.column()
            column.addView(activity.sectionLabel("KULLANICI ADI"))
            column.addSpace(activity.dp(6))
            column.addView(TextView(activity).apply {
                text = username
                setTextColor(Palette.textPrimary)
                textSize = 20f
                typeface = android.graphics.Typeface.DEFAULT_BOLD
            })
            column.addSpace(activity.dp(18))

            val serverTotal = if (result is AeroKeyApi.Result.Ok &&
                result.body.optString("durum") == "basarili"
            ) {
                result.body.optLong("toplam_saniye", -1L)
            } else -1L

            column.addView(statRow(activity, "Bu oyundaki süren",
                formatPlaytime(AeroKeySession.currentGameSeconds(activity))))
            column.addSpace(activity.dp(10))
            column.addView(statRow(activity, "Toplam oyun süren",
                formatPlaytime(
                    if (serverTotal >= 0) serverTotal
                    else AeroKeySession.currentTotalSeconds(activity)
                )))

            if (serverTotal < 0) {
                column.addSpace(activity.dp(14))
                column.addView(activity.bodyText(
                    "Sunucudaki kaydın henüz oluşmamış olabilir; biraz " +
                        "oynadıktan sonra burada görünecek.", 12f
                ))
            }

            replaceContent(content, column)
        }
    }

    private fun statRow(activity: Activity, label: String, value: String): View {
        val row = activity.row().apply {
            background = roundedFill(Color.parseColor("#0DFFFFFF"), activity.dp(12))
            setPadding(activity.dp(14), activity.dp(12), activity.dp(14), activity.dp(12))
        }
        row.addView(
            activity.bodyText(label),
            LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        )
        row.addView(TextView(activity).apply {
            text = value
            setTextColor(Palette.accentAlt)
            textSize = 15f
            typeface = android.graphics.Typeface.DEFAULT_BOLD
        })
        return row
    }

    // --- Hata bildirimi --------------------------------------------------

    fun showBugReport(activity: Activity) {
        val (dialog, content) = sheet(activity, "🐞 Hata Bildir")

        val column = activity.column()
        column.addView(activity.bodyText(
            "Oyunda karşılaştığın sorunu birkaç cümleyle anlat; " +
                "doğrudan geliştiriciye ulaşır."
        ))
        column.addSpace(activity.dp(14))

        val input = activity.styledInput("Ne oldu?").apply {
            maxLines = 5
            minLines = 3
            inputType = InputType.TYPE_CLASS_TEXT or
                InputType.TYPE_TEXT_FLAG_MULTI_LINE or
                InputType.TYPE_TEXT_FLAG_CAP_SENTENCES
            gravity = Gravity.TOP or Gravity.START
        }
        column.addView(
            input,
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
            )
        )
        column.addSpace(activity.dp(14))

        val status = activity.bodyText("")
        val send = activity.primaryButton("Gönder").apply {
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
            )
        }
        column.addView(send)
        column.addSpace(activity.dp(10))
        column.addView(status)

        send.setOnClickListener {
            val message = input.text.toString().trim()
            if (message.length < 5) {
                status.setTextColor(Palette.danger)
                status.text = "Lütfen biraz daha ayrıntı yaz."
                return@setOnClickListener
            }
            send.isEnabled = false
            status.setTextColor(Palette.textSecondary)
            status.text = "Gönderiliyor…"

            val deviceId = AeroKeyPrefs.deviceId(activity)
            val username = AeroKeyPrefs.username(activity)
            AeroKeyAsync.run({
                AeroKeyApi.reportBug(deviceId, username, "[${AeroKeyConfig.GAME_ID}] $message")
            }) { result ->
                if (activity.isFinishing) return@run
                if (result is AeroKeyApi.Result.Ok) {
                    status.setTextColor(Palette.success)
                    status.text = "Teşekkürler! Bildirimin iletildi."
                    input.setText("")
                    input.postDelayed({ if (!activity.isFinishing) dialog.dismiss() }, 1400)
                } else {
                    send.isEnabled = true
                    status.setTextColor(Palette.danger)
                    status.text = (result as AeroKeyApi.Result.Failed).reason
                }
            }
        }

        content.addView(column)
        dialog.show()
    }
}
