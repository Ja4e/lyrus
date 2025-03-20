import curses
import subprocess
import re
import os
import bisect
import time
import textwrap
import requests
import urllib.parse
import syncedlyrics 
import multiprocessing
from datetime import datetime, timedelta
from typing import List, Tuple, Optional, Dict

# ========================
#      Configuration
# ========================
class Config:
	LYRICS_TIMEOUT_LOG = "logs/lyrics_timeouts.log"
	DEBUG_LOG = "logs/debug.log"
	CACHE_DIR = "synced_lyrics"
	LOG_RETENTION_DAYS = 10
	LYRIC_SOURCES = ['syncedlyrics', 'lrclib']
	FETCH_TIMEOUT = 15
	SCROLL_DECAY = 2.0

# ========================
#        Logging
# ========================
class Logger:
	@staticmethod
	def _ensure_dir(path: str):
		os.makedirs(os.path.dirname(path), exist_ok=True)

	@classmethod
	def log(cls, message: str, level: str = "DEBUG"):
		if level == "DEBUG" and os.environ.get('CMUS_LYRIC_DEBUG') != '1':
			return
			
		entry = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {message}\n"
		path = Config.DEBUG_LOG if level == "DEBUG" else Config.LYRICS_TIMEOUT_LOG
		
		try:
			cls._ensure_dir(path)
			with open(path, 'a', encoding='utf-8') as f:
				f.write(entry)
			cls._clean_logs(path)
		except Exception as e:
			pass

	@staticmethod
	def _clean_logs(path: str):
		if not os.path.exists(path):
			return

		with open(path, 'r+', encoding='utf-8') as f:
			lines = f.readlines()
			cutoff = datetime.now() - timedelta(days=Config.LOG_RETENTION_DAYS)
			
			if path == Config.LYRICS_TIMEOUT_LOG:
				valid = [ln for ln in lines if datetime.strptime(ln.split(' | ')[0], "%Y-%m-%d %H:%M:%S") >= cutoff]
			else:
				valid = lines[-100:]  # Keep last 100 debug lines
				
			f.seek(0)
			f.writelines(valid)
			f.truncate()

# ========================
#    Lyrics Management
# ========================
class LyricManager:
	def __init__(self):
		self.cache = LyricCache()
		self.fetcher = LyricFetcher()

	def get_lyrics(self, audio_path: str, artist: str, title: str, duration: int) -> Tuple[List, List]:
		# Check local files first
		if lyric_file := self._find_local_lyrics(audio_path, artist, title):
			return LyricParser.load(lyric_file)
		
		# Check instrumental metadata
		if self._is_instrumental(artist, title):
			return [("[Instrumental]", None)], []
			
		# Fetch from remote sources
		lyrics = self.fetcher.fetch(artist, title, duration)
		if lyrics:
			path = self.cache.save(lyrics, title, artist, self._get_extension(lyrics))
			return LyricParser.load(path)
			
		return [], ["No lyrics found"]

	def _find_local_lyrics(self, audio_path: str, artist: str, title: str) -> Optional[str]:
		base = os.path.splitext(os.path.basename(audio_path))[0]
		directory = os.path.dirname(audio_path)
		
		for ext in ['a2', 'lrc', 'txt']:
			path = os.path.join(directory, f"{base}.{ext}")
			if os.path.exists(path) and self._validate_lyrics_file(path, artist, title):
				return path
		return None

	def _validate_lyrics_file(self, path: str, artist: str, title: str) -> bool:
		try:
			with open(path, 'r', encoding='utf-8') as f:
				content = f.read()
				return self._validate_content(content, artist, title)
		except Exception as e:
			Logger.log(f"Validation failed for {path}: {str(e)}")
			return False

	@staticmethod
	def _validate_content(content: str, artist: str, title: str) -> bool:
		if re.search(r'(instrumental|\d+:\d+\.\d+)', content, re.I):
			return True
			
		norm = lambda s: re.sub(r'\W+', '', str(s).lower())
		return (SequenceMatcher(None, norm(content), norm(title)).ratio() > 0.6 or
				SequenceMatcher(None, norm(content), norm(artist)).ratio() > 0.5)

	@staticmethod
	def _is_instrumental(artist: str, title: str) -> bool:
		return any("instrumental" in s.lower() for s in [artist, title] if s)

	@staticmethod
	def _get_extension(content: str) -> str:
		if re.search(r'<\d+:\d+\.\d+>', content):
			return 'a2'
		return 'lrc' if re.search(r'\[\d+:\d+\.\d+\]', content) else 'txt'

class LyricFetcher:
	def fetch(self, artist: str, title: str, duration: int) -> Optional[str]:
		for source in Config.LYRIC_SOURCES:
			if lyrics := getattr(self, f"_fetch_{source}")(artist, title, duration):
				if self._validate_content(lyrics, artist, title):
					return lyrics
		return None

	def _fetch_syncedlyrics(self, artist: str, title: str, duration: int) -> Optional[str]:
		search_term = f"{title} {artist}".strip()
		queue = multiprocessing.Queue()
		
		def worker():
			try: queue.put(syncedlyrics.search(search_term))
			except: queue.put(None)
			
		return self._run_with_timeout(worker, queue, artist, title)

	def _fetch_lrclib(self, artist: str, title: str, duration: int) -> Optional[str]:
		try:
			params = {'artist_name': artist, 'track_name': title}
			if duration: params['duration'] = duration
			
			resp = requests.get("https://lrclib.net/api/get", params=params, timeout=10)
			if resp.status_code == 200:
				data = resp.json()
				return data.get('syncedLyrics') or data.get('plainLyrics')
		except Exception as e:
			Logger.log(f"LRCLib error: {str(e)}")
		return None

	def _run_with_timeout(self, worker, queue, artist: str, title: str) -> Optional[str]:
		proc = multiprocessing.Process(target=worker)
		proc.start()
		proc.join(Config.FETCH_TIMEOUT)
		
		if proc.is_alive():
			proc.terminate()
			proc.join()
			Logger.log(f"Timeout fetching {artist} - {title}", "TIMEOUT")
			return None
			
		return queue.get() if not queue.empty() else None

class LyricCache:
	def save(self, content: str, title: str, artist: str, ext: str) -> str:
		os.makedirs(Config.CACHE_DIR, exist_ok=True)
		sanitize = lambda s: re.sub(r'[<>:"/\\|?*]', '_', s)
		filename = f"{sanitize(title)}_{sanitize(artist or 'Unknown')}.{ext}"
		path = os.path.join(Config.CACHE_DIR, filename)
		
		try:
			with open(path, 'w', encoding='utf-8') as f:
				f.write(content)
			return path
		except Exception as e:
			Logger.log(f"Cache save failed: {str(e)}")
			return ""

# ========================
#       Lyrics Parsing
# ========================
class LyricParser:
	@staticmethod
	def load(path: str) -> Tuple[List, List]:
		try:
			with open(path, 'r', encoding='utf-8') as f:
				return LyricParser.parse(f.readlines(), path.endswith('.a2')), []
		except Exception as e:
			return [], [str(e)]

	@staticmethod
	def parse(lines: List[str], is_a2: bool) -> List[Tuple]:
		lyrics = []
		current_line = []
		
		for line in lines:
			line = line.strip()
			if not line:
				continue
				
			if is_a2:
				if line.startswith('['):
					if current_line:
						lyrics.extend(current_line)
						current_line = []
					time_str = line[1:line.index(']')]
					lyrics.append((LyricParser.time_to_sec(time_str), None))
				else:
					for match in re.finditer(r'<(\d+:\d+\.\d+)>(.*?)<(\d+:\d+\.\d+)>', line):
						start, text, end = match.groups()
						current_line.append((
							LyricParser.time_to_sec(start),
							(text.strip(), LyricParser.time_to_sec(end))
						))
			else:
				if line.startswith('['):
					time_str = line[1:line.index(']')]
					lyrics.append((LyricParser.time_to_sec(time_str), line.split(']', 1)[1].strip()))
				else:
					lyrics.append((None, line))
		return lyrics

	@staticmethod
	def time_to_sec(time_str: str) -> float:
		try:
			mins, secs = time_str.split(':', 1)
			secs = secs.split('.', 1)[0]  # Ignore milliseconds
			return int(mins)*60 + int(secs)
		except:
			return 0.0

# ========================
#      Cmus Handler
# ========================
class CmusHandler:
	def get_status(self) -> Tuple:
		try:
			output = subprocess.run(['cmus-remote', '-Q'], 
								   capture_output=True, text=True, check=True).stdout
			return self._parse_output(output)
		except subprocess.CalledProcessError:
			return None, 0, None, None, 0, "stopped"

	def _parse_output(self, output: str) -> Tuple:
		status = {'file': None, 'position': 0, 'artist': None, 
				 'title': None, 'duration': 0, 'status': 'stopped'}
		
		for line in output.splitlines():
			parts = line.split(maxsplit=1)
			if not parts: continue
			
			key = parts[0]
			if key in ['file', 'tag artist', 'tag title', 'status']:
				status[key.replace('tag ', '')] = parts[1] if len(parts) > 1 else None
			elif key in ['position', 'duration'] and len(parts) > 1:
				status[key] = int(parts[1]) if parts[1].isdigit() else 0
				
		return (status['file'], status['position'], status['artist'],
				status['title'], status['duration'], status['status'])

# ========================
#      Display Manager
# ========================
class DisplayManager:
	def __init__(self, stdscr):
		self.stdscr = stdscr
		self.height, self.width = 0, 0
		self._init_colors()
		curses.curs_set(0)
		stdscr.timeout(100)

	def _init_colors(self):
		curses.start_color()
		curses.init_pair(1, curses.COLOR_GREEN, curses.COLOR_BLACK)
		curses.init_pair(2, curses.COLOR_WHITE, curses.COLOR_BLACK)

	def update(self, lyrics: List, position: float, time_adjust: float, 
			  current_idx: int, scroll_offset: int, status: Dict):
		self._handle_resize()
		self.stdscr.erase()
		
		if lyrics and any(t for t,_ in lyrics):  # Synced lyrics
			self._draw_synced(lyrics, position + time_adjust, current_idx)
		else:
			self._draw_plain(lyrics, scroll_offset, current_idx)
			
		self._draw_status(status, time_adjust)
		self.stdscr.refresh()

	def _draw_synced(self, lyrics: List, position: float, current_idx: int):
		idx = bisect.bisect_left([t for t,_ in lyrics if t is not None], position) - 1
		idx = max(0, min(idx, len(lyrics)-1))
		
		start = max(0, idx - self.height//2)
		for i, (t, line) in enumerate(lyrics[start:start+self.height]):
			color = 1 if i+start == idx else 2
			self._safe_addstr(i, 0, line, color)

	def _draw_plain(self, lyrics: List, offset: int, current_idx: int):
		wrapped = []
		for idx, (_, line) in enumerate(lyrics):
			wrapped.extend([(idx, l) for l in textwrap.wrap(line, self.width-2)])
			
		start = max(0, min(offset, len(wrapped)-self.height))
		for i, (orig_idx, line) in enumerate(wrapped[start:start+self.height]):
			color = 1 if orig_idx == current_idx else 2
			self._safe_addstr(i, (self.width-len(line))//2, line, color)

	def _draw_status(self, status: Dict, adjust: float):
		parts = [
			status['state'].upper(),
			f"{status['position']}/{status['duration']}s",
			f"Line {status['current_line']}/{status['total_lines']}",
			f"Adj: {adjust:+.1f}" if adjust else None,
			f"{status['artist']} - {status['title']}"
		]
		status_line = " | ".join(p for p in parts if p)
		self._safe_addstr(self.height-1, 0, status_line[:self.width-1], 2, curses.A_REVERSE)

	def _safe_addstr(self, y: int, x: int, text: str, color: int, attr=0):
		if y < self.height and x < self.width:
			try: self.stdscr.addstr(y, x, text[:self.width-x], curses.color_pair(color) | attr)
			except: pass

	def _handle_resize(self):
		new_h, new_w = self.stdscr.getmaxyx()  # Assign new_h and new_w
		if (new_h, new_w) != self.stdscr.getmaxyx():  
			self.stdscr.clear()
			self.stdscr.refresh()


	def get_input(self) -> int:
		return self.stdscr.getch()

# ========================
#    Main Application
# ========================
class LyricDisplay:
	def __init__(self):
		self.cmus = CmusHandler()
		self.lyrics = LyricManager()
		self.state = {
			'file': None,
			'lyrics': [],
			'position': 0,
			'scroll': 0,
			'adjust': 0.0,
			'last_input': 0.0
		}

	def run(self, stdscr):
		display = DisplayManager(stdscr)
		while True:
			self._update_state()
			self._handle_input(display.get_input())
			display.update(
				lyrics=self.state['lyrics'],
				position=self.state['position'],
				time_adjust=self.state['adjust'],
				current_idx=self._current_index(),
				scroll_offset=self.state['scroll'],
				status=self._status_info()
			)
			time.sleep(0.02)

	def _update_state(self):
		file, pos, artist, title, dur, status = self.cmus.get_status()
		
		if file != self.state['file']:
			self.state.update({
				'file': file,
				'lyrics': self.lyrics.get_lyrics(file, artist, title, dur)[0],
				'scroll': 0,
				'last_input': 0.0
			})
			
		self.state.update({
			'position': pos + (time.time() - self.state.get('pos_time', 0)) 
					   if status == 'playing' else pos,
			'pos_time': time.time() if status == 'playing' else self.state.get('pos_time', 0),
			'status': status
		})

	def _handle_input(self, key: int):
		if key == -1:
			if time.time() - self.state['last_input'] > Config.SCROLL_DECAY:
				self.state['scroll'] = 0
			return

		self.state['last_input'] = time.time()
		
		if key == ord('q'): raise KeyboardInterrupt
		if key == ord('r'): self.state['file'] = None  # Trigger reload
		if key == ord('+'): self.state['adjust'] += 0.5
		if key == ord('-'): self.state['adjust'] -= 0.5
		if key == curses.KEY_UP: self.state['scroll'] = max(0, self.state['scroll']-1)
		if key == curses.KEY_DOWN: self.state['scroll'] += 1

	def _current_index(self) -> int:
		times = [t for t,_ in self.state['lyrics'] if t is not None]
		return bisect.bisect_left(times, self.state['position'] + self.state['adjust']) - 1

	def _status_info(self) -> Dict:
		file = self.state['file'] or "Unknown"
		return {
			'artist': self.state.get('artist', 'Unknown'),
			'title': os.path.splitext(os.path.basename(file))[0],
			'duration': self.state.get('duration', 0),
			'position': int(self.state['position']),
			'current_line': self._current_index() + 1,
			'total_lines': len(self.state['lyrics']),
			'state': self.state.get('status', 'stopped')
		}

if __name__ == "__main__":
	try:
		curses.wrapper(LyricDisplay().run)
	except KeyboardInterrupt:
		pass
