package com.clipzit.app.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

val ClipYellow = Color(0xFFFFD400)
val ClipYellow2 = Color(0xFFFFC400)
val ClipBg = Color(0xFF090909)
val ClipSurface = Color(0xFF111111)
val ClipCard = Color(0xFF171717)
val ClipElevated = Color(0xFF202020)
val ClipMuted = Color(0xFFA1A1AA)
val ClipBorder = Color(0xFF292929)

private val scheme = darkColorScheme(
    primary = ClipYellow,
    onPrimary = Color.Black,
    secondary = ClipYellow2,
    onSecondary = Color.Black,
    background = ClipBg,
    onBackground = Color.White,
    surface = ClipSurface,
    onSurface = Color.White,
    surfaceVariant = ClipCard,
    onSurfaceVariant = ClipMuted,
    outline = ClipBorder,
    error = Color(0xFFFF6B6B),
)

@Composable
fun ClipzitTheme(content: @Composable () -> Unit) {
    MaterialTheme(colorScheme = scheme, content = content)
}
