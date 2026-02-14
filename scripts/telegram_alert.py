"""
Telegram Alert Modülü
Smart money cüzdanları aynı tokeni aldığında bildirim gönderir.
"""

import requests
import sys
import os
from datetime import datetime, timezone, timedelta

# Config'i import et
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID


def send_telegram_message(message: str, parse_mode: str = "HTML") -> bool:
    """
    Telegram grubuna mesaj gönderir.

    Args:
        message: Gönderilecek mesaj (HTML formatı desteklenir)
        parse_mode: Mesaj formatı (HTML veya Markdown)

    Returns:
        bool: Başarılı ise True
    """
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": parse_mode,
        "disable_web_page_preview": False
    }

    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"Telegram mesaj hatası: {e}")
        return False


def format_number(num: float) -> str:
    """Sayıyı okunabilir formata çevir (1.5M, 500K gibi)."""
    if num >= 1_000_000:
        return f"${num/1_000_000:.2f}M"
    elif num >= 1_000:
        return f"${num/1_000:.1f}K"
    else:
        return f"${num:.2f}"


def _select_best_pair(pairs: list) -> dict:
    """
    DexScreener pair listesinden en güvenilir pair'i seç.

    Strateji:
    1. Base chain pair'lerini filtrele
    2. WETH/ETH quote pair'lerini tercih et (en doğru MCap)
    3. Minimum $500 likidite eşiği (dust pair'leri ele)
    4. Kalan pair'ler arasından en yüksek likiditeyi seç
    5. MCap: Seçilen pair'in MCap'i, tüm kaliteli pair'lerdeki max MCap ile karşılaştırılır
    """
    if not pairs:
        return None

    # Base chain pair'lerini filtrele
    base_pairs = [p for p in pairs if p.get('chainId') == 'base']
    if not base_pairs:
        base_pairs = pairs  # Base yoksa tümünü kullan

    # Likidite $500+ olan pair'ler (dust pair'leri ele)
    MIN_PAIR_LIQUIDITY = 500
    quality_pairs = [
        p for p in base_pairs
        if float(p.get('liquidity', {}).get('usd', 0) or 0) >= MIN_PAIR_LIQUIDITY
    ]
    if not quality_pairs:
        quality_pairs = base_pairs  # Kaliteli yoksa tümünü kullan

    # WETH/ETH quote pair'lerini tercih et
    WETH_SYMBOLS = {'WETH', 'ETH'}
    weth_pairs = [
        p for p in quality_pairs
        if p.get('quoteToken', {}).get('symbol', '').upper() in WETH_SYMBOLS
    ]

    # WETH pair varsa onlar arasından, yoksa tüm kaliteli pair'ler arasından seç
    candidate_pairs = weth_pairs if weth_pairs else quality_pairs

    # En yüksek likiditeye sahip pair'i seç (ana veri kaynağı)
    best_pair = max(candidate_pairs, key=lambda x: float(x.get('liquidity', {}).get('usd', 0) or 0))

    # MCap doğrulama: Tüm kaliteli pair'lerdeki en yüksek MCap'i bul
    # DexScreener bazen gecikmeli güncelliyor, en güncel MCap genelde en yüksek olan
    best_mcap = float(best_pair.get('marketCap', 0) or best_pair.get('fdv', 0) or 0)
    for p in quality_pairs:
        p_mcap = float(p.get('marketCap', 0) or p.get('fdv', 0) or 0)
        p_liq = float(p.get('liquidity', {}).get('usd', 0) or 0)
        # Sadece anlamlı likiditeye sahip pair'lerin MCap'ini dikkate al
        if p_liq >= MIN_PAIR_LIQUIDITY and p_mcap > best_mcap:
            best_mcap = p_mcap

    # Best pair'in MCap'ini en yüksek kaliteli değerle güncelle
    best_pair = dict(best_pair)  # Kopyala (orijinali bozma)
    best_pair['_corrected_mcap'] = best_mcap

    return best_pair


def get_token_info_dexscreener(token_address: str) -> dict:
    """
    DEXScreener API'den token bilgisi al.
    Birden fazla pair varsa en güvenilir olanı seçer.

    Returns:
        dict: {symbol, name, mcap, price, liquidity}
    """
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
        response = requests.get(url, timeout=10)
        data = response.json()

        if data.get('pairs') and len(data['pairs']) > 0:
            pair = _select_best_pair(data['pairs'])
            if pair:
                # _corrected_mcap varsa onu kullan (multi-pair doğrulama)
                mcap = pair.get('_corrected_mcap', 0) or float(pair.get('marketCap', 0) or pair.get('fdv', 0) or 0)
                return {
                    'symbol': pair.get('baseToken', {}).get('symbol', 'UNKNOWN'),
                    'name': pair.get('baseToken', {}).get('name', 'Unknown Token'),
                    'mcap': mcap,
                    'price': float(pair.get('priceUsd', 0) or 0),
                    'liquidity': float(pair.get('liquidity', {}).get('usd', 0) or 0),
                    'price_change_24h': float(pair.get('priceChange', {}).get('h24', 0) or 0),
                    'volume_24h': float(pair.get('volume', {}).get('h24', 0) or 0),
                    'txns_24h_buys': int(pair.get('txns', {}).get('h24', {}).get('buys', 0) or 0),
                    'txns_24h_sells': int(pair.get('txns', {}).get('h24', {}).get('sells', 0) or 0),
                    'pair_address': pair.get('pairAddress', '')
                }
    except Exception as e:
        print(f"DEXScreener API hatası: {e}")

    return {
        'symbol': 'UNKNOWN',
        'name': 'Unknown Token',
        'mcap': 0,
        'price': 0,
        'liquidity': 0,
        'price_change_24h': 0,
        'volume_24h': 0,
        'txns_24h_buys': 0,
        'txns_24h_sells': 0,
        'pair_address': ''
    }


def send_smart_money_alert(
    token_address: str,
    wallet_purchases: list,  # [(wallet_address, eth_amount, buy_mcap), ...]
    first_buy_time: str,
    current_mcap: float = None,
    token_info: dict = None,
    is_bullish: bool = False,
    alert_count: int = 1,
    first_alert_mcap: float = 0
) -> bool:
    """
    Smart money alert mesajı gönderir (gelişmiş versiyon).

    Args:
        token_address: Token contract adresi
        wallet_purchases: [(wallet_address, eth_amount, buy_mcap), ...] listesi
        first_buy_time: İlk alım zamanı
        current_mcap: Şu anki market cap (opsiyonel, yoksa API'den alınır)
        token_info: Token bilgileri dict (opsiyonel, yoksa API'den alınır)

    Returns:
        bool: Başarılı ise True
    """

    # Token bilgisi yoksa API'den al
    if token_info is None:
        token_info = get_token_info_dexscreener(token_address)

    if current_mcap is None:
        current_mcap = token_info.get('mcap', 0)

    wallet_count = len(wallet_purchases)
    token_symbol = token_info.get('symbol', 'UNKNOWN')
    token_name = token_info.get('name', 'Unknown Token')
    liquidity = token_info.get('liquidity', 0)
    price_change = token_info.get('price_change_24h', 0)
    volume_24h = token_info.get('volume_24h', 0)

    # Toplam ETH alım tutarı
    total_eth = sum([p[1] for p in wallet_purchases if len(p) > 1])

    # Cüzdan listesi (ETH miktarı ile)
    wallet_lines = []
    for i, purchase in enumerate(wallet_purchases[:5]):
        wallet = purchase[0]
        eth_amount = purchase[1] if len(purchase) > 1 else 0
        buy_mcap = purchase[2] if len(purchase) > 2 else 0

        line = f"  • <code>{wallet[:8]}...{wallet[-4:]}</code>"
        details = []
        if eth_amount > 0:
            details.append(f"<b>{eth_amount:.3f} ETH</b>")
        if buy_mcap > 0:
            details.append(f"MCap: {format_number(buy_mcap)}")
        if details:
            line += f" | {' | '.join(details)}"
        wallet_lines.append(line)

    wallet_list = "\n".join(wallet_lines)
    if wallet_count > 5:
        wallet_list += f"\n  • ... ve {wallet_count - 5} cüzdan daha"

    # MCap değişimi
    mcap_info = ""
    if current_mcap > 0:
        mcap_info = f"\n💰 <b>Şu anki MCap:</b> {format_number(current_mcap)}"
        if liquidity > 0:
            mcap_info += f"\n💧 <b>Likidite:</b> {format_number(liquidity)}"
        if volume_24h > 0:
            mcap_info += f"\n📊 <b>24s Hacim:</b> {format_number(volume_24h)}"
        if price_change != 0:
            emoji = "📈" if price_change > 0 else "📉"
            mcap_info += f"\n{emoji} <b>24s Değişim:</b> {price_change:+.1f}%"

    # === BULLISH HEADER ===
    if is_bullish and first_alert_mcap > 0:
        mcap_change_pct = ((current_mcap - first_alert_mcap) / first_alert_mcap * 100) if first_alert_mcap > 0 else 0
        bullish_header = f"""🔥🔥 <b>BULLISH ALERT!</b> 🔥🔥

🔁 <b>{alert_count}. alert</b> (30dk içinde)
📈 <b>İlk alert MCap:</b> {format_number(first_alert_mcap)} → Şimdi: {format_number(current_mcap)} ({mcap_change_pct:+.0f}%)
"""
    elif is_bullish:
        bullish_header = f"""🔥🔥 <b>BULLISH ALERT!</b> 🔥🔥

🔁 <b>{alert_count}. alert</b> (30dk içinde)
"""
    else:
        bullish_header = "🚨 <b>SMART MONEY ALERT!</b> 🚨"

    message = f"""
{bullish_header}

📊 <b>Token:</b> {token_symbol}
📛 <b>Ad:</b> {token_name}
📍 <b>Contract:</b>
<code>{token_address}</code>
{mcap_info}

👛 <b>Alım Yapan Cüzdanlar ({wallet_count}):</b>
{wallet_list}

💵 <b>Toplam Alım:</b> {total_eth:.3f} ETH
⏰ <b>Tespit Zamanı:</b> {first_buy_time}

🔗 <b>Linkler:</b>
• <a href="https://dexscreener.com/base/{token_address}">DEXScreener</a>
• <a href="https://basescan.org/token/{token_address}">BaseScan</a>
• <a href="https://www.dextools.io/app/en/base/pair-explorer/{token_address}">DexTools</a>

⚡️ <b>{wallet_count} smart money cüzdanı 20 saniye içinde aynı tokeni aldı!</b>
"""

    return send_telegram_message(message.strip())


def send_status_update(status: str) -> bool:
    """
    Durum güncellemesi gönderir.
    """
    message = f"ℹ️ <b>Durum:</b> {status}"
    return send_telegram_message(message)


def send_error_alert(error: str) -> bool:
    """
    Hata bildirimi gönderir.
    """
    message = f"⚠️ <b>Hata:</b>\n<code>{error}</code>"
    return send_telegram_message(message)


# Test fonksiyonu
if __name__ == "__main__":
    print("Telegram Alert Test (Gelişmiş)")
    print("=" * 50)

    # Gerçek bir token ile test (MOLT - Base chain'de)
    test_token = "0x39e6EED85927e08af90cBBF9467EF5Ef06263798"  # MOLT

    print(f"\n📡 Token bilgisi alınıyor: {test_token[:10]}...")
    token_info = get_token_info_dexscreener(test_token)
    print(f"  Symbol: {token_info['symbol']}")
    print(f"  Name: {token_info['name']}")
    print(f"  MCap: {format_number(token_info['mcap'])}")
    print(f"  Liquidity: {format_number(token_info['liquidity'])}")

    # Alert test (gerçek format)
    print("\n🚨 Alert testi yapılıyor...")
    alert_success = send_smart_money_alert(
        token_address=test_token,
        wallet_purchases=[
            ("0xc51b211fe1f47982b27a35ad56de634d7391c206", 2.5, 1500000),
            ("0xb878a06dde8e7e8dbc61d94c89b7bc9ad6b9183d", 1.8, 1520000),
            ("0x6c8c3784151932a06f32606f99a49b76ab7b8905", 3.2, 1510000),
        ],
        first_buy_time="şimdi",
        token_info=token_info
    )

    if alert_success:
        print("✅ Alert testi başarılı! Telegram grubunu kontrol et.")
    else:
        print("❌ Alert testi başarısız!")
