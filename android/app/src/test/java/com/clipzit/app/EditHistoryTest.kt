package com.clipzit.app

import com.clipzit.app.media.EditHistory
import com.clipzit.app.media.EditState
import com.clipzit.app.media.platformLimitSeconds
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class EditHistoryTest {
    private val a = EditState(0, 10_000, "9:16", 0.5f)
    private val b = a.copy(startMs = 1_000)
    private val c = b.copy(endMs = 8_000)

    @Test
    fun undoAndRedoWalkTheHistory() {
        val h = EditHistory(a)
        h.push(b)
        h.push(c)
        assertEquals(c, h.current)
        assertEquals(b, h.undo())
        assertEquals(a, h.undo())
        assertNull(h.undo())
        assertEquals(b, h.redo())
        assertEquals(c, h.redo())
        assertNull(h.redo())
    }

    @Test
    fun newEditClearsRedo() {
        val h = EditHistory(a)
        h.push(b)
        h.undo()
        assertTrue(h.canRedo)
        h.push(c)
        assertFalse(h.canRedo)
        assertEquals(c, h.current)
    }

    @Test
    fun identicalStateIsNotAStep() {
        val h = EditHistory(a)
        h.push(a)
        assertFalse(h.canUndo)
    }

    @Test
    fun historyIsBounded() {
        val h = EditHistory(a, limit = 3)
        for (i in 1..10) h.push(a.copy(startMs = i * 100L))
        var steps = 0
        while (h.undo() != null) steps++
        assertEquals(3, steps)
    }

    @Test
    fun platformLimitsMatchTheBackend() {
        assertEquals(180, platformLimitSeconds("shorts"))
        assertEquals(90, platformLimitSeconds("reels"))
        assertEquals(600, platformLimitSeconds("tiktok"))
        assertNull(platformLimitSeconds("custom"))
    }
}
