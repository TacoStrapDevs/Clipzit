package com.clipzit.app

import com.clipzit.app.ui.parseSignals
import com.clipzit.app.ui.signalLabels
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class SignalsTest {
    @Test
    fun parsesBackendSignalLines() {
        val m = parseSignals("hook:82\ncompleteness:100\nbroken\nclarity:x\ndelivery: 61")
        assertEquals(82, m["hook"])
        assertEquals(100, m["completeness"])
        assertEquals(61, m["delivery"])
        assertEquals(3, m.size)
    }

    @Test
    fun everyBackendSignalHasALabel() {
        val keys = signalLabels.map { it.first }.toSet()
        assertTrue(
            keys.containsAll(
                setOf("hook", "completeness", "coherence", "information", "clarity", "emotion", "standalone",
                    "delivery", "pacing", "duration"),
            ),
        )
    }
}
