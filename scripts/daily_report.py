"""
Daily Report System
Her gün 20:30'da Telegram'a günlük rapor gönderir.
"""

import json
import sys
import os
from datetime import datetime, timezone, timedelta
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.telegram_alert import send_telegram_message
from scripts.early_detector import load_smartest_wallets, SMARTEST_TARGET
from scripts.data_cleanup import run_full_cleanup
from scripts.fake_alert_tracker import load_fake_alerts
from scripts.database import is_db_available

# Rapor saati (Türkiye saati UTC+3)
REPORT_HOUR = 20
REPORT_MINUTE = 30

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
UTC_PLUS_3 = timezone(timedelta(hours=3))

# Bugün gönderildi mi flag (basit duplicate kontrolü)
_last_report_date = None


def _get_call_stats() -> dict:
    """
    Alert kalite istatistiklerini hesapla.
    DB'den token_evaluations veya alert_analysis.json'dan çeker.
    """
    stats = {
        "total_alerts": 0,
        "short_list_5min": 0,      # 5dk MCap +20%
        "contracts_check_30min": 0, # 30dk MCap +50%
        "trash_calls": 0,
        "success_rate_5min": 0,
        "success_rate_30min": 0,
    }

    # Önce DB'den dene
    if is_db_available():
        try:
            from scripts.database import get_all_token_evaluations
            evaluations = get_all_token_evaluations()
            if evaluations:
                for ev in evaluations:
                    cls = ev.get("classification", "")
                    if cls == "short_list":
                        stats["short_list_5min"] += 1
                    elif cls == "contracts_check":
                        stats["contracts_check_30min"] += 1
                    elif cls in ("trash", "not_short_list", "dead"):
                        stats["trash_calls"] += 1

                stats["total_alerts"] = stats["short_list_5min"] + stats["contracts_check_30min"] + stats["trash_calls"]

                if stats["total_alerts"] > 0:
                    stats["success_rate_5min"] = (stats["short_list_5min"] + stats["contracts_check_30min"]) / stats["total_alerts"] * 100
                    stats["success_rate_30min"] = stats["contracts_check_30min"] / stats["total_alerts"] * 100

                return stats
        except Exception:
            pass

    # Fallback: alert_analysis.json
    analysis_file = os.path.join(DATA_DIR, "alert_analysis.json")
    if os.path.exists(analysis_file):
        try:
            with open(analysis_file, 'r') as f:
                data = json.load(f)
            counts = data.get("counts", {})
            stats["total_alerts"] = counts.get("total_alerts", 0)
            stats["short_list_5min"] = counts.get("short_list", 0)
            stats["contracts_check_30min"] = counts.get("contracts_check", 0)
            stats["trash_calls"] = counts.get("trash_calls", 0)

            if stats["total_alerts"] > 0:
                stats["success_rate_5min"] = (stats["short_list_5min"] + stats["contracts_check_30min"]) / stats["total_alerts"] * 100
                stats["success_rate_30min"] = stats["contracts_check_30min"] / stats["total_alerts"] * 100
        except Exception:
            pass

    return stats


def generate_daily_report() -> str:
    """Günlük rapor mesajı oluştur."""
    now = datetime.now(UTC_PLUS_3)

    # Call istatistikleri
    call_stats = _get_call_stats()

    # Smartest wallets durumu
    smartest = load_smartest_wallets()
    smartest_count = smartest.get("current_count", 0)

    # Fake alert durumu
    fake_data = load_fake_alerts()
    fake_flagged_count = len(fake_data.get("flagged_wallets", []))
    fake_total_alerts = len(fake_data.get("alerts_log", []))

    # İzlenen cüzdan sayısı
    wallets_file = os.path.join(DATA_DIR, "smart_money_final.json")
    total_wallets = 0
    try:
        with open(wallets_file, 'r') as f:
            wallet_data = json.load(f)
        total_wallets = len(wallet_data.get("wallets", []))
    except Exception:
        pass

    report = f"""
📊 <b>GÜN SONU RAPORU</b> - {now.strftime('%d.%m.%Y')}

━━━━━━━━━━━━━━━━━━━━

📡 <b>ALERT KALİTE ANALİZİ</b>
├─ Toplam Alert: {call_stats['total_alerts']}
├─ ✅ 5dk Başarılı (MCap +20%): {call_stats['short_list_5min'] + call_stats['contracts_check_30min']} ({call_stats['success_rate_5min']:.0f}%)
├─ 🏆 30dk Başarılı (MCap +50%): {call_stats['contracts_check_30min']} ({call_stats['success_rate_30min']:.0f}%)
└─ 🗑️ Trash Call: {call_stats['trash_calls']}

━━━━━━━━━━━━━━━━━━━━

👛 <b>CÜZDAN DURUMU</b>
├─ İzlenen: {total_wallets} cüzdan
├─ 🧠 Smartest: {smartest_count}/{SMARTEST_TARGET} bulundu
└─ 🚩 Fake Alert: {fake_total_alerts} tespit | {fake_flagged_count} cüzdan flagli
"""

    # Self-improving engine cüzdan durumu (ekleme/çıkarma)
    try:
        from scripts.wallet_evaluator import get_daily_wallet_report_summary
        wallet_summary = get_daily_wallet_report_summary()
        if wallet_summary:
            report += "\n" + wallet_summary
    except Exception:
        pass

    return report.strip()


def send_daily_report() -> bool:
    """Günlük raporu Telegram'a gönder."""
    global _last_report_date

    print("\n📤 Günlük rapor gönderiliyor...")

    # Rapor oluştur ve gönder
    report = generate_daily_report()
    success = send_telegram_message(report)

    if success:
        print("✅ Günlük rapor gönderildi!")
        _last_report_date = datetime.now(UTC_PLUS_3).date()
    else:
        print("❌ Rapor gönderilemedi!")

    # Veri temizleme (her gun rapor sonrasi)
    try:
        run_full_cleanup()
    except Exception as e:
        print(f"⚠️ Cleanup hatası: {e}")

    # Self-improving engine günlük değerlendirme
    try:
        from scripts.self_improving_engine import run_daily_evaluation, SELF_IMPROVE_ENABLED
        if SELF_IMPROVE_ENABLED:
            print("\n🔄 Self-improving engine günlük değerlendirme...")
            run_daily_evaluation()
    except Exception as e:
        print(f"⚠️ Self-improving engine hatası: {e}")

    return success


async def schedule_daily_report():
    """
    Her gün 20:30'da rapor gönder.
    Bu fonksiyon ana monitor ile birlikte çalışır.
    """
    while True:
        now = datetime.now()
        target = now.replace(hour=REPORT_HOUR, minute=REPORT_MINUTE, second=0, microsecond=0)

        # Eğer hedef saat geçtiyse, yarına ayarla
        if now >= target:
            target = target.replace(day=now.day + 1)

        # Bekleme süresi
        wait_seconds = (target - now).total_seconds()

        print(f"⏰ Sonraki rapor: {target.strftime('%d.%m.%Y %H:%M')} ({wait_seconds/3600:.1f} saat sonra)")

        # Bekle
        await asyncio.sleep(wait_seconds)

        # Rapor gönder
        send_daily_report()


def check_and_send_if_time():
    """
    Rapor zamanı geldi mi kontrol et.
    Polling-based sistemlerde kullanılır.
    """
    global _last_report_date
    now = datetime.now(UTC_PLUS_3)

    # 20:30-20:35 arası mı?
    if now.hour == REPORT_HOUR and REPORT_MINUTE <= now.minute < REPORT_MINUTE + 5:
        # Bugün zaten gönderildi mi kontrol et
        if _last_report_date == now.date():
            return False

        send_daily_report()
        return True

    return False


# Test
if __name__ == "__main__":
    print("Daily Report Test")
    print("=" * 50)

    # Test raporu oluştur
    report = generate_daily_report()
    print(report)

    # Gönderme testi (yorum satırını kaldırarak test edilebilir)
    # send_daily_report()
