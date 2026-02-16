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
# ALCHEMY RPC AYARLARI (Base Mainnet) — Multi-key failover
# =============================================================================
# Birden fazla key: biri tükenince diğerine geçer
# Pipe (|) separator — Koyeb CLI virgülü env separator olarak kullanıyor
_ALCHEMY_KEYS_STR = os.getenv("ALCHEMY_API_KEYS", os.getenv("ALCHEMY_API_KEY", "v2VIrBNyeXX-pl3yOlVSc|Lwf_pyOddPDM4qQPcvZr2"))
ALCHEMY_API_KEYS = [k.strip() for k in _ALCHEMY_KEYS_STR.split("|") if k.strip()]
ALCHEMY_API_KEY = ALCHEMY_API_KEYS[0]  # Aktif key (runtime'da değişebilir)
BASE_RPC_HTTP = f"https://base-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}"
BASE_RPC_WSS = f"wss://base-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}"

# =============================================================================
# ALERT AYARLARI
# =============================================================================
# Kaç cüzdan aynı tokeni almalı ki alert tetiklensin
ALERT_THRESHOLD = int(os.getenv("ALERT_THRESHOLD", "3"))

# Maksimum market cap filtresi (USD) - Bunun üstündeki tokenlar alert dışı
MAX_MCAP = int(os.getenv("MAX_MCAP", "700000"))  # $700K

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

# Soft blackout saatleri (UTC+3) - Bu saatlerde alert eşiği yükselir (ALERT_THRESHOLD + BLACKOUT_EXTRA)
# Hard block yerine soft kontrol: banger kaçırma riskini azaltır, ama zayıf sinyalleri engeller
BLACKOUT_HOURS_STR = os.getenv("BLACKOUT_HOURS", "2,4,16,20,21")
BLACKOUT_HOURS = [int(h.strip()) for h in BLACKOUT_HOURS_STR.split(",") if h.strip()]
BLACKOUT_EXTRA_THRESHOLD = int(os.getenv("BLACKOUT_EXTRA_THRESHOLD", "1"))  # Blackout'ta eşik +1 (3→4)

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
    "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",  # USDC (native)
    "0xEB466342C4d449BC9f53A865D5Cb90586f405215",  # axlUSDC (Axelar)
    "0xd9aAEc86B65D86f6A7B5B1b0c42FFA531710b6CA",  # USDbC (bridged USDC)
    "0xfde4C96c8593536E31F229EA8f37b2ADa2699bb2",  # USDT
    "0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb",  # DAI
    "0x4200000000000000000000000000000000000042",  # OP (Optimism token)
    "0x0000000000000000000000000000000000000000",  # Native ETH placeholder
    "0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf",  # cbBTC (Coinbase Wrapped BTC)
]

# Excluded token sembolleri (büyük/küçük harf duyarsız)
EXCLUDED_SYMBOLS = ["WETH", "USDC", "USDT", "DAI", "ETH", "AXLUSDC", "USDBC", "CBBTC"]

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

# =============================================================================
# SELF-IMPROVING ENGINE AYARLARI
# =============================================================================
# Self-improving engine açık/kapalı (kill switch)
SELF_IMPROVE_ENABLED = os.getenv("SELF_IMPROVE_ENABLED", "false").lower() == "true"

# Alert kalite eşikleri
SHORT_LIST_THRESHOLD = float(os.getenv("SHORT_LIST_THRESHOLD", "0.20"))  # %20 artış
CONTRACTS_CHECK_THRESHOLD = float(os.getenv("CONTRACTS_CHECK_THRESHOLD", "0.50"))  # %50 artış
DEAD_TOKEN_MCAP = int(os.getenv("DEAD_TOKEN_MCAP", "20000"))  # $20K altı = ölü token

# Cüzdan değerlendirme eşikleri
TRASH_WARN_THRESHOLD = float(os.getenv("TRASH_WARN_THRESHOLD", "0.60"))  # %60 trash → uyarı
TRASH_REMOVE_THRESHOLD = float(os.getenv("TRASH_REMOVE_THRESHOLD", "0.80"))  # %80 trash → çıkarma
MIN_APPEARANCES_FOR_REMOVAL = int(os.getenv("MIN_APPEARANCES_FOR_REMOVAL", "3"))

# Cüzdan keşif filtreleri
DISCOVER_MIN_BUY_USD = int(os.getenv("DISCOVER_MIN_BUY_USD", "50"))  # Min $50 alım
DISCOVER_ACCOUNT_MIN_AGE = int(os.getenv("DISCOVER_ACCOUNT_MIN_AGE", "100"))  # 100 gün
DISCOVER_WEEKLY_TOKEN_LIMIT = int(os.getenv("DISCOVER_WEEKLY_TOKEN_LIMIT", "80"))
NEW_WALLET_WEEKLY_LIMIT = int(os.getenv("NEW_WALLET_WEEKLY_LIMIT", "80"))

# Basescan API Key (merkezi)
ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY", "6TE7PX7TDS777Z3T7NQCZVUK4KBK9HHDJQ")
