package com.clipzit.app.ui

import android.content.Context
import android.graphics.Bitmap
import android.media.MediaMetadataRetriever
import android.net.Uri
import android.util.LruCache
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.produceState
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.clipzit.app.media.Framing
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/** Small in-memory cache of video frames used as clip and project thumbnails. */
object Thumbnails {
    private val cache = LruCache<String, ImageBitmap>(48)

    suspend fun load(context: Context, uri: String, timeMs: Long): ImageBitmap? {
        val key = "$uri@${timeMs / 500}"
        cache.get(key)?.let { return it }
        val image: ImageBitmap? = withContext(Dispatchers.IO) {
            val r = MediaMetadataRetriever()
            try {
                r.setDataSource(context, Uri.parse(uri))
                val frame = r.getFrameAtTime(timeMs * 1000, MediaMetadataRetriever.OPTION_CLOSEST_SYNC)
                if (frame == null) {
                    null
                } else {
                    val w = 360
                    val h = (frame.height * (w.toFloat() / frame.width)).toInt().coerceAtLeast(1)
                    Bitmap.createScaledBitmap(frame, w, h, true).asImageBitmap()
                }
            } catch (e: Exception) {
                null
            } finally {
                r.release()
            }
        }
        if (image != null) cache.put(key, image)
        return image
    }
}

@Composable
fun Thumb(uri: String, timeMs: Long, modifier: Modifier = Modifier) {
    val ctx = LocalContext.current
    val image by produceState<ImageBitmap?>(null, uri, timeMs) { value = Thumbnails.load(ctx, uri, timeMs) }
    Box(modifier.background(ClipElevated)) {
        val img = image
        if (img != null) {
            Image(
                bitmap = img,
                contentDescription = null,
                contentScale = ContentScale.Crop,
                modifier = Modifier.fillMaxSize(),
            )
        }
    }
}

@Composable
fun ScoreRing(score: Int, diameter: Dp = 54.dp) {
    val color = when {
        score >= 80 -> ClipYellow
        score >= 60 -> ClipYellow2.copy(alpha = 0.8f)
        else -> ClipMuted
    }
    Box(Modifier.size(diameter), contentAlignment = Alignment.Center) {
        Canvas(Modifier.fillMaxSize()) {
            val stroke = diameter.toPx() * 0.11f
            val arcSize = Size(this.size.width - stroke, this.size.height - stroke)
            val topLeft = Offset(stroke / 2, stroke / 2)
            drawArc(ClipBorder, 0f, 360f, false, topLeft, arcSize, style = Stroke(stroke))
            drawArc(
                color, -90f, 360f * score.coerceIn(0, 100) / 100f, false, topLeft, arcSize,
                style = Stroke(stroke, cap = StrokeCap.Round),
            )
        }
        Text("$score", color = color, fontWeight = FontWeight.Bold, fontSize = (diameter.value * 0.32f).sp)
    }
}

/** Little rounded label, used for durations and "Top pick". */
@Composable
fun Pill(text: String, background: Color = Color.Black.copy(alpha = 0.65f), content: Color = Color.White) {
    Box(
        Modifier.clip(RoundedCornerShape(50)).background(background).padding(horizontal = 8.dp, vertical = 3.dp),
    ) {
        Text(text, color = content, fontSize = 11.sp, fontWeight = FontWeight.SemiBold)
    }
}

val signalLabels = listOf(
    "hook" to "Hook",
    "completeness" to "Complete story",
    "coherence" to "Stays on topic",
    "information" to "Information",
    "clarity" to "Clarity",
    "emotion" to "Emotion",
    "standalone" to "Standalone",
    "delivery" to "Delivery",
    "pacing" to "Pacing",
    "duration" to "Length fit",
)

fun parseSignals(raw: String): Map<String, Int> =
    raw.lines().mapNotNull {
        val i = it.indexOf(':')
        if (i <= 0) null else it.substring(0, i) to (it.substring(i + 1).trim().toIntOrNull() ?: return@mapNotNull null)
    }.toMap()

/** Per-signal breakdown of the content-quality score. Every bar comes from a real analysis signal. */
@Composable
fun SignalBars(signals: Map<String, Int>) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        signalLabels.forEach { (key, label) ->
            val v = signals[key] ?: return@forEach
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(label, modifier = Modifier.width(112.dp), style = MaterialTheme.typography.bodySmall, color = ClipMuted)
                LinearProgressIndicator(
                    progress = { v.coerceIn(0, 100) / 100f },
                    modifier = Modifier.weight(1f).height(6.dp).clip(RoundedCornerShape(3.dp)),
                    color = if (v >= 75) ClipYellow else if (v >= 50) ClipYellow2.copy(alpha = 0.7f) else ClipMuted,
                    trackColor = ClipBorder,
                )
                Text(
                    "$v", modifier = Modifier.width(34.dp).padding(start = 8.dp), fontSize = 12.sp,
                    fontWeight = FontWeight.SemiBold, textAlign = TextAlign.End,
                )
            }
        }
    }
}

/** Dims everything outside the chosen crop and shows an approximate caption preview inside it. */
@Composable
fun CropOverlay(videoRatio: Float, aspect: String, cropX: Float, captionPreset: String, showCaption: Boolean) {
    val target = Framing.ratio(aspect) ?: return
    BoxWithConstraints(Modifier.fillMaxSize()) {
        val w = maxWidth
        val h = maxHeight
        val cx = cropX.coerceIn(0f, 1f)
        val cropW: Dp
        val cropH: Dp
        val left: Dp
        val top: Dp
        if (target < videoRatio) {
            val f = target / videoRatio
            cropW = w * f
            cropH = h
            left = (w - cropW) * cx
            top = 0.dp
        } else {
            val f = videoRatio / target
            cropW = w
            cropH = h * f
            left = 0.dp
            top = (h - cropH) / 2
        }
        val scrim = Color.Black.copy(alpha = 0.62f)
        Box(Modifier.offset(0.dp, 0.dp).size(left, h).background(scrim))
        Box(Modifier.offset(left + cropW, 0.dp).size(w - left - cropW, h).background(scrim))
        Box(Modifier.offset(left, 0.dp).size(cropW, top).background(scrim))
        Box(Modifier.offset(left, top + cropH).size(cropW, h - top - cropH).background(scrim))
        Box(
            Modifier.offset(left, top).size(cropW, cropH)
                .clip(RoundedCornerShape(4.dp))
                .background(Color.Transparent),
        ) {
            Canvas(Modifier.fillMaxSize()) {
                drawRect(ClipYellow, style = Stroke(3f))
            }
            if (showCaption) {
                CaptionSample(captionPreset, Modifier.align(Alignment.BottomCenter).padding(bottom = cropH * 0.14f))
            }
        }
    }
}

@Composable
private fun CaptionSample(preset: String, modifier: Modifier) {
    val upper = preset == "bold"
    val boxed = preset == "clean"
    val small = preset == "minimal" || preset == "professional"
    val hl = if (preset == "minimal" || preset == "clean" || preset == "professional") Color.White else ClipYellow
    val text = if (upper) "THIS IS HOW" else "This is how"
    Row(
        modifier.then(if (boxed) Modifier.background(Color.Black.copy(alpha = 0.6f)).padding(horizontal = 6.dp, vertical = 2.dp) else Modifier),
        horizontalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        val parts = text.split(" ")
        parts.forEachIndexed { i, word ->
            Text(
                word,
                color = if (i == 1) hl else Color.White,
                fontSize = if (small) 11.sp else if (upper) 17.sp else 14.sp,
                fontWeight = if (small) FontWeight.Normal else FontWeight.ExtraBold,
            )
        }
    }
}
