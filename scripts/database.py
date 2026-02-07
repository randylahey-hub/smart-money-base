"""
Database Module - PostgreSQL (Neon) ile kalıcı veri depolama.
JSON dosyaları yerine DB kullanır. DATABASE_URL yoksa JSON fallback çalışır.
"""

import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import DATABASE_URL

# PostgreSQL bağlantısı (opsiyonel)
_connection = None
_db_available = False

if DATABASE_URL:
    try:
        import psycopg2
        from psycopg2.extras import Json
        _db_available = True
    except ImportError:
        print("⚠️ psycopg2 yüklü değil, JSON fallback kullanılacak")
        _db_available = False


def get_connection():
    """PostgreSQL bağlantısını al veya oluştur."""
    global _connection
    if not _db_available:
        return None

    try:
        if _connection is None or _connection.closed:
            _connection = psycopg2.connect(DATABASE_URL, connect_timeout=10)
            _connection.autocommit = True
            print("✅ PostgreSQL bağlantısı kuruldu")
        return _connection
    except Exception as e:
        print(f"❌ PostgreSQL bağlantı hatası: {e}")
        return None


def init_db():
    """Tabloları oluştur (IF NOT EXISTS)."""
    conn = get_connection()
    if not conn:
        if DATABASE_URL:
            print("⚠️ Database bağlantısı kurulamadı, JSON fallback aktif")
        return False

    try:
        cur = conn.cursor()

        # 4 ana tablo
        tables = [
            "virtual_portfolio",
            "smartest_wallets",
            "early_smart_money",
            "fake_alerts",
            "real_portfolio"
        ]

        for table in tables:
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {table} (
                    id SERIAL PRIMARY KEY,
                    data JSONB NOT NULL,
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """)

        # trade_signals tablosu (ayrı yapı — her sinyal bir satır)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS trade_signals (
                id SERIAL PRIMARY KEY,
                token_address VARCHAR(42) NOT NULL,
                token_symbol VARCHAR(20),
                entry_mcap BIGINT,
                trigger_type VARCHAR(20),
                wallet_count INT DEFAULT 1,
                status VARCHAR(20) DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT NOW(),
                processed_at TIMESTAMP,
                trade_result JSONB
            )
        """)

        cur.close()
        print(f"✅ Database tabloları hazır ({len(tables)} + trade_signals)")
        return True

    except Exception as e:
        print(f"❌ Tablo oluşturma hatası: {e}")
        return False


def is_db_available() -> bool:
    """DB kullanılabilir mi?"""
    return _db_available and DATABASE_URL != ""


# =============================================================================
# GENERIC CRUD (Tüm tablolar için ortak)
# =============================================================================

def _load_from_db(table_name: str) -> dict:
    """DB'den JSONB verisini oku."""
    conn = get_connection()
    if not conn:
        return None

    try:
        cur = conn.cursor()
        cur.execute(f"SELECT data FROM {table_name} ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        cur.close()

        if row:
            return row[0] if isinstance(row[0], dict) else json.loads(row[0])
        return None

    except Exception as e:
        print(f"⚠️ DB okuma hatası ({table_name}): {e}")
        return None


def _save_to_db(table_name: str, data: dict):
    """DB'ye JSONB verisini yaz (upsert: tek satır tutulur)."""
    conn = get_connection()
    if not conn:
        return False

    try:
        cur = conn.cursor()

        # Mevcut kayıt var mı?
        cur.execute(f"SELECT id FROM {table_name} LIMIT 1")
        existing = cur.fetchone()

        if existing:
            cur.execute(
                f"UPDATE {table_name} SET data = %s, updated_at = NOW() WHERE id = %s",
                (Json(data), existing[0])
            )
        else:
            cur.execute(
                f"INSERT INTO {table_name} (data) VALUES (%s)",
                (Json(data),)
            )

        cur.close()
        return True

    except Exception as e:
        print(f"⚠️ DB yazma hatası ({table_name}): {e}")
        return False


# =============================================================================
# VIRTUAL PORTFOLIO
# =============================================================================

def load_portfolio_db() -> dict:
    """Portföyü DB'den yükle."""
    if not is_db_available():
        return None
    return _load_from_db("virtual_portfolio")


def save_portfolio_db(data: dict) -> bool:
    """Portföyü DB'ye kaydet."""
    if not is_db_available():
        return False
    return _save_to_db("virtual_portfolio", data)


# =============================================================================
# SMARTEST WALLETS
# =============================================================================

def load_smartest_wallets_db() -> dict:
    """Smartest wallets'ı DB'den yükle."""
    if not is_db_available():
        return None
    return _load_from_db("smartest_wallets")


def save_smartest_wallets_db(data: dict) -> bool:
    """Smartest wallets'ı DB'ye kaydet."""
    if not is_db_available():
        return False
    return _save_to_db("smartest_wallets", data)


# =============================================================================
# EARLY SMART MONEY
# =============================================================================

def load_early_smart_money_db() -> dict:
    """Early smart money'yi DB'den yükle."""
    if not is_db_available():
        return None
    return _load_from_db("early_smart_money")


def save_early_smart_money_db(data: dict) -> bool:
    """Early smart money'yi DB'ye kaydet."""
    if not is_db_available():
        return False
    return _save_to_db("early_smart_money", data)


# =============================================================================
# FAKE ALERTS
# =============================================================================

def load_fake_alerts_db() -> dict:
    """Fake alerts'ı DB'den yükle."""
    if not is_db_available():
        return None
    return _load_from_db("fake_alerts")


def save_fake_alerts_db(data: dict) -> bool:
    """Fake alerts'ı DB'ye kaydet."""
    if not is_db_available():
        return False
    return _save_to_db("fake_alerts", data)


# =============================================================================
# REAL PORTFOLIO
# =============================================================================

def load_real_portfolio_db() -> dict:
    """Gerçek trading portföyünü DB'den yükle."""
    if not is_db_available():
        return None
    return _load_from_db("real_portfolio")


def save_real_portfolio_db(data: dict) -> bool:
    """Gerçek trading portföyünü DB'ye kaydet."""
    if not is_db_available():
        return False
    return _save_to_db("real_portfolio", data)


# =============================================================================
# TRADE SIGNALS (Trading bot ile iletişim kuyruğu)
# =============================================================================

def save_trade_signal(token_address: str, token_symbol: str, entry_mcap: int,
                      trigger_type: str, wallet_count: int = 1) -> bool:
    """Alert bot'tan gelen trade sinyalini DB'ye yaz."""
    conn = get_connection()
    if not conn:
        return False

    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO trade_signals (token_address, token_symbol, entry_mcap, trigger_type, wallet_count)
            VALUES (%s, %s, %s, %s, %s)
        """, (token_address.lower(), token_symbol, entry_mcap, trigger_type, wallet_count))
        cur.close()
        print(f"📡 Trade signal yazıldı: {token_symbol} ({trigger_type})")
        return True
    except Exception as e:
        print(f"⚠️ Trade signal yazma hatası: {e}")
        return False


def get_pending_signals(max_age_seconds: int = 300) -> list:
    """Pending durumundaki sinyalleri al (max 5dk eski)."""
    conn = get_connection()
    if not conn:
        return []

    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, token_address, token_symbol, entry_mcap, trigger_type, wallet_count, created_at
            FROM trade_signals
            WHERE status = 'pending'
              AND created_at > NOW() - INTERVAL '%s seconds'
            ORDER BY created_at ASC
        """, (max_age_seconds,))
        rows = cur.fetchall()
        cur.close()

        signals = []
        for row in rows:
            signals.append({
                "id": row[0],
                "token_address": row[1],
                "token_symbol": row[2],
                "entry_mcap": row[3],
                "trigger_type": row[4],
                "wallet_count": row[5],
                "created_at": row[6].isoformat() if row[6] else None
            })
        return signals
    except Exception as e:
        print(f"⚠️ Pending signals okuma hatası: {e}")
        return []


def update_signal_status(signal_id: int, status: str, trade_result: dict = None) -> bool:
    """Sinyal durumunu güncelle (processing, executed, failed, skipped)."""
    conn = get_connection()
    if not conn:
        return False

    try:
        cur = conn.cursor()
        if trade_result:
            cur.execute("""
                UPDATE trade_signals
                SET status = %s, processed_at = NOW(), trade_result = %s
                WHERE id = %s
            """, (status, Json(trade_result), signal_id))
        else:
            cur.execute("""
                UPDATE trade_signals
                SET status = %s, processed_at = NOW()
                WHERE id = %s
            """, (status, signal_id))
        cur.close()
        return True
    except Exception as e:
        print(f"⚠️ Signal güncelleme hatası: {e}")
        return False


def expire_old_signals(max_age_seconds: int = 300) -> int:
    """5dk'dan eski pending sinyalleri 'skipped' olarak işaretle."""
    conn = get_connection()
    if not conn:
        return 0

    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE trade_signals
            SET status = 'skipped', processed_at = NOW()
            WHERE status = 'pending'
              AND created_at <= NOW() - INTERVAL '%s seconds'
        """, (max_age_seconds,))
        count = cur.rowcount
        cur.close()
        if count > 0:
            print(f"⏰ {count} eski sinyal skipped yapıldı")
        return count
    except Exception as e:
        print(f"⚠️ Signal expire hatası: {e}")
        return 0


def is_duplicate_signal(token_address: str, cooldown_seconds: int = 300) -> bool:
    """Aynı token için son 5dk içinde sinyal var mı? (dedup)"""
    conn = get_connection()
    if not conn:
        return False

    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT COUNT(*) FROM trade_signals
            WHERE token_address = %s
              AND status IN ('pending', 'processing', 'executed')
              AND created_at > NOW() - INTERVAL '%s seconds'
        """, (token_address.lower(), cooldown_seconds))
        count = cur.fetchone()[0]
        cur.close()
        return count > 0
    except Exception as e:
        print(f"⚠️ Duplicate signal kontrol hatası: {e}")
        return False


# =============================================================================
# TEST
# =============================================================================

if __name__ == "__main__":
    print("Database Module Test")
    print("=" * 50)
    print(f"DATABASE_URL: {'***' + DATABASE_URL[-20:] if DATABASE_URL else 'YOK'}")
    print(f"DB Available: {is_db_available()}")

    if is_db_available():
        success = init_db()
        print(f"Init DB: {'✅' if success else '❌'}")

        # Test write/read
        test_data = {"test": True, "timestamp": datetime.now().isoformat()}
        save_ok = _save_to_db("smartest_wallets", test_data)
        print(f"Test Write: {'✅' if save_ok else '❌'}")

        read_data = _load_from_db("smartest_wallets")
        print(f"Test Read: {'✅' if read_data else '❌'}")
        if read_data:
            print(f"  Data: {read_data}")
