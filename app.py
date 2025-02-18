import streamlit as st
import json
import uuid  # Import uuid for generating unique keys
from api import fetch_data
from utils import process_response, bearing_to_direction
from shapely.geometry import Point, Polygon
import requests
from oauthlib.oauth2 import BackendApplicationClient
from requests_oauthlib import OAuth2Session

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

# Button to fetch data from Sentinel API
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
                nearest_location = feature['properties'].get('sceneName', 'Unknown Location')
                st.write(f"Nearest Location: {nearest_location}, Distance: {distance:.2f} units")
                
                # Get the URL for downloading
                download_url = feature['properties'].get('url', None)
                
                # Add a button to open the URL if it exists
                if download_url:
                    if st.button(f"Open Metadata for {nearest_location}", key=str(uuid.uuid4())):
                        # Use markdown to create a link that opens in a new tab
                        st.markdown(f'<a href="{download_url}" target="_blank">Open Metadata</a>', unsafe_allow_html=True)
            
            # Display the entire response for the platform
            st.json(data)  # Display the raw JSON response

    # If no valid features were found for any platform, show a warning
    if not any_valid_features:
        st.warning("No valid features returned for any platform.")

# Function to authenticate and fetch data from Copernicus API
def get_oauth_session(client_id, client_secret):
    client = BackendApplicationClient(client_id=client_id)
    oauth = OAuth2Session(client=client)
    token_url = 'https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token'
    
    try:
        token = oauth.fetch_token(
            token_url=token_url,
            client_id=client_id,
            client_secret=client_secret,
            include_client_id=True
        )
        return oauth
    except Exception as e:
        st.error("Error acquiring OAuth token: " + str(e))
        return None

def search_catalog(oauth):
    search_data = {
        "bbox": [longitude - 0.1, latitude - 0.1, longitude + 0.1, latitude + 0.1],  # Example bounding box
        "datetime": f"{start_date}T00:00:00Z/{end_date}T23:59:59Z",
        "collections": ["sentinel-1-grd"],
        "limit": 5,
    }
    
    search_url = "https://sh.dataspace.copernicus.eu/api/v1/catalog/1.0.0/search"
    
    try:
        response = oauth.post(search_url, json=search_data)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as http_err:
        st.error("HTTP error occurred during catalog search: " + str(http_err))
        return None

# Button to fetch data from Copernicus API
if st.button("Fetch Copernicus Data"):
    CLIENT_ID = "sh-3851bb68-978a-4b30-8a2d-d9a09a7395d1"
    CLIENT_SECRET = "tZHX1ciEvMjtsUVPomP2lx71LyiS8dp9"
    
    oauth = get_oauth_session(CLIENT_ID, CLIENT_SECRET)
    
    if oauth:
        catalog_result = search_catalog(oauth)
        if catalog_result and "features" in catalog_result:
            for feature in catalog_result["features"]:
                st.json(feature)  # Display each feature's details
        else:
            st.warning("No SAR products found in the catalog search.") 