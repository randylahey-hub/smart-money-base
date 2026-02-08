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
MIN_VOLUME_24H = int(os.getenv("MIN_VOLUME_24H", "10000"))  # $10K

# Minimum 24 saatlik islem sayisi (buys + sells) - Unique trader proxy
MIN_TXNS_24H = int(os.getenv("MIN_TXNS_24H", "15"))

# Minimum alım değeri (USD) - Bunun altı dust/airdrop kabul edilir
MIN_BUY_VALUE_USD = int(os.getenv("MIN_BUY_VALUE_USD", "5"))  # $5

# Minimum likidite (USD) - Bunun altı güvenilmez token
MIN_LIQUIDITY = int(os.getenv("MIN_LIQUIDITY", "5000"))  # $5K

# Bullish tekrarlayan alert penceresi (saniye) - Bu süre içinde tekrar alert = bullish
BULLISH_WINDOW = int(os.getenv("BULLISH_WINDOW", "1800"))  # 30 dakika

# Fake alarm eşiği - Bir cüzdan kaç fake alert üretirse flaglenir
FAKE_ALERT_FLAG_THRESHOLD = int(os.getenv("FAKE_ALERT_FLAG_THRESHOLD", "3"))

# Data retention (gün) - Bu süreden eski veriler temizlenir
DATA_RETENTION_DAYS = int(os.getenv("DATA_RETENTION_DAYS", "30"))

# =============================================================================
# SMARTEST WALLET DETECTION
# =============================================================================
# Kaç farklı tokende early alım yapmış olmalı (smartest eşik)
EARLY_BUY_THRESHOLD = int(os.getenv("EARLY_BUY_THRESHOLD", "3"))

# Spray-and-pray filtresi: haftalık max token alımı
MAX_TOKENS_PER_WEEK = int(os.getenv("MAX_TOKENS_PER_WEEK", "20"))

# Minimum early hit rate (seçicilik oranı) - %15
MIN_EARLY_HIT_RATE = float(os.getenv("MIN_EARLY_HIT_RATE", "0.15"))

# Early buyer tespiti için geriye bakılacak blok sayısı (~2 saat)
EARLY_LOOKBACK_BLOCKS = int(os.getenv("EARLY_LOOKBACK_BLOCKS", "3600"))

# Puanlama penceresi (gün)
WALLET_SCORING_WINDOW_DAYS = int(os.getenv("WALLET_SCORING_WINDOW_DAYS", "30"))

# Hedef smartest wallet sayısı
SMARTEST_WALLET_TARGET = int(os.getenv("SMARTEST_WALLET_TARGET", "15"))

# =============================================================================
# DATABASE AYARLARI (Neon PostgreSQL)
# =============================================================================
DATABASE_URL = os.getenv("DATABASE_URL", "")  # Neon connection string

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
# EVENT SIGNATURES
# =============================================================================
# Transfer event: Transfer(address,address,uint256)
TRANSFER_EVENT_SIGNATURE = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

# Swap event signatures (birden fazla DEX destegi)
SWAP_SIGNATURES = [
    "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67",  # Uniswap V3
    "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822",  # Uniswap V2
    "0xb3e2773606abfd36b5bd91394b3a54d1398336c65005baf7f44571de7dacfd46",  # Aerodrome (classic AMM)
    "0x40e9cecb9f5f1f1c5b9c97dec2917b7ee92e57ba5563708daca94dd84ad7112f",  # Aerodrome CL/Slipstream (concentrated liquidity)
]

# Geriye uyumluluk icin (eski kod referanslari)
SWAP_EVENT_SIGNATURE = SWAP_SIGNATURES[0]

# =============================================================================
# GERÇEK TRADİNG AYARLARI
# =============================================================================
# Trading cüzdanı private key'i (Koyeb env variable olarak saklanır, ASLA kodda tutma!)
TRADING_PRIVATE_KEY = os.getenv("TRADING_PRIVATE_KEY", "")

# Gerçek trading açık/kapalı (kill switch)
REAL_TRADING_ENABLED = os.getenv("REAL_TRADING_ENABLED", "false").lower() == "true"
