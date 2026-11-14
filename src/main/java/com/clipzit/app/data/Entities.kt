package com.clipzit.app.data

import androidx.room.Entity
import androidx.room.ForeignKey
import androidx.room.Index
import androidx.room.PrimaryKey

@Entity(tableName = "projects")
data class ProjectEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val name: String,
    val fileName: String,
    val sourceUri: String,
    val durationMs: Long,
    val width: Int,
    val height: Int,
    val createdAt: Long,
    val jobId: String? = null,
    val uploadId: String? = null,
)

@Entity(
    tableName = "clips",
    foreignKeys = [
        ForeignKey(
            entity = ProjectEntity::class,
            parentColumns = ["id"],
            childColumns = ["projectId"],
            onDelete = ForeignKey.CASCADE,
        ),
    ],
    indices = [Index("projectId")],
)
data class ClipEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val projectId: Long,
    val startMs: Long,
    val endMs: Long,
    val title: String,
    val hook: String = "",
    val score: Int = 0,
    val reasons: String = "",
    val transcript: String = "",
    val jobId: String? = null,
    val clipIndex: Int = -1,
    val source: String = "manual",
    val aspect: String = "9:16",
    val cropX: Float = 0.5f,
    val hooks: String = "",
    val hashtags: String = "",
    val description: String = "",
    val suggestions: String = "",
    val signals: String = "",
)

@Entity(tableName = "exports")
data class ExportRecord(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val title: String,
    val kind: String,
    val path: String?,
    val ok: Boolean,
    val error: String?,
    val createdAt: Long,
)
