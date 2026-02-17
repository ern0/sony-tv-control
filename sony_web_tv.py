#!/usr/bin/env python3
"""
Sony TV Web Service
Provides REST API endpoints to control Sony Bravia TV
Gets channel list directly from TV via HTTP API
Includes AJAX interface with channel switching and reordering
"""

import json
import tomllib
import logging
from typing import Dict, List, Optional, Any, Union
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
import socket
from datetime import datetime, timedelta
import re

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class SonyTVController:
    """Controller for Sony Bravia TV using REST API"""

    def __init__(self, ip_address: str, access_code: str, timeout: int = 5):
        self.ip_address = ip_address
        self.access_code = access_code
        self.timeout = timeout
        self.base_url = f"http://{ip_address}/sony"
        self.channels = []  # Will be populated from TV
        self.channels_last_updated = None
        self.channels_cache_duration = 300  # 5 minutes cache

    def _make_request(self, service: str, method: str, params: List = None,
                     version: str = "1.0") -> Optional[Dict[str, Any]]:
        """Make HTTP request to TV API"""
        if params is None:
            params = []

        url = f"{self.base_url}/{service}"

        payload = {
            "method": method,
            "params": params,
            "id": 1,
            "version": version
        }

        data = json.dumps(payload).encode('utf-8')

        req = Request(url, data=data, method='POST')
        req.add_header('Content-Type', 'application/json')
        req.add_header('X-Auth-PSK', self.access_code)

        try:
            logger.debug(f"Request to {url}: {payload}")
            with urlopen(req, timeout=self.timeout) as response:
                response_data = response.read().decode('utf-8')
                result = json.loads(response_data)
                logger.debug(f"Response: {result}")
                return result
        except HTTPError as e:
            logger.error(f"HTTP Error {e.code}: {e.reason}")
            return None
        except URLError as e:
            logger.error(f"URL Error: {e.reason}")
            return None
        except socket.timeout:
            logger.error("Request timeout")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            return None

    def check_connection(self) -> bool:
        """Test if TV is reachable"""
        result = self._make_request("system", "getSystemInformation")
        return result is not None and "result" in result

    def get_power_status(self) -> str:
        """Get TV power status"""
        result = self._make_request("system", "getPowerStatus")
        if result and "result" in result:
            return result["result"][0].get("status", "unknown")
        return "unknown"

    def fetch_channels_from_tv(self) -> List[Dict]:
        """
        Fetch channels directly from TV using the 3-step API process
        This is the key method that gets real channel data from the TV
        """
        logger.info("Fetching channels directly from TV...")
        all_channels = []

        # Step 1: Get available schemes
        logger.debug("Step 1: Getting scheme list")
        schemes_result = self._make_request("avContent", "getSchemeList")

        if not schemes_result or "result" not in schemes_result:
            logger.error("Failed to get scheme list")
            return []

        # Handle the actual response format
        schemes_data = schemes_result["result"]
        logger.debug(f"Raw schemes data: {schemes_data}")

        schemes = []
        if schemes_data and len(schemes_data) > 0:
            first_item = schemes_data[0]
            if isinstance(first_item, list):
                # Format: [["tv", "extInput", ...]]
                schemes = first_item
            elif isinstance(first_item, str):
                # Format: ["tv", "extInput", ...]
                schemes = schemes_data
            else:
                logger.warning(f"Unexpected scheme format: {type(first_item)}")

        logger.info(f"Found schemes: {schemes}")

        # Look for TV-related schemes
        tv_keywords = ['tv', 'tuner', 'digital', 'dtv', 'dvbt', 'dvbc', 'dvbs', 'atsc', 'isdb']
        tv_schemes = []

        for scheme in schemes:
            if isinstance(scheme, str):
                if any(kw in scheme.lower() for kw in tv_keywords):
                    tv_schemes.append(scheme)
            elif isinstance(scheme, dict):
                # Handle if scheme is a dict with 'name' or similar
                scheme_name = scheme.get('name') or scheme.get('scheme') or str(scheme)
                if any(kw in scheme_name.lower() for kw in tv_keywords):
                    tv_schemes.append(scheme_name)

        if not tv_schemes:
            logger.warning("No TV schemes found, trying common scheme names")
            tv_schemes = ['tv', 'digital', 'tuner']  # Try common names

        # Step 2: Get sources for each TV scheme
        for scheme in tv_schemes:
            logger.debug(f"Step 2: Getting sources for scheme: {scheme}")
            sources_result = self._make_request(
                "avContent",
                "getSourceList",
                params=[{"scheme": scheme}]
            )

            if not sources_result or "result" not in sources_result:
                continue

            # Handle the actual response format for sources
            sources_data = sources_result["result"]
            sources = []

            if sources_data and len(sources_data) > 0:
                first_item = sources_data[0]
                if isinstance(first_item, list):
                    # Format: [[{"source": "tv:dvbt", ...}]]
                    sources = first_item
                elif isinstance(first_item, dict):
                    # Format: [{"source": "tv:dvbt", ...}]
                    sources = sources_data
                else:
                    logger.warning(f"Unexpected sources format: {type(first_item)}")

            for source in sources:
                # Extract source URI - can be in different fields
                source_uri = None
                source_title = scheme

                if isinstance(source, dict):
                    source_uri = source.get('uri') or source.get('source')
                    source_title = source.get('title', scheme)
                elif isinstance(source, str):
                    source_uri = source

                if not source_uri:
                    continue

                logger.info(f"Found source: {source_title} - {source_uri}")

                # Step 3: Get channels from this source
                channels_from_source = self._fetch_channels_from_source(source_uri, source_title)
                all_channels.extend(channels_from_source)

        # If no channels found with standard method, try direct approach
        if not all_channels:
            logger.info("No channels found with standard method, trying direct approach")
            all_channels = self._fetch_channels_direct()

        logger.info(f"Total channels found: {len(all_channels)}")
        return all_channels

    def _fetch_channels_from_source(self, source_uri: str, source_title: str) -> List[Dict]:
        """Fetch channels from a specific source URI"""
        channels = []

        # Try different tuner variations
        uris_to_try = [source_uri]

        # Some TVs need tuner parameter
        if '?' not in source_uri:
            for tuner in range(0, 4):  # Try tuners 0-3
                uris_to_try.append(f"{source_uri}?tuner={tuner}")

        for uri in uris_to_try:
            logger.debug(f"Getting content list for {uri}")

            # Try with pagination
            for start_idx in range(0, 200, 50):  # Get up to 200 channels
                content_result = self._make_request(
                    "avContent",
                    "getContentList",
                    params=[{
                        "uri": uri,
                        "stIdx": start_idx,
                        "cnt": 50
                    }],
                    version="1.5"  # Use version 1.5 for channel info
                )

                if not content_result or "result" not in content_result:
                    continue

                # Handle the actual response format for content
                content_data = content_result["result"]
                items = []

                if content_data and len(content_data) > 0:
                    first_item = content_data[0]
                    if isinstance(first_item, list):
                        # Format: [[{channel1}, {channel2}]]
                        items = first_item
                    elif isinstance(first_item, dict):
                        # Format: [{channel1}, {channel2}]
                        items = content_data

                for item in items:
                    if not isinstance(item, dict):
                        continue

                    # Determine if this is a TV channel
                    is_tv_channel = self._is_tv_channel(item)

                    if is_tv_channel:
                        channel = {
                            'number': item.get('index', ''),
                            'name': item.get('title', 'Unknown'),
                            'uri': item.get('uri', ''),
                            'source': source_title,
                            'source_uri': source_uri
                        }

                        # Only add if not duplicate
                        if not any(c['uri'] == channel['uri'] for c in channels):
                            channels.append(channel)
                            logger.debug(f"Found channel: {channel.get('number')} - {channel.get('name')}")

                # If we got fewer items than requested, we've reached the end
                if len(items) < 50:
                    break

        return channels

    def _is_tv_channel(self, item: Dict) -> bool:
        """Determine if an item is a TV channel"""
        # Check by media type
        if item.get('programMediaType') == 'tv':
            return True

        # Check by URI patterns
        uri_str = item.get('uri', '').lower()
        if any(pattern in uri_str for pattern in ['sid=', 'channel=', 'program=', 'aid=']):
            return True

        # Check by title patterns (often have numbers or common channel names)
        title = item.get('title', '')
        if title and len(title) > 1:
            # Channels often have numbers or are common names
            if any(word in title.lower() for word in
                  ['bbc', 'itv', 'channel', 'tv', 'hd', 'news', 'sport', 'radio', 'cbs', 'nbc', 'abc', 'fox']):
                return True

            # Check if title contains numbers (like "BBC One" or "Channel 4")
            if re.search(r'\d', title):
                return True

        return False

    def _fetch_channels_direct(self) -> List[Dict]:
        """Fallback method: Try direct common URIs"""
        channels = []

        # Common URI patterns for different regions
        common_uris = [
            "tv:dvbt?tuner=1",
            "tv:dvbt?tuner=0",
            "tv:dvbc?tuner=1",
            "tv:dvbc?tuner=0",
            "tv:dvbs?tuner=1",
            "tv:dvbs?tuner=0",
            "tv:atsc?tuner=1",
            "tv:atsc?tuner=0",
            "tv:isdb?tuner=1",
            "tv:digital?tuner=1",
            "tv:tuner?index=0",
            "tv:0",
            "tv:1",
            "tv:dvbt",
            "tv:dvbc",
            "tv:dvbs"
        ]

        for uri in common_uris:
            logger.debug(f"Trying direct URI: {uri}")

            result = self._make_request(
                "avContent",
                "getContentList",
                params=[{"uri": uri, "stIdx": 0, "cnt": 100}],
                version="1.5"
            )

            if result and "result" in result:
                content_data = result["result"]
                items = []

                if content_data and len(content_data) > 0:
                    first_item = content_data[0]
                    if isinstance(first_item, list):
                        items = first_item
                    elif isinstance(first_item, dict):
                        items = content_data

                for item in items:
                    if not isinstance(item, dict):
                        continue

                    if self._is_tv_channel(item):
                        channels.append({
                            'number': item.get('channelNumber', ''),
                            'name': item.get('title', 'Unknown'),
                            'uri': item.get('uri', ''),
                            'source': 'direct',
                            'source_uri': uri
                        })

                if channels:
                    logger.info(f"Found {len(channels)} channels via direct URI {uri}")
                    break  # Stop if we found channels

        return channels

    def get_channels(self, force_refresh: bool = False) -> List[Dict]:
        """
        Get channels with caching
        If cache is expired or force_refresh is True, fetch from TV
        """
        cache_valid = False

        if self.channels_last_updated and not force_refresh:
            cache_age = (datetime.now() - self.channels_last_updated).total_seconds()
            cache_valid = cache_age < self.channels_cache_duration

            if cache_valid:
                logger.info(f"Using cached channels (age: {cache_age:.0f}s)")

        if not cache_valid or force_refresh:
            logger.info("Cache expired or refresh forced, fetching from TV")
            self.channels = self.fetch_channels_from_tv()
            self.channels_last_updated = datetime.now()

            # Sort channels by number for consistency
            self.channels.sort(key=lambda x: self._extract_channel_number(x))

        return self.channels

    def _extract_channel_number(self, channel: Dict) -> int:
        """Extract numeric channel number for sorting"""
        num_str = channel.get('number', '')
        if num_str:
            try:
                return int(num_str)
            except ValueError:
                pass

        # Try to extract from name
        numbers = re.findall(r'\d+', channel.get('name', ''))
        return int(numbers[0]) if numbers else 9999

    def switch_to_channel(self, channel_identifier: str) -> bool:
        """
        Switch to a specific channel by number or name
        Uses the channel URI from TV data
        """
        logger.info(f"Attempting to switch to channel: {channel_identifier}")

        # Get fresh channels if needed
        channels = self.get_channels()

        # Try to find channel by number
        target_channel = None
        for channel in channels:
            if str(channel.get('number')) == str(channel_identifier):
                target_channel = channel
                break

        # If not found by number, try by name (case-insensitive partial match)
        if not target_channel:
            channel_id_lower = channel_identifier.lower()
            for channel in channels:
                if channel_id_lower in channel.get('name', '').lower():
                    target_channel = channel
                    break

        if not target_channel:
            logger.warning(f"Channel '{channel_identifier}' not found")
            return False

        # Switch to channel using setPlayContent
        uri = target_channel.get('uri')
        if not uri:
            logger.error("Channel has no URI")
            return False

        logger.info(f"Switching to {target_channel.get('name')} (URI: {uri})")

        result = self._make_request(
            "avContent",
            "setPlayContent",
            params=[{"uri": uri}],
            version="1.0"
        )

        success = result is not None and "result" in result
        if success:
            logger.info(f"Successfully switched to {target_channel.get('name')}")
        else:
            logger.error("Failed to switch channel")

        return success


class TVRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler for TV web service"""

    def __init__(self, *args, **kwargs):
        # These will be set after loading config
        self.tv = None
        super().__init__(*args, **kwargs)

    def load_config(self):
        """Load configuration from tv.toml"""
        try:
            with open('tv.toml', 'rb') as f:
                config = tomllib.load(f)

            tv_config = config.get('tv', {})

            self.tv = SonyTVController(
                ip_address=tv_config.get('ip_address', '192.168.1.100'),
                access_code=tv_config.get('access_code', ''),
                timeout=tv_config.get('timeout', 5)
            )
            return True
        except FileNotFoundError:
            logger.error("tv.toml configuration file not found")
            return False
        except Exception as e:
            logger.error(f"Error loading config: {e}")
            return False

    def _send_response(self, status_code: int, data: Any):
        """Send JSON response"""
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()

        response = {
            'status': 'success' if 200 <= status_code < 300 else 'error',
            'data': data,
            'timestamp': datetime.now().isoformat()
        }

        self.wfile.write(json.dumps(response).encode('utf-8'))

    def _send_error(self, status_code: int, message: str):
        """Send error response"""
        self._send_response(status_code, {'message': message})

    def _send_html(self, html: str):
        """Send HTML response"""
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode('utf-8'))

    def do_GET(self):
        """Handle GET requests"""
        # Load config for each request
        if not self.load_config():
            self._send_error(500, "Configuration not loaded")
            return

        # Parse URL
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        query = parse_qs(parsed_url.query)

        # Route requests
        if path == '/':
            self.handle_root()
        elif path == '/list':
            self.handle_list_channels(query)
        elif path.startswith('/switch/'):
            channel = path.replace('/switch/', '')
            self.handle_switch_channel(channel)
        elif path == '/status':
            self.handle_status()
        elif path == '/power':
            self.handle_power()
        elif path == '/refresh':
            self.handle_refresh_channels()
        elif path == '/channels':
            self.handle_channels_api(query)
        else:
            self._send_error(404, f"Endpoint not found: {path}")

    def handle_root(self):
        """Handle root endpoint - Main HTML interface with AJAX"""
        html = self._generate_main_html()
        self._send_html(html)

    def _generate_main_html(self) -> str:
        """Generate the main HTML page with AJAX functionality"""
        return """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Sony TV Remote</title>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <style>
                * {
                    box-sizing: border-box;
                    margin: 0;
                    padding: 0;
                }

                body {
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    min-height: 100vh;
                    padding: 20px;
                }

                .container {
                    max-width: 1200px;
                    margin: 0 auto;
                    background: white;
                    border-radius: 20px;
                    box-shadow: 0 20px 60px rgba(0,0,0,0.3);
                    overflow: hidden;
                }

                .header {
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    color: white;
                    padding: 30px;
                    text-align: center;
                }

                .header h1 {
                    font-size: 2.5em;
                    margin-bottom: 10px;
                }

                .header p {
                    opacity: 0.9;
                    font-size: 1.1em;
                }

                .status-bar {
                    background: #f8f9fa;
                    padding: 15px 30px;
                    border-bottom: 1px solid #e9ecef;
                    display: flex;
                    align-items: center;
                    gap: 20px;
                    flex-wrap: wrap;
                }

                .status-item {
                    display: flex;
                    align-items: center;
                    gap: 8px;
                }

                .status-label {
                    font-weight: 600;
                    color: #495057;
                }

                .status-value {
                    color: #212529;
                }

                .status-dot {
                    width: 12px;
                    height: 12px;
                    border-radius: 50%;
                    display: inline-block;
                }

                .dot-connected {
                    background: #28a745;
                    box-shadow: 0 0 10px #28a745;
                }

                .dot-disconnected {
                    background: #dc3545;
                }

                .channel-count {
                    background: #007bff;
                    color: white;
                    padding: 4px 12px;
                    border-radius: 20px;
                    font-size: 0.9em;
                    margin-left: auto;
                }

                .search-section {
                    padding: 20px 30px;
                    background: white;
                    border-bottom: 1px solid #e9ecef;
                }

                .search-box {
                    width: 100%;
                    padding: 12px 20px;
                    font-size: 1em;
                    border: 2px solid #e9ecef;
                    border-radius: 10px;
                    transition: border-color 0.3s;
                }

                .search-box:focus {
                    outline: none;
                    border-color: #667eea;
                }

                .channels-section {
                    padding: 0 30px 30px 30px;
                    background: white;
                }

                .channels-header {
                    display: flex;
                    justify-content: space-between;
                    align-items: center;
                    margin-bottom: 20px;
                    padding-top: 20px;
                }

                .channels-title {
                    font-size: 1.5em;
                    color: #333;
                }

                .refresh-btn {
                    background: #28a745;
                    color: white;
                    border: none;
                    padding: 10px 20px;
                    border-radius: 8px;
                    cursor: pointer;
                    font-size: 0.9em;
                    display: flex;
                    align-items: center;
                    gap: 5px;
                    transition: background 0.3s;
                }

                .refresh-btn:hover {
                    background: #218838;
                }

                .refresh-btn:disabled {
                    background: #6c757d;
                    cursor: not-allowed;
                }

                .channels-grid {
                    display: grid;
                    grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
                    gap: 15px;
                    max-height: 600px;
                    overflow-y: auto;
                    padding: 5px;
                }

                .channel-card {
                    background: #f8f9fa;
                    border-radius: 10px;
                    padding: 15px;
                    display: flex;
                    justify-content: space-between;
                    align-items: center;
                    transition: all 0.3s;
                    border: 2px solid transparent;
                    animation: fadeIn 0.5s ease-out;
                }

                .channel-card:hover {
                    transform: translateY(-2px);
                    box-shadow: 0 5px 15px rgba(0,0,0,0.1);
                    border-color: #667eea;
                }

                .channel-card.current {
                    background: linear-gradient(135deg, #667eea20 0%, #764ba220 100%);
                    border-color: #667eea;
                    order: -1;
                }

                @keyframes fadeIn {
                    from {
                        opacity: 0;
                        transform: translateY(10px);
                    }
                    to {
                        opacity: 1;
                        transform: translateY(0);
                    }
                }

                .channel-info {
                    flex: 1;
                }

                .channel-number {
                    font-size: 1.2em;
                    font-weight: bold;
                    color: #667eea;
                }

                .channel-name {
                    font-size: 1.1em;
                    color: #333;
                    margin-top: 4px;
                }

                .channel-source {
                    font-size: 0.8em;
                    color: #6c757d;
                    margin-top: 4px;
                }

                .switch-btn {
                    background: #007bff;
                    color: white;
                    border: none;
                    padding: 8px 16px;
                    border-radius: 6px;
                    cursor: pointer;
                    font-size: 0.9em;
                    transition: background 0.3s;
                    margin-left: 10px;
                }

                .switch-btn:hover {
                    background: #0056b3;
                }

                .switch-btn:disabled {
                    background: #6c757d;
                    cursor: not-allowed;
                }

                .loading {
                    text-align: center;
                    padding: 40px;
                    color: #6c757d;
                }

                .loading-spinner {
                    border: 4px solid #f3f3f3;
                    border-top: 4px solid #667eea;
                    border-radius: 50%;
                    width: 40px;
                    height: 40px;
                    animation: spin 1s linear infinite;
                    margin: 0 auto 20px;
                }

                @keyframes spin {
                    0% { transform: rotate(0deg); }
                    100% { transform: rotate(360deg); }
                }

                .error-message {
                    background: #f8d7da;
                    color: #721c24;
                    padding: 15px;
                    border-radius: 8px;
                    margin: 20px 0;
                }

                .success-message {
                    background: #d4edda;
                    color: #155724;
                    padding: 10px 15px;
                    border-radius: 8px;
                    margin: 10px 0;
                    animation: slideIn 0.3s ease-out;
                }

                @keyframes slideIn {
                    from {
                        transform: translateY(-20px);
                        opacity: 0;
                    }
                    to {
                        transform: translateY(0);
                        opacity: 1;
                    }
                }

                .notification {
                    position: fixed;
                    top: 20px;
                    right: 20px;
                    padding: 15px 20px;
                    border-radius: 8px;
                    color: white;
                    animation: slideInRight 0.3s ease-out;
                    z-index: 1000;
                }

                @keyframes slideInRight {
                    from {
                        transform: translateX(100%);
                        opacity: 0;
                    }
                    to {
                        transform: translateX(0);
                        opacity: 1;
                    }
                }

                .notification.success {
                    background: #28a745;
                }

                .notification.error {
                    background: #dc3545;
                }

                .notification.info {
                    background: #17a2b8;
                }

                .stats {
                    display: flex;
                    gap: 10px;
                    align-items: center;
                }

                .last-updated {
                    font-size: 0.9em;
                    color: #6c757d;
                }

                .no-channels {
                    text-align: center;
                    padding: 60px;
                    color: #6c757d;
                }

                .no-channels i {
                    font-size: 4em;
                    margin-bottom: 20px;
                    display: block;
                }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>📺 Sony TV Remote</h1>
                    <p>Control your TV with live channel data</p>
                </div>

                <div class="status-bar" id="statusBar">
                    <div class="status-item">
                        <span class="status-label">Status:</span>
                        <span class="status-dot" id="connectionDot"></span>
                        <span class="status-value" id="connectionStatus">Checking...</span>
                    </div>
                    <div class="status-item">
                        <span class="status-label">Power:</span>
                        <span class="status-value" id="powerStatus">Unknown</span>
                    </div>
                    <div class="status-item">
                        <span class="status-label">TV IP:</span>
                        <span class="status-value" id="tvIp">""" + self.tv.ip_address + """</span>
                    </div>
                    <div class="channel-count" id="channelCount">0 channels</div>
                </div>

                <div class="search-section">
                    <input type="text" class="search-box" id="searchBox" placeholder="🔍 Search channels by name or number...">
                </div>

                <div class="channels-section">
                    <div class="channels-header">
                        <h2 class="channels-title">TV Channels</h2>
                        <div class="stats">
                            <span class="last-updated" id="lastUpdated"></span>
                            <button class="refresh-btn" id="refreshBtn" onclick="refreshChannels()">
                                🔄 Refresh
                            </button>
                        </div>
                    </div>

                    <div id="channelsContainer" class="channels-grid">
                        <div class="loading">
                            <div class="loading-spinner"></div>
                            Loading channels from TV...
                        </div>
                    </div>
                </div>
            </div>

            <div id="notification" style="display: none;"></div>

            <script>
                // Pure JavaScript - No external libraries
                let currentChannel = null;
                let allChannels = [];
                let searchTimeout = null;

                // Load channels on page load
                document.addEventListener('DOMContentLoaded', function() {
                    loadChannels();
                    loadStatus();

                    // Set up search with debounce
                    document.getElementById('searchBox').addEventListener('input', function(e) {
                        clearTimeout(searchTimeout);
                        searchTimeout = setTimeout(() => {
                            filterChannels(e.target.value);
                        }, 300);
                    });

                    // Auto-refresh status every 30 seconds
                    setInterval(loadStatus, 30000);
                });

                function showNotification(message, type) {
                    const notification = document.getElementById('notification');
                    notification.style.display = 'block';
                    notification.className = 'notification ' + type;
                    notification.textContent = message;

                    setTimeout(() => {
                        notification.style.display = 'none';
                    }, 3000);
                }

                function loadStatus() {
                    fetch('/status')
                        .then(response => response.json())
                        .then(data => {
                            const connected = data.data.connected;
                            const dot = document.getElementById('connectionDot');
                            const status = document.getElementById('connectionStatus');
                            const power = document.getElementById('powerStatus');

                            dot.className = 'status-dot ' + (connected ? 'dot-connected' : 'dot-disconnected');
                            status.textContent = connected ? 'Connected' : 'Disconnected';
                            power.textContent = data.data.power || 'Unknown';

                            if (data.data.channels) {
                                document.getElementById('channelCount').textContent =
                                    data.data.channels.total + ' channels';

                                if (data.data.channels.last_updated) {
                                    const updated = new Date(data.data.channels.last_updated);
                                    document.getElementById('lastUpdated').textContent =
                                        'Updated: ' + updated.toLocaleTimeString();
                                }
                            }
                        })
                        .catch(error => {
                            console.error('Status error:', error);
                        });
                }

                function loadChannels() {
                    fetch('/channels')
                        .then(response => response.json())
                        .then(data => {
                            if (data.status == 'success') {
                                allChannels = data.data.channels;
                                renderChannels(allChannels);
                                document.getElementById('channelCount').textContent =
                                    allChannels.length + ' channels';
                            } else {
                                showNotification('Failed to load channels', 'error');
                            }
                        })
                        .catch(error => {
                            console.error('Error:', error);
                            document.getElementById('channelsContainer').innerHTML =
                                '<div class="error-message">Failed to load channels. Check connection.</div>';
                        });
                }

                function refreshChannels() {
                    const btn = document.getElementById('refreshBtn');
                    btn.disabled = true;
                    btn.innerHTML = '🔄 Refreshing...';

                    document.getElementById('channelsContainer').innerHTML = `
                        <div class="loading">
                            <div class="loading-spinner"></div>
                            Refreshing channels from TV...
                        </div>
                    `;

                    fetch('/refresh')
                        .then(response => response.json())
                        .then(data => {
                            if (data.status == 'success') {
                                allChannels = data.data.channels;
                                renderChannels(allChannels);
                                showNotification('Channels refreshed!', 'success');
                                loadStatus();
                            } else {
                                showNotification('Refresh failed', 'error');
                            }
                        })
                        .catch(error => {
                            console.error('Error:', error);
                            showNotification('Refresh failed', 'error');
                        })
                        .finally(() => {
                            btn.disabled = false;
                            btn.innerHTML = '🔄 Refresh';
                        });
                }

                function filterChannels(query) {
                    if (!query.trim()) {
                        renderChannels(allChannels);
                        return;
                    }

                    const filtered = allChannels.filter(channel => {
                        const nameMatch = channel.name.toLowerCase().includes(query.toLowerCase());
                        const numMatch = channel.number.toLowerCase().includes(query.toLowerCase());
                        return nameMatch || numMatch;
                    });

                    renderChannels(filtered);
                }

                function switchChannel(channelNumber, channelName) {
                    const btn = event.target;
                    btn.disabled = true;
                    const originalText = btn.textContent;
                    btn.textContent = '⏳ Switching...';

                    fetch('/switch/' + encodeURIComponent(channelNumber))
                        .then(response => response.json())
                        .then(data => {
                            if (data.status == 'success') {
                                showNotification('Switched to ' + channelName, 'success');
                                // Move selected channel to top
                                moveChannelToTop(channelNumber);
                            } else {
                                showNotification('Failed to switch: ' + data.data.message, 'error');
                            }
                        })
                        .catch(error => {
                            console.error('Error:', error);
                            showNotification('Switch failed', 'error');
                        })
                        .finally(() => {
                            btn.disabled = false;
                            btn.textContent = originalText;
                        });
                }

                function moveChannelToTop(channelNumber) {
                    // Find the channel
                    const index = allChannels.findIndex(c => c.number == channelNumber);
                    if (index == -1) return;

                    // Remove from current position and insert at beginning
                    const channel = allChannels.splice(index, 1)[0];
                    allChannels.unshift(channel);

                    // Re-render with current channel highlighted
                    currentChannel = channelNumber;
                    renderChannels(allChannels);
                }

                function renderChannels(channels) {
                    const container = document.getElementById('channelsContainer');

                    if (!channels || channels.length == 0) {
                        container.innerHTML = `
                            <div class="no-channels">
                                <i>📺</i>
                                <h3>No channels found</h3>
                                <p>Try refreshing or check TV connection</p>
                            </div>
                        `;
                        return;
                    }

                    let html = '';
                    channels.forEach(channel => {
                        const isCurrent = channel.number == currentChannel;
                        const channelClass = 'channel-card' + (isCurrent ? ' current' : '');

                        html += `
                            <div class="${channelClass}" data-number="${channel.number}">
                                <div class="channel-info">
                                    <div class="channel-number">${channel.number || '---'}</div>
                                    <div class="channel-name">${channel.name}</div>
                                    <div class="channel-source">${channel.source || 'TV'}</div>
                                </div>
                                <button class="switch-btn"
                                        onclick="switchChannel('${channel.number}', '${channel.name.replace(/'/g, "\\'")}')"
                                        ${isCurrent ? 'disabled' : ''}>
                                    ${isCurrent ? '📺 Current' : 'Switch'}
                                </button>
                            </div>
                        `;
                    });

                    container.innerHTML = html;
                }
            </script>
        </body>
        </html>
        """

    def handle_list_channels(self, query):
        """Handle /list endpoint - return channel list from TV"""
        format_type = query.get('format', ['json'])[0]
        refresh = query.get('refresh', ['false'])[0].lower() == 'true'

        # Get channels directly from TV
        channels = self.tv.get_channels(force_refresh=refresh)

        if format_type == 'html':
            # For HTML format, generate a simple table (though main interface uses AJAX)
            html = self._generate_simple_channel_list(channels)
            self._send_html(html)
        else:
            self._send_response(200, {
                'total': len(channels),
                'channels': channels,
                'source': 'tv',
                'cached': not refresh and self.tv.channels_last_updated is not None,
                'last_updated': self.tv.channels_last_updated.isoformat() if self.tv.channels_last_updated else None
            })

    def _generate_simple_channel_list(self, channels):
        """Generate a simple HTML channel list (fallback)"""
        html = "<html><head><title>TV Channels</title></head><body>"
        html += "<h1>TV Channels</h1><ul>"
        for ch in channels:
            html += f"<li>{ch.get('number')} - {ch.get('name')}</li>"
        html += "</ul></body></html>"
        return html

    def handle_switch_channel(self, channel):
        """Handle /switch/<channel> endpoint"""
        if not channel:
            self._send_error(400, "Channel identifier required")
            return

        success = self.tv.switch_to_channel(channel)

        if success:
            self._send_response(200, {
                'message': f'Switched to channel {channel}',
                'channel': channel
            })
        else:
            self._send_error(404, f'Channel "{channel}" not found or switch failed')

    def handle_status(self):
        """Handle /status endpoint - TV connection and channel status"""
        connected = self.tv.check_connection()
        power = self.tv.get_power_status() if connected else "unknown"

        # Get channel count
        channels = self.tv.get_channels()

        self._send_response(200, {
            'connected': connected,
            'power': power,
            'tv_ip': self.tv.ip_address,
            'channels': {
                'total': len(channels),
                'last_updated': self.tv.channels_last_updated.isoformat() if self.tv.channels_last_updated else None,
                'cache_age_seconds': (datetime.now() - self.tv.channels_last_updated).total_seconds()
                                    if self.tv.channels_last_updated else None
            }
        })

    def handle_power(self):
        """Handle /power endpoint - power status"""
        power = self.tv.get_power_status()
        self._send_response(200, {'power': power})

    def handle_refresh_channels(self):
        """Handle /refresh endpoint - force refresh channel list from TV"""
        logger.info("Manual channel refresh requested")
        channels = self.tv.get_channels(force_refresh=True)

        self._send_response(200, {
            'message': 'Channel list refreshed from TV',
            'total': len(channels),
            'channels': channels,  # Return all channels for AJAX
            'total_found': len(channels)
        })

    def handle_channels_api(self, query):
        """Handle /channels endpoint - AJAX API for channels"""
        refresh = query.get('refresh', ['false'])[0].lower() == 'true'
        channels = self.tv.get_channels(force_refresh=refresh)

        self._send_response(200, {
            'channels': channels,
            'total': len(channels),
            'last_updated': self.tv.channels_last_updated.isoformat() if self.tv.channels_last_updated else None
        })

    def log_message(self, format, *args):
        """Override to use our logger"""
        logger.info(f"{self.address_string()} - {format % args}")

def main():
    """Main function to start the web service"""
    # Load configuration
    try:
        with open('tv.toml', 'rb') as f:
            config = tomllib.load(f)
    except FileNotFoundError:
        logger.error("tv.toml configuration file not found")
        print("\n❌ Configuration file 'tv.toml' not found!")
        print("Please create it with the following content:")
        print("""
[tv]
ip_address = "192.168.1.100"
access_code = "your_psk_here"

[server]
port = 8080
host = "0.0.0.0"
        """)
        return
    except Exception as e:
        logger.error(f"Error loading config: {e}")
        return

    # Get server configuration
    server_config = config.get('server', {})
    host = server_config.get('host', '0.0.0.0')
    port = server_config.get('port', 8080)
    debug = server_config.get('debug', False)

    if debug:
        logging.getLogger().setLevel(logging.DEBUG)

    # Test TV connection and fetch initial channels
    tv_config = config.get('tv', {})
    tv = SonyTVController(
        ip_address=tv_config.get('ip_address', ''),
        access_code=tv_config.get('access_code', ''),
        timeout=tv_config.get('timeout', 5)
    )

    print("\n" + "="*70)
    print("📺 SONY TV WEB SERVICE - Live Channel Data with AJAX")
    print("="*70)

    print(f"\n📡 Testing connection to TV at {tv.ip_address}...")
    if tv.check_connection():
        print("✅ Successfully connected to TV")

        # Fetch initial channels
        print("\n🔍 Scanning for channels directly from TV...")
        channels = tv.fetch_channels_from_tv()
        print(f"✅ Found {len(channels)} channels")

        if channels:
            print("\n📺 Sample channels:")
            for ch in channels[:5]:
                num = ch.get('number', 'N/A')
                name = ch.get('name', 'Unknown')
                print(f"   {num:4} | {name}")
    else:
        print("⚠️  Could not connect to TV - check configuration")

    # Start server
    server_address = (host, port)
    httpd = HTTPServer(server_address, TVRequestHandler)

    print(f"\n🌐 Web service running at:")
    print(f"   http://{host if host != '0.0.0.0' else 'localhost'}:{port}/")
    print(f"\n📋 AJAX-enabled interface with:")
    print(f"   - Live channel switching")
    print(f"   - Selected channel moves to top")
    print(f"   - Search/filter channels")
    print(f"   - Real-time status updates")
    print(f"\n🛑 Press Ctrl+C to stop")
    print("="*70)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n\n👋 Shutting down...")
        httpd.shutdown()

if __name__ == "__main__":
    main()
