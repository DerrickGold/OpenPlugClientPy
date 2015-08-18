# OpenPlugClientPy

Python Client for[OpenPlugServer](https://github.com/DerrickGold/OpenPlugServer)

###Dependencies
- Python3
- FFMpeg
- Mplayer
- ncurses
- operating system with mkfifo support
- youtube-dl


###Description

OpenPlugClientPy allows one to create their own music playlist of youtube videos on an OpenPlugServer.
This playlist can be access by anyone who happens to know the name of the playlist and server. The idea
being that everyone who "joins" into a playlist with their client will be listening to the same songs
at roughly the same time.

###Installation
- git clone this repository
- create 'cache' folder in same directory
- configure 'GLOBAL_SETTINGS' at top of OpenPlugClient.py

###To be added:
As the OpenPlugServer advances feature wise, so will the client.


###Appologies
The source is a bit of a mess as it waswas hacked together in about a day. The thread handling of global
data is abysmal. Do not use this source for learning reference You will get burned.