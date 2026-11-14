package com.clipzit.app

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import com.clipzit.app.data.AppDb
import com.clipzit.app.data.ClipImporter
import com.clipzit.app.data.Prefs
import com.clipzit.app.net.BackendClient
import com.clipzit.app.net.BackendException
import kotlinx.coroutines.delay
import java.util.concurrent.TimeUnit

/**
 * Keeps following a backend analysis job while the app is closed, imports the clips when done and posts a
 * notification. Android limits a worker run to about 10 minutes, so it polls for 8 and then asks to be retried.
 */
class AnalysisWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result {
        val projectId = inputData.getLong(KEY_PROJECT, -1L)
        val jobId = inputData.getString(KEY_JOB) ?: return Result.failure()
        val prefs = Prefs(applicationContext)
        if (projectId < 0 || prefs.backendUrl.isBlank()) return Result.failure()
        val client = BackendClient(prefs.backendUrl, prefs.apiToken)
        val db = AppDb.get(applicationContext)
        val project = db.projects().get(projectId) ?: return Result.failure()
        val deadline = System.currentTimeMillis() + 8 * 60_000L
        while (System.currentTimeMillis() < deadline) {
            val job = try {
                client.job(jobId)
            } catch (e: BackendException) {
                return if (e.code == 404 || e.code == 401) Result.failure() else Result.retry()
            }
            when (job.status) {
                "completed" -> {
                    return try {
                        ClipImporter.importClips(db, projectId, jobId, client.clips(jobId))
                        Notifier.show(applicationContext, projectId.toInt(), "Clips ready", project.name)
                        Result.success()
                    } catch (e: BackendException) {
                        Result.retry()
                    }
                }
                "failed", "cancelled" -> {
                    Notifier.show(
                        applicationContext,
                        projectId.toInt(),
                        "Clip analysis ${job.status}",
                        job.error ?: project.name,
                    )
                    return Result.failure()
                }
            }
            delay(5_000)
        }
        return Result.retry()
    }

    companion object {
        private const val KEY_PROJECT = "projectId"
        private const val KEY_JOB = "jobId"

        fun enqueue(context: Context, projectId: Long, jobId: String) {
            val request = OneTimeWorkRequestBuilder<AnalysisWorker>()
                .setInputData(workDataOf(KEY_PROJECT to projectId, KEY_JOB to jobId))
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setBackoffCriteria(BackoffPolicy.LINEAR, 30, TimeUnit.SECONDS)
                .build()
            WorkManager.getInstance(context)
                .enqueueUniqueWork("analysis-$projectId", ExistingWorkPolicy.REPLACE, request)
        }

        fun cancel(context: Context, projectId: Long) {
            WorkManager.getInstance(context).cancelUniqueWork("analysis-$projectId")
        }
    }
}
