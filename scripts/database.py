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

        # wallet_activity — her cüzdan alımını takip (seçicilik skoru için)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS wallet_activity (
                id SERIAL PRIMARY KEY,
                wallet_address VARCHAR(42) NOT NULL,
                token_address VARCHAR(42) NOT NULL,
                token_symbol VARCHAR(20),
                block_number BIGINT,
                is_early BOOLEAN DEFAULT FALSE,
                alert_mcap BIGINT DEFAULT 0,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_wa_wallet ON wallet_activity(wallet_address)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_wa_created ON wallet_activity(created_at)")

        # alert_snapshots — alert anındaki MCap/block kaydı
        cur.execute("""
            CREATE TABLE IF NOT EXISTS alert_snapshots (
                id SERIAL PRIMARY KEY,
                token_address VARCHAR(42) NOT NULL,
                token_symbol VARCHAR(20),
                alert_mcap BIGINT,
                alert_block BIGINT,
                wallet_count INT,
                first_sm_block BIGINT,
                early_buyers_found INT DEFAULT 0,
                wallets_involved TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        # wallets_involved sütunu yoksa ekle (mevcut tablolar için migration)
        try:
            cur.execute("""
                ALTER TABLE alert_snapshots ADD COLUMN IF NOT EXISTS wallets_involved TEXT DEFAULT ''
            """)
        except Exception:
            pass

        # token_evaluations tablosu (alert kalite analizi)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS token_evaluations (
                id SERIAL PRIMARY KEY,
                token_address VARCHAR(42) NOT NULL,
                token_symbol VARCHAR(20),
                alert_mcap BIGINT,
                mcap_5min BIGINT,
                mcap_30min BIGINT,
                change_5min_pct FLOAT,
                change_30min_pct FLOAT,
                classification VARCHAR(20),
                wallets_involved JSONB,
                alert_time TIMESTAMP,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_te_token ON token_evaluations(token_address)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_te_class ON token_evaluations(classification)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_te_alert_time ON token_evaluations(alert_time)")

        # ath_mcap sütunu yoksa ekle (alert sonrası görülen en yüksek MCap)
        try:
            cur.execute("ALTER TABLE token_evaluations ADD COLUMN IF NOT EXISTS ath_mcap BIGINT DEFAULT 0")
        except Exception:
            pass

        cur.close()
        print(f"✅ Database tabloları hazır ({len(tables)} + trade_signals + wallet_activity + alert_snapshots + token_evaluations)")
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
                      trigger_type: str, wallet_count: int = 1, status: str = None) -> bool:
    """Alert bot'tan gelen trade sinyalini DB'ye yaz.
    status: None=otomatik (strateji bazlı), veya 'pending'/'pending_confirmation' manuel.
    """
    conn = get_connection()
    if not conn:
        return False

    # Status belirtilmemişse aktif stratejiye göre ata
    if status is None:
        try:
            from config.settings import ACTIVE_STRATEGY
            if ACTIVE_STRATEGY == "confirmation_sniper" and trigger_type == "scenario_1":
                status = "pending_confirmation"  # 5dk MCap check bekleyecek
            else:
                status = "pending"  # Anında işleme alınacak
        except Exception:
            status = "pending"

    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO trade_signals (token_address, token_symbol, entry_mcap, trigger_type, wallet_count, status)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (token_address.lower(), token_symbol, entry_mcap, trigger_type, wallet_count, status))
        cur.close()
        status_emoji = "🎯" if status == "pending_confirmation" else "📡"
        print(f"{status_emoji} Trade signal yazıldı: {token_symbol} ({trigger_type}) → {status}")
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
    """5dk'dan eski pending sinyalleri 'skipped' olarak işaretle.
    pending_confirmation sinyalleri 10dk'da expire olur (MCap check süresi).
    """
    conn = get_connection()
    if not conn:
        return 0

    try:
        cur = conn.cursor()
        # Normal pending sinyaller: 5dk
        cur.execute("""
            UPDATE trade_signals
            SET status = 'skipped', processed_at = NOW()
            WHERE status = 'pending'
              AND created_at <= NOW() - INTERVAL '%s seconds'
        """, (max_age_seconds,))
        count1 = cur.rowcount

        # Confirmation bekleyen sinyaller: 10dk (5dk check süresi + buffer)
        cur.execute("""
            UPDATE trade_signals
            SET status = 'skipped', processed_at = NOW(),
                trade_result = '{"reason": "mcap_confirmation_timeout"}'::jsonb
            WHERE status = 'pending_confirmation'
              AND created_at <= NOW() - INTERVAL '600 seconds'
        """)
        count2 = cur.rowcount

        # Approved ama işlenmemiş sinyaller: 10dk
        cur.execute("""
            UPDATE trade_signals
            SET status = 'skipped', processed_at = NOW(),
                trade_result = '{"reason": "approved_but_not_executed"}'::jsonb
            WHERE status = 'approved'
              AND created_at <= NOW() - INTERVAL '600 seconds'
        """)
        count3 = cur.rowcount

        cur.close()
        total = count1 + count2 + count3
        if total > 0:
            print(f"⏰ {total} eski sinyal skipped ({count1} pending, {count2} confirmation, {count3} approved)")
        return total
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
# WALLET ACTIVITY (Smartest wallet scorer için)
# =============================================================================

def save_wallet_activity(wallet_address: str, token_address: str, token_symbol: str,
                         block_number: int, is_early: bool = False, alert_mcap: int = 0) -> bool:
    """Cüzdan alım aktivitesini kaydet."""
    conn = get_connection()
    if not conn:
        return False
    try:
        cur = conn.cursor()
        # Aynı cüzdan+token çiftini tekrar ekleme (dedup)
        cur.execute("""
            SELECT id FROM wallet_activity
            WHERE wallet_address = %s AND token_address = %s
            LIMIT 1
        """, (wallet_address.lower(), token_address.lower()))
        if cur.fetchone():
            # Zaten var, early bilgisini güncelle (false→true olabilir)
            if is_early:
                cur.execute("""
                    UPDATE wallet_activity SET is_early = TRUE, alert_mcap = %s
                    WHERE wallet_address = %s AND token_address = %s
                """, (alert_mcap, wallet_address.lower(), token_address.lower()))
            cur.close()
            return True
        cur.execute("""
            INSERT INTO wallet_activity (wallet_address, token_address, token_symbol, block_number, is_early, alert_mcap)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (wallet_address.lower(), token_address.lower(), token_symbol, block_number, is_early, alert_mcap))
        cur.close()
        return True
    except Exception as e:
        print(f"⚠️ Wallet activity yazma hatası: {e}")
        return False


def get_wallet_activity_summary(wallet_address: str, days: int = 30) -> dict:
    """Cüzdanın son N gündeki aktivite özeti."""
    conn = get_connection()
    if not conn:
        return {"unique_tokens": 0, "early_hits": 0, "early_hit_rate": 0.0}
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                COUNT(DISTINCT token_address) as unique_tokens,
                COUNT(DISTINCT CASE WHEN is_early THEN token_address END) as early_hits
            FROM wallet_activity
            WHERE wallet_address = %s AND created_at > NOW() - INTERVAL '%s days'
        """, (wallet_address.lower(), days))
        row = cur.fetchone()
        cur.close()
        unique = row[0] or 0
        early = row[1] or 0
        rate = early / unique if unique > 0 else 0.0
        return {"unique_tokens": unique, "early_hits": early, "early_hit_rate": round(rate, 3)}
    except Exception as e:
        print(f"⚠️ Wallet activity okuma hatası: {e}")
        return {"unique_tokens": 0, "early_hits": 0, "early_hit_rate": 0.0}


def get_weekly_token_count(wallet_address: str) -> int:
    """Cüzdanın son 7 gündeki benzersiz token alım sayısı."""
    conn = get_connection()
    if not conn:
        return 0
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT COUNT(DISTINCT token_address) FROM wallet_activity
            WHERE wallet_address = %s AND created_at > NOW() - INTERVAL '7 days'
        """, (wallet_address.lower(),))
        count = cur.fetchone()[0] or 0
        cur.close()
        return count
    except Exception as e:
        return 0


def get_all_early_wallets(min_early_count: int = 3, days: int = 30) -> list:
    """Early buy sayısı eşiği geçen tüm cüzdanları getir."""
    conn = get_connection()
    if not conn:
        return []
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT wallet_address,
                   COUNT(DISTINCT CASE WHEN is_early THEN token_address END) as early_count,
                   COUNT(DISTINCT token_address) as total_tokens
            FROM wallet_activity
            WHERE created_at > NOW() - INTERVAL '%s days'
            GROUP BY wallet_address
            HAVING COUNT(DISTINCT CASE WHEN is_early THEN token_address END) >= %s
            ORDER BY early_count DESC
        """, (days, min_early_count))
        rows = cur.fetchall()
        cur.close()
        return [{"wallet": r[0], "early_count": r[1], "total_tokens": r[2]} for r in rows]
    except Exception as e:
        print(f"⚠️ Early wallets okuma hatası: {e}")
        return []


def save_alert_snapshot(token_address: str, token_symbol: str, alert_mcap: int,
                        alert_block: int, wallet_count: int, first_sm_block: int,
                        early_buyers_found: int = 0, wallets_involved: list = None) -> bool:
    """Alert snapshot kaydet. wallets_involved: alım yapan cüzdan adresleri listesi."""
    conn = get_connection()
    if not conn:
        return False
    try:
        wallets_str = ",".join(wallets_involved) if wallets_involved else ""
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO alert_snapshots (token_address, token_symbol, alert_mcap, alert_block,
                                         wallet_count, first_sm_block, early_buyers_found, wallets_involved)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (token_address.lower(), token_symbol, alert_mcap, alert_block,
              wallet_count, first_sm_block, early_buyers_found, wallets_str))
        cur.close()
        return True
    except Exception as e:
        print(f"⚠️ Alert snapshot yazma hatası: {e}")
        return False


# =============================================================================
# TOKEN EVALUATIONS (Alert kalite analizi için)
# =============================================================================

def init_token_evaluations():
    """token_evaluations tablosunu oluştur."""
    conn = get_connection()
    if not conn:
        return False
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS token_evaluations (
                id SERIAL PRIMARY KEY,
                token_address VARCHAR(42) NOT NULL,
                token_symbol VARCHAR(20),
                alert_mcap BIGINT,
                mcap_5min BIGINT,
                mcap_30min BIGINT,
                change_5min_pct FLOAT,
                change_30min_pct FLOAT,
                classification VARCHAR(20),
                wallets_involved JSONB,
                alert_time TIMESTAMP,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_te_token ON token_evaluations(token_address)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_te_class ON token_evaluations(classification)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_te_alert_time ON token_evaluations(alert_time)")
        cur.close()
        return True
    except Exception as e:
        print(f"⚠️ token_evaluations tablo oluşturma hatası: {e}")
        return False


def save_token_evaluation(token_address: str, token_symbol: str, alert_mcap: int,
                          wallets_involved: list = None, alert_time: str = None,
                          mcap_5min: int = None, mcap_30min: int = None,
                          change_5min_pct: float = None, change_30min_pct: float = None,
                          classification: str = None, ath_mcap: int = None) -> bool:
    """Token değerlendirme kaydı oluştur veya güncelle."""
    conn = get_connection()
    if not conn:
        return False
    try:
        cur = conn.cursor()

        # Aynı token + alert_time var mı? (güncelleme için)
        cur.execute("""
            SELECT id, COALESCE(ath_mcap, 0) FROM token_evaluations
            WHERE token_address = %s AND alert_time = %s
            LIMIT 1
        """, (token_address.lower(), alert_time))
        existing = cur.fetchone()

        if existing:
            # Güncelle (5dk/30dk sonrası MCap gelmiş olabilir)
            updates = []
            params = []
            if mcap_5min is not None:
                updates.append("mcap_5min = %s")
                params.append(mcap_5min)
            if mcap_30min is not None:
                updates.append("mcap_30min = %s")
                params.append(mcap_30min)
            if change_5min_pct is not None:
                updates.append("change_5min_pct = %s")
                params.append(change_5min_pct)
            if change_30min_pct is not None:
                updates.append("change_30min_pct = %s")
                params.append(change_30min_pct)
            if classification is not None:
                updates.append("classification = %s")
                params.append(classification)
            # ATH MCap: sadece mevcut değerden büyükse güncelle
            if ath_mcap is not None:
                current_ath = existing[1] or 0
                if ath_mcap > current_ath:
                    updates.append("ath_mcap = %s")
                    params.append(ath_mcap)
            if updates:
                params.append(existing[0])
                cur.execute(f"UPDATE token_evaluations SET {', '.join(updates)} WHERE id = %s", params)
        else:
            cur.execute("""
                INSERT INTO token_evaluations
                    (token_address, token_symbol, alert_mcap, wallets_involved, alert_time,
                     mcap_5min, mcap_30min, change_5min_pct, change_30min_pct, classification, ath_mcap)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (token_address.lower(), token_symbol, alert_mcap,
                  Json(wallets_involved or []), alert_time,
                  mcap_5min, mcap_30min, change_5min_pct, change_30min_pct, classification,
                  ath_mcap or alert_mcap))  # İlk kayıtta ath_mcap = alert_mcap

        cur.close()
        return True
    except Exception as e:
        print(f"⚠️ Token evaluation yazma hatası: {e}")
        return False


def get_all_token_evaluations() -> list:
    """Tüm token değerlendirmelerini getir."""
    conn = get_connection()
    if not conn:
        return []
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, token_address, token_symbol, alert_mcap, mcap_5min, mcap_30min,
                   change_5min_pct, change_30min_pct, classification, wallets_involved, alert_time, created_at
            FROM token_evaluations ORDER BY alert_time ASC
        """)
        rows = cur.fetchall()
        cur.close()
        return [{
            "id": r[0], "token_address": r[1], "token_symbol": r[2],
            "alert_mcap": r[3], "mcap_5min": r[4], "mcap_30min": r[5],
            "change_5min_pct": r[6], "change_30min_pct": r[7],
            "classification": r[8], "wallets_involved": r[9],
            "alert_time": r[10].isoformat() if r[10] else None,
            "created_at": r[11].isoformat() if r[11] else None
        } for r in rows]
    except Exception as e:
        print(f"⚠️ Token evaluations okuma hatası: {e}")
        return []


# =============================================================================
# ALERT SNAPSHOT & TRADE SIGNAL QUERIES (Tarihsel analiz)
# =============================================================================

def get_all_alert_snapshots() -> list:
    """Tüm alert snapshot'ları getir (tarihsel analiz için)."""
    conn = get_connection()
    if not conn:
        return []
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, token_address, token_symbol, alert_mcap, alert_block,
                   wallet_count, first_sm_block, early_buyers_found, created_at,
                   COALESCE(wallets_involved, '')
            FROM alert_snapshots ORDER BY created_at ASC
        """)
        rows = cur.fetchall()
        cur.close()
        return [{
            "id": r[0], "token_address": r[1], "token_symbol": r[2],
            "alert_mcap": r[3], "alert_block": r[4], "wallet_count": r[5],
            "first_sm_block": r[6], "early_buyers_found": r[7],
            "created_at": r[8].isoformat() if r[8] else None,
            "wallets_involved": [w for w in r[9].split(",") if w] if r[9] else [],
        } for r in rows]
    except Exception as e:
        print(f"⚠️ Alert snapshots okuma hatası: {e}")
        return []


def get_all_trade_signals_history() -> list:
    """Tüm trade signal geçmişini getir."""
    conn = get_connection()
    if not conn:
        return []
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, token_address, token_symbol, entry_mcap, trigger_type,
                   wallet_count, status, created_at, processed_at, trade_result
            FROM trade_signals ORDER BY created_at ASC
        """)
        rows = cur.fetchall()
        cur.close()
        return [{
            "id": r[0], "token_address": r[1], "token_symbol": r[2],
            "entry_mcap": r[3], "trigger_type": r[4], "wallet_count": r[5],
            "status": r[6],
            "created_at": r[7].isoformat() if r[7] else None,
            "processed_at": r[8].isoformat() if r[8] else None,
            "trade_result": r[9]
        } for r in rows]
    except Exception as e:
        print(f"⚠️ Trade signals history okuma hatası: {e}")
        return []


def get_wallet_alert_participation() -> list:
    """Her cüzdanın hangi alertlere katıldığını getir."""
    conn = get_connection()
    if not conn:
        return []
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT wallet_address, token_address, token_symbol, alert_mcap, created_at
            FROM wallet_activity
            ORDER BY wallet_address, created_at ASC
        """)
        rows = cur.fetchall()
        cur.close()
        return [{
            "wallet_address": r[0], "token_address": r[1], "token_symbol": r[2],
            "alert_mcap": r[3],
            "created_at": r[4].isoformat() if r[4] else None
        } for r in rows]
    except Exception as e:
        print(f"⚠️ Wallet alert participation okuma hatası: {e}")
        return []


def get_wallet_participation_from_snapshots() -> list:
    """Alert snapshot'lardan her cüzdanın hangi alertlere katıldığını çıkar."""
    conn = get_connection()
    if not conn:
        return []
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT token_address, token_symbol, wallets_involved, created_at
            FROM alert_snapshots
            WHERE wallets_involved IS NOT NULL AND wallets_involved != ''
            ORDER BY created_at ASC
        """)
        rows = cur.fetchall()
        cur.close()

        results = []
        for r in rows:
            token_addr = r[0]
            token_symbol = r[1]
            wallets_str = r[2] or ""
            created_at = r[3]
            for wallet in wallets_str.split(","):
                wallet = wallet.strip().lower()
                if wallet:
                    results.append({
                        "wallet_address": wallet,
                        "token_address": token_addr,
                        "token_symbol": token_symbol,
                        "created_at": created_at.isoformat() if created_at else None,
                    })
        return results
    except Exception as e:
        print(f"⚠️ Snapshot wallet participation okuma hatası: {e}")
        return []


def get_signal_by_token_recent(token_address: str, max_age_seconds: int = 600) -> dict:
    """Token adresi ile son 10dk içindeki pending/pending_confirmation sinyali bul."""
    conn = get_connection()
    if not conn:
        return None
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, token_address, token_symbol, entry_mcap, trigger_type, wallet_count, status, created_at
            FROM trade_signals
            WHERE token_address = %s
              AND status IN ('pending', 'pending_confirmation')
              AND created_at > NOW() - INTERVAL '%s seconds'
            ORDER BY created_at DESC
            LIMIT 1
        """, (token_address.lower(), max_age_seconds))
        row = cur.fetchone()
        cur.close()
        if row:
            return {
                "id": row[0], "token_address": row[1], "token_symbol": row[2],
                "entry_mcap": row[3], "trigger_type": row[4], "wallet_count": row[5],
                "status": row[6], "created_at": row[7].isoformat() if row[7] else None
            }
        return None
    except Exception as e:
        print(f"⚠️ Signal by token okuma hatası: {e}")
        return None


def approve_signal(signal_id: int, approval_data: dict = None) -> bool:
    """Pending_confirmation sinyalini 'approved' yap (5dk MCap check geçtikten sonra)."""
    conn = get_connection()
    if not conn:
        return False
    try:
        cur = conn.cursor()
        if approval_data:
            cur.execute("""
                UPDATE trade_signals
                SET status = 'approved', trade_result = %s
                WHERE id = %s AND status = 'pending_confirmation'
            """, (Json(approval_data), signal_id))
        else:
            cur.execute("""
                UPDATE trade_signals
                SET status = 'approved'
                WHERE id = %s AND status = 'pending_confirmation'
            """, (signal_id,))
        updated = cur.rowcount
        cur.close()
        if updated > 0:
            print(f"✅ Signal #{signal_id} approved (5dk MCap check geçti)")
        return updated > 0
    except Exception as e:
        print(f"⚠️ Signal approve hatası: {e}")
        return False


def get_approved_signals(max_age_seconds: int = 600) -> list:
    """Approved durumundaki sinyalleri al (Confirmation Sniper için)."""
    conn = get_connection()
    if not conn:
        return []
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, token_address, token_symbol, entry_mcap, trigger_type, wallet_count, created_at
            FROM trade_signals
            WHERE status = 'approved'
              AND created_at > NOW() - INTERVAL '%s seconds'
            ORDER BY created_at ASC
        """, (max_age_seconds,))
        rows = cur.fetchall()
        cur.close()
        return [{
            "id": r[0], "token_address": r[1], "token_symbol": r[2],
            "entry_mcap": r[3], "trigger_type": r[4], "wallet_count": r[5],
            "created_at": r[6].isoformat() if r[6] else None
        } for r in rows]
    except Exception as e:
        print(f"⚠️ Approved signals okuma hatası: {e}")
        return []


def expire_old_confirmation_signals(max_age_seconds: int = 600) -> int:
    """10dk'dan eski pending_confirmation sinyalleri 'skipped' yap (MCap check geçemedi)."""
    conn = get_connection()
    if not conn:
        return 0
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE trade_signals
            SET status = 'skipped', processed_at = NOW(),
                trade_result = '{"reason": "mcap_confirmation_timeout"}'::jsonb
            WHERE status = 'pending_confirmation'
              AND created_at <= NOW() - INTERVAL '%s seconds'
        """, (max_age_seconds,))
        count = cur.rowcount
        cur.close()
        if count > 0:
            print(f"⏰ {count} onay bekleyen sinyal timeout (5dk check geçemedi)")
        return count
    except Exception as e:
        print(f"⚠️ Confirmation expire hatası: {e}")
        return 0


def cleanup_old_wallet_activity(days: int = 30) -> int:
    """Eski wallet activity kayıtlarını temizle."""
    conn = get_connection()
    if not conn:
        return 0
    try:
        cur = conn.cursor()
        cur.execute("""
            DELETE FROM wallet_activity WHERE created_at < NOW() - INTERVAL '%s days'
        """, (days,))
        count = cur.rowcount
        cur.close()
        if count > 0:
            print(f"🗑️ {count} eski wallet activity kaydı temizlendi")
        return count
    except Exception as e:
        print(f"⚠️ Cleanup hatası: {e}")
        return 0


def get_alerts_by_date_range(start_utc: str, end_utc: str) -> list:
    """
    Belirli UTC tarih aralığındaki alert snapshot'larını getir.
    token_evaluations'tan alert_mcap ve classification bilgisini LEFT JOIN ile çeker.
    alert_snapshots.alert_mcap=0 ise token_evaluations'taki değeri kullanır.

    Returns:
        list: [{token_address, token_symbol, alert_mcap, wallet_count, classification, created_at}, ...]
    """
    conn = get_connection()
    if not conn:
        return []
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                a.token_address,
                a.token_symbol,
                CASE WHEN a.alert_mcap > 0 THEN a.alert_mcap ELSE COALESCE(te.alert_mcap, 0) END as alert_mcap,
                a.wallet_count,
                a.created_at,
                te.classification,
                COALESCE(te.ath_mcap, 0) as ath_mcap
            FROM alert_snapshots a
            LEFT JOIN LATERAL (
                SELECT alert_mcap, classification, ath_mcap
                FROM token_evaluations
                WHERE token_address = a.token_address
                ORDER BY ABS(EXTRACT(EPOCH FROM (alert_time - a.created_at))) ASC
                LIMIT 1
            ) te ON true
            WHERE a.created_at >= %s AND a.created_at < %s
            ORDER BY a.created_at ASC
        """, (start_utc, end_utc))
        rows = cur.fetchall()
        cur.close()
        return [{
            "token_address": r[0],
            "token_symbol": r[1],
            "alert_mcap": r[2] or 0,
            "wallet_count": r[3] or 0,
            "created_at": r[4].isoformat() if r[4] else None,
            "classification": r[5] or "unknown",
            "ath_mcap": r[6] or 0,
        } for r in rows]
    except Exception as e:
        print(f"⚠️ Date range alert sorgu hatası: {e}")
        return []


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
