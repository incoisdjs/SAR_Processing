import streamlit as st
import json
import uuid  # Import uuid for generating unique keys
from api import fetch_data
from utils import process_response, bearing_to_direction
from shapely.geometry import Point, Polygon
import requests
from oauthlib.oauth2 import BackendApplicationClient
from requests_oauthlib import OAuth2Session
import dotenv
import os
import streamlit.components.v1 as components
from botocore.config import Config
from botocore.auth import UNSIGNED_PAYLOAD
from urllib.parse import urlparse

dotenv.load_dotenv()

# Custom CSS to style download buttons (if needed)
st.markdown(
    """
    <style>
    .download-button {
        background-color: #4CAF50;
        border: none;
        color: white;
        padding: 8px 16px;
        text-align: center;
        text-decoration: none;
        display: inline-block;
        font-size: 14px;
        margin: 4px 2px;
        cursor: pointer;
        border-radius: 4px;
    }
    </style>
    """,
    unsafe_allow_html=True
)

st.title("Satellite Data Fetcher")

# List of platforms for Sentinel API queries
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

# Button to fetch data from the Sentinel (Alaska) API
if st.button("Fetch Alaska Data"):
    any_valid_features = False
    for platform in platforms:
        data = fetch_data(platform, latitude, longitude, start_date, end_date, result_limit)
        if data and 'features' in data and data['features']:
            st.header(platform)
            any_valid_features = True
            for feature in data['features']:
                coordinates = feature['geometry']['coordinates'][0]
                polygon = Polygon(coordinates)
                point_of_interest = Point(longitude, latitude)
                distance = 0 if polygon.contains(point_of_interest) else point_of_interest.distance(polygon)
                download_url = feature['properties'].get('url', None)
                nearest_location = feature['properties'].get('sceneName', 'Unknown Location')
                st.write(f"Nearest Location: {nearest_location}, Distance: {distance:.2f} units and file is {download_url}")
                if download_url:
                    st.link_button(label="Download Data", url=download_url)
            st.json(data)
    if not any_valid_features:
        st.warning("No valid features returned for any platform.")

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

def search_catalog(oauth, latitude, longitude, start_date, end_date):
    search_data = {
        "bbox": [longitude - 0.1, latitude - 0.1, longitude + 0.1, latitude + 0.1],
        "datetime": f"{start_date}T00:00:00Z/{end_date}T23:59:59Z",
        "collections": ["sentinel-1-grd"],
        "limit": 5,
    }
    search_url = "https://sh.dataspace.copernicus.eu/api/v1/catalog/1.0.0/search"
    try:
        headers = {"Authorization": f"Bearer {oauth.token['access_token']}", "Content-Type": "application/json"}
        response = oauth.post(search_url, json=search_data, headers=headers)
        if response.status_code == 401:
            st.error("401 Unauthorized: Please provide a valid access token. Check your CLIENT_ID and CLIENT_SECRET.")
            return None
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as http_err:
        st.error("HTTP error occurred during catalog search: " + str(http_err))
        return None

# Function to download a file using GET with the access token in the header
def download_file(download_url, access_token):
    headers = {"Authorization": f"Bearer {access_token}"}
    response = requests.get(download_url, headers=headers)
    response.raise_for_status()  # Raise an error for bad responses
    return response.content  # Return the content of the response

def parse_s3_uri(s3_uri):
    """
    Parse an S3 URI (e.g., s3://bucket-name/path/to/object)
    and return the bucket name and object key.
    """
    parsed = urlparse(s3_uri)
    bucket = parsed.netloc
    key = parsed.path.lstrip('/')
    return bucket, key

def convert_s3_uri_to_http(s3_uri):
    """
    Convert an S3 URI to an HTTP URL.
    Note: This works for many public S3 buckets.
    """
    bucket, key = parse_s3_uri(s3_uri)
    return f"https://{bucket}.s3.amazonaws.com/{key}"

# Button to fetch data from Copernicus API and display download buttons
if st.button("Fetch Copernicus Data"):
    CLIENT_ID = os.getenv("CLIENT_ID")
    CLIENT_SECRET = os.getenv("CLIENT_SECRET")
    
    oauth = get_oauth_session(CLIENT_ID, CLIENT_SECRET)
    if oauth:
        catalog_result = search_catalog(oauth, latitude, longitude, start_date, end_date)
        if catalog_result and "features" in catalog_result:
            for feature in catalog_result["features"]:
                st.json(feature)  # Display the raw feature data
                # Get the S3 URI from assets.data.href
                s3_uri = feature.get("assets", {}).get("data", {}).get("href", None)
                if s3_uri:
                    st.write("S3 URI found:", s3_uri)
                    # Convert the S3 URI to an HTTP URL
                    http_url = convert_s3_uri_to_http(s3_uri)
                    # Button to open the HTTP URL in a new browser tab.
                    # Using a lambda to defer the function call until the button is clicked.
                    st.link_button(label="Open S3 URI",url=http_url)
                        # components.html(
                        #     f"""
                        #     <script>
                        #         window.open("{http_url}", "_blank");
                        #     </script>
                        #     """,
                        #     height=0,
                        # )
        else:
            st.warning("No SAR products found in the catalog search.")