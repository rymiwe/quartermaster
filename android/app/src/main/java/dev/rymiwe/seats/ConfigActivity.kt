package dev.rymiwe.seats

import android.app.Activity
import android.appwidget.AppWidgetManager
import android.content.Intent
import android.os.Bundle
import android.text.InputType
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import kotlin.concurrent.thread

// Asks for the secret gist that `seat-usage-push --setup` created. One gist feeds
// every widget, so the id is stored once.
class ConfigActivity : Activity() {
    private var widgetId = AppWidgetManager.INVALID_APPWIDGET_ID

    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
        widgetId = intent?.extras?.getInt(AppWidgetManager.EXTRA_APPWIDGET_ID, AppWidgetManager.INVALID_APPWIDGET_ID)
            ?: AppWidgetManager.INVALID_APPWIDGET_ID
        setResult(RESULT_CANCELED, result())

        val pad = (20 * resources.displayMetrics.density).toInt()
        val help = TextView(this).apply {
            text = "Paste the gist id or URL that `seat-usage-push --setup` printed on the desktop."
            setPadding(0, 0, 0, pad / 2)
        }
        val input = EditText(this).apply {
            hint = "gist id or https://gist.github.com/…"
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
            setSingleLine()
            setText(SeatWidget.prefs(this@ConfigActivity).getString(SeatWidget.KEY_GIST, ""))
        }
        val status = TextView(this)
        val save = Button(this).apply { text = "Save" }
        save.setOnClickListener {
            val id = Rules.gistId(input.text.toString())
            if (id == null) {
                status.text = "That doesn't contain a gist id."
                return@setOnClickListener
            }
            SeatWidget.prefs(this).edit().putString(SeatWidget.KEY_GIST, id).apply()
            save.isEnabled = false
            status.text = "Fetching…"
            thread {
                SeatWidget.fetch(this)
                SeatWidget.drawAll(this)
                runOnUiThread {
                    setResult(RESULT_OK, result())
                    finish()
                }
            }
        }
        setContentView(LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad, pad, pad)
            addView(help, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))
            addView(input, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))
            addView(save, LinearLayout.LayoutParams(WRAP_CONTENT, WRAP_CONTENT))
            addView(status, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))
        })
    }

    private fun result() = Intent().putExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, widgetId)
}
