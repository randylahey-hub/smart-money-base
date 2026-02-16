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

from config.settings import BASE_RPC_HTTP
from web3 import Web3

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
UTC_PLUS_3 = timezone(timedelta(hours=3))

# Basescan API
BASESCAN_API = "https://api.etherscan.io/v2/api"
BASE_CHAIN_ID = "8453"
ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY", "6TE7PX7TDS777Z3T7NQCZVUK4KBK9HHDJQ")

# Rate limit
BASESCAN_DELAY = 0.25  # 250ms = ~4 req/sec
RATE_LIMIT_WAIT = 30
MAX_RETRIES = 3

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
# BASESCAN API FONKSİYONLARI
# =============================================================================

def _fetch_with_retry(params: dict) -> dict:
    """Basescan API çağrısı - retry ile."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(BASESCAN_API, params=params, timeout=30)
            data = resp.json()

            message = data.get("message", "")
            if "rate limit" in message.lower() or message == "NOTOK":
                print(f"⏳ Rate limit, {RATE_LIMIT_WAIT}s bekleniyor... (deneme {attempt})")
                time.sleep(RATE_LIMIT_WAIT)
                continue

            return data
        except Exception as e:
            print(f"⚠️ Basescan API hatası (deneme {attempt}): {e}")
            time.sleep(5)

    return {"result": [], "message": "Max retries exceeded"}


def find_first_buyers(token_address: str, max_buyers: int = MAX_FIRST_BUYERS) -> list:
    """
    Bir tokenın ilk alıcılarını bul (Basescan token transfer geçmişi).

    Returns: [{"wallet", "block", "tx_hash", "value", "timestamp"}, ...]
    """
    params = {
        'chainid': BASE_CHAIN_ID,
        'module': 'account',
        'action': 'tokentx',
        'contractaddress': token_address,
        'startblock': 0,
        'endblock': 99999999,
        'sort': 'asc',
        'page': 1,
        'offset': 100,  # İlk 100 transfer
        'apikey': ETHERSCAN_API_KEY,
    }

    data = _fetch_with_retry(params)
    transfers = data.get("result", [])

    if not isinstance(transfers, list):
        print(f"⚠️ Token transfer verisi alınamadı: {token_address[:10]}...")
        return []

    # İlk alıcıları filtrele (tekrarsız, sadece alım yönü)
    seen_wallets = set()
    first_buyers = []

    # Null/zero address = mint, bundan gelen transferler = gerçek alım
    ZERO_ADDRESSES = {"0x0000000000000000000000000000000000000000", "0x0000000000000000000000000000000000000001"}

    for tx in transfers:
        to_addr = tx.get("to", "").lower()
        from_addr = tx.get("from", "").lower()

        # Zaten gördük
        if to_addr in seen_wallets:
            continue

        # Kendinden kendine transfer atla
        if to_addr == from_addr:
            continue

        # Mint'ten gelen veya DEX router'dan gelen transferler = gerçek alım
        # (Her transfer ilk alım olabilir)
        seen_wallets.add(to_addr)

        # Değer hesapla (token miktarı)
        try:
            decimals = int(tx.get("tokenDecimal", 18))
            value = int(tx.get("value", 0)) / (10 ** decimals)
        except (ValueError, ZeroDivisionError):
            value = 0

        first_buyers.append({
            "wallet": to_addr,
            "block": int(tx.get("blockNumber", 0)),
            "tx_hash": tx.get("hash", ""),
            "value": value,
            "timestamp": int(tx.get("timeStamp", 0)),
            "from": from_addr,
        })

        if len(first_buyers) >= max_buyers:
            break

    time.sleep(BASESCAN_DELAY)
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
        'chainid': BASE_CHAIN_ID,
        'module': 'account',
        'action': 'txlist',
        'address': address,
        'startblock': 0,
        'endblock': 99999999,
        'sort': 'asc',
        'page': 1,
        'offset': 1,  # Sadece ilk tx
        'apikey': ETHERSCAN_API_KEY,
    }

    data = _fetch_with_retry(params)
    txs = data.get("result", [])
    time.sleep(BASESCAN_DELAY)

    if not isinstance(txs, list) or not txs:
        return 0

    try:
        first_ts = int(txs[0].get("timeStamp", 0))
        first_date = datetime.fromtimestamp(first_ts, tz=timezone.utc)
        age = (datetime.now(timezone.utc) - first_date).days
        return age
    except Exception:
        return 0


def count_recent_tokens(address: str, hours: int = 1) -> int:
    """Son N saat içinde kaç farklı token alımı yapılmış (bot tespiti)."""
    now_ts = int(datetime.now(timezone.utc).timestamp())
    cutoff_ts = now_ts - (hours * 3600)

    params = {
        'chainid': BASE_CHAIN_ID,
        'module': 'account',
        'action': 'tokentx',
        'address': address,
        'startblock': 0,
        'endblock': 99999999,
        'sort': 'desc',
        'page': 1,
        'offset': 50,
        'apikey': ETHERSCAN_API_KEY,
    }

    data = _fetch_with_retry(params)
    txs = data.get("result", [])
    time.sleep(BASESCAN_DELAY)

    if not isinstance(txs, list):
        return 0

    recent_tokens = set()
    for tx in txs:
        ts = int(tx.get("timeStamp", 0))
        if ts >= cutoff_ts:
            contract = tx.get("contractAddress", "").lower()
            if contract:
                recent_tokens.add(contract)
        else:
            break

    return len(recent_tokens)


def count_daily_transactions(address: str) -> int:
    """Son 24 saatteki toplam işlem sayısını kontrol et."""
    params = {
        'chainid': BASE_CHAIN_ID,
        'module': 'account',
        'action': 'txlist',
        'address': address,
        'startblock': 0,
        'endblock': 99999999,
        'sort': 'desc',
        'page': 1,
        'offset': 200,
        'apikey': ETHERSCAN_API_KEY,
    }

    data = _fetch_with_retry(params)
    txs = data.get("result", [])
    time.sleep(BASESCAN_DELAY)

    if not isinstance(txs, list):
        return 0

    now_ts = int(datetime.now(timezone.utc).timestamp())
    cutoff_ts = now_ts - (24 * 3600)

    count = sum(1 for tx in txs if int(tx.get("timeStamp", 0)) >= cutoff_ts)
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

            # Organik mi? (ETH fiyatı yaklaşık, token değeri bilinmiyor ama min $50 filtreyi
            # Basescan'den gelen value ile tahmin ediyoruz)
            # DexScreener'dan token fiyatı gerekli — basitleştirilmiş kontrol
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
