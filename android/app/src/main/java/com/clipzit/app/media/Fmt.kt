package com.clipzit.app.media

import java.util.Locale

fun formatMs(ms: Long): String {
    val m = ms / 60_000
    val s = (ms % 60_000) / 1000.0
    return String.format(Locale.US, "%d:%04.1f", m, s)
}

fun safeFileName(name: String): String {
    val cleaned = name.map { if (it.isLetterOrDigit()) it else '_' }.joinToString("").trim('_')
    return cleaned.take(40).ifEmpty { "clip" }
}
