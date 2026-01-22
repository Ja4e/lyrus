#!/usr/bin/env python
"""
CMUS Lyrics Viewer with Synchronized Display
Displays time-synced lyrics for cmus music player using multiple lyric sources

Remember fetched lyrics has inaccuracies... this code has a very robust snyc to your current play position you can adjust whatever you want
"""

# ==============
#  DEPENDENCIES
# ==============
import curses
import argparse
try:
	import redis
except ImportError:
	redis = None
import aiohttp
import threading
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor
import subprocess
import re
import bisect
import time
import textwrap
import asyncio
from datetime import datetime
try:
	from mpd import MPDClient
except ImportError:
	pass
import socket
import appdirs
import pathlib
import unicodedata
from wcwidth import wcswidth
import os, json, sys
import argparse
import atexit
# try to use import math for that

# ==============
#  GLOBALS
# ==============
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
VERSION = "1.0.1"

config_dir = "~/.config/lyrus"
config_files = ["config.json", "config1.json", "config2.json"]

def parse_args():
	parser = argparse.ArgumentParser(description="Lyrus - cmus Lyrics synchronization project")
	parser.add_argument("-c", "--config", help="Path to configuration file")
	parser.add_argument("-d", "--default", action="store_true", help="Use default settings without loading a config file")
	parser.add_argument("-p", "--player", choices=["cmus", "mpd", "playerctl"], help="Specify which player you want to load only")
	parser.add_argument("--version", action="version", version=VERSION)
	return parser.parse_args()

def deep_merge_dicts(base, updates):
	for key, value in updates.items():
		if key in base and isinstance(base[key], dict) and isinstance(value, dict):
			deep_merge_dicts(base[key], value)
		else:
			base[key] = value

def resolve_value(item):
	"""Resolve {"env": ..., "default": ...} into actual value"""
	if isinstance(item, dict) and "env" in item and "default" in item:
		return os.environ.get(item["env"], item["default"])
	return item

class ConfigManager:
	
	def __init__(self, use_user_dirs=True, config_path=None, use_default=False, player_override=None):
		self.use_user_dirs = use_user_dirs
		self.user_config_dir = os.path.expanduser(config_dir)
		os.makedirs(self.user_config_dir, exist_ok=True)
		self.use_default = use_default
		self.config_path = config_path
		self.player_override = player_override
		
		self.config = self.load_config()
		self.setup_logging()
		self.setup_colors()
		self.setup_player()
		self.setup_lyrics()
		self.setup_ui()

	@staticmethod
	def normalize_path(path: str) -> str:
		path = os.path.expanduser(path)
		if os.path.isabs(path):
			return os.path.normpath(path)
		return os.path.normpath(os.path.abspath(path))

	def load_config(self):

		# Default configuration
		default_config = {
			"global": {
				"logs_dir": "~/.cache/lyrus",
				"log_file": "application.log",
				"log_level": "FATAL",
				"lyrics_timeout_log": "lyrics_timeouts.log",
				"debug_log": "debug.log",
				"log_retention_days": 10,
				"max_debug_count": 100,
				"max_log_count": 100,
				"enable_debug": {"env": "DEBUG", "default": "0"}
			},
			"player": {
				"enable_cmus": True,
				"enable_mpd": True,
				"enable_playerctl": True,
				"mpd": {
					"host": {"env": "MPD_HOST", "default": "localhost"},
					"port": {"env": "MPD_PORT", "default": 6600},
					"password": {"env": "MPD_PASSWORD", "default": None},
					"timeout": 10
				}
			},
			"redis": {
				"enabled": False,
				"host": {"env": "REDIS_HOST", "default": "localhost"},
				"port": {"env": "REDIS_PORT", "default": 6379}
			},
			"status_messages": {
				"start": "Starting lyric search...",
				"local": "Checking local files",
				"synced": "Searching online sources",
				"lrc_lib": "Checking LRCLIB database",
				"instrumental": "Instrumental track detected",
				"time_out": "In time-out log",
				"failed": "No lyrics found",
				"no_player": "scanning for activity",
				"mpd": "",
				"cmus": "loading cmus",
				"done": "Loaded",
				"clear": ""
			},
			"terminal_states": ["done", "instrumental", "time_out", "failed", "mpd", "clear", "cmus"],
			"lyrics": {
				"search_timeout": 15,
				"cache_dir": "~/.local/state/lyrus/synced_lyrics",
				"local_extensions": ["a2", "lrc", "txt"],
				"validation": {"title_match_length": 15, "artist_match_length": 15},
				"Syncedlyrics": True,
				"Sources": ["Musixmatch", "Lrclib", "NetEase", "Megalobiz", "Genius"],
				"Fallback": True,
				"Format_priority": ["a2", "lrc" ,"txt"]
			},
			"ui": {
				"alignment": "left",
				"name": True,
				"colors": {
					"txt": {
						"active": {"env": "TXT_ACTIVE", "default": "254"},
						"inactive": {"env": "TXT_INACTIVE", "default": "white"}
					},
					"lrc": {
						"active": {"env": "LRC_ACTIVE", "default": "046"},
						"inactive": {"env": "LRC_INACTIVE", "default": "250"}
					},
					"error": {"env": "ERROR_COLOR", "default": 196}
				},
				"scroll_timeout": 4,
				"sync": {
					"refresh_interval_ms": 1000,
					"coolcpu_ms": 100,
					"smart-tracking": 0,
					"bisect_offset": 0,
					"proximity_threshold": 0,
					"wrap_width_percent": 90,
					"smart_refresh_duration": 1,
					"smart_coolcpu_ms": 20,
					"jump_threshold_sec": 1,
					"end_trigger_threshold_sec": 1,
					"proximity": {
						"smart-proximity": True,
						"refresh_proximity_interval_ms": 100,
						"smart_coolcpu_ms_v2": 50,
						"proximity_threshold_sec": 0.1,
						"proximity_threshold_percent": 200,
						"proximity_min_threshold_sec": 0.0,
						"proximity_max_threshold_sec": 1
					},
					"sync_offset_sec": 0.005,
					"VRR_R_bol": False,
					"VRR_bol": False
				}
			},
			"key_bindings": {
				"quit": ["q", "Q"],
				"refresh": "R",
				"scroll_up": "KEY_UP",
				"scroll_down": "KEY_DOWN",
				"time_decrease": ["-", "_"],
				"time_increase": ["=", "+"],
				"time_jump_increase": ["]"],
				"time_jump_decrease": ["["],
				"time_reset": "0",
				"align_cycle_forward": "a",
				"align_cycle_backward": "A",
				"align_left": "1",
				"align_center": "2",
				"align_right": "3"
			}
		}

		merged_config = default_config

		if not self.use_default:
			config_paths = [self.config_path] if self.config_path else [os.path.join(self.user_config_dir, f) for f in config_files]
			for path in config_paths:
				if path and os.path.exists(os.path.expanduser(path)):
					try:
						with open(os.path.expanduser(path), "r") as f:
							file_config = json.load(f)
						if self.player_override:
							if "player" in file_config:
								del file_config["player"]
						deep_merge_dicts(merged_config, file_config)
						break
					except Exception as e:
						print(f"Error loading config from {path}: {e}")

		merged_config["global"]["enable_debug"] = str(resolve_value(merged_config["global"]["enable_debug"])) == "1"
		return merged_config

	def setup_colors(self):
		colors = self.config["ui"]["colors"]
		self.COLOR_NAMES = {
			"black": 0, "red": 1, "green": 2, "yellow": 3,
			"blue": 4, "magenta": 5, "cyan": 6, "white": 7
		}
		self.COLOR_TXT_ACTIVE = resolve_value(colors["txt"]["active"])
		self.COLOR_TXT_INACTIVE = resolve_value(colors["txt"]["inactive"])
		self.COLOR_LRC_ACTIVE = resolve_value(colors["lrc"]["active"])
		self.COLOR_LRC_INACTIVE = resolve_value(colors["lrc"]["inactive"])
		self.COLOR_ERROR = resolve_value(colors["error"])

	def setup_logging(self):
		logs_dir = self.config["global"]["logs_dir"]
		if not self.use_user_dirs and logs_dir.startswith("~"):
			logs_dir = os.path.join(os.getcwd(), os.path.basename(logs_dir))
		self.LOG_DIR = self.normalize_path(logs_dir)
		os.makedirs(self.LOG_DIR, exist_ok=True)

		self.LYRICS_TIMEOUT_LOG = self.config["global"]["lyrics_timeout_log"]
		self.DEBUG_LOG = self.config["global"]["debug_log"]
		self.LOG_RETENTION_DAYS = self.config["global"]["log_retention_days"]
		self.MAX_DEBUG_COUNT = self.config["global"]["max_debug_count"]
		self.ENABLE_DEBUG_LOGGING = self.config["global"]["enable_debug"]
		if self.ENABLE_DEBUG_LOGGING:
			print("Debug logging ENABLED")
			print("==== FULL CONFIG ====")
			import pprint
			pprint.pprint(self.config)
			print("=====================")

	def setup_player(self):
		self.MPD_HOST = resolve_value(self.config["player"]["mpd"]["host"])
		self.MPD_PORT = resolve_value(self.config["player"]["mpd"]["port"])
		self.MPD_PASSWORD = resolve_value(self.config["player"]["mpd"]["password"])
		self.MPD_TIMEOUT = self.config["player"]["mpd"]["timeout"]
		
		if self.player_override:
			self.ENABLE_CMUS = self.player_override == "cmus"
			self.ENABLE_MPD = self.player_override == "mpd"
			self.ENABLE_PLAYERCTL = self.player_override == "playerctl"
		else:
			self.ENABLE_CMUS = self.config["player"]["enable_cmus"]
			self.ENABLE_MPD = self.config["player"]["enable_mpd"]
			self.ENABLE_PLAYERCTL = self.config["player"]["enable_playerctl"]

	def setup_lyrics(self):
		self.LYRIC_EXTENSIONS = self.config["lyrics"]["local_extensions"]
		cache_dir = self.config["lyrics"]["cache_dir"]
		if not self.use_user_dirs and cache_dir.startswith("~"):
			cache_dir = os.path.join(os.getcwd(), os.path.basename(cache_dir))
		self.LYRIC_CACHE_DIR = self.normalize_path(cache_dir)
		os.makedirs(self.LYRIC_CACHE_DIR, exist_ok=True)
		self.SEARCH_TIMEOUT = self.config["lyrics"]["search_timeout"]
		self.VALIDATION_LENGTHS = self.config["lyrics"]["validation"]
		
		self.ALLOW_SYNCEDLYRIC = self.config["lyrics"]["Syncedlyrics"]
		self.PROVIDERS = list(dict.fromkeys(self.config["lyrics"]["Sources"]))
		
		self.PROVIDER_FALLBACK = self.config["lyrics"]["Fallback"]
		self.PROVIDER_FORMAT_PRIORITY = self.config["lyrics"]["Format_priority"]
		
	def setup_ui(self):
		self.DISPLAY_NAME = self.config["ui"]["name"]
		self.MESSAGES = self.config["status_messages"]
		self.TERMINAL_STATES = set(self.config["terminal_states"])

# Initialize
CONFIG_MANAGER = ConfigManager()
CONFIG = CONFIG_MANAGER.config


# ================
#  LOGGING SYSTEM
# ================
class Logger:
	"""Handle application logging"""
	
	def __init__(self, config_manager):
		self.LOG_DIR = CONFIG_MANAGER.LOG_DIR
		self.LYRICS_TIMEOUT_LOG = CONFIG_MANAGER.LYRICS_TIMEOUT_LOG
		self.DEBUG_LOG = CONFIG_MANAGER.DEBUG_LOG
		self.LOG_RETENTION_DAYS = CONFIG_MANAGER.LOG_RETENTION_DAYS
		self.MAX_DEBUG_COUNT = CONFIG_MANAGER.MAX_DEBUG_COUNT
		self.ENABLE_DEBUG_LOGGING = CONFIG_MANAGER.ENABLE_DEBUG_LOGGING
	
	def clean_debug_log(self):
		"""Maintain debug log size by keeping only last 100 entries"""
		log_path = os.path.join(self.LOG_DIR, self.DEBUG_LOG)
		
		if not os.path.exists(log_path):
			return

		try:
			# Read existing log contents
			with open(log_path, 'r', encoding='utf-8') as f:
				lines = f.readlines()
			
			# Trim if over 100 lines
			if len(lines) > self.MAX_DEBUG_COUNT:
				with open(log_path, 'w', encoding='utf-8') as f:
					f.writelines(lines[-self.MAX_DEBUG_COUNT:])
					
		except Exception as e:
			print(f"Error cleaning debug log: {e}")

	def clean_log(self):
		"""Maintain log size by rotating files"""
		log_path = os.path.join(self.LOG_DIR, CONFIG["global"]["log_file"])
		
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

	def log_message(self, level: str, message: str):
		"""Unified logging function with level-based filtering and rotation"""
		# Get config values
		main_log = os.path.join(self.LOG_DIR, CONFIG["global"]["log_file"])
		debug_log = os.path.join(self.LOG_DIR, self.DEBUG_LOG)
		configured_level = LOG_LEVELS.get(CONFIG["global"]["log_level"], 2)
		message_level = LOG_LEVELS.get(level.upper(), 2)
		
		try:
			# Create log directory if needed
			os.makedirs(self.LOG_DIR, exist_ok=True)
			timestamp = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}.{int(time.time() * 1000000) % 1000000:06d}"
			
			# Always write to debug log if enabled and level <= DEBUG
			if CONFIG["global"]["enable_debug"] and message_level <= LOG_LEVELS["DEBUG"]:
				debug_entry = f"{timestamp} | {level.upper()} | {message}\n"
				with open(debug_log, "a", encoding='utf-8') as f:
					f.write(debug_entry)
				self.clean_debug_log()

			# Write to main log if message level >= configured level
			if message_level >= configured_level:
				main_entry = f"{timestamp} | {level.upper()} | {message}\n"
				with open(main_log, "a", encoding='utf-8') as f:
					f.write(main_entry)
				
				# Rotate main log if needed
				if os.path.getsize(main_log) > CONFIG["global"]["max_log_count"] * 1024:
					self.clean_log()

		except Exception as e:
			sys.stderr.write(f"Logging failed: {str(e)}\n")

	# Specific level helpers
	def log_fatal(self, message: str):
		self.log_message("FATAL", message)

	def log_error(self, message: str):
		self.log_message("ERROR", message)

	def log_warn(self, message: str):
		self.log_message("WARN", message)

	def log_info(self, message: str):
		self.log_message("INFO", message)

	def log_debug(self, message: str):
		self.log_message("DEBUG", message)

	def log_trace(self, message: str):
		self.log_message("TRACE", message)

# Initialize logger
LOGGER = Logger(CONFIG_MANAGER)

# Status system
fetch_status_lock = threading.Lock()
fetch_status = {
	"current_step": None,
	"start_time": None,
	"lyric_count": 0,
	"done_time": None
}

def update_fetch_status(step, lyrics_found=0):
	with fetch_status_lock:
		fetch_status.update({
			'current_step': step,
			'lyric_count': lyrics_found,
			'start_time': time.time() if step == 'start' else fetch_status['start_time'],
			'done_time': time.time() if step in CONFIG_MANAGER.TERMINAL_STATES else None
		})

def get_current_status():
	"""Return a formatted status message"""
	with fetch_status_lock:
		step = fetch_status['current_step']
		if not step:
			return None
		
		# Cache terminal states check
		terminal_states = CONFIG_MANAGER.TERMINAL_STATES
		messages = CONFIG_MANAGER.MESSAGES
		
		# Hide status after 2 seconds for terminal states
		if step in terminal_states and fetch_status['done_time']:
			if time.time() - fetch_status['done_time'] > 2:
				return ""

		if step == 'clear':
			return ""

		# Return pre-defined message with elapsed time if applicable
		base_msg = messages.get(step, step)
		if fetch_status['start_time'] and step != 'done':
			# Use done_time if available for terminal states
			end_time = fetch_status['done_time'] or time.time()
			elapsed = end_time - fetch_status['start_time']
			return f"{base_msg} {elapsed:.1f}s"
		
		return base_msg

# ================
#  NETWORK UTILS
# ================
def has_internet_global(timeout=3):
	"""Check internet connectivity with robust error handling"""
	global_hosts = ["http://www.google.com", "http://1.1.1.1"]
	china_hosts = ["http://www.baidu.com", "http://www.qq.com"]
	
	for url in (global_hosts + china_hosts):
		try:
			import urllib.request
			urllib.request.urlopen(url, timeout=timeout)
			return True
		except:
			continue
	return False

# ================
#  ASYNC HELPERS
# ================
async def fetch_lrclib_async(artist, title, duration=None, session=None):
	"""Async version of LRCLIB fetch using aiohttp with robust error handling"""
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
				except (aiohttp.ContentTypeError, json.JSONDecodeError):
					content = await response.text()
					LOGGER.log_debug(f"LRCLIB async error: Invalid JSON. Raw response: {content[:200]}")
			else:
				LOGGER.log_debug(f"LRCLIB async error: HTTP {response.status}")
	except (aiohttp.ClientError, asyncio.TimeoutError) as e:
		LOGGER.log_debug(f"LRCLIB async error: {e}")
	finally:
		if own_session:
			await session.close()

	return None, None

def log_timeout(artist, title):
	"""Record failed lyric lookup with duplicate prevention and robust error handling"""
	try:
		timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
		log_entry = f"{timestamp} | Artist: {artist or 'Unknown'} | Title: {title or 'Unknown'}\n"
		
		log_path = os.path.join(CONFIG_MANAGER.LOG_DIR, CONFIG_MANAGER.LYRICS_TIMEOUT_LOG)

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
			with open(log_path, 'a', encoding='utf-8') as f:
				f.write(log_entry)
			LOGGER.clean_log()
	except Exception as e:
		LOGGER.log_error(f"Failed to write timeout log: {e}")

# ======================
#  CORE LYRIC FUNCTIONS
# ======================
# Pre-compiled regex patterns for performance
_FILENAME_SANITIZE_PATTERN = re.compile(r'[<>:"/\\|?*]')
_STRING_SANITIZE_PATTERN = re.compile(r'[^a-zA-Z0-9]')
_TIMESTAMP_PATTERN = re.compile(r'\[\d+:\d+\.\d+\]')
_A2_WORD_PATTERN = re.compile(r'<(\d{2}:\d{2}\.\d{2})>(.*?)<(\d{2}:\d{2}\.\d{2})>')
_A2_LINE_PATTERN = re.compile(r'^\[(\d{2}:\d{2}\.\d{2})\](.*)')
_LRC_PATTERN = re.compile(r'\[(\d+:\d+\.\d+)\](.*)')
_TIME_PATTERNS = [
	re.compile(r'^(?P<m>\d+):(?P<s>\d+\.\d+)$'),
	re.compile(r'^(?P<m>\d+):(?P<s>\d+):(?P<ms>\d{1,3})$'),
	re.compile(r'^(?P<m>\d+):(?P<s>\d+)$'),
	re.compile(r'^(?P<s>\d+\.\d+)$'),
	re.compile(r'^(?P<s>\d+)$')
]

def sanitize_filename(name):
	"""Make strings safe for filenames"""
	return _FILENAME_SANITIZE_PATTERN.sub('_', str(name))

def sanitize_string(s):
	"""Normalize strings for comparison"""
	return _STRING_SANITIZE_PATTERN.sub('', str(s)).lower()

async def fetch_lyrics_lrclib_async(artist_name, track_name, duration=None):
	"""Async version of LRCLIB fetch"""
	LOGGER.log_debug(f"Querying LRCLIB API: {artist_name} - {track_name}")
	try:
		result = await fetch_lrclib_async(artist_name, track_name, duration)
		if result[0]:
			LOGGER.log_info(f"LRCLIB returned {'synced' if result[1] else 'plain'} lyrics")
		return result
	except Exception as e:
		LOGGER.log_error(f"LRCLIB fetch failed: {str(e)}")
		return None, None

def validate_lyrics(content, artist, title):
	"""More lenient validation with robust error handling"""
	# Use pre-compiled pattern
	if _TIMESTAMP_PATTERN.search(content):
		return True
		
	# Allow empty content for instrumental markers
	if not content.strip():
		return True
		
	# Normalize comparison parameters
	norm_content = sanitize_string(content)
	
	return True  # Temporary accept all content

executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)

async def fetch_lyrics_syncedlyrics_async(artist_name, track_name, duration=None, timeout=15):
	"""Async version of syncedlyrics fetch"""
	LOGGER.log_debug(f"Starting syncedlyrics search: {artist_name} - {track_name} ({duration}s)")
	try:
		import syncedlyrics
		
		LOGGER.log_debug(f"Loaded providers: {CONFIG_MANAGER.PROVIDERS}")
		
		def worker(search_term, synced=True):
			"""Worker for lyric search"""
			try: 
				result = syncedlyrics.search(search_term) if synced else syncedlyrics.search(search_term, plain_only=True, providers=CONFIG_MANAGER.PROVIDERS)
				return result, synced
			except Exception as e:
				LOGGER.log_debug(f"Lyrics search error: {e}")
				return None, False

		search_term = f"{track_name} {artist_name}".strip()
		if not search_term:
			LOGGER.log_debug("Empty search term")
			return None, None

		# Run in thread to avoid blocking
		loop = asyncio.get_event_loop()
		
		# Fetch synced lyrics first
		lyrics, is_synced = await loop.run_in_executor(None, worker, search_term, True)
		
		if lyrics:
			LOGGER.log_debug(f"Found {'synced' if is_synced else 'plain'} lyrics via syncedlyrics")
			if not validate_lyrics(lyrics, artist_name, track_name):
				LOGGER.log_warn("Lyrics validation failed but using anyway")
			return lyrics, is_synced
		
		# Fallback to plain lyrics
		LOGGER.log_trace("Initiating plain lyrics fallback search")
		lyrics, is_synced = await loop.run_in_executor(None, worker, search_term, False)
		
		if lyrics and validate_lyrics(lyrics, artist_name, track_name):
			return lyrics, False

		return None, None
		
	except Exception as e:
		LOGGER.log_debug(f"Lyrics fetch error: {e}")
		return None, None

def save_lyrics(lyrics, track_name, artist_name, extension):
	"""Save lyrics to appropriate file format with robust error handling"""
	try:
		folder = CONFIG_MANAGER.LYRIC_CACHE_DIR
		os.makedirs(folder, exist_ok=True)
		
		# Generate safe filename
		sanitized_track = sanitize_filename(track_name)
		sanitized_artist = sanitize_filename(artist_name)
		filename = f"{sanitized_track}_{sanitized_artist}.{extension}"
		file_path = os.path.join(folder, filename)
		
		with open(file_path, "w", encoding="utf-8") as f:
			f.write(lyrics)
		LOGGER.log_info(f"Saved lyrics to: {file_path}")
		LOGGER.log_trace(f"Lyrics content sample: {lyrics[:200]}...")
		return file_path
	except Exception as e:
		LOGGER.log_error(f"Failed to save lyrics: {str(e)}")
		return None

def is_lyrics_timed_out(artist_name, track_name):
	"""Check if track is in timeout log"""
	log_path = os.path.join(CONFIG_MANAGER.LOG_DIR, CONFIG_MANAGER.LYRICS_TIMEOUT_LOG)

	if not os.path.exists(log_path):
		return False

	try:
		search_artist = artist_name or 'Unknown'
		search_title = track_name or 'Unknown'
		artist_str = f"Artist: {search_artist}"
		title_str = f"Title: {search_title}"
		
		with open(log_path, 'r', encoding='utf-8') as f:
			for line in f:
				if artist_str in line and title_str in line:
					return True
		return False
	except Exception as e:
		LOGGER.log_debug(f"Timeout check error: {e}")
		return False 

async def find_lyrics_file_async(audio_file, directory, artist_name, track_name, duration=None):
	"""Async version of find_lyrics_file with non-blocking operations and concurrent online fetch"""
	update_fetch_status('local')
	LOGGER.log_info(f"Starting lyric search for: {artist_name or 'Unknown'} - {track_name}")

	try:
		LOGGER.log_debug(f"{audio_file}, {directory}, {artist_name}, {track_name}, {duration}")

		# --- Instrumental early check ---
		is_instrumental = (
			"instrumental" in track_name.lower() or 
			(artist_name and "instrumental" in artist_name.lower())
		)
		if is_instrumental:
			LOGGER.log_debug("Instrumental track detected")
			update_fetch_status('instrumental')
			return save_lyrics("[Instrumental]", track_name, artist_name, 'txt')

		# --- Local file search (direct audio_file base name) ---
		if audio_file and directory and audio_file != "None":
			base_name, _ = os.path.splitext(os.path.basename(audio_file))
			local_files = [
				(os.path.join(directory, f"{base_name}.a2"), 'a2'),
				(os.path.join(directory, f"{base_name}.lrc"), 'lrc'),
				(os.path.join(directory, f"{base_name}.txt"), 'txt')
			]

			for file_path, ext in local_files:
				if os.path.exists(file_path):
					try:
						# --- Delete empty files ---
						if os.path.getsize(file_path) == 0:
							LOGGER.log_debug(f"Deleting empty file: {file_path}")
							os.remove(file_path)
							continue

						with open(file_path, 'r', encoding='utf-8') as f:
							content = f.read()

						# Delete if content only whitespace or newlines
						if not content.strip():
							LOGGER.log_debug(f"Deleting blank lyric file: {file_path}")
							os.remove(file_path)
							continue

						if validate_lyrics(content, artist_name, track_name):
							LOGGER.log_info(f"Using validated lyrics file: {file_path}")
							return file_path
						else:
							LOGGER.log_info(f"Using unvalidated local {ext} file: {file_path}")
							return file_path

					except Exception as e:
						LOGGER.log_debug(f"File read error: {file_path} - {e}")
						continue

		# --- Build possible filename patterns ---
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

		# --- Search in directory + cache dir ---
		dirs_to_check = [d for d in [directory, CONFIG_MANAGER.LYRIC_CACHE_DIR] if d]
		for dir_path in dirs_to_check:
			for filename in possible_filenames:
				file_path = os.path.join(dir_path, filename)
				if os.path.exists(file_path):
					try:
						# --- Delete empty/blank files ---
						if os.path.getsize(file_path) == 0:
							LOGGER.log_debug(f"Deleting empty file: {file_path}")
							os.remove(file_path)
							continue

						with open(file_path, 'r', encoding='utf-8') as f:
							content = f.read()

						if not content.strip():
							LOGGER.log_debug(f"Deleting blank lyric file: {file_path}")
							os.remove(file_path)
							continue

						if validate_lyrics(content, artist_name, track_name):
							LOGGER.log_debug(f"Using validated file: {file_path}")
							return file_path
						else:
							LOGGER.log_debug(f"Using unvalidated file: {file_path}")
							return file_path
					except Exception as e:
						LOGGER.log_debug(f"Error reading {file_path}: {e}")
						continue

		# --- Timeout check ---
		if is_lyrics_timed_out(artist_name, track_name):
			update_fetch_status('time_out')
			LOGGER.log_debug(f"Lyrics timeout active for {artist_name} - {track_name}")
			return None

		# --- Concurrent online fetch ---
		update_fetch_status('synced')
		LOGGER.log_debug(f"Fetching lyrics concurrently for: {artist_name} - {track_name}")

		tasks = [fetch_lyrics_lrclib_async(artist_name, track_name, duration)]

		if CONFIG_MANAGER.ALLOW_SYNCEDLYRIC:
			tasks.append(fetch_lyrics_syncedlyrics_async(artist_name, track_name, duration))
		elif not CONFIG_MANAGER.PROVIDER_FALLBACK:
			tasks = [fetch_lyrics_lrclib_async(artist_name, track_name, duration)]

		results = await asyncio.gather(*tasks, return_exceptions=True)

		candidates = []
		for idx, result in enumerate(results):
			if isinstance(result, Exception):
				LOGGER.log_debug(f"Fetch task {idx} raised an exception: {result}")
				continue

			fetched_lyrics, is_synced = result
			if not fetched_lyrics:
				continue

			if not validate_lyrics(fetched_lyrics, artist_name, track_name):
				LOGGER.log_debug("Validation warning - possible mismatch")
				fetched_lyrics = "[Validation Warning] Potential mismatch\n" + fetched_lyrics

			is_enhanced = any(re.search(r'<\d+:\d+\.\d+>', line) for line in fetched_lyrics.split('\n'))
			has_lrc_timestamps = re.search(r'\[\d+:\d+\.\d+\]', fetched_lyrics) is not None

			if is_enhanced:
				extension = 'a2'
			elif is_synced and has_lrc_timestamps:
				extension = 'lrc'
			else:
				extension = 'txt'

			candidates.append((extension, fetched_lyrics))

			line_count = len(fetched_lyrics.split('\n'))
			LOGGER.log_debug(f"Lyrics stats - Lines: {line_count}, "
							 f"Chars: {len(fetched_lyrics)}, "
							 f"Synced: {is_synced}, Format: {extension}")

		if not candidates:
			LOGGER.log_debug("No lyrics found from any source")
			update_fetch_status("failed")
			if has_internet_global():
				log_timeout(artist_name, track_name)
			return None

		priority_order = CONFIG_MANAGER.PROVIDER_FORMAT_PRIORITY
		candidates.sort(key=lambda x: priority_order.index(x[0]))
		best_extension, best_lyrics = candidates[0]

		LOGGER.log_debug(f"Selected lyrics format: {best_extension}")
		return save_lyrics(best_lyrics, track_name, artist_name, best_extension)

	except Exception as e:
		LOGGER.log_error(f"Error in find_lyrics_file: {str(e)}")
		update_fetch_status("failed")
		return None

def parse_time_to_seconds(time_str):
	"""Convert various timestamp formats to seconds with millisecond precision."""
	# Use pre-compiled patterns
	patterns = _TIME_PATTERNS
	
	for pattern in patterns:
		match = pattern.match(time_str)
		if match:
			parts = match.groupdict()
			minutes = int(parts.get('m', 0) or 0)
			seconds = float(parts.get('s', 0) or 0)
			milliseconds = int(parts.get('ms', 0) or 0) / 1000
			return round(minutes * 60 + seconds + milliseconds, 3)
	
	raise ValueError(f"Invalid time format: {time_str}")

def load_lyrics(file_path):
	"""Parse lyric file into time-text pairs with robust error handling"""
	lyrics = []
	errors = []
	LOGGER.log_trace(f"Parsing lyrics file: {file_path}")
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
			
			# Use pre-compiled patterns
			line_pattern = _A2_LINE_PATTERN
			word_pattern = _A2_WORD_PATTERN

			for line in lines:
				line = line.strip()
				if not line:
					continue

				# Parse line timing
				line_match = line_pattern.match(line)
				if line_match:
					try:
						line_time = parse_time_to_seconds(line_match.group(1))
						lyrics.append((line_time, None))
						content = line_match.group(2)
						
						# Parse word-level timing
						words = word_pattern.findall(content)
						for start_str, text, end_str in words:
							try:
								start = parse_time_to_seconds(start_str)
								end = parse_time_to_seconds(end_str)
								clean_text = re.sub(r'<.*?>', '', text).strip()
								if clean_text:
									lyrics.append((start, (clean_text, end)))
							except ValueError as e:
								errors.append(f"Invalid word timestamp format: {e}")
								continue
						
						# Handle remaining text
						remaining = re.sub(word_pattern, '', content).strip()
						if remaining:
							lyrics.append((line_time, (remaining, line_time)))
						lyrics.append((line_time, None))
					except ValueError as e:
						errors.append(f"Invalid line timestamp format: {e}")
						continue

		# Plain Text Format
		elif file_path.endswith('.txt'):
			for line in lines:
				raw_line = line.rstrip('\n')
				lyrics.append((None, raw_line))
		# LRC Format
		else:
			# Use pre-compiled pattern
			lrc_pattern = _LRC_PATTERN
			for line in lines:
				raw_line = line.rstrip('\n')
				line_match = lrc_pattern.match(raw_line)
				if line_match:
					try:
						line_time = parse_time_to_seconds(line_match.group(1))
						lyric_content = line_match.group(2).strip()
						lyrics.append((line_time, lyric_content))
					except ValueError as e:
						errors.append(f"Invalid timestamp format: {e}")
						continue
				else:
					lyrics.append((None, raw_line))
		
		return lyrics, errors
	except Exception as e:
		errors.append(f"Unexpected parsing error: {str(e)}")
		if errors:
			LOGGER.log_warn(f"Found {len(errors)} parsing errors")
		return lyrics, errors

# ==============
#  PLAYER DETECTION
# ==============
'''
Using single variable is faster than dictionary so single variable is faster
╭─jake@macbook 68 ~ 
╰─$ python -m timeit "x = 0"     
10000000 loops, best of 5: 31.6 nsec per loop
╭─jake@macbook 68 ~ 
╰─$ python -m timeit "x = []"    
5000000 loops, best of 5: 71.7 nsec per loop

I may need to optimze the whole program with just fixed variable it will give at least 50% performance boost
'''

async def get_cmus_info():
	try:
		proc = await asyncio.create_subprocess_exec(
			'cmus-remote', '-Q',
			stdout=asyncio.subprocess.PIPE,
			stderr=asyncio.subprocess.PIPE
		)
		stdout, _ = await proc.communicate()
		if proc.returncode != 0:
			return (None, 0, "", None, 0, "stopped")

		# LOGGER.log_debug("Cmus-remote polling...") cache the class call
		output = stdout.decode().splitlines()
		
		file = None
		position = 0
		artist = []
		title = None
		duration = 0
		status = "stopped"
		tags = {}
		
		for line in output:
			if line.startswith("file "):
				file = line[5:].strip()
			elif line.startswith("status "):
				status = line[7:].strip()
			elif line.startswith("position "):
				try:
					position = int(line[9:].strip())
				except ValueError:
					position = 0
			elif line.startswith("duration "):
				try:
					duration = int(line[9:].strip())
				except ValueError:
					duration = 0
			elif line.startswith("tag "):
				parts = line.split(" ", 2)
				if len(parts) == 3:
					tag_name, tag_value = parts[1], parts[2].strip()
					tags[tag_name] = tag_value

		def split_artists(tag_value):
			if not tag_value:
				return []
			return [a.strip() for a in tag_value.replace("/", ";").split(";") if a.strip()]

		aa = tags.get("albumartist")
		ar = tags.get("artist")

		if aa == "Various Artists" and ar:
			artists_list = split_artists(ar)
		elif aa:
			artists_list = split_artists(aa)
		elif ar:
			artists_list = split_artists(ar)
		else:
			artists_list = []

		artist_str = ", ".join(artists_list) if artists_list else ""

		title = tags.get("title")
		
		return (file, position, artist_str, title, duration, status)
		
	except Exception:
		return (None, 0, "", None, 0, "stopped")

async def get_mpd_info():
	"""Async get current playback info from MPD"""
	def _sync_mpd():
		client = MPDClient()
		client.timeout = CONFIG_MANAGER.MPD_TIMEOUT
		try:
			client.connect(CONFIG_MANAGER.MPD_HOST, CONFIG_MANAGER.MPD_PORT)
			LOGGER.log_debug("MPD polling...")
			if CONFIG_MANAGER.MPD_PASSWORD:
				client.password(CONFIG_MANAGER.MPD_PASSWORD)
			status = client.status()
			current_song = client.currentsong()
			artist = current_song.get("artist", "")
			if isinstance(artist, list):
				artist = ", ".join(artist)
			
			file = current_song.get("file", "")
			position = float(status.get("elapsed", 0))
			title = current_song.get("title", None),
			duration = float(status.get("duration", status.get("time", 0)))
			status = status.get("state", "stopped")
			
			client.close()
			client.disconnect()
			return (file, position, artist, title, duration, status)
			
		except (socket.error, ConnectionRefusedError):
			pass
		except Exception as e:
			LOGGER.log_debug(f"Unexpected MPD error: {str(e)}")
		update_fetch_status("mpd")
		return (None, 0.0, "", None, 0.0, "stopped")
	return await asyncio.to_thread(_sync_mpd)

async def get_playerctl_info():
	"""Async get current playback info from any player via playerctl"""
	try:
		proc = await asyncio.create_subprocess_exec(
			"playerctl", "metadata",
			"--format",
			"{{playerName}}|{{artist}}|{{title}}|{{position}}|{{status}}|{{mpris:length}}",
			stdout=asyncio.subprocess.PIPE,
			stderr=asyncio.subprocess.PIPE
		)
		stdout, _ = await proc.communicate()
		output = stdout.decode().strip()

		LOGGER.log_debug("playerctl polling...")

		if "No players found" in output or not output:
			return (None, 0.0, "", None, 0.0, "stopped")

		fields = output.split("|")
		if len(fields) != 6:
			return (None, 0.0, "", None, 0.0, "stopped")

		_, artist, title, position, status, duration = fields

		position_sec = float(position) / 1_000_000 if position else 0.0
		duration_sec = float(duration) / 1_000_000 if duration else 0.0

		status = status.lower() if status else "stopped"
		if position_sec < 0 or (duration_sec > 0 and position_sec > duration_sec * 1.5):
			position_sec = duration_sec if status == "paused" else 0.0

		return (None, position_sec, artist or "", title, duration_sec, status)

	except Exception:
		return (None, 0.0, "", None, 0.0, "stopped")


async def get_player_info():
	"""Async detect active player (CMUS, MPD, or playerctl)"""
	if CONFIG_MANAGER.ENABLE_CMUS:
		try:
			cmus_info = await get_cmus_info()
			if cmus_info[0] is not None:
				return 'cmus', cmus_info
		except Exception as e:
			LOGGER.log_debug(f"CMUS detection failed: {str(e)}")

	if CONFIG_MANAGER.ENABLE_MPD:
		try:
			mpd_info = await get_mpd_info()
			if mpd_info[0] is not None:
				return 'mpd', mpd_info
		except Exception as e:
			LOGGER.log_debug(f"MPD detection failed: {str(e)}")

	if CONFIG_MANAGER.ENABLE_PLAYERCTL:
		try:
			playerctl_info = await get_playerctl_info()
			if playerctl_info[3] is not None:
				return "playerctl", playerctl_info
		except Exception as e:
			LOGGER.log_debug(f"MPRIS detection failed: {str(e)}")

	update_fetch_status("no_player")
	LOGGER.log_debug("No active music player detected")
	return None, (None, 0, "", None, 0, "stopped")

# ==============
#  UI RENDERING
# ==============
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
			return CONFIG_MANAGER.COLOR_NAMES.get(color, 7)  # Default to white
			
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

# Global display cache
_display_cache = {
	'lyrics_hash': None,
	'window_width': None,
	'wrapped_lines': [],
	'wrapped_widths': [],
	'widths_cache': {},
	'a2_groups': None,
	'a2_word_cache': {}
}

def get_lyrics_hash(lyrics):
	"""Generate a simple hash for lyrics to detect changes"""
	if not lyrics:
		return 0
	# Create a hashable representation
	return hash(tuple((t, str(item)) for t, item in lyrics))

def wrap_by_display_width(text, width, subsequent_indent=''):
	"""
	Wrap text by display cell width, not character count.
	Uses wcswidth to handle multi-byte characters properly.
	"""
	# Cache wcswidth function locally for performance
	get_width = wcswidth
	
	if not text:
		return []
	
	lines = []
	current_line = []
	current_width = 0
	
	words = re.split(r'(\s+)', text)
	
	for word in words:
		if not word:
			continue
			
		word_width = get_width(word)
		
		# If it's a whitespace-only word, we might want to collapse it
		if word.isspace() and not current_line:
			continue  # Skip leading whitespace
		
		# If word fits on current line or line is empty
		if current_width + word_width <= width or not current_line:
			current_line.append(word)
			current_width += word_width
		else:
			# Start new line
			lines.append(''.join(current_line))
			# For continuation lines, add the indent
			if lines:  # If this is not the first line
				current_line = [subsequent_indent + word.lstrip()]
				current_width = get_width(subsequent_indent) + get_width(word.lstrip())
			else:
				current_line = [word]
				current_width = word_width
	
	if current_line:
		lines.append(''.join(current_line))
	
	# Clean up: remove any trailing whitespace from each line
	lines = [line.rstrip() for line in lines]
	
	return lines

def display_lyrics(
	stdscr,
	lyrics,
	errors,
	position,
	current_title,
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
	"""Render lyrics in curses interface with caching optimizations"""
	
	height, width = stdscr.getmaxyx()
	
	# Generate lyrics hash for cache invalidation
	lyrics_hash = get_lyrics_hash(lyrics)
	
	# Layout constants
	STATUS_LINES = 2
	MAIN_STATUS_LINE = height - 1
	TIME_ADJUST_LINE = height - 2
	LYRICS_AREA_HEIGHT = height - STATUS_LINES - 1
	
	if LYRICS_AREA_HEIGHT <= 0:
		stdscr.noutrefresh()
		return 0
	
	# Cache invalidation logic
	global _display_cache
	cache_invalid = (
		_display_cache['lyrics_hash'] != lyrics_hash or
		_display_cache['window_width'] != width
	)
	
	if cache_invalid:
		_display_cache.update({
			'lyrics_hash': lyrics_hash,
			'window_width': width,
			'wrapped_lines': [],
			'wrapped_widths': [],
			'widths_cache': {},
			'a2_groups': None,
			'a2_word_cache': {}
		})
	
	# Handle window resizing
	if not hasattr(display_lyrics, '_dims') or display_lyrics._dims != (height, width):
		curses.resizeterm(height, width)
		display_lyrics.error_win = curses.newwin(1, width, 0, 0)
		display_lyrics.lyrics_win = curses.newwin(LYRICS_AREA_HEIGHT, width, 1, 0)
		display_lyrics.adjust_win = curses.newwin(1, width, TIME_ADJUST_LINE, 0)
		display_lyrics.status_win = curses.newwin(1, width, MAIN_STATUS_LINE, 0)
		display_lyrics._dims = (height, width)
		cache_invalid = True  # Force cache rebuild
	
	error_win = display_lyrics.error_win
	lyrics_win = display_lyrics.lyrics_win
	adjust_win = display_lyrics.adjust_win
	status_win = display_lyrics.status_win
	
	if use_manual_offset and manual_offset != 0 and position is not None:
		try:
			position += int(manual_offset * 1_000_000)
		except Exception:
			pass
	
	# Cache frequently used functions locally
	wrap_func = wrap_by_display_width
	get_width = wcswidth
	max_func = max
	min_func = min
	
	# --- 1) Render errors ---
	error_win.erase()
	if errors:
		try:
			err_str = f"Errors: {len(errors)}"[:width - 1]
			error_win.addstr(0, 0, err_str, curses.color_pair(1))
		except curses.error:
			pass
	error_win.noutrefresh()
	
	# --- 2) Render lyrics with caching ---
	lyrics_win.erase()
	
	if is_a2_format:
		# Build A2 group lines (only if cache is invalid)
		if cache_invalid or _display_cache['a2_groups'] is None:
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
			_display_cache['a2_groups'] = a2_lines
		else:
			a2_lines = _display_cache['a2_groups']
		
		visible = LYRICS_AREA_HEIGHT
		max_start = max_func(0, len(a2_lines) - visible)
		start_line = (min_func(max_func(manual_offset, 0), max_start)
					 if use_manual_offset else max_start)
		y = 0
		
		for idx in range(start_line, min_func(start_line + visible, len(a2_lines))):
			if y >= visible:
				break
			line = a2_lines[idx]
			# Cache line strings
			line_key = tuple((t, str(text)) for t, (text, _) in line)
			if line_key not in _display_cache['a2_word_cache']:
				line_str = " ".join(text for _, (text, _) in line)
				# Pre-calculate widths for each word
				word_widths = []
				for _, (text, _) in line:
					if text not in _display_cache['widths_cache']:
						_display_cache['widths_cache'][text] = get_width(text)
					word_widths.append(_display_cache['widths_cache'][text])
				_display_cache['a2_word_cache'][line_key] = (line_str, word_widths)
			
			line_str, word_widths = _display_cache['a2_word_cache'][line_key]
			
			# Compute x for alignment
			total_width = sum(word_widths) + (len(word_widths) - 1)  # Account for spaces
			if alignment == 'right':
				x = max_func(0, width - total_width - 1)
			elif alignment == 'center':
				x = max_func(0, (width - total_width) // 2)
			else:
				x = 1
			
			cursor = 0
			for word_idx, (_, (text, _)) in enumerate(line):
				txt_width = word_widths[word_idx]
				space_left = width - x - cursor - 1
				if space_left <= 0:
					break
				txt = text[:space_left]
				color = curses.color_pair(2) if idx == len(a2_lines) - 1 else curses.color_pair(3)
				try:
					lyrics_win.addstr(y, x + cursor, txt, color)
				except curses.error:
					break
				cursor += txt_width + 1
			y += 1
		start_screen_line = start_line
	
	else:
		# LRC/TXT format with caching - FIXED VERSION
		wrap_w = max_func(10, width - 2)
		
		if cache_invalid or not _display_cache['wrapped_lines']:
			wrapped = []
			widths = []
			for orig_i, (_, ly) in enumerate(lyrics):
				if ly and ly.strip():
					# Use cached wrap function for East Asian characters
					lines = wrap_func(ly, wrap_w, subsequent_indent=' ')
					
					if lines:
						# First line
						wrapped.append((orig_i, lines[0]))
						if lines[0] not in _display_cache['widths_cache']:
							_display_cache['widths_cache'][lines[0]] = get_width(lines[0])
						widths.append(_display_cache['widths_cache'][lines[0]])
						for cont in lines[1:]:
							wrapped.append((orig_i, cont))
							if cont not in _display_cache['widths_cache']:
								_display_cache['widths_cache'][cont] = get_width(cont)
							widths.append(_display_cache['widths_cache'][cont])
				else:
					wrapped.append((orig_i, ''))
					widths.append(0)
			_display_cache['wrapped_lines'] = wrapped
			_display_cache['wrapped_widths'] = widths
		else:
			wrapped = _display_cache['wrapped_lines']
			widths = _display_cache['wrapped_widths']
		
		total = len(wrapped)
		avail = LYRICS_AREA_HEIGHT
		max_start = max_func(0, total - avail)
		
		if use_manual_offset:
			start_screen_line = min_func(max_func(manual_offset, 0), max_start)
		else:
			if current_idx >= len(lyrics) - 1:
				start_screen_line = max_start
			else:
				idxs = [i for i, (o, _) in enumerate(wrapped) if o == current_idx]
				if idxs:
					center = (idxs[0] + idxs[-1]) // 2
					ideal = center - avail // 2
					start_screen_line = min_func(max_func(ideal, 0), max_start)
				else:
					start_screen_line = min_func(max_func(current_idx, 0), max_start)
		
		y = 0
		for i in range(avail):
			if start_screen_line + i >= total:
				break
			
			orig_i, line = wrapped[start_screen_line + i]
			txt = line.strip()[:width - 1]
			disp_width = widths[start_screen_line + i]
			
			if alignment == 'right':
				x = max_func(0, width - disp_width - 1)
			elif alignment == 'center':
				x = max_func(0, (width - disp_width) // 2)
			else:
				x = 1
			
			if is_txt_format:
				color = curses.color_pair(4) if orig_i == current_idx else curses.color_pair(5)
			else:
				color = curses.color_pair(2) if orig_i == current_idx else curses.color_pair(3)
			
			try:
				lyrics_win.addstr(y, x, txt, color)
			except curses.error:
				pass
			y += 1
		
		lyrics_win.noutrefresh()

	# --- 3) Time-adjust or End-of-lyrics ---
	adjust_win.erase()
	if current_idx is not None and current_idx == len(lyrics) - 1 and not is_txt_format and len(lyrics) > 1:
		try:
			adjust_win.addstr(0, 0, " End of lyrics ", curses.color_pair(2) | curses.A_BOLD)
		except curses.error:
			pass
	elif time_adjust:
		adj_str = f" Offset: {time_adjust:+.1f}s "[:width - 1]
		try:
			adjust_win.addstr(0, max_func(0, width - len(adj_str) - 1),
							   adj_str, curses.color_pair(2) | curses.A_BOLD)
		except curses.error:
			pass
	adjust_win.noutrefresh()

	# --- 4) Status bar ---
	status_win.erase()
	if CONFIG_MANAGER.DISPLAY_NAME:
		if player_info:
			_, data = player_info
			artist = data[2] or ''
			file_basename = ''
			if data[0] and data[0] != "None":
				try:
					file_basename = os.path.basename(data[0])
				except (TypeError, AttributeError):
					file_basename = ''
			title = data[3] or file_basename
			is_inst = any(x in title.lower() for x in ['instrumental', 'karaoke'])
		else:
			title, artist, is_inst = 'No track', '', False

		ps = f"{title} - {artist}"
		cur_line = min_func(current_idx + 1, len(lyrics)) if lyrics else 0
		adj_flag = '' if is_inst else ('[Adj] ' if time_adjust else '')
		icon = ' ⏳ ' if is_fetching else ' 🎵 '

		# Compose right text
		right_text_full = f"Line {cur_line}/{len(lyrics)}{adj_flag}"
		right_text_fallback = f" {cur_line}/{len(lyrics)}{adj_flag} "

		# Determine available space for left text
		if len(f"{icon}{ps} • {right_text_full}") <= width - 1:
			display_line = f"{icon}{ps} • {right_text_full}"
		elif len(f"{icon}{ps} • {right_text_fallback}") <= width - 1:
			right_text = right_text_fallback
			left_max = width - 1 - len(right_text) - 1
			ps_trunc = f"{icon}{ps}"
			if len(ps_trunc) > left_max:
				trunc_len = max_func(0, left_max - 3)
				ps_trunc = ps_trunc[:trunc_len] + '...' if trunc_len > 0 else ''
			padding = ' ' * max_func(left_max - len(ps_trunc), 0)
			display_line = f"{ps_trunc}{padding} {right_text} "
		else:
			# Not enough space for full right text
			right_text = right_text_fallback
			max_right = width - 1
			if len(right_text) > max_right:
				right_text = right_text[:max_right]
				display_line = right_text
			else:
				left_max = width - 1 - len(right_text) - 1
				ps_trunc = f"{icon}{ps} "
				if len(ps_trunc) > left_max:
					trunc_len = max_func(0, left_max - 3)
					ps_trunc = ps_trunc[:trunc_len] + '...' if trunc_len > 0 else ''
				padding = ' ' * max_func(left_max - len(ps_trunc), 0)
				display_line = f"{ps_trunc}{padding} {right_text} "

		try:
			safe_width = max_func(0, width - 1)
			status_win.addstr(0, 0, display_line[:safe_width], curses.color_pair(5) | curses.A_BOLD)
		except curses.error:
			pass
	else:
		info = f"Line {min_func(current_idx + 1, len(lyrics))}/{len(lyrics)}"
		if time_adjust:
			info += '[Adj]'
		try:
			status_win.addstr(0, 0, info[:width - 1], curses.A_BOLD)
		except curses.error:
			pass
	status_win.noutrefresh()

	# Overlay centered status message
	status_msg = get_current_status()
	if status_msg:
		msg = f"  [{status_msg}]  "[:width - 1]
		try:
			status_win.addstr(0, max_func(0, (width - len(msg)) // 2),
							   msg, curses.color_pair(2) | curses.A_BOLD)
		except curses.error:
			pass
	status_win.noutrefresh()

	curses.doupdate()
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
	"""Input handler with scroll, timing, and alignment logic"""

	new_alignment = current_alignment
	input_processed = False
	manual_input = False
	time_adjust_input = False
	alignment_input = False

	# Cache alignments list
	alignments = ["left", "center", "right"]
	
	# Quit
	if key in key_bindings["quit"]:
		atexit.register(executor.shutdown)
		sys.exit("Exiting")

	# Scroll handling
	if key in key_bindings["scroll_up"]:
		manual_offset = max(0, manual_offset - 1)
		manual_input, input_processed = True, True
	elif key in key_bindings["scroll_down"]:
		manual_offset += 1
		manual_input, input_processed = True, True

	# Time adjustments (explicit elif chain)
	elif key in key_bindings["time_decrease"]:
		time_adjust -= 0.1
		time_adjust_input, input_processed = True, True

	elif key in key_bindings["time_increase"]:
		time_adjust += 0.1
		time_adjust_input, input_processed = True, True

	elif key in key_bindings["time_reset"]:
		time_adjust = 0.0
		time_adjust_input, input_processed = True, True

	elif key in key_bindings["time_jump_increase"]:
		time_adjust += 5.0
		time_adjust_input, input_processed = True, True

	elif key in key_bindings["time_jump_decrease"]:
		time_adjust -= 5.0
		time_adjust_input, input_processed = True, True

	# Alignment direct selection (explicit elif chain)
	elif key in key_bindings["align_left"]:
		new_alignment = "left"
		alignment_input, input_processed = True, True

	elif key in key_bindings["align_center"]:
		new_alignment = "center"
		alignment_input, input_processed = True, True

	elif key in key_bindings["align_right"]:
		new_alignment = "right"
		alignment_input, input_processed = True, True

	# Alignment cycling
	elif key in key_bindings["align_cycle_forward"]:
		new_alignment = alignments[(alignments.index(current_alignment) + 1) % 3]
		alignment_input, input_processed = True, True

	elif key in key_bindings["align_cycle_backward"]:
		new_alignment = alignments[(alignments.index(current_alignment) - 1) % 3]
		alignment_input, input_processed = True, True

	# Resize handling
	if key == curses.KEY_RESIZE:
		needs_redraw = True
	elif input_processed:
		# Timestamp logic
		if manual_input:
			last_input_time = time.time()
		elif time_adjust_input or alignment_input:
			last_input_time = 0
		needs_redraw = True

	return True, manual_offset, last_input_time, needs_redraw, time_adjust, new_alignment

def update_display(stdscr, lyrics, errors, position, current_title, manual_offset, 
				   is_txt_format, is_a2_format, current_idx, manual_scroll_active, 
				   time_adjust=0, is_fetching=False, subframe_fraction=0.0,alignment='center', player_info = None):
	"""Update display based on current state."""
	if is_txt_format:
		return display_lyrics(stdscr, lyrics, errors, position, 
							  current_title, manual_offset, 
							  is_txt_format, is_a2_format, current_idx, True, 
							  time_adjust, is_fetching, subframe_fraction, alignment, player_info)
	else:
		return display_lyrics(stdscr, lyrics, errors, position, 
							  current_title, manual_offset, 
							  is_txt_format, is_a2_format, current_idx, 
							  manual_scroll_active, time_adjust, is_fetching, subframe_fraction, alignment, player_info)

# ================
#  LYRIC FETCHING
# ================
async def fetch_lyrics_async(audio_file, directory, artist, title, duration):
	"""Async function to fetch lyrics with non-blocking operations"""
	try:
		lyrics_file = await find_lyrics_file_async(audio_file, directory, artist, title, duration)
		if lyrics_file:
			is_txt_format = lyrics_file.endswith('.txt')
			is_a2_format = lyrics_file.endswith('.a2')
			lyrics, errors = load_lyrics(lyrics_file)
			update_fetch_status('done', len(lyrics))
			return (lyrics, errors), is_txt_format, is_a2_format
		update_fetch_status('failed')
		return ([], []), False, False
	except Exception as e:
		LOGGER.log_error(f"{title} lyrics fetch error: {e}")
		update_fetch_status('failed')
		return ([], []), False, False
		


# ================
#  SYNC UTILITIES
# ================
def sync_player_position(status, raw_pos, last_time, time_adjust, duration):
	now = time.perf_counter()
	elapsed = now - last_time
	
	if status == "playing":
		estimated = raw_pos + elapsed + time_adjust
	else:
		estimated = raw_pos + time_adjust
	LOGGER.log_debug(f"Position sync - Raw: {raw_pos}, Adjusted: {estimated}")
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

def get_monitor_refresh_rate():
	try:
		# Query active monitor mode
		xrandr_output = subprocess.check_output(["xrandr"]).decode()
		# Find the line with '*' which marks the active mode
		match = re.search(r"(\d+\.\d+)\*", xrandr_output)
		if match:
			return float(match.group(1))
	except Exception as e:
		print("Could not detect refresh rate:", e)
	return 60.0  # fallback

# ================
#  MAIN APPLICATION
# ================
# --- Module-level constants and cached immutable data (hot) ---
PLAYER_TYPES = ("cmus", "playerctl")         # tuple (faster membership)
INSTRUMENTAL_KEYWORDS = ("instrumental", "karaoke")
STATUS_PLAYING = "playing"
STATUS_PAUSED = "paused"
STATUS_STOPPED = "stopped"

async def main_async(stdscr, CONFIG, LOGGER):
	# Local references to reduce attribute lookups
	logger = LOGGER
	log_debug = logger.log_debug
	log_info = logger.log_info

	# Cache commonly-used functions
	perf = time.perf_counter
	path_exists = os.path.exists
	path_basename = os.path.basename
	path_dirname = os.path.dirname

	# Cache stdscr methods (pre-bind)
	stdscr_getch = stdscr.getch
	stdscr_timeout = stdscr.timeout
	stdscr_nodelay = stdscr.nodelay
	stdscr_keypad = stdscr.keypad
	stdscr_curs_set = curses.curs_set
	get_size = stdscr.getmaxyx  # still need access; call only when resize detected

	# Small helper to safely get nested config value
	ui_config = CONFIG["ui"]

	# Cache config slices once
	color_config = ui_config["colors"]
	sync_config = ui_config["sync"] if "sync" in ui_config else CONFIG["ui"]["sync"]
	proximity_config = sync_config["proximity"]

	# Cache frequently accessed config values (immutable during runtime)
	refresh_interval_ms = sync_config["refresh_interval_ms"]
	refresh_interval = refresh_interval_ms / 1000.0
	refresh_interval_2 = sync_config["coolcpu_ms"]

	smart_refresh_interval = sync_config["smart_coolcpu_ms"]
	smart_refresh_interval_v2 = proximity_config["smart_coolcpu_ms_v2"]
	refresh_proximity_interval_ms = sync_config.get("refresh_proximity_interval_ms", 200)
	refresh_proximity_interval = proximity_config["smart_coolcpu_ms_v2"]

	JUMP_THRESHOLD = sync_config.get("jump_threshold_sec", 1.0)
	TEMPORARY_REFRESH_SEC = sync_config["smart_refresh_duration"]

	smart_tracking_bol = sync_config.get("smart-tracking", 0)
	proximity_threshold = sync_config.get("proximity_threshold", 0)
	smart_proximity_bol = proximity_config.get("smart-proximity", False)

	# Cache proximity thresholds (immutable)
	PROXIMITY_THRESHOLD_SEC = proximity_config.get("proximity_threshold_sec", 0.05)
	PROXIMITY_THRESHOLD_PERCENT = proximity_config.get("proximity_threshold_percent", 0.05)
	PROXIMITY_MIN_THRESHOLD_SEC = proximity_config.get("proximity_min_threshold_sec", 1.0)
	PROXIMITY_MAX_THRESHOLD_SEC = proximity_config.get("proximity_max_threshold_sec", 2.0)

	END_TRIGGER_SEC = sync_config.get("end_trigger_threshold_sec", 1.0)
	SCROLL_TIMEOUT = ui_config["scroll_timeout"]
	base_offset = sync_config.get("sync_offset_sec", 0.0)
	bisect_offset = sync_config.get("bisect_offset", 0)

	VRR_ENABLED = sync_config.get("VRR_bol", False)

	# Cache color values (resolve_color assumed available)
	error_color = resolve_color(color_config["error"])
	txt_active = resolve_color(color_config["txt"]["active"])
	txt_inactive = resolve_color(color_config["txt"]["inactive"])
	lrc_active = resolve_color(color_config["lrc"]["active"])
	lrc_inactive = resolve_color(color_config["lrc"]["inactive"])

	# Initialize curses display basics (once)
	curses.start_color()
	use_256 = curses.COLORS >= 256
	curses.init_pair(1, error_color, curses.COLOR_BLACK)
	curses.init_pair(2, lrc_active, curses.COLOR_BLACK)
	curses.init_pair(3, lrc_inactive, curses.COLOR_BLACK)
	curses.init_pair(4, txt_active, curses.COLOR_BLACK)
	curses.init_pair(5, txt_inactive, curses.COLOR_BLACK)

	# Cache key bindings and convert to sets for O(1) membership
	key_bindings = load_key_bindings(CONFIG)
	quit_keys = set(key_bindings["quit"])
	scroll_up_keys = set(key_bindings["scroll_up"])
	scroll_down_keys = set(key_bindings["scroll_down"])
	time_decrease_keys = set(key_bindings["time_decrease"])
	time_increase_keys = set(key_bindings["time_increase"])
	time_reset_keys = set(key_bindings["time_reset"])
	time_jump_increase_keys = set(key_bindings.get("time_jump_increase", []))
	time_jump_decrease_keys = set(key_bindings.get("time_jump_decrease", []))
	align_left_keys = set(key_bindings["align_left"])
	align_center_keys = set(key_bindings["align_center"])
	align_right_keys = set(key_bindings["align_right"])
	align_cycle_forward_keys = set(key_bindings["align_cycle_forward"])
	align_cycle_backward_keys = set(key_bindings["align_cycle_backward"])

	# Cache frequently used functions and methods
	bisect_left = bisect.bisect_left
	bisect_right = bisect.bisect_right
	max_func = max
	min_func = min
	abs_func = abs
	int_func = int
	str_func = str
	float_func = float
	wrap_func = wrap_by_display_width
	get_width_func = wcswidth

	# Alignments
	alignments_list = ("left", "center", "right")
	alignment_index = {"left": 0, "center": 1, "right": 2}

	# Set up curses modes (once)
	stdscr_curs_set(0)
	stdscr_nodelay(True)
	stdscr_keypad(True)
	# Start with immediate response (no timeout for manual scroll)
	stdscr_timeout(0)

	# Initialize application state as local variables (FASTEST access)
	current_title = None
	current_artist = None
	current_file = None
	lyrics = []
	errors = []
	manual_offset = 0
	last_input = 0.0
	time_adjust = 0.0
	last_raw_pos = 0.0
	last_pos_time = perf()
	timestamps = []
	valid_indices = []
	last_idx = -1
	force_redraw = True
	is_txt = False
	is_a2 = False
	window_size = get_size()
	manual_timeout_handled = True
	alignment = ui_config.get("alignment", "center").lower()
	wrapped_lines = []
	max_wrapped_offset = 0
	window_width = window_size[1]
	last_player_update = 0.0
	player_type = None
	player_data = (None, 0, "", None, 0, STATUS_STOPPED)
	
	prev_player_data = None
	# Cached unpacked player fields (update only when player_data changes)
	p_audio_file = None
	p_raw_pos = 0.0
	p_artist = ""
	p_title = ""
	p_duration = 0.0
	p_status = STATUS_STOPPED

	resume_trigger_time = None
	smart_tracking = smart_tracking_bol
	smart_proximity = smart_proximity_bol
	proximity_trigger_time = None
	proximity_active = False
	poll = False
	lyric_future = None
	lyrics_loaded_time = None
	end_triggered = False
	window_height, window_width = window_size

	# Player position tracking
	last_cmus_position = 0.0
	estimated_position = 0.0
	playback_paused = False

	# VRR variables (cached)
	next_frame_time = 0.0
	skip_redraw_for_vrr = False
	frame_time = None
	if VRR_ENABLED:
		refresh_rate = get_monitor_refresh_rate()
		frame_time = 1.0 / refresh_rate
		next_frame_time = last_pos_time + frame_time

	# State tracking for optimizations
	prev_lyrics_hash = None
	prev_window_width = window_width
	prev_continuous_position = None
	# sync_compensation = 0.0
	bisect_right = bisect.bisect_right

	# Suppress output during initialization
	sys.stdout = open(os.devnull, 'w')
	sys.stderr = open(os.devnull, 'w')

	# Main loop optimized for manual scroll with zero delays
	while True:
		# try:
		current_time = perf()
		draw_start = current_time
		needs_redraw = False

		# Compute time_since_input
		time_since_input = 0.0
		if last_input > 0.0:
			time_since_input = current_time - last_input
			if time_since_input >= SCROLL_TIMEOUT:
				if not manual_timeout_handled:
					needs_redraw = True
					manual_timeout_handled = True
				last_input = 0.0
			else:
				manual_timeout_handled = False

		manual_scroll = (last_input > 0.0)  # Simplified check


		# stdscr_timeout(0)
		
		# Read input (non-blocking, immediate response)
		key = stdscr_getch()
		new_input = key != -1

		# Handle immediate resize event via KEY_RESIZE
		if key == curses.KEY_RESIZE:
			new_size = get_size()
			if new_size != window_size:
				old_h, old_w = window_size
				new_h, new_w = new_size
				
				# Invalidate cache if width changed
				if old_w != new_w:
					# Reset cache key to force recalculation
					global _display_cache
					_display_cache['window_width'] = None
				
				# Maintain manual_offset proportionally (avoid division by zero)
				if lyrics and old_h > 0 and new_h > 0:
					manual_offset = int_func(manual_offset * (new_h / old_h))
				
				window_size = new_size
				window_height, window_width = new_size
				max_wrapped_offset = max_func(0, max_wrapped_offset)
				needs_redraw = True

		# Temporary high-frequency refresh after resume or jump
		status_for_checks = p_status  # use cached status for quick checks before updating player info
		if (player_type in PLAYER_TYPES and resume_trigger_time and
			(current_time - resume_trigger_time <= TEMPORARY_REFRESH_SEC) and
			status_for_checks == STATUS_PLAYING and lyrics):
			stdscr_timeout(smart_refresh_interval)
			poll = True
		else:
			stdscr_timeout(refresh_interval_2)
			poll = False

		# Determine fetch interval with proximity overlay (use cached proximity_active)
		if proximity_active and status_for_checks == STATUS_PLAYING:
			interval = refresh_interval
		else:
			if resume_trigger_time and (current_time - resume_trigger_time <= TEMPORARY_REFRESH_SEC):
				interval = 0.0
			else:
				interval = refresh_interval

		# Update player info if needed (this may call external async get_player_info)
		if (current_time - last_player_update >= interval):
			try:
				prev_status = p_status
				new_player_type, new_player_data = await get_player_info()
				# Update global variables that indicate player changed
				if new_player_type != player_type or new_player_data != player_data:
					player_type = new_player_type
					player_data = new_player_data
				# detect jump vs previous estimated_position using raw_val
				_, raw_val, _, _, _, status_val = player_data
				new_raw = float_func(raw_val or 0.0)
				drift = abs_func(new_raw - estimated_position)
				if drift > JUMP_THRESHOLD and status_val == STATUS_PLAYING:
					resume_trigger_time = current_time
					log_debug(f"Jump detected: {drift:.3f}s")
					needs_redraw = True

				if player_type and prev_status == STATUS_PAUSED and status_val == STATUS_PLAYING:
					resume_trigger_time = current_time
					log_debug("Pause→play refresh")
					needs_redraw = True

			except Exception as e:
				log_debug(f"Error refreshing player info: {e}")
			finally:
				last_player_update = current_time

		# Unpack player info ONLY when player_data changed
		if player_data != prev_player_data:
			prev_player_data = player_data
			# Unpack into cached locals
			p_audio_file, p_raw_pos, p_artist, p_title, p_duration, p_status = player_data
			# Normalize audio_file
			if p_audio_file in ("None", ""):
				p_audio_file = None
			# Convert numeric fields
			p_raw_pos = float_func(p_raw_pos or 0.0)
			p_duration = float_func(p_duration or 0.0)
			# Keep estimated position in sync with raw position when player changes
			estimated_position = p_raw_pos
			last_pos_time = current_time
			# Reset some state on track change (if title/artist/file changed compared to current cached ones)
			if (p_title, p_artist, p_audio_file) != (current_title, current_artist, current_file) and p_status != STATUS_STOPPED:
				# Log new track detected (cache basename lookup)
				if p_audio_file and path_exists(p_audio_file) and player_type in ("cmus", "mpd"):
					try:
						log_info(f"New track detected: {path_basename(p_audio_file)}")
					except (TypeError, AttributeError):
						log_info("New track detected: Unknown File")
				else:
					log_info(f"New track detected: {p_title or 'Unknown Track'}")

				# Update current_* cache and reset lyric state
				current_title = p_title or ""
				current_artist = p_artist or ""
				current_file = p_audio_file
				lyrics = []
				errors = []
				last_raw_pos = p_raw_pos
				last_idx = -1
				force_redraw = True
				is_txt = False
				is_a2 = False
				lyrics_loaded_time = None
				wrapped_lines = []
				max_wrapped_offset = 0
				end_triggered = False
				prev_lyrics_hash = None  # Reset lyrics hash

				# Cancel existing lyric fetching task (if present)
				if lyric_future and not lyric_future.done():
					lyric_future.cancel()
					try:
						await asyncio.wait_for(lyric_future, timeout=4.0)
					except (asyncio.CancelledError, asyncio.TimeoutError):
						log_debug("Previous lyric fetching task cancelled")
					finally:
						lyric_future = None

				# Determine search directory (avoid filesystem checks except here)
				search_directory = None
				if p_audio_file and path_exists(p_audio_file) and player_type in ('cmus', 'mpd'):
					search_directory = path_dirname(p_audio_file)

				# Start new lyric fetch if we have title+artist
				if current_title and current_artist:
					lyric_future = asyncio.create_task(
						fetch_lyrics_async(
							audio_file=p_audio_file,
							directory=search_directory,
							artist=current_artist or "",
							title=current_title or "",
							duration=p_duration
						)
					)
					log_debug(f"{p_audio_file}, {p_artist}, {p_title}, {p_duration}")

				# Sync last cmus position
				last_cmus_position = p_raw_pos
				estimated_position = p_raw_pos

		# Now use cached unpacked player values for the rest of the loop
		audio_file = p_audio_file
		raw_position = p_raw_pos
		artist = p_artist
		title = p_title
		duration_val = p_duration
		status = p_status

		# Handle completed lyric fetching (if any)
		if lyric_future and lyric_future.done():
			try:
				(new_lyrics, new_errors), new_is_txt, new_is_a2 = lyric_future.result()
				if new_errors:
					log_debug(new_errors)

				# Update local variables from fetch result
				lyrics = new_lyrics
				errors = new_errors
				is_txt = new_is_txt
				is_a2 = new_is_a2
				last_idx = -1
				force_redraw = True
				lyrics_loaded_time = current_time
				wrapped_lines = []
				max_wrapped_offset = 0
				prev_lyrics_hash = None  # Force cache update

				# Prepare timestamps if applicable
				if not (is_txt or is_a2):
					timestamps = sorted(t for t, _ in lyrics if t is not None)
					valid_indices = [i for i, (t, _) in enumerate(lyrics) if t is not None]
				else:
					timestamps = []
					valid_indices = []

				# Trigger timed refresh if playing
				if status == STATUS_PLAYING and player_type in ("cmus", "mpd"):
					resume_trigger_time = current_time
					log_debug("Refresh triggered by new lyrics loading")

				estimated_position = raw_position

			except (asyncio.CancelledError, Exception) as e:
				if isinstance(e, asyncio.CancelledError):
					log_debug("Lyric fetching cancelled")
				else:
					log_debug(f"Lyric load error: {e}")
				errors = [f"Lyric load error: {e}"]
				force_redraw = True
				lyrics_loaded_time = current_time
			finally:
				lyric_future = None

		# Delayed redraw after lyric load
		if lyrics_loaded_time and (current_time - lyrics_loaded_time >= 2.0):
			force_redraw = True
			lyrics_loaded_time = None

		# Track pause state
		playback_paused = (status == STATUS_PAUSED)

		# Update position tracking when external raw pos changes
		if (not playback_paused) and (raw_position != last_cmus_position):
			last_cmus_position = raw_position
			last_pos_time = current_time
			estimated_position = raw_position

		# Player-specific estimation (use cached values/locals)
		if player_type:
			if not playback_paused:
				elapsed = current_time - last_pos_time
				estimated_position = raw_position + elapsed
				if estimated_position > duration_val:
					estimated_position = duration_val
			else:
				estimated_position = raw_position
		else:
			playback_paused = (status == "pause")

		# Calculate continuous_position with cached offsets
		# offset_val = base_offset + sync_compensation + next_frame_time
		offset_val = base_offset + next_frame_time
		
		continuous_position = max_func(0.0, estimated_position + time_adjust + offset_val)
		if continuous_position > duration_val:
			continuous_position = duration_val

		# End-of-track trigger
		if (duration_val > 0.0 and (duration_val - continuous_position) <= END_TRIGGER_SEC and not end_triggered):
			end_triggered = True
			force_redraw = True
			log_debug(f"End-of-track reached (pos={continuous_position:.3f}s)")

		# Cancel proximity if paused
		if status != STATUS_PLAYING and proximity_active:
			proximity_active = False
			proximity_trigger_time = None
			
			# stdscr_timeout(refresh_interval_2)
			log_debug("Proximity reset due to pause")

		# Smart proximity logic (uses cached timestamps and thresholds)
		if (smart_proximity and timestamps and not is_txt and last_idx >= 0 and last_idx + 1 < len(timestamps)
			and status == STATUS_PLAYING and not poll and not playback_paused):

			idx = last_idx
			t0, t1 = timestamps[idx], timestamps[idx + 1]
			line_duration = t1 - t0
			percent_thresh = line_duration * (PROXIMITY_THRESHOLD_PERCENT / 100)
			abs_thresh = PROXIMITY_THRESHOLD_SEC
			raw_thresh = max_func(percent_thresh, abs_thresh)
			threshold = min_func(max_func(raw_thresh, PROXIMITY_MIN_THRESHOLD_SEC), min_func(PROXIMITY_MAX_THRESHOLD_SEC, line_duration))
			time_to_next = min_func(line_duration, max_func(0.0, t1 - continuous_position))

			if PROXIMITY_MIN_THRESHOLD_SEC <= time_to_next <= threshold:
				proximity_trigger_time = current_time
				proximity_active = True
				stdscr_timeout(refresh_proximity_interval_ms)
				last_player_update = 0.0
				log_debug(f"Proximity TRIG: time_to_next={time_to_next:.3f}s within [{PROXIMITY_MIN_THRESHOLD_SEC:.3f}, {threshold:.3f}]")
			elif (proximity_trigger_time is not None and (time_to_next < PROXIMITY_MIN_THRESHOLD_SEC or time_to_next > threshold
				  or current_time - proximity_trigger_time > threshold)):
				
				stdscr_timeout(refresh_interval_2)
				proximity_trigger_time = None
				proximity_active = False
				log_debug(f"Proximity RESET: time_to_next={time_to_next:.3f}s outside [{PROXIMITY_MIN_THRESHOLD_SEC:.3f}, {threshold:.3f}]")
			else:
				proximity_active = False
		else:
			proximity_active = False

		# Generate wrapped lines for TXT files only when needed
		if is_txt:
			# Re-wrap when we have no wrapped lines (or after lyric changes) or window_width changed
			if not wrapped_lines or prev_window_width != window_width:
				wrap_width = max_func(10, window_width - 2)
				wrapped = []
				for orig_idx, (_, lyric) in enumerate(lyrics):
					if lyric and lyric.strip():
						# Use cached wrap function for East Asian characters
						lines = wrap_func(lyric, wrap_width, subsequent_indent=' ')
						wrapped.extend([(orig_idx, line) for line in lines])
					else:
						wrapped.append((orig_idx, ""))
				wrapped_lines = wrapped
				lyrics_area_height = window_height - 3
				max_wrapped_offset = max_func(0, len(wrapped) - lyrics_area_height)
				prev_window_width = window_width

		# Calculate current lyric index using cached timestamps and strategy flags
		if smart_tracking == 1:
			current_idx = last_idx
			if timestamps and not is_txt:
				ts = timestamps
				n = len(ts)
				if current_idx < 0:
					current_idx = bisect_right(ts, continuous_position + offset_val) - 1
					current_idx = max_func(-1, min_func(current_idx, n - 1))
				elif current_idx + 1 < n:
					t_cur = ts[current_idx]
					t_next = ts[current_idx + 1]
					if continuous_position >= t_next - proximity_threshold:
						current_idx += 1
				current_idx = max_func(-1, min_func(current_idx, n - 1))
			elif is_txt and wrapped_lines and duration_val > 0.0:
				num_wrapped = len(wrapped_lines)
				target_idx = int_func((continuous_position / duration_val) * num_wrapped)
				current_idx = max_func(0, min_func(target_idx, num_wrapped - 1))
			else:
				current_idx = -1
			last_idx = current_idx
		else:
			if timestamps and not is_txt:
				ts = timestamps
				idx = bisect_right(ts, continuous_position + offset_val) - 1
				if idx >= 0:
					current_idx = idx
					continuous_position = ts[idx]
				else:
					current_idx = -1
			elif is_txt and wrapped_lines and duration_val > 0.0:
				num_wrapped = len(wrapped_lines)
				target_idx = int_func((continuous_position / duration_val) * num_wrapped)
				current_idx = max_func(0, min_func(target_idx, num_wrapped - 1))
			else:
				current_idx = -1

		# Auto scroll logic (uses cached window height/width)
		if last_input == 0 and not manual_scroll:
			if is_txt and wrapped_lines:
				lyrics_area_height = window_height - 3
				ideal_offset = current_idx - (lyrics_area_height // 2)
				target_offset = max_func(0, min_func(ideal_offset, max_wrapped_offset))
				if target_offset != manual_offset:
					manual_offset = target_offset
					needs_redraw = True
				if current_idx != last_idx:
					if target_offset != manual_offset:
						manual_offset = target_offset
						needs_redraw = True
			elif not is_txt and wrapped_lines:
				ideal_offset = current_idx - ((window_height - 3) // 2)
				target_offset = max_func(0, min_func(ideal_offset, max_wrapped_offset))
				if target_offset != manual_offset:
					manual_offset = target_offset
					needs_redraw = True
				if current_idx != last_idx:
					if target_offset != manual_offset:
						manual_offset = target_offset
						needs_redraw = True

		# Handle user input (key was read earlier)
		if new_input:
			# Quit key
			if key in quit_keys:
				try:
					atexit.register(executor.shutdown)
				except NameError:
					# executor may not exist in some contexts; ignore if so
					pass
				sys.exit("Exiting")

			# store originals
			old_manual_offset = manual_offset
			old_time_adjust = time_adjust
			old_alignment = alignment

			# Process navigation/time keys (sets for O(1))
			needs_redraw_input = False

			if key in scroll_up_keys:
				manual_offset = max_func(0, manual_offset - 1)
				last_input = current_time
				needs_redraw_input = True
			elif key in scroll_down_keys:
				manual_offset += 1
				last_input = current_time
				needs_redraw_input = True
			elif key in time_decrease_keys:
				time_adjust -= 0.1
				needs_redraw_input = True
			elif key in time_increase_keys:
				time_adjust += 0.1
				needs_redraw_input = True
			elif key in time_reset_keys:
				time_adjust = 0.0
				needs_redraw_input = True
			elif key in time_jump_increase_keys:
				time_adjust += 5.0
				needs_redraw_input = True
			elif key in time_jump_decrease_keys:
				time_adjust -= 5.0
				needs_redraw_input = True
			elif key in align_left_keys:
				alignment = "left"
				needs_redraw_input = True
			elif key in align_center_keys:
				alignment = "center"
				needs_redraw_input = True
			elif key in align_right_keys:
				alignment = "right"
				needs_redraw_input = True
			elif key in align_cycle_forward_keys:
				current_idx_align = alignment_index[alignment]
				alignment = alignments_list[(current_idx_align + 1) % 3]
				needs_redraw_input = True
			elif key in align_cycle_backward_keys:
				current_idx_align = alignment_index[alignment]
				alignment = alignments_list[(current_idx_align - 1) % 3]
				needs_redraw_input = True
			# KEY_RESIZE handled above

			if needs_redraw_input:
				force_redraw = True

		# VRR frame timing (cached frame_time)
		skip_redraw_for_vrr = False
		if VRR_ENABLED:
			if current_time < next_frame_time:
				skip_redraw_for_vrr = True
			else:
				skip_redraw_for_vrr = False
				next_frame_time += frame_time
				while next_frame_time < current_time:
					next_frame_time += frame_time
			if current_idx != last_idx or force_redraw:
				skip_redraw_for_vrr = False

		# Determine if should skip redraw (early exit optimization)
		skip_conditions = (
			not new_input and
			not needs_redraw and
			not force_redraw and
			current_idx == last_idx and
			status == STATUS_PAUSED and
			not manual_scroll and
			not proximity_active and
			skip_redraw_for_vrr
		)

		# Update display if needed
		if new_input or needs_redraw or force_redraw or (current_idx != last_idx):
			log_debug(
				f"Redraw triggered: new_input={new_input}, needs_redraw={needs_redraw}, "
				f"force_redraw={force_redraw}, idx={last_idx} → {current_idx}, paused={status == STATUS_PAUSED}"
			)
			stdscr_timeout(0)
			
			display_lyrics_data = wrapped_lines if is_txt else lyrics

			start_screen_line = update_display(
				stdscr,
				display_lyrics_data,
				errors,
				continuous_position,
				current_title,
				manual_offset,
				is_txt,
				is_a2,
				current_idx,
				manual_scroll,
				time_adjust,
				lyric_future is not None and not lyric_future.done(),
				alignment=alignment,
				player_info=(player_type, player_data),
			)

			# sync compensation based on how long drawing took
			# sync_compensation = (perf() - draw_start) * 0.965
			
			# sync_compensation = 0.0
			
			# time_delta = current_time - last_pos_time - sync_compensation
			time_delta = current_time - last_pos_time
			
			# log_debug(
			# 	f"Triggered at: {continuous_position}, Compensated: {sync_compensation}, Time_delta: {time_delta}"
			# )
			log_debug(
				f"Triggered at: {continuous_position}, Time_delta: {time_delta}"
			)
			# Update state
			manual_offset = start_screen_line
			last_idx = current_idx
			force_redraw = False

		# CPU throttling: set sleep_time conservatively - but only when NOT in manual scroll
		if status == STATUS_PAUSED and not manual_scroll:
			# Keep coarse timeout values for curses
			if time_since_input > 5.0:
				stdscr_timeout(400)
				sleep_time = 0.002
			elif time_since_input > 2.0:
				stdscr_timeout(300)
				sleep_time = 0.002
			else:
				stdscr_timeout(250)
				sleep_time = 0.002
		else:
			stdscr_timeout(refresh_interval_2)
			sleep_time = 0.0

		# High-frequency conditions (override)
		if poll or proximity_active or manual_scroll:
			sleep_time = 0.0
		else:
			stdscr_timeout(refresh_interval_2)

		await asyncio.sleep(sleep_time)


	# except Exception as e:
		# log_debug(f"Main loop error: {str(e)}")
		# await asyncio.sleep(0)
		# log_debug("System fault")

def main(stdscr, config_path=None, use_default=False, player=None): #Hacks
	"""Main function that runs the async event loop"""
	CONFIG_MANAGER = ConfigManager(
		config_path=config_path, 
		use_default=use_default, 
		player_override=player
	)
	
	CONFIG = CONFIG_MANAGER.config
	LOGGER = Logger(CONFIG_MANAGER)
	
	asyncio.run(main_async(stdscr, CONFIG, LOGGER))

def run_main(stdscr):
	main(
		stdscr, 
		config_path=args.config, 
		use_default=args.default,
		player=args.player
	)

if __name__ == "__main__":
	args = parse_args()

	# while True:
	try:
		curses.wrapper(run_main)
	except KeyboardInterrupt:
		print("Exited by user (Ctrl+C).")
		try:
			atexit.register(executor.shutdown)
		except NameError:
			# executor may not exist in some contexts; ignore if so
			pass
		exit()
	except Exception as e:
		try:
			temp_config = ConfigManager(config_path=args.config, use_default=args.default)
			temp_logger = Logger(temp_config)
			temp_logger.log_debug(f"Fatal error: {str(e)}")
		except Exception:
			print(f"Fatal error (and failed to create temp logger): {e}", file=sys.stderr)
		time.sleep(1)
