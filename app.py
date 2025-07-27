import streamlit as st
import json
import uuid
from api import fetch_data
from utils import process_response, bearing_to_direction
from shapely.geometry import Point, Polygon, shape
import requests
import dotenv
import os
import folium
from streamlit_folium import st_folium
import numpy as np
import pandas as pd
from datetime import date, datetime, timedelta
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

# Current date, time and user info
current_datetime = "2025-04-25 13:56:21"  # UTC
current_user = "SauravHaldar04"

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

# Custom CSS for modern UI with enhanced theming
st.markdown("""
    <style>
    /* Import Google Fonts */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap');
    
    /* Enhanced root variables for theming */
    :root {
        --primary-color: #667eea;
        --primary-hover: #5a6fd8;
        --primary-light: #f0f2ff;
        --secondary-color: #764ba2;
        --accent-color: #f093fb;
        --success-color: #4ade80;
        --warning-color: #fbbf24;
        --error-color: #f87171;
        --info-color: #60a5fa;
        
        --background-color: #fafaff;
        --surface-color: #ffffff;
        --surface-hover: #f8fafc;
        --surface-dark: #f1f5f9;
        
        --text-primary: #1e293b;
        --text-secondary: #475569;
        --text-muted: #94a3b8;
        --text-light: #cbd5e1;
        
        --border-color: #e2e8f0;
        --border-light: #f1f5f9;
        --border-dark: #cbd5e1;
        
        --shadow-xs: 0 1px 2px 0 rgba(0, 0, 0, 0.02);
        --shadow-sm: 0 1px 3px 0 rgba(0, 0, 0, 0.08), 0 1px 2px 0 rgba(0, 0, 0, 0.02);
        --shadow-md: 0 4px 6px -1px rgba(0, 0, 0, 0.08), 0 2px 4px -1px rgba(0, 0, 0, 0.04);
        --shadow-lg: 0 10px 15px -3px rgba(0, 0, 0, 0.08), 0 4px 6px -2px rgba(0, 0, 0, 0.04);
        --shadow-xl: 0 20px 25px -5px rgba(0, 0, 0, 0.08), 0 10px 10px -5px rgba(0, 0, 0, 0.02);
        
        --radius-xs: 4px;
        --radius-sm: 6px;
        --radius-md: 8px;
        --radius-lg: 12px;
        --radius-xl: 16px;
        --radius-2xl: 20px;
        
        --spacing-xs: 0.25rem;
        --spacing-sm: 0.5rem;
        --spacing-md: 1rem;
        --spacing-lg: 1.5rem;
        --spacing-xl: 2rem;
        --spacing-2xl: 3rem;
        
        --transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
        --transition-fast: all 0.15s cubic-bezier(0.4, 0, 0.2, 1);
    }
    
    /* Dark mode variables */
    @media (prefers-color-scheme: dark) {
        :root {
            --background-color: #0f172a;
            --surface-color: #1e293b;
            --surface-hover: #334155;
            --surface-dark: #0f172a;
            --text-primary: #f8fafc;
            --text-secondary: #cbd5e1;
            --text-muted: #64748b;
            --border-color: #334155;
            --border-light: #475569;
        }
    }
    
    /* Global font and base styling */
    .stApp, .stApp * {
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif !important;
    }
    
    /* Main container styling */
    .main {
        background: linear-gradient(135deg, var(--background-color) 0%, #f8faff 100%);
        padding: var(--spacing-md);
        min-height: 100vh;
    }
    
    /* Enhanced navbar styling */
    .navbar {
        background: linear-gradient(135deg, var(--primary-color) 0%, var(--secondary-color) 50%, var(--accent-color) 100%);
        color: white;
        padding: var(--spacing-lg) var(--spacing-xl);
        border-radius: var(--radius-xl);
        margin-bottom: var(--spacing-lg);
        display: flex;
        justify-content: space-between;
        align-items: center;
        box-shadow: var(--shadow-lg);
        position: sticky;
        top: var(--spacing-md);
        z-index: 1000;
        backdrop-filter: blur(10px);
        border: 1px solid rgba(255, 255, 255, 0.1);
    }
    
    .navbar::before {
        content: '';
        position: absolute;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        background: linear-gradient(135deg, rgba(255,255,255,0.1) 0%, rgba(255,255,255,0.05) 100%);
        border-radius: var(--radius-xl);
        pointer-events: none;
    }
    
    .navbar-brand {
        font-size: 1.75rem;
        font-weight: 800;
        margin: 0;
        text-shadow: 0 2px 4px rgba(0,0,0,0.1);
        display: flex;
        align-items: center;
        gap: var(--spacing-sm);
    }
    
    .navbar-description {
        font-size: 1rem;
        opacity: 0.9;
        margin: var(--spacing-xs) 0 0 0;
        font-weight: 400;
        letter-spacing: 0.025em;
    }
    
    .navbar-user-info {
        font-size: 0.875rem;
        opacity: 0.95;
        text-align: right;
        background: rgba(255,255,255,0.1);
        padding: var(--spacing-sm) var(--spacing-md);
        border-radius: var(--radius-lg);
        backdrop-filter: blur(5px);
        border: 1px solid rgba(255,255,255,0.2);
    }
    
    /* Enhanced card styling */
    .card {
        background: var(--surface-color);
        border-radius: var(--radius-xl);
        padding: var(--spacing-xl);
        margin-bottom: var(--spacing-lg);
        box-shadow: var(--shadow-md);
        border: 1px solid var(--border-color);
        transition: var(--transition);
        position: relative;
        overflow: hidden;
    }
    
    .card::before {
        content: '';
        position: absolute;
        top: 0;
        left: 0;
        right: 0;
        height: 3px;
        background: linear-gradient(90deg, var(--primary-color), var(--accent-color));
        border-radius: var(--radius-xl) var(--radius-xl) 0 0;
    }
    
    .card:hover {
        box-shadow: var(--shadow-lg);
        transform: translateY(-2px);
        border-color: var(--border-dark);
    }
    
    /* Compact card variant */
    .compact-card {
        background: var(--surface-color);
        border-radius: var(--radius-lg);
        padding: var(--spacing-lg);
        margin-bottom: var(--spacing-md);
        box-shadow: var(--shadow-sm);
        border: 1px solid var(--border-color);
        transition: var(--transition);
        position: relative;
    }
    
    .compact-card::before {
        content: '';
        position: absolute;
        top: 0;
        left: 0;
        right: 0;
        height: 2px;
        background: linear-gradient(90deg, var(--primary-color), var(--secondary-color));
        border-radius: var(--radius-lg) var(--radius-lg) 0 0;
    }
    
    .compact-card:hover {
        box-shadow: var(--shadow-md);
        transform: translateY(-1px);
        background: var(--surface-hover);
    }
    
    /* Enhanced button styling */
    .stButton button {
        background: linear-gradient(135deg, var(--primary-color), var(--secondary-color)) !important;
        color: white !important;
        border-radius: var(--radius-lg) !important;
        padding: var(--spacing-md) var(--spacing-xl) !important;
        font-weight: 600 !important;
        font-size: 0.875rem !important;
        border: none !important;
        transition: var(--transition) !important;
        box-shadow: var(--shadow-sm) !important;
        letter-spacing: 0.025em !important;
        position: relative !important;
        overflow: hidden !important;
    }
    
    .stButton button::before {
        content: '' !important;
        position: absolute !important;
        top: 0 !important;
        left: -100% !important;
        width: 100% !important;
        height: 100% !important;
        background: linear-gradient(90deg, transparent, rgba(255,255,255,0.2), transparent) !important;
        transition: left 0.5s !important;
    }
    
    .stButton button:hover::before {
        left: 100% !important;
    }
    
    .stButton button:hover {
        transform: translateY(-2px) !important;
        box-shadow: var(--shadow-lg) !important;
        background: linear-gradient(135deg, var(--primary-hover), var(--secondary-color)) !important;
    }
    
    /* Enhanced download button */
    .download-btn {
        background: linear-gradient(135deg, var(--success-color), #22c55e);
        color: white;
        border-radius: var(--radius-lg);
        padding: var(--spacing-sm) var(--spacing-lg);
        font-weight: 600;
        font-size: 0.875rem;
        border: none;
        cursor: pointer;
        transition: var(--transition);
        box-shadow: var(--shadow-sm);
        letter-spacing: 0.025em;
        text-decoration: none;
        display: inline-flex;
        align-items: center;
        gap: var(--spacing-xs);
        position: relative;
        overflow: hidden;
    }
    
    .download-btn::before {
        content: '';
        position: absolute;
        top: 0;
        left: -100%;
        width: 100%;
        height: 100%;
        background: linear-gradient(90deg, transparent, rgba(255,255,255,0.2), transparent);
        transition: left 0.5s;
    }
    
    .download-btn:hover::before {
        left: 100%;
    }
    
    .download-btn:hover {
        transform: translateY(-2px);
        box-shadow: var(--shadow-md);
        background: linear-gradient(135deg, #22c55e, #16a34a);
    }
    
    /* Enhanced input styling */
    .stTextInput input, .stNumberInput input, .stSelectbox select, .stDateInput input {
        border-radius: var(--radius-lg) !important;
        border: 2px solid var(--border-color) !important;
        padding: var(--spacing-md) var(--spacing-lg) !important;
        font-size: 0.875rem !important;
        transition: var(--transition) !important;
        background-color: var(--surface-color) !important;
        font-family: 'Inter', sans-serif !important;
        font-weight: 500 !important;
    }
    
    .stTextInput input:focus, .stNumberInput input:focus, .stSelectbox select:focus, .stDateInput input:focus {
        border-color: var(--primary-color) !important;
        box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1) !important;
        outline: none !important;
        background-color: var(--surface-hover) !important;
    }
    
    /* Enhanced form styling */
    .stForm {
        background-color: var(--surface-color) !important;
        border: 1px solid var(--border-color) !important;
        border-radius: var(--radius-xl) !important;
        padding: var(--spacing-xl) !important;
        margin: var(--spacing-md) 0 !important;
        box-shadow: var(--shadow-sm) !important;
        position: relative !important;
    }
    
    .stForm::before {
        content: '';
        position: absolute;
        top: 0;
        left: 0;
        right: 0;
        height: 2px;
        background: linear-gradient(90deg, var(--primary-color), var(--accent-color));
        border-radius: var(--radius-xl) var(--radius-xl) 0 0;
    }
    
    /* Enhanced expander styling */
    .streamlit-expanderHeader {
        background: linear-gradient(135deg, var(--surface-color), var(--surface-hover)) !important;
        border-radius: var(--radius-lg) !important;
        border: 1px solid var(--border-color) !important;
        padding: var(--spacing-md) var(--spacing-lg) !important;
        font-weight: 600 !important;
        color: var(--text-primary) !important;
        transition: var(--transition) !important;
        box-shadow: var(--shadow-xs) !important;
    }
    
    .streamlit-expanderHeader:hover {
        background: linear-gradient(135deg, var(--surface-hover), var(--surface-dark)) !important;
        box-shadow: var(--shadow-sm) !important;
        transform: translateY(-1px) !important;
    }
    
    .streamlit-expanderContent {
        background-color: var(--surface-color) !important;
        border: 1px solid var(--border-color) !important;
        border-top: none !important;
        border-radius: 0 0 var(--radius-lg) var(--radius-lg) !important;
        padding: var(--spacing-lg) !important;
        box-shadow: var(--shadow-xs) !important;
    }
    
    /* Enhanced sidebar styling */
    .css-1d391kg {
        background: linear-gradient(180deg, var(--surface-color) 0%, var(--surface-hover) 100%) !important;
        border-right: 2px solid var(--border-color) !important;
        box-shadow: var(--shadow-sm) !important;
    }
    
    /* Enhanced alert styling */
    .stAlert {
        border-radius: var(--radius-lg) !important;
        border: none !important;
        box-shadow: var(--shadow-sm) !important;
        margin: var(--spacing-md) 0 !important;
        font-weight: 500 !important;
        letter-spacing: 0.025em !important;
    }
    
    .stSuccess {
        background: linear-gradient(135deg, #ecfdf5, #f0fdf4) !important;
        color: #166534 !important;
        border-left: 4px solid var(--success-color) !important;
    }
    
    .stError {
        background: linear-gradient(135deg, #fef2f2, #fef7f7) !important;
        color: #991b1b !important;
        border-left: 4px solid var(--error-color) !important;
    }
    
    .stWarning {
        background: linear-gradient(135deg, #fffbeb, #fefce8) !important;
        color: #92400e !important;
        border-left: 4px solid var(--warning-color) !important;
    }
    
    .stInfo {
        background: linear-gradient(135deg, #eff6ff, #f0f9ff) !important;
        color: #1e40af !important;
        border-left: 4px solid var(--info-color) !important;
    }
    
    /* Enhanced metric styling */
    .metric-container {
        background: linear-gradient(135deg, var(--surface-color), var(--surface-hover));
        border: 1px solid var(--border-color);
        border-radius: var(--radius-lg);
        padding: var(--spacing-lg);
        margin: var(--spacing-md);
        box-shadow: var(--shadow-sm);
        text-align: center;
        transition: var(--transition);
    }
    
    .metric-container:hover {
        transform: translateY(-2px);
        box-shadow: var(--shadow-md);
    }
    
    /* Enhanced map container */
    .map-container {
        border-radius: var(--radius-xl);
        overflow: hidden;
        box-shadow: var(--shadow-lg);
        border: 2px solid var(--border-color);
        margin: var(--spacing-lg) 0;
        transition: var(--transition);
    }
    
    .map-container:hover {
        box-shadow: var(--shadow-xl);
        border-color: var(--primary-color);
    }
    
    /* Platform badges */
    .platform-badge {
        background: linear-gradient(135deg, var(--primary-color), var(--secondary-color));
        color: white;
        padding: var(--spacing-sm) var(--spacing-md);
        border-radius: var(--radius-lg);
        margin: var(--spacing-sm) 0;
        font-weight: 600;
        text-align: center;
        box-shadow: var(--shadow-sm);
        transition: var(--transition);
        border: 1px solid rgba(255,255,255,0.2);
    }
    
    .platform-badge:hover {
        transform: translateY(-1px);
        box-shadow: var(--shadow-md);
    }
    
    /* Enhanced scrollbar */
    ::-webkit-scrollbar {
        width: 8px;
        height: 8px;
    }
    
    ::-webkit-scrollbar-track {
        background: var(--border-light);
        border-radius: var(--radius-sm);
    }
    
    ::-webkit-scrollbar-thumb {
        background: linear-gradient(135deg, var(--primary-color), var(--secondary-color));
        border-radius: var(--radius-sm);
    }
    
    ::-webkit-scrollbar-thumb:hover {
        background: linear-gradient(135deg, var(--primary-hover), var(--secondary-color));
    }
    
    /* Enhanced progress bar */
    .stProgress > div > div {
        background: linear-gradient(90deg, var(--primary-color), var(--accent-color)) !important;
        border-radius: var(--radius-sm) !important;
    }
    
    .stProgress > div {
        background-color: var(--border-color) !important;
        border-radius: var(--radius-sm) !important;
    }
    
    /* Animated background elements */
    .main::before {
        content: '';
        position: fixed;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        background: 
            radial-gradient(circle at 20% 80%, rgba(102, 126, 234, 0.1) 0%, transparent 50%),
            radial-gradient(circle at 80% 20%, rgba(240, 147, 251, 0.1) 0%, transparent 50%),
            radial-gradient(circle at 40% 40%, rgba(118, 75, 162, 0.05) 0%, transparent 50%);
        pointer-events: none;
        z-index: -1;
    }
    
    /* Responsive design */
    @media (max-width: 768px) {
        .main {
            padding: var(--spacing-sm);
        }
        
        .navbar {
            flex-direction: column;
            gap: var(--spacing-md);
            text-align: center;
            padding: var(--spacing-lg);
        }
        
        .navbar-brand {
            font-size: 1.5rem;
        }
        
        .navbar-description {
            font-size: 0.875rem;
        }
        
        .card, .compact-card {
            padding: var(--spacing-lg);
        }
    }
    
    /* Animation keyframes */
    @keyframes fadeInUp {
        from {
            opacity: 0;
            transform: translateY(30px);
        }
        to {
            opacity: 1;
            transform: translateY(0);
        }
    }
    
    @keyframes slideInLeft {
        from {
            opacity: 0;
            transform: translateX(-30px);
        }
        to {
            opacity: 1;
            transform: translateX(0);
        }
    }
    
    @keyframes pulse {
        0%, 100% {
            opacity: 1;
        }
        50% {
            opacity: 0.8;
        }
    }
    
    .card {
        animation: fadeInUp 0.6s ease-out;
    }
    
    .compact-card {
        animation: slideInLeft 0.4s ease-out;
    }
    
    .navbar {
        animation: fadeInUp 0.8s ease-out;
    }
    
    /* Loading states */
    .loading {
        animation: pulse 2s infinite;
    }
    
    /* Focus states for accessibility */
    button:focus, input:focus, select:focus {
        outline: 2px solid var(--primary-color) !important;
        outline-offset: 2px !important;
    }
    </style>
""", unsafe_allow_html=True)

st.set_page_config(layout="wide")

# Enhanced navbar with better styling
st.markdown(f"""
    <div class='navbar'>
        <div>
            <div class='navbar-brand'>
                <span>🛰️</span>
                <span>Satellite Data Explorer</span>
            </div>
            <div class='navbar-description'>Explore and download satellite imagery from Alaska Satellite Facility</div>
        </div>
        
    </div>
""", unsafe_allow_html=True)

# Sidebar content with enhanced styling
with st.sidebar:
    st.markdown("""
        <div style='height: 50px; overflow-y: auto;'>
            <h3 style='color: var(--text-primary); font-size: 1.25rem; margin-bottom: 0rem; display: flex; align-items: center; gap: 0.5rem;'>
                <span>🔍</span> Search Results
            </h3>
    """, unsafe_allow_html=True)
    
    # Display results with enhanced styling
    if st.session_state.alaska_search_results:
        serial_number = 1
        
        for platform, features in st.session_state.alaska_search_results.items():
            if platform.startswith('_'):
                continue
            
            if features:
                st.markdown(f"""
                    <div class='platform-badge'>
                        📡 {platform} ({len(features)} results)
                    </div>
                """, unsafe_allow_html=True)
                
                for feature in features:
                    scene_name = feature['properties'].get('sceneName', 'Unknown')
                    start_time = feature['properties'].get('startTime', 'N/A')
                    
                    with st.expander(f"#{serial_number:02d} • {scene_name[:25]}{'...' if len(scene_name) > 25 else ''}", expanded=False):
                        # Enhanced info display
                        st.markdown(f"""
                            <div style='font-size: 0.9rem; line-height: 1.6; background: var(--surface-hover); padding: var(--spacing-md); border-radius: var(--radius-lg); margin-bottom: var(--spacing-md);'>
                                <strong>🛰️ Scene:</strong> {scene_name}<br>
                                <strong>📅 Date:</strong> {start_time}<br>
                                <strong>🔧 Platform:</strong> {feature['properties'].get('platform', 'N/A')}<br>
                                <strong>📊 Sensor:</strong> {feature['properties'].get('sensor', 'N/A')}
                            </div>
                        """, unsafe_allow_html=True)
                        
                        # File size info with enhanced error handling
                        try:
                            if 'fileSize' in feature['properties']:
                                file_size = feature['properties'].get('fileSize', 0)
                                if isinstance(file_size, (int, float)):
                                    size_mb = file_size / (1024 * 1024)
                                    if size_mb >= 1024:
                                        size_gb = size_mb / 1024
                                        st.markdown(f"**📦 Size:** {size_gb:.2f} GB")
                                    else:
                                        st.markdown(f"**📦 Size:** {size_mb:.2f} MB")
                            elif 'bytes' in feature['properties']:
                                bytes_field = feature['properties'].get('bytes', 0)
                                if isinstance(bytes_field, dict):
                                    size_bytes = bytes_field.get('value', 0) if 'value' in bytes_field else 0
                                elif isinstance(bytes_field, (int, float)):
                                    size_bytes = bytes_field
                                elif isinstance(bytes_field, str) and bytes_field.isdigit():
                                    size_bytes = int(bytes_field)
                                else:
                                    size_bytes = 0
                                
                                if size_bytes > 0:
                                    size_mb = size_bytes / (1024 * 1024)
                                    if size_mb >= 1024:
                                        size_gb = size_mb / 1024
                                        st.markdown(f"**📦 Size:** {size_gb:.2f} GB")
                                    else:
                                        st.markdown(f"**📦 Size:** {size_mb:.2f} MB")
                        except (ValueError, TypeError, KeyError):
                            st.markdown("**📦 Size:** N/A")
                        
                        # Flight direction with enhanced display
                        if 'flightDirection' in feature['properties']:
                            try:
                                flight_direction = feature['properties'].get('flightDirection', 0)
                                if isinstance(flight_direction, str):
                                    flight_direction = float(flight_direction)
                                direction = bearing_to_direction(flight_direction)
                                st.markdown(f"**🧭 Direction:** {direction} ({flight_direction:.1f}°)")
                            except (ValueError, TypeError):
                                st.markdown(f"**🧭 Direction:** {feature['properties'].get('flightDirection', 'N/A')}")
                        
                        # Enhanced download button
                        download_url = feature['properties'].get('url', '')
                        if download_url:
                            file_name = feature['properties'].get('fileName', feature['properties'].get('sceneName', 'download') + '.zip')
                            st.markdown(f"""
                                <div style="margin: var(--spacing-md) 0; text-align: center;">
                                    <a href="{download_url}" download="{file_name}" target="_blank" style="text-decoration: none;">
                                        <button class="download-btn">
                                            ⬇️ Download
                                        </button>
                                    </a>
                                </div>
                            """, unsafe_allow_html=True)
                        
                        if st.checkbox("🔍 Raw Data", key=f"raw_{serial_number}_{feature['properties'].get('fileID', str(uuid.uuid4()))}"):
                            st.json(feature)
                    
                    serial_number += 1
    else:
        st.markdown("""
            <div style='text-align: center; padding: var(--spacing-xl); color: var(--text-muted);'>
                <div style='font-size: 3rem; margin-bottom: var(--spacing-md);'>🔍</div>
                <p style='font-weight: 500;'>No results to display</p>
                <p style='font-size: 0.875rem; opacity: 0.8;'>Use the search form to find satellite data</p>
            </div>
        """, unsafe_allow_html=True)
    
    st.markdown("</div>", unsafe_allow_html=True)

# Main content area - two column layout with full width
map_col, results_col = st.columns([2.75, 1.25])

with map_col:
    st.markdown("""
        <div class='card'>
            <h3 style='color: var(--text-primary); font-size: 1.25rem; margin-bottom: 1rem; text-align: center;'>
                Coverage Map
            </h3>
    """, unsafe_allow_html=True)
    
    # Display map if we have features
    if st.session_state.alaska_search_results and '_all_features' in st.session_state.alaska_search_results:
        try:
            all_features = st.session_state.alaska_search_results['_all_features']
            center_coords = st.session_state.alaska_search_results['_center_coords']
            
            center_lat = float(all_features[0]['properties'].get('centerLat', center_coords['lat']))
            center_lon = float(all_features[0]['properties'].get('centerLon', center_coords['lon']))
            
            with st.spinner("Generating map..."):
                m = create_map(all_features, center_lat, center_lon)
                st.markdown('<div class="map-container">', unsafe_allow_html=True)
                map_data = st_folium(m, width=900, height=700, returned_objects=["last_active_drawing", "last_clicked"])
                st.markdown('</div>', unsafe_allow_html=True)
                
                # Display information about clicked locations
                if map_data["last_clicked"]:
                    st.info(f"Clicked: {map_data['last_clicked']['lat']:.4f}, {map_data['last_clicked']['lng']:.4f}")
        except Exception as e:
            st.error(f"Error displaying map: {str(e)}")
    else:
        # Show placeholder when no data
        st.markdown("""
            <div style='text-align: center; padding: 5rem; color: var(--text-muted);'>
                <h4>No Data to Display</h4>
                <p>Use the search form to find satellite data</p>
            </div>
        """, unsafe_allow_html=True)
    
    st.markdown("</div>", unsafe_allow_html=True)

with results_col:
    # Enhanced search parameters card
    st.markdown("""
        <div class='compact-card'>
            <h3 style='color: var(--text-primary); font-size: 1.1rem; margin: 0 0 var(--spacing-md) 0; display: flex; align-items: center; gap: var(--spacing-sm);'>
                <span>🔍</span> Search Parameters
            </h3>
    """, unsafe_allow_html=True)
    
    # Compact search form
    with st.form(key="alaska_search_form"):
        # Single column layout for coordinates
        latitude = st.number_input("Latitude", format="%.4f", key="alaska_lat", help="Latitude")
        longitude = st.number_input("Longitude", format="%.4f", key="alaska_lon", help="Longitude")
        
        # Two column layout for dates
        date_col1, date_col2 = st.columns(2)
        with date_col1:
            start_date = st.date_input("From", key="alaska_start", help="Start Date")
        with date_col2:
            end_date = st.date_input("To", key="alaska_end", help="End Date")
        
        # Single row for limit and search
        limit_col, search_col = st.columns([1, 1])
        with limit_col:
            result_limit = st.number_input("Limit", min_value=1, value=5, key="alaska_limit", help="Results per platform")
        with search_col:
            st.markdown("<br>", unsafe_allow_html=True)  # Add some spacing
            search_submitted = st.form_submit_button("🔍 Search", use_container_width=True)
    
    st.markdown("</div>", unsafe_allow_html=True)
    
    # Enhanced download settings card
    st.markdown("""
        <div class='compact-card'>
            <h3 style='color: var(--text-primary); font-size: 1.1rem; margin: 0 0 var(--spacing-md) 0; display: flex; align-items: center; gap: var(--spacing-sm);'>
                <span>💾</span> Download Settings
            </h3>
    """, unsafe_allow_html=True)
    
    # Compact download directory input
    new_download_dir = st.text_input(
        "Path", 
        value=st.session_state.download_dir, 
        key="download_dir_input",
        help="Download directory path"
    )
    
    # Compact apply button
    if st.button("📂 Apply", key="apply_download_path", use_container_width=True):
        try:
            if not os.path.exists(new_download_dir):
                os.makedirs(new_download_dir)
            
            test_file = os.path.join(new_download_dir, ".write_test")
            with open(test_file, "w") as f:
                f.write("test")
            os.remove(test_file)
            
            st.session_state.download_dir = new_download_dir
            st.success("✅ Path updated")
        except Exception as e:
            st.error(f"❌ Error: {str(e)}")
    
    st.markdown("</div>", unsafe_allow_html=True)
    
    # Handle search submission
    if search_submitted:
        with st.spinner("Fetching data..."):
            st.session_state.alaska_search_results = {}
            
            any_valid_features = False
            all_features = []
            failed_platforms = []
            
            for platform in platforms:
                try:
                    data = fetch_data(platform, latitude, longitude, start_date, end_date, result_limit)
                    if data and 'features' in data and data['features']:
                        st.session_state.alaska_search_results[platform] = data['features']
                        any_valid_features = True
                        all_features.extend(data['features'])
                except Exception as e:
                    failed_platforms.append(f"{platform}: {str(e)}")
                    continue
            
            if any_valid_features:
                st.session_state.alaska_search_results['_all_features'] = all_features
                st.session_state.alaska_search_results['_center_coords'] = {
                    'lat': latitude,
                    'lon': longitude
                }
                st.success(f"Found {len(all_features)} results!")
                
                if failed_platforms:
                    with st.expander("⚠️ Some platforms failed", expanded=False):
                        for failure in failed_platforms:
                            st.warning(failure)
            else:
                if failed_platforms:
                    st.error("Search failed. Check connection.")
                    with st.expander("Error Details", expanded=True):
                        for failure in failed_platforms:
                            st.error(failure)
                else:
                    st.warning("No results found.")
        
        st.rerun()
    
    # Enhanced search summary
    if st.session_state.alaska_search_results:
        total_results = sum(len(features) for platform, features in st.session_state.alaska_search_results.items() if not platform.startswith('_'))
        platforms_with_data = [platform for platform, features in st.session_state.alaska_search_results.items() if not platform.startswith('_') and features]
        
        st.markdown("""
            <div class='compact-card'>
                <h3 style='color: var(--text-primary); font-size: 1.1rem; margin: 0 0 var(--spacing-sm) 0; display: flex; align-items: center; gap: var(--spacing-sm);'>
                    <span>📊</span> Summary
                </h3>
        """, unsafe_allow_html=True)
        
        metric_col1, metric_col2 = st.columns(2)
        with metric_col1:
            st.metric("🎯 Results", total_results)
        with metric_col2:
            st.metric("🛰️ Platforms", len(platforms_with_data))
        
        st.markdown("</div>", unsafe_allow_html=True)