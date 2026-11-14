package com.clipzit.app.media

import kotlin.math.abs
import kotlin.math.roundToInt

/** Pure framing math (unit-tested). Produces Media3 Crop values in normalised device coordinates. */
object Framing {
    val aspects = listOf("Original", "9:16", "1:1", "16:9", "4:5")

    fun ratio(aspect: String): Float? = when (aspect) {
        "9:16" -> 9f / 16f
        "1:1" -> 1f
        "16:9" -> 16f / 9f
        "4:5" -> 4f / 5f
        else -> null
    }

    data class Crop(
        val left: Float,
        val right: Float,
        val bottom: Float,
        val top: Float,
        val outWidth: Int,
        val outHeight: Int,
    )

    private fun even(v: Float): Int = maxOf(2, (v.roundToInt() / 2) * 2)

    /** Returns null when no crop is needed (Original, or source already has the target ratio). */
    fun compute(srcWidth: Int, srcHeight: Int, aspect: String, cropX: Float): Crop? {
        val target = ratio(aspect) ?: return null
        if (srcWidth <= 0 || srcHeight <= 0) return null
        val source = srcWidth.toFloat() / srcHeight.toFloat()
        val cx = cropX.coerceIn(0f, 1f)
        return when {
            abs(target - source) < 0.005f -> null
            target < source -> {
                val f = target / source // visible fraction of width
                val center = (2f * cx - 1f) * (1f - f)
                Crop(center - f, center + f, -1f, 1f, even(srcHeight * target), even(srcHeight.toFloat()))
            }
            else -> {
                val f = source / target // visible fraction of height
                Crop(-1f, 1f, -f, f, even(srcWidth.toFloat()), even(srcWidth / target))
            }
        }
    }
}
