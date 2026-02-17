# lyrus lyric player
featuring fully fledged cmus, mpd lyric player
automatic lyrics fetcher and supports local lyrics player, txt and lrc scrolling

supports wide range of lyric sources
curses, scrollable

use ``PKGBUILD``  and  ``makepkg -si`` (currently on arch works) bewarned that ``python-syncedlyrics`` dependencies is AUR you might need to install that beforehand
or through AUR ``yay -S lyrus-git`` (recommended)
, for other linux do use make sure use virual environment to get it working (it should)
like
```bash
git clone https://github.com/Ja4e/lyrus.git
cd lyrus
python -m venv lyrus-123 
pip -r requirements.txt
python lyrus.py
```
``python-syncedlyrics`` exist as mandatory because it provides best source of lrc


![image](https://github.com/user-attachments/assets/5d5fdbc5-7d4b-4b38-b2db-0cee5722806f)

---

### All the timeouts, debugs logs are stored in ``~/.cache/lyrus`` by default

### All cached lyrics are stored in ``~/.local/state/lyrus/synced_lyrics`` by default

### You can copy paste the default config form the wiki and save it under ``~/.config/lyrus`` make sure you create the directory yourself with ``mkdir ~/.config/lyrus``

---

best cmus simple lyrics player out there

unfortunately it uses traditional polling system couldve used playerctl since the beginning

the dbus does not actually implement properly still doing traditional polling, I know this isnt the right approach to the lrc control but I will need to go through some of the documentation before executing all that

tbh i am happy to introduce this that this program now works on any music player that works with playerctl


Scrollable lyrics btw 

very customizeable


run this script with these requirement  installable through pip if doownloaded directly through the source code


It detects your custon config.json (monfiable btw) make sure you backup this json configurations






will attempt to get a2 working

also need better instrument detection


hmm i wanted to add option to allow tab like functionality to be able to edit the setting in curses interface
