"""
Transaction Classifier - Airdrop/Multicall/Batch Transfer Tespiti

Basescan'deki Action sütunundaki hareket tiplerine göre işlemleri sınıflandırır.
Airdrop, Multicall, Batch Transfer gibi işlemler alım olarak sayılmaz.
"""

# =============================================================================
# BİLİNEN FONKSİYON SELECTÖRLERİ (tx input'un ilk 4 byte'ı)
# =============================================================================

# Multicall selektörleri
MULTICALL_SELECTORS = {
    "0xac9650d8",  # multicall(bytes[])  - Uniswap V3 Router
    "0x5ae401dc",  # multicall(uint256,bytes[]) - Uniswap V3 Router (deadline)
    "0x252dba42",  # aggregate((address,bytes)[]) - Multicall
    "0x82ad56cb",  # aggregate3((address,bool,bytes)[]) - Multicall3
    "0xcaa5c23f",  # aggregate3Value - Multicall3
    "0x174dea71",  # blockAndAggregate - Multicall2
}

# Airdrop / Token dağıtım selektörleri
AIRDROP_SELECTORS = {
    "0xa9059cbb",  # transfer(address,uint256) - tek transfer (airdrop amaçlı kullanılır)
    # NOT: transfer tek başına airdrop değil, log count ile birlikte değerlendirilir
    "0x0b66f3f5",  # airdrop(address[],uint256[])
    "0x67243482",  # airdrop - farklı imza
    "0xc204642c",  # airdropETH / disperseEther
    "0x40c10f19",  # mint(address,uint256) - bazı airdroplar mint fonksiyonu kullanır
    "0x36895558",  # distributeTokens
    "0xab883d28",  # batchTransfer (Disperse.app tarzı)
    "0xc73a2d60",  # disperseToken
    "0xe63d38ed",  # disperseTokenSimple
}

# Batch Transfer selektörleri
BATCH_TRANSFER_SELECTORS = {
    "0x2e7ba6ef",  # claim(uint256,address,uint256,bytes32[]) - Merkle airdrop
    "0x4e71d92d",  # claim() - basit claim
    "0xa217fddf",  # batchTransfer
    "0x38ed1739",  # swapExactTokensForTokens (V2 router - güvenli, ama multicall içinde olabilir)
}

# Tüm skip edilecek selektörler (Multicall + Airdrop + Batch)
SKIP_SELECTORS = MULTICALL_SELECTORS | AIRDROP_SELECTORS | BATCH_TRANSFER_SELECTORS

# Bilinen multicall/batch contract adresleri (Base chain)
KNOWN_MULTICALL_CONTRACTS = {
    "0xca11bde05977b3631167028862be2a173976ca11",  # Multicall3
    "0x6e82c738d827bc24d6a09142d2e13a301df0f709",  # Disperse.app (Base)
}

# =============================================================================
# SINIFLANDIRMA FONKSİYONLARI
# =============================================================================

# Log count eşikleri
BATCH_LOG_THRESHOLD = 10  # 10+ Transfer log = batch/airdrop şüphesi
AIRDROP_LOG_THRESHOLD = 20  # 20+ Transfer log = kesin airdrop


def classify_transaction(tx_hash, receipt, w3) -> dict:
    """
    Bir transaction'ı sınıflandırır.

    Args:
        tx_hash: Transaction hash
        receipt: Transaction receipt (zaten alınmış)
        w3: Web3 instance

    Returns:
        {
            "type": "normal_swap" | "airdrop" | "multicall" | "batch_transfer",
            "skip": bool,  # True ise bu işlem alım olarak sayılmaz
            "reason": str   # Atlama nedeni
        }
    """
    try:
        # 1. Transaction input verisini al (function selector)
        tx = w3.eth.get_transaction(tx_hash)
        tx_input = tx.get('input', b'')

        # input hex string veya bytes olabilir
        if isinstance(tx_input, bytes):
            input_hex = '0x' + tx_input.hex()
        else:
            input_hex = str(tx_input)

        # Function selector (ilk 4 byte = 10 karakter: 0x + 8 hex)
        selector = input_hex[:10].lower() if len(input_hex) >= 10 else ""

        # 2. To adresi bilinen multicall contract mı?
        to_address = (tx.get('to') or '').lower()
        if to_address in KNOWN_MULTICALL_CONTRACTS:
            return {
                "type": "multicall",
                "skip": True,
                "reason": f"Bilinen multicall contract: {to_address[:10]}..."
            }

        # 3. Function selector kontrolü - Multicall
        if selector in MULTICALL_SELECTORS:
            return {
                "type": "multicall",
                "skip": True,
                "reason": f"Multicall fonksiyon selektörü: {selector}"
            }

        # 4. Function selector kontrolü - Airdrop
        if selector in AIRDROP_SELECTORS:
            # transfer(address,uint256) özel durum: tek başına airdrop olmayabilir
            # Ama log sayısına bakarak karar verelim
            if selector == "0xa9059cbb":
                transfer_count = _count_transfer_logs(receipt)
                if transfer_count >= BATCH_LOG_THRESHOLD:
                    return {
                        "type": "airdrop",
                        "skip": True,
                        "reason": f"Transfer fonksiyonu + {transfer_count} log (batch airdrop)"
                    }
                # Tek transfer - normal olabilir, geçir
            else:
                return {
                    "type": "airdrop",
                    "skip": True,
                    "reason": f"Airdrop fonksiyon selektörü: {selector}"
                }

        # 5. Function selector kontrolü - Batch Transfer
        if selector in BATCH_TRANSFER_SELECTORS:
            return {
                "type": "batch_transfer",
                "skip": True,
                "reason": f"Batch transfer fonksiyon selektörü: {selector}"
            }

        # 6. Log count kontrolü kaldırıldı.
        # Swap doğrulaması (wallet_monitor.py satır 237-254) zaten meşru alımları doğruluyor.
        # Log sayısı sadece routing karmaşıklığını gösterir, airdrop tespiti için
        # function selector + bilinen contract kontrolü yeterli.

        # 7. Hepsi geçti → normal swap
        return {
            "type": "normal_swap",
            "skip": False,
            "reason": ""
        }

    except Exception as e:
        # Hata durumunda fail-open: işlemi geçir (false positive'den kaçın)
        print(f"⚠️ TX sınıflandırma hatası ({str(tx_hash)[:10]}...): {e}")
        return {
            "type": "unknown",
            "skip": False,
            "reason": f"Sınıflandırma hatası: {e}"
        }


def _count_transfer_logs(receipt) -> int:
    """Receipt'teki Transfer event log sayısını say."""
    transfer_sig = "ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
    count = 0
    for log in receipt.get('logs', []):
        topics = log.get('topics', [])
        if topics and topics[0].hex().lower().replace('0x', '') == transfer_sig:
            count += 1
    return count


def _count_unique_recipients(receipt) -> int:
    """Receipt'teki Transfer eventlerinden unique alıcı sayısını say."""
    transfer_sig = "ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
    recipients = set()
    for log in receipt.get('logs', []):
        topics = log.get('topics', [])
        if topics and topics[0].hex().lower().replace('0x', '') == transfer_sig:
            if len(topics) >= 3:
                # topics[2] = to address (Transfer event'in 3. parametresi)
                to_addr = '0x' + topics[2].hex()[-40:]
                recipients.add(to_addr.lower())
    return len(recipients)
