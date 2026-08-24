package com.winlator.gameport

import android.app.Activity
import android.content.ContentValues
import android.content.Context
import android.os.Build
import android.os.Environment
import android.os.Handler
import android.os.Looper
import android.os.StatFs
import android.provider.MediaStore
import android.widget.Toast
import com.winlator.R
import com.winlator.core.ProcessHelper
import com.winlator.xenvironment.RootFS
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Oyun başlatma tanılaması.
 *
 * NEDEN VAR: Winlator'ın "Starting up..." diyaloğu YALNIZCA oyun ilk
 * penceresini açtığında kapanıyor. Wine çökerse, exe bulunamazsa ya da bir
 * DLL eksikse diyalog sonsuza kadar döner ve hiçbir hata görünmez.
 *
 * Üstelik ProcessHelper.exec(), kayıtlı hiç debug callback yoksa süreç
 * çıktısını /dev/null'a yönlendiriyor — yani logcat'te bile hiçbir şey yok.
 * Bu sınıf bir callback kaydederek o susturmayı kaldırıyor, çıktıyı
 * biriktiriyor ve belirlenen süre sonunda İndirilenler klasörüne yazıyor.
 *
 * Not: Wine'ın gerçekten konuşması için ayrıca `enable_wine_debug`
 * tercihinin açık olması gerekiyor (aksi halde WINEDEBUG=-all veriliyor);
 * onu GameLauncherActivity ayarlıyor.
 */
object GamePortDiagnostics {

    private const val MAX_LINES = 40_000
    private val started = AtomicBoolean(false)

    private val lines = ArrayList<String>(4096)
    private var startedAtMs = 0L
    private var droppedLines = 0

    /**
     * Toplamayı başlatır. XServerDisplayActivity.onCreate içinden, guest
     * süreç başlamadan ÖNCE çağrılmalı (yama bunu garanti ediyor).
     */
    @JvmStatic
    fun start(activity: Activity) {
        val config = GamePortConfig.get(activity)
        if (!config.diagnosticsEnabled) return
        if (!started.compareAndSet(false, true)) return

        startedAtMs = System.currentTimeMillis()
        synchronized(lines) { lines.clear(); droppedLines = 0 }

        val collector = com.winlator.core.Callback<String> { line -> record(line) }
        ProcessHelper.addDebugCallback(collector)

        val seconds = config.diagnosticsSeconds
        record("[gameport] tanılama başladı, ${seconds}s toplanacak")

        Handler(Looper.getMainLooper()).postDelayed({
            ProcessHelper.removeDebugCallback(collector)
            finish(activity, config)
        }, seconds * 1000L)
    }

    private fun record(line: String) {
        val elapsed = System.currentTimeMillis() - startedAtMs
        synchronized(lines) {
            if (lines.size >= MAX_LINES) {
                droppedLines++
                return
            }
            lines.add(String.format(Locale.ENGLISH, "[%7d ms] %s", elapsed, line))
        }
    }

    private fun finish(activity: Activity, config: GamePortConfig) {
        val snapshot = synchronized(lines) { ArrayList(lines) }
        val dropped = synchronized(lines) { droppedLines }

        Executors.newSingleThreadExecutor().execute {
            val report = buildString {
                append(header(activity, config, snapshot.size, dropped))
                append('\n')
                if (snapshot.isEmpty()) {
                    append(
                        "!!! HİÇ ÇIKTI YAKALANMADI.\n" +
                            "Bu, guest sürecin (box64/wine) hiç başlamadığı ya da anında\n" +
                            "öldüğü anlamına gelir. Yukarıdaki 'Container durumu' bölümüne bak.\n"
                    )
                } else {
                    snapshot.forEach { append(it).append('\n') }
                }
                if (dropped > 0) append("\n[... $dropped satır sınır aşımı nedeniyle atlandı]\n")
            }

            val target = writeReport(activity, config, report)
            Handler(Looper.getMainLooper()).post {
                val message = if (target != null) {
                    activity.getString(R.string.gameport_log_saved, target)
                } else {
                    activity.getString(R.string.gameport_log_failed)
                }
                Toast.makeText(activity, message, Toast.LENGTH_LONG).show()
            }
        }
    }

    // ------------------------------------------------------------------ rapor
    private fun header(activity: Activity, config: GamePortConfig,
                       captured: Int, dropped: Int): String {
        val stamp = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.ENGLISH).format(Date())
        val rootFS = RootFS.find(activity)
        val rootDir = rootFS.rootDir
        val containerDir = File(rootDir, "home/${RootFS.USER}-${config.containerId}")
        val exeUnix = dosToUnix(containerDir, config.execPathDos)

        return buildString {
            append("=".repeat(72)).append('\n')
            append("Winlator tek-oyun port — tanılama raporu\n")
            append("=".repeat(72)).append('\n')
            append("Tarih          : $stamp\n")
            append("Toplama süresi : ${config.diagnosticsSeconds}s\n")
            append("Yakalanan satır: $captured${if (dropped > 0) " (+$dropped atlandı)" else ""}\n")
            append('\n')
            append("-- Port --------------------------------------------------------\n")
            append("Uygulama       : ${activity.packageName}\n")
            append("Oyun           : ${config.gameName}\n")
            append("Exe (DOS)      : ${config.execPathDos}\n")
            append("Motor          : ${config.engine} / ${config.scriptingBackend}\n")
            append("Box64 preset   : ${config.box64Preset}\n")
            append("Grafik sürücü  : ${config.graphicsDriver}\n")
            append("DX sarmalayıcı : ${config.dxwrapper}\n")
            append("Exec args      : ${config.execArgs}\n")
            append('\n')
            append("-- Cihaz -------------------------------------------------------\n")
            append("Model          : ${Build.MANUFACTURER} ${Build.MODEL} (${Build.DEVICE})\n")
            append("Android        : ${Build.VERSION.RELEASE} (API ${Build.VERSION.SDK_INT})\n")
            append("ABI            : ${Build.SUPPORTED_ABIS.joinToString()}\n")
            append("Boş alan       : ${freeSpace(activity)}\n")
            append('\n')
            append("-- Container durumu --------------------------------------------\n")
            append(pathLine("rootfs", rootDir))
            append(pathLine("container", containerDir))
            append(pathLine("drive_c", File(containerDir, ".wine/drive_c")))
            append(pathLine("oyun exe", exeUnix))
            append(pathLine("system.reg", File(containerDir, ".wine/system.reg")))
            append("=".repeat(72)).append('\n')
        }
    }

    private fun pathLine(label: String, file: File?): String {
        if (file == null) return String.format(Locale.ENGLISH, "%-14s : ?\n", label)
        val state = when {
            !file.exists() -> "YOK  <-- sorun burada olabilir"
            file.isDirectory -> "dizin (${file.list()?.size ?: 0} girdi)"
            else -> "dosya (${file.length()} bayt)"
        }
        return String.format(Locale.ENGLISH, "%-14s : %s\n                 %s\n",
            label, state, file.absolutePath)
    }

    /** `C:\Games\X\X.exe` -> `<container>/.wine/drive_c/Games/X/X.exe` */
    private fun dosToUnix(containerDir: File, dosPath: String): File? {
        if (!dosPath.startsWith("C:\\", ignoreCase = true)) return null
        val relative = dosPath.substring(3).replace('\\', '/')
        return File(containerDir, ".wine/drive_c/$relative")
    }

    private fun freeSpace(context: Context): String = try {
        val stat = StatFs(context.filesDir.absolutePath)
        val bytes = stat.availableBlocksLong * stat.blockSizeLong
        String.format(Locale.ENGLISH, "%.1f GB", bytes / 1073741824.0)
    } catch (error: Throwable) {
        "?"
    }

    // ------------------------------------------------------------------ yazma
    /**
     * Raporu İndirilenler klasörüne yazar; dönüş kullanıcıya gösterilecek yol.
     *
     * targetSdk 28 olduğu için eski cihazlarda doğrudan dosya yazımı çalışıyor;
     * Android 10+ için MediaStore yolu var. İkisi de olmazsa uygulamanın kendi
     * harici klasörüne düşüyoruz — hiçbir koşulda rapor kaybolmasın.
     */
    private fun writeReport(context: Context, config: GamePortConfig,
                            report: String): String? {
        val stamp = SimpleDateFormat("yyyyMMdd-HHmmss", Locale.ENGLISH).format(Date())
        val safeName = config.gameName.filter { it.isLetterOrDigit() || it == '-' || it == '_' }
            .ifEmpty { "game" }
        val fileName = "winlator-port-$safeName-$stamp.log"

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            runCatching {
                val values = ContentValues().apply {
                    put(MediaStore.Downloads.DISPLAY_NAME, fileName)
                    put(MediaStore.Downloads.MIME_TYPE, "text/plain")
                    put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS)
                }
                val uri = context.contentResolver
                    .insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
                    ?: error("MediaStore kaydı oluşturulamadı")
                context.contentResolver.openOutputStream(uri)?.use {
                    it.write(report.toByteArray(Charsets.UTF_8))
                } ?: error("MediaStore akışı açılamadı")
                return "Download/$fileName"
            }
        }

        runCatching {
            @Suppress("DEPRECATION")
            val downloads =
                Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS)
            if (!downloads.isDirectory) downloads.mkdirs()
            val file = File(downloads, fileName)
            file.writeText(report)
            return "Download/$fileName"
        }

        runCatching {
            val fallback = File(context.getExternalFilesDir(null), fileName)
            fallback.writeText(report)
            return fallback.absolutePath
        }

        return null
    }
}
