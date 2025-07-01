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
if 'alaska_search_results' not in st.session_state:
    st.session_state.alaska_search_results = {}
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
                return None, False
        
        # Clean the product name to ensure it's a valid filename
        clean_name = "".join(c for c in product_name if c.isalnum() or c in (' ', '-', '_', '.')).strip()
        output_path = os.path.join(output_dir, f"{clean_name}.zip")
        
        # Check if file already exists
        if os.path.exists(output_path):
            status_placeholder.warning(f"File already exists at: {output_path}")
            # Return the file path and True to indicate file exists
            return output_path, True
        
        # Check write permissions on output directory
        try:
            test_file = os.path.join(output_dir, ".write_test")
            with open(test_file, "w") as f:
                f.write("test")
            os.remove(test_file)
        except Exception as e:
            status_placeholder.error(f"No write permission on directory: {output_dir}. Error: {str(e)}")
            return None, False
            
        headers = {"Authorization": f"Bearer {token}"}
        
        # Get the download URL
        url = f"{COPERNICUS_CATALOG_URL}({product_id})/$value"
        
        # Follow redirects
        async with session.get(url, headers=headers, allow_redirects=False) as response:
            while response.status in (301, 302, 303, 307):
                url = response.headers["Location"]
                response = await session.get(url, headers=headers, allow_redirects=False)
        
        # Get file size using HEAD request
        try:
            async with session.head(url, headers=headers) as response:
                total_size = int(response.headers.get('content-length', 0))
                status_placeholder.write(f"Total size: {total_size / (1024*1024):.2f} MB")
                
                # Check available disk space
                try:
                    import shutil
                    free_space = shutil.disk_usage(output_dir).free
                    if free_space < total_size * 1.2:  # Add 20% buffer
                        status_placeholder.error(f"Not enough disk space. Required: {total_size * 1.2 / (1024*1024*1024):.2f} GB, Available: {free_space / (1024*1024*1024):.2f} GB")
                        return None, False
                except Exception as e:
                    status_placeholder.warning(f"Could not check disk space: {str(e)}")
        except Exception as e:
            # If HEAD request fails, continue anyway and get size during download
            status_placeholder.warning(f"Couldn't determine file size in advance: {str(e)}")
            total_size = 0  # Will be updated during download if available
        
        # Create a temporary file for streaming
        temp_file_path = output_path + ".part"
        
        # Stream download directly to disk to handle large files
        try:
            # Create a BytesIO object for browser download (limited size)
            browser_data = io.BytesIO() if total_size < 200 * 1024 * 1024 and total_size > 0 else None  # Only store in memory if < 200 MB
            can_browser_download = browser_data is not None
            
            downloaded = 0
            async with session.get(url, headers=headers) as response:
                # If we couldn't get the size before, get it now
                if total_size == 0:
                    total_size = int(response.headers.get('content-length', 0))
                    status_placeholder.write(f"Total size: {total_size / (1024*1024):.2f} MB")
                    
                    # Update browser_data decision based on actual size
                    if total_size < 200 * 1024 * 1024 and total_size > 0:
                        browser_data = io.BytesIO()
                        can_browser_download = True
                    else:
                        browser_data = None
                        can_browser_download = False
                
                with open(temp_file_path, 'wb') as f:
                    async for chunk in response.content.iter_chunked(8192):
                        if chunk:
                            # Write chunk to disk
                            f.write(chunk)
                            
                            # Also save to BytesIO if it's not too large
                            if can_browser_download:
                                browser_data.write(chunk)
                            
                            downloaded += len(chunk)
                            progress = (downloaded / total_size) * 100 if total_size > 0 else 0
                            
                            # Update progress less frequently to reduce UI load
                            if int(progress) % 2 == 0 or downloaded == total_size:
                                progress_placeholder.progress(progress / 100)
                                status_placeholder.write(
                                    f"Downloading: {downloaded / (1024*1024):.2f} MB / "
                                    f"{total_size / (1024*1024):.2f} MB ({progress:.1f}%)"
                                )
            
            # Rename temp file to final file
            os.rename(temp_file_path, output_path)
            
            # Prepare browser download if available
            if can_browser_download:
                browser_data.seek(0)
            
            status_placeholder.success(f"Download complete! File saved to: {output_path}")
            
            # Return both the file path and the BytesIO object (or None if too large for browser download)
            return (output_path, browser_data), False
        
        except Exception as e:
            # Clean up temp file if download failed
            if os.path.exists(temp_file_path):
                try:
                    os.remove(temp_file_path)
                except:
                    pass
            status_placeholder.error(f"Error downloading file: {str(e)}")
            return None, False
            
    except Exception as e:
        status_placeholder.error(f"Download failed: {str(e)}")
        return None, False

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
        try:
            # Convert flightDirection to float if it's a string
            flight_direction = props.get('flightDirection', 0)
            if isinstance(flight_direction, str):
                flight_direction = float(flight_direction)
            direction = bearing_to_direction(flight_direction)
            st.markdown(f"**Flight Direction:** {direction} ({flight_direction}°)")
        except (ValueError, TypeError):
            # If conversion fails, just display the raw value
            st.markdown(f"**Flight Direction:** {props.get('flightDirection', 'N/A')}")
    
    if 'fileSize' in props:
        size_mb = props.get('fileSize', 0) / (1024 * 1024)
        st.markdown(f"**File Size:** {size_mb:.2f} MB")
    elif 'bytes' in props:
        # Use bytes field if fileSize is not available
        size_mb = int(props.get('bytes', 0)) / (1024 * 1024)
        st.markdown(f"**File Size:** {size_mb:.2f} MB")
    
    # Simple download button using direct URL
    download_url = props.get('url', '')
    if download_url:
        file_name = props.get('fileName', props.get('sceneName', 'download') + '.zip')
        
        # Create download link using HTML
        st.markdown(f"""
            <div style="margin: 1rem 0;">
                <a href="{download_url}" download="{file_name}" target="_blank" style="text-decoration: none;">
                    <button class="download-btn">
                        Download {props.get('sceneName', 'File')}
                    </button>
                </a>
            </div>
        """, unsafe_allow_html=True)
        
        # Also show the direct URL for reference
        with st.expander("Show Download URL"):
            st.code(download_url, language="text")
    else:
        st.warning("Download URL not available for this product")

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
    /* Import Google Fonts */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
    
    /* Root variables for consistent theming */
    :root {
        --primary-color: #3b82f6;
        --primary-hover: #2563eb;
        --secondary-color: #6366f1;
        --background-color: #f8fafc;
        --surface-color: #ffffff;
        --text-primary: #1e293b;
        --text-secondary: #64748b;
        --text-muted: #94a3b8;
        --border-color: #e2e8f0;
        --success-color: #10b981;
        --warning-color: #f59e0b;
        --error-color: #ef4444;
        --shadow-sm: 0 1px 2px 0 rgba(0, 0, 0, 0.05);
        --shadow-md: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
        --shadow-lg: 0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -2px rgba(0, 0, 0, 0.05);
        --radius-sm: 6px;
        --radius-md: 8px;
        --radius-lg: 12px;
        --spacing-xs: 0.25rem;
        --spacing-sm: 0.5rem;
        --spacing-md: 1rem;
        --spacing-lg: 1.5rem;
        --spacing-xl: 2rem;
    }
    
    /* Global font family */
    .stApp, .stApp * {
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif !important;
    }
    
    /* Main container styling */
    .main {
        background-color: var(--background-color);
        padding: var(--spacing-xl);
        min-height: 100vh;
    }
    
    /* Header styling */
    .stApp header {
        background-color: var(--surface-color);
        box-shadow: var(--shadow-sm);
        border-bottom: 1px solid var(--border-color);
    }
    
    /* Title styling */
    h1 {
        color: var(--text-primary) !important;
        font-size: 2.5rem !important;
        font-weight: 700 !important;
        margin-bottom: var(--spacing-lg) !important;
        letter-spacing: -0.025em !important;
    }
    
    h2 {
        color: var(--text-primary) !important;
        font-size: 1.875rem !important;
        font-weight: 600 !important;
        margin-bottom: var(--spacing-md) !important;
    }
    
    h3 {
        color: var(--text-primary) !important;
        font-size: 1.5rem !important;
        font-weight: 600 !important;
        margin-bottom: var(--spacing-md) !important;
    }
    
    /* Card styling */
    .card {
        background-color: var(--surface-color);
        border-radius: var(--radius-lg);
        padding: var(--spacing-xl);
        margin-bottom: var(--spacing-lg);
        box-shadow: var(--shadow-md);
        border: 1px solid var(--border-color);
        transition: all 0.2s ease-in-out;
    }
    
    .card:hover {
        box-shadow: var(--shadow-lg);
        transform: translateY(-1px);
    }
    
    /* Button styling */
    .stButton button {
        background: linear-gradient(135deg, var(--primary-color), var(--secondary-color)) !important;
        color: white !important;
        border-radius: var(--radius-md) !important;
        padding: var(--spacing-sm) var(--spacing-lg) !important;
        font-weight: 600 !important;
        font-size: 0.875rem !important;
        border: none !important;
        transition: all 0.2s ease-in-out !important;
        box-shadow: var(--shadow-sm) !important;
        letter-spacing: 0.025em !important;
    }
    
    .stButton button:hover {
        transform: translateY(-2px) !important;
        box-shadow: var(--shadow-md) !important;
        background: linear-gradient(135deg, var(--primary-hover), var(--secondary-color)) !important;
    }
    
    .stButton button:active {
        transform: translateY(0) !important;
    }
    
    /* Custom download button */
    .download-btn {
        background: linear-gradient(135deg, var(--success-color), #059669);
        color: white;
        border-radius: var(--radius-md);
        padding: var(--spacing-sm) var(--spacing-lg);
        font-weight: 600;
        font-size: 0.875rem;
        border: none;
        cursor: pointer;
        transition: all 0.2s ease-in-out;
        box-shadow: var(--shadow-sm);
        letter-spacing: 0.025em;
        text-decoration: none;
        display: inline-block;
    }
    
    .download-btn:hover {
        transform: translateY(-2px);
        box-shadow: var(--shadow-md);
        background: linear-gradient(135deg, #059669, #047857);
    }
    
    /* Input field styling */
    .stTextInput input, .stNumberInput input, .stSelectbox select {
        border-radius: var(--radius-md) !important;
        border: 2px solid var(--border-color) !important;
        padding: var(--spacing-sm) var(--spacing-md) !important;
        font-size: 0.875rem !important;
        transition: all 0.2s ease-in-out !important;
        background-color: var(--surface-color) !important;
    }
    
    .stTextInput input:focus, .stNumberInput input:focus, .stSelectbox select:focus {
        border-color: var(--primary-color) !important;
        box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.1) !important;
        outline: none !important;
    }
    
    /* Date input styling */
    .stDateInput input {
        border-radius: var(--radius-md) !important;
        border: 2px solid var(--border-color) !important;
        padding: var(--spacing-sm) var(--spacing-md) !important;
        font-size: 0.875rem !important;
        transition: all 0.2s ease-in-out !important;
    }
    
    /* Map container styling */
    .map-container {
        border-radius: var(--radius-lg);
        overflow: hidden;
        box-shadow: var(--shadow-lg);
        border: 1px solid var(--border-color);
        margin: var(--spacing-lg) 0;
    }
    
    /* Expander styling */
    .streamlit-expanderHeader {
        background-color: var(--background-color) !important;
        border-radius: var(--radius-md) !important;
        border: 1px solid var(--border-color) !important;
        padding: var(--spacing-md) !important;
        font-weight: 500 !important;
        color: var(--text-primary) !important;
        transition: all 0.2s ease-in-out !important;
    }
    
    .streamlit-expanderHeader:hover {
        background-color: var(--surface-color) !important;
        box-shadow: var(--shadow-sm) !important;
    }
    
    .streamlit-expanderContent {
        background-color: var(--surface-color) !important;
        border: 1px solid var(--border-color) !important;
        border-top: none !important;
        border-radius: 0 0 var(--radius-md) var(--radius-md) !important;
        padding: var(--spacing-lg) !important;
    }
    
    /* Tab styling */
    .stTabs [data-baseweb="tab-list"] {
        gap: var(--spacing-md);
        background-color: var(--background-color);
        padding: var(--spacing-sm);
        border-radius: var(--radius-lg);
        border: 1px solid var(--border-color);
    }
    
    .stTabs [data-baseweb="tab"] {
        padding: var(--spacing-md) var(--spacing-lg) !important;
        border-radius: var(--radius-md) !important;
        font-weight: 500 !important;
        transition: all 0.2s ease-in-out !important;
        border: 1px solid transparent !important;
    }
    
    .stTabs [data-baseweb="tab"]:hover {
        background-color: var(--surface-color) !important;
        border-color: var(--border-color) !important;
    }
    
    .stTabs [data-baseweb="tab"][aria-selected="true"] {
        background-color: var(--primary-color) !important;
        color: white !important;
        box-shadow: var(--shadow-sm) !important;
    }
    
    /* Sidebar styling */
    .css-1d391kg {
        background-color: var(--surface-color) !important;
        border-right: 1px solid var(--border-color) !important;
    }
    
    /* Form styling */
    .stForm {
        background-color: var(--surface-color) !important;
        border: 1px solid var(--border-color) !important;
        border-radius: var(--radius-lg) !important;
        padding: var(--spacing-lg) !important;
        margin: var(--spacing-md) 0 !important;
    }
    
    /* Loading spinner styling */
    .stSpinner > div {
        border-color: var(--primary-color) var(--border-color) var(--border-color) var(--border-color) !important;
    }
    
    /* Alert styling */
    .stAlert {
        border-radius: var(--radius-md) !important;
        border: none !important;
        box-shadow: var(--shadow-sm) !important;
        margin: var(--spacing-md) 0 !important;
    }
    
    .stSuccess {
        background-color: #f0fdf4 !important;
        color: #166534 !important;
        border-left: 4px solid var(--success-color) !important;
    }
    
    .stError {
        background-color: #fef2f2 !important;
        color: #991b1b !important;
        border-left: 4px solid var(--error-color) !important;
    }
    
    .stWarning {
        background-color: #fffbeb !important;
        color: #92400e !important;
        border-left: 4px solid var(--warning-color) !important;
    }
    
    .stInfo {
        background-color: #eff6ff !important;
        color: #1e40af !important;
        border-left: 4px solid var(--primary-color) !important;
    }
    
    /* User info bar */
    .user-info-bar {
        background: linear-gradient(135deg, var(--surface-color), #f1f5f9);
        padding: var(--spacing-md) var(--spacing-lg);
        border-radius: var(--radius-lg);
        margin-bottom: var(--spacing-lg);
        display: flex;
        justify-content: space-between;
        align-items: center;
        font-size: 0.875rem;
        font-weight: 500;
        color: var(--text-secondary);
        border: 1px solid var(--border-color);
        box-shadow: var(--shadow-sm);
    }
    
    /* Code block styling */
    .stCodeBlock {
        background-color: #1e293b !important;
        border-radius: var(--radius-md) !important;
        padding: var(--spacing-lg) !important;
        margin: var(--spacing-md) 0 !important;
        border: 1px solid #334155 !important;
    }
    
    /* Progress bar styling */
    .stProgress > div > div {
        background-color: var(--primary-color) !important;
        border-radius: var(--radius-sm) !important;
    }
    
    .stProgress > div {
        background-color: var(--border-color) !important;
        border-radius: var(--radius-sm) !important;
    }
    
    /* JSON viewer styling */
    .stJson {
        background-color: var(--background-color) !important;
        border: 1px solid var(--border-color) !important;
        border-radius: var(--radius-md) !important;
        padding: var(--spacing-md) !important;
    }
    
    /* Checkbox styling */
    .stCheckbox {
        margin: var(--spacing-sm) 0 !important;
    }
    
    /* Metric styling */
    .metric-container {
        background-color: var(--surface-color);
        border: 1px solid var(--border-color);
        border-radius: var(--radius-lg);
        padding: var(--spacing-lg);
        margin: var(--spacing-md);
        box-shadow: var(--shadow-sm);
        text-align: center;
    }
    
    /* Custom scrollbar */
    ::-webkit-scrollbar {
        width: 8px;
        height: 8px;
    }
    
    ::-webkit-scrollbar-track {
        background: var(--background-color);
        border-radius: var(--radius-sm);
    }
    
    ::-webkit-scrollbar-thumb {
        background: var(--border-color);
        border-radius: var(--radius-sm);
    }
    
    ::-webkit-scrollbar-thumb:hover {
        background: var(--text-muted);
    }
    
    /* Responsive design */
    @media (max-width: 768px) {
        .main {
            padding: var(--spacing-md);
        }
        
        .card {
            padding: var(--spacing-lg);
        }
        
        h1 {
            font-size: 2rem !important;
        }
        
        .user-info-bar {
            flex-direction: column;
            gap: var(--spacing-sm);
            text-align: center;
        }
    }
    
    /* Animation keyframes */
    @keyframes fadeInUp {
        from {
            opacity: 0;
            transform: translateY(20px);
        }
        to {
            opacity: 1;
            transform: translateY(0);
        }
    }
    
    .card {
        animation: fadeInUp 0.3s ease-out;
    }
    
    /* Focus states for accessibility */
    button:focus, input:focus, select:focus {
        outline: 2px solid var(--primary-color) !important;
        outline-offset: 2px !important;
    }
    </style>
""", unsafe_allow_html=True)

# Main title with modern styling
st.markdown("""
    <div style='text-align: center; margin-bottom: 2rem;'>
        <h1 style='color: var(--text-primary); font-size: 2.5rem; font-weight: 700; margin-bottom: 0.5rem;'>
            Satellite Data Explorer
        </h1>
        <p style='color: var(--text-secondary); font-size: 1.1rem; font-weight: 400; margin: 0;'>
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
            <h2 style='color: var(--text-primary); font-size: 1.5rem; margin-bottom: 1rem;'>
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
            # Clear previous results
            st.session_state.alaska_search_results = {}
            
            any_valid_features = False
            all_features = []
            
            for platform in platforms:
                data = fetch_data(platform, latitude, longitude, start_date, end_date, result_limit)
                if data and 'features' in data and data['features']:
                    st.session_state.alaska_search_results[platform] = data['features']
                    any_valid_features = True
                    all_features.extend(data['features'])
            
            if any_valid_features:
                st.session_state.alaska_search_results['_all_features'] = all_features
                st.session_state.alaska_search_results['_center_coords'] = {
                    'lat': latitude,
                    'lon': longitude
                }
        st.rerun()
    
    # Display stored results
    if st.session_state.alaska_search_results:
        # Display individual platform results
        for platform, features in st.session_state.alaska_search_results.items():
            if platform.startswith('_'):  # Skip metadata keys
                continue
                
            st.markdown(f"""
                <div class='card'>
                    <h3 style='color: var(--text-primary); font-size: 1.25rem; margin-bottom: 1rem;'>
                        {platform}
                    </h3>
            """, unsafe_allow_html=True)
            
            for feature in features:
                with st.expander(f"Scene: {feature['properties'].get('sceneName', 'Unknown')}", expanded=False):
                    display_alaska_feature_info(feature)
                    
                    if st.checkbox("Show Raw Data", key=f"raw_{feature['properties'].get('fileID', str(uuid.uuid4()))}"):
                        st.json(feature)
            
            st.markdown("</div>", unsafe_allow_html=True)
        
        # Display map if we have features
        if '_all_features' in st.session_state.alaska_search_results:
            st.markdown("""
                <div class='card'>
                    <h3 style='color: var(--text-primary); font-size: 1.25rem; margin-bottom: 1rem;'>
                        Coverage Map
                    </h3>
            """, unsafe_allow_html=True)
            
            try:
                all_features = st.session_state.alaska_search_results['_all_features']
                center_coords = st.session_state.alaska_search_results['_center_coords']
                
                center_lat = float(all_features[0]['properties'].get('centerLat', center_coords['lat']))
                center_lon = float(all_features[0]['properties'].get('centerLon', center_coords['lon']))
                
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
                        
                        # Store download status in session state
                        download_key = f"download_{row['Id']}"
                        if download_key not in st.session_state:
                            st.session_state[download_key] = {
                                'started': False,
                                'completed': False,
                                'file_data': None,
                                'file_name': None
                            }

                        # Form for initiating download
                        with st.form(key=f"download_form_{row['Id']}"):
                            # Use the selected download directory from session state
                            downloads_dir = st.session_state.download_dir
                            
                            # Display the current download location
                            st.info(f"Files will be downloaded to: {downloads_dir}")
                            
                            download_submitted = st.form_submit_button("Start Download")

                        # Handle download process outside the form
                        download_state = st.session_state[download_key]
                        
                        if download_submitted:
                            download_state['started'] = True
                            progress_placeholder = st.empty()
                            status_placeholder = st.empty()
                            
                            # Define the async function outside
                            async def perform_download():
                                async with aiohttp.ClientSession() as session:
                                    result, file_exists = await download_product(
                                        session,
                                        row['Id'],
                                        st.session_state.token,
                                        row['Name'],
                                        downloads_dir,
                                        progress_placeholder,
                                        status_placeholder
                                    )
                                    
                                    # Store result in session state for later use
                                    if result and not file_exists:
                                        output_path, file_data = result
                                        file_name = os.path.basename(output_path)
                                        download_state['completed'] = True
                                        download_state['file_data'] = file_data
                                        download_state['file_name'] = file_name
                            
                            # Run the async function
                            asyncio.run(perform_download())
                        
                        # Display download button outside the form if download is complete
                        if download_state['completed'] and download_state['file_data'] is not None:
                            st.success("Download complete! You can now save the file to your browser.")
                            st.download_button(
                                label="Download to browser",
                                data=download_state['file_data'],
                                file_name=download_state['file_name'],
                                mime="application/zip",
                                key=f"browser_download_{row['Id']}"
                            )
                        elif download_state['completed'] and download_state['file_data'] is None:
                            st.success("Download complete! File was saved to your local disk.")
                            st.info(f"The file was too large to enable browser download. Please check the download directory: {downloads_dir}")

    st.markdown("</div>", unsafe_allow_html=True)