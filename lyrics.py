import curses
import subprocess
import time
import re
import os

def get_cmus_position():
    result = subprocess.run(['cmus-remote', '-Q'], stdout=subprocess.PIPE)
    output = result.stdout.decode('utf-8')
    match = re.search(r'position (\d+)', output)
    return int(match.group(1)) if match else 0

def get_cmus_track_file():
    result = subprocess.run(['cmus-remote', '-Q'], stdout=subprocess.PIPE)
    output = result.stdout.decode('utf-8')
    match = re.search(r'file (.+)', output)
    return match.group(1) if match else None

def find_lyrics_file(audio_file, directory):
    base_name, _ = os.path.splitext(os.path.basename(audio_file))
    lrc_file = os.path.join(directory, f"{base_name}.lrc")
    return lrc_file if os.path.exists(lrc_file) else None

def parse_time_to_seconds(time_str):
    minutes, seconds = time_str.split(':')
    seconds, milliseconds = seconds.split('.')
    return int(minutes) * 60 + int(seconds) + float(f"0.{milliseconds}")

def load_lyrics(file_path):
    with open(file_path, 'r', encoding="utf-8") as f:
        lyrics = f.readlines()

    parsed_lyrics = []
    for line in lyrics:
        match = re.match(r'\[(\d+:\d+\.\d+)\](.*)', line)
        if match:
            timestamp = parse_time_to_seconds(match.group(1))
            lyric = match.group(2).strip()
            parsed_lyrics.append((timestamp, lyric)) 

    return parsed_lyrics

def display_lyrics(stdscr, lyrics, position, track_info):
    stdscr.clear()
    stdscr.addstr(0, 0, f"Now Playing: {track_info}")
    height, width = stdscr.getmaxyx()

    current_idx = next((i for i, (t, _) in enumerate(lyrics) if t > position), len(lyrics)) - 1

    max_scroll_lines = height - 3
    start_line = max(0, current_idx - (height // 2))

    if len(lyrics) - start_line < max_scroll_lines:
        start_line = max(0, len(lyrics) - max_scroll_lines)

    for idx, (time, lyric) in enumerate(lyrics[start_line: start_line + max_scroll_lines]):
        if idx + start_line == current_idx:
            stdscr.attron(curses.color_pair(2))
        else:
            stdscr.attron(curses.color_pair(3))

        stdscr.addstr(idx + 2, 0, lyric[:width - 1])

        stdscr.attroff(curses.color_pair(2))
        stdscr.attroff(curses.color_pair(3))

    if current_idx == len(lyrics) - 1:
        stdscr.addstr(height - 1, 0, "End of lyrics.")

    stdscr.refresh()

def main(stdscr):
    curses.start_color()
    curses.init_pair(2, curses.COLOR_GREEN, curses.COLOR_BLACK)
    curses.init_pair(3, curses.COLOR_WHITE, curses.COLOR_BLACK)

    curses.curs_set(0)
    
    current_audio_file = None 
    
    while True:
        audio_file = get_cmus_track_file()
        
        if audio_file != current_audio_file:
            current_audio_file = audio_file
            if not audio_file:
                stdscr.clear()
                stdscr.addstr(2, 0, "No track is currently playing.")
                stdscr.refresh()
                time.sleep(2)
                continue

            directory = os.path.dirname(audio_file)

            lyrics_file = find_lyrics_file(audio_file, directory)
            if not lyrics_file:
                stdscr.clear()
                stdscr.addstr(2, 0, "No lyrics file found.")
                stdscr.refresh()
                time.sleep(2)
                continue

            lyrics = load_lyrics(lyrics_file)

        position = get_cmus_position()
        title = os.path.basename(audio_file)
        display_lyrics(stdscr, lyrics, position, title) 
        time.sleep(0.5)

if __name__ == "__main__":
    curses.wrapper(main)
