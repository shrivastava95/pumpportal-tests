import sqlite3
import os

# Define DB Path relative to this script's location
DB_PATH = os.path.join(os.path.dirname(__file__), 'trades.db')
TABLE_NAME = 'trades'

def verify_database():
    """Connects to the database and prints all rows from the trades table."""
    if not os.path.exists(DB_PATH):
        print(f"Error: Database file not found at {DB_PATH}")
        return

    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        print(f"--- Verifying Table: {TABLE_NAME} in {DB_PATH} ---")

        # Get column names
        cursor.execute(f"PRAGMA table_info({TABLE_NAME})")
        columns = [col[1] for col in cursor.fetchall()]
        if not columns:
            print(f"Error: Table '{TABLE_NAME}' seems to be empty or doesn't exist.")
            return
            
        print("Columns:", columns)
        print("-" * 80)

        # Fetch and print all rows
        cursor.execute(f"SELECT * FROM {TABLE_NAME}")
        rows = cursor.fetchall()

        if not rows:
            print("No rows found in the table.")
        else:
            print(f"Found {len(rows)} rows:")
            for i, row in enumerate(rows):
                # Print row tuple compactly
                print(f"Row {i+1}: {row}") 

    except sqlite3.Error as e:
        print(f"SQLite error during verification: {e}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
    finally:
        if conn:
            conn.close()
        print("--- Verification Complete ---")

if __name__ == "__main__":
    verify_database() 