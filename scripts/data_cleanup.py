"""
Data Cleanup System
Eski verileri temizleyerek disk ve RAM kullanimini kontrol altinda tutar.
Daily report sirasinda otomatik calisir.
"""

import json
import os
from datetime import datetime, timedelta
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import DATA_RETENTION_DAYS

# Data dosya yollari
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORTFOLIO_FILE = os.path.join(BASE_DIR, "data", "virtual_portfolio.json")
TRADES_LOG = os.path.join(BASE_DIR, "logs", "trades.log")
EARLY_SMART_MONEY_FILE = os.path.join(BASE_DIR, "data", "early_smart_money.json")
FAKE_ALERTS_FILE = os.path.join(BASE_DIR, "data", "fake_alerts.json")


def cleanup_portfolio_snapshots():
    """
    Virtual portfolio'daki eski daily snapshot'lari sil.
    Son DATA_RETENTION_DAYS gunluk snapshot'lari tutar.
    """
    if not os.path.exists(PORTFOLIO_FILE):
        return 0

    with open(PORTFOLIO_FILE, 'r') as f:
        data = json.load(f)

    snapshots = data.get("daily_snapshots", [])
    if not snapshots:
        return 0

    cutoff = datetime.now() - timedelta(days=DATA_RETENTION_DAYS)
    cutoff_str = cutoff.isoformat()

    original_count = len(snapshots)
    data["daily_snapshots"] = [
        s for s in snapshots
        if s.get("timestamp", "") >= cutoff_str
    ]
    removed = original_count - len(data["daily_snapshots"])

    if removed > 0:
        with open(PORTFOLIO_FILE, 'w') as f:
            json.dump(data, f, indent=2)
        print(f"  🧹 Portfolio: {removed} eski snapshot silindi (son {DATA_RETENTION_DAYS} gun tutuldu)")

    return removed


def cleanup_trades_log():
    """
    Eski trade log satirlarini sil.
    Son DATA_RETENTION_DAYS gunluk loglari tutar.
    """
    if not os.path.exists(TRADES_LOG):
        return 0

    cutoff = datetime.now() - timedelta(days=DATA_RETENTION_DAYS)
    cutoff_str = cutoff.strftime("%Y-%m-%d")

    with open(TRADES_LOG, 'r') as f:
        lines = f.readlines()

    original_count = len(lines)

    # Format: [2026-02-06 14:47:23] [S1] BUY: ...
    kept_lines = []
    for line in lines:
        try:
            # Tarih bilgisini cikar: [YYYY-MM-DD ...]
            if line.startswith("["):
                date_part = line[1:11]  # YYYY-MM-DD
                if date_part >= cutoff_str:
                    kept_lines.append(line)
            else:
                kept_lines.append(line)  # Format disiysa tut
        except Exception:
            kept_lines.append(line)

    removed = original_count - len(kept_lines)

    if removed > 0:
        with open(TRADES_LOG, 'w') as f:
            f.writelines(kept_lines)
        print(f"  🧹 Trades log: {removed} eski satir silindi")

    return removed


def cleanup_early_smart_money():
    """
    DATA_RETENTION_DAYS gun boyunca aktif olmayan early smart money cuzdanlarini temizle.
    Not: Smartest wallet'a terfi etmis olanlar temizlenmez.
    """
    if not os.path.exists(EARLY_SMART_MONEY_FILE):
        return 0

    with open(EARLY_SMART_MONEY_FILE, 'r') as f:
        data = json.load(f)

    wallets = data.get("wallets", {})
    if not wallets:
        return 0

    cutoff = datetime.now() - timedelta(days=90)  # Early detection icin 90 gun
    cutoff_str = cutoff.isoformat()

    # Smartest wallets'i yukle (bunlari silme)
    smartest_file = os.path.join(BASE_DIR, "data", "smartest_wallets.json")
    smartest_addresses = set()
    if os.path.exists(smartest_file):
        with open(smartest_file, 'r') as f:
            smartest_data = json.load(f)
        smartest_addresses = {w["address"].lower() for w in smartest_data.get("wallets", [])}

    to_remove = []
    for wallet, info in wallets.items():
        last_seen = info.get("last_seen", "")
        # Smartest wallet degilse ve uzun suredir aktif degilse sil
        if wallet.lower() not in smartest_addresses and last_seen < cutoff_str:
            to_remove.append(wallet)

    for wallet in to_remove:
        del data["wallets"][wallet]

    if to_remove:
        data["updated_at"] = datetime.now().isoformat()
        with open(EARLY_SMART_MONEY_FILE, 'w') as f:
            json.dump(data, f, indent=2)
        print(f"  🧹 Early smart money: {len(to_remove)} inaktif cuzdan silindi")

    return len(to_remove)


def cleanup_fake_alerts():
    """Eski fake alert loglarini temizle."""
    if not os.path.exists(FAKE_ALERTS_FILE):
        return 0

    with open(FAKE_ALERTS_FILE, 'r') as f:
        data = json.load(f)

    alerts_log = data.get("alerts_log", [])
    if not alerts_log:
        return 0

    cutoff = datetime.now() - timedelta(days=DATA_RETENTION_DAYS)
    cutoff_str = cutoff.isoformat()

    original_count = len(alerts_log)
    data["alerts_log"] = [
        a for a in alerts_log
        if a.get("time", "") >= cutoff_str
    ]
    removed = original_count - len(data["alerts_log"])

    if removed > 0:
        data["updated_at"] = datetime.now().isoformat()
        with open(FAKE_ALERTS_FILE, 'w') as f:
            json.dump(data, f, indent=2)
        print(f"  🧹 Fake alerts log: {removed} eski kayit silindi")

    return removed


def run_full_cleanup():
    """
    Tum veri temizleme islemlerini calistir.
    Daily report sirasinda cagrilir.
    """
    print(f"\n🧹 Veri temizleme baslatildi (retention: {DATA_RETENTION_DAYS} gun)")

    total_removed = 0
    total_removed += cleanup_portfolio_snapshots()
    total_removed += cleanup_trades_log()
    total_removed += cleanup_early_smart_money()
    total_removed += cleanup_fake_alerts()

    if total_removed == 0:
        print("  ✅ Temizlenecek veri yok")
    else:
        print(f"  ✅ Toplam {total_removed} eski kayit temizlendi")

    return total_removed


# Test
if __name__ == "__main__":
    print("Data Cleanup Test")
    print("=" * 50)
    run_full_cleanup()
