import curses
import subprocess
import re
import os
import bisect
import time
import textwrap
import syncedlyrics 
import multiprocessing
from datetime import datetime, timedelta
import requests
from urllib.parse import quote

class LyricsLogger:
	LYRICS_TIMEOUT_LOG = "lyrics_timeouts.log"
	LOG_RETENTION_DAYS = 10

	def __init__(self):
		self.log_dir = os.path.join(os.getcwd(), "logs")
		os.makedirs(self.log_dir, exist_ok=True)

	def log_timeout(self, artist, title):
		timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
		log_entry = f"{timestamp} | Artist: {artist or 'Unknown'} | Title: {title or 'Unknown'}\n"
		log_path = os.path.join(self.log_dir, self.LYRICS_TIMEOUT_LOG)
		
		try:
			with open(log_path, 'a', encoding='utf-8') as f:
				f.write(log_entry)
			self.clean_old_timeouts()
		except Exception as e:
			pass

	def clean_old_timeouts(self):
		log_path = os.path.join(self.log_dir, self.LYRICS_TIMEOUT_LOG)
		if not os.path.exists(log_path):
			return

		cutoff = datetime.now() - timedelta(days=self.LOG_RETENTION_DAYS)
		new_lines = []

		try:
			with open(log_path, 'r', encoding='utf-8') as f:
				for line in f:
					parts = line.split(' | ', 1)
					if len(parts) < 1:
						continue
					try:
						entry_time = datetime.strptime(parts[0], "%Y-%m-%d %H:%M:%S")
						if entry_time >= cutoff:
							new_lines.append(line)
					except ValueError:
						continue

			with open(log_path, 'w', encoding='utf-8') as f:
				f.writelines(new_lines)
		except Exception:
			pass

class CmusPlayer:
	def __init__(self):
		self.current_file = None
		self.artist = None
		self.title = None
		self.duration = 0
		self.status = "stopped"
		self._position = 0
		self._last_update = time.time()
		self._playback_paused = False

	def update(self):
		try:
			result = subprocess.run(['cmus-remote', '-Q'], 
								  stdout=subprocess.PIPE, 
								  stderr=subprocess.PIPE,
								  timeout=0.1)
			output = result.stdout.decode('utf-8')
		except:
			self._handle_disconnect()
			return

		new_file = None
		new_pos = 0
		new_dur = 0
		new_status = "stopped"

		for line in output.split('\n'):
			line = line.strip()
			if line.startswith('file '):
				new_file = line[5:]
			elif line.startswith('tag artist '):
				self.artist = line[11:]
			elif line.startswith('tag title '):
				self.title = line[10:]
			elif line.startswith('duration '):
				new_dur = int(line.split()[1])
			elif line.startswith('position '):
				new_pos = int(line.split()[1])
			elif line.startswith('status '):
				new_status = line.split()[1]

		now = time.time()
		self._update_position(new_pos, new_status, new_dur, now)
		self._update_track_state(new_file, new_dur, new_status)
		self.current_file = new_file
		self.duration = new_dur
		self.status = new_status

	def _handle_disconnect(self):
		self.current_file = None
		self.artist = None
		self.title = None
		self.duration = 0
		self.status = "stopped"
		self._position = 0

	def _update_track_state(self, new_file, new_dur, status):
		if new_file != self.current_file:
			self._position = 0
			self.duration = new_dur
			self._last_update = time.time()
			self._playback_paused = False

	def _update_position(self, new_pos, status, duration, now):
		if new_pos != self._position:
			self._position = new_pos
			self._last_update = now
			self._playback_paused = (status == "paused")

		if status == "playing" and not self._playback_paused:
			elapsed = now - self._last_update
			self._position = min(new_pos + elapsed, duration)
		else:
			self._position = new_pos

	@property
	def position(self):
		return max(0, min(self._position, self.duration))

class LyricsManager:
	def __init__(self):
		self.lyrics = []
		self.errors = []
		self.current_file = None
		self.is_txt = False
		self.is_a2 = False
		self.logger = LyricsLogger()

	@staticmethod
	def sanitize_filename(name):
		return re.sub(r'[<>:"/\\|?*]', '_', name)

	@staticmethod
	def sanitize_string(s):
		return re.sub(r'[^a-zA-Z0-9]', '', str(s)).lower()

	def load_lyrics(self, audio_file, artist, title, duration):
		if not audio_file or audio_file == self.current_file:
			return

		self.current_file = audio_file
		self.lyrics = []
		self.errors = []
		self.is_txt = False
		self.is_a2 = False

		lyrics_file = self._find_lyrics_file(audio_file, artist, title, duration)
		if lyrics_file:
			self._parse_lyrics_file(lyrics_file)

	def _find_lyrics_file(self, audio_file, artist, title, duration):
		base_name = os.path.splitext(os.path.basename(audio_file))[0]
		search_dirs = [
			os.path.dirname(audio_file),
			os.path.join(os.getcwd(), "synced_lyrics")
		]

		local_files = self._check_local_files(base_name, search_dirs)
		if local_files:
			return local_files

		if self._is_instrumental(artist, title):
			return self._save_instrumental(artist, title)

		lyrics_content, is_synced = self._fetch_online_lyrics(artist, title, duration)
		if lyrics_content:
			return self._save_lyrics(lyrics_content, title, artist, is_synced)

		return None

	def _check_local_files(self, base_name, search_dirs):
		for dir_path in search_dirs:
			for ext in ['a2', 'lrc', 'txt']:
				file_path = os.path.join(dir_path, f"{base_name}.{ext}")
				if os.path.exists(file_path):
					return file_path
		return None

	def _is_instrumental(self, artist, title):
		title_clean = self.sanitize_string(title)
		artist_clean = self.sanitize_string(artist)
		return ("instrumental" in title_clean) or ("instrumental" in artist_clean)

	def _save_instrumental(self, artist, title):
		content = "[Instrumental]"
		return self._save_lyrics(content, title, artist, False)

	def _fetch_online_lyrics(self, artist, title, duration, timeout=15):
		def worker(queue, search_term):
			try:
				lyrics = syncedlyrics.search(search_term)
				queue.put(lyrics)
			except Exception:
				queue.put(None)

		search_term = f"{title} {artist}".strip()
		if not search_term:
			return None, None

		queue = multiprocessing.Queue()
		process = multiprocessing.Process(target=worker, args=(queue, search_term))
		process.start()
		process.join(timeout)

		if process.is_alive():
			process.terminate()
			process.join()
			self.logger.log_timeout(artist, title)
			return None, None

		lyrics = queue.get() if not queue.empty() else None
		if not lyrics or not self._validate_lyrics(lyrics, artist, title):
			return None, None

		is_synced = any(re.match(r'^\[\d+:\d+\.\d+\]', line) for line in lyrics.split('\n'))
		return lyrics, is_synced

	def _validate_lyrics(self, content, artist, title):
		if re.search(r'\[\d+:\d+\.\d+\]', content):
			return True
			
		if re.search(r'\b(instrumental)\b', content, re.IGNORECASE):
			return True

		norm_title = self.sanitize_string(title)[:15]
		norm_artist = self.sanitize_string(artist)[:15] if artist else ''
		norm_content = self.sanitize_string(content)

		return norm_title in norm_content or norm_artist in norm_content

	def _save_lyrics(self, content, title, artist, is_synced):
		folder = os.path.join(os.getcwd(), "synced_lyrics")
		os.makedirs(folder, exist_ok=True)
		
		sanitized_title = self.sanitize_filename(title)
		sanitized_artist = self.sanitize_filename(artist)
		is_enhanced = any(re.search(r'<\d+:\d+\.\d+>', line) for line in content.split('\n'))
		
		ext = 'a2' if is_enhanced else 'lrc' if is_synced else 'txt'
		filename = f"{sanitized_title}_{sanitized_artist}.{ext}"
		file_path = os.path.join(folder, filename)
		
		try:
			with open(file_path, "w", encoding="utf-8") as f:
				f.write(content)
			return file_path
		except Exception:
			return None

	def _parse_lyrics_file(self, file_path):
		self.lyrics = []
		self.errors = []
		self.is_a2 = file_path.endswith('.a2')
		self.is_txt = file_path.endswith('.txt')

		try:
			with open(file_path, 'r', encoding='utf-8') as f:
				lines = f.readlines()
		except Exception as e:
			self.errors.append(f"File error: {str(e)}")
			return

		if self.is_a2:
			self._parse_a2_format(lines)
		else:
			self._parse_standard_format(lines, file_path.endswith('.lrc'))

	def _parse_a2_format(self, lines):
		current_line = []
		for line in lines:
			line = line.strip()
			if not line:
				if current_line:
					self.lyrics.append((None, None))
					current_line = []
				continue

			line_match = re.match(r'^\[(\d{2}:\d{2}\.\d{2})\](.*)', line)
			if line_match:
				if current_line:
					self.lyrics.append((None, None))
				line_time = self._parse_time(line_match.group(1))
				self.lyrics.append((line_time, None))
				self._parse_a2_words(line_match.group(2), line_time)
				self.lyrics.append((line_time, None))
			elif current_line:
				self._parse_a2_words(line, None)

	def _parse_a2_words(self, content, line_time):
		word_pattern = re.compile(r'<(\d{2}:\d{2}\.\d{2})>(.*?)<(\d{2}:\d{2}\.\d{2})>')
		words = word_pattern.findall(content)
		for start_str, text, end_str in words:
			start = self._parse_time(start_str)
			end = self._parse_time(end_str)
			clean_text = re.sub(r'<.*?>', '', text).strip()
			if clean_text:
				self.lyrics.append((start, (clean_text, end)))

	def _parse_standard_format(self, lines, is_lrc):
		for idx, line in enumerate(lines):
			raw_line = line.rstrip('\n')
			if not raw_line.strip():
				self.lyrics.append((None, ""))
				continue

			if is_lrc:
				line_match = re.match(r'\[(\d+:\d+\.\d+)\](.*)', raw_line)
				if line_match:
					line_time = self._parse_time(line_match.group(1))
					lyric_content = line_match.group(2)
					self.lyrics.append((line_time, lyric_content.strip()))
					continue

			self.lyrics.append((None, raw_line))

	@staticmethod
	def _parse_time(time_str):
		try:
			if '.' in time_str:
				mins, secs = time_str.split('.', 1)
				return int(mins) * 60 + float(f"0.{secs}")
			return int(time_str) * 60
		except:
			return 0

class LyricsDisplay:
	def __init__(self, stdscr):
		self.stdscr = stdscr
		self.manual_offset = 0
		self.time_adjust = 0.0
		self.last_input_time = None
		self.current_idx = -1
		self.start_line = 0
		self._setup_curses()

	def _setup_curses(self):
		curses.start_color()
		curses.init_pair(1, curses.COLOR_RED, curses.COLOR_BLACK)
		curses.init_pair(2, curses.COLOR_GREEN, curses.COLOR_BLACK)
		curses.init_pair(3, curses.COLOR_WHITE, curses.COLOR_BLACK)
		curses.curs_set(0)
		self.stdscr.nodelay(True)
		self.stdscr.timeout(50)

	def render(self, lyrics, position, is_txt, is_a2, manual_scroll_active):  # Add parameter
		self.stdscr.erase()
		height, width = self.stdscr.getmaxyx()
		self.current_idx = bisect.bisect_right([t for t, _ in lyrics if t is not None], position) - 1
		
		if is_a2:
			self._render_a2_lyrics(lyrics, position, height, width)
		else:
			self._render_standard_lyrics(lyrics, position, height, width, is_txt, manual_scroll_active)

		self._render_status(position, height, width)
		self.stdscr.refresh()

	def _render_a2_lyrics(self, lyrics, position, height, width):
		a2_lines = []
		current_line = []
		for t, item in lyrics:
			if item is None:
				if current_line:
					a2_lines.append(current_line)
					current_line = []
			else:
				current_line.append((t, item))

		active_line_idx = -1
		active_words = []
		for line_idx, line in enumerate(a2_lines):
			for word_idx, (start, (text, end)) in enumerate(line):
				if start <= position < end:
					active_line_idx = line_idx
					active_words.append(word_idx)
					break
			if active_line_idx != -1:
				break

		visible_lines = height - 1  # Leave space for status
		start_line = max(0, active_line_idx - visible_lines // 2)
		
		for y_offset, line_idx in enumerate(range(start_line, start_line + visible_lines)):
			if line_idx >= len(a2_lines) or y_offset >= height - 1:
				break

			line = a2_lines[line_idx]
			x_pos = max(0, (width - sum(len(text) for _, (text, _) in line)) // 2)
			
			for word_idx, (start, (text, end)) in enumerate(line):
				color = curses.color_pair(2) if line_idx == active_line_idx and word_idx in active_words else 3
				try:
					self.stdscr.addstr(y_offset + 1, x_pos, text, color)
					x_pos += len(text) + 1
				except curses.error:
					break

	def _render_standard_lyrics(self, lyrics, position, height, width, is_txt):
		available_lines = height - 2  # Account for status line
		wrap_width = width - 2
		wrapped_lines = []
		
		# Create wrapped lines with original indices
		for orig_idx, (_, lyric) in enumerate(lyrics):
			if lyric.strip():
				lines = textwrap.wrap(lyric, wrap_width, drop_whitespace=False)
				if lines:
					wrapped_lines.append((orig_idx, lines[0]))
					for line in lines[1:]:
						wrapped_lines.append((orig_idx, " " + line))
			else:
				wrapped_lines.append((orig_idx, ""))
		
		total_wrapped = len(wrapped_lines)
		max_start = max(0, total_wrapped - available_lines)

		# Calculate start line based on scroll mode
		if self._manual_scroll_active():
			start_line = max(0, min(self.manual_offset, max_start))
		else:
			# Find all indices matching current lyric
			indices = [i for i, (orig, _) in enumerate(wrapped_lines) if orig == self.current_idx]
			if indices:
				center = (indices[0] + indices[-1]) // 2
				start_line = max(0, min(center - (available_lines // 2), max_start))
			else:
				start_line = max(0, min(self.current_idx - (available_lines // 2), max_start))

		# Render visible lines
		for y, (orig_idx, line) in enumerate(wrapped_lines[start_line:start_line+available_lines]):
			y_pos = y + 1
			if y_pos >= height - 1:
				break
			
			trimmed_line = line.strip()
			padding = max(0, (width - len(trimmed_line)) // 2)  # Fixed missing )
			centered_line = " " * padding + trimmed_line
			color = curses.color_pair(2) if orig_idx == self.current_idx else curses.color_pair(3)
			
			try:
				self.stdscr.addstr(y_pos, 0, centered_line, color)
			except curses.error:
				pass

		# Update manual offset to match actual start line
		self.manual_offset = start_line
		return start_line
	
	def _render_status(self, position, height, width):
		mins, secs = divmod(int(position), 60)
		time_str = f"{mins:02d}:{secs:02d}"
		status_text = f" Line {self.current_idx+1}/{len(self.lyrics)} | {time_str} "  # Changed to len(lyrics)
		
		if self.time_adjust != 0:
			status_text += f"[Adj {self.time_adjust:+.1f}s]"
		
		status_text = status_text[:width-1]
		
		try:
			self.stdscr.addstr(height-1, 0, status_text, curses.A_BOLD)
		except curses.error:
			pass

	def _manual_scroll_active(self):
		return self.last_input_time and (time.time() - self.last_input_time < 2)

	def handle_input(self):
		key = self.stdscr.getch()
		if key == -1:
			return True

		self.last_input_time = time.time()
		
		if key == ord('q'):
			return False
		elif key == curses.KEY_UP:
			self.manual_offset = max(0, self.manual_offset - 1)
		elif key == curses.KEY_DOWN:
			self.manual_offset += 1
		elif key == curses.KEY_RESIZE:
			self._handle_resize()
		elif key == ord('+'):
			self.time_adjust += 0.5
		elif key == ord('-'):
			self.time_adjust -= 0.5

		return True

	def _handle_resize(self):
		height, _ = self.stdscr.getmaxyx()
		self.manual_offset = int(self.manual_offset * (height / max(height, 1)))

class NowPlaying:
	def __init__(self, stdscr):
		self.player = CmusPlayer()
		self.lyrics_mgr = LyricsManager()  # Fixed variable name
		self.display = LyricsDisplay(stdscr)
		self.last_file = None
	
	def run(self):
		while True:
			# Update player state
			self.player.update()
			
			manual_scroll_active = self.display.last_input_time and \
				(time.time() - self.display.last_input_time < 2)
				
			# Update lyrics if track changed
			if self.player.current_file != self.last_file:
				self.last_file = self.player.current_file
				self.lyrics_mgr.load_lyrics(
					self.player.current_file,
					self.player.artist,
					self.player.title,
					self.player.duration
				)
			
			# Handle input and render
			if not self.display.handle_input():
				break
			
			# Calculate adjusted position
			adj_position = self.player.position + self.display.time_adjust
			
			# Render display
			self.display.render(
				self.lyrics_mgr.lyrics,  # Fixed variable name
				adj_position,
				self.lyrics_mgr.is_txt,
				self.lyrics_mgr.is_a2,
				manual_scroll_active  # ADD THIS PARAMETER
			)
			
			time.sleep(0.01)

	def _update_lyrics(self):
		if self.player.current_file != self.last_file:
			self.last_file = self.player.current_file
			self.lyrics.load_lyrics(
				self.player.current_file,
				self.player.artist,
				self.player.title,
				self.player.duration
			)

	def _update_display(self):
		if not self.display.handle_input():
			return False

		adj_position = self.player.position + self.display.time_adjust
		self.display.render(
			self.lyrics.lyrics,
			adj_position,
			self.lyrics.is_txt,
			self.lyrics.is_a2
		)
		return True

if __name__ == "__main__":
	while True:
		try:
			curses.wrapper(lambda stdscr: NowPlaying(stdscr).run())
		except KeyboardInterrupt:
			break
		except Exception as e:
			with open("error_log.txt", "a") as f:
				f.write(f"{datetime.now()}: {str(e)}\n")
