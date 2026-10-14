package dev.rymiwe.seats

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.res.Configuration
import android.os.Bundle
import android.widget.RemoteViews
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URI
import kotlin.concurrent.thread

// Reads the gist seat-usage-push writes, keeps the last good copy, and redraws every
// widget from it. A tap refetches. Android calls onUpdate every 30 minutes.
class SeatWidget : AppWidgetProvider() {

    override fun onUpdate(ctx: Context, mgr: AppWidgetManager, ids: IntArray) {
        drawAll(ctx)
        fetchAsync(ctx)
    }

    override fun onAppWidgetOptionsChanged(ctx: Context, mgr: AppWidgetManager, id: Int, opts: Bundle) {
        draw(ctx, mgr, id)
    }

    override fun onReceive(ctx: Context, intent: Intent) {
        if (intent.action == ACTION_REFRESH) {
            drawAll(ctx)
            fetchAsync(ctx)
            return
        }
        super.onReceive(ctx, intent)
    }

    private fun fetchAsync(ctx: Context) {
        val pending = goAsync()
        thread {
            try {
                fetch(ctx)
                drawAll(ctx)
            } finally {
                pending.finish()
            }
        }
    }

    companion object {
        const val ACTION_REFRESH = "dev.rymiwe.seats.REFRESH"
        private const val PREFS = "seats"
        const val KEY_GIST = "gist_id"
        private const val KEY_PAYLOAD = "payload"
        private const val KEY_ERROR = "error"
        private const val FILE = "seats.json"

        fun prefs(ctx: Context) = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

        fun fetch(ctx: Context) {
            val p = prefs(ctx)
            val id = p.getString(KEY_GIST, null) ?: return
            val err = try {
                val conn = URI("https://api.github.com/gists/$id").toURL().openConnection() as HttpURLConnection
                conn.connectTimeout = 6000
                conn.readTimeout = 6000
                conn.setRequestProperty("Accept", "application/vnd.github+json")
                conn.setRequestProperty("User-Agent", "seats-widget")
                try {
                    if (conn.responseCode != 200) throw RuntimeException("HTTP ${conn.responseCode}")
                    val body = conn.inputStream.bufferedReader().use { it.readText() }
                    val content = JSONObject(body).getJSONObject("files").getJSONObject(FILE).getString("content")
                    parse(content)  // reject a payload we can't draw before replacing the good one
                    p.edit().putString(KEY_PAYLOAD, content).apply()
                    null
                } finally {
                    conn.disconnect()
                }
            } catch (e: Exception) {
                e.message ?: e.javaClass.simpleName
            }
            p.edit().putString(KEY_ERROR, err).apply()
        }

        fun parse(s: String): Payload {
            val o = JSONObject(s)
            fun JSONObject.num(k: String): Double? = if (isNull(k) || !has(k)) null else optDouble(k).takeIf { !it.isNaN() }
            fun JSONObject.str(k: String): String? = if (isNull(k) || !has(k)) null else optString(k)
            val pools = o.getJSONArray("pools")
            return Payload(
                ts = o.num("ts"),
                pushedAt = o.num("pushed_at"),
                pools = (0 until pools.length()).map { i ->
                    val po = pools.getJSONObject(i)
                    val seats = po.getJSONArray("seats")
                    Pool(
                        id = po.getString("id"),
                        name = po.optString("name"),
                        hue = po.optInt("hue", 7),
                        seats = (0 until seats.length()).map { j ->
                            val so = seats.getJSONObject(j)
                            Seat(
                                id = so.getString("id"),
                                short = so.optString("short"),
                                label = so.optString("label", so.getString("id")),
                                pct = so.num("pct"),
                                resetTs = so.num("reset_ts"),
                                windowS = so.num("window_s"),
                                pace = so.optString("pace"),
                                exhausted = so.optBoolean("exhausted"),
                                asOf = so.num("as_of"),
                            )
                        },
                    )
                },
            )
        }

        fun drawAll(ctx: Context) {
            val mgr = AppWidgetManager.getInstance(ctx)
            for (id in mgr.getAppWidgetIds(ComponentName(ctx, SeatWidget::class.java))) draw(ctx, mgr, id)
        }

        fun draw(ctx: Context, mgr: AppWidgetManager, id: Int) {
            val p = prefs(ctx)
            val payload = p.getString(KEY_PAYLOAD, null)?.let { runCatching { parse(it) }.getOrNull() }
            val opts = mgr.getAppWidgetOptions(id)
            val landscape = ctx.resources.configuration.orientation == Configuration.ORIENTATION_LANDSCAPE
            // Launchers report min width/max height for portrait and the reverse for landscape.
            val w = opts.getInt(if (landscape) AppWidgetManager.OPTION_APPWIDGET_MAX_WIDTH else AppWidgetManager.OPTION_APPWIDGET_MIN_WIDTH, 0).takeIf { it > 0 } ?: 300
            val h = opts.getInt(if (landscape) AppWidgetManager.OPTION_APPWIDGET_MIN_HEIGHT else AppWidgetManager.OPTION_APPWIDGET_MAX_HEIGHT, 0).takeIf { it > 0 } ?: 56
            val bmp = Render(ctx.resources.displayMetrics.density).draw(
                w, h, payload, p.getString(KEY_ERROR, null), p.getString(KEY_GIST, null) != null,
                System.currentTimeMillis() / 1000.0,
            )
            val views = RemoteViews(ctx.packageName, R.layout.widget)
            views.setImageViewBitmap(R.id.canvas, bmp)
            val tap = Intent(ctx, SeatWidget::class.java).setAction(ACTION_REFRESH)
            views.setOnClickPendingIntent(
                R.id.canvas,
                PendingIntent.getBroadcast(ctx, 0, tap, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT),
            )
            mgr.updateAppWidget(id, views)
        }
    }
}
