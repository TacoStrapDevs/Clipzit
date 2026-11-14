package com.clipzit.app.ui

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import com.clipzit.app.AppViewModel
import com.clipzit.app.data.ClipEntity
import com.clipzit.app.data.ProjectEntity
import com.clipzit.app.media.formatMs
import com.clipzit.app.media.safeFileName

// ---------------------------------------------------------------------------- Home

@Composable
fun HomeScreen(vm: AppViewModel, modifier: Modifier = Modifier) {
    val projects by vm.projects.collectAsState()
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.OpenMultipleDocuments()) { uris ->
        vm.importVideos(uris)
    }
    LazyColumn(
        modifier = modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Logo(44)
                Spacer(Modifier.width(12.dp))
                Column {
                    Text("Clipzit", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
                    Text("Long videos into short clips", color = ClipMuted, style = MaterialTheme.typography.bodySmall)
                }
            }
        }
        item {
            Button(
                onClick = { picker.launch(arrayOf("video/*")) },
                modifier = Modifier.fillMaxWidth().height(54.dp),
                shape = RoundedCornerShape(14.dp),
            ) {
                Icon(Icons.Default.Add, contentDescription = null)
                Spacer(Modifier.width(8.dp))
                Text("Import videos", fontWeight = FontWeight.SemiBold)
            }
        }
        item { LinkImportSection(vm) }
        item {
            val url = vm.prefs.backendUrl
            CardBox {
                Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text("AI clip discovery", fontWeight = FontWeight.SemiBold)
                    Text(
                        if (url.isBlank()) {
                            "No backend configured. Manual clipping, trimming, reframing and export work on this device. " +
                                "Add a self-hosted backend in Settings to find clips with AI."
                        } else {
                            "Runs on your backend: $url (transcription, scoring and captioned rendering happen there)."
                        },
                        color = ClipMuted,
                        style = MaterialTheme.typography.bodySmall,
                    )
                }
            }
        }
        if (projects.isEmpty()) {
            item {
                CardBox {
                    Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                        Text("Your first clips in 3 steps", fontWeight = FontWeight.Bold)
                        Step("1", "Import a long video (podcast, stream, interview, tutorial).")
                        Step("2", "Analyse it. Clipzit finds the strongest self-contained moments and shows why.")
                        Step("3", "Pick a hook, set the framing and captions, then export or share.")
                    }
                }
            }
        } else {
            item { Text("${projects.size} project(s)", color = ClipMuted, style = MaterialTheme.typography.labelLarge) }
            items(projects, key = { it.id }) { p ->
                ProjectCard(p, onOpen = { vm.open(p.id) }, onDelete = { vm.deleteProject(p) })
            }
        }
    }
}

@Composable
private fun ProjectCard(project: ProjectEntity, onOpen: () -> Unit, onDelete: () -> Unit) {
    var confirm by remember { mutableStateOf(false) }
    CardBox(Modifier.clickable(onClick = onOpen)) {
        Row(Modifier.padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(width = 104.dp, height = 66.dp)) {
                Thumb(
                    project.sourceUri, project.durationMs / 10,
                    Modifier.fillMaxSize().clip(RoundedCornerShape(10.dp)),
                )
                Box(Modifier.align(Alignment.BottomEnd).padding(4.dp)) { Pill(formatMs(project.durationMs)) }
            }
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(project.name, fontWeight = FontWeight.SemiBold, maxLines = 2)
                Text(
                    "${project.width}x${project.height}",
                    color = ClipMuted,
                    style = MaterialTheme.typography.bodySmall,
                )
                if (project.jobId != null) Pill("Analysed", ClipYellow, Color.Black)
            }
            IconButton(onClick = { confirm = true }) {
                Icon(Icons.Default.Delete, contentDescription = "Delete project", tint = ClipMuted)
            }
        }
    }
    if (confirm) {
        AlertDialog(
            onDismissRequest = { confirm = false },
            title = { Text("Delete project?") },
            text = { Text("This removes the project and its clips from Clipzit. Your original video is not deleted.") },
            confirmButton = { TextButton(onClick = { confirm = false; onDelete() }) { Text("Delete") } },
            dismissButton = { TextButton(onClick = { confirm = false }) { Text("Cancel") } },
        )
    }
}

// ---------------------------------------------------------------------------- Project

private val presets = listOf("15-30" to "15–30s", "30-45" to "30–45s", "45-60" to "45–60s", "60-90" to "60–90s")

@Composable
fun ProjectScreen(vm: AppViewModel, projectId: Long, modifier: Modifier = Modifier) {
    val projects by vm.projects.collectAsState()
    val project = projects.firstOrNull { it.id == projectId }
    val clips by remember(projectId) { vm.clipsFor(projectId) }.collectAsState(initial = emptyList())
    var preset by remember { mutableStateOf(vm.lastPreset) }
    var query by remember { mutableStateOf("") }
    var selected by remember { mutableStateOf(setOf<Long>()) }
    val analysis = vm.analysis?.takeIf { it.projectId == projectId }
    val ctx = LocalContext.current
    val notifications = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { }

    LaunchedEffect(project?.id) {
        if (project != null && clips.isEmpty()) vm.resumeAnalysis(project)
    }

    if (project == null) {
        Column(modifier.fillMaxSize().padding(16.dp)) {
            Text("This project no longer exists.")
            TextButton(onClick = { vm.goHome() }) { Text("Back") }
        }
        return
    }
    val backendClips = clips.filter { it.source == "backend" }

    LazyColumn(
        modifier = modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            Row(verticalAlignment = Alignment.CenterVertically) {
                IconButton(onClick = { vm.goHome() }) { Icon(Icons.Default.ArrowBack, contentDescription = "Back") }
                Thumb(project.sourceUri, project.durationMs / 10, Modifier.size(width = 112.dp, height = 64.dp).clip(RoundedCornerShape(10.dp)))
                Spacer(Modifier.width(12.dp))
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    Text(project.name, style = MaterialTheme.typography.titleMedium, maxLines = 2, fontWeight = FontWeight.Bold)
                    Text(
                        "${formatMs(project.durationMs)} · ${project.width}x${project.height}",
                        color = ClipMuted,
                        style = MaterialTheme.typography.bodySmall,
                    )
                    if (backendClips.isNotEmpty()) {
                        Text(
                            "${backendClips.size} AI clips · best ${backendClips.maxOf { it.score }}",
                            color = ClipYellow,
                            style = MaterialTheme.typography.labelMedium,
                        )
                    }
                }
            }
        }
        item {
            CardBox {
                Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text("Find clips with AI", fontWeight = FontWeight.SemiBold)
                    val url = vm.prefs.backendUrl
                    Text(
                        if (url.isBlank()) "Needs a backend. Set its URL in Settings." else "Processing runs on: $url",
                        color = ClipMuted,
                        style = MaterialTheme.typography.bodySmall,
                    )
                    ChipRow(presets.map { it.first }, preset, label = { k -> presets.first { it.first == k }.second }) {
                        preset = it
                    }
                    if (analysis == null) {
                        Button(
                            onClick = {
                                if (Build.VERSION.SDK_INT >= 33 &&
                                    ContextCompat.checkSelfPermission(ctx, Manifest.permission.POST_NOTIFICATIONS) !=
                                    PackageManager.PERMISSION_GRANTED
                                ) {
                                    notifications.launch(Manifest.permission.POST_NOTIFICATIONS)
                                }
                                vm.startAnalysis(project, preset)
                            },
                            enabled = vm.backendConfigured(),
                            modifier = Modifier.fillMaxWidth(),
                        ) { Text(if (backendClips.isNotEmpty()) "Regenerate suggestions" else "Analyse with backend") }
                        Text(
                            "Uploads resume if the connection drops, and you get a notification when clips are ready.",
                            color = ClipMuted,
                            style = MaterialTheme.typography.labelSmall,
                        )
                    } else {
                        Text(analysis.phase, fontWeight = FontWeight.Medium)
                        if (analysis.running) {
                            LinearProgressIndicator(progress = { analysis.progress }, modifier = Modifier.fillMaxWidth())
                        }
                        analysis.error?.let { Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall) }
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            if (analysis.running) {
                                OutlinedButton(onClick = { vm.cancelAnalysis() }) { Text("Cancel") }
                            } else {
                                Button(onClick = { vm.retryAnalysis(project) }) {
                                    Text(if (analysis.jobId == null) "Resume" else "Retry")
                                }
                                OutlinedButton(onClick = { vm.dismissAnalysis() }) { Text("Dismiss") }
                            }
                        }
                    }
                }
            }
        }
        if (project.jobId != null) {
            item {
                CardBox {
                    Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text("Search the transcript", fontWeight = FontWeight.SemiBold)
                        OutlinedTextField(
                            value = query,
                            onValueChange = { query = it },
                            label = { Text("Word or phrase") },
                            singleLine = true,
                            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
                            keyboardActions = KeyboardActions(onSearch = { vm.searchTranscript(project, query) }),
                            modifier = Modifier.fillMaxWidth(),
                        )
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            Button(onClick = { vm.searchTranscript(project, query) }) { Text("Search") }
                            if (vm.searchHits.isNotEmpty()) {
                                OutlinedButton(onClick = { vm.clearSearch() }) { Text("Clear") }
                            }
                        }
                        vm.searchHits.take(20).forEach { hit ->
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Column(Modifier.weight(1f)) {
                                    Text(formatMs((hit.startSec * 1000).toLong()), color = ClipYellow, style = MaterialTheme.typography.labelMedium)
                                    Text(hit.text, style = MaterialTheme.typography.bodySmall, maxLines = 3)
                                }
                                TextButton(onClick = { vm.addManualClip(project, (hit.startSec * 1000).toLong()) }) {
                                    Text("Clip from here")
                                }
                            }
                        }
                    }
                }
            }
        }
        item {
            OutlinedButton(onClick = { vm.addManualClip(project) }, modifier = Modifier.fillMaxWidth()) {
                Icon(Icons.Default.Add, contentDescription = null)
                Spacer(Modifier.width(8.dp))
                Text("Add manual clip")
            }
        }
        if (clips.isEmpty()) {
            item { Text("No clips yet.", color = ClipMuted) }
        } else {
            item {
                Text(
                    "${clips.size} clip(s)" + if (backendClips.isNotEmpty()) " · rings show an estimated content-quality score, not a view prediction" else "",
                    color = ClipMuted,
                    style = MaterialTheme.typography.labelLarge,
                )
            }
            items(clips, key = { it.id }) { c ->
                ClipItem(
                    clip = c,
                    sourceUri = project.sourceUri,
                    topPick = c.source == "backend" && c.score >= 75 && c.id == backendClips.firstOrNull()?.id,
                    selected = c.id in selected,
                    onToggle = { selected = if (c.id in selected) selected - c.id else selected + c.id },
                    onOpen = { vm.openEditor(project.id, c.id) },
                    onDuplicate = { vm.duplicateClip(c) },
                    onDelete = { vm.deleteClip(c) },
                )
            }
        }
        if (backendClips.isNotEmpty()) {
            item {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    RenderOptionsPanel(vm)
                    val picked = backendClips.filter { it.id in selected }
                    Button(
                        onClick = { vm.exportZip(project, picked.ifEmpty { backendClips }) },
                        enabled = vm.export?.running != true && vm.backendConfigured(),
                        modifier = Modifier.fillMaxWidth(),
                    ) { Text("Export ${if (picked.isEmpty()) "all" else picked.size.toString()} as ZIP (MP4 + SRT + metadata)") }
                    ExportStatus(vm, saveName = safeFileName(project.name) + "_clips.zip", mime = "application/zip")
                }
            }
        }
    }
}

@Composable
private fun ClipItem(
    clip: ClipEntity,
    sourceUri: String,
    topPick: Boolean,
    selected: Boolean,
    onToggle: () -> Unit,
    onOpen: () -> Unit,
    onDuplicate: () -> Unit,
    onDelete: () -> Unit,
) {
    CardBox(Modifier.clickable(onClick = onOpen)) {
        Row(Modifier.padding(12.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Box(Modifier.size(width = 98.dp, height = 156.dp)) {
                Thumb(sourceUri, clip.startMs + 800, Modifier.fillMaxSize().clip(RoundedCornerShape(12.dp)))
                Box(Modifier.align(Alignment.BottomStart).padding(6.dp)) {
                    Pill("${(clip.endMs - clip.startMs) / 1000}s")
                }
                if (topPick) {
                    Box(Modifier.align(Alignment.TopStart).padding(6.dp)) { Pill("TOP PICK", ClipYellow, Color.Black) }
                }
            }
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Row(verticalAlignment = Alignment.Top) {
                    Text(
                        clip.title,
                        fontWeight = FontWeight.SemiBold,
                        modifier = Modifier.weight(1f).padding(end = 8.dp),
                        maxLines = 3,
                    )
                    if (clip.source == "backend") ScoreRing(clip.score, 50.dp)
                }
                Text(
                    "${formatMs(clip.startMs)} – ${formatMs(clip.endMs)}",
                    color = ClipMuted,
                    style = MaterialTheme.typography.bodySmall,
                )
                if (clip.source == "backend") {
                    clip.reasons.lines().filter { it.isNotBlank() }.take(2).forEach {
                        Text("• $it", style = MaterialTheme.typography.bodySmall, maxLines = 2)
                    }
                }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    if (clip.source == "backend") {
                        androidx.compose.material3.Checkbox(checked = selected, onCheckedChange = { onToggle() })
                    }
                    TextButton(onClick = onOpen) { Text("Edit") }
                    TextButton(onClick = onDuplicate) { Text("Copy") }
                    IconButton(onClick = onDelete) {
                        Icon(Icons.Default.Delete, contentDescription = "Delete clip", tint = ClipMuted)
                    }
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------- Settings

private val qualityLevels = listOf("performance", "balanced", "quality")

@Composable
fun SettingsScreen(vm: AppViewModel, modifier: Modifier = Modifier) {
    var url by remember { mutableStateOf(vm.prefs.backendUrl) }
    var token by remember { mutableStateOf(vm.prefs.apiToken) }
    var quality by remember { mutableStateOf(vm.prefs.quality) }
    var language by remember { mutableStateOf(vm.prefs.language) }
    Column(
        modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Logo(36)
            Spacer(Modifier.width(10.dp))
            Text("Settings", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
        }
        CardBox {
            Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text("Self-hosted backend", fontWeight = FontWeight.SemiBold)
                Text(
                    "Clip discovery and captioned rendering run on your own Clipzit backend (see the README). " +
                        "Use HTTPS when the server is reachable over the internet.",
                    color = ClipMuted,
                    style = MaterialTheme.typography.bodySmall,
                )
                OutlinedTextField(
                    value = url,
                    onValueChange = { url = it },
                    label = { Text("Backend URL, e.g. http://192.168.1.20:8000") },
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value = token,
                    onValueChange = { token = it },
                    label = { Text("API token (optional)") },
                    singleLine = true,
                    visualTransformation = PasswordVisualTransformation(),
                    modifier = Modifier.fillMaxWidth(),
                )
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(onClick = {
                        vm.prefs.backendUrl = url
                        vm.prefs.apiToken = token
                        vm.message = "Saved"
                    }) { Text("Save") }
                    OutlinedButton(onClick = {
                        vm.prefs.backendUrl = url
                        vm.prefs.apiToken = token
                        vm.testBackend()
                    }) { Text("Test connection") }
                }
            }
        }
        CardBox {
            Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Transcription quality", fontWeight = FontWeight.SemiBold)
                ChipRow(qualityLevels, quality) { quality = it; vm.prefs.quality = it }
                Text(
                    "Performance = fastest model, Balanced = default, Quality = most accurate but slower. " +
                        "The model runs on your backend (CPU) and is downloaded there on first use.",
                    color = ClipMuted,
                    style = MaterialTheme.typography.bodySmall,
                )
                OutlinedTextField(
                    value = language,
                    onValueChange = { language = it; vm.prefs.language = it },
                    label = { Text("Spoken language code (empty = auto-detect, e.g. en, es, de)") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        }
        RenderOptionsPanel(vm)
        CardBox {
            Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text("Storage and privacy", fontWeight = FontWeight.SemiBold)
                Text(
                    "Exports are staged in temporary app storage until you save or share them. Videos are only sent " +
                        "to the backend you configure, and only when you tap Analyse.",
                    color = ClipMuted,
                    style = MaterialTheme.typography.bodySmall,
                )
                OutlinedButton(onClick = { vm.clearExportCache() }) { Text("Clear temporary exports") }
            }
        }
        CardBox {
            Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text("Help", fontWeight = FontWeight.SemiBold)
                Text(
                    "1. Import a video.  2. Choose a length and tap Analyse (needs a backend), or add a manual clip.  " +
                        "3. Open a clip to trim, reframe, read why it was chosen, and pick a hook.  " +
                        "4. Export on the device, or render captions on the backend, then save or share.",
                    color = ClipMuted,
                    style = MaterialTheme.typography.bodySmall,
                )
                Text("Clipzit 1.1.0", color = ClipMuted, style = MaterialTheme.typography.labelSmall)
            }
        }
    }
}
