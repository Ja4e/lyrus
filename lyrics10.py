"""
MIT License
Copyright (c) 2025 Saul Gman
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:
The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.
THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""
"""
CMUS Lyrics Viewer with Synchronized Display
Displays time-synced lyrics for cmus music player using multiple lyric sources

Remember fetched lyrics has inaccuracies... this code has a very robust snyc to your current play position you can adjust whatever you want
"""

# ==============
#  DEPENDENCIES
# ==============
import curses  # Terminal UI framework
try:  # Optional Redis import just ignore you dont need to actually need this yet
	import redis
except ImportError:
	redis = None
import aiohttp  # Async HTTP client
import threading # For tracking Status
import concurrent.futures # For concurrent API requests
from concurrent.futures import ThreadPoolExecutor
import subprocess  # For cmus interaction
import re  # Regular expressions
import os  # File system operations
import sys
import bisect  # For efficient list searching
import time  # Timing functions
import textwrap  # Text formatting
import requests  # HTTP requests for lyric APIs
import urllib.parse  # URL encoding
import urllib.request # Network detection triggers
import syncedlyrics  # Lyric search library
import multiprocessing  # Parallel lyric fetching
import asyncio
from datetime import datetime, timedelta  # Time handling for logs
from mpd import MPDClient  # MPD support
import socket # used for listening for common mpd port 6600
import json
from functools import partial

# ==============
#  GLOBALS
# ==============
sync_results = {
	'bisect_index': 0,
	'proximity_index': 0,
	'lock': threading.Lock()
}
LOG_LEVELS = {
	"FATAL": 5,
	"ERROR": 4,
	"WARN": 3,
	"INFO": 2,
	"DEBUG": 1,
	"TRACE": 0
}


# ==============
#  CONFIGURATION
# ==============
config_files = ["config.json", "config1.json", "config2.json"] # change the configuration name if you wanted, just make sure you sture them in the same directory of this project

def load_config():
	"""Load and merge configuration from file and environment"""
	default_config = {
		"global": {
			"logs_dir": "logs",
			"log_file": "application.log", # useless line but incase i decided to update the code
			"log_level": "FATAL", # a list of log levels shown above in LOG_LEVELS dictionary
			"lyrics_timeout_log": "lyrics_timeouts.log", # stored under that logs_dir
			"debug_log": "debug.log", # stored under that logs_dir
			"log_retention_days": 10, # resets the songs that in timedout log in days
			"max_debug_count": 100, # just incase the log fire gets abdnormally big.
			"max_log_count": 100,
			"enable_debug": {"env": "DEBUG", "default": "0"}  # debug environment can be enabled through terminal:  DEBUG=1 python lyrics.py very useful for ease of debugging
		},
		"player": {
			"prioritize_cmus": True, # Highly customizable 
			"mpd": { # just incase you need to change them by default it should work
				"host": {"env": "MPD_HOST", "default": "localhost"},
				"port": {"env": "MPD_PORT", "default": 6600},
				"password": {"env": "MPD_PASSWORD", "default": None},
				"timeout": 10
			}
		},
		"redis": { # need to implement more robust system for this for now just ignore
			"enabled": False,
			"host": {"env": "REDIS_HOST", "default": "localhost"},
			"port": {"env": "REDIS_PORT", "default": 6379}
		},
		"status_messages": { # ignore these but if you wanted custom msg there you go then
			"start": "Starting lyric search...",
			"local": "Checking local files",
			"synced": "Searching online sources",
			"lrc_lib": "Checking LRCLIB database",
			"instrumental": "Instrumental track detected",
			"time_out": "In time-out log",
			"failed": "No lyrics found",
			"mpd": "scanning for MPD activity",
			"cmus": "loading cmus",
			"done": "Loaded",
			"clear": ""
		},
		"terminal_states": ["done", "instrumental", "time_out", "failed", "mpd", "clear", "cmus"], # ignore these too
		"lyrics": { # possible tweakings for poor networks
			"search_timeout": 15,
			"cache_dir": "synced_lyrics",
			"local_extensions": ["a2", "lrc", "txt"], # a2 currently broken dont use that yet
			"validation": {"title_match_length": 15, "artist_match_length": 15}
		},
		"ui": {
			"alignment": "left",  # Options: "left", "center", "right" you get thee idea
			"name": True, # Do false if you wanted to hide the song name whatsover
			"colors": {
				"txt": {
					"active": {"env": "TXT_ACTIVE", "default": "254"},  # or in numbers ranging from 0-256 will add support for hex color
					"inactive": {"env": "TXT_INACTIVE", "default": "white"}  # Dark gray
				},
				"lrc": {
					"active": {"env": "LRC_ACTIVE", "default": "green"},     # Greenish
					"inactive": {"env": "LRC_INACTIVE", "default": "250"} # Yellow changed to grey
				},
				"error": {"env": "ERROR_COLOR", "default": 196}         # Bright red
			},
			"scroll_timeout": 4, # scroll timeout to auto scroll
			"refresh_interval_ms": 1000, # delays on continuations when nothing is triggered delays on fetching player infos just incase your cpu is absolute bs, dont increase unless its necessary sorry i overcoded this part, increase this if mpd fills up your local bandwidth #100 or 0, I would recommend you to include this ms latency into that snyc offset sec
			"coolcpu_ms": 100, # cool cpu, your cpu will fill up 100% in one core if set to 0 in my case it will shoot up to 30 the small gains arent worthed it #10 or 100
			
			
			"smart-tracking": 0, # incase you need to enable it, it will certainly lock to the next early but accurate This is not in boolean format because i will implement more sophiscated ones in future
			"bisect_offset": 0,  # Time offset (in seconds) added to the current position before bisecting.
									# Helps in slightly anticipating the upcoming timestamp, reducing jitter and improving sync stability.
									# Value of 0.01 (~10ms) smooths transitions while avoiding premature jumps.

			"proximity_threshold": 0,  # Fractional threshold used to determine when to switch to the next timestamp line.
										  # If more than 99% of the current line duration has passed, it allows switching early.
										  # Value of 0.01 enables precise, stable lyric syncing with minimal visible delay or flicker. Not implemented yet can be changed over if needed
										  
		
		
			"wrap_width_percent": 90,  # Just incase you need them need better implementations not yet implemented
			"smart_refresh_duration": 1, # in second hmmm not implemented yet just leave this alone Actually I implemented to define an optional way to where it could trigerr something/s
			#"smart_refresh_interval": 80, in experimental
			"smart_coolcpu_ms": 20, # used by triggers , Just keep it like this just to keep the number accurate and syncd
			"jump_threshold_sec": 1, # Please do adjust this so it does not cause too much cpu cycles, this is at point where the cpu matter the most, cmus updates in seconds  sigmas rizzler
			"end_trigger_threshold_sec": 1,
			
			"smart-proximity": True, # turns the proximity on just to keep up the next line being sync regardless of speed of the lyrics it will use the smart coolcpu ms freq , Might need to separate this with the refresh interval ms somewhere
			"refresh_proximity_interval_ms": 150, #originally 100
			"smart_coolcpu_ms_v2": 50, # used by proximity to keep the lyrics sync to patch stupid issue with long refresh interval ms and cmus's 1ms interval updates
			
			"proximity_threshold_sec": 0.1, # original 0.1
			"proximity_threshold_percent": 500, # original 2
			"proximity_min_threshold_sec": 0.00, # original 0.01
			#"proximity_min_threshold_sec": 0.2,
			"proximity_max_threshold_sec": 1, # Just capping originall is 2.0 seems unecessary
			
			"sync_offset_sec": 0.0008, # just incase uhhh thiis script does not follow what your monitor's refresh rate so it will be inregularrities in tracking, already hurting my eyes, will implement this
			
			# "sync_offset_sec": -0.015,
			# try to adjust them based on that refresh interval ms
			# "sync_offset_sec": 0.095,
			# "sync_offset_sec": 0.32,
			# "sync_offset_sec": 0.045,
			# "sync_offset_sec": 0.125, # perfect? maybe should be good enough anyway but bewarned the high coolcpu ms may not work properly for fast paced lyrics nevermind it works properly with proximity feature shit was complete an exact ratio against 0ms to 0ms
		},
		"key_bindings": { # Set as "null" if you do not want it assigned 
			"quit": ["q", "Q"], # kinds of broken in this implementation but i will fix it, its no big deal
			"refresh": "R",
			"scroll_up": "KEY_UP", # keep it the same or you wanted it customized
			"scroll_down": "KEY_DOWN", #same for this too
			"time_decrease": ["-", "_"],
			"time_increase": ["=", "+"],
			"time_jump_increase": ["]"], # you incase you need them.
			"time_jump_decrease": ["["],
			"time_reset": "0",
			"align_cycle_forward": "a",
			"align_cycle_backward": "A",
			"align_left": "1",
			"align_center": "2",
			"align_right": "3"
		}	
	}
	
	# Merge with found config files
	for file in config_files:
		if os.path.exists(file):
			try:
				with open(file) as f:
					file_config = json.load(f).get("config", {})
					if "global" in file_config and "logs_dir" not in file_config["global"]:
						file_config["global"]["logs_dir"] = "logs"
					# Deep merge strategy
					for key in file_config:
						if key in default_config:
							default_config[key].update(file_config[key])
						else:
							default_config[key] = file_config[key]
				print(f"Successfully loaded and merged config from {file}")
				break 
			except Exception as e:
				pass
		else:
			pass


	def resolve(item):
		if isinstance(item, dict) and "env" in item:
			return os.environ.get(item["env"], item.get("default"))
		return item

	for section in ["mpd"]:
		for key in default_config["player"][section]:
			default_config["player"][section][key] = resolve(default_config["player"][section][key])
	
	for key in default_config["redis"]:
		default_config["redis"][key] = resolve(default_config["redis"][key])

	default_config["global"]["enable_debug"] = resolve(default_config["global"]["enable_debug"]) == "1"

	return default_config

CONFIG = load_config()

# ==============
#  INITIALIZATION
# ==============

LOG_DIR = CONFIG["global"]["logs_dir"]
try:
	created = not os.path.exists("logs")
	os.makedirs("logs", exist_ok=True)
	if created:
		print(f"Directory 'logs' created at: {os.path.abspath('logs')}")

except Exception as e:
	print(f"CRITICAL ERROR: Failed to create logs directory - {str(e)}")
	raise SystemExit(1)

if not os.path.exists("logs"):
	print("FATAL: 'logs' directory missing after creation attempt")
	raise SystemExit(1)

LYRICS_TIMEOUT_LOG = CONFIG["global"]["lyrics_timeout_log"]
DEBUG_LOG = CONFIG["global"]["debug_log"]
LOG_RETENTION_DAYS = CONFIG["global"]["log_retention_days"]
MAX_DEBUG_COUNT = CONFIG["global"]["max_debug_count"]

ENABLE_DEBUG_LOGGING = CONFIG["global"]["enable_debug"]
# Debug startup message if enabled
if ENABLE_DEBUG_LOGGING:
	debug_msg = "Debug logging ENABLED"
	print(debug_msg)  # Confirm in console
	print("=== Application started ===")
	print(f"Loaded config: {json.dumps(CONFIG, indent=2)}")

# Redis connection
REDIS_ENABLED = CONFIG["redis"]["enabled"] and redis is not None
redis_client = None
if REDIS_ENABLED:
	try:
		redis_client = redis.Redis(
			host=CONFIG["redis"]["host"],
			port=CONFIG["redis"]["port"],
			decode_responses=True
		)
		redis_client.ping()  # Test connection
		log_info("Redis connected successfully")
	except Exception as e:
		REDIS_ENABLED = False
		print(f"Redis connection failed: {str(e)}. Disabling Redis features.")
elif CONFIG["redis"]["enabled"]:  # Config enabled but module missing
	print("Redis enabled in config but package not installed. Install with 'pip install redis'")

# Player configuration
MPD_HOST = CONFIG["player"]["mpd"]["host"] 
MPD_PORT = CONFIG["player"]["mpd"]["port"] 
MPD_PASSWORD = CONFIG["player"]["mpd"]["password"] 
MPD_TIMEOUT = CONFIG["player"]["mpd"]["timeout"]
PRIORITIZE_CMUS = CONFIG["player"]["prioritize_cmus"]

# Lyrics configuration
LYRIC_EXTENSIONS = CONFIG["lyrics"]["local_extensions"]
LYRIC_CACHE_DIR = CONFIG["lyrics"]["cache_dir"]
SEARCH_TIMEOUT = CONFIG["lyrics"]["search_timeout"]
VALIDATION_LENGTHS = CONFIG["lyrics"]["validation"]

# UI configuration
COLOR_NAMES = {
	"black": 0, "red": 1, "green": 2, "yellow": 3,
	"blue": 4, "magenta": 5, "cyan": 6, "white": 7
}

DISPLAY_NAME = CONFIG["ui"]["name"]

def get_color_value(color_input):
	"""Convert color input to valid terminal color number (0-255)"""
	# Get terminal capabilities first
	curses.start_color()
	max_colors = curses.COLORS if curses.COLORS > 8 else 8
	
	try:
		# Handle numeric inputs (string or integer)
		if isinstance(color_input, (int, str)) and str(color_input).isdigit():
			return max(0, min(int(color_input), max_colors - 1))
		
		# Handle named colors
		if isinstance(color_input, str):
			color = color_input.lower()
			return COLOR_NAMES.get(color, 7)  # Default to white
			
		return 7  # Fallback to white
	except Exception:
		return 7  # Fallback on any error

def resolve_color(setting):
	"""Resolve color from config with environment override"""
	# Get value from environment or config
	raw_value = os.environ.get(
		setting["env"], 
		setting.get("default", 7)
	)
	return get_color_value(raw_value)

SCROLL_TIMEOUT = CONFIG["ui"]["scroll_timeout"]
REFRESH_INTERVAL = CONFIG["ui"]["refresh_interval_ms"]
WRAP_WIDTH_PERCENT = CONFIG["ui"]["wrap_width_percent"]

# Status system
MESSAGES = CONFIG["status_messages"]
TERMINAL_STATES = set(CONFIG["terminal_states"])
fetch_status_lock = threading.Lock()
fetch_status = {
	"current_step": None,
	"start_time": None,
	"lyric_count": 0,
	"done_time": None
}

TERMINAL_STATES = {'done', 'instrumental', 'time_out', 'failed', 'mpd', 'clear','cmus'}  # Ensure this is defined


# ================
#  LOGGING SYSTEM
# ================
def clean_debug_log():
	"""Maintain debug log size by keeping only last 100 entries"""
	log_dir = os.path.join(os.getcwd(), "logs")
	log_path = os.path.join(LOG_DIR, DEBUG_LOG)
	
	if not os.path.exists(log_path):
		return

	try:
		# Read existing log contents
		with open(log_path, 'r', encoding='utf-8') as f:
			lines = f.readlines()
		
		# Trim if over 100 lines
		if len(lines) > MAX_DEBUG_COUNT:
			with open(log_path, 'w', encoding='utf-8') as f:
				f.writelines(lines[-MAX_DEBUG_COUNT:])
				
	except Exception as e:
		print(f"Error cleaning debug log: {e}")

def clean_log():
	"""Maintain log size by rotating files"""
	log_dir = os.path.join(os.getcwd(), CONFIG["global"]["logs_dir"])
	log_path = os.path.join(log_dir, CONFIG["global"]["log_file"])
	
	try:
		if os.path.exists(log_path):
			with open(log_path, "r+") as f:
				lines = f.readlines()
				if len(lines) > CONFIG["global"]["max_log_count"]:
					keep = lines[-CONFIG["global"]["max_log_count"]:]
					f.seek(0)
					f.truncate()
					f.writelines(keep)
	except Exception as e:
		print(f"Log cleanup failed: {str(e)}", file=sys.stderr)

def log_message(level: str, message: str):
	"""Unified logging function with level-based filtering and rotation"""
	# Get config values
	log_dir = os.path.join(os.getcwd(), CONFIG["global"]["logs_dir"])
	main_log = os.path.join(log_dir, CONFIG["global"]["log_file"])
	debug_log = os.path.join(log_dir, DEBUG_LOG)
	configured_level = LOG_LEVELS.get(CONFIG["global"]["log_level"], 2)
	message_level = LOG_LEVELS.get(level.upper(), 2)
	
	try:
		# Create log directory if needed
		os.makedirs(log_dir, exist_ok=True)
		# timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
		#timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
		timestamp = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}.{int(time.time() * 1000000) % 1000000:06d}"
		
		# Always write to debug log if enabled and level <= DEBUG
		if CONFIG["global"]["enable_debug"] and message_level <= LOG_LEVELS["DEBUG"]:
			debug_entry = f"{timestamp} | {level.upper()} | {message}\n"
			with open(debug_log, "a", encoding="utf-8") as f:
				f.write(debug_entry)
			clean_debug_log()

		# Write to main log if message level >= configured level
		if message_level >= configured_level:
			main_entry = f"{timestamp} | {level.upper()} | {message}\n"
			with open(main_log, "a", encoding="utf-8") as f:
				f.write(main_entry)
			
			# Rotate main log if needed
			if os.path.getsize(main_log) > CONFIG["global"]["max_log_count"] * 1024:
				clean_log()

	except Exception as e:
		sys.stderr.write(f"Logging failed: {str(e)}\n")

# Specific level helpers
def log_fatal(message: str):
	log_message("FATAL", message)

def log_error(message: str):
	log_message("ERROR", message)

def log_warn(message: str):
	log_message("WARN", message)

def log_info(message: str):
	log_message("INFO", message)

def log_debug(message: str):
	log_message("DEBUG", message)

def log_trace(message: str):
	log_message("TRACE", message)

def update_fetch_status(step, lyrics_found=0):
	with fetch_status_lock:
		fetch_status.update({
			'current_step': step,
			'lyric_count': lyrics_found,
			'start_time': time.time() if step == 'start' else fetch_status['start_time'],
			'done_time': time.time() if step in TERMINAL_STATES else None
		})

def get_current_status(e=None, current_e=None):
	"""Return a formatted status message"""
	current_e = e
	with fetch_status_lock:
		if current_e != e:
			return e
		
		step = fetch_status['current_step']
		if not step:
			return None
		

		# Hide status after 2 seconds for terminal states
		if step in TERMINAL_STATES and fetch_status['done_time']:
			if time.time() - fetch_status['done_time'] > 2:
				return ""

		if step == 'clear':
			return ""

		# Return pre-defined message with elapsed time if applicable
		base_msg = MESSAGES.get(step, step)
		if fetch_status['start_time'] and step != 'done':
			# Use done_time if available for terminal states
			end_time = fetch_status['done_time'] or time.time()
			elapsed = end_time - fetch_status['start_time']
			return f"{base_msg} {elapsed:.1f}s"
		
		return base_msg
		

def has_internet_global(timeout=3):
	global_hosts = ["http://www.google.com", "http://1.1.1.1"]
	china_hosts = ["http://www.baidu.com", "http://www.qq.com"]
	
	for url in (global_hosts + china_hosts):
		try:
			urllib.request.urlopen(url, timeout=timeout)
			return True
		except:
			continue
	return False


# ================
#  ASYNC HELPERS
# ================
async def fetch_lrclib_async(artist, title, duration=None, session=None):
	"""Async version of LRCLIB fetch using aiohttp"""
	base_url = "https://lrclib.net/api/get"
	params = {'artist_name': artist, 'track_name': title}
	if duration:
		params['duration'] = duration

	# Create a new session only if one isn't passed
	own_session = False
	if session is None:
		session = aiohttp.ClientSession()
		own_session = True

	try:
		async with session.get(base_url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as response:
			if response.status == 200:
				try:
					data = await response.json(content_type=None)
					if data.get('instrumental', False):
						return None, None
					return data.get('syncedLyrics') or data.get('plainLyrics'), bool(data.get('syncedLyrics'))
				except aiohttp.ContentTypeError:
					content = await response.text()
					log_debug(f"LRCLIB async error: Invalid JSON. Raw response: {content[:200]}")
			else:
				log_debug(f"LRCLIB async error: HTTP {response.status}")
	except aiohttp.ClientError as e:
		log_debug(f"LRCLIB async error: {e}")
	finally:
		if own_session:
			await session.close()

	return None, None
# async def fetch_lrclib_async(artist, title, duration=None, session=None):
	# """Async version of LRCLIB fetch using aiohttp"""
	# base_url = "https://lrclib.net/api/get"
	# params = {'artist_name': artist, 'track_name': title}
	# if duration:
		# params['duration'] = duration

	# try:
		# # Use existing session if provided, otherwise create one temporarily
		# async with (session or aiohttp.ClientSession()) as s:
			# async with s.get(base_url, params=params) as response:
				# if response.status == 200:
					# try:
						# data = await response.json(content_type=None)
						# if data.get('instrumental', False):
							# return None, None
						# return data.get('syncedLyrics') or data.get('plainLyrics'), bool(data.get('syncedLyrics'))
					# except aiohttp.ContentTypeError:
						# log_debug("LRCLIB async error: Invalid JSON response")
				# else:
					# log_debug(f"LRCLIB async error: HTTP {response.status}")
	# except aiohttp.ClientError as e:
		# log_debug(f"LRCLIB async error: {e}")
	
	# return None, None

# Added comprehensive debug points throughout code
log_trace("Initializing configuration manager")



def log_timeout(artist, title):
	"""Record failed lyric lookup with duplicate prevention"""
	timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
	log_entry = f"{timestamp} | Artist: {artist or 'Unknown'} | Title: {title or 'Unknown'}\n"
	
	log_dir = os.path.join(os.getcwd(), "logs")
	os.makedirs(LOG_DIR, exist_ok=True)
	log_path = os.path.join(LOG_DIR, LYRICS_TIMEOUT_LOG)

	# Check for existing entry
	entry_exists = False
	if os.path.exists(log_path):
		search_artist = artist or 'Unknown'
		search_title = title or 'Unknown'
		with open(log_path, 'r', encoding='utf-8') as f:
			for line in f:
				if (
					f"Artist: {search_artist}" in line and 
					f"Title: {search_title}" in line
				):
					entry_exists = True
					break

	# Add new entry if unique
	if not entry_exists:
		try:
			with open(log_path, 'a', encoding='utf-8') as f:
				f.write(log_entry)
			# clean_old_timeouts()
			clean_log()
		except Exception as e:
			log_debug(f"Failed to write timeout log: {e}")

# ======================
#  CORE LYRIC FUNCTIONS
# ======================
def sanitize_filename(name):
	"""Make strings safe for filenames"""
	return re.sub(r'[<>:"/\\|?*]', '_', name)

def sanitize_string(s):
	"""Normalize strings for comparison"""
	return re.sub(r'[^a-zA-Z0-9]', '', str(s)).lower()

# def fetch_lyrics_lrclib(artist_name, track_name, duration=None):
	# """Sync wrapper for async LRCLIB fetch"""
	# log_debug(f"Querying LRCLIB API: {artist_name} - {track_name}")
	# try:
		# return asyncio.run(fetch_lrclib_async(artist_name, track_name, duration))
# ######################################################################################################
		# if result[0]:
			# log_info(f"LRCLIB returned {'synced' if result[1] else 'plain'} lyrics")
# ######################################################################################################
	# except Exception as e:
		# log_error(f"LRCLIB fetch failed: {str(e)}")
		# return None, None

def fetch_lyrics_lrclib(artist_name, track_name, duration=None):
	"""Sync wrapper for async LRCLIB fetch"""
	log_debug(f"Querying LRCLIB API: {artist_name} - {track_name}")
	try:
		result = asyncio.run(fetch_lrclib_async(artist_name, track_name, duration))
		if result[0]:
			log_info(f"LRCLIB returned {'synced' if result[1] else 'plain'} lyrics")
		return result
	except Exception as e:
		log_error(f"LRCLIB fetch failed: {str(e)}")
		return None, None


# def parse_lrc_tags(lyrics):
	# """Extract metadata tags from LRC lyrics"""
	# tags = {}
	# for line in lyrics.split('\n'):
		# match = re.match(r'^\[(ti|ar|al):(.+)\]$', line, re.IGNORECASE)
		# if match:
			# key = match.group(1).lower()
			# value = match.group(2).strip()
			# tags[key] = value
	# return tags

# def validate_lyrics(content, artist, title): # Too Harsh
	# """Basic validation that lyrics match track"""
	# # Check for timing markers
	# if re.search(r'\[\d+:\d+\.\d+\]', content):
		# return True
		
	# # Check for instrumental markers
	# if re.search(r'\b(instrumental)\b', content, re.IGNORECASE):
		# return True

	# # Normalize strings for comparison
	# def normalize(s):
		# return re.sub(r'[^\w]', '', str(s)).lower().replace(' ', '')[:15]

	# norm_title = normalize(title)[:15]
	# norm_artist = normalize(artist)[:15] if artist else ''
	# norm_content = normalize(content)

	# # Verify title/artist presence in lyrics
	# return (norm_title in norm_content if norm_title else True) or \
		   # (norm_artist in norm_content if norm_artist else True)

def validate_lyrics(content, artist, title):
	"""More lenient validation"""
	# Always allow files with timestamps
	if re.search(r'\[\d+:\d+\.\d+\]', content):
		return True
		
	# Allow empty content for instrumental markers
	if not content.strip():
		return True
		
	# Normalize comparison parameters
	norm_content = sanitize_string(content)
	
	# log_warn(f"Lyrics validation warning for {artist} - {title}")
	
	return True  # Temporary accept all content

def fetch_lyrics_syncedlyrics(artist_name, track_name, duration=None, timeout=15):
	"""Fetch lyrics using syncedlyrics with a fallback"""
	log_debug(f"Starting syncedlyrics search: {artist_name} - {track_name} ({duration}s)")
	try:
		def worker(result_dict, search_term, synced=True):
			log_trace(f"Worker started: {'synced' if synced else 'plain'} search")
			"""Async worker for lyric search"""
			try: 
				""" 
				can be customized I havent implement it yet attach this into these: providers=["NetEase"] 
				allowed providers:
					Musixmatch (requires requested api but apparently it actually fetch it for you)
					Lrclib (my first implementations, im just leaving this implementation as a backward compabilities incase this fails)
					NetEase
					Megalobiz
					Genius
				"""
				result = syncedlyrics.search(search_term) if synced else \
						 syncedlyrics.search(search_term, plain_only=True)
				result_dict["lyrics"] = result
				result_dict["synced"] = synced
			except Exception as e:
				log_debug(f"Lyrics search error: {e}")
				result_dict["lyrics"] = None
				result_dict["synced"] = False
				return None, None

		search_term = f"{track_name} {artist_name}".strip()
		log_trace(f"Formatted search term: '{search_term}'")
		log_trace(f"Starting synced lyrics search for: {search_term}")
		
		if not search_term:
			log_debug("Empty search term")
			return None, None

		# Shared dictionary for results
		manager = multiprocessing.Manager()
		result_dict = manager.dict()

		# Fetch synced lyrics first
		process = multiprocessing.Process(target=worker, args=(result_dict, search_term, True))
		process.start()
		process.join(timeout)

		lyrics, is_synced = result_dict.get("lyrics"), result_dict.get("synced", False)
################################################################################################################
		if lyrics:
			log_debug(f"Found {'synced' if is_synced else 'plain'} lyrics via syncedlyrics")
			if not validate_lyrics(lyrics, artist_name, track_name):
				log_warn("Lyrics validation failed but using anyway")
			return lyrics, is_synced
		else:
			log_debug("No lyrics found in syncedlyrics primary search")
################################################################################################################
		# Check if lyrics are valid
		if lyrics and validate_lyrics(lyrics, artist_name, track_name):
			if is_synced and re.search(r'^\[\d+:\d+\.\d+\]', lyrics, re.MULTILINE):
				return lyrics, True
			else:
				return lyrics, False

		# Cleanup in case of timeout
		if process.is_alive():
			process.terminate()
			process.join()
			log_debug("Synced lyrics search timed out")

		# Fallback to plain lyrics
		log_trace("Initiating plain lyrics fallback search")
		log_info("Falling back to plain lyrics search")
		process = multiprocessing.Process(target=worker, args=(result_dict, search_term, False))
		process.start()
		process.join(timeout)

		lyrics = result_dict.get("lyrics")

		if lyrics and validate_lyrics(lyrics, artist_name, track_name):
			return lyrics, False

		# Cleanup in case of timeout
		if process.is_alive():
			process.terminate()
			process.join()
			log_debug("Plain lyrics search timed out")

		return None, None
	except Exception as e:
		log_debug(f"Lyrics fetch error: {e}")
		return None, None


def save_lyrics(lyrics, track_name, artist_name, extension):
	"""Save lyrics to appropriate file format"""
	folder = os.path.join(os.getcwd(), "synced_lyrics")
	os.makedirs(folder, exist_ok=True)
	
	# Generate safe filename
	sanitized_track = sanitize_filename(track_name)
	sanitized_artist = sanitize_filename(artist_name)
	filename = f"{sanitized_track}_{sanitized_artist}.{extension}"
	file_path = os.path.join("synced_lyrics", filename)
	
	try:
		with open(file_path, "w", encoding="utf-8") as f:
			f.write(lyrics)
		log_info(f"Saved lyrics to: {file_path}")
		log_trace(f"Lyrics content sample: {lyrics[:200]}...")
		return file_path
	except Exception as e:
		log_debug(f"Lyric save error: {e}")
		log_error(f"Failed to save lyrics: {str(e)}")
		return None

def get_cmus_info():
	"""Get current playback info from cmus"""
	try:
		output = subprocess.run(['cmus-remote', '-Q'], 
							   capture_output=True, 
							   text=True, 
							   check=True).stdout.splitlines()
		log_debug("Cmus-remote polling...")
	except subprocess.CalledProcessError:
		log_debug("Error occurred. Aborting...")
		return None, 0, None, None, 0, "stopped"

	# Parse cmus output
	data = {
		"file": None,
		"position": 0,
		"artist": None,
		"title": None,
		"duration": 0,
		"status": "stopped",
		"tags": {}
	}

	for line in output:
		if line.startswith("file "):
			data["file"] = line[5:].strip()
		elif line.startswith("status "):
			data["status"] = line[7:].strip()
		elif line.startswith("position "):
			data["position"] = int(line[9:].strip())
		elif line.startswith("duration "):
			data["duration"] = int(line[9:].strip())
		elif line.startswith("tag "):
			parts = line.split(" ", 2)
			if len(parts) == 3:
				tag_name, tag_value = parts[1], parts[2].strip()
				data["tags"][tag_name] = tag_value

	data["artist"] = data["tags"].get("artist")
	data["title"] = data["tags"].get("title")

	return (data["file"], data["position"], data["artist"], 
			data["title"], data["duration"], data["status"])

def is_lyrics_timed_out(artist_name, track_name):
	"""Check if track is in timeout log"""
	log_path = os.path.join("logs", LYRICS_TIMEOUT_LOG)

	if not os.path.exists(log_path):
		return False

	try:
		with open(log_path, 'r', encoding='utf-8') as f:
			for line in f:
				if artist_name and track_name:
					if f"Artist: {artist_name}" in line and f"Title: {track_name}" in line:
						return True
		return False
	except Exception as e:
		log_debug(f"Timeout check error: {e}")
		return False

def find_lyrics_file(audio_file, directory, artist_name, track_name, duration=None):
	"""Locate or fetch lyrics for current track"""
	update_fetch_status('local')
	log_info(f"Starting lyric search for: {artist_name or 'Unknown'} - {track_name}")
	base_name, _ = os.path.splitext(os.path.basename(audio_file))
	
	local_files = [
		(os.path.join(directory, f"{base_name}.a2"), 'a2'),
		(os.path.join(directory, f"{base_name}.lrc"), 'lrc'),
		(os.path.join(directory, f"{base_name}.txt"), 'txt')
	]

	# Validate existing files
	for file_path, ext in local_files:
		if os.path.exists(file_path):
			try:
				with open(file_path, 'r', encoding='utf-8') as f:
					content = f.read()
				
				if validate_lyrics(content, artist_name, track_name):
					# log_debug(f"Validated local {ext} file")
					log_info(f"Using validated lyrics file: {file_path}")
					return file_path
				else:
					# log_debug(f"Using unvalidated local {ext} file")
					log_info(f"Using unvalidated local {ext} file")
					return file_path
			except Exception as e:
				log_debug(f"File read error: {file_path} - {e}")
				continue

	# Handle instrumental tracks
	is_instrumental = (
		"instrumental" in track_name.lower() or 
		(artist_name and "instrumental" in artist_name.lower())
	)
	
	sanitized_track = sanitize_filename(track_name)
	sanitized_artist = sanitize_filename(artist_name)
	possible_filenames = [
		f"{sanitized_track}.a2",
		f"{sanitized_track}.lrc",
		f"{sanitized_track}.txt",
		f"{sanitized_track}_{sanitized_artist}.a2",
		f"{sanitized_track}_{sanitized_artist}.lrc",
		f"{sanitized_track}_{sanitized_artist}.txt"
	]

	synced_dir = os.path.join(os.getcwd(), "synced_lyrics")

	for dir_path in [directory, synced_dir]:
		for filename in possible_filenames:
			file_path = os.path.join(dir_path, filename)
			if os.path.exists(file_path):
				try:
					with open(file_path, 'r', encoding='utf-8') as f:
						content = f.read()
					if validate_lyrics(content, artist_name, track_name):
						log_debug(f"Using validated file: {file_path}")
						return file_path
					else:
						log_debug(f"Skipping invalid file: {file_path}")
				except Exception as e:
					log_debug(f"Error reading {file_path}: {e}")
					continue
	
	if is_instrumental:
		log_debug("Instrumental track detected")
		update_fetch_status('instrumental')
		return save_lyrics("[Instrumental]", track_name, artist_name, 'txt')

	
	# Check timeout status
	if is_lyrics_timed_out(artist_name, track_name):
		update_fetch_status('time_out')
		log_debug(f"Lyrics timeout active for {artist_name} - {track_name}")
		return None

	update_fetch_status('synced')
	# Fetch from syncedlyrics
	log_debug("Fetching from syncedlyrics...")
	search_start = time.time()
	log_info(f"Searching online sources for: {artist_name} - {track_name}")
	fetched_lyrics, is_synced = fetch_lyrics_syncedlyrics(artist_name, track_name, duration)
	if fetched_lyrics:
		search_time = time.time() - search_start
		log_debug(f"Online search completed in {search_time:.3f}s")
		
		log_info(f"Found {'synced' if is_synced else 'plain'} lyrics online")
		# Add validation warning if needed
		if not validate_lyrics(fetched_lyrics, artist_name, track_name):
			log_debug("Validation warning - possible mismatch")
			fetched_lyrics = "[Validation Warning] Potential mismatch\n" + fetched_lyrics
		
		line_count = len(fetched_lyrics.split('\n'))
		log_debug(f"Lyrics stats - Lines: {line_count}, "
				 f"Chars: {len(fetched_lyrics)}, "
				 f"Synced: {is_synced}")
		
		# Determine file format
		is_enhanced = any(re.search(r'<\d+:\d+\.\d+>', line) 
						for line in fetched_lyrics.split('\n'))
		# extension = 'a2' if is_enhanced else ('lrc' if is_synced else 'txt')
		has_lrc_timestamps = re.search(r'\[\d+:\d+\.\d+\]', fetched_lyrics) is not None
		# if is_enhanced:
			# extension = 'a2'
		# else:
			# # Check if lyrics actually contain LRC timestamps
			# has_lrc_timestamps = re.search(r'\[\d+:\d+\.\d+\]', fetched_lyrics) is not None
			# extension = 'lrc' if (is_synced and has_lrc_timestamps) else 'txt'
		if is_enhanced:
			extension = 'a2'
		elif is_synced and has_lrc_timestamps:
			extension = 'lrc'
		else:
			extension = 'txt'
		return save_lyrics(fetched_lyrics, track_name, artist_name, extension)
	
	# Fallback to LRCLIB
	if fetched_result is None:
		log_debug("Error occurred during lyric fetch, skipping to next source")
		update_fetch_status("lrc_lib")
	# update_fetch_status("lrc_lib")
	log_debug("Fetching from LRCLIB...")
	fetched_lyrics, is_synced = fetch_lyrics_lrclib(artist_name, track_name, duration)
	if fetched_lyrics:
		search_time = time.time() - search_start
		log_debug(f"Online search completed in {search_time:.3f}s")
		extension = 'lrc' if is_synced else 'txt'
		return save_lyrics(fetched_lyrics, track_name, artist_name, extension)
	
	log_debug("No lyrics found from any source")
	update_fetch_status("failed")
	if has_internet_global():
		log_timeout(artist_name, track_name)
	return None

def parse_time_to_seconds(time_str):
	"""Convert various timestamp formats to seconds with millisecond precision."""
	patterns = [
		r'^(?P<m>\d+):(?P<s>\d+\.\d+)$',  # MM:SS.ms
		r'^(?P<m>\d+):(?P<s>\d+):(?P<ms>\d{1,3})$',  # MM:SS:ms
		r'^(?P<m>\d+):(?P<s>\d+)$',  # MM:SS
		r'^(?P<s>\d+\.\d+)$',  # SS.ms
		r'^(?P<s>\d+)$'  # SS
	]
	
	for pattern in patterns:
		match = re.match(pattern, time_str)
		if match:
			parts = match.groupdict()
			minutes = int(parts.get('m', 0) or 0)
			seconds = float(parts.get('s', 0) or 0)
			milliseconds = int(parts.get('ms', 0) or 0) / 1000
			return round(minutes * 60 + seconds + milliseconds, 3)
	
	raise ValueError(f"Invalid time format: {time_str}")

def load_lyrics(file_path):
	"""Parse lyric file into time-text pairs"""
	lyrics = []
	errors = []
	log_trace(f"Parsing lyrics file: {file_path}")
	try:
		try:
			with open(file_path, 'r', encoding="utf-8") as f:
				lines = f.readlines()
		except Exception as e:
			errors.append(f"File open error: {str(e)}")
			return lyrics, errors

		# A2 Format Parsing
		if file_path.endswith('.a2'):
			current_line = []
			line_pattern = re.compile(r'^\[(\d{2}:\d{2}\.\d{2})\](.*)')
			word_pattern = re.compile(r'<(\d{2}:\d{2}\.\d{2})>(.*?)<(\d{2}:\d{2}\.\d{2})>')

			for line in lines:
				line = line.strip()
				if not line:
					continue

				# Parse line timing
				line_match = line_pattern.match(line)
				if line_match:
					line_time = parse_time_to_seconds(line_match.group(1))
					lyrics.append((line_time, None))
					content = line_match.group(2)
					
					# Parse word-level timing
					words = word_pattern.findall(content)
					for start_str, text, end_str in words:
						start = parse_time_to_seconds(start_str)
						end = parse_time_to_seconds(end_str)
						clean_text = re.sub(r'<.*?>', '', text).strip()
						if clean_text:
							lyrics.append((start, (clean_text, end)))
					
					# Handle remaining text
					remaining = re.sub(word_pattern, '', content).strip()
					if remaining:
						lyrics.append((line_time, (remaining, line_time)))
					lyrics.append((line_time, None))

		# Plain Text Format
		elif file_path.endswith('.txt'):
			for line in lines:
				raw_line = line.rstrip('\n')
				lyrics.append((None, raw_line))
		# LRC Format
		else:
			for line in lines:
				raw_line = line.rstrip('\n')
				line_match = re.match(r'\[(\d+:\d+\.\d+)\](.*)', raw_line)
				if line_match:
					line_time = parse_time_to_seconds(line_match.group(1))
					lyric_content = line_match.group(2).strip()
					lyrics.append((line_time, lyric_content))
				else:
					lyrics.append((None, raw_line))
		
		return lyrics, errors

	except Exception as e:
		if errors:
			log_warn(f"Found {len(errors)} parsing errors")
		return lyrics, errors

# ==============
#  PLAYER DETECTION
# ==============
def get_player_info():
	"""Detect active player (CMUS or MPD)"""
	# Try CMUS first
	cmus_info = get_cmus_info()
	if cmus_info[0] is not None:
		return 'cmus', cmus_info
	
	# Fallback to MPD
	try:
		mpd_info = get_mpd_info()
		#log_debug(f"CMUS status: {cmus_info[5]}, MPD status: {mpd_info[5]}")
		if mpd_info[0] is not None:
			return 'mpd', mpd_info
	except (base.ConnectionError, socket.error) as e:
		# log_debug(f"MPD connection error: {str(e)}")
		log_error(f"MPD connection failed: {str(e)}")
	except base.CommandError as e:
		log_error(f"MPD command error: {str(e)}")
	except Exception as e:
		log_error(f"Unexpected MPD error: {str(e)}")
		log_fatal("No active music player detected")

	return None, (None, 0, None, None, 0, "stopped")

def get_mpd_info():
	"""Get current playback info from MPD, handling password authentication."""
	client = MPDClient()
	client.timeout = MPD_TIMEOUT
	
	try:
		client.connect(MPD_HOST, MPD_PORT)
		
		# Authenticate if a password is set
		if MPD_PASSWORD:
			client.password(MPD_PASSWORD)
		
		status = client.status()
		current_song = client.currentsong()

		# Ensure artist is always a string (handle lists)
		artist = current_song.get("artist", None)
		if isinstance(artist, list):
			artist = ", ".join(artist)  # Convert list to comma-separated string
		
		data = {
			"file": current_song.get("file", ""),
			"position": float(status.get("elapsed", 0)),
			"artist": artist,
			"title": current_song.get("title", None),
			"duration": float(status.get("duration", status.get("time", 0))),
			"status": status.get("state", "stopped")
		}

		client.close()
		client.disconnect()

		return (data["file"], data["position"], data["artist"], 
				data["title"], data["duration"], data["status"])

	except (socket.error, ConnectionRefusedError) as e:
		log_debug(f"MPD connection error: {str(e)}")
	except Exception as e:
		log_debug(f"Unexpected MPD error: {str(e)}")

	update_fetch_status("mpd")
	return (None, 0, None, None, 0, "stopped")

# ==============
#  UI RENDERING
# ==============
def display_lyrics( 
		stdscr,
		lyrics,
		errors,
		position,
		track_info,
		manual_offset,
		is_txt_format,
		is_a2_format,
		current_idx,
		use_manual_offset,
		time_adjust=0,
		is_fetching=False,
		subframe_fraction=0.0,
		alignment='center',
		player_info=None
):
	"""Render lyrics in curses interface with minimal redraw using separate windows and dynamic resize."""
	# Get terminal dimensions
	height, width = stdscr.getmaxyx()
	status_msg = get_current_status()

	# Layout constants
	STATUS_LINES = 2
	MAIN_STATUS_LINE = height - 1
	TIME_ADJUST_LINE = height - 2
	LYRICS_AREA_HEIGHT = height - STATUS_LINES - 1

	# On first call or after resize, (re)initialize windows
	if (not hasattr(display_lyrics, '_dims')
		or display_lyrics._dims != (height, width)):

		# *** Begin resize handling ***
		# Inform ncurses of the new terminal size, so it blank-fills extended areas
		curses.resizeterm(height, width)
		# Also clear the stdscr so no old content remains
		stdscr.clear()
		# *** End resize handling ***

		display_lyrics._dims = (height, width)
		# Create/Recreate sub‑windows for each section
		display_lyrics.error_win = curses.newwin(1, width, 0, 0)
		display_lyrics.lyrics_win = curses.newwin(LYRICS_AREA_HEIGHT, width, 1, 0)
		display_lyrics.adjust_win = curses.newwin(1, width, TIME_ADJUST_LINE, 0)
		display_lyrics.status_win = curses.newwin(1, width, MAIN_STATUS_LINE, 0)

	# Alias windows
	error_win   = display_lyrics.error_win
	lyrics_win  = display_lyrics.lyrics_win
	adjust_win  = display_lyrics.adjust_win
	status_win  = display_lyrics.status_win

	# --- 1) Render errors ---
	error_win.erase()
	if errors:
		try:
			err_str = f"Errors: {len(errors)}"[:width-1]
			error_win.addstr(0, 0, err_str, curses.color_pair(1))
		except curses.error:
			pass
	error_win.noutrefresh()

	# --- 2) Render lyrics ---
	lyrics_win.erase()

	if is_a2_format:
		# Build A2 group lines
		a2_lines, cur = [], []
		for t, item in lyrics:
			if item is None:
				if cur:
					a2_lines.append(cur)
					cur = []
			else:
				cur.append((t, item))
		if cur:
			a2_lines.append(cur)

		visible = LYRICS_AREA_HEIGHT
		max_start = max(0, len(a2_lines) - visible)
		start_line = (min(max(manual_offset, 0), max_start)
					  if use_manual_offset else max_start)
		y = 0
		for idx in range(start_line, min(start_line + visible, len(a2_lines))):
			if y >= visible:
				break
			line = a2_lines[idx]
			line_str = " ".join(text for _, (text, _) in line)
			# Alignment
			if alignment == 'right':
				x = max(0, width - len(line_str) - 1)
			elif alignment == 'center':
				x = max(0, (width - len(line_str)) // 2)
			else:
				x = 1
			# Draw words in line
			cursor = 0
			for _, (text, _) in line:
				space_left = width - x - cursor - 1
				if space_left <= 0:
					break
				txt = text[:space_left]
				color = curses.color_pair(2) if idx == len(a2_lines)-1 else curses.color_pair(3)
				try:
					lyrics_win.addstr(y, x + cursor, txt, color)
				except curses.error:
					break
				cursor += len(txt) + 1
			y += 1
		start_screen_line = start_line

	else:
		# LRC/TXT wrapping
		wrap_w = max(10, width-2)
		wrapped = []
		for orig_i, (_, ly) in enumerate(lyrics):
			if ly.strip():
				lines = textwrap.wrap(ly, wrap_w, drop_whitespace=False)
				wrapped.append((orig_i, lines[0]))
				for cont in lines[1:]:
					wrapped.append((orig_i, ' ' + cont))
			else:
				wrapped.append((orig_i, ''))

		total = len(wrapped)
		avail = LYRICS_AREA_HEIGHT
		max_start = max(0, total - avail)

		if use_manual_offset:
			start_screen_line = min(max(manual_offset, 0), max_start)
		else:
			if current_idx >= len(lyrics)-1:
				start_screen_line = max_start
			else:
				idxs = [i for i,(o,_) in enumerate(wrapped) if o==current_idx]
				if idxs:
					# center current line
					center = (idxs[0]+idxs[-1])//2
					ideal = center - avail//2
					start_screen_line = min(max(ideal, 0), max_start)
				else:
					start_screen_line = min(max(current_idx, 0), max_start)

		end_line = min(start_screen_line + avail, total)
		y = 0
		for _, line in wrapped[start_screen_line:end_line]:
			if y >= avail:
				break
			txt = line.strip()[:width-1]
			if alignment == 'right':
				x = max(0, width - len(txt) - 1)
			elif alignment == 'center':
				x = max(0, (width - len(txt))//2)
			else:
				x = 1
			if is_txt_format:
				color = curses.color_pair(4) if wrapped[start_screen_line+y][0]==current_idx else curses.color_pair(5)
			else:
				color = curses.color_pair(2) if wrapped[start_screen_line+y][0]==current_idx else curses.color_pair(3)
			try:
				lyrics_win.addstr(y, x, txt, color)
			except curses.error:
				pass
			y += 1

		lyrics_win.noutrefresh()

	# --- 3) End-of-lyrics or Time-adjustment display ---
	adjust_win.erase()
	if (current_idx is not None and
		current_idx == len(lyrics) - 1 and
		not is_txt_format and
		len(lyrics) > 1):
		try:
			adjust_win.addstr(0, 0, " End of lyrics ", curses.color_pair(2) | curses.A_BOLD)
		except curses.error:
			pass
	elif time_adjust:
		adj_str = f" Offset: {time_adjust:+.1f}s "[:width-1]
		try:
			adjust_win.addstr(0, max(0, width - len(adj_str) - 1),
							   adj_str, curses.color_pair(2) | curses.A_BOLD)
		except curses.error:
			pass
	adjust_win.noutrefresh()

	# --- 4) Status bar ---
	status_win.erase()
	if globals().get('DISPLAY_NAME'):
		# Build player status text
		if player_info:
			_, data = player_info
			artist = data[2] or 'Unknown Artist'
			title = data[3] or os.path.basename(data[0]) or 'Unknown Track'
			is_inst = any(x in title.lower() for x in ['instrumental','karaoke'])
			ps = f"{title} - {artist}"
		else:
			ps, is_inst = 'No track', False
			
		ps = f"{title} - {artist}"
		cur_line = min(current_idx + 1, len(lyrics)) if lyrics else 0
		line_info = f"Line {cur_line}/{len(lyrics)}"
		adj_flag = '' if is_inst else ('[Adj] ' if time_adjust else '')
		icon = ' ⏳ ' if is_fetching else ' 🎵 '

		# Compose right text section
		right_text_full = f"{line_info}{adj_flag}"
		right_text_fallback = f" {cur_line}/{len(lyrics)}{adj_flag} "

		# Check how much space remains for left-side
		if len(f"{icon}{ps} • {right_text_full}") <= width - 1:
			# All fits
			display_line = f"{icon}{ps} • {right_text_full}"
		elif len(f"{icon}{ps} • {right_text_fallback}") <= width - 1:
			# Use short fallback right text (drop "Line")
			right_text = right_text_fallback
			left_max = width - 1 - len(right_text) - 1
			ps_trunc = f"{icon}{ps}"
			if len(ps_trunc) > left_max:
				trunc_len = left_max - 3  # for "..."
				ps_trunc = ps_trunc[:trunc_len] + '...' if trunc_len > 0 else ''
			padding = ' ' * max(left_max - len(ps_trunc), 0)
			display_line = f"{ps_trunc}{padding} {right_text} "
		else:
			# Not even fallback fits cleanly — truncate both
			# Ensure right_text fits first
			right_text = right_text_fallback
			max_right = width - 1
			if len(right_text) > max_right:
				right_text = right_text[:max_right]
				display_line = right_text
			else:
				left_max = width - 1 - len(right_text) - 1
				ps_trunc = f"{icon}{ps} "
				if len(ps_trunc) > left_max:
					trunc_len = left_max - 3  # for "..."
					ps_trunc = ps_trunc[:trunc_len] + '...' if trunc_len > 0 else ''
				padding = ' ' * max(left_max - len(ps_trunc), 0)
				display_line = f"{ps_trunc}{padding} {right_text} "

		try:
			safe_width = max(0, width - 1)
			safe_line = display_line[:safe_width]
			status_win.addstr(0, 0, safe_line, curses.color_pair(5) | curses.A_BOLD)
		except curses.error:
			pass
	else:
		info = f"Line {min(current_idx+1, len(lyrics))}/{len(lyrics)}"
		if time_adjust:
			info += '[Adj]'
		try:
			status_win.addstr(0, 0, info[:width-1], curses.A_BOLD)
		except curses.error:
			pass
	status_win.noutrefresh()

	# Overlay centered status message
	if status_msg:
		msg = f"  [{status_msg}]  "[:width-1]
		try:
			status_win.addstr(0, max(0, (width - len(msg)) // 2),
							   msg, curses.color_pair(2) | curses.A_BOLD)
		except curses.error:
			pass
	status_win.noutrefresh()

	# Refresh all windows at once
	curses.doupdate()
	# Return scroll start for caller
	return start_screen_line
	
# ================
#  INPUT HANDLING
# ================
def parse_key_config(key_config):
	"""Convert key config strings to key codes"""
	if isinstance(key_config, list):
		return [parse_single_key(k) for k in key_config]
	return [parse_single_key(key_config)]

def parse_single_key(key_str):
	"""Convert single key string to key code"""
	if key_str.startswith("KEY_"):
		return getattr(curses, key_str, None)
	elif len(key_str) == 1:
		return ord(key_str)
	return None

def load_key_bindings(config):
	"""Load and parse key bindings from config with None handling"""
	bindings = config.get("key_bindings", {})
	parsed = {}
	
	for action, key_config in bindings.items():
		# Filter out None values and invalid entries
		keys = parse_key_config(key_config)
		parsed[action] = [k for k in keys if k is not None]
	
	# Set defaults only if no valid config exists
	defaults = {
		"quit": [ord("q"), ord("Q")],
		"refresh": [ord("R")],
		"scroll_up": [curses.KEY_UP],
		"scroll_down": [curses.KEY_DOWN],
		"time_decrease": [ord("-"), ord("_")],
		"time_increase": [ord("="), ord("+")],
		"time_reset": [ord("0")],
		"align_cycle_forward": [ord("a")],
		"align_cycle_backward": [ord("A")],
		"align_left": [ord("1")],
		"align_center": [ord("2")],
		"align_right": [ord("3")]
	}
	
	for key, default in defaults.items():
		parsed[key] = parsed.get(key, default) if key not in parsed or not parsed[key] else parsed[key]
	
	return parsed

def handle_scroll_input(key, manual_offset, last_input_time, needs_redraw, 
					   time_adjust, current_alignment, key_bindings):
	"""Complete input handler with full scroll logic"""
	new_alignment = current_alignment
	input_processed = False
	manual_input = False
	time_adjust_input = False
	alignment_input = False

	# Quit handling
	if key in key_bindings["quit"]:
		sys.exit('Exiting')
		return False, manual_offset, last_input_time, needs_redraw, time_adjust, current_alignment

	# Scroll up with boundary check
	if key in key_bindings["scroll_up"]:
		manual_offset = max(0, manual_offset - 1)
		input_processed = True
		manual_input = True

	# Scroll down (clamping done in display_lyrics)
	elif key in key_bindings["scroll_down"]:
		manual_offset += 1
		input_processed = True
		manual_input = True

	# Time adjustments
	elif key in key_bindings["time_decrease"]:
		# time_adjust = max(-10.0, time_adjust - 0.1)
		time_adjust = time_adjust - 0.1
		input_processed = True
		time_adjust_input = True
	elif key in key_bindings["time_increase"]:
		# time_adjust = min(10.0, time_adjust + 0.1)
		time_adjust = time_adjust + 0.1
		input_processed = True
		time_adjust_input = True
	elif key in key_bindings["time_reset"]:
		time_adjust = 0.0
		input_processed = True
		time_adjust_input = True
	elif key in key_bindings["time_jump_increase"]:
		time_adjust = time_adjust + 5.0
		input_processed = True
		time_adjust_input = True
	elif key in key_bindings["time_jump_decrease"]:
		time_adjust = time_adjust - 5.0
		input_processed = True
		time_adjust_input = True
	# add your custom binding here what you wanted, imaginary is your limitation

	# Direct alignment selection
	elif key in key_bindings["align_left"]:
		new_alignment = "left"
		input_processed = True
		alignment_input = True
	elif key in key_bindings["align_center"]:
		new_alignment = "center"
		input_processed = True
		alignment_input = True
	elif key in key_bindings["align_right"]:
		new_alignment = "right"
		input_processed = True
		alignment_input = True

	# Alignment cycling
	elif key in key_bindings["align_cycle_forward"]:
		alignments = ['left', 'center', 'right']
		new_index = (alignments.index(current_alignment) + 1) % 3
		new_alignment = alignments[new_index]
		input_processed = True
		alignment_input = True
	elif key in key_bindings["align_cycle_backward"]:
		alignments = ['left', 'center', 'right']
		new_index = (alignments.index(current_alignment) - 1) % 3
		new_alignment = alignments[new_index]
		input_processed = True
		alignment_input = True

	# Window resize handling
	if key == curses.KEY_RESIZE:
		needs_redraw = True
	elif input_processed:
		# Update input timestamps
		if manual_input:
			last_input_time = time.time()
		elif time_adjust_input or alignment_input:
			last_input_time = 0  # Reset to enable auto-centering
		needs_redraw = True
	return True, manual_offset, last_input_time, needs_redraw, time_adjust, new_alignment


def update_display(stdscr, lyrics, errors, position, audio_file, manual_offset, 
				   is_txt_format, is_a2_format, current_idx, manual_scroll_active, 
				   time_adjust=0, is_fetching=False, subframe_fraction=0.0,alignment='center', player_info = None):
	"""Update display based on current state.
	
	Now includes subframe_fraction for fine-grained progress within a lyric line.
	"""
	# log_debug(f"Display params - Manual offset: {manual_offset}, Time adjust: {time_adjust}")
	if is_txt_format:
		return display_lyrics(stdscr, lyrics, errors, position, 
							  os.path.basename(audio_file), manual_offset, 
							  is_txt_format, is_a2_format, current_idx, True, 
							  time_adjust, is_fetching, subframe_fraction, alignment, player_info)
	else:
		return display_lyrics(stdscr, lyrics, errors, position, 
							  os.path.basename(audio_file), manual_offset, 
							  is_txt_format, is_a2_format, current_idx, 
							  manual_scroll_active, time_adjust, is_fetching, subframe_fraction, alignment, player_info)


executor = concurrent.futures.ThreadPoolExecutor(max_workers=10)
#executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
future_lyrics = None  # Holds the async result

def fetch_lyrics_async(audio_file, directory, artist, title, duration):
	"""Function to fetch lyrics in a separate thread"""
	try:
		lyrics_file = find_lyrics_file(audio_file, directory, artist, title, duration)
		if lyrics_file:
			is_txt_format = lyrics_file.endswith('.txt')
			is_a2_format = lyrics_file.endswith('.a2')
			lyrics, errors = load_lyrics(lyrics_file)
			update_fetch_status('done', len(lyrics))
			return (lyrics, errors), is_txt_format, is_a2_format
		update_fetch_status('failed')
		return ([], []), False, False
	except Exception as e:
		log_debug(f"Async fetch error: {e}")
		update_fetch_status('failed')
		return ([], []), False, False
		

# def clean_lyrics(raw_lyrics):
	# """Ensure lyrics have valid timestamps."""
	# cleaned = []
	# last_valid = 0.0
	# for t, text in raw_lyrics:
		# if t is None:
			# cleaned_t = last_valid  # Use last valid timestamp
		# else:
			# cleaned_t = max(0.0, float(t))
			# last_valid = cleaned_t
		# cleaned.append((cleaned_t, text))
	# return cleaned

def sync_player_position(status, raw_pos, last_time, time_adjust, duration):
	now = time.perf_counter()
	elapsed = now - last_time
	
	if status == "playing":
		estimated = raw_pos + elapsed + time_adjust
	else:
		estimated = raw_pos + time_adjust
	log_debug(f"Position sync - Raw: {raw_pos}, Adjusted: {estimated}")
	return max(0.0, min(estimated, duration)), now

def find_current_lyric_index(position, timestamps):
	if not timestamps:
		return 0

	idx = bisect.bisect_left(timestamps, position)
	idx = max(0, min(idx, len(timestamps)-1))

	if idx+1 < len(timestamps):
		current_duration = timestamps[idx+1] - timestamps[idx]
		position_in_line = position - timestamps[idx]
		if current_duration > 0 and (position_in_line / current_duration) > 0.95:
			return idx + 1
	
	return idx

EPSILON = 1e-3  # Small constant to avoid division by zero

def bisect_worker(position, timestamps, offset):
	"""Returns the closest index using bisect based on a given offset."""
	if not timestamps:
		return None  # No timestamps available

	idx = bisect.bisect_right(timestamps, position + offset) - 1
	return max(0, min(idx, len(timestamps) - 1))

def proximity_worker(position, timestamps, threshold):
	"""Returns the closest index based on proximity and progress within the current line."""
	if not timestamps:
		return None  # No timestamps available

	idx = bisect.bisect_left(timestamps, position)
	idx = max(0, min(idx, len(timestamps) - 1))

	# Check if the next timestamp is close enough to switch early
	if idx + 1 < len(timestamps):
		current_duration = timestamps[idx + 1] - timestamps[idx]
		position_in_line = position - timestamps[idx]
		if current_duration > 0:
			progress_ratio = position_in_line / current_duration
			if progress_ratio > (1 - threshold):
				idx += 1
	return idx

# def compute_confidence(continuous_position, ts_value):
	# """
	# Compute a confidence score based on the absolute difference between the continuous position and a timestamp.
	# Lower difference gives higher confidence.
	# """
	# diff = abs(continuous_position - ts_value)
	# return 1.0 / (diff + EPSILON)

# def compute_weighted_index(continuous_position, timestamps, bisect_idx, proximity_idx):
	# """
	# Compute a weighted index from two methods using their confidence scores.
	# If one method returns None, fallback to the other.
	# """
	# if bisect_idx is None:
		# return proximity_idx
	# if proximity_idx is None:
		# return bisect_idx

	# conf_bisect = compute_confidence(continuous_position, timestamps[bisect_idx])
	# conf_proximity = compute_confidence(continuous_position, timestamps[proximity_idx])
	# weighted = (bisect_idx * conf_bisect + proximity_idx * conf_proximity) / (conf_bisect + conf_proximity)
	# return int(round(weighted))

def subframe_interpolation(continuous_position, timestamps, index):
	"""
	Given an index, compute a fraction (0.0 to 1.0) representing the progress between this timestamp and the next.
	This sub-frame fraction can be used for smoother UI transitions.
	"""
	if index < 0 or index >= len(timestamps) - 1:
		return index, 0.0
	start = timestamps[index]
	end = timestamps[index + 1]
	if end - start == 0:
		return index, 0.0
	fraction = (continuous_position - start) / (end - start)
	fraction = max(0.0, min(1.0, fraction))
	return index, fraction



def main(stdscr):
	# Initialize colors and UI
	log_info("Initializing colors and UI")

	curses.start_color()
	use_256 = curses.COLORS >= 256
	color_config = CONFIG["ui"]["colors"]

	# Resolve color configurations
	error_color    = resolve_color(color_config["error"])
	txt_active     = resolve_color(color_config["txt"]["active"])
	txt_inactive   = resolve_color(color_config["txt"]["inactive"])
	lrc_active     = resolve_color(color_config["lrc"]["active"])
	lrc_inactive   = resolve_color(color_config["lrc"]["inactive"])

	# Load intervals and thresholds
	refresh_interval_ms            = CONFIG["ui"]["refresh_interval_ms"]
	refresh_interval               = refresh_interval_ms / 1000.0
	refresh_interval_2             = CONFIG["ui"]["coolcpu_ms"]
	smart_refresh_interval         = CONFIG["ui"]["smart_coolcpu_ms"]
	smart_refresh_interval_v2      = CONFIG["ui"]["smart_coolcpu_ms_v2"]
	JUMP_THRESHOLD                 = CONFIG["ui"].get("jump_threshold_sec", 1.0)
	refresh_proximity_interval_ms  = CONFIG["ui"].get("refresh_proximity_interval_ms", 200)
	#refresh_proximity_interval     = refresh_proximity_interval_ms / 1000.0
	refresh_proximity_interval     = CONFIG["ui"]["smart_coolcpu_ms_v2"]

	PROXIMITY_THRESHOLD_SEC        = CONFIG["ui"].get("proximity_threshold_sec", 0.05)
	PROXIMITY_THRESHOLD_PERCENT    = CONFIG["ui"].get("proximity_threshold_percent", 0.05)
	#PROXIMITY_MIN_THRESHOLD_SEC    = CONFIG["ui"].get("proximity_min_threshold_sec", 0.01)
	PROXIMITY_MIN_THRESHOLD_SEC    = CONFIG["ui"].get("proximity_min_threshold_sec", 1.0)
	PROXIMITY_MAX_THRESHOLD_SEC    = CONFIG["ui"].get("proximity_max_threshold_sec", 2.0)

	# New: initialize synchronization compensation variable
	# This will store the last measured redraw duration (in seconds)
	sync_compensation = 0.0

	# Initialize color pairs
	curses.init_pair(1, error_color,     curses.COLOR_BLACK)
	curses.init_pair(2, lrc_active,      curses.COLOR_BLACK)
	curses.init_pair(3, lrc_inactive,    curses.COLOR_BLACK)
	curses.init_pair(4, txt_active,      curses.COLOR_BLACK)
	curses.init_pair(5, txt_inactive,    curses.COLOR_BLACK)
	if use_256 and curses.can_change_color():
		pass  # custom 256-color definitions

	# Load key bindings and configure UI
	key_bindings = load_key_bindings(CONFIG)
	curses.curs_set(0)
	stdscr.nodelay(True)
	stdscr.keypad(True)
	stdscr.timeout(refresh_interval_2)

	# Initialize application state
	state = {
		'current_file': None,
		'lyrics': [],
		'errors': [],
		'manual_offset': 0,
		'last_input': 0.0,
		'time_adjust': 0.0,
		'last_raw_pos': 0.0,
		'last_pos_time': time.perf_counter(),
		'timestamps': [],
		'valid_indices': [],
		'last_idx': -1,
		'force_redraw': False,
		'is_txt': False,
		'is_a2': False,
		'window_size': stdscr.getmaxyx(),
		'manual_timeout_handled': True,
		'alignment': CONFIG["ui"].get("alignment", "center").lower(),
		'wrapped_lines': [],
		'max_wrapped_offset': 0,
		'window_width': 0,
		'last_player_update': 0.0,
		'player_info': (None, (None, 0, None, None, 0, "stopped")),
		'resume_trigger_time': None,
		'smart_tracking': CONFIG["ui"].get("smart-tracking"),
		'smart_proximity': CONFIG["ui"].get("smart-proximity", False),
		'proximity_trigger_time': None,
		'proximity_active': False,
	}

	TEMPORARY_REFRESH_SEC = CONFIG["ui"]["smart_refresh_duration"]
	executor = ThreadPoolExecutor(max_workers=4)
	#executor = ThreadPoolExecutor(max_workers=1)
	future_lyrics = None
	last_cmus_position = 0.0
	estimated_position = 0.0
	playback_paused = False

	# Unpack initial player info
	player_type, (audio_file, raw_pos, artist, title, duration, status) = state["player_info"]

	# Main application loop
	while True:
		try:
			current_time = time.perf_counter()
			draw_start = time.perf_counter()
			needs_redraw = False
			time_since_input = current_time - (state['last_input'] or 0.0)

			# Manual scroll timeout
			if state['last_input'] > 0:
				if time_since_input >= SCROLL_TIMEOUT:
					if not state['manual_timeout_handled']:
						needs_redraw = True
						state['manual_timeout_handled'] = True
					state['last_input'] = 0.0
				else:
					state['manual_timeout_handled'] = False

			manual_scroll = state['last_input'] > 0 and time_since_input < SCROLL_TIMEOUT

			# Window resize handling
			new_size = stdscr.getmaxyx()
			if new_size != state['window_size']:
				old_h, _ = state['window_size']
				new_h, _ = new_size
				if state['lyrics']:
					state['manual_offset'] = int(state['manual_offset'] * (new_h / old_h))
				state['window_size'] = new_size
				needs_redraw = True

			# Temporary high-frequency refresh after resume or jump
			if (state['player_info'][0] == 'cmus' and
				state.get('resume_trigger_time') and
				(current_time - state['resume_trigger_time'] <= TEMPORARY_REFRESH_SEC) and
				state['player_info'][1][5] == "playing" and
				state['lyrics']):
				stdscr.timeout(smart_refresh_interval)
			else:
				stdscr.timeout(refresh_interval_2)

			# Determine fetch interval with proximity override
			if state['proximity_active'] and status == "playing":
				interval = refresh_proximity_interval
			elif (state.get('resume_trigger_time') and
				  (current_time - state['resume_trigger_time'] <= TEMPORARY_REFRESH_SEC)):
				interval = 0.0
			else:
				interval = refresh_interval

			# Refresh player info when due
			if current_time - state['last_player_update'] >= interval:
				try:
					prev_status = state['player_info'][1][5]
					p_type, p_data = get_player_info()
					state['player_info'] = (p_type, p_data)

					_, raw_val, _, _, _, status_val = p_data
					new_raw = float(raw_val or 0.0)
					drift = abs(new_raw - estimated_position)
					if drift > JUMP_THRESHOLD and status_val == "playing":
						state['resume_trigger_time'] = time.perf_counter()
						log_debug(f"Jump detected: {drift:.3f}s")
						needs_redraw = True

					if (p_type == "cmus" and prev_status == "paused" and status_val == "playing"):
						state['resume_trigger_time'] = time.perf_counter()
						log_debug("Pause→play refresh")
						needs_redraw = True
				except Exception as e:
					log_debug("Error getting player info: " + str(e))
				state['last_player_update'] = current_time

			# Unpack the (possibly cached) player info
			player_type, (audio_file, raw_pos, artist, title, duration, status) = state["player_info"]
			raw_position = float(raw_pos or 0.0)
			duration = float(duration or 0.0)
			estimated_position = raw_position
			now = time.perf_counter()

			# Handle track changes
			if audio_file != state['current_file']:
				log_info(f"New track detected: {os.path.basename(audio_file)}")
				state.update({
					'current_file': audio_file,
					'lyrics': [],
					'errors': [],
					'last_raw_pos': raw_position,
					'last_pos_time': now,
					'last_idx': -1,
					'force_redraw': True,
					'is_txt': False,
					'is_a2': False,
					'lyrics_loaded_time': None,
					'wrapped_lines': [],
					'max_wrapped_offset': 0
				})
				if audio_file:
					future_lyrics = executor.submit(
						fetch_lyrics_async,
						audio_file,
						os.path.dirname(audio_file) if player_type == 'cmus' else "",
						artist or "Unknown",
						title or os.path.basename(audio_file),
						duration
					)
				last_cmus_position = raw_position
				estimated_position = raw_position

			# Handle loaded lyrics
			if future_lyrics and future_lyrics.done():
				try:
					(new_lyrics, errors), is_txt, is_a2 = future_lyrics.result()
					if errors:  # This checks if the list is non-empty
						log_debug(errors)
					state.update({
						'lyrics': new_lyrics,
						'errors': [],  # Keep errors empty to prevent display
						#'errors': errors,
						'timestamps': ([] if (is_txt or is_a2)
									   else sorted(t for t, _ in new_lyrics if t is not None)),
						'valid_indices': [i for i, (t, _) in enumerate(new_lyrics) if t is not None],
						'last_idx': -1,
						'force_redraw': True,
						'is_txt': is_txt,
						'is_a2': is_a2,
						'lyrics_loaded_time': time.perf_counter(),
						'wrapped_lines': [],
						'max_wrapped_offset': 0
					})
					if status == "playing" and player_type == "cmus":
						state['resume_trigger_time'] = time.perf_counter()
						log_debug("Refresh triggered by new lyrics loading")
					estimated_position = raw_position
					future_lyrics = None
				except Exception as e:
					state.update({
						'errors': [f"Lyric load error: {e}"],
						'force_redraw': True,
						'lyrics_loaded_time': time.perf_counter()
					})
					future_lyrics = None

			# Delayed redraw after lyric load
			if (state['lyrics_loaded_time'] and
				time.perf_counter() - state['lyrics_loaded_time'] >= 2.0):
				state['force_redraw'] = True
				state['lyrics_loaded_time'] = None

			# Update position estimation
			if raw_position != last_cmus_position:
				last_cmus_position = raw_position
				state['last_pos_time'] = now
				estimated_position = raw_position
				playback_paused = (status == "paused")

			# Player-specific estimation
			if player_type == "cmus":
				if status == "playing":
					elapsed = now - state['last_pos_time']
					estimated_position = raw_position + elapsed
					estimated_position = min(estimated_position, duration)
				else:
					if raw_position != last_cmus_position:
						state['last_pos_time'] = now
					#state['last_pos_time'] = now
					estimated_position = min(estimated_position, duration)
				#estimated_position = max(0.0, min(estimated_position, duration))
				
			else:
				playback_paused = (status == "pause")

			base_offset = CONFIG["ui"].get("sync_offset_sec", 0.0)
			offset = base_offset + sync_compensation

			continuous_position = max(0.0, estimated_position + state['time_adjust'] + offset)
			continuous_position = min(continuous_position, duration)
			
			# offset = CONFIG["ui"].get("sync_offset_sec", 0.0) + sync_compensation
			
			# sync_compensation = 0.0
			
			# ─── End‑of‑track proximity trigger ────────────────────────────────────────────
			END_TRIGGER_SEC = CONFIG["ui"].get("end_trigger_threshold_sec", 1.0)

			# only run once per track
			if duration > 0 \
			   and (duration - continuous_position) <= END_TRIGGER_SEC \
			   and not state.get("end_triggered", False):

				state["end_triggered"] = True

				# === your custom action here ===
				# e.g. force a final redraw:
				state["force_redraw"] = True
				log_debug(f"End‑of‑track reached (pos={continuous_position:.3f}s), triggered final redraw")
			# ────────────────────────────────────────────────────────────────────────────────

			# Cancel proximity if playback paused
			if status != "playing" and state['proximity_active']:
				state['proximity_active'] = False
				state['proximity_trigger_time'] = None
				stdscr.timeout(refresh_interval_2)
				log_debug("Proximity forcibly reset due to pause")

			# log_debug(f"Continuous position = {continuous_position:.6f} seconds") # debugging purpose
			
			# Proximity refresh
			# state['proximity_active'] = False
			if (state['smart_proximity']
				and state['timestamps'] and not state['is_txt']
				and state['last_idx'] >= 0
				and state['last_idx'] + 1 < len(state['timestamps'])
				and status == "playing"
				and not playback_paused):

				idx = state['last_idx']
				t0, t1 = state['timestamps'][idx], state['timestamps'][idx + 1]
				line_duration = t1 - t0
				percent_thresh = line_duration * (PROXIMITY_THRESHOLD_PERCENT/100)
				abs_thresh = PROXIMITY_THRESHOLD_SEC
				raw_thresh = max(percent_thresh, abs_thresh)
				threshold = min(
					max(raw_thresh, PROXIMITY_MIN_THRESHOLD_SEC),
					min(PROXIMITY_MAX_THRESHOLD_SEC, line_duration)
				)
				time_to_next = min(line_duration, max(0.0, t1 - continuous_position))

				if PROXIMITY_MIN_THRESHOLD_SEC <= time_to_next <= threshold:
					state['proximity_trigger_time'] = now
					state['proximity_active'] = True
					stdscr.timeout(refresh_proximity_interval_ms)  # use ms
					state['last_player_update'] = 0.0
					log_debug(
						f"Proximity TRIG: time_to_next={time_to_next:.3f}s "
						f"within [{PROXIMITY_MIN_THRESHOLD_SEC:.3f}, {threshold:.3f}]"
					)
				elif (state['proximity_trigger_time'] is not None
					  and (time_to_next < PROXIMITY_MIN_THRESHOLD_SEC
						   or time_to_next > threshold
						   or now - state['proximity_trigger_time'] > threshold)):
					stdscr.timeout(refresh_interval_2)
					state['proximity_trigger_time'] = None
					state['proximity_active'] = False
					log_debug(
						f"Proximity RESET: time_to_next={time_to_next:.3f}s "
						f"outside [{PROXIMITY_MIN_THRESHOLD_SEC:.3f}, {threshold:.3f}]"
					)
				else:
					state['proximity_active'] = False
			else:
				state['proximity_active'] = False

			# Generate wrapped lines for TXT files
			window_h, window_w = state['window_size']
			if state['is_txt']:
				if window_w != state['window_width'] or not state['wrapped_lines']:
					wrap_width = max(10, window_w - 2)
					wrapped = []
					for orig_idx, (_, lyric) in enumerate(state['lyrics']):
						if lyric.strip():
							lines = textwrap.wrap(lyric, wrap_width, drop_whitespace=False)
							wrapped.extend([(orig_idx, line) for line in lines])
						else:
							wrapped.append((orig_idx, ""))
					state['wrapped_lines'] = wrapped
					lyrics_area_height = window_h - 3
					state['max_wrapped_offset'] = max(0, len(wrapped) - lyrics_area_height)
					state['window_width'] = window_w
			
			# ─── Calculate current lyric index ────────────────────────────────────────────
			if state['smart_tracking'] == 1:
				current_idx = -1

				if state['timestamps'] and not state['is_txt']:
					# LRC synchronization logic
					with ThreadPoolExecutor(max_workers=2) as sync_exec:
					#with ThreadPoolExecutor(max_workers=1) as sync_exec:
						bisect_idx = sync_exec.submit(
							bisect_worker,
							continuous_position,
							state['timestamps'],
							#CONFIG["ui"]["bisect_offset"] + sync_compensation
							CONFIG["ui"]["bisect_offset"] + offset
						).result()
						proximity_idx = sync_exec.submit(
							proximity_worker,
							continuous_position,
							state['timestamps'],
							#CONFIG["ui"]["proximity_threshold"]
							CONFIG["ui"]["proximity_threshold"] + offset
						).result()

					if abs(bisect_idx - proximity_idx) > 1:
						chosen_idx = bisect_idx
					else:
						chosen_idx = min(bisect_idx, proximity_idx)

					current_idx = max(-1, min(chosen_idx, len(state['timestamps']) - 1))
					# if current_idx >= 0 and continuous_position < state['timestamps'][current_idx]:
						# current_idx = max(-1, current_idx - 1)
						# last_position_time = now  # Reset timer to prevent residual elapsed time
					if current_idx >= 0:
						t_cur = state['timestamps'][current_idx]
						# only compare floats against floats
						if t_cur is not None and continuous_position < t_cur:
							current_idx = max(-1, current_idx - 1)
							last_position_time = now


				elif state['is_txt'] and state['wrapped_lines'] and duration > 0:
					# TXT file synchronization (unchanged)
					num_wrapped = len(state['wrapped_lines'])
					target_idx  = int((continuous_position / duration) * num_wrapped)
					current_idx = max(0, min(target_idx, num_wrapped - 1))
					last_position_time = now  # Reset timer to prevent residual elapsed time

				else:
					current_idx = -1
					last_position_time = now  # Reset timer to prevent residual elapsed time


			else:
				if state['timestamps'] and not state['is_txt']:
					ts     = state['timestamps']
					# include sync_compensation in your manual‑offset branch too
					# offset = CONFIG["ui"].get("sync_offset_sec", 0.0) + sync_compensation
					idx    = bisect.bisect_right(ts, continuous_position + offset) - 1

					if idx >= 0:
						current_idx         = idx
						continuous_position = ts[idx]
						if status == "paused" and not manual_scroll and not state['current_file']:
							last_position_time = now  # Reset timer to prevent residual elapsed time
					else:
						current_idx = -1

				elif state['is_txt'] and state['wrapped_lines'] and duration > 0:
					# TXT fallback (unchanged)
					num_wrapped = len(state['wrapped_lines'])
					target_idx  = int((continuous_position / duration) * num_wrapped)
					current_idx = max(0, min(target_idx, num_wrapped - 1))

				else:
					current_idx = -1
					last_position_time = now  # Reset timer to prevent residual elapsed time
			# ────────────────────────────────────────────────────────────────────────────────
		
			#sync_compensation = 0.0
			
			# Auto scroll logic
			if state['last_input'] == 0 and not manual_scroll:
				if state['is_txt'] and state['wrapped_lines']:
					lyrics_area_height = window_h - 3
					ideal_offset = current_idx - (lyrics_area_height // 2)
					target_offset = max(0, min(ideal_offset, state['max_wrapped_offset']))
					if target_offset != state['manual_offset']:
						state['manual_offset'] = target_offset
						needs_redraw = True
					if current_idx != state['last_idx']:
						# only update scroll if index changed
						if target_offset != state['manual_offset']:
							state['manual_offset'] = target_offset
							needs_redraw = True

				
				elif not state['is_txt'] and state['wrapped_lines']:
					# LRC: Standard auto-center
					ideal_offset = current_idx - ((window_h-3) // 2)
					target_offset = max(0, min(ideal_offset, state['max_wrapped_offset']))
					if target_offset != state['manual_offset']:
						state['manual_offset'] = target_offset
						needs_redraw = True
					if current_idx != state['last_idx']:
						# only update scroll if index changed
						if target_offset != state['manual_offset']:
							state['manual_offset'] = target_offset
							needs_redraw = True


			
			# Handle user input
			key = stdscr.getch()
			new_input = key != -1
			if new_input:
				(cont, new_manual_offset, dummy_last_input, needs_redraw_input,
				 new_time_adjust, new_alignment) = handle_scroll_input(
					key, state['manual_offset'], state['last_input'],
					needs_redraw, state['time_adjust'], state['alignment'], key_bindings)

				if new_alignment != state['alignment']:
					state['alignment'] = new_alignment
					needs_redraw = True # checked

				# Update manual scroll timestamp
				if key in key_bindings["scroll_up"] or key in key_bindings["scroll_down"]:
					state['last_input'] = current_time

				# Apply manual offset clamp
				if state['is_txt']:
					state['manual_offset'] = max(0, min(
						new_manual_offset,
						state['max_wrapped_offset']
					))
				else:
					state['manual_offset'] = new_manual_offset

				state['time_adjust'] = new_time_adjust
				state['force_redraw'] = state['force_redraw'] or needs_redraw_input
				if not cont:
					break
			
			# Update display if needed
			skip_redraw = (
				status == "paused" and
				not manual_scroll and
				not state['force_redraw'] and
				current_idx == state['last_idx'] and
				not state['proximity_active']
			)
			
			if not skip_redraw:
				if new_input or needs_redraw or state['force_redraw'] or (current_idx != state['last_idx']):
					log_debug(
						f"Redraw triggered: new_input={new_input}, "
						f"needs_redraw={needs_redraw}, force_redraw={state['force_redraw']}, "
						f"idx={state['last_idx']} → {current_idx}, paused={status == 'paused'}"
					)

					start_screen_line = update_display(
						stdscr,
						state['wrapped_lines'] if state['is_txt'] else state['lyrics'],
						state['errors'],
						continuous_position,
						state['current_file'],
						state['manual_offset'],
						state['is_txt'],
						state['is_a2'],
						current_idx,
						manual_scroll,
						state['time_adjust'],
						future_lyrics is not None,
						alignment=state['alignment'],
						player_info=state["player_info"],
					)
					
					log_debug(
						f"Triggered at: {continuous_position}."
					)
					draw_end = time.perf_counter()
					sync_compensation = draw_end - draw_start
					#sync_compensation = draw_start - draw_end
					#sync_compensation = 0
					
					log_debug(
						f"Compensated: {sync_compensation}"
					)

					# Synchronize actual offset used
					state['manual_offset'] = start_screen_line
					state.update({
						'force_redraw': False,
						'last_manual': manual_scroll,
						'last_start_screen_line': start_screen_line,
						'last_idx': current_idx
					})
			# else:
				# log_debug(
					# f"Redraw skipped (paused): idx={state['last_idx']}, "
					# f"manual_scroll={manual_scroll}, force_redraw={state['force_redraw']}"
				# )
			
			
			#cpu destressor
			if status == "paused" and not manual_scroll and not state['current_file']:
				time_since_input = current_time - state['last_input']
				if time_since_input > 5.0:
					sleep_time = 0.5
					stdscr.timeout(400)
				elif time_since_input > 2.0:
					sleep_time = 0.2
					stdscr.timeout(300)
				else:
					sleep_time = 0.1
					stdscr.timeout(250)
				time.sleep(sleep_time)
			else:
				stdscr.timeout(refresh_interval_2)
			
		except Exception as e:
			log_debug(f"Main loop error: {str(e)}")
			
			time.sleep(1)
			stdscr.timeout(400)
			log_debug("Systemfault /s")

if __name__ == "__main__":
	while True:
		try:
			curses.wrapper(main)
		except KeyboardInterrupt:
			print("Exited by user (Ctrl+C).")
			exit() # Please do not delete this
			# raise KeyboardInterrupt
		except Exception as e:
			log_debug(f"Fatal error: {str(e)}")
			time.sleep(1)
		


