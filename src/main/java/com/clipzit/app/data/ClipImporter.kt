package com.clipzit.app.data

import androidx.room.withTransaction
import com.clipzit.app.net.RemoteClip
import kotlin.math.roundToLong

object ClipImporter {
    suspend fun importClips(db: AppDb, projectId: Long, jobId: String, clips: List<RemoteClip>) {
        val project = db.projects().get(projectId) ?: return
        val rows = clips.map { c ->
            val start = (c.startSec * 1000).roundToLong().coerceAtLeast(0L)
            val end = (c.endSec * 1000).roundToLong().coerceAtMost(project.durationMs).coerceAtLeast(start + 1000L)
            ClipEntity(
                projectId = projectId,
                startMs = start,
                endMs = end,
                title = c.title.ifBlank { "Clip ${c.index + 1}" },
                hook = c.hook,
                score = c.score,
                reasons = c.reasons.joinToString("\n"),
                transcript = c.transcript,
                jobId = jobId,
                clipIndex = c.index,
                source = "backend",
                hooks = c.hooks.joinToString("\n"),
                hashtags = c.hashtags.joinToString(" "),
                description = c.description,
                suggestions = c.suggestions.joinToString("\n"),
                signals = c.signals.entries.joinToString("\n") { "${it.key}:${it.value}" },
            )
        }
        db.withTransaction {
            db.clips().deleteBackendClips(projectId)
            db.clips().insertAll(rows)
        }
    }
}
