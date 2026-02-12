"""
Daily Report System
Her gün 00:00 UTC+3'te Telegram'a günlük kapanış raporu gönderir.

Format:
- Bir önceki günün tüm alertleri token bazlı listelenir
- Her token: alert MCap → ATH/ATL MCap, % değişim
- Pozitif = W (Win), Negatif = L (Loss)
- Toplam W/L sayısıyla biter
"""

import json
import sys
import os
import time
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.telegram_alert import send_telegram_message
from scripts.data_cleanup import run_full_cleanup
from scripts.database import is_db_available, get_alerts_by_date_range

# Rapor saati (Türkiye saati UTC+3) — gece yarısı
REPORT_HOUR = 0
REPORT_MINUTE = 0

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
UTC_PLUS_3 = timezone(timedelta(hours=3))

# Bugün gönderildi mi flag
_last_report_date = None


def _format_mcap(mcap: float) -> str:
    """MCap'i okunabilir formata çevir: $1.5M, $500K vb."""
    if mcap >= 1_000_000:
        return f"${mcap / 1_000_000:.1f}M"
    elif mcap >= 1_000:
        return f"${mcap / 1_000:.0f}K"
    elif mcap > 0:
        return f"${mcap:.0f}"
    else:
        return "$0"


def _fetch_token_ath_atl(token_address: str) -> dict:
    """DexScreener'dan token'in mevcut MCap bilgisini al."""
    try:
        from scripts.alert_analyzer import fetch_current_mcap
        data = fetch_current_mcap(token_address)
        return {"current_mcap": data.get("mcap", 0)}
    except Exception as e:
        print(f"⚠️ MCap fetch hatası ({token_address[:10]}...): {e}")
        return {"current_mcap": 0}


def _get_wallet_changes() -> str:
    """Cüzdan ekleme/çıkarma bilgisini al (wallet_evaluator'dan)."""
    try:
        from scripts.wallet_evaluator import get_daily_wallet_report_summary
        summary = get_daily_wallet_report_summary()
        if summary and summary.strip():
            # HTML formatına çevir
            return summary.strip()
        return ""
    except Exception:
        return ""


def _get_yesterday_alerts() -> list:
    """
    Dünün alertlerini DB'den çek (UTC+3 00:00 - 23:59).
    Aynı token birden fazla kez alert olmuşsa gruplanır.
    """
    if not is_db_available():
        return []

    # Dünün UTC+3 tarih aralığı
    now_tr = datetime.now(UTC_PLUS_3)
    yesterday_tr = now_tr - timedelta(days=1)

    # UTC+3 00:00 → UTC olarak hesapla (UTC+3'ten 3 saat çıkar)
    start_tr = yesterday_tr.replace(hour=0, minute=0, second=0, microsecond=0)
    end_tr = now_tr.replace(hour=0, minute=0, second=0, microsecond=0)

    start_utc = (start_tr - timedelta(hours=3)).isoformat()
    end_utc = (end_tr - timedelta(hours=3)).isoformat()

    alerts = get_alerts_by_date_range(start_utc, end_utc)
    return alerts


def _build_token_summary(alerts: list) -> list:
    """
    Alert listesinden token bazlı özet oluştur.
    Aynı token birden fazla alert almışsa ilk alert_mcap kullanılır.
    DexScreener'dan güncel MCap çekilir.
    """
    # Token bazında grupla (ilk alert_mcap'i tut)
    token_map = {}
    for alert in alerts:
        addr = alert["token_address"]
        if addr not in token_map:
            token_map[addr] = {
                "token_address": addr,
                "token_symbol": alert["token_symbol"] or "???",
                "alert_mcap": alert["alert_mcap"],
                "alert_count": 1,
                "wallet_count": alert.get("wallet_count", 0),
            }
        else:
            token_map[addr]["alert_count"] += 1

    # Her token için güncel MCap çek
    results = []
    for addr, info in token_map.items():
        # Rate limit: DexScreener'a 300ms arası
        time.sleep(0.3)

        current_data = _fetch_token_ath_atl(addr)
        current_mcap = current_data["current_mcap"]
        alert_mcap = info["alert_mcap"]

        # % değişim hesapla
        if alert_mcap > 0:
            change_pct = ((current_mcap - alert_mcap) / alert_mcap) * 100
        else:
            change_pct = 0

        # W veya L
        is_win = change_pct >= 0

        results.append({
            "token_symbol": info["token_symbol"],
            "token_address": addr,
            "alert_mcap": alert_mcap,
            "current_mcap": current_mcap,
            "change_pct": round(change_pct, 1),
            "is_win": is_win,
            "alert_count": info["alert_count"],
        })

    # Değişim yüzdesine göre sırala (en iyi → en kötü)
    results.sort(key=lambda x: x["change_pct"], reverse=True)
    return results


def generate_daily_report() -> str:
    """Günlük kapanış raporu oluştur."""
    now = datetime.now(UTC_PLUS_3)
    yesterday = now - timedelta(days=1)
    date_str = yesterday.strftime('%d.%m.%Y')

    # Dünün alertlerini al
    alerts = _get_yesterday_alerts()

    if not alerts:
        report = (
            f"📊 <b>GÜNLÜK KAPANIŞ</b> — {date_str}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Dün alert gönderilmedi."
        )
        return report

    # Token bazlı özet oluştur
    token_summary = _build_token_summary(alerts)

    wins = sum(1 for t in token_summary if t["is_win"])
    losses = sum(1 for t in token_summary if not t["is_win"])
    total = len(token_summary)

    # Rapor başlığı
    lines = [
        f"📊 <b>GÜNLÜK KAPANIŞ</b> — {date_str}",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"",
    ]

    # Token listesi
    for t in token_summary:
        emoji = "🟢" if t["is_win"] else "🔴"
        wl = "W" if t["is_win"] else "L"
        change_str = f"+{t['change_pct']:.0f}%" if t["change_pct"] >= 0 else f"{t['change_pct']:.0f}%"
        alert_mcap_str = _format_mcap(t["alert_mcap"])
        current_mcap_str = _format_mcap(t["current_mcap"])

        line = f"{emoji} <b>{t['token_symbol']}</b> | {alert_mcap_str} → {current_mcap_str} ({change_str}) <b>{wl}</b>"
        lines.append(line)

    # Toplam W/L
    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━")

    win_rate = (wins / total * 100) if total > 0 else 0
    lines.append(f"📈 <b>{wins}W</b> / <b>{losses}L</b> — {total} token ({win_rate:.0f}% başarı)")

    # Cüzdan ekleme/çıkarma bilgisi
    wallet_summary = _get_wallet_changes()
    if wallet_summary:
        lines.append("")
        lines.append(wallet_summary)

    return "\n".join(lines)


def send_daily_report() -> bool:
    """Günlük raporu Telegram'a gönder."""
    global _last_report_date

    print("\n📤 Günlük kapanış raporu gönderiliyor...")

    # Rapor oluştur ve gönder
    report = generate_daily_report()
    success = send_telegram_message(report)

    if success:
        print("✅ Günlük kapanış raporu gönderildi!")
        _last_report_date = datetime.now(UTC_PLUS_3).date()
    else:
        print("❌ Rapor gönderilemedi!")

    # Veri temizleme (her gün rapor sonrası)
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

    # Smartest wallet günlük yenileme
    try:
        from scripts.wallet_scorer import daily_refresh
        print("\n🔄 Smartest wallet günlük yenileme...")
        daily_refresh()
    except Exception as e:
        print(f"⚠️ Smartest wallet refresh hatası: {e}")

    return success


def check_and_send_if_time():
    """
    Rapor zamanı geldi mi kontrol et.
    Polling-based sistemlerde kullanılır.
    00:00-00:05 UTC+3 arası tetiklenir.
    """
    global _last_report_date
    now = datetime.now(UTC_PLUS_3)

    # 00:00-00:05 arası mı?
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
