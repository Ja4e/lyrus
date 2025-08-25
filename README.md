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
playerctl metadata --format "{{playerName}}, {{ artist }}, {{ title }}, {{ duration(position) }}, {{ uc(status) }},{{ duration(mpris:length) }}" --follow
```
it will be another fall back option when mpd and cmus are not found which enables spotify and many other player lyrics scrolling in real time. It would nice to make this program useable

will add more support in detecting players
will attempt to get a2 working

Scrollable lyrics btw 

very customizeable

![image](https://github.com/user-attachments/assets/5d5fdbc5-7d4b-4b38-b2db-0cee5722806f)


run this script with these requirement  installable through pip


Currently need to clean up the code its too big to clean if theres a bug


It detects your custon config.json (monfiable btw) make sure you backup this json configurations


I will add more synced lyrics provider options directly to the config
