#!/usr/bin/env python
"""
CMUS Lyrics Viewer with Synchronized Display
Displays time-synced lyrics for cmus music player using multiple lyric sources

Remember fetched lyrics has inaccuracies... this code has a very robust sync to your current play position you can adjust whatever you want
"""

# ==============
#  DEPENDENCIES
# ==============
import contextlib
import curses
import argparse
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Optional
import subprocess
import re
import bisect
import time
import asyncio
from datetime import datetime
from wcwidth import wcswidth
from functools import lru_cache
import urllib.request
import tempfile
import os
import json
import sys
import atexit
import socket

try:
	from mpd import MPDClient
except ImportError:
	MPDClient = None  # type: ignore


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

THREAD_POOL_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="lyrus_worker")

STATUS_PLAYING = "playing"
STATUS_PAUSED = "paused"
STATUS_STOPPED = "stopped"

PLAYER_CMUS = "cmus"
PLAYER_MPD = "mpd"
PLAYER_PLAYERCTL = "playerctl"

FORMAT_A2 = '.a2'
FORMAT_LRC = '.lrc'
FORMAT_TXT = '.txt'

ALIGN_LEFT = "left"
ALIGN_CENTER = "center"
ALIGN_RIGHT = "right"

# ==============
#  CONFIGURATION
# ==============
VERSION = "1.0.2"

config_dir = "~/.config/lyrus"
config_files = ["config.json", "config1.json", "config2.json"]


def parse_args():
	parser = argparse.ArgumentParser(description="Lyrus - cmus Lyrics synchronization project")
	parser.add_argument("-c", "--config", help="Path to configuration file")
	parser.add_argument("-d", "--default", action="store_true",
						help="Use default settings without loading a config file")
	parser.add_argument("-p", "--player", choices=["cmus", "mpd", "playerctl"],
						help="Specify which player you want to load only")
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
	__slots__ = (
		"use_user_dirs",
		"user_config_dir",
		"use_default",
		"config_path",
		"player_override",

		# Colors
		"COLOR_NAMES",
		"COLOR_TXT_ACTIVE",
		"COLOR_TXT_INACTIVE",
		"COLOR_LRC_ACTIVE",
		"COLOR_LRC_INACTIVE",
		"COLOR_ERROR",

		# Logging
		"LOG_DIR",
		"LYRICS_TIMEOUT_LOG",
		"LYRICS_INSTRUMENT_LOG",
		"DEBUG_LOG",
		"LOG_RETENTION_DAYS",
		"MAX_DEBUG_COUNT",
		"ENABLE_DEBUG_LOGGING",

		# Player
		"MPD_HOST",
		"MPD_PORT",
		"MPD_PASSWORD",
		"MPD_TIMEOUT",
		"ENABLE_CMUS",
		"ENABLE_MPD",
		"ENABLE_PLAYERCTL",

		# Lyrics
		"LYRIC_EXTENSIONS",
		"LYRIC_CACHE_DIR",
		"SEARCH_TIMEOUT",
		"VALIDATION_LENGTHS",
		"ALLOW_SYNCEDLYRIC",
		"PROVIDERS",
		"PROVIDER_FALLBACK",
		"PROVIDER_FORMAT_PRIORITY",
		"ALLOW_TRANSLATION",
		"LANGUAGE",
		"READ_EMBEDDED_LYRICS",
		"SKIP_EMBEDDED_TXT",

		# UI
		"DISPLAY_NAME",
		"MESSAGES",
		"TERMINAL_STATES",

		# Config storage
		"config",
	)
	def __init__(self, use_user_dirs=True, config_path=None, use_default=False, player_override=None):
		self.use_user_dirs: bool = use_user_dirs
		self.user_config_dir: str = os.path.expanduser(config_dir)
		os.makedirs(self.user_config_dir, exist_ok=True)
		self.use_default: bool = use_default
		self.config_path: Optional[str] = config_path
		self.player_override: Optional[str] = player_override

		# Color – set by setup_colors()
		self.COLOR_NAMES: dict = {}
		self.COLOR_TXT_ACTIVE: Any = None
		self.COLOR_TXT_INACTIVE: Any = None
		self.COLOR_LRC_ACTIVE: Any = None
		self.COLOR_LRC_INACTIVE: Any = None
		self.COLOR_ERROR: Any = None

		# Logging – set by setup_logging()
		self.LOG_DIR: str = ""
		self.LYRICS_TIMEOUT_LOG: str = ""
		self.DEBUG_LOG: str = ""
		self.LOG_RETENTION_DAYS: int = 10
		self.MAX_DEBUG_COUNT: int = 100
		self.ENABLE_DEBUG_LOGGING: bool = False

		# Player – set by setup_player()
		self.MPD_HOST: str = "localhost"
		self.MPD_PORT: Any = 6600
		self.MPD_PASSWORD: Optional[str] = None
		self.MPD_TIMEOUT: int = 10
		self.ENABLE_CMUS: bool = True
		self.ENABLE_MPD: bool = True
		self.ENABLE_PLAYERCTL: bool = True

		# Lyrics – set by setup_lyrics()
		self.LYRIC_EXTENSIONS: list = []
		self.LYRIC_CACHE_DIR: str = ""
		self.SEARCH_TIMEOUT: int = 15
		self.VALIDATION_LENGTHS: dict = {}
		self.ALLOW_SYNCEDLYRIC: bool = True
		self.PROVIDERS: list = []
		self.PROVIDER_FALLBACK: bool = True
		self.PROVIDER_FORMAT_PRIORITY: list = []
		self.ALLOW_TRANSLATION: bool = False
		self.LANGUAGE: str = "en"
		self.READ_EMBEDDED_LYRICS: bool = True
		self.SKIP_EMBEDDED_TXT: bool = True

		# UI – set by setup_ui()
		self.DISPLAY_NAME: bool = True
		self.MESSAGES: dict = {}
		self.TERMINAL_STATES: set = set()

		# Load – must be last so setup_* methods can assign above
		self.config: dict = self.load_config()
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
		default_config = {
			"global": {
				"logs_dir": "~/.cache/lyrus",
				"log_file": "application.log",
				"log_level": "FATAL",
				"lyrics_timeout_log": "lyrics_timeouts.log",
				"lyrics_instrument_log": "instrument.log",
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
			"terminal_states": ["done", "instrumental", "time_out", "failed", "mpd", "clear", "cmus", "no_player"],
			"lyrics": {
				"search_timeout": 15,
				"cache_dir": "~/.local/state/lyrus/synced_lyrics",
				"local_extensions": ["a2", "lrc", "txt"],
				"validation": {"title_match_length": 15, "artist_match_length": 15},
				"Syncedlyrics": True,
				"Sources": ["Musixmatch", "Lrclib", "NetEase", "Megalobiz", "Genius"],
				"Fallback": True,
				"Format_priority": ["a2", "lrc", "txt"],
				"read_embedded_lyrics": True,
				"skip_embedded_txt": True,
				"Translation": {
					"enable_translation": False,
					"language": "en",
				}
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
						"refresh_proximity_interval_ms": 0,
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
			config_paths = (
				[self.config_path]
				if self.config_path
				else [os.path.join(self.user_config_dir, f) for f in config_files]
			)
			for path in config_paths:
				if path and os.path.exists(os.path.expanduser(path)):
					try:
						with open(os.path.expanduser(path), "r") as f:
							file_config = json.load(f)
						if self.player_override and "player" in file_config:
							del file_config["player"]
						deep_merge_dicts(merged_config, file_config)
						break
					except (json.JSONDecodeError, OSError) as e:
						print(f"Error loading config from {path}: {e}")

		merged_config["global"]["enable_debug"] = (
			str(resolve_value(merged_config["global"]["enable_debug"])) == "1"
		)
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
		self.LYRICS_INSTRUMENT_LOG = self.config["global"]["lyrics_instrument_log"]
		self.DEBUG_LOG = self.config["global"]["debug_log"]
		self.LOG_RETENTION_DAYS = self.config["global"]["log_retention_days"]
		self.MAX_DEBUG_COUNT = self.config["global"]["max_debug_count"]
		self.ENABLE_DEBUG_LOGGING = self.config["global"]["enable_debug"]
		if self.ENABLE_DEBUG_LOGGING:
			print("Debug logging ENABLED")

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
		self.ALLOW_TRANSLATION = self.config["lyrics"]["Translation"]["enable_translation"]
		self.LANGUAGE = self.config["lyrics"]["Translation"]["language"]
		self.READ_EMBEDDED_LYRICS = self.config["lyrics"].get("read_embedded_lyrics")
		self.SKIP_EMBEDDED_TXT = self.config["lyrics"].get("skip_embedded_txt")

	def setup_ui(self):
		self.DISPLAY_NAME = self.config["ui"]["name"]
		self.MESSAGES = self.config["status_messages"]
		self.TERMINAL_STATES = set(self.config["terminal_states"])


# ================
#  LOGGING SYSTEM
# ================
class Logger:
	__slots__ = (
		'LOG_DIR', 'LYRICS_TIMEOUT_LOG', 'LYRICS_INSTRUMENT_LOG','DEBUG_LOG',
		'LOG_RETENTION_DAYS', 'MAX_DEBUG_COUNT', 'ENABLE_DEBUG_LOGGING',
		'config', '_log_dir_created',
		'_timeout_log_cache', '_timeout_log_cache_loaded', 
		'_instrumental_log_cache', '_instrumental_log_cache_loaded'
	)

	def __init__(self, config_manager):
		self.LOG_DIR = config_manager.LOG_DIR
		self.LYRICS_TIMEOUT_LOG = config_manager.LYRICS_TIMEOUT_LOG
		self.LYRICS_INSTRUMENT_LOG = config_manager.LYRICS_INSTRUMENT_LOG
		self.DEBUG_LOG = config_manager.DEBUG_LOG
		self.LOG_RETENTION_DAYS = config_manager.LOG_RETENTION_DAYS
		self.MAX_DEBUG_COUNT = config_manager.MAX_DEBUG_COUNT
		self.ENABLE_DEBUG_LOGGING = config_manager.ENABLE_DEBUG_LOGGING
		self.config = config_manager.config
		os.makedirs(self.LOG_DIR, exist_ok=True)
		self._log_dir_created = True
		self._timeout_log_cache = set()
		self._timeout_log_cache_loaded = False
		self._instrumental_log_cache = set()
		self._instrumental_log_cache_loaded = False

	def clean_debug_log(self):
		log_path = os.path.join(self.LOG_DIR, self.DEBUG_LOG)
		if not os.path.exists(log_path):
			return
		try:
			with open(log_path, 'r', encoding='utf-8') as f:
				lines = f.readlines()
			if len(lines) > self.MAX_DEBUG_COUNT:
				with open(log_path, 'w', encoding='utf-8') as f:
					f.writelines(lines[-self.MAX_DEBUG_COUNT:])
		except (OSError, IOError) as e:
			print(f"Error cleaning debug log: {e}")

	def clean_log(self):
		log_path = os.path.join(self.LOG_DIR, self.config["global"]["log_file"])
		try:
			if os.path.exists(log_path):
				with open(log_path, "r+") as f:
					lines = f.readlines()
					if len(lines) > self.config["global"]["max_log_count"]:
						keep = lines[-self.config["global"]["max_log_count"]:]
						f.seek(0)
						f.truncate()
						f.writelines(keep)
		except (OSError, IOError) as e:
			print(f"Log cleanup failed: {str(e)}", file=sys.stderr)

	def log_message(self, level: str, message: str):
		main_log = os.path.join(self.LOG_DIR, self.config["global"]["log_file"])
		debug_log = os.path.join(self.LOG_DIR, self.DEBUG_LOG)
		configured_level = LOG_LEVELS.get(self.config["global"]["log_level"], 2)
		message_level = LOG_LEVELS.get(level.upper(), 2)
		try:
			timestamp = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}.{int(time.time() * 1000000) % 1000000:06d}"
			if self.config["global"]["enable_debug"] and message_level <= LOG_LEVELS["DEBUG"]:
				debug_entry = f"{timestamp} | {level.upper()} | {message}\n"
				with open(debug_log, "a", encoding='utf-8') as f:
					f.write(debug_entry)
				self.clean_debug_log()
			if message_level >= configured_level:
				main_entry = f"{timestamp} | {level.upper()} | {message}\n"
				with open(main_log, "a", encoding='utf-8') as f:
					f.write(main_entry)
				if os.path.getsize(main_log) > self.config["global"]["max_log_count"] * 1024:
					self.clean_log()
		except Exception as e:  # noqa: BLE001
			sys.stderr.write(f"Logging failed: {str(e)}\n")

	def log_fatal(self, message: str): self.log_message("FATAL", message)
	def log_error(self, message: str): self.log_message("ERROR", message)
	def log_warn(self, message: str):  self.log_message("WARN", message)
	def log_info(self, message: str):  self.log_message("INFO", message)
	def log_debug(self, message: str): self.log_message("DEBUG", message)
	def log_trace(self, message: str): self.log_message("TRACE", message)

	def log_timeout(self, artist, title):
		try:
			timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
			log_path = os.path.join(self.LOG_DIR, self.LYRICS_TIMEOUT_LOG)
			if not self._timeout_log_cache_loaded and os.path.exists(log_path):
				with open(log_path, 'r', encoding='utf-8') as f:
					for line in f:
						if "Artist: " in line and "Title: " in line:
							parts = line.split("|")
							if len(parts) >= 3:
								a = parts[1].replace("Artist:", "").strip()
								t = parts[2].replace("Title:", "").strip()
								self._timeout_log_cache.add((a, t))
				self._timeout_log_cache_loaded = True

			entry_key = (artist or 'Unknown', title or 'Unknown')
			if entry_key in self._timeout_log_cache:
				return

			log_entry = f"{timestamp} | Artist: {artist or 'Unknown'} | Title: {title or 'Unknown'}\n"
			with open(log_path, 'a', encoding='utf-8') as f:
				f.write(log_entry)
			self._timeout_log_cache.add(entry_key)
			self.clean_log()
		except OSError as e:
			self.log_error(f"Failed to write timeout log: {e}")
	
	def log_instrumental(self, artist, title):
		try:
			timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
			log_path = os.path.join(self.LOG_DIR, self.LYRICS_INSTRUMENT_LOG)

			# Load cache if not already loaded
			if not self._instrumental_log_cache_loaded and os.path.exists(log_path):
				with open(log_path, 'r', encoding='utf-8') as f:
					for line in f:
						if "Artist: " in line and "Title: " in line:
							parts = line.split("|")
							if len(parts) >= 3:
								a = parts[1].replace("Artist:", "").strip()
								t = parts[2].replace("Title:", "").strip()
								self._instrumental_log_cache.add((a, t))
				self._instrumental_log_cache_loaded = True

			entry_key = (artist or 'Unknown', title or 'Unknown')
			if entry_key in self._instrumental_log_cache:
				return

			log_entry = f"{timestamp} | Artist: {artist or 'Unknown'} | Title: {title or 'Unknown'}\n"
			with open(log_path, 'a', encoding='utf-8') as f:
				f.write(log_entry)
			self._instrumental_log_cache.add(entry_key)
			self.clean_log()
		except OSError as e:
			self.log_error(f"Failed to write instrumental log: {e}")


# ================
#  FETCH STATE
# ================
class FetchState:
	"""Thread-safe fetch status tracker."""
	__slots__ = ('_lock', 'current_step', 'start_time', 'lyric_count', 'done_time')

	def __init__(self):
		self._lock = threading.Lock()
		self.current_step: Optional[str] = None
		self.start_time: Optional[float] = None
		self.lyric_count: int = 0
		self.done_time: Optional[float] = None

	def update(self, step: str, lyrics_found: int = 0, config_manager=None):
		with self._lock:
			self.current_step = step
			self.lyric_count = lyrics_found
			if step == 'start':
				self.start_time = time.time()
			if config_manager and step in config_manager.TERMINAL_STATES:
				self.done_time = time.time()
			else:
				self.done_time = None

	def get_status_message(self, config_manager) -> Optional[str]:
		with self._lock:
			step = self.current_step
			if not step:
				return None
			if step in config_manager.TERMINAL_STATES and self.done_time:
				if time.time() - self.done_time > 2:
					return ""
			if step == 'clear':
				return ""
			base_msg = config_manager.MESSAGES.get(step, step)
			if self.start_time and step != 'done':
				end_time = self.done_time or time.time()
				elapsed = end_time - self.start_time
				return f"{base_msg} {elapsed:.1f}s"
			return base_msg

_fetch_state = FetchState()

def update_fetch_status(step: str, lyrics_found: int = 0, config_manager=None):
	_fetch_state.update(step, lyrics_found, config_manager)

def get_current_status(config_manager) -> Optional[str]:
	return _fetch_state.get_status_message(config_manager)


# ================
#  NETWORK UTILS
# ================
_internet_cache: dict = {'result': None, 'ts': 0.0, 'ttl': 30.0}

def has_internet_global(timeout: int = 3) -> bool:
	now = time.monotonic()
	if (_internet_cache['result'] is not None and
			now - _internet_cache['ts'] < _internet_cache['ttl']):
		return _internet_cache['result']

	hosts = [
		"https://1.1.1.1",
		"https://www.google.com",
		"https://www.baidu.com",
		"https://www.qq.com",
	]
	result = False
	for url in hosts:
		try:
			urllib.request.urlopen(url, timeout=timeout)
			result = True
			break
		except Exception:
			continue

	_internet_cache['result'] = result
	_internet_cache['ts'] = now
	return result


# ================
#  ASYNC HELPERS
# ================
async def fetch_lrclib_async(artist, title, instrumental = False, duration=None, session=None):
	import aiohttp

	base_url = "https://lrclib.net/api/get"
	params = {'artist_name': artist, 'track_name': title}
	if duration:
		params['duration'] = duration

	own_session = session is None
	if own_session:
		session = aiohttp.ClientSession()

	try:
		async with session.get(
			base_url, params=params, timeout=aiohttp.ClientTimeout(total=15)
		) as response:
			if response.status == 200:
				try:
					data = await response.json(content_type=None)
					if data.get('instrumental', False):
						return None, None, True
					return data.get('syncedLyrics') or data.get('plainLyrics'), bool(data.get('syncedLyrics')), False
				except (aiohttp.ContentTypeError, json.JSONDecodeError):
					pass
	except (aiohttp.ClientError, asyncio.TimeoutError):
		pass
	finally:
		if own_session:
			await session.close()

	return None, None, False


# ======================
#  CORE LYRIC FUNCTIONS
# ======================
_FILENAME_SANITIZE_PATTERN = re.compile(r'[<>:"/\\|?*]')
_STRING_SANITIZE_PATTERN = re.compile(r'[^a-zA-Z0-9]')
_TIMESTAMP_PATTERN = re.compile(r'\[\d+:\d+(?:[.:]\d+)?]')
_A2_WORD_PATTERN = re.compile(r'<(\d{2}:\d{2}\.\d{2})>(.*?)<(\d{2}:\d{2}\.\d{2})>')
_A2_LINE_PATTERN = re.compile(r'^\[(\d{2}:\d{2}\.\d{2})](.*)')
_LRC_PATTERN = re.compile(r'^\s*\[(\d+:\d+(?:[.:]\d+)?)]\s*(.*)$')
_TIME_PATTERNS = [
	re.compile(r'^(?P<m>\d+):(?P<s>\d+\.\d+)$'),
	re.compile(r'^(?P<m>\d+):(?P<s>\d+):(?P<ms>\d{1,3})$'),
	re.compile(r'^(?P<m>\d+):(?P<s>\d+)$'),
	re.compile(r'^(?P<s>\d+\.\d+)$'),
	re.compile(r'^(?P<s>\d+)$')
]


@lru_cache(maxsize=128)
def sanitize_filename(name):
	return _FILENAME_SANITIZE_PATTERN.sub('_', str(name))


@lru_cache(maxsize=128)
def sanitize_string(s):
	return _STRING_SANITIZE_PATTERN.sub('', str(s)).lower()


async def fetch_lyrics_lrclib_async(artist_name: str, track_name: str, instrumental: bool, duration: Optional[float] = None):
	try:
		result = await fetch_lrclib_async(artist_name, track_name, duration, Instrumental)
		return result
	except Exception:
		return None, None, False



def validate_lyrics(content: str) -> bool:
	"""Validate that lyrics content is non-empty and structurally plausible."""
	if not content or not content.strip():
		return False
	# Timestamped formats are self-validating
	if _TIMESTAMP_PATTERN.search(content):
		return True
	# Plain text: require at least 2 non-empty lines
	non_empty = [ln for ln in content.splitlines() if ln.strip()]
	return len(non_empty) >= 2


async def fetch_lyrics_syncedlyrics_async(
	artist_name, track_name, config_manager=None
):
	import syncedlyrics

	try:
		search_term = f"{track_name} {artist_name}".strip()

		def worker(term: str, synced: bool = True):
			try:
				kwargs: dict = {}
				if synced:
					kwargs["synced_only"] = True
				else:
					kwargs["plain_only"] = True
					kwargs["providers"] = config_manager.PROVIDERS
				if config_manager.ALLOW_TRANSLATION:
					kwargs["lang"] = config_manager.LANGUAGE
				return syncedlyrics.search(term, **kwargs), synced
			except Exception:
				return None, False

		if not search_term:
			return None, None

		# FIX: asyncio.get_event_loop() is deprecated in 3.10+; use get_running_loop()
		loop = asyncio.get_running_loop()
		lyrics, is_synced = await loop.run_in_executor(THREAD_POOL_EXECUTOR, worker, search_term, True)
		if lyrics:
			if not validate_lyrics(lyrics):
				pass  # use anyway, caller may prepend a warning
			return lyrics, is_synced

		lyrics, is_synced = await loop.run_in_executor(THREAD_POOL_EXECUTOR, worker, search_term, False)
		if lyrics and validate_lyrics(lyrics):
			return lyrics, False

		return None, None
	except Exception:
		return None, None


# FIX: save_lyrics now returns (path, error) so callers can distinguish a
# successful save from a save failure, rather than treating both as "no lyrics".
def save_lyrics(lyrics, track_name, artist_name, extension, config_manager, logger):
	try:
		folder = config_manager.LYRIC_CACHE_DIR
		sanitized_track = sanitize_filename(track_name)
		sanitized_artist = sanitize_filename(artist_name)
		filename = f"{sanitized_track}_{sanitized_artist}.{extension}"
		file_path = os.path.join(folder, filename)
		with open(file_path, "w", encoding="utf-8") as f:
			f.write(lyrics)
		return file_path, None
	except (OSError, IOError) as e:
		logger.log_error(f"Failed to save lyrics: {str(e)}")
		return None, str(e)


def is_lyrics_timed_out(artist_name, track_name, config_manager, logger):
	log_path = os.path.join(config_manager.LOG_DIR, config_manager.LYRICS_TIMEOUT_LOG)
	if not os.path.exists(log_path):
		return False
	try:
		search_artist = artist_name or 'Unknown'
		search_title = track_name or 'Unknown'
		with open(log_path, 'r', encoding='utf-8') as f:
			for line in f:
				if f"Artist: {search_artist}" in line and f"Title: {search_title}" in line:
					return True
		return False
	except (OSError, IOError) as e:
		logger.log_debug(f"Timeout check error: {e}")
		return False

def is_lyrics_instrumental(artist_name, track_name, config_manager, logger):
	log_path = os.path.join(config_manager.LOG_DIR, config_manager.LYRICS_INSTRUMENT_LOG)
	if not os.path.exists(log_path):
		return False
	try:
		search_artist = artist_name or 'Unknown'
		search_title = track_name or 'Unknown'
		with open(log_path, 'r', encoding='utf-8') as f:
			for line in f:
				if f"Artist: {search_artist}" in line and f"Title: {search_title}" in line:
					return True
		return False
	except (OSError, IOError) as e:
		logger.log_debug(f"Timeout check error: {e}")
		return False


# ====================================
#  EMBEDDED LYRICS READER
# ====================================

async def read_embedded_lyrics(audio_file: str, logger):
	import mutagen.flac
	import mutagen.oggvorbis
	import mutagen.oggopus
	import mutagen.mp3
	import mutagen.mp4

	if not audio_file or not os.path.exists(audio_file):
		return None

	ext = os.path.splitext(audio_file)[1].lower()

	try:
		if ext == ".flac":
			audio = await asyncio.to_thread(lambda: mutagen.flac.FLAC(audio_file))
			return _read_vorbis_comments(audio)

		if ext == ".ogg":
			audio = await asyncio.to_thread(lambda: mutagen.oggvorbis.OggVorbis(audio_file))
			return _read_vorbis_comments(audio)

		if ext == ".opus":
			audio = await asyncio.to_thread(lambda: mutagen.oggopus.OggOpus(audio_file))
			return _read_vorbis_comments(audio)

		if ext == ".mp3":
			audio = await asyncio.to_thread(lambda: mutagen.mp3.MP3(audio_file))
			if not audio.tags:
				return None
			sylt_frames = audio.tags.getall("SYLT")
			if sylt_frames:
				frame = sylt_frames[0]
				sylt_lines: list = []
				for text, timestamp in frame.text:
					minutes = int(timestamp) // 60000
					seconds = (timestamp % 60000) / 1000
					sylt_lines.append(f"[{minutes:02d}:{seconds:05.2f}]{text}")
				sylt_content = "\n".join(sylt_lines).strip()
				if sylt_content:
					return {"type": "embedded", "format": "lrc", "content": sylt_content, "path": None}
			uslt_frames = audio.tags.getall("USLT")
			if uslt_frames:
				uslt_content = uslt_frames[0].text.strip()
				if uslt_content:
					return {"type": "embedded", "format": "txt", "content": uslt_content, "path": None}
			return None

		if ext in {".m4a", ".mp4"}:
			audio = await asyncio.to_thread(lambda: mutagen.mp4.MP4(audio_file))
			if "©lyr" in audio:
				values = audio["©lyr"]
				if values:
					m4a_content = "\n".join(v.strip() for v in values if v.strip())
					if m4a_content:
						return {"type": "embedded", "format": "txt", "content": m4a_content, "path": None}
			return None

	except Exception as e:  # noqa: BLE001
		logger.log_debug(f"Embedded lyrics read error for {audio_file}: {e}")
		return None

	return None


def _read_vorbis_comments(audio):
	lower_map = {k.lower(): k for k in audio.keys()}

	def detect_format(text: str) -> str:
		return "lrc" if re.search(r'\[\d+:\d+(?:[.:]\d+)?]', text) else "txt"

	for key_name in ("lrc", "lyrics", "unsyncedlyrics"):
		if key_name in lower_map:
			key = lower_map[key_name]
			values = audio.get(key)
			if values:
				content = "\n".join(v.strip() for v in values if v.strip())
				if content:
					fmt = detect_format(content) if key_name != "unsyncedlyrics" else "txt"
					return {"type": "embedded", "format": fmt, "content": content, "path": None}
	return None


# ========================
#  LYRIC FILE SEARCH
# ========================

def _load_lyric_path(file_path: str, logger) -> str | None:
	"""Read a lyric file path, deleting it if empty. Returns path or None."""
	try:
		if os.path.getsize(file_path) == 0:
			logger.log_debug(f"Deleting empty file: {file_path}")
			os.remove(file_path)
			return None
		with open(file_path, 'r', encoding='utf-8') as f:
			content = f.read()
		if not content.strip():
			logger.log_debug(f"Deleting blank lyric file: {file_path}")
			os.remove(file_path)
			return None
		return file_path
	except OSError as exc:
		logger.log_debug(f"File access error {file_path}: {exc}")
		return None


async def find_lyrics_file_async(
	audio_file, directory, artist_name, track_name,
	duration=None, config_manager=None, logger=None
):
	update_fetch_status('local', config_manager=config_manager)
	logger.log_info(f"Starting lyric search for: {artist_name or 'Unknown'} - {track_name}")

	try:
		is_instrumental = (
			"instrumental" in track_name.lower() or
			(artist_name and "instrumental" in artist_name.lower())
		)
		if is_instrumental:
			logger.log_debug("Instrumental track detected")
			logger.log_instrumental(artist_name, track_name)
			update_fetch_status('instrumental', config_manager=config_manager)
			path, err = save_lyrics("[Instrumental]", track_name, artist_name, 'txt', config_manager, logger)
			return path

		if config_manager.READ_EMBEDDED_LYRICS and audio_file and os.path.exists(audio_file):
			embedded = await read_embedded_lyrics(audio_file, logger)
			if embedded:
				logger.log_debug(f"Embedded lyrics first 200 chars:\n{embedded['content'][:200]}")
				if config_manager.SKIP_EMBEDDED_TXT and embedded['format'] == 'txt':
					logger.log_debug("Skipping embedded plain text (skip_embedded_txt=True)")
				else:
					if validate_lyrics(embedded['content']):
						update_fetch_status('done', config_manager=config_manager)
						logger.log_debug("Using embedded lyrics")
						return embedded
					else:
						embedded['warning'] = "Validation warning"
						update_fetch_status('done', config_manager=config_manager)
						return embedded

		if audio_file and directory and audio_file != "None":
			base_name, _ = os.path.splitext(os.path.basename(audio_file))
			for ext in ('a2', 'lrc', 'txt'):
				file_path = os.path.join(directory, f"{base_name}.{ext}")
				if os.path.isfile(file_path):
					result = _load_lyric_path(file_path, logger)
					if result is not None:
						logger.log_info(f"Using local file: {result}")
						return result
					continue

		sanitized_track = sanitize_filename(track_name)
		sanitized_artist = sanitize_filename(artist_name)
		possible_filenames = [
			f"{sanitized_track}.a2", f"{sanitized_track}.lrc", f"{sanitized_track}.txt",
			f"{sanitized_track}_{sanitized_artist}.a2",
			f"{sanitized_track}_{sanitized_artist}.lrc",
			f"{sanitized_track}_{sanitized_artist}.txt",
		]

		for dir_path in [d for d in [directory, config_manager.LYRIC_CACHE_DIR] if d]:
			for filename in possible_filenames:
				file_path = os.path.join(dir_path, filename)
				if os.path.isfile(file_path):
					result = _load_lyric_path(file_path, logger)
					if result is not None:
						logger.log_debug(f"Using cached file: {result}")
						return result
					continue

		if is_lyrics_instrumental(artist_name, track_name, config_manager, logger):
			update_fetch_status('instrumental', config_manager=config_manager)
			logger.log_debug(f"{artist_name} - {track_name} Lyrics is instrumental")
			return None

		if is_lyrics_timed_out(artist_name, track_name, config_manager, logger):
			update_fetch_status('time_out', config_manager=config_manager)
			logger.log_debug(f"Lyrics timeout active for {artist_name} - {track_name}")
			return None

		update_fetch_status('synced', config_manager=config_manager)
		logger.log_debug(f"Fetching lyrics online: {artist_name} - {track_name}")

		tasks = [fetch_lyrics_lrclib_async(artist_name, track_name, instrumental, duration)]
		if config_manager.ALLOW_SYNCEDLYRIC:
			tasks.append(
				fetch_lyrics_syncedlyrics_async(artist_name, track_name, config_manager=config_manager)
			)
		elif not config_manager.PROVIDER_FALLBACK:
			tasks = [fetch_lyrics_lrclib_async(artist_name, track_name, duration)]
		elif instrumental:
			logger.log_debug("instrumental detected")
			logger.log_instrumental(artist_name, track_name)

		results = await asyncio.gather(*tasks, return_exceptions=True)

		candidates = []
		for idx, result in enumerate(results):
			if isinstance(result, Exception):
				logger.log_debug(f"Fetch task {idx} raised: {result}")
				continue
			fetched_lyrics, is_synced = result
			if not fetched_lyrics:
				continue

			if not validate_lyrics(fetched_lyrics):
				logger.log_debug("Validation warning - possible mismatch")
				fetched_lyrics = "[Validation Warning] Potential mismatch\n" + fetched_lyrics

			is_enhanced = any(re.search(r'<\d+:\d+\.\d+>', line) for line in fetched_lyrics.split('\n'))
			has_lrc_timestamps = re.search(r'\[\d+:\d+\.\d+]', fetched_lyrics) is not None

			if is_enhanced:
				extension = 'a2'
			elif is_synced and has_lrc_timestamps:
				extension = 'lrc'
			else:
				extension = 'txt'
			candidates.append((extension, fetched_lyrics))
			logger.log_debug(f"Candidate: lines={len(fetched_lyrics.splitlines())}, fmt={extension}")

		if not candidates:
			logger.log_debug("No lyrics found from any source")
			update_fetch_status("failed", config_manager=config_manager)
			if has_internet_global():
				logger.log_timeout(artist_name, track_name)
			return None

		priority_order = config_manager.PROVIDER_FORMAT_PRIORITY
		candidates.sort(key=lambda x: priority_order.index(x[0]) if x[0] in priority_order else 99)
		best_extension, best_lyrics = candidates[0]

		logger.log_debug(f"Selected format: {best_extension}")
		# FIX: save_lyrics failure is now distinguishable from "no lyrics found"
		path, err = save_lyrics(best_lyrics, track_name, artist_name, best_extension, config_manager, logger)
		if err:
			logger.log_error(f"Lyrics fetched but save failed: {err}")
		return path

	except Exception as e:  # noqa: BLE001
		logger.log_error(f"Error in find_lyrics_file: {str(e)}")
		update_fetch_status("failed", config_manager=config_manager)
		return None


async def fetch_lyrics_async(audio_file, directory, artist, title, duration, config_manager, logger):
	try:
		result = await find_lyrics_file_async(
			audio_file, directory, artist, title, duration, config_manager, logger
		)
		if result is None:
			return ([], []), False, False

		if isinstance(result, dict) and result.get('type') == 'embedded':
			lyrics_content = result['content']
			fmt = result['format']
			with tempfile.NamedTemporaryFile(mode='w', suffix=f'.{fmt}', delete=False) as tmp:
				tmp.write(lyrics_content)
				tmp_path = tmp.name
			try:
				lyrics, errors = load_lyrics(tmp_path, logger)
				is_txt = (fmt == 'txt')
				is_a2 = (fmt == 'a2')
				update_fetch_status('done', lyrics_found=len(lyrics), config_manager=config_manager)
				return (lyrics, errors), is_txt, is_a2
			finally:
				with contextlib.suppress(OSError):
					os.unlink(tmp_path)

		elif isinstance(result, str):
			is_txt = result.endswith('.txt')
			is_a2 = result.endswith('.a2')
			lyrics, errors = load_lyrics(result, logger)
			update_fetch_status('done', lyrics_found=len(lyrics), config_manager=config_manager)
			return (lyrics, errors), is_txt, is_a2

		return ([], []), False, False

	except Exception as e:  # noqa: BLE001
		logger.log_error(f"{title} lyrics fetch error: {e}")
		update_fetch_status('failed', config_manager=config_manager)
		return ([], []), False, False


def parse_time_to_seconds(time_str: str) -> float:
	for pattern in _TIME_PATTERNS:
		match = pattern.match(time_str)
		if match:
			parts = match.groupdict()
			minutes = int(parts.get('m', 0) or 0)
			seconds = float(parts.get('s', 0) or 0)
			milliseconds = int(parts.get('ms', 0) or 0) / 1000
			return round(minutes * 60 + seconds + milliseconds, 3)
	raise ValueError(f"Invalid time format: {time_str}")


def load_lyrics(file_path, logger):
	lyrics = []
	errors = []
	logger.log_trace(f"Parsing lyrics file: {file_path}")
	try:
		try:
			with open(file_path, 'r', encoding="utf-8") as f:
				lines = f.readlines()
		except OSError as e:
			errors.append(f"File open error: {str(e)}")
			return lyrics, errors

		if file_path.endswith('.a2'):
			for line in lines:
				line = line.strip()
				if not line:
					continue
				line_match = _A2_LINE_PATTERN.match(line)
				if line_match:
					try:
						line_time = parse_time_to_seconds(line_match.group(1))
						lyrics.append((line_time, None))
						content = line_match.group(2)
						words = _A2_WORD_PATTERN.findall(content)
						for start_str, text, end_str in words:
							try:
								start = parse_time_to_seconds(start_str)
								clean_text = re.sub(r'<.*?>', '', text).strip()
								if clean_text:
									lyrics.append((start, (clean_text, end_str)))
							except ValueError as e:
								errors.append(f"Invalid word timestamp: {e}")
						remaining = re.sub(_A2_WORD_PATTERN, '', content).strip()
						if remaining:
							lyrics.append((line_time, (remaining, line_time)))
						lyrics.append((line_time, None))
					except ValueError as e:
						errors.append(f"Invalid line timestamp: {e}")

		elif file_path.endswith('.txt'):
			for line in lines:
				lyrics.append((None, line.rstrip('\n')))
		else:
			for line in lines:
				raw_line = line.rstrip('\n')
				line_match = _LRC_PATTERN.match(raw_line)
				if line_match:
					try:
						line_time = parse_time_to_seconds(line_match.group(1))
						lyrics.append((line_time, line_match.group(2).strip()))
					except ValueError as e:
						errors.append(f"Invalid timestamp: {e}")
				else:
					lyrics.append((None, raw_line))

		if errors:
			logger.log_warn(f"Found {len(errors)} parsing errors in {file_path}")
		return lyrics, errors
	except Exception as e:  # noqa: BLE001
		errors.append(f"Unexpected parsing error: {str(e)}")
		return lyrics, errors


# ==============
#  PLAYER DETECTION
# ==============
async def get_cmus_info():
	try:
		proc = await asyncio.create_subprocess_exec(
			'cmus-remote', '-Q',
			stdout=asyncio.subprocess.PIPE,
			stderr=asyncio.subprocess.PIPE
		)
		stdout, _ = await proc.communicate()
		if proc.returncode != 0:
			return None, 0, "", None, 0, STATUS_STOPPED

		output = stdout.decode().splitlines()
		file = None
		position = 0
		duration = 0
		status = STATUS_STOPPED
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
					tags[parts[1]] = parts[2].strip()

		def split_artists(tag_value):
			if not tag_value:
				return []
			return [a.strip() for a in tag_value.replace("/", ";").split(";") if a.strip()]

		aa, ar = tags.get("albumartist"), tags.get("artist")
		if aa == "Various Artists" and ar:
			artists_list = split_artists(ar)
		elif aa:
			artists_list = split_artists(aa)
		elif ar:
			artists_list = split_artists(ar)
		else:
			artists_list = []

		artist_str = ", ".join(artists_list) if artists_list else ""
		return file, position, artist_str, tags.get("title"), duration, status

	except Exception:
		return None, 0, "", None, 0, STATUS_STOPPED


async def get_mpd_info(config_manager):
	def _sync_mpd():
		if MPDClient is None:
			return None, 0.0, "", None, 0.0, STATUS_STOPPED
		client = MPDClient()
		client.timeout = config_manager.MPD_TIMEOUT
		try:
			client.connect(config_manager.MPD_HOST, config_manager.MPD_PORT)  # type: ignore
			if config_manager.MPD_PASSWORD:
				client.password(config_manager.MPD_PASSWORD)  # type: ignore
			status = client.status()  # type: ignore
			current_song = client.currentsong()  # type: ignore
			artist = current_song.get("artist", "")
			if isinstance(artist, list):
				artist = ", ".join(artist)
			file = current_song.get("file", "")
			position = float(status.get("elapsed", 0))
			title = current_song.get("title", None)
			duration = float(status.get("duration", status.get("time", 0)))
			state = status.get("state", STATUS_STOPPED)
			client.close()  # type: ignore
			client.disconnect()  # type: ignore
			return file, position, artist, title, duration, state
		except (socket.error, ConnectionRefusedError):
			pass
		except Exception:
			pass
		update_fetch_status("mpd", config_manager=config_manager)
		return None, 0.0, "", None, 0.0, STATUS_STOPPED

	loop = asyncio.get_running_loop()
	return await loop.run_in_executor(THREAD_POOL_EXECUTOR, _sync_mpd)


async def get_playerctl_info():
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

		if "No players found" in output or not output:
			return None, 0.0, "", None, 0.0, STATUS_STOPPED

		fields = output.split("|")
		if len(fields) != 6:
			return None, 0.0, "", None, 0.0, STATUS_STOPPED

		_, artist, title, position, status, duration = fields
		position_sec = float(position) / 1_000_000 if position else 0.0
		duration_sec = float(duration) / 1_000_000 if duration else 0.0
		status = status.lower() if status else STATUS_STOPPED

		if position_sec < 0 or (duration_sec > 0 and position_sec > duration_sec * 1.5):
			position_sec = duration_sec if status == STATUS_PAUSED else 0.0

		return None, position_sec, artist or "", title, duration_sec, status

	except Exception:
		return None, 0.0, "", None, 0.0, STATUS_STOPPED


async def get_player_info(config_manager):
	if config_manager.ENABLE_CMUS:
		try:
			cmus_info = await get_cmus_info()
			if cmus_info[0] is not None:
				return PLAYER_CMUS, cmus_info
		except Exception:
			pass

	if config_manager.ENABLE_MPD:
		try:
			mpd_info = await get_mpd_info(config_manager)
			if mpd_info[0] is not None:
				return PLAYER_MPD, mpd_info
		except Exception:
			pass

	if config_manager.ENABLE_PLAYERCTL:
		try:
			playerctl_info = await get_playerctl_info()
			if playerctl_info[3] is not None:
				return PLAYER_PLAYERCTL, playerctl_info
		except Exception:
			pass

	update_fetch_status("no_player", config_manager=config_manager)
	return None, (None, 0, "", None, 0, STATUS_STOPPED)


# ==============
#  UI RENDERING
# ==============
def get_color_value(color_input: Any) -> int:
	curses.start_color()
	max_colors = curses.COLORS if curses.COLORS > 8 else 8
	try:
		if isinstance(color_input, (int, str)) and str(color_input).isdigit():
			return max(0, min(int(color_input), max_colors - 1))
		if isinstance(color_input, str):
			color = color_input.lower()
			color_names = {
				"black": 0, "red": 1, "green": 2, "yellow": 3,
				"blue": 4, "magenta": 5, "cyan": 6, "white": 7
			}
			return color_names.get(color, 7)
		return 7
	except Exception:
		return 7


def resolve_color(setting: dict) -> int:
	raw_value = os.environ.get(setting["env"], setting.get("default", 7))
	return get_color_value(raw_value)


@dataclass(slots=True)
class DisplayState:
	"""Encapsulates display cache and curses window handles."""
	lyrics_hash: int = -1
	window_width: int = -1
	wrapped_lines: list = field(default_factory=list)
	wrapped_widths: list = field(default_factory=list)
	widths_cache: dict = field(default_factory=dict)
	a2_groups: Optional[list] = None
	a2_word_cache: dict = field(default_factory=dict)
	error_win: Any = None
	lyrics_win: Any = None
	adjust_win: Any = None
	status_win: Any = None
	dims: Optional[tuple[int, int]] = None

	def invalidate(self):
		self.lyrics_hash = -1
		self.window_width = -1
		self.wrapped_lines = []
		self.wrapped_widths = []
		self.widths_cache = {}
		self.a2_groups = None
		self.a2_word_cache = {}


def get_lyrics_hash(lyrics) -> int:
	if not lyrics:
		return 0
	return hash(tuple((t, str(item)) for t, item in lyrics))


def wrap_by_display_width(text, width, subsequent_indent=''):
	if not text:
		return []

	lines = []
	current_line = []
	current_width = 0

	for word in re.split(r'(\s+)', text):
		if not word:
			continue
		word_width = wcswidth(word)
		if word.isspace() and not current_line:
			continue
		if current_width + word_width <= width or not current_line:
			current_line.append(word)
			current_width += word_width
		else:
			lines.append(''.join(current_line))
			stripped = word.lstrip()
			current_line = [subsequent_indent + stripped] if lines else [word]
			current_width = wcswidth(subsequent_indent) + wcswidth(stripped) if lines else word_width

	if current_line:
		lines.append(''.join(current_line))

	return [line.rstrip() for line in lines]


def display_lyrics(
	stdscr,
	ds: DisplayState,
	lyrics,
	errors,
	position: Optional[float],
	manual_offset: int,
	is_txt_format,
	is_a2_format,
	current_idx,
	use_manual_offset,
	time_adjust=0,
	is_fetching=False,
	alignment='center',
	player_info: Optional[tuple] = None,
	config_manager=None
):
	"""Render lyrics in curses interface."""
	height, width = stdscr.getmaxyx()
	lyrics_hash = get_lyrics_hash(lyrics)

	status_lines = 2
	main_status_line = height - 1
	time_adjust_line = height - 2
	lyrics_area_height = height - status_lines - 1

	if lyrics_area_height <= 0:
		stdscr.noutrefresh()
		return 0

	cache_invalid = (ds.lyrics_hash != lyrics_hash or ds.window_width != width)

	if cache_invalid:
		ds.lyrics_hash = lyrics_hash
		ds.window_width = width
		ds.wrapped_lines = []
		ds.wrapped_widths = []
		ds.widths_cache = {}
		ds.a2_groups = None
		ds.a2_word_cache = {}

	if ds.dims != (height, width):
		curses.resizeterm(height, width)
		ds.error_win = curses.newwin(1, width, 0, 0)
		ds.lyrics_win = curses.newwin(lyrics_area_height, width, 1, 0)
		ds.adjust_win = curses.newwin(1, width, time_adjust_line, 0)
		ds.status_win = curses.newwin(1, width, main_status_line, 0)
		ds.dims = (height, width)
		cache_invalid = True

	error_win = ds.error_win
	lyrics_win = ds.lyrics_win
	adjust_win = ds.adjust_win
	status_win = ds.status_win

	if use_manual_offset and manual_offset != 0 and position is not None:
		with contextlib.suppress(Exception):
			position += int(manual_offset * 1_000_000)

	# 1) Error line
	error_win.erase()
	if errors:
		with contextlib.suppress(curses.error):
			error_win.addstr(0, 0, f"Errors: {len(errors)}"[:width - 1], curses.color_pair(1))
	error_win.noutrefresh()

	# 2) Lyrics area
	lyrics_win.erase()

	if is_a2_format:
		if cache_invalid or ds.a2_groups is None:
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
			ds.a2_groups = a2_lines
		else:
			a2_lines = ds.a2_groups

		visible = lyrics_area_height
		max_start = max(0, len(a2_lines) - visible)
		start_line = (min(max(manual_offset, 0), max_start)
					  if use_manual_offset else max_start)
		y = 0

		for idx in range(start_line, min(start_line + visible, len(a2_lines))):
			if y >= visible:
				break
			line = a2_lines[idx]
			line_key = tuple((t, str(text)) for t, (text, _) in line)
			if line_key not in ds.a2_word_cache:
				word_widths = []
				for _, (text, _) in line:
					if text not in ds.widths_cache:
						ds.widths_cache[text] = wcswidth(text)
					word_widths.append(ds.widths_cache[text])
				ds.a2_word_cache[line_key] = word_widths

			word_widths = ds.a2_word_cache[line_key]
			total_width = sum(word_widths) + max(0, len(word_widths) - 1)

			if alignment == ALIGN_RIGHT:
				x = max(0, width - total_width - 1)
			elif alignment == ALIGN_CENTER:
				x = max(0, (width - total_width) // 2)
			else:
				x = 1

			cursor = 0
			color = curses.color_pair(2) if idx == len(a2_lines) - 1 else curses.color_pair(3)
			for word_idx, (_, (text, _)) in enumerate(line):
				space_left = width - x - cursor - 1
				if space_left <= 0:
					break
				with contextlib.suppress(curses.error):
					lyrics_win.addstr(y, x + cursor, text[:space_left], color)
				cursor += word_widths[word_idx] + 1
			y += 1
		start_screen_line = start_line

	else:
		wrap_w = max(10, width - 2)

		if cache_invalid or not ds.wrapped_lines:
			wrapped, widths = [], []
			for orig_i, (_, ly) in enumerate(lyrics):
				if ly and ly.strip():
					lines = wrap_by_display_width(ly, wrap_w, subsequent_indent=' ')
					if lines:
						wrapped.append((orig_i, lines[0]))
						if lines[0] not in ds.widths_cache:
							ds.widths_cache[lines[0]] = wcswidth(lines[0])
						widths.append(ds.widths_cache[lines[0]])
						for cont in lines[1:]:
							wrapped.append((orig_i, cont))
							if cont not in ds.widths_cache:
								ds.widths_cache[cont] = wcswidth(cont)
							widths.append(ds.widths_cache[cont])
				else:
					wrapped.append((orig_i, ''))
					widths.append(0)
			ds.wrapped_lines = wrapped
			ds.wrapped_widths = widths
		else:
			wrapped, widths = ds.wrapped_lines, ds.wrapped_widths

		total = len(wrapped)
		avail = lyrics_area_height
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

		for i in range(avail):
			if start_screen_line + i >= total:
				break
			orig_i, line = wrapped[start_screen_line + i]
			txt = line.strip()[:width - 1]
			disp_width = widths[start_screen_line + i]

			if alignment == ALIGN_RIGHT:
				x = max(0, width - disp_width - 1)
			elif alignment == ALIGN_CENTER:
				x = max(0, (width - disp_width) // 2)
			else:
				x = 1

			color = (
				curses.color_pair(4) if orig_i == current_idx else curses.color_pair(5)
			) if is_txt_format else (
				curses.color_pair(2) if orig_i == current_idx else curses.color_pair(3)
			)
			with contextlib.suppress(curses.error):
				lyrics_win.addstr(i, x, txt, color)

		lyrics_win.noutrefresh()

	# 3) Time-adjust / end-of-lyrics bar
	adjust_win.erase()
	if current_idx is not None and current_idx == len(lyrics) - 1 and not is_txt_format and len(lyrics) > 1:
		with contextlib.suppress(curses.error):
			adjust_win.addstr(0, 0, " End of lyrics ", curses.color_pair(2) | curses.A_BOLD)
	elif time_adjust:
		adj_str = f" Offset: {time_adjust:+.1f}s "[:width - 1]
		with contextlib.suppress(curses.error):
			adjust_win.addstr(0, max(0, width - len(adj_str) - 1),
							  adj_str, curses.color_pair(2) | curses.A_BOLD)
	adjust_win.noutrefresh()

	# 4) Status bar
	status_win.erase()
	if config_manager.DISPLAY_NAME:
		if player_info:
			_, data = player_info
			artist = data[2] or ''
			file_basename = ''
			if data[0] and data[0] != "None":
				with contextlib.suppress(TypeError, AttributeError):
					file_basename = os.path.basename(data[0])
			title = data[3] or file_basename
			is_inst = any(x in title.lower() for x in ['instrumental', 'karaoke'])
		else:
			title, artist, is_inst = 'No track', '', False

		ps = f"{title} - {artist}"
		cur_line = min(current_idx + 1, len(lyrics)) if lyrics else 0
		adj_flag = '' if is_inst else ('[Adj] ' if time_adjust else '')
		icon = ' ⏳ ' if is_fetching else ' 🎵 '
		right_full = f"Line {cur_line}/{len(lyrics)}{adj_flag}"
		right_short = f" {cur_line}/{len(lyrics)}{adj_flag} "

		if len(f"{icon}{ps} • {right_full}") <= width - 1:
			display_line = f"{icon}{ps} • {right_full}"
		else:
			right = right_short
			left_max = width - 1 - len(right) - 1
			ps_t = f"{icon}{ps}"
			if len(ps_t) > left_max:
				trunc = max(0, left_max - 3)
				ps_t = ps_t[:trunc] + '...' if trunc > 0 else ''
			display_line = f"{ps_t}{' ' * max(left_max - len(ps_t), 0)} {right} "

		with contextlib.suppress(curses.error):
			status_win.addstr(0, 0, display_line[:max(0, width - 1)],
							  curses.color_pair(5) | curses.A_BOLD)
	else:
		info = f"Line {min(current_idx + 1, len(lyrics))}/{len(lyrics)}"
		if time_adjust:
			info += '[Adj]'
		with contextlib.suppress(curses.error):
			status_win.addstr(0, 0, info[:width - 1], curses.A_BOLD)

	status_msg = get_current_status(config_manager)
	if status_msg:
		msg = f"  [{status_msg}]  "[:width - 1]
		with contextlib.suppress(curses.error):
			status_win.addstr(0, max(0, (width - len(msg)) // 2),
							  msg, curses.color_pair(2) | curses.A_BOLD)
	status_win.noutrefresh()

	curses.doupdate()
	return start_screen_line


# ================
#  INPUT HANDLING
# ================
def parse_key_config(key_config):
	if isinstance(key_config, list):
		return [parse_single_key(k) for k in key_config]
	return [parse_single_key(key_config)]


def parse_single_key(key_str):
	if key_str.startswith("KEY_"):
		return getattr(curses, key_str, None)
	elif len(key_str) == 1:
		return ord(key_str)
	return None


def load_key_bindings(config):
	bindings = config.get("key_bindings", {})
	parsed = {}
	for action, key_config in bindings.items():
		keys = parse_key_config(key_config)
		parsed[action] = [k for k in keys if k is not None]

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
		if key not in parsed or not parsed[key]:
			parsed[key] = default
	return parsed


def update_display(stdscr, ds, lyrics, errors, position, manual_offset,
				   is_txt_format, is_a2_format, current_idx, manual_scroll_active,
				   time_adjust=0, is_fetching=False,
				   alignment='center', player_info=None, config_manager=None):
	use_manual = True if is_txt_format else manual_scroll_active
	return display_lyrics(
		stdscr, ds, lyrics, errors, position,
		manual_offset, is_txt_format, is_a2_format, current_idx,
		use_manual, time_adjust, is_fetching,
		alignment, player_info, config_manager
	)


# ================
#  SYNC UTILITIES
# ================
def find_current_lyric_index(position, timestamps):
	if not timestamps:
		return 0
	idx = bisect.bisect_left(timestamps, position)
	idx = max(0, min(idx, len(timestamps) - 1))
	if idx + 1 < len(timestamps):
		current_duration = timestamps[idx + 1] - timestamps[idx]
		position_in_line = position - timestamps[idx]
		if current_duration > 0 and (position_in_line / current_duration) > 0.95:
			return idx + 1
	return idx


def get_monitor_refresh_rate():
	try:
		xrandr_output = subprocess.check_output(["xrandr"]).decode()
		match = re.search(r"(\d+\.\d+)\*", xrandr_output)
		if match:
			return float(match.group(1))
	except Exception:
		pass
	return 60.0


# ================
#  MAIN APPLICATION
# ================

async def main_async(stdscr, config_manager, logger):
	# pylint: disable=duplicate-code
	log_debug = logger.log_debug
	log_info = logger.log_info
	perf = time.perf_counter
	path_exists = os.path.exists
	path_dirname = os.path.dirname
	max_func = max
	min_func = min
	int_func = int
	float_func = float
	abs_func = abs
	bisect_right = bisect.bisect_right

	stdscr_getch = stdscr.getch
	stdscr_timeout = stdscr.timeout
	stdscr_nodelay = stdscr.nodelay
	stdscr_keypad = stdscr.keypad
	stdscr_curs_set = curses.curs_set
	get_size = stdscr.getmaxyx

	config = config_manager.config
	ui_config = config["ui"]
	sync_config = ui_config["sync"]
	proximity_config = sync_config["proximity"]
	color_config = ui_config["colors"]

	refresh_interval = sync_config["refresh_interval_ms"] / 1000.0
	refresh_interval_2 = sync_config["coolcpu_ms"]
	smart_refresh_interval = sync_config["smart_coolcpu_ms"]
	refresh_proximity_interval_ms = sync_config.get("refresh_proximity_interval_ms", 200)
	jump_threshold = sync_config.get("jump_threshold_sec", 1.0)
	temporary_refresh_sec = sync_config["smart_refresh_duration"]
	smart_tracking = sync_config.get("smart-tracking", 0)
	smart_proximity = proximity_config.get("smart-proximity", False)
	proximity_threshold = sync_config.get("proximity_threshold", 0)
	proximity_threshold_sec = proximity_config.get("proximity_threshold_sec", 0.05)
	proximity_threshold_percent = proximity_config.get("proximity_threshold_percent", 0.05)
	proximity_min_threshold_sec = proximity_config.get("proximity_min_threshold_sec", 1.0)
	proximity_max_threshold_sec = proximity_config.get("proximity_max_threshold_sec", 2.0)
	end_trigger_sec = sync_config.get("end_trigger_threshold_sec", 1.0)
	scroll_timeout = ui_config["scroll_timeout"]
	base_offset = sync_config.get("sync_offset_sec", 0.0)
	vrr_enabled = sync_config.get("VRR_bol", False)

	curses.start_color()
	curses.init_pair(1, resolve_color(color_config["error"]), curses.COLOR_BLACK)
	curses.init_pair(2, resolve_color(color_config["lrc"]["active"]), curses.COLOR_BLACK)
	curses.init_pair(3, resolve_color(color_config["lrc"]["inactive"]), curses.COLOR_BLACK)
	curses.init_pair(4, resolve_color(color_config["txt"]["active"]), curses.COLOR_BLACK)
	curses.init_pair(5, resolve_color(color_config["txt"]["inactive"]), curses.COLOR_BLACK)

	raw_bindings = load_key_bindings(config)
	quit_keys = set(raw_bindings["quit"])
	scroll_up_keys = set(raw_bindings["scroll_up"])
	scroll_down_keys = set(raw_bindings["scroll_down"])
	time_decrease_keys = set(raw_bindings["time_decrease"])
	time_increase_keys = set(raw_bindings["time_increase"])
	time_reset_keys = set(raw_bindings["time_reset"])
	time_jump_increase_keys = set(raw_bindings.get("time_jump_increase", []))
	time_jump_decrease_keys = set(raw_bindings.get("time_jump_decrease", []))
	align_left_keys = set(raw_bindings["align_left"])
	align_center_keys = set(raw_bindings["align_center"])
	align_right_keys = set(raw_bindings["align_right"])
	align_cycle_forward_keys = set(raw_bindings["align_cycle_forward"])
	align_cycle_backward_keys = set(raw_bindings["align_cycle_backward"])

	alignments_list = (ALIGN_LEFT, ALIGN_CENTER, ALIGN_RIGHT)
	alignment_index = {ALIGN_LEFT: 0, ALIGN_CENTER: 1, ALIGN_RIGHT: 2}

	stdscr_curs_set(0)
	stdscr_nodelay(True)
	stdscr_keypad(True)
	stdscr_timeout(0)

	ds = DisplayState()

	current_title: Optional[str] = None
	current_artist: Optional[str] = None
	current_file: Optional[str] = None
	lyrics: list = []
	errors: list = []
	timestamps: list = []
	is_txt: bool = False
	is_a2: bool = False
	player_type: Optional[str] = None
	player_data: tuple = (None, 0, "", None, 0, STATUS_STOPPED)
	prev_player_data: tuple = (None, 0, "", None, 0, STATUS_STOPPED)
	p_audio_file: Optional[str] = None
	p_raw_pos: float = 0.0
	p_artist: str = ""
	p_title: Optional[str] = None
	p_duration: float = 0.0
	p_status: str = STATUS_STOPPED
	estimated_position: float = 0.0
	last_cmus_position: float = 0.0
	last_pos_time: float = perf()
	last_player_update: float = 0.0
	manual_offset: int = 0
	last_input: float = 0.0
	time_adjust: float = 0.0
	alignment: str = ui_config.get("alignment", ALIGN_CENTER).lower()
	last_idx: int = -1
	current_idx: int = -1
	force_redraw: bool = True
	resume_trigger_time: Optional[float] = None
	proximity_trigger_time: Optional[float] = None
	proximity_active: bool = False
	lyric_future: Any = None
	lyrics_loaded_time: Optional[float] = None
	end_triggered: bool = False
	manual_timeout_handled: bool = True
	window_size: tuple[int, int] = get_size()
	wrapped_lines: list = []
	max_wrapped_offset: int = 0
	playback_paused: bool = False
	poll: bool = False
	next_frame_time: float = 0.0
	frame_time: Optional[float] = None

	prev_window_width = window_size[1]

	with open(os.devnull, 'w') as _devnull, \
		 contextlib.redirect_stdout(_devnull), \
		 contextlib.redirect_stderr(_devnull):

		while True:
			current_time = perf()
			needs_redraw = False

			# Manual scroll timeout
			time_since_input = 0.0
			if last_input > 0.0:
				time_since_input = current_time - last_input
				if time_since_input >= scroll_timeout:
					if not manual_timeout_handled:
						needs_redraw = True
						manual_timeout_handled = True
					last_input = 0.0
				else:
					manual_timeout_handled = False

			manual_scroll = (last_input > 0.0)

			# Input handling
			key = stdscr_getch()
			new_input = key != -1

			if key == curses.KEY_RESIZE:
				new_size = get_size()
				if new_size != window_size:
					old_h, old_w = window_size
					new_h, new_w = new_size
					if old_w != new_w:
						ds.invalidate()
					if lyrics and old_h > 0 and new_h > 0:
						manual_offset = int_func(manual_offset * (new_h / old_h))
					window_size = new_size
					max_wrapped_offset = max_func(0, max_wrapped_offset)
					needs_redraw = True
			elif new_input:
				if key in quit_keys:
					try:
						atexit.register(THREAD_POOL_EXECUTOR.shutdown, wait=False)
					except NameError:
						pass
					sys.exit("Exiting")

				if key in scroll_up_keys:
					manual_offset = max_func(0, manual_offset - 1)
					last_input = current_time
					needs_redraw = True
				elif key in scroll_down_keys:
					manual_offset += 1
					last_input = current_time
					needs_redraw = True
				elif key in time_decrease_keys:
					time_adjust -= 0.1
					needs_redraw = True
				elif key in time_increase_keys:
					time_adjust += 0.1
					needs_redraw = True
				elif key in time_reset_keys:
					time_adjust = 0.0
					needs_redraw = True
				elif key in time_jump_increase_keys:
					time_adjust += 5.0
					needs_redraw = True
				elif key in time_jump_decrease_keys:
					time_adjust -= 5.0
					needs_redraw = True
				elif key in align_left_keys:
					alignment = ALIGN_LEFT
					needs_redraw = True
				elif key in align_center_keys:
					alignment = ALIGN_CENTER
					needs_redraw = True
				elif key in align_right_keys:
					alignment = ALIGN_RIGHT
					needs_redraw = True
				elif key in align_cycle_forward_keys:
					alignment = alignments_list[(alignment_index[alignment] + 1) % 3]
					needs_redraw = True
				elif key in align_cycle_backward_keys:
					alignment = alignments_list[(alignment_index[alignment] - 1) % 3]
					needs_redraw = True

				if needs_redraw:
					force_redraw = True

			# Smart refresh timing
			in_smart_window = (resume_trigger_time is not None and
							   (current_time - resume_trigger_time <= temporary_refresh_sec))
			if (player_type in (PLAYER_CMUS, PLAYER_PLAYERCTL) and
					in_smart_window and p_status == STATUS_PLAYING and lyrics):
				stdscr_timeout(int_func(smart_refresh_interval))
				poll = True
			else:
				stdscr_timeout(int_func(refresh_interval_2))
				poll = False

			# Player poll interval
			interval = 0.0 if in_smart_window else refresh_interval
			if proximity_active and p_status == STATUS_PLAYING:
				interval = refresh_interval

			if current_time - last_player_update >= interval:
				# Poll player (inlined)
				try:
					prev_status = p_status
					new_player_type, new_player_data = await get_player_info(config_manager)
					if new_player_type != player_type or new_player_data != player_data:
						player_type = new_player_type
						player_data = new_player_data

					_, raw_val, _, _, _, status_val = player_data
					new_raw = float_func(raw_val or 0.0)
					drift = abs_func(new_raw - estimated_position)

					if drift > jump_threshold and status_val == STATUS_PLAYING:
						resume_trigger_time = current_time
						log_debug(f"Jump detected: {drift:.3f}s")
						needs_redraw = True
						if smart_tracking == 1:
							last_idx = -1

					if player_type and prev_status == STATUS_PAUSED and status_val == STATUS_PLAYING:
						resume_trigger_time = current_time
						log_debug("Pause→play refresh")
						needs_redraw = True
						if smart_tracking == 1:
							last_idx = -1

					if smart_tracking == 1 and status_val == STATUS_PAUSED and drift > jump_threshold:
						resume_trigger_time = current_time
						log_debug(f"Paused jump detected: {drift:.3f}s")
						needs_redraw = True
						last_idx = -1

				except Exception as e:
					log_debug(f"Error polling player: {e}")

				last_player_update = current_time

			# Update player data if changed
			if player_data != prev_player_data:
				prev_player_data = player_data
				p_audio_file, p_raw_pos, p_artist, p_title, p_duration, p_status = player_data

				if p_audio_file in ("None", ""):
					p_audio_file = None
				p_raw_pos = float_func(p_raw_pos or 0.0)
				p_duration = float_func(p_duration or 0.0)
				estimated_position = p_raw_pos
				last_pos_time = current_time

				track_changed = ((p_title, p_artist, p_audio_file) !=
								 (current_title, current_artist, current_file) and
								 p_status != STATUS_STOPPED)
				if track_changed:
					log_info(f"New track: {p_title or 'Unknown'} – {p_artist or 'Unknown'}")
					current_title = p_title or ""
					current_artist = p_artist or ""
					current_file = p_audio_file

					# Cancel previous lyric fetch
					if lyric_future and not lyric_future.done():
						lyric_future.cancel()
						with contextlib.suppress(asyncio.CancelledError, Exception):
							await lyric_future
						lyric_future = None
						log_debug("Previous lyric task cancelled")

					lyrics = []
					errors = []
					last_idx = -1
					force_redraw = True
					is_txt = False
					is_a2 = False
					lyrics_loaded_time = None
					wrapped_lines = []
					max_wrapped_offset = 0
					end_triggered = False

					search_directory = None
					if (p_audio_file and path_exists(p_audio_file) and
							player_type in (PLAYER_CMUS, PLAYER_MPD)):
						search_directory = path_dirname(p_audio_file)

					if current_title and current_artist:
						lyric_future = asyncio.create_task(
							fetch_lyrics_async(
								audio_file=p_audio_file,
								directory=search_directory,
								artist=current_artist or "",
								title=current_title or "",
								duration=p_duration,
								config_manager=config_manager,
								logger=logger,
							)
						)
						log_debug(f"Lyric task started: {p_artist} - {p_title}")

					last_cmus_position = p_raw_pos
					estimated_position = p_raw_pos

			# Collect finished lyric task
			if lyric_future and lyric_future.done():
				try:
					(new_lyrics, new_errors), new_is_txt, new_is_a2 = lyric_future.result()
					if new_errors:
						log_debug(str(new_errors))
					lyrics = new_lyrics
					errors = new_errors
					is_txt = new_is_txt
					is_a2 = new_is_a2
					last_idx = -1
					force_redraw = True
					lyrics_loaded_time = current_time
					wrapped_lines = []
					max_wrapped_offset = 0
					if not (is_txt or is_a2):
						timestamps = sorted(t for t, _ in lyrics if t is not None)
					else:
						timestamps = []
					if p_status == STATUS_PLAYING and player_type in (PLAYER_CMUS, PLAYER_MPD):
						resume_trigger_time = current_time
					estimated_position = p_raw_pos
				except (asyncio.CancelledError, Exception) as e:
					if not isinstance(e, asyncio.CancelledError):
						log_debug(f"Lyric load error: {e}")
					errors = [f"Lyric load error: {e}"]
					force_redraw = True
					lyrics_loaded_time = current_time
				finally:
					lyric_future = None

			if lyrics_loaded_time and (current_time - lyrics_loaded_time >= 2.0):
				force_redraw = True
				lyrics_loaded_time = None

			# Position estimation
			playback_paused = (p_status == STATUS_PAUSED)
			if p_raw_pos != last_cmus_position and not playback_paused:
				last_cmus_position = p_raw_pos
				last_pos_time = current_time
				estimated_position = p_raw_pos

			if player_type:
				if not playback_paused:
					pos = p_raw_pos + (current_time - last_pos_time)
					estimated_position = min_func(pos, p_duration)
				else:
					estimated_position = p_raw_pos

			continuous_position = max_func(
				0.0,
				min_func(estimated_position + time_adjust + base_offset, p_duration)
			)

			# End‑of‑track trigger
			if (p_duration > 0.0 and
					(p_duration - continuous_position) <= end_trigger_sec and
					not end_triggered):
				end_triggered = True
				force_redraw = True
				log_debug(f"End-of-track (pos={continuous_position:.3f}s)")

			# Proximity smart refresh
			if p_status != STATUS_PLAYING:
				proximity_active = False
				proximity_trigger_time = None

			if (smart_proximity and timestamps and not is_txt and
					last_idx >= 0 and last_idx + 1 < len(timestamps) and
					p_status == STATUS_PLAYING and not poll and not playback_paused):

				idx = last_idx
				ts = timestamps
				line_duration = ts[idx + 1] - ts[idx]
				raw_thresh = max_func(
					line_duration * (proximity_threshold_percent / 100),
					proximity_threshold_sec
				)
				threshold = min_func(
					max_func(raw_thresh, proximity_min_threshold_sec),
					min_func(proximity_max_threshold_sec, line_duration)
				)
				time_to_next = min_func(line_duration, max_func(0.0, ts[idx + 1] - continuous_position))

				if proximity_min_threshold_sec <= time_to_next <= threshold:
					proximity_trigger_time = current_time
					proximity_active = True
					stdscr_timeout(refresh_proximity_interval_ms)
					last_player_update = 0.0
				elif (proximity_trigger_time is not None and
					  (time_to_next < proximity_min_threshold_sec or
					   time_to_next > threshold or
					   current_time - proximity_trigger_time > threshold)):
					stdscr_timeout(int_func(refresh_interval_2))
					proximity_trigger_time = None
					proximity_active = False
				else:
					proximity_active = False
			else:
				proximity_active = False

			# Wrapped‑line computation for .txt
			if is_txt and (not wrapped_lines or prev_window_width != window_size[1]):
				wrap_width = max_func(10, window_size[1] - 2)
				wrapped = []
				for orig_idx, (_, lyric) in enumerate(lyrics):
					if lyric and lyric.strip():
						for ln in wrap_by_display_width(lyric, wrap_width, subsequent_indent=' '):
							wrapped.append((orig_idx, ln))
					else:
						wrapped.append((orig_idx, ""))
				wrapped_lines = wrapped
				max_wrapped_offset = max_func(0, len(wrapped_lines) - (window_size[0] - 3))
				prev_window_width = window_size[1]

			# Lyric index
			if is_txt and wrapped_lines and p_duration > 0.0:
				num_wrapped = len(wrapped_lines)
				target = int_func((continuous_position / p_duration) * num_wrapped)
				current_idx = max_func(0, min_func(target, num_wrapped - 1))
			elif not timestamps or is_txt:
				current_idx = -1
			elif smart_tracking == 1:
				idx = last_idx
				n = len(timestamps)
				if idx < 0:
					idx = bisect_right(timestamps, continuous_position) - 1
					idx = max_func(-1, min_func(idx, n - 1))
				elif idx + 1 < n and continuous_position >= timestamps[idx + 1] - proximity_threshold:
					idx += 1
				current_idx = max_func(-1, min_func(idx, n - 1))
			else:
				idx = bisect_right(timestamps, continuous_position) - 1
				current_idx = idx if idx >= 0 else -1

			# Auto‑scroll for txt
			if last_input == 0 and not manual_scroll:
				if is_txt and wrapped_lines:
					ideal = current_idx - ((window_size[0] - 3) // 2)
					target = max_func(0, min_func(ideal, max_wrapped_offset))
					if target != manual_offset:
						manual_offset = target
						needs_redraw = True

			# VRR frame gate
			skip_for_vrr = False
			if vrr_enabled and frame_time is not None:
				if current_time < next_frame_time:
					skip_for_vrr = True
				else:
					skip_for_vrr = False
					next_frame_time += frame_time
					while next_frame_time < current_time:
						next_frame_time += frame_time
				if current_idx != last_idx or force_redraw:
					skip_for_vrr = False

			# Render
			should_render = (new_input or needs_redraw or force_redraw or current_idx != last_idx) and not skip_for_vrr
			if should_render:
				log_debug(
					f"Render: new_input={new_input} needs={needs_redraw} "
					f"force={force_redraw} idx={last_idx}→{current_idx}"
				)
				display_data = wrapped_lines if is_txt else lyrics
				start_screen_line = update_display(
					stdscr, ds,
					display_data, errors,
					continuous_position,
					manual_offset,
					is_txt, is_a2,
					current_idx,
					manual_scroll,
					time_adjust,
					lyric_future is not None,
					alignment=alignment,
					player_info=(player_type, player_data),
					config_manager=config_manager,
				)
				manual_offset = start_screen_line
				last_idx = current_idx
				force_redraw = False

			# Sleep timeout
			if playback_paused and not manual_scroll:
				if time_since_input > 5.0:
					stdscr_timeout(400)
					sleep_time = 0.004
				elif time_since_input > 2.0:
					stdscr_timeout(300)
					sleep_time = 0.003
				else:
					stdscr_timeout(250)
					sleep_time = 0.002
			else:
				stdscr_timeout(int_func(refresh_interval_2))
				sleep_time = 0.0

			if poll or proximity_active or manual_scroll:
				sleep_time = 0.0

			await asyncio.sleep(sleep_time)


def main(stdscr, *_: Any) -> None:
	cli_args = parse_args()
	config_manager = ConfigManager(
		config_path=cli_args.config,
		use_default=cli_args.default,
		player_override=cli_args.player,
	)
	logger = Logger(config_manager)
	asyncio.run(main_async(stdscr, config_manager, logger))


def shutdown():
	THREAD_POOL_EXECUTOR.shutdown(wait=False)


if __name__ == "__main__":
	args = parse_args()
	atexit.register(shutdown)
	try:
		curses.wrapper(main)
	except KeyboardInterrupt:
		print("Exited by user (Ctrl+C).")
		with contextlib.suppress(Exception):
			shutdown()
	except Exception as exc:
		with contextlib.suppress(Exception):
			temp_config = ConfigManager(
				config_path=args.config,
				use_default=args.default,
				player_override=args.player,
			)
			Logger(temp_config).log_error(f"Fatal error: {str(exc)}")
		print(f"Fatal error: {exc}", file=sys.stderr)
		time.sleep(1)
