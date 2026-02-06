"""
Fake Alert Tracker
Dusuk hacimli tokenlerde alert ureten cuzdanlari flagler.
FAKE_ALERT_FLAG_THRESHOLD kez fake alert ureten cuzdanlar isaretlenir.
"""

import json
import os
from datetime import datetime, timedelta
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import FAKE_ALERT_FLAG_THRESHOLD, DATA_RETENTION_DAYS
from scripts.database import load_fake_alerts_db, save_fake_alerts_db, is_db_available

# Data dosya yolu
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAKE_ALERTS_FILE = os.path.join(BASE_DIR, "data", "fake_alerts.json")


def load_fake_alerts() -> dict:
    """Fake alert verilerini yukle. Önce DB, yoksa JSON."""
    if is_db_available():
        db_data = load_fake_alerts_db()
        if db_data:
            return db_data

    if os.path.exists(FAKE_ALERTS_FILE):
        with open(FAKE_ALERTS_FILE, 'r') as f:
            return json.load(f)
    return {
        "wallets": {},
        "flagged_wallets": [],
        "alerts_log": [],
        "updated_at": None
    }


def save_fake_alerts(data: dict):
    """Fake alert verilerini kaydet. DB + JSON."""
    data["updated_at"] = datetime.now().isoformat()

    if is_db_available():
        save_fake_alerts_db(data)

    try:
        with open(FAKE_ALERTS_FILE, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def record_fake_alert(wallet_addresses: list, token_address: str, token_symbol: str, volume_24h: float):
    """
    Fake alert kaydeder ve cuzdanlari flagler.

    Args:
        wallet_addresses: Alert'teki cuzdan adresleri
        token_address: Token contract adresi
        token_symbol: Token sembolu
        volume_24h: 24 saatlik hacim (USD)
    """
    data = load_fake_alerts()

    # Alert loguna ekle
    alert_record = {
        "token": token_address,
        "symbol": token_symbol,
        "volume_24h": volume_24h,
        "wallets": [w[:10] + "..." for w in wallet_addresses],
        "time": datetime.now().isoformat()
    }
    data["alerts_log"].append(alert_record)

    # Her cuzdanin fake alert sayisini artir
    newly_flagged = []
    for wallet in wallet_addresses:
        wallet_lower = wallet.lower()

        if wallet_lower not in data["wallets"]:
            data["wallets"][wallet_lower] = {
                "fake_count": 0,
                "tokens": [],
                "first_fake": datetime.now().isoformat(),
                "last_fake": datetime.now().isoformat(),
                "flagged": False
            }

        w_data = data["wallets"][wallet_lower]
        w_data["fake_count"] += 1
        w_data["last_fake"] = datetime.now().isoformat()

        if token_symbol not in w_data["tokens"]:
            w_data["tokens"].append(token_symbol)

        # Esik kontrolu
        if w_data["fake_count"] >= FAKE_ALERT_FLAG_THRESHOLD and not w_data["flagged"]:
            w_data["flagged"] = True
            if wallet_lower not in data["flagged_wallets"]:
                data["flagged_wallets"].append(wallet_lower)
                newly_flagged.append(wallet_lower)
                print(f"  🚩 FLAGGED: {wallet_lower[:10]}... | {w_data['fake_count']} fake alert")

    save_fake_alerts(data)

    print(f"  📝 Fake alert kaydedildi: {token_symbol} | Vol: ${volume_24h:.0f} | {len(wallet_addresses)} cuzdan")

    return newly_flagged


def is_flagged_wallet(address: str) -> bool:
    """Cuzdanin flagli olup olmadigini kontrol et."""
    data = load_fake_alerts()
    return address.lower() in data.get("flagged_wallets", [])


def get_flagged_wallets() -> list:
    """Tum flagli cuzdanlari dondur."""
    data = load_fake_alerts()
    return data.get("flagged_wallets", [])


def get_wallet_fake_count(address: str) -> int:
    """Cuzdanin fake alert sayisini dondur."""
    data = load_fake_alerts()
    w_data = data.get("wallets", {}).get(address.lower(), {})
    return w_data.get("fake_count", 0)


def cleanup_old_fake_alerts():
    """Eski fake alert loglarini temizle (DATA_RETENTION_DAYS gun oncesi)."""
    data = load_fake_alerts()
    cutoff = datetime.now() - timedelta(days=DATA_RETENTION_DAYS)
    cutoff_str = cutoff.isoformat()

    # Eski alert loglarini sil
    original_count = len(data["alerts_log"])
    data["alerts_log"] = [
        a for a in data["alerts_log"]
        if a.get("time", "") >= cutoff_str
    ]
    removed = original_count - len(data["alerts_log"])

    if removed > 0:
        save_fake_alerts(data)
        print(f"  🧹 {removed} eski fake alert logu temizlendi")

    return removed
