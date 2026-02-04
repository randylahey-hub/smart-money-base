"""
WETH-Only Token Filtreleme Scripti
Mükerrer tokenleri elimine eder, sadece WETH pairleri tutar.
Her unique token için en yüksek likiditeye sahip WETH pair'i seçer.
"""

import json
from datetime import datetime

def load_tokens():
    """Mevcut filtrelenmiş tokenleri yükle"""
    with open('/Users/emrecapin/Desktop/smart-money-base/data/tokens_filtered.json') as f:
        return json.load(f)

def filter_weth_only(tokens_data):
    """
    1. Sadece WETH pair'leri filtrele
    2. Her unique token için en yüksek likiditeye sahip pair'i seç
    """

    tokens = tokens_data.get('tokens', [])

    print(f"Toplam pair sayısı: {len(tokens)}")

    # WETH pairleri filtrele
    weth_pairs = [
        t for t in tokens
        if t.get('quoteToken', {}).get('symbol') == 'WETH'
    ]

    print(f"WETH pair sayısı: {len(weth_pairs)}")

    # Her unique token için en yüksek likiditeye sahip pair'i seç
    token_map = {}

    for pair in weth_pairs:
        token_addr = pair.get('baseToken', {}).get('address', '').lower()
        liquidity = pair.get('liquidity', {}).get('usd', 0)

        if token_addr not in token_map or liquidity > token_map[token_addr]['liquidity']['usd']:
            token_map[token_addr] = pair

    unique_tokens = list(token_map.values())

    print(f"Unique token sayısı: {len(unique_tokens)}")

    # Token detaylarını yazdır
    print("\n" + "=" * 70)
    print("WETH-ONLY UNIQUE TOKENLAR")
    print("=" * 70)

    for i, t in enumerate(unique_tokens, 1):
        symbol = t.get('baseToken', {}).get('symbol', 'Unknown')
        token_addr = t.get('baseToken', {}).get('address', '')[:16]
        mcap = t.get('marketCap', 0) / 1_000_000  # M
        vol = t.get('volume', {}).get('h24', 0) / 1_000_000  # M
        liq = t.get('liquidity', {}).get('usd', 0) / 1_000_000  # M
        created = t.get('pairCreatedAt', 0)

        # Yaş hesapla
        if created:
            age_hours = (datetime.now().timestamp() * 1000 - created) / (1000 * 3600)
        else:
            age_hours = 0

        print(f"{i}. {symbol:15} | MCap: ${mcap:>6.2f}M | Vol: ${vol:>5.2f}M | Liq: ${liq:>5.2f}M | Yaş: {age_hours:>5.0f}h")

    return unique_tokens

def save_filtered(tokens):
    """Filtrelenmiş tokenleri kaydet"""

    output = {
        'timestamp': datetime.now().isoformat(),
        'filter': 'WETH-only, max liquidity per token',
        'count': len(tokens),
        'criteria': {
            'min_mcap': 1500000,
            'min_volume_24h': 500000,
            'age_hours': '24-168',
            'quote_token': 'WETH'
        },
        'tokens': tokens
    }

    output_path = '/Users/emrecapin/Desktop/smart-money-base/data/tokens_weth_only.json'

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\nKaydedildi: {output_path}")

    return output_path

def main():
    print("=" * 70)
    print("WETH-Only Token Filtreleme")
    print("=" * 70)
    print(f"Başlangıç: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    # Tokenleri yükle
    tokens_data = load_tokens()

    # WETH-only filtrele
    unique_tokens = filter_weth_only(tokens_data)

    # Kaydet
    save_filtered(unique_tokens)

    print("\n" + "=" * 70)
    print("TAMAMLANDI")
    print("=" * 70)

    return unique_tokens

if __name__ == "__main__":
    tokens = main()
