"""
Wallet Discoverer - Yeni kaliteli cüzdan keşfi.

FAZ 8: contracts_check tokenlarının ilk alıcılarını bulur.
Filtreler: min $50 alım, EOA, organik, hesap yaşı > 100 gün, haftalık limit 80.
"""

import json
import os
import sys
import time
import requests
from datetime import datetime, timezone, timedelta
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import BASE_RPC_HTTP, ALCHEMY_API_KEYS
from web3 import Web3

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
UTC_PLUS_3 = timezone(timedelta(hours=3))

# Alchemy API
ALCHEMY_URL = f"https://base-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEYS[0]}"

# Filtreler
MIN_BUY_VALUE_USD = 50       # Min $50 alım
ACCOUNT_MIN_AGE_DAYS = 100   # Hesap yaşı > 100 gün
WEEKLY_TOKEN_LIMIT = 80      # Haftalık max farklı token
BOT_RAPID_BUY_THRESHOLD = 5  # 1 saatte 5+ farklı token = bot
MAX_FIRST_BUYERS = 30        # Her token için max ilk alıcı kontrolü
NEW_WALLET_WEEKLY_LIMIT = 80  # Haftada max yeni cüzdan ekleme
MAX_DAILY_TX_COUNT = 200     # Günde 200+ tx = bot

# Bilinen adresler blocklist (burn, dead, null, router, protokol)
BLOCKED_ADDRESSES = {
    "0x0000000000000000000000000000000000000000",  # Null
    "0x0000000000000000000000000000000000000001",  # Null
    "0x000000000000000000000000000000000000dead",  # Burn
    "0x0000000000000000000000000000000000000dead",  # Burn variant
    "0x2626664c2603336e57b271c5c0b26f421741e481",  # Uniswap V2 Router
    "0xcf77a3ba9a5ca399b7c97c74d54e5b1beb874e43",  # Aerodrome Router
    "0x6131b5fae19ea4f9d964eac0408e4408b66337b5",  # BaseSwap Router
    "0x3fc91a3afd70395cd496c647d5a6cc9d4b2b7fad",  # Uniswap Universal Router
    "0x4752ba5dbc23f44d87826276bf6fd6b1c372ad24",  # Uniswap V2 Router (Base)
    "0x327df1e6de05895d2ab08513aadd9313fe505d86",  # BaseSwap V2
    "0x1111111254eeb25477b68fb85ed929f73a960582",  # 1inch Router
    "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",  # USDC
    "0x4200000000000000000000000000000000000006",  # WETH
    "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf",  # cbBTC
}

# Web3 bağlantısı
w3 = Web3(Web3.HTTPProvider(BASE_RPC_HTTP))


# =============================================================================
# ALCHEMY API FONKSİYONLARI
# =============================================================================

def _alchemy_call(method: str, params: list) -> dict:
    """Alchemy JSON-RPC çağrısı."""
    resp = requests.post(
        ALCHEMY_URL,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        timeout=30,
    )
    return resp.json()


def _get_asset_transfers(params: dict) -> list:
    """
    alchemy_getAssetTransfers ile sayfalama yaparak tüm transferleri çek.
    """
    all_transfers = []
    page_key = None

    while True:
        if page_key:
            params["pageKey"] = page_key

        result = _alchemy_call("alchemy_getAssetTransfers", [params])

        if "error" in result:
            print(f"    ⚠️ alchemy_getAssetTransfers hatası: {result['error']}")
            break

        data = result.get("result", {})
        transfers = data.get("transfers", [])
        all_transfers.extend(transfers)

        page_key = data.get("pageKey")
        if not page_key or not transfers:
            break

        time.sleep(0.1)

    return all_transfers


def find_first_buyers(token_address: str, max_buyers: int = MAX_FIRST_BUYERS) -> list:
    """
    Bir tokenın ilk alıcılarını bul (Alchemy ERC-20 transfer geçmişi).

    Returns: [{"wallet", "block", "tx_hash", "value", "timestamp"}, ...]
    """
    params = {
        "contractAddresses": [Web3.to_checksum_address(token_address)],
        "category": ["erc20"],
        "order": "asc",
        "maxCount": hex(max_buyers * 2),  # İlk alıcıları bulmak için biraz fazla çek
        "withMetadata": True,
        "excludeZeroValue": True,
    }

    transfers = _get_asset_transfers(params)

    if not transfers:
        print(f"⚠️ Token transfer verisi alınamadı: {token_address[:10]}...")
        return []

    # İlk alıcıları filtrele
    seen_wallets = set()
    first_buyers = []

    for tx in transfers:
        to_addr = (tx.get("to") or "").lower()
        from_addr = (tx.get("from") or "").lower()

        if not to_addr or to_addr in seen_wallets:
            continue
        if to_addr == from_addr:
            continue

        seen_wallets.add(to_addr)

        # Block numarası
        block_num = int(tx.get("blockNum", "0x0"), 16)

        # Timestamp
        ts = 0
        meta = tx.get("metadata") or {}
        block_ts = meta.get("blockTimestamp", "")
        if block_ts:
            try:
                dt = datetime.fromisoformat(block_ts.replace("Z", "+00:00"))
                ts = int(dt.timestamp())
            except Exception:
                pass

        first_buyers.append({
            "wallet": to_addr,
            "block": block_num,
            "tx_hash": tx.get("hash", ""),
            "value": float(tx.get("value") or 0),
            "timestamp": ts,
            "from": from_addr,
        })

        if len(first_buyers) >= max_buyers:
            break

    return first_buyers


def is_eoa(address: str) -> bool:
    """Adresin EOA (externally owned account) olup olmadığını kontrol et."""
    try:
        code = w3.eth.get_code(Web3.to_checksum_address(address))
        return code == b'' or code == b'\x00'
    except Exception:
        return True  # Hata durumunda geçir


def get_account_age_days(address: str) -> int:
    """Hesabın ilk işlem tarihinden bugüne kaç gün geçtiğini bul."""
    params = {
        "fromAddress": Web3.to_checksum_address(address),
        "category": ["external", "erc20"],
        "order": "asc",
        "maxCount": "0x1",
        "withMetadata": True,
        "excludeZeroValue": False,
    }

    transfers = _get_asset_transfers(params)

    if not transfers:
        # Hiç giden TX yoksa, gelen TX'e bak (yeni hesap olabilir)
        params2 = {
            "toAddress": Web3.to_checksum_address(address),
            "category": ["external", "erc20"],
            "order": "asc",
            "maxCount": "0x1",
            "withMetadata": True,
            "excludeZeroValue": False,
        }
        transfers = _get_asset_transfers(params2)

    if not transfers:
        return 0

    try:
        meta = transfers[0].get("metadata") or {}
        block_ts = meta.get("blockTimestamp", "")
        if not block_ts:
            return 0
        dt = datetime.fromisoformat(block_ts.replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - dt).days
        return max(0, age)
    except Exception:
        return 0


def count_recent_tokens(address: str, hours: int = 1) -> int:
    """Son N saat içinde kaç farklı token alımı yapılmış (bot tespiti)."""
    params = {
        "toAddress": Web3.to_checksum_address(address),
        "category": ["erc20"],
        "order": "desc",
        "maxCount": "0x32",  # 50
        "withMetadata": True,
        "excludeZeroValue": True,
    }

    transfers = _get_asset_transfers(params)

    if not transfers:
        return 0

    cutoff_dt = datetime.now(timezone.utc) - timedelta(hours=hours)
    recent_tokens = set()

    for tx in transfers:
        meta = tx.get("metadata") or {}
        block_ts = meta.get("blockTimestamp", "")
        if not block_ts:
            continue
        try:
            dt = datetime.fromisoformat(block_ts.replace("Z", "+00:00"))
            if dt >= cutoff_dt:
                # rawContract.address veya asset bilgisi
                raw = tx.get("rawContract") or {}
                contract = (raw.get("address") or "").lower()
                if contract:
                    recent_tokens.add(contract)
            else:
                # Azalan sırayla geldiği için eski TX'e ulaştık
                break
        except Exception:
            continue

    return len(recent_tokens)


def count_daily_transactions(address: str) -> int:
    """Son 24 saatteki toplam işlem sayısını kontrol et."""
    params = {
        "fromAddress": Web3.to_checksum_address(address),
        "category": ["external", "erc20"],
        "order": "desc",
        "maxCount": "0xc8",  # 200
        "withMetadata": True,
        "excludeZeroValue": False,
    }

    transfers = _get_asset_transfers(params)

    if not transfers:
        return 0

    cutoff_dt = datetime.now(timezone.utc) - timedelta(hours=24)
    count = 0

    for tx in transfers:
        meta = tx.get("metadata") or {}
        block_ts = meta.get("blockTimestamp", "")
        if not block_ts:
            continue
        try:
            dt = datetime.fromisoformat(block_ts.replace("Z", "+00:00"))
            if dt >= cutoff_dt:
                count += 1
            else:
                break
        except Exception:
            continue

    return count


def is_organic_buyer(address: str, buy_value_usd: float = 0) -> dict:
    """
    Organik alıcı kontrolü.

    Returns:
        {"organic": bool, "reject_reason": str or None, "details": dict}
    """
    details = {}

    # 0. Blocklist kontrolü
    if address.lower() in BLOCKED_ADDRESSES:
        return {"organic": False, "reject_reason": "Blocklist (burn/null/router)", "details": details}

    # 1. Min alım değeri (0 = bilinmiyor, atla)
    if buy_value_usd > 0 and buy_value_usd < MIN_BUY_VALUE_USD:
        return {"organic": False, "reject_reason": f"Düşük alım: ${buy_value_usd:.2f} < ${MIN_BUY_VALUE_USD}", "details": details}

    # 2. EOA kontrolü
    if not is_eoa(address):
        return {"organic": False, "reject_reason": "Contract adresi (bot)", "details": details}

    # 3. Hesap yaşı
    age = get_account_age_days(address)
    details["account_age_days"] = age
    if age < ACCOUNT_MIN_AGE_DAYS:
        return {"organic": False, "reject_reason": f"Genç hesap: {age} gün < {ACCOUNT_MIN_AGE_DAYS}", "details": details}

    # 4. Son 1 saatteki token çeşitliliği (bot tespiti)
    recent = count_recent_tokens(address, hours=1)
    details["recent_tokens_1h"] = recent
    if recent >= BOT_RAPID_BUY_THRESHOLD:
        return {"organic": False, "reject_reason": f"Bot pattern: 1 saatte {recent} farklı token", "details": details}

    # 5. Günlük işlem sayısı (aşırı aktif = bot)
    daily_tx = count_daily_transactions(address)
    details["daily_tx_count"] = daily_tx
    if daily_tx >= MAX_DAILY_TX_COUNT:
        return {"organic": False, "reject_reason": f"Aşırı aktif: {daily_tx} tx/gün >= {MAX_DAILY_TX_COUNT}", "details": details}

    return {"organic": True, "reject_reason": None, "details": details}


# =============================================================================
# ANA KEŞİF FONKSİYONU
# =============================================================================

def discover_new_wallets(contracts_check_tokens: list = None) -> dict:
    """
    Başarılı tokenların ilk alıcılarından yeni cüzdanlar keşfet.
    Kaynak: contracts_check + short_list token'ları (her ikisi de başarılı sinyaller).

    Args:
        contracts_check_tokens: [{"token_address": "0x...", "token_symbol": "..."}, ...]
                                 None ise data/ dosyalarından yükler.

    Returns:
        {"discovered": int, "added": int, "rejected": int, "wallets": [...]}
    """
    print("=" * 60)
    print("🔍 YENİ CÜZDAN KEŞFİ")
    print("=" * 60)

    # Başarılı token listelerini yükle (contracts_check + short_list)
    if contracts_check_tokens is None:
        contracts_check_tokens = []

        # 1. contracts_check (en iyi tokenlar: +%50 in 30dk)
        cc_file = os.path.join(DATA_DIR, "contracts_check.json")
        if os.path.exists(cc_file):
            with open(cc_file, 'r') as f:
                contracts_check_tokens.extend(json.load(f))

        # 2. short_list (iyi tokenlar: +%20 in 5dk)
        sl_file = os.path.join(DATA_DIR, "short_list_tokens.json")
        if os.path.exists(sl_file):
            with open(sl_file, 'r') as f:
                contracts_check_tokens.extend(json.load(f))

    if not contracts_check_tokens:
        print("❌ Başarılı token listesi boş")
        return {"discovered": 0, "added": 0, "rejected": 0, "wallets": []}

    # Mevcut smart money listesini yükle (duplike kontrolü)
    wallets_file = os.path.join(DATA_DIR, "smart_money_final.json")
    with open(wallets_file, 'r') as f:
        wallet_data = json.load(f)
    existing_wallets = set(w.lower() for w in wallet_data.get("wallets", []))

    # Duplicate tokenları çıkar (unique token bazında işle)
    seen_tokens = set()
    unique_tokens = []
    for token in contracts_check_tokens:
        addr = token["token_address"].lower()
        if addr not in seen_tokens:
            seen_tokens.add(addr)
            unique_tokens.append(token)

    print(f"📋 {len(contracts_check_tokens)} entry → {len(unique_tokens)} unique token")

    # Her token için ilk alıcıları bul
    all_candidates = []
    rejected = []

    for i, token in enumerate(unique_tokens):
        token_addr = token["token_address"]
        token_symbol = token.get("token_symbol", "UNKNOWN")

        print(f"\n[{i+1}/{len(unique_tokens)}] {token_symbol} ({token_addr[:10]}...)")

        # İlk alıcıları bul
        first_buyers = find_first_buyers(token_addr)
        print(f"  İlk alıcı sayısı: {len(first_buyers)}")

        for buyer in first_buyers:
            wallet = buyer["wallet"]

            # Zaten listede mi?
            if wallet in existing_wallets:
                continue

            # Zaten aday mı?
            if wallet in {c["address"] for c in all_candidates}:
                continue

            # Organik mi?
            check = is_organic_buyer(wallet)

            if check["organic"]:
                all_candidates.append({
                    "address": wallet,
                    "source_token": token_addr,
                    "source_symbol": token_symbol,
                    "first_buy_block": buyer["block"],
                    "account_age_days": check["details"].get("account_age_days", 0),
                    "discovery_date": datetime.now(UTC_PLUS_3).strftime("%Y-%m-%d"),
                })
                print(f"  ✅ {wallet[:10]}... → organik (yaş: {check['details'].get('account_age_days', '?')} gün)")
            else:
                rejected.append({
                    "address": wallet,
                    "reason": check["reject_reason"],
                    "source_token": token_symbol,
                })
                # Her 5 reject'te bir göster
                if len(rejected) % 5 == 0:
                    print(f"  ❌ {len(rejected)} aday reddedildi")

            # Haftalık limit kontrolü
            if len(all_candidates) >= NEW_WALLET_WEEKLY_LIMIT:
                print(f"⚠️ Haftalık limit ({NEW_WALLET_WEEKLY_LIMIT}) doldu")
                break

        if len(all_candidates) >= NEW_WALLET_WEEKLY_LIMIT:
            break

    # Cüzdanları ekle
    added_count = 0
    if all_candidates:
        added_count = _add_wallets_to_smart_list(
            [c["address"] for c in all_candidates]
        )

    # Sonuçları kaydet
    result = {
        "discovery_time": datetime.now(UTC_PLUS_3).isoformat(),
        "tokens_checked": len(unique_tokens),
        "discovered": len(all_candidates),
        "added": added_count,
        "rejected": len(rejected),
        "wallets": all_candidates,
        "rejected_details": rejected[:50],  # İlk 50 red
    }

    _save_json("discovered_wallets.json", result)

    print(f"\n📊 Keşif Sonuçları:")
    print(f"  Token tarandı: {len(unique_tokens)}")
    print(f"  Aday bulundu: {len(all_candidates)}")
    print(f"  Eklendi: {added_count}")
    print(f"  Reddedildi: {len(rejected)}")
    print("=" * 60)

    return result


def _add_wallets_to_smart_list(new_wallets: list) -> int:
    """Yeni cüzdanları smart_money_final.json'a ekle."""
    wallets_file = os.path.join(DATA_DIR, "smart_money_final.json")

    with open(wallets_file, 'r') as f:
        data = json.load(f)

    # Backup
    backup_name = f"smart_money_final_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    backup_path = os.path.join(DATA_DIR, backup_name)
    with open(backup_path, 'w') as f:
        json.dump(data, f, indent=2)

    existing = set(w.lower() for w in data.get("wallets", []))
    added = 0

    for wallet in new_wallets:
        wallet_lower = wallet.lower()
        if wallet_lower not in existing:
            data["wallets"].append(wallet_lower)
            existing.add(wallet_lower)
            added += 1

    data["count"] = len(data["wallets"])

    with open(wallets_file, 'w') as f:
        json.dump(data, f, indent=2)

    print(f"✅ {added} yeni cüzdan eklendi (toplam: {data['count']})")
    return added


def _save_json(filename: str, data):
    """Data dizinine JSON kaydet."""
    filepath = os.path.join(DATA_DIR, filename)
    with open(filepath, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    print(f"💾 Kaydedildi: {filename}")


if __name__ == "__main__":
    result = discover_new_wallets()
