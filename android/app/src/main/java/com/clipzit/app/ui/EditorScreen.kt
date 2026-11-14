package com.clipzit.app.ui

import android.net.Uri
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RangeSlider
import androidx.compose.material3.Slider
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.State
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.ui.platform.LocalContext
import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.ui.PlayerView
import com.clipzit.app.AppViewModel
import com.clipzit.app.data.ClipEntity
import com.clipzit.app.media.EditHistory
import com.clipzit.app.media.EditState
import com.clipzit.app.media.Framing
import com.clipzit.app.media.formatMs
import com.clipzit.app.media.platformLimitSeconds
import com.clipzit.app.media.safeFileName
import com.clipzit.app.net.Alternative
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

private const val MIN_CLIP_MS = 500L

@Composable
private fun PositionLabel(position: State<Long>, durationMs: Long) {
    Text(
        "${formatMs(position.value)} / ${formatMs(durationMs)}",
        color = ClipMuted,
        style = MaterialTheme.typography.bodySmall,
    )
}

@Composable
private fun Timeline(
    durationMs: Long,
    startMs: Long,
    endMs: Long,
    position: State<Long>,
    onSeek: (Long) -> Unit,
) {
    val d = maxOf(durationMs, 1L).toFloat()
    Canvas(
        Modifier
            .fillMaxWidth()
            .height(44.dp)
            .clip(RoundedCornerShape(10.dp))
            .background(ClipElevated)
            .pointerInput(durationMs) {
                detectTapGestures { offset ->
                    onSeek(((offset.x / size.width) * d).toLong().coerceIn(0L, durationMs))
                }
            },
    ) {
        val x0 = size.width * (startMs / d)
        val x1 = size.width * (endMs / d)
        drawRect(ClipYellow.copy(alpha = 0.35f), Offset(x0, 0f), Size(maxOf(x1 - x0, 2f), size.height))
        drawRect(ClipYellow, Offset(x0, 0f), Size(3f, size.height))
        drawRect(ClipYellow, Offset(maxOf(x1 - 3f, 0f), 0f), Size(3f, size.height))
        val px = size.width * (position.value / d)
        drawRect(Color.White, Offset(px, 0f), Size(2f, size.height))
    }
}

@OptIn(ExperimentalMaterial3Api::class, UnstableApi::class)
@Composable
fun EditorScreen(vm: AppViewModel, projectId: Long, clipId: Long, modifier: Modifier = Modifier) {
    val projects by vm.projects.collectAsState()
    val project = projects.firstOrNull { it.id == projectId }
    var loaded by remember { mutableStateOf<ClipEntity?>(null) }
    var missing by remember { mutableStateOf(false) }

    LaunchedEffect(clipId) {
        val c = vm.clip(clipId)
        if (c == null) missing = true else loaded = c
    }
    val clip = loaded
    if (project == null || missing) {
        Column(modifier.fillMaxSize().padding(16.dp)) {
            Text("This clip no longer exists.")
            TextButton(onClick = { vm.back() }) { Text("Back") }
        }
        return
    }
    if (clip == null) return

    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    val clipboard = LocalClipboardManager.current
    val duration = project.durationMs

    val initial = EditState(
        clip.startMs.coerceIn(0, duration),
        clip.endMs.coerceIn(MIN_CLIP_MS, duration),
        clip.aspect,
        clip.cropX,
    )
    val history = remember { EditHistory(initial) }
    var tick by remember { mutableIntStateOf(0) }
    var startMs by remember { mutableLongStateOf(initial.startMs) }
    var endMs by remember { mutableLongStateOf(initial.endMs) }
    var aspect by remember { mutableStateOf(initial.aspect) }
    var cropX by remember { mutableFloatStateOf(initial.cropX) }
    var title by remember { mutableStateOf(clip.title) }
    var hook by remember { mutableStateOf(clip.hook) }
    // Only the timeline and the time label read this, so playback does not recompose the whole editor.
    val position = remember { mutableLongStateOf(0L) }
    var playing by remember { mutableStateOf(false) }
    var playerError by remember { mutableStateOf<String?>(null) }
    var alternatives by remember { mutableStateOf<List<Alternative>>(emptyList()) }
    var altMessage by remember { mutableStateOf<String?>(null) }
    var platform by remember { mutableStateOf(vm.prefs.platform) }

    val startState = rememberUpdatedState(startMs)
    val endState = rememberUpdatedState(endMs)

    val player = remember {
        ExoPlayer.Builder(ctx).build().apply {
            setMediaItem(MediaItem.fromUri(Uri.parse(project.sourceUri)))
            prepare()
            seekTo(initial.startMs)
        }
    }
    DisposableEffect(player) {
        val listener = object : Player.Listener {
            override fun onIsPlayingChanged(isPlaying: Boolean) {
                playing = isPlaying
            }

            override fun onPlayerError(error: PlaybackException) {
                playerError = "Cannot play this video: ${error.message}. The file may have been moved or deleted."
            }
        }
        player.addListener(listener)
        onDispose {
            player.removeListener(listener)
            player.release()
        }
    }
    // Keep playback inside the selected range.
    LaunchedEffect(player) {
        while (true) {
            delay(80)
            val now = player.currentPosition
            if (kotlin.math.abs(now - position.longValue) >= 40) position.longValue = now
            if (player.isPlaying && player.currentPosition >= endState.value) {
                player.pause()
                player.seekTo(startState.value)
            }
        }
    }

    fun current(): ClipEntity = clip.copy(
        startMs = startMs,
        endMs = endMs,
        aspect = aspect,
        cropX = cropX,
        title = title.ifBlank { "Clip" },
        hook = hook,
    )

    fun commit() {
        history.push(EditState(startMs, endMs, aspect, cropX))
        tick++
    }

    fun applyState(s: EditState) {
        startMs = s.startMs
        endMs = s.endMs
        aspect = s.aspect
        cropX = s.cropX
        player.seekTo(s.startMs)
        tick++
    }

    // Auto-save a moment after the last change so work is never lost.
    LaunchedEffect(startMs, endMs, aspect, cropX, title, hook) {
        delay(900)
        vm.saveClip(current())
    }

    Column(
        modifier.fillMaxSize().verticalScroll(rememberScrollState()).imePadding().padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = { vm.saveClip(current()); vm.back() }) {
                Icon(Icons.Default.ArrowBack, contentDescription = "Save and go back")
            }
            Text(
                "Edit clip",
                style = MaterialTheme.typography.titleLarge,
                fontWeight = FontWeight.Bold,
                modifier = Modifier.weight(1f),
            )
            TextButton(onClick = { history.undo()?.let { applyState(it) } }, enabled = tick >= 0 && history.canUndo) {
                Text("Undo")
            }
            TextButton(onClick = { history.redo()?.let { applyState(it) } }, enabled = tick >= 0 && history.canRedo) {
                Text("Redo")
            }
        }

        val videoRatio = (project.width.toFloat() / project.height.toFloat()).coerceIn(0.4f, 3f)
        Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
            val frame = if (videoRatio >= 1f) {
                Modifier.fillMaxWidth().aspectRatio(videoRatio)
            } else {
                Modifier.height(380.dp).aspectRatio(videoRatio)
            }
            Box(frame.clip(RoundedCornerShape(14.dp)).background(Color.Black)) {
                AndroidView(
                    factory = { c ->
                        val exo = player
                        PlayerView(c).apply {
                            useController = false
                            this.player = exo
                        }
                    },
                    modifier = Modifier.fillMaxSize(),
                )
                CropOverlay(videoRatio, aspect, cropX, vm.prefs.captionPreset, vm.prefs.captionsOn)
            }
        }
        if (aspect != "Original") {
            Text(
                "Yellow frame = what gets exported. Caption preview is approximate.",
                color = ClipMuted,
                style = MaterialTheme.typography.labelSmall,
            )
        }
        playerError?.let { Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall) }

        Timeline(duration, startMs, endMs, position) { player.seekTo(it) }

        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = {
                if (playing) {
                    player.pause()
                } else {
                    if (player.currentPosition < startMs || player.currentPosition >= endMs) player.seekTo(startMs)
                    player.play()
                }
            }) { Text(if (playing) "Pause" else "Play clip") }
            PositionLabel(position, duration)
        }

        Text(
            "Trim: ${formatMs(startMs)} → ${formatMs(endMs)}  (${(endMs - startMs) / 1000.0}s)",
            fontWeight = FontWeight.Medium,
        )
        val limit = platformLimitSeconds(platform)
        if (limit != null && (endMs - startMs) / 1000.0 > limit) {
            Text(
                "Longer than the $limit s that $platform allows. Trim it or choose another platform.",
                color = MaterialTheme.colorScheme.error,
                style = MaterialTheme.typography.bodySmall,
            )
        }
        RangeSlider(
            value = startMs.toFloat()..endMs.toFloat(),
            onValueChange = { r ->
                val s = r.start.toLong()
                val e = r.endInclusive.toLong()
                if (e - s >= MIN_CLIP_MS) {
                    if (s != startMs) player.seekTo(s) else if (e != endMs) player.seekTo(maxOf(s, e - 1500))
                    startMs = s
                    endMs = e
                }
            },
            onValueChangeFinished = { commit() },
            valueRange = 0f..duration.toFloat(),
        )
        Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(onClick = {
                startMs = (startMs - 100).coerceAtLeast(0); player.seekTo(startMs); commit()
            }) { Text("Start −0.1s") }
            OutlinedButton(onClick = {
                if (endMs - (startMs + 100) >= MIN_CLIP_MS) { startMs += 100; player.seekTo(startMs); commit() }
            }) { Text("Start +0.1s") }
            OutlinedButton(onClick = {
                if ((endMs - 100) - startMs >= MIN_CLIP_MS) { endMs -= 100; commit() }
            }) { Text("End −0.1s") }
            OutlinedButton(onClick = { endMs = (endMs + 100).coerceAtMost(duration); commit() }) { Text("End +0.1s") }
        }
        Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(onClick = { startMs = position.longValue.coerceIn(0, endMs - MIN_CLIP_MS); commit() }) {
                Text("Set start to playhead")
            }
            OutlinedButton(onClick = { endMs = position.longValue.coerceIn(startMs + MIN_CLIP_MS, duration); commit() }) {
                Text("Set end to playhead")
            }
            OutlinedButton(onClick = {
                val at = position.longValue
                vm.splitClip(current(), at)
                if (at >= startMs + MIN_CLIP_MS && at <= endMs - MIN_CLIP_MS) { endMs = at; commit() }
            }) { Text("Split at playhead") }
            OutlinedButton(onClick = { vm.duplicateClip(current()) }) { Text("Duplicate") }
        }

        Text("Frame", fontWeight = FontWeight.Medium)
        ChipRow(Framing.aspects, aspect) { aspect = it; commit() }
        if (aspect != "Original") {
            Text("Horizontal position of the crop", color = ClipMuted, style = MaterialTheme.typography.bodySmall)
            Slider(value = cropX, onValueChange = { cropX = it }, onValueChangeFinished = { commit() })
            Text(
                "On-device export is centred by this slider. For automatic face following, enable it in the " +
                    "backend render options below.",
                color = ClipMuted,
                style = MaterialTheme.typography.labelSmall,
            )
        }

        OutlinedTextField(
            value = title, onValueChange = { title = it }, label = { Text("Title") },
            singleLine = true, modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(
            value = hook, onValueChange = { hook = it }, label = { Text("Hook (shown over the opening if enabled)") },
            modifier = Modifier.fillMaxWidth(),
        )

        if (clip.source == "backend") {
            CardBox {
                Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("Assistant", fontWeight = FontWeight.SemiBold)
                    Text(
                        "Score ${clip.score}/100 is an estimated content-quality indicator from the transcript and " +
                            "audio, not a prediction of views.",
                        color = ClipMuted,
                        style = MaterialTheme.typography.bodySmall,
                    )
                    val sig = parseSignals(clip.signals)
                    if (sig.isNotEmpty()) {
                        Text("Score breakdown", style = MaterialTheme.typography.labelLarge)
                        SignalBars(sig)
                    }
                    val reasons = clip.reasons.lines().filter { it.isNotBlank() }
                    if (reasons.isNotEmpty()) {
                        Text("Why this clip", style = MaterialTheme.typography.labelLarge)
                        reasons.forEach { Text("• $it", style = MaterialTheme.typography.bodySmall) }
                    }
                    val tips = clip.suggestions.lines().filter { it.isNotBlank() }
                    if (tips.isNotEmpty()) {
                        Text("To improve it", style = MaterialTheme.typography.labelLarge)
                        tips.forEach { Text("• $it", style = MaterialTheme.typography.bodySmall) }
                    }
                    val hooks = clip.hooks.lines().filter { it.isNotBlank() }
                    if (hooks.isNotEmpty()) {
                        Text("Hook options (verbatim from the video)", style = MaterialTheme.typography.labelLarge)
                        hooks.forEach { h ->
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Text(h, modifier = Modifier.weight(1f), style = MaterialTheme.typography.bodySmall)
                                TextButton(onClick = { hook = h }) { Text("Use hook") }
                                TextButton(onClick = { title = h.take(70) }) { Text("As title") }
                            }
                        }
                    }
                    if (clip.hashtags.isNotBlank()) {
                        Text("Hashtags", style = MaterialTheme.typography.labelLarge)
                        Text(clip.hashtags, style = MaterialTheme.typography.bodySmall)
                        TextButton(onClick = {
                            clipboard.setText(AnnotatedString(clip.hashtags))
                            vm.message = "Hashtags copied"
                        }) { Text("Copy hashtags") }
                    }
                    if (clip.description.isNotBlank()) {
                        Text("Description (excerpt)", style = MaterialTheme.typography.labelLarge)
                        Text(clip.description, style = MaterialTheme.typography.bodySmall)
                        TextButton(onClick = {
                            clipboard.setText(AnnotatedString(clip.description))
                            vm.message = "Description copied"
                        }) { Text("Copy description") }
                    }
                    OutlinedButton(onClick = {
                        scope.launch {
                            altMessage = "Looking for better boundaries…"
                            try {
                                alternatives = vm.alternatives(current())
                                altMessage = if (alternatives.isEmpty()) "No alternatives found." else null
                            } catch (e: Exception) {
                                altMessage = e.message ?: "Could not load suggestions"
                            }
                        }
                    }) { Text("Suggest better start/end") }
                    altMessage?.let { Text(it, color = ClipMuted, style = MaterialTheme.typography.bodySmall) }
                    alternatives.forEach { a ->
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f)) {
                                Text(
                                    "${formatMs((a.startSec * 1000).toLong())} – ${formatMs((a.endSec * 1000).toLong())}" +
                                        "  ·  ${a.score}" + (if (a.delta != 0) " (${if (a.delta > 0) "+" else ""}${a.delta})" else "") +
                                        (if (a.current) "  · current" else ""),
                                    style = MaterialTheme.typography.bodySmall,
                                )
                                a.reasons.firstOrNull()?.let {
                                    Text(it, color = ClipMuted, style = MaterialTheme.typography.labelSmall)
                                }
                            }
                            TextButton(onClick = {
                                startMs = (a.startSec * 1000).toLong().coerceIn(0, duration)
                                endMs = (a.endSec * 1000).toLong().coerceIn(startMs + MIN_CLIP_MS, duration)
                                player.seekTo(startMs)
                                commit()
                            }) { Text("Use") }
                        }
                    }
                }
            }
            if (clip.transcript.isNotBlank()) {
                Text("Transcript", fontWeight = FontWeight.Medium)
                Text(clip.transcript, color = ClipMuted, style = MaterialTheme.typography.bodySmall)
            }
            RenderOptionsPanel(vm) { p ->
                platform = p
                if (p != "custom") { aspect = "9:16"; commit() }
            }
        }

        Button(onClick = { vm.saveClip(current()); vm.message = "Saved" }, modifier = Modifier.fillMaxWidth()) {
            Text("Save changes")
        }

        val export = vm.export
        Text("Export", fontWeight = FontWeight.Medium)
        OutlinedButton(
            onClick = { val c = current(); vm.saveClip(c); vm.exportLocal(project, c) },
            enabled = export?.running != true,
            modifier = Modifier.fillMaxWidth(),
        ) { Text("Export MP4 on this device") }
        if (clip.jobId != null && clip.clipIndex >= 0) {
            OutlinedButton(
                onClick = { val c = current(); vm.saveClip(c); vm.exportWithCaptions(project, c) },
                enabled = export?.running != true && vm.backendConfigured(),
                modifier = Modifier.fillMaxWidth(),
            ) { Text("Export with captions & options (backend)") }
        }
        ExportStatus(vm, saveName = safeFileName(title) + ".mp4", mime = "video/mp4")
    }
}
