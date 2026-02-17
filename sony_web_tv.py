#!/usr/bin/env python3
"""
Sony TV Web Service
Provides REST API endpoints to control Sony Bravia TV
Gets channel list directly from TV via HTTP API
Reads only IP and access code from tv.toml
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

        # FIX: Handle the actual response format
        # The result might be an array of scheme names directly
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

            # FIX: Handle the actual response format for sources
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

                # FIX: Handle the actual response format for content
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
            print(channel.get('number'))
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

        self.wfile.write(json.dumps(response, indent=2).encode('utf-8'))

    def _send_error(self, status_code: int, message: str):
        """Send error response"""
        self._send_response(status_code, {'message': message})

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
        else:
            self._send_error(404, f"Endpoint not found: {path}")

    def handle_root(self):
        """Handle root endpoint - API documentation"""
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Sony TV Web Service</title>
            <style>
                body { font-family: Arial, sans-serif; margin: 40px; line-height: 1.6; }
                h1 { color: #333; }
                code { background: #f4f4f4; padding: 2px 5px; border-radius: 3px; }
                .endpoint { background: #e8f4f8; padding: 10px; margin: 10px 0; border-left: 4px solid #2196F3; }
                .note { background: #fff3e0; padding: 10px; border-left: 4px solid #ff9800; margin: 10px 0; }
            </style>
        </head>
        <body>
            <h1>📺 Sony TV Web Service</h1>
            <p>REST API for controlling your Sony Bravia TV</p>

            <div class="note">
                <strong>📡 Live TV Data:</strong> Channel lists are fetched directly from your TV via its HTTP API.
                No manual configuration needed!
            </div>

            <h2>Available Endpoints:</h2>

            <div class="endpoint">
                <strong>GET /</strong> - This documentation
            </div>

            <div class="endpoint">
                <strong>GET /list</strong> - List all TV channels (from TV)<br>
                <em>Optional query params:</em><br>
                <code>?format=json</code> - Returns JSON (default)<br>
                <code>?format=html</code> - Returns HTML table<br>
                <code>?refresh=true</code> - Force refresh from TV
            </div>

            <div class="endpoint">
                <strong>GET /refresh</strong> - Force refresh channel list from TV
            </div>

            <div class="endpoint">
                <strong>GET /switch/&lt;channel&gt;</strong> - Switch to a channel<br>
                <em>Examples:</em><br>
                <code>/switch/101</code> - Switch to channel 101<br>
                <code>/switch/bbc</code> - Switch to channel containing "bbc"
            </div>

            <div class="endpoint">
                <strong>GET /status</strong> - Get TV status (power, connection, channel count)
            </div>

            <div class="endpoint">
                <strong>GET /power</strong> - Get power status
            </div>

            <h2>Configuration:</h2>
            <p>Edit <code>tv.toml</code> to configure:</p>
            <ul>
                <li>TV IP address</li>
                <li>Access code (PSK)</li>
                <li>Web server port</li>
            </ul>

            <p><em>Channel list is automatically discovered from your TV!</em></p>
        </body>
        </html>
        """

        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode('utf-8'))

    def handle_list_channels(self, query):
        """Handle /list endpoint - return channel list from TV"""
        format_type = query.get('format', ['json'])[0]
        refresh = query.get('refresh', ['false'])[0].lower() == 'true'

        # Get channels directly from TV
        channels = self.tv.get_channels(force_refresh=refresh)

        if format_type == 'html':
            self.send_html_channel_list(channels)
        else:
            self._send_response(200, {
                'total': len(channels),
                'channels': channels,
                'source': 'tv',
                'cached': not refresh and self.tv.channels_last_updated is not None,
                'last_updated': self.tv.channels_last_updated.isoformat() if self.tv.channels_last_updated else None
            })

    def send_html_channel_list(self, channels):
        """Send channel list as HTML table"""
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>TV Channels - Live from TV</title>
            <style>
                body { font-family: Arial, sans-serif; margin: 40px; }
                h1 { color: #333; }
                .info { background: #e3f2fd; padding: 10px; border-radius: 5px; margin-bottom: 20px; }
                table { border-collapse: collapse; width: 100%; max-width: 800px; }
                th, td { border: 1px solid #ddd; padding: 12px; text-align: left; }
                th { background-color: #2196F3; color: white; }
                tr:nth-child(even) { background-color: #f2f2f2; }
                tr:hover { background-color: #e8f4f8; }
                .channel-link {
                    background: #4CAF50;
                    color: white;
                    padding: 5px 10px;
                    text-decoration: none;
                    border-radius: 3px;
                    display: inline-block;
                }
                .channel-link:hover { background: #45a049; }
                .refresh { margin-bottom: 20px; }
                .refresh a { margin-right: 10px; }
                .source-badge {
                    background: #9e9e9e;
                    color: white;
                    padding: 2px 6px;
                    border-radius: 3px;
                    font-size: 0.8em;
                }
            </style>
        </head>
        <body>
            <h1>📺 TV Channels - Live from Sony TV</h1>
            <div class="info">
                <strong>📡 Live Data:</strong> This list is fetched directly from your TV via its HTTP API
            </div>
            <div class="refresh">
                <a href="/list?format=html&refresh=true">🔄 Refresh from TV</a>
                <a href="/list?format=json">📋 JSON</a>
                <a href="/refresh">🔄 Force Refresh</a>
            </div>
        """

        if channels:
            html += f"""
            <p>Found <strong>{len(channels)}</strong> channels from your TV</p>
            <table>
                <tr>
                    <th>Channel #</th>
                    <th>Channel Name</th>
                    <th>Source</th>
                    <th>Action</th>
                </tr>
            """

            for channel in sorted(channels, key=lambda x: x.get('number', '9999')):
                channel_num = channel.get('number', '')
                name = channel.get('name', 'Unknown')
                source = channel.get('source', 'TV Tuner')

                html += f"""
                    <tr>
                        <td>{channel_num}</td>
                        <td>{name}</td>
                        <td><span class="source-badge">{source}</span></td>
                        <td><a href="/switch/{channel_num if channel_num else name}" class="channel-link">Switch</a></td>
                    </tr>
                """

            html += "</table>"
        else:
            html += """
            <div style="background: #ffebee; padding: 20px; border-radius: 5px;">
                <strong>⚠️ No channels found</strong>
                <p>Could not retrieve channels from your TV. This could be because:</p>
                <ul>
                    <li>TV is in standby mode</li>
                    <li>TV tuner is not active</li>
                    <li>Authentication issue</li>
                </ul>
                <p><a href="/refresh">Click here to try refreshing</a></p>
            </div>
            """

        html += """
            <p style="margin-top: 20px; color: #666;">
                <em>Channel data retrieved directly from TV via HTTP API</em>
            </p>
        </body>
        </html>
        """

        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode('utf-8'))

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
            'channels': channels[:10],  # Return first 10 as preview
            'total_found': len(channels)
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
    print("📺 SONY TV WEB SERVICE - Live Channel Data")
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
    print(f"\n📋 Available endpoints (live TV data):")
    print(f"   GET /              - API documentation")
    print(f"   GET /list          - List all channels (from TV)")
    print(f"   GET /refresh       - Force refresh channel list")
    print(f"   GET /switch/101    - Switch to channel 101")
    print(f"   GET /status        - TV & channel status")
    print(f"\n🛑 Press Ctrl+C to stop")
    print("="*70)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n\n👋 Shutting down...")
        httpd.shutdown()

if __name__ == "__main__":
    main()
