import sqlite3
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import os

# --- Configuration ---
DB_PATH = os.path.join(os.path.dirname(__file__), 'trades.db')
TABLE_NAME = 'trades'

# --- Main Plotting Function ---
def plot_trade_data():
    """Reads data from trades.db and generates the requested plots."""
    if not os.path.exists(DB_PATH):
        print(f"Error: Database file not found at {DB_PATH}")
        return

    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        
        # Use pandas to read SQL query directly into a DataFrame
        # Select necessary columns: received_timestamp for time, and tracked_token_count_at_event
        query = f"SELECT received_timestamp, tracked_token_count_at_event FROM {TABLE_NAME}"
        df = pd.read_sql_query(query, conn)

        if df.empty:
            print("No data found in the trades table.")
            return

        # --- Data Preparation ---
        # Convert received_timestamp (likely stored as text by SQLite CURRENT_TIMESTAMP)
        # into datetime objects. If it were a Unix timestamp (REAL), we'd use unit='s'.
        df['datetime'] = pd.to_datetime(df['received_timestamp'])
        
        # Set datetime as the index for time series analysis
        df.set_index('datetime', inplace=True)
        
        # Sort by time just in case data isn't perfectly ordered
        df.sort_index(inplace=True)

        # --- Filter for Second Run ---
        # Based on plot analysis, the second run started around 2025-04-22 20:00
        # Let's filter for data from 19:00 onwards on that day
        # cutoff_timestamp = pd.Timestamp('2025-04-22 19:00:00') # Original second run cutoff
        # Further analysis shows the main bulk starts just before 2025-04-23 00:00
        cutoff_timestamp = pd.Timestamp('2025-04-22 23:00:00') # New cutoff for main bulk
        original_count = len(df)
        df_filtered = df[df.index >= cutoff_timestamp].copy()
        filtered_count = len(df_filtered)
        # print(f"\nFiltering data for second run (on/after {cutoff_timestamp})...")
        print(f"\nFiltering data for main active period (on/after {cutoff_timestamp})...") # Updated print
        print(f"Original trade count: {original_count}")
        print(f"Filtered trade count (main period): {filtered_count}") # Updated print
        
        if df_filtered.empty:
            print("No data found after the cutoff timestamp for the main active period.")
            return

        # --- Calculations for Plots (using filtered data) ---
        # 1. Trade counts per 1 minute
        trades_per_1min = df_filtered.resample('1min').size()

        # 2. Trade counts per 5 minutes
        trades_per_5min = df_filtered.resample('5min').size()
        
        # 3. Tracked token count over time (use the filtered data)
        token_count_over_time = df_filtered['tracked_token_count_at_event']

        # --- Plotting (using filtered data) ---
        fig, axes = plt.subplots(3, 1, figsize=(12, 15), sharex=True)
        # Update title to reflect filtering
        # fig.suptitle('PumpPortal Trade Analysis (Second Run Only)', fontsize=16)
        fig.suptitle('PumpPortal Trade Analysis (Main Active Period Only)', fontsize=16) # Updated title

        # Plot 1: Trades per 1 minute
        axes[0].plot(trades_per_1min.index, trades_per_1min.values, marker='.', linestyle='-', label='Trades/min')
        axes[0].set_title('Trade Frequency (1-Minute Intervals)')
        axes[0].set_ylabel('Number of Trades')
        axes[0].grid(True, which='major', linestyle='--', linewidth='0.5')
        axes[0].legend()

        # Plot 2: Trades per 5 minutes
        axes[1].plot(trades_per_5min.index, trades_per_5min.values, marker='.', linestyle='-', color='orange', label='Trades/5min')
        axes[1].set_title('Trade Frequency (5-Minute Intervals)')
        axes[1].set_ylabel('Number of Trades')
        axes[1].grid(True, which='major', linestyle='--', linewidth='0.5')
        axes[1].legend()

        # Plot 3: Tracked Token Count Over Time
        # Use steps-post to show when the count changes
        axes[2].plot(token_count_over_time.index, token_count_over_time.values, drawstyle='steps-post', color='green', label='Tracked Tokens')
        axes[2].set_title('Number of Tokens Tracked Over Time')
        axes[2].set_ylabel('Unique Token Count')
        axes[2].set_xlabel('Time')
        axes[2].grid(True, which='major', linestyle='--', linewidth='0.5')
        axes[2].legend()
        
        # Improve date formatting on x-axis
        fig.autofmt_xdate() # Auto-rotate date labels
        for ax in axes: # Apply date formatter to all shared axes
             ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d %H:%M:%S'))

        plt.tight_layout(rect=[0, 0.03, 1, 0.97]) # Adjust layout to prevent title overlap
        print("Displaying plots... Close the plot window to exit the script.")
        plt.show()

    except sqlite3.Error as e:
        print(f"SQLite error during plotting: {e}")
    except Exception as e:
        print(f"An unexpected error occurred during plotting: {e}")
    finally:
        if conn:
            conn.close()

# --- Script Entry Point ---
if __name__ == "__main__":
    plot_trade_data() 