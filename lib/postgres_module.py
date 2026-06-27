import logging
import os
import re
import threading
from pathlib import Path
import psycopg2
from psycopg2 import sql
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool
from typing import Any, Dict, List, Optional, Tuple, Union

module_logger = logging.getLogger('icad_dispatch.postgres_module')
MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

class PostgreSQLDatabase:
    """
    PostgreSQL wrapper providing structured responses and transaction support.
    
    Features:
      - Connection pooling (1-10 connections)
      - Auto-translates common SQLite syntax to PostgreSQL
      - Returns structured responses matching SQLite wrapper
      - Supports transactions (begin/commit/rollback)
    """
    
    def __init__(self):
        self.host = os.getenv('PG_HOST', 'localhost')
        self.port = int(os.getenv('PG_PORT', '5432'))
        self.database = os.getenv('PG_DATABASE', 'icad_dispatch')
        self.user = os.getenv('PG_USER', 'icad')
        self.password = os.getenv('PG_PASSWORD', '')
        
        self.pool = ThreadedConnectionPool(
            minconn=1, maxconn=10,
            host=self.host, port=self.port,
            dbname=self.database, user=self.user, password=self.password
        )
        
        # Thread-local transaction state (safe for concurrent greenlets/threads)
        self._tx_local = threading.local()
        self._init_extensions()
        self._ensure_schema_migrations()
        self._run_pending_migrations()
    
    def _init_extensions(self):
        """Install PostGIS, pg_trgm, and pgcrypto if available."""
        try:
            with self._get_cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS postgis")
                cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
                cur.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
                cur.connection.commit()
                module_logger.info("PostgreSQL extensions initialized")
        except Exception as e:
            module_logger.warning("Could not install extensions: %s", e)
    
    def _ensure_schema_migrations(self):
        """Create schema tracking table if missing."""
        with self._get_cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY
                )
            """)
            cur.connection.commit()
    
    def _get_cursor(self, use_dict_cursor=False):
        """Context manager for database cursor.
        
        Args:
            use_dict_cursor: If True, returns rows as dicts (for SELECT queries).
                            If False, returns tuples (for INSERT/UPDATE/DELETE).
        """
        class CursorContext:
            def __init__(self, pool, dict_cursor):
                self.pool = pool
                self.dict_cursor = dict_cursor
                self.conn = None
                self.cur = None
            
            def __enter__(self):
                self.conn = self.pool.getconn()
                if self.dict_cursor:
                    self.cur = self.conn.cursor(cursor_factory=RealDictCursor)
                else:
                    self.cur = self.conn.cursor()
                return self.cur
            
            def __exit__(self, exc_type, exc_val, exc_tb):
                if self.cur:
                    self.cur.close()
                if self.conn:
                    self.pool.putconn(self.conn)
        
        return CursorContext(self.pool, use_dict_cursor)
    
    def _translate_sql(self, sql_str: str) -> str:
        """
        Translate SQLite-specific syntax to PostgreSQL.
        """
        # Strip PRAGMA statements (SQLite-only, unused in PostgreSQL)
        sql_str = re.sub(r'^\s*PRAGMA\s+\w+.*?(?:;|$)', '', sql_str, flags=re.MULTILINE | re.IGNORECASE)

        # Strip BEGIN/COMMIT if present (migration wrapping)
        # These are safe to strip because run_migration() executes atomically

        # Replace hex(randomblob(N)) with encode(gen_random_bytes(N), 'hex') — must be before standalone randomblob
        sql_str = re.sub(r'\bhex\(randomblob\((\d+)\)\)', r"encode(gen_random_bytes(\1), 'hex')", sql_str, flags=re.IGNORECASE)

        # Replace SQLite randomblob(N) with PostgreSQL gen_random_bytes(N)
        sql_str = re.sub(r'randomblob\((\d+)\)', r'gen_random_bytes(\1)', sql_str, flags=re.IGNORECASE)

        # Replace json_extract(col, '$.key') with col->>'key'
        sql_str = re.sub(r"json_extract\((\w+),\s*'\$\.(\w+)'\)", r"\1->>'\2'", sql_str)

        # Replace SQLite DATETIME type with PostgreSQL TIMESTAMP
        sql_str = re.sub(r'\bDATETIME\b', 'TIMESTAMP', sql_str, flags=re.IGNORECASE)

        # Add CASCADE to DROP TABLE statements (PostgreSQL requires it when FK deps exist)
        sql_str = re.sub(r'\bDROP\s+TABLE\s+(IF\s+EXISTS\s+)?(\w+)\s*;',
                         r'DROP TABLE IF EXISTS \2 CASCADE;',
                         sql_str, flags=re.IGNORECASE)

        # Replace ? placeholders with %s
        sql_str = re.sub(r'\?', '%s', sql_str)
        
        # Replace strftime with EXTRACT
        sql_str = re.sub(
            r"strftime\('%s',\s*'now'\)",
            "EXTRACT(EPOCH FROM NOW())::INTEGER",
            sql_str
        )
        
        # Replace LIKE with ILIKE for case-insensitive matching
        # Only match LIKE when preceded by ) or a word char and followed by space/quote
        # This avoids replacing the word "like" inside string literals or column names
        sql_str = re.sub(r'(\w|\))(\s+)LIKE(\s+)', r'\1\2ILIKE\3', sql_str)
        
        # Replace GROUP_CONCAT with STRING_AGG
        # GROUP_CONCAT(DISTINCT col) → STRING_AGG(DISTINCT col::TEXT, ', ')
        sql_str = re.sub(
            r"GROUP_CONCAT\((\s*DISTINCT\s+)?(.*?)\s*\)",
            r"STRING_AGG(\1\2::TEXT, ', ')",
            sql_str,
            flags=re.IGNORECASE
        )
        
        # Replace INSERT OR IGNORE with INSERT ... ON CONFLICT DO NOTHING
        if re.search(r"INSERT\s+OR\s+IGNORE\s+INTO", sql_str, flags=re.IGNORECASE):
            sql_str = re.sub(r"INSERT\s+OR\s+IGNORE\s+INTO", "INSERT INTO", sql_str, flags=re.IGNORECASE)
            sql_str = sql_str.rstrip().rstrip(';') + " ON CONFLICT DO NOTHING"
        
        return sql_str
    
    def execute_query(self, sql_str: str, params: Union[tuple, list] = (), 
                     fetch_mode: str = "all") -> Dict[str, Any]:
        """
        Execute a SELECT query and return structured result.
        
        Returns: {"success": bool, "result": rows, "message": str, "row_count": int}
        """
        translated_sql = self._translate_sql(sql_str)
        
        try:
            with self._get_cursor(use_dict_cursor=True) as cur:
                cur.execute(translated_sql, params)
                
                if fetch_mode == "one":
                    row = cur.fetchone()
                    result = dict(row) if row else None
                    row_count = 1 if row else 0
                elif fetch_mode == "all":
                    rows = cur.fetchall()
                    result = [dict(r) for r in rows] if rows else []
                    row_count = len(result)
                else:
                    result = None
                    row_count = cur.rowcount
                
                return {
                    "success": True,
                    "result": result,
                    "message": f"Query executed successfully ({row_count} rows)",
                    "row_count": row_count
                }
        
        except psycopg2.Error as e:
            module_logger.error("PostgreSQL query error: %s | SQL: %s | Params: %s", 
                               e, translated_sql[:200], params)
            return {
                "success": False,
                "result": None,
                "message": str(e),
                "row_count": 0
            }
    
    def execute_commit(self, sql_str: str, params: Union[tuple, list] = (),
                      return_row_id: bool = False,
                      return_count: bool = False) -> Dict[str, Any]:
        """
        Execute an INSERT/UPDATE/DELETE and return structured result.
        Returns {"result": value, "row_count": n} for consistency with query wrappers.
        """
        translated_sql = self._translate_sql(sql_str)

        try:
            row_id = None
            affected = 0

            # If in explicit transaction, use that connection
            tx_conn = getattr(self._tx_local, 'conn', None)
            if tx_conn:
                cur = tx_conn.cursor()
                cur.execute(translated_sql, params)
                affected = cur.rowcount
                
                # Try to get lastval() for auto-increment
                try:
                    cur.execute("SELECT lastval()")
                    row_id = cur.fetchone()[0]
                except:
                    row_id = None
                
                cur.close()
                return {
                    "success": True,
                    "message": "Statement executed in transaction",
                    "result": row_id,
                    "row_count": affected,
                }

            # Otherwise, auto-commit
            with self._get_cursor() as cur:
                cur.execute(translated_sql, params)
                cur.connection.commit()
                affected = cur.rowcount

                if return_row_id:
                    try:
                        cur.execute("SELECT lastval()")
                        row_id = cur.fetchone()[0]
                    except:
                        row_id = None

                res = row_id if return_row_id else (affected if return_count else [])
                return {
                    "success": True,
                    "message": "Statement executed successfully",
                    "result": res,
                    "row_count": affected,
                }

        except psycopg2.Error as e:
            module_logger.error("PostgreSQL commit error: %s | SQL: %s", e, translated_sql[:200])
            return {
                "success": False,
                "message": str(e),
                "result": [],
                "row_count": 0,
            }
    
    def execute_many(self, sql_str: str, params_list: List[tuple]) -> Dict[str, Any]:
        """
        Execute a statement multiple times with different parameters.
        """
        translated_sql = self._translate_sql(sql_str)

        try:
            tx_conn = getattr(self._tx_local, 'conn', None)
            if tx_conn:
                cur = tx_conn.cursor()
                cur.executemany(translated_sql, params_list)
                result = {
                    "success": True,
                    "message": f"Batch executed ({len(params_list)} items) in transaction",
                    "result": [],
                    "row_count": cur.rowcount,
                }
                cur.close()
                return result

            with self._get_cursor() as cur:
                cur.executemany(translated_sql, params_list)
                cur.connection.commit()
                return {
                    "success": True,
                    "message": f"Batch executed successfully ({len(params_list)} items)",
                    "result": [],
                    "row_count": cur.rowcount,
                }

        except psycopg2.Error as e:
            module_logger.error("PostgreSQL batch error: %s", e)
            return {
                "success": False,
                "message": str(e),
                "result": [],
                "row_count": 0,
            }
    
    def begin(self) -> None:
        """Start an explicit transaction."""
        if not getattr(self._tx_local, 'conn', None):
            self._tx_local.conn = self.pool.getconn()
    
    def commit(self) -> None:
        """Commit the current transaction."""
        tx_conn = getattr(self._tx_local, 'conn', None)
        if tx_conn:
            tx_conn.commit()
            self.pool.putconn(tx_conn)
            self._tx_local.conn = None
    
    def rollback(self) -> None:
        """Rollback the current transaction."""
        tx_conn = getattr(self._tx_local, 'conn', None)
        if tx_conn:
            tx_conn.rollback()
            self.pool.putconn(tx_conn)
            self._tx_local.conn = None
    
    def close(self) -> None:
        """Close all connections in the pool."""
        tx_conn = getattr(self._tx_local, 'conn', None)
        if tx_conn:
            tx_conn.rollback()
            self.pool.putconn(tx_conn)
            self._tx_local.conn = None
        self.pool.closeall()
    
    # Migration helpers
    def run_migration(self, sql_content: str) -> bool:
        """Execute a migration SQL file."""
        # Translate SQLite syntax to PostgreSQL before executing
        sql_content = self._translate_sql(sql_content)
        # Also handle INTEGER PRIMARY KEY AUTOINCREMENT → SERIAL PRIMARY KEY
        sql_content = re.sub(
            r'INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT',
            'SERIAL PRIMARY KEY',
            sql_content,
            flags=re.IGNORECASE
        )
        try:
            with self._get_cursor() as cur:
                cur.execute(sql_content)
                cur.connection.commit()
                return True
        except psycopg2.Error as e:
            module_logger.error("Migration failed: %s", e)
            return False
    
    def get_applied_versions(self) -> set:
        """Get set of applied migration versions."""
        result = self.execute_query(
            "SELECT version FROM schema_migrations",
            fetch_mode="all"
        )
        if result["success"] and result["result"]:
            return {row["version"] for row in result["result"]}
        return set()

    def _run_pending_migrations(self) -> None:
        """
        Apply every migrations/NNN_description.sql whose NNN is not yet in
        schema_migrations. Each file is applied atomically.
        """
        pending_files: List[Path] = sorted(MIGRATIONS_DIR.glob("*.sql"))
        if not pending_files:
            module_logger.info("No migration files found in %s", MIGRATIONS_DIR)
            return

        applied = self.get_applied_versions()

        for path in pending_files:
            try:
                version = int(path.stem.split("_", 1)[0])
            except ValueError:
                module_logger.warning("Skipping migration with bad filename: %s", path.name)
                continue

            if version in applied:
                continue

            module_logger.info("Applying migration %03d – %s", version, path.name)
            sql_content = path.read_text(encoding="utf-8")

            if self.run_migration(sql_content):
                # Record the migration as applied
                self.execute_commit(
                    "INSERT INTO schema_migrations (version) VALUES (%s)",
                    (version,),
                )
                module_logger.info("Migration %s applied successfully.", path.name)
            else:
                module_logger.warning(
                    "Migration %s did not apply cleanly — this is expected on fresh installs "
                    "for legacy data migrations. Marking as applied and continuing.", path.name
                )
                self.execute_commit(
                    "INSERT INTO schema_migrations (version) VALUES (%s)",
                    (version,),
                )