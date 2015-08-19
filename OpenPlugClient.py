from __future__ import unicode_literals
import requests
import youtube_dl
import subprocess
import threading
import time
import os
import curses


GLOBAL_SETTINGS = {
    'server-url': 'http://192.168.1.10',
    'server-port': 25222,
    'default-playlist': "_sup_secrets_test_playlist_of_doom",
    'cache-dir': './cache/',
    'fifo-file': './cache/mplayer.fifo',
    'debug-out': False
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


class MediaControls:
    CTRLFILE = GLOBAL_SETTINGS['fifo-file']
    MAXBUFS = 2

    def __init__(self):
        self.curBuf = 0

        for i in range(0, self.MAXBUFS):
            if not os.path.exists(self.getFifo()):
                os.mkfifo(self.getFifo())

            self.swap()

    def getFifo(self):
        return self.CTRLFILE+"."+str(self.curBuf)

    def swap(self):
        self.curBuf+=1
        if self.curBuf >= self.MAXBUFS:
            self.curBuf = 0


    def sendCmd(self, command, file=None):
        if file is None: file = self.getFifo()

        with open(file, 'w') as fp:
            fp.write(str(command) + '\n')

#make controls globally accessible
MEDIAPLAYER = MediaControls()





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

        if filestream:
            if offset:
                parameters.append("-ss")
                parameters.append(str(offset))

            parameters.append("-")


            self.process = subprocess.Popen(parameters, stdout=subprocess.PIPE, stdin=filestream)
        else:
            self.process = subprocess.Popen(parameters, stdout=subprocess.PIPE)


    def sendCmd(self, cmd):
        with open('temp.fifo', 'w') as fp:
            fp.write("\n" + str(cmd) + "\n")


    def getOutput(self):
        for line in self.process.stdout:
            if "ANS_" in line:
                return line


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








class PlaybackProcess:
    def __init__(self, fileStream, startTime):
        self.fileStream = fileStream
        self.startTime = startTime

        self.fifofile = MEDIAPLAYER.getFifo()
        self.process = subprocess.Popen(self.defaultParams(), stdin=self.fileStream)
        MEDIAPLAYER.swap()



        #MEDIAPLAYER.swap()

    def defaultParams(self):
        defaults = ['mplayer', '-input', 'file='+self.fifofile, '-demuxer', 'lavf',
                                         '-idx', '-ss', str(self.startTime),'-']

        if not GLOBAL_SETTINGS['debug-out']:
            defaults.extend(['-really-quiet'])

        return defaults

    def isPlaying(self):
        return self.process.poll() == None

    def stop(self):
        self.process.kill()

    def mute(self):
        MEDIAPLAYER.sendCmd('mute', self.fifofile)






class AudioManager:
    DECODE_TO = GLOBAL_SETTINGS['cache-dir']


    def __init__(self):
        self.keepAlive = True
        self.playbackProcess = None
        self.decodeProcess = None
        self.curSong = None
        self.messages = EventMessages('General')
        self.mplayer = None


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

        if self.decodeProcess is not None and self.decodeProcess.isDecoding():
            self.messages.writeOut("Waiting for stream to seek: " + str(ytsong.getStartOffset()))
            #if audio is decoding while we want to play it, then pull it from stdout
            #self.playbackProcess = PlaybackProcess(self.decodeProcess.stdout(), ytsong.getStartOffset())
            if self.mplayer: self.mplayer.stop()
            self.mplayer = MPlayer(self.decodeProcess.stdout(), ytsong.getStartOffset())
        else:
            #otherwise, we have the file cached already, so lets go!
            self.messages.writeOut("Playing song from cache.")
            #self.playbackProcess = PlaybackProcess(open(self.songCacheName(ytsong)), ytsong.getStartOffset())
            if self.mplayer: self.mplayer.stop()
            self.mplayer = MPlayer()
            self.mplayer.play(self.songCacheName(ytsong), ytsong.getStartOffset())

        self.curSong = ytsong
        return self.playbackProcess


    def isPlaying(self):
        return True
        if self.playbackProcess == None: return False
        return self.playbackProcess.isPlaying()

    def isDecoding(self):
        if self.decodeProcess == None: return False
        return self.decodeProcess.isDecoding()


    def stop(self):
        if self.isDecoding(): self.decodeProcess.stop()
        self.decodeProcess = None

        if self.mplayer: self.mplayer.stop()
        #if self.isPlaying(): self.playbackProcess.stop()
        #self.playbackProcess = None

    def mute(self):
        if self.mplayer: self.mplayer.mute()
        #if self.isPlaying(): self.playbackProcess.mute()

    def getSong(self):
        return self.curSong

    def getPos(self):
        if self.mplayer: self.mplayer.getPos()


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

    def pingCurrentSong(self):
        (status, songData) = self.getPlaylist()
        if status == 200:
            return songData['current_song']

        return None




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
            if Player.isPlaying():
                Player.mute()
            #if the last song is still decoding, mute the audio and let it finish
            #then get the next song started
            (playListStatus, currentSong) = api.updateCurrentSong()

        time.sleep(1)


class GUI:
    COLOR_PAIRS = {
        'banner': 1,
        'messages': 2,
        'footer': 3,
    }

    def __init__(self):
        self.stdscr = curses.initscr()
        curses.start_color()

        (height, width) = self.stdscr.getmaxyx()
        self.width = width
        self.height = height
        self.win = curses.newwin(height, width, 0, 0)
        self.win.box()

        self.initBanner()
        self.initMessages()
        self.initTrackInfo()
        self.initInputBox()
        self.initHelp()
        self.initFooter()



    def initBanner(self):
        self.banner = curses.newwin(1, self.width-1, 0, 0)
        curses.init_pair(self.COLOR_PAIRS['banner'], curses.COLOR_YELLOW, curses.COLOR_BLUE)
        self.banner.bkgd(" ", curses.color_pair(self.COLOR_PAIRS['banner']))

    def drawBanner(self, text):
        self.banner.addstr(0, 0, text)
        self.banner.refresh()

    def initFooter(self):
        self.footer = curses.newwin(1, self.width-1, self.height - 3, 0)
        curses.init_pair(self.COLOR_PAIRS['footer'], curses.COLOR_WHITE, curses.COLOR_GREEN)
        self.footer.bkgd(" ", curses.color_pair(self.COLOR_PAIRS['footer']))

    def drawFooter(self):
        self.footer.addstr(0, 0, "Enter Command:")
        self.footer.refresh()


    def initMessages(self):
        self.messages = curses.newwin(self.height - 4, int(self.width / 2), 1, int(self.width/2))
        self.messages.box()
        curses.init_pair(self.COLOR_PAIRS['messages'], curses.COLOR_GREEN, curses.COLOR_BLACK)
        self.messages.bkgd(" ", curses.color_pair(self.COLOR_PAIRS['messages']))

    def drawMessages(self, eventMessage):
        #self.messages.clear()
        self.messages.addstr(0, 0, "Program Events:")

        start = 1
        for msg in eventMessage.all():
            self.messages.addstr(start, 1, msg)
            start+=1

        self.messages.refresh()


    def initTrackInfo(self):
        self.trackInfo = curses.newwin(int(self.height/2)-1, int(self.width / 2), 1, 0)
        #self.trackInfo.box()

    def drawTrackInfo(self, song):
        self.trackInfo.clear()
        self.trackInfo.box()
        self.trackInfo.addstr(0,0, "Song Details:")

        if song is not None:
            song = song.song
            self.trackInfo.addstr(2, 1, "Artist: " + song.artist)
            self.trackInfo.addstr(3, 1, "Title: " + song.title)

        #self.trackInfo.addstr(1,1, str(song.song))
        self.trackInfo.refresh()


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


    def initHelp(self):
        self.help = curses.newwin(int(self.height/2)-2, int(self.width/2), int(self.height/2), 0)
        self.help.box()

    def drawHelp(self):
        self.help.addstr(0, 0, "Commands:")
        self.help.addstr(3, 2, "add {youtube url}\tAdd new song to connected playlist.")
        self.help.addstr(4, 2, "exit\t\t\tExit program")
        self.help.refresh()



def gui(Player, api):

    g = GUI()
    g.stdscr.clear()

    def inputThread():
        while True:
            curses.echo()
            g.drawInputBox()
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





    doRedraw = True
    oldMsgs = 0

    g.stdscr.refresh()
    g.drawBanner("Server: " + str(api.playlistURL()))
    g.drawHelp()
    g.drawFooter()


    inThread = threading.Thread(target=inputThread)
    inThread.start()
    oldSong = None
    while True:

        if doRedraw:
            g.drawMessages(Player.messages)
            g.drawTrackInfo(oldSong)
            doRedraw = False


        if not inThread.is_alive(): break

        newCount = Player.messages.msgCount()
        if oldMsgs != newCount:
            doRedraw = True
            oldMsgs = newCount

        curSong = Player.getSong()
        if oldSong != curSong:
            doRedraw = True
            oldSong = curSong


        time.sleep(1)

    curses.endwin()

def main():

    api = API(GLOBAL_SETTINGS['default-playlist'])

    Player = AudioManager()
    playThread = threading.Thread(target=playlistThread, args=(api, Player))
    playThread.start()

    gui(Player, api)

main()

