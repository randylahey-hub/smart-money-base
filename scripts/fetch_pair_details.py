"""
Pair Detay Çekici
DEXScreener'dan alınan pair adreslerinin detaylarını çeker
"""

import requests
import json
import time
from datetime import datetime

DEXSCREENER_API = "https://api.dexscreener.com"

def fetch_pair_info(pair_address):
    """Tek bir pair'in detaylarını çeker"""

    url = f"{DEXSCREENER_API}/latest/dex/pairs/base/{pair_address}"

    headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0"
    }

    try:
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200:
            data = response.json()
            pairs = data.get('pairs', [])
            if pairs:
                return pairs[0]
    except Exception as e:
        print(f"  Hata {pair_address[:12]}...: {e}")

    return None

def fetch_all_pairs(pair_addresses, batch_size=30):
    """
    Tüm pair'lerin detaylarını çeker
    DEXScreener 30 adrese kadar tek istekte izin veriyor
    """

    all_pairs = []
    total = len(pair_addresses)

    # Batch'ler halinde işle
    for i in range(0, total, batch_size):
        batch = pair_addresses[i:i+batch_size]
        batch_str = ','.join(batch)

        url = f"{DEXSCREENER_API}/latest/dex/pairs/base/{batch_str}"

        print(f"  Batch {i//batch_size + 1}: {len(batch)} pair işleniyor...", end='\r')

        try:
            response = requests.get(url, timeout=30)
            if response.status_code == 200:
                data = response.json()
                pairs = data.get('pairs', [])
                all_pairs.extend(pairs)
        except Exception as e:
            print(f"\n  Batch hatası: {e}")

        time.sleep(0.5)  # Rate limit

    print()
    return all_pairs

def filter_by_volume(pairs, min_volume=500000):
    """Volume kriterine göre filtreler"""

    filtered = []

    for pair in pairs:
        volume_24h = pair.get('volume', {}).get('h24', 0) or 0
        if volume_24h >= min_volume:
            filtered.append(pair)

    return filtered

def save_results(data, filename):
    """Sonuçları JSON dosyasına kaydeder"""

    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"  Kaydedildi: {filename}")

def main():
    print("=" * 60)
    print("Pair Detay Çekici")
    print("=" * 60)
    print(f"Başlangıç: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    # Pair adreslerini oku
    with open('/Users/emrecapin/Desktop/smart-money-base/data/pair_addresses.json') as f:
        data = json.load(f)

    pair_addresses = data.get('pairs', [])
    print(f"[1/3] {len(pair_addresses)} pair adresi yüklendi")

    # Detayları çek
    print("\n[2/3] Pair detayları çekiliyor...")
    pairs = fetch_all_pairs(pair_addresses)
    print(f"  {len(pairs)} pair detayı alındı")

    # Volume filtresi uygula
    print("\n[3/3] Volume filtresi uygulanıyor (min $500K)...")
    filtered = filter_by_volume(pairs, min_volume=500000)
    print(f"  {len(filtered)} pair $500K+ volume kriterini karşılıyor")

    # Tüm pairleri kaydet
    all_data = {
        'timestamp': datetime.now().isoformat(),
        'total_pairs': len(pairs),
        'filtered_count': len(filtered),
        'filter_criteria': {
            'min_volume_24h': 500000
        },
        'pairs': pairs
    }
    save_results(all_data, '/Users/emrecapin/Desktop/smart-money-base/data/pairs_detailed.json')

    # Filtrelenmiş pairleri kaydet
    filtered_data = {
        'timestamp': datetime.now().isoformat(),
        'count': len(filtered),
        'criteria': {
            'min_mcap': 1500000,
            'min_volume_24h': 500000,
            'age_hours': '24-168'
        },
        'tokens': filtered
    }
    save_results(filtered_data, '/Users/emrecapin/Desktop/smart-money-base/data/tokens_filtered.json')

    # Özet
    print("\n" + "=" * 60)
    print("ÖZET")
    print("=" * 60)

    if filtered:
        # Market cap'e göre sırala
        sorted_tokens = sorted(filtered, key=lambda x: x.get('marketCap', 0) or 0, reverse=True)

        print(f"\nKriterleri karşılayan token sayısı: {len(filtered)}")
        print(f"\nTop 15 Token (Market Cap'e göre):")
        print("-" * 80)
        print(f"{'#':>2} {'Symbol':12} {'MCap':>14} {'24H Vol':>12} {'Age':>6} {'Pair Address':42}")
        print("-" * 80)

        for i, token in enumerate(sorted_tokens[:15], 1):
            symbol = token.get('baseToken', {}).get('symbol', 'N/A')
            mcap = token.get('marketCap', 0) or 0
            vol = token.get('volume', {}).get('h24', 0) or 0

            # Yaş hesapla
            created = token.get('pairCreatedAt')
            if created:
                age_hours = (datetime.now().timestamp() * 1000 - created) / (1000 * 3600)
                age_str = f"{int(age_hours)}h"
            else:
                age_str = "N/A"

            pair_addr = token.get('pairAddress', 'N/A')[:42]

            print(f"{i:>2} {symbol:12} ${mcap:>13,.0f} ${vol:>11,.0f} {age_str:>6} {pair_addr}")
    else:
        print("\n[!] Kriterlere uyan token bulunamadı")

    print("\n" + "=" * 60)
    print(f"Bitiş: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    return filtered

if __name__ == "__main__":
    tokens = main()
