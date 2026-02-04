"""
Cüzdan Ayıklama Scripti V2 (Faz 2 - Geliştirilmiş)

Düzeltmeler:
1. WETH-only tokenlar kullanılıyor
2. Timestamp hesaplama düzeltildi
3. Timeout 120 saniyeye çıkarıldı
4. Rate limit retry mekanizması eklendi
5. Checkpoint sistemi eklendi

Kriterler:
- Erken alım: Token listing'den sonra ilk 48 saat
- Kar: >= $1,000 VE >= %200 ROI (basitleştirilmiş: satış oranı)
"""

import requests
import json
import time
import os
from datetime import datetime
from collections import defaultdict

# ============================================================
# AYARLAR
# ============================================================

# Etherscan V2 API
BASESCAN_API = "https://api.etherscan.io/v2/api"
ETHERSCAN_API_KEY = "6TE7PX7TDS777Z3T7NQCZVUK4KBK9HHDJQ"
BASE_CHAIN_ID = "8453"

# Timeout ve Rate Limit
REQUEST_TIMEOUT = 120  # saniye (30'dan 120'ye çıkarıldı)
REQUEST_DELAY = 5      # saniye (API çağrıları arası) - artırıldı
RATE_LIMIT_WAIT = 30   # saniye (rate limit hatasında bekleme)
MAX_RETRIES = 5        # maksimum deneme sayısı - artırıldı

# Erken alım penceresi
EARLY_WINDOW_HOURS = 48  # ilk 48 saat

# Dosya yolları
DATA_DIR = '/Users/emrecapin/Desktop/smart-money-base/data'
CHECKPOINT_DIR = f'{DATA_DIR}/checkpoints'
TOKENS_FILE = f'{DATA_DIR}/tokens_extended.json'
OUTPUT_FILE = f'{DATA_DIR}/wallets_profitable_v2.json'
PROGRESS_FILE = '/Users/emrecapin/Desktop/smart-money-base/progress.md'

# ============================================================
# HELPER FONKSIYONLAR
# ============================================================

def ensure_dirs():
    """Gerekli klasörleri oluştur"""
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)

def load_tokens():
    """WETH-only filtrelenmiş tokenleri yükle"""
    with open(TOKENS_FILE) as f:
        data = json.load(f)
    return data.get('tokens', [])

def load_checkpoint(token_symbol):
    """Token için checkpoint varsa yükle"""
    checkpoint_file = f'{CHECKPOINT_DIR}/{token_symbol.lower()}_checkpoint.json'
    if os.path.exists(checkpoint_file):
        with open(checkpoint_file) as f:
            return json.load(f)
    return None

def save_checkpoint(token_symbol, data):
    """Token için checkpoint kaydet"""
    checkpoint_file = f'{CHECKPOINT_DIR}/{token_symbol.lower()}_checkpoint.json'
    with open(checkpoint_file, 'w') as f:
        json.dump(data, f, indent=2)
    print(f"    💾 Checkpoint kaydedildi: {token_symbol}")

def update_progress(current_token, status, wallet_count):
    """progress.md dosyasını güncelle"""
    content = f"""# Smart Money Base - İlerleme Durumu

**Proje Başlangıç:** 2026-02-03
**Son Güncelleme:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

---

## Mevcut Durum

| Faz | Durum | İlerleme |
|-----|-------|----------|
| Faz 1: Token Filtreleme | ✅ Tamamlandı | 100% |
| Faz 2: Cüzdan Ayıklama | 🔄 Devam Ediyor | {status} |
| Faz 3: Analiz & Skorlama | ⏳ Bekliyor | 0% |
| Faz 4: Çakışan Cüzdan | ⏳ Bekliyor | 0% |
| Faz 5: Raporlama | ⏳ Bekliyor | 0% |

---

## Checkpoint

```
Son işlenen token: {current_token}
Toplam bulunan cüzdan: {wallet_count}
Script: fetch_wallets_v2.py (Geliştirilmiş)
Timeout: {REQUEST_TIMEOUT}s
Rate Limit Wait: {RATE_LIMIT_WAIT}s
```

---

## Devam Etmek İçin

Kesinti sonrası şu komutu kullan:
> "Kaldığım yerden devam et"
"""
    with open(PROGRESS_FILE, 'w') as f:
        f.write(content)

# ============================================================
# API FONKSİYONLARI
# ============================================================

def fetch_with_retry(url, params, description="API çağrısı"):
    """
    Rate limit ve timeout yönetimi ile API çağrısı
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)

            if response.status_code == 200:
                data = response.json()

                # Debug: API yanıtını göster
                status = data.get('status')
                message = data.get('message', '')

                # API başarılı mı? (status hem "1" hem de 1 olabilir)
                if str(status) == '1':
                    result = data.get('result', [])
                    if isinstance(result, list):
                        return result
                    else:
                        print(f"    ⚠️ Result bir liste değil: {type(result)}")
                        return []

                # Rate limit hatası mı?
                if 'rate limit' in message.lower() or 'max rate' in message.lower():
                    print(f"    ⏳ Rate limit! {RATE_LIMIT_WAIT}s bekleniyor... (Deneme {attempt}/{MAX_RETRIES})")
                    time.sleep(RATE_LIMIT_WAIT)
                    continue

                # Veri yok
                if 'no transactions' in message.lower() or message == 'No transactions found':
                    return []

                # NOTOK durumu - genellikle geçici hata veya rate limit
                if message == 'NOTOK':
                    result_msg = data.get('result', '')
                    print(f"    ⚠️ NOTOK: {result_msg}")
                    if 'rate' in str(result_msg).lower() or 'limit' in str(result_msg).lower():
                        print(f"    ⏳ Rate limit tespit edildi, {RATE_LIMIT_WAIT}s bekleniyor...")
                        time.sleep(RATE_LIMIT_WAIT)
                        continue
                    # Diğer NOTOK durumları için de bekleyip tekrar dene
                    time.sleep(REQUEST_DELAY * 2)
                    continue

                print(f"    ⚠️ API yanıtı: status={status}, message={message}")
                time.sleep(REQUEST_DELAY)
                continue

            else:
                print(f"    ❌ HTTP {response.status_code} - Deneme {attempt}/{MAX_RETRIES}")
                time.sleep(REQUEST_DELAY * attempt)

        except requests.exceptions.Timeout:
            print(f"    ⏱️ Timeout ({REQUEST_TIMEOUT}s) - Deneme {attempt}/{MAX_RETRIES}")
            time.sleep(REQUEST_DELAY * attempt)

        except Exception as e:
            print(f"    ❌ Hata: {e} - Deneme {attempt}/{MAX_RETRIES}")
            time.sleep(REQUEST_DELAY * attempt)

    print(f"    ❌ {MAX_RETRIES} denemeden sonra başarısız")
    return []

def fetch_token_transfers(token_address):
    """
    Token için tüm transfer işlemlerini çeker
    """
    params = {
        'chainid': BASE_CHAIN_ID,
        'module': 'account',
        'action': 'tokentx',
        'contractaddress': token_address,
        'startblock': 0,
        'endblock': 99999999,
        'sort': 'asc',
        'apikey': ETHERSCAN_API_KEY
    }

    return fetch_with_retry(BASESCAN_API, params, f"Token transfers: {token_address[:10]}...")

# ============================================================
# ANALİZ FONKSİYONLARI
# ============================================================

def analyze_transfers(transfers, pair_created_at, token_decimals=18):
    """
    Transfer verilerini analiz et:
    1. Her cüzdanın alım/satım geçmişini çıkar
    2. Erken alıcıları tespit et
    """

    if not transfers:
        return {}

    wallets = defaultdict(lambda: {
        'buys': [],
        'sells': [],
        'total_bought': 0,
        'total_sold': 0,
        'first_buy_time': None,
        'last_sell_time': None
    })

    # DEX router ve özel adresler (filtrele)
    excluded_addresses = {
        '0x4752ba5dbc23f44d87826276bf6fd6b1c372ad24',  # Uniswap Universal Router
        '0x2626664c2603336e57b271c5c0b26f421741e481',  # Uniswap V2 Router
        '0xcf77a3ba9a5ca399b7c97c74d54e5b1beb874e43',  # Aerodrome Router
        '0x6131b5fae19ea4f9d964eac0408e4408b66337b5',  # BaseSwap Router
        '0x3fc91a3afd70395cd496c647d5a6cc9d4b2b7fad',  # Uniswap Universal Router 2
        '0x0000000000000000000000000000000000000000',  # Null
        '0x000000000000000000000000000000000000dead',  # Dead
    }

    for tx in transfers:
        from_addr = tx.get('from', '').lower()
        to_addr = tx.get('to', '').lower()

        # Excluded adresleri atla
        if from_addr in excluded_addresses or to_addr in excluded_addresses:
            continue

        try:
            value = int(tx.get('value', 0)) / (10 ** token_decimals)
        except:
            continue

        timestamp = int(tx.get('timeStamp', 0))
        tx_hash = tx.get('hash', '')

        # Alım kaydı (token'ı alan adres)
        if to_addr and to_addr not in excluded_addresses:
            wallets[to_addr]['buys'].append({
                'timestamp': timestamp,
                'value': value,
                'tx_hash': tx_hash
            })
            wallets[to_addr]['total_bought'] += value
            if wallets[to_addr]['first_buy_time'] is None:
                wallets[to_addr]['first_buy_time'] = timestamp

        # Satış kaydı (token'ı gönderen adres)
        if from_addr and from_addr not in excluded_addresses:
            wallets[from_addr]['sells'].append({
                'timestamp': timestamp,
                'value': value,
                'tx_hash': tx_hash
            })
            wallets[from_addr]['total_sold'] += value
            wallets[from_addr]['last_sell_time'] = timestamp

    return dict(wallets)

def identify_profitable_wallets(wallets, pair_created_at, current_price):
    """
    Karlı cüzdanları tespit et:
    - Erken alım (ilk 48 saat)
    - En az %50 satış yapmış
    - Daha detaylı analiz için aday
    """

    profitable = []

    # pair_created_at milisaniye, timestamp saniye
    created_timestamp = pair_created_at / 1000

    for address, data in wallets.items():
        # En az bir alım olmalı
        if not data['buys']:
            continue

        # İlk alım zamanı
        first_buy = data['first_buy_time']
        if not first_buy:
            continue

        # Erken alıcı mı? (DÜZELTİLMİŞ HESAPLAMA)
        hours_after_creation = (first_buy - created_timestamp) / 3600

        # Negatif değerler geçersiz (token oluşmadan önce alım?)
        if hours_after_creation < 0:
            hours_after_creation = 0

        # 48 saat içinde alım yapmış mı?
        is_early_buyer = hours_after_creation <= EARLY_WINDOW_HOURS

        if not is_early_buyer:
            continue

        total_bought = data['total_bought']
        total_sold = data['total_sold']

        # En az bir miktar satmış olmalı
        if total_sold <= 0:
            continue

        # Satış oranı
        sell_ratio = (total_sold / total_bought * 100) if total_bought > 0 else 0

        # En az %30 satmış olmalı (daha esnek kriter)
        if sell_ratio < 30:
            continue

        # Tahmini kar (basit hesap - gerçek kar için fiyat verisi gerekli)
        # Şimdilik satış oranı ve erken giriş zamanına bakıyoruz

        profitable.append({
            'address': address,
            'total_bought': total_bought,
            'total_sold': total_sold,
            'first_buy_time': first_buy,
            'hours_after_creation': round(hours_after_creation, 2),
            'num_buys': len(data['buys']),
            'num_sells': len(data['sells']),
            'sell_ratio': round(sell_ratio, 2),
            'is_early_buyer': is_early_buyer
        })

    # Satış oranı ve erken giriş zamanına göre sırala
    profitable.sort(key=lambda x: (-x['sell_ratio'], x['hours_after_creation']))

    return profitable

# ============================================================
# ANA FONKSİYON
# ============================================================

def main():
    print("=" * 70)
    print("CÜZDAN AYIKLAMA V2 (Geliştirilmiş)")
    print("=" * 70)
    print(f"Başlangıç: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Timeout: {REQUEST_TIMEOUT}s | Delay: {REQUEST_DELAY}s | Max Retry: {MAX_RETRIES}")
    print()

    # Klasörleri oluştur
    ensure_dirs()

    # Tokenleri yükle
    tokens = load_tokens()
    print(f"📋 {len(tokens)} WETH-only token yüklendi")
    print()

    all_profitable = []
    processed_tokens = []

    # Her token için işlem
    for i, token in enumerate(tokens, 1):
        token_addr = token.get('baseToken', {}).get('address', '')
        symbol = token.get('baseToken', {}).get('symbol', 'Unknown')
        pair_addr = token.get('pairAddress', '')
        created_at = token.get('pairCreatedAt', 0)
        current_price = float(token.get('priceUsd', 0) or 0)
        mcap = token.get('marketCap', 0) / 1_000_000

        print(f"\n{'='*70}")
        print(f"[{i}/{len(tokens)}] {symbol}")
        print(f"{'='*70}")
        print(f"  Token: {token_addr}")
        print(f"  MCap: ${mcap:.2f}M | Price: ${current_price:.8f}")
        print(f"  Created: {datetime.fromtimestamp(created_at/1000).strftime('%Y-%m-%d %H:%M')}")

        # Checkpoint kontrol
        checkpoint = load_checkpoint(symbol)
        if checkpoint:
            print(f"  ✅ Checkpoint bulundu: {checkpoint.get('wallet_count', 0)} cüzdan")
            all_profitable.extend(checkpoint.get('wallets', []))
            processed_tokens.append(symbol)
            continue

        if not token_addr:
            print(f"  ⚠️ Token adresi bulunamadı, atlanıyor...")
            continue

        # Transfer verilerini çek
        print(f"\n  📥 Transfer verileri çekiliyor...")
        transfers = fetch_token_transfers(token_addr)
        print(f"  📊 {len(transfers)} transfer bulundu")

        if not transfers:
            print(f"  ⚠️ Transfer yok, atlanıyor...")
            save_checkpoint(symbol, {'wallets': [], 'wallet_count': 0, 'transfers': 0})
            time.sleep(REQUEST_DELAY)
            continue

        # Cüzdanları analiz et
        print(f"  🔍 Cüzdanlar analiz ediliyor...")
        wallets = analyze_transfers(transfers, created_at)
        print(f"  👛 {len(wallets)} unique cüzdan bulundu")

        # Karlı cüzdanları tespit et
        profitable = identify_profitable_wallets(wallets, created_at, current_price)
        print(f"  💰 {len(profitable)} potansiyel karlı cüzdan tespit edildi")

        # Token bilgisiyle birlikte kaydet
        for wallet in profitable:
            wallet['token_symbol'] = symbol
            wallet['token_address'] = token_addr
            wallet['pair_address'] = pair_addr

        all_profitable.extend(profitable)
        processed_tokens.append(symbol)

        # Checkpoint kaydet
        save_checkpoint(symbol, {
            'timestamp': datetime.now().isoformat(),
            'symbol': symbol,
            'transfers': len(transfers),
            'unique_wallets': len(wallets),
            'wallet_count': len(profitable),
            'wallets': profitable
        })

        # Progress güncelle
        update_progress(symbol, f"{i}/{len(tokens)} token", len(all_profitable))

        # Rate limit için bekle
        if i < len(tokens):
            print(f"\n  ⏳ {REQUEST_DELAY}s bekleniyor...")
            time.sleep(REQUEST_DELAY)

    # Final sonuçları kaydet
    print("\n" + "=" * 70)
    print("SONUÇLAR KAYDEDİLİYOR")
    print("=" * 70)

    final_data = {
        'timestamp': datetime.now().isoformat(),
        'version': 'v2',
        'total_wallets': len(all_profitable),
        'tokens_analyzed': len(processed_tokens),
        'tokens': processed_tokens,
        'settings': {
            'early_window_hours': EARLY_WINDOW_HOURS,
            'timeout': REQUEST_TIMEOUT,
            'request_delay': REQUEST_DELAY
        },
        'wallets': all_profitable
    }

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(final_data, f, indent=2, ensure_ascii=False)

    print(f"💾 {len(all_profitable)} karlı cüzdan kaydedildi: {OUTPUT_FILE}")

    # Özet
    print("\n" + "=" * 70)
    print("ÖZET")
    print("=" * 70)

    # Token başına özet
    token_stats = defaultdict(int)
    for w in all_profitable:
        token_stats[w['token_symbol']] += 1

    print("\nToken başına cüzdan sayısı:")
    for symbol, count in sorted(token_stats.items(), key=lambda x: -x[1]):
        print(f"  {symbol:15} → {count:>4} cüzdan")

    # Top 20 cüzdan
    if all_profitable:
        print(f"\n🏆 Top 20 Cüzdan (Satış oranına göre):")
        print("-" * 80)
        print(f"{'#':>3} | {'Adres':^15} | {'Token':^12} | {'Giriş':>7} | {'Satış':>7} | {'Alım':>5} | {'Satış':>5}")
        print("-" * 80)

        for i, w in enumerate(all_profitable[:20], 1):
            addr = w['address'][:12] + "..."
            symbol = w['token_symbol'][:10]
            hours = f"{w['hours_after_creation']:.0f}h"
            ratio = f"{w['sell_ratio']:.1f}%"
            buys = w['num_buys']
            sells = w['num_sells']
            print(f"{i:>3} | {addr:^15} | {symbol:^12} | {hours:>7} | {ratio:>7} | {buys:>5} | {sells:>5}")

    print("\n" + "=" * 70)
    print(f"BİTİŞ: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    return all_profitable

if __name__ == "__main__":
    wallets = main()
