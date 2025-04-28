try:
    from geopy.distance import geodesic
except ImportError:
    print("Warning: geopy module not found. Please install it using: pip install geopy")
    # Define a stub function to prevent errors
    def geodesic(point1, point2):
        return type('Distance', (), {'meters': 0})

from shapely.geometry import Point, Polygon
import math

def calculate_bearing(pointA, pointB):
    lat1, lon1 = math.radians(pointA[0]), math.radians(pointA[1])
    lat2, lon2 = math.radians(pointB[0]), math.radians(pointB[1])
    
    dLon = lon2 - lon1
    x = math.sin(dLon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - (math.sin(lat1) * math.cos(lat2) * math.cos(dLon))
    
    initial_bearing = math.atan2(x, y)
    initial_bearing = math.degrees(initial_bearing)
    compass_bearing = (initial_bearing + 360) % 360
    return compass_bearing

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

def process_response(data, reference_point):
    try:
        # Extract necessary information from the feature
        properties = data['properties']
        coordinates = data['geometry']['coordinates'][0][0]  # Get the first polygon's coordinates
        centroid = (float(properties['centerLat']), float(properties['centerLon']))  # Use centerLat and centerLon for location
        
        # Create a Point object for the reference point
        point_of_interest = Point(reference_point[1], reference_point[0])  # (lon, lat)
        
        # Create a Polygon object from the coordinates
        polygon = Polygon(coordinates)
        
        # Calculate distance to the polygon
        if polygon.contains(point_of_interest):
            distance = 0
        else:
            distance = point_of_interest.distance(polygon)

        # Calculate bearing to the centroid
        bearing = calculate_bearing(reference_point, centroid)

        return centroid, distance, bearing
    except KeyError as e:
        print(f"KeyError: {e} in data: {data}")
        return None, None, None

def calculate_distance(pointA, pointB):
    # Calculate distance using Shapely or simple math if geopy is not available
    try:
        return geodesic(pointA, pointB).meters
    except:
        # Fallback to approximate calculation
        lat1, lon1 = math.radians(pointA[0]), math.radians(pointA[1])
        lat2, lon2 = math.radians(pointB[0]), math.radians(pointB[1])
        # Approximate Earth radius in meters
        R = 6371000
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
        return R * c