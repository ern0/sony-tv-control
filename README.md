# sony-tv-control

Small web service and web client to control your SONY TV using its REST API.

99% AI-generated code, list of prompts and manual changes are included.

## 📋 Features

- Power ON/OFF
- Volume Up/Down/Mute
- Live channel switching
- Instant search filtering
- 5 channels per row layout

## Setup

You should set up Access Code in your TV.

Configuration is self-explanatory:
```
[tv]
ip_address = "tv"
access_code = "qqqqqqqqqqqqqqqqqqqq"  # whatever you set for the TV

[server]
port = 8080
host = "0.0.0.0"
```

You should start the Python script on a machine,
which is on the same local network as the TV is on.

## Prompts

I was using DeepSeek's web interface.

### POC

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

### Service

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

## V2 changes

### Prompt

Looked ugly on mobile:
```
make responsive
```

### Code change

Added webapp tags to HTML:
```
<meta name="theme-color" content="#2196F3">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="SONY TV Control">
<meta name="mobile-web-app-capable" content="yes">
```

## Manual changes

### Button order

I've changed button order manually.
E.g. volume up was at left, down at right.
Yes, one-column mode it will be wrong,
should be fixed by changing order dynamically,
but we'll use it with big smartphones.

### Bad array index

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

### Type mismatch

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
