# lyrus cmus lyric player
featuring fully fledged cmus, mpd lyric player
automatic lyrics fetcher and supports local lyrics player, txt and lrc scrolling
supports wide range of lyric sources
ncurses, scrollable

use PKGBUILD  and  makepkg -si (currently on arch works)

other linux do use make sure use virual environment to get it working (it should)

customizable through this local where this program installed.

best cmus simple lyrics player out there

unfortunately it uses traditional polling system couldve used playerctl since the beginning

Currently implementing this out
```bash
playerctl metadata --format "{{playerName}}, {{ artist }}, {{ title }}, {{ duration(position) }}, {{ uc(status) }},{{ duration(mpris:length) }}"
```
the dbus does not actually implement properly still doing traditional polling, I know this isnt the right approach to the lrc control but I will need to go through some of the documentation before executing all that

tbh i am happy to introduce this that this program now works on any music player that works with playerctl


### All the timeouts, debugs logs and synced lyrics are stored in ``~/.cache/lyrus`` by default


will attempt to get a2 working

Scrollable lyrics btw 

very customizeable

![image](https://github.com/user-attachments/assets/5d5fdbc5-7d4b-4b38-b2db-0cee5722806f)


run this script with these requirement  installable through pip


Currently need to clean up the code its too big to clean if theres a bug


It detects your custon config.json (monfiable btw) make sure you backup this json configurations


I will add more synced lyrics provider options directly to the config
