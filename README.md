# sony-tv-control

## What is it

Small web service and web client to control your SONY TV using its REST API.

99% AI-generated code, list of prompts and manual changes are included.

## The story

The battery is on our RC is started to discharge,
so I've downloaded Android apps,
which can control our Sony smart TV.
Tried some, but I was disappointed,
asked DeepSeek if Sony TVs have some API.
It showed me the POC, which looked great,
so I decided to vibe-code my version,
a webapp with Python backend.

## Features

- Implemented basic remote control functions:
  - Power ON/OFF
  - Volume Slider and Mute
  - Live channel switching
- Instant channel filtering
- Blacklist for defunct channels
- Responsive design
- Can be installed as webapp

## Setup

You should set up Access Code in your TV.

Configuration is self-explanatory:
```
[tv]
ip_address = "tv"
access_code = "qqqqqqqqqqqqqqqqqqqq"  # whatever you set for the TV
channel_filter = [".*test$", "^mtv.*"]

[server]
port = 8080
host = "0.0.0.0"
```

You should start the Python script on a machine,
which is on the same local network as the TV.

## V1

I was using DeepSeek's web interface.
Also made some changes by hand.

### Started as POC

I've read somewhere that Sony TVs has HTTP API,
let's ask AI:
```
how to access a sony tv using http
```

I've set up the TV (entered access key),
ask AI to write a small backend:
```
rest get tv channels
```

Listing TV channels requires 3 step,
I did not wanted to write it myself:
```
write python program which lists tv channels
```

Remove dependencies:
```
write python program which lists tv channels without bravia library
```

### The service

Okay, let's make a full working service:
```
write python web service, simple GET API
read tv IP address and acces code from tv.toml configuration
also read webserver port from config
/list get list of tv channels
/switch/3 switch to channel 3
```

I don't want to enter my channel list of 200 channels:
```
modify sony-tv-web.service.py
get channel list from tv, not from config
```

Fix error, please, just copied error message (part):
```
  File "/Users/ern0/work/sony-tv-control/./sony_web_tv.py", line 113, in <genexpr>
    tv_schemes = [s for s in schemes if any(kw in s.lower() for kw in tv_keywords)]
                                                  ^^^^^^^
AttributeError: 'dict' object has no attribute 'lower'
```

### The UI

I wanted to do it later, but the AI already added
a single-page web UI.

It was quite good, but after channel select,
it jumped to the result JSON, let's fix it:
```
use ajax on web interface switch to channel
do not use external javascript libraries
upon channel switch move selected channel to top
```

Great, only some minor features are missing:
```
add on off function
```

I've changed button order manually.
E.g. volume up was at left, down at right.
Yes, one-column mode it will be wrong,
should be fixed by changing order dynamically,
but we'll use it with big smartphones.

Finally, I've changed 5-column mode (we've 200 channels),
fixed search field (it was searching for channel number)
and finally separated backend and frontend,
all the HTML+CSS+JS was embedded into the Python code:
```
Remove toggle power, keep on and off
Add volume up, down and mute
Do not reload channel list by timer
Use smaller panels, default 5 per row
Search field instant result, show-hide items by channel name
Separate HTML and Python file
```

### Bugfix: bad array index

In the backend function `_fetch_channels_from_source()`,
the AI missed the index of the channel list array:
```
if self._is_tv_channel(item):
    channel = {
        'number': item.get('channelNumber', ''),
        'name': item.get('title', 'Unknown'),
        'uri': item.get('uri', ''),
        'source': source_title
    }
```

It had to change to:
```
        'number': item.get('index', ''),
```

### Bugfix: type mismatch

Comparing different types is a typical AI bug:
```
for channel in channels:
    if channel.get('number') == channel_identifier:
```

Changed to (safe play):
```
for channel in channels:
    if str(channel.get('number')) == str(channel_identifier):
```

## V2 changes - responsive

Looked ugly on mobile:
```
make responsive
```

## V3 changes - webapp, better UI, features

### Convert to webapp

Added webapp tags to HTML:
```
<meta name="theme-color" content="#2196F3">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="SONY TV Control">
<meta name="mobile-web-app-capable" content="yes">
```

Added 192x192 icon, fetched an image from the web,
then cropped and resized.

Generated app manifest file.

On backend, added a function call by hand,
in order to serve files (icon and manifest):
```
elif path == '/api/refresh':
    self.handle_refresh_channels()
else:
    self._send_file(path)
```

Then instructed AI to write the missing function:
```
write _send_file(path)
send file, detect mime type from extension: html, css, js, png and json
```

### Bugfix: eliminate unnecessary API calls

Sometimes, especially upon startup,
channel switches were somewhat unreliable,
sometimes they failed.

After some investigations, I've found a serious issue:
the backend fetched channels from the TV on each
channel switch:
```
2026-02-18 19:36:20,394 - INFO - Switching to channel: 2
2026-02-18 19:36:20,394 - INFO - Fetching channels from TV
2026-02-18 19:36:20,394 - INFO - Fetching channels directly from TV...
2026-02-18 19:36:20,871 - INFO - Total channels found: 200
2026-02-18 19:36:21,045 - INFO - 192.168.8.120 - "GET /api/switch/2 HTTP/1.1" 200 -
```

The channel switching function asks for channel list:
```
    def switch_to_channel(self, channel_identifier: str) -> bool:
        """Switch to a specific channel by number or name"""
        logger.info(f"Switching to channel: {channel_identifier}")

        channels = self.get_channels()
```

The `get_channels()` is a lazy method,
caches and simetimes updates the channel list:
```
    def get_channels(self, force_refresh: bool = False) -> List[Dict]:
        """Get channels with caching"""

        # print(f"GETCH refr={force_refresh} last={self.channels_last_updated}")

        if force_refresh or not self.channels_last_updated:
            logger.info("Fetching channels from TV")
            self.channels = self.fetch_channels_from_tv()
            self.channels_last_updated = datetime.now()
            self.channels.sort(key=lambda x: self._extract_channel_number(x))

        return self.channels
```

Turned out, `self.channels_last_updated` is always None,
so the channel cache gets always updating from the TV,
which is quite overhead for a channel switch.

As you see, in the `get_channels()` method,
`self.channels_last_updated` is set properly.
So, somewhere it's cleared unnecessary, let's find it.

```
class SonyTVController:

    def __init__(self, ip_address: str, access_code: str, timeout: int = 5):

        # print("########## ctor")
        self.ip_address = ip_address
        self.access_code = access_code
        self.timeout = timeout
        self.base_url = f"http://{ip_address}/sony"
        self.channels = []
        self.channels_last_updated = None
        self.channels_cache_duration = 300  # 5 minutes cache
```

It happens only in the constructor, which is fine.
Just check it:
```
########## ctor
2026-02-18 19:49:58,247 - INFO - Switching to channel: 1
GETCH refr=False last=None
2026-02-18 19:49:58,248 - INFO - Fetching channels from TV
2026-02-18 19:49:58,248 - INFO - Fetching channels directly from TV...
2026-02-18 19:49:58,737 - INFO - Total channels found: 200
2026-02-18 19:49:58,830 - INFO - 192.168.8.120 - "GET /api/switch/1 HTTP/1.1" 200 -
########## ctor
2026-02-18 19:50:02,336 - INFO - Switching to channel: 2
```

Wow, a new object is created for each request.
The root cause is that `load_config_and_html()`
method, which creates the new object, is called
from the main request handler:
```
    def do_GET(self):
        """Handle GET requests"""
        if not self.load_config_and_html():
            self._send_error(500, "Configuration or HTML template not loaded")
            return
```

The fix is easy, prompted:
```
do not load config on each request
```

And the result is:
```
2026-02-19 10:04:37,217 - INFO - Switching to channel: 1
2026-02-19 10:04:37,217 - INFO - Fetching channels from TV
2026-02-19 10:04:37,217 - INFO - Fetching channels directly from TV...
2026-02-19 10:04:37,861 - INFO - Total channels found: 200
2026-02-19 10:04:38,006 - INFO - 192.168.8.120 - "GET /api/switch/1 HTTP/1.1" 200 -
2026-02-19 10:04:46,261 - INFO - Switching to channel: 9
2026-02-19 10:04:46,429 - INFO - 192.168.8.120 - "GET /api/switch/9 HTTP/1.1" 200 -
```

I can even feel the difference on the frontend as well.

### Polish GUI

On mobile devices, the header is just too tall,
occupies lot of space:
```
remove large title
put buttons and status information to a single line, use smaller buttons
change volume up and down buttons to a slider
add search button
```

Hm, volume slider does not work:
```
2026-02-19 10:27:58,863 - INFO - 192.168.8.108 - "POST /api/volume/set/7 HTTP/1.1" 501 -
2026-02-19 10:28:06,857 - INFO - 192.168.8.108 - code 501, message Unsupported method ('POST')
```

Let's fix it:
```
Change volume set to GET
```

And also change API:
```
change volume up and volume down API calls to /volume/set/{value}
```

### Feature: filter out garbage channels

Our channel list is full of non-working ones.
They should be filtered out by name.

```
when creating channell list, use config for skip channels by name, use regex
filter = ["^megsz.*", ".*teszt$", "^mtv.*"]
```

> "megsz" is for "megszűnt" (discontinued) etc.,
but instead of the letter "ű",
there's a garbage character in the channel's name.
Also MTV is ended this year :(

The config key somehow became "channel_filters" and
it should be in the "[tv]" section,
but I can live with it.
