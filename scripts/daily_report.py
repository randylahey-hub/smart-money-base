"""
Daily Report System
Her gün 00:00 UTC+3'te Telegram'a günlük kapanış raporu gönderir.

Format:
- Bir önceki günün tüm alertleri token bazlı listelenir
- Her token: alert MCap → güncel MCap, % değişim
- Pozitif = W (Win), Negatif = L (Loss)
- Trash call oranı
- Cüzdan ekleme/çıkarma bilgisi
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

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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


def _fetch_current_mcap(token_address: str) -> float:
    """DexScreener'dan token'in güncel MCap bilgisini al."""
    try:
        from scripts.alert_analyzer import fetch_current_mcap
        data = fetch_current_mcap(token_address)
        return data.get("mcap", 0)
    except Exception as e:
        print(f"⚠️ MCap fetch hatası ({token_address[:10]}...): {e}")
        return 0


def _get_wallet_changes() -> str:
    """Cüzdan durumu: toplam sayı + varsa ekleme/çıkarma bilgisi."""
    lines = []

    # Toplam cüzdan sayısı (her zaman göster)
    wallets_file = os.path.join(DATA_DIR, "data", "smart_money_final.json")
    try:
        with open(wallets_file, 'r') as f:
            wallet_data = json.load(f)
        total = len(wallet_data.get("wallets", []))
        lines.append(f"👛 <b>Cüzdan:</b> {total} izleniyor")
    except Exception:
        pass

    # Ekleme/çıkarma bilgisi (wallet_evaluator çalışmışsa)
    try:
        from scripts.wallet_evaluator import get_daily_wallet_report_summary
        summary = get_daily_wallet_report_summary()
        if summary and summary.strip():
            # "Cüzdan Durumu:" başlığını çıkar (çift başlık olmasın)
            for line in summary.strip().split("\n"):
                stripped = line.strip()
                if stripped and "Cüzdan Durumu" not in stripped and "Toplam:" not in stripped:
                    lines.append(stripped)
    except Exception:
        pass

    return "\n".join(lines) if lines else ""


def _get_yesterday_alerts() -> list:
    """
    Dünün alertlerini DB'den çek (UTC+3 00:00 - 23:59).
    alert_snapshots + token_evaluations JOIN ile alert_mcap ve classification alır.
    """
    if not is_db_available():
        return []

    now_tr = datetime.now(UTC_PLUS_3)
    yesterday_tr = now_tr - timedelta(days=1)

    # UTC+3 00:00 → UTC (3 saat çıkar)
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
    # Token bazında grupla (ilk alert_mcap'i tut, classification al, max ath_mcap)
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
                "classification": alert.get("classification", "unknown"),
                "ath_mcap": alert.get("ath_mcap", 0),
            }
        else:
            token_map[addr]["alert_count"] += 1
            # İlk alert'te classification unknown ama sonraki doluysa güncelle
            if token_map[addr]["classification"] == "unknown":
                token_map[addr]["classification"] = alert.get("classification", "unknown")
            # En yüksek ATH MCap'i tut
            new_ath = alert.get("ath_mcap", 0)
            if new_ath > token_map[addr]["ath_mcap"]:
                token_map[addr]["ath_mcap"] = new_ath

    # Her token için güncel MCap çek
    results = []
    for addr, info in token_map.items():
        time.sleep(0.3)  # DexScreener rate limit

        current_mcap = _fetch_current_mcap(addr)
        alert_mcap = info["alert_mcap"]
        ath_mcap = info.get("ath_mcap", 0)

        # ATH MCap: DB'den gelen vs şu anki MCap — hangisi büyükse
        if current_mcap > ath_mcap:
            ath_mcap = current_mcap

        # alert_mcap=0 ise W/L hesaplanamaz
        has_alert_mcap = alert_mcap > 0

        # HOLD senaryosu: alert → şu an
        if has_alert_mcap:
            change_pct = ((current_mcap - alert_mcap) / alert_mcap) * 100
        else:
            change_pct = None

        # ATH senaryosu: alert → max MCap (ideal sell)
        if has_alert_mcap and ath_mcap > 0:
            ath_change_pct = ((ath_mcap - alert_mcap) / alert_mcap) * 100
        else:
            ath_change_pct = None

        # W/L: ATH bazlı (token en az bir kere yükseldiyse W)
        if ath_change_pct is not None:
            is_win = ath_change_pct > 0
        elif change_pct is not None:
            is_win = change_pct > 0
        else:
            is_win = False

        results.append({
            "token_symbol": info["token_symbol"],
            "token_address": addr,
            "alert_mcap": alert_mcap,
            "current_mcap": current_mcap,
            "ath_mcap": ath_mcap,
            "change_pct": round(change_pct, 1) if change_pct is not None else None,
            "ath_change_pct": round(ath_change_pct, 1) if ath_change_pct is not None else None,
            "is_win": is_win,
            "alert_count": info["alert_count"],
            "classification": info["classification"],
            "has_alert_mcap": has_alert_mcap,
        })

    # ATH değişim yüzdesine göre sırala (en iyi → en kötü, None'lar sona)
    results.sort(key=lambda x: x["ath_change_pct"] if x["ath_change_pct"] is not None else -9999, reverse=True)
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

    # Toplam alert sayısı (snapshot bazlı, token bazlı değil)
    total_alerts = len(alerts)

    # W/L sayıları (sadece alert_mcap verisi olan tokenlar)
    tokens_with_data = [t for t in token_summary if t["has_alert_mcap"]]
    tokens_no_data = [t for t in token_summary if not t["has_alert_mcap"]]
    wins = sum(1 for t in tokens_with_data if t["is_win"])
    losses = sum(1 for t in tokens_with_data if not t["is_win"])
    total_tokens = len(token_summary)

    # Trash call hesabı (classification bazlı)
    trash_count = sum(1 for t in token_summary if t["classification"] in ("not_short_list", "trash", "dead"))
    success_count = sum(1 for t in token_summary if t["classification"] in ("short_list", "contracts_check"))
    unknown_count = sum(1 for t in token_summary if t["classification"] in ("unknown",))

    # Rapor başlığı
    lines = [
        f"📊 <b>GÜNLÜK KAPANIŞ</b> — {date_str}",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"",
    ]

    # Token listesi — ATH (ideal sell) ve Current (hold) göster
    for t in token_summary:
        symbol = t["token_symbol"]
        alert_mcap_str = _format_mcap(t["alert_mcap"])
        current_mcap_str = _format_mcap(t["current_mcap"])
        ath_mcap_str = _format_mcap(t["ath_mcap"])

        if t["has_alert_mcap"] and t["ath_change_pct"] is not None:
            emoji = "🟢" if t["is_win"] else "🔴"
            wl = "W" if t["is_win"] else "L"

            # ATH satır (ana gösterge)
            ath_str = f"+{t['ath_change_pct']:.0f}%" if t["ath_change_pct"] >= 0 else f"{t['ath_change_pct']:.0f}%"

            # Current satır (hold durumu)
            if t["change_pct"] is not None:
                cur_str = f"+{t['change_pct']:.0f}%" if t["change_pct"] >= 0 else f"{t['change_pct']:.0f}%"
            else:
                cur_str = "?"

            # Tek satır: Alert → ATH (peak %) | Şimdi (hold %)
            line = f"{emoji} <b>{symbol}</b> | {alert_mcap_str} → 🔝{ath_mcap_str} ({ath_str}) 📍{current_mcap_str} ({cur_str}) <b>{wl}</b>"
        elif t["has_alert_mcap"] and t["change_pct"] is not None:
            # ATH yok ama current var
            emoji = "🟢" if t["change_pct"] > 0 else "🔴"
            wl = "W" if t["change_pct"] > 0 else "L"
            change_str = f"+{t['change_pct']:.0f}%" if t["change_pct"] >= 0 else f"{t['change_pct']:.0f}%"
            line = f"{emoji} <b>{symbol}</b> | {alert_mcap_str} → {current_mcap_str} ({change_str}) <b>{wl}</b>"
        else:
            line = f"⚪ <b>{symbol}</b> | ? → {current_mcap_str} (veri yok)"
        lines.append(line)

    # Separator
    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━")

    # W/L özet (ATH bazlı — token yükseldiyse W)
    if tokens_with_data:
        win_rate = (wins / len(tokens_with_data) * 100) if tokens_with_data else 0
        lines.append(f"📈 <b>{wins}W</b> / <b>{losses}L</b> — {len(tokens_with_data)} token ({win_rate:.0f}% ATH başarı)")
    if tokens_no_data:
        lines.append(f"⚪ {len(tokens_no_data)} token MCap verisi eksik")

    # Trash call oranı
    if trash_count > 0 or success_count > 0:
        total_classified = trash_count + success_count
        trash_pct = (trash_count / total_classified * 100) if total_classified > 0 else 0
        lines.append(f"🗑️ Trash: {trash_count}/{total_classified} ({trash_pct:.0f}%) | ✅ Başarılı: {success_count}")
        if unknown_count > 0:
            lines.append(f"❓ Henüz değerlendirilmemiş: {unknown_count}")
    elif unknown_count > 0:
        lines.append(f"❓ {unknown_count} token henüz değerlendirilmemiş")

    # Toplam alert sayısı (snapshot bazlı)
    lines.append(f"📡 Toplam alert: {total_alerts} ({total_tokens} farklı token)")

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
        if _last_report_date == now.date():
            return False

        send_daily_report()
        return True

    return False


# Test
if __name__ == "__main__":
    print("Daily Report Test")
    print("=" * 50)

    report = generate_daily_report()
    print(report)
