import sqlite3
import pandas as pd
import os
import glob
from datetime import datetime

def analyze_db_file(db_path):
    """Analyzes a single trades_N.db file and prints statistics."""
    print(f"--- Analyzing Archived Database: {os.path.basename(db_path)} ---")

    if not os.path.exists(db_path) or os.path.getsize(db_path) == 0:
        print("File does not exist or is empty.")
        print("--- End of Analysis for this file ---\n")
        return

    conn = None
    try:
        conn = sqlite3.connect(db_path)
        # Check if the 'trades' table exists
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='trades';")
        if not cursor.fetchone():
            print("Table 'trades' does not exist in this database.")
            print("--- End of Analysis for this file ---\n")
            return

        # Load data into pandas DataFrame
        df = pd.read_sql_query("SELECT * FROM trades", conn)

        if df.empty:
            print("No trades found in this database.")
            print("--- End of Analysis for this file ---\n")
            return

        total_trades = len(df)
        unique_tokens = df['mint'].nunique()

        # Convert received_timestamp to datetime if not already
        # Assuming timestamps are stored as strings or numbers that pandas can convert
        try:
            df['received_timestamp_dt'] = pd.to_datetime(df['received_timestamp'])
            time_range_start = df['received_timestamp_dt'].min()
            time_range_end = df['received_timestamp_dt'].max()
            duration = time_range_end - time_range_start
            time_range_str = f"{time_range_start} to {time_range_end} (Duration: {duration})"
        except Exception as e:
            time_range_str = f"Could not parse timestamps: {e}"


        buy_sell_counts = df['txType'].value_counts().to_dict()

        print(f"Total Trades Logged: {total_trades}")
        print(f"Unique Tokens with Trades: {unique_tokens}")
        print(f"Trade Time Range: {time_range_str}")
        print(f"Transaction Types: {buy_sell_counts}")
        
        # Optional: Add more stats like in verify_db.py if needed
        # For example, stats for solAmount or marketCapSol
        # if 'solAmount' in df.columns:
        #     print(f"SOL Amount Stats (sum): {df['solAmount'].sum()}")

    except sqlite3.Error as e:
        print(f"SQLite error: {e}")
    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        if conn:
            conn.close()
    print("--- End of Analysis for this file ---\n")

if __name__ == "__main__":
    script_dir = os.path.dirname(__file__)
    db_pattern = os.path.join(script_dir, "trades_*.db")
    
    archived_db_files = sorted(glob.glob(db_pattern))

    if not archived_db_files:
        print(f"No archived trade databases (trades_*.db) found in {script_dir}")
    else:
        print(f"Found {len(archived_db_files)} archived trade database(s) to analyze.")
        for db_file in archived_db_files:
            # Exclude the main 'trades.db' if it matches the pattern, focus on archives
            if os.path.basename(db_file) == "trades.db":
                continue
            analyze_db_file(db_file)
        print("All archived databases analyzed.") 