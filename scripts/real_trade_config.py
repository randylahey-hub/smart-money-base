"""
Real Trading Configuration
Gerçek trade parametreleri — DİKKATLİ DEĞİŞTİR!
Tüm ayarlar Koyeb env variable ile override edilebilir.
"""

import os

# =============================================================================
# TRADE BOYUTLARI
# =============================================================================
# Her trade'de kullanılacak ETH miktarı
REAL_TRADE_SIZE_ETH = float(os.getenv("REAL_TRADE_SIZE_ETH", "0.005"))

# Tek trade'de max ETH
MAX_SINGLE_TRADE_ETH = float(os.getenv("MAX_SINGLE_TRADE_ETH", "0.01"))

# Tüm açık pozisyonlarda max toplam ETH
MAX_TOTAL_EXPOSURE_ETH = float(os.getenv("MAX_TOTAL_EXPOSURE_ETH", "0.03"))

# =============================================================================
# POZİSYON LİMİTLERİ
# =============================================================================
MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", "3"))

# =============================================================================
# KAYIP LİMİTİ
# =============================================================================
# Günlük max kayıp — aşılırsa trading durur (gece yarısı sıfırlanır)
MAX_DAILY_LOSS_ETH = float(os.getenv("MAX_DAILY_LOSS_ETH", "0.01"))

# =============================================================================
# SLIPPAGE
# =============================================================================
# Memecoins için %20 güvenli. DEXScreener fiyatına göre hesaplanır.
SLIPPAGE_PERCENT = float(os.getenv("SLIPPAGE_PERCENT", "20"))

# =============================================================================
# TP/SL VARSAYILANLARI (Konservatif strateji)
# =============================================================================
DEFAULT_TP_LEVELS = [
    {"multiplier": 2.0, "sell_pct": 50},   # 2x olunca %50 sat
    {"multiplier": 3.0, "sell_pct": 100},   # 3x olunca kalanı sat
]
DEFAULT_SL_MULTIPLIER = float(os.getenv("DEFAULT_SL_MULTIPLIER", "0.6"))  # -40% stop loss

# =============================================================================
# GAS STRATEJİSİ
# =============================================================================
GAS_MULTIPLIER = float(os.getenv("GAS_MULTIPLIER", "2.0"))  # current_gas × 2
MAX_GAS_GWEI = float(os.getenv("MAX_GAS_GWEI", "1.0"))  # Base chain ucuz

# =============================================================================
# İZLEME
# =============================================================================
# Pozisyon fiyat kontrol sıklığı (saniye)
POSITION_CHECK_INTERVAL = int(os.getenv("POSITION_CHECK_INTERVAL", "30"))

# =============================================================================
# FEATURE TOGGLE (Kill Switch)
# =============================================================================
# false = gerçek trade YOK, sadece virtual çalışır
REAL_TRADING_ENABLED = os.getenv("REAL_TRADING_ENABLED", "false").lower() == "true"

# =============================================================================
# UNISWAP V3 FEE TIERS
# =============================================================================
# Memecoin pool'ları genelde %1 (10000) veya %0.3 (3000) fee kullanır
PRIMARY_FEE_TIER = 10000    # Önce dene
FALLBACK_FEE_TIER = 3000    # Revert ederse bunu dene

# =============================================================================
# BASE CHAIN
# =============================================================================
BASE_CHAIN_ID = 8453
