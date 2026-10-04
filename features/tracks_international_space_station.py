"""
Feature: tracks_international_space_station
Description: Fetches the International Space Station's live coordinates (latitude, longitude), altitude, and speed, then renders a dark-mode map showing its current position.
"""

FEATURE_METADATA = {
    "name": "tracks_international_space_station",
    "description": "Fetches the International Space Station's live coordinates (latitude, longitude), altitude, and speed, then renders a dark-mode map showing its current position.",
    "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    "version": "1.0.0",
    "active": True
}

import requests
import matplotlib
matplotlib.use('Agg') # Use 'Agg' backend for non-interactive plotting
import matplotlib.pyplot as plt
import os
import json

def execute(**kwargs):
    """
    Fetches the International Space Station's live coordinates (latitude, longitude),
    altitude, and speed, then renders a dark-mode map showing its current position.
    """
    # No specific parameters are required for this skill, so kwargs can be ignored.

    iss_api_url = "https://api.wheretheiss.at/v1/satellites/25544"
    timeout_seconds = 8

    try:
        # Fetch ISS data
        response = requests.get(iss_api_url, timeout=timeout_seconds)
        response.raise_for_status() # Raise an HTTPError for bad responses (4xx or 5xx)
        iss_data = response.json()

        latitude = iss_data.get('latitude')
        longitude = iss_data.get('longitude')
        altitude_km = iss_data.get('altitude') # in kilometers
        velocity_kmh = iss_data.get('velocity') # in kilometers per hour

        if any(x is None for x in [latitude, longitude, altitude_km, velocity_kmh]):
            raise ValueError("Missing one or more ISS data points from API response.")

        # --- Generate the dark-mode map ---
        plt.style.use('dark_background')
        fig, ax = plt.subplots(figsize=(10, 6))

        # Set dark HUD theme styling
        fig.patch.set_facecolor('#0B0F19')
        ax.set_facecolor('#0B0F19')

        # Plot the ISS position
        ax.scatter(longitude, latitude, color='#00F0FF', s=200, zorder=5, label='Current ISS Position', edgecolor='white', linewidth=1.5)
        ax.plot(longitude, latitude, 'o', color='#00F0FF', markersize=10, alpha=0.7) # A slightly larger, semi-transparent marker

        # Add a marker for the current position with an arrow or text
        ax.annotate('ISS', (longitude, latitude), textcoords="offset points", xytext=(10,10), ha='center', color='white', fontsize=12,
                    arrowprops=dict(arrowstyle="->", connectionstyle="arc3,rad=.2", color='white'))


        # Set map boundaries (global view)
        ax.set_xlim([-180, 180])
        ax.set_ylim([-90, 90])

        # Customize ticks and grid
        ax.set_xticks(range(-180, 181, 30))
        ax.set_yticks(range(-90, 91, 30))
        ax.grid(True, linestyle='--', alpha=0.6, color='#1E293B')
        ax.tick_params(axis='x', colors='white', labelsize=10)
        ax.tick_params(axis='y', colors='white', labelsize=10)

        # Add labels and title
        ax.set_xlabel("Longitude (°)", color='white')
        ax.set_ylabel("Latitude (°)", color='white')
        ax.set_title("International Space Station Live Position", color='white', fontsize=16)

        # Create output directory if it doesn't exist
        output_dir = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'NIGHTFALLAI', 'deliverables')
        os.makedirs(output_dir, exist_ok=True)
        image_path = os.path.join(output_dir, 'tracks_international_space_station_output.png')

        plt.tight_layout()
        plt.savefig(image_path, dpi=300, bbox_inches='tight', facecolor=fig.get_facecolor())
        plt.close(fig) # Close the figure to free up memory

        summary = (
            f"The International Space Station is currently at:\n"
            f"Latitude: {latitude:.4f}°\n"
            f"Longitude: {longitude:.4f}°\n"
            f"Altitude: {altitude_km:.2f} km\n"
            f"Speed: {velocity_kmh:.2f} km/h"
        )

        return {
            "image_path": image_path,
            "title": "International Space Station Live Tracking",
            "summary": summary
        }

    except requests.exceptions.RequestException as e:
        return {
            "error": f"Network or API error while fetching ISS data: {e}",
            "details": str(e)
        }
    except json.JSONDecodeError as e:
        return {
            "error": f"Failed to parse ISS data from API: {e}",
            "details": str(e)
        }
    except ValueError as e:
        return {
            "error": f"Data processing error: {e}",
            "details": str(e)
        }
    except Exception as e:
        return {
            "error": f"An unexpected error occurred: {e}",
            "details": str(e)
        }
