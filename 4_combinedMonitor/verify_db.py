import sqlite3
import pandas as pd
import os

# Define DB Path relative to this script's location
DB_PATH = os.path.join(os.path.dirname(__file__), 'trades.db')
TABLE_NAME = 'trades'

def analyze_database():
    """Connects to the database, loads data into pandas, and prints analysis."""
    if not os.path.exists(DB_PATH):
        print(f"Error: Database file not found at {DB_PATH}")
        return

    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        
        print(f"--- Analyzing Table: {TABLE_NAME} in {DB_PATH} ---")

        # Load entire table into pandas DataFrame
        query = f"SELECT * FROM {TABLE_NAME}"
        df = pd.read_sql_query(query, conn)

        if df.empty:
            print("No data found in the table.")
            return

        # --- Basic Info & Format Check ---
        print(f"Total Samples (Trades Logged): {len(df)}")
        print("\nFirst 5 Samples (Rows):")
        print(df.head().to_string()) # Use to_string for better console formatting
        print("\nDataFrame Info (Columns, Types, Non-Null Counts):")
        df.info()

        # Convert timestamp column to datetime for analysis
        try:
            df['datetime'] = pd.to_datetime(df['received_timestamp'])
            df.sort_values('datetime', inplace=True)
            print("\nConverted 'received_timestamp' to datetime successfully.")
        except Exception as e:
            print(f"\nWarning: Could not convert 'received_timestamp' to datetime: {e}")
            # Proceed without time-based analysis if conversion fails

        # --- Statistics ---
        print("\n--- Statistics ---")
        
        # Time Range (if conversion worked)
        if 'datetime' in df.columns:
            min_time = df['datetime'].min()
            max_time = df['datetime'].max()
            print(f"Time Range: {min_time} to {max_time}")
            print(f"Duration: {max_time - min_time}")
        
        # Unique Tokens
        unique_tokens = df['mint'].nunique()
        print(f"\nUnique Tokens Traded: {unique_tokens}")
        
        # Tracked Token Count Stats
        print("\nStats for 'tracked_token_count_at_event':")
        print(df['tracked_token_count_at_event'].describe())
        
        # Transaction Type Counts
        print("\nTransaction Type Counts ('txType'):")
        print(df['txType'].value_counts())
        
        # SOL Amount Stats (handle potential None/NaN)
        print("\nStats for 'solAmount' (excluding potential nulls):")
        print(df['solAmount'].dropna().describe())
        
        # Market Cap Stats (handle potential None/NaN)
        print("\nStats for 'marketCapSol' (excluding potential nulls):")
        print(df['marketCapSol'].dropna().describe())

    except sqlite3.Error as e:
        print(f"SQLite error during analysis: {e}")
    except ImportError:
         print("Error: pandas library not found. Please install it: pip install pandas")
    except Exception as e:
        print(f"An unexpected error occurred during analysis: {e}")
    finally:
        if conn:
            conn.close()
        print("\n--- Analysis Complete ---")

if __name__ == "__main__":
    analyze_database() 