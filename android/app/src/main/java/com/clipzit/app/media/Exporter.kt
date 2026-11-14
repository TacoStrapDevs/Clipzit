package com.clipzit.app.media

import android.content.Context
import android.media.MediaMetadataRetriever
import android.net.Uri
import android.os.Handler
import android.os.Looper
import androidx.media3.common.Effect
import androidx.media3.common.MediaItem
import androidx.media3.common.MimeTypes
import androidx.media3.common.util.UnstableApi
import androidx.media3.effect.Crop
import androidx.media3.effect.Presentation
import androidx.media3.transformer.Composition
import androidx.media3.transformer.EditedMediaItem
import androidx.media3.transformer.Effects
import androidx.media3.transformer.ExportException
import androidx.media3.transformer.ExportResult
import androidx.media3.transformer.ProgressHolder
import androidx.media3.transformer.Transformer
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import java.io.File
import java.io.IOException
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/** On-device trim + reframe export using Media3 Transformer (H.264 / AAC MP4). */
@OptIn(UnstableApi::class)
class Exporter(private val context: Context) {

    suspend fun export(
        sourceUri: Uri,
        startMs: Long,
        endMs: Long,
        crop: Framing.Crop?,
        outFile: File,
        onProgress: (Float) -> Unit,
    ) = withContext(Dispatchers.Main) {
        require(endMs > startMs) { "End must be after start" }
        outFile.parentFile?.mkdirs()
        if (outFile.exists()) outFile.delete()

        val clipping = MediaItem.ClippingConfiguration.Builder()
            .setStartPositionMs(startMs)
            .setEndPositionMs(endMs)
            .build()
        val item = MediaItem.Builder().setUri(sourceUri).setClippingConfiguration(clipping).build()
        val videoEffects = mutableListOf<Effect>()
        if (crop != null) {
            videoEffects += Crop(crop.left, crop.right, crop.bottom, crop.top)
            videoEffects += Presentation.createForWidthAndHeight(
                crop.outWidth,
                crop.outHeight,
                Presentation.LAYOUT_STRETCH_TO_FIT,
            )
        }
        val edited = EditedMediaItem.Builder(item)
            .setEffects(Effects(emptyList(), videoEffects))
            .build()

        suspendCancellableCoroutine<Unit> { cont ->
            val handler = Handler(Looper.getMainLooper())
            val transformer = Transformer.Builder(context)
                .setVideoMimeType(MimeTypes.VIDEO_H264)
                .setAudioMimeType(MimeTypes.AUDIO_AAC)
                .addListener(object : Transformer.Listener {
                    override fun onCompleted(composition: Composition, exportResult: ExportResult) {
                        if (cont.isActive) cont.resume(Unit)
                    }

                    override fun onError(
                        composition: Composition,
                        exportResult: ExportResult,
                        exportException: ExportException,
                    ) {
                        if (cont.isActive) cont.resumeWithException(exportException)
                    }
                })
                .build()
            cont.invokeOnCancellation { handler.post { transformer.cancel() } }
            transformer.start(edited, outFile.absolutePath)

            val holder = ProgressHolder()
            val poll = object : Runnable {
                override fun run() {
                    if (!cont.isActive) return
                    if (transformer.getProgress(holder) != Transformer.PROGRESS_STATE_NOT_STARTED) {
                        onProgress(holder.progress / 100f)
                    }
                    handler.postDelayed(this, 300)
                }
            }
            handler.post(poll)
        }

        // Never report success until the file exists and can be re-opened with a sensible duration.
        withContext(Dispatchers.IO) { validate(outFile, endMs - startMs) }
    }

    private fun validate(file: File, expectedMs: Long) {
        if (!file.exists() || file.length() == 0L) throw IOException("Export produced no file")
        val r = MediaMetadataRetriever()
        try {
            r.setDataSource(file.absolutePath)
            val dur = r.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)?.toLongOrNull()
                ?: throw IOException("Exported file could not be read")
            if (dur < expectedMs * 0.8 || dur > expectedMs * 1.2 + 500) {
                throw IOException("Exported duration ${dur}ms does not match the requested ${expectedMs}ms")
            }
        } finally {
            r.release()
        }
    }
}
