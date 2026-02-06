"""
Smart Money Alert Bot - Konfigürasyon Dosyası
Railway.app deployment için environment variables destekler.
"""

import os

# =============================================================================
# TELEGRAM AYARLARI
# =============================================================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "7966901223:AAFJBTFtVkxJacvJNJ4UoheNtO-p1Lf6cUU")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "-5200749160")

# =============================================================================
# ALCHEMY RPC AYARLARI (Base Mainnet)
# =============================================================================
ALCHEMY_API_KEY = os.getenv("ALCHEMY_API_KEY", "Lwf_pyOddPDM4qQPcvZr2")
BASE_RPC_HTTP = f"https://base-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}"
BASE_RPC_WSS = f"wss://base-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}"

# =============================================================================
# ALERT AYARLARI
# =============================================================================
# Kaç cüzdan aynı tokeni almalı ki alert tetiklensin
ALERT_THRESHOLD = int(os.getenv("ALERT_THRESHOLD", "3"))

# Maksimum market cap filtresi (USD) - Bunun üstündeki tokenlar alert dışı
MAX_MCAP = int(os.getenv("MAX_MCAP", "300000"))  # $300K

# Zaman penceresi (saniye) - Bu süre içinde alımlar olmalı
TIME_WINDOW = int(os.getenv("TIME_WINDOW", "20"))

# Aynı token için tekrar alert gönderilmeden önce bekleme süresi (saniye)
ALERT_COOLDOWN = int(os.getenv("ALERT_COOLDOWN", "300"))  # 5 dakika

# Minimum 24 saatlik hacim (USD) - Bunun altındaki tokenlar fake alarm kabul edilir
MIN_VOLUME_24H = int(os.getenv("MIN_VOLUME_24H", "1000"))  # $1K

# Minimum 24 saatlik islem sayisi (buys + sells) - Unique trader proxy
MIN_TXNS_24H = int(os.getenv("MIN_TXNS_24H", "15"))

# Fake alarm eşiği - Bir cüzdan kaç fake alert üretirse flaglenir
FAKE_ALERT_FLAG_THRESHOLD = int(os.getenv("FAKE_ALERT_FLAG_THRESHOLD", "3"))

# Data retention (gün) - Bu süreden eski veriler temizlenir
DATA_RETENTION_DAYS = int(os.getenv("DATA_RETENTION_DAYS", "30"))

# =============================================================================
# BASE CHAIN ADRESLERI
# =============================================================================
# WETH adresi (Base)
WETH_ADDRESS = "0x4200000000000000000000000000000000000006"

# Uniswap V3 Router adresleri (Base)
UNISWAP_V3_ROUTER = "0x2626664c2603336E57B271c5C0b26F421741e481"
UNISWAP_UNIVERSAL_ROUTER = "0x3fC91A3afd70395Cd496C647d5a6CC9D4B2b7FAD"

# =============================================================================
# EXCLUDED TOKENS (Alert dışı bırakılacak tokenlar)
# =============================================================================
EXCLUDED_TOKENS = [
    "0x4200000000000000000000000000000000000006",  # WETH
    "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",  # USDC
    "0xfde4C96c8593536E31F229EA8f37b2ADa2699bb2",  # USDT
    "0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb",  # DAI
    "0x4200000000000000000000000000000000000042",  # OP (Optimism token)
    "0x0000000000000000000000000000000000000000",  # Native ETH placeholder
]

# Excluded token sembolleri (büyük/küçük harf duyarsız)
EXCLUDED_SYMBOLS = ["WETH", "USDC", "USDT", "DAI", "ETH"]

# =============================================================================
# İZLEME AYARLARI
# =============================================================================
# Log dosyası
LOG_FILE = "logs/monitor.log"

# Checkpoint dosyası (bağlantı kesilirse devam için)
CHECKPOINT_FILE = "data/monitor_checkpoint.json"

# =============================================================================
# UNISWAP V3 EVENT SIGNATURES
# =============================================================================
# Swap event: Swap(address,address,int256,int256,uint160,uint128,int24)
SWAP_EVENT_SIGNATURE = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"

# Transfer event: Transfer(address,address,uint256)
TRANSFER_EVENT_SIGNATURE = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
