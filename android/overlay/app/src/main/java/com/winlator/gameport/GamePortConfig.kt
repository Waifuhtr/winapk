package com.winlator.gameport

import android.content.Context
import org.json.JSONObject

/**
 * AŞAMA A'nın ürettiği `gameport/game_config.json` dosyasının tipli okuyucusu.
 *
 * Bu dosya iki aşama arasındaki tek sözleşmedir: Android tarafı Wine kurulum
 * mantığını bilmez, sadece burada yazan değerleri container config'ine ve
 * kısayolun [Extra Data] bölümüne aktarır.
 *
 * Kullanıcıya hiçbir ayar gösterilmez; buradaki değerler build-time sabitidir.
 */
class GamePortConfig private constructor(root: JSONObject) {

    private val gameNode: JSONObject = root.optJSONObject("game") ?: JSONObject()
    private val containerNode: JSONObject = root.optJSONObject("container") ?: JSONObject()
    private val shortcutNode: JSONObject = root.optJSONObject("shortcut") ?: JSONObject()
    private val payloadNode: JSONObject = root.optJSONObject("payload") ?: JSONObject()

    val schemaVersion: Int = root.optInt("schemaVersion", 1)
    val appLabel: String = root.optString("appLabel", "Game")
    val rootfsVersion: Int = root.optInt("rootfsVersion", 0)

    /** Oyunun görünen adı; container adı olarak da kullanılır. */
    val gameName: String = gameNode.optString("name", "Game")

    /** `C:\Games\<Ad>\<Ad>.exe` biçiminde tam DOS yolu. */
    val execPathDos: String = gameNode.optString("execPathDos", "")
    val execDirDos: String = gameNode.optString("execDirDos", "")
    val execFile: String = gameNode.optString("execFile", "")

    val engine: String = gameNode.optString("engine", "")
    val scriptingBackend: String = gameNode.optString("scriptingBackend", "")

    // --- container ayarları (Container.saveData() alan adlarıyla birebir) ---
    val containerId: Int = containerNode.optInt("id", 1)
    val screenSize: String = containerNode.optString("screenSize", "1280x720")
    val envVars: String = containerNode.optString("envVars", "")
    val graphicsDriver: String = containerNode.optString("graphicsDriver", "vortek,gladio")
    val dxwrapper: String = containerNode.optString("dxwrapper", "dxvk")
    val audioDriver: String = containerNode.optString("audioDriver", "alsa")
    val box64Preset: String = containerNode.optString("box64Preset", "STABILITY")
    val wincomponents: String = containerNode.optString("wincomponents", "")
    val desktopTheme: String = containerNode.optString("desktopTheme", "")
    val startupSelection: Int = containerNode.optInt("startupSelection", 1)
    val drives: String = containerNode.optString("drives", "")

    // --- kısayol [Extra Data] alanları ---
    val execArgs: String = shortcutNode.optString("execArgs", "")
    val forceFullscreen: String = shortcutNode.optString("forceFullscreen", "1")
    val controlsProfile: String = shortcutNode.optString("controlsProfile", "")

    // --- payload ---
    val payloadFile: String = payloadNode.optString("file", "game_payload.tzst")

    /** AŞAMA A'nın prefix'ten sildiği, cihazda da silinmesi gereken yollar. */
    val removedPaths: List<String> = payloadNode.optJSONArray("removedPaths")
        ?.let { array -> (0 until array.length()).map { array.getString(it) } }
        ?: emptyList()

    /**
     * Payload'ın kimliği. Bu değişince cihazdaki container yeniden kurulur;
     * böylece APK güncellemesi oyunu da güncelleyebilir.
     */
    val payloadStamp: String =
        "${schemaVersion}:${payloadNode.optInt("changedCount", 0)}:" +
            "${payloadNode.optLong("packedBytes", 0L)}:$execPathDos"

    /** Kısayolun [Extra Data] bölümüne yazılacak tüm alanlar. */
    fun shortcutExtras(): Map<String, String> {
        val extras = LinkedHashMap<String, String>()
        val keys = shortcutNode.keys()
        while (keys.hasNext()) {
            val key = keys.next()
            val value = shortcutNode.optString(key, "")
            if (value.isNotEmpty()) extras[key] = value
        }
        return extras
    }

    companion object {
        const val ASSET_DIR = "gameport"
        const val CONFIG_ASSET = "$ASSET_DIR/game_config.json"

        @Volatile
        private var cached: GamePortConfig? = null

        fun get(context: Context): GamePortConfig =
            cached ?: synchronized(this) {
                cached ?: load(context).also { cached = it }
            }

        private fun load(context: Context): GamePortConfig {
            val text = context.assets.open(CONFIG_ASSET).use { input ->
                input.readBytes().toString(Charsets.UTF_8)
            }
            return GamePortConfig(JSONObject(text))
        }
    }
}
