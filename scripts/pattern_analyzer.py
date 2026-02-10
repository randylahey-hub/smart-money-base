"""
Pattern Analyzer - Alert verilerinden pattern çıkarımı.

FAZ 5: 3 tip pattern analizi (cüzdan, token, zamanlama)
FAZ 6: Adaptif plan oluşturma

Çıktı: data/pattern_analysis.json + data/adaptive_plan.json
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from collections import defaultdict, Counter
import math

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
UTC_PLUS_3 = timezone(timedelta(hours=3))


# =============================================================================
# FAZ 5A: CÜZDAN BAZLI PATTERNLER
# =============================================================================

def analyze_wallet_patterns(short_list: list, contracts_check: list,
                             trash_calls: list, wallet_participation: list = None) -> dict:
    """
    Cüzdan bazlı pattern analizi:
    - Cüzdan kesişimi (hangi listeler arasında)
    - Ortak alıcılar (short_list tokenlarında)
    - Cüzdan kümeleme (birlikte hareket edenler)
    """

    # Token bazlı cüzdan haritası (wallet_participation'dan veya wallets_involved'dan)
    token_wallets = defaultdict(set)  # token → {wallet1, wallet2, ...}
    wallet_tokens = defaultdict(set)  # wallet → {token1, token2, ...}

    if wallet_participation:
        for wp in wallet_participation:
            token_wallets[wp["token_address"]].add(wp["wallet_address"])
            wallet_tokens[wp["wallet_address"]].add(wp["token_address"])

    # Liste bazlı cüzdan setleri
    short_tokens = {a["token_address"] for a in short_list}
    check_tokens = {a["token_address"] for a in contracts_check}
    trash_tokens = {a["token_address"] for a in trash_calls}

    short_wallets = set()
    check_wallets = set()
    trash_wallets = set()

    for wallet, tokens in wallet_tokens.items():
        if tokens & short_tokens:
            short_wallets.add(wallet)
        if tokens & check_tokens:
            check_wallets.add(wallet)
        if tokens & trash_tokens:
            trash_wallets.add(wallet)

    # Kesişim analizi
    overlap = {
        "short_and_contracts": len(short_wallets & check_wallets),
        "short_and_trash": len(short_wallets & trash_wallets),
        "contracts_and_trash": len(check_wallets & trash_wallets),
        "only_short": len(short_wallets - trash_wallets - check_wallets),
        "only_trash": len(trash_wallets - short_wallets - check_wallets),
        "only_contracts": len(check_wallets - short_wallets - trash_wallets),
        "all_three": len(short_wallets & check_wallets & trash_wallets),
    }

    # En çok short_list'te görünen cüzdanlar (top performers)
    wallet_short_count = Counter()
    for token in short_tokens:
        for wallet in token_wallets.get(token, set()):
            wallet_short_count[wallet] += 1

    top_performers = [
        {"wallet": w, "short_list_appearances": c}
        for w, c in wallet_short_count.most_common(20)
    ]

    # Cüzdan kümeleme (co-occurrence) - aynı tokenleri alan cüzdanlar
    wallet_pairs = Counter()
    for token, wallets in token_wallets.items():
        wallets_list = sorted(wallets)
        for i in range(len(wallets_list)):
            for j in range(i + 1, len(wallets_list)):
                wallet_pairs[(wallets_list[i], wallets_list[j])] += 1

    # En sık birlikte hareket eden cüzdan çiftleri
    cluster_groups = [
        {"wallet_a": pair[0], "wallet_b": pair[1], "co_appearances": count}
        for pair, count in wallet_pairs.most_common(15)
        if count >= 2
    ]

    # Cüzdan başarı oranı (hit rate)
    wallet_hit_rates = []
    for wallet in wallet_tokens:
        total = len(wallet_tokens[wallet])
        short = len(wallet_tokens[wallet] & short_tokens)
        trash = len(wallet_tokens[wallet] & trash_tokens)
        if total >= 2:  # minimum 2 alert'te görünmüş olsun
            wallet_hit_rates.append({
                "wallet": wallet,
                "total_alerts": total,
                "short_list_hits": short,
                "trash_hits": trash,
                "hit_rate": round(short / total, 3) if total > 0 else 0,
                "trash_rate": round(trash / total, 3) if total > 0 else 0,
            })

    wallet_hit_rates.sort(key=lambda x: x["hit_rate"], reverse=True)

    return {
        "wallet_overlap": overlap,
        "top_performers": top_performers,
        "cluster_groups": cluster_groups,
        "wallet_hit_rates": wallet_hit_rates[:30],  # Top 30
        "total_unique_wallets": len(wallet_tokens),
        "short_list_wallet_count": len(short_wallets),
        "trash_wallet_count": len(trash_wallets),
    }


# =============================================================================
# FAZ 5B: TOKEN BAZLI PATTERNLER
# =============================================================================

def analyze_token_patterns(short_list: list, contracts_check: list,
                            trash_calls: list) -> dict:
    """
    Token bazlı pattern analizi:
    - MCap dağılımı (winning vs losing)
    - Likidite seviyeleri
    - Alım miktarları
    """

    def _stats(values: list) -> dict:
        if not values:
            return {"min": 0, "max": 0, "median": 0, "avg": 0, "count": 0}
        sorted_v = sorted(values)
        n = len(sorted_v)
        return {
            "min": sorted_v[0],
            "max": sorted_v[-1],
            "median": sorted_v[n // 2],
            "avg": round(sum(sorted_v) / n, 2),
            "count": n,
        }

    # MCap dağılımları
    short_mcaps = [a.get("alert_mcap", 0) for a in short_list if a.get("alert_mcap", 0) > 0]
    check_mcaps = [a.get("alert_mcap", 0) for a in contracts_check if a.get("alert_mcap", 0) > 0]
    trash_mcaps = [a.get("alert_mcap", 0) for a in trash_calls if a.get("alert_mcap", 0) > 0]

    # Wallet count dağılımları
    short_wallets = [a.get("wallet_count", 0) for a in short_list if a.get("wallet_count")]
    trash_wallets_count = [a.get("wallet_count", 0) for a in trash_calls if a.get("wallet_count")]

    # MCap değişim dağılımları
    short_changes = [a.get("change_5min_pct", 0) * 100 for a in short_list if a.get("change_5min_pct")]
    trash_changes = [a.get("change_5min_pct", 0) * 100 for a in trash_calls if a.get("change_5min_pct")]

    # MCap aralıklarına göre gruplama
    def _mcap_bucket(mcap):
        if mcap < 10000:
            return "<$10K"
        elif mcap < 50000:
            return "$10K-$50K"
        elif mcap < 100000:
            return "$50K-$100K"
        elif mcap < 200000:
            return "$100K-$200K"
        elif mcap < 300000:
            return "$200K-$300K"
        else:
            return "$300K+"

    short_buckets = Counter(_mcap_bucket(m) for m in short_mcaps)
    trash_buckets = Counter(_mcap_bucket(m) for m in trash_mcaps)

    # Winning token ortak özellikleri
    winning_characteristics = []
    if short_mcaps:
        avg_short_mcap = sum(short_mcaps) / len(short_mcaps)
        winning_characteristics.append(f"Ortalama alert MCap: ${avg_short_mcap:,.0f}")
    if short_wallets:
        avg_short_wallets = sum(short_wallets) / len(short_wallets)
        winning_characteristics.append(f"Ortalama cüzdan sayısı: {avg_short_wallets:.1f}")

    # Losing token özellikleri
    losing_characteristics = []
    if trash_mcaps:
        avg_trash_mcap = sum(trash_mcaps) / len(trash_mcaps)
        losing_characteristics.append(f"Ortalama alert MCap: ${avg_trash_mcap:,.0f}")
    if trash_wallets_count:
        avg_trash_wallets = sum(trash_wallets_count) / len(trash_wallets_count)
        losing_characteristics.append(f"Ortalama cüzdan sayısı: {avg_trash_wallets:.1f}")

    return {
        "winning_mcap_range": _stats(short_mcaps),
        "contracts_mcap_range": _stats(check_mcaps),
        "losing_mcap_range": _stats(trash_mcaps),
        "winning_wallet_count": _stats(short_wallets),
        "losing_wallet_count": _stats(trash_wallets_count),
        "winning_change_pct": _stats(short_changes),
        "losing_change_pct": _stats(trash_changes),
        "mcap_distribution": {
            "short_list": dict(short_buckets),
            "trash_calls": dict(trash_buckets),
        },
        "winning_characteristics": winning_characteristics,
        "losing_characteristics": losing_characteristics,
    }


# =============================================================================
# FAZ 5C: ZAMANLAMA PATTERNLERİ
# =============================================================================

def analyze_timing_patterns(short_list: list, contracts_check: list,
                             trash_calls: list) -> dict:
    """
    Zamanlama bazlı pattern analizi:
    - Saat dilimi bazlı başarı oranı (UTC+3)
    - Gün bazlı dağılım
    - MCap büyüme hızı
    """

    def _extract_hour(alert):
        ts = alert.get("created_at_utc3") or alert.get("created_at", "")
        if not ts:
            return None
        try:
            if 'T' in ts:
                dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
            else:
                parts = ts.split(' ')
                if len(parts) >= 2:
                    dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
                else:
                    return None
            return dt.hour
        except Exception:
            return None

    def _extract_day(alert):
        ts = alert.get("created_at_utc3") or alert.get("created_at", "")
        if not ts:
            return None
        try:
            if 'T' in ts:
                dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
            else:
                parts = ts.split(' ')
                if len(parts) >= 2:
                    dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
                else:
                    return None
            return dt.strftime("%A")  # Gün adı
        except Exception:
            return None

    # Saat bazlı dağılım
    short_hours = Counter(h for h in (_extract_hour(a) for a in short_list) if h is not None)
    trash_hours = Counter(h for h in (_extract_hour(a) for a in trash_calls) if h is not None)

    # Her saat için başarı oranı
    hourly_stats = {}
    all_hours = set(short_hours.keys()) | set(trash_hours.keys())
    for hour in sorted(all_hours):
        s = short_hours.get(hour, 0)
        t = trash_hours.get(hour, 0)
        total = s + t
        hourly_stats[f"{hour:02d}:00"] = {
            "short_list": s,
            "trash_calls": t,
            "total": total,
            "success_rate": round(s / total, 3) if total > 0 else 0,
        }

    # En iyi saatler
    best_hours = sorted(
        [(k, v) for k, v in hourly_stats.items() if v["total"] >= 2],
        key=lambda x: x[1]["success_rate"],
        reverse=True
    )
    best_hours_list = [{"hour": h, **stats} for h, stats in best_hours[:5]]

    # En kötü saatler
    worst_hours = sorted(
        [(k, v) for k, v in hourly_stats.items() if v["total"] >= 2],
        key=lambda x: x[1]["success_rate"]
    )
    worst_hours_list = [{"hour": h, **stats} for h, stats in worst_hours[:5]]

    # Gün bazlı dağılım
    short_days = Counter(d for d in (_extract_day(a) for a in short_list) if d is not None)
    trash_days = Counter(d for d in (_extract_day(a) for a in trash_calls) if d is not None)

    daily_stats = {}
    all_days = set(short_days.keys()) | set(trash_days.keys())
    for day in all_days:
        s = short_days.get(day, 0)
        t = trash_days.get(day, 0)
        total = s + t
        daily_stats[day] = {
            "short_list": s,
            "trash_calls": t,
            "total": total,
            "success_rate": round(s / total, 3) if total > 0 else 0,
        }

    # MCap büyüme hızı analizi
    growth_speeds = []
    for a in short_list:
        alert_mcap = a.get("alert_mcap", 0)
        current_mcap = a.get("current_mcap", 0)
        if alert_mcap > 0 and current_mcap > 0:
            growth = (current_mcap - alert_mcap) / alert_mcap
            growth_speeds.append(round(growth * 100, 2))

    avg_growth = round(sum(growth_speeds) / len(growth_speeds), 2) if growth_speeds else 0
    max_growth = max(growth_speeds) if growth_speeds else 0

    return {
        "hourly_stats": hourly_stats,
        "best_hours_utc3": best_hours_list,
        "worst_hours_utc3": worst_hours_list,
        "daily_stats": daily_stats,
        "mcap_growth_speed": {
            "avg_growth_pct": avg_growth,
            "max_growth_pct": max_growth,
            "growth_distribution": _growth_distribution(growth_speeds),
        },
    }


def _growth_distribution(speeds: list) -> dict:
    """Büyüme hızı dağılımı."""
    if not speeds:
        return {}
    buckets = {"20-50%": 0, "50-100%": 0, "100-200%": 0, "200-500%": 0, "500%+": 0}
    for s in speeds:
        if s < 50:
            buckets["20-50%"] += 1
        elif s < 100:
            buckets["50-100%"] += 1
        elif s < 200:
            buckets["100-200%"] += 1
        elif s < 500:
            buckets["200-500%"] += 1
        else:
            buckets["500%+"] += 1
    return buckets


# =============================================================================
# FAZ 6: ADAPTİF PLAN
# =============================================================================

def generate_adaptive_plan(patterns: dict) -> dict:
    """
    Pattern analizine dayalı filtre iyileştirme önerileri.
    trash_calls'ı minimize etmek için.
    """
    recommendations = []

    # 1. MCap aralığı önerisi
    token_patterns = patterns.get("token_patterns", {})
    winning_mcap = token_patterns.get("winning_mcap_range", {})
    losing_mcap = token_patterns.get("losing_mcap_range", {})

    if winning_mcap.get("median", 0) > 0 and losing_mcap.get("median", 0) > 0:
        if winning_mcap["median"] < losing_mcap["median"]:
            recommendations.append({
                "type": "filter_adjustment",
                "param": "MAX_MCAP",
                "current": 300000,
                "suggested": int(winning_mcap["max"] * 1.2) if winning_mcap["max"] > 0 else 300000,
                "reason": f"Winning tokenlar genelde ${winning_mcap['median']:,.0f} MCap'te, "
                         f"losing tokenlar ${losing_mcap['median']:,.0f}'de. MCap üst limiti düşürülebilir.",
                "confidence": "medium",
            })

    # 2. Saat bazlı filtre
    timing = patterns.get("timing_patterns", {})
    worst_hours = timing.get("worst_hours_utc3", [])
    if worst_hours and worst_hours[0].get("success_rate", 1) < 0.2:
        bad_hours = [h["hour"] for h in worst_hours if h.get("success_rate", 1) < 0.2]
        if bad_hours:
            recommendations.append({
                "type": "time_filter",
                "param": "ALERT_BLACKOUT_HOURS",
                "current": "yok",
                "suggested": bad_hours,
                "reason": f"Bu saatlerde başarı oranı %20'nin altında: {', '.join(bad_hours)}",
                "confidence": "low",
            })

    # 3. Cüzdan sayısı eşiği
    wallet_patterns = patterns.get("wallet_patterns", {})
    winning_wc = token_patterns.get("winning_wallet_count", {})
    losing_wc = token_patterns.get("losing_wallet_count", {})

    if winning_wc.get("avg", 0) > losing_wc.get("avg", 0):
        recommendations.append({
            "type": "filter_adjustment",
            "param": "ALERT_THRESHOLD",
            "current": 3,
            "suggested": max(3, int(winning_wc["avg"])),
            "reason": f"Winning alertlerde ortalama {winning_wc['avg']:.1f} cüzdan, "
                     f"losing'de {losing_wc['avg']:.1f}. Eşik artırılabilir.",
            "confidence": "medium",
        })

    # 4. Cüzdan eleme önerisi
    wallet_hit_rates = wallet_patterns.get("wallet_hit_rates", [])
    low_performers = [w for w in wallet_hit_rates if w.get("trash_rate", 0) > 0.8 and w.get("total_alerts", 0) >= 3]
    if low_performers:
        recommendations.append({
            "type": "wallet_cleanup",
            "param": "auto_remove_wallets",
            "count": len(low_performers),
            "wallets": [w["wallet"] for w in low_performers[:10]],
            "reason": f"{len(low_performers)} cüzdan %80+ trash oranına sahip. Eleme önerilir.",
            "confidence": "high",
        })

    # 5. Sürekli eleme sistemi parametreleri
    recommendations.append({
        "type": "continuous_evaluation",
        "params": {
            "daily_eval_time": "20:30 UTC+3",
            "trash_warn_threshold": 0.70,
            "trash_remove_threshold": 0.90,
            "min_appearances_for_removal": 5,
            "min_wallet_count": 50,
            "new_wallet_weekly_limit": 80,
        },
        "reason": "Günlük cüzdan değerlendirme parametreleri",
        "confidence": "high",
    })

    plan = {
        "generated_at": datetime.now(UTC_PLUS_3).isoformat(),
        "total_recommendations": len(recommendations),
        "recommendations": recommendations,
        "expected_impact": {
            "trash_reduction_estimate": "Cüzdan eleme ile %20-40 trash azalması beklenir",
            "quality_improvement": "Hit rate artışı ile alert kalitesi yükselir",
        },
        "notes": [
            "Bu öneriler otomatik uygulanmaz, onay gerektirir",
            "Her önerinin güven seviyesi belirtilmiştir (low/medium/high)",
            "Yeterli veri biriktikçe öneriler daha isabetli olur",
        ],
    }

    return plan


# =============================================================================
# ANA PATTERN ANALİZİ
# =============================================================================

def generate_full_pattern_report(analysis_data: dict = None) -> dict:
    """
    Tüm pattern analizlerini çalıştır ve yapısal JSON oluştur.

    Args:
        analysis_data: alert_analyzer.run_full_alert_analysis() sonucu.
                       None ise dosyadan yükler.
    """
    if analysis_data is None:
        analysis_file = os.path.join(DATA_DIR, "alert_analysis.json")
        if os.path.exists(analysis_file):
            with open(analysis_file, 'r') as f:
                analysis_data = json.load(f)
        else:
            print("❌ alert_analysis.json bulunamadı. Önce alert_analyzer'ı çalıştırın.")
            return {}

    short_list = analysis_data.get("short_list_tokens", [])
    contracts_check = analysis_data.get("contracts_check", [])
    trash_calls = analysis_data.get("trash_calls", [])

    # Wallet participation (DB'den veya analiz verisinden)
    from scripts.database import get_wallet_alert_participation
    wallet_participation = get_wallet_alert_participation()

    print("=" * 60)
    print("🔬 PATTERN ANALİZİ BAŞLADI")
    print("=" * 60)

    # 5A: Cüzdan patternleri
    print("\n📊 A. Cüzdan Bazlı Patternler...")
    wallet_patterns = analyze_wallet_patterns(short_list, contracts_check, trash_calls, wallet_participation)

    # 5B: Token patternleri
    print("📊 B. Token Bazlı Patternler...")
    token_patterns = analyze_token_patterns(short_list, contracts_check, trash_calls)

    # 5C: Zamanlama patternleri
    print("📊 C. Zamanlama Patternleri...")
    timing_patterns = analyze_timing_patterns(short_list, contracts_check, trash_calls)

    # Tam rapor
    patterns = {
        "generated_at": datetime.now(UTC_PLUS_3).isoformat(),
        "data_summary": {
            "short_list_count": len(short_list),
            "contracts_check_count": len(contracts_check),
            "trash_calls_count": len(trash_calls),
        },
        "wallet_patterns": wallet_patterns,
        "token_patterns": token_patterns,
        "timing_patterns": timing_patterns,
    }

    # FAZ 6: Adaptif plan
    print("\n📋 Adaptif Plan oluşturuluyor...")
    adaptive_plan = generate_adaptive_plan(patterns)
    patterns["recommendations"] = adaptive_plan.get("recommendations", [])

    # Kaydet
    _save_json("pattern_analysis.json", patterns)
    _save_json("adaptive_plan.json", adaptive_plan)

    # Özet yazdır
    print("\n" + "=" * 60)
    print("📊 PATTERN ANALİZİ SONUÇLARI:")
    print(f"  Cüzdan kümeleri: {len(wallet_patterns.get('cluster_groups', []))}")
    print(f"  Top performers: {len(wallet_patterns.get('top_performers', []))}")
    wo = wallet_patterns.get("wallet_overlap", {})
    print(f"  Cüzdan kesişimi: short∩trash={wo.get('short_and_trash', 0)}, only_trash={wo.get('only_trash', 0)}")
    print(f"  Winning MCap median: ${token_patterns.get('winning_mcap_range', {}).get('median', 0):,.0f}")
    print(f"  Losing MCap median: ${token_patterns.get('losing_mcap_range', {}).get('median', 0):,.0f}")
    best_h = timing_patterns.get("best_hours_utc3", [])
    if best_h:
        print(f"  En iyi saat: {best_h[0].get('hour', 'N/A')} (başarı: {best_h[0].get('success_rate', 0)*100:.0f}%)")
    print(f"  Öneri sayısı: {len(adaptive_plan.get('recommendations', []))}")
    print("=" * 60)

    return patterns


def _save_json(filename: str, data):
    """Data dizinine JSON kaydet."""
    filepath = os.path.join(DATA_DIR, filename)
    with open(filepath, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    print(f"💾 Kaydedildi: {filename}")


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    patterns = generate_full_pattern_report()
    if patterns:
        print("\nPattern analizi tamamlandı. Dosyalar data/ dizininde.")
