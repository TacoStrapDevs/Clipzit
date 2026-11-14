package com.clipzit.app.data

import androidx.room.Dao
import androidx.room.Delete
import androidx.room.Insert
import androidx.room.Query
import androidx.room.Update
import kotlinx.coroutines.flow.Flow

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
