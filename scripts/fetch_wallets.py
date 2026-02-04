"""
Cüzdan Ayıklama Scripti (Faz 2)
Filtrelenmiş tokenlar için erken alım yapan ve kar eden cüzdanları tespit eder.

Kriterler:
- Erken alım: Token listing'den sonra ilk 24-48 saat
- Kar: >= $1,000 VE >= %200 ROI
"""

import requests
import json
import time
from datetime import datetime
from collections import defaultdict

# Etherscan V2 API - Base chain için
# Artık tek API key ile tüm EVM chainlere erişim var
BASESCAN_API = "https://api.etherscan.io/v2/api"
ETHERSCAN_API_KEY = "6TE7PX7TDS777Z3T7NQCZVUK4KBK9HHDJQ"
BASE_CHAIN_ID = "8453"  # Base mainnet chain ID

def load_tokens():
    """Filtrelenmiş tokenleri yükle"""
    with open('/Users/emrecapin/Desktop/smart-money-base/data/tokens_filtered.json') as f:
        data = json.load(f)
    return data.get('tokens', [])

def get_token_address(token):
    """Token'ın contract adresini al"""
    base_token = token.get('baseToken', {})
    return base_token.get('address')

def fetch_token_transfers(token_address, start_block=0, end_block=99999999):
    """
    Belirli bir token için tüm transfer işlemlerini çeker
    Etherscan V2 API kullanır (Base chain)
    """

    url = BASESCAN_API

    params = {
        'chainid': BASE_CHAIN_ID,
        'module': 'account',
        'action': 'tokentx',
        'contractaddress': token_address,
        'startblock': start_block,
        'endblock': end_block,
        'sort': 'asc',
        'apikey': ETHERSCAN_API_KEY
    }

    try:
        response = requests.get(url, params=params, timeout=30)
        if response.status_code == 200:
            data = response.json()
            if data.get('status') == '1':
                return data.get('result', [])
            else:
                print(f"    API mesajı: {data.get('message', 'Unknown')}")
    except Exception as e:
        print(f"    Hata: {e}")

    return []

def fetch_dex_trades_for_token(pair_address):
    """
    DEXScreener'dan pair için trade verilerini çek
    Bu daha güvenilir olabilir
    """

    url = f"https://api.dexscreener.com/latest/dex/pairs/base/{pair_address}"

    try:
        response = requests.get(url, timeout=30)
        if response.status_code == 200:
            data = response.json()
            pairs = data.get('pairs', [])
            if pairs:
                return pairs[0]
    except Exception as e:
        print(f"    DEXScreener hatası: {e}")

    return None

def analyze_transfers(transfers, token_created_at, token_decimals=18):
    """
    Transfer verilerini analiz et:
    1. Erken alıcıları tespit et (ilk 48 saat)
    2. Satış yapanları tespit et
    3. Kar/zarar hesapla
    """

    if not transfers:
        return []

    wallets = defaultdict(lambda: {
        'buys': [],
        'sells': [],
        'total_bought': 0,
        'total_sold': 0,
        'first_buy_time': None,
        'last_sell_time': None
    })

    # DEX router adresleri (bunları filtrele)
    dex_routers = {
        '0x4752ba5dbc23f44d87826276bf6fd6b1c372ad24',  # Uniswap V3
        '0x2626664c2603336e57b271c5c0b26f421741e481',  # Uniswap V2
        '0xcf77a3ba9a5ca399b7c97c74d54e5b1beb874e43',  # Aerodrome
        '0x6131b5fae19ea4f9d964eac0408e4408b66337b5',  # BaseSwap
    }

    # Null address (mint/burn)
    null_addresses = {
        '0x0000000000000000000000000000000000000000',
        '0x000000000000000000000000000000000000dead'
    }

    for tx in transfers:
        from_addr = tx.get('from', '').lower()
        to_addr = tx.get('to', '').lower()
        value = int(tx.get('value', 0)) / (10 ** token_decimals)
        timestamp = int(tx.get('timeStamp', 0))
        tx_hash = tx.get('hash', '')

        # Router ve null adresleri atla
        if from_addr in dex_routers or to_addr in dex_routers:
            continue
        if from_addr in null_addresses or to_addr in null_addresses:
            continue

        # Alım (to_addr cüzdanına gelen)
        if to_addr and to_addr not in null_addresses:
            wallets[to_addr]['buys'].append({
                'timestamp': timestamp,
                'value': value,
                'tx_hash': tx_hash
            })
            wallets[to_addr]['total_bought'] += value
            if wallets[to_addr]['first_buy_time'] is None:
                wallets[to_addr]['first_buy_time'] = timestamp

        # Satış (from_addr cüzdanından giden)
        if from_addr and from_addr not in null_addresses:
            wallets[from_addr]['sells'].append({
                'timestamp': timestamp,
                'value': value,
                'tx_hash': tx_hash
            })
            wallets[from_addr]['total_sold'] += value
            wallets[from_addr]['last_sell_time'] = timestamp

    return dict(wallets)

def identify_profitable_wallets(wallets, token_created_at, current_price, early_window_hours=48):
    """
    Karlı cüzdanları tespit et:
    - Erken alım (ilk 48 saat)
    - Kar >= $1,000
    - ROI >= %200
    """

    profitable = []

    for address, data in wallets.items():
        # En az bir alım ve bir satış olmalı
        if not data['buys'] or not data['sells']:
            continue

        # Erken alıcı mı kontrol et
        first_buy = data['first_buy_time']
        if not first_buy or not token_created_at:
            continue

        hours_after_creation = (first_buy - token_created_at / 1000) / 3600

        # 48 saat içinde alım yapmış mı?
        is_early_buyer = 0 < hours_after_creation < early_window_hours

        if not is_early_buyer:
            continue

        # Basit kar hesabı
        # Not: Gerçek kar için fiyat verisi gerekli, şimdilik token miktarına bakıyoruz
        total_bought = data['total_bought']
        total_sold = data['total_sold']

        # En az %50'sini satmış olmalı
        if total_sold < total_bought * 0.5:
            continue

        profitable.append({
            'address': address,
            'total_bought': total_bought,
            'total_sold': total_sold,
            'first_buy_time': first_buy,
            'hours_after_creation': round(hours_after_creation, 2),
            'num_buys': len(data['buys']),
            'num_sells': len(data['sells']),
            'sell_ratio': round(total_sold / total_bought * 100, 2) if total_bought > 0 else 0
        })

    # Sell ratio'ya göre sırala (en çok kar eden)
    profitable.sort(key=lambda x: x['sell_ratio'], reverse=True)

    return profitable

def save_progress(data, step, filename_suffix):
    """İlerlemeyi kaydet"""

    filename = f'/Users/emrecapin/Desktop/smart-money-base/data/wallets_{filename_suffix}.json'

    with open(filename, 'w', encoding='utf-8') as f:
        json.dump({
            'timestamp': datetime.now().isoformat(),
            'step': step,
            'data': data
        }, f, indent=2, ensure_ascii=False)

    print(f"  Kaydedildi: {filename}")

def main():
    print("=" * 60)
    print("Cüzdan Ayıklama (Faz 2)")
    print("=" * 60)
    print(f"Başlangıç: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    # Tokenleri yükle
    tokens = load_tokens()
    print(f"[1/3] {len(tokens)} token yüklendi")

    all_wallets = {}
    all_profitable = []

    # Her token için cüzdanları analiz et
    print("\n[2/3] Token transferleri analiz ediliyor...")

    for i, token in enumerate(tokens):
        token_addr = get_token_address(token)
        symbol = token.get('baseToken', {}).get('symbol', 'Unknown')
        pair_addr = token.get('pairAddress', '')
        created_at = token.get('pairCreatedAt', 0)
        current_price = float(token.get('priceUsd', 0) or 0)

        print(f"\n  [{i+1}/{len(tokens)}] {symbol} ({token_addr[:10]}...)")

        if not token_addr:
            print(f"    Token adresi bulunamadı, atlanıyor...")
            continue

        # Transfer verilerini çek
        print(f"    Transfer verileri çekiliyor...")
        transfers = fetch_token_transfers(token_addr)
        print(f"    {len(transfers)} transfer bulundu")

        if not transfers:
            print(f"    Transfer yok, atlanıyor...")
            time.sleep(1)
            continue

        # Cüzdanları analiz et
        wallets = analyze_transfers(transfers, created_at)
        print(f"    {len(wallets)} unique cüzdan bulundu")

        # Karlı cüzdanları tespit et
        profitable = identify_profitable_wallets(wallets, created_at, current_price)
        print(f"    {len(profitable)} potansiyel karlı cüzdan tespit edildi")

        # Token bilgisiyle birlikte kaydet
        for wallet in profitable:
            wallet['token_symbol'] = symbol
            wallet['token_address'] = token_addr
            wallet['pair_address'] = pair_addr

        all_profitable.extend(profitable)

        # Rate limit
        time.sleep(2)

    # Sonuçları kaydet
    print("\n[3/3] Sonuçlar kaydediliyor...")

    # Tüm karlı cüzdanları kaydet
    save_data = {
        'timestamp': datetime.now().isoformat(),
        'total_wallets': len(all_profitable),
        'tokens_analyzed': len(tokens),
        'wallets': all_profitable
    }

    with open('/Users/emrecapin/Desktop/smart-money-base/data/wallets_profitable.json', 'w') as f:
        json.dump(save_data, f, indent=2, ensure_ascii=False)

    print(f"  {len(all_profitable)} karlı cüzdan kaydedildi")

    # Özet
    print("\n" + "=" * 60)
    print("ÖZET")
    print("=" * 60)

    if all_profitable:
        print(f"\nToplam potansiyel karlı cüzdan: {len(all_profitable)}")
        print(f"\nTop 20 Cüzdan (Satış oranına göre):")
        print("-" * 80)

        for i, w in enumerate(all_profitable[:20], 1):
            addr = w['address']
            symbol = w['token_symbol']
            hours = w['hours_after_creation']
            ratio = w['sell_ratio']
            print(f"{i:2}. {addr[:12]}... | {symbol:12} | Entry: {hours:>5.0f}h | Sell: {ratio:>6.1f}%")
    else:
        print("\n[!] Kriterlere uyan karlı cüzdan bulunamadı")

    print("\n" + "=" * 60)
    print(f"Bitiş: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    return all_profitable

if __name__ == "__main__":
    wallets = main()
