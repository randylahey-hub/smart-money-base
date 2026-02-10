"""
MCap Checker - Alert sonrası zamanlı MCap kontrolü.

Gelecek alertler için:
- 5dk sonra MCap kontrolü → short_list_tokens
- 30dk sonra MCap kontrolü → contracts_check

Ana monitoring loop'a entegre edilir.
"""

import time
import threading
from datetime import datetime, timezone, timedelta
from collections import deque

from scripts.alert_analyzer import fetch_current_mcap, SHORT_LIST_THRESHOLD, CONTRACTS_CHECK_THRESHOLD, DEAD_TOKEN_MCAP
from scripts.database import save_token_evaluation

# UTC+3
UTC_PLUS_3 = timezone(timedelta(hours=3))

# Bekleyen kontroller (thread-safe deque)
_pending_checks = deque()
_lock = threading.Lock()


def schedule_mcap_check(token_address: str, token_symbol: str, alert_mcap: int,
                         wallets_involved: list = None, alert_time: str = None):
    """
    Alert sonrası 5dk ve 30dk MCap kontrolü planla.

    Args:
        token_address: Token contract adresi
        token_symbol: Token sembolü
        alert_mcap: Alert anındaki MCap
        wallets_involved: Alım yapan cüzdanlar
        alert_time: Alert zamanı (ISO format)
    """
    now = time.time()

    if not alert_time:
        alert_time = datetime.now(UTC_PLUS_3).isoformat()

    check_data = {
        "token_address": token_address,
        "token_symbol": token_symbol,
        "alert_mcap": alert_mcap,
        "wallets_involved": wallets_involved or [],
        "alert_time": alert_time,
    }

    with _lock:
        # 5dk kontrolü
        _pending_checks.append({
            **check_data,
            "check_type": "5min",
            "check_at": now + 300,  # 5 dakika
            "threshold": SHORT_LIST_THRESHOLD,
        })

        # 30dk kontrolü
        _pending_checks.append({
            **check_data,
            "check_type": "30min",
            "check_at": now + 1800,  # 30 dakika
            "threshold": CONTRACTS_CHECK_THRESHOLD,
        })

    print(f"⏰ MCap check planlandı: {token_symbol} → 5dk ve 30dk sonra")


def process_pending_checks() -> list:
    """
    Zamanı gelen kontrolleri işle.
    Ana monitoring loop'tan periyodik çağrılır.

    Returns: İşlenen kontrollerin sonuçları
    """
    now = time.time()
    results = []
    checks_to_process = []

    with _lock:
        remaining = deque()
        while _pending_checks:
            check = _pending_checks.popleft()
            if check["check_at"] <= now:
                checks_to_process.append(check)
            else:
                remaining.append(check)
        _pending_checks.extend(remaining)

    for check in checks_to_process:
        result = _execute_check(check)
        results.append(result)

    return results


def _execute_check(check: dict) -> dict:
    """Tek bir MCap kontrolünü çalıştır."""
    token_addr = check["token_address"]
    token_symbol = check["token_symbol"]
    alert_mcap = check["alert_mcap"]
    check_type = check["check_type"]
    threshold = check["threshold"]

    # DexScreener'dan güncel MCap
    current = fetch_current_mcap(token_addr)
    current_mcap = current["mcap"]

    # Değişim yüzdesi
    if alert_mcap > 0:
        change_pct = (current_mcap - alert_mcap) / alert_mcap
    else:
        change_pct = 0

    # Sınıflandırma
    if current_mcap <= DEAD_TOKEN_MCAP:
        classification = "trash"
        passed = False
    elif change_pct >= threshold:
        classification = "short_list" if check_type == "5min" else "contracts_check"
        passed = True
    else:
        classification = "not_passed" if check_type == "5min" else "short_list_only"
        passed = False

    # DB'ye kaydet
    if check_type == "5min":
        save_token_evaluation(
            token_address=token_addr,
            token_symbol=token_symbol,
            alert_mcap=alert_mcap,
            alert_time=check.get("alert_time"),
            mcap_5min=int(current_mcap),
            change_5min_pct=round(change_pct * 100, 2),
            classification=classification if check_type == "5min" else None,
            wallets_involved=check.get("wallets_involved", [])
        )
    else:
        save_token_evaluation(
            token_address=token_addr,
            token_symbol=token_symbol,
            alert_mcap=alert_mcap,
            alert_time=check.get("alert_time"),
            mcap_30min=int(current_mcap),
            change_30min_pct=round(change_pct * 100, 2),
            classification=classification
        )

    emoji = "✅" if passed else "❌"
    print(f"{emoji} MCap Check ({check_type}): {token_symbol} | "
          f"Alert: ${alert_mcap:,.0f} → Şimdi: ${current_mcap:,.0f} ({change_pct*100:+.1f}%) "
          f"→ {classification}")

    return {
        "token_address": token_addr,
        "token_symbol": token_symbol,
        "check_type": check_type,
        "alert_mcap": alert_mcap,
        "current_mcap": current_mcap,
        "change_pct": round(change_pct * 100, 2),
        "classification": classification,
        "passed": passed,
    }


def get_pending_count() -> int:
    """Bekleyen kontrol sayısı."""
    with _lock:
        return len(_pending_checks)
