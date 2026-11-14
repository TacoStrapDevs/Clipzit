package com.clipzit.app.ui

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.clipzit.app.AppViewModel
import com.clipzit.app.data.ExportRecord
import com.clipzit.app.media.safeFileName
import java.io.File
import java.text.DateFormat
import java.util.Date

@Composable
fun ExportsScreen(vm: AppViewModel, modifier: Modifier = Modifier) {
    val records by vm.exports.collectAsState()
    val ctx = LocalContext.current
    var pending by remember { mutableStateOf<ExportRecord?>(null) }
    val videoSaver = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("video/mp4")) { uri ->
        val p = pending?.path
        if (uri != null && p != null) vm.saveFileTo(p, uri)
    }
    val zipSaver = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("application/zip")) { uri ->
        val p = pending?.path
        if (uri != null && p != null) vm.saveFileTo(p, uri)
    }
    LazyColumn(
        modifier = modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Logo(36)
                Spacer(Modifier.width(10.dp))
                Text("Export history", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
            }
        }
        if (records.isEmpty()) {
            item { Text("Nothing exported yet.", color = ClipMuted) }
        }
        items(records, key = { it.id }) { r ->
            val exists = r.ok && r.path != null && File(r.path).exists()
            CardBox {
                Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text(r.title, fontWeight = FontWeight.SemiBold, maxLines = 1)
                            Text(
                                DateFormat.getDateTimeInstance(DateFormat.MEDIUM, DateFormat.SHORT).format(Date(r.createdAt)) +
                                    " · " + when (r.kind) {
                                        "device" -> "on device"
                                        "captioned" -> "backend render"
                                        else -> "ZIP"
                                    },
                                color = ClipMuted,
                                style = MaterialTheme.typography.bodySmall,
                            )
                        }
                        IconButton(onClick = { vm.deleteExport(r) }) {
                            Icon(Icons.Default.Delete, contentDescription = "Delete export", tint = ClipMuted)
                        }
                    }
                    when {
                        !r.ok -> Text("Failed: ${r.error ?: "unknown error"}", color = MaterialTheme.colorScheme.error,
                            style = MaterialTheme.typography.bodySmall)
                        !exists -> Text("The temporary file was cleared. Export again to get a new copy.", color = ClipMuted,
                            style = MaterialTheme.typography.bodySmall)
                        else -> Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            val zip = r.kind == "zip"
                            TextButton(onClick = {
                                pending = r
                                val name = safeFileName(r.title) + if (zip) ".zip" else ".mp4"
                                if (zip) zipSaver.launch(name) else videoSaver.launch(name)
                            }) { Text("Save to device") }
                            TextButton(onClick = {
                                shareFile(ctx, r.path!!, if (zip) "application/zip" else "video/mp4")
                            }) { Text("Share") }
                        }
                    }
                }
            }
        }
    }
}
