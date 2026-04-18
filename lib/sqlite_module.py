import logging
import os
import sqlite3
from pathlib import Path
from sqlite3 import Connection
from contextlib import contextmanager
from typing import Any, Generator, Set, List, Iterable, Optional
import fcntl
import threading

import bcrypt

module_logger = logging.getLogger('icad_dispatch.sqlite_module')

# ---------------------------------------------------------------------------
#  constants
# ---------------------------------------------------------------------------
MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
SCHEMA_TRACK_TABLE = "schema_migrations"


class SQLiteDatabase:
    """
    SQLite wrapper that returns structured responses and supports explicit transactions.

    Features:
      - Creates/initializes the DB file and applies SQL migrations on first run.
      - Enforces PRAGMA foreign_keys = ON on every connection.
      - Provides structured return objects for query/commit operations.
      - Supports explicit multi-statement transactions (`begin/commit/rollback`).
      - Falls back to auto-commit when not in an explicit transaction.

    The wrapper is designed to work with higher-level modules that may call
    `begin()` → multiple `execute_*()` → `commit()` (or `rollback()` on error).
    """

    def __init__(self):
        """
        Initialize the SQLite database. If the file doesn't exist, create it and
        build an initial schema, then run any pending migrations and bootstrap users.

        :raises ValueError:
            If the `db_path` is empty or not a valid string.
        :raises IsADirectoryError:
            If the provided `db_path` points to a directory rather than a file.
        :raises RuntimeError:
            If bootstrap credentials are missing on first boot.
        """
        db_path = os.getenv("SQLITE_DATABASE_PATH", "var/icad_dispatch.sqlite")

        # Validate input
        if db_path is None or not isinstance(db_path, str) or not db_path.strip():
            raise ValueError("Invalid database path provided (empty or not a string).")

        # Ensure the parent folder exists
        parent_dir = os.path.dirname(db_path)
        if parent_dir and not os.path.exists(parent_dir):
            module_logger.info("Creating database directory at '%s'", parent_dir)
            os.makedirs(parent_dir, exist_ok=True)

        if os.path.isdir(db_path):
            raise IsADirectoryError(f"Provided path '{db_path}' is a directory, not a file.")

        self.db_path = db_path

        # active transaction connection (if any)
        # per-thread transaction connection (NOT shared across threads)
        self._tx_local = threading.local()

        # lock file lives next to the DB (good: inside your /app/var volume)
        self._migrate_lock_path = f"{self.db_path}.migrate.lock"

        # ------------------------------------------------------------------
        # 1st start-up: create empty file & migration meta-table
        # ------------------------------------------------------------------
        if not Path(self.db_path).exists():
            module_logger.warning("Database file not found – creating a new one at '%s'.", self.db_path)

        with self._migration_lock():
            with self._get_connection() as conn:
                conn.executescript(f"""
                    PRAGMA foreign_keys = ON;
                    CREATE TABLE IF NOT EXISTS {SCHEMA_TRACK_TABLE}(
                        version INTEGER PRIMARY KEY
                    );
                """)
                conn.commit()

        # ------------------------------------------------------------------
        # run any pending migrations
        # ------------------------------------------------------------------
            self._run_pending_migrations()

        # ------------------------------------------------------------------
        # ensure there is at least one admin (idempotent)
        # ------------------------------------------------------------------
            self._bootstrap_users()

    # ======================================================================
    # Connection management
    # ======================================================================

    def _get_tx_conn(self) -> Optional[Connection]:
        return getattr(self._tx_local, "conn", None)

    def _set_tx_conn(self, conn: Optional[Connection]) -> None:
        self._tx_local.conn = conn

    @contextmanager
    def _migration_lock(self) -> Generator[None, Any, None]:
        Path(os.path.dirname(self._migrate_lock_path) or ".").mkdir(parents=True, exist_ok=True)
        with open(self._migrate_lock_path, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)  # blocks until lock is acquired
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def _configure_conn(self, conn: sqlite3.Connection) -> None:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA busy_timeout = 30000;")  # wait up to 30s on locks
        try:
            conn.execute("PRAGMA journal_mode = WAL;")
        except Exception:
            pass

    @contextmanager
    def _get_connection(self) -> Generator[Connection, Any, None]:
        """
        Provide a short-lived, context-managed connection to the SQLite database.

        Returns a new connection each time; enables FK constraints for that connection.
        The connection is automatically closed upon exiting the context block.
        """
        conn = None
        try:
            conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30.0)
            self._configure_conn(conn)
            yield conn
        finally:
            if conn:
                conn.close()

    @contextmanager
    def _conn_ctx(self) -> Generator[Connection, Any, None]:
        """
        Yield the active transaction connection if present; otherwise open
        a short-lived connection. This lets higher-level code run multi-statement
        transactions safely without changing call sites for execute_* functions.
        """
        tx = self._get_tx_conn()
        if tx is not None:
            yield tx
        else:
            with self._get_connection() as conn:
                yield conn

    def begin(self) -> None:
        """
        Begin a connection-scoped transaction.

        If a transaction is already active, this is a no-op.
        """
        if self._get_tx_conn() is not None:
            return
        conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30.0)
        self._configure_conn(conn)
        try:
            conn.execute("BEGIN;")
        except Exception:
            conn.close()
            raise
        self._set_tx_conn(conn)

    def commit(self) -> None:
        """
        Commit the active transaction (if any) and close the transaction connection.
        """
        tx = self._get_tx_conn()
        if tx is None:
            return
        try:
            tx.commit()
        finally:
            tx.close()
            self._set_tx_conn(None)

    def rollback(self) -> None:
        """
        Roll back the active transaction (if any) and close the transaction connection.
        """
        tx = self._get_tx_conn()
        if tx is None:
            return
        try:
            tx.rollback()
        finally:
            tx.close()
            self._set_tx_conn(None)

    # ======================================================================
    # Secrets / bootstrap
    # ======================================================================

    @staticmethod
    def _read_secret(name: str, default: str | None = None) -> str | None:
        """
        Prefer file-based secret (NAME_FILE) then env var (NAME).
        Example: ROOT_PASSWORD_FILE=/run/secrets/icad_root_password
        """
        file_var = os.getenv(f"{name}_FILE")
        if file_var and os.path.isfile(file_var):
            try:
                with open(file_var, "r", encoding="utf-8") as f:
                    return f.read().strip()
            except Exception:
                pass
        val = os.getenv(name, default)
        return val.strip() if isinstance(val, str) else val

    def _bootstrap_users(self) -> None:
        """
        Create ROOT and USER accounts on first init, idempotently.

        Env (or *_FILE) inputs:
          - ROOT_USERNAME, ROOT_PASSWORD
          - USER_USERNAME, USER_PASSWORD
        """
        root_username = self._read_secret("ROOT_USERNAME", "root")
        root_password = self._read_secret("ROOT_PASSWORD", "root")

        # If database already has any users, assume it has been bootstrapped
        existing = self.execute_query("SELECT COUNT(*) AS c FROM users", fetch_mode="one")
        if not existing["success"]:
            module_logger.error("Could not count users: %s", existing["message"])
            return
        if existing["result"].get("c", 0) > 0:
            return

        # Guardrails: require both passwords on first boot
        missing = []
        if not root_password:
            missing.append("ROOT_PASSWORD or ROOT_PASSWORD_FILE")
        if missing:
            module_logger.error("First-boot user bootstrap aborted; missing: %s", ", ".join(missing))
            raise RuntimeError("Missing bootstrap credentials")

        root_hash = bcrypt.hashpw(root_password.encode("utf-8"), bcrypt.gensalt())

        # Insert both users atomically
        q = "INSERT INTO users (user_username, user_password) VALUES (?, ?)"
        with self._get_connection() as conn:
            try:
                with conn:  # transaction
                    conn.execute(q, (root_username, root_hash))
                module_logger.warning("Bootstrapped ROOT (%s).", root_username)
            except sqlite3.IntegrityError as e:
                # If usernames already exist, do nothing (idempotent)
                module_logger.info("Bootstrap skipped; users already present: %s", e)
            except sqlite3.Error as e:
                module_logger.error("Bootstrap insert failed: %s", e)
                raise

    # ======================================================================
    # Migrations
    # ======================================================================

    @staticmethod
    def _row_to_dict(row, column_names) -> dict:
        """
        Convert a single database row (tuple) into a dictionary using the provided column names.

        :param row: A row tuple returned by a database cursor. If None, an empty dict is returned.
        :param column_names: The list of column names corresponding to the row's columns.
        :return: A dictionary mapping column names to values. If `row` is None, returns {}.
        """
        if not row:
            return {}
        row_dict = {}
        for i, val in enumerate(row):
            row_dict[column_names[i]] = val
        return row_dict

    def _get_applied_versions(self, conn: sqlite3.Connection) -> Set[int]:
        cur = conn.execute(f"SELECT version FROM {SCHEMA_TRACK_TABLE}")
        return {row["version"] for row in cur.fetchall()}

    def _run_pending_migrations(self) -> None:
        """
        Apply every `migrations/NNN_description.sql` whose NNN
        is not yet in `schema_migrations`. Each file is applied atomically.
        """
        pending_files: List[Path] = sorted(MIGRATIONS_DIR.glob("*.sql"))
        if not pending_files:
            module_logger.info("No migration files found in %s", MIGRATIONS_DIR)
            return

        with self._get_connection() as conn:
            applied = self._get_applied_versions(conn)

            for path in pending_files:
                try:
                    version = int(path.stem.split("_", 1)[0])
                except ValueError:
                    module_logger.warning("Skipping migration with bad filename: %s", path.name)
                    continue

                if version in applied:
                    continue  # already applied

                module_logger.info("Applying migration %03d – %s", version, path.name)
                sql = path.read_text(encoding="utf-8")

                try:
                    with conn:  # atomic per file
                        conn.executescript(sql)
                        conn.execute(
                            f"INSERT INTO {SCHEMA_TRACK_TABLE}(version) VALUES (?)",
                            (version,),
                        )
                    module_logger.info("Migration %s applied successfully.", path.name)
                except sqlite3.Error as e:
                    module_logger.error("Migration %s failed: %s\nRolling back.", path.name, e)
                    raise

    # ======================================================================
    # Public query/commit APIs
    # ======================================================================

    def execute_query(self, query: str, params=None, fetch_mode: str = "all") -> dict:
        """
        Execute a SELECT-like query, returning data in a structured dictionary.

        :param query: SQL string using '?' placeholders (or '%s' which will be rewritten).
        :param params: Tuple/list of parameters or None.
        :param fetch_mode: "all" (default) | "many" | "one".
        :return: dict: {"success": bool, "message": str, "result": list|dict}
        """
        if "%s" in query:
            query = query.replace("%s", "?")

        with self._conn_ctx() as conn:
            cursor = conn.cursor()
            try:
                if params is not None:
                    cursor.execute(query, params)
                else:
                    cursor.execute(query)

                column_names = [desc[0] for desc in cursor.description] if cursor.description else []

                if fetch_mode == "all":
                    rows = cursor.fetchall()
                    result = [self._row_to_dict(row, column_names) for row in rows]
                elif fetch_mode == "many":
                    rows = cursor.fetchmany()
                    result = [self._row_to_dict(row, column_names) for row in rows]
                elif fetch_mode == "one":
                    row = cursor.fetchone()
                    result = self._row_to_dict(row, column_names) if row else {}
                else:
                    return {"success": False, "message": f"Invalid fetch_mode: {fetch_mode}", "result": []}

                return {"success": True, "message": "SQLite Query executed successfully", "result": result}

            except sqlite3.Error as e:
                module_logger.error("SQLite Query Failure: %s | Query: %s | Params: %s", e, query, params)
                return {"success": False, "message": str(e), "result": []}
            finally:
                cursor.close()

    def execute_commit(
            self,
            query: str,
            params=None,
            return_row_id: bool = True,
            return_count: bool = False
    ) -> dict:
        """
        Execute an INSERT/UPDATE/DELETE (write) and commit changes when appropriate.

        Auto-commit behavior:
          - If inside an explicit transaction (after `begin()`), this method does NOT
            commit—caller must call `commit()` (or `rollback()` on error).
          - If NOT in a transaction, this method commits immediately.

        :param query: SQL string using '?' placeholders (or '%s' which will be rewritten).
        :param params: Tuple/list of parameters or None.
        :param return_row_id: If True, return `lastrowid`; if False and `return_count=True`,
                              return affected row count; else return [].
        :param return_count: If True (and `return_row_id` is False), return affected row count.
        """
        if "%s" in query:
            query = query.replace("%s", "?")

        with self._conn_ctx() as conn:
            cursor = conn.cursor()
            try:
                if params is not None:
                    cursor.execute(query, params)
                else:
                    cursor.execute(query)

                row_id = cursor.lastrowid
                affected = cursor.rowcount

                # Only auto-commit when we're NOT inside an explicit transaction
                if self._get_tx_conn() is None:
                    conn.commit()

                res = row_id if return_row_id else (affected if return_count else [])
                return {"success": True, "message": "SQLite Commit Query executed successfully", "result": res}

            except sqlite3.Error as e:
                module_logger.error("SQLite Commit Failure: %s | Query: %s | Params: %s", e, query, params)
                if self._get_tx_conn() is None:
                    conn.rollback()
                # If in a transaction, caller will decide to rollback()
                return {"success": False, "message": str(e), "result": []}
            finally:
                cursor.close()

    def execute_many(self, query: str, data: list, batch_size: int = 1000) -> dict:
        """
        Execute multiple INSERT/UPDATE/DELETE statements in batches.

        Auto-commit behavior mirrors `execute_commit`:
          - Inside an explicit transaction: no auto-commit per batch.
          - Outside a transaction: each batch is committed immediately.

        :param query: SQL string with '?' placeholders (or '%s' which will be rewritten).
        :param data: List of parameter tuples to be executed in batches.
        :param batch_size: Number of rows to process per batch commit.
        """
        if "%s" in query:
            query = query.replace("%s", "?")

        if not data:
            module_logger.warning("No data provided for batch execution.")
            return {"success": False, "message": "No data provided for batch execution.", "result": []}

        with self._conn_ctx() as conn:
            cursor = conn.cursor()
            try:
                total_rows = len(data)
                total_batches = (total_rows + batch_size - 1) // batch_size

                for batch_num in range(total_batches):
                    start_index = batch_num * batch_size
                    batch_data = data[start_index:start_index + batch_size]
                    cursor.executemany(query, batch_data)

                    # Only auto-commit each batch if there is no explicit transaction
                    if self._get_tx_conn() is None:
                        conn.commit()

                return {"success": True, "message": "SQLite Multi-Commit Query executed successfully", "result": []}
            except sqlite3.Error as e:
                module_logger.debug("SQLite Multi-Commit Failure: %s | Query: %s | Data size: %s",
                                    e, query, len(data))
                if self._get_tx_conn() is None:
                    conn.rollback()
                return {"success": False, "message": str(e), "result": []}
            finally:
                cursor.close()
