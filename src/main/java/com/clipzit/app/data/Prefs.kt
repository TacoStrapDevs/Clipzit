package com.clipzit.app.data

import android.content.Context
import android.content.SharedPreferences

class Prefs(context: Context) {
    private val sp: SharedPreferences =
        context.applicationContext.getSharedPreferences("clipzit_prefs", Context.MODE_PRIVATE)

    private fun str(key: String, default: String) = sp.getString(key, default) ?: default

    var backendUrl: String
        get() = str("backend_url", "")
        set(v) = sp.edit().putString("backend_url", v.trim()).apply()

    var apiToken: String
        get() = str("api_token", "")
        set(v) = sp.edit().putString("api_token", v.trim()).apply()

    var quality: String
        get() = str("quality", "balanced")
        set(v) = sp.edit().putString("quality", v).apply()

    var language: String
        get() = str("language", "")
        set(v) = sp.edit().putString("language", v.trim()).apply()

    var platform: String
        get() = str("platform", "custom")
        set(v) = sp.edit().putString("platform", v).apply()

    var captionsOn: Boolean
        get() = sp.getBoolean("captions_on", true)
        set(v) = sp.edit().putBoolean("captions_on", v).apply()

    var captionPreset: String
        get() = str("caption_preset", "creator")
        set(v) = sp.edit().putString("caption_preset", v).apply()

    var pacing: String
        get() = str("pacing", "off")
        set(v) = sp.edit().putString("pacing", v).apply()

    var removeFillers: Boolean
        get() = sp.getBoolean("remove_fillers", false)
        set(v) = sp.edit().putBoolean("remove_fillers", v).apply()

    var autoFrame: Boolean
        get() = sp.getBoolean("auto_frame", false)
        set(v) = sp.edit().putBoolean("auto_frame", v).apply()

    var hookOverlay: Boolean
        get() = sp.getBoolean("hook_overlay", false)
        set(v) = sp.edit().putBoolean("hook_overlay", v).apply()

    var watermark: String
        get() = str("watermark", "")
        set(v) = sp.edit().putString("watermark", v).apply()
}
