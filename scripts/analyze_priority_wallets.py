"""
Öncelikli Cüzdan ETH P&L Analizi
Sadece 5+ ve 4 token cüzdanları analiz eder (31 cüzdan)
"""

import requests
import json
import time
import sys
from datetime import datetime

sys.stdout.reconfigure(line_buffering=True)

# API ayarları
BASESCAN_API = "https://api.etherscan.io/v2/api"
ETHERSCAN_API_KEY = "6TE7PX7TDS777Z3T7NQCZVUK4KBK9HHDJQ"
BASE_CHAIN_ID = "8453"
WETH_ADDRESS = "0x4200000000000000000000000000000000000006".lower()

def fetch_api(action, address):
    """API çağrısı"""
    params = {
        'chainid': BASE_CHAIN_ID,
        'module': 'account',
        'action': action,
        'address': address,
        'startblock': 0,
        'endblock': 99999999,
        'sort': 'desc',
        'apikey': ETHERSCAN_API_KEY
    }

    for _ in range(3):
        try:
            r = requests.get(BASESCAN_API, params=params, timeout=60)
            if r.status_code == 200:
                data = r.json()
                if str(data.get('status')) == '1':
                    return data.get('result', [])
                if 'NOTOK' in str(data.get('message', '')):
                    time.sleep(5)
                    continue
            time.sleep(2)
        except:
            time.sleep(2)
    return []

def analyze_wallet(address):
    """Cüzdan P&L hesapla"""
    address = address.lower()

    # Normal TX
    normal = fetch_api('txlist', address)
    time.sleep(1)

    # Internal TX
    internal = fetch_api('txlistinternal', address)
    time.sleep(1)

    # Token TX
    tokens = fetch_api('tokentx', address)
    time.sleep(1)

    # Hesapla
    eth_in = eth_out = gas = 0
    for tx in normal:
        try:
            v = int(tx.get('value', 0)) / 1e18
            g = (int(tx.get('gasUsed', 0)) * int(tx.get('gasPrice', 0))) / 1e18
            if tx.get('from', '').lower() == address:
                eth_out += v
                gas += g
            elif tx.get('to', '').lower() == address:
                eth_in += v
        except: pass

    int_in = int_out = 0
    for tx in internal:
        try:
            v = int(tx.get('value', 0)) / 1e18
            if tx.get('to', '').lower() == address:
                int_in += v
            elif tx.get('from', '').lower() == address:
                int_out += v
        except: pass

    weth_in = weth_out = 0
    for tx in tokens:
        if tx.get('contractAddress', '').lower() != WETH_ADDRESS:
            continue
        try:
            v = int(tx.get('value', 0)) / 1e18
            if tx.get('to', '').lower() == address:
                weth_in += v
            elif tx.get('from', '').lower() == address:
                weth_out += v
        except: pass

    total_in = eth_in + int_in + weth_in
    total_out = eth_out + int_out + weth_out + gas
    net = total_in - total_out

    return {
        'total_in': total_in,
        'total_out': total_out,
        'net_pnl': net,
        'is_profitable': net > 0
    }

# Ana
print("=" * 60)
print("ÖNCELİKLİ CÜZDAN ANALİZİ (5+4 token)")
print("=" * 60)

with open('data/wallets_priority.json') as f:
    data = json.load(f)

wallets = data['wallets']
print(f"📋 {len(wallets)} cüzdan analiz edilecek\n")

results = []
for i, w in enumerate(wallets, 1):
    addr = w['address']
    tc = w['token_count']

    print(f"[{i}/{len(wallets)}] {addr[:16]}... ({tc} token)")

    pnl = analyze_wallet(addr)

    status = "✅" if pnl['is_profitable'] else "❌"
    print(f"  In: {pnl['total_in']:.2f} | Out: {pnl['total_out']:.2f} | Net: {pnl['net_pnl']:+.2f} {status}")

    results.append({
        'address': addr,
        'token_count': tc,
        'tokens': w.get('tokens', []),
        **pnl
    })

# Sonuçlar
print("\n" + "=" * 60)
print("SONUÇLAR")
print("=" * 60)

profitable = [r for r in results if r['is_profitable']]
profitable.sort(key=lambda x: -x['net_pnl'])

print(f"\nToplam: {len(results)}")
print(f"✅ Karlı: {len(profitable)}")
print(f"❌ Zararlı: {len(results) - len(profitable)}")

print("\n🏆 KARLI CÜZDANLAR:")
print("-" * 70)
for i, r in enumerate(profitable, 1):
    print(f"{i:2}. {r['address'][:20]}... | {r['token_count']}T | {r['net_pnl']:+.2f} ETH")

# Kaydet
with open('data/wallets_priority_pnl.json', 'w') as f:
    json.dump({
        'timestamp': datetime.now().isoformat(),
        'total': len(results),
        'profitable_count': len(profitable),
        'profitable': profitable,
        'all': results
    }, f, indent=2)

print(f"\n💾 Kaydedildi: data/wallets_priority_pnl.json")
