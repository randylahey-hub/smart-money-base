"""
ETH Kar/Zarar Analiz Scripti
384 filtrelenmiş cüzdanın gerçek ETH bazlı P&L'ini hesaplar.

Kriterler:
- Net P&L > 0 olanları karlı say
- Toplam işlem < 200 (bot filtresi zaten uygulanmış)
"""

import requests
import json
import time
import os
import sys
from datetime import datetime
from collections import defaultdict

# Flush output immediately
sys.stdout.reconfigure(line_buffering=True)

# ============================================================
# AYARLAR
# ============================================================

BASESCAN_API = "https://api.etherscan.io/v2/api"
ETHERSCAN_API_KEY = "6TE7PX7TDS777Z3T7NQCZVUK4KBK9HHDJQ"
BASE_CHAIN_ID = "8453"

REQUEST_TIMEOUT = 120
REQUEST_DELAY = 1  # Daha hızlı - 1 saniye
RATE_LIMIT_WAIT = 30
MAX_RETRIES = 3

# WETH adresi
WETH_ADDRESS = "0x4200000000000000000000000000000000000006".lower()

# Dosya yolları
DATA_DIR = '/Users/emrecapin/Desktop/smart-money-base/data'
INPUT_FILE = f'{DATA_DIR}/wallets_filtered_no_bots.json'
OUTPUT_FILE = f'{DATA_DIR}/wallets_eth_pnl.json'
CHECKPOINT_FILE = f'{DATA_DIR}/eth_pnl_checkpoint.json'

# ============================================================
# API FONKSİYONLARI
# ============================================================

def fetch_with_retry(url, params):
    """Rate limit ve retry ile API çağrısı"""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)

            if response.status_code == 200:
                data = response.json()
                status = data.get('status')
                message = data.get('message', '')

                if str(status) == '1':
                    result = data.get('result', [])
                    return result if isinstance(result, list) else []

                if 'rate limit' in message.lower() or message == 'NOTOK':
                    time.sleep(RATE_LIMIT_WAIT)
                    continue

                if 'no transactions' in message.lower():
                    return []

                time.sleep(REQUEST_DELAY)
                continue

        except Exception as e:
            time.sleep(REQUEST_DELAY * attempt)

    return []

def fetch_normal_tx(address):
    """Normal ETH işlemlerini çek"""
    params = {
        'chainid': BASE_CHAIN_ID,
        'module': 'account',
        'action': 'txlist',
        'address': address,
        'startblock': 0,
        'endblock': 99999999,
        'sort': 'desc',
        'apikey': ETHERSCAN_API_KEY
    }
    return fetch_with_retry(BASESCAN_API, params)

def fetch_internal_tx(address):
    """Internal ETH işlemlerini çek"""
    params = {
        'chainid': BASE_CHAIN_ID,
        'module': 'account',
        'action': 'txlistinternal',
        'address': address,
        'startblock': 0,
        'endblock': 99999999,
        'sort': 'desc',
        'apikey': ETHERSCAN_API_KEY
    }
    return fetch_with_retry(BASESCAN_API, params)

def fetch_token_tx(address):
    """Token transferlerini çek"""
    params = {
        'chainid': BASE_CHAIN_ID,
        'module': 'account',
        'action': 'tokentx',
        'address': address,
        'startblock': 0,
        'endblock': 99999999,
        'sort': 'desc',
        'apikey': ETHERSCAN_API_KEY
    }
    return fetch_with_retry(BASESCAN_API, params)

# ============================================================
# ANALİZ FONKSİYONLARI
# ============================================================

def analyze_wallet_pnl(address):
    """Cüzdanın ETH bazlı kar/zarar hesabı"""

    address = address.lower()

    # API çağrıları
    normal_txs = fetch_normal_tx(address)
    time.sleep(REQUEST_DELAY)

    internal_txs = fetch_internal_tx(address)
    time.sleep(REQUEST_DELAY)

    token_txs = fetch_token_tx(address)
    time.sleep(REQUEST_DELAY)

    # ETH hesabı
    eth_received = 0
    eth_sent = 0
    gas_spent = 0

    for tx in normal_txs:
        try:
            value = int(tx.get('value', 0)) / 1e18
            gas_used = int(tx.get('gasUsed', 0))
            gas_price = int(tx.get('gasPrice', 0))
            gas_cost = (gas_used * gas_price) / 1e18

            if tx.get('from', '').lower() == address:
                eth_sent += value
                gas_spent += gas_cost
            elif tx.get('to', '').lower() == address:
                eth_received += value
        except:
            continue

    # Internal ETH
    internal_in = 0
    internal_out = 0

    for tx in internal_txs:
        try:
            value = int(tx.get('value', 0)) / 1e18
            if tx.get('to', '').lower() == address:
                internal_in += value
            elif tx.get('from', '').lower() == address:
                internal_out += value
        except:
            continue

    # WETH hesabı
    weth_in = 0
    weth_out = 0

    for tx in token_txs:
        if tx.get('contractAddress', '').lower() != WETH_ADDRESS:
            continue

        try:
            decimals = int(tx.get('tokenDecimal', 18))
            value = int(tx.get('value', 0)) / (10 ** decimals)

            if tx.get('to', '').lower() == address:
                weth_in += value
            elif tx.get('from', '').lower() == address:
                weth_out += value
        except:
            continue

    # Toplam hesap
    total_in = eth_received + internal_in + weth_in
    total_out = eth_sent + internal_out + weth_out + gas_spent
    net_pnl = total_in - total_out

    return {
        'eth_received': eth_received,
        'eth_sent': eth_sent,
        'internal_in': internal_in,
        'internal_out': internal_out,
        'weth_in': weth_in,
        'weth_out': weth_out,
        'gas_spent': gas_spent,
        'total_in': total_in,
        'total_out': total_out,
        'net_pnl': net_pnl,
        'is_profitable': net_pnl > 0,
        'normal_tx_count': len(normal_txs),
        'internal_tx_count': len(internal_txs),
        'token_tx_count': len(token_txs)
    }

def load_checkpoint():
    """Checkpoint varsa yükle"""
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE) as f:
            return json.load(f)
    return {'processed': [], 'results': []}

def save_checkpoint(processed, results):
    """Checkpoint kaydet"""
    with open(CHECKPOINT_FILE, 'w') as f:
        json.dump({
            'timestamp': datetime.now().isoformat(),
            'processed': processed,
            'results': results
        }, f)

# ============================================================
# ANA FONKSİYON
# ============================================================

def main():
    print("=" * 70)
    print("ETH KAR/ZARAR ANALİZİ")
    print("=" * 70)
    print(f"Başlangıç: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    # Cüzdanları yükle
    with open(INPUT_FILE) as f:
        data = json.load(f)

    wallets = data.get('wallets', [])
    print(f"📋 {len(wallets)} cüzdan yüklendi")

    # Checkpoint kontrol
    checkpoint = load_checkpoint()
    processed_addresses = set(checkpoint.get('processed', []))
    results = checkpoint.get('results', [])

    if processed_addresses:
        print(f"📌 Checkpoint bulundu: {len(processed_addresses)} cüzdan zaten işlenmiş")

    # Her cüzdan için analiz
    for i, wallet in enumerate(wallets, 1):
        address = wallet['address']

        # Zaten işlendiyse atla
        if address in processed_addresses:
            continue

        token_count = wallet.get('token_count', 0)
        tokens = wallet.get('tokens', [])

        print(f"\n[{i}/{len(wallets)}] {address[:16]}... ({token_count} token)")

        # P&L analizi
        pnl = analyze_wallet_pnl(address)

        # Sonucu kaydet
        result = {
            'address': address,
            'token_count': token_count,
            'tokens': tokens,
            'total_trades': wallet.get('total_trades', 0),
            **pnl
        }

        results.append(result)
        processed_addresses.add(address)

        # Durum göster
        status = "✅ KAR" if pnl['is_profitable'] else "❌ ZARAR"
        print(f"  Giren: {pnl['total_in']:.4f} ETH | Çıkan: {pnl['total_out']:.4f} ETH")
        print(f"  Net P&L: {pnl['net_pnl']:+.4f} ETH {status}")

        # Her 20 cüzdanda checkpoint
        if len(results) % 20 == 0:
            save_checkpoint(list(processed_addresses), results)
            print(f"\n  💾 Checkpoint kaydedildi ({len(results)} cüzdan)")

    # Final kaydet
    print("\n" + "=" * 70)
    print("SONUÇLAR")
    print("=" * 70)

    profitable = [r for r in results if r['is_profitable']]
    unprofitable = [r for r in results if not r['is_profitable']]

    print(f"\nToplam analiz: {len(results)} cüzdan")
    print(f"✅ Karlı: {len(profitable)} ({len(profitable)/len(results)*100:.1f}%)")
    print(f"❌ Zararlı: {len(unprofitable)} ({len(unprofitable)/len(results)*100:.1f}%)")

    # Karlı cüzdanları P&L'e göre sırala
    profitable.sort(key=lambda x: -x['net_pnl'])

    # Kaydet
    output = {
        'timestamp': datetime.now().isoformat(),
        'total_analyzed': len(results),
        'profitable_count': len(profitable),
        'unprofitable_count': len(unprofitable),
        'profitable_wallets': profitable,
        'all_results': results
    }

    with open(OUTPUT_FILE, 'w') as f:
        json.dump(output, f, indent=2)

    print(f"\n💾 Kaydedildi: {OUTPUT_FILE}")

    # Top 20 karlı cüzdan
    if profitable:
        print("\n" + "=" * 70)
        print("TOP 20 KARLI CÜZDAN")
        print("=" * 70)
        print(f"\n{'#':>3} | {'Adres':^18} | {'Token':>5} | {'Net P&L':>12} | {'Giren':>10} | {'Çıkan':>10}")
        print("-" * 75)

        for i, w in enumerate(profitable[:20], 1):
            addr = w['address'][:14] + "..."
            print(f"{i:>3} | {addr:^18} | {w['token_count']:>5} | {w['net_pnl']:>+12.4f} | {w['total_in']:>10.4f} | {w['total_out']:>10.4f}")

    # Checkpoint temizle
    if os.path.exists(CHECKPOINT_FILE):
        os.remove(CHECKPOINT_FILE)

    print("\n" + "=" * 70)
    print(f"BİTİŞ: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    return profitable

if __name__ == "__main__":
    result = main()
