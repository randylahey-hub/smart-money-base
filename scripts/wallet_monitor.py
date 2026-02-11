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
from datetime import datetime, timezone, timedelta
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
    MIN_VOLUME_24H,
    MIN_TXNS_24H,
    MIN_BUY_VALUE_USD,
    MIN_LIQUIDITY,
    BULLISH_WINDOW,
    WETH_ADDRESS,
    TRANSFER_EVENT_SIGNATURE,
    SWAP_SIGNATURES,
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
from scripts.tx_classifier import classify_transaction
from scripts.fake_alert_tracker import record_fake_alert, is_flagged_wallet
from scripts.database import init_db, is_db_available, save_trade_signal, is_duplicate_signal, save_wallet_activity
from scripts.wallet_scorer import process_alert_v2, record_wallet_activity

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

        # Son alert bilgileri: {token_address: {"time": timestamp, "mcap": mcap, "count": alert_count}}
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

    def _can_send_alert(self, token_address: str, unique_wallet_count: int = 0) -> bool:
        """
        Alert cooldown kontrolü.
        - Normal: 5dk cooldown
        - Bullish: Cooldown içinde bile, daha fazla cüzdan aldıysa geçir
        """
        if token_address not in self.last_alerts:
            return True
        last_info = self.last_alerts[token_address]
        elapsed = time.time() - last_info["time"]

        # Cooldown geçmişse → normal alert
        if elapsed > ALERT_COOLDOWN:
            return True

        # Cooldown içinde ama daha fazla cüzdan aldıysa → bullish alert olarak geçir
        if unique_wallet_count > last_info.get("wallet_count", 0):
            return True

        return False

    def _is_bullish_alert(self, token_address: str) -> tuple:
        """
        Bullish alert kontrolü.
        Returns: (is_bullish, alert_count, first_alert_mcap)
        """
        if token_address not in self.last_alerts:
            return False, 1, 0

        last_info = self.last_alerts[token_address]
        elapsed = time.time() - last_info["time"]

        # BULLISH_WINDOW (30dk) içinde tekrar alert geliyorsa = bullish
        if elapsed <= BULLISH_WINDOW:
            return True, last_info["count"] + 1, last_info["mcap"]

        return False, 1, 0

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

            # === SWAP DOĞRULAMASI ===
            # Airdrop/dust saldırılarını engelle: Transaction içinde Swap event yoksa → alım değil
            try:
                tx_hash = log['transactionHash']
                receipt = self.w3.eth.get_transaction_receipt(tx_hash)

                has_swap = False
                for receipt_log in receipt['logs']:
                    if receipt_log['topics'] and receipt_log['topics'][0].hex() in [s.replace('0x', '') for s in SWAP_SIGNATURES]:
                        has_swap = True
                        break
                if not has_swap:
                    print(f"⏭️  Skip: {token_address[:10]}... → {to_address[:10]}... | Swap yok (airdrop/dust)")
                    return

            except Exception as e:
                print(f"⚠️ Swap doğrulama hatası: {e}")
                return

            # === AIRDROP / MULTICALL / BATCH TRANSFER FİLTRESİ ===
            # Basescan Action sütunundaki hareket tiplerini kontrol et
            tx_class = classify_transaction(tx_hash, receipt, self.w3)
            if tx_class["skip"]:
                print(f"⏭️  Skip: {token_address[:10]}... → {to_address[:10]}... | {tx_class['type']}: {tx_class['reason']}")
                return

            # Token bilgisini al (sembol kontrolü için)
            token_info = get_token_info_dexscreener(token_address)
            token_symbol = token_info.get('symbol', 'UNKNOWN')

            # Sembol bazlı excluded token kontrolü
            if token_symbol.upper() in [s.upper() for s in EXCLUDED_SYMBOLS]:
                return

            # === AIRDROP/DUST FILTRESI ===
            # Kontrol 1: Likidite kontrolü - düşük likidite = güvenilmez token
            liquidity = token_info.get('liquidity', 0)
            if liquidity < MIN_LIQUIDITY:
                print(f"⏭️  Skip: {token_symbol} | Likidite: ${liquidity:.0f} < ${MIN_LIQUIDITY:,} minimum")
                return

            # NOT: from_address whitelist kontrolü kaldırıldı (v1) - çok agresifti
            # Yerine: Swap event doğrulaması eklendi (v2) - tx receipt içinde Swap varsa gerçek alım

            # ETH değerini tahmin et
            eth_amount = self._estimate_eth_from_transfer(log)

            # Kontrol 3: Minimum alım değeri - dust transfer filtresi
            eth_price_usd = 2500  # Yaklaşık ETH fiyatı
            buy_value_usd = eth_amount * eth_price_usd
            if 0 < buy_value_usd < MIN_BUY_VALUE_USD:
                print(f"⏭️  Skip: {token_symbol} | Dust: ${buy_value_usd:.2f} < ${MIN_BUY_VALUE_USD} minimum")
                return

            current_mcap = token_info.get('mcap', 0)

            # Market cap filtresi - MAX_MCAP üstündeki tokenlar alert dışı
            if current_mcap > MAX_MCAP:
                print(f"⏭️  Skip: {token_symbol} | MCap: ${current_mcap/1e3:.0f}K > ${MAX_MCAP/1e3:.0f}K limit")
                return

            # === COP TOKEN FILTRESI (Erken eleme) ===
            # Hacim kontrolu - dusuk hacimli tokenlar direkt elensin
            volume_24h = token_info.get('volume_24h', 0)
            if volume_24h < MIN_VOLUME_24H:
                print(f"⏭️  Skip: {token_symbol} | 24s Hacim: ${volume_24h:.0f} < ${MIN_VOLUME_24H:,} minimum")
                return

            # Islem sayisi kontrolu (makers proxy) - cok az islem = cop token
            txns_buys = token_info.get('txns_24h_buys', 0)
            txns_sells = token_info.get('txns_24h_sells', 0)
            total_txns = txns_buys + txns_sells
            if total_txns < MIN_TXNS_24H:
                print(f"⏭️  Skip: {token_symbol} | 24s Islem: {total_txns} < {MIN_TXNS_24H} minimum")
                return

            # Alımı kaydet: (wallet, eth_amount, mcap, timestamp)
            self.token_purchases[token_address].append(
                (to_address, eth_amount, current_mcap, current_time)
            )

            print(f"📥 Alım: {to_address[:10]}... → {token_symbol} | {eth_amount:.3f} ETH | MCap: ${current_mcap/1e6:.2f}M")

            # === WALLET ACTIVITY KAYDI (Smartest wallet scorer için) ===
            try:
                record_wallet_activity(
                    wallet_address=to_address,
                    token_address=token_address,
                    token_symbol=token_symbol,
                    block_number=log.get('blockNumber', 0),
                    is_early=False,
                    alert_mcap=int(current_mcap)
                )
            except Exception as e:
                print(f"⚠️ Wallet activity kayıt hatası: {e}")

            # === SMARTEST WALLET CHECK - Senaryo 2 (Virtual trading devre dışı) ===
            # try:
            #     if is_smartest_wallet(to_address):
            #         print(f"🧠 SMARTEST WALLET alım yaptı: {to_address[:10]}... → {token_symbol}")
            #         trader = get_trader()
            #         trader.buy_token_scenario2(
            #             token_address=token_address,
            #             token_symbol=token_symbol,
            #             entry_mcap=current_mcap
            #         )
            # except Exception as e:
            #     print(f"⚠️ Virtual trade S2 hatası: {e}")

            # === TRADE SIGNAL - Senaryo 2 (Smartest Wallet) ===
            try:
                if is_smartest_wallet(to_address) and not is_duplicate_signal(token_address):
                    save_trade_signal(token_address, token_symbol, current_mcap, "scenario_2", 1)
            except Exception as e:
                print(f"⚠️ Trade signal S2 hatası: {e}")

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
            if not self._can_send_alert(token_address, len(unique_wallets)):
                print(f"⏳ Alert cooldown aktif: {token_address[:10]}...")
                return

            print(f"\n🚨 ALERT! {len(unique_wallets)} cüzdan aynı tokeni aldı!")

            # Token bilgisi al
            token_info = get_token_info_dexscreener(token_address)

            # === COP TOKEN KONTROLU (2. katman - alert oncesi son kontrol) ===
            volume_24h = token_info.get('volume_24h', 0)
            txns_buys = token_info.get('txns_24h_buys', 0)
            txns_sells = token_info.get('txns_24h_sells', 0)
            total_txns = txns_buys + txns_sells
            token_sym = token_info.get('symbol', 'UNKNOWN')

            is_fake = False
            fake_reason = ""

            if volume_24h < MIN_VOLUME_24H:
                is_fake = True
                fake_reason = f"24s Hacim: ${volume_24h:.0f} < ${MIN_VOLUME_24H:,}"

            if total_txns < MIN_TXNS_24H:
                is_fake = True
                fake_reason += f" | 24s Islem: {total_txns} < {MIN_TXNS_24H}" if fake_reason else f"24s Islem: {total_txns} < {MIN_TXNS_24H}"

            if is_fake:
                print(f"⚠️  FAKE ALERT ENGELLENDI: {token_sym} | {fake_reason}")
                # Fake alert'teki cuzdanlari flagle
                wallet_list = [p[0] for p in unique_wallets.values()]
                record_fake_alert(
                    wallet_addresses=wallet_list,
                    token_address=token_address,
                    token_symbol=token_sym,
                    volume_24h=volume_24h
                )
                # Bu token icin tracking'i temizle (tekrar alert gondermesin)
                self.token_purchases.pop(token_address, None)
                return

            # wallet_purchases formatı: [(wallet, eth_amount, buy_mcap), ...]
            wallet_purchases = [
                (p[0], p[1], p[2])  # wallet, eth_amount, mcap
                for p in unique_wallets.values()
            ]

            # === BULLISH KONTROL ===
            current_mcap_val = token_info.get('mcap', 0)
            is_bullish, alert_count, first_alert_mcap = self._is_bullish_alert(token_address)

            if is_bullish:
                print(f"🔥 BULLISH ALERT! {token_sym} — {alert_count}. alert | İlk: ${first_alert_mcap/1e3:.0f}K → Şimdi: ${current_mcap_val/1e3:.0f}K")

            # Alert gönder
            # UTC+3 (Turkiye saati)
            tr_time = datetime.now(timezone.utc) + timedelta(hours=3)
            first_buy_time = tr_time.strftime("%H:%M:%S")
            success = send_smart_money_alert(
                token_address=token_address,
                wallet_purchases=wallet_purchases,
                first_buy_time=first_buy_time,
                token_info=token_info,
                is_bullish=is_bullish,
                alert_count=alert_count,
                first_alert_mcap=first_alert_mcap
            )

            if success:
                self.last_alerts[token_address] = {
                    "time": time.time(),
                    "mcap": first_alert_mcap if is_bullish else current_mcap_val,
                    "count": alert_count,
                    "wallet_count": len(unique_wallets)
                }
                print(f"✅ Alert gönderildi: {token_info.get('symbol', token_address[:10])}")

                # === EARLY DETECTION (v2 - wallet scorer entegrasyonu) ===
                try:
                    process_alert_v2(
                        token_address=token_address,
                        token_symbol=token_info.get('symbol', 'UNKNOWN'),
                        smart_money_purchases=wallet_purchases,
                        smart_money_wallets=self.wallets_set,
                        current_block=self.w3.eth.block_number
                    )
                except Exception as e:
                    print(f"⚠️ Early detection v2 hatası: {e}")
                    # Fallback: eski sistemi dene
                    try:
                        process_alert_for_early_detection(
                            token_address=token_address,
                            token_symbol=token_info.get('symbol', 'UNKNOWN'),
                            smart_money_purchases=wallet_purchases,
                            smart_money_wallets=self.wallets_set,
                            current_block=self.w3.eth.block_number
                        )
                    except Exception as e2:
                        print(f"⚠️ Early detection fallback hatası: {e2}")

                # === VIRTUAL TRADING - Senaryo 1 (Devre dışı) ===
                # try:
                #     trader = get_trader()
                #     current_mcap = token_info.get('mcap', 0)
                #     trader.buy_token_scenario1(
                #         token_address=token_address,
                #         token_symbol=token_info.get('symbol', 'UNKNOWN'),
                #         entry_mcap=current_mcap
                #     )
                # except Exception as e:
                #     print(f"⚠️ Virtual trade S1 hatası: {e}")

                # === TRADE SIGNAL - Senaryo 1 (Smart Money Alert) ===
                try:
                    if not is_duplicate_signal(token_address):
                        save_trade_signal(token_address, token_info.get('symbol', 'UNKNOWN'), current_mcap, "scenario_1", len(unique_wallets))
                except Exception as e:
                    print(f"⚠️ Trade signal S1 hatası: {e}")

                # === SELF-IMPROVING ENGINE: MCap Timer ===
                try:
                    from scripts.self_improving_engine import run_per_alert_check
                    run_per_alert_check(
                        token_address=token_address,
                        token_symbol=token_info.get('symbol', 'UNKNOWN'),
                        alert_mcap=int(current_mcap),
                        wallets_involved=[p[0] for p in wallet_purchases]
                    )
                except Exception as e:
                    print(f"⚠️ Self-improving MCap timer hatası: {e}")
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
        print(f"💰 Max MCap: ${MAX_MCAP/1e3:.0f}K")
        print(f"📊 Min Hacim: ${MIN_VOLUME_24H:,}")
        print(f"👥 Min İşlem: {MIN_TXNS_24H}")
        print(f"💧 Min Likidite: ${MIN_LIQUIDITY:,}")
        print(f"🛡️  Min Alım: ${MIN_BUY_VALUE_USD}")
        print(f"🔥 Bullish Pencere: {BULLISH_WINDOW}sn")
        print(f"⏳ Alert cooldown: {ALERT_COOLDOWN} saniye")
        print(f"🔍 Swap Doğrulama: Aktif ({len(SWAP_SIGNATURES)} DEX)")
        print(f"📡 Trade Signals: DB üzerinden (ayrı bot)")
        print("=" * 60 + "\n")

        # Başlangıç bildirimi
        send_status_update(
            f"🟢 Monitor v2.0 başlatıldı!\n"
            f"• {len(self.wallets)} cüzdan izleniyor\n"
            f"• Alert eşiği: {ALERT_THRESHOLD} cüzdan / {TIME_WINDOW}sn\n"
            f"• Max MCap: ${MAX_MCAP/1e3:.0f}K\n"
            f"• Min Hacim: ${MIN_VOLUME_24H:,}\n"
            f"• Min İşlem: {MIN_TXNS_24H}\n"
            f"• Min Likidite: ${MIN_LIQUIDITY:,}\n"
            f"• Swap Doğrulama: Aktif ({len(SWAP_SIGNATURES)} DEX)\n"
            f"• Airdrop Filtresi: Aktif (${MIN_BUY_VALUE_USD}+ alım)\n"
            f"• Bullish Alert: {BULLISH_WINDOW//60}dk pencere\n"
            f"• Virtual Trading: Aktif (0.5 ETH)\n"
            f"• 📡 Trade Signals: DB (ayrı bot)\n"
            f"• Daily Report: 20:30\n"
            f"• Self-Improving: {'Aktif' if os.getenv('SELF_IMPROVE_ENABLED', 'false').lower() == 'true' else 'Kapalı'}"
        )

        # Polling başlat
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

                        # Günlük rapor kontrolü (20:30)
                        try:
                            check_and_send_if_time()
                        except Exception as e:
                            print(f"⚠️ Daily report hatası: {e}")

                        # Self-improving engine: Bekleyen MCap check'leri
                        try:
                            from scripts.mcap_checker import process_pending_checks, get_pending_count
                            pending = get_pending_count()
                            if pending > 0:
                                results = process_pending_checks()
                                if results:
                                    print(f"📈 MCap check: {len(results)} token kontrol edildi ({pending - get_pending_count()} kalan)")
                        except Exception as e:
                            if "No module" not in str(e):
                                print(f"⚠️ MCap checker hatası: {e}")

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

    # Database başlat
    if is_db_available():
        init_db()
        print("🗄️  PostgreSQL aktif")
    else:
        print("📁 JSON dosya sistemi aktif (DATABASE_URL yok)")

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
