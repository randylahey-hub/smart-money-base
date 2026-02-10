"""
Alert Analyzer - Tarihsel alert verilerini analiz eder.

FAZ 1: Alert log tarama
FAZ 2: Short list tokens (5dk MCap kontrolu)
FAZ 3: Contracts check (30dk MCap kontrolu)
FAZ 4: Trash calls + cuzdan temizligi
"""

import json
import os
import sys
import time
import requests
from datetime import datetime, timezone, timedelta
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.database import (
    get_all_alert_snapshots,
    get_all_trade_signals_history,
    get_wallet_alert_participation,
    save_token_evaluation,
    get_all_token_evaluations,
    is_db_available
)

# UTC+3 timezone
UTC_PLUS_3 = timezone(timedelta(hours=3))

# DexScreener rate limit
DEXSCREENER_DELAY = 0.35  # 350ms arasi

# Esikler
SHORT_LIST_THRESHOLD = 0.20   # %20 artis = short_list
CONTRACTS_CHECK_THRESHOLD = 0.50  # %50 artis = contracts_check
DEAD_TOKEN_MCAP = 20000  # $20K alti = olu token

# Data dizini
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


# =============================================================================
# FAZ 1: ALERT LOG TARAMA
# =============================================================================

def get_all_historical_alerts() -> list:
    """
    DB'den tum gecmis alertleri cek.
    alert_snapshots + trade_signals birlestirilir.
    Zamanlar UTC+3.
    """
    alerts = []

    # 1. Alert snapshots
    snapshots = get_all_alert_snapshots()
    for s in snapshots:
        alerts.append({
            "source": "alert_snapshot",
            "token_address": s["token_address"],
            "token_symbol": s.get("token_symbol", "UNKNOWN"),
            "alert_mcap": s.get("alert_mcap", 0),
            "wallet_count": s.get("wallet_count", 0),
            "alert_block": s.get("alert_block", 0),
            "created_at": s.get("created_at", ""),
            "created_at_utc3": _to_utc3(s.get("created_at", "")),
        })

    # 2. Trade signals (snapshot olmayan ek sinyaller)
    signals = get_all_trade_signals_history()
    snapshot_tokens = {(a["token_address"], a["created_at"][:16]) for a in alerts if a["created_at"]}

    for sig in signals:
        # Zaten snapshot'ta varsa ekleme
        key = (sig["token_address"], sig["created_at"][:16] if sig["created_at"] else "")
        if key in snapshot_tokens:
            continue
        alerts.append({
            "source": "trade_signal",
            "token_address": sig["token_address"],
            "token_symbol": sig.get("token_symbol", "UNKNOWN"),
            "alert_mcap": sig.get("entry_mcap", 0),
            "wallet_count": sig.get("wallet_count", 0),
            "trigger_type": sig.get("trigger_type", ""),
            "created_at": sig.get("created_at", ""),
            "created_at_utc3": _to_utc3(sig.get("created_at", "")),
        })

    # Zamana gore sirala
    alerts.sort(key=lambda x: x.get("created_at", ""))

    print(f"📊 Toplam {len(alerts)} alert bulundu (snapshots: {len(snapshots)}, signals: {len(signals)})")
    return alerts


def get_alert_summary(alerts: list = None) -> dict:
    """Alert ozet istatistikleri."""
    if alerts is None:
        alerts = get_all_historical_alerts()

    if not alerts:
        return {"total_alerts": 0, "date_range": "N/A", "unique_tokens": 0}

    unique_tokens = set(a["token_address"] for a in alerts)
    dates = [a["created_at"] for a in alerts if a["created_at"]]

    return {
        "total_alerts": len(alerts),
        "unique_tokens": len(unique_tokens),
        "date_range": f"{dates[0][:10] if dates else 'N/A'} ~ {dates[-1][:10] if dates else 'N/A'}",
        "avg_wallet_count": round(sum(a.get("wallet_count", 0) for a in alerts) / len(alerts), 1) if alerts else 0,
        "avg_mcap": round(sum(a.get("alert_mcap", 0) for a in alerts) / len(alerts), 0) if alerts else 0,
    }


# =============================================================================
# FAZ 2: SHORT LIST TOKENS (5dk MCap kontrolu)
# =============================================================================

def fetch_current_mcap(token_address: str) -> dict:
    """DexScreener'dan token'in guncel MCap ve bilgilerini al."""
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
        resp = requests.get(url, timeout=10)
        data = resp.json()

        pairs = data.get("pairs", [])
        if not pairs:
            return {"mcap": 0, "price": 0, "liquidity": 0, "volume_24h": 0, "symbol": "UNKNOWN"}

        # En yuksek likiditeli pair'i sec
        best = max(pairs, key=lambda p: float(p.get("liquidity", {}).get("usd", 0) or 0))
        return {
            "mcap": float(best.get("marketCap", 0) or 0),
            "price": float(best.get("priceUsd", 0) or 0),
            "liquidity": float(best.get("liquidity", {}).get("usd", 0) or 0),
            "volume_24h": float(best.get("volume", {}).get("h24", 0) or 0),
            "symbol": best.get("baseToken", {}).get("symbol", "UNKNOWN"),
            "pair_created": best.get("pairCreatedAt", ""),
        }
    except Exception as e:
        print(f"⚠️ DexScreener hatasi ({token_address[:10]}...): {e}")
        return {"mcap": 0, "price": 0, "liquidity": 0, "volume_24h": 0, "symbol": "UNKNOWN"}


def build_short_list_tokens(alerts: list) -> tuple:
    """
    FAZ 2: Her alert icin MCap kontrolu yap.
    - Guncel MCap, alert MCap'den %20+ yukarida → short_list
    - MCap <= $20K → dead token (trash)

    Returns: (short_list, remaining_alerts)
    """
    short_list = []
    remaining = []

    print(f"\n📈 Short List analizi basliyor ({len(alerts)} alert)...")

    for i, alert in enumerate(alerts):
        token_addr = alert["token_address"]
        alert_mcap = alert.get("alert_mcap", 0)
        token_symbol = alert.get("token_symbol", "UNKNOWN")

        if not alert_mcap or alert_mcap <= 0:
            remaining.append(alert)
            continue

        # DexScreener'dan guncel MCap
        current = fetch_current_mcap(token_addr)
        current_mcap = current["mcap"]

        # Rate limit
        time.sleep(DEXSCREENER_DELAY)

        # Degisim yuzdesi
        if alert_mcap > 0:
            change_pct = (current_mcap - alert_mcap) / alert_mcap
        else:
            change_pct = 0

        alert["current_mcap"] = current_mcap
        alert["change_5min_pct"] = round(change_pct, 4)

        # Token olmus mu? (MCap <= $20K)
        if current_mcap <= DEAD_TOKEN_MCAP:
            alert["classification"] = "trash"
            alert["trash_reason"] = f"Dead token (MCap: ${current_mcap:,.0f})"
            remaining.append(alert)
            print(f"  💀 [{i+1}/{len(alerts)}] {token_symbol}: Dead (MCap ${current_mcap:,.0f})")
            continue

        # %20+ artis → short_list
        if change_pct >= SHORT_LIST_THRESHOLD:
            alert["classification"] = "short_list"
            short_list.append(alert)
            print(f"  ✅ [{i+1}/{len(alerts)}] {token_symbol}: +{change_pct*100:.1f}% (${alert_mcap:,.0f} → ${current_mcap:,.0f})")
        else:
            alert["classification"] = "not_short_list"
            remaining.append(alert)
            print(f"  ❌ [{i+1}/{len(alerts)}] {token_symbol}: {change_pct*100:+.1f}% (${alert_mcap:,.0f} → ${current_mcap:,.0f})")

        # DB'ye kaydet
        save_token_evaluation(
            token_address=token_addr,
            token_symbol=token_symbol,
            alert_mcap=alert_mcap,
            alert_time=alert.get("created_at"),
            mcap_5min=int(current_mcap),
            change_5min_pct=round(change_pct * 100, 2),
            classification=alert.get("classification", "unknown"),
            wallets_involved=alert.get("wallets_involved", [])
        )

    print(f"\n📊 Short List sonucu: {len(short_list)} token (toplam {len(alerts)}'den)")
    return short_list, remaining


# =============================================================================
# FAZ 3: CONTRACTS CHECK (30dk MCap kontrolu)
# =============================================================================

def build_contracts_check(short_list: list) -> tuple:
    """
    FAZ 3: short_list tokenlari icin ek MCap kontrolu.
    Alert MCap vs guncel MCap %50+ artis → contracts_check

    NOT: Guncel MCap zaten FAZ 2'de cekildi.
    Burada alert_mcap vs current_mcap (guncel) karsilastirilir.
    30dk sonrasi MCap olarak guncel MCap kullanilir (gecmis alertler icin en iyi proxy).

    Returns: (contracts_check, short_only)
    """
    contracts_check = []
    short_only = []

    print(f"\n🔍 Contracts Check analizi ({len(short_list)} short_list token)...")

    for alert in short_list:
        alert_mcap = alert.get("alert_mcap", 0)
        current_mcap = alert.get("current_mcap", 0)
        token_symbol = alert.get("token_symbol", "UNKNOWN")

        if alert_mcap <= 0:
            short_only.append(alert)
            continue

        change_pct = (current_mcap - alert_mcap) / alert_mcap if alert_mcap > 0 else 0

        if change_pct >= CONTRACTS_CHECK_THRESHOLD:
            alert["classification"] = "contracts_check"
            alert["change_30min_pct"] = round(change_pct, 4)
            contracts_check.append(alert)
            print(f"  🏆 {token_symbol}: +{change_pct*100:.1f}% (${alert_mcap:,.0f} → ${current_mcap:,.0f})")

            # DB guncelle
            save_token_evaluation(
                token_address=alert["token_address"],
                token_symbol=token_symbol,
                alert_mcap=alert_mcap,
                alert_time=alert.get("created_at"),
                mcap_30min=int(current_mcap),
                change_30min_pct=round(change_pct * 100, 2),
                classification="contracts_check"
            )
        else:
            short_only.append(alert)
            print(f"  📋 {token_symbol}: +{change_pct*100:.1f}% (short_list'te kaldi)")

    print(f"\n📊 Contracts Check: {len(contracts_check)} token ({len(short_list)} short_list'ten)")
    return contracts_check, short_only


# =============================================================================
# FAZ 4: TRASH CALLS + CUZDAN TEMIZLIGI
# =============================================================================

def identify_trash_calls(all_alerts: list, short_list: list) -> list:
    """Short list'te olmayan alertler = trash_calls."""
    short_tokens = {a["token_address"] for a in short_list}
    trash = [a for a in all_alerts if a["token_address"] not in short_tokens]

    for t in trash:
        if t.get("classification") != "trash":
            t["classification"] = "trash"
            t["trash_reason"] = t.get("trash_reason", "Not in short_list")

    print(f"🗑️ Trash calls: {len(trash)} token")
    return trash


def identify_trash_only_wallets(trash_calls: list, short_list: list,
                                 wallet_participation: list = None) -> list:
    """
    SADECE trash_calls'ta gorunen cuzdanlar (short_list'te hic yok).
    Bu cuzdanlar smart_money_final.json'dan cikarilacak.
    """
    if wallet_participation is None:
        wallet_participation = get_wallet_alert_participation()

    # Token bazli cuzdan katilimi haritasi
    short_tokens = {a["token_address"] for a in short_list}
    trash_tokens = {a["token_address"] for a in trash_calls}

    # Her cuzdanin hangi tokenlerde gorundugunuu bul
    wallet_tokens = defaultdict(set)
    for wp in wallet_participation:
        wallet_tokens[wp["wallet_address"]].add(wp["token_address"])

    # Sadece trash'ta gorunen cuzdanlar
    trash_only_wallets = []
    for wallet, tokens in wallet_tokens.items():
        in_short = bool(tokens & short_tokens)
        in_trash = bool(tokens & trash_tokens)

        if in_trash and not in_short:
            trash_only_wallets.append({
                "wallet": wallet,
                "trash_token_count": len(tokens & trash_tokens),
                "total_tokens": len(tokens),
            })

    trash_only_wallets.sort(key=lambda x: x["trash_token_count"], reverse=True)
    print(f"🚩 Sadece trash'ta gorunen cuzdan: {len(trash_only_wallets)}")
    return trash_only_wallets


def remove_wallets_from_smart_list(wallets_to_remove: list, min_remaining: int = 50) -> dict:
    """
    Cuzdanlari smart_money_final.json'dan cikar.
    Oncesinde backup olusturur.
    Minimum cuzdan sayisi korur.
    """
    wallets_file = os.path.join(DATA_DIR, "smart_money_final.json")

    if not os.path.exists(wallets_file):
        return {"error": "smart_money_final.json bulunamadi"}

    # Yedek olustur
    with open(wallets_file, 'r') as f:
        data = json.load(f)

    backup_name = f"smart_money_final_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    backup_path = os.path.join(DATA_DIR, backup_name)
    with open(backup_path, 'w') as f:
        json.dump(data, f, indent=2)
    print(f"💾 Backup olusturuldu: {backup_name}")

    current_wallets = [w.lower() for w in data.get("wallets", [])]
    remove_set = {w.lower() for w in wallets_to_remove}

    # Minimum kontrol
    remaining_count = len(current_wallets) - len(remove_set & set(current_wallets))
    if remaining_count < min_remaining:
        max_removable = len(current_wallets) - min_remaining
        print(f"⚠️ Minimum {min_remaining} cuzdan korumasi: {len(remove_set)} yerine max {max_removable} cikarilacak")
        # En cok trash olan cuzdanlardan baslayarak cikar
        remove_set = set(list(remove_set)[:max_removable])

    # Cikarma islemi
    new_wallets = [w for w in current_wallets if w not in remove_set]
    removed = len(current_wallets) - len(new_wallets)

    data["wallets"] = new_wallets
    data["count"] = len(new_wallets)

    with open(wallets_file, 'w') as f:
        json.dump(data, f, indent=2)

    # Cikarilan cuzdanlari kaydet
    removed_file = os.path.join(DATA_DIR, "removed_wallets.json")
    removed_data = {"wallets": list(remove_set & set(current_wallets)),
                    "removed_at": datetime.now(UTC_PLUS_3).isoformat(),
                    "reason": "trash_only_wallet",
                    "count": removed}
    with open(removed_file, 'w') as f:
        json.dump(removed_data, f, indent=2)

    print(f"✅ {removed} cuzdan cikarildi. Kalan: {len(new_wallets)}")
    return {"removed": removed, "remaining": len(new_wallets), "backup": backup_name}


# =============================================================================
# ANA ANALIZ FONKSIYONU
# =============================================================================

def run_full_alert_analysis() -> dict:
    """
    Faz 1-4 komple analiz:
    1. Tum alertleri tara
    2. Short list olustur
    3. Contracts check olustur
    4. Trash calls belirle + cuzdan temizligi
    """
    print("=" * 60)
    print("🔬 ALERT ANALIZI BASLADI")
    print("=" * 60)

    # FAZ 1: Alert loglari
    alerts = get_all_historical_alerts()
    if not alerts:
        print("❌ Hic alert bulunamadi!")
        return {"error": "No alerts found"}

    summary = get_alert_summary(alerts)
    print(f"\n📋 Alert Ozeti:")
    print(f"  Toplam: {summary['total_alerts']}")
    print(f"  Tarih araligi: {summary['date_range']}")
    print(f"  Benzersiz token: {summary['unique_tokens']}")
    print(f"  Ortalama MCap: ${summary['avg_mcap']:,.0f}")

    # FAZ 2: Short list
    short_list, remaining = build_short_list_tokens(alerts)

    # FAZ 3: Contracts check
    contracts_check, short_only = build_contracts_check(short_list)

    # FAZ 4: Trash calls
    trash_calls = identify_trash_calls(alerts, short_list)

    # Wallet participation analizi
    wallet_participation = get_wallet_alert_participation()
    trash_only_wallets = identify_trash_only_wallets(trash_calls, short_list, wallet_participation)

    # Sonuclari kaydet
    results = {
        "analysis_time": datetime.now(UTC_PLUS_3).isoformat(),
        "summary": summary,
        "short_list_tokens": [{
            "token_address": a["token_address"],
            "token_symbol": a.get("token_symbol", ""),
            "alert_mcap": a.get("alert_mcap", 0),
            "current_mcap": a.get("current_mcap", 0),
            "change_pct": round(a.get("change_5min_pct", 0) * 100, 2),
            "created_at_utc3": a.get("created_at_utc3", ""),
        } for a in short_list],
        "contracts_check": [{
            "token_address": a["token_address"],
            "token_symbol": a.get("token_symbol", ""),
            "alert_mcap": a.get("alert_mcap", 0),
            "current_mcap": a.get("current_mcap", 0),
            "change_pct": round(a.get("change_30min_pct", 0) * 100, 2),
            "created_at_utc3": a.get("created_at_utc3", ""),
        } for a in contracts_check],
        "trash_calls": [{
            "token_address": a["token_address"],
            "token_symbol": a.get("token_symbol", ""),
            "alert_mcap": a.get("alert_mcap", 0),
            "current_mcap": a.get("current_mcap", 0),
            "trash_reason": a.get("trash_reason", ""),
            "created_at_utc3": a.get("created_at_utc3", ""),
        } for a in trash_calls],
        "trash_only_wallets": trash_only_wallets,
        "counts": {
            "total_alerts": len(alerts),
            "short_list": len(short_list),
            "contracts_check": len(contracts_check),
            "trash_calls": len(trash_calls),
            "trash_only_wallets": len(trash_only_wallets),
        }
    }

    # JSON dosyalarina kaydet
    _save_json("short_list_tokens.json", results["short_list_tokens"])
    _save_json("contracts_check.json", results["contracts_check"])
    _save_json("trash_calls.json", results["trash_calls"])
    _save_json("alert_analysis.json", results)

    print("\n" + "=" * 60)
    print("📊 ANALIZ SONUCLARI:")
    print(f"  ✅ Short List: {len(short_list)} token")
    print(f"  🏆 Contracts Check: {len(contracts_check)} token")
    print(f"  🗑️ Trash Calls: {len(trash_calls)} token")
    print(f"  🚩 Trash-only cuzdanlar: {len(trash_only_wallets)}")
    print("=" * 60)

    return results


# =============================================================================
# YARDIMCI FONKSIYONLAR
# =============================================================================

def _to_utc3(timestamp_str: str) -> str:
    """ISO timestamp'i UTC+3'e cevir."""
    if not timestamp_str:
        return ""
    try:
        # ISO format parse
        if 'T' in timestamp_str:
            dt = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
        else:
            dt = datetime.fromisoformat(timestamp_str)

        # UTC varsayarak UTC+3'e cevir
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt_utc3 = dt.astimezone(UTC_PLUS_3)
        return dt_utc3.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return timestamp_str


def _save_json(filename: str, data):
    """Data dizinine JSON dosyasi kaydet."""
    filepath = os.path.join(DATA_DIR, filename)
    with open(filepath, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    print(f"💾 Kaydedildi: {filename}")


# =============================================================================
# CLI CALISTIRMA
# =============================================================================

if __name__ == "__main__":
    results = run_full_alert_analysis()

    if "error" not in results:
        print(f"\nSonuclar data/ dizinine kaydedildi.")

        # Trash-only cuzdanlar varsa bilgilendir
        if results["trash_only_wallets"]:
            print(f"\n⚠️ {len(results['trash_only_wallets'])} cuzdan sadece trash_calls'ta gorunuyor.")
            print("Bu cuzdanlari cikarma icin: remove_wallets_from_smart_list()")
