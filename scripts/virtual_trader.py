"""
Virtual Trading System v2 — Strateji Bazlı Paper Trading
Senaryo 1: Confirmation Sniper (5dk bekle, MCap check geçerse al)
Senaryo 2: Speed Demon (anında al, büyük TP hedefle)

Her senaryo kendi TP/SL kurallarını uygular.
"""

import json
import os
from datetime import datetime, timezone, timedelta
from typing import Optional
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.telegram_alert import get_token_info_dexscreener
from scripts.database import load_portfolio_db, save_portfolio_db, is_db_available
from config.settings import SNIPER_CONFIG, DEMON_CONFIG

# Data dosya yolu
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORTFOLIO_FILE = os.path.join(BASE_DIR, "data", "virtual_portfolio.json")
TRADES_LOG = os.path.join(BASE_DIR, "logs", "trades.log")

# UTC+3
UTC_PLUS_3 = timezone(timedelta(hours=3))

# Sabit değerler
INITIAL_BALANCE = 0.05  # ETH (gerçekçi küçük bakiye)
ETH_PRICE_USD = 2500    # Yaklaşık ETH fiyatı


def ensure_logs_dir():
    """Logs klasörünü oluştur."""
    logs_dir = os.path.join(BASE_DIR, "logs")
    if not os.path.exists(logs_dir):
        os.makedirs(logs_dir)


def _default_portfolio() -> dict:
    """Yeni boş portföy şablonu — strateji bazlı."""
    return {
        "version": 2,
        "initial_balance": INITIAL_BALANCE,
        "created_at": datetime.now().isoformat(),
        "scenario1": {
            "name": "Confirmation Sniper",
            "strategy": "confirmation_sniper",
            "balance_eth": INITIAL_BALANCE / 2,
            "positions": [],
            "closed_trades": [],
            "total_pnl_eth": 0.0,
            "win_count": 0,
            "loss_count": 0,
            "consecutive_sl_count": 0,
            "cooldown_until": None
        },
        "scenario2": {
            "name": "Speed Demon",
            "strategy": "speed_demon",
            "balance_eth": INITIAL_BALANCE / 2,
            "positions": [],
            "closed_trades": [],
            "total_pnl_eth": 0.0,
            "win_count": 0,
            "loss_count": 0,
            "consecutive_sl_count": 0,
            "cooldown_until": None
        },
        "daily_snapshots": []
    }


def load_portfolio() -> dict:
    """Portföyü yükle. Önce DB, yoksa JSON fallback."""
    if is_db_available():
        db_data = load_portfolio_db()
        if db_data:
            # v1→v2 migration: eski portföy varsa yenisini oluştur
            if db_data.get("version") != 2:
                print("🔄 Virtual portfolio v1→v2 migration...")
                return _default_portfolio()
            return db_data

    if os.path.exists(PORTFOLIO_FILE):
        with open(PORTFOLIO_FILE, 'r') as f:
            data = json.load(f)
            if data.get("version") != 2:
                return _default_portfolio()
            return data

    return _default_portfolio()


def save_portfolio(data: dict):
    """Portföyü kaydet. DB + JSON (ikisine de yaz)."""
    data["updated_at"] = datetime.now().isoformat()

    if is_db_available():
        save_portfolio_db(data)

    try:
        with open(PORTFOLIO_FILE, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def log_trade(scenario: str, action: str, token: str, details: str):
    """Trade logla."""
    ensure_logs_dir()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_line = f"[{timestamp}] [{scenario}] {action}: {token} - {details}\n"

    try:
        with open(TRADES_LOG, 'a') as f:
            f.write(log_line)
    except Exception:
        pass

    print(f"📝 {log_line.strip()}")


def get_current_price(token_address: str) -> tuple:
    """Token'ın güncel fiyatını ve MCap'ini al."""
    info = get_token_info_dexscreener(token_address)
    return info.get('price', 0), info.get('mcap', 0)


def _get_strategy_config(scenario_num: int) -> dict:
    """Senaryo numarasına göre strateji config'ini döndür."""
    if scenario_num == 1:
        return SNIPER_CONFIG
    return DEMON_CONFIG


class VirtualTrader:
    """Strateji bazlı sanal trading sistemi."""

    def __init__(self):
        self.portfolio = load_portfolio()

    def save(self):
        """Değişiklikleri kaydet."""
        save_portfolio(self.portfolio)

    def get_scenario(self, scenario_num: int) -> dict:
        """Senaryo verilerini al."""
        key = f"scenario{scenario_num}"
        return self.portfolio.get(key, {})

    def _check_entry_conditions(self, scenario_num: int, entry_mcap: float,
                                 wallet_count: int = 3, change_5min_pct: float = None) -> tuple:
        """
        Giriş koşullarını kontrol et.

        Returns:
            (bool, str): (girilebilir mi, ret sebebi)
        """
        config = _get_strategy_config(scenario_num)
        scenario = self.portfolio[f"scenario{scenario_num}"]

        # Bakiye kontrolü
        if scenario["balance_eth"] < config["trade_size_eth"]:
            return False, f"Yetersiz bakiye ({scenario['balance_eth']:.4f} ETH)"

        # Max pozisyon kontrolü
        if len(scenario["positions"]) >= config["max_positions"]:
            return False, f"Max pozisyon limiti ({config['max_positions']})"

        # Max exposure kontrolü
        total_exposure = sum(p["eth_spent"] for p in scenario["positions"])
        if total_exposure + config["trade_size_eth"] > config["max_exposure_eth"]:
            return False, f"Max exposure limiti ({config['max_exposure_eth']} ETH)"

        # MCap filtresi
        if entry_mcap < config["min_mcap"]:
            return False, f"MCap çok düşük (${entry_mcap:,.0f} < ${config['min_mcap']:,.0f})"
        if entry_mcap > config["max_mcap"]:
            return False, f"MCap çok yüksek (${entry_mcap:,.0f} > ${config['max_mcap']:,.0f})"

        # Wallet count
        if wallet_count < config["min_wallet_count"]:
            return False, f"Yetersiz cüzdan ({wallet_count} < {config['min_wallet_count']})"

        # Saat filtresi (sadece Sniper)
        if config.get("active_hours"):
            now_tr = datetime.now(UTC_PLUS_3)
            start_h, end_h = config["active_hours"]
            if not (start_h <= now_tr.hour < end_h):
                return False, f"Aktif saat dışı (şimdi: {now_tr.hour}:00, aktif: {start_h}-{end_h})"

        # 5dk momentum filtresi (sadece Sniper)
        min_change = config.get("min_5min_change_pct")
        if min_change is not None:
            if change_5min_pct is None:
                return False, "5dk MCap verisi henüz yok"
            if change_5min_pct < min_change:
                return False, f"5dk momentum yetersiz (+{change_5min_pct:.1f}% < +{min_change}%)"

        # Cooldown kontrolü (Speed Demon — 3 ardışık SL sonrası)
        cooldown_until = scenario.get("cooldown_until")
        if cooldown_until:
            cooldown_dt = datetime.fromisoformat(cooldown_until)
            if datetime.now() < cooldown_dt:
                return False, f"Soğukluk süresinde (bitiş: {cooldown_until})"
            else:
                scenario["cooldown_until"] = None
                scenario["consecutive_sl_count"] = 0

        return True, "OK"

    def buy_token(self, scenario_num: int, token_address: str, token_symbol: str,
                  entry_mcap: float, wallet_count: int = 3, change_5min_pct: float = None) -> bool:
        """
        Strateji bazlı token alımı.

        Args:
            scenario_num: 1=Sniper, 2=Demon
            token_address: Token adresi
            token_symbol: Token sembolü
            entry_mcap: Alert anındaki MCap
            wallet_count: Alım yapan cüzdan sayısı
            change_5min_pct: 5dk MCap değişim % (Sniper için zorunlu)
        """
        config = _get_strategy_config(scenario_num)
        scenario = self.portfolio[f"scenario{scenario_num}"]
        tag = f"S{scenario_num}"

        # Duplicate kontrolü
        for pos in scenario["positions"]:
            if pos["token"].lower() == token_address.lower():
                print(f"⚠️ {tag}: {token_symbol} pozisyonu zaten mevcut")
                return False

        # Giriş koşullarını kontrol et
        can_enter, reason = self._check_entry_conditions(
            scenario_num, entry_mcap, wallet_count, change_5min_pct
        )
        if not can_enter:
            print(f"⏭️ {tag}: {token_symbol} SKIP → {reason}")
            return False

        # Fiyat bilgisi
        price, mcap = get_current_price(token_address)
        if price <= 0:
            print(f"⚠️ {tag}: {token_symbol} fiyat alınamadı")
            return False

        # Sabit pozisyon boyutu (strateji config'inden)
        eth_to_spend = config["trade_size_eth"]
        if eth_to_spend > scenario["balance_eth"]:
            eth_to_spend = scenario["balance_eth"]

        # Token miktarı hesapla
        usd_value = eth_to_spend * ETH_PRICE_USD
        token_amount = usd_value / price

        # TP/SL seviyeleri (strateji bazlı)
        tp_levels = [
            {"multiplier": tp["multiplier"], "sell_pct": tp["sell_pct"], "hit": False}
            for tp in config["tp_levels"]
        ]

        # Pozisyon aç
        position = {
            "token": token_address,
            "symbol": token_symbol,
            "amount": token_amount,
            "entry_price": price,
            "entry_mcap": entry_mcap,
            "entry_time": datetime.now().isoformat(),
            "eth_spent": eth_to_spend,
            "tp_levels": tp_levels,
            "sl_multiplier": config["sl_multiplier"],
            "time_sl_minutes": config.get("time_sl_minutes", 30),
            "strategy": config["name"],
        }

        scenario["positions"].append(position)
        scenario["balance_eth"] -= eth_to_spend
        self.save()

        change_info = f" | 5dk: +{change_5min_pct:.1f}%" if change_5min_pct else ""
        log_trade(tag, "BUY", token_symbol,
                  f"{eth_to_spend:.4f} ETH @ MCap ${entry_mcap/1e3:.0f}K{change_info}")

        emoji = "🎯" if scenario_num == 1 else "⚡"
        print(f"{emoji} {tag} BUY: {token_symbol} | {eth_to_spend:.4f} ETH | MCap: ${entry_mcap/1e3:.0f}K{change_info}")
        return True

    def sell_token(self, scenario_num: int, token_address: str,
                   sell_ratio: float = 1.0, reason: str = "MANUAL") -> bool:
        """Token sat (partial veya full)."""
        scenario = self.portfolio[f"scenario{scenario_num}"]
        tag = f"S{scenario_num}"

        position_idx = None
        for i, pos in enumerate(scenario["positions"]):
            if pos["token"].lower() == token_address.lower():
                position_idx = i
                break

        if position_idx is None:
            return False

        position = scenario["positions"][position_idx]
        price, mcap = get_current_price(token_address)
        if price <= 0:
            return False

        sell_amount = position["amount"] * sell_ratio
        sell_value_usd = sell_amount * price
        sell_value_eth = sell_value_usd / ETH_PRICE_USD

        entry_value_eth = position["eth_spent"] * sell_ratio
        pnl_eth = sell_value_eth - entry_value_eth
        pnl_percent = (pnl_eth / entry_value_eth) * 100 if entry_value_eth > 0 else 0

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
            "exit_time": datetime.now().isoformat(),
            "reason": reason,
            "sell_ratio": sell_ratio,
            "strategy": position.get("strategy", "unknown"),
        }

        scenario["closed_trades"].append(closed_trade)
        scenario["balance_eth"] += sell_value_eth
        scenario["total_pnl_eth"] += pnl_eth

        if pnl_eth >= 0:
            scenario["win_count"] += 1
            scenario["consecutive_sl_count"] = 0  # Reset
        else:
            scenario["loss_count"] += 1

        # Ardışık SL tracking (Speed Demon cooldown)
        if "SL" in reason:
            scenario["consecutive_sl_count"] = scenario.get("consecutive_sl_count", 0) + 1
            config = _get_strategy_config(scenario_num)
            cooldown_threshold = config.get("consecutive_sl_cooldown", 999)
            if scenario["consecutive_sl_count"] >= cooldown_threshold:
                cooldown_end = datetime.now() + timedelta(hours=1)
                scenario["cooldown_until"] = cooldown_end.isoformat()
                print(f"🧊 {tag}: {cooldown_threshold} ardışık SL → 1 saat soğukluk")

        # Pozisyonu güncelle veya kaldır
        if sell_ratio >= 1.0:
            scenario["positions"].pop(position_idx)
        else:
            position["amount"] -= sell_amount
            position["eth_spent"] -= entry_value_eth

        self.save()

        emoji = "🟢" if pnl_eth >= 0 else "🔴"
        log_trade(tag, "SELL", position["symbol"],
                  f"{sell_value_eth:.4f} ETH | PnL: {pnl_eth:+.4f} ETH ({pnl_percent:+.1f}%) | {reason}")

        print(f"{emoji} {tag} SELL: {position['symbol']} | PnL: {pnl_eth:+.4f} ETH ({pnl_percent:+.1f}%) | {reason}")
        return True

    def check_tp_sl(self):
        """
        Tüm açık pozisyonlar için TP/SL kontrol et.
        Periyodik olarak çağrılır (30sn veya daily_report'ta).
        """
        for scenario_num in [1, 2]:
            scenario = self.portfolio[f"scenario{scenario_num}"]
            tag = f"S{scenario_num}"
            positions_to_check = list(scenario["positions"])  # Copy

            for pos in positions_to_check:
                token_addr = pos["token"]
                symbol = pos["symbol"]

                price, mcap = get_current_price(token_addr)
                if price <= 0:
                    continue

                entry_price = pos["entry_price"]
                if entry_price <= 0:
                    continue

                multiplier = price / entry_price

                # --- STOP LOSS ---
                sl_mult = pos.get("sl_multiplier", 0.6)
                if multiplier <= sl_mult:
                    self.sell_token(scenario_num, token_addr, 1.0,
                                   reason=f"SL_{sl_mult}x")
                    continue

                # --- TIME-BASED SL ---
                time_sl_min = pos.get("time_sl_minutes", 30)
                entry_time = datetime.fromisoformat(pos["entry_time"])
                elapsed_minutes = (datetime.now() - entry_time).total_seconds() / 60

                # Zaman SL: süresi dolmuş VE TP1 henüz tutmamış
                tp_levels = pos.get("tp_levels", [])
                any_tp_hit = any(tp.get("hit", False) for tp in tp_levels)

                if elapsed_minutes >= time_sl_min and not any_tp_hit:
                    self.sell_token(scenario_num, token_addr, 1.0,
                                   reason=f"TIME_SL_{time_sl_min}min")
                    continue

                # --- TAKE PROFIT ---
                for tp in tp_levels:
                    if tp.get("hit", False):
                        continue
                    if multiplier >= tp["multiplier"]:
                        sell_ratio = tp["sell_pct"] / 100
                        tp["hit"] = True
                        self.sell_token(scenario_num, token_addr, sell_ratio,
                                       reason=f"TP_{tp['multiplier']}x")
                        break  # Bir seferde bir TP

            self.save()

    def get_portfolio_value(self, scenario_num: int) -> tuple:
        """Senaryo portföy değerini hesapla. Returns: (total_eth, unrealized_pnl_eth)"""
        scenario = self.portfolio[f"scenario{scenario_num}"]
        total_eth = scenario["balance_eth"]
        unrealized_pnl = 0.0

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

        initial_each = INITIAL_BALANCE / 2

        return {
            "date": datetime.now().strftime("%d.%m.%Y"),
            "scenario1": {
                "name": s1.get("name", "Confirmation Sniper"),
                "strategy": "confirmation_sniper",
                "initial": initial_each,
                "current": s1_value,
                "realized_pnl": s1["total_pnl_eth"],
                "unrealized_pnl": s1_unrealized,
                "total_pnl": s1_value - initial_each,
                "open_positions": len(s1["positions"]),
                "closed_trades": len(s1["closed_trades"]),
                "wins": s1["win_count"],
                "losses": s1["loss_count"],
                "win_rate": round(s1["win_count"] / max(s1["win_count"] + s1["loss_count"], 1) * 100, 1)
            },
            "scenario2": {
                "name": s2.get("name", "Speed Demon"),
                "strategy": "speed_demon",
                "initial": initial_each,
                "current": s2_value,
                "realized_pnl": s2["total_pnl_eth"],
                "unrealized_pnl": s2_unrealized,
                "total_pnl": s2_value - initial_each,
                "open_positions": len(s2["positions"]),
                "closed_trades": len(s2["closed_trades"]),
                "wins": s2["win_count"],
                "losses": s2["loss_count"],
                "win_rate": round(s2["win_count"] / max(s2["win_count"] + s2["loss_count"], 1) * 100, 1)
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

    # === Eski uyumluluk (wallet_monitor.py'den çağrılıyor olabilir) ===
    def buy_token_scenario1(self, token_address: str, token_symbol: str, entry_mcap: float):
        """Eski API uyumluluğu: Senaryo 1 alım."""
        return self.buy_token(1, token_address, token_symbol, entry_mcap)

    def buy_token_scenario2(self, token_address: str, token_symbol: str, entry_mcap: float):
        """Eski API uyumluluğu: Senaryo 2 alım."""
        return self.buy_token(2, token_address, token_symbol, entry_mcap)

    def check_and_sell_on_smart_money_sell(self, token_address: str, sell_ratio: float):
        """Smart money sattığında oransal olarak sat (Senaryo 1)."""
        return self.sell_token(1, token_address, sell_ratio, reason="SM_SELL")

    def check_and_sell_on_smartest_sell(self, token_address: str, sell_ratio: float):
        """Smartest wallet sattığında oransal olarak sat (Senaryo 2)."""
        return self.sell_token(2, token_address, sell_ratio, reason="SW_SELL")


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
    print("Virtual Trader v2 Test")
    print("=" * 50)

    trader = get_trader()
    summary = trader.get_daily_summary()

    print(f"\n📊 Portföy Durumu:")
    for s in ["scenario1", "scenario2"]:
        data = summary[s]
        print(f"\n  {data['name']}:")
        print(f"    Bakiye: {data['current']:.4f} ETH")
        print(f"    PnL: {data['total_pnl']:+.4f} ETH")
        print(f"    Win Rate: {data['win_rate']}% ({data['wins']}W/{data['losses']}L)")
        print(f"    Açık: {data['open_positions']} | Kapalı: {data['closed_trades']}")

    print(f"\n  Toplam: {summary['total']['current']:.4f} ETH (PnL: {summary['total']['total_pnl']:+.4f})")
