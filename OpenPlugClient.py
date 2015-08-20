from __future__ import unicode_literals
import requests
import youtube_dl
import subprocess
import threading
import time
import os
import curses
import copy


GLOBAL_SETTINGS = {
    'server-url': 'http://192.168.1.10',
    'server-port': 25222,
    'default-playlist': "_sup_secrets_test_playlist_of_doom",
    'cache-dir': './cache/',
    'fifo-file': './cache/mplayer.fifo',
    'debug-out': False,
    'error-log': './log.txt'
}

class EventMessages:
    def __init__(self, name):
        self.name = name
        self.log = []

    def writeOut(self, message):
        self.log.append(message)

    def lastMessage(self):
        if len(self.log) > 0:
            return self.log[-1]
        return "No Messages!"

    def all(self):
        return self.log

    def msgCount(self):
        return len(self.log)


class DecodeProcess:
    def __init__(self, inputFile, outputFile):
        self.process = None
        self.input = inputFile
        self.output = outputFile
        self.finished = False
        self.inProgress = False
        self.watchThread = None


    def decodeWatcher(self):
        self.inProgress = True
        self.process.wait()
        status = self.process.poll()
        if status == 0:
            os.rename(self.output + '.part', self.output)

        #delete input file
        os.remove(self.input)
        self.finished = True
        self.inProgress = False
        return

    def isDecoding(self):
        return self.inProgress

    def isDone(self):
        return self.finished

    '''
    Decoding is piped to stdout so mplayer can grab what it needs when its available. At the same time, a cache file
    is written out for the next time the song needs to be played.
    So trying to play from the cache file while decoding won't work, thus, the file is given a .part extension.
    When decoding is done, the file will be renamed so the next time it comes up, it'll play fine
    Extra insurance for when someone stops streaming in the middle of a song
    '''
    def startWatch(self):
        self.watchThread = threading.Thread(target=self.decodeWatcher)
        self.watchThread.start()


    def defaultParams(self):
        defaults = ['ffmpeg', '-i', '-', '-f', 'mp3', '-y', self.output+'.part']


        if not GLOBAL_SETTINGS['debug-out']:
            defaults.extend(['-loglevel', 'panic'])

        return defaults

    def streamDecode(self):
        params = self.defaultParams()
        params.extend(['-f', 'mp3', '-'])
        self.process = subprocess.Popen(params,stdin=open(self.input, 'rb'), stdout=subprocess.PIPE)
        self.startWatch()

    def decode(self):
        params = self.defaultParams()
        self.process = subprocess.Popen(params, stdin=open(self.input, 'rb'))
        self.startWatch()

    def stdout(self):
        return self.process.stdout

    def stop(self):

        return self.process.kill()


class MPlayer:

    def __init__(self, filestream=None, offset=None):

        if not os.path.exists("temp.fifo"):
            os.mkfifo("temp.fifo")


        parameters = ['mplayer', '-quiet', '-input', 'file=temp.fifo', '-idle', '-demuxer', 'lavf', '-idx']

        if filestream is not None:
            if offset is not None:
                parameters.append("-ss")
                parameters.append(str(offset))

            parameters.append("-")


            self.process = subprocess.Popen(parameters, stdout=subprocess.PIPE, stdin=filestream,
                                            stderr=open(GLOBAL_SETTINGS['error-log'], 'a'))
        else:
            self.process = subprocess.Popen(parameters, stdout=subprocess.PIPE,
                                            stderr=open(GLOBAL_SETTINGS['error-log'], 'a'))


    def sendCmd(self, cmd):
        if self.process.poll() is not None: return

        with open('temp.fifo', 'w') as fp:
            fp.write("\n" + str(cmd) + "\n")


    def getOutput(self):
        if self.process.stdout is None: return None
        if self.process.poll() is not None: return None

        timeout = 0
        for line in self.process.stdout:
            line = line.decode()
            if "ANS_" in line:
                if "=" in line:
                    line = line.split("=")
                    line = line[1].strip()
                return line

            timeout+=1

            if timeout > 20: return None


    def seekTo(self, offset):
        self.sendCmd("seek " + str(offset) + " 2")

    def play(self, filename, offset):
        self.sendCmd('loadfile "' + filename + '"')
        self.seekTo(offset)

    def stop(self):
        self.sendCmd("quit")
        self.process.kill()

    def mute(self):
        self.sendCmd("mute")

    def getPos(self):
        self.sendCmd("get_time_pos")
        return self.getOutput()

    def getPercent(self):
        self.sendCmd("get_percent_pos")
        return self.getOutput()

    def isAlive(self):
        if self.process is None: return False
        return self.process.poll() is not None



class AudioManager:
    DECODE_TO = GLOBAL_SETTINGS['cache-dir']


    def __init__(self):
        self.keepAlive = True
        self.decodeProcess = None
        self.curSong = None
        self.messages = EventMessages('General')
        self.mplayer = None
        self.decodeBacklog = []


    def ytToAPISong(self, song):
        if type(song) is YoutubeSong:
            return song.song

        return song


    def isSongCached(self, song):
        return os.path.isfile(self.songCacheName(song))


    def songCacheName(self, song):
        curSong = self.ytToAPISong(song)

        return self.DECODE_TO + \
            str(curSong.artist) + '-' + str(curSong.title) + '.mp3'

    def decode(self, ytsong):
        self.messages.writeOut("Decoding Stream...")
        self.decodeProcess = DecodeProcess(ytsong.filename, self.songCacheName(ytsong))
        self.decodeProcess.streamDecode()
        return self.decodeProcess

    def play(self, ytsong):

        if self.mplayer: self.mplayer.stop()

        if self.decodeProcess is not None and self.decodeProcess.isDecoding():
            self.messages.writeOut("Waiting for stream to seek: " + str(ytsong.getStartOffset()))
            self.mplayer = MPlayer(self.decodeProcess.stdout(), ytsong.getStartOffset())
        else:
            self.messages.writeOut("Playing song from cache.")
            self.mplayer = MPlayer()
            while self.mplayer.isAlive() == False:
                continue

            self.mplayer.play(self.songCacheName(ytsong), ytsong.getStartOffset())

        self.curSong = ytsong

    def getMplayer(self):
        return self.mplayer

    def isPlaying(self):
        return True


    def isDecoding(self):
        if self.decodeProcess == None: return False
        return self.decodeProcess.isDecoding()


    def clearDecodeBacklog(self):
        #keep track of processes that are still running
        if len(self.decodeBacklog) > 0:
            self.decodeBacklog = [a for a in self.decodeBacklog if a.isDecoding()]


    def songEnd(self):
        self.clearDecodeBacklog()
        #if we are switching songs but the current one is still decoding,
        #add it to the backlog so it can finish while the next song prepares
        if self.isDecoding(): self.decodeBacklog.append(self.decodeProcess)
        #then detach the current decoder
        self.decodeProcess = None


    def stopDecoding(self):
        if self.isDecoding(): self.decodeProcess.stop()
        self.clearDecodeBacklog()
        for p in self.decodeBacklog: p.stop()


    def stop(self):
        self.stopDecoding()
        if self.mplayer: self.mplayer.stop()

    def mute(self):
        if self.mplayer: self.mplayer.mute()

    def getSong(self):
        return self.curSong

    def getPos(self):
        if self.mplayer: return self.mplayer.getPos()
        return None

    def getPercent(self):
        if self.mplayer: return self.mplayer.getPercent()
        return None


class APISong:
    def __init__(self, jsonDict):
        self.title = jsonDict.get("title")
        self.artist = jsonDict.get("artist")
        self.length = jsonDict.get("length")
        self.filesize = jsonDict.get("filesize")
        self.url = jsonDict.get('youtube_url')
        self.id = jsonDict.get('id')


    def addTimestamps(self, curSongJson):
        self.startTime = curSongJson.get('song_start_time')
        self.requestTime = curSongJson.get('requested_time')

    def getObj(self):
        return {
            'title': self.title,
            'artist': self.artist,
            'length': self.length,
            'filesize': self.filesize,
            'youtube_url': self.url
        }

    def getStartOffset(self):
        return self.requestTime - self.startTime

    def __str__(self):
        return str(self.getObj())


class API:
    SERVERURL = GLOBAL_SETTINGS['server-url']
    PORT = GLOBAL_SETTINGS['server-port']

    def __init__(self, playlistName):
        self.playlist = playlistName
        self.currentSong = None
        self.currentSongID = None
        self.serverPos = None

    def pingCurrentSong(self):
        (status, songData) = self.getPlaylist()

        if status == 200:
            self.serverPos = int(songData['requested_time']) - int(songData['song_start_time'])
            return songData['current_song']

        return None

    def getServerPos(self):
        if self.serverPos is None: return 1
        return self.serverPos

    def updateCurrentSong(self):
        (status, songData) = self.getPlaylist()
        if status == 200:
            newSongID = songData['current_song']

            if self.currentSongID != newSongID:
                self.currentSongID = newSongID

                (status, songInfo) = self.lookupSong(self.currentSongID)
                if status == 200:
                    self.currentSong = APISong(songInfo)
                    self.currentSong.addTimestamps(songData)

        return (songData, self.currentSong)


    def getURL(self):
        return self.SERVERURL + ":" + str(self.PORT) + "/playlists"

    def playlistURL(self):
        return self.getURL() + "/" + self.playlist

    def songsURL(self):
        return self.playlistURL() + "/songs"

    def getPlaylist(self):
        r = requests.get(self.playlistURL())

        newStatus = r.status_code
        data = None
        if newStatus == 404:
            (newStatus, data) = self.makePlaylist()
            data = data.json()
        else:
            data = r.json()



        return (newStatus, data)


    def makePlaylist(self):
        r = requests.post(self.getURL(), params={'playlist_name': self.playlist})
        return (r.status_code, r.json())


    def addNewSong(self, song):
        if type(song) != APISong: return
        r = requests.post(self.songsURL(), data=song.getObj())
        return (r.status_code, r.json())

    def lookupSong(self, songID):
        r = requests.get(self.songsURL()+'/'+str(songID))
        return (r.status_code, r.json())


class YoutubeSong:
    MULTIPLIERS = {
        'kb': 1000,
        'mb': 1000000,
    }

    def __init__(self, song, ytStatus=None):
        self.song = song
        self.length = song.length
        self.addYTData(ytStatus)

        #don't manually override any size already in the database
        if self.song.filesize == 0:
            filesize = ytStatus.get('_total_bytes_str')
            multiplier = 0

            sizeStr = filesize[-3:]
            if sizeStr == "MiB":
                multiplier = self.MULTIPLIERS['mb']
            else:
                multiplier = self.MULTIPLIERS['kb']

            self.song.filesize = int(filesize.replace(sizeStr, '')) * multiplier


    def addYTData(self, ytStatus):
        if ytStatus is not None:
            self.streamStartTime = ytStatus.get('elapsed', 0)
            self.filename = ytStatus.get('filename', None)
        else:
            self.streamStartTime = 0


    def getStartOffset(self):
        return self.song.getStartOffset() + int(self.streamStartTime)

    def fillFromUrl(self, url):
        opts = {
            'format': 'bestaudio/best',
            'nopart': True,
            'forceduration': True,
            'forcetitle': True,
            'skip_download': True,
        }

        if not GLOBAL_SETTINGS['debug-out']: opts['quiet'] = True

        with youtube_dl.YoutubeDL(opts) as ydl:
            data = ydl.extract_info(url, False)

            title = data['title'].split('-', 1)

            newSong = {
                'title': title[1],
                'artist': title[0],
                'length': data['duration'],
                'filesize': data['filesize'],
                'youtube_url': url
            }

            self.song = APISong(newSong)






class Youtube:
    opts = {
        'format': 'bestaudio/best',
        'nopart': True,
    }

    def __init__(self, ytsong, cb):
        self.url = ytsong.song.url
        self.filename = None
        self.streaming = False
        self.streamStartCallBack = None
        self.ytsong = ytsong
        self.cb = cb
        self.messages = EventMessages("youtube-dl")

        if not GLOBAL_SETTINGS['debug-out']:
            self.opts['quiet'] = True

    def getSong(self):
        return self.ytsong

    def streamStarted(self, d):
        self.ytsong.addYTData(d)
        self.cb(self.ytsong)



    def hooks(self, d):

        if d['status'] == 'downloading' and self.streaming == False:
            if os.path.getsize(d['filename']) > 48000 * 10:
                self.streamStarted(d)
                self.streaming = True

        if d['status'] == 'finished':
            self.streaming = False
            self.messages.writeOut("Video fully downloaded")


    def download(self):
        self.opts['outtmpl'] = str(self.ytsong.song.title) + ' ' + str(self.ytsong.song.artist) + '.%(ext)s'
        self.opts['progress_hooks'] = [self.hooks]
        with youtube_dl.YoutubeDL(self.opts) as ydl:
            self.messages.writeOut("Starting youtube stream...")
            ydl.download([self.url])


def playlistThread(api, Player):


    def startPlayback(ytsong):
        Player.decode(ytsong)


    lastSong = None
    Player.messages.writeOut("Getting current track...")
    (playListStatus, currentSong) = api.updateCurrentSong()
    while Player.keepAlive:
        if lastSong != currentSong.id:
            ytSong = YoutubeSong(currentSong)

            if not Player.isSongCached(currentSong):
                Player.messages.writeOut("Song not in cache, getting stream...")
                yt = Youtube(ytSong, startPlayback)
                t = threading.Thread(target=yt.download)
                t.start()

                while Player.isDecoding() == False:
                    time.sleep(0.01)

            Player.play(ytSong)
            lastSong = currentSong.id

        if api.pingCurrentSong() != lastSong:
            Player.messages.writeOut("Getting next track...")
            Player.mute()
            Player.songEnd()
            #Player.songEnd()
            #if the last song is still decoding, mute the audio and let it finish
            #then get the next song started
            (playListStatus, currentSong) = api.updateCurrentSong()

        time.sleep(1)



class GUIWindow(object):
    def __init__(self, height=0,width=0,y=0,x=0):
        self.window = curses.newwin(height, width, y, x)
        self.color = None

    def draw(self):
        self.window.refresh()

    def getSize(self):
        return self.window.getmaxyx()

    def setColor(self, color):
        self.color = color
        self.window.bkgd(' ', color)

    def addstr(self, y, x, text):
        if self.color is None:
            self.window.addstr(y, x, text)
        else:
            self.window.addstr(y, x, text, self.color)



class GUIBanner(GUIWindow):
    def __init__(self, height, width, y, x, color):
        super().__init__(height, width, y, x)
        self.setColor(color)


    def setBannerText(self, text):
        self.bannerText = text


    def draw(self):
        self.window.erase()
        self.window.addstr(0, 1, str(self.bannerText))
        super().draw()


class GUITrackInfo(GUIWindow):

    def __init__(self, height, width, y, x):
        super().__init__(height, width, y, x)

    def draw(self, curTrack):
        #self.window.clear()
        self.window.box()
        self.window.addstr(0,0, "Song Details:")

        if curTrack is not None:
            self.window.addstr(2, 1, "Artist: " + curTrack.song.artist)
            self.window.addstr(3, 1, "Title: " + curTrack.song.title)
            self.window.addstr(4, 1, "Pos:")
            self.window.addstr(6, 1, "Server Pos:")

        super().draw()

class GUITrackProgressBar(GUIWindow):
    def __init__(self, width, y, x):
        super().__init__(1, width, y, x)

    def draw(self, progress):
        self.window.clrtoeol()
        (h, w) = self.getSize()

        newPos = progress * w
        self.addstr(0, 0, "#"*int(newPos))
        super().draw()


class GUIHelp(GUIWindow):
    def __init__(self, height, width, y, x):
        super().__init__(height, width, y, x)

    def draw(self):
        self.window.erase()
        self.window.box()
        self.window.addstr(0, 0, "Commands:")
        self.window.addstr(3, 2, "add {youtube url}\tAdd new song to connected playlist.")
        self.window.addstr(4, 2, "exit\t\t\tExit program")
        self.window.refresh()

class GUIEvents(GUIWindow):
    def __init__(self, height, width, y, x):
        super().__init__(height, width, y, x)
        self.msgCount = 0

    def draw(self):
        self.window.box()
        self.window.addstr(0, 0, "Program Events:")
        super().draw()

    def addMessage(self, msg):
        (h,w) = self.getSize()

        if 1 + self.msgCount >= h:
            self.msgCount = 0
            self.window.clear()

        self.window.addstr(1 + self.msgCount, 1, str(msg))
        self.msgCount +=1
        self.draw()




class GUI:

    def __init__(self, stdscr):
        (height, width) = stdscr.getmaxyx()
        starty = 0
        startx = 0
        self.width = width
        self.height = height


        curses.init_pair(9, curses.COLOR_YELLOW, curses.COLOR_BLUE)
        curses.init_pair(10, curses.COLOR_GREEN, curses.COLOR_BLUE)
        curses.init_pair(11, curses.COLOR_WHITE, curses.COLOR_RED)
        self.COLOR_PAIRS = {
            'banner': curses.color_pair(9)|curses.A_NORMAL,
            'progress': curses.color_pair(10)|curses.A_DIM,
            'serverProg': curses.color_pair(11)
        }


        self.trackInfo = GUITrackInfo(9, int(self.width/2), starty+1, 0)

        self.progressBar = GUITrackProgressBar(int(self.width/2)-4, starty+6, 2)
        self.progressBar.setColor(self.COLOR_PAIRS['progress'])

        self.serverProgress = GUITrackProgressBar(int(self.width/2)-4, starty+8, 2)
        self.serverProgress.setColor(self.COLOR_PAIRS['serverProg'])


        self.footer = GUIBanner(1, self.width-1, starty + self.height-3, 0, self.COLOR_PAIRS['banner'])
        self.banner = GUIBanner(1, self.width-1, starty, 0, self.COLOR_PAIRS['banner'])

        self.help = GUIHelp(10, int(self.width/2), starty + self.height - 13, 0)
        self.messages = GUIEvents(self.height - 4, int(self.width/2), starty + 1, int(self.width/2))

        self.initInputBox()


    def initInputBox(self):
        self.inputBox = curses.newwin(1, self.width, self.height - 2, 0)
        #self.inputBox.box()

    def drawInputBox(self):
        self.inputBox.addstr(0, 0, ':')
        self.inputBox.move(0, 1)
        self.inputBox.clrtoeol()
        self.inputBox.refresh()
    def getInput(self):
        return self.inputBox.getstr(0, 1).decode()





def gui(stdscr, Player, api):

    g = GUI(stdscr)


    def inputThread():
        while True:
            curses.echo()
            #g.drawInputBox()
            command = g.getInput()
            #command = command.replace('\n', '')
            if command == "exit":
                Player.stop()
                Player.keepAlive = False
                print("Closing...")
                break


            elif command[:len("add")] == "add":
                url = command[len("add"):].strip()
                Player.messages.writeOut("Adding URL: " + url)
                song = YoutubeSong(APISong({}))
                song.fillFromUrl(url)
                (result, msg) = api.addNewSong(song.song)
                if result == 200:
                    Player.messages.writeOut("Successfully added track.")
                else:
                    Player.messages.writeOut("An error occured:")
                    Player.messages.writeOut(msg)

            elif command == "mute":
                Player.mute()

            elif command == "pos":
                Player.messages.writeOut(str(Player.getPos()))
            elif command == "per":
                Player.messages.writeOut(str(Player.getPercent()))




    oldMsgs = 0

    g.help.draw()
    g.footer.setBannerText("Enter Command:")
    g.footer.draw()
    g.banner.setBannerText("Server: " + api.playlistURL())
    g.banner.draw()

    inThread = threading.Thread(target=inputThread)
    inThread.start()

    playThread = threading.Thread(target=playlistThread, args=(api, Player))
    playThread.start()

    Player.messages.writeOut("Can color? - " + str(curses.can_change_color()))

    oldSong = None
    while True:
        if not inThread.is_alive(): break

        newCount = Player.messages.msgCount()
        if oldMsgs != newCount:
            g.messages.addMessage(Player.messages.lastMessage())
            oldMsgs = newCount

        curSong = Player.getSong()
        if oldSong is None or oldSong != curSong:
            oldSong = curSong
            g.trackInfo.draw(oldSong)


        time.sleep(1)

        if Player.getMplayer() is not None and oldSong is not None:
            pos = Player.getPos()
            #Player.messages.writeOut("Position: " + str(pos))
            if pos is not None:
                count = (int(float(pos)) + 1) / (int(oldSong.length) + 1)
                g.progressBar.draw(count)


            count = api.getServerPos()/ (int(oldSong.length + 1))
            g.serverProgress.draw(count)




def main(stdscr):

    curses.start_color()
    curses.use_default_colors()
    stdscr.clear()
    stdscr.refresh()

    api = API(GLOBAL_SETTINGS['default-playlist'])
    gui(stdscr, AudioManager(), api)


curses.wrapper(main)


