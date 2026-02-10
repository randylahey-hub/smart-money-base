"""
Wallet Evaluator - Sürekli cüzdan kalite değerlendirmesi.

FAZ 7: Günlük cüzdan eleme sistemi
- Her cüzdanın trash_ratio, hit_rate hesaplanır
- Yüksek trash oranı olan cüzdanlar uyarılır/çıkarılır
- Günlük raporda eklenen/silinen cüzdan sayısı belirtilir
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.database import (
    get_all_token_evaluations,
    get_wallet_alert_participation,
    is_db_available
)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
UTC_PLUS_3 = timezone(timedelta(hours=3))

# Eşikler
TRASH_WARN_THRESHOLD = 0.70      # %70+ trash oranı → uyarı
TRASH_REMOVE_THRESHOLD = 0.90    # %90+ trash oranı → çıkarma
MIN_APPEARANCES_FOR_REMOVAL = 5  # Minimum 5 alert'te görünmüş olmalı
MIN_WALLET_COUNT = 50            # Minimum cüzdan sayısı korunur


def evaluate_wallet_quality(wallet_address: str, token_evaluations: list = None,
                             wallet_participation: list = None) -> dict:
    """
    Tek bir cüzdanın kalitesini değerlendir.

    Returns:
        {
            "address": str,
            "total_appearances": int,
            "short_list_hits": int,
            "contracts_check_hits": int,
            "trash_hits": int,
            "hit_rate": float,    # short_list / total
            "trash_rate": float,  # trash / total
            "score": float,       # 0-1 arası kalite skoru
            "flag": "keep" | "warn" | "remove"
        }
    """
    if token_evaluations is None:
        token_evaluations = get_all_token_evaluations()
    if wallet_participation is None:
        wallet_participation = get_wallet_alert_participation()

    wallet_addr = wallet_address.lower()

    # Bu cüzdanın katıldığı tokenlar
    wallet_tokens = set()
    for wp in wallet_participation:
        if wp["wallet_address"] == wallet_addr:
            wallet_tokens.add(wp["token_address"])

    if not wallet_tokens:
        return {
            "address": wallet_addr,
            "total_appearances": 0,
            "short_list_hits": 0,
            "contracts_check_hits": 0,
            "trash_hits": 0,
            "hit_rate": 0,
            "trash_rate": 0,
            "score": 0.5,  # Veri yok, nötr
            "flag": "keep",
        }

    # Token sınıflandırmaları
    token_classes = {}
    for te in token_evaluations:
        token_classes[te["token_address"]] = te.get("classification", "unknown")

    # Sayımlar
    short_list_hits = sum(1 for t in wallet_tokens if token_classes.get(t) in ("short_list", "contracts_check"))
    contracts_check_hits = sum(1 for t in wallet_tokens if token_classes.get(t) == "contracts_check")
    trash_hits = sum(1 for t in wallet_tokens if token_classes.get(t) == "trash")
    total = len(wallet_tokens)

    hit_rate = short_list_hits / total if total > 0 else 0
    trash_rate = trash_hits / total if total > 0 else 0

    # Skor hesaplama (0-1)
    # hit_rate ağırlıklı, trash_rate cezalı
    score = hit_rate * 0.7 + (1 - trash_rate) * 0.3

    # Flag belirleme
    if total >= MIN_APPEARANCES_FOR_REMOVAL and trash_rate >= TRASH_REMOVE_THRESHOLD:
        flag = "remove"
    elif trash_rate >= TRASH_WARN_THRESHOLD:
        flag = "warn"
    else:
        flag = "keep"

    return {
        "address": wallet_addr,
        "total_appearances": total,
        "short_list_hits": short_list_hits,
        "contracts_check_hits": contracts_check_hits,
        "trash_hits": trash_hits,
        "hit_rate": round(hit_rate, 3),
        "trash_rate": round(trash_rate, 3),
        "score": round(score, 3),
        "flag": flag,
    }


def run_daily_wallet_evaluation() -> dict:
    """
    Tüm smart money cüzdanlarını değerlendir.

    Returns:
        {
            "evaluated": int,
            "flagged_warn": int,
            "flagged_remove": int,
            "removed": int,
            "wallet_details": [...]
        }
    """
    print("=" * 60)
    print("📋 GÜNLÜK CÜZDAN DEĞERLENDİRMESİ")
    print("=" * 60)

    # Smart money listesini yükle
    wallets_file = os.path.join(DATA_DIR, "smart_money_final.json")
    if not os.path.exists(wallets_file):
        return {"error": "smart_money_final.json bulunamadı"}

    with open(wallets_file, 'r') as f:
        wallet_data = json.load(f)
    current_wallets = [w.lower() for w in wallet_data.get("wallets", [])]

    # Verileri yükle (tek seferde, her cüzdan için tekrar çekmemek için)
    token_evaluations = get_all_token_evaluations()
    wallet_participation = get_wallet_alert_participation()

    # Her cüzdanı değerlendir
    evaluations = []
    for wallet in current_wallets:
        ev = evaluate_wallet_quality(wallet, token_evaluations, wallet_participation)
        evaluations.append(ev)

    # İstatistikler
    keep_count = sum(1 for e in evaluations if e["flag"] == "keep")
    warn_count = sum(1 for e in evaluations if e["flag"] == "warn")
    remove_count = sum(1 for e in evaluations if e["flag"] == "remove")
    no_data = sum(1 for e in evaluations if e["total_appearances"] == 0)

    # Çıkarılacak cüzdanlar
    wallets_to_remove = [e["address"] for e in evaluations if e["flag"] == "remove"]
    actually_removed = 0

    if wallets_to_remove:
        # Minimum cüzdan sayısı kontrolü
        remaining_after = len(current_wallets) - len(wallets_to_remove)
        if remaining_after < MIN_WALLET_COUNT:
            max_removable = len(current_wallets) - MIN_WALLET_COUNT
            # En yüksek trash_rate olanlardan başla
            remove_sorted = sorted(
                [e for e in evaluations if e["flag"] == "remove"],
                key=lambda x: x["trash_rate"],
                reverse=True
            )
            wallets_to_remove = [e["address"] for e in remove_sorted[:max_removable]]
            print(f"⚠️ Minimum {MIN_WALLET_COUNT} cüzdan korunması: {len(remove_sorted)} yerine {len(wallets_to_remove)} çıkarılacak")

        if wallets_to_remove:
            from scripts.alert_analyzer import remove_wallets_from_smart_list
            result = remove_wallets_from_smart_list(wallets_to_remove, MIN_WALLET_COUNT)
            actually_removed = result.get("removed", 0)

    # Sonuçları kaydet
    evaluation_result = {
        "evaluation_time": datetime.now(UTC_PLUS_3).isoformat(),
        "total_wallets": len(current_wallets),
        "evaluated": len(evaluations),
        "flagged_warn": warn_count,
        "flagged_remove": remove_count,
        "actually_removed": actually_removed,
        "no_data_wallets": no_data,
        "keep_count": keep_count,
        "top_scorers": sorted(
            [e for e in evaluations if e["total_appearances"] >= 2],
            key=lambda x: x["score"],
            reverse=True
        )[:15],
        "worst_performers": sorted(
            [e for e in evaluations if e["total_appearances"] >= 2],
            key=lambda x: x["trash_rate"],
            reverse=True
        )[:15],
        "removed_wallets": wallets_to_remove[:actually_removed] if actually_removed > 0 else [],
    }

    _save_json("wallet_evaluation.json", evaluation_result)

    # Özet
    print(f"\n📊 Değerlendirme Sonuçları:")
    print(f"  Toplam cüzdan: {len(current_wallets)}")
    print(f"  ✅ Keep: {keep_count}")
    print(f"  ⚠️ Warn: {warn_count}")
    print(f"  🚩 Remove flag: {remove_count}")
    print(f"  🗑️ Gerçekten çıkarılan: {actually_removed}")
    print(f"  📭 Veri yok: {no_data}")
    print("=" * 60)

    return evaluation_result


def get_daily_wallet_report_summary() -> str:
    """Günlük Telegram raporuna eklenecek cüzdan özeti."""
    eval_file = os.path.join(DATA_DIR, "wallet_evaluation.json")
    if not os.path.exists(eval_file):
        return ""

    try:
        with open(eval_file, 'r') as f:
            data = json.load(f)

        removed = data.get("actually_removed", 0)
        warn = data.get("flagged_warn", 0)
        total = data.get("total_wallets", 0)

        # Eklenen cüzdanlar (wallet_discoverer'dan)
        discovered_file = os.path.join(DATA_DIR, "discovered_wallets.json")
        added = 0
        if os.path.exists(discovered_file):
            with open(discovered_file, 'r') as f:
                disc = json.load(f)
                today = datetime.now(UTC_PLUS_3).strftime("%Y-%m-%d")
                added = sum(1 for w in disc.get("wallets", [])
                          if w.get("added_date", "").startswith(today))

        lines = ["\n👛 Cüzdan Durumu:"]
        lines.append(f"  Toplam: {total}")
        if added > 0:
            lines.append(f"  ➕ Bugün eklenen: {added}")
        if removed > 0:
            lines.append(f"  ➖ Bugün çıkarılan: {removed}")
        if warn > 0:
            lines.append(f"  ⚠️ Uyarı: {warn} cüzdan")

        return "\n".join(lines)
    except Exception:
        return ""


def _save_json(filename: str, data):
    """Data dizinine JSON kaydet."""
    filepath = os.path.join(DATA_DIR, filename)
    with open(filepath, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    print(f"💾 Kaydedildi: {filename}")


if __name__ == "__main__":
    result = run_daily_wallet_evaluation()
