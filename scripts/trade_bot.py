"""
Trade Bot v2 — Strateji Bazlı Trading Bot

Alert Bot (wallet_monitor.py) → DB'ye trade_signal yazar
Trade Bot (bu dosya) → DB'den sinyal okur → strateji kurallarına göre işlem yapar

Strateji Modları:
- confirmation_sniper: Sadece 'approved' sinyalleri işler (5dk MCap check geçmiş)
- speed_demon: 'pending' sinyalleri anında işler (mevcut davranış)

Koyeb'de ayrı Worker servis olarak çalışır.
"""

import asyncio
import sys
import os
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import (
    REAL_TRADING_ENABLED, DATABASE_URL,
    ACTIVE_STRATEGY, get_active_strategy_config
)
from scripts.database import (
    init_db,
    is_db_available,
    get_pending_signals,
    get_approved_signals,
    update_signal_status,
    expire_old_signals,
)
from scripts.telegram_alert import send_status_update, send_error_alert
from scripts.real_trade_config import POSITION_CHECK_INTERVAL
from scripts.virtual_trader import get_trader as get_virtual_trader

sys.stdout.reconfigure(line_buffering=True)

# Signal polling interval (saniye)
SIGNAL_POLL_INTERVAL = 5

# Virtual trader TP/SL check interval (saniye)
VIRTUAL_CHECK_INTERVAL = 30


async def poll_trade_signals(real_trader):
    """
    DB'den sinyal okur ve strateji bazlı işlem yapar.

    confirmation_sniper: status='approved' sinyalleri (5dk MCap check geçmiş)
    speed_demon: status='pending' sinyalleri (anında)
    """
    strategy_config = get_active_strategy_config()
    strategy_name = strategy_config["name"]
    v_trader = get_virtual_trader()

    print(f"📡 Signal poller başladı | Strateji: {strategy_name} | Her {SIGNAL_POLL_INTERVAL}sn...")

    processed_count = 0
    skipped_count = 0

    while True:
        try:
            # Eski sinyalleri expire et
            expire_old_signals(300)

            # Aktif stratejiye göre sinyal al
            if ACTIVE_STRATEGY == "confirmation_sniper":
                signals = get_approved_signals(max_age_seconds=600)
            else:  # speed_demon
                signals = get_pending_signals(max_age_seconds=300)

            for signal in signals:
                signal_id = signal['id']
                token_address = signal['token_address']
                token_symbol = signal['token_symbol']
                entry_mcap = signal['entry_mcap']
                trigger_type = signal['trigger_type']
                wallet_count = signal.get('wallet_count', 3)

                print(f"\n📡 Sinyal: {token_symbol} ({trigger_type}) | MCap: ${entry_mcap/1e3:.0f}K | Strateji: {strategy_name}")

                # === VIRTUAL TRADING (her zaman aktif) ===
                try:
                    # Scenario 2 (Speed Demon) her zaman anında alır
                    v_trader.buy_token(2, token_address, token_symbol, entry_mcap, wallet_count)

                    # Scenario 1 (Confirmation Sniper) sadece approved sinyallerde alır
                    if ACTIVE_STRATEGY == "confirmation_sniper":
                        # Bu zaten approved sinyal — trade_result'tan 5dk verisini al
                        trade_result = signal.get('trade_result') or {}
                        change_5min = trade_result.get('change_5min_pct')
                        v_trader.buy_token(1, token_address, token_symbol, entry_mcap,
                                          wallet_count, change_5min_pct=change_5min)
                except Exception as e:
                    print(f"⚠️ Virtual trade hatası: {e}")

                # === REAL TRADING ===
                if not REAL_TRADING_ENABLED:
                    update_signal_status(signal_id, 'skipped', {"reason": "trading_disabled"})
                    skipped_count += 1
                    continue

                # Processing olarak işaretle
                update_signal_status(signal_id, 'processing')

                try:
                    result = real_trader.buy_token(
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
                            "strategy": strategy_name,
                            "timestamp": datetime.now(timezone.utc).isoformat()
                        }
                        update_signal_status(signal_id, 'executed', trade_result)
                        processed_count += 1
                        print(f"✅ Trade executed: {token_symbol} | {result.get('eth_spent', 0):.4f} ETH")
                    else:
                        update_signal_status(signal_id, 'failed', {"reason": "buy_returned_none"})
                        print(f"❌ Trade failed: {token_symbol}")

                except Exception as e:
                    update_signal_status(signal_id, 'failed', {"reason": str(e)})
                    print(f"❌ Trade error: {token_symbol} | {e}")
                    send_error_alert(f"Trade bot error: {token_symbol} - {e}")

        except Exception as e:
            print(f"⚠️ Signal poller hatası: {e}")

        await asyncio.sleep(SIGNAL_POLL_INTERVAL)


async def virtual_tp_sl_monitor():
    """Virtual trader pozisyonlarını periyodik olarak kontrol et (TP/SL)."""
    print(f"📊 Virtual TP/SL monitor başladı (her {VIRTUAL_CHECK_INTERVAL}sn)...")

    while True:
        try:
            v_trader = get_virtual_trader()
            v_trader.check_tp_sl()
        except Exception as e:
            print(f"⚠️ Virtual TP/SL check hatası: {e}")

        await asyncio.sleep(VIRTUAL_CHECK_INTERVAL)


async def main():
    """Ana fonksiyon — signal poller + virtual TP/SL + real position monitor paralel."""
    strategy_config = get_active_strategy_config()

    print("\n" + "=" * 60)
    print("🤖 TRADE BOT v2 BAŞLATILIYOR")
    print("=" * 60)
    print(f"🎯 Aktif Strateji: {strategy_config['name']}")
    print(f"💎 Real Trading: {'AKTİF' if REAL_TRADING_ENABLED else 'KAPALI'}")
    print(f"💰 Trade boyutu: {strategy_config['trade_size_eth']} ETH")
    print(f"📊 Max pozisyon: {strategy_config['max_positions']}")
    print(f"💼 Max exposure: {strategy_config['max_exposure_eth']} ETH")
    print(f"🛑 Günlük kayıp limiti: {strategy_config['max_daily_loss_eth']} ETH")
    tp_str = ", ".join(f"{tp['multiplier']}x→%{tp['sell_pct']}" for tp in strategy_config['tp_levels'])
    print(f"📈 TP seviyeleri: [{tp_str}]")
    print(f"📉 SL: {strategy_config['sl_multiplier']}x | Zaman SL: {strategy_config.get('time_sl_minutes', 30)}dk")
    if ACTIVE_STRATEGY == "confirmation_sniper":
        print(f"⏱️  5dk MCap filtre: +{strategy_config['min_5min_change_pct']}%")
        print(f"🕐 Aktif saatler: {strategy_config['active_hours'][0]}:00-{strategy_config['active_hours'][1]}:00 UTC+3")
    print(f"🗄️  Database: {'Bağlı' if DATABASE_URL else 'YOK'}")
    print("=" * 60 + "\n")

    # Database başlat
    if not is_db_available():
        print("❌ Database bağlantısı yok! Trade bot çalışamaz.")
        return

    init_db()

    # Real Trader (opsiyonel)
    real_trader = None
    if REAL_TRADING_ENABLED:
        try:
            from scripts.real_trader import get_real_trader
            real_trader = get_real_trader()
            print(f"✅ Real trader hazır")
        except Exception as e:
            print(f"❌ Real trader başlatılamadı: {e}")
            send_error_alert(f"Trade bot: Real trader başlatılamadı: {e}")
            return

    # Telegram bildirimi
    send_status_update(
        f"🤖 Trade Bot v2 başlatıldı!\n"
        f"• Strateji: {strategy_config['name']}\n"
        f"• Real Trading: {'AKTİF' if REAL_TRADING_ENABLED else 'KAPALI (paper trade)'}\n"
        f"• Trade: {strategy_config['trade_size_eth']} ETH | Max: {strategy_config['max_positions']} poz\n"
        f"• TP: {strategy_config['tp_levels'][0]['multiplier']}x/{strategy_config['tp_levels'][1]['multiplier']}x/{strategy_config['tp_levels'][2]['multiplier']}x | SL: {strategy_config['sl_multiplier']}x"
    )

    # Async tasks
    tasks = [
        poll_trade_signals(real_trader),
        virtual_tp_sl_monitor(),
    ]

    if REAL_TRADING_ENABLED and real_trader:
        tasks.append(real_trader.monitor_positions())
        print("📊 Real pozisyon monitörü başlatıldı")

    print("🚀 Trade bot v2 çalışıyor...\n")

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
