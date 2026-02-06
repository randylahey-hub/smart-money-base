"""
Telegram Alert Modülü
Smart money cüzdanları aynı tokeni aldığında bildirim gönderir.
"""

import requests
import sys
import os

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


def get_token_info_dexscreener(token_address: str) -> dict:
    """
    DEXScreener API'den token bilgisi al.

    Returns:
        dict: {symbol, name, mcap, price, liquidity}
    """
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
        response = requests.get(url, timeout=10)
        data = response.json()

        if data.get('pairs') and len(data['pairs']) > 0:
            # En yüksek likiditeye sahip pair'i seç
            pair = max(data['pairs'], key=lambda x: float(x.get('liquidity', {}).get('usd', 0) or 0))
            return {
                'symbol': pair.get('baseToken', {}).get('symbol', 'UNKNOWN'),
                'name': pair.get('baseToken', {}).get('name', 'Unknown Token'),
                'mcap': float(pair.get('marketCap', 0) or 0),
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
    token_info: dict = None
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
        if eth_amount > 0:
            line += f" | <b>{eth_amount:.3f} ETH</b>"
        if buy_mcap > 0:
            line += f" @ {format_number(buy_mcap)}"
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

    message = f"""
🚨 <b>SMART MONEY ALERT!</b> 🚨

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
