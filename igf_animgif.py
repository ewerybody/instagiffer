import base64
import configparser
import glob
import hashlib
import json
import locale
import logging
import math
import os
import random
import re
import shutil
import subprocess
import time
import traceback
import uuid

import PIL.Image
import PIL.ImageDraw

import igf_common
import igf_paths
from igf_common import IM_A_MAC, IM_A_PC, __release__, re_scale, run_process

if IM_A_PC:
    # Windows uses the PIL ImageGrab module for screen capture
    import PIL.ImageGrab

CAPTURE_RE = r'^::capture ([\.0-9]+) ([\.0-9]+) ([0-9]+)x([0-9]+)\+(\-?[0-9]+)\+(\-?[0-9]+) cursor=(\d+) retina=(\d+) web=(\d+)$'
IMGUR_API_URL = 'https://api.imgur.com/3/upload.json'
__imgur_cid__ = '58fc34d08ab311d'


class AnimatedGif:
    """
    Try to keep this class fully de-coupled from the GUI. lol.
    """

    description = 'Animated Gif Engine'
    author = 'Justin Todd'

    def __init__(
        self, config: igf_common.InstaConfig, mediaLocator, workDir, periodicCallback, rootWindow
    ):
        self.conf = config
        self.workDir = workDir
        self.callback = periodicCallback
        self.origURL = mediaLocator
        self.isUrl = False
        self.videoWidth = 0
        self.videoHeight = 0
        self.videoLength = None
        self.videoFps: float = 0.0
        self.videoPath = None
        self.videoFileName = ''
        self.imageSequence = []
        self.imageSequenceCropParams = None  # At the moment, used for mac screen grab only. When the image sequence is "extracted" we sneak in the crop operation instead of resizing
        self.fonts: None | ImagemagickFont = None
        self.rootWindow = rootWindow  # Needed for mouse cursor
        self.gifCreated = False
        self.gifOutPath: str | None = None  # Warning: Don't use this directly!
        self.lastSavedGifPath = None
        self.overwriteGif = True
        self.frameDir = workDir + os.sep + 'original'
        self.resizeDir = workDir + os.sep + 'resized'
        self.processedDir = workDir + os.sep + 'processed'
        self.captureDir = workDir + os.sep + 'capture'
        self.maskDir = workDir + os.sep + 'mask'
        self.downloadDir = workDir + os.sep + 'downloads'
        self.previewFile = workDir + os.sep + 'preview.gif'
        self.vidThumbFile = workDir + os.sep + 'thumb.png'
        self.blankImgFile = workDir + os.sep + 'blank.gif'
        self.audioClipFile = workDir + os.sep + 'audio.wav'

        self.OverwriteOutputGif(self.conf.GetParamBool('settings', 'overwriteGif'))

        if self.conf.GetParam('paths', 'gifOutputPath').lower() == 'default':
            self.gifOutPath = self.GetDefaultOutputDir() + os.sep + 'insta.gif'
        else:
            self.gifOutPath = self.conf.GetParam('paths', 'gifOutputPath')

        startupLog = 'AnimatedGif::  gifOut: [' + self.gifOutPath + ']'
        startupLog = (
            'AnimatedGif:: media: ['
            + mediaLocator
            + '], workingDir: ['
            + workDir
            + '], gifOut: ['
            + self.GetNextOutputPath()
            + ']'
        )
        logging.info(startupLog)

        for path, name in (
            (os.path.dirname(self.gifOutPath), 'gif output'),
            (self.frameDir, None),
            (self.resizeDir, None),
            (self.processedDir, None),
            (self.downloadDir, None),
            (self.captureDir, None),
            (self.maskDir, None),
        ):
            if os.path.isdir(path):
                continue
            os.makedirs(path)
            if not os.path.isdir(path):
                self.FatalError(f'Failed to create {name or "working"} directory: {path}')

        self.LoadFonts()
        logging.info('4')
        self.CheckPaths()
        logging.info('5')
        self.DeleteResizedImages()
        logging.info('6')
        self.DeleteExtractedImages()
        logging.info('7')
        self.DeleteProcessedImages()
        logging.info('8')
        self.DeleteCapturedImages()
        self.DeleteMaskImages()
        self.DeleteAudioClip()

        # self.DeleteGifOutput()

        mediaLocator = self.ResolveUrlShortcutFile(mediaLocator)

        logging.info('Analyzing the media path to determine what kind of video this is...')
        self.isUrl = igf_paths.is_url(mediaLocator)
        captureRe = re.findall(CAPTURE_RE, mediaLocator)
        isImgSeq = '|' in mediaLocator or igf_paths.is_picture_file(mediaLocator)

        if captureRe and len(captureRe[0]) == 9:
            logging.info('Media locator indicates screen capture')
            capDuration = float(captureRe[0][0])
            capTargetFps = float(captureRe[0][1])
            capWidth = int(captureRe[0][2])
            capHeight = int(captureRe[0][3])
            capX = int(captureRe[0][4])
            capY = int(captureRe[0][5])
            cursorOn = int(captureRe[0][6])
            retina = int(captureRe[0][7])
            web = int(captureRe[0][8])

            self.Capture(
                capDuration,
                capTargetFps,
                capWidth,
                capHeight,
                capX,
                capY,
                cursorOn,
                retina,
                web,
            )

        elif isImgSeq:
            logging.info('Media Locator is an image sequence')

            for fname in mediaLocator.split('|'):
                if len(fname):
                    self.imageSequence.append(fname)

            # Arbitrarily pick an FPS of 10 for image sequences
            self.videoFps = 10.0

        else:
            if self.isUrl:
                logging.info('Media locator is a URL')
                self.downloadQuality = self.conf.GetParam('settings', 'downloadQuality')
                self.videoPath = self.DownloadVideo(mediaLocator)
            else:
                logging.info('Media locator points to a local file')
                self.videoPath = igf_paths.cleanup_path(mediaLocator)
                self.videoFileName = os.path.basename(mediaLocator)

        self.GetVideoParameters()

    def ResolveUrlShortcutFile(self, filename) -> str:
        """Given a Windows .url filename, returns the main URL, or argument passed in if it can't
        find one."""

        fname, fext = os.path.splitext(filename)
        if not fext or len(fext) == 0 or str(fext.lower()) != '.url':
            return filename

        # Windows .URL file format is compatible with built-in ConfigParser class.
        config = configparser.RawConfigParser()
        try:
            config.read(filename)
        except Exception:
            return filename

        # Return the URL= value from the [InternetShortcut] section.
        if config.has_option('InternetShortcut', 'url'):
            return config.get('InternetShortcut', 'url').strip('"')
        # If there is none, return the BASEURL= value from the [DEFAULT] section.
        if 'baseurl' in list(config.defaults().keys()):
            return config.defaults()['baseurl'].strip('"')
        else:
            return filename

    def GetConfig(self) -> igf_common.InstaConfig:
        return self.conf

    def Capture(self, seconds, targetFps, width, height, x, y, showCursor, retinaDisplay, web):
        if seconds < 1:
            return False

        # Capture as fast as possible
        imgIdx = 1
        nowTs = time.time()
        endTs = nowTs + seconds
        nextFrameTs = nowTs

        #
        imgDataArray = []
        imgDimensions = (0, 0)

        if retinaDisplay:
            width *= 2
            height *= 2
            x *= 2
            y *= 2

        resizeRatio = 1.0

        # Max width/height restrictions
        if web:
            maxWH = int(self.conf.GetParam('screencap', 'webMaxWidthHeight'))
            targetFps = int(self.conf.GetParam('screencap', 'webMaxFps'))

            if width >= height and width > maxWH:
                resizeRatio = maxWH / float(width)
            elif height >= width and height > maxWH:
                resizeRatio = maxWH / float(height)

        while time.time() < endTs:
            # Rate-limiting
            if targetFps != 0:
                nowTs = time.time()
                if nowTs < nextFrameTs:
                    time.sleep(nextFrameTs - nowTs)
                nextFrameTs = time.time() + 1.0 / targetFps

            # Filename
            capFileName = self.GetCapturedImagesDir() + 'cap%04d' % (imgIdx)

            if IM_A_PC:
                capFileName += '.bmp'

                try:
                    img = PIL.ImageGrab.grab((x, y, x + width, y + height))
                except MemoryError:
                    self.callback(True)
                    self.FatalError(
                        'Ran out of memory during screen capture. Try recording a smaller area, or decreasing your duration.'
                    )
                    return False

                imgDimensions = img.size

                if showCursor:
                    # Get mouse cursor position
                    cursorX, cursorY = self.rootWindow.winfo_pointerxy()

                    if cursorX > x and cursorX < x + width and cursorY > y and cursorY < y + height:
                        # Draw Cursor (Just a dot for now)
                        r = 2  # radius
                        draw = PIL.ImageDraw.Draw(img)
                        draw.ellipse(
                            (
                                cursorX - x - r,
                                cursorY - y - r,
                                cursorX - x + r,
                                cursorY - y + r,
                            ),
                            fill='#ffffff',
                            outline='#000000',
                        )

                if self.conf.GetParamBool('screencap', 'DirectToDisk'):
                    img.save(capFileName)
                else:
                    # imgDataArray.append(img.tostring()) # PIL
                    imgDataArray.append(img.tobytes())  # PILLOW

            elif IM_A_MAC:
                capFileName += '.bmp'  # Supported formats: png, bmp jpg

                scrCapCmd = 'screencapture -x '

                if showCursor:
                    scrCapCmd += '-C '

                scrCapCmd += '"%s"' % (capFileName)

                os.system(scrCapCmd)

            self.imageSequence.append(capFileName)
            imgIdx += 1
            self.callback(False)

        # Post-process
        if IM_A_PC:
            if not self.conf.GetParamBool('screencap', 'DirectToDisk'):
                logging.info('Using fps-optimized screen cap')

                frameCount = 0
                for x in range(0, len(imgDataArray)):
                    try:
                        capPath = self.imageSequence[frameCount]
                    except IndexError:
                        break

                    # PIL uses fromstring
                    PIL.Image.frombytes('RGB', imgDimensions, imgDataArray[x]).resize(
                        (int(width * resizeRatio), int(height * resizeRatio)),
                        PIL.Image.Resampling.BICUBIC,
                    ).save(capPath)
                    if os.path.exists(capPath):
                        frameCount += 1
                    else:
                        logging.error(
                            'Capture file ' + capPath + ' was not saved to disk for some reason'
                        )

                # Trim the list to the actual size
                missingCount = len(self.imageSequence) - frameCount
                if missingCount != 0:
                    logging.error(
                        'Not all capture files were accounted for: %d missing ' % (missingCount)
                    )
                    self.imageSequence = self.imageSequence[
                        0 : min(frameCount, len(self.imageSequence))
                    ]

        # Screen capper for Mac can't crop a specific region, so we need to do it
        # Todo: for some reason, PNG to PNG doesn't work!!
        if IM_A_MAC:
            for i in range(0, len(self.imageSequence)):
                newFileName = (
                    os.path.dirname(self.imageSequence[i])
                    + '/'
                    + os.path.splitext(os.path.basename(self.imageSequence[i]))[0]
                    + '.jpg'
                )

                cmdConvert = (
                    '"%s" -comment "Converting captured frames:%d" -comment "instagiffer" "%s" -quality 100%% %s "%s"'
                    % (
                        self.conf.GetParam('paths', 'convert'),
                        i * 100 / len(self.imageSequence),
                        self.imageSequence[i],
                        '-crop %dx%d+%d+%d' % (width, height, x, y),
                        newFileName,
                    )
                )

                self.imageSequence[i] = newFileName

                if not run_process(cmdConvert, self.callback, False, False):
                    self.FatalError(
                        'Unable to crop screen capture frame: '
                        + os.path.basename(self.imageSequence[x])
                    )

            self.callback(True)

        self.videoLength = '00:00:%02d.000' % (seconds)
        self.videoFps = imgIdx / seconds
        self.callback(True)
        logging.info('Capture complete. FPS achieved: %f' % (self.videoFps))

        return True

    def CheckPaths(self):
        if self.gifOutPath is None:
            return False

        if not os.access(os.path.dirname(self.gifOutPath), os.W_OK):
            logging.error('Warning. ' + os.path.dirname(self.gifOutPath) + ' is not writable')

        if not os.path.exists(self.conf.GetParam('paths', 'ffmpeg')):
            self.FatalError('ffmpeg not found')
        elif not os.path.exists(self.conf.GetParam('paths', 'convert')):
            self.FatalError('imagemagick convert not found')
        # elif not os.path.exists(self.conf.GetParam('paths', 'youtubedl')):
        #     self.FatalError('Youtube-dl not found')
        # elif not os.path.exists(self.conf.GetParam('paths','gifsicle')):
        #     self.FatalError("gifsicle not found")
        elif self.videoPath is not None and not os.path.exists(self.videoPath):
            self.FatalError("Local video file '" + self.videoPath + "' does not exist")

        logging.info('Check paths... OK')
        return True

    def LoadFonts(self):
        logging.info('Retrieve font list...')
        cmdListFonts = f'{self.conf.GetParam("paths", "convert")} -list font'
        t0 = time.perf_counter()
        exitcode, fonts_output = subprocess.getstatusoutput(cmdListFonts)
        self.fonts = ImagemagickFont(fonts_output)
        logging.info(f'  took {time.perf_counter() - t0:.3f}s')

    def GetFonts(self) -> ImagemagickFont:
        return self.fonts

    def SetSavePath(self, savePath):
        self.gifOutPath = savePath

    def RotateImageFile(self, fileName, rotateDegrees):
        if rotateDegrees % 360 == 0:
            return True

        cmdRotate = '"%s" "%s" -rotate %d -alpha remove -alpha off "%s"' % (
            self.conf.GetParam('paths', 'convert'),
            fileName,
            rotateDegrees,
            fileName,
        )

        if not run_process(cmdRotate, self.callback, False):
            self.FatalError('Unable to rotate image %s by %d degrees' % (fileName, rotateDegrees))
            return False

        return True

    def ExportFrames(self, start, end, prefix, includeCropAndResize, rotateDeg, path):
        files = []

        if includeCropAndResize:
            files = self.GetResizedImageList()
        else:
            files = self.GetExtractedImageList()

        logging.info('Export frames %d to %d' % (start, end))
        x = 1

        for i in range(start, end + 1):
            fromFile = files[i - 1]
            toFile = '%s%s%04d.png' % (path + os.sep, prefix, x)
            logging.info('Export %s to %s...' % (fromFile, toFile))

            try:
                shutil.copy(fromFile, toFile)
            except Exception:
                self.callback(True)
                return False
                # self.FatalError("Unable to export frames to the directory specified. Are you sure you can write files to this location?")

            self.RotateImageFile(toFile, rotateDeg)

            self.callback(False)
            x += 1

        self.callback(True)
        return True

    # If manual deletions are made, the enumeration gets messed up, which screws up the import
    def ReEnumerateExtractedFrames(self):
        if not self.ExtractedImagesExist():
            return True
        return self.ReEnumeratePngFrames(self.GetExtractedImagesDir(), self.GetExtractedImageList())

    def ReEnumeratePngFrames(self, directory, imageList):
        imageList.sort()
        retVal = True

        x = 1

        if len(imageList) > 0:
            logging.info('Re-enumerate %d files starting with %s' % (len(imageList), imageList[0]))

        for fromFile in imageList:
            self.callback(False)

            toFile = '%simage%04d.png' % (directory + os.sep, x)
            # logging.info("Re-enumerate %s to %s" % (fromFile, toFile))
            try:
                shutil.move(fromFile, toFile)
            except Exception:
                retVal = False
                break

            x += 1

        self.callback(True)

        return retVal

    def ReverseFrames(self):
        # Get current image list
        currentImgList = self.GetExtractedImageList()
        numImgs = len(currentImgList)

        def GetOrigName(idx):
            return '%simage%04d.png' % (self.GetExtractedImagesDir() + os.sep, idx)

        def GetRenamedName(idx):
            return '%scurrent_image%04d.png' % (
                self.GetExtractedImagesDir() + os.sep,
                idx,
            )

        for x in range(0, len(currentImgList)):
            toFile = GetRenamedName(x + 1)
            logging.info('Temporarily rename image %s to %s' % (currentImgList[x], toFile))
            shutil.move(currentImgList[x], toFile)

        for x in range(0, len(currentImgList)):
            fromFile = GetRenamedName(numImgs - x)
            toFile = GetOrigName(x + 1)
            logging.info('Move %s to %s' % (fromFile, toFile))
            shutil.move(fromFile, toFile)

        return True

    def CreateBlankFrame(self, color):
        cmdConvert = '"%s" -size %dx%d xc:%s "%s"' % (
            self.conf.GetParam('paths', 'convert'),
            self.GetVideoWidth(),
            self.GetVideoHeight(),
            color,
            self.blankImgFile,
        )

        if not run_process(cmdConvert, self.callback, False) or not os.path.exists(
            self.blankImgFile
        ):
            self.FatalError("Couldn't create blank image!")

        return True

    def CreateCrossFade(self, start, end):
        totCount = self.GetNumFrames()
        if start > end:
            xfadeFrames = totCount - (start - end)
        else:
            xfadeFrames = end - start

        if xfadeFrames < 3:
            return False

        if xfadeFrames % 2:
            if end < totCount:
                xfadeFrames += 1
            elif start > 1:
                xfadeFrames += 1
                start -= 1

        xfadeLen = xfadeFrames / 2

        logging.info(
            'Create cross fade between %d and %d (%d fade frames) - fade length: %d - %d frames total'
            % (start, end, xfadeFrames, xfadeLen, totCount)
        )

        origImgList = self.GetExtractedImageList()

        # Add up to start
        for x in range(0, xfadeLen):
            fadePercent = (x + 1) * 100 / (xfadeLen + 1)
            ia = (start - 1 + x) % totCount
            ib = (start - 1 + x + xfadeLen) % totCount

            fa = origImgList[ia]
            fb = origImgList[ib]

            logging.info('xfade %d with %d by %d percent' % (ia + 1, ib + 1, fadePercent))

            cmdConvert = (
                '"%s" -comment "Creating cross-fade:%d" "%s" "%s" -alpha on -compose dissolve -define compose:args=%d -composite "%s"'
                % (
                    self.conf.GetParam('paths', 'convert'),
                    x * 100 / xfadeLen,
                    fa,
                    fb,
                    fadePercent,
                    fa,
                )
            )

            if not run_process(cmdConvert, self.callback, False):
                self.DeleteExtractedImages()
                self.FatalError("Couldn't fade!")

            try:
                os.remove(fb)
            except Exception:
                self.DeleteExtractedImages()
                self.FatalError("Couldn't delete frame: " + fb)

        if not self.ReEnumerateExtractedFrames():
            self.FatalError('Failed to re-enumerate frames')

        return True

    def ImportFrames(
        self,
        start,
        importedImgList,
        reverseImport,
        insertAfter,
        riffleShuffle,
        keepAspectRatio,
    ):
        logging.info('Import image sequence: ' + ', '.join(importedImgList))

        importedImgList.sort()

        numNewFiles = len(importedImgList)

        if numNewFiles <= 0:
            return False

        # Check for blank frames
        for x in range(0, len(importedImgList)):
            i = importedImgList[x]
            if i.startswith('<') and i.endswith('>'):
                self.CreateBlankFrame(i.strip('<>'))
                importedImgList[x] = self.blankImgFile

        if insertAfter:
            start += 1

        # Get current image list
        currentImgList = self.GetExtractedImageList()

        # Temporarily rename existing images
        for x in range(0, len(currentImgList)):
            toFile = '%scurrent_image%04d.png' % (self.GetExtractedImagesDir(), x + 1)
            logging.info('Temporarily rename image %s to %s' % (currentImgList[x], toFile))

            if currentImgList[x] in importedImgList:
                shutil.copy(currentImgList[x], toFile)
            else:
                shutil.move(currentImgList[x], toFile)

            currentImgList[x] = toFile

        # Temporarily rename and resize imported images
        logging.info('Rename, resize and rotate imported image sequence')

        def GetImportFileName(idx):
            return '%simported_image%04d.png' % (self.GetExtractedImagesDir(), x)

        x = 1
        # totalCount = len(importedImgList)
        newImportList = list()
        for importFile in importedImgList:
            toFile = GetImportFileName(
                x
            )  # "%simported_image%04d.png" % (self.GetExtractedImagesDir() + os.sep, x)
            logging.info("Copy and resize '%s' to '%s'" % (importFile, toFile))

            aspectRatioModifier = ''
            if keepAspectRatio:
                # ( -clone 0 -blur 0x9 -resize %dx%d! ) ( -clone 0 -resize WxH ) -delete 0
                aspectRatioModifier = ' -background black -gravity center -extent %dx%d ' % (
                    self.GetVideoWidth(),
                    self.GetVideoHeight(),
                )
            else:
                aspectRatioModifier = '! '

            # this is a bit weird, because if user imports a gif, the number of frames increases and im blocks for a long time
            percentDone = (x - 1) * 100 / len(importedImgList)
            if percentDone > 100 or igf_paths.is_gif(importFile):
                percentDone = -1

            comment = ' -comment "Importing frames:%d" -comment "instagiffer" ' % (percentDone)
            cmdConvert = '"%s" %s "%s" -resize %dx%d%s "%s"' % (
                self.conf.GetParam('paths', 'convert'),
                comment,
                importFile,
                self.GetVideoWidth(),
                self.GetVideoHeight(),
                aspectRatioModifier,
                toFile,
            )

            if not run_process(cmdConvert, self.callback, False, False):
                self.DeleteExtractedImages()
                self.FatalError('Unable to resize import image %s. Import failed!' % (toFile))

            if os.path.exists(toFile):
                newImportList.append(toFile)
                x += 1
            else:
                fname, fext = os.path.splitext(toFile)

                sx = 0
                while True:
                    x += 1
                    toFile = GetImportFileName(x)
                    subFile = '%s-%d.png' % (fname, sx)
                    if not os.path.exists(subFile):
                        break
                    sx += 1

                    shutil.move(subFile, toFile)
                    newImportList.append(toFile)

                if sx == 0:
                    self.DeleteExtractedImages()
                    self.FatalError('Import error')

        # Sort alphabetical. Reverse?
        newImportList.sort(reverse=reverseImport)

        # Let the array magic begin
        newImgList = []

        # Add up to start
        for x in range(0, start - 1):
            newImgList.append(currentImgList.pop(0))

        # Add the new frames
        if not riffleShuffle:
            while len(newImportList):
                newImgList.append(newImportList.pop(0))

        # Add the rest of the frames after. Riffle shuffle occurs if there are still imported images in the list
        while len(currentImgList) or len(newImportList):
            if len(currentImgList):
                newImgList.append(currentImgList.pop(0))
            if len(newImportList):
                newImgList.append(newImportList.pop(0))

        # Properly name the files
        for x in range(0, len(newImgList)):
            toFile = '%simage%04d.png' % (self.GetExtractedImagesDir(), x + 1)
            logging.info('Move %s to %s' % (newImgList[x], toFile))
            shutil.move(newImgList[x], toFile)

        self.callback(True)
        return True

    def GetDefaultOutputDir(self):
        gifDir = os.path.expanduser('~')
        gifDir += os.sep + 'Desktop'

        try:
            gifDir.encode(locale.getpreferredencoding())
        except UnicodeError:
            logging.info(
                'GIF output directory is problematic due to non-latin characters: ' + gifDir
            )
            gifDir = igf_paths.get_fail_safe_dir(self.conf, gifDir)

        return gifDir

    def OverwriteOutputGif(self, enable):
        self.overwriteGif = enable

    def GetLastGifOutputPath(self):
        return self.lastSavedGifPath

    def GetNextOutputPath(self):
        if self.gifOutPath is None:
            return ''
        file_name = str(self.gifOutPath)

        if not self.overwriteGif:
            # If overwrite is off, figure out what next file is
            orig_file_name = self.gifOutPath
            # prevFileName = orig_file_name
            idx = 1
            # largestTs = 0

            while True:
                if not os.path.isfile(file_name):
                    break
                # else:
                #     fileTs = os.stat(file_name).st_mtime
                #     if fileTs < largestTs:
                # broken sequence
                #     else:
                #         largestTs = fileTs

                # prevFileName = file_name
                file_name = (
                    os.path.dirname(orig_file_name)
                    + os.sep
                    + os.path.splitext(os.path.basename(orig_file_name))[0]
                    + '%03d.%s' % (idx, self.GetFinalOutputFormat())
                )
                idx += 1

        if not file_name:
            self.FatalError('Configuration error detected. No GIF output path specified.')

        return file_name

    # Error handler
    def FatalError(self, message):
        logging.error('FatalError occurred in the animation core: ' + message)
        logging.debug('Stack:')
        for line in traceback.format_stack():
            logging.error(line.strip())

        logging.debug(self.conf)
        self.callback(True)

        raise RuntimeError(message)

    def GetVideoThumb(self, timeStr, maxWidthHeight):
        if not self.SourceIsVideo():
            return False

        run_process(
            '"'
            + self.conf.GetParam('paths', 'ffmpeg')
            + '" -loglevel panic -i "%s" -y -ss %s -vframes 1 "%s"'
            % (self.videoPath, timeStr, self.GetThumbImagePath()),
            None,
            False,
        )

        return True

    def GetThumbImagePath(self):
        return self.vidThumbFile

    def ThumbFileExists(self):
        return os.path.exists(self.GetThumbImagePath())

    def GetThumbAge(self):
        if self.ThumbFileExists():
            return time.time() - os.stat(self.GetThumbImagePath()).st_mtime
        else:
            return 1000

    def GetVideoParameters(self):
        mediaPath = None

        if self.videoPath is None:
            mediaPath = self.imageSequence[0]
        else:
            # Check path against invalid extensions list
            invalidExtensions = ['.exe', '.bat']

            for invalidExtension in invalidExtensions:
                if invalidExtension in self.videoPath:
                    self.FatalError('This video contains an unsupported file extension')

            mediaPath = self.videoPath

        logging.info('Extracting video information from ' + mediaPath)

        if not os.path.exists(mediaPath):
            self.FatalError("'" + mediaPath + "' does not exist!")

        cmd = f'"{self.conf.GetParam("paths", "ffmpeg")}" -i "{igf_paths.cleanup_path(mediaPath)}"'
        logging.info(f'ffmpeg cmd: {cmd}')
        output = subprocess.getoutput(cmd)
        # stdout, stderr = run_process(cmd, None, True)

        pattern = re.compile(r'Stream.*Video.* ([0-9]+)x([0-9]+)')
        match = pattern.search(output)

        if match:
            w, h = list(map(int, match.groups()[0:2]))
            self.videoWidth = w
            self.videoHeight = h
        else:
            self.FatalError('Unable to get video width and height parameters.')

        # Display aspect ratio - non square pixels
        pattern = re.compile(
            r'Stream #0.+Video.+\[SAR (\d+):(\d+) DAR (\d+):(\d+)\]'
        )  # older versions of ffmpeg
        match = pattern.search(output)

        if match:
            sarX, sarY, darX, darY = list(map(int, match.groups()[0:4]))

            rDar = darX / float(darY)
            rSar = sarX / float(sarY)

            if rSar != 1.0 and rDar != rSar:
                logging.info(
                    'Storage aspect ratio (%.2f) differs from display aspect ratio (%.2f)'
                    % (rSar, rDar)
                )
                self.videoWidth = self.videoHeight * rDar

        # Side Rotation
        pattern = re.compile(r'\s+rotate\s+:\s+(90|270|-90|-270)')
        match = pattern.search(output)

        if match:
            logging.info('Side rotation detected')
            # rotation = int(match.groups()[0])
            self.videoWidth, self.videoHeight = self.videoHeight, self.videoWidth

        # Try to get length
        pattern = re.compile(r'Duration: ([0-9\.:]+),')
        match = pattern.search(output)

        if self.videoPath and match:
            self.videoLength = match.groups()[0]

        # Try to get fps
        pattern = re.compile(r'Video:.+?([0-9\.]+) tbr')
        match = pattern.search(output)

        if self.videoPath and match:
            self.videoFps = float(match.groups()[0])
        elif self.videoFps <= 0.0:
            self.videoFps = 10.0
            logging.info(
                'Unable to determine frame rate! Arbitrarily setting it to %d' % (self.videoFps)
            )

        logging.info(
            'Video Parameters: %dx%d (%d:%d or %0.3f:1); %d fps'
            % (
                self.GetVideoWidth(),
                self.GetVideoHeight(),
                self.GetVideoWidth() / math.gcd(self.GetVideoWidth(), self.GetVideoHeight()),
                self.GetVideoHeight() / math.gcd(self.GetVideoWidth(), self.GetVideoHeight()),
                self.GetVideoWidth() / float(self.GetVideoHeight()),
                self.GetVideoFps(),
            )
        )

        return True

    def GetResizedImagesDir(self):
        return self.resizeDir + os.sep

    def GetResizedImagesLastModifiedTs(self):
        if self.ResizedImagesExist():
            largestTimestamp = os.stat(self.GetResizedImagesDir()).st_mtime
            files = glob.glob(self.GetResizedImagesDir() + '*')

            for f in files:
                if os.stat(f).st_mtime > largestTimestamp:
                    largestTimestamp = os.stat(f).st_mtime
            return largestTimestamp
        else:
            return 0

    def GetResizedImageList(self, idx=None):
        ret = []
        files = glob.glob(self.GetResizedImagesDir() + '*')
        for f in files:
            if idx is not None:
                origFiles = self.GetExtractedImageList()
                f = self.GetResizedImagesDir() + os.path.basename(origFiles[idx - 1])
                return f
                # doesn't handle deleted frames - DOESN'T WORK
                # imageName = "image%04d.png" % (idx)
                # if imageName in f:
                #     return f

            ret.append(f)

        return ret

    def ResizedImagesExist(self):
        count = 0
        files = glob.glob(self.GetResizedImagesDir() + '*')
        for f in files:
            count = count + 1

        if count > 0:
            return True
        else:
            return False

    def DeleteResizedImages(self):
        files = glob.glob(self.resizeDir + os.sep + '*')
        for f in files:
            try:
                os.remove(f)
            except Exception:  # WindowsError:
                logging.error(f"Can't delete {f}")

    def GetExtractedImagesDir(self):
        return self.frameDir + os.sep

    def GetExtractedImagesLastModifiedTs(self):
        if self.ExtractedImagesExist():
            largestTimestamp = os.stat(self.GetExtractedImagesDir()).st_mtime
            files = glob.glob(self.GetExtractedImagesDir() + '*')

            for f in files:
                if os.path.exists(f) and os.stat(f).st_mtime > largestTimestamp:
                    largestTimestamp = os.stat(f).st_mtime
            return largestTimestamp
        else:
            return 0

    def ExtractedImagesExist(self):
        count = 0
        files = glob.glob(self.GetExtractedImagesDir() + '*')
        for f in files:
            count = count + 1

        if count > 0:
            return True
        else:
            return False

    def GetNumFrames(self):
        return len(self.GetExtractedImageList())

    def GetExtractedImageList(self):
        ret = []
        files = glob.glob(self.GetExtractedImagesDir() + '*')
        for f in files:
            ret.append(f)
        return ret

    def DeleteExtractedImages(self):
        files = glob.glob(self.GetExtractedImagesDir() + '*')
        for f in files:
            try:
                os.remove(f)
            except Exception:  # WindowsError:
                error_msg = (
                    f"Can't delete the following file:\n\n{f}\n\nIs it open in another program?"
                )
                self.FatalError(error_msg)

    def GetProcessedImagesDir(self):
        return self.processedDir + os.sep

    def GetProcessedImageList(self):
        ret = []
        files = glob.glob(self.GetProcessedImagesDir() + '*.' + self.GetIntermediaryFrameFormat())
        for f in files:
            ret.append(f)
        return ret

    def DeleteProcessedImages(self):
        if os.path.exists(self.previewFile):
            try:
                os.remove(self.previewFile)
            except Exception:
                pass

        files = glob.glob(self.GetProcessedImagesDir() + '*')

        for f in files:
            try:
                os.remove(f)
            except Exception:  # WindowsError:
                error_msg = "Can't delete %s. Is it open in another program?" % (f)
                self.FatalError(error_msg)

    def GetCapturedImagesDir(self):
        return self.captureDir + os.sep

    def DeleteCapturedImages(self):
        files = glob.glob(self.GetCapturedImagesDir() + '*')
        for f in files:
            os.remove(f)

    def GetGifLastModifiedTs(self):
        if self.GifExists():
            return os.stat(self.GetLastGifOutputPath()).st_mtime
        else:
            return 0

    def GifExists(self):
        return self.gifCreated and os.path.exists(self.GetLastGifOutputPath())

    def DeleteGifOutput(self):
        if self.gifOutPath is None or self.overwriteGif:
            return
        if os.path.isfile(self.gifOutPath):
            try:
                os.remove(self.gifOutPath)
            except Exception:
                self.FatalError('Failed to delete GIF out file. Is it use?')

    def UploadGifToImgur(self):
        import urllib.request
        import urllib.parse

        if not self.GifExists():
            self.FatalError("Can't find the GIF. Unable to upload to Imgur")
            return None

        try:
            b64Image = base64.b64encode(open(self.GetLastGifOutputPath(), 'rb').read())
        except Exception:
            return None

        data = (
            (
                'key',
                __imgur_cid__,
            ),
            ('image', b64Image.decode()),
            ('type', 'base64'),
            ('name', 'Instagiffer.gif'),
            ('title', 'Created and uploaded using Instagiffer'),
        )

        req = urllib.request.Request(IMGUR_API_URL, urllib.parse.urlencode(data).encode('utf-8'))
        req.add_header('Authorization', 'Client-ID ' + __imgur_cid__)

        try:
            response = urllib.request.urlopen(req)
        except Exception:
            return None

        response = json.loads(response.read().decode('utf-8'))

        imgUrl = response['data']['link']
        logging.info('Imgur URL: ' + imgUrl)

        if self.rootWindow is not None:
            self.rootWindow.clipboard_clear()
            self.rootWindow.clipboard_append(imgUrl)

        return imgUrl

    def GetMaskFileName(self, maskIdx):
        return f'{self.maskDir}{os.sep}image{maskIdx + 1:04d}.png'

    def DeleteMaskImages(self):
        files = glob.glob(self.maskDir + os.sep + '*')
        for f in files:
            os.remove(f)

    def CopyFramesToResizeFolder(self):
        # Copy extracted images over again
        files = glob.glob(self.frameDir + os.sep + '*')
        for f in files:
            self.callback(False)
            shutil.copy(f, self.resizeDir)
        self.callback(True)

    def CopyFramesToProcessedFolder(self):
        # Copy extracted images over again
        files = glob.glob(self.resizeDir + os.sep + '*')
        for f in files:
            shutil.copy(f, self.processedDir)

    def IsDownloadedVideo(self):
        return self.isUrl

    def GetVideoFileName(self):
        if self.videoFileName != '':
            return self.videoFileName

        if self.isUrl and len(self.origURL):
            cmdVideoTitle = (
                '"'
                + self.conf.GetParam('paths', 'youtubedl')
                + '"'
                + ' --get-filename '
                + ' "'
                + self.origURL
                + '"'
            )

            stdout, stderr = run_process(cmdVideoTitle, self.callback, True)

            if stdout != '':
                self.videoFileName = stdout.strip()
                return self.videoFileName

        return self.videoFileName

    def SaveOriginalVideoAs(self, newFileName):
        if self.videoPath is not None and len(self.videoPath):
            self.callback(False)
            shutil.copy(self.videoPath, newFileName)
        self.callback(True)

    def GetDownloadedQuality(self):
        return self.downloadQuality

    def DeleteAudioClip(self):
        try:
            os.remove(self.GetAudioClipPath())
        except Exception:
            pass

    def GetAudioClipPath(self):
        if os.path.exists(self.audioClipFile):
            return self.audioClipFile
        else:
            return None

    def ExtractAudioClip(self):
        audioPath = self.conf.GetParam('audio', 'path')
        startTimeStr = float(self.conf.GetParam('audio', 'startTime'))
        volume = int(self.conf.GetParam('audio', 'volume')) / 100.0
        durationSec = self.GetTotalRuntimeSec()

        if len(audioPath) == 0:
            return None

        try:
            os.remove(self.audioClipFile)
        except Exception:
            pass

        cmdExtractImages = '"%s" -y -v verbose -ss %s -t %.1f -i "%s" -af "volume=%.1f" "%s"' % (
            self.conf.GetParam('paths', 'ffmpeg'),
            startTimeStr,
            durationSec,
            audioPath,
            volume,
            self.audioClipFile,
        )

        success = run_process(cmdExtractImages, self.callback)

        if not success:
            return None
        else:
            return self.GetAudioClipPath()

    def DownloadAudio(self, url):
        # Make sure they don't download a playlist
        if url.lower().find('youtube') != -1 and url.find('&list=') != -1:
            logging.info('Youtube playlist detected. Removing playlist component from URL')
            url, sep, extra = url.partition('&list=')

        downloadFileName = self.downloadDir + os.sep + 'audiofile_' + str(uuid.uuid4())

        cmdVideoDownload = (
            '"'
            + self.conf.GetParam('paths', 'youtubedl')
            + '"'
            + ' -v '
            + ' --ffmpeg-location "'
            + self.conf.GetParam('paths', 'ffmpeg')
            + '"'
            + ' --restrict-filenames '
            + ' --no-check-certificate '
            + ' --newline '
            + ' -f bestaudio '
            + ' -o "'
            + downloadFileName
            + '"'
            + '   "'
            + url
            + '"'
        )
        # + ' -o "' + self.downloadDir + os.sep + '%(title)s.%(ext)s"' \

        stdout, stderr = run_process(cmdVideoDownload, self.callback, True)

        # matches1 = re.findall('\[download\] (.+) has already been downloaded', stdout, re.MULTILINE)
        # matches2 = re.findall(' Destination: (.+)', stdout, re.MULTILINE)

        # if matches1:
        #     downloadFileName = matches1[0]
        # elif matches2:
        #     downloadFileName = matches2[0]

        if not os.path.exists(downloadFileName):
            err_str = 'youtube-dl failed to download audio track'
            logging.error(err_str)
            self.FatalError(err_str)

        return downloadFileName

    def DownloadVideo(self, url):
        downloadFileName = self.downloadDir + os.sep + 'videofile_' + str(uuid.uuid4())

        maxHeight = 360

        if self.downloadQuality == 'Low':
            maxHeight = 240
        elif self.downloadQuality == 'Medium':
            maxHeight = 360
        elif self.downloadQuality == 'High':
            maxHeight = 720
        elif self.downloadQuality == 'Highest':
            maxHeight = 1080

        # Make sure they don't download a playlist
        if url.lower().find('youtube') != -1 and url.find('&list=') != -1:
            logging.info('Youtube playlist detected. Removing playlist component from URL')
            url, sep, extra = url.partition('&list=')

        # Build format str
        fmtStr = f'"[height<=?{maxHeight}]"'
        if self.downloadQuality == 'Highest':
            fmtStr = 'bestvideo'

        fmtStr = ' --format ' + fmtStr

        # Don't specify
        if self.downloadQuality == 'None':
            fmtStr = ''

        cmdVideoDownload = (
            '"'
            + self.conf.GetParam('paths', 'youtubedl')
            + '"'
            + ' -v -k '
            + ' --ffmpeg-location /dont/use '
            + ' --no-check-certificate '
            + ' --newline '
            + fmtStr
            + ' -o "'
            + downloadFileName
            + '"'
            + '   "'
            + url
            + '"'
        )

        # stdout, stderr = run_process(cmdVideoDownload, self.callback, True)
        exitcode, stdout = subprocess.getstatusoutput(cmdVideoDownload)

        if exitcode != 0 and not os.path.isfile(downloadFileName):
            # Video didn't download. Let's see what happened
            error_msg = 'Failed to download video\n\n'
            for line in stdout.splitlines(True):
                if 'This video does not exist' in line:
                    error_msg += 'Video was not found.'
                elif 'Community Guidelines' in line:
                    error_msg += 'Video removed because it broke the rules'
                elif 'is not a valid URL' in line:
                    error_msg += 'This is an invalid video URL'
                elif any(x in line for x in ('10013', '11001', 'CERTIFICATE_VERIFY_FAILED')):
                    error_msg += 'Unable to download video. Bad URL? Is it a private video? Is your firewall blocking Instagiffer?'
                elif 'Signature extraction failed' in line or 'HTTP Error 403' in line:
                    error_msg += 'There appears to be copyright protection on this video. This frequently occurs with music videos. Ask the Instagiffer devs to release a new version to get around this, or use the screen capture feature.'
                elif line.endswith('yt-dlp: not found'):
                    error_msg += (
                        'The downloader app could not be found!\n'
                        'Get it from:\nhttps://github.com/yt-dlp/yt-dlp\n'
                        'Or on Linux do:\nsudo apt install yt-dlp\n'
                    )
                else:
                    error_msg += line

            logging.error('youtube-dl failed to download video')
            logging.error(stdout)
            self.FatalError(error_msg)

        return downloadFileName

    def SourceIsVideo(self):
        if self.videoPath is None and len(self.imageSequence) <= 0:
            self.FatalError('Something is wrong. No video, and no image sequence!')
        return self.videoPath is not None

    def IsSameVideo(self, pathCheck, dlQuality):
        if (
            self.SourceIsVideo()
            and self.isUrl
            and self.origURL == pathCheck
            and self.downloadQuality == dlQuality
        ):
            return True
        else:
            return False

    def ExtractFrames(self):
        # self.DeleteResizedImages()
        self.DeleteExtractedImages()

        doDeglitch = False

        # Video source?
        if self.SourceIsVideo():
            startTimeStr = self.conf.GetParam('length', 'starttime')
            durationSec = float(self.conf.GetParam('length', 'durationsec'))

            # User chose random start time
            if startTimeStr.lower() == 'random':
                vidLenMs = igf_common.duration_str_to_milliseconds(self.videoLength)
                startTimeStr = igf_common.milliseconds_to_duration_str(random.randrange(vidLenMs))
                logging.info(
                    'Pick random start time between 0 and %d ms -> %s' % (vidLenMs, startTimeStr)
                )

            # Grab the previous second. This is where the error is found
            if self.conf.GetParamBool('settings', 'fixSlowdownGlitch'):
                startTimeMs = igf_common.duration_str_to_milliseconds(startTimeStr)

                if startTimeMs > 2000:
                    startTimeMs = startTimeMs - 2000
                    startTimeStr = igf_common.milliseconds_to_duration_str(startTimeMs)
                    durationSec = durationSec + 2.0
                    doDeglitch = True
                    logging.info(
                        'Fixing FPS glitch. New start time: '
                        + startTimeStr
                        + '; New duration: '
                        + str(durationSec)
                    )

            # FFMPEG options (order matters!):
            # -sn: disable subtitles?
            # -t:  duration
            # -ss: start time
            # -i:  video path
            # -r:  frame rate

            if not __release__:
                verbosityLevel = 'verbose'
            else:
                verbosityLevel = 'verbose'  # error"

            cmdExtractImages = '"%s" -v %s -sn -t %.1f -ss %s -i "%s" -r %s "%simage%%04d.png"' % (
                self.conf.GetParam('paths', 'ffmpeg'),
                verbosityLevel,
                durationSec,
                startTimeStr,
                self.videoPath,
                self.conf.GetParam('rate', 'framerate'),
                self.frameDir + os.sep,
            )

            success = run_process(cmdExtractImages, self.callback)

            if not success:
                self.DeleteExtractedImages()

        else:  # Sequence
            resizeArg = ' -resize %dx%d!' % (
                self.GetVideoWidth(),
                self.GetVideoHeight(),
            )

            frameCount = 1
            for x in range(len(self.imageSequence)):
                if os.path.exists(self.imageSequence[x]):
                    cmdConvert = (
                        '"%s" -comment "Importing image seqeuence:%d" -comment "instagiffer" "%s" %s +set date:create +set date:modify "%s%s"'
                        % (
                            self.conf.GetParam('paths', 'convert'),
                            x * 100 / len(self.imageSequence),
                            self.imageSequence[x],
                            resizeArg,
                            self.frameDir + os.sep,
                            'image%04d.png' % (frameCount),
                        )
                    )

                    if run_process(cmdConvert, self.callback, False, False):
                        frameCount += 1
                    else:
                        logging.error(
                            "Unable to convert image '"
                            + os.path.basename(self.imageSequence[x])
                            + "' to png. Conversion failed."
                        )
                else:
                    logging.error(
                        "Unable to convert image '"
                        + os.path.basename(self.imageSequence[x])
                        + "' to png. File not found."
                    )

            self.callback(True)

        # Verify we have at least one extracted frame
        if not os.path.exists(self.frameDir + os.sep + 'image0001.png'):
            if self.GetVideoLength() is not None:
                if igf_common.duration_str_to_milliseconds(
                    self.conf.GetParam('length', 'starttime')
                ) > igf_common.duration_str_to_milliseconds(self.GetVideoLength()):
                    self.FatalError(
                        'Start time specified is greater than ' + self.GetVideoLength() + '.'
                    )
                else:
                    self.FatalError('Unsupported file type or DRM-protected.')
            else:
                self.FatalError(
                    "Unable to extract images. Your start time might be greater than the video's length, which is unknown."
                )

        # DEGLITCH
        # Delete the first second's worth of frames
        if doDeglitch:
            # self.callback(False, "De-glitch...")
            deleteCount = 2 * int(self.conf.GetParam('rate', 'framerate'))

            logging.info('Deglitch. Remove frames 1 to %d' % deleteCount)

            for x in range(1, deleteCount + 1):
                framePath = self.frameDir + os.sep
                framePath += 'image%04d.png' % (x)

                if not os.path.exists(framePath):
                    self.FatalError('De-glitch failed. Frame not found: ' + framePath)
                try:
                    os.remove(framePath)
                except Exception:  # WindowsError:
                    self.FatalError('De-glitch failed. Delete failed: ' + framePath)

                self.callback(False)
                # logging.info("Deglitch: Removed " + framePath)

            # re-numerate after de-glitch
            if not self.ReEnumerateExtractedFrames():
                self.FatalError('Failed to re-enumerate frames')

        # This command can take a while. Is it even necessary?
        # self.CopyFramesToResizeFolder()
        return True

    def CheckDuplicates(self, cull=False):
        dupCount = 0
        hashes = {}

        for imgPath in self.GetExtractedImageList():
            self.callback(False)

            sha_hash = hashlib.sha256(open(imgPath, 'rb').read()).digest()

            if sha_hash in hashes:
                dupCount += 1
                hashes[sha_hash].append(imgPath)

                if cull is True:
                    try:
                        os.remove(imgPath)
                        logging.info('Removing duplicate frame: %s' % (imgPath))
                    except Exception:
                        logging.error("Can't delete duplicate frame: %s" % (imgPath))

            else:
                hashes[sha_hash] = [imgPath]

        if cull and dupCount > 0:
            self.ReEnumerateExtractedFrames()

        self.callback(True)

        return dupCount

    def PositionToGravity(self, positionStr):
        # Positioning
        posMapping = {
            'Top Left': 'NorthWest',
            'Top': 'North',
            'Top Right': 'NorthEast',
            'Middle Left': 'West',
            'Center': 'Center',
            'Middle Right': 'East',
            'Bottom Left': 'SouthWest',
            'Bottom': 'South',
            'Bottom Right': 'SouthEast',
        }

        if positionStr in posMapping:
            return posMapping[positionStr]
        else:
            raise ValueError('Invalid position to gravity value')

    # png is prefered
    def GetIntermediaryFrameFormat(self):
        return 'png'

    def GetFinalOutputFormat(self):
        if self.gifOutPath is None:
            return ''
        return igf_paths.get_file_extension(self.gifOutPath)

    def BlitImage(self, layerIdx, beforeFXchain):
        cmdProcImage = ''
        layerId = 'imagelayer%d' % (layerIdx)
        imgPath = self.conf.GetParam(layerId, 'path')

        if self.conf.GetParamBool(layerId, 'applyFx') != beforeFXchain:
            return ''

        if imgPath is None or imgPath == '':
            return ''

        if not os.path.exists(imgPath):
            self.FatalError('Unable to find specified image file:\n%s' % (imgPath))

        gravity = self.PositionToGravity(self.conf.GetParam(layerId, 'positioning'))
        resize = self.conf.GetParam(layerId, 'resize')
        opacity = self.conf.GetParam(layerId, 'opacity')
        xNudge = int(self.conf.GetParam(layerId, 'xNudge'))
        yNudge = int(self.conf.GetParam(layerId, 'yNudge'))

        # -compose dissolve -define compose:args=%d -composite
        cmdProcImage += ' ( "%s"  -resize %d%% ) ' % (imgPath, resize)
        cmdProcImage += (
            ' -gravity %s -geometry %+d%+d -compose dissolve -define compose:args=%d -composite '
            % (gravity, xNudge, yNudge, opacity)
        )

        return cmdProcImage

    def CaptionProcessing(self, captionIdx, frameIdx, beforeFXchain, borderOffset):
        captionId = 'caption%d' % (captionIdx)
        cmdProcImage = ''

        if len(self.conf.GetParam(captionId, 'text')) > 0:
            fromFrame = int(self.conf.GetParam(captionId, 'frameStart'))
            toFrame = int(self.conf.GetParam(captionId, 'frameEnd'))

            if frameIdx < fromFrame or frameIdx > toFrame:
                return ''

        else:
            return ''

        # tricky please
        if self.conf.GetParamBool(captionId, 'applyFx') != beforeFXchain:
            return ''

        opacity = float(self.conf.GetParam(captionId, 'opacity'))  # Starting opacity
        # We need to nudge the font so it doesn't ride up against the edge
        positionAdjX = 0
        positionAdjY = 0
        #
        # Time-based effects
        #

        animationEnvelopeName = self.conf.GetParam(captionId, 'animationEnvelope').lower()

        if animationEnvelopeName != 'off':
            fps = int(self.conf.GetParam('rate', 'framerate'))
            animationDuration = 1.0

            if 'slow' in animationEnvelopeName:
                animationDuration = 2.0
            if 'medium' in animationEnvelopeName:
                animationDuration = 1.0
            if 'fast' in animationEnvelopeName:
                animationDuration = 0.5

            dutyCycle = float(fps) * animationDuration
            animStep = int(round(100 / dutyCycle))

            if animStep == 0:
                animStep = 1

            saw = [x / 100.0 for x in range(0, 101, animStep)]
            tri = [x / 100.0 for x in range(0, 101, animStep)]
            squ = ([1.00] * int(dutyCycle)) + ([0.00] * int(dutyCycle))

            if saw[-1] != 1.0:
                saw.append(1.0)

            if tri[-1] != 1.0:
                tri.append(1.0)

            if len(squ) == 0:
                squ = [1.0, 0.0]

            tri = tri + tri[::-1][1:-1]
            rnd = []

            for _ in range(0, 50):
                rnd.append(random.randint(0, 100) / 100.0)

            totalTextFrames = 1 + toFrame - fromFrame

            patternEnv = []
            if 'triangle' in animationEnvelopeName:
                patternEnv = tri
            elif 'square' in animationEnvelopeName:
                patternEnv = squ
            elif 'random' in animationEnvelopeName:
                patternEnv = rnd
            elif 'sawtooth' in animationEnvelopeName:
                patternEnv = saw
            else:
                patternEnv = [1.0]

            # repeat pattern
            if totalTextFrames > 0:
                patternEnv = ([op for op in patternEnv * totalTextFrames])[0:totalTextFrames]

            if 'fade' in animationEnvelopeName and 'in' in animationEnvelopeName:
                for fx in range(0, min(len(saw), len(patternEnv))):
                    patternEnv[fx] *= saw[fx]

            if 'fade' in animationEnvelopeName and 'out' in animationEnvelopeName:
                si = 0
                for fx in range(len(patternEnv) - 1, -1, -1):
                    patternEnv[fx] *= saw[si]
                    si += 1
                    if si >= len(saw):
                        break

            animationEnv = [0.0] * (fromFrame - 1)
            animationEnv += patternEnv
            animationEnv += [0.0] * (self.GetNumFrames() - len(animationEnv))

            # Animation type: Blink
            if self.conf.GetParam(captionId, 'animationType').lower() == 'blink':
                opacity *= animationEnv[frameIdx - 1]

            if self.conf.GetParam(captionId, 'animationType').lower() == 'left-right':
                moveRange = 50
                positionAdjX += -moveRange / 2 + (moveRange * animationEnv[frameIdx - 1])

            if self.conf.GetParam(captionId, 'animationType').lower() == 'up-down':
                moveRange = 50
                positionAdjY += -moveRange / 2 + (moveRange * animationEnv[frameIdx - 1])

            if self.conf.GetParam(captionId, 'animationType').lower() == 'subtle change':
                moveRange = 2
                moveAmount = -moveRange / 2 + (moveRange * animationEnv[frameIdx - 1])
                positionAdjY += moveAmount
                positionAdjX += moveAmount
                opacity *= re_scale(animationEnv[frameIdx - 1], (0.0, 1.0), (0.8, 1.0))

        if opacity <= 1:
            return ''

        captionText = self.conf.GetParam(captionId, 'text')
        captionMargin = int(self.conf.GetParam('captiondefaults', 'margin'))
        gravity = self.PositionToGravity(self.conf.GetParam(captionId, 'positioning'))

        if gravity.find('West') != -1 or gravity.find('East') != -1:
            positionAdjX += captionMargin + borderOffset

        if gravity.find('South') != -1 or gravity.find('North') != -1:
            positionAdjY += captionMargin + borderOffset

        # Escape captions
        captionText = captionText.replace('[enter]', '\n')
        captionText = captionText.replace('\\', '\\\\')
        captionText = captionText.replace('"', '\\"')
        captionText = captionText.replace('@', '\\@')

        fontFamily = self.conf.GetParam(captionId, 'font')
        fontStyle = self.conf.GetParam(captionId, 'style')

        if self.fonts is None:
            fontId = None
        else:
            fontId = self.fonts.GetFontId(fontFamily, fontStyle)
        fontSize = int(self.conf.GetParam(captionId, 'size').replace('pt', ''))
        fontColor = '"%s"' % (self.conf.GetParam(captionId, 'color'))
        fontOuterColor = '"%s"' % (self.conf.GetParam(captionId, 'outlineColor'))
        fontOutlineThickness = int(self.conf.GetParam(captionId, 'outlineThickness'))
        fontOpacity = int(opacity)
        isSmooth = False  # int(self.conf.GetParam(captionId, 'smoothOutline'))
        hasShadow = int(self.conf.GetParam(captionId, 'dropShadow'))

        fontBlur = ''
        outlineBlur = ''

        if fontOutlineThickness >= 1:
            if isSmooth:
                outlineBlur = '-blur 0.1x1'  # SigmaxRadius

            if fontSize > 13:
                fontOutlineThickness += 1
            else:
                fontOutlineThickness += 0
        try:
            int(fontSize)
        except Exception:
            fontSize = 24

        if fontId is None:
            self.FatalError('Unable to find font: %s (%s) ' % (fontFamily, fontStyle))

        cmdProcImage += '( +clone -alpha transparent -font %s -pointsize %d -gravity %s ' % (
            fontId,
            fontSize,
            gravity,
        )

        interlineSpacing = int(self.conf.GetParam(captionId, 'interlineSpacing'))

        if interlineSpacing != 0:
            cmdProcImage += ' -interline-spacing %d ' % (interlineSpacing)

        captionTweakX = [0, 0]
        captionTweakY = [0, 0]

        if 'South' in gravity:
            captionTweakY[1] = 1
        elif 'North' in gravity:
            captionTweakY[1] = -1
        else:
            captionTweakY[1] = -1

        if 'West' in gravity:
            captionTweakX[0] = -1
        elif 'East' in gravity:
            captionTweakX[0] = 1
        else:
            captionTweakX[0] = -1

        if fontOutlineThickness >= 1:
            cmdProcImage += ' -stroke %s -strokewidth %d -annotate %+d%+d "%s" %s ' % (
                fontOuterColor,
                fontOutlineThickness,
                positionAdjX + captionTweakX[0],
                positionAdjY + captionTweakY[0],
                captionText,
                outlineBlur,
            )
            cmdProcImage += ' -stroke %s -strokewidth %d -annotate %+d%+d "%s" %s ' % (
                fontOuterColor,
                fontOutlineThickness,
                positionAdjX + captionTweakX[1],
                positionAdjY + captionTweakY[1],
                captionText,
                outlineBlur,
            )

        cmdProcImage += ' -stroke none  -strokewidth %d -fill %s -annotate %+d%+d "%s" %s ' % (
            fontOutlineThickness,
            fontColor,
            positionAdjX,
            positionAdjY,
            captionText,
            fontBlur,
        )

        if hasShadow:
            cmdProcImage += ' ( +clone -gravity none -background none -shadow 60x1-5-5 ) +swap -compose over -composite '

        cmdProcImage += ' ) -compose dissolve -define compose:args=%d -composite ' % (fontOpacity)
        return cmdProcImage

    def CropAndResize(self, argFrameIdx=None):
        files = glob.glob(self.frameDir + os.sep + '*.png')
        files.sort()

        origWidth = self.GetVideoWidth()
        origHeight = self.GetVideoHeight()

        cinemagraphKeyFrame = int(self.conf.GetParam('blend', 'cinemagraphKeyFrameIdx'))
        keyframeFile = files[cinemagraphKeyFrame]

        if argFrameIdx is not None:
            files = [files[argFrameIdx]]
            frameIdx = argFrameIdx + 1
            logging.info('Crop, Resize and Blend frame %d' % (frameIdx))

        else:
            logging.info('Crop, Resize and Blend')
            self.DeleteResizedImages()
            frameIdx = 1

        for f in files:
            inputFileName = f
            outputFileName = self.resizeDir + os.sep + os.path.basename(f)

            cmdResize = (
                '"%s" -comment "Crop and Resize:%d" -comment "instagiffer" "%s" -resize %dx%d! +repage '
                % (
                    self.conf.GetParam('paths', 'convert'),
                    min(len(files), frameIdx - 1) * 100 / len(files),
                    inputFileName,
                    origWidth,
                    origHeight,
                )
            )
            cmdResize += '  -strip '  # Get rid of weird gamma correction

            #
            # Blend: Cinemagraph
            #

            if frameIdx > 1 and self.conf.GetParamBool('blend', 'cinemagraph'):
                maskFile = self.GetMaskFileName(cinemagraphKeyFrame)

                negation = ''
                if self.conf.GetParamBool('blend', 'cinemagraphInvert'):
                    negation = ' +negate '

                if os.path.isfile(maskFile):
                    cmdResize += (
                        f' ( "{keyframeFile}" -resize {origWidth}x{origHeight}! ( "{maskFile}" {negation} ) '
                        '-alpha off -compose copy_opacity -composite ) -compose over -composite '
                    )
                    # Transparent cinemagraphs
                    if self.conf.GetParamBool('blend', 'cinemagraphUseTransparency'):
                        cmdResize += (
                            f' ( ( "{maskFile}" {negation} ) -fill black -fuzz 0%% +opaque "#ffffff" '
                            '-negate -transparent black -negate ) -compose copy_opacity -composite '
                        )

            #
            # Crop
            #

            if self.conf.GetParam('size', 'cropenabled'):
                cmdResize += (
                    ' +repage '
                    + ' -crop '
                    + self.conf.GetParam('size', 'cropwidth')
                    + 'x'
                    + self.conf.GetParam('size', 'cropheight')
                    + '+'
                    + self.conf.GetParam('size', 'cropoffsetx')
                    + '+'
                    + self.conf.GetParam('size', 'cropoffsety')
                    + ' +repage'
                )

            #
            # Resize
            #

            x, y = self.GetCroppedAndResizedDimensions()
            cmdResize += ' -resize %dx%d! ' % (x, y)
            cmdResize += ' "%s" ' % (outputFileName)

            if not run_process(cmdResize, self.callback, False, False):
                errMsg = 'Image crop, resize, and blend failed or aborted'
                self.DeleteResizedImages()
                self.FatalError(errMsg)
                return False

            frameIdx += 1
        return True

    def ImageProcessing(self, previewFrameIdx=-1):
        # Dump the settings
        # if __release__ == False:
        #     self.conf.Dump()

        if previewFrameIdx >= 0:
            genPreview = True
            frameIdx = previewFrameIdx + 1
            files = [self.GetResizedImageList(frameIdx)]
            logging.info('Processing frame %d' % (frameIdx))
        else:
            genPreview = False
            logging.info('Processing frames')
            files = glob.glob(self.resizeDir + os.sep + '*.png')
            self.DeleteProcessedImages()
            frameIdx = 1

        files.sort()
        for f in files:
            inputFileName = f

            if genPreview:
                outputFileName = self.previewFile
            else:
                outputFileName = (
                    self.processedDir
                    + os.sep
                    + os.path.splitext(os.path.basename(f))[0]
                    + '.'
                    + self.GetIntermediaryFrameFormat()
                )

            borderOffset = 0
            if self.conf.GetParamBool('effects', 'border'):
                thickness = re_scale(
                    int(self.conf.GetParam('effects', 'borderAmount')),
                    (0, 100),
                    (1, 40),
                )
                borderOffset = thickness

            cmdProcImage = (
                '"%s" -comment "Applying Filters, Effects and Captions:%d" -comment "instagiffer" "%s" '
                % (
                    self.conf.GetParam('paths', 'convert'),
                    min(frameIdx - 1, len(files)) * 100 / len(files),
                    inputFileName,
                )
            )

            # Pre Filter fonts
            for x in range(1, 30):
                cmdProcImage += self.CaptionProcessing(x, frameIdx, True, borderOffset)

            # Pre Filter blits
            for x in range(1, 2):
                cmdProcImage += self.BlitImage(x, True)

            #
            # Effects
            #

            # Brightness and contrast (not supported in older versions of Imagemagick)
            if (
                self.conf.GetParam('effects', 'brightness') != '0'
                or self.conf.GetParam('effects', 'brightness') != '0'
            ):
                cmdProcImage += '-brightness-contrast %sx%s ' % (
                    self.conf.GetParam('effects', 'brightness'),
                    self.conf.GetParam('effects', 'contrast'),
                )

            if self.conf.GetParamBool('effects', 'sharpen'):
                cmdProcImage += '-sharpen 3 '

            if self.conf.GetParamBool('effects', 'oilPaint'):
                cmdProcImage += '-morphology OpenI Disk:1.75 '

            if self.conf.GetParam('color', 'saturation') != '0':
                scaledVal = 100 + re_scale(
                    int(self.conf.GetParam('color', 'saturation')),
                    (-100, 100),
                    (-80, 80),
                )
                cmdProcImage += '-modulate 100,%d ' % (scaledVal)

            if self.conf.GetParamBool('effects', 'nashville'):
                amt = re_scale(
                    int(self.conf.GetParam('effects', 'nashvilleAmount')),
                    (0, 100),
                    (10, 65),
                )

                cmdProcImage += (
                    ' ( -clone 0 -fill "#222b6d" -colorize %d%% ) ( -clone 0 -colorspace gray -negate ) -compose blend -define compose:args=50,0  -composite '
                    % (amt)
                )
                cmdProcImage += (
                    ' ( -clone 0 -fill "#f7daae" -colorize %d%% ) ( -clone 0 -colorspace gray -negate ) -compose blend -define compose:args=120,1 -composite '
                    % (amt)
                )
                cmdProcImage += ' -contrast -modulate 100,150,100 -auto-gamma '

            # Sepia
            if self.conf.GetParamBool('effects', 'sepiaTone'):
                scaledVal = re_scale(
                    int(self.conf.GetParam('effects', 'sepiaToneAmount')),
                    (0, 100),
                    (75, 100),
                )
                cmdProcImage += '-sepia-tone %d%% ' % (scaledVal)

            # Cartoon
            # cmdProcImage += '-edge 1 -negate -normalize -colorspace Gray -blur 0x.5 -contrast-stretch 0x50% '

            if self.conf.GetParamBool('effects', 'colorTint'):
                color = '"%s"' % (self.conf.GetParam('effects', 'colorTintColor'))
                amt = re_scale(
                    int(self.conf.GetParam('effects', 'colorTintAmount')),
                    (0, 100),
                    (30, 100),
                )
                cmdProcImage += '-fill %s -tint %d ' % (color, amt)

            # Fade edges
            if self.conf.GetParamBool('effects', 'fadeEdges'):
                rad = 100 - int(self.conf.GetParam('effects', 'fadeEdgeAmount'))
                sig = 100 - int(self.conf.GetParam('effects', 'fadeEdgeAmount'))
                rad = re_scale(rad, (0, 100), (20, 60))
                sig = re_scale(sig, (0, 100), (50, 5000))
                vx = -30
                vy = -30
                cmdProcImage += '-background black -vignette %dx%d%d%d ' % (
                    rad,
                    sig,
                    vx,
                    vy,
                )

            # Blur
            if int(self.conf.GetParam('effects', 'blur')) > 0:
                rad = 0
                sig = re_scale(int(self.conf.GetParam('effects', 'blur')), (0, 100), (1, 11))
                cmdProcImage += '-blur %dx%s ' % (rad, sig)

            # Border
            if borderOffset > 0:
                color = self.conf.GetParam('effects', 'borderColor')
                thickness = borderOffset
                cmdProcImage += '-bordercolor "%s" -border %d ' % (color, thickness)

            # Enhancement: Dithering

            # misc size optimization -normalize
            if self.conf.GetParamBool('effects', 'sharpen'):
                sharpAmount = int(self.conf.GetParam('effects', 'sharpenAmount'))
                scaledVal = re_scale(sharpAmount, (0, 100), (0, 5))
                ditherIdx = 0

                if sharpAmount >= 60:
                    ditherIdx = 2
                elif sharpAmount >= 30:
                    ditherIdx = 1

                ditherType = [
                    '-ordered-dither checks,20',
                    '-dither Riemersma',
                    '-dither FloydSteinberg',
                ]

                cmdProcImage += '-sharpen %d %s ' % (scaledVal, ditherType[ditherIdx])
            else:
                cmdProcImage += '-dither none '

            # Post Filter captions
            for x in range(1, 30):
                cmdProcImage += self.CaptionProcessing(x, frameIdx, False, borderOffset)

            # Post Filter blits
            for x in range(1, 2):
                cmdProcImage += self.BlitImage(x, False)

            #
            # Colorspace conversion
            #
            if self.conf.GetParam('color', 'colorspace') != 'CMYK':
                cmdProcImage += '-colorspace %s ' % (
                    self.conf.GetParam('color', 'colorspace')
                )  # -matte

            # Color palette - gif only
            if self.GetFinalOutputFormat() == igf_paths.EXT_GIF:
                cmdProcImage += ' -depth 8 -colors %s ' % (self.conf.GetParam('color', 'numcolors'))

            cmdProcImage += ' -format %s ' % (self.GetIntermediaryFrameFormat())
            cmdProcImage += '"%s" ' % (outputFileName)

            if not run_process(cmdProcImage, self.callback, False, False):
                errMsg = 'Image processing failed or aborted'
                self.DeleteProcessedImages()
                self.FatalError(errMsg)
                return False

            frameIdx += 1
        return True

    # Generate final output. Returns size of generated GIF in bytes
    def Generate(self, skipProcessing=False):
        err = ''
        fileName = self.GetNextOutputPath()

        # Process all frames
        if not skipProcessing:
            self.ImageProcessing()

        #
        # Now what file format are we dealing with?
        #

        if self.GetFinalOutputFormat() == igf_paths.EXT_GIF:
            # Using convert util
            cmdCreateGif = '"%s" ' % (self.conf.GetParam('paths', 'convert'))
            # Playback rate and looping
            cmdCreateGif += ' -delay %d ' % (self.GetGifFrameDelay())
            cmdCreateGif += ' -loop %d ' % (int(self.conf.GetParam('rate', 'numLoops')))

            if self.conf.GetParamBool('blend', 'cinemagraphUseTransparency'):
                cmdCreateGif += ' -alpha set -dispose %d ' % (
                    int(self.conf.GetParamBool('blend', 'cinemagraphKeyFrameIdx'))
                )
            else:
                cmdCreateGif += '-layers optimizePlus '

            # Input files
            cmdCreateGif += (
                '"' + self.processedDir + os.sep + '*.' + self.GetIntermediaryFrameFormat() + '" '
            )
            cmdCreateGif += '"' + fileName + '"'

            (out, err) = run_process(cmdCreateGif, self.callback, returnOutput=True)

        elif self.GetFinalOutputFormat() in ('.mp4', 'webm'):
            self.ExtractAudioClip()

            secPerFrame = self.GetGifFrameDelay() * 10 / 1000.0
            fps = 1.0 / secPerFrame
            finalFps = 30
            framesDir = self.GetProcessedImagesDir()

            self.ReEnumeratePngFrames(self.GetProcessedImagesDir(), self.GetProcessedImageList())

            #
            # - vf Make width/height even
            # - shortest Finish encoding when the shortest input stream ends.
            # - h.264 format
            # - use null audio
            #
            # "e:\ffmpeg\ffmpeg.exe" -r 1/5 -start_number 0 -i "E:\images\01\padlock%3d.png" -c:v libx264 -r 30 -pix_fmt yuv420p e:\out.mp4

            cmdConvertToVideo = (
                '"%s" -v verbose -y -r %.2f -start_number 0 -i "%simage%%04d.%s" '
                % (
                    self.conf.GetParam('paths', 'ffmpeg'),
                    fps,
                    framesDir,
                    self.GetIntermediaryFrameFormat(),
                )
            )

            # Audio
            if self.conf.GetParamBool('audio', 'audioEnabled'):
                if not os.path.exists(self.conf.GetParam('audio', 'path')):
                    self.FatalError('Could not find audio file')

                if self.GetFinalOutputFormat() in ['webm']:
                    audioCodec = ' libvorbis '
                else:
                    audioCodec = 'aac -strict experimental'

                volume = int(self.conf.GetParam('audio', 'volume')) / 100.0

                cmdConvertToVideo += ' -ss "%s" -i "%s" -af "volume=%.f" -c:a %s -b:a 128k  ' % (
                    self.conf.GetParam('audio', 'startTime'),
                    self.conf.GetParam('audio', 'path'),
                    volume,
                    audioCodec,
                )
            else:
                cmdConvertToVideo += ' -f lavfi -i aevalsrc=0 '

            # video
            if self.GetFinalOutputFormat() in ['mp4']:
                cmdConvertToVideo += ' -c:v libx264 -crf 18 -preset slow -vf "scale=trunc(in_w/2)*2:trunc(in_h/2)*2",setsar=1:1 -pix_fmt yuv420p '
            elif self.GetFinalOutputFormat() in ['webm']:
                cmdConvertToVideo += ' -c:v libvpx -crf 4 -b:v 312.5k -vf setsar=1:1 '
            else:
                cmdConvertToVideo += ' -vf setsar=1:1 '

            cmdConvertToVideo += ' -shortest  -r %d "%s"' % (finalFps, fileName)

            (out, err) = run_process(cmdConvertToVideo, self.callback, returnOutput=True)
        else:
            self.FatalError("I don't know how to create %s files" % (self.GetFinalOutputFormat()))

        if not os.path.exists(fileName) or os.path.getsize(fileName) == 0:
            logging.error(err)
            self.FatalError('Failed to create %s :( ' % (self.GetFinalOutputFormat()))
            return 0

        self.gifCreated = True
        self.lastSavedGifPath = fileName

        # Run the gif optimizer
        if self.GetFinalOutputFormat() == 'gif':
            self.AlterGifFrameTiming(fileName)
            self.OptimizeGif(fileName)

        return self.GetSize()

    def AlterGifFrameTiming(self, fileName):
        frameTimingsStr = self.conf.GetParam('rate', 'customFrameTimingMs')

        if len(frameTimingsStr) == 0:
            return

        cmdChangeGifTiming = '"%s" "%s" ' % (
            self.conf.GetParam('paths', 'convert'),
            fileName,
        )

        for frameStr in frameTimingsStr.split(','):
            (frameIdx, frameMs) = frameStr.split(':')

            frameIdx = int(frameIdx)
            frameMs = int(frameMs)

            cmdChangeGifTiming += ' ( -clone %d -set delay %d ) -swap %d,-1 +delete ' % (
                frameIdx,
                frameMs / 10,
                frameIdx,
            )

        cmdChangeGifTiming += ' "%s"' % (fileName)
        (out, err) = run_process(cmdChangeGifTiming, self.callback, returnOutput=True)

    def OptimizeGif(self, fileName):
        # Run optimizer
        if self.conf.GetParamBool('size', 'fileOptimizer') and os.path.exists(
            self.conf.GetParam('paths', 'gifsicle')
        ):
            olevel = 3
            beforeSize = self.GetSize()

            cmdOptimizeGif = '"%s" -O%d --colors 256 "%s" -o "%s"' % (
                self.conf.GetParam('paths', 'gifsicle'),
                olevel,
                fileName,
                fileName,
            )

            (out, err) = run_process(cmdOptimizeGif, self.callback, returnOutput=True)

            afterSize = self.GetSize()

            logging.info(
                'Optimization shaved off %.1f kB' % (float(beforeSize - afterSize) / 1024.0)
            )

    def GenerateFramePreview(self, idx):
        idx -= 1
        self.CropAndResize(idx)
        self.ImageProcessing(idx)
        self.callback(True)
        return self.previewFile

    def GetPreviewImagePath(self):
        return self.previewFile

    def PreviewFileExists(self):
        return os.path.exists(self.GetPreviewImagePath())

    def GetPreviewLastModifiedTs(self):
        if self.PreviewFileExists():
            return os.stat(self.GetPreviewImagePath()).st_mtime
        else:
            return 0

    def GetTotalRuntimeSec(self):
        secPerFrame = (self.GetGifFrameDelay() * 10) / 1000.0
        totalSec = secPerFrame * self.GetNumFrames()
        return totalSec

    def GetGifFrameDelay(self, modifyer=None):
        if modifyer is None:
            modifyer = int(self.conf.GetParam('rate', 'speedmodifier'))

        timePerFrame = 100 // int(self.conf.GetParam('rate', 'framerate'))
        speedModification = modifyer
        normalizedMod = 1 + (abs(speedModification) - 0) * (timePerFrame - 0) / (10 - 0)
        gifFrameDelay = timePerFrame

        if speedModification < 0:
            gifFrameDelay += int(normalizedMod * 2)  # Increase the effect when slowing down
        elif speedModification > 0:
            gifFrameDelay -= normalizedMod

        if gifFrameDelay < 2:  # frame delay of 1 means realtime??
            gifFrameDelay = 2

        return gifFrameDelay

    def GetSize(self):
        sizeBytes = os.path.getsize(self.GetLastGifOutputPath())
        return sizeBytes

    def GetVideoWidth(self):
        return int(self.videoWidth)

    def GetVideoHeight(self):
        return int(self.videoHeight)

    def GetVideoLength(self):
        return self.videoLength

    def GetVideoLengthSec(self):
        vidLen = float(
            '%.1f' % (igf_common.duration_str_to_milliseconds(self.videoLength) / 1000.0)
        )
        return vidLen

    def GetVideoFps(self):
        if self.videoFps < 1:
            return 1
        else:
            return int(round(float(self.videoFps)))

    def CompatibilityWarningsEnabled(self):
        return self.conf.GetParamBool('warnings', 'socialMedia')

    def GetCroppedAndResizedDimensions(self):
        w, h = self.conf.GetParam('size', 'resizePostCrop').split('x')
        return int(w), int(h)

    def GetCompatibilityWarning(self):
        w, h = self.GetCroppedAndResizedDimensions()
        aspectRatio = w / float(h)

        warnings = ''
        warnTwitter = self.conf.GetParamBool('warnings', 'twitter')
        warnTumblr = self.conf.GetParamBool('warnings', 'tumblr')
        warnImgur = self.conf.GetParamBool('warnings', 'imgur')
        warnGPlus = self.conf.GetParamBool('warnings', 'gplus')
        warnFacebook = self.conf.GetParamBool('warnings', 'facebook')
        warnInstagram = self.conf.GetParamBool('warnings', 'instagram')
        warnVine = self.conf.GetParamBool('warnings', 'vine')

        if warnTumblr:
            # TODO: Verify. is this still a thing?
            # if w > 400 and w <= 500 and self.GetSize() >= 1000 * 1024 and self.GetSize() < 2000 * 1024:
            #    warnings += "Tumblr Warning: If image width exceeds 400px, file size must be less than 1000kB\n\n"

            if w > 540 or h > 750:
                warnings += (
                    'Tumblr Warning: Image dimensions ('
                    + str(w)
                    + 'x'
                    + str(h)
                    + ') are larger than the 500x750 maximum.\n\n'
                )

            if self.GetSize() >= 2000 * 1024:
                warnings += (
                    'Tumblr Warning: File size of '
                    + str(int(self.GetSize() / 1024))
                    + 'kB is too large. It must be less than 2000kB. Try reducing animation smoothness, viewable region, frame size, or quality.  You can also try enabling black & white mode.\n\n'
                )

        if warnImgur and self.GetSize() >= 2 * 1024 * 1024:
            warnings += (
                'Imgur Warning: File size of '
                + str(int(self.GetSize() / 1024))
                + 'kB is too large. It must be less than 2MB unless you have a premium account, in which case, the max upload limit is 5MB.\n\n'
            )

        if warnTwitter:
            twitWarn = 0
            if self.GetSize() >= 5 * 1024 * 1024:
                warnings += (
                    'Twitter Warning: File size of '
                    + str(int(self.GetSize() / 1024))
                    + 'kB is too large. It must be less than 5MB.\n\n'
                )
                twitWarn += 1

            if self.GetNumFrames() > 350:
                warnings += 'Twitter Warning: Number of frames must not exceed 350.\n\n'
                twitWarn += 1

            if twitWarn > 0:
                # might as well tell them about this too
                if w < 506 or aspectRatio != 1.0 or aspectRatio != 0.5:
                    warnings += (
                        'Twitter Warning: recommended dimensions are 506x506 or 506x253.\n\n'
                    )

        if warnGPlus and (w < 496 or h < 496 or aspectRatio != 1.0):
            warnings += 'Google Plus Warning: Recommended dimensions are 496x496.\n\n'

        if warnInstagram:
            if w != 600 or h != 600:
                warnings += 'Instagram Warning: Recommended dimensions are 600x600.\n\n'

            if self.GetTotalRuntimeSec() > 15:
                warnings += 'Instagram Warning: Total runtime must not exceed 15 seconds.\n\n'

        if warnFacebook:
            if w != 504 or h != 283:
                warnings += 'Facebook Warning: Recommended dimensions are 504x283.\n\n'

        if warnVine:
            if w != 480 or h != 480:
                warnings += 'Vine Warning: Required dimensions are 480x480.\n\n'

            if self.GetTotalRuntimeSec() > 6:
                warnings += 'Vine Warning: Total run time cannot exceed 6 seconds.\n\n'

            if self.GetSize() >= 1500 * 1024:
                warnings += 'Vine Warning: Total file size must be under 1.5 MB\n\n'

        return warnings


class ImagemagickFont:
    """Wrapper around the Imagemagick font engine."""

    def __init__(self, imagemagick_font_data):
        self.fonts = {}
        fonts = re.findall(
            r'\s*Font: (.+?)\n\s*family: (.+?)\n\s*style: (.+?)\n\s*stretch: (.+?)\n\s*weight: (.+?)\n\s*glyphs: (.+?)\n',
            imagemagick_font_data,
            re.DOTALL | re.M | re.UNICODE,
        )

        for font in fonts:
            fontFamily = font[1].strip()
            fontId = font[0].strip()
            # fontFile = font[5].strip()
            fontStyle = font[2].strip()
            fontStretch = font[3].strip()
            fontWeight = font[4].strip()

            # ignore stretched fonts, and styles other than italic, and weights we don't know about
            if (
                fontFamily != 'unknown'
                and fontStretch == 'Normal'
                and (fontStyle == 'Italic' or fontStyle == 'Normal')
                and (fontWeight == '400' or fontWeight == '700')
            ):
                overallStyle = None
                if fontStyle == 'Normal' and fontWeight == '400':
                    overallStyle = 'Regular'
                elif fontStyle == 'Normal' and fontWeight == '700':
                    overallStyle = 'Bold'
                elif fontStyle == 'Italic' and fontWeight == '400':
                    overallStyle = 'Italic'
                elif fontStyle == 'Italic' and fontWeight == '700':
                    overallStyle = 'Bold Italic'

                if overallStyle is not None:
                    if fontFamily not in self.fonts:
                        self.fonts[fontFamily] = {}
                    self.fonts[fontFamily][overallStyle] = fontId

    def GetFontCount(self):
        return len(self.fonts)

    def GetFamilyList(self):
        return tuple(sorted(self.fonts))

    def GetFontAttributeList(self, fontFamily):
        return tuple(sorted(self.fonts[fontFamily], reverse=True))

    def GetFontId(self, fontFamily, fontStyle):
        return self.fonts[fontFamily][fontStyle]

    def GetBestFontFamilyIdx(self, userChoice=''):
        fontFamilyList = self.GetFamilyList()

        if len(userChoice) and userChoice in fontFamilyList:
            return fontFamilyList.index(userChoice)
        elif 'Impact' in fontFamilyList:
            return fontFamilyList.index('Impact')
        elif 'Arial Rounded MT Bold' in fontFamilyList:
            return fontFamilyList.index('Arial Rounded MT Bold')
        elif 'Arial' in fontFamilyList:
            return fontFamilyList.index('Arial')
        else:
            return 0
