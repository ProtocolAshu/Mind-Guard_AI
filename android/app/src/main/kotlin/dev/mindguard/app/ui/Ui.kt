package dev.mindguard.app.ui

import android.content.Context
import android.content.res.Configuration
import android.graphics.Typeface
import android.text.InputType
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView

/** Programmatic UI kit mirroring the web design tokens (cool paper by day, deep navy by night). */
class Ui(private val context: Context) {
    private val night = (context.resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK) == Configuration.UI_MODE_NIGHT_YES
    val paper = if (night) 0xFF0E1620.toInt() else 0xFFF5F7F8.toInt()
    val ink = if (night) 0xFFE3E9F0.toInt() else 0xFF17212B.toInt()
    val soft = if (night) 0xFFA3B0BE.toInt() else 0xFF4D5B69.toInt()
    val horizon = if (night) 0xFF8AB2DE.toInt() else 0xFF2E5E88.toInt()
    val alarm = if (night) 0xFFE57A89.toInt() else 0xFFA83246.toInt()
    private val density = context.resources.displayMetrics.density

    fun dp(value: Int): Int = (value * density).toInt()

    fun screen(build: LinearLayout.() -> Unit): View = ScrollView(context).apply {
        setBackgroundColor(paper)
        addView(LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(24), dp(32), dp(24), dp(48))
            build()
        })
    }

    fun LinearLayout.title(text: String) = addView(label(text, 26f, ink, bold = true))
    fun LinearLayout.body(text: String) = addView(label(text, 16f, ink).apply { setPadding(0, dp(8), 0, dp(8)) })
    fun LinearLayout.note(text: String) = addView(label(text, 14f, soft).apply { setPadding(0, dp(4), 0, dp(12)) })
    fun LinearLayout.readout(value: String, caption: String) {
        addView(label(value, 56f, ink, bold = true).apply { typeface = Typeface.create("sans-serif-condensed", Typeface.BOLD) })
        note(caption)
    }

    fun LinearLayout.button(text: String, primary: Boolean = true, onClick: () -> Unit): Button = Button(context).apply {
        this.text = text
        isAllCaps = false
        setTextColor(if (primary) paper else horizon)
        setBackgroundColor(if (primary) horizon else paper)
        setOnClickListener { onClick() }
        layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = dp(8) }
    }.also { addView(it) }

    fun LinearLayout.input(hint: String, password: Boolean = false, multiline: Boolean = false): EditText = EditText(context).apply {
        this.hint = hint
        setTextColor(ink)
        setHintTextColor(soft)
        inputType = when {
            password -> InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
            multiline -> InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE
            else -> InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_EMAIL_ADDRESS
        }
        if (multiline) minLines = 4
    }.also { addView(it) }

    fun label(text: String, size: Float, color: Int, bold: Boolean = false) = TextView(context).apply {
        this.text = text
        textSize = size
        setTextColor(color)
        if (bold) setTypeface(typeface, Typeface.BOLD)
    }
}
