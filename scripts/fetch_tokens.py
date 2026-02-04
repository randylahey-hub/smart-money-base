"""
Base Chain Token Fetcher v2
DEXScreener API kullanarak Base chain tokenlarını çeker ve filtreler.

Kriterler:
- Market Cap: >= $1.5M
- Token yaşı: 1-7 gün
- 24H Volume: >= $500K
"""

import requests
import json
import time
from datetime import datetime, timedelta

DEXSCREENER_BASE_URL = "https://api.dexscreener.com"

def fetch_new_pairs_base():
    """
    DEXScreener'ın yeni pair'ler endpoint'ini kullanarak
    Base chain'deki son pairleri çeker
    """

    # Son token boosted/profiles'ı çekelim
    url = f"{DEXSCREENER_BASE_URL}/token-boosts/top/v1"

    headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
    }

    all_pairs = []

    try:
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200:
            data = response.json()
            # Base chain olanları filtrele
            for item in data:
                if item.get('chainId') == 'base':
                    all_pairs.append(item)
            print(f"  Token boosts'tan {len(all_pairs)} Base token bulundu")
    except Exception as e:
        print(f"  Boost API hatası: {e}")

    return all_pairs

def get_pair_details(token_address, chain="base"):
    """Token adresinden pair detaylarını çeker"""

    url = f"{DEXSCREENER_BASE_URL}/tokens/v1/{chain}/{token_address}"

    headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
    }

    try:
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200:
            return response.json()
    except Exception as e:
        print(f"  Hata {token_address[:10]}...: {e}")

    return []

def fetch_trending_base():
    """Trending tokenları çeker"""

    # Farklı stratejiler deneyelim
    pairs = []

    # Strateji 1: Popüler Base tokenlarını ara
    popular_searches = [
        "meme base", "ai base", "degen base", "brett base",
        "toshi base", "higher base", "well base", "aero base"
    ]

    for term in popular_searches:
        url = f"{DEXSCREENER_BASE_URL}/latest/dex/search?q={term}"
        try:
            response = requests.get(url, timeout=30)
            if response.status_code == 200:
                data = response.json()
                search_pairs = data.get('pairs', [])
                base_pairs = [p for p in search_pairs if p.get('chainId') == 'base']
                pairs.extend(base_pairs)
                if base_pairs:
                    print(f"  '{term}': {len(base_pairs)} pair")
            time.sleep(0.3)
        except Exception as e:
            print(f"  Hata '{term}': {e}")

    # Strateji 2: Direkt Base chain arama
    base_searches = ["base chain", "coinbase", "base network"]

    for term in base_searches:
        url = f"{DEXSCREENER_BASE_URL}/latest/dex/search?q={term}"
        try:
            response = requests.get(url, timeout=30)
            if response.status_code == 200:
                data = response.json()
                search_pairs = data.get('pairs', [])
                base_pairs = [p for p in search_pairs if p.get('chainId') == 'base']
                pairs.extend(base_pairs)
                if base_pairs:
                    print(f"  '{term}': {len(base_pairs)} pair")
            time.sleep(0.3)
        except Exception as e:
            print(f"  Hata '{term}': {e}")

    return pairs

def fetch_from_web_url():
    """
    DEXScreener web URL parametrelerini kullanarak veri çeker
    Bu daha kapsamlı sonuç verebilir
    """

    # Web'deki filtreli URL'deki pairleri çekmek için
    # Token profiles endpoint kullan

    url = f"{DEXSCREENER_BASE_URL}/token-profiles/latest/v1"

    headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
    }

    try:
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200:
            data = response.json()
            base_tokens = [t for t in data if t.get('chainId') == 'base']
            print(f"  Token profiles'tan {len(base_tokens)} Base token bulundu")
            return base_tokens
    except Exception as e:
        print(f"  Profiles API hatası: {e}")

    return []

def enrich_with_pair_data(tokens):
    """Token listesini pair verileriyle zenginleştirir"""

    enriched = []
    total = len(tokens)

    for i, token in enumerate(tokens):
        token_addr = token.get('tokenAddress')
        if not token_addr:
            continue

        print(f"  [{i+1}/{total}] {token_addr[:10]}... işleniyor", end='\r')

        pairs = get_pair_details(token_addr, 'base')

        if pairs:
            # En yüksek likiditeye sahip pair'i al
            best_pair = max(pairs, key=lambda x: x.get('liquidity', {}).get('usd', 0) or 0)

            enriched.append({
                'tokenAddress': token_addr,
                'pairAddress': best_pair.get('pairAddress'),
                'baseToken': best_pair.get('baseToken', {}),
                'quoteToken': best_pair.get('quoteToken', {}),
                'priceUsd': best_pair.get('priceUsd'),
                'marketCap': best_pair.get('marketCap') or best_pair.get('fdv') or 0,
                'volume24h': best_pair.get('volume', {}).get('h24', 0) or 0,
                'liquidity': best_pair.get('liquidity', {}).get('usd', 0) or 0,
                'pairCreatedAt': best_pair.get('pairCreatedAt'),
                'priceChange24h': best_pair.get('priceChange', {}).get('h24', 0) or 0,
                'txns24h': best_pair.get('txns', {}).get('h24', {}),
                'dexId': best_pair.get('dexId'),
                'url': best_pair.get('url'),
                'profile': token
            })

        time.sleep(0.2)  # Rate limit

    print()  # Yeni satır
    return enriched

def filter_tokens(pairs, min_mcap=1500000, min_volume=500000, min_age_hours=24, max_age_hours=168):
    """Tokenleri kriterlere göre filtreler"""

    filtered = []
    now = datetime.now()

    for pair in pairs:
        try:
            # Market cap kontrolü
            mcap = pair.get('marketCap', 0) or 0
            if mcap < min_mcap:
                continue

            # Volume kontrolü
            volume_24h = pair.get('volume24h', 0) or pair.get('volume', {}).get('h24', 0) or 0
            if volume_24h < min_volume:
                continue

            # Yaş kontrolü
            created_at = pair.get('pairCreatedAt')
            if created_at:
                created_time = datetime.fromtimestamp(created_at / 1000)
                age_hours = (now - created_time).total_seconds() / 3600

                if age_hours < min_age_hours or age_hours > max_age_hours:
                    continue

                pair['ageHours'] = round(age_hours, 2)
            else:
                continue

            filtered.append(pair)

        except Exception as e:
            continue

    return filtered

def save_tokens(tokens, filename):
    """Tokenleri JSON dosyasına kaydeder"""

    with open(filename, 'w', encoding='utf-8') as f:
        json.dump({
            'timestamp': datetime.now().isoformat(),
            'count': len(tokens),
            'filters': {
                'min_mcap': 1500000,
                'min_volume': 500000,
                'min_age_hours': 24,
                'max_age_hours': 168
            },
            'tokens': tokens
        }, f, indent=2, ensure_ascii=False)

    print(f"  {len(tokens)} token kaydedildi: {filename}")

def main():
    print("=" * 60)
    print("Base Chain Token Fetcher v2")
    print("=" * 60)
    print(f"Başlangıç: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    all_pairs = []

    # Adım 1: Çeşitli kaynaklardan veri topla
    print("[1/4] Veri kaynakları taranıyor...")

    # Token profiles
    profiles = fetch_from_web_url()

    # Token boosts
    boosts = fetch_new_pairs_base()

    # Trending aramalar
    print("\n  Trending aramalar:")
    trending = fetch_trending_base()

    # Tüm verileri birleştir
    all_data = profiles + boosts

    # Trending pair'leri de ekle (farklı format)
    all_pairs.extend(trending)

    print(f"\n  Toplam ham veri: {len(all_data)} profile/boost + {len(all_pairs)} pair")

    # Adım 2: Profile/boost verilerini pair verileriyle zenginleştir
    if all_data:
        print("\n[2/4] Token verileri zenginleştiriliyor...")
        enriched = enrich_with_pair_data(all_data[:50])  # İlk 50'yi işle (rate limit)
        all_pairs.extend(enriched)

    # Duplicate'leri kaldır
    seen = set()
    unique_pairs = []
    for pair in all_pairs:
        addr = pair.get('pairAddress') or pair.get('tokenAddress')
        if addr and addr not in seen:
            seen.add(addr)
            unique_pairs.append(pair)

    print(f"\n  Unique pair sayısı: {len(unique_pairs)}")

    # Adım 3: Filtrele
    print("\n[3/4] Filtreler uygulanıyor...")
    print(f"  - Min Market Cap: $1.5M")
    print(f"  - Min 24H Volume: $500K")
    print(f"  - Token Yaşı: 1-7 gün (24-168 saat)")

    filtered_tokens = filter_tokens(
        unique_pairs,
        min_mcap=1500000,
        min_volume=500000,
        min_age_hours=24,
        max_age_hours=168
    )

    print(f"\n  Filtreleme sonucu: {len(filtered_tokens)} token")

    # Eğer çok az sonuç varsa, kriterleri gevşet
    if len(filtered_tokens) < 10:
        print("\n  [!] Az sonuç, kriterler gevşetiliyor...")

        # Volume kriterini düşür
        filtered_tokens = filter_tokens(
            unique_pairs,
            min_mcap=1000000,  # $1M
            min_volume=100000,  # $100K
            min_age_hours=24,
            max_age_hours=168
        )
        print(f"  Gevşetilmiş filtre sonucu: {len(filtered_tokens)} token")

    # Adım 4: Kaydet
    print("\n[4/4] Sonuçlar kaydediliyor...")

    output_file = "/Users/emrecapin/Desktop/smart-money-base/data/tokens.json"
    save_tokens(filtered_tokens, output_file)

    # Tüm unique pairleri de kaydet (analiz için)
    all_file = "/Users/emrecapin/Desktop/smart-money-base/data/all_pairs.json"
    save_tokens(unique_pairs, all_file)

    # Özet
    print("\n" + "=" * 60)
    print("ÖZET")
    print("=" * 60)

    if filtered_tokens:
        sorted_tokens = sorted(filtered_tokens, key=lambda x: x.get('marketCap', 0) or 0, reverse=True)

        print(f"\nBulunan Token Sayısı: {len(filtered_tokens)}")
        print(f"\nTop 10 Token (Market Cap'e göre):")
        print("-" * 60)

        for i, token in enumerate(sorted_tokens[:10], 1):
            name = token.get('baseToken', {}).get('symbol', 'Unknown')
            mcap = token.get('marketCap', 0) or 0
            vol = token.get('volume24h', 0) or 0
            age = token.get('ageHours', 0)
            print(f"{i:2}. {name:12} | MCap: ${mcap:>12,.0f} | Vol: ${vol:>10,.0f} | Age: {age:>5.0f}h")
    else:
        print("\n[!] Kriterlere uyan token bulunamadı.")
        print("    Tüm pair'ler all_pairs.json'a kaydedildi, manuel inceleme yapılabilir.")

    print("\n" + "=" * 60)
    print(f"Bitiş: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    return filtered_tokens, unique_pairs

if __name__ == "__main__":
    filtered, all_pairs = main()
