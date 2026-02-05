import os
import re
import sys
import time
import shlex
import locale
import logging
import subprocess
import configparser

from threading import Thread
from queue import Queue

__release__ = True
IM_A_MAC = sys.platform == 'darwin'
IM_A_PC = sys.platform == 'win32'
IM_LINUX = sys.platform == 'linux'
ON_POSIX = 'posix' in sys.builtin_module_names
# Only use odd-numbered minor revisions for pre-release builds
INSTAGIFFER_VERSION = '1.8'
# If not a pre-release set to "", else set to "pre-X"
INSTAGIFFER_PRERELEASE = ''
__version__ = INSTAGIFFER_VERSION + INSTAGIFFER_PRERELEASE
__changelogUrl__ = 'http://instagiffer.com/post/146636589471/instagiffer-175-macpc'
__faqUrl__ = 'http://www.instagiffer.com/post/51787746324/frequently-asked-questions'


class InstaConfig:
    description = 'Configuration Class'
    author = 'Justin Todd'

    def __init__(self, configPath):
        self.path = configPath
        self.config: None | configparser.ConfigParser = None

        # Load configuration file
        if not os.path.exists(self.path):
            logging.error('Unable to find configuration file: ' + self.path)

        self.ReloadFromFile()

    def ReloadFromFile(self):
        self.config = None
        self.config = configparser.ConfigParser()
        self.config.read(self.path)

    def ParamExists(self, category, key):
        if self.config is None or category not in self.config:
            return False

        if key.lower() not in self.config[category.lower()]:
            # self.Dump()
            # logging.error("Configuration parameter %s.%s does not exist" % (category, key))
            return False
        else:
            # logging.info("Configuration parameter %s.%s exists" % (category, key))
            return True

    def GetParam(self, category, key):
        if self.config is None:
            return ''
        retVal = ''

        if self.ParamExists(category, key):
            retVal = self.config[category.lower()][key.lower()]
        elif self.ParamExists(category + '-' + sys.platform, key):
            retVal = self.config[category.lower() + '-' + sys.platform][
                key.lower()
            ]  # platform specific config

        if isinstance(retVal, bool) or isinstance(retVal, int):
            return retVal

        # We are dealing with strings or unicode

        # Expand variables
        try:
            retVal = os.path.expandvars(retVal)
        except Exception:
            pass

        # Config file encoding is UTF-8
        # if not isinstance(retVal, unicode):
        if not isinstance(retVal, str):
            retVal = str(retVal, 'utf-8')

        if retVal.startswith(';'):
            retVal = ''

        return retVal

    def GetParamBool(self, category, key):
        val = self.GetParam(category, key)
        boolVal = True

        if isinstance(val, int):
            boolVal = not (val == 0)
        elif val is None:
            boolVal = False
        elif val == '':
            boolVal = False
        elif val.lower() == 'false' or val == '0':
            boolVal = False

        return boolVal

    def SetParam(self, category, key, value):
        if self.config is None:
            return 0

        try:
            current = self.config[category.lower()][key.lower()]
        except KeyError:
            current = None

        if value == current:
            return 0

        if not isinstance(value, str):
            value = str(value)

        self.config[category.lower()][key.lower()] = value
        return 1

    def SetParamBool(self, category, key, value):
        if isinstance(value, bool):
            boolVal = value
        elif isinstance(value, int):
            boolVal = not (value == 0)
        elif value is None:
            boolVal = False
        elif value == '':
            boolVal = False
        elif value.lower() == 'false' or value == '0':
            boolVal = False
        else:
            boolVal = False

        changed = self.SetParam(category, key, str(boolVal))
        return changed

    def Dump(self):
        if self.config is None:
            return

        logging.info('=== GIF Configuration =========================================')

        for cat in self.config:
            logging.info('%s:' % (str(cat)))

            for k in self.config[cat]:
                dumpStr = '  - ' + k + ': '
                val = self.GetParam(cat, k)

                if isinstance(val, bool):
                    dumpStr += str(val) + ' (boolean)'
                elif isinstance(val, int):
                    dumpStr += str(val) + ' (int)'
                else:
                    dumpStr += val
                logging.info(dumpStr)

        logging.info('===============================================================')


def default_output_handler(stdoutLines, stderrLines, cmd):
    """Convert process output to status bar messages.
    There is some cross-cutting here.
    """
    s = None
    i = False

    for outData in [stdoutLines, stderrLines, cmd]:
        if outData is None or len(outData) == 0:
            continue

        if IM_A_MAC and isinstance(outData, list):
            outData = ' '.join('"{0}"'.format(arg) for arg in outData)
        else:
            outData = ' '.join('"{0}"'.format(arg) for arg in outData)

        # youtube dl
        youtubeDlSearch = re.search(
            r'\[download\]\s+([0-9\.]+)% of', outData, re.MULTILINE
        )
        if youtubeDlSearch:
            i = int(float(youtubeDlSearch.group(1)))
            s = 'Downloaded %d%%...' % (i)

        # ffmpeg frame extraction progress
        ffmpegSearch = re.search(
            r'frame=.+time=(\d+:\d+:\d+\.\d+)', outData, re.MULTILINE
        )
        if ffmpegSearch:
            secs = duration_str_to_milliseconds(ffmpegSearch.group(1))
            s = 'Extracted %.1f seconds...' % (secs / 1000.0)

        # imagemagick - figure out what we're doing based on comments
        imSearch = re.search(
            r'^".+(convert\.exe|convert)".+-comment"? "([^"]+):(\d+)"', outData
        )
        if imSearch:
            n = int(imSearch.group(3))

            if n == -1:
                s = '%s' % (imSearch.group(2))
            else:
                i = n
                s = '%d%% %s' % (i, imSearch.group(2))

    return s, i


def run_process(
    cmd,
    callback=None,
    returnOutput=False,
    callBackFinalize=True,
    outputTranslator=default_output_handler,
):
    if not __release__:
        logging.info('Running Command: ' + cmd)
    try:
        #    cmd = cmd.encode(locale.getpreferredencoding())
        cmd = cmd
    except UnicodeError:
        logging.error(
            "RunProcess: Command '"
            + cmd
            + "' contained undecodable unicode. Local encoding: "
            + str(locale.getpreferredencoding())
        )

        if returnOutput:
            return '', ''
        else:
            return False

    env = os.environ.copy()

    if IM_A_PC:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
    elif IM_A_MAC:
        startupinfo = None
        cmd = shlex.split(cmd)
    else:
        startupinfo = None
        cmd = shlex.split(cmd)

    logging.info('z')

    pipe = subprocess.Popen(
        cmd,
        startupinfo=startupinfo,
        shell=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        bufsize=1,
        close_fds=ON_POSIX,
    )
    qOut = Queue()
    qErr = Queue()
    tOut = Thread(target=enqueue_process_output, args=('OUT', pipe.stdout, qOut))
    tErr = Thread(target=enqueue_process_output, args=('ERR', pipe.stderr, qErr))

    tOut.start()
    tErr.start()

    logging.info('a')
    callbackReturnedFalse = False

    stdout = ''
    stderr = ''

    percent = None
    while True:
        statusStr = None
        stderrLines = None
        stdoutLines = None
        logging.info('b')

        try:
            while True:  # Exhaust the queue
                stdoutLines = qOut.get_nowait()
                stdout += str(stdoutLines)
        except Exception:
            pass

        try:
            while True:
                stderrLines = qErr.get_nowait()
                stderr += str(stderrLines)
        except Exception:
            pass

        logging.info('c')
        if outputTranslator is not None:
            # try:
            logging.info('d')
            statusStr, percentDoneInt = outputTranslator(stdoutLines, stderrLines, cmd)
            logging.info('d')

            if isinstance(percentDoneInt, int):
                percent = percentDoneInt
            elif percent is not None:
                percentDoneInt = percent
            logging.info('e')

            # except Exception:
            #    pass

        # Caller wants to abort!
        if callback is not None and not callback(percentDoneInt, statusStr):
            try:
                pipe.terminate()
                pipe.kill()
            except Exception:
                logging.error('RunProcess: kill() or terminate() caused an exception')

            callbackReturnedFalse = True
            break
        logging.info('f')

        # Check if done
        if pipe.poll() is not None:
            break

        time.sleep(
            0.1
        )  # Polling frequency. Lengthening this will decrease responsiveness

    # Notify callback of exit. Check callballFinalize so we don't prematurely reset the progress bar
    if callback is not None and callBackFinalize is True:
        callback(True)

    # Callback aborted command
    if callbackReturnedFalse:
        logging.error('RunProcess was aborted by caller')
        # return False

    # result
    try:
        remainingStdout = ''
        remainingStderr = ''
        remainingStdout, remainingStderr = pipe.communicate()
    except IOError as e:
        logging.error('Encountered error communicating with sub-process' + str(e))

    success = pipe.returncode == 0
    stdout += str(remainingStdout)
    stderr += str(remainingStderr)

    # Logging
    if not __release__:
        logging.info(f'return:  {success}')
        if len(stdout) > 128:
            logging.info(f'stdout:  {stdout[:128]} ...')
        else:
            logging.info(f'stdout:  {stdout}')
        logging.error('stderr: ' + str(stderr))

    if returnOutput:
        return stdout, stderr  # , success
    else:
        return success


def enqueue_process_output(streamId, inStream, outQueue):
    for line in iter(inStream.readline, b''):
        # logging.info(streamId + ": " + line)
        outQueue.put(line)


def duration_str_to_milliseconds(str, throw_parse_error=False):
    """Convert a time or duration (hh:mm:ss.ms) string into a value in milliseconds."""
    if str is None:
        if throw_parse_error:
            raise ValueError('Invalid duration format')
        return 0

    r = re.compile('[^0-9]+')
    tokens = r.split(str)
    vid_len = (
        (int(tokens[0]) * 3600) + (int(tokens[1]) * 60) + (int(tokens[2]))
    ) * 1000 + int(tokens[3])
    return vid_len


def duration_str_to_sec(duration_str):
    ms = duration_str_to_milliseconds(duration_str)
    if ms == 0:
        return 0

    # return int((ms + 500) / 1000) # Rounding
    return int(ms / 1000)  # Floor


def milliseconds_to_duration_components(msTotal):
    secTotal = msTotal / 1000
    h = int(secTotal / 3600)
    m = int((secTotal % 3600) / 60)
    s = int(secTotal % 60)
    ms = int(msTotal % 1000)

    return [h, m, s, ms]


def milliseconds_to_duration_str(msTotal):
    dur = milliseconds_to_duration_components(msTotal)
    return '%02d:%02d:%02d.%03d' % (dur[0], dur[1], dur[2], dur[3])


def re_scale(val, oldScale, newScale):
    OldMax = oldScale[1]
    OldMin = oldScale[0]
    NewMax = newScale[1]
    NewMin = newScale[0]
    OldValue = val
    OldRange = OldMax - OldMin
    NewRange = NewMax - NewMin
    NewValue = (((OldValue - OldMin) * NewRange) / OldRange) + NewMin
    return NewValue
