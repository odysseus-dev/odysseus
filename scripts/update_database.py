"""
update_database.py

This script updates the database schema by adding new columns to the sessions table
and populating them with appropriate values. It handles SQLite's limitations
with ALTER TABLE operations by checking if columns exist before attempting to add them.

The following columns are added:
- last_accessed (DateTime): Set to created_at for existing records
- is_important (Boolean): Set to False for existing records
- message_count (Integer): Calculated from the number of messages in chat_messages table

Usage:
    python update_database.py
"""

import sqlite3
import os
from datetime import datetime
from sqlalchemy import create_engine, inspect, text
from database import DATABASE_URL, SessionLocal, Base

def check_column_exists(engine, table_name, column_name):
    """Check if a column exists in a table."""
    inspector = inspect(engine)
    columns = inspector.get_columns(table_name)
    return any(col['name'] == column_name for col in columns)

def add_column_sqlite(db_path, table_name, column_name, column_type, default_value=None):
    """
    Add a column to a SQLite table by creating a new table, copying data, and renaming.
    This is necessary because SQLite has limited ALTER TABLE support.
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Get current table info
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = cursor.fetchall()
    column_names = [col[1] for col in columns]
    
    # Create new table with additional column
    new_table_name = f"{table_name}_new"
    
    # Build new column list
    new_columns = []
    for col in columns:
        new_columns.append(f"{col[1]} {col[2]}")
    
    # Add the new column
    new_column_def = f"{column_name} {column_type}"
    if default_value is not None:
        new_column_def += f" DEFAULT {default_value}"
    new_columns.append(new_column_def)
    
    # Create new table
    columns_sql = ", ".join(new_columns)
    create_sql = f"CREATE TABLE {new_table_name} ({columns_sql})"
    cursor.execute(create_sql)
    
    # Copy data from old table to new table
    column_names_str = ", ".join(column_names)
    insert_sql = f"INSERT INTO {new_table_name} ({column_names_str}) SELECT {column_names_str} FROM {table_name}"
    cursor.execute(insert_sql)
    
    # Drop old table and rename new table
    cursor.execute(f"DROP TABLE {table_name}")
    cursor.execute(f"ALTER TABLE {new_table_name} RENAME TO {table_name}")
    
    conn.commit()
    conn.close()

def update_database():
    """Update the database schema and populate new columns."""
    # Create engine from DATABASE_URL
    engine = create_engine(DATABASE_URL)
    
    # Extract database path from DATABASE_URL for SQLite
    db_path = None
    if "sqlite" in DATABASE_URL:
        db_path = DATABASE_URL.replace("sqlite:///", "")
        # Handle relative paths
        if not os.path.isabs(db_path):
            db_path = os.path.join(os.path.dirname(__file__), db_path)
    
    print(f"Updating database at: {DATABASE_URL}")
    
    # Start a transaction
    db = SessionLocal()
    try:
        # Add last_accessed column if it doesn't exist
        if not check_column_exists(engine, 'sessions', 'last_accessed'):
            print("Adding last_accessed column...")
            if db_path:  # SQLite
                add_column_sqlite(db_path, 'sessions', 'last_accessed', 'DATETIME')
            else:  # Other databases
                with engine.connect() as conn:
                    conn.execute(text("ALTER TABLE sessions ADD COLUMN last_accessed DATETIME"))
                    conn.commit()
        
        # Add is_important column if it doesn't exist
        if not check_column_exists(engine, 'sessions', 'is_important'):
            print("Adding is_important column...")
            if db_path:  # SQLite
                add_column_sqlite(db_path, 'sessions', 'is_important', 'BOOLEAN', '0')
            else:  # Other databases
                with engine.connect() as conn:
                    conn.execute(text("ALTER TABLE sessions ADD COLUMN is_important BOOLEAN DEFAULT FALSE"))
                    conn.commit()
        
        # Add message_count column if it doesn't exist
        if not check_column_exists(engine, 'sessions', 'message_count'):
            print("Adding message_count column...")
            if db_path:  # SQLite
                add_column_sqlite(db_path, 'sessions', 'message_count', 'INTEGER', '0')
            else:  # Other databases
                with engine.connect() as conn:
                    conn.execute(text("ALTER TABLE sessions ADD COLUMN message_count INTEGER DEFAULT 0"))
                    conn.commit()
        
        # Populate last_accessed with created_at for existing records where last_accessed is NULL
        print("Populating last_accessed column...")
        with engine.connect() as conn:
            conn.execute(text("""
                UPDATE sessions 
                SET last_accessed = created_at 
                WHERE last_accessed IS NULL
            """))
            conn.commit()
        
        # Populate is_important with FALSE for existing records where is_important is NULL
        print("Populating is_important column...")
        with engine.connect() as conn:
            conn.execute(text("""
                UPDATE sessions 
                SET is_important = 0 
                WHERE is_important IS NULL
            """))
            conn.commit()
        
        # Calculate and populate message_count from chat_messages table
        print("Calculating and populating message_count column...")
        with engine.connect() as conn:
            # First, set all message_count to 0
            conn.execute(text("UPDATE sessions SET message_count = 0"))
            
            # Then, count messages for each session and update
            conn.execute(text("""
                UPDATE sessions 
                SET message_count = (
                    SELECT COUNT(*) 
                    FROM chat_messages 
                    WHERE chat_messages.session_id = sessions.id
                )
            """))
            conn.commit()
        
        print("Database update completed successfully!")
        
    except Exception as e:
        print(f"Error updating database: {e}")
        db.rollback()
        raise
    finally:
        db.close()

if __name__ == "__main__":
    update_database()
