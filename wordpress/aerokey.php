<?php
/**
 * Plugin Name: AeroKey Lisans Yöneticisi V8.8 (Gacha + Anket + Oyun Süresi + Ücretsiz Gün + Push Duyuru)
 * Description: Kart çevirme sistemi, pity, admin tabloları, gün/saat mantığı, Steam tarzı inceleme sistemi API'si, ücretsiz erişim günleri ve oyunlara push duyuru gönderimi.
 * Version: 8.8
 * Author: Riaslink
 */

if (!defined('ABSPATH')) exit;

function aerokey_create_db_table() {
    global $wpdb;
    $charset_collate = $wpdb->get_charset_collate();
    $p = $wpdb->prefix;

    // dbDelta için her sütun ayrı satırda olmalıdır!
    $sql1 = "CREATE TABLE {$p}aerokey_lisanslar (
      id mediumint(9) NOT NULL AUTO_INCREMENT,
      lisans_anahtari varchar(255) NOT NULL,
      cihaz_id varchar(255) DEFAULT '' NOT NULL,
      is_vip tinyint(1) DEFAULT 0 NOT NULL,
      bitis_tarihi datetime DEFAULT NULL,
      olusturulma_tarihi datetime DEFAULT CURRENT_TIMESTAMP NOT NULL,
      PRIMARY KEY  (id)
    ) $charset_collate;";
    
    $sql2 = "CREATE TABLE {$p}aerokey_istatistik (
      id mediumint(9) NOT NULL AUTO_INCREMENT,
      cihaz_id varchar(255) NOT NULL,
      kullanici_adi varchar(50) DEFAULT 'GizemliOyuncu' NOT NULL,
      toplam_saniye bigint(20) DEFAULT 0 NOT NULL,
      oyun_sureleri text DEFAULT NULL,
      son_gorev_tarihi date DEFAULT NULL,
      davet_kodu varchar(20) DEFAULT NULL,
      kullanilan_davet tinyint(1) DEFAULT 0 NOT NULL,
      basarimlar text DEFAULT NULL,
      son_ucretsiz_kart date DEFAULT NULL,
      kart_pity int DEFAULT 0 NOT NULL,
      son_guncelleme datetime DEFAULT CURRENT_TIMESTAMP NOT NULL,
      PRIMARY KEY  (id),
      UNIQUE KEY cihaz_id (cihaz_id)
    ) $charset_collate;";
    
    $sql3 = "CREATE TABLE {$p}aerokey_anket (
      id mediumint(9) NOT NULL AUTO_INCREMENT,
      soru varchar(255) NOT NULL,
      secenek1 varchar(100) NOT NULL,
      secenek2 varchar(100) NOT NULL,
      oy1 int DEFAULT 0 NOT NULL,
      oy2 int DEFAULT 0 NOT NULL,
      aktif tinyint(1) DEFAULT 1 NOT NULL,
      PRIMARY KEY  (id)
    ) $charset_collate;";
    
    $sql4 = "CREATE TABLE {$p}aerokey_bildirim (
      id mediumint(9) NOT NULL AUTO_INCREMENT,
      cihaz_id varchar(255) NOT NULL,
      kullanici varchar(50) NOT NULL,
      mesaj text NOT NULL,
      tarih datetime DEFAULT CURRENT_TIMESTAMP NOT NULL,
      PRIMARY KEY  (id)
    ) $charset_collate;";

    // v8.8: oyunlara gonderilen push duyurulari. MEVCUT TABLOLARA
    // DOKUNULMADI; bu tamamen yeni bir tablo.
    $sql5 = "CREATE TABLE {$p}aerokey_duyurular (
      id mediumint(9) NOT NULL AUTO_INCREMENT,
      baslik varchar(120) NOT NULL,
      mesaj text NOT NULL,
      oyun_id varchar(100) DEFAULT '' NOT NULL,
      tarih datetime DEFAULT CURRENT_TIMESTAMP NOT NULL,
      PRIMARY KEY  (id)
    ) $charset_collate;";

    require_once(ABSPATH . 'wp-admin/includes/upgrade.php');
    dbDelta($sql1); 
    dbDelta($sql2); 
    dbDelta($sql3); 
    dbDelta($sql4);
    dbDelta($sql5);
}
register_activation_hook(__FILE__, 'aerokey_create_db_table');

add_action('plugins_loaded', 'aerokey_check_db_tables');
function aerokey_check_db_tables() {
    global $wpdb; 
    $table_stats = $wpdb->prefix . 'aerokey_istatistik';
    $row = $wpdb->get_results("SHOW COLUMNS FROM `$table_stats` LIKE 'kart_pity'");
    if(empty($row)) { aerokey_create_db_table(); }

    // v8.8: eklenti guncellendiginde yeni duyuru tablosu da olusmali.
    // Eski kurulumlarda activation hook yeniden calismadigi icin burada
    // kontrol ediyoruz.
    $table_duyuru = $wpdb->prefix . 'aerokey_duyurular';
    if ($wpdb->get_var($wpdb->prepare("SHOW TABLES LIKE %s", $table_duyuru)) != $table_duyuru) {
        aerokey_create_db_table();
    }
}

add_action('rest_api_init', function () {
    register_rest_route('lisans/v1', '/kontrol', array('methods' => 'GET', 'callback' => 'aerokey_api_kontrol', 'permission_callback' => '__return_true'));
    register_rest_route('lisans/v1', '/vip-kontrol', array('methods' => 'GET', 'callback' => 'aerokey_api_vip_kontrol', 'permission_callback' => '__return_true'));
    register_rest_route('lisans/v1', '/sync', array('methods' => array('GET', 'POST'), 'callback' => 'aerokey_api_sync', 'permission_callback' => '__return_true'));
    register_rest_route('lisans/v1', '/liderlik', array('methods' => 'GET', 'callback' => 'aerokey_api_liderlik', 'permission_callback' => '__return_true'));
    register_rest_route('lisans/v1', '/gorev-odul', array('methods' => 'POST', 'callback' => 'aerokey_api_gorev_odul', 'permission_callback' => '__return_true'));
    register_rest_route('lisans/v1', '/referans', array('methods' => 'POST', 'callback' => 'aerokey_api_referans', 'permission_callback' => '__return_true'));
    register_rest_route('lisans/v1', '/gorev-anahtar', array('methods' => 'POST', 'callback' => 'aerokey_api_gorev_anahtar', 'permission_callback' => '__return_true'));
    register_rest_route('lisans/v1', '/profil', array('methods' => 'GET', 'callback' => 'aerokey_api_profil', 'permission_callback' => '__return_true'));
    register_rest_route('lisans/v1', '/anket', array('methods' => array('GET', 'POST'), 'callback' => 'aerokey_api_anket', 'permission_callback' => '__return_true'));
    register_rest_route('lisans/v1', '/hata-bildir', array('methods' => 'POST', 'callback' => 'aerokey_api_hata_bildir', 'permission_callback' => '__return_true'));
    register_rest_route('lisans/v1', '/kart-cevir', array('methods' => 'POST', 'callback' => 'aerokey_api_kart_cevir', 'permission_callback' => '__return_true'));
    // YENİ EKLENEN OYUN SÜRESİ ÇEKME API'Sİ (Steam Tarzı)
    register_rest_route('lisans/v1', '/oyun-suresi', array('methods' => 'GET', 'callback' => 'aerokey_api_oyun_suresi', 'permission_callback' => '__return_true'));
    // YENI (v8.8): oyun istemcisinin duzenli sordugu durum ucu --
    // "bugun ucretsiz gun mu" + "gormedigim yeni duyurular"
    register_rest_route('lisans/v1', '/durum', array('methods' => 'GET', 'callback' => 'aerokey_api_durum', 'permission_callback' => '__return_true'));
});

function aerokey_api_kontrol($request) {
    global $wpdb; $table_name = $wpdb->prefix . 'aerokey_lisanslar'; 
    $anahtar = sanitize_text_field($request->get_param('anahtar'));
    if (empty($anahtar)) return new WP_REST_Response(array('durum' => 'hata'), 200);
    $kayit = $wpdb->get_row($wpdb->prepare("SELECT * FROM $table_name WHERE lisans_anahtari = %s AND is_vip = 0", $anahtar));
    if (!$kayit) return new WP_REST_Response(array('durum' => 'hata'), 200);
    if (!empty($kayit->bitis_tarihi) && $kayit->bitis_tarihi !== '0000-00-00 00:00:00') {
        if (current_time('Y-m-d H:i:s') > $kayit->bitis_tarihi) return new WP_REST_Response(array('durum' => 'hata', 'mesaj' => 'Suresi dolmus'), 200);
    }
    return new WP_REST_Response(array('durum' => 'basarili', 'bitis' => (empty($kayit->bitis_tarihi) || $kayit->bitis_tarihi == '0000-00-00 00:00:00') ? 'Sınırsız' : date('d.m.Y H:i', strtotime($kayit->bitis_tarihi))), 200);
}

function aerokey_api_vip_kontrol($request) {
    global $wpdb; $table_name = $wpdb->prefix . 'aerokey_lisanslar'; 
    $cihaz_id = sanitize_text_field($request->get_param('device_id'));
    if (empty($cihaz_id)) return new WP_REST_Response(array('durum' => 'hata'), 200);
    $kayit = $wpdb->get_row($wpdb->prepare("SELECT * FROM $table_name WHERE cihaz_id = %s AND is_vip = 1", $cihaz_id));
    if ($kayit) {
        if (!empty($kayit->bitis_tarihi) && $kayit->bitis_tarihi !== '0000-00-00 00:00:00' && current_time('Y-m-d H:i:s') > $kayit->bitis_tarihi) return new WP_REST_Response(array('durum' => 'hata', 'mesaj' => 'Suresi dolmus'), 200);
        return new WP_REST_Response(array('durum' => 'basarili', 'bitis' => (empty($kayit->bitis_tarihi) || $kayit->bitis_tarihi == '0000-00-00 00:00:00') ? 'Sınırsız VIP' : date('d.m.Y H:i', strtotime($kayit->bitis_tarihi))), 200);
    } 
    return new WP_REST_Response(array('durum' => 'hata'), 200);
}

function aerokey_api_sync($request) {
    global $wpdb; $table_stats = $wpdb->prefix . 'aerokey_istatistik'; 
    $params = $request->get_json_params(); if(!$params) $params = $_POST;
    
    $cihaz_id = sanitize_text_field($params['cihaz_id']); 
    $kullanici_adi = sanitize_text_field($params['kullanici_adi']); 
    $toplam_saniye = intval($params['toplam_saniye']);
    $oyun_id = sanitize_text_field($params['oyun_id']); 
    $oyun_saniye = intval($params['oyun_saniye']); 
    $basarimlar = isset($params['basarimlar']) ? json_encode($params['basarimlar']) : '[]';
    
    $canli = $wpdb->get_var("SELECT COUNT(*) FROM $table_stats WHERE son_guncelleme >= NOW() - INTERVAL 3 MINUTE");
    $duyuru = get_option('aerokey_duyuru', 'İyi Oyunlar!');
    
    if (empty($cihaz_id)) return new WP_REST_Response(array('durum' => 'hata'), 400);

    $kayit = $wpdb->get_row($wpdb->prepare("SELECT * FROM $table_stats WHERE cihaz_id = %s", $cihaz_id));
    if ($kayit) {
        $davet = empty($kayit->davet_kodu) ? 'RIAS-' . strtoupper(substr(md5($cihaz_id . time()), 0, 5)) : $kayit->davet_kodu;
        $oyunlar = json_decode($kayit->oyun_sureleri, true); if(!is_array($oyunlar)) $oyunlar = array();
        if($oyun_id != '' && $oyun_saniye > ($oyunlar[$oyun_id] ?? 0)) $oyunlar[$oyun_id] = $oyun_saniye;

        if($toplam_saniye > $kayit->toplam_saniye) {
             $wpdb->update($table_stats, array('kullanici_adi'=>$kullanici_adi, 'toplam_saniye'=>$toplam_saniye, 'oyun_sureleri'=>json_encode($oyunlar), 'davet_kodu'=>$davet, 'basarimlar'=>$basarimlar, 'son_guncelleme'=>current_time('mysql')), array('cihaz_id'=>$cihaz_id));
             return new WP_REST_Response(array('durum'=>'guncellendi', 'canli'=>$canli, 'duyuru'=>$duyuru, 'davet_kodu'=>$davet, 'kullanilan'=>$kayit->kullanilan_davet), 200);
        } else if ($toplam_saniye < $kayit->toplam_saniye) {
             return new WP_REST_Response(array('durum'=>'buluttan_yukle', 'saniye'=>$kayit->toplam_saniye, 'kullanici_adi'=>$kayit->kullanici_adi, 'oyun_saniye'=>($oyunlar[$oyun_id] ?? 0), 'canli'=>$canli, 'duyuru'=>$duyuru, 'davet_kodu'=>$davet, 'kullanilan'=>$kayit->kullanilan_davet), 200);
        }
        $wpdb->update($table_stats, array('davet_kodu'=>$davet, 'basarimlar'=>$basarimlar, 'son_guncelleme'=>current_time('mysql')), array('cihaz_id'=>$cihaz_id));
        return new WP_REST_Response(array('durum'=>'esit', 'canli'=>$canli, 'duyuru'=>$duyuru, 'davet_kodu'=>$davet, 'kullanilan'=>$kayit->kullanilan_davet), 200);
    } else {
        $davet = 'RIAS-' . strtoupper(substr(md5($cihaz_id . time()), 0, 5));
        $wpdb->insert($table_stats, array('cihaz_id'=>$cihaz_id, 'kullanici_adi'=>$kullanici_adi, 'toplam_saniye'=>$toplam_saniye, 'oyun_sureleri'=>json_encode(array($oyun_id=>$oyun_saniye)), 'davet_kodu'=>$davet, 'basarimlar'=>$basarimlar));
        return new WP_REST_Response(array('durum'=>'eklendi', 'canli'=>$canli, 'duyuru'=>$duyuru, 'davet_kodu'=>$davet, 'kullanilan'=>0), 200);
    }
}

function aerokey_api_kart_cevir($request) {
    global $wpdb; $table_stats = $wpdb->prefix . 'aerokey_istatistik'; $table_lisans = $wpdb->prefix . 'aerokey_lisanslar';
    $params = $request->get_json_params(); if(!$params) $params = $_POST;
    
    $cihaz_id = sanitize_text_field($params['cihaz_id']); 
    $anahtar = sanitize_text_field($params['anahtar'] ?? ''); 
    $is_vip = ($params['is_vip'] ?? false) ? 1 : 0;
    
    $istatistik = $wpdb->get_row($wpdb->prepare("SELECT * FROM $table_stats WHERE cihaz_id = %s", $cihaz_id));
    if(!$istatistik) return new WP_REST_Response(array('durum' => 'hata', 'mesaj' => 'Kayıt bulunamadı.'), 200);

    $bugun = current_time('Y-m-d');
    $ucretsiz_mi = ($istatistik->son_ucretsiz_kart !== $bugun);
    $bedel = 3000; // 50 XP Karşılığı
    
    if(!$ucretsiz_mi) {
        if($istatistik->toplam_saniye < $bedel) {
            return new WP_REST_Response(array('durum' => 'hata', 'mesaj' => "Yetersiz Oyun Süresi!\n(1 Çekim = 50 XP)"), 200);
        }
        $wpdb->update($table_stats, array('toplam_saniye' => $istatistik->toplam_saniye - $bedel), array('cihaz_id' => $cihaz_id));
    } else {
        $wpdb->update($table_stats, array('son_ucretsiz_kart' => $bugun), array('cihaz_id' => $cihaz_id));
    }

    $pity = $istatistik->kart_pity;
    $zar = rand(1, 1000) + ($pity * 20);

    $odul_tipi = "bos"; $eklenecek_saat = 0; $nadir_renk = "#9E9E9E"; $mesaj = "Rüzgar esti, kart uçtu... Boş!";
    
    if ($zar > 999) { $odul_tipi = "sinirsiz"; $mesaj = "EFSANEVİ! Sınırsız Anahtar Kazandın!"; $nadir_renk = "#FFD700"; }
    elseif ($zar > 950) { $odul_tipi = "sure"; $eklenecek_saat = 168; $mesaj = "DESTANSI! +1 Hafta Kazandın!"; $nadir_renk = "#D50000"; }
    elseif ($zar > 900) { $odul_tipi = "sure"; $eklenecek_saat = 72; $mesaj = "EPİK! +3 Gün Kazandın!"; $nadir_renk = "#C51162"; }
    elseif ($zar > 820) { $odul_tipi = "sure"; $eklenecek_saat = 48; $mesaj = "EPİK! +2 Gün Kazandın!"; $nadir_renk = "#AA00FF"; }
    elseif ($zar > 700) { $odul_tipi = "sure"; $eklenecek_saat = 24; $mesaj = "NADİR! +1 Gün Kazandın!"; $nadir_renk = "#6200EA"; }
    elseif ($zar > 550) { $odul_tipi = "sure"; $eklenecek_saat = 10; $mesaj = "Nadir! +10 Saat Kazandın!"; $nadir_renk = "#2962FF"; }
    elseif ($zar > 350) { $odul_tipi = "sure"; $eklenecek_saat = 5; $mesaj = "Yaygın! +5 Saat Kazandın!"; $nadir_renk = "#00B8D4"; }
    elseif ($zar > 150) { $odul_tipi = "sure"; $eklenecek_saat = 1; $mesaj = "Yaygın! +1 Saat Kazandın!"; $nadir_renk = "#00C853"; }

    $yeni_sure_str = "Sınırsız";
    if ($odul_tipi !== "bos") {
        $wpdb->update($table_stats, array('kart_pity' => 0), array('cihaz_id' => $cihaz_id));
        
        if($is_vip) { 
            $lisans = $wpdb->get_row($wpdb->prepare("SELECT * FROM $table_lisans WHERE cihaz_id = %s AND is_vip = 1 ORDER BY id DESC LIMIT 1", $cihaz_id)); 
        } else { 
            $lisans = $wpdb->get_row($wpdb->prepare("SELECT * FROM $table_lisans WHERE lisans_anahtari = %s AND is_vip = 0 ORDER BY id DESC LIMIT 1", $anahtar)); 
        }
        
        if($lisans) {
            if ($odul_tipi == "sinirsiz") {
                $wpdb->update($table_lisans, array('bitis_tarihi' => null), array('id' => $lisans->id));
            } elseif (!empty($lisans->bitis_tarihi) && $lisans->bitis_tarihi != '0000-00-00 00:00:00') {
                $yeni_bitis = date('Y-m-d H:i:s', strtotime("+$eklenecek_saat hours", strtotime($lisans->bitis_tarihi)));
                $wpdb->update($table_lisans, array('bitis_tarihi' => $yeni_bitis), array('id' => $lisans->id));
                $yeni_sure_str = date('d.m.Y H:i', strtotime($yeni_bitis));
            }
        }
    } else {
        $wpdb->query($wpdb->prepare("UPDATE $table_stats SET kart_pity = kart_pity + 1 WHERE cihaz_id = %s", $cihaz_id));
    }

    return new WP_REST_Response(array('durum' => 'basarili', 'odul_tipi' => $odul_tipi, 'mesaj' => $mesaj, 'renk' => $nadir_renk, 'yeni_sure' => $yeni_sure_str, 'ucretsiz_mi' => $ucretsiz_mi), 200);
}

function aerokey_api_gorev_odul($request) {
    global $wpdb; $table_lisans = $wpdb->prefix . 'aerokey_lisanslar'; $table_stats = $wpdb->prefix . 'aerokey_istatistik';
    $params = $request->get_json_params(); if(!$params) $params = $_POST;
    $cihaz_id = sanitize_text_field($params['cihaz_id']); $anahtar = sanitize_text_field($params['anahtar'] ?? ''); $is_vip = ($params['is_vip'] ?? false) ? 1 : 0;
    $bugun = current_time('Y-m-d');

    if(empty($cihaz_id)) return new WP_REST_Response(array('durum' => 'hata'), 400);
    $istatistik = $wpdb->get_row($wpdb->prepare("SELECT son_gorev_tarihi FROM $table_stats WHERE cihaz_id = %s", $cihaz_id));
    if($istatistik && $istatistik->son_gorev_tarihi == $bugun) return new WP_REST_Response(array('durum' => 'zaten_aldi'), 200);

    if($is_vip) { $lisans = $wpdb->get_row($wpdb->prepare("SELECT * FROM $table_lisans WHERE cihaz_id = %s AND is_vip = 1 ORDER BY id DESC LIMIT 1", $cihaz_id)); } 
    else { $lisans = $wpdb->get_row($wpdb->prepare("SELECT * FROM $table_lisans WHERE lisans_anahtari = %s AND is_vip = 0 ORDER BY id DESC LIMIT 1", $anahtar)); }
    
    if($lisans && !empty($lisans->bitis_tarihi) && $lisans->bitis_tarihi != '0000-00-00 00:00:00') {
        $yeni_bitis = date('Y-m-d H:i:s', strtotime("+1 hour", strtotime($lisans->bitis_tarihi)));
        $wpdb->update($table_lisans, array('bitis_tarihi' => $yeni_bitis), array('id' => $lisans->id));
        $wpdb->update($table_stats, array('son_gorev_tarihi' => $bugun), array('cihaz_id' => $cihaz_id));
        return new WP_REST_Response(array('durum' => 'basarili', 'yeni_sure' => date('d.m.Y H:i', strtotime($yeni_bitis))), 200);
    }
    return new WP_REST_Response(array('durum' => 'hata', 'mesaj' => 'Sinirsiz veya lisans bulunamadi'), 200);
}

function aerokey_api_referans($request) {
    global $wpdb; $table_lisans = $wpdb->prefix . 'aerokey_lisanslar'; $table_stats = $wpdb->prefix . 'aerokey_istatistik';
    $params = $request->get_json_params(); if(!$params) $params = $_POST;
    $cihaz_id = sanitize_text_field($params['cihaz_id']); $girilen_kod = sanitize_text_field($params['davet_kodu']);
    $anahtar = sanitize_text_field($params['anahtar'] ?? ''); $is_vip = ($params['is_vip'] ?? false) ? 1 : 0;

    $kendi_kaydi = $wpdb->get_row($wpdb->prepare("SELECT * FROM $table_stats WHERE cihaz_id = %s", $cihaz_id));
    if(!$kendi_kaydi) return new WP_REST_Response(array('durum' => 'hata', 'mesaj' => 'Önce biraz oynamalısın.'), 200);
    if($kendi_kaydi->kullanilan_davet == 1) return new WP_REST_Response(array('durum' => 'hata', 'mesaj' => 'Zaten bir davet kodu kullandın!'), 200);
    if(strtoupper($kendi_kaydi->davet_kodu) == strtoupper($girilen_kod)) return new WP_REST_Response(array('durum' => 'hata', 'mesaj' => 'Kendi kodunu giremezsin!'), 200);

    $arkadas_kaydi = $wpdb->get_row($wpdb->prepare("SELECT * FROM $table_stats WHERE davet_kodu = %s", $girilen_kod));
    if(!$arkadas_kaydi) return new WP_REST_Response(array('durum' => 'hata', 'mesaj' => 'Böyle bir davet kodu bulunamadı!'), 200);

    if($is_vip) { $lisans = $wpdb->get_row($wpdb->prepare("SELECT * FROM $table_lisans WHERE cihaz_id = %s AND is_vip = 1 ORDER BY id DESC LIMIT 1", $cihaz_id)); } 
    else { $lisans = $wpdb->get_row($wpdb->prepare("SELECT * FROM $table_lisans WHERE lisans_anahtari = %s AND is_vip = 0 ORDER BY id DESC LIMIT 1", $anahtar)); }
    
    $yeni_sure = "Sınırsız";
    if($lisans && !empty($lisans->bitis_tarihi) && $lisans->bitis_tarihi != '0000-00-00 00:00:00') {
        $yeni_bitis = date('Y-m-d H:i:s', strtotime("+5 hours", strtotime($lisans->bitis_tarihi)));
        $wpdb->update($table_lisans, array('bitis_tarihi' => $yeni_bitis), array('id' => $lisans->id));
        $yeni_sure = date('d.m.Y H:i', strtotime($yeni_bitis));
    }

    $arkadas_lisans = $wpdb->get_row($wpdb->prepare("SELECT * FROM $table_lisans WHERE cihaz_id = %s OR lisans_anahtari = (SELECT saved_key FROM $table_stats WHERE cihaz_id = %s LIMIT 1) ORDER BY id DESC LIMIT 1", $arkadas_kaydi->cihaz_id, $arkadas_kaydi->cihaz_id));
    if($arkadas_lisans && !empty($arkadas_lisans->bitis_tarihi) && $arkadas_lisans->bitis_tarihi != '0000-00-00 00:00:00') {
        $ark_yeni_bitis = date('Y-m-d H:i:s', strtotime("+5 hours", strtotime($arkadas_lisans->bitis_tarihi)));
        $wpdb->update($table_lisans, array('bitis_tarihi' => $ark_yeni_bitis), array('id' => $arkadas_lisans->id));
    }

    $wpdb->update($table_stats, array('kullanilan_davet' => 1), array('cihaz_id' => $cihaz_id));
    return new WP_REST_Response(array('durum' => 'basarili', 'mesaj' => '+5 Saat kazandın!', 'yeni_sure' => $yeni_sure), 200);
}

function aerokey_api_liderlik($request) {
    global $wpdb; $table_stats = $wpdb->prefix . 'aerokey_istatistik';
    $liste = $wpdb->get_results("SELECT kullanici_adi, toplam_saniye, basarimlar FROM $table_stats ORDER BY toplam_saniye DESC LIMIT 10", ARRAY_A);
    return new WP_REST_Response(array('durum' => 'basarili', 'liste' => $liste ?: array()), 200);
}

function aerokey_api_gorev_anahtar($request) {
    global $wpdb; $table_lisans = $wpdb->prefix . 'aerokey_lisanslar';
    $params = $request->get_json_params(); if(!$params) $params = $_POST;
    $girilen_kod = sanitize_text_field($params['gizli_kod']);
    $gizli_sifre = get_option('aerokey_link_sifresi', 'GIZLI2026'); 
    
    if(strtoupper($girilen_kod) === strtoupper($gizli_sifre)) {
        $yeni_anahtar = 'GOREV-' . strtoupper(wp_generate_password(6, false));
        $bitis = date('Y-m-d H:i:s', strtotime("+1 days", current_time('timestamp'))); 
        $wpdb->insert($table_lisans, array('lisans_anahtari' => $yeni_anahtar, 'bitis_tarihi' => $bitis, 'is_vip' => 0));
        return new WP_REST_Response(array('durum' => 'basarili', 'yeni_anahtar' => $yeni_anahtar), 200);
    }
    return new WP_REST_Response(array('durum' => 'hata', 'mesaj' => 'Yanlış veya eksik kod!'), 200);
}

function aerokey_api_profil($request) {
    global $wpdb; $table_stats = $wpdb->prefix . 'aerokey_istatistik';
    $kullanici_adi = sanitize_text_field($request->get_param('kullanici_adi'));
    $kayit = $wpdb->get_row($wpdb->prepare("SELECT kullanici_adi, toplam_saniye, basarimlar FROM $table_stats WHERE kullanici_adi = %s LIMIT 1", $kullanici_adi));
    if($kayit) { return new WP_REST_Response(array('durum' => 'basarili', 'kullanici_adi' => $kayit->kullanici_adi, 'toplam_saniye' => $kayit->toplam_saniye, 'basarimlar' => json_decode($kayit->basarimlar, true)), 200); }
    return new WP_REST_Response(array('durum' => 'hata'), 200);
}

function aerokey_api_anket($request) {
    global $wpdb; $table_anket = $wpdb->prefix . 'aerokey_anket';
    $params = $request->get_json_params(); if(!$params) $params = $_POST;
    if($request->get_method() === 'POST') {
        $anket_id = intval($params['anket_id']); $oy = intval($params['oy']);
        if($oy == 1) $wpdb->query("UPDATE $table_anket SET oy1 = oy1 + 1 WHERE id = $anket_id");
        else if($oy == 2) $wpdb->query("UPDATE $table_anket SET oy2 = oy2 + 1 WHERE id = $anket_id");
        return new WP_REST_Response(array('durum' => 'basarili'), 200);
    } else {
        $anket = $wpdb->get_row("SELECT * FROM $table_anket WHERE aktif = 1 ORDER BY id DESC LIMIT 1");
        if($anket) return new WP_REST_Response(array('durum' => 'basarili', 'id' => $anket->id, 'soru' => $anket->soru, 'secenek1' => $anket->secenek1, 'secenek2' => $anket->secenek2, 'oy1' => $anket->oy1, 'oy2' => $anket->oy2), 200);
        return new WP_REST_Response(array('durum' => 'hata'), 200);
    }
}

function aerokey_api_hata_bildir($request) {
    global $wpdb; $table_bildirim = $wpdb->prefix . 'aerokey_bildirim';
    $params = $request->get_json_params(); if(!$params) $params = $_POST;
    $wpdb->insert($table_bildirim, array('cihaz_id' => sanitize_text_field($params['cihaz_id']), 'kullanici' => sanitize_text_field($params['kullanici_adi']), 'mesaj' => sanitize_textarea_field($params['mesaj'])));
    return new WP_REST_Response(array('durum' => 'basarili'), 200);
}

// --- OYUN SÜRESİ ÇEKME API'Sİ (Steam Tarzı) ---
function aerokey_api_oyun_suresi($request) {
    global $wpdb; 
    $table_stats = $wpdb->prefix . 'aerokey_istatistik';

    $kullanici_adi = sanitize_text_field($request->get_param('kullanici_adi'));
    $oyun_id = sanitize_text_field($request->get_param('oyun_id'));

    if (empty($kullanici_adi) || empty($oyun_id)) {
        return new WP_REST_Response(array('durum' => 'hata', 'mesaj' => 'Kullanici adi veya oyun ID eksik.'), 400);
    }

    $kayit = $wpdb->get_row($wpdb->prepare("SELECT oyun_sureleri FROM $table_stats WHERE kullanici_adi = %s LIMIT 1", $kullanici_adi));

    if ($kayit) {
        $oyunlar = json_decode($kayit->oyun_sureleri, true);
        if (!is_array($oyunlar)) $oyunlar = array();
        
        $oyun_saniyesi = isset($oyunlar[$oyun_id]) ? intval($oyunlar[$oyun_id]) : 0;

        $saat = floor($oyun_saniyesi / 3600);
        $dakika = floor(($oyun_saniyesi % 3600) / 60);

        if ($saat > 0) {
            $ondalik_saat = round($oyun_saniyesi / 3600, 1);
            $formatli_sure = $ondalik_saat . " saat"; 
        } else {
            $formatli_sure = $dakika . " dakika";
        }

        return new WP_REST_Response(array(
            'durum' => 'basarili',
            'kullanici_adi' => $kullanici_adi,
            'oyun_id' => $oyun_id,
            'saniye' => $oyun_saniyesi,
            'steam_format' => $formatli_sure . ' kayıtlarda'
        ), 200);
    }

    return new WP_REST_Response(array('durum' => 'hata', 'mesaj' => 'Kullanici bulunamadi veya hic oynamamis.'), 200);
}

// --- ÜCRETSİZ GÜN + PUSH DUYURU (v8.8) ---

/**
 * Ücretsiz erişim günlerini (Y-m-d biçiminde) dizi olarak döner.
 */
function aerokey_serbest_gunler() {
    $raw = get_option('aerokey_serbest_gunler', '');
    if (empty($raw)) return array();
    $liste = json_decode($raw, true);
    return is_array($liste) ? $liste : array();
}

/**
 * Bugün ücretsiz erişim günü mü?
 *
 * WordPress'in kendi saat dilimini kullanıyoruz (current_time), yani
 * "gün" kavramı sitenin ayarlarına göre belirleniyor -- cihazın saatine
 * göre değil. Böylece farklı saat dilimlerindeki oyuncular için gün
 * aynı anda başlayıp bitiyor.
 */
function aerokey_bugun_serbest_mi() {
    $bugun = current_time('Y-m-d');
    return in_array($bugun, aerokey_serbest_gunler(), true);
}

/**
 * GET /durum -- oyun istemcisinin düzenli sorduğu uç.
 *
 * Tek çağrıda iki şey döner:
 *   1) serbest      : bugün anahtar gerekmiyor mu
 *   2) duyurular    : istemcinin henüz görmediği push duyuruları
 *
 * İkisini ayırmak gereksiz trafik olurdu; istemci zaten ikisini de aynı
 * aralıkla sorguluyor.
 */
function aerokey_api_durum($request) {
    global $wpdb;
    $table_duyuru = $wpdb->prefix . 'aerokey_duyurular';

    $oyun_id     = sanitize_text_field($request->get_param('oyun_id'));
    $son_duyuru  = intval($request->get_param('son_duyuru'));

    $serbest = aerokey_bugun_serbest_mi();
    $serbest_mesaj = get_option('aerokey_serbest_mesaj', 'Bugün anahtarlar ücretsiz!');

    // Bu oyuna ait ya da TÜM oyunlara gönderilmiş (oyun_id boş) duyurular.
    $duyurular = $wpdb->get_results($wpdb->prepare(
        "SELECT id, baslik, mesaj, tarih FROM $table_duyuru
         WHERE id > %d AND (oyun_id = '' OR oyun_id = %s)
         ORDER BY id ASC LIMIT 20",
        $son_duyuru, $oyun_id
    ), ARRAY_A);
    if (!is_array($duyurular)) $duyurular = array();

    // En yüksek kimliği istemciye bildiriyoruz ki bir sonraki sorguda
    // yalnızca ondan yenilerini istesin.
    $son_id = $son_duyuru;
    foreach ($duyurular as $d) {
        $d_id = intval($d['id']);
        if ($d_id > $son_id) $son_id = $d_id;
    }

    // İstemci ilk kez soruyorsa (son_duyuru = 0) eski duyuruları TOPLUCA
    // bildirim olarak yağdırmak istemeyiz; yalnızca imleci ileri alıp
    // bundan sonrakileri gönderiyoruz.
    if ($son_duyuru === 0) {
        $en_yuksek = intval($wpdb->get_var("SELECT MAX(id) FROM $table_duyuru"));
        $duyurular = array();
        $son_id = $en_yuksek;
    }

    return new WP_REST_Response(array(
        'durum'          => 'basarili',
        'serbest'        => $serbest,
        'serbest_mesaj'  => $serbest_mesaj,
        'sunucu_tarihi'  => current_time('Y-m-d'),
        'duyurular'      => $duyurular,
        'son_duyuru_id'  => $son_id,
    ), 200);
}

// --- ADMIN PANEL KISMI ---

add_action('admin_menu', 'aerokey_admin_menu');
function aerokey_admin_menu() { 
    add_menu_page('AeroKey Yöneticisi', 'AeroKey Lisans', 'manage_options', 'aerokey-manager', 'aerokey_admin_page', 'dashicons-admin-network', 6); 
}

function aerokey_admin_page() {
    global $wpdb; 
    $table_name = $wpdb->prefix . 'aerokey_lisanslar'; 
    $table_stats = $wpdb->prefix . 'aerokey_istatistik'; 
    $table_anket = $wpdb->prefix . 'aerokey_anket'; 
    $table_bildirim = $wpdb->prefix . 'aerokey_bildirim';
    $table_duyurular = $wpdb->prefix . 'aerokey_duyurular';
    
    if (isset($_POST['islem'])) {
        if ($_POST['islem'] == 'duyuru_kaydet') { 
            update_option('aerokey_duyuru', sanitize_text_field($_POST['duyuru_metni'])); 
            echo '<div class="notice notice-success"><p>Duyuru Kaydedildi.</p></div>'; 
        }
        elseif ($_POST['islem'] == 'link_sifre_kaydet') { 
            update_option('aerokey_link_sifresi', sanitize_text_field($_POST['link_sifresi'])); 
            echo '<div class="notice notice-success"><p>Link Şifresi Kaydedildi.</p></div>'; 
        } 
        elseif ($_POST['islem'] == 'anket_olustur') { 
            $wpdb->query("UPDATE $table_anket SET aktif = 0"); 
            $wpdb->insert($table_anket, array('soru' => sanitize_text_field($_POST['soru']), 'secenek1' => sanitize_text_field($_POST['sec1']), 'secenek2' => sanitize_text_field($_POST['sec2']))); 
            echo '<div class="notice notice-success"><p>Yeni Anket Başlatıldı!</p></div>'; 
        }
        elseif ($_POST['islem'] == 'serbest_gun_ekle' && !empty($_POST['serbest_tarih'])) {
            $tarih = sanitize_text_field($_POST['serbest_tarih']);
            // Y-m-d bicimini dogruluyoruz: bozuk bir deger listeye girerse
            // karsilastirma sessizce hep false doner ve ozellik calismiyormus
            // gibi gorunurdu.
            $d = DateTime::createFromFormat('Y-m-d', $tarih);
            if ($d && $d->format('Y-m-d') === $tarih) {
                $liste = aerokey_serbest_gunler();
                if (!in_array($tarih, $liste, true)) {
                    $liste[] = $tarih;
                    sort($liste);
                    update_option('aerokey_serbest_gunler', wp_json_encode($liste));
                }
                echo '<div class="notice notice-success"><p>Ücretsiz gün eklendi: <strong>' . esc_html($tarih) . '</strong></p></div>';
            } else {
                echo '<div class="notice notice-error"><p>Geçersiz tarih biçimi.</p></div>';
            }
        }
        elseif ($_POST['islem'] == 'serbest_gun_sil' && !empty($_POST['serbest_tarih'])) {
            $tarih = sanitize_text_field($_POST['serbest_tarih']);
            $liste = array_values(array_diff(aerokey_serbest_gunler(), array($tarih)));
            update_option('aerokey_serbest_gunler', wp_json_encode($liste));
            echo '<div class="notice notice-success"><p>Ücretsiz gün kaldırıldı: <strong>' . esc_html($tarih) . '</strong></p></div>';
        }
        elseif ($_POST['islem'] == 'serbest_mesaj_kaydet') {
            update_option('aerokey_serbest_mesaj', sanitize_text_field($_POST['serbest_mesaj']));
            echo '<div class="notice notice-success"><p>Ücretsiz gün mesajı kaydedildi.</p></div>';
        }
        elseif ($_POST['islem'] == 'duyuru_gonder' && !empty($_POST['duyuru_mesaj'])) {
            $wpdb->insert($table_duyurular, array(
                'baslik'  => sanitize_text_field($_POST['duyuru_baslik']),
                'mesaj'   => sanitize_textarea_field($_POST['duyuru_mesaj']),
                'oyun_id' => sanitize_text_field($_POST['duyuru_oyun_id']),
            ));
            echo '<div class="notice notice-success"><p>Duyuru kuyruğa alındı. Oyuncular oyun açıkken kısa sürede, kapalıyken en geç ~15 dakika içinde bildirimi alır.</p></div>';
        }
        elseif ($_POST['islem'] == 'duyuru_sil' && isset($_POST['id'])) {
            $wpdb->delete($table_duyurular, array('id' => intval($_POST['id'])));
            echo '<div class="notice notice-success"><p>Duyuru silindi.</p></div>';
        }
        elseif ($_POST['islem'] == 'anket_kapat') { 
            $wpdb->query("UPDATE $table_anket SET aktif = 0"); 
            echo '<div class="notice notice-success"><p>Aktif anket sonlandırıldı ve kaldırıldı.</p></div>'; 
        }
        elseif ($_POST['islem'] == 'sil' && isset($_POST['id'])) { 
            $wpdb->delete($table_name, array('id' => intval($_POST['id']))); 
            echo '<div class="notice notice-success"><p>Lisans Silindi.</p></div>'; 
        } 
        elseif ($_POST['islem'] == 'yeni_anahtar') {
            $miktar = intval($_POST['sure_miktar']); 
            $tur = sanitize_text_field($_POST['sure_tur']); 
            $now = current_time('timestamp'); 
            
            if ($tur == 'dakika') {
                $bitis = date('Y-m-d H:i:s', strtotime("+$miktar minutes", $now)); 
            } elseif ($tur == 'saat') {
                $bitis = date('Y-m-d H:i:s', strtotime("+$miktar hours", $now)); 
            } elseif ($tur == 'gun') {
                $bitis = date('Y-m-d 23:59:59', strtotime("+$miktar days", $now)); 
            } else {
                $bitis = null;
            }
            
            $yeni_anahtar = !empty($_POST['ozel_anahtar']) ? sanitize_text_field($_POST['ozel_anahtar']) : 'RIASKEY-' . strtoupper(wp_generate_password(8, false));
            $wpdb->insert($table_name, array('lisans_anahtari' => $yeni_anahtar, 'bitis_tarihi' => $bitis, 'is_vip' => 0)); 
            echo '<div class="notice notice-success"><p>Anahtar Üretildi: <strong>' . $yeni_anahtar . '</strong></p></div>';
        }
        elseif ($_POST['islem'] == 'vip_ekle' && !empty($_POST['vip_cihaz_id'])) {
            $miktar = intval($_POST['vip_miktar']); 
            $tur = sanitize_text_field($_POST['vip_tur']); 
            $now = current_time('timestamp'); 
            $bitis = null;
            
            if ($tur == 'dakika') {
                $bitis = date('Y-m-d H:i:s', strtotime("+$miktar minutes", $now)); 
            } elseif ($tur == 'saat') {
                $bitis = date('Y-m-d H:i:s', strtotime("+$miktar hours", $now)); 
            } elseif ($tur == 'gun') {
                $bitis = date('Y-m-d 23:59:59', strtotime("+$miktar days", $now));
            }
            
            $vip_id = sanitize_text_field($_POST['vip_cihaz_id']); 
            $wpdb->insert($table_name, array('lisans_anahtari' => 'VIP-GIRIS', 'cihaz_id' => $vip_id, 'bitis_tarihi' => $bitis, 'is_vip' => 1)); 
            echo '<div class="notice notice-success"><p>VIP Eklendi: <strong>' . $vip_id . '</strong></p></div>';
        }
    }
    
    $kayitlar = $wpdb->get_results("SELECT * FROM $table_name ORDER BY id DESC"); 
    $liderlik_kayitlari = $wpdb->get_results("SELECT * FROM $table_stats ORDER BY toplam_saniye DESC LIMIT 10"); 
    $bildirimler = $wpdb->get_results("SELECT * FROM $table_bildirim ORDER BY id DESC LIMIT 15"); 
    $anket = $wpdb->get_row("SELECT * FROM $table_anket WHERE aktif = 1 ORDER BY id DESC LIMIT 1");
    
    $suan_str = current_time('Y-m-d H:i:s'); 
    $mevcut_duyuru = get_option('aerokey_duyuru', 'Hoş Geldiniz!'); 
    $mevcut_sifre = get_option('aerokey_link_sifresi', 'GIZLI2026');
    ?>
    <div class="wrap">
        <h1>AeroKey Komuta Merkezi (v8.7)</h1>
        
        <div style="display: flex; gap: 20px; margin-bottom: 20px;">
            <div style="background: #e3f2fd; padding: 15px; border: 1px solid #90caf9; width: 48%;">
                <h3>📢 Oyun İçi Global Duyuru Sistemi</h3>
                <form method="post">
                    <input type="hidden" name="islem" value="duyuru_kaydet">
                    <input type="text" name="duyuru_metni" value="<?php echo esc_attr($mevcut_duyuru); ?>" style="width: 100%; padding: 5px; margin-bottom: 10px;">
                    <button type="submit" class="button button-primary">Duyuruyu Yayınla</button>
                </form>
            </div>
            <div style="background: #e8f5e9; padding: 15px; border: 1px solid #a5d6a7; width: 48%;">
                <h3>🔗 Link Geçme Görevi Şifresi</h3>
                <form method="post">
                    <input type="hidden" name="islem" value="link_sifre_kaydet">
                    <input type="text" name="link_sifresi" value="<?php echo esc_attr($mevcut_sifre); ?>" style="width: 100%; padding: 5px; margin-bottom: 10px;">
                    <button type="submit" class="button button-primary">Şifreyi Güncelle</button>
                </form>
            </div>
        </div>

        <div style="display: flex; gap: 20px; margin-bottom: 20px;">
            <div style="background: #fff; padding: 15px; border: 1px solid #ddd; width: 48%;">
                <h3>🔑 Manuel Anahtar Üret</h3>
                <form method="post">
                    <input type="hidden" name="islem" value="yeni_anahtar">
                    <div style="display:flex; gap: 10px; margin-bottom: 10px;">
                        <input type="number" name="sure_miktar" value="1" min="1" style="width: 30%;">
                        <select name="sure_tur" style="width: 70%;">
                            <option value="dakika">Dakika</option>
                            <option value="saat">Saat</option>
                            <option value="gun">Gün</option>
                            <option value="sinirsiz">Sınırsız</option>
                        </select>
                    </div>
                    <input type="text" name="ozel_anahtar" placeholder="Özel Anahtar" style="width: 100%; margin-bottom: 10px;">
                    <button type="submit" class="button button-primary">Oluştur</button>
                </form>
            </div>
            
            <div style="background: #fff; padding: 15px; border: 1px solid #ddd; width: 48%;">
                <h3 style="color: #FFA500;">⭐ VIP Cihaz Ekle</h3>
                <form method="post">
                    <input type="hidden" name="islem" value="vip_ekle">
                    <div style="display:flex; gap: 10px; margin-bottom: 10px;">
                        <input type="number" name="vip_miktar" value="1" min="1" style="width: 30%;">
                        <select name="vip_tur" style="width: 70%;">
                            <option value="dakika">Dakika</option>
                            <option value="saat">Saat</option>
                            <option value="gun">Gün</option>
                            <option value="sinirsiz">Sınırsız</option>
                        </select>
                    </div>
                    <input type="text" name="vip_cihaz_id" placeholder="Cihaz ID" required style="width: 100%; margin-bottom: 10px;">
                    <button type="submit" class="button button-primary" style="background: #FFA500; border-color: #e69500; color: #fff;">VIP Ekle</button>
                </form>
            </div>
        </div>

        <div style="display: flex; gap: 20px; margin-bottom: 20px;">

            <!-- ÜCRETSİZ GÜN (v8.8) -->
            <div style="background: #fffde7; padding: 15px; border: 1px solid #fff176; width: 48%;">
                <h3>🎁 Ücretsiz Erişim Günleri</h3>
                <p style="color:#555; font-size:13px; margin-top:0;">
                    Buraya eklediğiniz tarihlerde oyunlar <b>anahtar sormaz</b>;
                    açılışta ekranın ortasında aşağıdaki mesaj gösterilir.
                    Gün, sitenizin saat dilimine göre hesaplanır.
                    <br><b>Bugün:</b> <?php echo esc_html(current_time('Y-m-d')); ?>
                    — <?php echo aerokey_bugun_serbest_mi()
                        ? '<span style="color:#2e7d32;font-weight:bold;">ÜCRETSİZ</span>'
                        : '<span style="color:#777;">normal (anahtar gerekli)</span>'; ?>
                </p>

                <form method="post" style="display:flex; gap:8px; margin-bottom:12px;">
                    <input type="hidden" name="islem" value="serbest_gun_ekle">
                    <input type="date" name="serbest_tarih" required style="flex:1;">
                    <button type="submit" class="button button-primary">Gün Ekle</button>
                </form>

                <?php $serbest_liste = aerokey_serbest_gunler(); ?>
                <?php if (!empty($serbest_liste)): ?>
                    <table class="wp-list-table widefat striped" style="margin-bottom:12px;">
                        <thead><tr><th>Tarih</th><th style="width:90px;">İşlem</th></tr></thead>
                        <tbody>
                        <?php foreach ($serbest_liste as $g): ?>
                            <tr>
                                <td><?php echo esc_html($g); ?>
                                    <?php if ($g === current_time('Y-m-d')) echo ' <b style="color:#2e7d32;">(bugün)</b>'; ?>
                                </td>
                                <td>
                                    <form method="post">
                                        <input type="hidden" name="islem" value="serbest_gun_sil">
                                        <input type="hidden" name="serbest_tarih" value="<?php echo esc_attr($g); ?>">
                                        <button type="submit" class="button" style="color:red;border-color:red;">Sil</button>
                                    </form>
                                </td>
                            </tr>
                        <?php endforeach; ?>
                        </tbody>
                    </table>
                <?php else: ?>
                    <p style="color:#777;"><i>Tanımlı ücretsiz gün yok.</i></p>
                <?php endif; ?>

                <form method="post">
                    <input type="hidden" name="islem" value="serbest_mesaj_kaydet">
                    <input type="text" name="serbest_mesaj"
                           value="<?php echo esc_attr(get_option('aerokey_serbest_mesaj', 'Bugün anahtarlar ücretsiz!')); ?>"
                           style="width:100%; margin-bottom:8px;">
                    <button type="submit" class="button">Mesajı Kaydet</button>
                </form>
            </div>

            <!-- PUSH DUYURU (v8.8) -->
            <div style="background: #ede7f6; padding: 15px; border: 1px solid #b39ddb; width: 48%;">
                <h3>🔔 Push Bildirim Gönder</h3>
                <p style="color:#555; font-size:13px; margin-top:0;">
                    Gönderdiğiniz duyuru, oyuncuların <b>bildirim çekmecesine</b>
                    düşer. Oyun açıkken kısa sürede, kapalıyken en geç ~15 dakika
                    içinde ulaşır.
                </p>
                <form method="post">
                    <input type="hidden" name="islem" value="duyuru_gonder">
                    <input type="text" name="duyuru_baslik" placeholder="Başlık" required
                           style="width:100%; margin-bottom:8px;">
                    <textarea name="duyuru_mesaj" placeholder="Mesaj" required rows="3"
                              style="width:100%; margin-bottom:8px;"></textarea>
                    <input type="text" name="duyuru_oyun_id"
                           placeholder="Oyun ID (boş = TÜM oyunlara)"
                           style="width:100%; margin-bottom:8px;">
                    <button type="submit" class="button button-primary"
                            style="background:#673ab7; border-color:#5e35b1;">Duyuruyu Gönder</button>
                </form>

                <?php
                $gonderilenler = $wpdb->get_results("SELECT * FROM $table_duyurular ORDER BY id DESC LIMIT 8");
                ?>
                <?php if ($gonderilenler): ?>
                    <hr>
                    <p style="margin-bottom:6px;"><b>Son gönderilenler</b></p>
                    <?php foreach ($gonderilenler as $d): ?>
                        <div style="border-bottom:1px solid #d1c4e9; padding:6px 0; font-size:13px;">
                            <b>#<?php echo intval($d->id); ?> <?php echo esc_html($d->baslik); ?></b>
                            <?php if (!empty($d->oyun_id)): ?>
                                <span style="background:#d1c4e9; padding:1px 6px; border-radius:3px;">
                                    <?php echo esc_html($d->oyun_id); ?>
                                </span>
                            <?php else: ?>
                                <span style="background:#c8e6c9; padding:1px 6px; border-radius:3px;">tüm oyunlar</span>
                            <?php endif; ?>
                            <br>
                            <i><?php echo esc_html($d->mesaj); ?></i>
                            <br><small style="color:#777;"><?php echo esc_html($d->tarih); ?></small>
                            <form method="post" style="display:inline;">
                                <input type="hidden" name="islem" value="duyuru_sil">
                                <input type="hidden" name="id" value="<?php echo intval($d->id); ?>">
                                <button type="submit" class="button button-small" style="color:red;">Sil</button>
                            </form>
                        </div>
                    <?php endforeach; ?>
                    <p style="color:#777; font-size:12px; margin-top:8px;">
                        Silmek yalnızca bu listeden kaldırır; bildirimi almış
                        cihazlardan geri çekmez.
                    </p>
                <?php endif; ?>
            </div>
        </div>

        <div style="display: flex; gap: 20px; margin-bottom: 20px;">
            <div style="background: #fff3e0; padding: 15px; border: 1px solid #ffcc80; width: 48%;">
                <h3>📊 Topluluk Anketi Başlat / Kapat</h3>
                <form method="post">
                    <input type="hidden" name="islem" value="anket_olustur">
                    <input type="text" name="soru" placeholder="Soru?" required style="width: 100%; margin-bottom: 10px;">
                    <input type="text" name="sec1" placeholder="Seçenek 1" required style="width: 48%;">
                    <input type="text" name="sec2" placeholder="Seçenek 2" required style="width: 48%; margin-bottom: 10px;">
                    <button type="submit" class="button button-primary" style="background:#ff9800; border:none;">Yayınla</button>
                </form>
                
                <?php if($anket): ?>
                    <hr>
                    <p><b>Aktif Anket:</b> <?php echo $anket->soru; ?></p>
                    <p><?php echo $anket->secenek1; ?>: <b><?php echo $anket->oy1; ?> Oy</b> | <?php echo $anket->secenek2; ?>: <b><?php echo $anket->oy2; ?> Oy</b></p>
                    
                    <form method="post" style="margin-top:10px;">
                        <input type="hidden" name="islem" value="anket_kapat">
                        <button type="submit" class="button" style="color:red; border-color:red;">Anketi Sonlandır ve Gizle</button>
                    </form>
                <?php else: ?>
                    <p style="margin-top:15px; color:#555;"><i>Şu an yayında bir anket yok.</i></p>
                <?php endif; ?>
            </div>
            
            <div style="background: #ffebee; padding: 15px; border: 1px solid #ef9a9a; width: 48%; max-height: 300px; overflow-y: auto;">
                <h3 style="color:#c62828;">🐞 Hata Bildirimleri</h3>
                <?php if($bildirimler): foreach($bildirimler as $b): ?>
                    <div style="border-bottom: 1px solid #ffcdd2; margin-bottom:10px; padding-bottom:5px;">
                        <b>[<?php echo $b->tarih; ?>] <?php echo esc_html($b->kullanici); ?>:</b><br>
                        <i><?php echo esc_html($b->mesaj); ?></i>
                    </div>
                <?php endforeach; else: echo "<p>Bildirim yok.</p>"; endif; ?>
            </div>
        </div>

        <h3>🏆 En Çok Oynayanlar (İlk 10)</h3>
        <table class="wp-list-table widefat fixed striped" style="margin-bottom:20px;">
            <thead><tr><th>Sıra</th><th>Kullanıcı Adı</th><th>Başarımlar</th><th>Toplam Süre (Sn)</th><th>Son Aktiflik</th></tr></thead>
            <tbody>
                <?php $sira=1; foreach ($liderlik_kayitlari as $lk): 
                    $basarimlar = json_decode($lk->basarimlar, true);
                    $basarim_sayisi = is_array($basarimlar) ? count($basarimlar) : 0;
                ?>
                    <tr>
                        <td>#<?php echo $sira++; ?></td>
                        <td><b><?php echo esc_html($lk->kullanici_adi); ?></b></td>
                        <td>🏆 <?php echo $basarim_sayisi; ?>/16</td>
                        <td><?php echo $lk->toplam_saniye; ?> sn</td>
                        <td><?php echo $lk->son_guncelleme; ?></td>
                    </tr>
                <?php endforeach; ?>
            </tbody>
        </table>

        <h3>🔑 Aktif Lisanslar (Manuel, VIP ve Görev Anahtarları)</h3>
        <table class="wp-list-table widefat fixed striped">
            <thead><tr><th>Tür</th><th>Anahtar / Cihaz ID</th><th>Bitiş Tarihi</th><th>Durum</th><th>İşlem</th></tr></thead>
            <tbody>
                <?php foreach ($kayitlar as $kayit): ?>
                    <tr>
                        <td>
                            <?php 
                                if($kayit->is_vip) echo '<span style="background:gold;padding:3px;font-weight:bold;">VIP</span>';
                                elseif(strpos($kayit->lisans_anahtari, 'GOREV-') !== false) echo '<span style="background:#a5d6a7;padding:3px;">Görev</span>';
                                else echo '<span style="background:#e0e0e0;padding:3px;">Manuel</span>'; 
                            ?>
                        </td>
                        <td>
                            <?php echo esc_html($kayit->lisans_anahtari); ?> 
                            <?php echo $kayit->is_vip ? "<br><small>({$kayit->cihaz_id})</small>" : ""; ?>
                        </td>
                        <td>
                            <?php echo (empty($kayit->bitis_tarihi) || $kayit->bitis_tarihi == '0000-00-00 00:00:00') ? 'Sınırsız' : date('d.m.Y H:i', strtotime($kayit->bitis_tarihi)); ?>
                        </td>
                        <td>
                            <?php echo (!empty($kayit->bitis_tarihi) && $suan_str > $kayit->bitis_tarihi) ? '<span style="color:red">Bitti</span>' : '<span style="color:green">Aktif</span>'; ?>
                        </td>
                        <td>
                            <form method="post">
                                <input type="hidden" name="islem" value="sil">
                                <input type="hidden" name="id" value="<?php echo $kayit->id; ?>">
                                <button type="submit" class="button" style="color:red; border-color:red;">Sil</button>
                            </form>
                        </td>
                    </tr>
                <?php endforeach; ?>
            </tbody>
        </table>
    </div>
    <?php
}

add_shortcode('aerokey_gorev', 'aerokey_shortcode_gorev_sayfasi');

function aerokey_shortcode_gorev_sayfasi($atts) {
    global $wpdb; 
    $table_lisans = $wpdb->prefix . 'aerokey_lisanslar'; 
    $gizli_sifre = get_option('aerokey_link_sifresi', 'GIZLI2026'); 
    
    $gorev_linki = "https://linkperisi.com/go/rhoklg"; 
    
    $html = '<div style="background:#f9f9f9; padding:25px; border-radius:15px; border:1px solid #ddd; text-align:center; max-width:400px; margin:20px auto; font-family:sans-serif, Arial;">';
    $html .= '<h3 style="color:#ff9800; margin-top:0;">🔑 Anahtar Al</h3>';
    
    $html .= '<div id="aero_quest_result_area"></div>';
    $html .= '<div id="aero_quest_form_area">';
    $html .= '<p style="color:#555; font-size:14px;">1 günlük erişim anahtarı almak için aşağıdaki butona tıklayıp görevi (linki) geçin. Linkin sonunda size verilen gizli kodu kopyalayıp aşağıdaki kutuya yapıştırın. Anahtar otomatik oluşacaktır NOT: ayrıca kart çevirme oyunundan gelen hediyeleri sorunsuz kullanırsınız</p>';
    
    if(isset($_POST['gorev_kodu_gonder'])) {
        $girilen_kod = sanitize_text_field($_POST['gizli_kod']);
        
        if(strtoupper($girilen_kod) === strtoupper($gizli_sifre)) {
            $yeni_anahtar = 'GOREV-' . strtoupper(wp_generate_password(6, false)); 
            $bitis = date('Y-m-d H:i:s', strtotime("+1 days", current_time('timestamp')));
            
            $wpdb->insert($table_lisans, array('lisans_anahtari' => $yeni_anahtar, 'bitis_tarihi' => $bitis, 'is_vip' => 0));
            
            $html .= '<script>
                localStorage.setItem("aero_quest_key", "'.$yeni_anahtar.'");
                localStorage.setItem("aero_quest_expire", new Date().getTime() + 86400000); 
            </script>';
            
        } else { 
            $html .= '<div style="background:#ffebee; color:#c62828; padding:10px; border-radius:5px; margin-bottom:15px; border:1px solid #ffcdd2;"><b>Hata:</b> Girdiğiniz kod yanlış veya eksik! Lütfen görevi tamamlayın.</div>'; 
        }
    }
    
    $html .= '<a href="'.$gorev_linki.'" target="_blank" style="display:block; background:#2196F3; color:white; padding:12px; text-decoration:none; border-radius:8px; font-weight:bold; margin-bottom:20px; transition:0.3s;">1. ADIM: Göreve Git (Linki Geç)</a>';
    $html .= '<form method="post" action="">';
    $html .= '<input type="text" name="gizli_kod" placeholder="Gizli Kodu Buraya Girin" required style="width:100%; padding:12px; margin-bottom:15px; border:2px solid #ccc; border-radius:8px; text-align:center; font-weight:bold; box-sizing:border-box;">';
    $html .= '<button type="submit" name="gorev_kodu_gonder" style="background:#4CAF50; color:white; padding:12px; border:none; border-radius:8px; font-weight:bold; cursor:pointer; width:100%; font-size:16px;">2. ADIM: Doğrula ve Anahtar Üret</button>';
    $html .= '</form>';
    $html .= '</div>'; 
    
    $html .= '<script>
        if ( window.history.replaceState ) {
            window.history.replaceState( null, null, window.location.href );
        }
        
        document.addEventListener("DOMContentLoaded", function() {
            var savedKey = localStorage.getItem("aero_quest_key");
            var expire = localStorage.getItem("aero_quest_expire");
            var now = new Date().getTime();
            
            if (savedKey && expire && now < expire) {
                var formArea = document.getElementById("aero_quest_form_area");
                if(formArea) formArea.style.display = "none";
                
                document.getElementById("aero_quest_result_area").innerHTML = 
                    "<div style=\'background:#e8f5e9; color:#2e7d32; padding:15px; border-radius:8px; border:1px solid #c8e6c9; text-align:center;\'>" +
                    "<b style=\'font-size:16px;\'>Geçerli Anahtarınız:</b><br><br>" +
                    "<span style=\'font-size:24px; font-weight:bold; letter-spacing:2px; background:#fff; padding:5px 10px; border-radius:5px; border:1px dashed #4CAF50;\'>" + savedKey + "</span>" +
                    "<br><br><small>(Bu anahtarın süresi bitene kadar görev sistemi sizin için kilitlenmiştir.)</small></div>";
            } else if(now >= expire) {
                localStorage.removeItem("aero_quest_key");
                localStorage.removeItem("aero_quest_expire");
            }
        });
    </script>';
    
    $html .= '</div>'; 
    return $html;
}
?>
