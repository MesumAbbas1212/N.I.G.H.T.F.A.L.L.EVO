"""
NIGHTFALL Geospatial Globe Engine.
Provides real-time 3D Earth tracking, great-circle distance/route calculation,
live flight radar integration, and geographic inspection.
"""

import math
import urllib.request
import urllib.parse
import json
from typing import Dict, Any, Tuple, Optional, List

# Quick-lookup coordinates for major world cities and airports to provide instant, offline response
KNOWN_LOCATIONS: Dict[str, Tuple[float, float, str]] = {
    # Major Countries & Continents
    "america": (38.8951, -77.0364, "United States (Washington D.C.)"),
    "united states": (38.8951, -77.0364, "United States (Washington D.C.)"),
    "usa": (38.8951, -77.0364, "United States (Washington D.C.)"),
    "us": (38.8951, -77.0364, "United States (Washington D.C.)"),
    "united states of america": (38.8951, -77.0364, "United States (Washington D.C.)"),
    "uk": (51.5074, -0.1278, "United Kingdom (London)"),
    "united kingdom": (51.5074, -0.1278, "United Kingdom (London)"),
    "britain": (51.5074, -0.1278, "United Kingdom (London)"),
    "great britain": (51.5074, -0.1278, "United Kingdom (London)"),
    "england": (51.5074, -0.1278, "England, UK (London)"),
    "india": (20.5937, 78.9629, "India (New Delhi)"),
    "bharat": (20.5937, 78.9629, "India (New Delhi)"),
    "russia": (55.7558, 37.6173, "Russia (Moscow)"),
    "china": (39.9042, 116.4074, "China (Beijing)"),
    "japan": (35.6762, 139.6503, "Japan (Tokyo)"),
    "germany": (52.5200, 13.4050, "Germany (Berlin)"),
    "france": (48.8566, 2.3522, "France (Paris)"),
    "australia": (-25.2744, 133.7751, "Australia (Canberra/Sydney)"),
    "canada": (45.4215, -75.6972, "Canada (Ottawa)"),
    "brazil": (-14.2350, -51.9253, "Brazil (Brasília)"),
    "italy": (41.9028, 12.4964, "Italy (Rome)"),
    "spain": (40.4168, -3.7038, "Spain (Madrid)"),
    "uae": (25.2048, 55.2708, "United Arab Emirates (Dubai)"),
    "united arab emirates": (25.2048, 55.2708, "United Arab Emirates (Dubai)"),
    "saudi arabia": (24.7136, 46.6753, "Saudi Arabia (Riyadh)"),
    "south africa": (-30.5595, 22.9375, "South Africa (Cape Town)"),
    "egypt": (30.0444, 31.2357, "Egypt (Cairo)"),
    "south korea": (37.5665, 126.9780, "South Korea (Seoul)"),
    "korea": (37.5665, 126.9780, "South Korea (Seoul)"),
    "singapore": (1.3521, 103.8198, "Singapore"),
    "switzerland": (46.8182, 8.2275, "Switzerland (Bern/Zurich)"),
    "netherlands": (52.3676, 4.9041, "Netherlands (Amsterdam)"),
    "turkey": (39.9334, 32.8597, "Turkey (Ankara)"),
    "mexico": (19.4326, -99.1332, "Mexico (Mexico City)"),
    "argentina": (-34.6037, -58.3816, "Argentina (Buenos Aires)"),
    "indonesia": (-6.2088, 106.8456, "Indonesia (Jakarta)"),
    "thailand": (13.7563, 100.5018, "Thailand (Bangkok)"),
    "malaysia": (3.1390, 101.6869, "Malaysia (Kuala Lumpur)"),
    "pakistan": (33.6844, 73.0479, "Pakistan (Islamabad)"),
    "bangladesh": (23.8103, 90.4125, "Bangladesh (Dhaka)"),
    "nepal": (27.7172, 85.3240, "Nepal (Kathmandu)"),
    "sri lanka": (6.9271, 79.8612, "Sri Lanka (Colombo)"),

    # India Cities & Hubs
    "mumbai": (19.0760, 72.8777, "Mumbai, India (BOM)"),
    "bombay": (19.0760, 72.8777, "Mumbai, India (BOM)"),
    "delhi": (28.6139, 77.2090, "New Delhi, India (DEL)"),
    "new delhi": (28.6139, 77.2090, "New Delhi, India (DEL)"),
    "kalyan": (19.2403, 73.1305, "Kalyan, Maharashtra, India"),
    "bengaluru": (12.9716, 77.5946, "Bengaluru, India (BLR)"),
    "bangalore": (12.9716, 77.5946, "Bengaluru, India (BLR)"),
    "hyderabad": (17.3850, 78.4867, "Hyderabad, India (HYD)"),
    "chennai": (13.0827, 80.2707, "Chennai, India (MAA)"),
    "kolkata": (22.5726, 88.3639, "Kolkata, India (CCU)"),
    "pune": (18.5204, 73.8567, "Pune, India (PNQ)"),
    "ahmedabad": (23.0225, 72.5714, "Ahmedabad, India (AMD)"),
    "jaipur": (26.9124, 75.7873, "Jaipur, India (JAI)"),
    "goa": (15.2993, 74.1240, "Goa, India (GOI)"),
    "kochi": (9.9312, 76.2673, "Kochi, India (COK)"),

    # Major Global Metro Cities
    "london": (51.5074, -0.1278, "London, United Kingdom (LHR)"),
    "new york": (40.7128, -74.0060, "New York, USA (JFK)"),
    "nyc": (40.7128, -74.0060, "New York, USA (JFK)"),
    "san francisco": (37.7749, -122.4194, "San Francisco, USA (SFO)"),
    "los angeles": (34.0522, -118.2437, "Los Angeles, USA (LAX)"),
    "washington": (38.8951, -77.0364, "Washington D.C., USA"),
    "chicago": (41.8781, -87.6298, "Chicago, USA (ORD)"),
    "miami": (25.7617, -80.1918, "Miami, USA (MIA)"),
    "tokyo": (35.6762, 139.6503, "Tokyo, Japan (HND/NRT)"),
    "dubai": (25.2048, 55.2708, "Dubai, UAE (DXB)"),
    "paris": (48.8566, 2.3522, "Paris, France (CDG)"),
    "sydney": (-33.8688, 151.2093, "Sydney, Australia (SYD)"),
    "melbourne": (-37.8136, 144.9631, "Melbourne, Australia (MEL)"),
    "toronto": (43.6532, -79.3832, "Toronto, Canada (YYZ)"),
    "berlin": (52.5200, 13.4050, "Berlin, Germany (BER)"),
    "frankfurt": (50.1109, 8.6821, "Frankfurt, Germany (FRA)"),
    "amsterdam": (52.3676, 4.9041, "Amsterdam, Netherlands (AMS)"),
    "rome": (41.9028, 12.4964, "Rome, Italy (FCO)"),
    "bangkok": (13.7563, 100.5018, "Bangkok, Thailand (BKK)"),
    "hong kong": (22.3193, 114.1694, "Hong Kong (HKG)"),
    "doha": (25.2854, 51.5310, "Doha, Qatar (DOH)"),
    "seoul": (37.5665, 126.9780, "Seoul, South Korea (ICN)"),
    "moscow": (55.7558, 37.6173, "Moscow, Russia (SVO)"),
    "cairo": (30.0444, 31.2357, "Cairo, Egypt (CAI)"),
    "cape town": (-33.9249, 18.4241, "Cape Town, South Africa (CPT)"),
    "rio": (-22.9068, -43.1729, "Rio de Janeiro, Brazil (GIG)"),
    "buenos aires": (-34.6037, -58.3816, "Buenos Aires, Argentina (EZE)"),
}


def geocode_location(location_name: str) -> Tuple[float, float, str]:
    """Resolve a city, country, airport, or area name to (lat, lon, label)."""
    norm = location_name.strip().lower()
    
    # Check device location aliases
    if norm in ("current", "current location", "here", "my location", "my area", "device"):
        try:
            from core.device_location import get_device_location
            loc = get_device_location()
            lat = float(loc.get("latitude") or 19.24)
            lon = float(loc.get("longitude") or 73.13)
            city = loc.get("city") or "Current Location"
            return lat, lon, f"{city} (Device Location)"
        except Exception:
            return 19.2403, 73.1305, "Kalyan (Local Area)"

    # 1. Exact match in curated database
    if norm in KNOWN_LOCATIONS:
        return KNOWN_LOCATIONS[norm]

    # 2. Match whole words in curated database
    import re
    for key, val in KNOWN_LOCATIONS.items():
        if len(key) >= 3 and re.search(rf"\b{re.escape(key)}\b", norm):
            return val

    # 3. Online Open-Meteo Geocoding API fallback
    try:
        url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(location_name)}&count=1"
        req = urllib.request.Request(url, headers={"User-Agent": "NIGHTFALLAI-HoloGlobe/1.0"})
        with urllib.request.urlopen(req, timeout=4) as response:
            payload = json.loads(response.read().decode("utf-8"))
            results = payload.get("results") or []
            if results:
                top = results[0]
                lat = float(top["latitude"])
                lon = float(top["longitude"])
                name = str(top.get("name") or location_name.title())
                country = str(top.get("country") or "")
                # Encode safely without crashing Windows terminal
                label = f"{name}, {country}".strip(", ")
                return lat, lon, label
    except Exception:
        pass

    # 4. Fallback: if user asked about a specific name, use title
    clean_title = location_name.title()
    return 20.5937, 78.9629, clean_title


def calculate_great_circle_route(lat1: float, lon1: float, lat2: float, lon2: float) -> Dict[str, Any]:
    """
    Calculate the Great Circle spherical distance, estimated flight time, initial bearing,
    and intermediate arc waypoints for 3D visualization.
    """
    R_KM = 6371.0
    R_MILES = 3958.8

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    # Haversine formula
    a = (math.sin(delta_phi / 2.0) ** 2 +
         math.cos(phi1) * math.cos(phi2) * (math.sin(delta_lambda / 2.0) ** 2))
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))

    distance_km = round(R_KM * c, 1)
    distance_miles = round(R_MILES * c, 1)
    distance_nm = round(distance_km * 0.539957, 1)

    # Initial Bearing (azimuth)
    y = math.sin(delta_lambda) * math.cos(phi2)
    x = (math.cos(phi1) * math.sin(phi2) -
         math.sin(phi1) * math.cos(phi2) * math.cos(delta_lambda))
    initial_bearing = (math.degrees(math.atan2(y, x)) + 360.0) % 360.0

    # Estimated Commercial Flight Time (cruising ~840 km/h + 20 mins runway/climb)
    flight_hours = (distance_km / 840.0) + 0.35
    hours = int(flight_hours)
    minutes = int(round((flight_hours - hours) * 60))
    time_str = f"{hours}h {minutes}m" if hours > 0 else f"{minutes} mins"

    lambda1 = math.radians(lon1)
    lambda2 = math.radians(lon2)

    # Compute intermediate waypoints along the Great Circle arc for 2D/3D visualization
    num_points = 50
    waypoints = []
    for i in range(num_points + 1):
        f = i / float(num_points)
        A = math.sin((1.0 - f) * c) / (math.sin(c) if c != 0 else 1.0)
        B = math.sin(f * c) / (math.sin(c) if c != 0 else 1.0)

        x_pt = A * math.cos(phi1) * math.cos(lambda1) + B * math.cos(phi2) * math.cos(lambda2)
        y_pt = A * math.cos(phi1) * math.sin(lambda1) + B * math.cos(phi2) * math.sin(lambda2)
        z_pt = A * math.sin(phi1) + B * math.sin(phi2)

        pt_lat = math.degrees(math.atan2(z_pt, math.sqrt(x_pt**2 + y_pt**2)))
        pt_lon = math.degrees(math.atan2(y_pt, x_pt))
        waypoints.append([round(pt_lat, 4), round(pt_lon, 4)])

    return {
        "distance_km": distance_km,
        "distance_miles": distance_miles,
        "distance_nm": distance_nm,
        "bearing_deg": round(initial_bearing, 1),
        "flight_time_estimate": time_str,
        "waypoints": waypoints,
    }


def fetch_live_flights_in_bounds(min_lat: float, max_lat: float, min_lon: float, max_lon: float) -> List[Dict[str, Any]]:
    """Fetch live aircraft states from OpenSky in the specified bounding box."""
    # Ensure bounds are within valid range and clamped to avoid API errors
    min_lat = max(-85.0, min(85.0, min_lat))
    max_lat = max(-85.0, min(85.0, max_lat))
    min_lon = max(-180.0, min(180.0, min_lon))
    max_lon = max(-180.0, min(180.0, max_lon))

    url = (
        f"https://opensky-network.org/api/states/all?"
        f"lamin={min_lat:.2f}&lomin={min_lon:.2f}&lamax={max_lat:.2f}&lomax={max_lon:.2f}"
    )

    flights = []
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "NIGHTFALLAI-FlightRadar/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            states = data.get("states") or []
            for s in states[:150]:  # Limit to 150 aircraft for optimal 3D frame rates
                if not s or len(s) < 17:
                    continue
                callsign = (s[1] or "").strip()
                origin_country = s[2] or "Unknown"
                lon = s[5]
                lat = s[6]
                baro_alt = s[7] or 0.0  # meters
                velocity = s[9] or 0.0  # m/s
                heading = s[10] or 0.0  # degrees
                on_ground = s[8] or False

                if lat is not None and lon is not None:
                    flights.append({
                        "callsign": callsign or "AIRCRAFT",
                        "country": origin_country,
                        "lat": round(float(lat), 4),
                        "lon": round(float(lon), 4),
                        "altitude_m": round(float(baro_alt), 1),
                        "altitude_ft": int(round(float(baro_alt) * 3.28084)),
                        "speed_kmh": int(round(float(velocity) * 3.6)),
                        "speed_kts": int(round(float(velocity) * 1.94384)),
                        "heading": round(float(heading), 1),
                        "on_ground": bool(on_ground),
                    })
    except Exception as e:
        print(f"[HoloGlobe] Live flights fetch notice: {e}")

    return flights


def reverse_geocode_area(lat: float, lon: float) -> str:
    """Identify region, country, or ocean body for current globe focus."""
    # Check bounding boxes for major world regions / oceans
    if lat > 66.5:
        return "Arctic Polar Region"
    if lat < -60.0:
        return "Antarctica / Southern Ocean"

    try:
        url = f"https://api.bigdatacloud.net/data/reverse-geocode-client?latitude={lat:.3f}&longitude={lon:.3f}&localityLanguage=en"
        req = urllib.request.Request(url, headers={"User-Agent": "NIGHTFALLAI-HoloGlobe/1.0"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            city = data.get("city") or data.get("locality") or ""
            principal = data.get("principalSubdivision") or ""
            country = data.get("countryName") or ""
            
            parts = [p for p in (city, principal, country) if p]
            if parts:
                return ", ".join(parts)
    except Exception:
        pass

    # Approximation by hemisphere / ocean if no response
    if 5.0 <= lat <= 35.0 and 65.0 <= lon <= 95.0:
        return "Indian Subcontinent & Northern Indian Ocean"
    elif 30.0 <= lat <= 60.0 and -10.0 <= lon <= 40.0:
        return "European Continent / Mediterranean"
    elif 15.0 <= lat <= 55.0 and -130.0 <= lon <= -65.0:
        return "North American Continent"

    return f"Geographic Coordinate ({lat:.2f}°, {lon:.2f}°)"


def fetch_driving_route(lat1: float, lon1: float, lat2: float, lon2: float) -> Dict[str, Any]:
    """
    Calculate real road driving route via OSRM (Open Source Routing Machine).
    Falls back gracefully to great-circle flight route if oceanic/unreachable by road.
    """
    url = (
        f"https://router.project-osrm.org/route/v1/driving/"
        f"{lon1:.5f},{lat1:.5f};{lon2:.5f},{lat2:.5f}"
        f"?overview=full&geometries=geojson"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "NIGHTFALLAI-Navigator/1.0"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("code") == "Ok" and data.get("routes"):
                best_route = data["routes"][0]
                dist_m = best_route.get("distance", 0.0)
                dur_s = best_route.get("duration", 0.0)
                coords = best_route.get("geometry", {}).get("coordinates", [])

                dist_km = round(dist_m / 1000.0, 1)
                hours = int(dur_s // 3600)
                mins = int(round((dur_s % 3600) / 60))
                time_str = f"{hours}h {mins}m" if hours > 0 else f"{mins} mins"

                # GeoJSON coordinates are [lon, lat], flip to [lat, lon] for Leaflet
                waypoints = [[round(pt[1], 5), round(pt[0], 5)] for pt in coords]

                return {
                    "mode": "driving",
                    "status": "success",
                    "distance_km": dist_km,
                    "distance_miles": round(dist_km * 0.621371, 1),
                    "duration_str": time_str,
                    "duration_sec": int(dur_s),
                    "waypoints": waypoints,
                }
    except Exception as e:
        print(f"[HoloGlobe] OSRM driving route fallback: {e}")

    # Fallback to great-circle flight route
    gc = calculate_great_circle_route(lat1, lon1, lat2, lon2)
    gc["mode"] = "flight_fallback"
    gc["duration_str"] = gc.get("flight_time_estimate", "")
    return gc


def fetch_live_iss() -> Dict[str, Any]:
    """Fetch live real-time orbital location and telemetry of the International Space Station."""
    url = "https://api.wheretheiss.at/v1/satellites/25544"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "NIGHTFALLAI-SpaceRadar/1.0"})
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            lat = round(float(data["latitude"]), 4)
            lon = round(float(data["longitude"]), 4)
            alt_km = round(float(data["altitude"]), 1)
            vel_kmh = round(float(data["velocity"]), 1)
            visibility = data.get("visibility", "daylight")
            return {
                "name": "International Space Station (ISS)",
                "lat": lat,
                "lon": lon,
                "altitude_km": alt_km,
                "altitude_miles": round(alt_km * 0.621371, 1),
                "velocity_kmh": vel_kmh,
                "velocity_mph": round(vel_kmh * 0.621371, 1),
                "visibility": visibility,
                "timestamp": data.get("timestamp"),
            }
    except Exception:
        # Fallback to Open-Notify if WhereTheISS is busy
        try:
            url2 = "http://api.open-notify.org/iss-now.json"
            req2 = urllib.request.Request(url2, headers={"User-Agent": "NIGHTFALLAI-SpaceRadar/1.0"})
            with urllib.request.urlopen(req2, timeout=4) as resp2:
                data2 = json.loads(resp2.read().decode("utf-8"))
                pos = data2.get("iss_position", {})
                lat = round(float(pos.get("latitude", 0)), 4)
                lon = round(float(pos.get("longitude", 0)), 4)
                return {
                    "name": "International Space Station (ISS)",
                    "lat": lat,
                    "lon": lon,
                    "altitude_km": 420.0,
                    "altitude_miles": 261.0,
                    "velocity_kmh": 27600.0,
                    "velocity_mph": 17150.0,
                    "visibility": "orbit",
                    "timestamp": data2.get("timestamp"),
                }
        except Exception as e:
            return {"error": str(e), "lat": 0.0, "lon": 0.0}


def fetch_live_earthquakes(min_magnitude: float = 2.5) -> List[Dict[str, Any]]:
    """Fetch live USGS earthquakes in the past 24 hours."""
    url = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "NIGHTFALLAI-Seismic/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            features = data.get("features", [])
            results = []
            for f in features:
                props = f.get("properties", {})
                geom = f.get("geometry", {})
                coords = geom.get("coordinates", [])
                mag = props.get("mag")
                if mag is None or float(mag) < min_magnitude or len(coords) < 2:
                    continue
                lon = float(coords[0])
                lat = float(coords[1])
                depth = float(coords[2]) if len(coords) > 2 else 0.0

                results.append({
                    "id": f.get("id"),
                    "place": props.get("place", "Unknown Location"),
                    "magnitude": round(float(mag), 1),
                    "lat": round(lat, 4),
                    "lon": round(lon, 4),
                    "depth_km": round(depth, 1),
                    "time": props.get("time"),
                    "url": props.get("url"),
                })
            # Sort descending by magnitude
            results.sort(key=lambda x: x["magnitude"], reverse=True)
            return results[:60]
    except Exception as e:
        print(f"[HoloGlobe] USGS earthquakes fetch notice: {e}")
        return []


def fetch_nearby_places(query_or_category: str, center_lat: Optional[float] = None, center_lon: Optional[float] = None, location_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """Search for nearby POIs (hospitals, hotels, restaurants, fuel, etc.) via Overpass API with Nominatim fallback."""
    lat = center_lat
    lon = center_lon
    if (lat is None or lon is None) and location_name and location_name.lower() not in ("current", "me", "here", "my location", "current location"):
        lat, lon, _ = geocode_location(location_name)
    if lat is None or lon is None:
        lat, lon, _ = geocode_location("current")

    q_lower = (query_or_category or "hospital").lower().strip()

    # Map natural queries to OpenStreetMap amenity tags
    overpass_filter = None
    if any(k in q_lower for k in ("hospital", "clinic", "doctor", "health", "medical")):
        overpass_filter = 'node["amenity"="hospital"](around:{radius},{lat},{lon});way["amenity"="hospital"](around:{radius},{lat},{lon});node["amenity"="clinic"](around:{radius},{lat},{lon});way["amenity"="clinic"](around:{radius},{lat},{lon});node["amenity"="doctors"](around:{radius},{lat},{lon});'
    elif any(k in q_lower for k in ("pharmacy", "chemist", "drugstore", "medicine")):
        overpass_filter = 'node["amenity"="pharmacy"](around:{radius},{lat},{lon});way["amenity"="pharmacy"](around:{radius},{lat},{lon});'
    elif any(k in q_lower for k in ("restaurant", "food", "dinner", "lunch", "eat", "dining")):
        overpass_filter = 'node["amenity"="restaurant"](around:{radius},{lat},{lon});node["amenity"="fast_food"](around:{radius},{lat},{lon});'
    elif any(k in q_lower for k in ("cafe", "coffee", "tea")):
        overpass_filter = 'node["amenity"="cafe"](around:{radius},{lat},{lon});'
    elif any(k in q_lower for k in ("fuel", "gas", "petrol", "diesel", "charging")):
        overpass_filter = 'node["amenity"="fuel"](around:{radius},{lat},{lon});node["amenity"="charging_station"](around:{radius},{lat},{lon});'
    elif any(k in q_lower for k in ("hotel", "motel", "lodging", "stay", "resort")):
        overpass_filter = 'node["tourism"="hotel"](around:{radius},{lat},{lon});node["tourism"="guest_house"](around:{radius},{lat},{lon});node["tourism"="motel"](around:{radius},{lat},{lon});'
    elif any(k in q_lower for k in ("bank", "atm", "cash")):
        overpass_filter = 'node["amenity"="bank"](around:{radius},{lat},{lon});node["amenity"="atm"](around:{radius},{lat},{lon});'
    elif any(k in q_lower for k in ("police", "cop", "security")):
        overpass_filter = 'node["amenity"="police"](around:{radius},{lat},{lon});'
    elif any(k in q_lower for k in ("grocery", "supermarket", "mart", "store")):
        overpass_filter = 'node["shop"="supermarket"](around:{radius},{lat},{lon});node["shop"="convenience"](around:{radius},{lat},{lon});'

    # Try Overpass API first
    if overpass_filter:
        try:
            radius = 8000  # 8km search radius
            ov_body = f"[out:json][timeout:8];({overpass_filter.format(radius=radius, lat=lat, lon=lon)});out center 20;"
            ov_url = "https://overpass-api.de/api/interpreter"
            req = urllib.request.Request(
                ov_url,
                data=urllib.parse.urlencode({"data": ov_body}).encode("utf-8"),
                headers={"User-Agent": "NIGHTFALLAI-GeospatialPOI/1.0"}
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                elements = data.get("elements", [])
                places = []
                seen_names = set()
                for el in elements:
                    tags = el.get("tags", {})
                    name = tags.get("name") or tags.get("operator") or tags.get("brand")
                    if not name or name in seen_names:
                        continue
                    seen_names.add(name)

                    p_lat = float(el.get("lat") or el.get("center", {}).get("lat", 0.0))
                    p_lon = float(el.get("lon") or el.get("center", {}).get("lon", 0.0))
                    if p_lat == 0.0 or p_lon == 0.0:
                        continue

                    dist_km = round(6371.0 * 2.0 * math.asin(math.sqrt(
                        math.sin(math.radians(p_lat - lat) / 2.0) ** 2 +
                        math.cos(math.radians(lat)) * math.cos(math.radians(p_lat)) *
                        (math.sin(math.radians(p_lon - lon) / 2.0) ** 2)
                    )), 2)

                    addr_parts = [tags.get("addr:street"), tags.get("addr:suburb"), tags.get("addr:city")]
                    addr_str = ", ".join([p for p in addr_parts if p]) or f"{dist_km} km away"

                    places.append({
                        "name": name,
                        "display_name": f"{name} ({addr_str})",
                        "lat": round(p_lat, 5),
                        "lon": round(p_lon, 5),
                        "distance_km": dist_km,
                        "type": tags.get("amenity") or tags.get("tourism") or "poi",
                    })

                if places:
                    places.sort(key=lambda p: p["distance_km"])
                    return places[:15]
        except Exception as e:
            print(f"[HoloGlobe] Overpass API fallback: {e}")

    # Fallback to Nominatim
    q = f"{query_or_category}"
    if location_name and location_name.lower() not in ("current", "me", "here"):
        q += f" in {location_name}"

    encoded_q = urllib.parse.quote(q)
    viewbox = f"{lon-0.3:.4f},{lat+0.3:.4f},{lon+0.3:.4f},{lat-0.3:.4f}"
    url = f"https://nominatim.openstreetmap.org/search?format=json&q={encoded_q}&viewbox={viewbox}&bounded=0&limit=12"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "NIGHTFALLAI-GeospatialPOI/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            places = []
            for item in data:
                p_lat = float(item["lat"])
                p_lon = float(item["lon"])
                dist_km = round(6371.0 * 2.0 * math.asin(math.sqrt(
                    math.sin(math.radians(p_lat - lat) / 2.0) ** 2 +
                    math.cos(math.radians(lat)) * math.cos(math.radians(p_lat)) *
                    (math.sin(math.radians(p_lon - lon) / 2.0) ** 2)
                )), 2)
                places.append({
                    "name": item.get("name") or item.get("display_name", "").split(",")[0],
                    "display_name": item.get("display_name", ""),
                    "lat": round(p_lat, 5),
                    "lon": round(p_lon, 5),
                    "distance_km": dist_km,
                    "type": item.get("type", "landmark"),
                })
            places.sort(key=lambda p: p["distance_km"])
            return places
    except Exception as e:
        print(f"[HoloGlobe] Nearby places notice: {e}")
        return []


def fetch_location_weather(lat: float, lon: float) -> Dict[str, Any]:
    """Fetch live weather metrics (temperature, humidity, wind, condition) from Open-Meteo."""
    url = (
        f"https://api.open-meteo.com/v1/forecast?"
        f"latitude={lat:.4f}&longitude={lon:.4f}&current=temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code"
    )
    code_map = {
        0: ("Clear Sky", "☀️"),
        1: ("Mainly Clear", "🌤️"),
        2: ("Partly Cloudy", "⛅"),
        3: ("Overcast", "☁️"),
        45: ("Foggy", "🌫️"),
        48: ("Rime Fog", "🌫️"),
        51: ("Light Drizzle", "🌦️"),
        53: ("Moderate Drizzle", "🌧️"),
        61: ("Light Rain", "🌧️"),
        63: ("Moderate Rain", "🌧️"),
        65: ("Heavy Rain", "⛈️"),
        80: ("Rain Showers", "🌦️"),
        95: ("Thunderstorm", "⚡"),
    }
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "NIGHTFALLAI-Weather/1.0"})
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            curr = data.get("current", {})
            temp_c = curr.get("temperature_2m", 25.0)
            hum = curr.get("relative_humidity_2m", 50)
            wind = curr.get("wind_speed_10m", 10.0)
            wcode = curr.get("weather_code", 0)
            cond_desc, icon = code_map.get(wcode, ("Partly Cloudy", "⛅"))
            return {
                "temperature_c": round(temp_c, 1),
                "temperature_f": round(temp_c * 9.0 / 5.0 + 32.0, 1),
                "humidity": hum,
                "wind_kmh": round(wind, 1),
                "condition": cond_desc,
                "icon": icon,
            }
    except Exception as e:
        return {
            "temperature_c": 28.0,
            "temperature_f": 82.4,
            "humidity": 60,
            "wind_kmh": 12.0,
            "condition": "Fair",
            "icon": "🌤️",
        }


def fetch_radar_timestamp() -> Optional[int]:
    """Fetch the latest RainViewer doppler radar frame timestamp."""
    try:
        url = "https://api.rainviewer.com/public/weather-maps.json"
        req = urllib.request.Request(url, headers={"User-Agent": "NIGHTFALLAI-WeatherRadar/1.0"})
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            past = data.get("radar", {}).get("past", [])
            if past:
                return past[-1].get("time")
    except Exception:
        pass
    return None
