import sqlite3
import pandas as pd
import os
from flask import Flask, render_template, request, abort, redirect, url_for
import json
import plotly.graph_objects as go # Import Plotly

app = Flask(__name__)

# --- Configuration ---
# Assume DB is in the parent directory's 4_combinedMonitor folder
PARENT_DIR = os.path.dirname(os.path.dirname(__file__)) 
DB_PATH = os.path.join(PARENT_DIR, '4_combinedMonitor', 'trades.db')
TABLE_NAME = 'trades'

# Add cutoff timestamp for filtering
CUTOFF_TIMESTAMP_STR = '2025-04-22 23:00:00'
CUTOFF_TIMESTAMP = pd.Timestamp(CUTOFF_TIMESTAMP_STR)

# --- Helper Functions --- 

def get_sorted_mints():
    """Fetches a sorted list of unique mint addresses from the database
       that have trades on or after the CUTOFF_TIMESTAMP."""
    if not os.path.exists(DB_PATH):
        print(f"[Warning] get_sorted_mints: Database not found at {DB_PATH}")
        return []
    conn = None
    mints = []
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        # Query distinct mints that appear in rows with timestamp >= cutoff
        # AND had a marketCapSol > 100 at some point in that period.
        # Assuming received_timestamp is stored as text in a comparable format
        query = f"""
            SELECT DISTINCT mint 
            FROM {TABLE_NAME} 
            WHERE received_timestamp >= ? 
              AND marketCapSol > ?
            ORDER BY mint ASC
        """
        # Add 100 as the second parameter for the marketCapSol threshold
        cursor.execute(query, (CUTOFF_TIMESTAMP_STR, 100))
        mints = [row[0] for row in cursor.fetchall()]
        print(f"[Info] Found {len(mints)} unique tokens with trades on/after {CUTOFF_TIMESTAMP_STR} AND marketCapSol > 100")
    except sqlite3.Error as e:
        print(f"SQLite error fetching filtered mints: {e}")
    finally:
        if conn:
            conn.close()
    return mints

def get_token_data(mint_address):
    """Fetches, analyzes, and generates Plotly chart JSON for a token."""
    if not os.path.exists(DB_PATH):
        raise FileNotFoundError(f"Database not found at {DB_PATH}")

    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        
        # Fetch all data for the mint first
        query = f"SELECT * FROM {TABLE_NAME} WHERE mint = ? ORDER BY received_timestamp ASC"
        df = pd.read_sql_query(query, conn, params=(mint_address,))

        if df.empty:
            return None, None, None, None

        # Convert to datetime and filter
        df['datetime'] = pd.to_datetime(df['received_timestamp'])
        df_filtered = df[df['datetime'] >= CUTOFF_TIMESTAMP].copy()
        
        # Check if any data remains after filtering
        if df_filtered.empty:
            print(f"[Info] No trades found for {mint_address} on/after {CUTOFF_TIMESTAMP_STR}")
            return None, None, None, None # Add None for chart data
        
        # --- Aggregate data for Candlestick/Indicator Chart ---
        df_filtered.set_index('datetime', inplace=True)
        ohlc_agg = {
            'marketCapSol': ['first', 'max', 'min', 'last'] 
        }
        # Use 1min interval as requested before
        df_ohlc = df_filtered.resample('1min').agg(ohlc_agg)
        
        # Explicitly handle potentially multi-level columns if needed (adjust if agg output changes)
        if isinstance(df_ohlc.columns, pd.MultiIndex):
            df_ohlc.columns = ['_'.join(col).strip() for col in df_ohlc.columns.values]

        # Rename based on aggregation: first -> open_first, max -> high, min -> low, last -> close
        # Adjust these names if your pandas version/agg output differs
        rename_map = { 
            'marketCapSol_first': 'open_first', 
            'marketCapSol_max': 'high', 
            'marketCapSol_min': 'low', 
            'marketCapSol_last': 'close'
        }
        df_ohlc = df_ohlc.rename(columns=rename_map)
        df_ohlc.dropna(subset=['open_first', 'high', 'low', 'close'], inplace=True) # Drop rows where ANY original value was NaN

        if df_ohlc.empty: # Check again after dropna
             print(f"[Info] No valid OHLC data after initial aggregation for {mint_address}...")
             return None, None, None, None

        # Create the final 'open' column based on previous 'close'
        df_ohlc['open'] = df_ohlc['close'].shift(1)
        # Fill the NaN in the first row's 'open' with the original first trade value
        df_ohlc['open'].fillna(df_ohlc['open_first'], inplace=True)

        # Select final columns in desired order
        df_ohlc = df_ohlc[['open', 'high', 'low', 'close']]

        if df_ohlc.empty:
             print(f"[Info] No OHLC data generated for {mint_address} in 1min intervals after {CUTOFF_TIMESTAMP_STR}")
             return None, None, None, None # Need data for chart

        # --- Calculate Example Indicator (SMA) using Pandas ---
        sma_period = 5 # Example: 5-period SMA
        df_ohlc['sma'] = df_ohlc['close'].rolling(window=sma_period).mean()
        
        # --- Create Plotly Figure --- 
        fig = go.Figure()

        # Add Candlestick trace
        fig.add_trace(go.Candlestick(x=df_ohlc.index, # Use the datetime index
                                     open=df_ohlc['open'], 
                                     high=df_ohlc['high'],
                                     low=df_ohlc['low'], 
                                     close=df_ohlc['close'],
                                     name='Market Cap (SOL)'))

        # Add SMA trace
        fig.add_trace(go.Scatter(x=df_ohlc.index, 
                                 y=df_ohlc['sma'], 
                                 mode='lines', 
                                 name=f'SMA({sma_period})',
                                 line=dict(color='blue', width=1)))

        # Configure Layout
        fig.update_layout(
            title=f'Market Cap (SOL) - 1min OHLC for {mint_address}',
            yaxis_title='Market Cap (SOL)',
            xaxis_title='Time',
            xaxis_rangeslider_visible=False, # Disable the range slider
            margin=dict(l=50, r=50, b=50, t=70), # Adjust margins
            legend_title_text='Legend'
        )
        
        # --- Calculate Trader Entry/Exit & Prepare Markers --- 
        entry_markers = []
        exit_markers = []
        trader_activity_summary = [] # For the summary table below chart
        
        grouped_traders = df_filtered.groupby('traderPublicKey')

        for trader, trades in grouped_traders:
            trades_sorted = trades.sort_index()
            running_balance = 0
            first_buy_time = None
            exit_time = None
            entry_ohlc_low = None # Store the low of the entry candle
            exit_ohlc_high = None # Store the high of the exit candle

            for timestamp, row in trades_sorted.iterrows():
                is_buy = (row['txType'] == 'buy')
                amount = row['tokenAmount'] if pd.notnull(row['tokenAmount']) else 0
                mc_sol = row['marketCapSol'] # Get market cap for this specific trade

                if is_buy:
                    if first_buy_time is None:
                        first_buy_time = timestamp 
                        # Prepare entry marker data - use the trade's market cap for Y
                        if mc_sol is not None: # Only add marker if market cap exists
                            entry_markers.append({
                                'x': timestamp, 
                                'y': mc_sol, 
                                'text': f'Entry: {trader[:6]}... @ {mc_sol:.2f} MC'
                            })
                    running_balance += amount
                else: 
                    running_balance -= amount
                
                if not is_buy and running_balance <= 1e-9 and exit_time is None:
                     if amount > 0: 
                         exit_time = timestamp
                         # Prepare exit marker data - use the trade's market cap for Y
                         if mc_sol is not None: # Only add marker if market cap exists
                            exit_markers.append({
                                'x': timestamp,
                                'y': mc_sol,
                                'text': f'Exit: {trader[:6]}... @ {mc_sol:.2f} MC'
                            })

            # Add to summary table results only if they actually bought something
            if first_buy_time is not None:
                trader_activity_summary.append({
                    'trader': trader,
                    'first_buy': first_buy_time.strftime('%Y-%m-%d %H:%M:%S'),
                    'apparent_exit': exit_time.strftime('%Y-%m-%d %H:%M:%S') if exit_time else '-',
                    'final_balance': f"{running_balance:.2f}"
                })
        
        # Sort summary table data
        trader_activity_summary.sort(key=lambda x: (x['apparent_exit'] == '-', x['apparent_exit'], x['first_buy']))
        
        # --- Add Marker Traces to Plotly Figure --- 
        if entry_markers:
            fig.add_trace(go.Scatter(
                x=[m['x'] for m in entry_markers],
                y=[m['y'] for m in entry_markers],
                mode='markers',
                marker=dict(symbol='triangle-up', size=8, color='green'),
                name='Trader Entry',
                text=[m['text'] for m in entry_markers],
                hoverinfo='x+text' # Show time and custom text on hover
            ))

        if exit_markers:
             fig.add_trace(go.Scatter(
                 x=[m['x'] for m in exit_markers],
                 y=[m['y'] for m in exit_markers],
                 mode='markers',
                 marker=dict(symbol='triangle-down', size=8, color='red'),
                 name='Trader Exit',
                 text=[m['text'] for m in exit_markers],
                 hoverinfo='x+text'
             ))

        # Convert figure (now including markers) to JSON
        chart_json = fig.to_json()
        
        # --- Calculate Summary Statistics (using original filtered data before resampling) ---
        summary = {
            'total_trades': len(df_filtered),
            'buy_trades': len(df_filtered[df_filtered['txType'] == 'buy']),
            'sell_trades': len(df_filtered[df_filtered['txType'] == 'sell']),
            # Use index.min/max before dropping index
            'first_trade': df_filtered.index.min().strftime('%Y-%m-%d %H:%M:%S'), 
            'last_trade': df_filtered.index.max().strftime('%Y-%m-%d %H:%M:%S'), 
            'avg_sol_amount': df_filtered['solAmount'].mean() if not df_filtered['solAmount'].isnull().all() else 0,
            'total_sol_buy': df_filtered.loc[df_filtered['txType'] == 'buy', 'solAmount'].sum(),
            'total_sol_sell': df_filtered.loc[df_filtered['txType'] == 'sell', 'solAmount'].sum(),
        }

        # --- Prepare data for HTML Table (using original filtered data) ---
        # Select and format columns for HTML table display from filtered data
        # Reset index if needed or select based on original df_filtered
        display_df = df_filtered.reset_index()[[ 'datetime', 'txType', 'solAmount', 'tokenAmount', 'marketCapSol', 'traderPublicKey', 'signature' ]].copy()
        display_df['datetime'] = display_df['datetime'].dt.strftime('%Y-%m-%d %H:%M:%S') # Format datetime
        
        for col in ['solAmount', 'tokenAmount', 'marketCapSol']:
             if col in display_df.columns:
                 display_df[col] = display_df[col].apply(lambda x: f'{x:.6f}' if pd.notnull(x) else 'N/A')

        trades_html = display_df.to_html(index=False, classes='table table-striped', border=0)

        # Return all calculated data
        return summary, trades_html, chart_json, trader_activity_summary

    finally:
        if conn:
            conn.close()

# --- Flask Routes ---
@app.route('/')
def home():
    """Redirects to the analysis page for the first token."""
    # Redirect to the first token's page
    return redirect(url_for('show_token', token_index=0))

@app.route('/token/<int:token_index>')
def show_token(token_index):
    """Displays analysis for the token at the given index."""
    sorted_mints = get_sorted_mints()
    total_tokens = len(sorted_mints)
    summary = None
    trades_html = None
    chart_json = None
    trader_activity = None
    error = None
    current_mint = None
    prev_index = None
    next_index = None

    if not sorted_mints:
        error = "No tokens found in the database."
    elif token_index < 0 or token_index >= total_tokens:
        # Redirect to the first token if index is out of bounds
        # Alternatively, show an error page (e.g., return abort(404))
        return redirect(url_for('show_token', token_index=0))
    else:
        current_mint = sorted_mints[token_index]
        try:
            summary, trades_html, chart_json, trader_activity = get_token_data(current_mint)
            if summary is None:
                # Should not happen if mint came from get_sorted_mints, but check anyway
                error = f"Could not retrieve or generate data for token {current_mint} during the specified period."
        except FileNotFoundError as e:
            error = str(e)
            app.logger.error(f"Database file error: {e}") 
        except Exception as e:
            error = f"An unexpected error occurred analyzing {current_mint}: {e}"
            app.logger.error(f"Error analyzing token {current_mint}: {e}")
        
        # Calculate previous and next indices for navigation
        if token_index > 0:
            prev_index = token_index - 1
        if token_index < total_tokens - 1:
            next_index = token_index + 1
            
    return render_template('index.html', 
                           current_mint=current_mint,
                           token_index=token_index,
                           total_tokens=total_tokens,
                           prev_index=prev_index,
                           next_index=next_index,
                           summary=summary, 
                           trades_html=trades_html, 
                           chart_json=chart_json,
                           trader_activity=trader_activity,
                           error=error)

# --- Run App ---
if __name__ == '__main__':
    # Check if DB exists on startup
    if not os.path.exists(DB_PATH):
        print(f"WARNING: Database file not found at {DB_PATH}. The app will run but show errors until the DB is present.")
    # Disable reloader to prevent restarts on code change during dev
    app.run(host='0.0.0.0', port=5003, debug=True, use_reloader=False) 