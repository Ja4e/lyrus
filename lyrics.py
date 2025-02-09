import curses
import subprocess
import time
import re
import os
import bisect

def get_cmus_info():
    result = subprocess.run(['cmus-remote', '-Q'], stdout=subprocess.PIPE)
    output = result.stdout.decode('utf-8')

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
    return lrc_file if os.path.exists(lrc_file) else None

def parse_time_to_seconds(time_str):
    minutes, seconds = time_str.split(':')
    seconds, milliseconds = seconds.split('.')
    return max(0, int(minutes) * 60 + int(seconds) + float(f"0.{milliseconds}") - 1)

def load_lyrics(file_path):
    with open(file_path, 'r', encoding="utf-8") as f:
        lines = f.readlines()

    lyrics = []
    for line in lines:
        match = re.match(r'\[(\d+:\d+\.\d+)\](.*)', line)
        if match:
            timestamp = parse_time_to_seconds(match.group(1))
            lyric = match.group(2).strip()
            lyrics.append((timestamp, lyric))

    return lyrics

def display_lyrics(stdscr, lyrics, position, track_info, last_index):
    stdscr.clear()
    stdscr.addstr(0, 0, f"Now Playing: {track_info}")

    height, width = stdscr.getmaxyx()


    current_idx = bisect.bisect_right([t for t, _ in lyrics], position) - 1
    if current_idx == last_index:
        return current_idx 

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
    return current_idx

def main(stdscr):
    curses.start_color()
    curses.init_pair(2, curses.COLOR_GREEN, curses.COLOR_BLACK)
    curses.init_pair(3, curses.COLOR_WHITE, curses.COLOR_BLACK)

    curses.curs_set(0)
    
    current_audio_file = None 
    lyrics = []
    last_index = -1

    while True:
        audio_file, position = get_cmus_info()
        
        if audio_file != current_audio_file:
            current_audio_file = audio_file
            stdscr.clear()
            
            if not audio_file:
                stdscr.addstr(2, 0, "No track is currently playing.")
                stdscr.refresh()
                time.sleep(2)
                continue

            directory = os.path.dirname(audio_file)
            lyrics_file = find_lyrics_file(audio_file, directory)
            
            if lyrics_file:
                lyrics = load_lyrics(lyrics_file)
            else:
                lyrics = []
                stdscr.addstr(2, 0, "No lyrics file found.")
                stdscr.refresh()
                time.sleep(2)
                continue

        title = os.path.basename(audio_file)
        last_index = display_lyrics(stdscr, lyrics, position, title, last_index)
        time.sleep(1)


if __name__ == "__main__":
    curses.wrapper(main)
