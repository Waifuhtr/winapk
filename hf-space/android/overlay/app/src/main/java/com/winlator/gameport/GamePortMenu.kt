package com.winlator.gameport

import android.view.Menu
import com.winlator.R

/**
 * Oyun içi çekmece menüsünün tek-oyun moduna göre sadeleştirilmesi.
 *
 * Winlator'ın XServerDisplayActivity'si bir "genel amaçlı emülatör" menüsü
 * taşıyor. Tek oyunluk bir port'ta bunların çoğu ya anlamsız ya da kullanıcıyı
 * editör/ayar ekranlarına götürüyor (özellikle Input Controls diyaloğundaki
 * "Düzenle" düğmesi MainActivity'yi açıyor).
 *
 * Menü öğelerini SİLMEK yerine GİZLİYORUZ; ilgili aktiviteler manifest'te
 * tanımlı kalıyor. Böylece upstream kodundaki hiçbir çağrı yolu kırılmıyor,
 * ama kullanıcı bunlara hiçbir şekilde ulaşamıyor.
 */
object GamePortMenu {

    /** Kullanıcıya açık kalacak öğeler — oynanışa doğrudan hizmet edenler. */
    private val KEEP = intArrayOf(
        R.id.menu_item_keyboard,           // ekran klavyesi: bazı oyunlarda gerekli
        R.id.menu_item_toggle_fullscreen,  // görüntü oranı sorunlarında kurtarıcı
        R.id.menu_item_magnifier,
        R.id.menu_item_exit,
    )

    @JvmStatic
    fun apply(menu: Menu) {
        for (index in 0 until menu.size()) {
            val item = menu.getItem(index)
            item.isVisible = if (item.itemId == R.id.menu_item_logs) {
                // Logs'a DOKUNMUYORUZ: upstream bunu enable_wine_debug
                // tercihine gore ayarladi. Tanilama acikken gorunur olmali
                // (canli Wine ciktisi), kapaliyken zaten gizli -- ustelik
                // debugDialog null oldugu icin tiklanirsa cokerdi.
                item.isVisible
            } else {
                KEEP.any { it == item.itemId }
            }
        }
    }
}
