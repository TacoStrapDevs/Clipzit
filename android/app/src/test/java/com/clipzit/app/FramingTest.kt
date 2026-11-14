package com.clipzit.app

import com.clipzit.app.media.Framing
import com.clipzit.app.media.formatMs
import com.clipzit.app.media.safeFileName
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.abs

class FramingTest {
    @Test
    fun originalNeedsNoCrop() {
        assertNull(Framing.compute(1920, 1080, "Original", 0.5f))
    }

    @Test
    fun landscapeToVerticalCropsWidthAndCentres() {
        val c = Framing.compute(1920, 1080, "9:16", 0.5f)!!
        assertEquals(-c.right, c.left, 1e-4f)
        assertEquals(-1f, c.bottom, 0f)
        assertEquals(1f, c.top, 0f)
        assertEquals(1080, c.outHeight)
        assertTrue(abs(c.outWidth.toFloat() / c.outHeight - 9f / 16f) < 0.01f)
        assertEquals(0, c.outWidth % 2)
        assertEquals(0, c.outHeight % 2)
    }

    @Test
    fun cropPositionMovesWindowButStaysInsideFrame() {
        val left = Framing.compute(1920, 1080, "9:16", 0f)!!
        val right = Framing.compute(1920, 1080, "9:16", 1f)!!
        assertEquals(-1f, left.left, 1e-4f)
        assertEquals(1f, right.right, 1e-4f)
        for (cx in listOf(0f, 0.25f, 0.5f, 0.75f, 1f)) {
            val c = Framing.compute(1920, 1080, "9:16", cx)!!
            assertTrue(c.left >= -1.0001f && c.right <= 1.0001f)
            assertEquals(right.right - right.left, c.right - c.left, 1e-4f)
        }
    }

    @Test
    fun portraitSourceToLandscapeCropsHeight() {
        val c = Framing.compute(1080, 1920, "16:9", 0.5f)!!
        assertEquals(-1f, c.left, 0f)
        assertEquals(1f, c.right, 0f)
        assertEquals(1080, c.outWidth)
        assertTrue(abs(c.outWidth.toFloat() / c.outHeight - 16f / 9f) < 0.02f)
    }

    @Test
    fun squareFromLandscape() {
        val c = Framing.compute(1280, 720, "1:1", 0.5f)
        assertNotNull(c)
        assertEquals(c!!.outWidth, c.outHeight)
    }

    @Test
    fun matchingRatioNeedsNoCrop() {
        assertNull(Framing.compute(1920, 1080, "16:9", 0.5f))
    }

    @Test
    fun invalidInputIsSafe() {
        assertNull(Framing.compute(0, 0, "9:16", 0.5f))
        assertNull(Framing.compute(1920, 1080, "bogus", 0.5f))
    }

    @Test
    fun timeFormatting() {
        assertEquals("0:00.0", formatMs(0))
        assertEquals("1:05.5", formatMs(65_500))
        assertEquals("12:00.0", formatMs(720_000))
    }

    @Test
    fun fileNamesAreSafe() {
        assertEquals("My_clip", safeFileName("My clip!"))
        assertEquals("clip", safeFileName("///"))
        assertTrue(safeFileName("x".repeat(200)).length <= 40)
    }
}
