#!/usr/bin/env python3
"""
Sony TV Web Service
Provides REST API endpoints to control Sony Bravia TV
Gets channel list directly from TV via HTTP API
Includes AJAX interface with channel switching and volume control
"""

import json
import tomllib
import logging
from typing import Dict, List, Optional, Any
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
import socket
from datetime import datetime
import re
import os

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
        self.channels = []
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
        except (HTTPError, URLError, socket.timeout, json.JSONDecodeError, Exception) as e:
            logger.error(f"Request error: {e}")
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

    def power_on(self) -> bool:
        """Turn on the TV"""
        logger.info("Attempting to turn TV on")
        result = self._make_request(
            "system",
            "setPowerStatus",
            params=[{"status": True}],
            version="1.0"
        )
        return result is not None and "result" in result

    def power_off(self) -> bool:
        """Turn off the TV"""
        logger.info("Attempting to turn TV off")
        result = self._make_request(
            "system",
            "setPowerStatus",
            params=[{"status": False}],
            version="1.0"
        )
        return result is not None and "result" in result

    def volume_up(self) -> bool:
        """Increase volume"""
        logger.info("Volume up")
        result = self._make_request(
            "audio",
            "setAudioVolume",
            params=[{"target": "speaker", "volume": "+1"}],
            version="1.0"
        )
        return result is not None and "result" in result

    def volume_down(self) -> bool:
        """Decrease volume"""
        logger.info("Volume down")
        result = self._make_request(
            "audio",
            "setAudioVolume",
            params=[{"target": "speaker", "volume": "-1"}],
            version="1.0"
        )
        return result is not None and "result" in result

    def volume_set(self, level: int) -> bool:
        """Set volume to specific level (0-100)"""
        logger.info(f"Setting volume to {level}")
        result = self._make_request(
            "audio",
            "setAudioVolume",
            params=[{"target": "speaker", "volume": str(level)}],
            version="1.0"
        )
        return result is not None and "result" in result

    def get_volume(self) -> Optional[int]:
        """Get current volume level"""
        result = self._make_request("audio", "getVolumeInformation")
        if result and "result" in result:
            for target in result["result"]:
                if isinstance(target, list) and len(target) > 0:
                    for vol_info in target:
                        if vol_info.get("target") == "speaker":
                            return vol_info.get("volume")
        return None

    def mute(self) -> bool:
        """Mute/unmute audio"""
        logger.info("Toggling mute")
        result = self._make_request(
            "audio",
            "setAudioMute",
            params=[{"status": True}],
            version="1.0"
        )
        return result is not None and "result" in result

    def unmute(self) -> bool:
        """Unmute audio"""
        logger.info("Unmuting")
        result = self._make_request(
            "audio",
            "setAudioMute",
            params=[{"status": False}],
            version="1.0"
        )
        return result is not None and "result" in result

    def get_mute_status(self) -> Optional[bool]:
        """Get mute status"""
        result = self._make_request("audio", "getVolumeInformation")
        if result and "result" in result:
            for target in result["result"]:
                if isinstance(target, list) and len(target) > 0:
                    for vol_info in target:
                        if vol_info.get("target") == "speaker":
                            return vol_info.get("mute", False)
        return None

    def fetch_channels_from_tv(self) -> List[Dict]:
        """Fetch channels directly from TV using the API"""
        logger.info("Fetching channels directly from TV...")
        all_channels = []

        # Get available schemes
        schemes_result = self._make_request("avContent", "getSchemeList")
        if not schemes_result or "result" not in schemes_result:
            return []

        schemes_data = schemes_result["result"]
        schemes = []
        if schemes_data and len(schemes_data) > 0:
            first_item = schemes_data[0]
            if isinstance(first_item, list):
                schemes = first_item
            elif isinstance(first_item, str):
                schemes = schemes_data

        # Look for TV-related schemes
        tv_keywords = ['tv', 'tuner', 'digital', 'dtv', 'dvbt', 'dvbc', 'dvbs']
        tv_schemes = []

        for scheme in schemes:
            if isinstance(scheme, str) and any(kw in scheme.lower() for kw in tv_keywords):
                tv_schemes.append(scheme)

        if not tv_schemes:
            tv_schemes = ['tv', 'digital', 'tuner']

        # Get channels from each scheme
        for scheme in tv_schemes:
            sources_result = self._make_request(
                "avContent",
                "getSourceList",
                params=[{"scheme": scheme}]
            )

            if not sources_result or "result" not in sources_result:
                continue

            sources_data = sources_result["result"]
            sources = []

            if sources_data and len(sources_data) > 0:
                first_item = sources_data[0]
                if isinstance(first_item, list):
                    sources = first_item
                elif isinstance(first_item, dict):
                    sources = sources_data

            for source in sources:
                source_uri = None
                source_title = scheme

                if isinstance(source, dict):
                    source_uri = source.get('uri') or source.get('source')
                    source_title = source.get('title', scheme)
                elif isinstance(source, str):
                    source_uri = source

                if not source_uri:
                    continue

                channels = self._fetch_channels_from_source(source_uri, source_title)
                all_channels.extend(channels)

        logger.info(f"Total channels found: {len(all_channels)}")
        return all_channels

    def _fetch_channels_from_source(self, source_uri: str, source_title: str) -> List[Dict]:
        """Fetch channels from a specific source URI"""
        channels = []

        uris_to_try = [source_uri]
        if '?' not in source_uri:
            for tuner in range(0, 2):
                uris_to_try.append(f"{source_uri}?tuner={tuner}")

        for uri in uris_to_try:
            for start_idx in range(0, 200, 50):
                content_result = self._make_request(
                    "avContent",
                    "getContentList",
                    params=[{"uri": uri, "stIdx": start_idx, "cnt": 50}],
                    version="1.5"
                )

                if not content_result or "result" not in content_result:
                    continue

                content_data = content_result["result"]
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
                        channel = {
                            'number': item.get('index', ''),
                            'name': item.get('title', 'Unknown'),
                            'uri': item.get('uri', ''),
                            'source': source_title
                        }

                        if not any(c['uri'] == channel['uri'] for c in channels):
                            channels.append(channel)

                if len(items) < 50:
                    break

        return channels

    def _is_tv_channel(self, item: Dict) -> bool:
        """Determine if an item is a TV channel"""
        if item.get('programMediaType') == 'tv':
            return True

        uri_str = item.get('uri', '').lower()
        if any(pattern in uri_str for pattern in ['sid=', 'channel=', 'program=']):
            return True

        title = item.get('title', '')
        if title and len(title) > 1:
            if any(word in title.lower() for word in
                  ['bbc', 'itv', 'channel', 'tv', 'hd', 'news', 'cbs', 'nbc', 'abc', 'fox']):
                return True
            if re.search(r'\d', title):
                return True

        return False

    def get_channels(self, force_refresh: bool = False) -> List[Dict]:
        """Get channels with caching"""
        if force_refresh or not self.channels_last_updated:
            logger.info("Fetching channels from TV")
            self.channels = self.fetch_channels_from_tv()
            self.channels_last_updated = datetime.now()
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

        numbers = re.findall(r'\d+', channel.get('name', ''))
        return int(numbers[0]) if numbers else 9999

    def switch_to_channel(self, channel_identifier: str) -> bool:
        """Switch to a specific channel by number or name"""
        logger.info(f"Switching to channel: {channel_identifier}")

        channels = self.get_channels()

        # Find channel by number or name
        target_channel = None
        for channel in channels:
            if str(channel.get('number')) == str(channel_identifier):
                target_channel = channel
                break

        if not target_channel:
            channel_id_lower = channel_identifier.lower()
            for channel in channels:
                if channel_id_lower in channel.get('name', '').lower():
                    target_channel = channel
                    break

        if not target_channel:
            logger.warning(f"Channel '{channel_identifier}' not found")
            return False

        uri = target_channel.get('uri')
        if not uri:
            return False

        result = self._make_request(
            "avContent",
            "setPlayContent",
            params=[{"uri": uri}],
            version="1.0"
        )

        return result is not None and "result" in result


class TVRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler for TV web service"""

    def __init__(self, *args, **kwargs):
        self.tv = None
        self.html_content = None
        super().__init__(*args, **kwargs)

    def load_config_and_html(self):
        """Load configuration and HTML template"""
        try:
            # Load config
            with open('tv.toml', 'rb') as f:
                config = tomllib.load(f)

            tv_config = config.get('tv', {})
            self.tv = SonyTVController(
                ip_address=tv_config.get('ip_address', '192.168.1.100'),
                access_code=tv_config.get('access_code', ''),
                timeout=tv_config.get('timeout', 5)
            )

            # Load HTML template
            html_path = os.path.join(os.path.dirname(__file__), 'tv_remote.html')
            with open(html_path, 'r', encoding='utf-8') as f:
                self.html_content = f.read()

            return True
        except FileNotFoundError as e:
            logger.error(f"File not found: {e}")
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

    def _send_html(self):
        """Send HTML response"""
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()

        # Replace placeholder with actual TV IP
        html = self.html_content.replace('{{TV_IP}}', self.tv.ip_address)
        self.wfile.write(html.encode('utf-8'))


    def _send_file(self, path):
        """Send a file with appropriate MIME type based on extension"""
        try:
            # Remove leading slash and handle empty path
            if path.startswith('/'):
                path = path[1:]

            # If path is empty, serve index.html
            if not path:
                path = 'index.html'

            # Get the file extension
            _, ext = os.path.splitext(path)
            ext = ext.lower()

            # Define MIME types mapping
            mime_types = {
                '.html': 'text/html',
                '.htm': 'text/html',
                '.css': 'text/css',
                '.js': 'application/javascript',
                '.json': 'application/json',
                '.png': 'image/png',
                '.jpg': 'image/jpeg',
                '.jpeg': 'image/jpeg',
                '.gif': 'image/gif',
                '.svg': 'image/svg+xml',
                '.ico': 'image/x-icon',
                '.txt': 'text/plain',
            }

            # Get MIME type or default to binary
            content_type = mime_types.get(ext, 'application/octet-stream')

            # Determine file path - look in current directory and static subdirectory
            file_paths = [
                os.path.join(os.path.dirname(__file__), path),
                os.path.join(os.path.dirname(__file__), 'static', path)
            ]

            file_found = False
            for file_path in file_paths:
                if os.path.exists(file_path) and os.path.isfile(file_path):
                    file_found = True
                    break

            if not file_found:
                self._send_error(404, f'File not found: {path}')
                return

            # Read and send the file
            with open(file_path, 'rb') as f:
                content = f.read()

            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Expires', '0')
            self.end_headers()

            self.wfile.write(content)

        except PermissionError:
            self._send_error(403, f'Permission denied: {path}')
        except Exception as e:
            logger.error(f"Error serving file {path}: {e}")
            self._send_error(500, f'Internal server error')

    def do_GET(self):
        """Handle GET requests"""
        if not self.load_config_and_html():
            self._send_error(500, "Configuration or HTML template not loaded")
            return

        parsed_url = urlparse(self.path)
        path = parsed_url.path
        query = parse_qs(parsed_url.query)

        # API endpoints
        if path == '/':
            self._send_html()
        elif path == '/api/channels':
            self.handle_get_channels(query)
        elif path == '/api/status':
            self.handle_get_status()
        elif path == '/api/volume':
            self.handle_get_volume()
        elif path.startswith('/api/switch/'):
            channel = path.replace('/api/switch/', '')
            self.handle_switch_channel(channel)
        elif path == '/api/power/on':
            self.handle_power_on()
        elif path == '/api/power/off':
            self.handle_power_off()
        elif path == '/api/volume/up':
            self.handle_volume_up()
        elif path == '/api/volume/down':
            self.handle_volume_down()
        elif path == '/api/volume/mute':
            self.handle_volume_mute()
        elif path == '/api/volume/unmute':
            self.handle_volume_unmute()
        elif path == '/api/refresh':
            self.handle_refresh_channels()
        else:
            self._send_file(path)

    def handle_get_channels(self, query):
        """Get channel list"""
        refresh = query.get('refresh', ['false'])[0].lower() == 'true'
        channels = self.tv.get_channels(force_refresh=refresh)

        self._send_response(200, {
            'channels': channels,
            'total': len(channels),
            'last_updated': self.tv.channels_last_updated.isoformat() if self.tv.channels_last_updated else None
        })

    def handle_get_status(self):
        """Get TV status"""
        connected = self.tv.check_connection()
        power = self.tv.get_power_status() if connected else "unknown"
        channels = self.tv.get_channels()

        self._send_response(200, {
            'connected': connected,
            'power': power,
            'tv_ip': self.tv.ip_address,
            'channels': {
                'total': len(channels),
                'last_updated': self.tv.channels_last_updated.isoformat() if self.tv.channels_last_updated else None
            }
        })

    def handle_get_volume(self):
        """Get volume and mute status"""
        volume = self.tv.get_volume()
        mute = self.tv.get_mute_status()

        self._send_response(200, {
            'volume': volume,
            'muted': mute
        })

    def handle_switch_channel(self, channel):
        """Switch to channel"""
        if not channel:
            self._send_error(400, "Channel identifier required")
            return

        success = self.tv.switch_to_channel(channel)
        if success:
            self._send_response(200, {'message': f'Switched to channel {channel}'})
        else:
            self._send_error(404, f'Channel "{channel}" not found')

    def handle_power_on(self):
        """Turn TV on"""
        success = self.tv.power_on()
        if success:
            self._send_response(200, {'message': 'Power on command sent'})
        else:
            self._send_error(500, 'Failed to send power on command')

    def handle_power_off(self):
        """Turn TV off"""
        success = self.tv.power_off()
        if success:
            self._send_response(200, {'message': 'Power off command sent'})
        else:
            self._send_error(500, 'Failed to send power off command')

    def handle_volume_up(self):
        """Increase volume"""
        success = self.tv.volume_up()
        if success:
            volume = self.tv.get_volume()
            self._send_response(200, {'message': 'Volume up', 'volume': volume})
        else:
            self._send_error(500, 'Failed to increase volume')

    def handle_volume_down(self):
        """Decrease volume"""
        success = self.tv.volume_down()
        if success:
            volume = self.tv.get_volume()
            self._send_response(200, {'message': 'Volume down', 'volume': volume})
        else:
            self._send_error(500, 'Failed to decrease volume')

    def handle_volume_mute(self):
        """Mute audio"""
        success = self.tv.mute()
        if success:
            self._send_response(200, {'message': 'Muted'})
        else:
            self._send_error(500, 'Failed to mute')

    def handle_volume_unmute(self):
        """Unmute audio"""
        success = self.tv.unmute()
        if success:
            self._send_response(200, {'message': 'Unmuted'})
        else:
            self._send_error(500, 'Failed to unmute')

    def handle_refresh_channels(self):
        """Refresh channel list"""
        channels = self.tv.get_channels(force_refresh=True)
        self._send_response(200, {
            'message': 'Channel list refreshed',
            'total': len(channels),
            'channels': channels
        })

    def log_message(self, format, *args):
        """Override to use our logger"""
        logger.info(f"{self.address_string()} - {format % args}")


def main():
    """Main function to start the web service"""
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

    server_config = config.get('server', {})
    host = server_config.get('host', '0.0.0.0')
    port = server_config.get('port', 8080)
    debug = server_config.get('debug', False)

    if debug:
        logging.getLogger().setLevel(logging.DEBUG)

    # Test connection
    tv_config = config.get('tv', {})
    tv = SonyTVController(
        ip_address=tv_config.get('ip_address', ''),
        access_code=tv_config.get('access_code', ''),
        timeout=tv_config.get('timeout', 5)
    )

    print("\n" + "="*70)
    print("📺 SONY TV WEB SERVICE")
    print("="*70)

    print(f"\n📡 Testing connection to TV at {tv.ip_address}...")
    if tv.check_connection():
        print("✅ Successfully connected to TV")

        power = tv.get_power_status()
        print(f"⚡ TV Power Status: {power}")

        print("\n🔍 Scanning for channels...")
        channels = tv.fetch_channels_from_tv()
        print(f"✅ Found {len(channels)} channels")
    else:
        print("⚠️  Could not connect to TV - check configuration")

    # Start server
    server_address = (host, port)
    httpd = HTTPServer(server_address, TVRequestHandler)

    print(f"\n🌐 Web interface available at:")
    print(f"   http://{host if host != '0.0.0.0' else 'localhost'}:{port}/")
    print(f"\n📋 Features:")
    print(f"   - Power ON/OFF")
    print(f"   - Volume Up/Down/Mute")
    print(f"   - Live channel switching")
    print(f"   - Instant search filtering")
    print(f"   - 5 channels per row layout")
    print(f"\n🛑 Press Ctrl+C to stop")
    print("="*70)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n\n👋 Shutting down...")
        httpd.shutdown()


if __name__ == "__main__":
    main()
