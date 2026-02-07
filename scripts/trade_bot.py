"""
Trade Bot - Alert Bot'tan bağımsız çalışan gerçek trading botu.
PostgreSQL üzerinden trade sinyallerini okur ve Uniswap V3 ile işlem yapar.

Alert Bot (wallet_monitor.py) → DB'ye trade_signal yazar
Trade Bot (bu dosya) → DB'den sinyal okur → buy/sell yapar → TP/SL izler

Koyeb'de ayrı Worker servis olarak çalışır.
"""

import asyncio
import sys
import os
from datetime import datetime, timezone, timedelta

# Config'i import et
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import REAL_TRADING_ENABLED, DATABASE_URL
from scripts.database import (
    init_db,
    is_db_available,
    get_pending_signals,
    update_signal_status,
    expire_old_signals,
    is_duplicate_signal
)
from scripts.telegram_alert import send_status_update, send_error_alert
from scripts.real_trade_config import (
    REAL_TRADE_SIZE_ETH,
    MAX_OPEN_POSITIONS,
    MAX_TOTAL_EXPOSURE_ETH,
    MAX_DAILY_LOSS_ETH,
    POSITION_CHECK_INTERVAL
)

# Flush için
sys.stdout.reconfigure(line_buffering=True)

# Signal polling interval (saniye)
SIGNAL_POLL_INTERVAL = 5

# Eski sinyalleri expire etme süresi (saniye)
SIGNAL_EXPIRY_SECONDS = 300  # 5 dakika


async def poll_trade_signals(trader):
    """
    DB'den pending trade sinyallerini okur ve işlem yapar.
    Her 5 saniyede bir kontrol eder.
    """
    print("📡 Signal poller başladı (her 5 saniye)...")
    processed_count = 0
    skipped_count = 0

    while True:
        try:
            # Önce eski sinyalleri expire et
            expired = expire_old_signals(SIGNAL_EXPIRY_SECONDS)

            # Pending sinyalleri al
            signals = get_pending_signals(max_age_seconds=SIGNAL_EXPIRY_SECONDS)

            for signal in signals:
                signal_id = signal['id']
                token_address = signal['token_address']
                token_symbol = signal['token_symbol']
                entry_mcap = signal['entry_mcap']
                trigger_type = signal['trigger_type']

                print(f"\n📡 Yeni sinyal: {token_symbol} ({trigger_type}) | MCap: ${entry_mcap/1e3:.0f}K")

                # Kill switch kontrolü
                if not REAL_TRADING_ENABLED:
                    update_signal_status(signal_id, 'skipped', {"reason": "trading_disabled"})
                    print(f"⏭️  Skip: Real trading kapalı")
                    skipped_count += 1
                    continue

                # Processing olarak işaretle
                update_signal_status(signal_id, 'processing')

                # Buy işlemi
                try:
                    result = trader.buy_token(
                        token_address=token_address,
                        token_symbol=token_symbol,
                        entry_mcap=entry_mcap
                    )

                    if result:
                        trade_result = {
                            "buy_tx": result.get('buy_tx', ''),
                            "eth_spent": result.get('eth_spent', 0),
                            "token_amount": result.get('amount', 0),
                            "entry_price": result.get('entry_price', 0),
                            "fee_tier": result.get('fee_tier', 0),
                            "timestamp": datetime.now(timezone.utc).isoformat()
                        }
                        update_signal_status(signal_id, 'executed', trade_result)
                        processed_count += 1
                        print(f"✅ Trade executed: {token_symbol} | {result.get('eth_spent', 0):.4f} ETH")
                    else:
                        update_signal_status(signal_id, 'failed', {"reason": "buy_returned_none"})
                        print(f"❌ Trade failed: {token_symbol} | buy_token returned None")

                except Exception as e:
                    update_signal_status(signal_id, 'failed', {"reason": str(e)})
                    print(f"❌ Trade error: {token_symbol} | {e}")
                    send_error_alert(f"Trade bot error: {token_symbol} - {e}")

        except Exception as e:
            print(f"⚠️ Signal poller hatası: {e}")

        await asyncio.sleep(SIGNAL_POLL_INTERVAL)


async def main():
    """Ana fonksiyon — signal poller + position monitor paralel çalışır."""
    print("\n" + "=" * 60)
    print("🤖 TRADE BOT BAŞLATILIYOR")
    print("=" * 60)
    print(f"💎 Real Trading: {'AKTİF' if REAL_TRADING_ENABLED else 'KAPALI'}")
    print(f"💰 Trade boyutu: {REAL_TRADE_SIZE_ETH} ETH")
    print(f"📊 Max pozisyon: {MAX_OPEN_POSITIONS}")
    print(f"💼 Max exposure: {MAX_TOTAL_EXPOSURE_ETH} ETH")
    print(f"🛑 Günlük kayıp limiti: {MAX_DAILY_LOSS_ETH} ETH")
    print(f"⏱️  Signal poll: her {SIGNAL_POLL_INTERVAL}sn")
    print(f"⏱️  Position check: her {POSITION_CHECK_INTERVAL}sn")
    print(f"⏰ Signal expiry: {SIGNAL_EXPIRY_SECONDS}sn")
    print(f"🗄️  Database: {'Bağlı' if DATABASE_URL else 'YOK'}")
    print("=" * 60 + "\n")

    # Database başlat
    if not is_db_available():
        print("❌ Database bağlantısı yok! Trade bot çalışamaz.")
        print("DATABASE_URL environment variable'ı ayarlayın.")
        return

    init_db()
    print("✅ Database tabloları hazır")

    # Real Trader başlat
    if not REAL_TRADING_ENABLED:
        print("⚠️  Real trading KAPALI — sinyaller alınacak ama işlem yapılmayacak")
        print("   Aktif etmek için: REAL_TRADING_ENABLED=true")

    trader = None
    if REAL_TRADING_ENABLED:
        try:
            from scripts.real_trader import get_real_trader
            trader = get_real_trader()
            print(f"✅ Real trader hazır")
        except Exception as e:
            print(f"❌ Real trader başlatılamadı: {e}")
            send_error_alert(f"Trade bot: Real trader başlatılamadı: {e}")
            return
    else:
        # Trader None olsa bile signal poller çalışsın (skip modunda)
        pass

    # Telegram bildirimi
    trading_status = "AKTİF" if REAL_TRADING_ENABLED else "KAPALI (izleme modu)"
    send_status_update(
        f"🤖 Trade Bot başlatıldı!\n"
        f"• Real Trading: {trading_status}\n"
        f"• Trade boyutu: {REAL_TRADE_SIZE_ETH} ETH\n"
        f"• Max pozisyon: {MAX_OPEN_POSITIONS}\n"
        f"• Signal poll: {SIGNAL_POLL_INTERVAL}sn\n"
        f"• Signal expiry: {SIGNAL_EXPIRY_SECONDS//60}dk"
    )

    # Async tasks paralel çalıştır
    tasks = [poll_trade_signals(trader)]

    # Position monitor sadece trading aktifse
    if REAL_TRADING_ENABLED and trader:
        tasks.append(trader.monitor_positions())
        print("📊 Pozisyon monitörü başlatıldı")

    print("🚀 Trade bot çalışıyor...\n")

    try:
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        print("\n⏹️ Trade bot durduruldu.")
        send_status_update("🔴 Trade Bot durduruldu.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Çıkış yapılıyor...")
