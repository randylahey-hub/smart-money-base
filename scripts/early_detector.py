"""
Early Smart Money Detector
Alert anında smart money'den önce alım yapan cüzdanları tespit eder.
%50 daha düşük MCap'te alan cüzdanlar = Early Smart Money
3+ tokende early olan cüzdanlar = Smartest Wallets
"""

import json
import os
from datetime import datetime
from web3 import Web3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import BASE_RPC_HTTP, TRANSFER_EVENT_SIGNATURE
from scripts.telegram_alert import get_token_info_dexscreener
from scripts.database import (
    load_early_smart_money_db, save_early_smart_money_db,
    load_smartest_wallets_db, save_smartest_wallets_db,
    is_db_available
)

# Data dosya yolları
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EARLY_SMART_MONEY_FILE = os.path.join(BASE_DIR, "data", "early_smart_money.json")
SMARTEST_WALLETS_FILE = os.path.join(BASE_DIR, "data", "smartest_wallets.json")

# Web3 bağlantısı
w3 = Web3(Web3.HTTPProvider(BASE_RPC_HTTP))

# Smartest wallets hedefi
SMARTEST_TARGET = 10
EARLY_BUY_THRESHOLD = 3  # Kaç farklı tokende early alım yapmış olmalı


def load_early_smart_money() -> dict:
    """Early smart money verilerini yükle. Önce DB, yoksa JSON."""
    if is_db_available():
        db_data = load_early_smart_money_db()
        if db_data:
            return db_data

    if os.path.exists(EARLY_SMART_MONEY_FILE):
        with open(EARLY_SMART_MONEY_FILE, 'r') as f:
            return json.load(f)
    return {"wallets": {}, "updated_at": None}


def save_early_smart_money(data: dict):
    """Early smart money verilerini kaydet. DB + JSON."""
    data["updated_at"] = datetime.now().isoformat()

    if is_db_available():
        save_early_smart_money_db(data)

    try:
        with open(EARLY_SMART_MONEY_FILE, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def load_smartest_wallets() -> dict:
    """Smartest wallets verilerini yükle. Önce DB, yoksa JSON."""
    if is_db_available():
        db_data = load_smartest_wallets_db()
        if db_data:
            return db_data

    if os.path.exists(SMARTEST_WALLETS_FILE):
        with open(SMARTEST_WALLETS_FILE, 'r') as f:
            return json.load(f)
    return {
        "wallets": [],
        "target": SMARTEST_TARGET,
        "current_count": 0,
        "completed": False
    }


def save_smartest_wallets(data: dict):
    """Smartest wallets verilerini kaydet. DB + JSON."""
    data["updated_at"] = datetime.now().isoformat()

    if is_db_available():
        save_smartest_wallets_db(data)

    try:
        with open(SMARTEST_WALLETS_FILE, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def get_token_transfers_before_block(token_address: str, end_block: int, lookback_blocks: int = 1800) -> list:
    """
    Token için belirli blok aralığındaki transfer event'lerini al.
    1800 blok ~ 1 saat (Base'de ~2sn/blok)

    Returns:
        list: [(wallet_address, block_number), ...]
    """
    try:
        start_block = max(0, end_block - lookback_blocks)

        logs = w3.eth.get_logs({
            'fromBlock': start_block,
            'toBlock': end_block,
            'address': Web3.to_checksum_address(token_address),
            'topics': [TRANSFER_EVENT_SIGNATURE]
        })

        transfers = []
        for log in logs:
            if len(log['topics']) >= 3:
                # to_address = alıcı
                to_address = '0x' + log['topics'][2].hex()[-40:]
                block_num = log['blockNumber']
                transfers.append((to_address.lower(), block_num))

        return transfers

    except Exception as e:
        print(f"⚠️ Transfer log hatası: {e}")
        return []


def estimate_mcap_at_block(token_address: str, block_number: int, current_mcap: float) -> float:
    """
    Geçmiş bloktaki tahmini MCap.
    Basit yaklaşım: Şu anki MCap kullanılır (gerçek historical data için paid API gerekli)
    Not: Daha doğru hesaplama için block timestamp ve price history gerekir.
    """
    # Basitleştirilmiş: current_mcap kullan
    # İleride Alchemy archive node veya DEXScreener history API eklenebilir
    return current_mcap


def find_early_buyers(
    token_address: str,
    smart_money_avg_mcap: float,
    alert_block: int,
    smart_money_wallets: set
) -> list:
    """
    Smart money'den önce (veya daha düşük MCap'te) alım yapan cüzdanları bul.

    Args:
        token_address: Token contract adresi
        smart_money_avg_mcap: Smart money cüzdanlarının ortalama alım MCap'i
        alert_block: Alert tetiklendiği blok
        smart_money_wallets: Smart money cüzdan adresleri seti

    Returns:
        list: Early buyer cüzdan adresleri
    """
    # Son 1 saatteki tüm transferleri al
    transfers = get_token_transfers_before_block(token_address, alert_block)

    if not transfers:
        return []

    # %50 eşik değeri
    early_threshold = smart_money_avg_mcap * 0.5

    # Token bilgisi
    token_info = get_token_info_dexscreener(token_address)
    current_mcap = token_info.get('mcap', smart_money_avg_mcap)

    early_buyers = []
    seen_wallets = set()

    for wallet, block_num in transfers:
        if wallet in seen_wallets:
            continue
        seen_wallets.add(wallet)

        # Smart money cüzdanı değilse ve erken almışsa
        if wallet not in smart_money_wallets:
            # Blok farkına göre tahmini MCap hesapla
            # Erken blok = daha düşük MCap varsayımı
            block_diff = alert_block - block_num

            # Her 100 blokta (~3.5 dk) MCap %10 arttığını varsay (çok basit model)
            mcap_multiplier = 1 + (block_diff / 100) * 0.1
            estimated_buy_mcap = current_mcap / max(mcap_multiplier, 1)

            if estimated_buy_mcap < early_threshold:
                early_buyers.append(wallet)
                print(f"  🔍 Early buyer: {wallet[:10]}... | Est. MCap: ${estimated_buy_mcap/1e6:.3f}M")

    return early_buyers


def process_alert_for_early_detection(
    token_address: str,
    token_symbol: str,
    smart_money_purchases: list,  # [(wallet, eth_amount, mcap), ...]
    smart_money_wallets: set,
    current_block: int
):
    """
    Alert tetiklendiğinde early smart money tespiti yap.

    Args:
        token_address: Token contract adresi
        token_symbol: Token sembolü
        smart_money_purchases: Smart money alımları
        smart_money_wallets: Tüm smart money cüzdan seti
        current_block: Şu anki blok
    """
    print(f"\n🔎 Early detection başlatılıyor: {token_symbol}")

    # Smart money ortalama MCap
    mcaps = [p[2] for p in smart_money_purchases if len(p) > 2 and p[2] > 0]
    if not mcaps:
        print("  ⚠️ MCap verisi yok, early detection atlanıyor")
        return

    avg_mcap = sum(mcaps) / len(mcaps)
    print(f"  📊 Smart money avg MCap: ${avg_mcap/1e6:.3f}M")
    print(f"  🎯 Early threshold (50%): ${avg_mcap*0.5/1e6:.3f}M")

    # Early buyer'ları bul
    early_buyers = find_early_buyers(
        token_address,
        avg_mcap,
        current_block,
        smart_money_wallets
    )

    if not early_buyers:
        print("  ℹ️ Early buyer bulunamadı")
        return

    print(f"  ✅ {len(early_buyers)} early buyer bulundu!")

    # Early smart money dosyasını güncelle
    data = load_early_smart_money()

    for wallet in early_buyers:
        wallet_lower = wallet.lower()

        if wallet_lower not in data["wallets"]:
            data["wallets"][wallet_lower] = {
                "early_buys": 0,
                "tokens": [],
                "first_seen": datetime.now().isoformat(),
                "last_seen": datetime.now().isoformat()
            }

        wallet_data = data["wallets"][wallet_lower]

        # Aynı token için tekrar ekleme
        if token_symbol not in wallet_data["tokens"]:
            wallet_data["early_buys"] += 1
            wallet_data["tokens"].append(token_symbol)
            wallet_data["last_seen"] = datetime.now().isoformat()

    save_early_smart_money(data)

    # Smartest wallets kontrolü
    update_smartest_wallets(data)


def update_smartest_wallets(early_data: dict):
    """
    Early smart money verilerinden smartest wallets listesini güncelle.
    3+ tokende early olan cüzdanlar smartest kabul edilir.
    """
    smartest = load_smartest_wallets()

    # Hedef tamamlandıysa çık
    if smartest.get("completed", False):
        return

    existing_addresses = {w["address"].lower() for w in smartest["wallets"]}

    new_additions = 0
    for wallet, info in early_data["wallets"].items():
        if info["early_buys"] >= EARLY_BUY_THRESHOLD:
            if wallet.lower() not in existing_addresses:
                smartest["wallets"].append({
                    "address": wallet,
                    "early_buy_count": info["early_buys"],
                    "tokens": info["tokens"],
                    "qualified_at": datetime.now().isoformat()
                })
                existing_addresses.add(wallet.lower())
                new_additions += 1
                print(f"  🧠 Yeni smartest wallet: {wallet[:10]}... ({info['early_buys']} early buy)")

    smartest["current_count"] = len(smartest["wallets"])

    # Hedef kontrol
    if smartest["current_count"] >= SMARTEST_TARGET:
        smartest["completed"] = True
        print(f"\n🎉 SMARTEST WALLETS TAMAMLANDI! {smartest['current_count']}/{SMARTEST_TARGET}")

    if new_additions > 0:
        save_smartest_wallets(smartest)
        print(f"  📊 Smartest wallets: {smartest['current_count']}/{SMARTEST_TARGET}")


def get_smartest_wallet_addresses() -> set:
    """Smartest wallet adreslerini döndür."""
    data = load_smartest_wallets()
    return {w["address"].lower() for w in data.get("wallets", [])}


def is_smartest_wallet(address: str) -> bool:
    """Adresin smartest wallet olup olmadığını kontrol et."""
    return address.lower() in get_smartest_wallet_addresses()


# Test
if __name__ == "__main__":
    print("Early Detector Test")
    print("=" * 50)

    # Mevcut veriyi göster
    early_data = load_early_smart_money()
    print(f"Early wallets: {len(early_data['wallets'])}")

    smartest_data = load_smartest_wallets()
    print(f"Smartest wallets: {smartest_data['current_count']}/{smartest_data['target']}")
