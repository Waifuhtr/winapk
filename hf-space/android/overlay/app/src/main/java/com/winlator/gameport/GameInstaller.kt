package com.winlator.gameport

import android.content.Context
import android.os.Process
import com.winlator.container.Container
import com.winlator.container.ContainerManager
import com.winlator.core.FileUtils
import com.winlator.core.TarCompressorUtils
import com.winlator.xenvironment.RootFS
import com.winlator.xenvironment.RootFSInstaller
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File

/**
 * Tek oyunluk kurulumun tamamı.
 *
 * Telefonda ilk açılışta yapılanlar, AŞAMA A'da build makinesinde yapılanların
 * aynısıdır — sadece sıra farklı: burada Winlator'ın kendi container akışı
 * çalışır, sonra AŞAMA A'nın çıkardığı delta onun üzerine serilir. Delta,
 * pristine container'a göre hesaplandığı için sonuç birebir aynı olur.
 *
 * Ana thread hiçbir noktada bloklanmaz; ilerleme [state] üzerinden akar.
 */
class GameInstaller(private val context: Context) {

    sealed interface Phase {
        val label: String

        data object Idle : Phase { override val label = "" }
        data object CheckingSystem : Phase { override val label = "Sistem denetleniyor" }
        data object InstallingRootFS : Phase { override val label = "Sistem dosyaları kuruluyor" }
        data object CreatingContainer : Phase { override val label = "Ortam hazırlanıyor" }
        data object ApplyingPayload : Phase { override val label = "Oyun dosyaları açılıyor" }
        data object Finalizing : Phase { override val label = "Son ayarlar" }
        data object Ready : Phase { override val label = "Başlatılıyor" }
        data class Failed(val message: String) : Phase { override val label = "Hata" }
    }

    data class State(
        val phase: Phase = Phase.Idle,
        /** 0f..1f; belirsizse -1f. */
        val progress: Float = -1f,
    )

    private val _state = MutableStateFlow(State())
    val state: StateFlow<State> = _state.asStateFlow()

    /**
     * Kurulumun gerekli kısımlarını yapar ve başlatmaya hazır container'ı döner.
     * Zaten kuruluysa hiçbir şey yapmadan hızlıca döner.
     */
    suspend fun ensureInstalled(): Result<LaunchTarget> = withContext(Dispatchers.IO) {
        // Kurulum I/O ağırlıklı; arka plan önceliği vererek UI'ın akıcı
        // kalmasını sağlıyoruz. (Oyunun kendi thread'lerine karışmıyoruz —
        // Wine süreci Winlator'ın kendi ProcessHelper'ı tarafından başlatılır.)
        runCatching { Process.setThreadPriority(Process.THREAD_PRIORITY_BACKGROUND) }

        try {
            val config = GamePortConfig.get(context)
            emit(Phase.CheckingSystem, -1f)

            installRootFsIfNeeded()
            val container = createContainerIfNeeded(config)
            applyPayloadIfNeeded(config, container)
            val shortcut = writeShortcut(config, container)

            emit(Phase.Ready, 1f)
            Result.success(LaunchTarget(container.id, shortcut.absolutePath))
        } catch (error: Throwable) {
            val message = error.message ?: error.javaClass.simpleName
            emit(Phase.Failed(message), -1f)
            Result.failure(error)
        }
    }

    data class LaunchTarget(val containerId: Int, val shortcutPath: String)

    // ------------------------------------------------------------------ 1/4
    private fun installRootFsIfNeeded() {
        val rootFS = RootFS.find(context)
        if (rootFS.isValid && rootFS.version >= RootFSInstaller.LATEST_VERSION) return

        emit(Phase.InstallingRootFS, 0f)
        val rootDir = rootFS.rootDir
        clearRootDir(rootDir)

        val total = TarCompressorUtils.getContentLength(
            TarCompressorUtils.Type.ZSTD, context, RootFSInstaller.FILENAME, rootDir,
        )
        var written = 0L
        val ok = TarCompressorUtils.extract(
            TarCompressorUtils.Type.ZSTD, context, RootFSInstaller.FILENAME, rootDir,
        ) { file, size ->
            file.parentFile?.let { if (!it.isDirectory) it.mkdirs() }
            if (size > 0 && total > 0) {
                written += size
                emit(Phase.InstallingRootFS, (written.toFloat() / total).coerceIn(0f, 1f))
            }
            file
        }
        if (!ok) {
            throw IllegalStateException(
                "Sistem dosyaları açılamadı (rootfs). Cihazda yeterli boş alan " +
                    "olmayabilir."
            )
        }
        rootFS.createRFSVersionFile(RootFSInstaller.LATEST_VERSION.toInt())
    }

    /**
     * RootFSInstaller.clearRootDir() ile aynı davranış: home ve opt korunur,
     * geri kalanı silinir. Böylece güncellemede oyun kayıtları kaybolmaz.
     */
    private fun clearRootDir(rootDir: File) {
        if (!rootDir.isDirectory) {
            rootDir.mkdirs()
            return
        }
        rootDir.listFiles()?.forEach { file ->
            val keep = file.isDirectory && (file.name == "home" || file.name == "opt")
            if (!keep) FileUtils.delete(file)
        }
    }

    // ------------------------------------------------------------------ 2/4
    private suspend fun createContainerIfNeeded(config: GamePortConfig): Container {
        val manager = ContainerManager(context)
        val existing = manager.getContainerById(config.containerId)
        if (existing != null) {
            manager.activateContainer(existing)
            return existing
        }

        emit(Phase.CreatingContainer, -1f)
        val data = JSONObject().apply {
            put("name", config.gameName)
            put("screenSize", config.screenSize)
            put("envVars", config.envVars)
            put("graphicsDriver", config.graphicsDriver)
            put("dxwrapper", config.dxwrapper)
            put("audioDriver", config.audioDriver)
            put("box64Preset", config.box64Preset)
            if (config.wincomponents.isNotEmpty()) put("wincomponents", config.wincomponents)
            if (config.desktopTheme.isNotEmpty()) put("desktopTheme", config.desktopTheme)
            put("startupSelection", config.startupSelection)
            // Boş bırakırsak Container.DEFAULT_DRIVES devreye girer (D:/E: harici
            // depolama). Tek oyunluk kurulumda dış sürücüye ihtiyaç yok.
            put("drives", config.drives)
        }

        // createContainerAsync icerideki Handler()'i olusturuyor; Looper'i olan
        // main thread'den cagirmak zorundayiz. Sonucu CompletableDeferred ile
        // topluyoruz: createContainer() basarisizlikta null donuyor, bu yuzden
        // beklenen tip acikca Container? olmali.
        val created = CompletableDeferred<Container?>()
        withContext(Dispatchers.Main) {
            manager.createContainerAsync(data) { container -> created.complete(container) }
        }
        val container = created.await()
            ?: throw IllegalStateException("Ortam oluşturulamadı (container).")

        manager.activateContainer(container)
        return container
    }

    // ------------------------------------------------------------------ 3/4
    private fun applyPayloadIfNeeded(config: GamePortConfig, container: Container) {
        val stampFile = File(container.rootDir, ".gameport_stamp")
        if (stampFile.isFile && FileUtils.readString(stampFile) == config.payloadStamp) return

        emit(Phase.ApplyingPayload, 0f)
        val asset = "${GamePortConfig.ASSET_DIR}/${config.payloadFile}"
        val target = container.rootDir

        val total = TarCompressorUtils.getContentLength(
            TarCompressorUtils.Type.ZSTD, context, asset, target,
        )
        var written = 0L
        val ok = TarCompressorUtils.extract(
            TarCompressorUtils.Type.ZSTD, context, asset, target,
        ) { file, size ->
            // Winlator'in cikarici mkdirs()'i YALNIZCA dizin girdileri icin
            // cagiriyor; normal dosyalarda dogrudan FileOutputStream aciyor.
            // Ust dizin yoksa FileNotFoundException aliniyor ve extract()
            // sessizce false donuyor. Payload artik dizin girdileri de
            // tasiyor, ama burada da garantiye aliyoruz: bu dinleyici dosya
            // yazilmadan ONCE cagriliyor, yani dogru kanca burasi.
            file.parentFile?.let { if (!it.isDirectory) it.mkdirs() }
            if (size > 0 && total > 0) {
                written += size
                emit(Phase.ApplyingPayload, (written.toFloat() / total).coerceIn(0f, 1f))
            }
            file
        }
        if (!ok) {
            throw IllegalStateException(
                "Oyun dosyaları açılamadı (payload). Arşiv bozuk olabilir ya da " +
                    "cihazda yeterli boş alan yok."
            )
        }

        // AŞAMA A'da prefix'ten silinenler burada da silinmeli.
        config.removedPaths.forEach { relative ->
            FileUtils.delete(File(target, relative))
        }

        FileUtils.writeString(stampFile, config.payloadStamp)
    }

    // ------------------------------------------------------------------ 4/4
    private fun writeShortcut(config: GamePortConfig, container: Container): File {
        emit(Phase.Finalizing, -1f)

        val desktopDir = File(container.userDir, "Desktop")
        if (!desktopDir.isDirectory) desktopDir.mkdirs()
        val file = File(desktopDir, "${sanitize(config.gameName)}.desktop")

        val builder = StringBuilder()
        builder.append("[Desktop Entry]\n")
        builder.append("Name=").append(config.gameName).append('\n')
        builder.append("Type=Application\n")
        builder.append("StartupWMClass=").append(config.execFile).append('\n')
        builder.append("Exec=wine ").append(escapeExecPath(config.execPathDos)).append('\n')
        builder.append("\n[Extra Data]\n")
        config.shortcutExtras().forEach { (key, value) ->
            builder.append(key).append('=').append(value).append('\n')
        }

        if (!FileUtils.writeString(file, builder.toString())) {
            throw IllegalStateException("Kısayol yazılamadı: ${file.absolutePath}")
        }
        return file
    }

    private fun sanitize(name: String): String =
        name.filter { it.isLetterOrDigit() || it == ' ' || it == '-' || it == '_' }
            .trim()
            .ifEmpty { "Game" }

    companion object {
        /**
         * `Exec=` satırındaki DOS yolunun kaçış biçimi.
         *
         * Shortcut.java yolu `StringUtils.unescapeDOSPath()` ile çözer ve o
         * fonksiyon ters-bölü silen regex'i İKİ KEZ uygular. Dolayısıyla yolun
         * `escapeDOSPath` uygulanmış hâlinin bir kez daha kaçışlanmış olması,
         * yani ayıraç başına DÖRT ters-bölü gerekir. Bu biçim, boşluklu ve
         * boşluksuz yollar için ayrıştırıcı simüle edilerek doğrulanmıştır:
         *
         *   C:\Games\My Game\g.exe
         *     -> Exec=wine C:\\\\Games\\\\My\ Game\\\\g.exe
         *     -> unescapeDOSPath -> C:\Games\My Game\g.exe
         */
        fun escapeExecPath(dosPath: String): String =
            dosPath.replace("\\", "\\\\\\\\").replace(" ", "\\ ")
    }

    private fun emit(phase: Phase, progress: Float) {
        _state.value = State(phase, progress)
    }
}
