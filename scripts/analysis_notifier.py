"""
ETH P&L Analizi İlerleme Bildirici
Her 15 dakikada bir Telegram'a analiz durumunu gönderir.
"""

import os
import sys
import time
import json
import re
from datetime import datetime

# Config'i import et
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.telegram_alert import send_status_update

# Flush için
sys.stdout.reconfigure(line_buffering=True)

# Dosya yolları
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_FILE = os.path.join(BASE_DIR, "logs", "eth_pnl_analysis.log")
CHECKPOINT_FILE = os.path.join(BASE_DIR, "data", "eth_pnl_checkpoint.json")

def get_analysis_status():
    """Log dosyasından analiz durumunu al."""
    try:
        # Checkpoint'ten ilerleme bilgisi
        if os.path.exists(CHECKPOINT_FILE):
            with open(CHECKPOINT_FILE, 'r') as f:
                checkpoint = json.load(f)
                processed = checkpoint.get('processed_count', 0)
                total = checkpoint.get('total_wallets', 384)
                profitable = checkpoint.get('profitable_count', 0)
                unprofitable = checkpoint.get('unprofitable_count', 0)
        else:
            processed = 0
            total = 384
            profitable = 0
            unprofitable = 0

        # Log dosyasından son satırları oku
        last_wallet = "Bilinmiyor"
        last_pnl = 0

        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, 'r') as f:
                lines = f.readlines()
                # Son işlenen cüzdanı bul
                for line in reversed(lines):
                    match = re.search(r'\[(\d+)/(\d+)\]\s+(0x[a-fA-F0-9]+)', line)
                    if match:
                        processed = int(match.group(1))
                        total = int(match.group(2))
                        last_wallet = match.group(3)[:12] + "..."
                        break

                # Son P&L bilgisini bul
                for line in reversed(lines):
                    match = re.search(r'Net P&L:\s+([+-]?\d+\.?\d*)\s+ETH', line)
                    if match:
                        last_pnl = float(match.group(1))
                        break

        return {
            'processed': processed,
            'total': total,
            'profitable': profitable,
            'unprofitable': unprofitable,
            'last_wallet': last_wallet,
            'last_pnl': last_pnl,
            'percent': round((processed / total) * 100, 1) if total > 0 else 0
        }

    except Exception as e:
        print(f"⚠️ Durum alma hatası: {e}")
        return None

def send_progress_update():
    """Telegram'a ilerleme bildirimi gönder."""
    status = get_analysis_status()

    if not status:
        return False

    # İlerleme çubuğu oluştur
    progress_bar_length = 10
    filled = int((status['processed'] / status['total']) * progress_bar_length)
    progress_bar = "█" * filled + "░" * (progress_bar_length - filled)

    # Tahmini kalan süre (ortalama 3 saniye/cüzdan)
    remaining = status['total'] - status['processed']
    eta_minutes = (remaining * 3) / 60

    message = (
        f"📊 **ETH P&L Analizi - İlerleme**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔄 İlerleme: {status['processed']}/{status['total']} cüzdan\n"
        f"[{progress_bar}] %{status['percent']}\n\n"
        f"✅ Karlı: {status['profitable']} cüzdan\n"
        f"❌ Zararlı: {status['unprofitable']} cüzdan\n\n"
        f"📍 Son işlenen: `{status['last_wallet']}`\n"
        f"💰 Son P&L: {'+' if status['last_pnl'] >= 0 else ''}{status['last_pnl']:.4f} ETH\n\n"
        f"⏱️ Tahmini kalan: ~{eta_minutes:.0f} dakika\n"
        f"🕐 Güncelleme: {datetime.now().strftime('%H:%M:%S')}"
    )

    success = send_status_update(message)

    if success:
        print(f"✅ [{datetime.now().strftime('%H:%M:%S')}] Telegram bildirimi gönderildi - {status['processed']}/{status['total']}")
    else:
        print(f"❌ [{datetime.now().strftime('%H:%M:%S')}] Telegram bildirimi gönderilemedi!")

    return success

def main():
    """Ana döngü - 15 dakikada bir bildirim gönder."""
    print("=" * 50)
    print("📢 ETH Analizi Bildirim Servisi Başlatıldı")
    print("=" * 50)
    print(f"⏱️ Bildirim aralığı: 15 dakika")
    print(f"📂 Log dosyası: {LOG_FILE}")
    print("=" * 50 + "\n")

    # İlk bildirim hemen gönder
    print("📤 İlk bildirim gönderiliyor...")
    send_progress_update()

    # Her 15 dakikada bir bildirim gönder
    interval = 15 * 60  # 15 dakika (saniye)

    while True:
        try:
            time.sleep(interval)

            # Analiz tamamlandı mı kontrol et
            status = get_analysis_status()
            if status and status['processed'] >= status['total']:
                # Son bildirim gönder
                final_message = (
                    f"🎉 **ETH P&L Analizi TAMAMLANDI!**\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"📊 Toplam: {status['total']} cüzdan analiz edildi\n"
                    f"✅ Karlı: {status['profitable']} cüzdan\n"
                    f"❌ Zararlı: {status['unprofitable']} cüzdan\n\n"
                    f"🕐 Tamamlanma: {datetime.now().strftime('%H:%M:%S')}"
                )
                send_status_update(final_message)
                print("\n🎉 Analiz tamamlandı! Bildirim servisi durduruluyor.")
                break

            # Periyodik bildirim
            send_progress_update()

        except KeyboardInterrupt:
            print("\n⏹️ Bildirim servisi durduruldu.")
            break
        except Exception as e:
            print(f"⚠️ Hata: {e}")
            time.sleep(60)  # Hata durumunda 1 dakika bekle

if __name__ == "__main__":
    main()
