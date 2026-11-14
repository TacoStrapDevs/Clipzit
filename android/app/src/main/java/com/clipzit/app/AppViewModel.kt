package com.clipzit.app

import android.app.Application
import android.content.Intent
import android.media.MediaMetadataRetriever
import android.net.Uri
import android.provider.OpenableColumns
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.clipzit.app.data.AppDb
import com.clipzit.app.data.ClipEntity
import com.clipzit.app.data.ClipImporter
import com.clipzit.app.data.ExportRecord
import com.clipzit.app.data.Prefs
import com.clipzit.app.data.ProjectEntity
import com.clipzit.app.media.Exporter
import com.clipzit.app.media.Framing
import com.clipzit.app.media.safeFileName
import com.clipzit.app.net.Alternative
import com.clipzit.app.net.BackendClient
import com.clipzit.app.net.BackendException
import com.clipzit.app.net.SearchHit
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File
import java.io.IOException

sealed interface Screen {
    object Home : Screen
    object Exports : Screen
    object Settings : Screen
    data class Project(val id: Long) : Screen
    data class Editor(val projectId: Long, val clipId: Long) : Screen
}

data class AnalysisUi(
    val projectId: Long,
    val phase: String,
    val progress: Float,
    val error: String?,
    val jobId: String?,
    val running: Boolean,
)

data class ExportUi(
    val running: Boolean,
    val label: String,
    val progress: Float,
    val file: String?,
    val error: String?,
)

class AppViewModel(private val app: Application) : AndroidViewModel(app) {
    private val db = AppDb.get(app)
    val prefs = Prefs(app)

    var screen by mutableStateOf<Screen>(Screen.Home)
        private set
    var message by mutableStateOf<String?>(null)
    var analysis by mutableStateOf<AnalysisUi?>(null)
        private set
    var export by mutableStateOf<ExportUi?>(null)
        private set
    var searchHits by mutableStateOf<List<SearchHit>>(emptyList())
        private set
    var lastPreset by mutableStateOf("30-45")

    val projects = db.projects().all()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())
    val exports = db.exports().all()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())

    private var analysisJob: Job? = null
    private var exportJob: Job? = null

    fun clipsFor(projectId: Long): Flow<List<ClipEntity>> = db.clips().forProject(projectId)

    // ------------------------------------------------------------------ navigation

    fun open(projectId: Long) {
        searchHits = emptyList()
        screen = Screen.Project(projectId)
    }

    fun openEditor(projectId: Long, clipId: Long) {
        export = null
        screen = Screen.Editor(projectId, clipId)
    }

    fun openSettings() {
        screen = Screen.Settings
    }

    fun openExports() {
        screen = Screen.Exports
    }

    fun goHome() {
        screen = Screen.Home
    }

    fun back() {
        screen = when (val s = screen) {
            is Screen.Editor -> {
                exportJob?.cancel()
                export = null
                Screen.Project(s.projectId)
            }
            else -> Screen.Home
        }
    }

    // ------------------------------------------------------------------ projects

    private data class FileInfo(val name: String?, val size: Long)

    private fun fileInfo(uri: Uri): FileInfo {
        var name: String? = null
        var size = -1L
        try {
            app.contentResolver.query(
                uri,
                arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE),
                null,
                null,
                null,
            )?.use { c ->
                if (c.moveToFirst()) {
                    val ni = c.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                    val si = c.getColumnIndex(OpenableColumns.SIZE)
                    if (ni >= 0 && !c.isNull(ni)) name = c.getString(ni)
                    if (si >= 0 && !c.isNull(si)) size = c.getLong(si)
                }
            }
        } catch (e: Exception) {
            // Fall through with unknown name/size.
        }
        return FileInfo(name, size)
    }

    private data class Meta(val durationMs: Long, val width: Int, val height: Int)

    private fun readMeta(uri: Uri): Meta? {
        val r = MediaMetadataRetriever()
        return try {
            r.setDataSource(app, uri)
            val dur = r.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)?.toLongOrNull()
                ?: return null
            var w = r.extractMetadata(MediaMetadataRetriever.METADATA_KEY_VIDEO_WIDTH)?.toIntOrNull() ?: 0
            var h = r.extractMetadata(MediaMetadataRetriever.METADATA_KEY_VIDEO_HEIGHT)?.toIntOrNull() ?: 0
            val rot = r.extractMetadata(MediaMetadataRetriever.METADATA_KEY_VIDEO_ROTATION)?.toIntOrNull() ?: 0
            if (rot == 90 || rot == 270) {
                val t = w
                w = h
                h = t
            }
            if (dur <= 0 || w <= 0 || h <= 0) null else Meta(dur, w, h)
        } catch (e: Exception) {
            null
        } finally {
            r.release()
        }
    }

    fun importVideos(uris: List<Uri>) {
        if (uris.isEmpty()) return
        viewModelScope.launch {
            var lastId = -1L
            var failed = 0
            for (uri in uris) {
                val result = withContext(Dispatchers.IO) {
                    try {
                        app.contentResolver.takePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION)
                    } catch (e: SecurityException) {
                        // Some providers do not offer persistable grants; the URI works for this session.
                    }
                    Pair(readMeta(uri), fileInfo(uri))
                }
                val meta = result.first
                if (meta == null) {
                    failed++
                    continue
                }
                val fileName = result.second.name ?: "video.mp4"
                lastId = db.projects().insert(
                    ProjectEntity(
                        name = fileName.substringBeforeLast('.'),
                        fileName = fileName,
                        sourceUri = uri.toString(),
                        durationMs = meta.durationMs,
                        width = meta.width,
                        height = meta.height,
                        createdAt = System.currentTimeMillis(),
                    ),
                )
            }
            if (failed > 0) message = "$failed file(s) could not be read as video."
            if (lastId >= 0 && uris.size == 1) screen = Screen.Project(lastId)
        }
    }

    fun deleteProject(project: ProjectEntity) {
        viewModelScope.launch {
            AnalysisWorker.cancel(app, project.id)
            db.projects().delete(project)
            try {
                app.contentResolver.releasePersistableUriPermission(
                    Uri.parse(project.sourceUri),
                    Intent.FLAG_GRANT_READ_URI_PERMISSION,
                )
            } catch (e: Exception) {
                // Permission may already be gone.
            }
            if (analysis?.projectId == project.id) analysis = null
        }
    }

    private suspend fun patchProject(id: Long, change: (ProjectEntity) -> ProjectEntity) {
        val current = db.projects().get(id) ?: return
        db.projects().update(change(current))
    }

    // ------------------------------------------------------------------ clips

    fun addManualClip(project: ProjectEntity, startMs: Long = 0L) {
        viewModelScope.launch {
            val start = startMs.coerceIn(0L, maxOf(0L, project.durationMs - 1000))
            val end = minOf(project.durationMs, start + 30_000L)
            db.clips().insert(
                ClipEntity(
                    projectId = project.id,
                    startMs = start,
                    endMs = end,
                    title = "Manual clip",
                    hook = "",
                    score = 0,
                    reasons = "",
                    transcript = "",
                    jobId = null,
                    clipIndex = -1,
                    source = "manual",
                ),
            )
        }
    }

    fun saveClip(clip: ClipEntity) {
        viewModelScope.launch { db.clips().update(clip) }
    }

    fun deleteClip(clip: ClipEntity) {
        viewModelScope.launch { db.clips().delete(clip) }
    }

    fun duplicateClip(clip: ClipEntity) {
        viewModelScope.launch {
            db.clips().insert(clip.copy(id = 0, title = clip.title + " (copy)"))
            message = "Clip duplicated"
        }
    }

    /** Splits at [atMs]: the original keeps the first part, a new clip gets the rest. */
    fun splitClip(clip: ClipEntity, atMs: Long) {
        if (atMs < clip.startMs + 500 || atMs > clip.endMs - 500) {
            message = "Move the playhead inside the clip (at least 0.5 s from each end) to split."
            return
        }
        viewModelScope.launch {
            db.clips().update(clip.copy(endMs = atMs))
            db.clips().insert(clip.copy(id = 0, startMs = atMs, title = clip.title + " (part 2)"))
            message = "Clip split"
        }
    }

    suspend fun clip(id: Long): ClipEntity? = db.clips().get(id)

    // ------------------------------------------------------------------ backend helpers

    fun backendConfigured(): Boolean = prefs.backendUrl.isNotBlank()

    private fun client(): BackendClient? =
        if (prefs.backendUrl.isBlank()) null else BackendClient(prefs.backendUrl, prefs.apiToken)

    fun testBackend() {
        val c = client()
        if (c == null) {
            message = "Enter the backend URL first."
            return
        }
        viewModelScope.launch {
            message = try {
                val h = c.health()
                val parts = mutableListOf("Connected (v${h.version})")
                parts += if (h.ffmpeg) "FFmpeg ready" else "FFmpeg MISSING"
                parts += if (h.transcriptionAvailable) "Whisper ready" else "Whisper NOT installed"
                parts += if (h.faceTracking) "face framing ready" else "face framing unavailable"
                parts.joinToString(" · ")
            } catch (e: Exception) {
                e.message ?: "Connection failed"
            }
        }
    }

    // ------------------------------------------------------------------ backend analysis

    fun startAnalysis(project: ProjectEntity, preset: String) {
        val c = client()
        if (c == null) {
            message = "Set the backend URL in Settings to use AI clip discovery."
            return
        }
        lastPreset = preset
        analysisJob?.cancel()
        analysisJob = viewModelScope.launch {
            analysis = AnalysisUi(project.id, "Preparing", 0f, null, null, true)
            try {
                val fresh = db.projects().get(project.id) ?: return@launch
                val existing = fresh.jobId
                if (existing != null) {
                    // Re-run on the server's saved transcript: no second upload, no second transcription.
                    try {
                        c.regenerate(existing, preset)
                        poll(c, project.id, existing)
                        return@launch
                    } catch (e: BackendException) {
                        if (e.code != 404) throw e
                        patchProject(project.id) { it.copy(jobId = null, uploadId = null) }
                    }
                }
                val latest = db.projects().get(project.id) ?: return@launch
                val uri = Uri.parse(latest.sourceUri)
                val info = withContext(Dispatchers.IO) { fileInfo(uri) }
                analysis = AnalysisUi(project.id, "Uploading video (resumable)", 0f, null, null, true)
                val jobId = c.uploadResumable(
                    resolver = app.contentResolver,
                    uri = uri,
                    fileName = info.name ?: latest.fileName,
                    sizeBytes = info.size,
                    projectName = latest.name,
                    preset = preset,
                    quality = prefs.quality,
                    language = prefs.language,
                    resumeId = latest.uploadId,
                    onSession = { id -> patchProject(project.id) { it.copy(uploadId = id) } },
                    onProgress = { p -> analysis = analysis?.copy(progress = p) },
                )
                patchProject(project.id) { it.copy(jobId = jobId) }
                AnalysisWorker.enqueue(app, project.id, jobId)
                poll(c, project.id, jobId)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                analysis = AnalysisUi(
                    project.id, "Upload paused", 0f,
                    (e.message ?: "Unknown error") + "\nNothing is lost: Resume continues where it stopped.",
                    null, false,
                )
            }
        }
    }

    /** Re-attach to a backend job (or a paused upload) after an app restart. */
    fun resumeAnalysis(project: ProjectEntity) {
        val c = client() ?: return
        if (analysis != null) return
        val jobId = project.jobId
        if (jobId == null) {
            if (project.uploadId != null) {
                analysis = AnalysisUi(
                    project.id, "Upload paused", 0f,
                    "An upload was interrupted. Resume continues from where it stopped.", null, false,
                )
            }
            return
        }
        analysisJob?.cancel()
        analysisJob = viewModelScope.launch {
            try {
                poll(c, project.id, jobId)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                analysis = null
            }
        }
    }

    fun retryAnalysis(project: ProjectEntity) {
        val c = client() ?: return
        val jobId = analysis?.jobId ?: project.jobId
        if (jobId == null) {
            startAnalysis(project, lastPreset)
            return
        }
        analysisJob?.cancel()
        analysisJob = viewModelScope.launch {
            try {
                c.retry(jobId)
                poll(c, project.id, jobId)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                analysis = AnalysisUi(project.id, "Retry failed", 0f, e.message, jobId, false)
            }
        }
    }

    fun cancelAnalysis() {
        val a = analysis ?: return
        val jobId = a.jobId
        if (jobId == null) {
            analysisJob?.cancel()
            analysis = null
            return
        }
        val c = client() ?: return
        viewModelScope.launch {
            try {
                c.cancel(jobId)
            } catch (e: Exception) {
                message = e.message
            }
        }
    }

    fun dismissAnalysis() {
        analysis = null
    }

    private suspend fun poll(c: BackendClient, projectId: Long, jobId: String) {
        while (true) {
            val job = try {
                c.job(jobId)
            } catch (e: BackendException) {
                analysis = AnalysisUi(projectId, "Connection lost", 0f, e.message, jobId, false)
                return
            }
            when (job.status) {
                "completed" -> {
                    ClipImporter.importClips(db, projectId, jobId, c.clips(jobId))
                    AnalysisWorker.cancel(app, projectId)
                    analysis = null
                    message = "Clips ready"
                    return
                }
                "failed", "cancelled" -> {
                    analysis = AnalysisUi(
                        projectId,
                        if (job.status == "cancelled") "Cancelled" else "Failed",
                        0f,
                        job.error ?: "The backend stopped the job (${job.status}).",
                        jobId,
                        false,
                    )
                    return
                }
                else -> analysis = AnalysisUi(
                    projectId,
                    job.stage.ifBlank { job.status },
                    job.progress.toFloat().coerceIn(0f, 1f),
                    null,
                    jobId,
                    true,
                )
            }
            delay(1500)
        }
    }

    // ------------------------------------------------------------------ transcript search, boundaries

    fun searchTranscript(project: ProjectEntity, query: String) {
        val c = client()
        val jobId = project.jobId
        if (c == null || jobId == null) {
            message = "Search needs an analysed project and a backend."
            return
        }
        if (query.isBlank()) return
        viewModelScope.launch {
            try {
                searchHits = c.search(jobId, query)
                if (searchHits.isEmpty()) message = "No matches"
            } catch (e: Exception) {
                message = e.message
            }
        }
    }

    fun clearSearch() {
        searchHits = emptyList()
    }

    suspend fun alternatives(clip: ClipEntity): List<Alternative> {
        val c = client() ?: throw BackendException("Backend not configured")
        val jobId = clip.jobId
        if (jobId == null || clip.clipIndex < 0) throw BackendException("Only AI-discovered clips have suggestions")
        return c.alternatives(jobId, clip.clipIndex)
    }

    // ------------------------------------------------------------------ export

    private fun exportFile(clip: ClipEntity, project: ProjectEntity, suffix: String, ext: String = "mp4"): File {
        val dir = File(app.cacheDir, "exports")
        return File(dir, "${safeFileName(project.name)}_${clip.id}_$suffix.$ext")
    }

    /** Options for the backend renderer, from the user's export settings and this clip's edits. */
    fun renderOptions(clip: ClipEntity, withRange: Boolean = true): JSONObject {
        val o = JSONObject()
        if (withRange) {
            o.put("start", clip.startMs / 1000.0)
            o.put("end", clip.endMs / 1000.0)
        }
        if (prefs.platform != "custom") {
            o.put("platform", prefs.platform)
        } else {
            o.put("aspect", if (clip.aspect == "Original") "original" else clip.aspect)
        }
        o.put("crop_x", clip.cropX.toDouble())
        o.put("captions", prefs.captionsOn)
        o.put("caption_preset", prefs.captionPreset)
        o.put("pacing", prefs.pacing)
        o.put("remove_fillers", prefs.removeFillers)
        o.put("auto_frame", prefs.autoFrame)
        if (prefs.hookOverlay && clip.hook.isNotBlank()) o.put("hook_text", clip.hook)
        if (prefs.watermark.isNotBlank()) o.put("watermark_text", prefs.watermark)
        return o
    }

    private suspend fun record(title: String, kind: String, path: String?, error: String?) {
        db.exports().insert(
            ExportRecord(
                title = title, kind = kind, path = path, ok = error == null, error = error,
                createdAt = System.currentTimeMillis(),
            ),
        )
    }

    fun exportLocal(project: ProjectEntity, clip: ClipEntity) {
        exportJob?.cancel()
        exportJob = viewModelScope.launch {
            val out = exportFile(clip, project, "local")
            export = ExportUi(true, "Exporting on this device", 0f, null, null)
            try {
                val crop = Framing.compute(project.width, project.height, clip.aspect, clip.cropX)
                Exporter(app).export(Uri.parse(project.sourceUri), clip.startMs, clip.endMs, crop, out) { p ->
                    export = export?.copy(progress = p)
                }
                export = ExportUi(false, "Export complete (on device, no captions)", 1f, out.absolutePath, null)
                record(clip.title, "device", out.absolutePath, null)
            } catch (e: CancellationException) {
                out.delete()
                throw e
            } catch (e: Exception) {
                out.delete()
                val msg = e.message ?: e.javaClass.simpleName
                export = ExportUi(false, "Export failed", 0f, null, msg)
                record(clip.title, "device", null, msg)
            }
        }
    }

    fun exportWithCaptions(project: ProjectEntity, clip: ClipEntity) {
        val c = client()
        val jobId = clip.jobId
        if (c == null || jobId == null || clip.clipIndex < 0) {
            message = "Backend rendering needs a backend and an AI-discovered clip."
            return
        }
        exportJob?.cancel()
        exportJob = viewModelScope.launch {
            val out = exportFile(clip, project, "backend")
            export = ExportUi(true, "Rendering on backend", 0f, null, null)
            try {
                c.render(jobId, clip.clipIndex, renderOptions(clip), out)
                export = ExportUi(false, "Export complete (backend render)", 1f, out.absolutePath, null)
                record(clip.title, "captioned", out.absolutePath, null)
            } catch (e: CancellationException) {
                out.delete()
                throw e
            } catch (e: Exception) {
                out.delete()
                val msg = e.message ?: "Unknown error"
                export = ExportUi(false, "Backend render failed", 0f, null, msg)
                record(clip.title, "captioned", null, msg)
            }
        }
    }

    fun exportZip(project: ProjectEntity, clips: List<ClipEntity>) {
        val c = client()
        val jobId = project.jobId
        val chosen = clips.filter { it.jobId == jobId && it.clipIndex >= 0 }
        if (c == null || jobId == null || chosen.isEmpty()) {
            message = "ZIP export needs a backend and AI-discovered clips."
            return
        }
        exportJob?.cancel()
        exportJob = viewModelScope.launch {
            val out = File(File(app.cacheDir, "exports"), "${safeFileName(project.name)}_clips.zip")
            export = ExportUi(true, "Rendering ${chosen.size} clips on backend", 0f, null, null)
            try {
                val options = renderOptions(chosen.first(), withRange = false)
                val ranges = JSONObject()
                chosen.forEach {
                    ranges.put(it.clipIndex.toString(), org.json.JSONArray(listOf(it.startMs / 1000.0, it.endMs / 1000.0)))
                }
                c.exportZip(jobId, chosen.map { it.clipIndex }, options, ranges, out)
                export = ExportUi(false, "ZIP ready (${chosen.size} clips)", 1f, out.absolutePath, null)
                record(project.name + " (ZIP)", "zip", out.absolutePath, null)
            } catch (e: CancellationException) {
                out.delete()
                throw e
            } catch (e: Exception) {
                out.delete()
                val msg = e.message ?: "Unknown error"
                export = ExportUi(false, "ZIP export failed", 0f, null, msg)
                record(project.name + " (ZIP)", "zip", null, msg)
            }
        }
    }

    fun cancelExport() {
        exportJob?.cancel()
        export = null
    }

    fun saveFileTo(path: String, destination: Uri) {
        viewModelScope.launch {
            message = try {
                withContext(Dispatchers.IO) {
                    File(path).inputStream().use { input ->
                        val output = app.contentResolver.openOutputStream(destination)
                            ?: throw IOException("Cannot open the destination")
                        output.use { input.copyTo(it) }
                    }
                }
                "Saved to the selected location"
            } catch (e: Exception) {
                "Save failed: ${e.message}"
            }
        }
    }

    fun saveExportTo(destination: Uri) {
        val path = export?.file ?: return
        saveFileTo(path, destination)
    }

    fun deleteExport(record: ExportRecord) {
        viewModelScope.launch(Dispatchers.IO) {
            record.path?.let { File(it).delete() }
            db.exports().delete(record)
        }
    }

    fun clearExportCache() {
        viewModelScope.launch(Dispatchers.IO) {
            File(app.cacheDir, "exports").deleteRecursively()
            export = null
            message = "Temporary exports cleared"
        }
    }
}
