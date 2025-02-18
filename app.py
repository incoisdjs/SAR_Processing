import streamlit as st
import json
import uuid  # Import uuid for generating unique keys
from api import fetch_data
from utils import process_response, bearing_to_direction
from shapely.geometry import Point, Polygon

# Streamlit app title
st.title("Satellite Data Fetcher")

# List of platforms
platforms = [
    "Sentinel-1", "SLC-BURST", "OPERA-S1", "ALOS PALSAR",
    "ALOS AVNIR-2", "SIR-C", "ARIA S1 GUNW", "SMAP",
    "UAVSAR", "RADARSAT-1", "ERS", "JERS-1", "AIRSAR", "SEASAT"
]

# Input fields for latitude, longitude, start date, and end date
latitude = st.number_input("Enter Latitude:", format="%.6f")
longitude = st.number_input("Enter Longitude:", format="%.6f")
start_date = st.date_input("Start Date")
end_date = st.date_input("End Date")

# Input field for limit on number of results to fetch from the API
result_limit = st.number_input("Enter Limit for Results to Fetch:", min_value=1, value=5)

# Button to fetch data
if st.button("Fetch Data"):
    # Flag to check if any valid features were found
    any_valid_features = False

    # Iterate through all platforms
    for platform in platforms:
        data = fetch_data(platform, latitude, longitude, start_date, end_date, result_limit)
        
        if data and 'features' in data and data['features']:
            # Display the platform name as a heading
            st.header(platform)
            any_valid_features = True
            
            # Process each feature
            for feature in data['features']:
                # Extract coordinates and calculate distance
                coordinates = feature['geometry']['coordinates'][0]  # Get the first polygon's coordinates
                polygon = Polygon(coordinates)  # Create a Polygon object
                
                # Create a Point object for the reference point
                point_of_interest = Point(longitude, latitude)  # (lon, lat)
                
                # Calculate distance to the polygon
                if polygon.contains(point_of_interest):
                    distance = 0
                else:
                    distance = point_of_interest.distance(polygon)

                # Display the feature details
                nearest_location = feature['properties'].get('url', 'Unknown Location')
                st.write(f"Nearest Location: {nearest_location}, Distance: {distance:.2f} units")
                
                # Get the URL for downloading
                download_url = feature['properties'].get('url', None)
                
                # Add a button to open the URL if it exists
                if download_url:
                    if st.button(f"Download Data", key=str(uuid.uuid4()),type="primary"):
                        js = f"window.open('{download_url}', '_blank')"
                        st.markdown(f"<script>{js}</script>", unsafe_allow_html=True)
            
            # Display the entire response for the platform
            st.json(data)  # Display the raw JSON response

    # If no valid features were found for any platform, show a warning
    if not any_valid_features:
        st.warning("No valid features returned for any platform.") 