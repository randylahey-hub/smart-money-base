"""
Cüzdan Analizi ve Skorlama Scripti (Faz 3-4)

İşlevler:
1. Ortak cüzdanları tespit et (birden fazla tokende görünen)
2. Win rate hesapla (her cüzdan için)
3. Skorlama sistemi uygula
4. Final 500 cüzdanı seç
"""

import json
from datetime import datetime
from collections import defaultdict

# Dosya yolları
DATA_DIR = '/Users/emrecapin/Desktop/smart-money-base/data'
INPUT_FILE = f'{DATA_DIR}/wallets_profitable_v2.json'
OUTPUT_FILE = f'{DATA_DIR}/wallets_analyzed.json'
FINAL_500_FILE = f'{DATA_DIR}/final_500_wallets.json'
RECURRING_FILE = f'{DATA_DIR}/recurring_wallets.json'

def load_wallets():
    """Karlı cüzdanları yükle"""
    with open(INPUT_FILE) as f:
        data = json.load(f)
    return data.get('wallets', [])

def find_recurring_wallets(wallets):
    """
    Birden fazla tokende görünen cüzdanları tespit et
    Bu cüzdanlar "akıllı para" olma ihtimali daha yüksek
    """

    # Her cüzdanın hangi tokenlerde görüldüğünü say
    wallet_tokens = defaultdict(list)

    for w in wallets:
        addr = w['address'].lower()
        token = w['token_symbol']
        wallet_tokens[addr].append({
            'token': token,
            'hours_after_creation': w['hours_after_creation'],
            'sell_ratio': w['sell_ratio'],
            'num_buys': w['num_buys'],
            'num_sells': w['num_sells']
        })

    # Birden fazla tokende görünen cüzdanları filtrele
    recurring = {}
    for addr, tokens in wallet_tokens.items():
        if len(tokens) >= 2:  # En az 2 tokende görünmeli
            recurring[addr] = {
                'address': addr,
                'token_count': len(tokens),
                'tokens': [t['token'] for t in tokens],
                'avg_entry_hours': sum(t['hours_after_creation'] for t in tokens) / len(tokens),
                'avg_sell_ratio': sum(t['sell_ratio'] for t in tokens) / len(tokens),
                'total_buys': sum(t['num_buys'] for t in tokens),
                'total_sells': sum(t['num_sells'] for t in tokens),
                'details': tokens
            }

    return recurring

def calculate_wallet_score(wallet, is_recurring=False, recurring_data=None):
    """
    Cüzdan skoru hesapla

    Skorlama kriterleri:
    - Erken giriş (48 saat içinde): +30 puan
    - Satış oranı (kar realize etme): +25 puan
    - Recurring (birden fazla token): +20 puan
    - İşlem sayısı (aktiflik): +15 puan
    - Tutarlılık: +10 puan
    """

    score = 0
    breakdown = {}

    # 1. Erken Giriş Skoru (0-30 puan)
    hours = wallet.get('hours_after_creation', 48)
    if hours <= 1:
        early_score = 30
    elif hours <= 6:
        early_score = 25
    elif hours <= 12:
        early_score = 20
    elif hours <= 24:
        early_score = 15
    elif hours <= 48:
        early_score = 10
    else:
        early_score = 0
    score += early_score
    breakdown['early_entry'] = early_score

    # 2. Satış Oranı Skoru (0-25 puan)
    sell_ratio = wallet.get('sell_ratio', 0)
    if sell_ratio >= 90:
        sell_score = 25
    elif sell_ratio >= 70:
        sell_score = 20
    elif sell_ratio >= 50:
        sell_score = 15
    elif sell_ratio >= 30:
        sell_score = 10
    else:
        sell_score = 5
    score += sell_score
    breakdown['sell_ratio'] = sell_score

    # 3. Recurring Skoru (0-20 puan)
    if is_recurring and recurring_data:
        token_count = recurring_data.get('token_count', 1)
        if token_count >= 4:
            recurring_score = 20
        elif token_count >= 3:
            recurring_score = 15
        elif token_count >= 2:
            recurring_score = 10
        else:
            recurring_score = 0
    else:
        recurring_score = 0
    score += recurring_score
    breakdown['recurring'] = recurring_score

    # 4. Aktivite Skoru (0-15 puan)
    num_trades = wallet.get('num_buys', 0) + wallet.get('num_sells', 0)
    if num_trades >= 20:
        activity_score = 15
    elif num_trades >= 10:
        activity_score = 12
    elif num_trades >= 5:
        activity_score = 8
    elif num_trades >= 2:
        activity_score = 5
    else:
        activity_score = 2
    score += activity_score
    breakdown['activity'] = activity_score

    # 5. Tutarlılık Skoru (0-10 puan)
    # Sell sayısı / Buy sayısı oranı
    num_buys = wallet.get('num_buys', 1)
    num_sells = wallet.get('num_sells', 0)
    if num_buys > 0:
        consistency = num_sells / num_buys
        if consistency >= 0.8:
            consistency_score = 10
        elif consistency >= 0.5:
            consistency_score = 7
        elif consistency >= 0.3:
            consistency_score = 4
        else:
            consistency_score = 2
    else:
        consistency_score = 0
    score += consistency_score
    breakdown['consistency'] = consistency_score

    return score, breakdown

def analyze_and_score_wallets(wallets):
    """
    Tüm cüzdanları analiz et ve skorla
    """

    print("=" * 70)
    print("CÜZDAN ANALİZİ VE SKORLAMA (Faz 3-4)")
    print("=" * 70)
    print(f"Başlangıç: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Toplam cüzdan: {len(wallets)}")
    print()

    # 1. Recurring cüzdanları tespit et
    print("🔍 Recurring cüzdanlar tespit ediliyor...")
    recurring = find_recurring_wallets(wallets)
    print(f"   {len(recurring)} cüzdan birden fazla tokende görünüyor")

    # Recurring cüzdan detayları
    if recurring:
        print("\n   Top 10 Recurring Cüzdan:")
        sorted_recurring = sorted(recurring.values(), key=lambda x: -x['token_count'])
        for i, r in enumerate(sorted_recurring[:10], 1):
            print(f"   {i}. {r['address'][:12]}... | {r['token_count']} token | {', '.join(r['tokens'])}")

    # 2. Her cüzdanı skorla
    print("\n📊 Cüzdanlar skorlanıyor...")

    scored_wallets = []
    recurring_addresses = set(recurring.keys())

    for wallet in wallets:
        addr = wallet['address'].lower()
        is_recurring = addr in recurring_addresses
        recurring_data = recurring.get(addr, None)

        score, breakdown = calculate_wallet_score(wallet, is_recurring, recurring_data)

        scored_wallet = {
            **wallet,
            'score': score,
            'score_breakdown': breakdown,
            'is_recurring': is_recurring,
            'recurring_token_count': recurring_data['token_count'] if recurring_data else 1
        }

        scored_wallets.append(scored_wallet)

    # Skora göre sırala
    scored_wallets.sort(key=lambda x: -x['score'])

    print(f"   Skorlama tamamlandı")

    # 3. İstatistikler
    print("\n" + "=" * 70)
    print("İSTATİSTİKLER")
    print("=" * 70)

    # Skor dağılımı
    score_ranges = {
        '90+': 0,
        '80-89': 0,
        '70-79': 0,
        '60-69': 0,
        '50-59': 0,
        '<50': 0
    }

    for w in scored_wallets:
        s = w['score']
        if s >= 90:
            score_ranges['90+'] += 1
        elif s >= 80:
            score_ranges['80-89'] += 1
        elif s >= 70:
            score_ranges['70-79'] += 1
        elif s >= 60:
            score_ranges['60-69'] += 1
        elif s >= 50:
            score_ranges['50-59'] += 1
        else:
            score_ranges['<50'] += 1

    print("\nSkor Dağılımı:")
    for range_name, count in score_ranges.items():
        bar = "█" * (count // 20) if count > 0 else ""
        print(f"  {range_name:>8}: {count:>5} {bar}")

    # Token bazında dağılım
    print("\nToken Bazında Cüzdan Sayısı:")
    token_counts = defaultdict(int)
    for w in wallets:
        token_counts[w['token_symbol']] += 1

    for token, count in sorted(token_counts.items(), key=lambda x: -x[1]):
        bar = "█" * (count // 20) if count > 0 else ""
        print(f"  {token:>12}: {count:>5} {bar}")

    # Recurring istatistikleri
    print(f"\nRecurring Cüzdanlar:")
    print(f"  2 tokende: {sum(1 for r in recurring.values() if r['token_count'] == 2)}")
    print(f"  3 tokende: {sum(1 for r in recurring.values() if r['token_count'] == 3)}")
    print(f"  4+ tokende: {sum(1 for r in recurring.values() if r['token_count'] >= 4)}")

    return scored_wallets, recurring

def select_top_500(scored_wallets):
    """
    En iyi 500 cüzdanı seç
    """

    # Zaten skora göre sıralı, ilk 500'ü al
    top_500 = scored_wallets[:500]

    print("\n" + "=" * 70)
    print("TOP 500 CÜZDAN")
    print("=" * 70)

    print("\n🏆 En Yüksek Skorlu 20 Cüzdan:")
    print("-" * 90)
    print(f"{'#':>3} | {'Adres':^15} | {'Token':^12} | {'Skor':>5} | {'Giriş':>7} | {'Satış':>7} | {'Recurring':^9}")
    print("-" * 90)

    for i, w in enumerate(top_500[:20], 1):
        addr = w['address'][:12] + "..."
        token = w['token_symbol'][:10]
        score = w['score']
        hours = f"{w['hours_after_creation']:.0f}h"
        ratio = f"{w['sell_ratio']:.1f}%"
        recurring = "✅" if w['is_recurring'] else "-"
        print(f"{i:>3} | {addr:^15} | {token:^12} | {score:>5} | {hours:>7} | {ratio:>7} | {recurring:^9}")

    # Özet istatistikler
    avg_score = sum(w['score'] for w in top_500) / len(top_500)
    recurring_count = sum(1 for w in top_500 if w['is_recurring'])
    avg_entry = sum(w['hours_after_creation'] for w in top_500) / len(top_500)

    print("\n📈 Top 500 Özeti:")
    print(f"  Ortalama Skor: {avg_score:.1f}")
    print(f"  Recurring Cüzdan: {recurring_count} ({recurring_count/5:.1f}%)")
    print(f"  Ort. Giriş Zamanı: {avg_entry:.1f} saat")

    return top_500

def save_results(scored_wallets, recurring, top_500):
    """
    Sonuçları kaydet
    """

    print("\n" + "=" * 70)
    print("SONUÇLAR KAYDEDİLİYOR")
    print("=" * 70)

    # 1. Tüm skorlanmış cüzdanlar
    all_data = {
        'timestamp': datetime.now().isoformat(),
        'total_wallets': len(scored_wallets),
        'wallets': scored_wallets
    }

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(all_data, f, indent=2, ensure_ascii=False)
    print(f"💾 Tüm cüzdanlar: {OUTPUT_FILE}")

    # 2. Recurring cüzdanlar
    recurring_data = {
        'timestamp': datetime.now().isoformat(),
        'total_recurring': len(recurring),
        'wallets': list(recurring.values())
    }

    with open(RECURRING_FILE, 'w', encoding='utf-8') as f:
        json.dump(recurring_data, f, indent=2, ensure_ascii=False)
    print(f"💾 Recurring cüzdanlar: {RECURRING_FILE}")

    # 3. Top 500
    top_500_data = {
        'timestamp': datetime.now().isoformat(),
        'total': len(top_500),
        'wallets': top_500
    }

    with open(FINAL_500_FILE, 'w', encoding='utf-8') as f:
        json.dump(top_500_data, f, indent=2, ensure_ascii=False)
    print(f"💾 Top 500 cüzdan: {FINAL_500_FILE}")

    # 4. CSV formatında da kaydet
    csv_file = f'{DATA_DIR}/final_500_wallets.csv'
    with open(csv_file, 'w') as f:
        # Header
        f.write("rank,address,token,score,hours_after_creation,sell_ratio,num_buys,num_sells,is_recurring,recurring_tokens\n")

        # Data
        for i, w in enumerate(top_500, 1):
            recurring_tokens = w.get('recurring_token_count', 1)
            f.write(f"{i},{w['address']},{w['token_symbol']},{w['score']},{w['hours_after_creation']},{w['sell_ratio']},{w['num_buys']},{w['num_sells']},{w['is_recurring']},{recurring_tokens}\n")

    print(f"💾 CSV export: {csv_file}")

def main():
    # Cüzdanları yükle
    wallets = load_wallets()

    if not wallets:
        print("❌ Cüzdan verisi bulunamadı!")
        return

    # Analiz et ve skorla
    scored_wallets, recurring = analyze_and_score_wallets(wallets)

    # Top 500 seç
    top_500 = select_top_500(scored_wallets)

    # Kaydet
    save_results(scored_wallets, recurring, top_500)

    print("\n" + "=" * 70)
    print(f"BİTİŞ: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    return top_500

if __name__ == "__main__":
    result = main()
