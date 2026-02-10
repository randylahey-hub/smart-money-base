"""
Self-Improving Engine - Orchestration modülü.

FAZ 9: Tüm alt sistemleri koordine eder.
- Tam döngü (haftalık): Analiz + pattern + ekleme/çıkarma
- Günlük değerlendirme: MCap check + wallet eval
- Per-alert: 5dk/30dk MCap timer
"""

import os
import sys
import time
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

UTC_PLUS_3 = timezone(timedelta(hours=3))

# Feature flag (default kapalı, test sonrası açılır)
SELF_IMPROVE_ENABLED = os.getenv("SELF_IMPROVE_ENABLED", "false").lower() == "true"


def run_full_cycle() -> dict:
    """
    Haftalık tam analiz döngüsü:
    1. Alert loglarını tara (FAZ 1)
    2. Short list oluştur (FAZ 2)
    3. Contracts check oluştur (FAZ 3)
    4. Trash calls belirle + cüzdan temizliği (FAZ 4)
    5. Pattern analizi (FAZ 5)
    6. Adaptif plan (FAZ 6)
    7. Cüzdan değerlendirmesi (FAZ 7)
    8. Yeni cüzdan keşfi (FAZ 8)
    """
    if not SELF_IMPROVE_ENABLED:
        print("⏸️ Self-improving engine devre dışı (SELF_IMPROVE_ENABLED=false)")
        return {"status": "disabled"}

    print("=" * 70)
    print("🔄 SELF-IMPROVING ENGINE - TAM DÖNGÜ")
    print(f"⏰ {datetime.now(UTC_PLUS_3).strftime('%Y-%m-%d %H:%M:%S')} UTC+3")
    print("=" * 70)

    results = {}
    start_time = time.time()

    try:
        # FAZ 1-4: Alert analizi
        print("\n📊 [1/4] Alert analizi başlıyor...")
        from scripts.alert_analyzer import run_full_alert_analysis
        analysis = run_full_alert_analysis()
        results["alert_analysis"] = {
            "total_alerts": analysis.get("counts", {}).get("total_alerts", 0),
            "short_list": analysis.get("counts", {}).get("short_list", 0),
            "contracts_check": analysis.get("counts", {}).get("contracts_check", 0),
            "trash_calls": analysis.get("counts", {}).get("trash_calls", 0),
        }

        # FAZ 5-6: Pattern analizi + Adaptif plan
        print("\n🔬 [2/4] Pattern analizi başlıyor...")
        from scripts.pattern_analyzer import generate_full_pattern_report
        patterns = generate_full_pattern_report(analysis)
        results["pattern_analysis"] = {
            "wallet_clusters": len(patterns.get("wallet_patterns", {}).get("cluster_groups", [])),
            "recommendations": len(patterns.get("recommendations", [])),
        }

        # FAZ 7: Cüzdan değerlendirmesi
        print("\n📋 [3/4] Cüzdan değerlendirmesi başlıyor...")
        from scripts.wallet_evaluator import run_daily_wallet_evaluation
        evaluation = run_daily_wallet_evaluation()
        results["wallet_evaluation"] = {
            "evaluated": evaluation.get("evaluated", 0),
            "removed": evaluation.get("actually_removed", 0),
            "warned": evaluation.get("flagged_warn", 0),
        }

        # FAZ 8: Yeni cüzdan keşfi
        print("\n🔍 [4/4] Cüzdan keşfi başlıyor...")
        from scripts.wallet_discoverer import discover_new_wallets
        discovery = discover_new_wallets(analysis.get("contracts_check", []))
        results["wallet_discovery"] = {
            "discovered": discovery.get("discovered", 0),
            "added": discovery.get("added", 0),
            "rejected": discovery.get("rejected", 0),
        }

    except Exception as e:
        print(f"❌ Tam döngü hatası: {e}")
        results["error"] = str(e)

    elapsed = time.time() - start_time
    results["elapsed_seconds"] = round(elapsed, 1)
    results["status"] = "completed"
    results["timestamp"] = datetime.now(UTC_PLUS_3).isoformat()

    # Özet
    print("\n" + "=" * 70)
    print("🔄 TAM DÖNGÜ TAMAMLANDI")
    print(f"⏱️ Süre: {elapsed:.1f} saniye")
    aa = results.get("alert_analysis", {})
    we = results.get("wallet_evaluation", {})
    wd = results.get("wallet_discovery", {})
    print(f"📊 Alertler: {aa.get('total_alerts', 0)} toplam, {aa.get('short_list', 0)} short_list, {aa.get('contracts_check', 0)} contracts_check")
    print(f"📋 Cüzdan eval: {we.get('removed', 0)} çıkarıldı, {we.get('warned', 0)} uyarı")
    print(f"🔍 Keşif: {wd.get('discovered', 0)} aday, {wd.get('added', 0)} eklendi")
    print("=" * 70)

    # Telegram bildirimi gönder
    _send_cycle_summary(results)

    return results


def run_daily_evaluation() -> dict:
    """
    Günlük hafif değerlendirme (20:30 UTC+3):
    - Bekleyen MCap check'leri işle
    - Cüzdan kalite değerlendirmesi
    """
    if not SELF_IMPROVE_ENABLED:
        return {"status": "disabled"}

    print("\n📋 Günlük değerlendirme başlıyor...")

    results = {}

    try:
        # Bekleyen MCap check'leri
        from scripts.mcap_checker import process_pending_checks, get_pending_count
        pending = get_pending_count()
        if pending > 0:
            check_results = process_pending_checks()
            results["mcap_checks"] = {
                "processed": len(check_results),
                "pending_remaining": get_pending_count(),
            }

        # Cüzdan değerlendirmesi
        from scripts.wallet_evaluator import run_daily_wallet_evaluation
        evaluation = run_daily_wallet_evaluation()
        results["wallet_evaluation"] = {
            "evaluated": evaluation.get("evaluated", 0),
            "removed": evaluation.get("actually_removed", 0),
            "warned": evaluation.get("flagged_warn", 0),
        }

    except Exception as e:
        print(f"❌ Günlük değerlendirme hatası: {e}")
        results["error"] = str(e)

    results["status"] = "completed"
    results["timestamp"] = datetime.now(UTC_PLUS_3).isoformat()
    return results


def run_per_alert_check(token_address: str, token_symbol: str, alert_mcap: int,
                         wallets_involved: list = None):
    """
    Her alert sonrası çağrılır:
    - 5dk ve 30dk MCap timer'ı planla
    """
    if not SELF_IMPROVE_ENABLED:
        return

    try:
        from scripts.mcap_checker import schedule_mcap_check
        schedule_mcap_check(
            token_address=token_address,
            token_symbol=token_symbol,
            alert_mcap=alert_mcap,
            wallets_involved=wallets_involved,
        )
    except Exception as e:
        print(f"⚠️ Per-alert check hatası: {e}")


def _send_cycle_summary(results: dict):
    """Tam döngü sonucu Telegram'a gönder."""
    try:
        from scripts.telegram_alert import send_status_update

        aa = results.get("alert_analysis", {})
        we = results.get("wallet_evaluation", {})
        wd = results.get("wallet_discovery", {})

        msg = "🔄 Self-Improving Engine Raporu\n\n"
        msg += f"📊 Alert Analizi:\n"
        msg += f"  Toplam: {aa.get('total_alerts', 0)}\n"
        msg += f"  ✅ Short list: {aa.get('short_list', 0)}\n"
        msg += f"  🏆 Contracts check: {aa.get('contracts_check', 0)}\n"
        msg += f"  🗑️ Trash calls: {aa.get('trash_calls', 0)}\n\n"
        msg += f"📋 Cüzdan Değerlendirmesi:\n"
        msg += f"  Değerlendirilen: {we.get('evaluated', 0)}\n"
        msg += f"  ➖ Çıkarılan: {we.get('removed', 0)}\n"
        msg += f"  ⚠️ Uyarı: {we.get('warned', 0)}\n\n"
        msg += f"🔍 Cüzdan Keşfi:\n"
        msg += f"  Aday: {wd.get('discovered', 0)}\n"
        msg += f"  ➕ Eklenen: {wd.get('added', 0)}\n\n"
        msg += f"⏱️ Süre: {results.get('elapsed_seconds', 0):.0f}s"

        send_status_update(msg)
    except Exception as e:
        print(f"⚠️ Telegram bildirimi gönderilemedi: {e}")


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Self-Improving Engine")
    parser.add_argument("--mode", choices=["full", "daily", "force"],
                       default="daily", help="Çalışma modu")
    args = parser.parse_args()

    if args.mode == "force":
        # Force: feature flag'i geç
        SELF_IMPROVE_ENABLED = True  # noqa: module-level override for CLI
        print("⚠️ Force mode: Feature flag override")
        run_full_cycle()
    elif args.mode == "full":
        run_full_cycle()
    else:
        run_daily_evaluation()
