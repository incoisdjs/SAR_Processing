import requests
import json
from geopy.distance import geodesic  # Import geodesic for distance calculation
import math  # Import math for trigonometric functions
from shapely.geometry import Point, Polygon  # Import Point and Polygon from shapely

base_url = "https://api.daac.asf.alaska.edu/services/search/param"

# Define the point of interest
point_of_interest = (30.342612, -88.026061)  # (latitude, longitude)

# Function to calculate bearing
def calculate_bearing(pointA, pointB):
    lat1, lon1 = math.radians(pointA[0]), math.radians(pointA[1])
    lat2, lon2 = math.radians(pointB[0]), math.radians(pointB[1])
    
    dLon = lon2 - lon1
    x = math.sin(dLon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - (math.sin(lat1) * math.cos(lat2) * math.cos(dLon))
    
    initial_bearing = math.atan2(x, y)
    # Convert from radians to degrees
    initial_bearing = math.degrees(initial_bearing)
    # Normalize the bearing to 0-360 degrees
    compass_bearing = (initial_bearing + 360) % 360
    return compass_bearing

# Function to convert bearing to cardinal direction
def bearing_to_direction(bearing):
    if bearing >= 337.5 or bearing < 22.5:
        return "north"
    elif 22.5 <= bearing < 67.5:
        return "northeast"
    elif 67.5 <= bearing < 112.5:
        return "east"
    elif 112.5 <= bearing < 157.5:
        return "southeast"
    elif 157.5 <= bearing < 202.5:
        return "south"
    elif 202.5 <= bearing < 247.5:
        return "southwest"
    elif 247.5 <= bearing < 292.5:
        return "west"
    elif 292.5 <= bearing < 337.5:
        return "northwest"

# List of satellite platforms to query
platforms = [
    "Sentinel-1", "SLC-BURST", "OPERA-S1", "ALOS PALSAR",
    "ALOS AVNIR-2", "SIR-C", "ARIA S1 GUNW", "SMAP",
    "UAVSAR", "RADARSAT-1", "ERS", "JERS-1", "AIRSAR", "SEASAT"
]

# Initialize lists to store results
sar_results = []
non_sar_results = []

for platform in platforms:
    params = {
        "platform": platform,
        "intersectsWith": f"POINT({point_of_interest[1]} {point_of_interest[0]})",  # WKT format for location
        "start": "2025-02-01",  # Start date
        "end": "2025-02-18",    # End date
        "output": "geojson",    # Desired output format
        "maxResults": 1         # Limit to the nearest result
    }

    # Send GET request to API
    response = requests.get(base_url, params=params)

    # Check if request was successful
    if response.status_code == 200:
        # Parse the response as JSON
        data = response.json()

        if data['features']:  # Check if there are any features in the response
            # Save the full response for the current platform to a JSON file
            platform_filename = f'response_{platform.replace(" ", "_")}.json'
            with open(platform_filename, 'w') as json_file:
                json.dump(data, json_file, indent=4)

            # Initialize variables to track the nearest location
            nearest_location = None
            nearest_distance = float('inf')  # Start with an infinitely large distance
            nearest_bearing = None

            for feature in data['features']:
                if 'properties' in feature:
                    # Create a polygon from the coordinates
                    if 'geometry' in feature and 'coordinates' in feature['geometry']:
                        coordinates = feature['geometry']['coordinates']
                        # Flatten the coordinates if necessary
                        if len(coordinates) > 0 and isinstance(coordinates[0][0], list):
                            polygon_coords = [(coord[0], coord[1]) for coord_set in coordinates for coord in coord_set]
                        else:
                            polygon_coords = [(coord[0], coord[1]) for coord in coordinates]

                        polygon = Polygon(polygon_coords)  # Create a polygon

                        # Create a point from the point of interest
                        point = Point(point_of_interest[1], point_of_interest[0])  # (longitude, latitude)

                        # Check if the point is inside the polygon
                        if polygon.contains(point):
                            distance = 0  # Point is inside the polygon
                        else:
                            # Calculate the distance to the nearest point on the polygon
                            distance = point.distance(polygon)  # Distance to the nearest edge

                        bearing = calculate_bearing(point_of_interest, (polygon.centroid.y, polygon.centroid.x))  # Calculate bearing

                        # Check if this is the nearest location
                        if distance < nearest_distance:
                            nearest_distance = distance
                            nearest_location = (polygon.centroid.y, polygon.centroid.x)  # Use centroid for location
                            nearest_bearing = bearing

            # Classify the platform as SAR or non-SAR
            if "SAR" in platform or "ALOS" in platform or "SLC" in platform:
                sar_results.append({
                    "platform": platform,
                    "distance_km": nearest_distance,
                    "location": nearest_location,
                    "bearing": nearest_bearing
                })
            else:
                non_sar_results.append({
                    "platform": platform,
                    "distance_km": nearest_distance,
                    "location": nearest_location,
                    "bearing": nearest_bearing
                })
        else:
            print(f"No features found for platform: {platform}")
    else:
        print(f"Error: {response.status_code} - {response.text}")

# Find the nearest SAR and non-SAR results
nearest_sar = min(sar_results, key=lambda x: x['distance_km'], default=None)
nearest_non_sar = min(non_sar_results, key=lambda x: x['distance_km'], default=None)

# Prepare the final results
final_results = {
    "nearest_sar": nearest_sar,
    "nearest_non_sar": nearest_non_sar
}

# Save the final results to a JSON file
with open('nearest_results.json', 'w') as json_file:
    json.dump(final_results, json_file, indent=4)

# Output the results
if nearest_sar:
    direction = bearing_to_direction(nearest_sar['bearing'])
    print(f"Nearest SAR Platform: {nearest_sar['platform']}, Distance: {nearest_sar['distance_km']:.2f} km to the {direction}")
if nearest_non_sar:
    direction = bearing_to_direction(nearest_non_sar['bearing'])
    print(f"Nearest Non-SAR Platform: {nearest_non_sar['platform']}, Distance: {nearest_non_sar['distance_km']:.2f} km to the {direction}")
