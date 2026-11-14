package com.clipzit.app.ui

import android.content.Context
import android.content.Intent
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.ui.graphics.Color
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Share
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.core.content.FileProvider
import com.clipzit.app.AppViewModel
import com.clipzit.app.R
import java.io.File

@Composable
fun Logo(size: Int = 40) {
    Image(
        painter = painterResource(R.drawable.clipzit_logo),
        contentDescription = "Clipzit logo",
        modifier = Modifier.size(size.dp).clip(RoundedCornerShape((size / 4).dp)),
    )
}

@Composable
fun CardBox(modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    Card(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(18.dp),
        colors = CardDefaults.cardColors(containerColor = ClipCard),
        border = BorderStroke(1.dp, ClipBorder),
    ) { content() }
}

@Composable
fun ChipRow(options: List<String>, selected: String, label: (String) -> String = { it }, onSelect: (String) -> Unit) {
    Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        options.forEach { o ->
            FilterChip(selected = selected == o, onClick = { onSelect(o) }, label = { Text(label(o)) })
        }
    }
}

@Composable
fun SwitchRow(label: String, checked: Boolean, onChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Text(label, modifier = Modifier.weight(1f))
        Switch(checked = checked, onCheckedChange = onChange)
    }
}

fun shareFile(context: Context, path: String, mime: String) {
    val uri = FileProvider.getUriForFile(context, "${context.packageName}.files", File(path))
    val send = Intent(Intent.ACTION_SEND).apply {
        type = mime
        putExtra(Intent.EXTRA_STREAM, uri)
        addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    }
    context.startActivity(Intent.createChooser(send, "Share"))
}

val captionPresets = listOf("creator", "bold", "karaoke", "highlight", "clean", "minimal", "professional")
val platforms = listOf("custom", "tiktok", "shorts", "reels", "facebook", "vertical")
val pacingLevels = listOf("off", "light", "medium", "strong")

/** Export defaults for backend rendering. Stored in preferences and applied to every backend render. */
@Composable
fun RenderOptionsPanel(vm: AppViewModel, onPlatformChange: (String) -> Unit = {}) {
    val p = vm.prefs
    var platform by remember { mutableStateOf(p.platform) }
    var preset by remember { mutableStateOf(p.captionPreset) }
    var pacing by remember { mutableStateOf(p.pacing) }
    var captions by remember { mutableStateOf(p.captionsOn) }
    var fillers by remember { mutableStateOf(p.removeFillers) }
    var face by remember { mutableStateOf(p.autoFrame) }
    var hook by remember { mutableStateOf(p.hookOverlay) }
    var mark by remember { mutableStateOf(p.watermark) }
    CardBox {
        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("Backend render options", fontWeight = FontWeight.SemiBold)
            Text(
                "Applied when you export with captions or as a ZIP. On-device export only trims and reframes.",
                color = ClipMuted,
                style = MaterialTheme.typography.bodySmall,
            )
            Text("Platform preset", style = MaterialTheme.typography.labelLarge)
            ChipRow(platforms, platform) {
                platform = it
                p.platform = it
                onPlatformChange(it)
            }
            SwitchRow("Burn in captions", captions) { captions = it; p.captionsOn = it }
            if (captions) {
                Text("Caption style", style = MaterialTheme.typography.labelLarge)
                ChipRow(captionPresets, preset) { preset = it; p.captionPreset = it }
            }
            Text("Remove pauses", style = MaterialTheme.typography.labelLarge)
            ChipRow(pacingLevels, pacing) { pacing = it; p.pacing = it }
            SwitchRow("Remove filler words (um, uh)", fillers) { fillers = it; p.removeFillers = it }
            SwitchRow("Follow the main face (needs OpenCV on server)", face) { face = it; p.autoFrame = it }
            SwitchRow("Show hook text over the first 3 seconds", hook) { hook = it; p.hookOverlay = it }
            OutlinedTextField(
                value = mark,
                onValueChange = { mark = it; p.watermark = it },
                label = { Text("Watermark text (optional, yours only)") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
        }
    }
}

/** Progress, errors and Save/Share buttons for the most recent export. */
@Composable
fun ExportStatus(vm: AppViewModel, saveName: String, mime: String) {
    val export = vm.export ?: return
    val ctx = LocalContext.current
    val saver = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument(mime)) { uri ->
        if (uri != null) vm.saveExportTo(uri)
    }
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(export.label, fontWeight = FontWeight.Medium)
        if (export.running) {
            LinearProgressIndicator(progress = { export.progress }, modifier = Modifier.fillMaxWidth())
            OutlinedButton(onClick = { vm.cancelExport() }) { Text("Cancel") }
        }
        export.error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        export.file?.let { path ->
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                Button(onClick = { saver.launch(saveName) }) { Text("Save to device") }
                OutlinedButton(onClick = { shareFile(ctx, path, mime) }) {
                    Icon(Icons.Default.Share, contentDescription = null)
                    Spacer(Modifier.width(6.dp))
                    Text("Share")
                }
            }
        }
    }
}

@Composable
fun Step(number: String, text: String) {
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        Box(Modifier.size(28.dp).clip(CircleShape).background(ClipYellow), contentAlignment = Alignment.Center) {
            Text(number, color = Color.Black, fontWeight = FontWeight.Bold)
        }
        Text(text, modifier = Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium)
    }
}
