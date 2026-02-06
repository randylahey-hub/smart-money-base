"""
Real Trading System
Base chain üzerinde gerçek ETH ile Uniswap V3 swap işlemi yapar.
Virtual trader ile aynı anda çalışır — virtual test, real kâr.
"""

import json
import os
import sys
import time
import asyncio
import copy
from datetime import datetime, timezone, timedelta
from typing import Optional

from web3 import Web3

# Config imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import (
    BASE_RPC_HTTP,
    WETH_ADDRESS,
    UNISWAP_V3_ROUTER,
    TRADING_PRIVATE_KEY,
    REAL_TRADING_ENABLED
)
from scripts.real_trade_config import (
    REAL_TRADE_SIZE_ETH,
    MAX_SINGLE_TRADE_ETH,
    MAX_TOTAL_EXPOSURE_ETH,
    MAX_OPEN_POSITIONS,
    MAX_DAILY_LOSS_ETH,
    SLIPPAGE_PERCENT,
    DEFAULT_TP_LEVELS,
    DEFAULT_SL_MULTIPLIER,
    GAS_MULTIPLIER,
    MAX_GAS_GWEI,
    POSITION_CHECK_INTERVAL,
    PRIMARY_FEE_TIER,
    FALLBACK_FEE_TIER,
    BASE_CHAIN_ID
)
from scripts.telegram_alert import (
    get_token_info_dexscreener,
    send_telegram_message,
    format_number
)
from scripts.database import (
    is_db_available,
    load_real_portfolio_db,
    save_real_portfolio_db
)

# Flush
sys.stdout.reconfigure(line_buffering=True)


# =============================================================================
# ABIs (Sadece ihtiyacımız olan fonksiyonlar)
# =============================================================================

SWAP_ROUTER_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"name": "tokenIn", "type": "address"},
                    {"name": "tokenOut", "type": "address"},
                    {"name": "fee", "type": "uint24"},
                    {"name": "recipient", "type": "address"},
                    {"name": "amountIn", "type": "uint256"},
                    {"name": "amountOutMinimum", "type": "uint256"},
                    {"name": "sqrtPriceLimitX96", "type": "uint160"}
                ],
                "name": "params",
                "type": "tuple"
            }
        ],
        "name": "exactInputSingle",
        "outputs": [{"name": "amountOut", "type": "uint256"}],
        "stateMutability": "payable",
        "type": "function"
    }
]

ERC20_ABI = [
    {
        "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [{"name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    }
]

WETH_WITHDRAW_ABI = [
    {
        "inputs": [{"name": "wad", "type": "uint256"}],
        "name": "withdraw",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    }
]


# =============================================================================
# Singleton
# =============================================================================

_real_trader_instance = None


def get_real_trader():
    """Singleton RealTrader instance."""
    global _real_trader_instance
    if _real_trader_instance is None:
        _real_trader_instance = RealTrader()
    return _real_trader_instance


# =============================================================================
# RealTrader Class
# =============================================================================

class RealTrader:
    """Gerçek on-chain trading sistemi."""

    def __init__(self):
        if not TRADING_PRIVATE_KEY:
            raise ValueError("❌ TRADING_PRIVATE_KEY env variable gerekli!")

        # Web3 bağlantısı
        self.w3 = Web3(Web3.HTTPProvider(BASE_RPC_HTTP))
        if not self.w3.is_connected():
            raise ConnectionError("❌ Base chain bağlantısı kurulamadı!")

        # Account
        self.account = self.w3.eth.account.from_key(TRADING_PRIVATE_KEY)
        self.wallet_address = self.account.address
        print(f"💼 Trading cüzdanı: {self.wallet_address}")

        # Bakiye kontrol
        balance = self.w3.eth.get_balance(self.wallet_address)
        balance_eth = float(self.w3.from_wei(balance, 'ether'))
        print(f"💰 Cüzdan bakiyesi: {balance_eth:.4f} ETH")

        # Router contract
        self.router = self.w3.eth.contract(
            address=Web3.to_checksum_address(UNISWAP_V3_ROUTER),
            abi=SWAP_ROUTER_ABI
        )

        # Portfolio yükle
        self.portfolio = self._load_portfolio()

        # Daily loss tracker
        self._daily_loss_eth = self.portfolio.get("daily_loss_tracker", {}).get("loss_eth", 0.0)
        self._daily_reset_date = self.portfolio.get("daily_loss_tracker", {}).get("date", "")

    # =========================================================================
    # PORTFOLIO MANAGEMENT
    # =========================================================================

    def _default_portfolio(self) -> dict:
        """Boş portföy şablonu."""
        return {
            "wallet_address": self.wallet_address,
            "positions": [],
            "closed_trades": [],
            "total_pnl_eth": 0.0,
            "win_count": 0,
            "loss_count": 0,
            "total_gas_spent_eth": 0.0,
            "daily_loss_tracker": {
                "date": datetime.now().strftime("%Y-%m-%d"),
                "loss_eth": 0.0
            }
        }

    def _load_portfolio(self) -> dict:
        """Portföyü yükle (DB → JSON → default)."""
        # DB'den dene
        if is_db_available():
            data = load_real_portfolio_db()
            if data:
                print("🗄️  Real portfolio DB'den yüklendi")
                return data

        # JSON'dan dene
        json_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "real_portfolio.json"
        )
        try:
            if os.path.exists(json_path):
                with open(json_path, 'r') as f:
                    data = json.load(f)
                print("📁 Real portfolio JSON'dan yüklendi")
                return data
        except Exception as e:
            print(f"⚠️ JSON okuma hatası: {e}")

        print("📋 Yeni real portfolio oluşturuldu")
        return self._default_portfolio()

    def _save_portfolio(self):
        """Portföyü kaydet (DB + JSON backup)."""
        # DB'ye kaydet
        if is_db_available():
            save_real_portfolio_db(self.portfolio)

        # JSON backup
        json_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "real_portfolio.json"
        )
        try:
            with open(json_path, 'w') as f:
                json.dump(self.portfolio, f, indent=2, default=str)
        except Exception:
            pass  # Koyeb ephemeral, JSON yazılamazsa sorun değil

    def _get_open_positions(self) -> list:
        """Açık pozisyonları döndür."""
        return self.portfolio.get("positions", [])

    def _find_position(self, token_address: str) -> Optional[dict]:
        """Token adresine göre açık pozisyon bul."""
        for pos in self._get_open_positions():
            if pos["token"].lower() == token_address.lower():
                return pos
        return None

    def _get_total_exposure(self) -> float:
        """Tüm açık pozisyonlardaki toplam ETH."""
        return sum(pos.get("eth_spent", 0) for pos in self._get_open_positions())

    # =========================================================================
    # SAFETY CHECKS
    # =========================================================================

    def _check_daily_loss_reset(self):
        """Günlük kayıp sayacını gece yarısı sıfırla."""
        today = datetime.now().strftime("%Y-%m-%d")
        if self._daily_reset_date != today:
            self._daily_loss_eth = 0.0
            self._daily_reset_date = today
            self.portfolio["daily_loss_tracker"] = {
                "date": today,
                "loss_eth": 0.0
            }
            self._save_portfolio()

    def _record_daily_loss(self, loss_eth: float):
        """Günlük kayba ekle (sadece pozitif kayıp)."""
        if loss_eth > 0:
            self._daily_loss_eth += loss_eth
            self.portfolio["daily_loss_tracker"]["loss_eth"] = self._daily_loss_eth
            self._save_portfolio()

    def _get_eth_price(self) -> float:
        """ETH fiyatını USD olarak al."""
        try:
            weth_info = get_token_info_dexscreener(WETH_ADDRESS)
            price = weth_info.get('price', 0)
            if price > 0:
                return price
        except:
            pass
        return 2500  # Fallback

    def _get_gas_params(self) -> dict:
        """Gas parametrelerini hesapla."""
        gas_price = self.w3.eth.gas_price
        max_fee = int(gas_price * GAS_MULTIPLIER)
        max_gas_wei = self.w3.to_wei(MAX_GAS_GWEI, 'gwei')

        # Cap uygula
        if max_fee > max_gas_wei:
            max_fee = max_gas_wei

        return {
            'maxFeePerGas': max_fee,
            'maxPriorityFeePerGas': self.w3.to_wei(0.001, 'gwei'),
            'chainId': BASE_CHAIN_ID
        }

    # =========================================================================
    # BUY
    # =========================================================================

    def buy_token(
        self,
        token_address: str,
        token_symbol: str,
        entry_mcap: float,
        eth_amount: float = None
    ) -> Optional[dict]:
        """
        ETH → Token swap via Uniswap V3 exactInputSingle.
        Returns: position dict veya None.
        """
        if not REAL_TRADING_ENABLED:
            return None

        if eth_amount is None:
            eth_amount = REAL_TRADE_SIZE_ETH

        # --- Safety Checks ---
        if eth_amount > MAX_SINGLE_TRADE_ETH:
            print(f"🛡️ SAFETY: Trade {eth_amount} > max {MAX_SINGLE_TRADE_ETH} ETH")
            return None

        if len(self._get_open_positions()) >= MAX_OPEN_POSITIONS:
            print(f"🛡️ SAFETY: Max pozisyon limiti ({MAX_OPEN_POSITIONS})")
            return None

        if self._get_total_exposure() + eth_amount > MAX_TOTAL_EXPOSURE_ETH:
            print(f"🛡️ SAFETY: Max exposure limiti ({MAX_TOTAL_EXPOSURE_ETH} ETH)")
            return None

        self._check_daily_loss_reset()
        if self._daily_loss_eth >= MAX_DAILY_LOSS_ETH:
            print(f"🛡️ SAFETY: Günlük kayıp limiti ({self._daily_loss_eth:.4f}/{MAX_DAILY_LOSS_ETH} ETH)")
            return None

        # Duplikat kontrolü
        if self._find_position(token_address):
            print(f"🛡️ SAFETY: {token_symbol} için zaten açık pozisyon var")
            return None

        # Bakiye kontrolü
        balance = self.w3.eth.get_balance(self.wallet_address)
        balance_eth = float(self.w3.from_wei(balance, 'ether'))
        if balance_eth < eth_amount + 0.002:
            print(f"🛡️ SAFETY: Yetersiz bakiye: {balance_eth:.4f} ETH")
            return None

        # --- Token bilgisi ---
        token_info = get_token_info_dexscreener(token_address)
        price_usd = token_info.get('price', 0)
        if price_usd <= 0:
            print(f"⚠️ {token_symbol} fiyat alınamadı, trade iptal")
            return None

        # Token decimals
        token_contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(token_address),
            abi=ERC20_ABI
        )
        try:
            decimals = token_contract.functions.decimals().call()
        except:
            decimals = 18

        # Slippage hesaplama
        eth_price_usd = self._get_eth_price()
        expected_tokens = (eth_amount * eth_price_usd) / price_usd
        min_amount_out = int(expected_tokens * (1 - SLIPPAGE_PERCENT / 100) * (10 ** decimals))
        amount_in_wei = self.w3.to_wei(eth_amount, 'ether')

        # --- İlk deneme: PRIMARY_FEE_TIER ---
        result = self._execute_buy(
            token_address, token_symbol, entry_mcap,
            eth_amount, amount_in_wei, min_amount_out,
            token_contract, decimals, price_usd,
            PRIMARY_FEE_TIER
        )

        # Başarısızsa FALLBACK_FEE_TIER dene
        if result is None:
            print(f"🔄 {token_symbol} fee tier {PRIMARY_FEE_TIER} başarısız, {FALLBACK_FEE_TIER} deneniyor...")
            result = self._execute_buy(
                token_address, token_symbol, entry_mcap,
                eth_amount, amount_in_wei, min_amount_out,
                token_contract, decimals, price_usd,
                FALLBACK_FEE_TIER
            )

        return result

    def _execute_buy(
        self,
        token_address: str,
        token_symbol: str,
        entry_mcap: float,
        eth_amount: float,
        amount_in_wei: int,
        min_amount_out: int,
        token_contract,
        decimals: int,
        price_usd: float,
        fee_tier: int
    ) -> Optional[dict]:
        """Swap TX oluştur, imzala, gönder."""
        try:
            # Önceki token bakiyesini oku (delta hesaplamak için)
            pre_balance = token_contract.functions.balanceOf(self.wallet_address).call()

            swap_params = (
                Web3.to_checksum_address(WETH_ADDRESS),      # tokenIn
                Web3.to_checksum_address(token_address),      # tokenOut
                fee_tier,                                      # fee
                self.wallet_address,                           # recipient
                amount_in_wei,                                 # amountIn
                min_amount_out,                                # amountOutMinimum
                0                                              # sqrtPriceLimitX96
            )

            gas_params = self._get_gas_params()
            nonce = self.w3.eth.get_transaction_count(self.wallet_address)

            tx = self.router.functions.exactInputSingle(swap_params).build_transaction({
                'from': self.wallet_address,
                'value': amount_in_wei,
                'nonce': nonce,
                'gas': 300000,
                **gas_params
            })

            # İmzala ve gönder
            signed_tx = self.w3.eth.account.sign_transaction(tx, private_key=TRADING_PRIVATE_KEY)
            tx_hash = self.w3.eth.send_raw_transaction(signed_tx.raw_transaction)
            print(f"📤 Buy TX gönderildi: {tx_hash.hex()[:16]}...")

            # Receipt bekle
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)

            if receipt['status'] == 1:
                # Gerçek alınan token miktarı (delta)
                post_balance = token_contract.functions.balanceOf(self.wallet_address).call()
                tokens_received = post_balance - pre_balance
                actual_amount = tokens_received / (10 ** decimals)

                # Gas maliyeti
                gas_cost_wei = receipt['gasUsed'] * receipt.get('effectiveGasPrice', self.w3.eth.gas_price)
                gas_cost_eth = float(self.w3.from_wei(gas_cost_wei, 'ether'))
                self.portfolio["total_gas_spent_eth"] = self.portfolio.get("total_gas_spent_eth", 0) + gas_cost_eth

                # Pozisyon oluştur
                position = {
                    "token": token_address,
                    "symbol": token_symbol,
                    "amount": actual_amount,
                    "amount_raw": str(tokens_received),
                    "decimals": decimals,
                    "entry_price": price_usd,
                    "entry_mcap": entry_mcap,
                    "entry_time": datetime.now().isoformat(),
                    "eth_spent": eth_amount,
                    "buy_tx": tx_hash.hex(),
                    "fee_tier": fee_tier,
                    "tp_levels": copy.deepcopy(DEFAULT_TP_LEVELS),
                    "sl_multiplier": DEFAULT_SL_MULTIPLIER
                }

                self.portfolio["positions"].append(position)
                self._save_portfolio()

                # Telegram bildirim
                _send_real_trade_alert(
                    action="BUY",
                    token_symbol=token_symbol,
                    token_address=token_address,
                    eth_amount=eth_amount,
                    tx_hash=tx_hash.hex(),
                    position_count=len(self._get_open_positions()),
                    wallet_balance=float(self.w3.from_wei(
                        self.w3.eth.get_balance(self.wallet_address), 'ether'
                    )),
                    extra_info=f"Entry MCap: {format_number(entry_mcap)}"
                )

                print(f"✅ BUY başarılı: {token_symbol} | {eth_amount:.4f} ETH | {actual_amount:.2f} token")
                return position
            else:
                print(f"❌ Buy TX FAILED: {tx_hash.hex()}")
                return None

        except Exception as e:
            error_msg = str(e)
            # execution reverted = pool yok veya yanlış fee tier
            if "execution reverted" in error_msg.lower():
                return None  # Caller will retry with fallback fee
            print(f"⚠️ Buy error ({fee_tier}): {error_msg}")
            return None

    # =========================================================================
    # SELL
    # =========================================================================

    def sell_token(
        self,
        token_address: str,
        sell_ratio: float = 1.0,
        reason: str = "MANUAL"
    ) -> Optional[dict]:
        """
        Token → WETH swap + WETH unwrap.
        sell_ratio: 0.0-1.0 (0.5 = yarısını sat)
        """
        position = self._find_position(token_address)
        if not position:
            print(f"⚠️ {token_address[:10]}... için açık pozisyon bulunamadı")
            return None

        token_contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(token_address),
            abi=ERC20_ABI
        )
        decimals = position.get("decimals", 18)

        # On-chain bakiye (güvenilir kaynak)
        on_chain_balance = token_contract.functions.balanceOf(self.wallet_address).call()
        sell_amount_raw = int(on_chain_balance * sell_ratio)

        if sell_amount_raw <= 0:
            print(f"⚠️ {position['symbol']} satacak bakiye yok")
            return None

        # --- Step 1: Approve ---
        try:
            current_allowance = token_contract.functions.allowance(
                self.wallet_address,
                Web3.to_checksum_address(UNISWAP_V3_ROUTER)
            ).call()

            if current_allowance < sell_amount_raw:
                max_approval = 2**256 - 1
                gas_params = self._get_gas_params()
                approve_tx = token_contract.functions.approve(
                    Web3.to_checksum_address(UNISWAP_V3_ROUTER),
                    max_approval
                ).build_transaction({
                    'from': self.wallet_address,
                    'nonce': self.w3.eth.get_transaction_count(self.wallet_address),
                    'gas': 100000,
                    **gas_params
                })

                signed = self.w3.eth.account.sign_transaction(approve_tx, TRADING_PRIVATE_KEY)
                approve_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
                self.w3.eth.wait_for_transaction_receipt(approve_hash, timeout=30)
                print(f"✅ Approve: {position['symbol']}")
        except Exception as e:
            print(f"❌ Approve hatası: {e}")
            return None

        # --- Step 2: Swap Token → WETH ---
        try:
            token_info = get_token_info_dexscreener(token_address)
            price_usd = token_info.get('price', 0)
            eth_price_usd = self._get_eth_price()

            sell_amount_human = sell_amount_raw / (10 ** decimals)
            expected_eth = (sell_amount_human * price_usd) / eth_price_usd if eth_price_usd > 0 else 0
            min_eth_out = self.w3.to_wei(max(expected_eth * (1 - SLIPPAGE_PERCENT / 100), 0), 'ether')

            fee_tier = position.get('fee_tier', PRIMARY_FEE_TIER)

            swap_params = (
                Web3.to_checksum_address(token_address),  # tokenIn
                Web3.to_checksum_address(WETH_ADDRESS),    # tokenOut
                fee_tier,
                self.wallet_address,                        # recipient
                sell_amount_raw,
                min_eth_out,
                0
            )

            gas_params = self._get_gas_params()
            nonce = self.w3.eth.get_transaction_count(self.wallet_address)

            # ETH bakiye öncesi (WETH dahil değil)
            pre_eth_balance = self.w3.eth.get_balance(self.wallet_address)

            tx = self.router.functions.exactInputSingle(swap_params).build_transaction({
                'from': self.wallet_address,
                'value': 0,
                'nonce': nonce,
                'gas': 350000,
                **gas_params
            })

            signed_tx = self.w3.eth.account.sign_transaction(tx, TRADING_PRIVATE_KEY)
            tx_hash = self.w3.eth.send_raw_transaction(signed_tx.raw_transaction)
            print(f"📤 Sell TX gönderildi: {tx_hash.hex()[:16]}...")

            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)

            if receipt['status'] != 1:
                print(f"❌ Sell TX FAILED: {tx_hash.hex()}")
                return None

            # Gas maliyeti
            gas_cost_wei = receipt['gasUsed'] * receipt.get('effectiveGasPrice', self.w3.eth.gas_price)
            gas_cost_eth = float(self.w3.from_wei(gas_cost_wei, 'ether'))
            self.portfolio["total_gas_spent_eth"] = self.portfolio.get("total_gas_spent_eth", 0) + gas_cost_eth

        except Exception as e:
            print(f"❌ Sell swap hatası: {e}")
            return None

        # --- Step 3: Unwrap WETH → ETH ---
        try:
            weth_contract = self.w3.eth.contract(
                address=Web3.to_checksum_address(WETH_ADDRESS),
                abi=WETH_WITHDRAW_ABI
            )
            weth_balance = self.w3.eth.contract(
                address=Web3.to_checksum_address(WETH_ADDRESS),
                abi=ERC20_ABI
            ).functions.balanceOf(self.wallet_address).call()

            if weth_balance > 0:
                gas_params = self._get_gas_params()
                unwrap_tx = weth_contract.functions.withdraw(weth_balance).build_transaction({
                    'from': self.wallet_address,
                    'nonce': self.w3.eth.get_transaction_count(self.wallet_address),
                    'gas': 50000,
                    **gas_params
                })
                signed = self.w3.eth.account.sign_transaction(unwrap_tx, TRADING_PRIVATE_KEY)
                unwrap_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
                self.w3.eth.wait_for_transaction_receipt(unwrap_hash, timeout=30)
                print(f"✅ WETH unwrap tamamlandı")
        except Exception as e:
            print(f"⚠️ WETH unwrap hatası (ETH olarak WETH'te kalabilir): {e}")

        # --- Step 4: PnL Hesaplama ---
        post_eth_balance = self.w3.eth.get_balance(self.wallet_address)
        # Alınan ETH = yeni bakiye - eski bakiye + gas harcanan
        eth_received = float(self.w3.from_wei(post_eth_balance - pre_eth_balance + gas_cost_wei, 'ether'))

        eth_spent = position["eth_spent"] * sell_ratio
        pnl_eth = eth_received - eth_spent
        pnl_percent = (pnl_eth / eth_spent * 100) if eth_spent > 0 else 0

        # Closed trade kaydı
        closed_trade = {
            "token": token_address,
            "symbol": position["symbol"],
            "entry_price": position["entry_price"],
            "exit_price": price_usd,
            "entry_mcap": position["entry_mcap"],
            "exit_mcap": token_info.get('mcap', 0),
            "eth_spent": eth_spent,
            "eth_received": eth_received,
            "pnl_eth": pnl_eth,
            "pnl_percent": pnl_percent,
            "entry_time": position["entry_time"],
            "exit_time": datetime.now().isoformat(),
            "buy_tx": position["buy_tx"],
            "sell_tx": tx_hash.hex(),
            "reason": reason,
            "sell_ratio": sell_ratio
        }

        self.portfolio["closed_trades"].append(closed_trade)
        self.portfolio["total_pnl_eth"] = self.portfolio.get("total_pnl_eth", 0) + pnl_eth

        if pnl_eth >= 0:
            self.portfolio["win_count"] = self.portfolio.get("win_count", 0) + 1
        else:
            self.portfolio["loss_count"] = self.portfolio.get("loss_count", 0) + 1
            self._record_daily_loss(abs(pnl_eth))

        # Pozisyonu güncelle
        if sell_ratio >= 1.0:
            # Full exit — pozisyonu kaldır
            self.portfolio["positions"] = [
                p for p in self.portfolio["positions"]
                if p["token"].lower() != token_address.lower()
            ]
        else:
            # Partial exit — miktarı güncelle
            position["eth_spent"] = position["eth_spent"] * (1 - sell_ratio)
            position["amount"] = position["amount"] * (1 - sell_ratio)
            remaining_raw = int(int(position["amount_raw"]) * (1 - sell_ratio))
            position["amount_raw"] = str(remaining_raw)

        self._save_portfolio()

        # Telegram bildirim
        action = "TP_HIT" if "TP" in reason else ("SL_HIT" if "SL" in reason else "SELL")
        _send_real_trade_alert(
            action=action,
            token_symbol=position["symbol"],
            token_address=token_address,
            eth_amount=eth_received,
            tx_hash=tx_hash.hex(),
            pnl_eth=pnl_eth,
            pnl_percent=pnl_percent,
            reason=reason,
            position_count=len(self._get_open_positions()),
            wallet_balance=float(self.w3.from_wei(
                self.w3.eth.get_balance(self.wallet_address), 'ether'
            ))
        )

        pnl_emoji = "🟢" if pnl_eth >= 0 else "🔴"
        print(f"{pnl_emoji} SELL: {position['symbol']} | {eth_received:.4f} ETH | PnL: {pnl_eth:+.4f} ETH ({pnl_percent:+.1f}%) | {reason}")
        return closed_trade

    # =========================================================================
    # TP/SL MONITOR
    # =========================================================================

    async def monitor_positions(self):
        """
        Her POSITION_CHECK_INTERVAL saniyede açık pozisyonları kontrol et.
        TP veya SL tetiklenirse otomatik sat.
        """
        print(f"📊 Pozisyon monitörü başladı (her {POSITION_CHECK_INTERVAL}sn)")

        while True:
            try:
                positions = list(self._get_open_positions())

                for pos in positions:
                    try:
                        token_info = get_token_info_dexscreener(pos["token"])
                        current_price = token_info.get('price', 0)

                        if current_price <= 0:
                            continue

                        entry_price = pos.get("entry_price", 0)
                        if entry_price <= 0:
                            continue

                        price_multiple = current_price / entry_price

                        # --- STOP LOSS ---
                        sl = pos.get("sl_multiplier", DEFAULT_SL_MULTIPLIER)
                        if price_multiple <= sl:
                            print(f"🛑 STOP LOSS: {pos['symbol']} @ {price_multiple:.2f}x (SL: {sl}x)")
                            self.sell_token(pos["token"], sell_ratio=1.0, reason=f"SL_{sl}x")
                            continue

                        # --- TAKE PROFIT ---
                        for tp_level in pos.get("tp_levels", []):
                            if not tp_level.get("hit", False) and price_multiple >= tp_level["multiplier"]:
                                sell_pct = tp_level["sell_pct"]
                                sell_ratio = sell_pct / 100.0

                                print(f"🎯 TP HIT: {pos['symbol']} @ {price_multiple:.2f}x → %{sell_pct} sat")
                                self.sell_token(
                                    pos["token"],
                                    sell_ratio=sell_ratio,
                                    reason=f"TP_{tp_level['multiplier']}x"
                                )
                                tp_level["hit"] = True
                                self._save_portfolio()
                                break

                    except Exception as e:
                        print(f"⚠️ Pozisyon kontrol hatası ({pos.get('symbol', '?')}): {e}")

                await asyncio.sleep(POSITION_CHECK_INTERVAL)

            except Exception as e:
                print(f"⚠️ Monitor loop hatası: {e}")
                await asyncio.sleep(60)


# =============================================================================
# TELEGRAM TRADE NOTIFICATION
# =============================================================================

def _send_real_trade_alert(
    action: str,
    token_symbol: str,
    token_address: str,
    eth_amount: float,
    tx_hash: str,
    pnl_eth: float = None,
    pnl_percent: float = None,
    reason: str = "",
    position_count: int = 0,
    wallet_balance: float = 0,
    extra_info: str = ""
) -> bool:
    """Gerçek trade Telegram bildirimi."""
    emoji_map = {
        "BUY": "🟢 ALIŞ",
        "SELL": "🔴 SATIŞ",
        "TP_HIT": "🎯 TAKE PROFIT",
        "SL_HIT": "🛑 STOP LOSS"
    }
    action_text = emoji_map.get(action, f"📊 {action}")

    pnl_line = ""
    if pnl_eth is not None:
        pnl_emoji = "✅" if pnl_eth >= 0 else "❌"
        pnl_line = f"\n{pnl_emoji} <b>PnL:</b> {pnl_eth:+.4f} ETH ({pnl_percent:+.1f}%)"

    reason_line = f"\n📋 <b>Sebep:</b> {reason}" if reason else ""
    extra_line = f"\n📈 {extra_info}" if extra_info else ""

    message = f"""
💎 <b>GERÇEK TRADE: {action_text}</b>

📊 <b>Token:</b> {token_symbol}
💵 <b>Miktar:</b> {eth_amount:.4f} ETH{pnl_line}{reason_line}{extra_line}

🔗 <a href="https://basescan.org/tx/{tx_hash}">BaseScan TX</a>

📈 <b>Açık pozisyon:</b> {position_count}
💼 <b>Bakiye:</b> {wallet_balance:.4f} ETH
"""
    return send_telegram_message(message.strip())
