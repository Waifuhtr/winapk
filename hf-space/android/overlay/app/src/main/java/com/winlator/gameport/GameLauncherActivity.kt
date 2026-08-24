package com.winlator.gameport

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.view.View
import android.view.WindowManager
import android.widget.ProgressBar
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import androidx.preference.PreferenceManager
import com.winlator.R
import com.winlator.XServerDisplayActivity
import kotlinx.coroutines.launch

/**
 * Uygulamanın TEK giriş noktası.
 *
 * Kullanıcı hiçbir container listesi, ayar menüsü ya da "oyun ekle" akışı
 * görmez: uygulama açılır, gerekiyorsa kurulumu yapar, sonra doğrudan oyunu
 * başlatır. Winlator'ın MainActivity'si ve editör ekranları manifest'ten
 * çıkarılmıştır.
 */
class GameLauncherActivity : AppCompatActivity() {

    private lateinit var installer: GameInstaller
    private lateinit var statusText: TextView
    private lateinit var detailText: TextView
    private lateinit var progressBar: ProgressBar

    private var launched = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        setContentView(R.layout.game_launcher_activity)

        statusText = findViewById(R.id.TVStatus)
        detailText = findViewById(R.id.TVDetail)
        progressBar = findViewById(R.id.PBInstall)

        findViewById<TextView>(R.id.TVTitle).text = GamePortConfig.get(this).gameName

        installer = GameInstaller(applicationContext)
        observeInstaller()

        if (hasStoragePermission()) start() else requestStoragePermission()
    }

    override fun onResume() {
        super.onResume()
        // Oyundan geri dönüldüğünde kurulumu tekrar tetiklemeyelim; oyun
        // kapandıysa uygulama da kapansın.
        if (launched) finish()
    }

    // ------------------------------------------------------------------ izin
    private fun hasStoragePermission(): Boolean {
        // targetSdk 28 olduğu için Android 10 ve üstünde legacy depolama
        // kullanılıyor; izin yalnızca Android 9 ve altında anlamlı.
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) return true
        return ContextCompat.checkSelfPermission(
            this, Manifest.permission.WRITE_EXTERNAL_STORAGE,
        ) == PackageManager.PERMISSION_GRANTED
    }

    private fun requestStoragePermission() {
        ActivityCompat.requestPermissions(
            this, arrayOf(Manifest.permission.WRITE_EXTERNAL_STORAGE), REQUEST_STORAGE,
        )
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray,
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode != REQUEST_STORAGE) return
        if (grantResults.isNotEmpty() && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
            start()
        } else {
            showFailure(getString(R.string.gameport_permission_required))
        }
    }

    // --------------------------------------------------------------- kurulum
    private fun start() {
        lifecycleScope.launch {
            val result = installer.ensureInstalled()
            result.onSuccess(::launchGame)
            // Hata durumu observeInstaller() üzerinden zaten ekrana yansıyor.
        }
    }

    private fun observeInstaller() {
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                installer.state.collect { state -> render(state) }
            }
        }
    }

    private fun render(state: GameInstaller.State) {
        val phase = state.phase
        if (phase is GameInstaller.Phase.Failed) {
            showFailure(phase.message)
            return
        }

        statusText.text = phase.label.ifEmpty { getString(R.string.gameport_starting) }

        if (state.progress < 0f) {
            progressBar.isIndeterminate = true
            detailText.visibility = View.INVISIBLE
        } else {
            val percent = (state.progress * 100).toInt()
            progressBar.isIndeterminate = false
            progressBar.progress = percent
            detailText.visibility = View.VISIBLE
            detailText.text = getString(R.string.gameport_percent, percent)
        }
    }

    private fun showFailure(message: String) {
        progressBar.isIndeterminate = false
        progressBar.progress = 0
        statusText.text = getString(R.string.gameport_failed)
        detailText.visibility = View.VISIBLE
        detailText.text = message
    }

    // -------------------------------------------------------------- başlatma
    private fun launchGame(target: GameInstaller.LaunchTarget) {
        if (launched) return
        launched = true

        applyDiagnosticsPreferences()

        val intent = Intent(this, XServerDisplayActivity::class.java).apply {
            putExtra("container_id", target.containerId)
            putExtra("shortcut_path", target.shortcutPath)
        }
        startActivity(intent)
        // Splash ile oyun arasinda gecis animasyonu olmasin (ani siyah flas
        // yerine dogrudan gecis). Modern karsiligi overrideActivityTransition
        // API 34 istiyor; bu proje targetSdk 28'de kaldigi icin (Winlator'in
        // legacy depolama davranisina bagimli) eski API dogru olan.
        @Suppress("DEPRECATION")
        overridePendingTransition(0, 0)
    }

    /**
     * Tanılama açıksa Wine'ın konuşmasını sağlar.
     *
     * XServerDisplayActivity, WINEDEBUG'u şuna göre belirliyor:
     *     enableWineDebug ? "+warn,+err,+fixme" : "-all"
     * Yani bu tercih kapalıyken Wine neredeyse hiçbir şey yazmıyor ve
     * "Starting up..." ekranında takılırsan elinde hiçbir ipucu olmuyor.
     *
     * Aynı tercih ayrıca DebugDialog'un kaydedilmesini ve oyun içi menüdeki
     * "Logs" öğesinin görünür olmasını sağlıyor. Tercihi BURADA, oyun
     * aktivitesi başlamadan önce yazmak zorundayız; o aktivite değeri
     * onCreate'in başında okuyor.
     */
    private fun applyDiagnosticsPreferences() {
        val config = GamePortConfig.get(this)
        if (!config.diagnosticsEnabled) return
        PreferenceManager.getDefaultSharedPreferences(this).edit()
            .putBoolean("enable_wine_debug", true)
            .apply()
    }

    private companion object {
        const val REQUEST_STORAGE = 1
    }
}
