package com.clipzit.app.media

data class EditState(
    val startMs: Long,
    val endMs: Long,
    val aspect: String,
    val cropX: Float,
)

/** Undo/redo stack for the editor. Pure Kotlin so it can be unit-tested. */
class EditHistory(initial: EditState, private val limit: Int = 100) {
    private val undoStack = ArrayList<EditState>()
    private val redoStack = ArrayList<EditState>()

    var current: EditState = initial
        private set

    val canUndo: Boolean get() = undoStack.isNotEmpty()
    val canRedo: Boolean get() = redoStack.isNotEmpty()

    /** Records [next] as a new step. Identical states are ignored; a new step clears the redo stack. */
    fun push(next: EditState) {
        if (next == current) return
        undoStack.add(current)
        if (undoStack.size > limit) undoStack.removeAt(0)
        redoStack.clear()
        current = next
    }

    fun undo(): EditState? {
        if (undoStack.isEmpty()) return null
        redoStack.add(current)
        current = undoStack.removeAt(undoStack.size - 1)
        return current
    }

    fun redo(): EditState? {
        if (redoStack.isEmpty()) return null
        undoStack.add(current)
        current = redoStack.removeAt(redoStack.size - 1)
        return current
    }
}

/** Longest allowed clip per platform preset, in seconds (null = unlimited). Mirrors the backend. */
fun platformLimitSeconds(platform: String): Int? = when (platform) {
    "tiktok" -> 600
    "shorts" -> 180
    "reels", "facebook" -> 90
    else -> null
}
