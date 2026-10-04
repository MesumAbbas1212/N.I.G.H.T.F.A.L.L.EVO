"""
Feature: market_analysis
Description: Analyzes the current Bitcoin price and displays a dark-mode graph card with recent price data. By default, it fetches BTCUSDT 1-hour klines for the last 24 hours.
"""

FEATURE_METADATA = {
    "name": "market_analysis",
    "description": "Analyzes the current Bitcoin price and displays a dark-mode graph card with recent price data. By default, it fetches BTCUSDT 1-hour klines for the last 24 hours.",
    "parameters": {"type": "OBJECT", "properties": {"symbol": {"type": "STRING", "description": "The trading pair symbol (e.g., 'BTCUSDT', 'ETHUSDT'). Defaults to 'BTCUSDT'."}, "interval": {"type": "STRING", "description": "The candlestick interval (e.g., '1m', '5m', '1h', '1d'). Defaults to '1h'."}, "limit": {"type": "INTEGER", "description": "The number of historical data points to fetch for the graph. Defaults to 24."}}, "required": []},
    "version": "1.0.0",
    "active": True
}

import urllib.request
import json
import os
import datetime
import matplotlib
matplotlib.use('Agg') # Use the 'Agg' backend for non-interactive plotting
import matplotlib.pyplot as plt

def execute(**kwargs):
    symbol = kwargs.get('symbol', 'BTCUSDT').upper()
    interval = kwargs.get('interval', '1h')
    limit = kwargs.get('limit', 24)

    # Ensure limit is an integer
    try:
        limit = int(limit)
    except ValueError:
        return {"error": "Invalid 'limit' parameter. Must be an integer."}

    # --- Fetch Live Price Data ---
    ticker_url = f"https://api.binance.com/api/v3/ticker/24hr?symbol={symbol}"
    current_price = "N/A"
    price_change_percent = "N/A"
    try:
        with urllib.request.urlopen(ticker_url, timeout=8) as response:
            ticker_data = json.loads(response.read().decode())
            current_price = float(ticker_data.get('lastPrice', 0))
            price_change_percent = float(ticker_data.get('priceChangePercent', 0))
    except urllib.error.URLError as e:
        print(f"Error fetching live ticker data for {symbol}: {e}")
    except json.JSONDecodeError as e:
        print(f"Error decoding live ticker JSON for {symbol}: {e}")
    except Exception as e:
        print(f"An unexpected error occurred fetching live ticker data: {e}")

    # --- Fetch Historical Klines Data for Graph ---
    klines_url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    times = []
    prices = []
    try:
        with urllib.request.urlopen(klines_url, timeout=8) as response:
            klines_data = json.loads(response.read().decode())
            for kline in klines_data:
                close_time_ms = kline[6]  # Close time in milliseconds
                close_price = float(kline[4]) # Close price
                times.append(datetime.datetime.fromtimestamp(close_time_ms / 1000))
                prices.append(close_price)
    except urllib.error.URLError as e:
        return {"error": f"Failed to fetch historical data for {symbol}: {e}"}
    except json.JSONDecodeError as e:
        return {"error": f"Failed to decode historical data JSON for {symbol}: {e}"}
    except Exception as e:
        return {"error": f"An unexpected error occurred fetching historical data: {e}"}

    if not times or not prices:
        return {"error": f"No historical data available for {symbol} with interval {interval} and limit {limit}."}

    # --- Generate Dark-Mode Graph Card ---
    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(10, 6))

    # Set dark HUD theme colors
    fig.patch.set_facecolor('#0B0F19')
    ax.set_facecolor('#0B0F19')

    # Plotting the data
    ax.plot(times, prices, color='#00F0FF', marker='o', markersize=4, linestyle='-', linewidth=2)

    # Title and labels
    ax.set_title(f'{symbol} Price Trend ({interval} Interval)', color='white', fontsize=16)
    ax.set_xlabel('Time', color='white', fontsize=12)
    ax.set_ylabel('Price (USD)', color='white', fontsize=12)

    # Grid
    ax.grid(True, linestyle='--', alpha=0.6, color='#1E293B')

    # Tick parameters
    ax.tick_params(axis='x', colors='white', rotation=45)
    ax.tick_params(axis='y', colors='white')

    # Spines
    for spine in ax.spines.values():
        spine.set_edgecolor('#1E293B')

    # Layout adjustment
    fig.tight_layout()

    # --- Save the plot to a file ---
    # Create the deliverables directory if it doesn't exist
    deliverables_dir = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'NIGHTFALLAI', 'deliverables')
    os.makedirs(deliverables_dir, exist_ok=True)

    image_path = os.path.join(deliverables_dir, f'{symbol}_price_trend_{interval}.png')
    plt.savefig(image_path)
    plt.close(fig) # Close the figure to free up memory

    # --- Prepare summary ---
    summary_text = f"Current {symbol} Price: ${current_price:,.2f}. "
    if price_change_percent != 'N/A':
        change_sign = '+' if price_change_percent >= 0 else ''
        summary_text += f"24hr Change: {change_sign}{price_change_percent:.2f}%. "
    summary_text += f"Displaying {len(prices)} {interval} data points."

    return {
        "image_path": image_path,
        "title": f"{symbol} Market Analysis",
        "summary": summary_text
    }
