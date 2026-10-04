"""
Feature: flights_specific_area
Description: Real-time flight radar tracking. Detects and displays active aircraft, callsigns, speed, altitude, and flight paths in a specific area, district, city, or over the user's local region with a live visual radar map. Call whenever the user asks about flights, planes in the sky, airlines, or air traffic.
"""

FEATURE_METADATA = {
    "name": "flights_specific_area",
    "description": "Real-time flight radar tracking. Detects and displays active aircraft, callsigns, speed, altitude, and flight paths in a specific area, district, city, or over the user's local region with a live visual radar map. Call whenever the user asks about flights, planes in the sky, airlines, or air traffic.",
    "parameters": {"type": "OBJECT", "properties": {"area": {"type": "STRING", "description": "City, district, region, or area name (e.g. 'Kalyan', 'Mumbai', 'London', or 'my area'). Defaults to current device location."}}, "required": []},
    "version": "1.0.0",
    "active": True
}

import urllib.request
import urllib.parse
import json
import os
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

def _geocode_location(area_name: str):
    if not area_name or area_name.lower().strip() in ('my area', 'local', 'here', 'current', 'device', 'current location'):
        try:
            from core.device_location import get_device_location
            loc = get_device_location()
            if loc.get('latitude') and loc.get('longitude'):
                return float(loc['latitude']), float(loc['longitude']), loc.get('city') or 'Local Area'
        except Exception:
            pass
        return 19.23, 73.12, 'Kalyan'

    clean_area = area_name.lower().replace('district', '').replace('area', '').strip()
    if 'kalyan' in clean_area or 'dombiv' in clean_area:
        return 19.23, 73.12, 'Kalyan'
    elif 'mumbai' in clean_area or 'bombay' in clean_area:
        return 19.07, 72.87, 'Mumbai'
    elif 'delhi' in clean_area:
        return 28.61, 77.20, 'Delhi'
    elif 'bangalore' in clean_area or 'bengaluru' in clean_area:
        return 12.97, 77.59, 'Bengaluru'

    try:
        url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(clean_area)}&count=1"
        req = urllib.request.Request(url, headers={'User-Agent': 'NIGHTFALLAI-FlightRadar/1.0'})
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode('utf-8'))
            results = data.get('results', [])
            if results:
                lat = float(results[0]['latitude'])
                lon = float(results[0]['longitude'])
                name = results[0].get('name', clean_area.title())
                return lat, lon, name
    except Exception:
        pass

    return 19.23, 73.12, area_name.title()

def execute(**kwargs):
    area_input = kwargs.get('area') or kwargs.get('location') or kwargs.get('city') or 'local'
    center_lat, center_lon, area_name = _geocode_location(str(area_input))

    delta = 0.6
    min_latitude = round(center_lat - delta, 3)
    max_latitude = round(center_lat + delta, 3)
    min_longitude = round(center_lon - delta, 3)
    max_longitude = round(center_lon + delta, 3)

    url = (f"https://opensky-network.org/api/states/all?"
           f"lamin={min_latitude}&lomin={min_longitude}&"
           f"lamax={max_latitude}&lomax={max_longitude}")

    headers = {'User-Agent': 'NIGHTFALLAI-Skill/1.0'}
    flights = []
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=8) as response:
            data = json.loads(response.read().decode('utf-8'))

        if data and 'states' in data and data['states']:
            for s in data['states']:
                if s[6] is not None and s[5] is not None:
                    callsign = s[1].strip() if s[1] else 'UNKNOWN'
                    alt_m = round(s[7], 1) if s[7] is not None else None
                    alt_ft = round(s[7] * 3.28084) if s[7] is not None else None
                    vel_kmh = round(s[9] * 3.6) if s[9] is not None else None
                    flights.append({
                        'icao24': s[0],
                        'callsign': callsign,
                        'origin_country': s[2],
                        'latitude': s[6],
                        'longitude': s[5],
                        'altitude_ft': alt_ft,
                        'speed_kmh': vel_kmh,
                        'heading_deg': round(s[10], 1) if s[10] is not None else None,
                        'on_ground': bool(s[8])
                    })
    except Exception as e:
        return {'error': f'Failed to query OpenSky live flight data: {e}'}

    num_flights = len(flights)
    image_path = None

    try:
        plt.style.use('dark_background')
        fig, ax = plt.subplots(figsize=(8, 6))
        fig.set_facecolor('#0B0F19')
        ax.set_facecolor('#0B0F19')

        ax.plot(center_lon, center_lat, marker='*', color='#F59E0B', markersize=14, label=f'{area_name} Center')
        ax.text(center_lon, center_lat - 0.05, f' {area_name}', color='#F59E0B', fontsize=10, fontweight='bold', ha='center')

        if num_flights > 0:
            lats = [f['latitude'] for f in flights]
            lons = [f['longitude'] for f in flights]
            ax.scatter(lons, lats, color='#00F0FF', s=80, alpha=0.85, edgecolors='white', linewidth=1, label=f'Active Aircraft ({num_flights})')
            for f in flights[:8]:
                ax.text(f['longitude'], f['latitude'] + 0.03, f['callsign'], color='#10B981', fontsize=8, ha='center')

        ax.set_title(f'Live Air Radar: {area_name} ({num_flights} Aircraft Tracked)', color='white', fontsize=12, fontweight='bold')
        ax.set_xlabel('Longitude', color='#94A3B8')
        ax.set_ylabel('Latitude', color='#94A3B8')
        ax.grid(True, linestyle='--', alpha=0.3, color='#1E293B')
        ax.legend(facecolor='#0F172A', edgecolor='#334155', loc='upper right')

        deliverables_dir = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'NIGHTFALLAI', 'deliverables')
        os.makedirs(deliverables_dir, exist_ok=True)
        image_path = os.path.join(deliverables_dir, 'flights_radar_output.png')
        plt.savefig(image_path, bbox_inches='tight', dpi=140)
        plt.close(fig)
    except Exception:
        pass

    flight_names = ", ".join(f['callsign'] for f in flights[:5]) if flights else ""
    summary_text = (
        f"Tracked {num_flights} active aircraft over {area_name}. "
        + (f"Key flights: {flight_names}." if flight_names else "No airborne flights detected in immediate airspace right now.")
    )

    return {
        'title': f'Live Air Radar: {area_name}',
        'summary': summary_text,
        'flight_count': num_flights,
        'flights': flights[:10],
        'image_path': image_path
    }
