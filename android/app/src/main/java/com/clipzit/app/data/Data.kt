package com.clipzit.app.data

import android.content.Context
import android.content.SharedPreferences
import androidx.room.Dao
import androidx.room.Database
import androidx.room.Delete
import androidx.room.Entity
import androidx.room.ForeignKey
import androidx.room.Index
import androidx.room.Insert
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.Update
import androidx.room.withTransaction
import com.clipzit.app.net.RemoteClip
import kotlin.math.roundToLong
import kotlinx.coroutines.flow.Flow

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

@Dao
interface ProjectDao {
    @Query("SELECT * FROM projects ORDER BY createdAt DESC")
    fun all(): Flow<List<ProjectEntity>>

    @Query("SELECT * FROM projects WHERE id = :id")
    suspend fun get(id: Long): ProjectEntity?

    @Insert
    suspend fun insert(project: ProjectEntity): Long

    @Update
    suspend fun update(project: ProjectEntity)

    @Delete
    suspend fun delete(project: ProjectEntity)
}

@Dao
interface ClipDao {
    @Query(
        "SELECT * FROM clips WHERE projectId = :projectId " +
            "ORDER BY CASE WHEN source = 'backend' THEN 0 ELSE 1 END, score DESC, startMs ASC",
    )
    fun forProject(projectId: Long): Flow<List<ClipEntity>>

    @Query("SELECT * FROM clips WHERE id = :id")
    suspend fun get(id: Long): ClipEntity?

    @Insert
    suspend fun insert(clip: ClipEntity): Long

    @Insert
    suspend fun insertAll(clips: List<ClipEntity>)

    @Update
    suspend fun update(clip: ClipEntity)

    @Delete
    suspend fun delete(clip: ClipEntity)

    @Query("DELETE FROM clips WHERE projectId = :projectId AND source = 'backend'")
    suspend fun deleteBackendClips(projectId: Long)
}

@Dao
interface ExportDao {
    @Query("SELECT * FROM exports ORDER BY createdAt DESC")
    fun all(): Flow<List<ExportRecord>>

    @Insert
    suspend fun insert(record: ExportRecord): Long

    @Delete
    suspend fun delete(record: ExportRecord)
}

@Database(
    entities = [ProjectEntity::class, ClipEntity::class, ExportRecord::class],
    version = 2,
    exportSchema = false,
)
abstract class AppDb : RoomDatabase() {
    abstract fun projects(): ProjectDao
    abstract fun clips(): ClipDao
    abstract fun exports(): ExportDao

    companion object {
        @Volatile
        private var instance: AppDb? = null

        fun get(context: Context): AppDb =
            instance ?: synchronized(this) {
                instance ?: Room.databaseBuilder(context.applicationContext, AppDb::class.java, "clipzit.db")
                    .fallbackToDestructiveMigration()
                    .build()
                    .also { instance = it }
            }
    }
}

class Prefs(context: Context) {
    private val sp: SharedPreferences =
        context.applicationContext.getSharedPreferences("clipzit_prefs", Context.MODE_PRIVATE)

    private fun str(key: String, default: String) = sp.getString(key, default) ?: default

    var backendUrl: String
        get() = str("backend_url", "")
        set(v) = sp.edit().putString("backend_url", v.trim()).apply()

    var apiToken: String
        get() = str("api_token", "")
        set(v) = sp.edit().putString("api_token", v.trim()).apply()

    var quality: String
        get() = str("quality", "balanced")
        set(v) = sp.edit().putString("quality", v).apply()

    var language: String
        get() = str("language", "")
        set(v) = sp.edit().putString("language", v.trim()).apply()

    var platform: String
        get() = str("platform", "custom")
        set(v) = sp.edit().putString("platform", v).apply()

    var captionsOn: Boolean
        get() = sp.getBoolean("captions_on", true)
        set(v) = sp.edit().putBoolean("captions_on", v).apply()

    var captionPreset: String
        get() = str("caption_preset", "creator")
        set(v) = sp.edit().putString("caption_preset", v).apply()

    var pacing: String
        get() = str("pacing", "off")
        set(v) = sp.edit().putString("pacing", v).apply()

    var removeFillers: Boolean
        get() = sp.getBoolean("remove_fillers", false)
        set(v) = sp.edit().putBoolean("remove_fillers", v).apply()

    var autoFrame: Boolean
        get() = sp.getBoolean("auto_frame", false)
        set(v) = sp.edit().putBoolean("auto_frame", v).apply()

    var hookOverlay: Boolean
        get() = sp.getBoolean("hook_overlay", false)
        set(v) = sp.edit().putBoolean("hook_overlay", v).apply()

    var watermark: String
        get() = str("watermark", "")
        set(v) = sp.edit().putString("watermark", v).apply()
}

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
