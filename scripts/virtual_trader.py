"""
Virtual Trading System
Sanal 0.5 ETH bakiye ile iki senaryo:
- Senaryo 1: Smart Money Copy (alert'leri takip et)
- Senaryo 2: Smartest Wallets Copy (en iyi cüzdanları takip et)
"""

import json
import os
from datetime import datetime
from typing import Optional
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.telegram_alert import get_token_info_dexscreener

# Data dosya yolu
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORTFOLIO_FILE = os.path.join(BASE_DIR, "data", "virtual_portfolio.json")
TRADES_LOG = os.path.join(BASE_DIR, "logs", "trades.log")

# Sabit değerler
INITIAL_BALANCE = 0.5  # ETH
ETH_PRICE_USD = 2500   # Yaklaşık ETH fiyatı (dynamic yapılabilir)


def ensure_logs_dir():
    """Logs klasörünü oluştur."""
    logs_dir = os.path.join(BASE_DIR, "logs")
    if not os.path.exists(logs_dir):
        os.makedirs(logs_dir)


def load_portfolio() -> dict:
    """Portföyü yükle veya yeni oluştur."""
    if os.path.exists(PORTFOLIO_FILE):
        with open(PORTFOLIO_FILE, 'r') as f:
            return json.load(f)

    # Yeni portföy
    return {
        "initial_balance": INITIAL_BALANCE,
        "created_at": datetime.now().isoformat(),
        "scenario1": {
            "name": "Smart Money Copy",
            "balance_eth": INITIAL_BALANCE / 2,  # 0.25 ETH
            "positions": [],
            "closed_trades": [],
            "total_pnl_eth": 0.0,
            "win_count": 0,
            "loss_count": 0
        },
        "scenario2": {
            "name": "Smartest Wallets Copy",
            "balance_eth": INITIAL_BALANCE / 2,  # 0.25 ETH
            "positions": [],
            "closed_trades": [],
            "total_pnl_eth": 0.0,
            "win_count": 0,
            "loss_count": 0
        },
        "daily_snapshots": []
    }


def save_portfolio(data: dict):
    """Portföyü kaydet."""
    data["updated_at"] = datetime.now().isoformat()
    with open(PORTFOLIO_FILE, 'w') as f:
        json.dump(data, f, indent=2)


def log_trade(scenario: str, action: str, token: str, details: str):
    """Trade logla."""
    ensure_logs_dir()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_line = f"[{timestamp}] [{scenario}] {action}: {token} - {details}\n"

    with open(TRADES_LOG, 'a') as f:
        f.write(log_line)

    print(f"📝 {log_line.strip()}")


def get_current_price(token_address: str) -> tuple:
    """
    Token'ın güncel fiyatını ve MCap'ini al.

    Returns:
        (price_usd, mcap)
    """
    info = get_token_info_dexscreener(token_address)
    return info.get('price', 0), info.get('mcap', 0)


class VirtualTrader:
    """Sanal trading sistemi."""

    def __init__(self):
        self.portfolio = load_portfolio()

    def save(self):
        """Değişiklikleri kaydet."""
        save_portfolio(self.portfolio)

    def get_scenario(self, scenario_num: int) -> dict:
        """Senaryo verilerini al."""
        key = f"scenario{scenario_num}"
        return self.portfolio.get(key, {})

    def buy_token_scenario1(self, token_address: str, token_symbol: str, entry_mcap: float):
        """
        Senaryo 1: Smart Money Copy - Alert geldiğinde al.
        Bakiyenin yarısını kullan.
        """
        scenario = self.portfolio["scenario1"]
        available_balance = scenario["balance_eth"]

        # Bakiyenin yarısını kullan
        eth_to_spend = available_balance / 2

        if eth_to_spend < 0.001:  # Minimum işlem
            print(f"⚠️ S1: Yetersiz bakiye ({available_balance:.4f} ETH)")
            return False

        # Pozisyon zaten var mı?
        for pos in scenario["positions"]:
            if pos["token"].lower() == token_address.lower():
                print(f"⚠️ S1: {token_symbol} pozisyonu zaten mevcut")
                return False

        # Fiyat bilgisi
        price, mcap = get_current_price(token_address)
        if price <= 0:
            print(f"⚠️ S1: {token_symbol} fiyat alınamadı")
            return False

        # Token miktarı hesapla
        usd_value = eth_to_spend * ETH_PRICE_USD
        token_amount = usd_value / price

        # Pozisyon aç
        position = {
            "token": token_address,
            "symbol": token_symbol,
            "amount": token_amount,
            "entry_price": price,
            "entry_mcap": entry_mcap,
            "entry_time": datetime.now().isoformat(),
            "eth_spent": eth_to_spend
        }

        scenario["positions"].append(position)
        scenario["balance_eth"] -= eth_to_spend

        self.save()
        log_trade("S1", "BUY", token_symbol, f"{eth_to_spend:.4f} ETH @ MCap ${entry_mcap/1e6:.3f}M")

        print(f"✅ S1 BUY: {token_symbol} | {eth_to_spend:.4f} ETH | MCap: ${entry_mcap/1e6:.3f}M")
        return True

    def buy_token_scenario2(self, token_address: str, token_symbol: str, entry_mcap: float):
        """
        Senaryo 2: Smartest Wallets Copy - Smartest wallet aldığında al.
        Bakiyenin yarısını kullan.
        """
        scenario = self.portfolio["scenario2"]
        available_balance = scenario["balance_eth"]

        # Bakiyenin yarısını kullan
        eth_to_spend = available_balance / 2

        if eth_to_spend < 0.001:
            print(f"⚠️ S2: Yetersiz bakiye ({available_balance:.4f} ETH)")
            return False

        # Pozisyon zaten var mı?
        for pos in scenario["positions"]:
            if pos["token"].lower() == token_address.lower():
                print(f"⚠️ S2: {token_symbol} pozisyonu zaten mevcut")
                return False

        # Fiyat bilgisi
        price, mcap = get_current_price(token_address)
        if price <= 0:
            print(f"⚠️ S2: {token_symbol} fiyat alınamadı")
            return False

        # Token miktarı
        usd_value = eth_to_spend * ETH_PRICE_USD
        token_amount = usd_value / price

        position = {
            "token": token_address,
            "symbol": token_symbol,
            "amount": token_amount,
            "entry_price": price,
            "entry_mcap": entry_mcap,
            "entry_time": datetime.now().isoformat(),
            "eth_spent": eth_to_spend
        }

        scenario["positions"].append(position)
        scenario["balance_eth"] -= eth_to_spend

        self.save()
        log_trade("S2", "BUY", token_symbol, f"{eth_to_spend:.4f} ETH @ MCap ${entry_mcap/1e6:.3f}M")

        print(f"✅ S2 BUY: {token_symbol} | {eth_to_spend:.4f} ETH | MCap: ${entry_mcap/1e6:.3f}M")
        return True

    def sell_token(self, scenario_num: int, token_address: str, sell_ratio: float = 1.0):
        """
        Token sat.

        Args:
            scenario_num: 1 veya 2
            token_address: Token adresi
            sell_ratio: Satış oranı (0-1 arası, 1 = tamamını sat)
        """
        scenario = self.portfolio[f"scenario{scenario_num}"]

        # Pozisyonu bul
        position_idx = None
        for i, pos in enumerate(scenario["positions"]):
            if pos["token"].lower() == token_address.lower():
                position_idx = i
                break

        if position_idx is None:
            print(f"⚠️ S{scenario_num}: Pozisyon bulunamadı")
            return False

        position = scenario["positions"][position_idx]

        # Güncel fiyat
        price, mcap = get_current_price(token_address)
        if price <= 0:
            print(f"⚠️ S{scenario_num}: Güncel fiyat alınamadı")
            return False

        # Satılacak miktar
        sell_amount = position["amount"] * sell_ratio
        sell_value_usd = sell_amount * price
        sell_value_eth = sell_value_usd / ETH_PRICE_USD

        # PnL hesapla
        entry_value_eth = position["eth_spent"] * sell_ratio
        pnl_eth = sell_value_eth - entry_value_eth
        pnl_percent = (pnl_eth / entry_value_eth) * 100 if entry_value_eth > 0 else 0

        # Kapatılan trade kaydı
        closed_trade = {
            "token": position["token"],
            "symbol": position["symbol"],
            "entry_price": position["entry_price"],
            "exit_price": price,
            "entry_mcap": position["entry_mcap"],
            "exit_mcap": mcap,
            "eth_spent": entry_value_eth,
            "eth_received": sell_value_eth,
            "pnl_eth": pnl_eth,
            "pnl_percent": pnl_percent,
            "entry_time": position["entry_time"],
            "exit_time": datetime.now().isoformat()
        }

        scenario["closed_trades"].append(closed_trade)
        scenario["balance_eth"] += sell_value_eth
        scenario["total_pnl_eth"] += pnl_eth

        if pnl_eth >= 0:
            scenario["win_count"] += 1
        else:
            scenario["loss_count"] += 1

        # Pozisyonu güncelle veya kaldır
        if sell_ratio >= 1.0:
            scenario["positions"].pop(position_idx)
        else:
            position["amount"] -= sell_amount
            position["eth_spent"] -= entry_value_eth

        self.save()

        emoji = "🟢" if pnl_eth >= 0 else "🔴"
        log_trade(
            f"S{scenario_num}", "SELL",
            position["symbol"],
            f"{sell_value_eth:.4f} ETH | PnL: {pnl_eth:+.4f} ETH ({pnl_percent:+.1f}%)"
        )

        print(f"{emoji} S{scenario_num} SELL: {position['symbol']} | {sell_value_eth:.4f} ETH | PnL: {pnl_eth:+.4f} ETH ({pnl_percent:+.1f}%)")
        return True

    def check_and_sell_on_smart_money_sell(self, token_address: str, sell_ratio: float):
        """
        Smart money sattığında oransal olarak sat (Senaryo 1).
        """
        return self.sell_token(1, token_address, sell_ratio)

    def check_and_sell_on_smartest_sell(self, token_address: str, sell_ratio: float):
        """
        Smartest wallet sattığında oransal olarak sat (Senaryo 2).
        """
        return self.sell_token(2, token_address, sell_ratio)

    def get_portfolio_value(self, scenario_num: int) -> tuple:
        """
        Senaryo portföy değerini hesapla.

        Returns:
            (total_eth, unrealized_pnl_eth)
        """
        scenario = self.portfolio[f"scenario{scenario_num}"]

        # Cash balance
        total_eth = scenario["balance_eth"]
        unrealized_pnl = 0.0

        # Açık pozisyonlar
        for pos in scenario["positions"]:
            price, _ = get_current_price(pos["token"])
            if price > 0:
                current_value_usd = pos["amount"] * price
                current_value_eth = current_value_usd / ETH_PRICE_USD
                total_eth += current_value_eth
                unrealized_pnl += current_value_eth - pos["eth_spent"]

        return total_eth, unrealized_pnl

    def get_daily_summary(self) -> dict:
        """Günlük özet bilgilerini döndür."""
        s1_value, s1_unrealized = self.get_portfolio_value(1)
        s2_value, s2_unrealized = self.get_portfolio_value(2)

        s1 = self.portfolio["scenario1"]
        s2 = self.portfolio["scenario2"]

        return {
            "date": datetime.now().strftime("%d.%m.%Y"),
            "scenario1": {
                "name": s1["name"],
                "initial": INITIAL_BALANCE / 2,
                "current": s1_value,
                "realized_pnl": s1["total_pnl_eth"],
                "unrealized_pnl": s1_unrealized,
                "total_pnl": s1_value - (INITIAL_BALANCE / 2),
                "open_positions": len(s1["positions"]),
                "closed_trades": len(s1["closed_trades"]),
                "wins": s1["win_count"],
                "losses": s1["loss_count"]
            },
            "scenario2": {
                "name": s2["name"],
                "initial": INITIAL_BALANCE / 2,
                "current": s2_value,
                "realized_pnl": s2["total_pnl_eth"],
                "unrealized_pnl": s2_unrealized,
                "total_pnl": s2_value - (INITIAL_BALANCE / 2),
                "open_positions": len(s2["positions"]),
                "closed_trades": len(s2["closed_trades"]),
                "wins": s2["win_count"],
                "losses": s2["loss_count"]
            },
            "total": {
                "initial": INITIAL_BALANCE,
                "current": s1_value + s2_value,
                "total_pnl": (s1_value + s2_value) - INITIAL_BALANCE
            }
        }

    def take_daily_snapshot(self):
        """Günlük snapshot al."""
        summary = self.get_daily_summary()
        self.portfolio["daily_snapshots"].append({
            "timestamp": datetime.now().isoformat(),
            "summary": summary
        })
        self.save()


# Global trader instance
_trader = None


def get_trader() -> VirtualTrader:
    """Global trader instance döndür."""
    global _trader
    if _trader is None:
        _trader = VirtualTrader()
    return _trader


# Test
if __name__ == "__main__":
    print("Virtual Trader Test")
    print("=" * 50)

    trader = get_trader()
    summary = trader.get_daily_summary()

    print(f"\n📊 Portföy Durumu:")
    print(f"Senaryo 1: {summary['scenario1']['current']:.4f} ETH")
    print(f"Senaryo 2: {summary['scenario2']['current']:.4f} ETH")
    print(f"Toplam: {summary['total']['current']:.4f} ETH")
