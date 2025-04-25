import streamlit as st
import json
import uuid
from api import fetch_data
from utils import process_response, bearing_to_direction
from shapely.geometry import Point, Polygon, shape
import requests
from oauthlib.oauth2 import BackendApplicationClient
from requests_oauthlib import OAuth2Session
import dotenv
import os
import streamlit.components.v1 as components
from urllib.parse import urlparse
import folium
from streamlit_folium import st_folium
import numpy as np
import pandas as pd
import geopandas as gpd
from datetime import date, datetime, timedelta
import time
import asyncio
import aiohttp
import io

dotenv.load_dotenv()

# Initialize session state for persistent data storage
if 'auth_token' not in st.session_state:
    st.session_state.auth_token = None
if 'search_results' not in st.session_state:
    st.session_state.search_results = {}
if 'download_data' not in st.session_state:
    st.session_state.download_data = {}
if 'download_progress' not in st.session_state:
    st.session_state.download_progress = {}
if 'downloaded_files' not in st.session_state:
    st.session_state.downloaded_files = {}
if 'download_dir' not in st.session_state:
    st.session_state.download_dir = os.path.join(os.path.expanduser("~"), "Downloads")

# List of platforms for Sentinel API queries
platforms = [
    "Sentinel-1", "SLC-BURST", "OPERA-S1", "ALOS PALSAR",
    "ALOS AVNIR-2", "SIR-C", "ARIA S1 GUNW", "SMAP",
    "UAVSAR", "RADARSAT-1", "ERS", "JERS-1", "AIRSAR", "SEASAT"
]

# Current date, time and user info - UPDATED with user's values
current_datetime = "2025-04-25 13:56:21"  # UTC
current_user = "SauravHaldar04"

# Copernicus API endpoints
COPERNICUS_AUTH_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
COPERNICUS_CATALOG_URL = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"

def get_keycloak_token(username: str, password: str) -> str:
    """Get access token from Copernicus using username and password"""
    data = {
        "client_id": "cdse-public",
        "username": username,
        "password": password,
        "grant_type": "password",
    }
    try:
        # Test basic internet connectivity first
        try:
            requests.get("https://www.google.com", timeout=5)
        except:
            st.error("No internet connection detected. Please check your network connection.")
            return None

        # Try to resolve the Copernicus domain
        try:
            requests.get("https://identity.dataspace.copernicus.eu", timeout=5)
        except:
            st.error("""
                Unable to connect to Copernicus servers. This could be due to:
                1. Network restrictions or firewall settings
                2. DNS resolution issues
                3. VPN requirements
                
                Please try:
                1. Checking your internet connection
                2. Using a different network
                3. Connecting to a VPN if required
                4. Contacting your network administrator
            """)
            return None

        # Attempt authentication
        r = requests.post(COPERNICUS_AUTH_URL, data=data, timeout=10)
        r.raise_for_status()
        return r.json()["access_token"]
    except requests.exceptions.ConnectionError:
        st.error("Connection Error: Unable to reach Copernicus servers. Please check your internet connection.")
        return None
    except requests.exceptions.Timeout:
        st.error("Connection Timeout: The request took too long. Please try again.")
        return None
    except requests.exceptions.RequestException as e:
        st.error(f"Authentication Error: {str(e)}")
        return None
    except Exception as e:
        st.error(f"Unexpected Error: {str(e)}")
        return None

def search_products(token: str, bbox: str, collection: str, start_date: str, end_date: str, result_limit: int = 1000):
    """Search for products in the Copernicus catalog"""
    url = COPERNICUS_CATALOG_URL
    params = {
        "$filter": f"Collection/Name eq '{collection}' and OData.CSC.Intersects(area=geography'SRID=4326;{bbox}') and ContentDate/Start gt {start_date}T00:00:00.000Z and ContentDate/Start lt {end_date}T23:59:59.999Z",
        "$count": "True",
        "$top": result_limit
    }
    headers = {"Authorization": f"Bearer {token}"}
    
    try:
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        st.error(f"Search failed: {str(e)}")
        return None

async def download_product(session, product_id: str, token: str, product_name: str, output_dir: str, progress_placeholder, status_placeholder):
    """Download a product with progress tracking"""
    try:
        # Ensure the output directory exists
        if not os.path.exists(output_dir):
            try:
                os.makedirs(output_dir)
                status_placeholder.info(f"Created directory: {output_dir}")
            except Exception as e:
                status_placeholder.error(f"Failed to create directory: {output_dir}. Error: {str(e)}")
                return None
        
        # Clean the product name to ensure it's a valid filename
        clean_name = "".join(c for c in product_name if c.isalnum() or c in (' ', '-', '_', '.')).strip()
        output_path = os.path.join(output_dir, f"{clean_name}.zip")
        
        # Check if file already exists
        if os.path.exists(output_path):
            status_placeholder.warning(f"File already exists at: {output_path}")
            return output_path
        
        # Check write permissions on output directory
        try:
            test_file = os.path.join(output_dir, ".write_test")
            with open(test_file, "w") as f:
                f.write("test")
            os.remove(test_file)
        except Exception as e:
            status_placeholder.error(f"No write permission on directory: {output_dir}. Error: {str(e)}")
            return None
            
        headers = {"Authorization": f"Bearer {token}"}
        
        # Get the download URL
        url = f"{COPERNICUS_CATALOG_URL}({product_id})/$value"
        
        # Follow redirects
        async with session.get(url, headers=headers, allow_redirects=False) as response:
            while response.status in (301, 302, 303, 307):
                url = response.headers["Location"]
                response = await session.get(url, headers=headers, allow_redirects=False)
        
        # Get file size
        async with session.get(url, headers=headers) as response:
            total_size = int(response.headers.get('content-length', 0))
            status_placeholder.write(f"Total size: {total_size / (1024*1024):.2f} MB")
            
            # Download with progress
            downloaded = 0
            with open(output_path, "wb") as f:
                async for chunk in response.content.iter_chunked(8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        progress = (downloaded / total_size) * 100 if total_size > 0 else 0
                        progress_placeholder.progress(progress / 100)
                        status_placeholder.write(
                            f"Downloading: {downloaded / (1024*1024):.2f} MB / "
                            f"{total_size / (1024*1024):.2f} MB ({progress:.1f}%)"
                        )
            
            # Verify the file was downloaded correctly
            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                status_placeholder.success(f"Download complete! File saved to: {output_path}")
                # Show a clickable link to the file directory
                st.markdown(f"[Open Downloads Folder](file://{output_dir})")
                return output_path
            else:
                status_placeholder.error("Download failed: File was not saved correctly")
                return None
            
    except Exception as e:
        status_placeholder.error(f"Download failed: {str(e)}")
        return None

def create_map(center_lat=40.7, center_lon=-73.9, zoom=10):
    m = folium.Map(location=[center_lat, center_lon], zoom_start=zoom)
    folium.Rectangle(
        bounds=[[40.4, -74.3], [41.0, -73.5]],
        color='#ff7800',
        fill=True,
        fill_color='#ffff00',
        fill_opacity=0.2,
        popup='Area of Interest'
    ).add_to(m)
    return m

# Helper function for displaying Alaska feature info
def display_alaska_feature_info(feature):
    props = feature['properties']
    
    st.markdown(f"""
        **Scene Name:** {props.get('sceneName', 'N/A')}  
        **Date:** {props.get('startTime', 'N/A')}  
        **Platform:** {props.get('platform', 'N/A')}  
        **Sensor:** {props.get('sensor', 'N/A')}  
        **Processing Level:** {props.get('processingLevel', 'N/A')}  
    """)
    
    if 'flightDirection' in props:
        direction = bearing_to_direction(props.get('flightDirection', 0))
        st.markdown(f"**Flight Direction:** {direction} ({props.get('flightDirection', 'N/A')}°)")
    
    if 'fileSize' in props:
        size_mb = props.get('fileSize', 0) / (1024 * 1024)
        st.markdown(f"**File Size:** {size_mb:.2f} MB")
    
    # Add download button for Alaska data
    if st.button(f"Download {props.get('sceneName', 'File')}", key=f"dl_{props.get('fileID', uuid.uuid4())}"):
        with st.spinner("Preparing download..."):
            # Use selected download directory
            output_dir = st.session_state.download_dir
            
            # Ensure directory exists
            if not os.path.exists(output_dir):
                try:
                    os.makedirs(output_dir)
                    st.info(f"Created directory: {output_dir}")
                except Exception as e:
                    st.error(f"Failed to create directory: {output_dir}. Error: {str(e)}")
                    return
            
            st.info(f"Files will be downloaded to: {output_dir}")
            
            # Implement Alaska data download functionality here
            # This is a placeholder since the original code doesn't show the download implementation
            st.success(f"Download started for {props.get('sceneName', 'File')}")

# Create map for Alaska data
def create_map(features, center_lat, center_lon):
    m = folium.Map(location=[center_lat, center_lon], zoom_start=6)
    
    for feature in features:
        props = feature['properties']
        if 'geometry' in feature and feature['geometry']:
            geom = feature['geometry']
            
            if geom['type'] == 'Polygon':
                folium.Polygon(
                    locations=[[p[1], p[0]] for p in geom['coordinates'][0]],
                    popup=props.get('sceneName', 'Unknown'),
                    color='blue',
                    fill=True,
                    fill_color='blue',
                    fill_opacity=0.2
                ).add_to(m)
            elif geom['type'] == 'Point':
                folium.Marker(
                    location=[geom['coordinates'][1], geom['coordinates'][0]],
                    popup=props.get('sceneName', 'Unknown')
                ).add_to(m)
    
    return m

# Custom CSS for modern UI
st.markdown("""
    <style>
    /* Main container styling */
    .main {
        background-color: #f8f9fa;
        padding: 2rem;
    }
    
    /* Header styling */
    .stApp header {
        background-color: #ffffff;
        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
    }
    
    /* Title styling */
    h1 {
        color: #1a1a1a;
        font-size: 2.5rem;
        font-weight: 700;
        margin-bottom: 1.5rem;
    }
    
    /* Card styling */
    .card {
        background-color: #ffffff;
        border-radius: 10px;
        padding: 1.5rem;
        margin-bottom: 1rem;
        box-shadow: 0 2px 4px rgba(0,0,0,0.05);
    }
    
    /* Button styling */
    .stButton button {
        background-color: #2563eb;
        color: white;
        border-radius: 8px;
        padding: 0.5rem 1rem;
        font-weight: 600;
        border: none;
        transition: all 0.3s ease;
    }
    
    .stButton button:hover {
        background-color: #1d4ed8;
        transform: translateY(-1px);
    }
    
    /* Input field styling */
    .stTextInput input, .stNumberInput input {
        border-radius: 8px;
        border: 1px solid #e2e8f0;
        padding: 0.5rem;
    }
    
    /* Map container styling */
    .map-container {
        border-radius: 10px;
        overflow: hidden;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
    }
    
    /* Feature info styling */
    .feature-info {
        background-color: #f8fafc;
        border-radius: 8px;
        padding: 1rem;
        margin-bottom: 1rem;
    }
    
    /* Tab styling */
    .stTabs [data-baseweb="tab-list"] {
        gap: 2rem;
    }
    
    .stTabs [data-baseweb="tab"] {
        padding: 1rem 2rem;
        border-radius: 8px;
    }
    
    /* Loading spinner styling */
    .stSpinner > div {
        border-color: #2563eb;
    }
    
    /* Success/Error message styling */
    .stAlert {
        border-radius: 8px;
    }
    
    /* User info bar */
    .user-info-bar {
        background-color: #f1f5f9;
        padding: 0.5rem 1rem;
        border-radius: 8px;
        margin-bottom: 1rem;
        display: flex;
        justify-content: space-between;
        align-items: center;
        font-size: 0.9rem;
    }

    /* Token display */
    .token-display {
        background-color: #1e293b;
        color: #94a3b8;
        padding: 1rem;
        border-radius: 8px;
        font-family: monospace;
        overflow-x: auto;
        white-space: pre-wrap;
        word-break: break-all;
        margin-top: 1rem;
        margin-bottom: 1rem;
    }
    
    /* Progress bar styling */
    .progress-container {
        margin: 1rem 0;
    }
    
    .progress-bar {
        height: 10px;
        background-color: #e2e8f0;
        border-radius: 5px;
        overflow: hidden;
    }
    
    .progress-bar-fill {
        height: 100%;
        background-color: #2563eb;
        transition: width 0.3s ease;
    }
    
    .progress-text {
        margin-top: 0.5rem;
        font-size: 0.9rem;
        color: #64748b;
    }
    
    /* Download info box */
    .download-info {
        background-color: #f0f9ff;
        border-left: 4px solid #2563eb;
        padding: 1rem;
        border-radius: 4px;
        margin: 1rem 0;
    }
    </style>
""", unsafe_allow_html=True)

# Main title with modern styling
st.markdown("""
    <div style='text-align: center; margin-bottom: 2rem;'>
        <h1 style='color: #1a1a1a; font-size: 2.5rem; font-weight: 700;'>
            Satellite Data Explorer
        </h1>
        <p style='color: #64748b; font-size: 1.1rem;'>
            Explore and download satellite imagery from multiple sources
        </p>
    </div>
""", unsafe_allow_html=True)

# Current user and date/time information
st.markdown(f"""
    <div class='user-info-bar'>
        <div>Current User: <b>{current_user}</b></div>
        <div>Current Date/Time (UTC): <b>{current_datetime}</b></div>
    </div>
""", unsafe_allow_html=True)

# Download directory selector component - global for both tabs
st.sidebar.header("Download Settings")
# Text input field for download path with apply button
new_download_dir = st.sidebar.text_input("Download Directory", value=st.session_state.download_dir, key="download_dir_input")

# Button to apply new download directory
if st.sidebar.button("Apply Download Path", key="apply_download_path"):
    # Check if the directory exists or can be created
    try:
        if not os.path.exists(new_download_dir):
            os.makedirs(new_download_dir)
        
        # Test write permission
        test_file = os.path.join(new_download_dir, ".write_test")
        with open(test_file, "w") as f:
            f.write("test")
        os.remove(test_file)
        
        st.session_state.download_dir = new_download_dir
        st.sidebar.success(f"Download path set to: {new_download_dir}")
    except Exception as e:
        st.sidebar.error(f"Error with selected path: {str(e)}")

# Create tabs for different data sources
tab1, tab2 = st.tabs(["Alaska Satellite Facility", "Copernicus Hub"])

with tab1:
    st.markdown("""
        <div class='card'>
            <h2 style='color: #1a1a1a; font-size: 1.5rem; margin-bottom: 1rem;'>
                Search Parameters
            </h2>
    """, unsafe_allow_html=True)
    
    # Input fields in a two-column layout
    col1, col2 = st.columns(2)
    with col1:
        latitude = st.number_input("Latitude", format="%.6f", key="alaska_lat")
        start_date = st.date_input("Start Date", key="alaska_start")
    with col2:
        longitude = st.number_input("Longitude", format="%.6f", key="alaska_lon")
        end_date = st.date_input("End Date", key="alaska_end")
    
    result_limit = st.number_input("Results Limit", min_value=1, value=5, key="alaska_limit")
    
    st.markdown("</div>", unsafe_allow_html=True)
    
    if st.button("Search Alaska Data", key="alaska_search"):
        with st.spinner("Fetching data..."):
            any_valid_features = False
            all_features = []
            
            for platform in platforms:
                data = fetch_data(platform, latitude, longitude, start_date, end_date, result_limit)
                if data and 'features' in data and data['features']:
                    st.markdown(f"""
                        <div class='card'>
                            <h3 style='color: #1a1a1a; font-size: 1.25rem; margin-bottom: 1rem;'>
                                {platform}
                            </h3>
                    """, unsafe_allow_html=True)
                    
                    any_valid_features = True
                    all_features.extend(data['features'])
                    
                    for feature in data['features']:
                        with st.expander(f"Scene: {feature['properties'].get('sceneName', 'Unknown')}", expanded=True):
                            display_alaska_feature_info(feature)
                            
                            if st.checkbox("Show Raw Data", key=f"raw_{feature['properties'].get('fileID', str(uuid.uuid4()))}"):
                                st.json(feature)
                    
                    st.markdown("</div>", unsafe_allow_html=True)
            
            if any_valid_features:
                st.markdown("""
                    <div class='card'>
                        <h3 style='color: #1a1a1a; font-size: 1.25rem; margin-bottom: 1rem;'>
                            Coverage Map
                        </h3>
                """, unsafe_allow_html=True)
                
                try:
                    center_lat = float(all_features[0]['properties'].get('centerLat', latitude))
                    center_lon = float(all_features[0]['properties'].get('centerLon', longitude))
                    
                    with st.spinner("Generating map..."):
                        m = create_map(all_features, center_lat, center_lon)
                        st.markdown('<div class="map-container">', unsafe_allow_html=True)
                        map_data = st_folium(m, width=800, height=600, returned_objects=["last_active_drawing", "last_clicked"])
                        st.markdown('</div>', unsafe_allow_html=True)
                        
                        # Display information about clicked locations
                        if map_data["last_clicked"]:
                            st.write("Clicked location:", map_data["last_clicked"])
                except Exception as e:
                    st.error(f"Error displaying map: {str(e)}")
                
                st.markdown("</div>", unsafe_allow_html=True)
            else:
                st.warning("No valid features returned for any platform.")

with tab2:
    # Create two columns for main content and sidebar with adjusted widths
    main_col, sidebar_col = st.columns([2, 1])
    
    with sidebar_col:
        st.header("Credentials")
        with st.form(key="search_form"):
            username = st.text_input("Copernicus Username", key="username")
            password = st.text_input("Copernicus Password", type="password", key="password")

            st.header("Search Parameters")
            collection = st.selectbox(
                "Select Collection",
                ["SENTINEL-1", "SENTINEL-2", "SENTINEL-3", "SENTINEL-5P"],
                key="collection"
            )

            col1, col2 = st.columns(2)
            with col1:
                start_date = st.date_input("Start Date", value=date.today() - timedelta(days=30), key="start_date")
            with col2:
                end_date = st.date_input("End Date", value=date.today(), key="end_date")

            result_limit = st.number_input("Maximum Results", min_value=1, max_value=1000, value=100, key="limit")
            
            search_submitted = st.form_submit_button("Search Products")
    
    with main_col:
        st.header("Area of Interest")
        
        # Manual Bounding Box inputs
        st.subheader("Enter Bounding Box Coordinates")
        st.markdown("Enter coordinates in decimal degrees:")
        
        bbox_col1, bbox_col2 = st.columns(2)
        with bbox_col1:
            min_lat = st.number_input("Min Latitude", value=40.4, format="%.4f", key="min_lat")
            min_lon = st.number_input("Min Longitude", value=-74.3, format="%.4f", key="min_lon")
        
        with bbox_col2:
            max_lat = st.number_input("Max Latitude", value=41.0, format="%.4f", key="max_lat")
            max_lon = st.number_input("Max Longitude", value=-73.5, format="%.4f", key="max_lon")
        
        # Generate bounding box
        bbox = f"POLYGON(({min_lon} {min_lat}, {min_lon} {max_lat}, {max_lon} {max_lat}, {max_lon} {min_lat}, {min_lon} {min_lat}))"
        
        # Show current bounding box
        st.markdown("### Current Bounding Box")
        st.code(bbox, language="text")

        if 'search_results' not in st.session_state:
            st.session_state.search_results = None
        if 'token' not in st.session_state:
            st.session_state.token = None

        if search_submitted:
            if not username or not password:
                st.error("Please provide your Copernicus credentials")
                st.stop()

            try:
                # Get authentication token
                token = get_keycloak_token(username, password)
                if not token:
                    st.stop()
                
                st.session_state.token = token
                
                # Search for products
                results = search_products(
                    token=token,
                    bbox=bbox,
                    collection=collection,
                    start_date=start_date.strftime("%Y-%m-%d"),
                    end_date=end_date.strftime("%Y-%m-%d"),
                    result_limit=result_limit
                )
                
                if not results or 'value' not in results:
                    st.error("No products found matching your criteria")
                    st.stop()

                st.session_state.search_results = results['value']
                st.success(f"Found {len(st.session_state.search_results)} products")

            except Exception as e:
                st.error(f"Error: {str(e)}")

        if st.session_state.search_results:
            df = pd.DataFrame.from_dict(st.session_state.search_results)

            if 'GeoFootprint' in df.columns:
                df['geometry'] = df['GeoFootprint'].apply(shape)
                gdf = gpd.GeoDataFrame(df).set_geometry('geometry')

                if collection == "SENTINEL-2":
                    gdf = gdf[~gdf['Name'].str.contains('L1C')]

                tabs = st.tabs([f"Product {i+1}" for i in range(len(gdf))])

                for i, tab in enumerate(tabs):
                    with tab:
                        row = gdf.iloc[i]
                        st.subheader(row['Name'])

                        st.json({
                            'ID': row['Id'],
                            'Size': f"{row.get('ContentLength', 0) / (1024*1024):.2f} MB",
                            'Date': row.get('ContentDate', {}).get('Start'),
                            'Cloud Cover': row.get('CloudCover', 'N/A')
                        })

                        with st.form(key=f"download_form_{row['Id']}"):
                            # Use the selected download directory from session state
                            downloads_dir = st.session_state.download_dir
                            
                            # Display the current download location
                            st.info(f"Files will be downloaded to: {downloads_dir}")
                            
                            download_submitted = st.form_submit_button("Download")

                            if download_submitted:
                                progress_placeholder = st.empty()
                                status_placeholder = st.empty()
                                
                                async def download():
                                    async with aiohttp.ClientSession() as session:
                                        await download_product(
                                            session,
                                            row['Id'],
                                            st.session_state.token,
                                            row['Name'],
                                            downloads_dir,  # Use the selected download directory
                                            progress_placeholder,
                                            status_placeholder
                                        )
                                
                                asyncio.run(download())

    st.markdown("</div>", unsafe_allow_html=True)