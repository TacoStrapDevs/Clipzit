package com.clipzit.app.ui

import android.app.Application
import android.media.MediaMetadataRetriever
import android.net.Uri
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import androidx.lifecycle.viewmodel.compose.viewModel
import com.clipzit.app.AnalysisWorker
import com.clipzit.app.AppViewModel
import com.clipzit.app.data.AppDb
import com.clipzit.app.data.ClipImporter
import com.clipzit.app.data.Prefs
import com.clipzit.app.data.ProjectEntity
import com.clipzit.app.net.BackendClient
import com.clipzit.app.net.BackendException
import com.clipzit.app.net.JobStatus
import java.io.File
import java.io.IOException
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject

/** What the link-import card shows. [progress] null means "working, amount unknown". */
data class LinkUi(
    val stage: String,
    val progress: Float?,
    val error: String?,
    val running: Boolean,
    val projectId: Long? = null,
)

/**
 * Link import: the backend downloads and analyses the video, the app copies the video to the phone
 * (so preview and on-device export work), creates a project and imports the clips.
 */
class LinkImportViewModel(private val app: Application) : AndroidViewModel(app) {
    private val db = AppDb.get(app)
    private val prefs = Prefs(app)

    var state by mutableStateOf<LinkUi?>(null)
        private set

    private var work: Job? = null
    private var activeJobId: String? = null
    private var activeProjectId: Long? = null

    private data class Meta(val durationMs: Long, val width: Int, val height: Int)

    private fun readMeta(uri: Uri): Meta? {
        val r = MediaMetadataRetriever()
        return try {
            r.setDataSource(app, uri)
            val dur = r.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)?.toLongOrNull() ?: return null
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

    private fun fail(job: JobStatus, projectId: Long? = null) {
        val text = job.error ?: (if (job.status == "cancelled") "Cancelled." else "The backend could not process this link.")
        state = LinkUi("", null, text, false, projectId)
    }

    fun start(rawUrl: String) {
        val url = rawUrl.trim()
        if (prefs.backendUrl.isBlank()) {
            state = LinkUi("", null, "Set your backend URL in Settings first. Your backend downloads and analyses the link.", false)
            return
        }
        if (!(url.startsWith("http://", ignoreCase = true) || url.startsWith("https://", ignoreCase = true))) {
            state = LinkUi("", null, "Paste a full link that starts with https://", false)
            return
        }
        work?.cancel()
        activeJobId = null
        activeProjectId = null
        work = viewModelScope.launch {
            val backend = BackendClient(prefs.backendUrl, prefs.apiToken)
            val links = LinkClient(prefs.backendUrl, prefs.apiToken)
            try {
                state = LinkUi("Sending link to your backend", null, null, true)
                val jobId = links.createFromUrl(url, "30-45", prefs.quality, prefs.language)
                activeJobId = jobId

                // 1) The backend downloads the video ("uploading" is the status while it does).
                while (true) {
                    val j = backend.job(jobId)
                    if (j.status == "failed" || j.status == "cancelled") {
                        fail(j)
                        return@launch
                    }
                    if (j.status != "uploading") break
                    state = LinkUi(j.stage.ifBlank { "Downloading on your backend" }, null, null, true)
                    delay(1500)
                }

                // 2) Copy the video to this phone.
                state = LinkUi("Saving video to this phone", null, null, true)
                val name = links.jobName(jobId) ?: "Linked video"
                val file = links.downloadSource(jobId, File(app.filesDir, "sources")) { p ->
                    state = LinkUi("Saving video to this phone", p, null, true)
                }
                val meta = withContext(Dispatchers.IO) { readMeta(Uri.fromFile(file)) }
                if (meta == null) {
                    file.delete()
                    state = LinkUi("", null, "The downloaded file could not be read as a video.", false)
                    return@launch
                }
                val projectId = db.projects().insert(
                    ProjectEntity(
                        name = name,
                        fileName = file.name,
                        sourceUri = Uri.fromFile(file).toString(),
                        durationMs = meta.durationMs,
                        width = meta.width,
                        height = meta.height,
                        createdAt = System.currentTimeMillis(),
                        jobId = jobId,
                    ),
                )
                activeProjectId = projectId
                AnalysisWorker.enqueue(app, projectId, jobId)

                // 3) Wait for the analysis, then import the clips.
                while (true) {
                    val j = backend.job(jobId)
                    when (j.status) {
                        "completed" -> {
                            ClipImporter.importClips(db, projectId, jobId, backend.clips(jobId))
                            AnalysisWorker.cancel(app, projectId)
                            state = LinkUi("Clips are ready", 1f, null, false, projectId)
                            return@launch
                        }
                        "failed", "cancelled" -> {
                            fail(j, projectId)
                            return@launch
                        }
                        else -> state = LinkUi(
                            j.stage.ifBlank { j.status },
                            j.progress.toFloat().coerceIn(0f, 1f),
                            null,
                            true,
                            projectId,
                        )
                    }
                    delay(1500)
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                state = LinkUi("", null, e.message ?: "Something went wrong.", false, activeProjectId)
            }
        }
    }

    fun cancel() {
        work?.cancel()
        val jobId = activeJobId
        val projectId = activeProjectId
        state = null
        if (projectId != null) AnalysisWorker.cancel(app, projectId)
        if (jobId != null && prefs.backendUrl.isNotBlank()) {
            viewModelScope.launch {
                try {
                    BackendClient(prefs.backendUrl, prefs.apiToken).cancel(jobId)
                } catch (e: Exception) {
                    // The job may already be finished.
                }
            }
        }
    }

    fun dismiss() {
        state = null
    }
}

@Composable
fun LinkImportSection(appVm: AppViewModel, vm: LinkImportViewModel = viewModel()) {
    var showDialog by remember { mutableStateOf(false) }
    var link by remember { mutableStateOf("") }
    val s = vm.state

    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        OutlinedButton(
            onClick = { showDialog = true },
            enabled = s?.running != true,
            modifier = Modifier.fillMaxWidth().height(54.dp),
            shape = RoundedCornerShape(14.dp),
        ) {
            Text("Paste a link", fontWeight = FontWeight.SemiBold)
        }
        if (s != null) {
            CardBox {
                Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (s.error != null) {
                        Text(s.error, color = MaterialTheme.colorScheme.error)
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            if (s.projectId != null) {
                                Button(onClick = { appVm.open(s.projectId); vm.dismiss() }) { Text("Open project") }
                            }
                            TextButton(onClick = { vm.dismiss() }) { Text("Dismiss") }
                        }
                    } else if (s.running) {
                        Text(s.stage, fontWeight = FontWeight.SemiBold)
                        val p = s.progress
                        if (p != null) {
                            LinearProgressIndicator(progress = { p }, modifier = Modifier.fillMaxWidth())
                        } else {
                            LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
                        }
                        TextButton(onClick = { vm.cancel() }) { Text("Cancel") }
                    } else {
                        Text(s.stage, fontWeight = FontWeight.SemiBold)
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            if (s.projectId != null) {
                                Button(onClick = { appVm.open(s.projectId); vm.dismiss() }) { Text("Open project") }
                            }
                            TextButton(onClick = { vm.dismiss() }) { Text("Dismiss") }
                        }
                    }
                }
            }
        }
    }

    if (showDialog) {
        AlertDialog(
            onDismissRequest = { showDialog = false },
            title = { Text("Clip from a link") },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    OutlinedTextField(
                        value = link,
                        onValueChange = { link = it },
                        label = { Text("Video link") },
                        singleLine = true,
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
                        modifier = Modifier.fillMaxWidth(),
                    )
                    Text(
                        "Your backend downloads the video and finds the best clips, so it must be running and set up in " +
                            "Settings. Only use videos you own or have permission to use.",
                        style = MaterialTheme.typography.bodySmall,
                        color = ClipMuted,
                    )
                }
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        vm.start(link)
                        showDialog = false
                        link = ""
                    },
                    enabled = link.isNotBlank(),
                ) { Text("Clip it") }
            },
            dismissButton = { TextButton(onClick = { showDialog = false }) { Text("Cancel") } },
        )
    }
}

/** Backend calls for "clip from a link": start the job, read its name, copy the downloaded video to the phone. */
class LinkClient(baseUrl: String, private val token: String) {
    private val base = baseUrl.trim().trimEnd('/')

    private val http = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .writeTimeout(60, TimeUnit.SECONDS)
        .readTimeout(5, TimeUnit.MINUTES)
        .build()

    private val videoExtensions = setOf("mp4", "mov", "mkv", "webm", "m4v", "avi")

    private fun request(path: String): Request.Builder {
        val builder = try {
            Request.Builder().url(base + path)
        } catch (e: IllegalArgumentException) {
            throw BackendException("Invalid backend URL: $base")
        }
        if (token.isNotBlank()) builder.header("Authorization", "Bearer $token")
        return builder
    }

    private fun errorText(code: Int, body: String): String {
        val detail = try {
            JSONObject(body).opt("detail")?.toString()
        } catch (e: Exception) {
            null
        }
        return when {
            code == 401 -> "The backend rejected the API token (401). Check Settings."
            code == 404 && detail == null ->
                "Not found on the backend (404). Update the backend to the latest code and restart it."
            detail != null -> detail
            else -> "Backend error $code"
        }
    }

    private fun send(req: Request): String {
        val response = try {
            http.newCall(req).execute()
        } catch (e: IOException) {
            throw BackendException("Cannot reach the backend: ${e.message ?: "network error"}")
        }
        return response.use { r ->
            val text = r.body?.string().orEmpty()
            if (!r.isSuccessful) throw BackendException(errorText(r.code, text), r.code)
            text
        }
    }

    /** Asks the backend to download [url] and analyse it. Returns the job id. */
    suspend fun createFromUrl(url: String, preset: String, quality: String, language: String): String =
        withContext(Dispatchers.IO) {
            val body = JSONObject()
                .put("url", url)
                .put("preset", preset)
                .put("quality", quality)
                .put("language", language)
            val req = request("/api/jobs/from-url")
                .post(body.toString().toRequestBody("application/json".toMediaType()))
                .build()
            JSONObject(send(req)).getString("id")
        }

    /** The video title the backend found, or null. */
    suspend fun jobName(id: String): String? = withContext(Dispatchers.IO) {
        try {
            JSONObject(send(request("/api/jobs/$id").build())).optString("name").ifBlank { null }
        } catch (e: Exception) {
            null
        }
    }

    /** Downloads the job's video into [dir] and returns the file. [onProgress] gets 0..1, or null if unknown. */
    suspend fun downloadSource(id: String, dir: File, onProgress: (Float?) -> Unit): File =
        withContext(Dispatchers.IO) {
            dir.mkdirs()
            val response = try {
                http.newCall(request("/api/jobs/$id/source").build()).execute()
            } catch (e: IOException) {
                throw BackendException("Cannot reach the backend: ${e.message ?: "network error"}")
            }
            response.use { r ->
                if (!r.isSuccessful) throw BackendException(errorText(r.code, r.body?.string().orEmpty()), r.code)
                val body = r.body ?: throw BackendException("Empty response from backend")
                val serverName = r.header("Content-Disposition")
                    ?.let { Regex("filename=\"?([^\";]+)").find(it)?.groupValues?.get(1) }
                val ext = serverName?.substringAfterLast('.', "")?.lowercase()
                    ?.takeIf { it in videoExtensions } ?: "mp4"
                val out = File(dir, "$id.$ext")
                val part = File(dir, "$id.part")
                val total = body.contentLength()
                var done = 0L
                var lastPercent = -1
                try {
                    body.byteStream().use { input ->
                        part.outputStream().use { output ->
                            val buffer = ByteArray(64 * 1024)
                            while (true) {
                                currentCoroutineContext().ensureActive()
                                val n = input.read(buffer)
                                if (n < 0) break
                                output.write(buffer, 0, n)
                                done += n
                                if (total > 0) {
                                    val percent = (done * 100 / total).toInt()
                                    if (percent != lastPercent) {
                                        lastPercent = percent
                                        onProgress(done.toFloat() / total)
                                    }
                                }
                            }
                        }
                    }
                } catch (e: CancellationException) {
                    part.delete()
                    throw e
                } catch (e: IOException) {
                    part.delete()
                    throw BackendException("Download interrupted: ${e.message}")
                }
                if (part.length() == 0L) {
                    part.delete()
                    throw BackendException("Backend returned an empty file")
                }
                out.delete()
                if (!part.renameTo(out)) {
                    part.delete()
                    throw BackendException("Could not save the video on this phone")
                }
                out
            }
        }
}
