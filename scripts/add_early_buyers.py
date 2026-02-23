"""
Early Buyers Ekleyici — Tek seferlik çalıştırma scripti.

Alchemy eth_getLogs kullanarak verilen token adreslerinin
ilk 5 dakikasında (~150 blok) alım yapan cüzdanları bulur
ve smart_money_final.json'a ekler.

Kullanım:
    python3 scripts/add_early_buyers.py
"""

import os
import sys
import json
import time
import requests
from datetime import datetime, timezone, timedelta
from web3 import Web3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import ALCHEMY_API_KEYS
from scripts.wallet_discoverer import (
    DATA_DIR,
    BLOCKED_ADDRESSES,
    _add_wallets_to_smart_list,
)

# ─── Alchemy RPC ───────────────────────────────────────────────────────────────
ALCHEMY_KEY = ALCHEMY_API_KEYS[0]  # settings.py'den aktif key
ALCHEMY_URL = f"https://base-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}"
w3 = Web3(Web3.HTTPProvider(ALCHEMY_URL))

# ERC-20 Transfer event topic
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

# Base chain: ~2 saniye/blok → 5 dakika ≈ 150 blok
FIRST_5MIN_BLOCKS = 150

# Alchemy eth_getLogs max blok aralığı (500 güvenli limit)
LOG_CHUNK_SIZE = 500

# Hedef tokenlar
TARGET_TOKENS = [
    {"symbol": "WILDE",    "address": "0xe4F5b998372443522B08b72D3B5b704c3BDAFAF4"},
    {"symbol": "ODAI",     "address": "0x5dc1DB3b262793fB00Ef0d973ed052daA5D27b07"},
    {"symbol": "clawster", "address": "0xfAC66d173BB6e1ee1C3D2321Dc13c25e36c3e525"},
    {"symbol": "daemon",   "address": "0xA87E0283B8814f37E8631BA266108351Fb4b0b07"},
    {"symbol": "KIMCHI",   "address": "0x59f2e635783d6e7a94eE8eCf3C913D481dC13166"},
]


def rpc_call(method: str, params: list) -> dict:
    """Alchemy JSON-RPC çağrısı."""
    resp = requests.post(
        ALCHEMY_URL,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        timeout=30,
    )
    return resp.json()


def get_current_block() -> int:
    result = rpc_call("eth_blockNumber", [])
    return int(result["result"], 16)


def get_asset_transfers(token_address: str, from_block: int, to_block: int) -> list:
    """
    alchemy_getAssetTransfers ile token transfer geçmişini al.
    eth_getLogs'un free tier 10-blok kısıtlaması yok.
    Sayfalama ile tüm sonuçları çeker.
    """
    all_transfers = []
    page_key = None

    while True:
        params = {
            "fromBlock": hex(from_block),
            "toBlock": hex(to_block),
            "contractAddresses": [Web3.to_checksum_address(token_address)],
            "category": ["erc20"],
            "withMetadata": False,
            "excludeZeroValue": True,
            "maxCount": "0x3e8",  # 1000 per page
            "order": "asc",
        }
        if page_key:
            params["pageKey"] = page_key

        result = rpc_call("alchemy_getAssetTransfers", [params])

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


def is_eoa(address: str) -> bool:
    """Adresin contract olmadığını kontrol et (Web3 ile)."""
    try:
        code = w3.eth.get_code(Web3.to_checksum_address(address))
        return code == b'' or code == b'\x00'
    except Exception:
        return True  # Hata durumunda geçir


def find_early_buyers_5min(token_address: str, token_symbol: str, current_block: int) -> list:
    """
    Token'ın ilk 5 dakikasındaki (~150 blok) alıcıları bul.
    Alchemy eth_getLogs kullanır.
    """
    # Tokeni bulmak için son 18000 blok (~10 saat) tara
    search_from = max(0, current_block - 18000)
    print(f"  📡 Transferler çekiliyor: blok {search_from:,} → {current_block:,}")

    transfers = get_asset_transfers(token_address, search_from, current_block)

    if not transfers:
        print(f"  ❌ Transfer bulunamadı")
        return []

    # Blok numarasına göre sırala (eskiden yeniye)
    transfers.sort(key=lambda x: int(x.get("blockNum", "0x0"), 16))

    # İlk transfer = token doğum bloğu
    creation_block = int(transfers[0].get("blockNum", "0x0"), 16)
    cutoff_block = creation_block + FIRST_5MIN_BLOCKS
    print(f"  🎂 Doğum bloğu: {creation_block:,} | 5dk sınırı: {cutoff_block:,}")

    # 5 dakika içindeki transferleri filtrele
    early_transfers = [
        t for t in transfers
        if int(t.get("blockNum", "0x0"), 16) <= cutoff_block
    ]
    print(f"  ⏱️  5dk içindeki transfer: {len(early_transfers)}")

    # Alıcı adresleri çıkar
    seen = set()
    buyers = []

    for t in early_transfers:
        from_addr = (t.get("from") or "").lower()
        to_addr   = (t.get("to")   or "").lower()

        if not to_addr or to_addr in seen:
            continue
        if to_addr == from_addr:
            continue
        if to_addr in BLOCKED_ADDRESSES:
            continue

        seen.add(to_addr)
        buyers.append({
            "wallet": to_addr,
            "block": int(t.get("blockNum", "0x0"), 16),
            "creation_block": creation_block,
        })

    print(f"  👥 Unique alıcı (filtre öncesi): {len(buyers)}")
    return buyers


def run():
    print("=" * 65)
    print("🚀 EARLY BUYER EKLEME — İlk 5 Dakika (Alchemy)")
    print(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 65)

    if not w3.is_connected():
        print("❌ Alchemy bağlantısı kurulamadı!")
        return

    current_block = get_current_block()
    print(f"📦 Güncel blok: {current_block:,}")

    # Mevcut wallet listesi
    wallets_file = os.path.join(DATA_DIR, "smart_money_final.json")
    with open(wallets_file, "r") as f:
        wallet_data = json.load(f)
    existing_wallets = set(w.lower() for w in wallet_data.get("wallets", []))
    print(f"📋 Mevcut liste: {len(existing_wallets)} cüzdan\n")

    all_candidates = []
    token_results = []

    for token in TARGET_TOKENS:
        symbol  = token["symbol"]
        address = token["address"]
        print(f"{'─'*55}")
        print(f"🔍 ${symbol} — {address[:12]}...")

        buyers = find_early_buyers_5min(address, symbol, current_block)

        if not buyers:
            token_results.append({"symbol": symbol, "buyers": 0, "organic": 0})
            continue

        organic_count = 0
        for buyer in buyers:
            wallet = buyer["wallet"]

            if wallet in existing_wallets:
                continue
            if wallet in {c["address"] for c in all_candidates}:
                continue

            # Sadece EOA kontrolü (Basescan yok, Alchemy ile)
            if not is_eoa(wallet):
                print(f"  ❌ {wallet[:12]}... contract (bot)")
                continue

            organic_count += 1
            all_candidates.append({
                "address": wallet,
                "source_token": address.lower(),
                "source_symbol": symbol,
                "first_buy_block": buyer["block"],
                "creation_block": buyer["creation_block"],
                "discovery_date": datetime.now().strftime("%Y-%m-%d"),
            })
            print(f"  ✅ {wallet[:12]}... eklendi")

        token_results.append({"symbol": symbol, "buyers": len(buyers), "organic": organic_count})

    # Listeye ekle
    print(f"\n{'='*65}")
    print(f"📊 SONUÇ: {len(all_candidates)} aday bulundu")

    added = 0
    if all_candidates:
        added = _add_wallets_to_smart_list([c["address"] for c in all_candidates])
        print(f"➕ {added} cüzdan smart_money_final.json'a eklendi")
    else:
        print("ℹ️  Eklenecek yeni cüzdan yok")

    print("\n📋 Token bazlı özet:")
    for r in token_results:
        print(f"  ${r['symbol']}: {r['buyers']} alıcı → {r['organic']} organik (EOA)")

    # Sonucu kaydet
    result = {
        "run_time": datetime.now().isoformat(),
        "initial_wallet_count": len(existing_wallets),
        "final_wallet_count": len(existing_wallets) + added,
        "added": added,
        "token_results": token_results,
        "added_wallets": all_candidates,
    }
    result_file = os.path.join(DATA_DIR, "early_buyers_result.json")
    with open(result_file, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"\n💾 Sonuçlar: {result_file}")
    print("=" * 65)
    return result


if __name__ == "__main__":
    run()
