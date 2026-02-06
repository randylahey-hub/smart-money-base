"""
Daily Report System
Her gün 23:30'da Telegram'a günlük PnL raporu gönderir.
"""

import sys
import os
from datetime import datetime
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.telegram_alert import send_telegram_message
from scripts.virtual_trader import get_trader
from scripts.early_detector import load_smartest_wallets, SMARTEST_TARGET
from scripts.data_cleanup import run_full_cleanup
from scripts.fake_alert_tracker import load_fake_alerts

# Rapor saati (Türkiye saati UTC+3)
REPORT_HOUR = 23
REPORT_MINUTE = 30


def format_pnl(value: float) -> str:
    """PnL değerini formatla."""
    if value >= 0:
        return f"+{value:.4f}"
    return f"{value:.4f}"


def format_percent(initial: float, current: float) -> str:
    """Yüzde değişimi formatla."""
    if initial <= 0:
        return "N/A"
    change = ((current - initial) / initial) * 100
    if change >= 0:
        return f"+{change:.1f}%"
    return f"{change:.1f}%"


def generate_daily_report() -> str:
    """Günlük rapor mesajı oluştur."""
    trader = get_trader()
    summary = trader.get_daily_summary()

    s1 = summary["scenario1"]
    s2 = summary["scenario2"]
    total = summary["total"]

    # Smartest wallets durumu
    smartest = load_smartest_wallets()
    smartest_count = smartest.get("current_count", 0)

    # Fake alert durumu
    fake_data = load_fake_alerts()
    fake_flagged_count = len(fake_data.get("flagged_wallets", []))
    fake_total_alerts = len(fake_data.get("alerts_log", []))

    # Win rate hesapla
    s1_total_trades = s1["wins"] + s1["losses"]
    s1_win_rate = (s1["wins"] / s1_total_trades * 100) if s1_total_trades > 0 else 0

    s2_total_trades = s2["wins"] + s2["losses"]
    s2_win_rate = (s2["wins"] / s2_total_trades * 100) if s2_total_trades > 0 else 0

    report = f"""
📊 <b>GÜN SONU RAPORU</b> - {summary['date']}

━━━━━━━━━━━━━━━━━━━━

💼 <b>SENARYO 1: Smart Money Copy</b>
├─ Başlangıç: {s1['initial']:.4f} ETH
├─ Güncel: {s1['current']:.4f} ETH
├─ PnL: {format_pnl(s1['total_pnl'])} ETH ({format_percent(s1['initial'], s1['current'])})
├─ Açık Pozisyon: {s1['open_positions']}
└─ Trade: {s1_total_trades} ({s1['wins']}W / {s1['losses']}L) - {s1_win_rate:.0f}% WR

━━━━━━━━━━━━━━━━━━━━

🎯 <b>SENARYO 2: Smartest Wallets Copy</b>
├─ Başlangıç: {s2['initial']:.4f} ETH
├─ Güncel: {s2['current']:.4f} ETH
├─ PnL: {format_pnl(s2['total_pnl'])} ETH ({format_percent(s2['initial'], s2['current'])})
├─ Açık Pozisyon: {s2['open_positions']}
└─ Trade: {s2_total_trades} ({s2['wins']}W / {s2['losses']}L) - {s2_win_rate:.0f}% WR

━━━━━━━━━━━━━━━━━━━━

📈 <b>TOPLAM PORTFÖY</b>
├─ Başlangıç: {total['initial']:.4f} ETH
├─ Güncel: {total['current']:.4f} ETH
└─ Toplam PnL: {format_pnl(total['total_pnl'])} ETH ({format_percent(total['initial'], total['current'])})

━━━━━━━━━━━━━━━━━━━━

🧠 <b>Smartest Wallets:</b> {smartest_count}/{SMARTEST_TARGET} bulundu
🚩 <b>Fake Alert:</b> {fake_total_alerts} tespit | {fake_flagged_count} cüzdan flagli
"""

    return report.strip()


def send_daily_report() -> bool:
    """Günlük raporu Telegram'a gönder."""
    print("\n📤 Günlük rapor gönderiliyor...")

    # Snapshot al
    trader = get_trader()
    trader.take_daily_snapshot()

    # Rapor oluştur ve gönder
    report = generate_daily_report()
    success = send_telegram_message(report)

    if success:
        print("✅ Günlük rapor gönderildi!")
    else:
        print("❌ Rapor gönderilemedi!")

    # Veri temizleme (her gun rapor sonrasi)
    try:
        run_full_cleanup()
    except Exception as e:
        print(f"⚠️ Cleanup hatası: {e}")

    return success


async def schedule_daily_report():
    """
    Her gün 23:30'da rapor gönder.
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
    now = datetime.now()

    # 23:30-23:35 arası mı?
    if now.hour == REPORT_HOUR and REPORT_MINUTE <= now.minute < REPORT_MINUTE + 5:
        # Bugün zaten gönderildi mi kontrol et
        trader = get_trader()
        snapshots = trader.portfolio.get("daily_snapshots", [])

        if snapshots:
            last_snapshot = snapshots[-1]
            last_time = datetime.fromisoformat(last_snapshot["timestamp"])

            # Bugün zaten gönderilmişse atla
            if last_time.date() == now.date():
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
