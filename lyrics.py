import curses
import subprocess
import re
import os
import bisect
import time
import textwrap

def get_cmus_info():
    try:
        result = subprocess.run(['cmus-remote', '-Q'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode != 0:
            return None, 0
        output = result.stdout.decode('utf-8')
    except Exception:
        return None, 0

    track_file = None
    position = 0

    track_match = re.search(r'file (.+)', output)
    position_match = re.search(r'position (\d+)', output)

    if track_match:
        track_file = track_match.group(1)
    if position_match:
        position = int(position_match.group(1))

    return track_file, position

def find_lyrics_file(audio_file, directory):
    base_name, _ = os.path.splitext(os.path.basename(audio_file))
    lrc_file = os.path.join(directory, f"{base_name}.lrc")
    txt_file = os.path.join(directory, f"{base_name}.txt")
    return lrc_file if os.path.exists(lrc_file) else (txt_file if os.path.exists(txt_file) else None)

def parse_time_to_seconds(time_str):
    minutes, seconds = time_str.split(':')
    seconds, milliseconds = seconds.split('.')
    return max(0, int(minutes) * 60 + int(seconds) + float(f"0.{milliseconds}"))

def load_lyrics(file_path):
    with open(file_path, 'r', encoding="utf-8") as f:
        lines = f.readlines()

    lyrics = []
    errors = []

    for line in lines:
        match = re.match(r'\[(\d+:\d+\.\d+)\](.*)', line)
        if match:
            try:
                timestamp = parse_time_to_seconds(match.group(1))
                lyric = " " + match.group(2).strip()
                lyrics.append((timestamp, lyric))
            except Exception:
                errors.append(line.strip())
        else:
            lyrics.append((None, " " + line.strip()))
            errors.append(line.strip())

    return lyrics, errors

def display_lyrics(stdscr, lyrics, errors, position, track_info, manual_offset, is_txt_format):
    height, width = stdscr.getmaxyx()
    max_scroll_lines = height - 3

    if not is_txt_format:
        current_idx = bisect.bisect_right([t for t, _ in lyrics if t is not None], position) - 1
        natural_start = max(0, current_idx - (height // 2))
    else:
        current_idx = -1
        natural_start = 0

    start_line = max(0, min(natural_start + manual_offset, len(lyrics) - max_scroll_lines))

    stdscr.clear()
    stdscr.addstr(0, 0, f"Now Playing: {track_info}")
    current_line_y = 2

    for idx, (time, lyric) in enumerate(lyrics[start_line: start_line + max_scroll_lines]):
        wrapped_lines = textwrap.wrap(lyric, width - 1)
        for line in wrapped_lines:
            if current_line_y < height - 1:
                if not is_txt_format and time is not None and (start_line + idx) == current_idx:
                    stdscr.attron(curses.color_pair(2))
                else:
                    stdscr.attron(curses.color_pair(3))
                stdscr.addstr(current_line_y, 0, line)
                stdscr.attroff(curses.color_pair(2))
                stdscr.attroff(curses.color_pair(3))
                current_line_y += 1

    if current_idx == len(lyrics) - 1 and not is_txt_format:
        stdscr.addstr(height - 1, 0, "End of lyrics.")
    stdscr.refresh()

def main(stdscr):
    curses.start_color()
    curses.init_pair(2, curses.COLOR_GREEN, curses.COLOR_BLACK)
    curses.init_pair(3, curses.COLOR_WHITE, curses.COLOR_BLACK)
    curses.curs_set(0)
    stdscr.nodelay(True)  # Enable non-blocking input

    current_audio_file = None
    lyrics = []
    errors = []
    is_txt_format = False
    last_input_time = None
    manual_offset = 0

    while True:
        current_time = time.time()

        if last_input_time is not None and (current_time - last_input_time >= 2.0):
            manual_offset = 0
            last_input_time = None  # Reset scrolling after 2s

        audio_file, position = get_cmus_info()

        if audio_file != current_audio_file:
            current_audio_file = audio_file
            manual_offset = 0
            last_input_time = None
            lyrics = []
            errors = []

            if audio_file:
                directory = os.path.dirname(audio_file)
                lyrics_file = find_lyrics_file(audio_file, directory)
                
                if lyrics_file:
                    is_txt_format = lyrics_file.endswith('.txt')
                    lyrics, errors = load_lyrics(lyrics_file)
                else:
                    is_txt_format = False

        if audio_file:
            title = os.path.basename(audio_file)
            display_lyrics(stdscr, lyrics, errors, position, title, manual_offset, is_txt_format)

        key = stdscr.getch()
        if key != -1:
            last_input_time = time.time()
            if key == curses.KEY_UP:
                manual_offset -= 1
            elif key == curses.KEY_DOWN:
                manual_offset += 1
            elif key == ord('q'):
                break

        time.sleep(0.05)

if __name__ == "__main__":
    try:
        curses.wrapper(main)
    except KeyboardInterrupt:
        exit()
