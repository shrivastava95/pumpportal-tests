import sqlite3
import pandas as pd
import os
import argparse
from collections import defaultdict

# --- Configuration ---
# Assume DB is in the parent directory's 4_combinedMonitor folder
PARENT_DIR = os.path.dirname(os.path.dirname(__file__)) 
DB_PATH = os.path.join(PARENT_DIR, '4_combinedMonitor', 'trades.db')
TABLE_NAME = 'trades'

# Add cutoff timestamp for filtering (same as web app)
CUTOFF_TIMESTAMP_STR = '2025-04-22 23:00:00'

# --- Analysis Function ---
def analyze_trader_entry_exit(mint_address):
    """Analyzes trades for a specific mint to find apparent entry/exit per trader."""
    if not os.path.exists(DB_PATH):
        print(f"Error: Database file not found at {DB_PATH}")
        return

    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        
        # Fetch all necessary columns for the specific mint, filtering by timestamp
        query = f""" 
            SELECT traderPublicKey, txType, tokenAmount, received_timestamp 
            FROM {TABLE_NAME} 
            WHERE mint = ? AND received_timestamp >= ?
            ORDER BY traderPublicKey, received_timestamp ASC
        """
        df = pd.read_sql_query(query, conn, params=(mint_address, CUTOFF_TIMESTAMP_STR))

        if df.empty:
            print(f"No trade data found for mint {mint_address} after {CUTOFF_TIMESTAMP_STR}.")
            return

        print(f"--- Analyzing {len(df)} trades for Mint: {mint_address} (after {CUTOFF_TIMESTAMP_STR}) ---")

        # Convert timestamp
        df['datetime'] = pd.to_datetime(df['received_timestamp'])
        
        # Group by trader
        grouped = df.groupby('traderPublicKey')
        
        results = []

        # Analyze each trader
        for trader, trades in grouped:
            trades = trades.sort_values('datetime') # Ensure sorted by time
            
            running_balance = 0
            first_buy_time = None
            exit_time = None
            total_bought = 0
            total_sold = 0

            # Calculate running balance and find times
            for index, row in trades.iterrows():
                is_buy = (row['txType'] == 'buy')
                amount = row['tokenAmount'] if pd.notnull(row['tokenAmount']) else 0
                
                if is_buy:
                    if first_buy_time is None:
                        first_buy_time = row['datetime']
                    running_balance += amount
                    total_bought += amount
                else: # is_sell
                    running_balance -= amount
                    total_sold += amount
                
                # Check for exit *after* processing the sell
                if not is_buy and running_balance <= 1e-9 and exit_time is None: # Use tolerance for float comparison
                     # Only record the *first* time balance goes to zero or below after a sell
                     if amount > 0: # Make sure it was a non-zero sell that caused the exit
                         exit_time = row['datetime']

            # Store result for this trader if they bought anything
            if first_buy_time is not None:
                results.append({
                    'trader': trader,
                    'first_buy': first_buy_time,
                    'apparent_exit': exit_time, # Will be None if balance never reached <= 0 after a sell
                    'total_bought': total_bought,
                    'total_sold': total_sold,
                    'final_balance': running_balance
                })
        
        # --- Print Summary --- 
        if not results:
            print("No traders found who bought this token during the period.")
            return
            
        print(f"\nFound {len(results)} traders with buy activity:")
        print("-" * 80)
        print(f"{'-Trader Key-':<45} {'First Buy':<20} {'Apparent Exit':<20} {'Final Balance':>15}")
        print("-" * 80)
        
        # Sort results, e.g., by exit time (placing None last), then first buy
        results.sort(key=lambda x: (x['apparent_exit'] is not None, x['apparent_exit'], x['first_buy']))
        
        for res in results:
            first_buy_str = res['first_buy'].strftime('%Y-%m-%d %H:%M')
            exit_str = res['apparent_exit'].strftime('%Y-%m-%d %H:%M') if res['apparent_exit'] else '-'
            balance_str = f"{res['final_balance']:.2f}" # Format balance
            # Truncate trader key for display
            trader_short = res['trader'][:20] + '...' + res['trader'][-20:] 
            print(f"{trader_short:<45} {first_buy_str:<20} {exit_str:<20} {balance_str:>15}")

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

# --- Script Entry Point ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Analyze trader apparent entry and exit times for a specific token mint from trades.db.')
    parser.add_argument('mint_address', type=str, help='The token mint address to analyze.')
    
    args = parser.parse_args()
    
    # Basic validation
    if not args.mint_address or len(args.mint_address) < 30:
        print("Error: Please provide a valid mint address.")
    else:
        analyze_trader_entry_exit(args.mint_address) 