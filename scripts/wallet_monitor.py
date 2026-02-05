"""
Smart Money Wallet Monitor
Base chain üzerinde smart money cüzdanlarını real-time izler.
2+ cüzdan 20 saniye içinde aynı tokeni alırsa alert gönderir.
"""

import asyncio
import json
import sys
import os
import time
from datetime import datetime
from collections import defaultdict
from web3 import Web3

# Config'i import et
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import (
    BASE_RPC_WSS,
    BASE_RPC_HTTP,
    ALERT_THRESHOLD,
    TIME_WINDOW,
    ALERT_COOLDOWN,
    MAX_MCAP,
    WETH_ADDRESS,
    TRANSFER_EVENT_SIGNATURE,
    EXCLUDED_TOKENS,
    EXCLUDED_SYMBOLS
)
from scripts.telegram_alert import (
    send_smart_money_alert,
    send_status_update,
    send_error_alert,
    get_token_info_dexscreener
)
from scripts.early_detector import (
    process_alert_for_early_detection,
    get_smartest_wallet_addresses,
    is_smartest_wallet
)
from scripts.virtual_trader import get_trader
from scripts.daily_report import check_and_send_if_time

# Flush için
sys.stdout.reconfigure(line_buffering=True)


class SmartMoneyMonitor:
    """Smart money cüzdanlarını izleyen ana sınıf."""

    def __init__(self, wallets_file: str):
        """
        Args:
            wallets_file: İzlenecek cüzdanların JSON dosyası
        """
        self.wallets = self._load_wallets(wallets_file)
        self.wallets_set = set(w.lower() for w in self.wallets)
        print(f"📋 {len(self.wallets)} cüzdan yüklendi")

        # Token alımlarını takip et: {token_address: [(wallet, eth_amount, mcap, timestamp), ...]}
        self.token_purchases = defaultdict(list)

        # Son alert zamanları: {token_address: timestamp}
        self.last_alerts = {}

        # Web3 bağlantısı
        self.w3 = Web3(Web3.HTTPProvider(BASE_RPC_HTTP))
        if self.w3.is_connected():
            print(f"✅ Base chain'e bağlandı (HTTP)")
            print(f"📦 Güncel blok: {self.w3.eth.block_number}")
        else:
            print(f"❌ Base chain bağlantısı başarısız!")

    def _load_wallets(self, wallets_file: str) -> list:
        """Cüzdan listesini yükle."""
        try:
            with open(wallets_file, 'r') as f:
                data = json.load(f)

            # Farklı formatları destekle
            if isinstance(data, list):
                # Direkt liste ise
                if isinstance(data[0], str):
                    return data
                elif isinstance(data[0], dict) and 'address' in data[0]:
                    return [w['address'] for w in data]

            elif isinstance(data, dict):
                # wallets key'i varsa (smart_money_final.json formatı)
                if 'wallets' in data:
                    wallets = data['wallets']
                    if isinstance(wallets[0], str):
                        return wallets
                    elif isinstance(wallets[0], dict):
                        return [w['address'] for w in wallets]
                # profitable veya all key'i varsa
                elif 'profitable' in data:
                    return [w['address'] for w in data['profitable']]
                elif 'all' in data:
                    return [w['address'] for w in data['all'] if w.get('is_profitable', False)]

            return []

        except Exception as e:
            print(f"❌ Cüzdan dosyası yüklenemedi: {e}")
            return []

    def _clean_old_purchases(self):
        """TIME_WINDOW'dan eski alımları temizle."""
        current_time = time.time()
        for token in list(self.token_purchases.keys()):
            self.token_purchases[token] = [
                p for p in self.token_purchases[token]
                if current_time - p[3] < TIME_WINDOW  # p[3] = timestamp
            ]
            if not self.token_purchases[token]:
                del self.token_purchases[token]

    def _can_send_alert(self, token_address: str) -> bool:
        """Alert cooldown kontrolü."""
        if token_address not in self.last_alerts:
            return True
        return time.time() - self.last_alerts[token_address] > ALERT_COOLDOWN

    def _get_eth_value_from_tx(self, tx_hash: str, wallet: str) -> float:
        """Transaction'dan ETH değerini al."""
        try:
            tx = self.w3.eth.get_transaction(tx_hash)
            if tx and tx['from'].lower() == wallet.lower():
                return float(self.w3.from_wei(tx['value'], 'ether'))
        except:
            pass
        return 0.0

    def _estimate_eth_from_transfer(self, log: dict) -> float:
        """
        Transfer log'undan tahmini ETH değeri hesapla.
        Token miktarını token fiyatıyla çarparak yaklaşık ETH değeri bulur.
        """
        try:
            # Token miktarını al (data field'ından)
            if log.get('data') and log['data'] != '0x':
                token_amount = int(log['data'], 16)
                # Basit bir tahmin: 18 decimal varsayımı
                token_amount_normalized = token_amount / (10 ** 18)

                # Token fiyatı için DEXScreener'dan al
                token_address = log['address']
                token_info = get_token_info_dexscreener(token_address)
                price_usd = token_info.get('price', 0)

                if price_usd > 0:
                    # ETH fiyatı (yaklaşık $2500)
                    eth_price = 2500
                    usd_value = token_amount_normalized * price_usd
                    return usd_value / eth_price

        except Exception as e:
            pass

        return 0.0

    def process_transfer(self, log: dict):
        """
        ERC-20 Transfer event'ini işle.
        Token alımı tespit edilirse tracking'e ekle.
        """
        try:
            # Transfer event'i decode et
            token_address = log['address'].lower()
            topics = log['topics']

            if len(topics) < 3:
                return

            # from ve to adreslerini çıkar
            from_address = '0x' + topics[1].hex()[-40:]
            to_address = '0x' + topics[2].hex()[-40:]

            # Excluded token kontrolü (WETH, USDC, USDT vs.)
            if token_address.lower() in [t.lower() for t in EXCLUDED_TOKENS]:
                return

            # to_address smart money cüzdanı mı?
            if to_address.lower() not in self.wallets_set:
                return

            current_time = time.time()

            # Bu cüzdan bu tokeni zaten aldı mı? (son TIME_WINDOW içinde)
            existing_wallets = [p[0] for p in self.token_purchases[token_address]]
            if to_address.lower() in [w.lower() for w in existing_wallets]:
                return  # Aynı cüzdan aynı tokeni zaten aldı

            # Token bilgisini al (sembol kontrolü için)
            token_info = get_token_info_dexscreener(token_address)
            token_symbol = token_info.get('symbol', 'UNKNOWN')

            # Sembol bazlı excluded token kontrolü
            if token_symbol.upper() in [s.upper() for s in EXCLUDED_SYMBOLS]:
                return

            # ETH değerini tahmin et
            eth_amount = self._estimate_eth_from_transfer(log)
            current_mcap = token_info.get('mcap', 0)

            # Market cap filtresi - MAX_MCAP üstündeki tokenlar alert dışı
            if current_mcap > MAX_MCAP:
                print(f"⏭️  Skip: {token_symbol} | MCap: ${current_mcap/1e6:.2f}M > ${MAX_MCAP/1e6:.0f}M limit")
                return

            # Alımı kaydet: (wallet, eth_amount, mcap, timestamp)
            self.token_purchases[token_address].append(
                (to_address, eth_amount, current_mcap, current_time)
            )

            print(f"📥 Alım: {to_address[:10]}... → {token_symbol} | {eth_amount:.3f} ETH | MCap: ${current_mcap/1e6:.2f}M")

            # === SMARTEST WALLET CHECK - Senaryo 2 ===
            try:
                if is_smartest_wallet(to_address):
                    print(f"🧠 SMARTEST WALLET alım yaptı: {to_address[:10]}... → {token_symbol}")
                    trader = get_trader()
                    trader.buy_token_scenario2(
                        token_address=token_address,
                        token_symbol=token_symbol,
                        entry_mcap=current_mcap
                    )
            except Exception as e:
                print(f"⚠️ Virtual trade S2 hatası: {e}")

            # Eski alımları temizle
            self._clean_old_purchases()

            # Alert kontrolü
            self._check_and_alert(token_address)

        except Exception as e:
            print(f"⚠️ Transfer işleme hatası: {e}")

    def _check_and_alert(self, token_address: str):
        """
        Token için alert koşullarını kontrol et.
        ALERT_THRESHOLD cüzdan alım yapmışsa alert gönder.
        """
        purchases = self.token_purchases.get(token_address, [])
        unique_wallets = {}
        for p in purchases:
            wallet = p[0].lower()
            if wallet not in unique_wallets:
                unique_wallets[wallet] = p  # (wallet, eth, mcap, ts)

        if len(unique_wallets) >= ALERT_THRESHOLD:
            if not self._can_send_alert(token_address):
                print(f"⏳ Alert cooldown aktif: {token_address[:10]}...")
                return

            print(f"\n🚨 ALERT! {len(unique_wallets)} cüzdan aynı tokeni aldı!")

            # Token bilgisi al
            token_info = get_token_info_dexscreener(token_address)

            # wallet_purchases formatı: [(wallet, eth_amount, buy_mcap), ...]
            wallet_purchases = [
                (p[0], p[1], p[2])  # wallet, eth_amount, mcap
                for p in unique_wallets.values()
            ]

            # Alert gönder
            first_buy_time = datetime.now().strftime("%H:%M:%S")
            success = send_smart_money_alert(
                token_address=token_address,
                wallet_purchases=wallet_purchases,
                first_buy_time=first_buy_time,
                token_info=token_info
            )

            if success:
                self.last_alerts[token_address] = time.time()
                print(f"✅ Alert gönderildi: {token_info.get('symbol', token_address[:10])}")

                # === EARLY DETECTION ===
                try:
                    process_alert_for_early_detection(
                        token_address=token_address,
                        token_symbol=token_info.get('symbol', 'UNKNOWN'),
                        smart_money_purchases=wallet_purchases,
                        smart_money_wallets=self.wallets_set,
                        current_block=self.w3.eth.block_number
                    )
                except Exception as e:
                    print(f"⚠️ Early detection hatası: {e}")

                # === VIRTUAL TRADING - Senaryo 1 ===
                try:
                    trader = get_trader()
                    current_mcap = token_info.get('mcap', 0)
                    trader.buy_token_scenario1(
                        token_address=token_address,
                        token_symbol=token_info.get('symbol', 'UNKNOWN'),
                        entry_mcap=current_mcap
                    )
                except Exception as e:
                    print(f"⚠️ Virtual trade S1 hatası: {e}")
            else:
                print(f"❌ Alert gönderilemedi!")

    async def start_monitoring(self):
        """HTTP polling ile real-time izlemeyi başlat."""
        print("\n" + "=" * 60)
        print("🚀 SMART MONEY MONITOR BAŞLATILIYOR")
        print("=" * 60)
        print(f"📊 İzlenen cüzdan sayısı: {len(self.wallets)}")
        print(f"⏱️  Zaman penceresi: {TIME_WINDOW} saniye")
        print(f"🎯 Alert eşiği: {ALERT_THRESHOLD} cüzdan")
        print(f"💰 Max MCap: ${MAX_MCAP/1e6:.0f}M")
        print(f"⏳ Alert cooldown: {ALERT_COOLDOWN} saniye")
        print("=" * 60 + "\n")

        # Başlangıç bildirimi
        send_status_update(
            f"🟢 Monitor v2.0 başlatıldı!\n"
            f"• {len(self.wallets)} cüzdan izleniyor\n"
            f"• Alert eşiği: {ALERT_THRESHOLD} cüzdan / {TIME_WINDOW}sn\n"
            f"• Max MCap: ${MAX_MCAP/1e6:.0f}M\n"
            f"• Virtual Trading: Aktif (0.5 ETH)\n"
            f"• Daily Report: 23:30"
        )

        # HTTP polling ile izleme
        await self._poll_transfers()

    async def _poll_transfers(self):
        """
        HTTP polling ile transfer event'lerini izle.
        Her 2 saniyede yeni blokları kontrol et.
        """
        last_block = self.w3.eth.block_number
        print(f"📦 Başlangıç bloğu: {last_block}")
        print(f"🔄 Polling başladı (her 2 saniye)...\n")

        block_count = 0
        transfer_count = 0

        while True:
            try:
                current_block = self.w3.eth.block_number

                if current_block > last_block:
                    # Yeni blokları işle
                    for block_num in range(last_block + 1, current_block + 1):
                        transfers = await self._process_block(block_num)
                        transfer_count += transfers
                        block_count += 1

                    # Her 50 blokta bir durum yazdır
                    if block_count % 50 == 0:
                        print(f"📊 {block_count} blok işlendi | {transfer_count} smart money transfer")

                        # Günlük rapor kontrolü (23:30)
                        try:
                            check_and_send_if_time()
                        except Exception as e:
                            print(f"⚠️ Daily report hatası: {e}")

                    last_block = current_block

                # 2 saniye bekle
                await asyncio.sleep(2)

            except KeyboardInterrupt:
                print("\n⏹️ Monitor durduruldu.")
                send_status_update("🔴 Monitor durduruldu.")
                break
            except Exception as e:
                print(f"⚠️ Polling hatası: {e}")
                await asyncio.sleep(5)

    async def _process_block(self, block_number: int) -> int:
        """
        Bir bloktaki transfer event'lerini işle.
        Returns: İşlenen smart money transfer sayısı
        """
        transfer_count = 0
        try:
            # Transfer event'lerini çek
            logs = self.w3.eth.get_logs({
                'fromBlock': block_number,
                'toBlock': block_number,
                'topics': [TRANSFER_EVENT_SIGNATURE]
            })

            for log in logs:
                # Sadece smart money'ye gelen transferleri işle
                if len(log['topics']) >= 3:
                    to_address = '0x' + log['topics'][2].hex()[-40:]
                    if to_address.lower() in self.wallets_set:
                        self.process_transfer(log)
                        transfer_count += 1

        except Exception as e:
            print(f"⚠️ Blok işleme hatası ({block_number}): {e}")

        return transfer_count


def main():
    """Ana fonksiyon."""
    # Cüzdan dosyası yolu
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # Önce final dosyasını dene, yoksa priority dosyasını kullan
    wallets_file = os.path.join(base_dir, "data", "smart_money_final.json")
    if not os.path.exists(wallets_file):
        wallets_file = os.path.join(base_dir, "data", "wallets_priority_pnl.json")

    if not os.path.exists(wallets_file):
        print(f"❌ Cüzdan dosyası bulunamadı!")
        print("Önce ETH P&L analizini tamamlayın!")
        return

    print(f"📂 Cüzdan dosyası: {wallets_file}")

    # Monitor başlat
    monitor = SmartMoneyMonitor(wallets_file)

    if not monitor.wallets:
        print("❌ İzlenecek cüzdan bulunamadı!")
        return

    # Async event loop
    try:
        asyncio.run(monitor.start_monitoring())
    except KeyboardInterrupt:
        print("\n👋 Çıkış yapılıyor...")


if __name__ == "__main__":
    main()
