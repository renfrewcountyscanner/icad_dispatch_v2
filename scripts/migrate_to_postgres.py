#!/usr/bin/env python3
"""
One-time SQLite → PostgreSQL migration script.
Run inside the iCAD Docker container after PostgreSQL is up.

Usage:
    docker compose exec icad_dispatch python scripts/migrate_to_postgres.py

This script:
1. Connects to the existing SQLite database
2. Connects to PostgreSQL (via env vars)
3. Converts SQLite schema to PostgreSQL
4. Copies all data table by table
5. Verifies row counts
"""
import os
import sys
import sqlite3
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
import re
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger('migrate_to_postgres')

# Configuration
SQLITE_PATH = os.getenv('SQLITE_DATABASE_PATH', '/app/var/icad_dispatch.db')
PG_HOST = os.getenv('PG_HOST', 'postgres')
PG_PORT = int(os.getenv('PG_PORT', '5432'))
PG_DATABASE = os.getenv('PG_DATABASE', 'icad_dispatch')
PG_USER = os.getenv('PG_USER', 'icad')
PG_PASSWORD = os.getenv('PG_PASSWORD', '')

def get_sqlite_tables(conn):
    """Get list of user tables from SQLite."""
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    return [row[0] for row in cur.fetchall()]

def get_sqlite_columns(conn, table):
    """Get column info from SQLite."""
    cur = conn.execute(f"PRAGMA table_info({table})")
    columns = []
    for row in cur.fetchall():
        # row: (cid, name, type, notnull, dflt_value, pk)
        columns.append({
            'name': row[1],
            'type': row[2].upper(),
            'notnull': row[3],
            'default': row[4],
            'pk': row[5]
        })
    return columns

def sqlite_to_postgres_type(col_type, is_pk):
    """Convert SQLite column type to PostgreSQL."""
    col_type = col_type.upper()
    
    if is_pk and 'INT' in col_type:
        return 'SERIAL PRIMARY KEY'
    
    # INTEGER PRIMARY KEY → SERIAL (auto-increment)
    if 'INTEGER' in col_type or 'INT' in col_type:
        if is_pk:
            return 'SERIAL PRIMARY KEY'
        return 'INTEGER'
    
    if 'REAL' in col_type or 'FLOAT' in col_type or 'DOUBLE' in col_type or 'NUMERIC' in col_type:
        return 'NUMERIC'
    
    if 'TEXT' in col_type or 'VARCHAR' in col_type or 'CHAR' in col_type:
        return 'TEXT'
    
    if 'BLOB' in col_type:
        return 'BYTEA'
    
    if 'DATETIME' in col_type or 'TIMESTAMP' in col_type:
        return 'TIMESTAMP'
    
    return 'TEXT'

def create_postgres_table(pg_conn, table_name, columns, foreign_keys=None):
    """Create a PostgreSQL table from SQLite schema."""
    if foreign_keys is None:
        foreign_keys = []
    
    # Build column definitions
    col_defs = []
    for col in columns:
        pg_type = sqlite_to_postgres_type(col['type'], col['pk'])
        
        # Skip PRIMARY KEY in column def if it's SERIAL PRIMARY KEY
        if col['pk'] and 'SERIAL' in pg_type:
            col_def = f'"{col["name"]}" {pg_type}'
        else:
            col_def = f'"{col["name"]}" {pg_type}'
            if col['notnull']:
                col_def += ' NOT NULL'
            if col['default'] is not None:
                # Handle SQLite-specific defaults
                default = str(col['default'])
                if 'CURRENT_TIMESTAMP' in default.upper():
                    default = 'NOW()'
                elif 'strftime' in default and '%s' in default and 'now' in default.lower():
                    default = 'EXTRACT(EPOCH FROM NOW())::INTEGER'
                col_def += f' DEFAULT {default}'
        
        col_defs.append(col_def)
    
    # Add foreign keys
    for fk in foreign_keys:
        col_defs.append(f'FOREIGN KEY ("{fk['from']}") REFERENCES "{fk['table']}" ("{fk['to']}") ON DELETE CASCADE')
    
    # Handle UNIQUE constraints
    # (PRAGMA doesn't give us unique constraints easily, we'll skip for now and rely on data)
    
    create_sql = f'CREATE TABLE IF NOT EXISTS "{table_name}" (\n    ' + ',\n    '.join(col_defs) + '\n)'
    
    try:
        pg_cur = pg_conn.cursor()
        pg_cur.execute(create_sql)
        pg_conn.commit()
        pg_cur.close()
        logger.info("Created table: %s", table_name)
        return True
    except Exception as e:
        logger.error("Failed to create table %s: %s", table_name, e)
        return False

def get_sqlite_foreign_keys(conn, table):
    """Get foreign key info from SQLite."""
    cur = conn.execute(f"PRAGMA foreign_key_list({table})")
    fks = []
    for row in cur.fetchall():
        # row: (id, seq, table, from, to, on_update, on_delete, match)
        fks.append({
            'id': row[0],
            'table': row[2],
            'from': row[3],
            'to': row[4]
        })
    return fks

def migrate_table(sqlite_conn, pg_conn, table_name):
    """Copy data from SQLite table to PostgreSQL."""
    # Get column names
    columns = get_sqlite_columns(sqlite_conn, table_name)
    col_names = [f'"{c["name"]}"' for c in columns]
    
    # Get data from SQLite
    sqlite_cur = sqlite_conn.execute(f'SELECT * FROM "{table_name}"')
    rows = sqlite_cur.fetchall()
    
    if not rows:
        logger.info("Table %s: 0 rows (skipped)", table_name)
        return True
    
    # Build INSERT statement
    placeholders = ', '.join(['%s'] * len(columns))
    insert_sql = f'INSERT INTO "{table_name}" ({', '.join(col_names)}) VALUES ({placeholders})'
    
    # Batch insert in chunks of 1000
    chunk_size = 1000
    total_inserted = 0
    
    try:
        pg_cur = pg_conn.cursor()
        for i in range(0, len(rows), chunk_size):
            chunk = rows[i:i+chunk_size]
            pg_cur.executemany(insert_sql, chunk)
            pg_conn.commit()
            total_inserted += len(chunk)
            logger.info("Table %s: inserted %d/%d rows", table_name, total_inserted, len(rows))
        
        pg_cur.close()
        
        # Verify
        pg_cur = pg_conn.cursor()
        pg_cur.execute(f'SELECT COUNT(*) FROM "{table_name}"')
        pg_count = pg_cur.fetchone()[0]
        pg_cur.close()
        
        if pg_count == len(rows):
            logger.info("✓ Table %s: verified %d rows", table_name, pg_count)
            return True
        else:
            logger.warning("✗ Table %s: count mismatch (expected %d, got %d)", 
                          table_name, len(rows), pg_count)
            return False
    
    except Exception as e:
        logger.error("Failed to migrate table %s: %s", table_name, e)
        return False

def reset_sequences(pg_conn):
    """Reset SERIAL sequence counters to match max IDs."""
    pg_cur = pg_conn.cursor()
    
    # Find all tables with SERIAL columns
    pg_cur.execute("""
        SELECT c.relname AS table_name, a.attname AS column_name
        FROM pg_class c
        JOIN pg_attribute a ON a.attrelid = c.oid
        JOIN pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
        WHERE c.relkind = 'r'
        AND pg_get_expr(d.adbin, d.adrelid) LIKE 'nextval%'
    """)
    
    serial_cols = pg_cur.fetchall()
    
    for table_name, col_name in serial_cols:
        try:
            pg_cur.execute(f'SELECT MAX("{col_name}") FROM "{table_name}"')
            max_id = pg_cur.fetchone()[0] or 0
            
            seq_name = f'{table_name}_{col_name}_seq'
            pg_cur.execute(f"SELECT setval('{seq_name}', {max_id}, true)")
            pg_conn.commit()
            logger.info("Reset sequence %s to %d", seq_name, max_id)
        except Exception as e:
            logger.warning("Could not reset sequence for %s.%s: %s", table_name, col_name, e)
    
    pg_cur.close()

def main():
    logger.info("=" * 60)
    logger.info("SQLite to PostgreSQL Migration")
    logger.info("=" * 60)
    
    # Connect to SQLite
    logger.info("Connecting to SQLite: %s", SQLITE_PATH)
    if not os.path.exists(SQLITE_PATH):
        logger.error("SQLite database not found at %s", SQLITE_PATH)
        sys.exit(1)
    
    sqlite_conn = sqlite3.connect(SQLITE_PATH)
    sqlite_conn.row_factory = sqlite3.Row
    
    # Connect to PostgreSQL
    logger.info("Connecting to PostgreSQL: %s:%s/%s", PG_HOST, PG_PORT, PG_DATABASE)
    try:
        pg_conn = psycopg2.connect(
            host=PG_HOST, port=PG_PORT, dbname=PG_DATABASE,
            user=PG_USER, password=PG_PASSWORD
        )
        pg_conn.autocommit = True
    except Exception as e:
        logger.error("Failed to connect to PostgreSQL: %s", e)
        sys.exit(1)
    
    # Get tables
    tables = get_sqlite_tables(sqlite_conn)
    logger.info("Found %d tables in SQLite", len(tables))
    
    # Create tables in PostgreSQL
    logger.info("Creating tables in PostgreSQL...")
    for table in tables:
        columns = get_sqlite_columns(sqlite_conn, table)
        fks = get_sqlite_foreign_keys(sqlite_conn, table)
        create_postgres_table(pg_conn, table, columns, fks)
    
    # Migrate data
    logger.info("Migrating data...")
    success_count = 0
    for table in tables:
        if migrate_table(sqlite_conn, pg_conn, table):
            success_count += 1
    
    # Reset sequences
    logger.info("Resetting SERIAL sequences...")
    reset_sequences(pg_conn)
    
    # Summary
    logger.info("=" * 60)
    logger.info("Migration Summary:")
    logger.info("  Tables: %d/%d migrated successfully", success_count, len(tables))
    logger.info("=" * 60)
    
    # Close connections
    sqlite_conn.close()
    pg_conn.close()
    
    if success_count == len(tables):
        logger.info("✓ Migration complete!")
        sys.exit(0)
    else:
        logger.warning("⚠ Migration completed with some errors")
        sys.exit(1)

if __name__ == '__main__':
    main()