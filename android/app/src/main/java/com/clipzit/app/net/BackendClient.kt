package com.clipzit.app.net

import android.content.ContentResolver
import android.net.Uri
import android.os.ParcelFileDescriptor
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import okhttp3.Call
import okhttp3.Callback
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/** [code] is the HTTP status, or 0 for network-level failures. */
class BackendException(message: String, val code: Int = 0) : Exception(message)

data class JobStatus(
    val id: String,
    val status: String,
    val stage: String,
    val progress: Double,
    val error: String?,
)

data class RemoteClip(
    val index: Int,
    val startSec: Double,
    val endSec: Double,
    val score: Int,
    val title: String,
    val hook: String,
    val hooks: List<String>,
    val hashtags: List<String>,
    val description: String,
    val reasons: List<String>,
    val suggestions: List<String>,
    val signals: Map<String, Int>,
    val transcript: String,
)

data class BackendHealth(
    val ffmpeg: Boolean,
    val transcriptionAvailable: Boolean,
    val faceTracking: Boolean,
    val whisperModel: String,
    val version: String,
)

data class SearchHit(val startSec: Double, val endSec: Double, val text: String)

data class Alternative(
    val startSec: Double,
    val endSec: Double,
    val score: Int,
    val delta: Int,
    val current: Boolean,
    val reasons: List<String>,
)

class BackendClient(baseUrl: String, private val token: String) {
    private val base = baseUrl.trim().trimEnd('/')

    private val http = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .writeTimeout(120, TimeUnit.SECONDS)
        .readTimeout(120, TimeUnit.SECONDS)
        .build()

    private val longHttp = http.newBuilder().readTimeout(20, TimeUnit.MINUTES).build()

    private fun request(path: String): Request.Builder {
        val builder = try {
            Request.Builder().url(base + path)
        } catch (e: IllegalArgumentException) {
            throw BackendException("Invalid backend URL: $base")
        }
        if (token.isNotBlank()) builder.header("Authorization", "Bearer $token")
        return builder
    }

    private suspend fun Call.await(): Response = suspendCancellableCoroutine { cont ->
        cont.invokeOnCancellation { cancel() }
        enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                if (cont.isActive) cont.resumeWithException(e)
            }

            override fun onResponse(call: Call, response: Response) {
                if (cont.isActive) cont.resume(response) else response.close()
            }
        })
    }

    private fun errorText(code: Int, body: String): String {
        val detail = try {
            when (val d = JSONObject(body).opt("detail")) {
                is String -> d
                null -> null
                else -> d.toString()
            }
        } catch (e: Exception) {
            null
        }
        return when {
            code == 401 -> "The backend rejected the API token (401). Check Settings."
            detail != null -> detail
            else -> "Backend error $code"
        }
    }

    private suspend fun execute(req: Request, client: OkHttpClient = http): String = withContext(Dispatchers.IO) {
        val response = try {
            client.newCall(req).await()
        } catch (e: IOException) {
            throw BackendException("Cannot reach the backend: ${e.message ?: "network error"}")
        }
        response.use { r ->
            val body = r.body?.string().orEmpty()
            if (!r.isSuccessful) throw BackendException(errorText(r.code, body), r.code)
            body
        }
    }

    private fun parseJob(o: JSONObject) = JobStatus(
        id = o.getString("id"),
        status = o.getString("status"),
        stage = o.optString("stage", ""),
        progress = o.optDouble("progress", 0.0),
        error = if (o.isNull("error")) null else o.optString("error"),
    )

    private fun strings(arr: JSONArray?): List<String> =
        if (arr == null) emptyList() else List(arr.length()) { arr.optString(it) }

    suspend fun health(): BackendHealth {
        val o = JSONObject(execute(request("/api/health").build()))
        return BackendHealth(
            ffmpeg = o.optBoolean("ffmpeg"),
            transcriptionAvailable = o.optBoolean("transcription_available"),
            faceTracking = o.optBoolean("face_tracking_available"),
            whisperModel = o.optString("whisper_model"),
            version = o.optString("version"),
        )
    }

    // ------------------------------------------------------------------ resumable upload

    private fun readChunk(resolver: ContentResolver, uri: Uri, offset: Long, length: Int): ByteArray {
        val buf = ByteArray(length)
        var read = 0
        val pfd = try {
            resolver.openFileDescriptor(uri, "r")
        } catch (e: Exception) {
            null
        }
        if (pfd != null) {
            ParcelFileDescriptor.AutoCloseInputStream(pfd).use { input ->
                input.channel.position(offset)
                while (read < length) {
                    val n = input.read(buf, read, length - read)
                    if (n < 0) break
                    read += n
                }
            }
        } else {
            val input = resolver.openInputStream(uri) ?: throw IOException("Cannot open the selected video")
            input.use { stream ->
                var skipped = 0L
                while (skipped < offset) {
                    val n = stream.skip(offset - skipped)
                    if (n > 0) {
                        skipped += n
                    } else {
                        if (stream.read() < 0) break
                        skipped++
                    }
                }
                while (read < length) {
                    val n = stream.read(buf, read, length - read)
                    if (n < 0) break
                    read += n
                }
            }
        }
        if (read < length) throw IOException("The video file ended unexpectedly ($read of $length bytes)")
        return buf
    }

    private suspend fun serverOffset(uploadId: String): Pair<Long, String?> {
        val o = JSONObject(execute(request("/api/uploads/$uploadId").build()))
        val job = if (o.isNull("job_id")) null else o.optString("job_id").ifEmpty { null }
        return Pair(o.getLong("offset"), job)
    }

    /**
     * Chunked, resumable upload. If [resumeId] is given (for example after an app restart) the transfer continues
     * from the offset the server already holds. Returns the analysis job id.
     */
    suspend fun uploadResumable(
        resolver: ContentResolver,
        uri: Uri,
        fileName: String,
        sizeBytes: Long,
        projectName: String,
        preset: String,
        quality: String,
        language: String,
        resumeId: String?,
        onSession: suspend (String) -> Unit,
        onProgress: (Float) -> Unit,
    ): String {
        if (sizeBytes <= 0) throw BackendException("Cannot determine the size of this video")
        var uploadId = resumeId
        var offset = 0L
        if (uploadId != null) {
            try {
                val (off, job) = serverOffset(uploadId)
                if (job != null) return job
                offset = off
            } catch (e: BackendException) {
                if (e.code == 404) {
                    uploadId = null // session expired on the server: start over
                } else {
                    throw e
                }
            }
        }
        if (uploadId == null) {
            val json = JSONObject()
                .put("filename", fileName)
                .put("size", sizeBytes)
                .put("name", projectName)
                .put("preset", preset)
                .put("quality", quality)
                .put("language", language)
            val text = execute(
                request("/api/uploads").post(json.toString().toRequestBody("application/json".toMediaType())).build(),
            )
            uploadId = JSONObject(text).getString("upload_id")
            offset = 0
            onSession(uploadId)
        }
        val id: String = checkNotNull(uploadId)
        val chunkSize = 4 * 1024 * 1024
        var failures = 0
        while (offset < sizeBytes) {
            val len = minOf(chunkSize.toLong(), sizeBytes - offset).toInt()
            val chunk = withContext(Dispatchers.IO) { readChunk(resolver, uri, offset, len) }
            val put = request("/api/uploads/$id?offset=$offset")
                .put(chunk.toRequestBody("application/octet-stream".toMediaType())).build()
            try {
                val o = JSONObject(execute(put))
                offset = o.getLong("offset")
                failures = 0
                onProgress((offset.toFloat() / sizeBytes).coerceIn(0f, 1f))
            } catch (e: BackendException) {
                if (e.code == 409) {
                    offset = serverOffset(id).first // another attempt already stored this chunk
                } else if (e.code == 0 || e.code >= 500) {
                    failures++
                    if (failures > 6) throw BackendException("Upload interrupted: ${e.message}. Tap Retry to resume.")
                    delay(1000L * (1 shl minOf(failures, 4)))
                    try {
                        offset = serverOffset(id).first
                    } catch (inner: BackendException) {
                        // Still unreachable; the next loop iteration retries.
                    }
                } else {
                    throw e
                }
            }
        }
        var tries = 0
        while (true) {
            try {
                val text = execute(request("/api/uploads/$id/complete").post("".toRequestBody(null)).build())
                return JSONObject(text).getString("id")
            } catch (e: BackendException) {
                tries++
                if ((e.code != 0 && e.code < 500) || tries >= 4) throw e
                delay(2000L * tries)
            }
        }
    }

    // ------------------------------------------------------------------ jobs

    suspend fun job(id: String): JobStatus =
        parseJob(JSONObject(execute(request("/api/jobs/$id").build())))

    suspend fun cancel(id: String) {
        execute(request("/api/jobs/$id/cancel").post("".toRequestBody(null)).build())
    }

    suspend fun retry(id: String) {
        execute(request("/api/jobs/$id/retry").post("".toRequestBody(null)).build())
    }

    suspend fun regenerate(id: String, preset: String) {
        val json = JSONObject().put("preset", preset)
        execute(
            request("/api/jobs/$id/regenerate")
                .post(json.toString().toRequestBody("application/json".toMediaType())).build(),
        )
    }

    suspend fun clips(id: String): List<RemoteClip> {
        val arr = JSONArray(execute(request("/api/jobs/$id/clips").build()))
        return List(arr.length()) { i ->
            val o = arr.getJSONObject(i)
            val hooksArr = o.optJSONArray("hooks")
            val hooks = if (hooksArr == null) emptyList() else List(hooksArr.length()) {
                hooksArr.getJSONObject(it).optString("text")
            }
            val sigObj = o.optJSONObject("signals")
            val signals = HashMap<String, Int>()
            if (sigObj != null) {
                val keys = sigObj.keys()
                while (keys.hasNext()) {
                    val k = keys.next()
                    signals[k] = sigObj.optInt(k)
                }
            }
            RemoteClip(
                index = o.getInt("index"),
                startSec = o.getDouble("start"),
                endSec = o.getDouble("end"),
                score = o.getInt("score"),
                title = o.optString("title"),
                hook = hooks.firstOrNull() ?: "",
                hooks = hooks,
                hashtags = strings(o.optJSONArray("hashtags")),
                description = o.optString("description"),
                reasons = strings(o.optJSONArray("reasons")),
                suggestions = strings(o.optJSONArray("suggestions")),
                signals = signals,
                transcript = o.optString("transcript"),
            )
        }
    }

    suspend fun search(id: String, query: String): List<SearchHit> {
        val q = java.net.URLEncoder.encode(query, "UTF-8")
        val arr = JSONArray(execute(request("/api/jobs/$id/search?q=$q").build()))
        return List(arr.length()) {
            val o = arr.getJSONObject(it)
            SearchHit(o.getDouble("start"), o.getDouble("end"), o.optString("text"))
        }
    }

    suspend fun alternatives(id: String, index: Int): List<Alternative> {
        val arr = JSONArray(execute(request("/api/jobs/$id/clips/$index/alternatives").build()))
        return List(arr.length()) {
            val o = arr.getJSONObject(it)
            Alternative(
                o.getDouble("start"), o.getDouble("end"), o.getInt("score"), o.optInt("delta"),
                o.optBoolean("is_current"), strings(o.optJSONArray("reasons")),
            )
        }
    }

    private suspend fun download(req: Request, out: File) {
        withContext(Dispatchers.IO) {
            val response = try {
                longHttp.newCall(req).await()
            } catch (e: IOException) {
                throw BackendException("Cannot reach the backend: ${e.message ?: "network error"}")
            }
            response.use { r ->
                if (!r.isSuccessful) throw BackendException(errorText(r.code, r.body?.string().orEmpty()), r.code)
                val body = r.body ?: throw BackendException("Empty response from backend")
                out.parentFile?.mkdirs()
                try {
                    body.byteStream().use { input -> out.outputStream().use { input.copyTo(it) } }
                } catch (e: IOException) {
                    out.delete()
                    throw BackendException("Download interrupted: ${e.message}")
                }
                if (out.length() == 0L) throw BackendException("Backend returned an empty file")
            }
        }
    }

    /** Renders one clip on the server with [options] (see docs/API.md) and saves the MP4 to [out]. */
    suspend fun render(jobId: String, index: Int, options: JSONObject, out: File) {
        val req = request("/api/jobs/$jobId/clips/$index/render")
            .post(options.toString().toRequestBody("application/json".toMediaType())).build()
        download(req, out)
    }

    /** Renders the selected clips and downloads one ZIP (MP4 + SRT + metadata per clip). */
    suspend fun exportZip(jobId: String, indices: List<Int>, options: JSONObject, ranges: JSONObject, out: File) {
        val body = JSONObject().put("indices", JSONArray(indices)).put("options", options).put("ranges", ranges)
        val req = request("/api/jobs/$jobId/export.zip")
            .post(body.toString().toRequestBody("application/json".toMediaType())).build()
        download(req, out)
    }
}
