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

def display_lyrics(stdscr, lyrics, errors, position, track_info, scroll_offset, is_txt_format):
    height, width = stdscr.getmaxyx()

    # Adjust to handle non-timestamped lyrics by checking for None and still displaying
    current_idx = bisect.bisect_right([t for t, _ in lyrics if t is not None], position) - 1

    max_scroll_lines = height - 3
    start_line = max(0, current_idx - (height // 2)) + scroll_offset
    start_line = max(0, min(start_line, len(lyrics) - max_scroll_lines))
    scroll_offset = max(0, min(scroll_offset, len(lyrics) - start_line - max_scroll_lines))

    stdscr.clear()
    stdscr.addstr(0, 0, f"Now Playing: {track_info}")

    current_line_y = 2
    wrapped_lyrics = []

    for idx, (time, lyric) in enumerate(lyrics[start_line: start_line + max_scroll_lines]):
        if time is not None and idx + start_line == current_idx:
            stdscr.attron(curses.color_pair(2))
        else:
            stdscr.attron(curses.color_pair(3))

        # Check if the lyric fits the width, else split into new lines
        wrapped_lines = textwrap.wrap(lyric, width - 1)  # Wrap lyrics to fit within screen width
        
        for line_idx, line in enumerate(wrapped_lines):
            # Indent only the overflowed part with extra space
            if line_idx > 0:
                line = " " + line  # Add extra space at the beginning for overflowed lines

            # Ensure current_line_y doesn't go out of bounds
            if current_line_y < height - 1:
                stdscr.addstr(current_line_y, 0, line)
                current_line_y += 1

        stdscr.attroff(curses.color_pair(2))
        stdscr.attroff(curses.color_pair(3))

    if not is_txt_format:
        max_error_lines = height - 2 - (start_line + max_scroll_lines)
        for idx, error_line in enumerate(errors[:max_error_lines]):
            error_line = error_line[:width - 1]
            stdscr.attron(curses.color_pair(4))
            stdscr.addstr(height - 2 + idx, 0, f"Error: {error_line}")
            stdscr.attroff(curses.color_pair(4))

    if current_idx == len(lyrics) - 1:
        stdscr.addstr(height - 1, 0, "End of lyrics.")
    stdscr.refresh()

def main(stdscr):
    curses.start_color()
    curses.init_pair(2, curses.COLOR_GREEN, curses.COLOR_BLACK)
    curses.init_pair(3, curses.COLOR_WHITE, curses.COLOR_BLACK)
    curses.init_pair(4, curses.COLOR_RED, curses.COLOR_BLACK)
    curses.curs_set(0)

    current_audio_file = None
    lyrics = []
    errors = []
    scroll_offset = 0
    is_txt_format = False

    # Initial timeout of 50 ms
    stdscr.timeout(50)

    while True:
        audio_file, position = get_cmus_info()

        if audio_file != current_audio_file:
            current_audio_file = audio_file
            stdscr.clear()

            if not audio_file:
                stdscr.addstr(2, 0, "No track is currently playing or cmus is not opened.")
                stdscr.refresh()
                time.sleep(2)  # Add a short delay here if necessary
                continue

            directory = os.path.dirname(audio_file)
            lyrics_file = find_lyrics_file(audio_file, directory)

            if lyrics_file:
                is_txt_format = lyrics_file.endswith('.txt')
                lyrics, errors = load_lyrics(lyrics_file)
            else:
                lyrics = []
                errors = []
                stdscr.addstr(2, 0, "No lyrics file found.")
                stdscr.refresh()
                time.sleep(2)  # Add a short delay here if necessary
                continue

        if audio_file:
            title = os.path.basename(audio_file)
            display_lyrics(stdscr, lyrics, errors, position, title, scroll_offset, is_txt_format)

        # Handle user input and scrolling
        key = stdscr.getch()

        if key == curses.KEY_UP:
            scroll_offset = max(0, scroll_offset - 1)

        elif key == curses.KEY_DOWN:
            max_scroll_lines = stdscr.getmaxyx()[0] - 3
            if len(lyrics) > max_scroll_lines:
                scroll_offset = min(scroll_offset + 1, len(lyrics) - max_scroll_lines)

        elif key == ord('q'):
            break

        # Adjust timeout dynamically if there's no key input
        if key == -1:  # -1 means no key was pressed
            stdscr.timeout(500)  # Wait for 100 ms when idle (adjust this value)
        else:
            stdscr.timeout(50)  # Shorter timeout when there's input activity


if __name__ == "__main__":
    try:
        curses.wrapper(main)
    except KeyboardInterrupt:
        exit()
