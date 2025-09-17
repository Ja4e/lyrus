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
import os
import sys
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
import json
import appdirs
import pathlib
import unicodedata
from wcwidth import wcswidth
import os, json, sys
import argparse
import atexit

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
VERSION = "1.0.0"

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
				"Format_priority": ['a2', 'lrc' ,"txt"]
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
		self.PROVIDERS = set(self.config["lyrics"]["Sources"])
		
		self.PROVIDER_FALLBACK = self.config["lyrics"]["Fallback"]
		self.PROVIDER_FORMAT_PRIORITY = set(self.config["lyrics"]["Format_priority"])
		
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
	
	def __init__(self):
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
				with open(debug_log, "a", encoding="utf-8") as f:
					f.write(debug_entry)
				self.clean_debug_log()

			# Write to main log if message level >= configured level
			if message_level >= configured_level:
				main_entry = f"{timestamp} | {level.upper()} | {message}\n"
				with open(main_log, "a", encoding="utf-8") as f:
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
LOGGER = Logger()

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
		
		# Hide status after 2 seconds for terminal states
		if step in CONFIG_MANAGER.TERMINAL_STATES and fetch_status['done_time']:
			if time.time() - fetch_status['done_time'] > 2:
				return ""

		if step == 'clear':
			return ""

		# Return pre-defined message with elapsed time if applicable
		base_msg = CONFIG_MANAGER.MESSAGES.get(step, step)
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
def sanitize_filename(name):
	"""Make strings safe for filenames"""
	return re.sub(r'[<>:"/\\|?*]', '_', name)

def sanitize_string(s):
	"""Normalize strings for comparison"""
	return re.sub(r'[^a-zA-Z0-9]', '', str(s)).lower()

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
	try:
		# Always allow files with timestamps
		if re.search(r'\[\d+:\d+\.\d+\]', content):
			return True
			
		# Allow empty content for instrumental markers
		if not content.strip():
			return True
			
		# Normalize comparison parameters
		norm_content = sanitize_string(content)
		
		return True  # Temporary accept all content
	except Exception as e:
		LOGGER.log_error(f"Error in validate_lyrics: {str(e)}")
		return True  # Fallback to accepting content

executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)

async def fetch_lyrics_syncedlyrics_async(artist_name, track_name, duration=None, timeout=15):
	"""Async version of syncedlyrics fetch"""
	LOGGER.log_debug(f"Starting syncedlyrics search: {artist_name} - {track_name} ({duration}s)")
	try:
		import syncedlyrics
		
		LOGGER.Log_debug(f"Loaded providers: {CONFIG_MANAGER.PROVIDER}")
		
		def worker(search_term, synced=True):
			"""Worker for lyric search"""
			try: 
				result = syncedlyrics.search(search_term) if synced else syncedlyrics.search(search_term, plain_only=True, providers=[CONFIG_MANAGER.PROVIDERS])
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
		with open(log_path, 'r', encoding='utf-8') as f:
			for line in f:
				if artist_name and track_name:
					if f"Artist: {artist_name}" in line and f"Title: {track_name}" in line:
						return True
		return False
	except Exception as e:
		LOGGER.log_debug(f"Timeout check error: {e}")
		return False


Allow_syncedlyric = CONFIG_MANAGER.ALLOW_SYNCEDLYRIC
Fallback_lrc = CONFIG_MANAGER.PROVIDER_FALLBACK 

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
						with open(file_path, 'r', encoding='utf-8') as f:
							content = f.read()
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
						with open(file_path, 'r', encoding='utf-8') as f:
							content = f.read()
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

		if Synced_lyrics:
			tasks.append(fetch_lyrics_syncedlyrics_async(artist_name, track_name, duration))
		elif not Fallback_lrc:
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

			# Validation warning
			if not validate_lyrics(fetched_lyrics, artist_name, track_name):
				LOGGER.log_debug("Validation warning - possible mismatch")
				fetched_lyrics = "[Validation Warning] Potential mismatch\n" + fetched_lyrics

			# Detect format
			is_enhanced = any(re.search(r'<\d+:\d+\.\d+>', line) for line in fetched_lyrics.split('\n'))
			has_lrc_timestamps = re.search(r'\[\d+:\d+\.\d+\]', fetched_lyrics) is not None

			if is_enhanced:
				extension = 'a2'
			elif is_synced and has_lrc_timestamps:
				extension = 'lrc'
			else:
				extension = 'txt'

			candidates.append((extension, fetched_lyrics))

			# Log stats
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

		# --- Choose best candidate by priority ---
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
			line_pattern = re.compile(r'^\[(\d{2}:\d{2}\.\d{2})\](.*)')
			word_pattern = re.compile(r'<(\d{2}:\d{2}\.\d{2})>(.*?)<(\d{2}:\d{2}\.\d{2})>')

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
			for line in lines:
				raw_line = line.rstrip('\n')
				line_match = re.match(r'\[(\d+:\d+\.\d+)\](.*)', raw_line)
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
def get_cmus_info():
	"""Get current playback info from cmus"""
	try:
		output = subprocess.run(['cmus-remote', '-Q'], 
							   capture_output=True, 
							   text=True, 
							   check=True).stdout.splitlines()
		LOGGER.log_debug("Cmus-remote polling...")
	except subprocess.CalledProcessError:
		#LOGGER.log_debug("Error occurred. Aborting...")
		return (None, 0, None, None, 0, "stopped")

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

def get_mpd_info():
	"""Get current playback info from MPD, handling password authentication."""
	client = MPDClient()
	client.timeout = CONFIG_MANAGER.MPD_TIMEOUT
	
	try:
		client.connect(CONFIG_MANAGER.MPD_HOST, CONFIG_MANAGER.MPD_PORT)
		LOGGER.log_debug("mpd polling...")
		
		# Authenticate if a password is set
		if CONFIG_MANAGER.MPD_PASSWORD:
			client.password(CONFIG_MANAGER.MPD_PASSWORD)
		
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
		#LOGGER.log_debug(f"MPD connection error: {str(e)}")
		pass
	except Exception as e:
		LOGGER.log_debug(f"Unexpected MPD error: {str(e)}")

	update_fetch_status("mpd")
	return (None, 0.0, None, None, 0.0, "stopped")

def get_playerctl_info():
	"""Get current playback info from any player via playerctl."""
	try:
		# Run playerctl with exact format
		result = subprocess.run(
			[
				"playerctl", "metadata",
				"--format",
				'"{{playerName}}","{{artist}}","{{title}}","{{position}}","{{status}}","{{mpris:length}}"'
			],
			capture_output=True, text=True, timeout=1
		)

		output = result.stdout.strip()
		
		LOGGER.log_debug("playerctl polling...")
		
		# Handle no players found
		if "No players found" in output or not output:
			return (None, 0.0, None, None, 0.0, "stopped")

		# Remove surrounding quotes and split by "," safely
		if output.startswith('"') and output.endswith('"'):
			output = output[1:-1]

		fields = output.split('","')
		if len(fields) != 6:
			# Unexpected output, fallback
			return (None, 0.0, None, None, 0.0, "stopped")

		player_name, artist, title, position, status, duration = fields

		# Convert microseconds → seconds with decimals preserved
		try:
			position_sec = float(position) / 1_000_000 if position else 0.0
		except ValueError:
			position_sec = 0.0

		try:
			duration_sec = float(position) / 1_000_000 if duration else 0.0
		except ValueError:
			duration_sec = 0.0

		# Normalize status
		status = status.lower() if status else "stopped"

		# Sanity check: clamp weird jumps
		if position_sec < 0 or (duration_sec > 0 and position_sec > duration_sec * 1.5):
			position_sec = duration_sec if status == "paused" else 0.0

		# playerctl cannot provide file path
		audio_file = None

		return (audio_file, position_sec, artist, title, duration_sec, status)

	except subprocess.TimeoutExpired:
		return (None, 0.0, None, None, 0.0, "stopped")
	except Exception:
		return (None, 0.0, None, None, 0.0, "stopped")

def get_player_info():
	"""Detect active player (CMUS or MPD)"""
	# Try CMUS first if prioritized
	if CONFIG_MANAGER.ENABLE_CMUS:
		try:
			cmus_info = get_cmus_info()
			if cmus_info[0] is not None:
				return 'cmus', cmus_info
		except Exception as e:
			LOGGER.log_debug(f"CMUS detection failed: {str(e)}")
	
	# Try MPD
	if CONFIG_MANAGER.ENABLE_MPD:	
		try:
			mpd_info = get_mpd_info()
			if mpd_info[0] is not None:
				return 'mpd', mpd_info
		except Exception as e:
			LOGGER.log_debug(f"MPD detection failed: {str(e)}")
	
	if CONFIG_MANAGER.ENABLE_PLAYERCTL:
		try:
			# Fallback to playerctl
			playerctl_info = get_playerctl_info()
			if playerctl_info[3] is not None:
				return "playerctl", playerctl_info
		except Exception as e:
			LOGGER.log_debug(f"Mpris detection failed: {str(e)}")

	update_fetch_status("no_player")
	LOGGER.log_debug("No active music player detected")
	
	return None, (None, 0, None, None, 0, "stopped")

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
	"""Render lyrics in curses interface"""
	
	height, width = stdscr.getmaxyx()
	status_msg = get_current_status()

	# Layout constants
	STATUS_LINES = 2
	MAIN_STATUS_LINE = height - 1
	TIME_ADJUST_LINE = height - 2
	LYRICS_AREA_HEIGHT = height - STATUS_LINES - 1
	
	if LYRICS_AREA_HEIGHT <= 0:
		stdscr.noutrefresh()
		return 0
	
	# Handle window resizing or first call
	if not hasattr(display_lyrics, '_dims') or display_lyrics._dims != (height, width):
		curses.resizeterm(height, width)
		
		display_lyrics.error_win  = curses.newwin(1, width, 0, 0)
		display_lyrics.lyrics_win = curses.newwin(LYRICS_AREA_HEIGHT, width, 1, 0)
		display_lyrics.adjust_win = curses.newwin(1, width, TIME_ADJUST_LINE, 0)
		display_lyrics.status_win = curses.newwin(1, width, MAIN_STATUS_LINE, 0)

		display_lyrics._dims = (height, width)
		
		display_lyrics._wrapped_cache = None
		display_lyrics._wrap_width = None
		display_lyrics._last_lyrics = None

	# Invalidate cache if lyrics changed
	if not hasattr(display_lyrics, "_last_lyrics") or display_lyrics._last_lyrics != lyrics:
		display_lyrics._wrapped_cache = None
		display_lyrics._last_lyrics = lyrics

	error_win = display_lyrics.error_win
	lyrics_win = display_lyrics.lyrics_win
	adjust_win = display_lyrics.adjust_win
	status_win = display_lyrics.status_win

	if use_manual_offset and manual_offset != 0 and position is not None:
		try:
			position += int(manual_offset * 1_000_000)
		except Exception:
			pass
	
	# --- 1) Render errors ---
	error_win.erase()
	if errors:
		try:
			err_str = f"Errors: {len(errors)}"[:width - 1]
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
		wcs_cache = {}
		for idx in range(start_line, min(start_line + visible, len(a2_lines))):
			if y >= visible:
				break
			line = a2_lines[idx]
			line_str = " ".join(text for _, (text, _) in line)  # alignment uses full line width

			# Compute x for alignment
			if alignment == 'right':
				x = max(0, width - wcswidth(line_str) - 1)
			elif alignment == 'center':
				x = max(0, (width - wcswidth(line_str)) // 2)
			else:
				x = 1

			cursor = 0
			for _, (text, _) in line:
				if text not in wcs_cache:
					wcs_cache[text] = wcswidth(text)
				txt_width = wcs_cache[text]

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
		# LRC/TXT format
		wrap_w = max(10, width - 2)
		if display_lyrics._wrapped_cache is None or display_lyrics._wrap_width != wrap_w:
			wrapped = []
			for orig_i, (_, ly) in enumerate(lyrics):
				if ly.strip():
					lines = textwrap.wrap(ly, wrap_w, drop_whitespace=False)
					wrapped.append((orig_i, lines[0]))
					for cont in lines[1:]:
						wrapped.append((orig_i, ' ' + cont))
				else:
					wrapped.append((orig_i, ''))
			display_lyrics._wrapped_cache = wrapped
			display_lyrics._wrap_width = wrap_w
		wrapped = display_lyrics._wrapped_cache

		total = len(wrapped)
		avail = LYRICS_AREA_HEIGHT
		max_start = max(0, total - avail)

		if use_manual_offset:
			start_screen_line = min(max(manual_offset, 0), max_start)
		else:
			if current_idx >= len(lyrics) - 1:
				start_screen_line = max_start
			else:
				idxs = [i for i, (o, _) in enumerate(wrapped) if o == current_idx]
				if idxs:
					center = (idxs[0] + idxs[-1]) // 2
					ideal = center - avail // 2
					start_screen_line = min(max(ideal, 0), max_start)
				else:
					start_screen_line = min(max(current_idx, 0), max_start)

		y = 0
		wcs_cache = {}
		for _, line in wrapped[start_screen_line:start_screen_line + avail]:
			if y >= avail:
				break
			txt = line.strip()[:width - 1]

			if txt not in wcs_cache:
				wcs_cache[txt] = wcswidth(txt)
			disp_width = wcs_cache[txt]

			if alignment == 'right':
				x = max(0, width - disp_width - 1)
			elif alignment == 'center':
				x = max(0, (width - disp_width) // 2)
			else:
				x = 1

			if is_txt_format:
				color = curses.color_pair(4) if wrapped[start_screen_line + y][0] == current_idx else curses.color_pair(5)
			else:
				color = curses.color_pair(2) if wrapped[start_screen_line + y][0] == current_idx else curses.color_pair(3)
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
			adjust_win.addstr(0, max(0, width - len(adj_str) - 1),
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
		cur_line = min(current_idx + 1, len(lyrics)) if lyrics else 0
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
				trunc_len = max(0, left_max - 3)
				ps_trunc = ps_trunc[:trunc_len] + '...' if trunc_len > 0 else ''
			padding = ' ' * max(left_max - len(ps_trunc), 0)
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
					trunc_len = max(0, left_max - 3)
					ps_trunc = ps_trunc[:trunc_len] + '...' if trunc_len > 0 else ''
				padding = ' ' * max(left_max - len(ps_trunc), 0)
				display_line = f"{ps_trunc}{padding} {right_text} "

		try:
			safe_width = max(0, width - 1)
			status_win.addstr(0, 0, display_line[:safe_width], curses.color_pair(5) | curses.A_BOLD)
		except curses.error:
			pass
	else:
		info = f"Line {min(current_idx + 1, len(lyrics))}/{len(lyrics)}"
		if time_adjust:
			info += '[Adj]'
		try:
			status_win.addstr(0, 0, info[:width - 1], curses.A_BOLD)
		except curses.error:
			pass
	status_win.noutrefresh()


	# Overlay centered status message
	if status_msg:
		msg = f"  [{status_msg}]  "[:width - 1]
		try:
			status_win.addstr(0, max(0, (width - len(msg)) // 2),
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
		alignments = ["left", "center", "right"]
		new_alignment = alignments[(alignments.index(current_alignment) + 1) % 3]
		alignment_input, input_processed = True, True

	elif key in key_bindings["align_cycle_backward"]:
		alignments = ["left", "center", "right"]
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
async def main_async(stdscr, config_path=None):
	# Suppress output during initialization
	sys.stdout = open(os.devnull, 'w')
	sys.stderr = open(os.devnull, 'w')
	# Initialize colors and UI
	LOGGER.log_info("Initializing colors and UI")

	curses.start_color()
	use_256 = curses.COLORS >= 256
	color_config = CONFIG["ui"]["colors"]

	# Resolve color configurations
	error_color     = resolve_color(color_config["error"])
	txt_active      = resolve_color(color_config["txt"]["active"])
	txt_inactive    = resolve_color(color_config["txt"]["inactive"])
	lrc_active      = resolve_color(color_config["lrc"]["active"])
	lrc_inactive    = resolve_color(color_config["lrc"]["inactive"])

	# Load intervals and thresholds
	refresh_interval_ms             = CONFIG["ui"]["sync"]["refresh_interval_ms"]
	refresh_interval                = refresh_interval_ms / 1000.0
	refresh_interval_2              = CONFIG["ui"]["sync"]["coolcpu_ms"]
	
	smart_refresh_interval          = CONFIG["ui"]["sync"]["smart_coolcpu_ms"]
	
	smart_refresh_interval_v2       = CONFIG["ui"]["sync"]["proximity"]["smart_coolcpu_ms_v2"]
	refresh_proximity_interval      = CONFIG["ui"]["sync"]["proximity"]["smart_coolcpu_ms_v2"]
	
	JUMP_THRESHOLD                  = CONFIG["ui"]["sync"].get("jump_threshold_sec", 1.0)
	refresh_proximity_interval_ms   = CONFIG["ui"]["sync"].get("refresh_proximity_interval_ms", 200)
	TEMPORARY_REFRESH_SEC           = CONFIG["ui"]["sync"]["smart_refresh_duration"]

	smart_tracking_bol              = CONFIG["ui"]["sync"].get("smart-tracking")

	proximity_threshold             = CONFIG["ui"]["sync"]["proximity_threshold"]
	smart_proximity_bol             = CONFIG["ui"]["sync"]["proximity"].get("smart-proximity", False)
	PROXIMITY_THRESHOLD_SEC         = CONFIG["ui"]["sync"]["proximity"].get("proximity_threshold_sec", 0.05)
	PROXIMITY_THRESHOLD_PERCENT     = CONFIG["ui"]["sync"]["proximity"].get("proximity_threshold_percent", 0.05)
	PROXIMITY_MIN_THRESHOLD_SEC     = CONFIG["ui"]["sync"]["proximity"].get("proximity_min_threshold_sec", 1.0)
	PROXIMITY_MAX_THRESHOLD_SEC     = CONFIG["ui"]["sync"]["proximity"].get("proximity_max_threshold_sec", 2.0)

	END_TRIGGER_SEC                 = CONFIG["ui"]["sync"].get("end_trigger_threshold_sec", 1.0)
	SCROLL_TIMEOUT                  = CONFIG["ui"]["scroll_timeout"]
	WRAP_WIDTH_PERCENT              = CONFIG["ui"]["sync"]["wrap_width_percent"]
	base_offset                     = CONFIG["ui"]["sync"].get("sync_offset_sec", 0.0)
	bisect_offset                   = CONFIG["ui"]["sync"]["bisect_offset"]

	sync_compensation = 0.0
	
	VRR_ENABLED                     = CONFIG["ui"]["sync"].get("VRR_bol", False)
	
	if CONFIG["ui"]["sync"].get("VRR_R_bol", False):
		refresh_rate = get_monitor_refresh_rate()
		frame_time_sec = 1.0 / refresh_rate

		# Normal polling every N frames
		NORMAL_POLL_FRAMES = CONFIG["ui"]["sync"]["VRR_R"].get("Norm_poll_F", 30)
		PROX_POLL_FRAMES   = CONFIG["ui"]["sync"]["VRR_R"].get("Proxi_poll_F", 10)

		# Apply frame-based interval but cap at config limit
		refresh_interval = min(frame_time_sec * NORMAL_POLL_FRAMES, refresh_interval)
		refresh_proximity_interval = min(frame_time_sec * PROX_POLL_FRAMES, refresh_proximity_interval)

	next_frame_time = 0
	
	if VRR_ENABLED:
		refresh_rate = get_monitor_refresh_rate()
		frame_time = 1.0 / refresh_rate  # Target time per frame (seconds)
		next_frame_time = time.perf_counter() + frame_time

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
		'current_title': None,
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
		'smart_tracking': smart_tracking_bol,
		'smart_proximity': smart_proximity_bol,
		'proximity_trigger_time': None,
		'proximity_active': False,
		"poll": False,
		'lyric_future': None,  # For async lyric fetching
		'lyrics_loaded_time': None,
	}

	last_cmus_position = 0.0
	estimated_position = 0.0
	playback_paused = False

	skip_redraw_for_vrr = False
	# Unpack initial player info
	player_type, (audio_file, raw_pos, artist, title, duration, status) = state["player_info"]
	
	if audio_file in ("None", ""):
		audio_file = None

	# current_idx = -1

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
			if ((state['player_info'][0] == 'cmus' or 'playerctl') and
				state.get('resume_trigger_time') and
				(current_time - state['resume_trigger_time'] <= TEMPORARY_REFRESH_SEC) and
				state['player_info'][1][5] == "playing" and
				state['lyrics']):
				stdscr.timeout(smart_refresh_interval)
				state["poll"] = True
			else:
				stdscr.timeout(refresh_interval_2)
				state["poll"] = False

			# Determine fetch interval with proximity overlay
			if state['proximity_active'] and status == "playing":
				# Keep normal polling but also trigger proximity updates
				interval = refresh_interval
			else:
				if (state.get('resume_trigger_time') and
					(current_time - state['resume_trigger_time'] <= TEMPORARY_REFRESH_SEC)):
					interval = 0.0
				else:
					interval = refresh_interval

			if (current_time - state['last_player_update'] >= interval):
				try:
					prev_status = state['player_info'][1][5]
					p_type, p_data = get_player_info()
					state['player_info'] = (p_type, p_data)

					_, raw_val, _, _, _, status_val = p_data
					new_raw = float(raw_val or 0.0)
					drift = abs(new_raw - estimated_position)
					if drift > JUMP_THRESHOLD and status_val == "playing" and p_type != "playerctl":
						state['resume_trigger_time'] = time.perf_counter()
						LOGGER.log_debug(f"Jump detected: {drift:.3f}s")
						needs_redraw = True

					# (p_type == "cmus" or p_type == "playerctl")
					if (p_type and prev_status == "paused" and status_val == "playing"):
						state['resume_trigger_time'] = time.perf_counter()
						LOGGER.log_debug("Pause→play refresh")
						needs_redraw = True

					# optionally update player_status_changed here
					if p_data[5] != prev_status:
						state['player_status_changed'] = True

					# safe update for current_idx
					if state.get('current_idx', -1) != -1:
						state['last_known_idx'] = state['current_idx']

				except Exception as e:
					LOGGER.log_debug(f"Error refreshing player info: {e}")
				finally:
					state['last_player_update'] = current_time

			# Unpack the (possibly cached) player info
			player_type, (audio_file, raw_pos, artist, title, duration, status) = state["player_info"]
			if audio_file in ("None", ""):
				audio_file = None
			
			raw_position = float(raw_pos or 0.0)
			duration = float(duration or 0.0)
			estimated_position = raw_position
			now = time.perf_counter()

			# Handle track changes
			if title and title.strip() != "" and title != state['current_title']:
				if audio_file and audio_file != "None":
					try:
						LOGGER.log_info(f"New track detected: {os.path.basename(audio_file)}")
					except (TypeError, AttributeError):
						LOGGER.log_info(f"New track detected: Unknown File")
				else:
					LOGGER.log_info(f"New track detected: {title or 'Unknown Track'}")
				state.update({
					'current_title': title or "",
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
					
				# Cancel any existing lyric fetching task
				if state['lyric_future'] and not state['lyric_future'].done():
					state['lyric_future'].cancel()
					try:
						# Await cancellation to fully stop the old task
						await asyncio.wait_for(state['lyric_future'], timeout=4.0)
					except asyncio.CancelledError:
						LOGGER.log_debug("Previous lyric fetching task cancelled successfully")
					except asyncio.TimeoutError:
						LOGGER.log_debug("Previous lyric fetch task timeout, forcefully stopped")
					finally:
						state['lyric_future'] = None
				
				search_directory = None
				
				if audio_file and os.path.exists(audio_file):
					search_directory = os.path.dirname(audio_file) if (player_type == 'cmus'or player_type == 'mpd') else None
				
				# Start async lyric fetching
				state['lyric_future'] = asyncio.create_task(
					fetch_lyrics_async(
						audio_file=audio_file,
						directory=search_directory,
						artist=artist or "",
						title=title or "",
						duration=duration
					)
				)
				LOGGER.log_debug(f"{audio_file}, {artist}, {title}, {duration}")
				
				last_cmus_position = raw_position
				estimated_position = raw_position

			# Handle loaded lyrics from async task
			if state['lyric_future'] and state['lyric_future'].done():
				try:
					(new_lyrics, errors), is_txt, is_a2 = state['lyric_future'].result()
					if errors:  # This checks if the list is non-empty
						LOGGER.log_debug(errors)
					state.update({
						'lyrics': new_lyrics,
						'errors': errors, # [] to prevent it
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
					if status == "playing" and (player_type == "cmus" or player_type == "mpd"):
						state['resume_trigger_time'] = time.perf_counter()
						LOGGER.log_debug("Refresh triggered by new lyrics loading")
					estimated_position = raw_position
					state['lyric_future'] = None
				except asyncio.CancelledError:
					LOGGER.log_debug("Lyric fetching cancelled")
					state['lyric_future'] = None
				except Exception as e:
					state.update({
						'errors': [f"Lyric load error: {e}"],
						'force_redraw': True,
						'lyrics_loaded_time': time.perf_counter()
					})
					state['lyric_future'] = None

			# Delayed redraw after lyric load
			if (state['lyrics_loaded_time'] and
				time.perf_counter() - state['lyrics_loaded_time'] >= 2.0):
				state['force_redraw'] = True
				state['lyrics_loaded_time'] = None

			# Track pause state first
			playback_paused = (status == "paused")

			# Update last position tracking only if playing
			if not playback_paused and raw_position != last_cmus_position:
				last_cmus_position = raw_position
				state['last_pos_time'] = now
				estimated_position = raw_position

				
			# Player-specific estimation
			# if player_type == "cmus" or player_type == "mpd" or player_type == "playerctl":
			if player_type:
				if not playback_paused:
					elapsed = now - state['last_pos_time']
					estimated_position = raw_position + elapsed
					estimated_position = min(estimated_position, duration)
				else:
					# do NOT update last_pos_time here!
					estimated_position = raw_position

			else:
				playback_paused = (status == "pause")

			# if player_type == "mpd" or player_type == "playerctl":
				# sync_compensation = 0.0

			offset = base_offset + sync_compensation + next_frame_time
			
			continuous_position = max(0.0, estimated_position + state['time_adjust'] + offset)
			
			continuous_position = min(continuous_position, duration)
			
			# End‑of‑track proximity trigger 
			# only run once per track
			if duration > 0 \
			   and (duration - continuous_position) <= END_TRIGGER_SEC \
			   and not state.get("end_triggered", False):

				state["end_triggered"] = True
				state["force_redraw"] = True
				LOGGER.log_debug(f"End‑of‑track reached (pos={continuous_position:.3f}s), triggered final redraw")

			# Cancel proximity if playback paused just incase 
			if status != "playing" and state['proximity_active']:
				state['proximity_active'] = False
				state['proximity_trigger_time'] = None
				stdscr.timeout(refresh_interval_2)
				LOGGER.log_debug("Proximity forcibly reset due to pause")

			if (state['smart_proximity']
				and state['timestamps'] and not state['is_txt']
				and state['last_idx'] >= 0
				and state['last_idx'] + 1 < len(state['timestamps'])
				and state['last_idx'] == max(state['last_idx'], 0)
				and status == "playing"
				and not state["poll"]
				and not playback_paused
				and not manual_scroll):

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
					LOGGER.log_debug(
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
					LOGGER.log_debug(
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

			# Calculate current lyric index
			if state['smart_tracking'] == 1:
				current_idx = state.get('last_idx', -1)

				if state['timestamps'] and not state['is_txt']:
					ts = state['timestamps']
					n  = len(ts)

					# If no index yet, fall back to bisect once
					if current_idx < 0:
						current_idx = bisect.bisect_right(ts, continuous_position + offset) - 1
						current_idx = max(-1, min(current_idx, n - 1))

					# Otherwise, only step forward when needed
					elif current_idx + 1 < n:
						t_cur = ts[current_idx]
						t_next = ts[current_idx + 1]

						if continuous_position >= t_next - proximity_threshold:
							# Jump to next line slightly early
							current_idx += 1

					# Clamp index
					current_idx = max(-1, min(current_idx, n - 1))
					last_position_time = now

				elif state['is_txt'] and state['wrapped_lines'] and duration > 0:
					num_wrapped = len(state['wrapped_lines'])
					target_idx  = int((continuous_position / duration) * num_wrapped)
					current_idx = max(0, min(target_idx, num_wrapped - 1))
					last_position_time = now

				else:
					current_idx = -1
					last_position_time = now

				# Save progress
				state['last_idx'] = current_idx

			else:
				if state['timestamps'] and not state['is_txt']:
					ts     = state['timestamps']
					idx    = bisect.bisect_right(ts, continuous_position + offset) - 1

					if idx >= 0:
						current_idx         = idx
						continuous_position = ts[idx]
						if status == "paused" and not manual_scroll and not state['current_title']:
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
					
		
			# Determine if we can draw this frame
			if VRR_ENABLED:
				if current_time < next_frame_time:
					# Not yet time for next frame, skip redraw but continue processing
					skip_redraw_for_vrr = True
				else:
					skip_redraw_for_vrr = False
					next_frame_time += frame_time
					# catch up if behind
					while next_frame_time < current_time:
						next_frame_time += frame_time
				if current_idx != state['last_idx'] or state['force_redraw']:
					skip_redraw = False  # always draw if something changed
			else:
				skip_redraw_for_vrr = False

			# Update display if needed
			skip_redraw = (
				status == "paused" and
				not manual_scroll and
				not state['force_redraw'] and
				current_idx == state['last_idx'] and
				not state['proximity_active'] and
				skip_redraw_for_vrr
			)
			
			if not skip_redraw:
				if new_input or needs_redraw or state['force_redraw'] or (current_idx != state['last_idx']):
					LOGGER.log_debug(
						f"Redraw triggered: new_input={new_input}, "
						f"needs_redraw={needs_redraw}, force_redraw={state['force_redraw']}, "
						f"idx={state['last_idx']} → {current_idx}, paused={status == 'paused'}"
					)

					start_screen_line = update_display(
						stdscr,
						state['wrapped_lines'] if state['is_txt'] else state['lyrics'],
						state['errors'],
						continuous_position,
						state['current_title'],
						state['manual_offset'],
						state['is_txt'],
						state['is_a2'],
						current_idx,
						manual_scroll,
						state['time_adjust'],
						state['lyric_future'] is not None and not state['lyric_future'].done(),
						alignment=state['alignment'],
						player_info=state["player_info"],
					)
					
					draw_end = time.perf_counter()
					sync_compensation = draw_end - draw_start
					
					time_delta = current_time - state['last_pos_time'] - sync_compensation
					
					LOGGER.log_debug(
						f"Triggered at: {continuous_position}, "
						f"Compensated: {sync_compensation}, "
						f"Time_delta: {time_delta}"
					)

					sync_compensation = sync_compensation * 0.9
					
					# Synchronize actual offset used
					state['manual_offset'] = start_screen_line
					state.update({
						'force_redraw': False,
						'last_manual': manual_scroll,
						'last_start_screen_line': start_screen_line,
						'last_idx': current_idx
					})

			
			# CPU destressor
			if status == "paused" and not manual_scroll and not state['current_title']:
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
					
				sleep_time = 0.002
			else:
				stdscr.timeout(refresh_interval_2)
				sleep_time = 0.001

			# If polling or proximity is active, override for high-frequency updates
			if state['poll'] or state['proximity_active'] or manual_scroll:
				sleep_time = 0.000

			await asyncio.sleep(sleep_time)

		except Exception as e:
			LOGGER.log_debug(f"Main loop error: {str(e)}")
			
			await asyncio.sleep(1)
			stdscr.timeout(400)
			LOGGER.log_debug("Systemfault /s")

def main(stdscr, config_path=None, use_default=False, player=None): #Hacks
	"""Main function that runs the async event loop"""
	CONFIG_MANAGER = ConfigManager(
		config_path=config_path, 
		use_default=use_default, 
		player_override=player
	)
	
	CONFIG = CONFIG_MANAGER.config
	LOGGER = Logger()
	
	asyncio.run(main_async(stdscr))


def run_main(stdscr):
	main(
		stdscr, 
		config_path=args.config, 
		use_default=args.default,
		player=args.player
	)


if __name__ == "__main__":
	args = parse_args()

	while True:
		try:
			curses.wrapper(run_main)
		except KeyboardInterrupt:
			print("Exited by user (Ctrl+C).")
			atexit.register(executor.shutdown)
			exit()
		except Exception as e:
			temp_config = ConfigManager(config_path=args.config, use_default=args.default)
			temp_logger = Logger()
			temp_logger.log_debug(f"Fatal error: {str(e)}")
			time.sleep(1)
