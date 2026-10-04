"""
Feature: check_current_stock_prices
Description: Retrieves the current stock price and historical data for a given stock symbol, then generates a dark-themed graph card showing price trends. Defaults to BMW.DE stock.
"""

FEATURE_METADATA = {
    "name": "check_current_stock_prices",
    "description": "Retrieves the current stock price and historical data for a given stock symbol, then generates a dark-themed graph card showing price trends. Defaults to BMW.DE stock.",
    "parameters": {"type": "OBJECT", "properties": {"symbol": {"type": "STRING", "description": "The stock ticker symbol (e.g., 'AAPL' for Apple, 'BMW.DE' for BMW on XETRA, 'TSLA' for Tesla). Defaults to 'BMW.DE'."}, "period": {"type": "STRING", "description": "The historical period for the graph (e.g., '1mo' for 1 month, '3mo' for 3 months, '1y' for 1 year). Defaults to '1mo'."}}, "required": []},
    "version": "1.0.0",
    "active": True
}

import urllib.request
import json
import os
import datetime
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

def execute(**kwargs):
    symbol = kwargs.get('symbol', 'BMW.DE')
    period = kwargs.get('period', '1mo')
    
    # Validate period to prevent arbitrary string injection into URL
    valid_periods = ['1d', '5d', '1mo', '3mo', '6mo', '1y', '2y', '5y', '10y', 'ytd', 'max']
    if period not in valid_periods:
        return {"error": f"Invalid period '{period}'. Please choose from: {', '.join(valid_periods)}"}

    # Yahoo Finance v8 API endpoint for historical data
    # We fetch historical data which also contains the latest price in the meta section
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={period}&interval=1d"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=8) as response:
            data = json.loads(response.read().decode())

        if not data or 'chart' not in data or not data['chart']['result']:
            return {"error": f"Could not retrieve data for symbol '{symbol}'. It might be invalid or unavailable."}

        chart_data = data['chart']['result'][0]
        meta = chart_data['meta']
        indicators = chart_data['indicators']['quote'][0]
        
        current_price = meta.get('regularMarketPrice')
        currency = meta.get('currency', 'USD')
        
        timestamps = chart_data['timestamp']
        closes = indicators['close']

        # Filter out None values from closes and corresponding timestamps
        valid_data = [(ts, close) for ts, close in zip(timestamps, closes) if close is not None]
        if not valid_data:
            return {"error": f"No valid historical closing price data found for '{symbol}' in the last {period}."}

        timestamps, closes = zip(*valid_data)
        dates = [datetime.datetime.fromtimestamp(ts) for ts in timestamps]

        # --- Plotting --- 
        # Configure matplotlib for headless mode and dark theme
        plt.style.use('dark_background')
        fig, ax = plt.subplots(figsize=(10, 6))

        # Set dark HUD theme colors
        fig.patch.set_facecolor('#0B0F19')
        ax.set_facecolor('#0B0F19')
        ax.tick_params(colors='#E0E0E0')
        ax.xaxis.label.set_color('#E0E0E0')
        ax.yaxis.label.set_color('#E0E0E0')
        ax.title.set_color('#E0E0E0')

        # Plot data with neon/cyberpunk accent color
        ax.plot(dates, closes, color='#00F0FF', linewidth=2, marker='o', markersize=3, markerfacecolor='#00F0FF', markeredgecolor='#00F0FF')

        # Customize grid
        ax.grid(True, linestyle='--', alpha=0.6, color='#1E293B')

        # Labels and Title
        ax.set_xlabel('Date', fontsize=12)
        ax.set_ylabel(f'Price ({currency})', fontsize=12)
        ax.set_title(f'{symbol} Stock Price Trend ({period})', fontsize=14, weight='bold')

        # Format x-axis dates
        fig.autofmt_xdate()

        # Create deliverables directory if it doesn't exist
        deliverables_dir = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'NIGHTFALLAI', 'deliverables')
        os.makedirs(deliverables_dir, exist_ok=True)

        # Save the plot to a file
        image_filename = f'{symbol.replace(".", "_")}_stock_price_trend_{period}.png'
        image_path = os.path.join(deliverables_dir, image_filename)
        plt.savefig(image_path, bbox_inches='tight', dpi=100, facecolor=fig.get_facecolor())
        plt.close(fig) # Close the figure to free memory

        summary_text = f"Current price for {symbol}: {current_price:.2f} {currency}. Historical trend over {period} shown in the graph."
        if current_price is None:
            summary_text = f"Could not retrieve current price for {symbol}. Historical trend over {period} shown in the graph."

        return {
            "image_path": image_path,
            "title": f"{symbol} Stock Price ({period})",
            "summary": summary_text
        }

    except urllib.error.URLError as e:
        return {"error": f"Network error while fetching data for {symbol}: {e.reason}. Please check your internet connection."}
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"error": f"Stock symbol '{symbol}' not found or data unavailable. Please check the symbol."}
        return {"error": f"HTTP error while fetching data for {symbol}: {e.code} - {e.reason}"}
    except json.JSONDecodeError:
        return {"error": f"Failed to decode JSON response for {symbol}. The API might have returned malformed data."}
    except Exception as e:
        return {"error": f"An unexpected error occurred: {str(e)}"}
