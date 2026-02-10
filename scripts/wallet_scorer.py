"""
Wallet Scorer - Smartest Wallet Tespit ve Puanlama Motoru

Alert tetiklendiğinde:
1. İlk smart money alımından ÖNCE tokeni alan cüzdanları bulur (time-based)
2. Her early buyer'ın seçicilik skorunu hesaplar
3. Spray-and-pray yapanları filtreler
4. Smartest wallets tablosunu günceller

Skor formülü: early_hit_rate * log2(early_hits + 1) * recency_weight
"""

import math
import sys
import os
from datetime import datetime
from web3 import Web3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import (
    BASE_RPC_HTTP,
    TRANSFER_EVENT_SIGNATURE,
    SWAP_SIGNATURES,
    EARLY_BUY_THRESHOLD,
    MAX_TOKENS_PER_WEEK,
    MIN_EARLY_HIT_RATE,
    EARLY_LOOKBACK_BLOCKS,
    WALLET_SCORING_WINDOW_DAYS,
    SMARTEST_WALLET_TARGET,
)
from scripts.database import (
    save_wallet_activity,
    get_wallet_activity_summary,
    get_weekly_token_count,
    get_all_early_wallets,
    save_alert_snapshot,
    cleanup_old_wallet_activity,
    save_smartest_wallets_db,
    is_db_available,
)

# Web3 bağlantısı
w3 = Web3(Web3.HTTPProvider(BASE_RPC_HTTP))


# =========================================================================
# 1. EARLY BUYER TESPİTİ (Zamana dayalı)
# =========================================================================

def find_early_buyers_time_based(
    token_address: str,
    first_sm_block: int,
    lookback_blocks: int = None,
    smart_money_wallets: set = None,
) -> list:
    """
    Alert'ten ÖNCE tokeni alan cüzdanları bul.
    MCap tahmini yapmaz - sadece blok zamanlamasına bakar.

    Args:
        token_address: Token contract adresi
        first_sm_block: İlk smart money alımının blok numarası
        lookback_blocks: Geriye bakılacak blok sayısı (varsayılan: config)
        smart_money_wallets: Smart money cüzdan seti (bunları hariç tut)

    Returns:
        list: [{"wallet": addr, "block": block_num}, ...]
    """
    if lookback_blocks is None:
        lookback_blocks = EARLY_LOOKBACK_BLOCKS

    if smart_money_wallets is None:
        smart_money_wallets = set()

    start_block = max(0, first_sm_block - lookback_blocks)
    end_block = first_sm_block - 1  # SM bloğundan bir öncesine kadar

    if end_block <= start_block:
        return []

    early_buyers = []
    seen_wallets = set()

    try:
        # Alchemy blok aralığı limiti nedeniyle parçalı sorgula
        CHUNK_SIZE = 2000
        for chunk_start in range(start_block, end_block + 1, CHUNK_SIZE):
            chunk_end = min(chunk_start + CHUNK_SIZE - 1, end_block)

            try:
                logs = w3.eth.get_logs({
                    'fromBlock': chunk_start,
                    'toBlock': chunk_end,
                    'address': Web3.to_checksum_address(token_address),
                    'topics': [TRANSFER_EVENT_SIGNATURE]
                })
            except Exception as e:
                # Blok aralığı çok genişse daha küçük parçalarla dene
                print(f"  ⚠️ Chunk hatası ({chunk_start}-{chunk_end}): {e}")
                continue

            for log in logs:
                if len(log['topics']) < 3:
                    continue

                to_address = '0x' + log['topics'][2].hex()[-40:]
                to_lower = to_address.lower()

                # Zaten gördüysek veya smart money ise atla
                if to_lower in seen_wallets or to_lower in smart_money_wallets:
                    continue
                seen_wallets.add(to_lower)

                # Swap doğrulaması - gerçek alım mı?
                try:
                    receipt = w3.eth.get_transaction_receipt(log['transactionHash'])
                    has_swap = False
                    for rlog in receipt['logs']:
                        if rlog['topics'] and rlog['topics'][0].hex() in [
                            s.replace('0x', '') for s in SWAP_SIGNATURES
                        ]:
                            has_swap = True
                            break
                    if not has_swap:
                        continue  # Swap yoksa airdrop/dust, atla
                except Exception:
                    continue  # Receipt alınamadıysa güvenli tarafta kal, atla

                early_buyers.append({
                    "wallet": to_lower,
                    "block": log['blockNumber'],
                })

    except Exception as e:
        print(f"  ⚠️ Early buyer tarama hatası: {e}")

    return early_buyers


# =========================================================================
# 2. WALLET ACTIVITY KAYDI
# =========================================================================

def record_wallet_activity(
    wallet_address: str,
    token_address: str,
    token_symbol: str,
    block_number: int,
    is_early: bool = False,
    alert_mcap: int = 0,
):
    """
    Cüzdan alım aktivitesini DB'ye kaydet.
    Her smart money alımında çağrılır + early buyer tespit edildiğinde is_early=True.
    """
    save_wallet_activity(
        wallet_address=wallet_address,
        token_address=token_address,
        token_symbol=token_symbol,
        block_number=block_number,
        is_early=is_early,
        alert_mcap=alert_mcap,
    )


# =========================================================================
# 3. SEÇİCİLİK SKORU HESAPLAMA
# =========================================================================

def calculate_selectivity_score(wallet_address: str) -> dict:
    """
    Cüzdanın seçicilik skorunu hesapla.

    Skor = early_hit_rate * log2(early_hits + 1) * recency_weight

    Filtreler:
    - early_hits >= EARLY_BUY_THRESHOLD (3)
    - weekly_tokens <= MAX_TOKENS_PER_WEEK (20)
    - early_hit_rate >= MIN_EARLY_HIT_RATE (0.15 = %15)

    Returns:
        dict: {
            "score": float,
            "early_hits": int,
            "unique_tokens": int,
            "early_hit_rate": float,
            "weekly_tokens": int,
            "passed_filters": bool,
            "reject_reason": str or None
        }
    """
    # Aktivite özeti (son 30 gün)
    summary = get_wallet_activity_summary(wallet_address, WALLET_SCORING_WINDOW_DAYS)
    early_hits = summary["early_hits"]
    unique_tokens = summary["unique_tokens"]
    early_hit_rate = summary["early_hit_rate"]

    # Haftalık token sayısı (spray filtresi)
    weekly_tokens = get_weekly_token_count(wallet_address)

    result = {
        "score": 0.0,
        "early_hits": early_hits,
        "unique_tokens": unique_tokens,
        "early_hit_rate": early_hit_rate,
        "weekly_tokens": weekly_tokens,
        "passed_filters": False,
        "reject_reason": None,
    }

    # Filtre 1: Minimum early hit sayısı
    if early_hits < EARLY_BUY_THRESHOLD:
        result["reject_reason"] = f"early_hits {early_hits} < {EARLY_BUY_THRESHOLD}"
        return result

    # Filtre 2: Spray-and-pray kontrolü
    if weekly_tokens > MAX_TOKENS_PER_WEEK:
        result["reject_reason"] = f"weekly_tokens {weekly_tokens} > {MAX_TOKENS_PER_WEEK}"
        return result

    # Filtre 3: Minimum seçicilik oranı
    if early_hit_rate < MIN_EARLY_HIT_RATE:
        result["reject_reason"] = f"hit_rate {early_hit_rate:.1%} < {MIN_EARLY_HIT_RATE:.0%}"
        return result

    # Skor hesapla
    # log2(early_hits + 1) → süreklilik ağırlığı (3 hit=2.0, 7 hit=3.0, 15 hit=4.0)
    consistency_weight = math.log2(early_hits + 1)

    # Güncellik ağırlığı şimdilik 1.0 (ileride son aktiviteye göre decay eklenebilir)
    recency_weight = 1.0

    score = early_hit_rate * consistency_weight * recency_weight
    result["score"] = round(score, 4)
    result["passed_filters"] = True

    return result


# =========================================================================
# 4. ALERT İŞLEME (process_alert_v2)
# =========================================================================

def process_alert_v2(
    token_address: str,
    token_symbol: str,
    smart_money_purchases: list,
    smart_money_wallets: set,
    current_block: int,
    alert_mcap: int = 0,
):
    """
    Alert tetiklendiğinde çağrılır.
    1. İlk SM alımının bloğunu bul
    2. O bloktan geriye tara → early buyer'ları bul
    3. Early buyer'ları kaydet
    4. Alert snapshot oluştur
    5. Smartest wallets tablosunu güncelle

    Args:
        token_address: Token CA
        token_symbol: Token sembolü
        smart_money_purchases: [(wallet, eth_amount, mcap), ...]
        smart_money_wallets: Tüm SM cüzdan seti
        current_block: Şu anki blok
        alert_mcap: Alert anındaki MCap
    """
    print(f"\n🔎 Early detection v2 başlatılıyor: {token_symbol}")

    if not is_db_available():
        print("  ⚠️ DB yok, early detection atlanıyor")
        return

    # İlk smart money alımının bloğunu tahmin et
    # Purchases listesinde blok bilgisi yok, current_block kullanıyoruz
    # Gerçek blok ~20sn öncesindeydi (TIME_WINDOW), ama current_block yeterli yakınlık
    first_sm_block = current_block

    print(f"  📦 İlk SM bloğu: ~{first_sm_block}")
    print(f"  🔍 {EARLY_LOOKBACK_BLOCKS} blok geriye taranıyor (~{EARLY_LOOKBACK_BLOCKS * 2 // 3600} saat)")

    # Early buyer'ları bul
    early_buyers = find_early_buyers_time_based(
        token_address=token_address,
        first_sm_block=first_sm_block,
        smart_money_wallets=smart_money_wallets,
    )

    print(f"  👥 {len(early_buyers)} early buyer bulundu")

    # Early buyer'ları DB'ye kaydet
    for buyer in early_buyers:
        record_wallet_activity(
            wallet_address=buyer["wallet"],
            token_address=token_address,
            token_symbol=token_symbol,
            block_number=buyer["block"],
            is_early=True,
            alert_mcap=alert_mcap,
        )
        print(f"  🔍 Early: {buyer['wallet'][:10]}... @ blok {buyer['block']}")

    # Alert snapshot kaydet
    save_alert_snapshot(
        token_address=token_address,
        token_symbol=token_symbol,
        alert_mcap=alert_mcap,
        alert_block=current_block,
        wallet_count=len(smart_money_purchases),
        first_sm_block=first_sm_block,
        early_buyers_found=len(early_buyers),
    )

    # Smartest wallets güncelle
    if early_buyers:
        evaluate_and_update_smartest_wallets()


# =========================================================================
# 5. SMARTEST WALLETS DEĞERLENDİRME
# =========================================================================

def evaluate_and_update_smartest_wallets():
    """
    Tüm aday cüzdanları değerlendir ve smartest_wallets tablosunu güncelle.
    Günlük yenilemede veya yeni early buyer bulunduğunda çağrılır.
    """
    if not is_db_available():
        return

    # Minimum early hit sayısını geçen tüm cüzdanları al
    candidates = get_all_early_wallets(
        min_early_count=EARLY_BUY_THRESHOLD,
        days=WALLET_SCORING_WINDOW_DAYS,
    )

    if not candidates:
        print("  ℹ️ Henüz yeterli early buyer adayı yok")
        return

    print(f"  📊 {len(candidates)} aday cüzdan değerlendiriliyor...")

    # Her adayı skorla
    scored = []
    for c in candidates:
        score_data = calculate_selectivity_score(c["wallet"])

        if score_data["passed_filters"]:
            scored.append({
                "address": c["wallet"],
                "score": score_data["score"],
                "early_hits": score_data["early_hits"],
                "unique_tokens": score_data["unique_tokens"],
                "early_hit_rate": score_data["early_hit_rate"],
                "weekly_tokens": score_data["weekly_tokens"],
                "qualified_at": datetime.now().isoformat(),
            })
            print(f"  ✅ {c['wallet'][:10]}... | Skor: {score_data['score']:.3f} | "
                  f"Early: {score_data['early_hits']}/{score_data['unique_tokens']} "
                  f"({score_data['early_hit_rate']:.0%})")
        else:
            print(f"  ❌ {c['wallet'][:10]}... | Red: {score_data['reject_reason']}")

    # Skora göre sırala ve en iyileri al
    scored.sort(key=lambda x: x["score"], reverse=True)
    top_wallets = scored[:SMARTEST_WALLET_TARGET]

    # DB'ye kaydet
    smartest_data = {
        "wallets": top_wallets,
        "target": SMARTEST_WALLET_TARGET,
        "current_count": len(top_wallets),
        "completed": len(top_wallets) >= SMARTEST_WALLET_TARGET,
        "last_evaluation": datetime.now().isoformat(),
    }

    save_smartest_wallets_db(smartest_data)
    print(f"  🧠 Smartest wallets: {len(top_wallets)}/{SMARTEST_WALLET_TARGET}")

    return top_wallets


def daily_refresh():
    """
    Günlük yenileme (23:30 daily report'ta çağrılır).
    - Eski verileri temizle
    - Tüm adayları tekrar değerlendir
    """
    print("\n🔄 Smartest wallet günlük yenileme başlatılıyor...")

    # Eski verileri temizle
    cleaned = cleanup_old_wallet_activity(WALLET_SCORING_WINDOW_DAYS)
    if cleaned > 0:
        print(f"  🗑️ {cleaned} eski kayıt temizlendi")

    # Tekrar değerlendir
    result = evaluate_and_update_smartest_wallets()

    if result:
        print(f"  ✅ Yenileme tamamlandı: {len(result)} smartest wallet")
    else:
        print(f"  ℹ️ Henüz smartest wallet adayı yok")

    return result


# =========================================================================
# TEST
# =========================================================================

if __name__ == "__main__":
    print("Wallet Scorer Test")
    print("=" * 50)
    print(f"Config:")
    print(f"  EARLY_BUY_THRESHOLD: {EARLY_BUY_THRESHOLD}")
    print(f"  MAX_TOKENS_PER_WEEK: {MAX_TOKENS_PER_WEEK}")
    print(f"  MIN_EARLY_HIT_RATE: {MIN_EARLY_HIT_RATE:.0%}")
    print(f"  EARLY_LOOKBACK_BLOCKS: {EARLY_LOOKBACK_BLOCKS} (~{EARLY_LOOKBACK_BLOCKS * 2 // 3600} saat)")
    print(f"  SCORING_WINDOW: {WALLET_SCORING_WINDOW_DAYS} gün")
    print(f"  TARGET: {SMARTEST_WALLET_TARGET} cüzdan")
    print()

    # DB test
    if is_db_available():
        print("DB bağlantısı: ✅")
        result = evaluate_and_update_smartest_wallets()
        if result:
            for w in result:
                print(f"  {w['address'][:10]}... | Skor: {w['score']:.3f}")
        else:
            print("  Henüz aday yok")
    else:
        print("DB bağlantısı: ❌ (DATABASE_URL gerekli)")
