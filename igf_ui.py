import os
import re
import sys
import time
import shlex
import shutil
import locale
import logging
import subprocess
import traceback

import PIL.Image
import PIL.ImageTk
import PIL.ImageDraw
import PIL.ImageFilter

import tkinter.messagebox
from tkinter import ttk

import tkinter
import tkinter.font
import tkinter.filedialog

import igf_paths
import igf_animgif
import igf_common
from igf_common import IM_A_PC, IM_A_MAC, re_scale, __version__, __release__

if IM_A_PC:
    import winsound

TIME_VALUES_10 = tuple(f'{i}' for i in range(10))
TIME_VALUES_60 = tuple(f'{i:02d}' for i in range(60))


def notify_user(title, msg):
    return tkinter.messagebox.showinfo(title, msg)


class GifPlayerWidget(tkinter.Label):
    """Tkinter widget that plays a gif."""

    def __init__(self, master, processedImgList, frameDelayMs, resizable, soundPath=None):
        self.delay = frameDelayMs
        self.images = []
        self.frames = []
        self.resizable = resizable
        self.imgList = processedImgList
        self.audioPath = soundPath

        if self.delay < 2:
            self.delay = 100

        self.LoadImages(False)

        # tkinter.Label(master, image=self.frames[0], padx=10, pady=10)
        super().__init__(image=self.frames[0], padx=10, pady=10)

        self.idx = 0
        self.cancel = self.after(self.delay, self.Play)

        if self.resizable:
            self.columnconfigure(0, weight=1)
            self.rowconfigure(0, weight=1)

        self.currW = self.winfo_width()
        self.currH = self.winfo_height()

    def GetInfo(self):
        width, height = self.images[0].size
        return 'Dimensions: %dx%d' % (width, height)

    def Stop(self):
        audio_play(None)
        self.after_cancel(self.cancel)

    def LoadImages(self, resize):
        self.images = []
        self.frames = []

        for imagePath in self.imgList:
            f = open(imagePath, 'rb')

            im = PIL.Image.open(f)

            if self.resizable and resize:
                im = im.resize(
                    (self.winfo_width(), self.winfo_height()),
                    PIL.Image.Resampling.BICUBIC,
                )

            self.images.append(im)
            self.frames.append(PIL.ImageTk.PhotoImage(im))

            f.close()
            del im
            del f

    def Play(self):
        resizePause = 0

        if self.idx == 0:
            audio_play(self.audioPath)

        self.idx += 1
        if self.idx >= len(self.frames):
            self.idx = 0

        # window was resized
        if self.resizable and (
            self.winfo_width() != self.currW or self.winfo_height() != self.currH
        ):
            logging.info(
                '%s %s => %d %d' % (self.currW, self.currH, self.winfo_width(), self.winfo_height())
            )

            self.Stop()
            self.LoadImages(True)
            resizePause = 0
            self.currW = self.winfo_width()
            self.currH = self.winfo_height()

        self.config(image=self.frames[self.idx])
        self.cancel = self.after(self.delay + resizePause, self.Play)


class GifApp:
    def __init__(self, parent, cmdline_video_path):
        global __release__

        self.gif: None | igf_animgif.AnimatedGif = None
        self.guiBusy = False
        self.showPreviewFlag = False
        self.parent = parent
        self.thumbnailIdx = 0
        self.timerHandle = None
        self.cancelRequest = False
        self.tempDir = None
        self.captions = {}

        self.cropResizeChanges = 0
        self.captionChanges = 0
        self.miscGifChanges = 1
        self.frameTimingOrCompressionChanges = 0
        self.audioChanges = 0

        self.screenCapDlgGeometry = ''
        self.mainTimerValueMS = 2000
        self.savePath = None
        self.parent.withdraw()  # Hide. add components then show at the end
        self.thumbNailsUpdatedTs = 0
        self.thumbNailCache = {}
        self.maskEventList = []
        self.maskEdited = False
        self.maskDraw: None | PIL.ImageDraw.ImageDraw = None
        self.trackBarTs = 0

        # self.cropWidth            = "0"
        # self.cropHeight           = "0"
        # self.cropStartX           = "0"
        # self.cropStartY           = "0"
        # self.finalSize            = "0x0"

        # DPI scaling
        defaultDpi = 96  # corresponds to Smaller 100%
        self.parent.tk.call('tk', 'scaling', '-displayof', '.', defaultDpi / 72.0)

        #
        # Child Dialog default values
        #

        self.OnCaptionConfigDefaults = {}
        self.OnSetLogoDefaults = {}

        #
        # Load config
        #

        binPath = os.path.dirname(os.path.realpath(sys.argv[0]))
        self.conf = igf_common.InstaConfig(binPath + os.sep + 'instagiffer.conf')
        self.savePath = None

        #
        # Initialize variables with configuration defaults
        #
        self.screenCapDlgGeometry = self.conf.GetParam('screencap', 'sizeandposition')
        timerMs = self.conf.GetParam('settings', 'idleProcessTimeoutMs')

        if timerMs != '' and timerMs.isdigit() and int(timerMs) > 1000:
            self.mainTimerValueMS = timerMs

        self.CreateAppDataFolder()
        self.ForceSingleInstance()

        #
        # Debug version?
        #

        if self.conf.ParamExists('plugins', 'debug'):
            __release__ = not self.conf.GetParamBool('plugins', 'debug')

        #
        # Build GUI
        #

        if __release__:
            self.parent.title('Instagiffer')
        else:
            self.parent.title('Instagiffer - DEBUG MODE *******')

        if IM_A_PC:
            self.parent.wm_iconbitmap('instagiffer.ico')

        frame = tkinter.Frame(parent)
        frame.grid()
        self.mainFrame = frame

        self.parent.resizable(width=tkinter.FALSE, height=tkinter.FALSE)
        # self.parent.protocol("WM_DELETE_WINDOW", self.OnWindowClose)

        #
        # GUI config. OS-dependant
        #

        if IM_A_PC:
            # Warning: Don't make the GUI too big, or it may not present
            # correctly on netbooks

            # Font configuration
            self.defaultFont = tkinter.font.nametofont('TkDefaultFont')
            self.defaultFont.configure(family='Arial', size=8)
            self.defaultFontBig = tkinter.font.Font(family='Arial', size=9)
            self.defaultFontTiny = tkinter.font.Font(family='Arial', size=7)

            self.guiConf = {}
            self.guiConf['guiPadding'] = 7  # GUI padding.
            self.guiConf['timeSpinboxWidth'] = 2  # Width of the MM:HH:SS spinboxes
            self.guiConf['fileEntryWidth'] = 105  # URL/path text field
            self.guiConf['canvasWidth'] = 365  # Viewing area (note: height = width)
            self.guiConf['canvasSliderWidth'] = self.guiConf['canvasWidth'] - 33
            self.guiConf['mainSliderHeight'] = 13  # Left-hand slider height
            self.guiConf['mainSliderWidth'] = (
                310 - (self.guiConf['guiPadding'] - 3) * 2
            )  # Left-hand slider width
        else:  # Mac
            # Font configuration
            self.defaultFont = tkinter.font.nametofont('TkDefaultFont')
            self.defaultFont.configure(family='Arial', size=11)
            self.defaultFontBig = tkinter.font.Font(family='Arial', size=11)
            self.defaultFontTiny = tkinter.font.Font(family='Arial', size=9)

            self.guiConf = {}
            self.guiConf['guiPadding'] = 9
            self.guiConf['timeSpinboxWidth'] = 3
            self.guiConf['fileEntryWidth'] = 119
            self.guiConf['canvasWidth'] = 400
            self.guiConf['canvasSliderWidth'] = self.guiConf['canvasWidth'] - 63
            self.guiConf['mainSliderHeight'] = 16
            self.guiConf['mainSliderWidth'] = 400 - (self.guiConf['guiPadding'] - 3) * 2

        # Menu
        #######################################################################

        self.menubar = tkinter.Menu(parent)

        # Override Apple menu
        if IM_A_MAC:
            apple = tkinter.Menu(self.menubar, name='apple')
            apple.add_command(label='About', command=self.About)
            self.menubar.add_cascade(menu=apple)

        # File
        self.fileMenu = tkinter.Menu(self.menubar, tearoff=0)

        self.uploadMenu = tkinter.Menu(self.fileMenu, tearoff=0)
        self.uploadMenu.add_command(label='Imgur', underline=0, command=self.OnImgurUpload)

        self.fileMenu.add_command(
            label='Download Video...', underline=1, command=self.OnSaveVideoForLater
        )
        self.fileMenu.add_command(
            label='Change Save Location...', underline=7, command=self.OnSetSaveLocation
        )
        self.fileMenu.add_cascade(label='Upload', underline=0, menu=self.uploadMenu)
        self.fileMenu.add_command(
            label='Delete Temporary Files',
            underline=0,
            command=self.OnDeleteTemporaryFiles,
        )
        self.fileMenu.add_command(label='Exit', underline=0, command=self.OnWindowClose)

        # Frame
        self.frameMenu = tkinter.Menu(self.menubar, tearoff=0)

        if IM_A_PC:
            viewInExternalViewerLabel = 'View Frames In Explorer...'
        else:
            viewInExternalViewerLabel = 'Reveal Frames in Finder'

        self.frameMenu.add_command(
            label=viewInExternalViewerLabel,
            underline=0,
            command=self.OnViewImageStillsInExplorer,
        )
        self.frameMenu.add_command(
            label='Delete Frames...', underline=0, command=self.OnDeleteFrames
        )
        self.frameMenu.add_command(
            label='Export Frames...', underline=0, command=self.OnExportFrames
        )
        self.frameMenu.add_command(
            label='Import Frames...', underline=0, command=self.OnImportFrames
        )
        self.frameMenu.add_command(
            label='Manual Crop...', underline=7, command=self.OnManualSizeAndCrop
        )
        # self.frameMenu.add_command(label="Edit Mask...",            underline=5, command=self.OnEditMask)
        self.frameEffectsMenu = tkinter.Menu(self.frameMenu, tearoff=0)
        self.frameEffectsMenu.add_command(
            label='Make It Loop!', underline=8, command=self.OnForwardReverseLoop
        )
        self.frameEffectsMenu.add_command(
            label='Reverse', underline=8, command=self.OnReverseFrames
        )
        self.frameEffectsMenu.add_command(
            label='Crossfade...', underline=8, command=self.OnCrossFade
        )
        self.frameMenu.add_cascade(label='Frame Effects', underline=0, menu=self.frameEffectsMenu)

        self.frameMenuItemCount = 6

        # Settings
        self.settingsMenu = tkinter.Menu(self.menubar, tearoff=0)
        self.qualityMenu = tkinter.Menu(self.settingsMenu, tearoff=0)
        self.downloadQuality = tkinter.StringVar()

        #
        # Youtube download quality
        #

        defaultQuality = self.conf.GetParam('settings', 'downloadQuality')
        youtubeQualityList = ['None', 'Low', 'Medium', 'High', 'Highest']

        if defaultQuality not in youtubeQualityList:
            defaultQuality = 'Medium'
            self.conf.SetParam('settings', 'downloadQuality', defaultQuality)

        for qual in youtubeQualityList:
            self.qualityMenu.add_radiobutton(
                label=qual,
                underline=0,
                variable=self.downloadQuality,
                command=self.OnChangeMenuSetting,
            )
        #

        self.socialMediaWarningsEnabled = tkinter.StringVar()
        self.overwriteOutputGif = tkinter.StringVar()
        self.fileSizeOptimize = tkinter.StringVar()

        self.settingsMenu.add_checkbutton(
            label='Overwrite Output GIF',
            underline=0,
            variable=self.overwriteOutputGif,
            command=self.OnChangeMenuSetting,
        )
        self.settingsMenu.add_checkbutton(
            label='Social Media Warnings',
            underline=0,
            variable=self.socialMediaWarningsEnabled,
            command=self.OnChangeMenuSetting,
        )
        self.settingsMenu.add_cascade(
            label='Youtube Download Quality', underline=0, menu=self.qualityMenu
        )
        self.settingsMenu.add_command(
            label='Configure Your Logo...', underline=0, command=self.OnSetLogo
        )

        if IM_A_PC:
            self.settingsMenu.add_checkbutton(
                label='Extra GIF Compression',
                underline=0,
                variable=self.fileSizeOptimize,
                command=self.OnChangeMenuSetting,
            )

        # Help
        self.helpMenu = tkinter.Menu(self.menubar, tearoff=0)
        self.helpMenu.add_command(label='About', underline=0, command=self.About)
        self.helpMenu.add_command(
            label='Check For Updates', underline=0, command=self.CheckForUpdates
        )
        self.helpMenu.add_command(
            label='Frequently Asked Questions', underline=0, command=self.OpenFAQ
        )
        self.helpMenu.add_separator()
        self.helpMenu.add_command(label='Generate Bug Report', underline=0, command=self.ViewLog)

        # Top-level
        self.menubar.add_cascade(label='File', underline=0, menu=self.fileMenu)
        self.menubar.add_cascade(label='Frame', underline=0, menu=self.frameMenu)
        self.menubar.add_cascade(label='Settings', underline=0, menu=self.settingsMenu)
        self.menubar.add_cascade(label='Help', underline=0, menu=self.helpMenu)
        parent.config(menu=self.menubar)

        # Status Bar
        #######################################################################

        padding = 2

        self.status = tkinter.Label(parent, text='', bd=1, relief=tkinter.SUNKEN, anchor=tkinter.W)
        self.status.grid(
            # side=tkinter.BOTTOM, fill=tkinter.X
        )

        # Progress bar
        #######################################################################

        # s = ttk.Style()
        # s.theme_use()
        # s.configure("red.Horizontal.TProgressbar", foreground='#395976', background='#395976')

        self.progressBar = ttk.Progressbar(
            parent,
            orient=tkinter.HORIZONTAL,
            maximum=100,
            mode='determinate',
            name='progressBar',
        )  # , style="red.Horizontal.TProgressbar")
        self.progressBar.grid(
            # side=tkinter.BOTTOM, fill=tkinter.X
        )
        self.showProgress = False
        self.progressBarPosition = tkinter.IntVar()
        self.progressBar['variable'] = self.progressBarPosition

        # Top area (colspan = 2)
        #######################################################################

        # Row 1
        rowIdx = 0

        # Top Box
        self.boxOpen = ttk.LabelFrame(
            frame,
            text=" Step 1: Click 'Load Video' to browse for a file, paste a Youtube URL, or click 'Capture Screen' ",
        )
        self.boxOpen.grid(
            row=rowIdx,
            column=0,
            columnspan=12,
            sticky='NS',
            padx=padding,
            pady=padding,
            ipadx=padding,
            ipady=padding,
        )

        rowIdx += 1

        self.txtFname = tkinter.Entry(
            self.boxOpen, font=self.defaultFont, width=self.guiConf['fileEntryWidth']
        )
        self.btnFopen = tkinter.Button(self.boxOpen, text='Load Video', command=self.OnLoadVideo)
        self.btnScreenCap = tkinter.Button(
            self.boxOpen, text='Capture Screen', command=self.OnScreenCapture
        )
        self.txtFname.grid(
            row=rowIdx,
            column=0,
            columnspan=5,
            sticky=tkinter.W,
            padx=padding,
            pady=padding,
        )
        self.btnFopen.grid(
            row=rowIdx,
            column=11,
            columnspan=2,
            sticky=tkinter.W,
            padx=padding,
            pady=padding,
        )
        self.btnScreenCap.grid(
            row=rowIdx,
            column=13,
            columnspan=2,
            sticky=tkinter.W,
            padx=padding,
            pady=padding,
        )

        #
        self.txtFname.bind('<Return>', self.OnLoadVideoEnterPressed)

        # Bind context menu (cut & paste) action to video URL text field

        if IM_A_MAC:
            whichRclickMouseButton = '<Button-2>'
            whichRclickReleaseMouseButton = '<ButtonRelease-2>'
        else:
            whichRclickMouseButton = '<Button-3>'
            whichRclickReleaseMouseButton = '<ButtonRelease-3>'

        # right-click open  for multi-select mode
        self.btnFopen.bind(whichRclickMouseButton, self.OnShiftLoadVideo)

        self.txtFname.bind(whichRclickMouseButton, self.OnRClickPopup, add='')

        # Top-level left column, where all of the settings sliders are
        #######################################################################

        rowIdx += 1
        self.boxTweaks = ttk.LabelFrame(frame, text=' Step 2: Video Extraction & GIF Settings ')
        self.boxTweaks.grid(
            row=rowIdx,
            column=0,
            columnspan=3,
            rowspan=10,
            sticky='NS',
            padx=padding,
            pady=padding,
            ipadx=padding,
            ipady=padding,
        )

        # Top-level right column. Cropping tool
        #######################################################################

        self.boxCropping = ttk.LabelFrame(
            frame,
            text=' Step 3: Trim edges. Right-Click to preview. Double-Click to delete ',
        )
        self.boxCropping.grid(
            row=rowIdx,
            column=4,
            columnspan=2,
            rowspan=10,
            sticky='NS',
            padx=padding,
            pady=padding,
            ipadx=padding,
            ipady=padding,
        )

        # Cropping tool
        #######################################################################

        rowIdx += 1

        self.canvasSize = self.guiConf['canvasWidth']
        self.cropSizerSize = 9  # Size of crop sizer handle

        self.btnTrackbarLeft = tkinter.Button(
            self.boxCropping,
            text='<',
            font=self.defaultFontTiny,
            command=self.OnTrackbarLeft,
            repeatinterval=1,
            repeatdelay=200,
        )
        self.btnTrackbarRight = tkinter.Button(
            self.boxCropping,
            text='>',
            font=self.defaultFontTiny,
            command=self.OnTrackbarRight,
            repeatinterval=1,
            repeatdelay=200,
        )
        self.sclFrameTrackbar = tkinter.Scale(
            self.boxCropping,
            from_=1,
            to=1,
            resolution=1,
            tickinterval=0,
            showvalue=False,
            orient=tkinter.HORIZONTAL,
            sliderlength=20,
            width=15,
            length=self.guiConf['canvasSliderWidth'],
            command=self.OnFrameTrackbarMove,
        )
        self.canCropTool = tkinter.Canvas(
            self.boxCropping,
            width=self.canvasSize + 1,
            height=self.canvasSize + 1,
            background='black',
            borderwidth=0,
            highlightthickness=0,
        )
        self.frameCounterStr = tkinter.StringVar()
        self.frameDimensionsStr = tkinter.StringVar()
        self.lblFrameCtr = tkinter.Label(self.boxCropping, textvariable=self.frameCounterStr)
        self.lblFrameDimensions = tkinter.Label(
            self.boxCropping, textvariable=self.frameDimensionsStr
        )

        self.canCropTool.grid(
            row=rowIdx,
            column=4,
            rowspan=9,
            columnspan=4,
            sticky=tkinter.W,
            padx=padding,
            pady=padding,
        )
        self.btnTrackbarLeft.grid(
            row=rowIdx + 9, column=4, columnspan=1, sticky='E', padx=0, pady=0
        )
        self.sclFrameTrackbar.grid(
            row=rowIdx + 9, column=5, columnspan=2, sticky='EW', padx=0, pady=0
        )
        self.btnTrackbarRight.grid(
            row=rowIdx + 9, column=7, columnspan=1, sticky='W', padx=0, pady=0
        )
        self.lblFrameCtr.grid(
            row=rowIdx + 10, column=4, columnspan=2, sticky='w', padx=padding, pady=0
        )
        self.lblFrameDimensions.grid(
            row=rowIdx + 10, column=6, columnspan=2, sticky='E', padx=padding, pady=0
        )

        self.canCropTool.bind(whichRclickMouseButton, self.OnShowPreview, add='')
        self.canCropTool.bind(whichRclickReleaseMouseButton, self.OnStopPreview, add='')

        # Settings sliders and checkboxes
        #######################################################################

        # Time

        padding = self.guiConf['guiPadding']
        rowIdx += 1
        self.lblStart = tkinter.Label(self.boxTweaks, text='Start Time')
        self.lblStart2 = tkinter.Label(self.boxTweaks, text='')

        self.startTimeHour = tkinter.StringVar()
        self.startTimeMin = tkinter.StringVar()
        self.startTimeSec = tkinter.StringVar()
        self.startTimeMilli = tkinter.StringVar()
        self.startTimeHour.set('0')
        self.startTimeMin.set('00')
        self.startTimeSec.set('00')
        self.startTimeMilli.set('0')

        self.sclStart = tkinter.Scale(
            self.boxTweaks,
            from_=1,
            to=1,
            resolution=1,
            tickinterval=0,
            orient=tkinter.HORIZONTAL,
            sliderlength=20,
            width=self.guiConf['mainSliderHeight'],
            length=self.guiConf['mainSliderWidth'],
            showvalue=False,
            command=self.OnStartSliderUpdated,
        )
        self.spnStartTimeHour = tkinter.Spinbox(
            self.boxTweaks,
            font=self.defaultFont,
            from_=0,
            to=9,
            values=TIME_VALUES_10,
            increment=1,
            width=self.guiConf['timeSpinboxWidth'],
            textvariable=self.startTimeHour,
            validate=tkinter.ALL,
            wrap=True,
            command=self.OnStartChanged,
            name='startHour',
            repeatdelay=200,
            repeatinterval=150,
        )
        self.lblHourSep = tkinter.Label(self.boxTweaks, text=':')
        self.spnStartTimeMin = tkinter.Spinbox(
            self.boxTweaks,
            font=self.defaultFont,
            from_=0,
            to=59,
            values=TIME_VALUES_60,
            increment=1,
            width=self.guiConf['timeSpinboxWidth'],
            textvariable=self.startTimeMin,
            validate=tkinter.ALL,
            wrap=True,
            command=self.OnStartChanged,
            name='startMin',
            repeatdelay=250,
            repeatinterval=50,
        )
        self.lblMinSep = tkinter.Label(self.boxTweaks, text=':')
        self.spnStartTimeSec = tkinter.Spinbox(
            self.boxTweaks,
            font=self.defaultFont,
            from_=0,
            to=59,
            values=TIME_VALUES_60,
            increment=1,
            width=self.guiConf['timeSpinboxWidth'],
            textvariable=self.startTimeSec,
            validate=tkinter.ALL,
            wrap=True,
            command=self.OnStartChanged,
            name='startSec',
            repeatdelay=250,
            repeatinterval=50,
        )
        self.lblSecSep = tkinter.Label(self.boxTweaks, text='.')
        self.spnStartTimeMilli = tkinter.Spinbox(
            self.boxTweaks,
            font=self.defaultFont,
            from_=0,
            to=9,
            values=TIME_VALUES_10,
            increment=1,
            width=self.guiConf['timeSpinboxWidth'],
            textvariable=self.startTimeMilli,
            validate=tkinter.ALL,
            wrap=True,
            command=self.OnStartChanged,
            name='startMilli',
            repeatdelay=200,
            repeatinterval=150,
        )

        self.lblStart.grid(
            row=rowIdx,
            column=0,
            columnspan=1,
            sticky=tkinter.W,
            padx=padding - 3,
            pady=padding - 3,
        )
        self.sclStart.grid(
            row=rowIdx,
            column=1,
            columnspan=15,
            sticky=tkinter.W,
            padx=padding - 3,
            pady=padding - 3,
        )
        rowIdx += 1
        self.lblStart2.grid(
            row=rowIdx,
            column=0,
            columnspan=1,
            sticky=tkinter.W,
            padx=padding,
            pady=padding,
        )
        self.spnStartTimeHour.grid(
            row=rowIdx,
            column=1,
            columnspan=1,
            sticky=tkinter.EW,
            padx=padding,
            pady=padding,
        )
        self.lblHourSep.grid(
            row=rowIdx,
            column=2,
            columnspan=1,
            sticky=tkinter.EW,
            padx=padding,
            pady=padding,
        )
        self.spnStartTimeMin.grid(
            row=rowIdx,
            column=3,
            columnspan=1,
            sticky=tkinter.EW,
            padx=padding,
            pady=padding,
        )
        self.lblMinSep.grid(
            row=rowIdx,
            column=4,
            columnspan=1,
            sticky=tkinter.EW,
            padx=padding,
            pady=padding,
        )
        self.spnStartTimeSec.grid(
            row=rowIdx,
            column=5,
            columnspan=1,
            sticky=tkinter.EW,
            padx=padding,
            pady=padding,
        )
        self.lblSecSep.grid(
            row=rowIdx,
            column=6,
            columnspan=1,
            sticky=tkinter.EW,
            padx=padding,
            pady=padding,
        )
        self.spnStartTimeMilli.grid(
            row=rowIdx,
            column=7,
            columnspan=1,
            sticky=tkinter.EW,
            padx=padding,
            pady=padding,
        )

        rowIdx += 1
        self.duration = tkinter.StringVar()
        self.lblDuration = tkinter.Label(self.boxTweaks, text='Length (sec)')
        self.spnDuration = tkinter.Spinbox(
            self.boxTweaks,
            font=self.defaultFont,
            from_=0.1,
            to=120,
            increment=0.1,
            width=5,
            textvariable=self.duration,
            command=self.OnDurationChanged,
            repeatdelay=300,
            repeatinterval=25,
            wrap=True,
        )
        self.lblDuration.grid(
            row=rowIdx,
            column=0,
            columnspan=1,
            sticky=tkinter.W,
            padx=padding,
            pady=padding,
        )
        self.spnDuration.grid(
            row=rowIdx,
            column=1,
            columnspan=2,
            sticky=tkinter.W,
            padx=padding,
            pady=padding,
        )

        self.duration.set('0.1')

        if IM_A_PC:
            self.spnDuration.bind('<MouseWheel>', self.OnDurationMouseWheel)

        valueFontColor = '#353535'

        maxFps = self.conf.GetParam('rate', 'maxFrameRate')

        rowIdx += 1
        self.lblFps = tkinter.Label(self.boxTweaks, text='Smoothness (fps)')
        self.sclFps = tkinter.Scale(
            self.boxTweaks,
            from_=1,
            to=maxFps,
            resolution=1,
            tickinterval=0,
            orient=tkinter.HORIZONTAL,
            sliderlength=20,
            width=self.guiConf['mainSliderHeight'],
            length=self.guiConf['mainSliderWidth'],
            font=self.defaultFontTiny,
            fg=valueFontColor,
            showvalue=True,
            command=self.OnFpsChanged,
        )
        self.lblFps.grid(row=rowIdx, column=0, sticky=tkinter.W, padx=padding, pady=padding)
        self.sclFps.grid(
            row=rowIdx,
            column=1,
            columnspan=15,
            sticky=tkinter.W,
            padx=padding - 3,
            pady=padding - 3,
        )

        rowIdx += 1
        self.lblBlankLine = tkinter.Label(self.boxTweaks, text=' ', font=self.defaultFontTiny)
        self.lblBlankLine.grid(row=rowIdx, column=0, sticky=tkinter.W, padx=0, pady=0)

        rowIdx += 1
        self.lblResize = tkinter.Label(self.boxTweaks, text='Frame Size')
        self.sclResize = tkinter.Scale(
            self.boxTweaks,
            from_=5,
            to=100,
            resolution=1,
            tickinterval=0,
            orient=tkinter.HORIZONTAL,
            sliderlength=20,
            width=self.guiConf['mainSliderHeight'],
            length=self.guiConf['mainSliderWidth'],
            font=self.defaultFontTiny,
            fg=valueFontColor,
            showvalue=True,
            command=self.OnCropUpdate,
        )
        self.lblResize.grid(row=rowIdx, column=0, sticky=tkinter.W, padx=padding, pady=padding)
        self.sclResize.grid(
            row=rowIdx,
            column=1,
            columnspan=15,
            sticky=tkinter.W,
            padx=padding - 3,
            pady=padding - 3,
        )

        rowIdx += 1
        self.lblNumColors = tkinter.Label(self.boxTweaks, text='Quality')
        self.sclNumColors = tkinter.Scale(
            self.boxTweaks,
            from_=1,
            to=100,
            resolution=1,
            tickinterval=0,
            orient=tkinter.HORIZONTAL,
            sliderlength=20,
            width=self.guiConf['mainSliderHeight'],
            length=self.guiConf['mainSliderWidth'],
            font=self.defaultFontTiny,
            fg=valueFontColor,
            showvalue=True,
        )
        self.lblNumColors.grid(row=rowIdx, column=0, sticky=tkinter.W, padx=padding, pady=padding)
        self.sclNumColors.grid(
            row=rowIdx,
            column=1,
            columnspan=15,
            sticky=tkinter.W,
            padx=padding - 3,
            pady=padding - 3,
        )

        rowIdx += 1
        self.lblBright = tkinter.Label(self.boxTweaks, text='Brightness')
        self.sclBright = tkinter.Scale(
            self.boxTweaks,
            from_=-9,
            to=9,
            resolution=1,
            tickinterval=0,
            orient=tkinter.HORIZONTAL,
            sliderlength=20,
            width=self.guiConf['mainSliderHeight'],
            length=self.guiConf['mainSliderWidth'],
            font=self.defaultFontTiny,
            fg=valueFontColor,
            showvalue=True,
        )
        # self.sclBright.bind('<Double-Button-1>', self.SnapResetBrightness)

        self.lblBright.grid(row=rowIdx, column=0, sticky=tkinter.W, padx=padding, pady=padding)
        self.sclBright.grid(
            row=rowIdx,
            column=1,
            columnspan=15,
            sticky=tkinter.W,
            padx=padding - 3,
            pady=padding - 3,
        )

        rowIdx += 1
        self.lblSpeedModifier = tkinter.Label(self.boxTweaks, text='Playback Rate')
        self.sclSpeedModifier = tkinter.Scale(
            self.boxTweaks,
            from_=-10,
            to=10,
            resolution=1,
            tickinterval=0,
            orient=tkinter.HORIZONTAL,
            sliderlength=20,
            width=self.guiConf['mainSliderHeight'],
            length=self.guiConf['mainSliderWidth'],
            font=self.defaultFontTiny,
            fg=valueFontColor,
            showvalue=True,
        )
        self.lblSpeedModifier.grid(
            row=rowIdx, column=0, sticky=tkinter.W, padx=padding, pady=padding
        )
        self.sclSpeedModifier.grid(
            row=rowIdx,
            column=1,
            columnspan=16,
            sticky=tkinter.W,
            padx=padding - 3,
            pady=padding - 3,
        )

        rowIdx += 1
        self.lblCaption = tkinter.Label(self.boxTweaks, text='Captions')
        self.currentCaption = tkinter.StringVar()
        self.cbxCaptionList = ttk.Combobox(self.boxTweaks, textvariable=self.currentCaption)
        self.captionTracer = None

        self.lblCaption.grid(
            row=rowIdx,
            column=0,
            columnspan=1,
            rowspan=1,
            sticky=tkinter.W,
            padx=padding,
            pady=padding,
        )
        self.cbxCaptionList.grid(
            row=rowIdx,
            column=1,
            columnspan=8,
            rowspan=1,
            sticky=tkinter.EW,
            padx=padding,
            pady=padding,
        )

        rowIdx += 1

        self.isGrayScale = tkinter.IntVar()
        self.isSharpened = tkinter.IntVar()
        self.sharpenedAmount = tkinter.IntVar()
        self.isDesaturated = tkinter.IntVar()
        self.desaturatedAmount = tkinter.IntVar()
        self.isSepia = tkinter.IntVar()
        self.sepiaAmount = tkinter.IntVar()
        self.isColorTint = tkinter.IntVar()
        self.colorTintAmount = tkinter.IntVar()
        self.colorTintColor = tkinter.StringVar()
        self.isFadedEdges = tkinter.IntVar()
        self.fadedEdgeAmount = tkinter.IntVar()
        self.isNashville = tkinter.IntVar()
        self.nashvilleAmount = tkinter.IntVar()
        self.isBlurred = tkinter.IntVar()
        self.blurredAmount = tkinter.IntVar()
        self.isBordered = tkinter.IntVar()
        self.borderAmount = tkinter.IntVar()
        self.borderColor = tkinter.StringVar()
        self.isCinemagraph = tkinter.IntVar()
        self.invertCinemagraph = tkinter.IntVar()
        self.isAudioEnabled = tkinter.IntVar()

        self.sepiaAmount.set(100)
        self.desaturatedAmount.set(100)
        self.sharpenedAmount.set(100)
        self.fadedEdgeAmount.set(100)
        self.colorTintAmount.set(100)
        self.borderAmount.set(100)
        self.colorTintColor.set('#0000FF')
        self.borderColor.set('#000000')
        self.nashvilleAmount.set(100)
        self.blurredAmount.set(100)

        self.isSharpened.trace_add('write', self.OnEffectsChange)
        self.sharpenedAmount.trace_add('write', self.OnEffectsChange)
        self.isDesaturated.trace_add('write', self.OnEffectsChange)
        self.desaturatedAmount.trace_add('write', self.OnEffectsChange)
        self.isSepia.trace_add('write', self.OnEffectsChange)
        self.sepiaAmount.trace_add('write', self.OnEffectsChange)
        self.isColorTint.trace_add('write', self.OnEffectsChange)
        self.colorTintAmount.trace_add('write', self.OnEffectsChange)
        self.colorTintColor.trace_add('write', self.OnEffectsChange)
        self.isFadedEdges.trace_add('write', self.OnEffectsChange)
        self.fadedEdgeAmount.trace_add('write', self.OnEffectsChange)
        self.isGrayScale.trace_add('write', self.OnEffectsChange)
        self.isBordered.trace_add('write', self.OnEffectsChange)
        self.borderAmount.trace_add('write', self.OnEffectsChange)
        self.borderColor.trace_add('write', self.OnEffectsChange)
        self.isCinemagraph.trace_add('write', self.OnEffectsChange)
        self.isNashville.trace_add('write', self.OnEffectsChange)
        self.nashvilleAmount.trace_add('write', self.OnEffectsChange)
        self.isBlurred.trace_add('write', self.OnEffectsChange)
        self.blurredAmount.trace_add('write', self.OnEffectsChange)
        self.invertCinemagraph.trace_add('write', self.OnEffectsChange)
        self.isAudioEnabled.trace_add('write', self.OnEffectsChange)

        self.lblEffects = tkinter.Label(self.boxTweaks, text='FX & Filters')
        self.btnEditEffects = tkinter.Button(
            self.boxTweaks,
            text='Open Effects Panel...',
            font=self.defaultFontTiny,
            command=self.OnEditEffects,
        )
        self.lblEffects.grid(row=rowIdx, column=0, sticky=tkinter.W, padx=padding, pady=padding)
        self.btnEditEffects.grid(
            row=rowIdx, column=1, columnspan=4, sticky='EW', padx=padding, pady=padding
        )

        # padding =  # Restore padding

        rowIdx += 1

        self.btnGenerateGif = tkinter.Button(
            frame,
            text='Create GIF!',
            height=2,
            font=self.defaultFontBig,
            command=self.OnCreateGif,
        )
        self.btnGenerateGif.grid(
            row=rowIdx, column=0, columnspan=8, sticky='EW', padx=padding, pady=padding
        )

        self.ResetInputs()

        # Show window
        self.parent.update()
        self.parent.deiconify()

        self.CenterWindow(self.parent)
        self.EnableInputs(False, True)

        #
        # Set default boolean menu settings
        #

        self.qualityMenu.invoke(youtubeQualityList.index(defaultQuality))

        if self.conf.GetParamBool('settings', 'overwriteGif'):
            self.settingsMenu.invoke(0)  # Argument refers to menu index

        if self.conf.GetParamBool('warnings', 'socialMedia'):
            self.settingsMenu.invoke(1)

        if self.conf.GetParamBool('size', 'fileOptimizer'):
            self.settingsMenu.invoke(4)

        # Load button gets focus
        self.btnFopen.focus()

        # Start timer
        self.RestartTimer()

        self.parent.bind('<Escape>', self.OnCancel)

        # Screen Capture Dialog variables
        #######################################################################

        self.screenCapDurationSec = tkinter.StringVar()
        self.screenCapLowerFps = tkinter.IntVar()
        self.screenCapRetina = tkinter.IntVar()
        self.screenCapShowCursor = tkinter.IntVar()
        self.screenCapDurationSec.set('5.0')

        tooltips = {
            self.txtFname: '',
            self.btnFopen: 'You can paste almost any website address containing a video. Otherwise leave the text field empty and click this button to browse your computer for a video. RIGHT-CLICK on this button if you want to multi-select images.',
            self.btnScreenCap: 'Want to record your screen? Use this feature to record game playback, Kodi, or whatever else.',
            self.sclStart: 'Use this slider to configure the time in the video where you want the GIF to begin. After a few seconds, Instagiffer will grab frames starting from here and put them in the preview area to the right.',
            self.spnStartTimeHour: 'Video extraction start time: Hour',
            self.spnStartTimeMin: 'Video extraction start time: Minute',
            self.spnStartTimeSec: 'Video extraction start time: Second',
            self.spnStartTimeMilli: 'Video extraction start time: Sub-second',
            self.sclFps: 'Choppy/Smooth: Use this slider to control the frame rate of your GIF. Increasing this setting will include more frames making the file size larger. This feature is disabled in Screen Capture mode.',
            self.sclResize: 'Tiny/Big: Use this slider to control the image size from 5% (for ants!) to 100%. Note: increasing this setting will make the file size larger.',
            self.sclNumColors: 'Low Quality/High Quality: Use this slider to control the images color quality.',
            self.sclBright: 'Dark/Bright: Control the image brightness. This setting does not normally affect the GIF file size.',
            self.sclSpeedModifier: 'Slowmo/Superfast: Slow down or speed up the playback rate. Does not affect the GIF file size.',
            self.btnEditEffects: 'All of the effects have been moved and improved. Click here to access.',
            self.cbxCaptionList: '',  # "Click here to add some text to your GIF",
            self.btnTrackbarLeft: 'View the previous frame. If you hold this button down, it will animate at the correct speed, but in reverse.',
            self.btnTrackbarRight: 'View the next frame. If you hold this button down, it will animate at the correct speed.',
        }

        # Bind tool tips
        for item, tipString in list(tooltips.items()):
            createToolTip(item, tipString)

        logging.info('Instagiffer main window has been created')

        if cmdline_video_path is not None:
            self.txtFname.insert(0, cmdline_video_path)
            self.OnLoadVideoEnterPressed(None)

        self._screencapXgbl = 0
        self._screencapYgbl = 0

    def CreateAppDataFolder(self):
        self.tempDir = igf_paths.create_working_dir(self.conf)

        if self.tempDir == '':
            self.Alert('Failed to create working folder', 'Unable to create working directory')
            raise SystemExit

    def ForceSingleInstance(self):
        # Enforced by plist setting in Mac
        if IM_A_PC:
            import win32api
            from win32event import CreateMutex
            from winerror import ERROR_ALREADY_EXISTS

            self.singleInstanceMutex = CreateMutex(None, False, 'instagiffer_single_instance_mutex')
            if win32api.GetLastError() == ERROR_ALREADY_EXISTS:
                self.Alert(
                    'Instagiffer is already running!',
                    'It looks like Instagiffer is already running. Please close it first.',
                )
                # raise SystemExit

    def OnDeleteTemporaryFiles(self, prompt=True):
        deleteConfirmed = False
        if prompt and self.tempDir is not None:
            deleteConfirmed = tkinter.messagebox.askyesno(
                'Are You Sure?',
                'This will delete all downloads as well as the session currently in progress. '
                'Are you sure? The following directory will be deleted:\n\n' + self.tempDir,
            )
        else:
            deleteConfirmed = True

        if deleteConfirmed and self.tempDir is not None:
            if self.gif is not None:
                self.gif = None
                self.ResetInputs()
                self.EnableInputs(False, True)
            try:
                if os.path.isdir(self.tempDir):
                    shutil.rmtree(self.tempDir)
            except Exception:
                self.Alert(
                    'Delete Failed',
                    "I was unable to delete Instagiffer's temporary files. Please delete the following folder manually:\n\n"
                    + self.tempDir,
                )
                return False

        logging.info('Temporary files deleted')
        return True

    def ReadConfig(self):
        # Read config
        self.conf.ReloadFromFile()

        # Menu config items
        if self.savePath is not None:
            self.conf.SetParam('paths', 'gifOutputPath', self.savePath)

        # Read menu settings
        self.OnChangeMenuSetting()

    def ChangeFileFormat(self, newFormat):
        if self.gif is None:
            return False

        if not newFormat:
            newFormat = igf_paths.EXT_GIF

        fname = os.path.splitext(self.gif.GetNextOutputPath())[0]
        return self.OnSetSaveLocation(f'{fname}{newFormat}')

    def OnSetSaveLocation(self, location=None):
        if location is None:
            formatList = [('Supported formats', ('*.gif', '*.webm', '*.mp4'))]

            if self.savePath is not None:
                default = self.savePath
            else:
                default = 'insta.gif'

            savePath = tkinter.filedialog.asksaveasfilename(
                filetypes=formatList, initialfile=default
            )

            if savePath == '':
                return
        else:
            savePath = location

        if igf_paths.get_file_extension(savePath) not in igf_paths.EXT_VIDEO:
            savePath += igf_paths.EXT_GIF

        if self.savePath != savePath:
            self.miscGifChanges += 1

        self.savePath = savePath

        self.conf.SetParam('paths', 'gifOutputPath', self.savePath)

        if self.gif is not None:
            self.gif.SetSavePath(self.savePath)

        self.SetStatus('Updated save location to ' + self.savePath)

    def OnImgurUpload(self):
        ret = None
        imgurErrorTitle = 'Imgur Upload Failed'
        imgurUploadSizeLimit = 2 * 1024 * 1024

        if self.gif is None or not self.gif.GifExists():
            self.Alert(
                imgurErrorTitle,
                "You haven't created your GIF yet. Just go ahead and click 'Create Gif' then try this again.",
            )
            return False
        elif self.gif.GetFinalOutputFormat() != igf_paths.EXT_GIF:
            self.Alert(imgurErrorTitle, f"File format must be '{igf_paths.EXT_GIF}'!")
            return False
        elif self.gif.GetSize() >= imgurUploadSizeLimit:
            self.Alert(
                imgurErrorTitle,
                f'File size must be under {imgurUploadSizeLimit / (1024 * 1024)} MB!',
            )
            return False

        self.SetStatus(
            'Uploading GIF to Imgur... During this time, Instagiffer will appear frozen. Patience, K?'
        )
        ret = self.gif.UploadGifToImgur()
        self.SetStatus(f'Gif uploaded to Imgur. URL: {ret}')

        if ret is None:
            self.Alert(imgurErrorTitle, 'Failed to upload GIF to Imgur. Grrrrr...')
            return False
        else:
            self.Alert(
                'Imgur Upload Succeeded!',
                'Your GIF has been uploaded. The following address has been copied to your clipboard:\n\n%s'
                % (ret),
            )
            return True

    def OnSaveVideoForLater(self):
        if self.gif is None:
            return False

        title = self.gif.GetVideoFileName()

        if not len(title):
            self.Alert(
                'Not a Video File',
                'Source media is not in video format. This feature is for video files only!',
            )
        else:
            savePath = tkinter.filedialog.asksaveasfilename(
                filetypes=[('All files', '*.*')], initialfile=title
            )
            if len(savePath):
                self.gif.SaveOriginalVideoAs(savePath)

        return False

    # This is the UI handler for all checkbox menu items
    def OnChangeMenuSetting(self):
        # Warning settings

        if len(self.socialMediaWarningsEnabled.get()):
            self.conf.SetParamBool(
                'Warnings',
                'socialMedia',
                bool(int(self.socialMediaWarningsEnabled.get()) == 1),
            )

        # Overwrite setting

        if len(self.overwriteOutputGif.get()):
            overwriteFlag = bool(int(self.overwriteOutputGif.get()) == 1)
            # Update the configuration object
            self.conf.SetParamBool('Settings', 'overwriteGif', overwriteFlag)
            # Update the GIF object with the new settings too
            if self.gif:
                self.gif.OverwriteOutputGif(overwriteFlag)

        # Download quality setting
        self.conf.SetParam('settings', 'downloadQuality', self.downloadQuality.get())

        # File size optimize
        if len(self.fileSizeOptimize.get()):
            self.frameTimingOrCompressionChanges += self.conf.SetParamBool(
                'size', 'fileOptimizer', bool(int(self.fileSizeOptimize.get()) == 1)
            )

    def OnCancel(self, event):
        if self.guiBusy:
            if tkinter.messagebox.askyesno(
                'Cancel Request',
                'Are you sure you want to cancel the current operation?',
            ):
                logging.info('Cancel Event')
                self.cancelRequest = True

    def OnFpsChanged(self, event):
        self.RestartTimer()

    def OnDurationMouseWheel(self, event):
        duration = float(self.duration.get())
        max_dur = float(self.spnDuration.cget('to'))
        min_dur = float(self.spnDuration.cget('from'))

        if event.num == 5 or event.delta == -120:
            duration -= 0.1
        if event.num == 4 or event.delta == 120:
            duration += 0.1

        if duration < min_dur:
            duration = max_dur
        elif duration > max_dur:
            duration = min_dur

        self.duration.set('%.1f' % (duration))
        self.OnDurationChanged()

    def OnDurationChanged(self):
        self.RestartTimer()

        retVal = True

        try:
            duration = float(self.duration.get())
            if duration < 0.1:
                retVal = False
        except ValueError:
            retVal = False

        return retVal

    def TrackbarToTimeFields(self):
        positionSec = self.sclStart.get()
        positionComponents = igf_common.milliseconds_to_duration_components(positionSec * 1000)

        self.spnStartTimeHour.delete(0, 'end')
        self.spnStartTimeHour.insert(0, '%d' % positionComponents[0])

        self.spnStartTimeMin.delete(0, 'end')
        self.spnStartTimeMin.insert(0, '%02d' % positionComponents[1])

        self.spnStartTimeSec.delete(0, 'end')
        self.spnStartTimeSec.insert(0, '%02d' % positionComponents[2])

        self.spnStartTimeMilli.delete(0, 'end')
        self.spnStartTimeMilli.insert(0, '0')

    def OnStartSliderUpdated(self, unknown):
        # if self.gif is not None:
        #     if self.gif.GetThumbAge() > 0.9:
        #         self.gif.GetVideoThumb(self.GetStartTimeString(), self.canvasSize)
        #         if self.gif.ThumbFileExists():
        #             self.ShowImageOnCanvas(self.gif.GetThumbImagePath())

        self.TrackbarToTimeFields()
        self.OnStartChanged()
        return True

    def OnStartChanged(self, widget_name='', prior_value=''):
        trackbarPosSec = igf_common.duration_str_to_sec(
            '%02d:%02d:%02d:%03d'
            % (
                int(self.spnStartTimeHour.get()),
                int(self.spnStartTimeMin.get()),
                int(self.spnStartTimeSec.get()),
                100 * int(self.spnStartTimeMilli.get()),
            )
        )

        maxTrackbarPos = 0

        if self.gif is not None:
            maxTrackbarPos = int(self.gif.GetVideoLengthSec()) - 2
            if maxTrackbarPos < 0:
                maxTrackbarPos = 0

        if maxTrackbarPos != 0 and trackbarPosSec > maxTrackbarPos:
            trackbarPosSec = maxTrackbarPos
            self.TrackbarToTimeFields()

        self.sclStart.set(trackbarPosSec)

        self.RestartTimer()

    def SetStatus(self, status):
        if self.status.cget('text') != status:
            logging.info("SetStatus: '" + status + "'")
            self.status.config(text=status)
            self.status.update_idletasks()

    def CenterWindow(self, widget):
        widget.update_idletasks()
        width = widget.winfo_width()
        height = widget.winfo_height()
        x = (widget.winfo_screenwidth() / 2) - (width / 2)
        y = (widget.winfo_screenheight() / 2) - (height / 2)
        widget.geometry('{0}x{1}+{2}+{3}'.format(width, height, int(x), int(y)))

    def InitializeCropTool(self):
        if not self.gif:
            return False

        # logging.info("Initialize Cropper")
        videoWidth = self.gif.GetVideoWidth()
        videoHeight = self.gif.GetVideoHeight()
        scaleFactor = self.canvasSize / float(max(videoWidth, videoHeight))
        newWidth = videoWidth * scaleFactor
        newHeight = videoHeight * scaleFactor
        previewX = 0
        previewY = 0
        previewX2 = 0
        previewY2 = 0

        # Set the resize value to match the scaled preview
        self.sclResize.set(scaleFactor * 100)

        if newWidth < self.canvasSize:
            previewX = int((self.canvasSize - newWidth) / 2) - 1

        if newHeight < self.canvasSize:
            previewY = int((self.canvasSize - newHeight) / 2) - 1

        previewX2 = previewX + newWidth
        previewY2 = previewY + newHeight

        self.frameCounterStr.set('')
        self.canCropTool.delete('all')
        self.canCropTool.create_rectangle(
            previewX,
            previewY,
            previewX2,
            previewY2,
            outline='black',
            fill='gray13',
            width=1,
            tags=['videoScale'],
        )
        self.canCropTool.create_rectangle(
            previewX,
            previewY,
            previewX2,
            previewY2,
            width=1,
            outline='red',
            tags=['cropRect'],
        )
        self.canCropTool.create_rectangle(
            0, 0, 0, 0, outline='black', fill='red', width=1, tags=['cropSizeTL']
        )
        self.canCropTool.create_rectangle(
            0, 0, 0, 0, outline='black', fill='red', width=1, tags=['cropSizeBR']
        )
        self.canCropTool.create_rectangle(
            0, 0, 0, 0, outline='red', fill='black', width=1, tags=['cropMove']
        )

        if IM_A_MAC:
            whichRMouseEvent = '<B2-Motion>'
        else:
            whichRMouseEvent = '<B3-Motion>'

        self.canCropTool.tag_bind('cropMove', '<B1-Motion>', self.OnCropMove)
        self.canCropTool.tag_bind('cropSizeTL', '<B1-Motion>', self.OnCropSizeTL)
        self.canCropTool.tag_bind('cropSizeBR', '<B1-Motion>', self.OnCropSizeBR)
        self.canCropTool.tag_bind('cropSizeTL', whichRMouseEvent, self.OnCropSizeTLRestrictAxis)
        self.canCropTool.tag_bind('cropSizeBR', whichRMouseEvent, self.OnCropSizeBRRestrictAxis)

        self.canCropTool.bind('<Double-Button-1>', self.OnDoubleClickDelete)
        self.OnCropUpdate()

    # This function needs to be re-entrant!!
    def UpdateThumbnailPreview(self):
        if self.gif is None:
            return

        self.canCropTool.delete('previewBG')
        self.canCropTool.delete('preview')

        imgList = self.gif.GetExtractedImageList()

        if len(imgList) <= 0:
            self.canCropTool.delete('thumbnail')
            return

        arrayIdx = self.GetThumbNailIndex() - 1

        try:
            imgPath = imgList[arrayIdx]
        except IndexError:
            logging.error('Error. %d out of range' % (arrayIdx))
            return

        (px, py, px2, py2) = self.canCropTool.coords('videoScale')

        img = None

        # Cached thumbnail mode
        if self.conf.GetParamBool('settings', 'cacheThumbs'):
            # Update thumbnail memory cache
            framesOnDiskTs = self.gif.GetExtractedImagesLastModifiedTs()
            if self.thumbNailsUpdatedTs < framesOnDiskTs:
                logging.info(
                    'Thumbnail cache is stale (%d < %d)'
                    % (self.thumbNailsUpdatedTs, framesOnDiskTs)
                )
                self.thumbNailsUpdatedTs = -1
                newThumbCache = dict()
                self.thumbNailCache = dict()  # erase cache

                self.SetStatus('Updating thumbnail previews...')

                for thumbPath in imgList:
                    self.OnShowProgress(False)
                    try:
                        newThumbCache[thumbPath] = PIL.Image.open(thumbPath)
                        newThumbCache[thumbPath] = newThumbCache[thumbPath].resize(
                            (int(px2 - px) + 1, int(py2 - py) + 1),
                            PIL.Image.Resampling.NEAREST,
                        )
                    except IOError:
                        logging.error(
                            'Unable to generate thumbnail for %s. Image does not exist'
                            % (thumbPath)
                        )
                        self.thumbNailsUpdatedTs = -2
                        return

                self.SetStatus('')
                self.OnShowProgress(True)

                self.thumbNailCache = newThumbCache
                self.thumbNailsUpdatedTs = time.time()

            try:
                img = self.thumbNailCache[imgPath]
            except Exception:
                logging.error(
                    'Thumbnail cache miss: %s. Marking thumbnail cache as stale' % imgPath
                )
                self.thumbNailsUpdatedTs = -3
                return
        #
        # Direct-from-disk thumbnail mode
        #
        else:
            img = PIL.Image.open(imgPath)
            img = img.resize((int(px2 - px) + 1, int(py2 - py) + 1), PIL.Image.Resampling.BICUBIC)

        self.thumbnailPreview = PIL.ImageTk.PhotoImage(img)
        self.canCropTool.delete('thumbnail')
        self.canCropTool.create_image(
            px, py, image=self.thumbnailPreview, tag='thumbnail', anchor=tkinter.NW
        )
        self.canCropTool.tag_lower('videoScale')
        self.canCropTool.tag_raise('cropRect')
        self.canCropTool.tag_raise('cropMove')
        self.canCropTool.tag_raise('cropSizeTL')
        self.canCropTool.tag_raise('cropSizeBR')

        # Update frame counter and track bar
        if len(imgList):
            self.frameCounterStr.set('Frame  %d / %d' % (self.thumbnailIdx, len(imgList)))
            self.sclFrameTrackbar.configure(to=len(imgList))  # This can recurse?

    def TrackbarCanPlay(self):
        since = (time.time() - self.trackBarTs) * 1000

        frameDelayMs = 100
        if self.gif:
            frameDelayMs = self.gif.GetGifFrameDelay(self.sclSpeedModifier.get()) * 10

        lateByMs = since - frameDelayMs
        if lateByMs < 80 and lateByMs > frameDelayMs:
            skipFrame = 1 + int(round(lateByMs / float(frameDelayMs)))
            self.trackBarTs = time.time()
            # logging.info("late by %d ms (frame delay %d) frames %f" %(lateByMs, frameDelayMs, skipFrame))
            return skipFrame
        elif since < 0 or since > frameDelayMs:
            self.trackBarTs = time.time()
            return 1
        else:
            return 0

    def OnTrackbarLeft(self):
        framesCount = self.TrackbarCanPlay()

        if framesCount >= 1:
            self.SetThumbNailIndex(self.GetThumbNailIndex() - framesCount)
            self.UpdateThumbnailPreview()
            self.parent.update_idletasks()
        return True

    def OnTrackbarRight(self):
        framesCount = self.TrackbarCanPlay()

        if framesCount >= 1:
            self.SetThumbNailIndex(self.GetThumbNailIndex() + framesCount)
            self.UpdateThumbnailPreview()
            self.parent.update_idletasks()
        return True

    def OnFrameTrackbarMove(self, newVal):
        self.SetThumbNailIndex(int(newVal))
        self.UpdateThumbnailPreview()
        return True

    def ResetFrameTrackbar(self):
        self.sclFrameTrackbar.set('1')

    def GetThumbNailIndex(self):
        return self.thumbnailIdx

    def SetThumbNailIndex(self, idx=None):
        # trackBarPos = 0

        if idx is None:
            idx = self.thumbnailIdx

        if self.gif is None:
            idx = 1
        else:
            if idx <= 0:
                idx = self.gif.GetNumFrames()
            elif idx > self.gif.GetNumFrames():
                idx = 1

        self.sclFrameTrackbar.set(idx)
        self.thumbnailIdx = idx

    def OnDoubleClickDelete(self, event):
        if self.gif is None or self.guiBusy:
            return

        # last frame currently selected
        isLastFrame = False
        if self.gif.GetNumFrames() == self.thumbnailIdx:
            isLastFrame = True

        self.DeleteFrame(self.thumbnailIdx, self.thumbnailIdx)

        #  Issue #157
        if isLastFrame and self.gif.GetNumFrames() > 0:
            self.SetThumbNailIndex(self.gif.GetNumFrames())

    def DeleteFrame(self, fromIdx, toIdx, evenOnly=0):
        if self.gif is None:
            return
        frameList = self.gif.GetExtractedImageList()
        countBeforeDelete = len(frameList)

        # Out of range
        if fromIdx > countBeforeDelete:
            return True

        if evenOnly:
            stepSize = 2
        else:
            stepSize = 1

        # Do we have frames to delete
        if countBeforeDelete > 1:
            for x in range(fromIdx - 1, toIdx, stepSize):
                try:
                    os.remove(frameList[x])
                except IndexError:
                    break

                self.SetStatus(
                    "Deleted frame %d '%s' from animation sequence"
                    % (x + 1, os.path.basename(frameList[x]))
                )

        # Tell cache not to update. Deletes are OK
        self.thumbNailsUpdatedTs = time.time()

        self.SetThumbNailIndex()

        # Update the frame counter
        if len(frameList) > 0:
            self.UpdateThumbnailPreview()
        else:
            self.frameCounterStr.set('')

        return True

    def TranslateToCanvas(self, val):
        if self.gif is None:
            return 0

        frameScale = self.sclResize.get() / 100.0
        videoWidth = self.gif.GetVideoWidth()
        videoHeight = self.gif.GetVideoHeight()
        scaleFactor = float(self.canvasSize) / (frameScale * max(videoWidth, videoHeight))

        ret = val * scaleFactor
        return ret

    def GetCropSettingsFromCanvas(self, isScaled=True, doRounding=True):
        if self.gif is None:
            return

        if len(self.canCropTool.find_withtag('cropRect')) <= 0:
            raise Exception('Cropper has not been initialized yet')

        if len(self.canCropTool.find_withtag('preview')) > 0:
            raise Exception('Preview being displayed. Wait..')

        (cx, cy, cx2, cy2) = self.canCropTool.coords('cropRect')
        (px, py, px2, py2) = self.canCropTool.coords('videoScale')

        videoWidth = self.gif.GetVideoWidth()
        videoHeight = self.gif.GetVideoHeight()
        scaleFactor = max(videoWidth, videoHeight) / float(self.canvasSize)

        if isScaled:
            frameScale = self.sclResize.get() / 100.0
        else:
            frameScale = 1.0

        rw = (cx2 - cx) * scaleFactor * frameScale
        rh = (cy2 - cy) * scaleFactor * frameScale
        ratio = rw / rh
        rw = rw
        rh = rh

        rx = (cx - px) * scaleFactor * frameScale
        ry = (cy - py) * scaleFactor * frameScale
        rwmax = videoWidth * frameScale
        rhmax = videoHeight * frameScale

        if doRounding:
            return (
                int(round(rx)),
                int(round(ry)),
                int(round(rw)),
                int(round(rh)),
                int(round(rwmax)),
                int(round(rhmax)),
                ratio,
            )
        else:
            return rx, ry, rw, rh, rwmax, rhmax, ratio

    def SnapCropperHandles(self):
        if len(self.canCropTool.find_withtag('cropRect')) <= 0:
            return

        (cx, cy, cx2, cy2) = self.canCropTool.coords('cropRect')
        (px, py, px2, py2) = self.canCropTool.coords('videoScale')

        if cx < px:
            cx = px
        if cy < py:
            cy = py
        if cx2 > px2:
            cx2 = px2
        if cy2 > py2:
            cy2 = py2

        # Correct cropper rect
        self.canCropTool.coords('cropRect', cx, cy, cx2, cy2)

        # Move sizer and mover handles
        self.canCropTool.coords(
            'cropSizeTL', cx, cy, cx + self.cropSizerSize, cy + self.cropSizerSize
        )

        self.canCropTool.coords(
            'cropSizeBR', cx2 - self.cropSizerSize, cy2 - self.cropSizerSize, cx2, cy2
        )

        self.canCropTool.coords(
            'cropMove',
            cx + (cx2 - cx) / 2 - self.cropSizerSize / 2,
            cy + (cy2 - cy) / 2 - self.cropSizerSize / 2,
            cx + (cx2 - cx) / 2 + self.cropSizerSize / 2,
            cy + (cy2 - cy) / 2 + self.cropSizerSize / 2,
        )

    def OnCropUpdate(self, unused=None):
        try:
            sx, sy, sw, sh, smaxw, smaxh, sratio = self.GetCropSettingsFromCanvas(True)
            x, y, w, h, maxw, maxh, ratio = self.GetCropSettingsFromCanvas(False)
        except Exception:
            return

        self.SnapCropperHandles()

        self.cropWidth = str(w)
        self.cropHeight = str(h)
        self.cropStartX = str(x)
        self.cropStartY = str(y)
        self.finalSize = '%dx%d' % (sw, sh)

        self.frameDimensionsStr.set(self.finalSize + ', ratio: %.3f:1' % (sratio))

    def OnCropMove(self, event):
        (cx, cy, cx2, cy2) = self.canCropTool.coords('cropRect')
        (px, py, px2, py2) = self.canCropTool.coords('videoScale')
        (mx, my, mx2, my2) = self.canCropTool.coords('cropMove')

        deltaX = (event.x - mx) - self.cropSizerSize / 2
        deltaY = (event.y - my) - self.cropSizerSize / 2

        if cy + deltaY < py:
            deltaY = (cy - py) * -1
        if cy2 + deltaY > py2:
            deltaY = (cy2 - py2) * -1
        if cx + deltaX < px:
            deltaX = (cx - px) * -1
        if cx2 + deltaX > px2:
            deltaX = (cx2 - px2) * -1

        self.canCropTool.coords('cropRect', cx + deltaX, cy + deltaY, cx2 + deltaX, cy2 + deltaY)
        self.OnCropUpdate()

    def OnCropSizeTL(self, event):
        self.OnCropSizeTLImpl(False, event)

    def OnCropSizeTLRestrictAxis(self, event):
        self.OnCropSizeTLImpl(True, event)

    def OnCropSizeTLImpl(self, freezeX, event):
        (cx, cy, cx2, cy2) = self.canCropTool.coords('cropRect')
        (px, py, px2, py2) = self.canCropTool.coords('videoScale')
        (sx, sy, sx2, sy2) = self.canCropTool.coords('cropSizeTL')

        deltaX = event.x - sx
        deltaY = event.y - sy

        if freezeX:
            deltaX = 0

        if sx + deltaX < px:
            deltaX = (sx - px) * -1
        if sy + deltaY < py:
            deltaY = (sy - py) * -1
        if sy2 + deltaY > cy2:
            deltaY = cy2 - sy2
        if sx2 + deltaX > cx2:
            deltaX = cx2 - sx2

        self.canCropTool.coords('cropRect', cx + deltaX, cy + deltaY, cx2, cy2)
        self.OnCropUpdate()

    def OnCropSizeBR(self, event):
        self.OnCropSizeBRImpl(False, event)

    def OnCropSizeBRRestrictAxis(self, event):
        self.OnCropSizeBRImpl(True, event)

    def OnCropSizeBRImpl(self, freezeY, event):
        (cx, cy, cx2, cy2) = self.canCropTool.coords('cropRect')
        (px, py, px2, py2) = self.canCropTool.coords('videoScale')
        (sx, sy, sx2, sy2) = self.canCropTool.coords('cropSizeBR')

        deltaX = event.x - sx
        deltaY = event.y - sy

        if freezeY:
            deltaY = 0

        if sx2 + deltaX > px2:
            deltaX = px2 - sx2
        if sy2 + deltaY > py2:
            deltaY = py2 - sy2
        if sy + deltaY < cy:
            deltaY = (sy - cy) * -1
        if sx + deltaX < cx:
            deltaX = (sx - cx) * -1

        self.canCropTool.coords('cropRect', cx, cy, cx2 + deltaX, cy2 + deltaY)
        self.OnCropUpdate()

    def OnShowProgress(self, doneFlag, statusBarOutput=None):
        if isinstance(doneFlag, bool) and doneFlag:
            self.progressBarPosition.set(0)
            self.guiBusy = False
        else:
            if statusBarOutput is not None:
                self.SetStatus(statusBarOutput.replace('\n', '').replace('\r', ''))

            if isinstance(doneFlag, int):
                self.progressBarPosition.set(doneFlag)
            else:
                self.progressBar.step(1)  # indefinite

            self.guiBusy = True

        self.parent.update_idletasks()
        self.parent.update()

        if self.cancelRequest:
            self.progressBarPosition.set(0)
            self.cancelRequest = False
            return False
        else:
            return True

    def OnWindowClose(self):
        # Cancel any actions in progress
        self.OnCancel(None)

        if self.conf:
            if self.conf.GetParamBool('settings', 'deleteTempFilesOnClose'):
                self.OnDeleteTemporaryFiles(False)  # Don't prompt

        self.parent.quit()

    def RestartTimer(self):
        if self.timerHandle is not None:
            self.parent.after_cancel(self.timerHandle)
        self.timerHandle = self.parent.after(self.mainTimerValueMS, self.OnTimer)

    def OnTimer(self):
        if not self.guiBusy:
            if self.conf.GetParamBool('settings', 'autoExtract'):
                self.ProcessImage(1)

        self.RestartTimer()

    def OnViewImageStillsInExplorer(self):
        if self.gif is None:
            return

        self.ProcessImage(1)

        openExplorerCmd = ''

        if IM_A_PC:
            openExplorerCmd = 'explorer '
        elif IM_A_MAC:
            openExplorerCmd = 'open '
        else:
            openExplorerCmd = 'xdg-open '

        openExplorerCmd += '"' + self.gif.GetExtractedImagesDir() + '"'
        logging.info('Open in explorer command: ' + openExplorerCmd)

        if not IM_A_PC:
            openExplorerCmd = shlex.split(openExplorerCmd)

        subprocess.Popen(openExplorerCmd)

    def Alert(self, title, message):
        logging.info('Alert: title: [%s], message: [%s]' % (title, message.strip()))
        tkinter.messagebox.showinfo(title, message)

    def OnRClickPopup(self, event):
        def RClickPaste(event):
            if IM_A_MAC:
                pasteAction = '<<Paste>>'
            else:
                pasteAction = '<Control-v>'

            event.widget.event_generate(pasteAction)

        def RClickClear(event):
            event.widget.delete(0, tkinter.END)

        event.widget.focus()
        popUp = tkinter.Menu(None, tearoff=0, takefocus=0)
        popUp.add_command(label='Paste', command=lambda event=event: RClickPaste(event))
        popUp.add_command(label='Clear', command=lambda event=event: RClickClear(event))
        popUp.tk_popup(event.x_root + 40, event.y_root + 10, entry='0')

    def About(self, event=None):
        self.Alert(
            'About Instagiffer',
            'You are running Instagiffer '
            + __version__
            + '!\nFollow us on Tumblr for updates, tips and tricks: www.instagiffer.com',
        )

    def ViewLog(self):
        numLines = sum(1 for line in open(igf_paths.get_log_path()))

        if numLines <= 7:
            tkinter.messagebox.showinfo(
                'Bug Report',
                'It looks like the bug report is currently empty. Please try to reproduce the bug first, and then generate the report',
            )

        igf_paths.open_file_with_default_app(igf_paths.get_log_path())

    def OpenFAQ(self):
        igf_paths.open_file_with_default_app(igf_common.__faqUrl__)

    def CheckForUpdates(self):
        igf_paths.open_file_with_default_app(igf_common.__changelogUrl__)

    def ResetInputs(self):
        self.canCropTool.delete('all')  # Clear off the crop tool
        # if not self.gif.ExtractedImagesExist():
        self.InitializeCropTool()

        self.maskEventList.clear()  # Remove all mask edits

        if self.captionTracer is not None:
            self.currentCaption.trace_vdelete('w', self.captionTracer)
            self.captionTracer = None

        self.cbxCaptionList['values'] = ('[Click here to add a new caption]',)
        self.cbxCaptionList.current(0)
        self.captionTracer = self.currentCaption.trace_add('write', self.OnCaptionSelect)

        for strVar in [self.startTimeHour, self.startTimeMilli]:
            strVar.set('0')

        for strVar in [self.startTimeMin, self.startTimeSec]:
            strVar.set('00')

        for scales in [
            self.sclNumColors,
            self.sclBright,
            self.sclSpeedModifier,
            self.sclResize,
            self.sclFps,
        ]:  # self.sclSaturation,
            scales.set(-1000)

        #
        # Set the maximum slider value for smoothness (FPS). Should not be able to set greater than the source material's fps
        #
        fps = float(self.conf.GetParam('rate', 'maxFrameRate'))

        if self.gif is not None and self.gif.GetVideoFps() < fps:
            fps = self.gif.GetVideoFps()

        self.sclFps.config(to=fps)
        self.sclStart.set(0)

    def ValidateInputs(self):
        retVal = True

        if (
            not self.startTimeHour.get().isdigit()
            or int(self.startTimeHour.get()) < 0
            or int(self.startTimeHour.get()) > 9
        ):
            retVal = False
        if (
            not self.startTimeMin.get().isdigit()
            or int(self.startTimeMin.get()) < 0
            or int(self.startTimeMin.get()) > 59
        ):
            retVal = False
        if (
            not self.startTimeSec.get().isdigit()
            or int(self.startTimeSec.get()) < 0
            or int(self.startTimeSec.get()) > 59
        ):
            retVal = False
        if int(self.startTimeMilli.get()) < 0 or int(self.startTimeMilli.get()) > 9:
            retVal = False

        # try:
        #     duration = float(self.duration.get())
        #     if duration < 0.1:
        #         retVal = False
        # except ValueError:
        #     retVal = False

        return retVal

    #
    # otherOptions:
    def EnableInputs(self, optionsRequiringLoadedVideo, otherOptions, forceEnable=False):
        if self.gif is None:
            optionsRequiringLoadedVideo = False

        # I need to reword the arguments or clean this up somehow. It's very confusing... the whole function. On the plus side, it works
        timeBasedOptionsAllowed = True
        if self.gif:
            if not forceEnable and not self.gif.SourceIsVideo():
                timeBasedOptionsAllowed = False

        for inputs in [
            self.spnStartTimeHour,
            self.spnStartTimeMin,
            self.spnStartTimeSec,
            self.spnStartTimeMilli,
            self.spnDuration,
        ]:
            if timeBasedOptionsAllowed and optionsRequiringLoadedVideo:
                inputs.configure(state='normal')
            else:
                inputs.configure(state='disabled')

        for inputs in [
            self.btnGenerateGif,
            self.sclFrameTrackbar,
            self.btnTrackbarRight,
            self.btnTrackbarLeft,
        ]:
            if optionsRequiringLoadedVideo:
                inputs.configure(state='normal')
            else:
                inputs.configure(state='disabled')

        if optionsRequiringLoadedVideo:
            if timeBasedOptionsAllowed:
                self.sclFps.configure(state='normal')

            self.cbxCaptionList.configure(state='readonly')
            self.sclNumColors.configure(state='normal')
            self.sclBright.configure(state='normal')
            self.sclResize.configure(state='normal')
            self.sclSpeedModifier.configure(state='normal')
            self.btnEditEffects.configure(state='normal')

            self.fileMenu.entryconfigure(2, state='normal')  # imgur

            if self.gif is not None and self.gif.IsDownloadedVideo():
                self.fileMenu.entryconfigure(0, state='normal')  # save for later

            for x in range(0, self.frameMenuItemCount):
                self.frameMenu.entryconfigure(x, state='normal')

        else:
            self.fileMenu.entryconfigure(0, state='disabled')  # save for later
            self.fileMenu.entryconfigure(2, state='disabled')  # imgur

            self.cbxCaptionList.configure(state='disabled')
            self.sclFps.configure(state='disabled')
            self.sclNumColors.configure(state='disabled')
            self.sclBright.configure(state='disabled')
            self.sclResize.configure(state='disabled')
            self.sclSpeedModifier.configure(state='disabled')
            self.btnEditEffects.configure(state='disabled')

            for x in range(2, 3):
                self.fileMenu.entryconfigure(x, state='disabled')
            for x in range(0, self.frameMenuItemCount):
                self.frameMenu.entryconfigure(x, state='disabled')

        if otherOptions:
            self.btnFopen.configure(state='normal')
            self.btnScreenCap.configure(state='normal')

            for x in (1, 3, 5):
                self.fileMenu.entryconfigure(x, state='normal')
            for x in range(0, 3):
                self.settingsMenu.entryconfigure(x, state='normal')  # Doesn't work
            for x in range(0, 3):
                self.qualityMenu.entryconfigure(x, state='normal')
        else:
            self.btnFopen.configure(state='disabled')
            self.btnScreenCap.configure(state='disabled')

            for x in range(0, 2, 3):
                self.fileMenu.entryconfigure(x, state='disabled')
            for x in range(0, 3):
                self.settingsMenu.entryconfigure(x, state='normal')  # Doesn't work
            for x in range(0, 3):
                self.qualityMenu.entryconfigure(x, state='normal')

    def LoadDefaultEntryValues(self, videoLen):
        if self.gif is None:
            return
        self.guiBusy = True

        self.lastProcessTsByLevel = [0, 0, 0, 0]

        # The following settings engine -> App

        duration = float(self.gif.GetConfig().GetParam('length', 'durationSec'))

        if duration == 0.0 or (videoLen > 0.0 and videoLen < duration):
            duration = videoLen

        self.duration.set(str(duration))

        if self.gif.SourceIsVideo():
            fps = int(self.gif.GetConfig().GetParam('rate', 'frameRate'))
        else:
            fps = self.gif.GetVideoFps()

        self.sclFps.set(fps)
        self.sclResize.set(int(self.gif.GetConfig().GetParam('size', 'resizePostCrop')))
        self.sclSpeedModifier.set(int(self.gif.GetConfig().GetParam('rate', 'speedModifier')))
        self.sclNumColors.set(int(self.gif.GetConfig().GetParam('color', 'numColors')) / 2.55)
        self.sclBright.set(int(self.gif.GetConfig().GetParam('effects', 'brightness')) / 10.0)
        self.isGrayScale.set(self.gif.GetConfig().GetParam('color', 'colorSpace') == 'Gray')
        self.isDesaturated.set(int(self.gif.GetConfig().GetParam('color', 'saturation')) < 0)
        self.isBlurred.set(int(self.gif.GetConfig().GetParam('effects', 'blur')) > 0)
        self.isSharpened.set(self.gif.GetConfig().GetParamBool('effects', 'sharpen'))
        self.isSepia.set(self.gif.GetConfig().GetParamBool('effects', 'sepia'))
        self.isFadedEdges.set(self.gif.GetConfig().GetParamBool('effects', 'fadeEdges'))
        self.isColorTint.set(self.gif.GetConfig().GetParamBool('effects', 'colorTint'))
        self.isNashville.set(self.gif.GetConfig().GetParamBool('effects', 'nashville'))
        self.isBordered.set(self.gif.GetConfig().GetParamBool('effects', 'border'))
        self.isCinemagraph.set(self.gif.GetConfig().GetParamBool('blend', 'cinemagraph'))
        self.invertCinemagraph.set(self.gif.GetConfig().GetParamBool('blend', 'cinemagraphInvert'))
        self.isAudioEnabled.set(self.gif.GetConfig().GetParamBool('audio', 'audioEnabled'))

        self.InitializeCropTool()
        self.guiBusy = False

    def ShowImageOnCanvas(self, file_name):
        if not os.path.exists(file_name):
            return False

        canvasSz = int(self.canvasSize)

        img = PIL.Image.open(file_name)

        # thumbAreaSize = (int(self.canvasSize)+1, int(self.canvasSize)+1)
        # img.thumbnail((canvasSz, canvasSz), PIL.Image.ANTIALIAS)

        w, h = img.size

        if w <= 0 or h <= 0:
            return

        scaleFactor = canvasSz / float(max(w, h))
        img = img.resize(
            (int(scaleFactor * w) + 1, int(scaleFactor * h) + 1),
            PIL.Image.Resampling.BICUBIC,
        )
        w, h = img.size

        x = abs(int((w - canvasSz) / 2)) - 1
        y = abs(int((h - canvasSz) / 2)) - 1

        if x < 0:
            x = 0
        if y < 0:
            y = 0

        self.thumbnailPreview = PIL.ImageTk.PhotoImage(img)
        self.canCropTool.delete('previewBG')
        self.canCropTool.delete('preview')
        self.canCropTool.create_rectangle(
            0,
            0,
            canvasSz,
            canvasSz,
            outline='black',
            fill='black',
            width=1,
            tags=['previewBG'],
        )
        self.canCropTool.create_image(
            x, y, image=self.thumbnailPreview, tag='preview', anchor=tkinter.NW
        )

    def OnShowPreview(self, event):
        if self.gif is None:
            return False

        # Right mouse clicks
        if event is not None and (self.guiBusy or self.showPreviewFlag):
            return False

        self.showPreviewFlag = True

        self.ProcessImage(3, True)

        if not self.showPreviewFlag:
            return False

        if self.gif.PreviewFileExists():
            self.ShowImageOnCanvas(self.gif.GetPreviewImagePath())

        return True

    def OnStopPreview(self, event):
        if self.gif is None or not self.showPreviewFlag:
            return False

        self.UpdateThumbnailPreview()
        self.showPreviewFlag = False

    def OnCreateGif(self):
        self.ProcessImage(3)

    def GetStartTimeString(self):
        return '%02d:%02d:%02d.%03d' % (
            int(self.spnStartTimeHour.get()),
            int(self.spnStartTimeMin.get()),
            int(self.spnStartTimeSec.get()),
            100 * int(self.spnStartTimeMilli.get()),
        )

    def ProcessImage(self, processStages, preview=False):
        errorMsg = ''
        doUpdateThumbs = False

        if not self.ValidateInputs():
            return False, 'Invalid input detected'

        if self.gif is None:
            return False

        timeOrRateSettingChanges = 0
        sizeSettingChanges = 0
        gifSettingChanges = 0
        fileFormatSettingChanges = 0

        if processStages >= 1:
            startTime = self.GetStartTimeString()

            timeOrRateSettingChanges += self.gif.GetConfig().SetParam(
                'rate', 'frameRate', str(self.sclFps.get())
            )
            timeOrRateSettingChanges += self.gif.GetConfig().SetParam(
                'length', 'startTime', startTime
            )
            timeOrRateSettingChanges += self.gif.GetConfig().SetParam(
                'length', 'durationSec', self.spnDuration.get()
            )

            # Sanity checks
            if timeOrRateSettingChanges > 0:
                totalFrames = int(float(self.spnDuration.get()) * int(self.sclFps.get()))
                if totalFrames > 9999:
                    return (
                        False,
                        'Instagiffer only supports up to 10000 frames per GIF internally',
                    )

                if totalFrames > int(self.conf.GetParam('settings', 'largeGif')):
                    if not tkinter.messagebox.askyesno(
                        'Be careful!',
                        "You're about to make a really long GIF. Are you sure you want to continue?",
                    ):
                        return False, 'User chose not to make a really long GIF'

        if processStages >= 2:
            if self.isCinemagraph.get():
                if self.maskEdited:
                    sizeSettingChanges += 1
                    self.maskEdited = 0

            sizeSettingChanges += self.cropResizeChanges
            sizeSettingChanges += self.gif.GetConfig().SetParamBool(
                'blend', 'cinemaGraph', self.isCinemagraph.get()
            )
            sizeSettingChanges += self.gif.GetConfig().SetParamBool(
                'blend', 'cinemaGraphInvert', self.invertCinemagraph.get()
            )
            sizeSettingChanges += self.gif.GetConfig().SetParam(
                'size', 'cropOffsetX', self.cropStartX
            )
            sizeSettingChanges += self.gif.GetConfig().SetParam(
                'size', 'cropOffsetY', self.cropStartY
            )
            sizeSettingChanges += self.gif.GetConfig().SetParam('size', 'cropWidth', self.cropWidth)
            sizeSettingChanges += self.gif.GetConfig().SetParam(
                'size', 'cropHeight', self.cropHeight
            )
            sizeSettingChanges += self.gif.GetConfig().SetParam(
                'size', 'resizePostCrop', self.finalSize
            )  # str(self.sclResize.get()))

            if not preview:
                self.cropResizeChanges = 0
            else:
                self.cropResizeChanges += sizeSettingChanges

        if processStages >= 3:
            colorSpace = 'CMYK'
            saturation = 0
            blur = 0

            if self.isGrayScale.get():
                colorSpace = 'Gray'
            if self.isDesaturated.get():
                saturation -= self.desaturatedAmount.get()
            if self.isBlurred.get():
                blur = self.blurredAmount.get()

            gifSettingChanges += self.gif.GetConfig().SetParam(
                'effects', 'brightness', self.sclBright.get() * 10
            )
            gifSettingChanges += self.gif.GetConfig().SetParam(
                'effects', 'contrast', self.sclBright.get() * 10
            )
            gifSettingChanges += self.gif.GetConfig().SetParam('color', 'saturation', saturation)
            gifSettingChanges += self.gif.GetConfig().SetParamBool(
                'effects', 'sharpen', self.isSharpened.get()
            )
            gifSettingChanges += self.gif.GetConfig().SetParam(
                'effects', 'sharpenAmount', self.sharpenedAmount.get()
            )
            gifSettingChanges += self.gif.GetConfig().SetParamBool(
                'effects', 'sepiaTone', self.isSepia.get()
            )
            gifSettingChanges += self.gif.GetConfig().SetParam(
                'effects', 'sepiaToneAmount', self.sepiaAmount.get()
            )
            gifSettingChanges += self.gif.GetConfig().SetParamBool(
                'effects', 'colorTint', self.isColorTint.get()
            )
            gifSettingChanges += self.gif.GetConfig().SetParam(
                'effects', 'colorTintAmount', self.colorTintAmount.get()
            )
            gifSettingChanges += self.gif.GetConfig().SetParam(
                'effects', 'colorTintColor', self.colorTintColor.get()
            )
            gifSettingChanges += self.gif.GetConfig().SetParamBool(
                'effects', 'fadeEdges', self.isFadedEdges.get()
            )
            gifSettingChanges += self.gif.GetConfig().SetParam(
                'effects', 'fadeEdgeAmount', self.fadedEdgeAmount.get()
            )
            gifSettingChanges += self.gif.GetConfig().SetParamBool(
                'effects', 'border', self.isBordered.get()
            )
            gifSettingChanges += self.gif.GetConfig().SetParam(
                'effects', 'borderAmount', self.borderAmount.get()
            )
            gifSettingChanges += self.gif.GetConfig().SetParam(
                'effects', 'borderColor', self.borderColor.get()
            )
            gifSettingChanges += self.gif.GetConfig().SetParamBool(
                'effects', 'nashville', self.isNashville.get()
            )
            gifSettingChanges += self.gif.GetConfig().SetParam(
                'effects', 'nashvilleAmount', self.nashvilleAmount.get()
            )
            gifSettingChanges += self.gif.GetConfig().SetParam('effects', 'blur', str(blur))
            gifSettingChanges += self.gif.GetConfig().SetParam(
                'color', 'numColors', str(int(self.sclNumColors.get() * 2.55))
            )
            gifSettingChanges += self.gif.GetConfig().SetParam('color', 'colorSpace', colorSpace)
            gifSettingChanges += self.gif.GetConfig().SetParamBool(
                'audio', 'audioEnabled', self.isAudioEnabled.get()
            )

            # Make sure we catch any caption and/or image blitting changes
            gifSettingChanges += self.captionChanges
            gifSettingChanges += self.miscGifChanges

            # Settings that only affect final file format
            fileFormatSettingChanges += self.audioChanges
            fileFormatSettingChanges += self.frameTimingOrCompressionChanges
            fileFormatSettingChanges += self.gif.GetConfig().SetParam(
                'rate', 'speedModifier', str(self.sclSpeedModifier.get())
            )

            # We are assuming we are going to process these settings. Reset change counters
            if not preview:
                self.captionChanges = 0
                self.miscGifChanges = 0
                self.audioChanges = 0
            else:
                self.miscGifChanges += gifSettingChanges  # for now, just store these here

        # This code is too complicated

        # Keep track of the last time edits were made

        if self.lastProcessTsByLevel[1] == 0:
            timeOrRateSettingChanges += 1
        if (
            self.lastProcessTsByLevel[2] == 0
            or (self.lastProcessTsByLevel[1] > self.lastProcessTsByLevel[2])
            or (
                self.gif.GetExtractedImagesLastModifiedTs()
                > self.gif.GetResizedImagesLastModifiedTs()
            )
        ):
            sizeSettingChanges += 1
        if (
            self.lastProcessTsByLevel[3] == 0
            or (self.lastProcessTsByLevel[2] > self.lastProcessTsByLevel[3])
            or (self.gif.GetResizedImagesLastModifiedTs() > self.gif.GetGifLastModifiedTs())
            or len(self.gif.GetProcessedImageList()) == 0
        ):
            gifSettingChanges += 1

        # Conflict. They changed form settings that will generate new stills, but they also made manual edits in explorer
        if (
            self.lastProcessTsByLevel[1] > 0
            and self.lastProcessTsByLevel[1] < self.gif.GetExtractedImagesLastModifiedTs()
            and timeOrRateSettingChanges
        ):
            logging.info(
                'Edits detected. Prompt user. TimestampLastProcess: %d ; TimestampImagesLastModified: %d',
                self.lastProcessTsByLevel[1],
                self.gif.GetExtractedImagesLastModifiedTs(),
            )

            if tkinter.messagebox.askyesno(
                'I just noticed something!',
                'It looks like you imported frames, deleted frames, or made image edits in another program. '
                + 'Making changes to animation smoothness, duration or start time will generate a new sequence of '
                + 'images, overwriting your changes. Would you like to generate a new sequence of images based your updated settings?',
            ):
                timeOrRateSettingChanges = 1
            else:
                timeOrRateSettingChanges = 0

        processOk = True
        inputDisabled = False

        try:
            if processStages >= 1 and timeOrRateSettingChanges > 0:
                self.ResetFrameTrackbar()
                self.EnableInputs(False, False)
                inputDisabled = True
                self.SetStatus('(1/' + str(processStages) + ') Extracting frames...')
                self.gif.ExtractFrames()

                #
                # Dup detection and removal
                #

                frameCount = self.gif.GetNumFrames()
                deleteDupFrames = self.conf.GetParamBool('settings', 'autoDeleteDuplicateFrames')

                self.SetStatus('(1/' + str(processStages) + ') Checking for duplicate frames...')
                numDups = self.gif.CheckDuplicates(deleteDupFrames)

                if numDups > 0 and deleteDupFrames:
                    self.SetStatus(
                        '%d/%d were duplicates. Delete: %s'
                        % (numDups, frameCount, str(deleteDupFrames))
                    )

                if not self.gif.SourceIsVideo() and frameCount > 20 and frameCount - 1 == numDups:
                    raise Exception(
                        "How boring! All of your frames are exactly the same! Note: If you're looking a black/blank image, try screen capturing on your other monitor - it's a known issue."
                    )

                self.lastProcessTsByLevel[1] = time.time()

                doUpdateThumbs = True

            if processStages >= 2 and (timeOrRateSettingChanges or sizeSettingChanges):
                self.EnableInputs(False, False)
                inputDisabled = True

                if preview:
                    self.SetStatus(
                        'Generating Preview... First preview takes a few secs to generate. Subsequent previews will be quicker...'
                    )
                else:
                    self.SetStatus('(2/' + str(processStages) + ') Cropping and resizing...')

                if not preview:
                    self.gif.CropAndResize()
                    self.lastProcessTsByLevel[2] = time.time()

            imageProcessingRequired = (
                timeOrRateSettingChanges or sizeSettingChanges or gifSettingChanges
            )
            if processStages >= 3 and (
                (imageProcessingRequired or fileFormatSettingChanges) or preview
            ):
                self.EnableInputs(False, False)
                inputDisabled = True

                if preview:
                    self.SetStatus('Generating preview')
                    self.gif.GenerateFramePreview(self.GetThumbNailIndex())
                else:
                    self.SetStatus(
                        '(3/'
                        + str(processStages)
                        + ') Applying effects and generating %s (%s)...'
                        % (
                            self.gif.GetFinalOutputFormat(),
                            self.gif.GetNextOutputPath(),
                        )
                    )
                    self.gif.Generate(not imageProcessingRequired)
                    self.lastProcessTsByLevel[3] = time.time()

                self.SetStatus('Done')

        except Exception as e:
            self.guiBusy = True
            errorMsg = str(e)
            logging.error(errorMsg)

            # Yuck. We get this if user kills the app with the X while the GUI is busy.
            # Intercept the nasty error message and avoid awkward app shutdown by forcing the app to close now
            if 'invalid command name' in errorMsg:
                # self.parent.quit()
                raise SystemExit

            processOk = False

            processStageNames = [
                'Unknown',
                'frame extraction',
                'cropping and resizing',
                '%s creation' % (self.gif.GetFinalOutputFormat()),
            ]

            self.Alert(
                'A problem occurred during %s' % processStageNames[processStages],
                '%s' % errorMsg,
            )

            # logging.error(traceback.format_exception(*sys.exc_info()))
            self.guiBusy = False

        if processOk and processStages >= 3 and not preview:
            self.EnableInputs(False, False)
            inputDisabled = True

            # Check for Tumblr warnings
            if not self.gif.GifExists():
                self.Alert(
                    'GIF not found!',
                    "I Can't find the GIF %s" % (self.gif.GetLastGifOutputPath()),
                )
            elif len(self.gif.GetProcessedImageList()) == 0:
                self.Alert(
                    'Frames Not Found',
                    'Processed %s frames not found' % (self.gif.GetIntermediaryFrameFormat()),
                )
            else:
                if self.gif.GetCompatibilityWarning() and self.gif.CompatibilityWarningsEnabled():
                    self.Alert(
                        "Wait! This won't display properly on some social media sites!!",
                        self.gif.GetCompatibilityWarning(),
                    )

                self.SetStatus(
                    'GIF saved. GIF size: '
                    + str(self.gif.GetSize() / 1024)
                    + 'kB. Path: '
                    + self.gif.GetLastGifOutputPath()
                )

                self.PlayGif(self.gif.GetProcessedImageList(), self.gif.GetGifFrameDelay())

        if doUpdateThumbs:
            self.SetThumbNailIndex(1)
            self.UpdateThumbnailPreview()

        if inputDisabled:
            self.EnableInputs(True, True)

        return processOk, errorMsg

    def ParseVideoPathInput(self, videoPath):
        if videoPath is None:
            return ''

        if type(videoPath) is list:
            fileList = list()
            for f in videoPath:
                if len(f) > 0:
                    fileList.append(f)
            fileList.sort()
        else:
            fileList = videoPath.split('|')

        imgCount = 0
        otherCount = 0
        for f in fileList:
            f = f.replace('/', os.sep)
            logging.info('Filename: "' + f + '"')
            if igf_paths.is_picture_file(f):
                imgCount += 1
            else:
                otherCount += 1

        totalCount = imgCount + otherCount

        logging.info(
            'Total file count %d (Images: %d; Other: %d)' % (totalCount, imgCount, otherCount)
        )

        if totalCount == 0:
            return ''

        if (imgCount > 0 and otherCount > 0) or (otherCount > 1):
            self.Alert(
                'Multiple Files',
                "You can only select multiple pictures. Only one video/GIF can be loaded at a time - e-mail us if you'd like us to add this feature.",
            )
            return ''

        if imgCount > 1:
            returnStr = '|'.join(fileList)
        else:
            returnStr = fileList[0]

        return returnStr

    def OnShiftLoadVideo(self, event):
        self.OnLoadVideo(False, True)

    def OnLoadVideoEnterPressed(self, event):
        self.OnLoadVideo(True)

    def OnLoadVideo(self, enterPressed=False, multi_select=False):
        rc = True
        errStr = 'Unknown error'
        urlPatterns = re.compile(r'^(www\.|https://|http://)')
        capPattern = re.compile(
            r'^::capture ([\.0-9]+) ([\.0-9]+) ([0-9]+)x([0-9]+)\+(\-?[0-9]+)\+(\-?[0-9]+) cursor=(\d) retina=(\d) web=(\d)$'
        )
        file_name = self.txtFname.get().strip()

        # Check same URL?
        if self.gif is not None and self.gif.IsSameVideo(file_name, self.downloadQuality.get()):
            logging.info('URL present in textfield. Show Open dialog')
            file_name = ''

        if file_name == 'random':
            file_name = 'http://www.petittube.com/'  # random url
            self.txtFname.delete(0, tkinter.END)

        if urlPatterns.match(file_name):
            self.SetStatus('Downloading video information. Please wait...')
            logging.info('Download ' + file_name)
        elif capPattern.match(file_name):
            self.SetStatus('Capturing screen...')
        elif enterPressed:
            self.SetStatus('Loading manually-specified path...')
            logging.info('User entered ' + file_name)

            file_name = self.ParseVideoPathInput(file_name)

            if len(file_name) == 0:
                return False

        else:
            file_names = ()
            if IM_A_PC:
                if multi_select:
                    file_names = tkinter.filedialog.askopenfilenames(
                        filetypes=[('Media files', '*.*')],
                        parent=self.parent,
                        title='Find a video or images to GIF',
                    )
                else:
                    file_names = tkinter.filedialog.askopenfilename(
                        filetypes=[('Media files', '*.*')],
                        parent=self.parent,
                        title='Find a video or images to GIF',
                    )
            else:
                if multi_select:
                    file_names = tkinter.filedialog.askopenfilenames()
                else:
                    file_names = tkinter.filedialog.askopenfilename()

            try:
                logging.info('Open returned: ' + str(file_names) + ' (%s)' % (type(file_names)))
            except Exception:
                logging.info('Failed to decode value returned by Open dialog')

            if file_names is None:
                return False

            if not isinstance(file_names, tuple):
                file_list = [file_names]
            else:
                file_list = list(file_names)

            file_name = self.ParseVideoPathInput(file_list)

            # Populate text field with user's choice
            if len(file_name):
                self.SetStatus('Loading video, please wait...')
                self.txtFname.delete(0, tkinter.END)
                self.txtFname.insert(0, file_name)
            else:
                return False

        # Delete ::capture text from textbox
        if capPattern.match(file_name):
            self.txtFname.delete(0, tkinter.END)

        # Load configuration defaults from file
        self.ReadConfig()
        self.SetLogoDefaults()  # Needs to be persistent over their session

        self.EnableInputs(False, False)

        # Attempt to open the video for processing
        if len(file_name):
            try:
                self.gif = igf_animgif.AnimatedGif(
                    self.conf, file_name, self.tempDir, self.OnShowProgress, self.parent
                )

            except Exception as e:
                self.gif = None
                rc = False
                errStr = str(e)

                # If we're in debug mode, show a stack trace
                if not __release__:
                    tb = traceback.format_exc()
                    errStr += '\n\n' + str(tb)

        # Allow inputs enabled on all inputs... so that we can load default values
        self.EnableInputs(True, True, True)
        self.ResetInputs()

        if rc and self.gif is not None:
            self.LoadDefaultEntryValues(videoLen=self.gif.GetVideoLengthSec())
            rc, estr = self.ProcessImage(1)
            errStr = estr

        # Turn off forceEnable
        self.EnableInputs(True, True, False)

        if rc and self.gif is not None:
            if self.gif.GetVideoLength() is None:
                self.SetStatus(
                    'Video loaded. Total runtime is unknown; '
                    + str(self.gif.GetVideoFps())
                    + ' fps'
                )
                self.spnDuration.config(wrap=False)

            else:
                self.SetStatus(
                    'Video loaded. Total runtime: '
                    + self.gif.GetVideoLength()
                    + '; '
                    + str(self.gif.GetVideoFps())
                    + ' fps'
                )

                # Set the trackbar properties
                trackbarTo = 1
                durationMax = 1

                if self.gif.GetVideoLengthSec() > 1.0:
                    trackbarTo = int(self.gif.GetVideoLengthSec())

                self.spnDuration.config(to=trackbarTo)
                self.spnDuration.config(wrap=True)
                self.sclStart.config(resolution=1, to=trackbarTo)

        else:
            self.gif = None
            self.txtFname.delete(0, tkinter.END)
            self.EnableInputs(False, True)

            if (
                'ordinal not in range' in errStr
            ):  # fix this particularly ugly error that keeps showing up
                self.Alert(
                    'Language Issue Detected',
                    'Instagiffer is having trouble with your language. Please generate a bug report and send it to instagiffer@gmail.com. This issue is a top priority! Sorry for the inconvenience!',
                )
                logging.error(errStr)
            else:
                self.Alert(
                    'A Problem Occurred',
                    f'Error: {errStr}\n\nIf you think this is a bug, please generate a bug report and '
                    'send it to instagiffer@gmail.com.',
                )

            self.SetStatus('Failed to load video!')
            self.ResetInputs()

        return rc

    def CreateChildDialog(self, title, resizable=False, parent=None):
        if parent is None:
            parent = self.parent

        popupWindow = tkinter.Toplevel(parent)
        popupWindow.withdraw()
        popupWindow.title(title)
        #  popupWindow.wm_iconbitmap('instagiffer.ico')
        # popupWindow.transient(self.mainFrame)         #

        if not resizable:
            popupWindow.resizable(False, False)

        self.guiBusy = True
        return popupWindow

    def ReModalDialog(self, dlg):
        dlg.update()
        dlg.deiconify()
        dlg.lift()
        dlg.focus()
        dlg.grab_set()
        # self.guiBusy = True

    def WaitForChildDialog(self, dlg, dlgGeometry=None):
        dlg.update()
        dlg.deiconify()
        dlg.lift()
        dlg.focus()

        # Get current geometry
        width = dlg.winfo_width()
        height = dlg.winfo_height()
        x = self.mainFrame.winfo_rootx()
        y = self.mainFrame.winfo_rooty()
        geom = '{0}x{1}+{2}+{3}'.format(width, height, x, y)

        if dlgGeometry == 'center':
            self.CenterWindow(dlg)
            geom = dlg.geometry()

        # Restore geometry
        if dlgGeometry is not None and len(dlgGeometry) and dlgGeometry != 'center':
            geom = dlgGeometry

        # Set window geometry
        dlg.geometry(geom)
        # Send mouse and key events to child window
        dlg.grab_set()

        # Block until window is destroyed
        self.parent.wait_window(dlg)

        self.guiBusy = False
        return True

    def PlayGif(self, filename, frameDelay):
        if not self.gif:
            self.Alert('Gif Player', 'Internal error. Unable to play!')
            return

        popupWindow = self.CreateChildDialog('Instagiffer GIF Preview')

        isResizable = self.conf.GetParamBool('settings', 'resizablePlayer')

        if isResizable:
            popupWindow.resizable(width=tkinter.TRUE, height=tkinter.TRUE)
            popupWindow.columnconfigure(0, weight=1)
            popupWindow.rowconfigure(0, weight=1)
        else:
            popupWindow.resizable(width=tkinter.FALSE, height=tkinter.FALSE)

        try:
            # Should the audio be previewed
            soundPath = None
            if (
                IM_A_PC
                and self.isAudioEnabled.get()
                and self.HaveAudioPath()
                and self.gif.GetFinalOutputFormat() != igf_paths.EXT_GIF
            ):
                soundPath = self.gif.GetAudioClipPath()

            anim = GifPlayerWidget(popupWindow, filename, frameDelay * 10, isResizable, soundPath)
        except MemoryError:
            self.Alert('Gif Player', 'Unable to show preview. Your GIF is too big.')
            return

        def OnDeletePlayer():
            try:
                anim.Stop()
            except Exception:
                pass

            popupWindow.destroy()

        lbl = 'Location: ' + self.gif.GetLastGifOutputPath()
        if IM_A_MAC and self.isAudioEnabled and self.HaveAudioPath():
            lbl += ' \n(Sound not available in this player)'

        # Build form componets
        lblInfo = tkinter.Label(popupWindow, text=lbl)
        btnClose = tkinter.Button(popupWindow, text='Close', padx=10, pady=10)

        # Place items on dialog
        anim.grid(row=0, column=0, padx=5, pady=5, sticky=tkinter.NSEW)
        lblInfo.grid(row=1, column=0, padx=5, pady=5, sticky=tkinter.NSEW)
        btnClose.grid(row=2, column=0, padx=5, pady=5, sticky=tkinter.NSEW)

        # Attach handlers
        popupWindow.protocol('WM_DELETE_WINDOW', OnDeletePlayer)
        btnClose.configure(command=OnDeletePlayer)

        return self.WaitForChildDialog(popupWindow)

    def OnCaptionSelect(self, *args):
        if not self.guiBusy:
            self.OnCaptionConfig()

    def OnScreenCapture(self):
        resizable = True
        popupWindow = self.CreateChildDialog('Screen Capture Configuration', resizable)

        # Set always-on-top and transparent
        popupWindow.wm_attributes('-alpha', 0.7)
        popupWindow.wm_attributes('-topmost', True)

        lblDuration = tkinter.Label(popupWindow, font=self.defaultFont, text='Length (s)')
        spnDuration = tkinter.Spinbox(
            popupWindow,
            font=self.defaultFont,
            from_=1,
            to=60,
            increment=0.5,
            width=4,
            textvariable=self.screenCapDurationSec,
        )
        chkLowFps = tkinter.Checkbutton(
            popupWindow,
            font=self.defaultFont,
            text='Web-optimized',
            variable=self.screenCapLowerFps,
        )
        chkRetina = tkinter.Checkbutton(
            popupWindow,
            font=self.defaultFont,
            text='Retina Display',
            variable=self.screenCapRetina,
        )
        chkCursor = tkinter.Checkbutton(
            popupWindow,
            font=self.defaultFont,
            text='Show Cursor',
            variable=self.screenCapShowCursor,
        )
        lblResizeWindow = tkinter.Label(popupWindow, text='', background='#A2DEF2')
        btnStartCap = tkinter.Button(popupWindow, text='Start')

        # Place items on grid

        columns = 1
        btnStartCap.grid(row=1, column=columns, padx=2, pady=5)
        columns += 1
        lblDuration.grid(row=1, column=columns, padx=2, pady=2)
        columns += 1
        spnDuration.grid(row=1, column=columns, padx=2, pady=2)
        columns += 1
        chkLowFps.grid(row=1, column=columns, padx=2, pady=2)
        columns += 1

        if IM_A_MAC:
            chkRetina.grid(row=1, column=columns, padx=2, pady=2)
            columns += 1
        else:
            chkCursor.grid(row=1, column=columns, padx=2, pady=2)
            columns += 1

        lblResizeWindow.grid(row=0, column=0, columnspan=columns + 1, padx=2, pady=5, sticky='NSEW')
        popupWindow.rowconfigure(0, weight=1)
        popupWindow.columnconfigure(0, weight=1)

        tooltips = {
            spnDuration: 'Choose how long you wish to capture for. A reasonable value here is from 5-15 seconds.',
            chkRetina: "Check this if your Mac has a retina display. If you don't check this, your capture region will not match what actually gets recorded.",
            chkLowFps: 'Select this option if you plan on posting the GIF online. A lower frame rate and smaller image size provides you some budget to increase image quality.',
            chkCursor: 'If you want the cursor to be visible as a small dot, select this option.',
            btnStartCap: 'Click here to start recording. If you CTRL-CLICK, a 5 second count-down will occur',
        }

        for item, tipString in list(tooltips.items()):
            createToolTip(item, tipString)

        def GetCaptureDimensions():
            ww = popupWindow.winfo_width()
            wh = popupWindow.winfo_height()

            h = lblResizeWindow.winfo_height()
            w = lblResizeWindow.winfo_width()

            # print ww,wh,h,w

            if ww < w:
                w = ww - 2

                if w <= 0:
                    w = 1

            if wh < h:
                h = wh - 2
                if h <= 0:
                    h = 1

            return w, h, ww, wh

        def OnResize(event):
            w, h, ww, wh = GetCaptureDimensions()

            dimensionStr = '%dx%d' % (w, h)

            lblResizeWindow.config(text=dimensionStr)

        def OnMouseWheel(event, freezeX=False, freezeY=False):
            w, h, ww, wh = GetCaptureDimensions()

            modification = 0
            if event.delta < 0:  # down
                modification = -10
            if event.delta > 0:  # up
                modification = 10

            newW = ww
            newH = wh

            if not freezeX and w > 15:
                newW += modification
            if not freezeY and h > 15:
                newH += modification

            popupWindow.wm_geometry('%dx%d' % (newW, newH))

        def OnMouseWheelX(event):
            OnMouseWheel(event, freezeX=False, freezeY=True)

        def OnMouseWheelY(event):
            OnMouseWheel(event, freezeX=True, freezeY=False)

        def StartMove(event):
            self._screencapXgbl = event.x
            self._screencapYgbl = event.y

        def StopMove(event):
            self._screencapXgbl = 0
            self._screencapYgbl = 0

        def OnMotion(event):
            deltax = event.x - self._screencapXgbl
            deltay = event.y - self._screencapYgbl
            x = popupWindow.winfo_x() + deltax
            y = popupWindow.winfo_y() + deltay
            popupWindow.geometry('+%s+%s' % (x, y))

        def OnCloseScreenCapDlg():
            # Save window position
            self.screenCapDlgGeometry = popupWindow.geometry()
            popupWindow.destroy()

        def OnStartClicked(doCountdown=False):
            fps = self.conf.GetParam('screencap', 'frameRateLimit')

            try:
                float(self.screenCapDurationSec.get())
            except ValueError:
                self.Alert('Invalid Duration', 'The duration specified is invalid')
                return False

            if float(self.screenCapDurationSec.get()) < 1.0:
                self.Alert('Invalid Duration', 'Duration must be at least 1 second')
                return False

            w, h, ww, wh = GetCaptureDimensions()
            x = lblResizeWindow.winfo_rootx()
            y = lblResizeWindow.winfo_rooty()

            self.txtFname.delete(0, tkinter.END)
            self.txtFname.insert(
                0,
                '::capture %s %s %dx%d+%d+%d cursor=%d retina=%d web=%d'
                % (
                    self.screenCapDurationSec.get(),
                    fps,
                    w,
                    h,
                    x,
                    y,
                    self.screenCapShowCursor.get(),
                    self.screenCapRetina.get(),
                    self.screenCapLowerFps.get(),
                ),
            )

            OnCloseScreenCapDlg()

            # Show an on screen alert
            if doCountdown:
                captureWarning = tkinter.Toplevel(self.parent)
                captureWarning.wm_attributes('-topmost', True)
                captureWarning.wm_attributes('-alpha', 0.5)
                captureWarning.wm_overrideredirect(True)
                lnlCaptureCountdown = tkinter.Label(
                    captureWarning,
                    text='',
                    justify=tkinter.CENTER,
                    background='white',
                    relief=tkinter.SOLID,
                    borderwidth=3,
                    font=('Arial', '25', 'normal'),
                )
                lnlCaptureCountdown.grid(expand=1, fill='both')
                captureWarning.wm_geometry('200x75+0+0')  # % (w, h, x, y))

                countDownStr = ['Go!']
                countDownTimeSec = [0.15]
                countDownValSecs = int(self.conf.GetParam('screencap', 'countDownSeconds'))

                for x in range(1, countDownValSecs + 1):
                    countDownStr.insert(0, 'Capture in %d' % (x))
                    countDownTimeSec.insert(0, 1)

                for i in range(0, len(countDownStr)):
                    self.SetStatus(countDownStr[i])
                    lnlCaptureCountdown.configure(text=countDownStr[i])
                    self.OnShowProgress(False)
                    time.sleep(countDownTimeSec[i])

                captureWarning.destroy()

            # Make progress bar move
            self.OnShowProgress(True)
            self.OnLoadVideo()

        def OnCtrlStartClicked(event):
            OnStartClicked(True)

        # Attach handlers
        popupWindow.protocol('WM_DELETE_WINDOW', OnCloseScreenCapDlg)
        btnStartCap.configure(command=OnStartClicked)
        btnStartCap.bind('<Control-Button-1>', OnCtrlStartClicked)

        lblResizeWindow.bind('<ButtonPress-1>', StartMove)
        lblResizeWindow.bind('<ButtonRelease-1>', StopMove)
        lblResizeWindow.bind('<B1-Motion>', OnMotion)
        popupWindow.bind('<Configure>', OnResize)

        popupWindow.bind('<Control-MouseWheel>', OnMouseWheelX)
        popupWindow.bind('<Shift-MouseWheel>', OnMouseWheelY)
        popupWindow.bind('<MouseWheel>', OnMouseWheel)

        # Block until dialog closes
        self.WaitForChildDialog(popupWindow, self.screenCapDlgGeometry)

    def SetLogoDefaults(self):
        if len(self.OnSetLogoDefaults) > 0:
            self.miscGifChanges += self.conf.SetParamBool(
                'imagelayer1', 'applyFx', self.OnSetLogoDefaults['logoApplyFx']
            )
            self.miscGifChanges += self.conf.SetParam(
                'imagelayer1', 'path', self.OnSetLogoDefaults['logoPath']
            )
            self.miscGifChanges += self.conf.SetParam(
                'imagelayer1', 'positioning', self.OnSetLogoDefaults['logoPositioning']
            )
            self.miscGifChanges += self.conf.SetParam(
                'imagelayer1', 'resize', self.OnSetLogoDefaults['logoResize']
            )
            self.miscGifChanges += self.conf.SetParam(
                'imagelayer1', 'opacity', self.OnSetLogoDefaults['logoOpacity']
            )
            self.miscGifChanges += self.conf.SetParam(
                'imagelayer1', 'xNudge', self.OnSetLogoDefaults['logoXoffset']
            )
            self.miscGifChanges += self.conf.SetParam(
                'imagelayer1', 'yNudge', self.OnSetLogoDefaults['logoYoffset']
            )

    def OnSetLogo(self):
        # Default form values
        if len(self.OnSetLogoDefaults) == 0:
            self.OnSetLogoDefaults['logoApplyFx'] = self.conf.GetParamBool('imagelayer1', 'applyFx')
            self.OnSetLogoDefaults['logoPath'] = self.conf.GetParam('imagelayer1', 'path')
            self.OnSetLogoDefaults['logoPositioning'] = self.conf.GetParam(
                'imagelayer1', 'positioning'
            )
            self.OnSetLogoDefaults['logoResize'] = self.conf.GetParam('imagelayer1', 'resize')
            self.OnSetLogoDefaults['logoOpacity'] = self.conf.GetParam('imagelayer1', 'opacity')
            self.OnSetLogoDefaults['logoXoffset'] = self.conf.GetParam('imagelayer1', 'xNudge')
            self.OnSetLogoDefaults['logoYoffset'] = self.conf.GetParam('imagelayer1', 'yNudge')

        dlg = self.CreateChildDialog('Configure Logo')

        if dlg is None:
            return False

        lblPath = tkinter.Label(dlg, font=self.defaultFont, text='Image path')
        txtPath = tkinter.Entry(dlg, font=self.defaultFont, width=40)
        btnChooseFile = tkinter.Button(
            dlg, text='Browse...', padx=4, pady=4, width=15, font=self.defaultFontTiny
        )
        lblPos = tkinter.Label(dlg, font=self.defaultFont, text='Positioning')
        positioning = tkinter.StringVar()
        cbxPosition = ttk.Combobox(
            dlg,
            textvariable=positioning,
            state='readonly',
            width=15,
            values=(
                'Top Left',
                'Top',
                'Top Right',
                'Middle Left',
                'Center',
                'Middle Right',
                'Bottom Left',
                'Bottom',
                'Bottom Right',
            ),
        )
        lblFilters = tkinter.Label(dlg, font=self.defaultFont, text='Apply Filters')
        applyFxToLogo = tkinter.IntVar()
        chkApplyFxToLogo = ttk.Checkbutton(dlg, text='', variable=applyFxToLogo)

        lblResize = tkinter.Label(dlg, font=self.defaultFont, text='Size Percentage')
        resizePercent = tkinter.IntVar()
        spnResizePercent = tkinter.Spinbox(
            dlg,
            font=self.defaultFont,
            from_=1,
            to=100,
            increment=1,
            width=5,
            textvariable=resizePercent,
            repeatdelay=300,
            repeatinterval=30,
            state='readonly',
            wrap=True,
        )

        lblOpacity = tkinter.Label(dlg, font=self.defaultFont, text='Opacity')
        opacity = tkinter.IntVar()
        spnOpacityPercent = tkinter.Spinbox(
            dlg,
            font=self.defaultFont,
            from_=1,
            to=100,
            increment=1,
            width=5,
            textvariable=opacity,
            repeatdelay=300,
            repeatinterval=30,
            state='readonly',
            wrap=True,
        )

        lblXOffset = tkinter.Label(dlg, font=self.defaultFont, text='X Offset')
        xoffset = tkinter.IntVar()
        spnX = tkinter.Spinbox(
            dlg,
            font=self.defaultFont,
            from_=-500,
            to=500,
            increment=1,
            width=5,
            textvariable=xoffset,
            repeatdelay=300,
            repeatinterval=30,
            state='readonly',
        )

        lblYOffset = tkinter.Label(dlg, font=self.defaultFont, text='Y Offset')
        yoffset = tkinter.IntVar()
        spnY = tkinter.Spinbox(
            dlg,
            font=self.defaultFont,
            from_=-500,
            to=500,
            increment=1,
            width=5,
            textvariable=yoffset,
            repeatdelay=300,
            repeatinterval=30,
            state='readonly',
        )

        btnOk = tkinter.Button(dlg, text='OK', padx=4, pady=4)

        # Populate
        cbxPosition.set(self.OnSetLogoDefaults['logoPositioning'])  # Bottom
        txtPath.insert(0, self.OnSetLogoDefaults['logoPath'])
        applyFxToLogo.set(self.OnSetLogoDefaults['logoApplyFx'])
        resizePercent.set(self.OnSetLogoDefaults['logoResize'])
        opacity.set(self.OnSetLogoDefaults['logoOpacity'])
        xoffset.set(self.OnSetLogoDefaults['logoXoffset'])
        yoffset.set(self.OnSetLogoDefaults['logoYoffset'])

        # Place elements on grid
        rowIdx = -1

        rowIdx += 1
        lblPath.grid(row=rowIdx, column=0, sticky=tkinter.W, padx=4, pady=4)
        txtPath.grid(row=rowIdx, column=1, sticky=tkinter.W, padx=4, pady=4)
        btnChooseFile.grid(row=rowIdx, column=2, sticky=tkinter.EW, padx=4, pady=4)

        rowIdx += 1
        lblPos.grid(row=rowIdx, column=0, sticky=tkinter.W, padx=4, pady=4)
        cbxPosition.grid(row=rowIdx, column=1, sticky=tkinter.W, padx=4, pady=4, columnspan=2)

        rowIdx += 1
        lblFilters.grid(row=rowIdx, column=0, sticky=tkinter.W, padx=4, pady=4)
        chkApplyFxToLogo.grid(row=rowIdx, column=1, sticky=tkinter.W, padx=4, pady=4, columnspan=2)

        rowIdx += 1
        lblResize.grid(row=rowIdx, column=0, sticky=tkinter.W, padx=4, pady=4)
        spnResizePercent.grid(row=rowIdx, column=1, sticky=tkinter.W, padx=4, pady=4, columnspan=2)

        rowIdx += 1
        lblOpacity.grid(row=rowIdx, column=0, sticky=tkinter.W, padx=4, pady=4)
        spnOpacityPercent.grid(row=rowIdx, column=1, sticky=tkinter.W, padx=4, pady=4, columnspan=2)

        rowIdx += 1
        lblXOffset.grid(row=rowIdx, column=0, sticky=tkinter.W, padx=4, pady=4)
        spnX.grid(row=rowIdx, column=1, sticky=tkinter.W, padx=4, pady=4, columnspan=2)

        rowIdx += 1
        lblYOffset.grid(row=rowIdx, column=0, sticky=tkinter.W, padx=4, pady=4)
        spnY.grid(row=rowIdx, column=1, sticky=tkinter.W, padx=4, pady=4, columnspan=2)

        rowIdx += 1
        btnOk.grid(row=rowIdx, column=0, sticky=tkinter.EW, padx=4, pady=4, columnspan=3)

        tooltips = {
            btnChooseFile: 'Choose a logo image in .gif format. Your logo can contain transparency.',
            cbxPosition: 'Select where you wish the logo to be positioned on your GIF',
            chkApplyFxToLogo: 'If unchecked, your logo will appear on top of all of the effects, unprocessed',
            spnResizePercent: 'Scale down your logo if it is too large.',
            spnOpacityPercent: 'Control how transparent your logo is.',
            spnX: 'Shift logo this many pixels in the horizontal direction.',
            spnY: 'Shift logo this many pixels in the vertical direction.',
        }

        # Populate
        for item, tipString in list(tooltips.items()):
            createToolTip(item, tipString)

        def OnChooseFileClicked():
            logoPath = tkinter.filedialog.askopenfilename(
                filetypes=[
                    (
                        'Graphics Interchange Format',
                        ('*.jpg', '*.gif', '*.bmp', '*.png'),
                    )
                ]
            )
            txtPath.delete(0, tkinter.END)
            self.OnSetLogoDefaults['logoPath'] = ''
            if logoPath is not None:
                txtPath.insert(0, logoPath)
                self.OnSetLogoDefaults['logoPath'] = logoPath

            return True

        def OnOkClicked():
            # Update defaults
            self.OnSetLogoDefaults['logoPath'] = txtPath.get()
            self.OnSetLogoDefaults['logoPositioning'] = positioning.get()
            self.OnSetLogoDefaults['logoApplyFx'] = applyFxToLogo.get()
            self.OnSetLogoDefaults['logoResize'] = resizePercent.get()
            self.OnSetLogoDefaults['logoOpacity'] = opacity.get()
            self.OnSetLogoDefaults['logoXoffset'] = xoffset.get()
            self.OnSetLogoDefaults['logoYoffset'] = yoffset.get()
            self.SetLogoDefaults()
            dlg.destroy()

        btnChooseFile.configure(command=OnChooseFileClicked)
        btnOk.configure(command=OnOkClicked)

        return self.WaitForChildDialog(dlg)

    def OnDeleteFrames(self):
        dlg = self.CreateChildDialog('Delete Frames')

        if dlg is None or self.gif is None:
            return False

        numFrames = self.gif.GetNumFrames()
        lblStartFrame = tkinter.Label(dlg, font=self.defaultFont, text='Start Frame')
        sclStartFrame = tkinter.Scale(
            dlg,
            font=self.defaultFontTiny,
            from_=1,
            to=numFrames,
            resolution=1,
            tickinterval=0,
            orient=tkinter.HORIZONTAL,
            sliderlength=20,
            width=15,
            length=275,
            showvalue=True,
        )

        lblEndFrame = tkinter.Label(dlg, font=self.defaultFont, text='End Frame')
        sclEndFrame = tkinter.Scale(
            dlg,
            font=self.defaultFontTiny,
            from_=1,
            to=numFrames,
            resolution=1,
            tickinterval=0,
            orient=tkinter.HORIZONTAL,
            sliderlength=20,
            width=15,
            length=275,
            showvalue=True,
        )
        sclEndFrame.set(numFrames)

        deleteEvenOnly = tkinter.IntVar()
        lblDeleteEvenOnly = tkinter.Label(
            dlg, font=self.defaultFont, text='Delete Even Frames Only'
        )
        chkDeleteEvenOnly = ttk.Checkbutton(dlg, text='', variable=deleteEvenOnly)

        btnDelete = tkinter.Button(dlg, text='Delete', padx=4, pady=4)

        lblStartFrame.grid(row=0, column=0, sticky=tkinter.W, padx=4, pady=4)
        sclStartFrame.grid(row=0, column=1, sticky=tkinter.W, padx=4, pady=4)
        lblEndFrame.grid(row=1, column=0, sticky=tkinter.W, padx=4, pady=4)
        sclEndFrame.grid(row=1, column=1, sticky=tkinter.W, padx=4, pady=4)
        lblDeleteEvenOnly.grid(row=2, column=0, sticky=tkinter.W, padx=4, pady=4)
        chkDeleteEvenOnly.grid(row=2, column=1, sticky=tkinter.W, padx=4, pady=4)
        btnDelete.grid(row=3, column=0, sticky=tkinter.EW, padx=4, pady=4, columnspan=2)

        tooltips = {
            sclStartFrame: 'Start deleting from this frame.',
            sclEndFrame: 'Delete up-to-and-including this frame',
            chkDeleteEvenOnly: 'Delete even numbered frames only. This is handy if you want to thin out your frames to reduce framerate and overall GIF file size. You can perform this over-and-over again in order to keep reducing frame rate.',
        }

        for item, tipString in list(tooltips.items()):
            createToolTip(item, tipString)

        def OnDeleteClicked():
            start = int(sclStartFrame.get())
            end = int(sclEndFrame.get())

            btnDelete.configure(state='disabled')

            if start == 1 and end == numFrames and deleteEvenOnly.get() == 0:
                tkinter.messagebox.showinfo(
                    "You're trying to delete every frame",
                    "You can't delete every frame. Please re-adjust your start and end position",
                )
                dlg.lift()
                return False

            self.DeleteFrame(start, end, deleteEvenOnly.get())
            dlg.destroy()
            return True

        def OnSetFramePosition(newIdx):
            self.SetThumbNailIndex(int(newIdx))
            self.UpdateThumbnailPreview()

            start = int(sclStartFrame.get())
            end = int(sclEndFrame.get())

            if start > end:
                sclStartFrame.set(end)
            return True

        btnDelete.configure(command=OnDeleteClicked)
        sclStartFrame.configure(command=OnSetFramePosition)
        sclEndFrame.configure(command=OnSetFramePosition)

        return self.WaitForChildDialog(dlg)

    def OnExportFrames(self):
        dlg = self.CreateChildDialog('Export Frames')

        if dlg is None or self.gif is None:
            return False

        lblPrefix = tkinter.Label(dlg, font=self.defaultFont, text='Prefix')
        txtPrefix = tkinter.Entry(dlg, font=self.defaultFont, width=10)

        rotationDegs = tkinter.StringVar()
        includeCropAndResize = tkinter.IntVar()
        includeCropAndResize.set(0)
        lblCropResize = tkinter.Label(dlg, font=self.defaultFont, text='Resize & Crop')
        chkCropResize = ttk.Checkbutton(dlg, text='', variable=includeCropAndResize)

        txtPrefix.delete(0, tkinter.END)
        txtPrefix.insert(0, 'img_')

        numFrames = self.gif.GetNumFrames()
        lblStartFrame = tkinter.Label(dlg, font=self.defaultFont, text='Start Frame')
        sclStartFrame = tkinter.Scale(
            dlg,
            font=self.defaultFontTiny,
            from_=1,
            to=numFrames,
            resolution=1,
            tickinterval=0,
            orient=tkinter.HORIZONTAL,
            sliderlength=20,
            width=15,
            length=275,
            showvalue=True,
        )
        lblEndFrame = tkinter.Label(dlg, font=self.defaultFont, text='End Frame')
        sclEndFrame = tkinter.Scale(
            dlg,
            font=self.defaultFontTiny,
            from_=1,
            to=numFrames,
            resolution=1,
            tickinterval=0,
            orient=tkinter.HORIZONTAL,
            sliderlength=20,
            width=15,
            length=275,
            showvalue=True,
        )
        sclEndFrame.set(numFrames)
        lblRotation = tkinter.Label(dlg, font=self.defaultFont, text='Rotation')
        spnRotation = tkinter.Spinbox(
            dlg,
            font=self.defaultFont,
            from_=0,
            to=359,
            increment=1,
            width=5,
            textvariable=rotationDegs,
            repeatdelay=300,
            repeatinterval=60,
            wrap=True,
        )

        btnExport = tkinter.Button(dlg, text='Select directory and export frames', padx=4, pady=4)

        rowNum = 0
        lblPrefix.grid(row=rowNum, column=0, sticky=tkinter.W, padx=4, pady=4)
        txtPrefix.grid(row=rowNum, column=1, sticky=tkinter.W, padx=4, pady=4)
        rowNum += 1
        lblRotation.grid(row=rowNum, column=0, sticky=tkinter.W, padx=4, pady=4)
        spnRotation.grid(row=rowNum, column=1, sticky=tkinter.W, padx=4, pady=4)
        rowNum += 1
        lblCropResize.grid(row=rowNum, column=0, sticky=tkinter.W, padx=4, pady=4)
        chkCropResize.grid(row=rowNum, column=1, sticky=tkinter.W, padx=4, pady=4, columnspan=2)
        rowNum += 1
        lblStartFrame.grid(row=rowNum, column=0, sticky=tkinter.W, padx=4, pady=4)
        sclStartFrame.grid(row=rowNum, column=1, sticky=tkinter.W, padx=4, pady=4)
        rowNum += 1
        lblEndFrame.grid(row=rowNum, column=0, sticky=tkinter.W, padx=4, pady=4)
        sclEndFrame.grid(row=rowNum, column=1, sticky=tkinter.W, padx=4, pady=4)
        rowNum += 1
        btnExport.grid(row=rowNum, column=0, sticky=tkinter.EW, padx=4, pady=4, columnspan=2)

        tooltips = {
            txtPrefix: 'All exported frames will start with this pattern. A sequential number will be appended to the end.',
            chkCropResize: 'Apply resize and crop settings to exported frames.',
            sclStartFrame: 'Start exporting from this frame.',
            sclEndFrame: 'Export up-to-and-including this frame',
            spnRotation: 'Rotate frames by this many degrees. 0 means no rotation',
        }

        for item, tipString in list(tooltips.items()):
            createToolTip(item, tipString)

        def OnSetFramePosition(newIdx):
            self.SetThumbNailIndex(int(newIdx))
            self.UpdateThumbnailPreview()

            start = int(sclStartFrame.get())
            end = int(sclEndFrame.get())

            if start > end:
                sclStartFrame.set(end)

            return True

        def OnExportClicked():
            start = int(sclStartFrame.get())
            end = int(sclEndFrame.get())
            prefix = txtPrefix.get()
            rotation = int(rotationDegs.get())
            outputDir = tkinter.filedialog.askdirectory(
                parent=dlg,
                title='Choose directory for exported images',
                mustexist=True,
                initialdir='/',
            )

            btnExport.configure(state='disabled')

            logging.info(
                'Output folder: %s. Write access: %d' % (outputDir, os.access(outputDir, os.W_OK))
            )

            if outputDir == '' or self.gif is None:
                btnExport.configure(state='normal')
                return False

            if includeCropAndResize.get():
                # They want crop settings. Apply cropping and resizing before exporting
                self.ProcessImage(2)

            if self.gif.ExportFrames(
                start, end, prefix, includeCropAndResize.get(), rotation, outputDir
            ):
                self.SetStatus('Frames %d to %d exported to %s' % (start, end, outputDir))
            else:
                # assume that this is the error.
                tkinter.messagebox.showinfo('Export Failed', 'Failed to export frames!')
                btnExport.configure(state='normal')
                return False

            dlg.destroy()
            return True

        # Attach handlers
        btnExport.configure(command=OnExportClicked)
        sclStartFrame.configure(command=OnSetFramePosition)
        sclEndFrame.configure(command=OnSetFramePosition)

        return self.WaitForChildDialog(dlg)

    def OnImportFrames(self):
        dlg = self.CreateChildDialog('Import Frames')

        buttonTitle = ['Browse for images to import', 'Insert blank frames']

        if dlg is None or self.gif is None:
            return False

        # Forward declare
        sclStartFrame = None

        numFrames = self.gif.GetNumFrames()
        lblStartFrame = tkinter.Label(dlg, font=self.defaultFont, text='Insert after Frame')
        sclStartFrame = tkinter.Scale(
            dlg,
            font=self.defaultFontTiny,
            from_=0,
            to=numFrames,
            resolution=1,
            tickinterval=0,
            orient=tkinter.HORIZONTAL,
            sliderlength=20,
            width=15,
            length=275,
            showvalue=True,
        )

        importReversed = tkinter.IntVar()
        lblImportReversed = tkinter.Label(dlg, font=self.defaultFont, text='Reverse frames')
        chkImportReversed = ttk.Checkbutton(dlg, text='', variable=importReversed)

        riffleShuffle = tkinter.IntVar()
        lblRiffleShuffle = tkinter.Label(dlg, font=self.defaultFont, text='Riffle shuffle')
        chkRiffleShuffle = ttk.Checkbutton(dlg, text='', variable=riffleShuffle)

        stretch = tkinter.IntVar()
        lblStretch = tkinter.Label(dlg, font=self.defaultFont, text='Stretch-to-Fit')
        chkStretch = ttk.Checkbutton(dlg, text='', variable=stretch)

        blankFrame = tkinter.IntVar()
        lblBlankFrame = tkinter.Label(dlg, font=self.defaultFont, text='Insert Blank Frames')
        chkBlankFrame = ttk.Checkbutton(dlg, text='', variable=blankFrame)

        numBlanks = tkinter.IntVar()
        spnBlanks = tkinter.Spinbox(
            dlg,
            font=self.defaultFont,
            from_=1,
            to=100,
            increment=1,
            width=5,
            textvariable=numBlanks,
            repeatdelay=300,
            repeatinterval=30,
            state='readonly',
        )
        numBlanks.set(1)

        btnImport = tkinter.Button(dlg, text=buttonTitle[blankFrame.get()], padx=4, pady=4)

        # Place elements on grid
        rowIdx = -1

        rowIdx += 1
        lblStartFrame.grid(row=rowIdx, column=0, sticky=tkinter.W, padx=4, pady=4)
        sclStartFrame.grid(row=rowIdx, column=1, sticky=tkinter.W, padx=4, pady=4, columnspan=2)

        rowIdx += 1
        lblImportReversed.grid(row=rowIdx, column=0, sticky=tkinter.W, padx=4, pady=4)
        chkImportReversed.grid(row=rowIdx, column=1, sticky=tkinter.W, padx=4, pady=4, columnspan=2)

        rowIdx += 1
        lblRiffleShuffle.grid(row=rowIdx, column=0, sticky=tkinter.W, padx=4, pady=4)
        chkRiffleShuffle.grid(row=rowIdx, column=1, sticky=tkinter.W, padx=4, pady=4, columnspan=2)

        rowIdx += 1
        lblStretch.grid(row=rowIdx, column=0, sticky=tkinter.W, padx=4, pady=4)
        chkStretch.grid(row=rowIdx, column=1, sticky=tkinter.W, padx=4, pady=4, columnspan=2)

        rowIdx += 1
        lblBlankFrame.grid(row=rowIdx, column=0, sticky=tkinter.W, padx=4, pady=4)
        chkBlankFrame.grid(row=rowIdx, column=1, sticky=tkinter.W, padx=4, pady=4)
        spnBlanks.grid(row=rowIdx, column=2, sticky=tkinter.W, padx=4, pady=4)

        rowIdx += 1
        btnImport.grid(row=rowIdx, column=0, sticky=tkinter.EW, padx=4, pady=4, columnspan=3)

        tooltips = {
            sclStartFrame: 'Set the position in your GIF where you want the frames to be imported. Importing at frame 0 will import frames before the first frame.',
            chkImportReversed: 'Import frames in reverse order. Handy for making bouncing loops (a->b->a). Also known as patrol loops, forward-reverse loops, boomerangs or symmetric loops.',
            chkRiffleShuffle: 'Interleave imported frames with existing frames.',
            chkStretch: "Stretch to fit. Otherwise maintain aspect ratio. You may end up with black bars if your images don't have similar sizes.",
            chkBlankFrame: 'Import blank (black) frames.',
            spnBlanks: 'Number of blank frames to insert.',
            btnImport: 'Once you click this button, a dialog will appear where you can multi-select files to be imported multiple files. Note: They will be resized to match your current GIF dimensions.',
        }

        for item, tipString in list(tooltips.items()):
            createToolTip(item, tipString)

        #
        # Handlers
        #

        def OnSetFramePosition(newIdx):
            newIdx = int(newIdx)
            numFrames = self.gif.GetNumFrames()

            if newIdx == 0:
                newIdx = 1
            elif newIdx > numFrames:
                newIdx = numFrames

            self.SetThumbNailIndex(newIdx)
            self.UpdateThumbnailPreview()
            return True

        def OnToggleBlankFrame(*args):
            btnImport.configure(text=buttonTitle[blankFrame.get()])

        def OnImportClicked():
            start = int(sclStartFrame.get())

            if start == 0:
                insertAfter = False
                start = 1
            else:
                insertAfter = True

            reverseImport = importReversed.get()
            riffle = riffleShuffle.get()

            if blankFrame.get():
                imgList = ['<black>'] * numBlanks.get()
            else:
                filesStr = tkinter.filedialog.askopenfilenames(
                    parent=dlg,
                    title='Choose images to import',
                    filetypes=[('Image Files', ('*.jpg', '*.gif', '*.bmp', '*.png'))],
                )
                imgList = list(self.parent.tk.splitlist(filesStr))

            self.SetStatus('Import %d images' % (len(imgList)))

            if len(imgList) <= 0:
                return False

            # Disable import button
            btnImport.configure(state='disabled')

            self.miscGifChanges += 1
            if self.gif is not None and self.gif.ImportFrames(
                start, imgList, reverseImport, insertAfter, riffle, stretch.get() == 0
            ):
                self.SetStatus('Imported images starting at frame %d' % (start))

            self.UpdateThumbnailPreview()  # We have new frames

            dlg.destroy()
            return True

        # Attach handlers
        btnImport.configure(command=OnImportClicked)
        sclStartFrame.configure(command=OnSetFramePosition)
        blankFrame.trace_add('write', OnToggleBlankFrame)

        return self.WaitForChildDialog(dlg)

    # Manual Size and Crop
    def OnManualSizeAndCrop(self):
        dlg = self.CreateChildDialog('Crop Settings')

        if dlg is None:
            return False

        try:
            sx, sy, sw, sh, smaxw, smaxh, sratio = self.GetCropSettingsFromCanvas(True)
        except Exception:
            return False

        cropX = tkinter.StringVar()
        cropY = tkinter.StringVar()
        cropWidth = tkinter.StringVar()
        cropHeight = tkinter.StringVar()
        aspectLock = tkinter.IntVar()

        cropX.set(sx)
        cropY.set(sy)
        cropWidth.set(sw)
        cropHeight.set(sh)

        lblStartX = tkinter.Label(dlg, font=self.defaultFont, text='Horizontal Start Position')
        lblStartY = tkinter.Label(dlg, font=self.defaultFont, text='Vertical Start Position')
        lblAspectLock = tkinter.Label(dlg, font=self.defaultFont, text='Maintain crop aspect ratio')
        lblWidth = tkinter.Label(dlg, font=self.defaultFont, text='GIF Width')
        lblHeight = tkinter.Label(dlg, font=self.defaultFont, text='GIF Height')
        lblAspectLock = tkinter.Label(dlg, font=self.defaultFont, text='Maintain crop aspect ratio')

        spnX = tkinter.Spinbox(
            dlg,
            font=self.defaultFont,
            from_=0,
            to=smaxw - 1,
            increment=1,
            width=5,
            textvariable=cropX,
            repeatdelay=300,
            repeatinterval=14,
            state='readonly',
        )
        spnY = tkinter.Spinbox(
            dlg,
            font=self.defaultFont,
            from_=0,
            to=smaxh - 1,
            increment=1,
            width=5,
            textvariable=cropY,
            repeatdelay=300,
            repeatinterval=14,
            state='readonly',
        )
        spnWidth = tkinter.Spinbox(
            dlg,
            font=self.defaultFont,
            from_=1,
            to=smaxw,
            increment=1,
            width=5,
            textvariable=cropWidth,
            repeatdelay=300,
            repeatinterval=14,
            state='readonly',
        )
        spnHeight = tkinter.Spinbox(
            dlg,
            font=self.defaultFont,
            from_=1,
            to=smaxh,
            increment=1,
            width=5,
            textvariable=cropHeight,
            repeatdelay=300,
            repeatinterval=14,
            state='readonly',
        )
        chkAspectLock = tkinter.Checkbutton(dlg, text='', variable=aspectLock)
        btnOK = tkinter.Button(dlg, text='Done', padx=4, pady=4)

        # Place elements on grid

        rowIdx = -1

        # rowIdx += 1
        # lblAspectLock.grid  (row=rowIdx, column=0, sticky=tkinter.W,  padx=4, pady=4)
        # chkAspectLock.grid  (row=rowIdx, column=1, sticky=tkinter.W,  padx=4, pady=4)

        rowIdx += 1
        lblStartX.grid(row=rowIdx, column=0, sticky=tkinter.W, padx=4, pady=4)
        spnX.grid(row=rowIdx, column=1, sticky=tkinter.W, padx=4, pady=4)

        rowIdx += 1
        lblStartY.grid(row=rowIdx, column=0, sticky=tkinter.W, padx=4, pady=4)
        spnY.grid(row=rowIdx, column=1, sticky=tkinter.W, padx=4, pady=4, columnspan=2)

        rowIdx += 1
        lblWidth.grid(row=rowIdx, column=0, sticky=tkinter.W, padx=4, pady=4)
        spnWidth.grid(row=rowIdx, column=1, sticky=tkinter.W, padx=4, pady=4)

        rowIdx += 1
        lblHeight.grid(row=rowIdx, column=0, sticky=tkinter.W, padx=4, pady=4)
        spnHeight.grid(row=rowIdx, column=1, sticky=tkinter.W, padx=4, pady=4, columnspan=2)

        rowIdx += 1
        btnOK.grid(row=rowIdx, column=0, sticky=tkinter.EW, padx=4, pady=4, columnspan=2)

        tooltips = {
            chkAspectLock: 'Maintain aspect ratio when adjusting width and height',
            spnWidth: 'Gif width in pixels',
            spnHeight: 'Gif height in pixels',
            spnX: 'Start coordinate X (horizontal)',
            spnY: 'Start coordinate Y (vertical)',
            btnOK: 'All done!',
        }

        for item, tipString in list(tooltips.items()):
            createToolTip(item, tipString)

        #
        # Handlers
        #

        def OnOK():
            self.UpdateThumbnailPreview()  # We have new frames
            dlg.destroy()
            return True

        def OnCropChange(*args):
            try:
                sx, sy, sw, sh, smaxw, smaxh, sratio = self.GetCropSettingsFromCanvas(True, False)
            except Exception:
                logging.error('Failed to get cropper settings from canvas')
                return False

            # Video port coords
            (px, py, px2, py2) = self.canCropTool.coords('videoScale')

            # Boundary checks
            iw = float(cropWidth.get())
            ih = float(cropHeight.get())
            ix = float(cropX.get())
            iy = float(cropY.get())

            if iw < self.cropSizerSize:
                iw = self.cropSizerSize

            if ih < self.cropSizerSize:
                ih = self.cropSizerSize

            if ix + iw > smaxw:
                iw = smaxw - sx

            if iy + ih > smaxh:
                ih = smaxh - sy

            # Update GUI elements
            cropWidth.set(str(iw))
            cropHeight.set(str(ih))

            # Update cropper GUI

            # Translate coordinates to canvas port
            nx = px + self.TranslateToCanvas(ix)
            ny = py + self.TranslateToCanvas(iy)
            nx2 = px + self.TranslateToCanvas(ix + iw)
            ny2 = py + self.TranslateToCanvas(iy + ih)

            self.canCropTool.coords('cropRect', nx, ny, nx2, ny2)
            self.OnCropUpdate()

            return True

        # Attach handlers
        btnOK.configure(command=OnOK)

        cropX.trace_add('write', OnCropChange)
        cropY.trace_add('write', OnCropChange)
        cropWidth.trace_add('write', OnCropChange)
        cropHeight.trace_add('write', OnCropChange)
        aspectLock.trace_add('write', OnCropChange)

        OnCropChange(None)
        return self.WaitForChildDialog(dlg)

    # Bouncing Loop
    def OnForwardReverseLoop(self):
        if self.gif is None or self.gif.GetNumFrames() <= 2:
            self.Alert('Unable to Make Loop', "You don't have enough frames to do this")
            return False

        self.gif.ReEnumerateExtractedFrames()
        if self.gif.ImportFrames(
            self.gif.GetNumFrames(),
            self.gif.GetExtractedImageList()[1:-1],
            True,
            True,
            False,
            False,
        ):
            self.SetStatus('Generated forward-reverse loop')
            self.UpdateThumbnailPreview()  # We have new frames
        else:
            self.Alert(
                'Unable to Make Loop',
                "Something weird happened. I couldn't loop this thing :(",
            )

        return True

    def OnReverseFrames(self):
        if self.gif is None or self.gif.GetNumFrames() < 2:
            return False

        if self.gif.ReverseFrames():
            self.miscGifChanges += 1
            self.SetStatus('Reversed frames')
            self.UpdateThumbnailPreview()  # We have new frames
            return True
        else:
            self.Alert(
                'Unable to reverse frames',
                "Something weird happened. I couldn't reverse this thing :(",
            )

    def OnCrossFade(self):
        if self.gif is None or self.gif.GetNumFrames() <= 2:
            return False

        dlg = self.CreateChildDialog('Cross Fader')

        if dlg is None:
            return False

        numFrames = self.gif.GetNumFrames()
        lblStartFrame = tkinter.Label(dlg, font=self.defaultFont, text='Start Frame')
        sclStartFrame = tkinter.Scale(
            dlg,
            font=self.defaultFontTiny,
            from_=1,
            to=numFrames,
            resolution=1,
            tickinterval=0,
            orient=tkinter.HORIZONTAL,
            sliderlength=20,
            width=15,
            length=275,
            showvalue=True,
        )
        lblEndFrame = tkinter.Label(dlg, font=self.defaultFont, text='End Frame')
        sclEndFrame = tkinter.Scale(
            dlg,
            font=self.defaultFontTiny,
            from_=1,
            to=numFrames,
            resolution=1,
            tickinterval=0,
            orient=tkinter.HORIZONTAL,
            sliderlength=20,
            width=15,
            length=275,
            showvalue=True,
        )
        sclEndFrame.set(numFrames)
        btnCreateFade = tkinter.Button(dlg, text='Generate Crossfade', padx=4, pady=4)

        lblStartFrame.grid(row=0, column=0, sticky=tkinter.W, padx=4, pady=4)
        sclStartFrame.grid(row=0, column=1, sticky=tkinter.W, padx=4, pady=4)
        lblEndFrame.grid(row=1, column=0, sticky=tkinter.W, padx=4, pady=4)
        sclEndFrame.grid(row=1, column=1, sticky=tkinter.W, padx=4, pady=4)
        btnCreateFade.grid(row=2, column=0, sticky=tkinter.EW, padx=4, pady=4, columnspan=2)

        tooltips = {
            sclStartFrame: 'Frame to start crossfade. If greater than end frame, crossfade will loop around. To make cross fade effective, make sure you include an equal number of frames on either side of the transition point.',
            sclEndFrame: 'Frame to end crossfade',
        }

        for item, tipString in list(tooltips.items()):
            createToolTip(item, tipString)

        def OnCreateFadeClicked():
            start = int(sclStartFrame.get())
            end = int(sclEndFrame.get())

            btnCreateFade.configure(state='disabled')

            if self.gif is not None and not self.gif.CreateCrossFade(start, end):
                self.Alert('Cross-fade Error', 'Make sure your range spans at least 3 frames')
                btnCreateFade.configure(state='normal')
                return False

            self.SetStatus('Added crossfade')
            self.UpdateThumbnailPreview()  # We have new frames

            dlg.destroy()
            return True

        def OnSetFramePosition(newIdx):
            self.SetThumbNailIndex(int(newIdx))
            self.UpdateThumbnailPreview()
            # start = int(sclStartFrame.get())
            # end = int(sclEndFrame.get())
            return True

        btnCreateFade.configure(command=OnCreateFadeClicked)
        sclStartFrame.configure(command=OnSetFramePosition)
        sclEndFrame.configure(command=OnSetFramePosition)

        return self.WaitForChildDialog(dlg)

    #
    def OnEditAudioSettings(self, parentDlg):
        if self.gif is None:
            return False

        dlg = self.CreateChildDialog('Configure Audio', parent=parentDlg)

        if dlg is None:
            return False

        audioChanged = True
        lblPath = tkinter.Label(dlg, font=self.defaultFont, text='Audio path')
        txtPath = tkinter.Entry(dlg, font=self.defaultFont, width=33)
        btnChooseFile = tkinter.Button(
            dlg, text='Load Audio', padx=4, pady=4, width=12, font=self.defaultFontTiny
        )

        lblStart = tkinter.Label(dlg, font=self.defaultFont, text='Start time')

        audioStartTimeMin = tkinter.StringVar()
        audioStartTimeSec = tkinter.StringVar()
        audioStartTimeMilli = tkinter.StringVar()

        spnAudioStartTimeMin = tkinter.Spinbox(
            dlg,
            font=self.defaultFont,
            from_=0,
            to=59,
            values=TIME_VALUES_60,
            increment=1,
            width=self.guiConf['timeSpinboxWidth'],
            textvariable=audioStartTimeMin,
            validate=tkinter.ALL,
            wrap=True,
            name='audioStartMin',
            repeatdelay=250,
            repeatinterval=35,
            state='readonly',
        )
        lblAudioMinSep = tkinter.Label(dlg, text=':')
        spnAudioStartTimeSec = tkinter.Spinbox(
            dlg,
            font=self.defaultFont,
            from_=0,
            to=59,
            values=TIME_VALUES_60,
            increment=1,
            width=self.guiConf['timeSpinboxWidth'],
            textvariable=audioStartTimeSec,
            validate=tkinter.ALL,
            wrap=True,
            name='audioStartSec',
            repeatdelay=250,
            repeatinterval=35,
            state='readonly',
        )
        lblAudioSecSep = tkinter.Label(dlg, text='.')
        spnAudioStartTimeMilli = tkinter.Spinbox(
            dlg,
            font=self.defaultFont,
            from_=0,
            to=9,
            values=TIME_VALUES_10,
            increment=1,
            width=self.guiConf['timeSpinboxWidth'],
            textvariable=audioStartTimeMilli,
            validate=tkinter.ALL,
            wrap=True,
            name='audioStartMilli',
            repeatdelay=200,
            repeatinterval=100,
            state='readonly',
        )

        lblVolume = tkinter.Label(dlg, font=self.defaultFont, text='Volume %')
        volume = tkinter.IntVar()
        spnVolume = tkinter.Spinbox(
            dlg,
            font=self.defaultFont,
            from_=0,
            to=300,
            increment=1,
            width=5,
            textvariable=volume,
            repeatdelay=300,
            repeatinterval=15,
            state='readonly',
        )

        btnPreview = tkinter.Button(
            dlg, text='Play', padx=4, pady=4, width=12, font=self.defaultFontTiny
        )
        btnOk = tkinter.Button(dlg, text='OK', padx=4, pady=4)

        # # Load default values
        txtPath.insert(0, self.conf.GetParam('audio', 'path'))
        secs = float(self.conf.GetParam('audio', 'startTime'))
        h, m, s, ms = igf_common.milliseconds_to_duration_components(secs * 1000.0)
        audioStartTimeMin.set('%02d' % m)
        audioStartTimeSec.set('%02d' % s)
        audioStartTimeMilli.set(ms / 100)
        volume.set(int(self.conf.GetParam('audio', 'volume')))

        btnPreview.configure(state='disabled')

        # Place elements on grid
        rowIdx = -1

        rowIdx += 1
        lblPath.grid(row=rowIdx, column=0, sticky=tkinter.W, padx=4, pady=4)
        txtPath.grid(row=rowIdx, column=1, sticky=tkinter.W, padx=4, pady=4, columnspan=5)
        btnChooseFile.grid(row=rowIdx, column=6, sticky=tkinter.EW, padx=4, pady=4)

        rowIdx += 1
        lblStart.grid(row=rowIdx, column=0, sticky=tkinter.W, padx=4, pady=4)

        spnAudioStartTimeMin.grid(row=rowIdx, column=1, sticky=tkinter.W, padx=4, pady=4)
        lblAudioMinSep.grid(row=rowIdx, column=2, sticky=tkinter.W, padx=4, pady=4)
        spnAudioStartTimeSec.grid(row=rowIdx, column=3, sticky=tkinter.W, padx=4, pady=4)
        lblAudioSecSep.grid(row=rowIdx, column=4, sticky=tkinter.W, padx=4, pady=4)
        spnAudioStartTimeMilli.grid(row=rowIdx, column=5, sticky=tkinter.W, padx=4, pady=4)
        btnPreview.grid(row=rowIdx, column=6, sticky=tkinter.EW, padx=4, pady=4)

        rowIdx += 1
        lblVolume.grid(row=rowIdx, column=0, sticky=tkinter.W, padx=4, pady=4)
        spnVolume.grid(row=rowIdx, column=1, sticky=tkinter.W, padx=4, pady=4, columnspan=2)

        rowIdx += 1
        btnOk.grid(row=rowIdx, column=0, sticky=tkinter.EW, padx=4, pady=4, columnspan=7)

        tooltips = {
            btnChooseFile: 'Click to start download of a URL, or to browse for a local file',
            txtPath: 'Hint: video URLs are supported in addition to local files',
        }

        for item, tipString in list(tooltips.items()):
            createToolTip(item, tipString)

        def OnTimeChanged():
            # audioChanged = True
            return True

        def OnChooseFileClicked():
            # audioChanged = True

            if igf_paths.is_url(txtPath.get()) and self.gif is not None:
                try:
                    downloadedFileName = self.gif.DownloadAudio(txtPath.get())
                except Exception:
                    self.Alert('Audio Download', 'Failed to download audio file')
                    return False

                txtPath.delete(0, 'end')
                txtPath.insert(0, downloadedFileName)
            else:
                # They're selecting a file
                audioPath = tkinter.filedialog.askopenfilename(
                    parent=dlg,
                    filetypes=[('Audio file', ('*.mp3', '*.aac', '*.wav', '*.ogg', '*.m4a'))],
                )
                txtPath.delete(0, tkinter.END)
                if audioPath is not None:
                    txtPath.insert(0, audioPath)
                PrepareAudio()

            return True

        def AudioFileExists():
            return os.path.exists(txtPath.get())

        def GetStartTime():
            timeStart = (
                float(spnAudioStartTimeMin.get()) * 60
                + float(spnAudioStartTimeSec.get())
                + float(spnAudioStartTimeMilli.get()) / 10.0
            )
            return timeStart

        def PrepareAudio():
            if len(txtPath.get()) == 0:
                return False

            if AudioFileExists():
                self.audioChanges += self.conf.SetParam('audio', 'path', txtPath.get())
                self.audioChanges += self.conf.SetParam('audio', 'startTime', str(GetStartTime()))
                self.audioChanges += self.conf.SetParam('audio', 'volume', str(volume.get()))

                # audioChanged = False
                btnPreview.configure(state='normal')
                return True
            else:
                self.Alert('Audio', "Can't find the audio file")
                return False

        def OnPlay():
            if self.gif is None:
                return
            if audioChanged:
                if not PrepareAudio() or not self.gif.ExtractAudioClip():
                    self.Alert('Audio', 'Failed to extract clip from audio file')
                    return False

            audio_play(self.gif.GetAudioClipPath())
            self.parent.update_idletasks()

        def OnOkClicked():
            if self.gif is None:
                return
            closeDialog = False

            if len(txtPath.get()) == 0:
                closeDialog = True
            elif PrepareAudio():
                # Do it automatically
                if self.gif.GetFinalOutputFormat() == igf_paths.EXT_GIF:
                    self.ChangeFileFormat('mp4')

                closeDialog = True
            else:
                self.Alert('Audio Problems', 'Something is wrong with your audio settings')

            # Should we close the audio dialog?
            if closeDialog:
                audio_play(None)
                dlg.destroy()

        btnChooseFile.configure(command=OnChooseFileClicked)
        btnOk.configure(command=OnOkClicked)
        btnPreview.configure(command=OnPlay)
        spnAudioStartTimeMin.configure(command=OnTimeChanged)
        spnAudioStartTimeSec.configure(command=OnTimeChanged)
        spnAudioStartTimeMilli.configure(command=OnTimeChanged)

        PrepareAudio()
        return self.WaitForChildDialog(dlg)

    # Edit mask
    def OnEditMask(self, parentDlg):
        if self.gif is None:
            return False

        dlg = self.CreateChildDialog('Edit Mask', parent=parentDlg)

        if dlg is None:
            return False

        maxX = self.parent.winfo_screenwidth() - 100
        maxY = self.parent.winfo_screenheight() - 250

        scaleFactor = 1.0

        if maxX < self.gif.GetVideoWidth() or maxY < self.gif.GetVideoHeight():
            scaleFactor = min(
                float(maxX) / float(self.gif.GetVideoWidth()),
                float(maxY) / float(self.gif.GetVideoHeight()),
            )

        brushSize = tkinter.IntVar()
        maxSize = max(self.gif.GetVideoWidth(), self.gif.GetVideoHeight())
        defBrushSize = re_scale(maxSize, (1, 1920), (1, 100))
        blurRadius = re_scale(maxSize, (200, 1920), (3, 15))
        brushSize.set(defBrushSize)

        # Create elements
        canvasContainer = tkinter.LabelFrame(dlg, text=' Paint the area you want to unfreeze ')
        paintCanvas = tkinter.Canvas(
            canvasContainer,
            width=int(self.gif.GetVideoWidth() * scaleFactor),
            height=int(self.gif.GetVideoHeight() * scaleFactor),
            background='black',
            borderwidth=0,
            highlightthickness=0,
        )
        btnOK = tkinter.Button(dlg, text='Done', padx=4, pady=4)
        btnReset = tkinter.Button(dlg, text='Reset', width=5, padx=4, pady=4)
        btnUndo = tkinter.Button(dlg, text='Undo', width=5, padx=4, pady=4)

        lblBrushSize = tkinter.Label(dlg, text='Brush Size')
        spnBrushSize = tkinter.Spinbox(
            dlg,
            font=self.defaultFont,
            from_=1,
            to=100,
            increment=1,
            width=5,
            textvariable=brushSize,
            repeatdelay=300,
            repeatinterval=30,
            state='readonly',
        )
        img = PIL.Image.open(self.gif.GetExtractedImageList()[self.GetThumbNailIndex() - 1])
        img = img.resize(
            (
                int(self.gif.GetVideoWidth() * scaleFactor),
                int(self.gif.GetVideoHeight() * scaleFactor),
            ),
            PIL.Image.Resampling.BICUBIC,
        )

        photoImg = PIL.ImageTk.PhotoImage(img)

        # Place elements on grid
        rowIdx = -1

        rowIdx += 1
        lblBrushSize.grid(row=rowIdx, column=0, sticky=tkinter.W, padx=4, pady=4)
        spnBrushSize.grid(row=rowIdx, column=1, sticky=tkinter.W, padx=4, pady=4)
        btnUndo.grid(row=rowIdx, column=2, sticky=tkinter.EW, padx=4, pady=4)
        btnReset.grid(row=rowIdx, column=3, sticky=tkinter.EW, padx=4, pady=4)

        rowIdx += 1
        canvasContainer.grid(row=rowIdx, column=0, columnspan=4, sticky=tkinter.W, padx=4, pady=4)
        paintCanvas.grid(row=0, column=0, sticky=tkinter.W, padx=4, pady=4)

        rowIdx += 1
        btnOK.grid(row=rowIdx, column=0, columnspan=4, sticky=tkinter.EW, padx=4, pady=4)

        tooltips = {
            btnOK: 'All done!',
        }

        for item, tipString in list(tooltips.items()):
            createToolTip(item, tipString)

        def DoDraw(x, y, brushSize, toCanvas=True):
            if toCanvas:
                brushColor = 'red'
                halfpb = brushSize / 2
                x1, y1 = (x - halfpb), (y - halfpb)
                x2, y2 = (x + halfpb), (y + halfpb)
                paintCanvas.create_oval(
                    x1, y1, x2, y2, fill=brushColor, outline=brushColor, tags=['paint']
                )
            else:
                # Scale everything
                invScaleFactor = 1.0 / scaleFactor
                x *= invScaleFactor
                y *= invScaleFactor
                brushSize *= invScaleFactor

                halfpb = brushSize / 2
                x1, y1 = (x - halfpb), (y - halfpb)
                x2, y2 = (x + halfpb), (y + halfpb)
                if self.maskDraw is not None:
                    self.maskDraw.ellipse((x1, y1, x2, y2), outline='#000000', fill='#000000')

        def WriteToCanvas():
            for e in self.maskEventList:
                DoDraw(e[0], e[1], e[2], True)

        #
        # Handlers
        #

        def OnUndo():
            paintCanvas.delete('paint')

            deleteChunk = 20

            if len(self.maskEventList) <= deleteChunk:
                self.maskEventList.clear()
            elif len(self.maskEventList) > deleteChunk:
                for x in range(0, deleteChunk):
                    del self.maskEventList[-1]

            WriteToCanvas()

        def OnPaint(event):
            paintBrushSize = brushSize.get() * 2
            saveEvent = [event.x, event.y, brushSize.get()]
            self.maskEdited = True  # To let GUI know
            self.maskEventList.append(saveEvent)
            DoDraw(saveEvent[0], saveEvent[1], saveEvent[2], True)

        def OnReset(onDialogLoad=False):
            if not onDialogLoad:
                self.maskEventList.clear()
                self.maskEdited = True

            paintCanvas.delete('paint')
            paintCanvas.create_image(0, 0, image=photoImg, tag='frame', anchor=tkinter.NW)

            return True

        def OnOK():
            if self.gif is None:
                return False

            if self.HaveMask():
                maskImage = PIL.Image.new(
                    'RGB',
                    (self.gif.GetVideoWidth(), self.gif.GetVideoHeight()),
                    '#ffffff',
                )
                self.maskDraw = PIL.ImageDraw.Draw(maskImage)

                for e in self.maskEventList:
                    DoDraw(e[0], e[1], e[2], False)

                maskImage = maskImage.filter(PIL.ImageFilter.GaussianBlur(blurRadius))
                maskImage.save(self.gif.GetMaskFileName(0))
            else:
                try:
                    os.remove(self.gif.GetMaskFileName(0))
                except Exception:
                    pass

            dlg.destroy()
            return True

        # Attach handlers
        dlg.protocol('WM_DELETE_WINDOW', OnOK)
        btnOK.configure(command=OnOK)
        btnReset.configure(command=OnReset)
        btnUndo.configure(command=OnUndo)
        paintCanvas.bind('<B1-Motion>', OnPaint)
        paintCanvas.bind('<Button-1>', OnPaint)

        OnReset(True)
        WriteToCanvas()

        return self.WaitForChildDialog(dlg, 'center')

    #
    #
    # Effects Configuration
    #
    #
    def OnEffectsChange(self, *args):
        # Add new fx to this list
        allFx = [
            self.isGrayScale,
            self.isSharpened,
            self.isDesaturated,
            self.isSepia,
            self.isColorTint,
            self.isFadedEdges,
            self.desaturatedAmount,
            self.sepiaAmount,
            self.sharpenedAmount,
            self.fadedEdgeAmount,
            self.colorTintAmount,
            self.colorTintColor,
            self.isBordered,
            self.borderAmount,
            self.borderColor,
            self.nashvilleAmount,
            self.isNashville,
            self.isBlurred,
            self.blurredAmount,
            self.isCinemagraph,
            self.invertCinemagraph,
            self.isAudioEnabled,
        ]

        newFxHash = ''
        for param in allFx:
            newFxHash += str(param.get())
            # if args[0] == str(param):
            #     logging.info("Effect Change. New value: " + str(param.get()) )

        if not self.guiBusy and (newFxHash != self.fxHash or self.maskEdited):
            self.fxHash = newFxHash

            if self.conf.GetParamBool('settings', 'autoPreview'):
                self.OnShowPreview(None)
                self.parent.update_idletasks()

    def HaveMask(self):
        return bool(self.maskEventList)

    def HaveAudioPath(self):
        audiofile = self.conf.GetParam('audio', 'path')
        return len(audiofile) and os.path.exists(audiofile)

    def OnEditEffects(self):
        self.fxHash = ''

        dlg = self.CreateChildDialog('Filters')

        if dlg is None:
            return False

        lblHeadingCol1 = tkinter.Label(dlg, text='Name')
        lblHeadingCol2 = tkinter.Label(dlg, text='Value')
        lblHeadingCol3 = tkinter.Label(dlg, text='Customize')

        if self.HaveMask():
            cinemagraphState = 'normal'
        else:
            cinemagraphState = 'disabled'

        if self.HaveAudioPath():
            audioState = 'normal'
        else:
            audioState = 'disabled'

        chkGrayScale = ttk.Checkbutton(dlg, text='Black & White', variable=self.isGrayScale)
        chkSharpen = ttk.Checkbutton(dlg, text='Enhance', variable=self.isSharpened)
        chkDesaturate = ttk.Checkbutton(dlg, text='Color Fade', variable=self.isDesaturated)
        chkSepia = ttk.Checkbutton(dlg, text='Sepia Tone', variable=self.isSepia)
        chkColorTint = ttk.Checkbutton(dlg, text='Colorize', variable=self.isColorTint)
        chkEdgeFade = ttk.Checkbutton(dlg, text='Burnt Corners', variable=self.isFadedEdges)
        chkBorder = ttk.Checkbutton(dlg, text='Border', variable=self.isBordered)
        chkBlurred = ttk.Checkbutton(dlg, text='Blur', variable=self.isBlurred)
        chkNashville = ttk.Checkbutton(dlg, text='Nashville', variable=self.isNashville)
        chkCinemagraph = ttk.Checkbutton(
            dlg, text='Cinemagraph', variable=self.isCinemagraph, state=cinemagraphState
        )
        chkCinemaInvert = ttk.Checkbutton(
            dlg, text='Invert', variable=self.invertCinemagraph, state=cinemagraphState
        )
        chkSound = ttk.Checkbutton(
            dlg, text='Sound', variable=self.isAudioEnabled, state=audioState
        )

        btnEditSound = tkinter.Button(dlg, font=self.defaultFont, text='Configure...')
        btnEditMask = tkinter.Button(dlg, font=self.defaultFont, text='Configure...')

        btnOK = tkinter.Button(dlg, text='Done')

        btnTintColor = tkinter.Button(dlg, font=self.defaultFont, text='Color Picker')
        btnBorderColor = tkinter.Button(dlg, font=self.defaultFont, text='Color Picker')

        def OnSpin():
            # prevent events from queuing up
            dlg.update_idletasks()

        repeatRateMs = 1500
        spnDesaturateAmount = tkinter.Spinbox(
            dlg,
            font=self.defaultFont,
            from_=0,
            to=100,
            increment=10,
            width=5,
            textvariable=self.desaturatedAmount,
            state='readonly',
            wrap=True,
            repeatdelay=300,
            repeatinterval=repeatRateMs,
            command=OnSpin,
        )
        spnSepiaAmount = tkinter.Spinbox(
            dlg,
            font=self.defaultFont,
            from_=0,
            to=100,
            increment=10,
            width=5,
            textvariable=self.sepiaAmount,
            state='readonly',
            wrap=True,
            repeatdelay=300,
            repeatinterval=repeatRateMs,
            command=OnSpin,
        )
        spnSharpenAmount = tkinter.Spinbox(
            dlg,
            font=self.defaultFont,
            from_=0,
            to=100,
            increment=10,
            width=5,
            textvariable=self.sharpenedAmount,
            state='readonly',
            wrap=True,
            repeatdelay=300,
            repeatinterval=repeatRateMs,
            command=OnSpin,
        )
        spnFadedEdgeAmount = tkinter.Spinbox(
            dlg,
            font=self.defaultFont,
            from_=0,
            to=100,
            increment=10,
            width=5,
            textvariable=self.fadedEdgeAmount,
            state='readonly',
            wrap=True,
            repeatdelay=300,
            repeatinterval=repeatRateMs,
            command=OnSpin,
        )
        spnColorTintAmount = tkinter.Spinbox(
            dlg,
            font=self.defaultFont,
            from_=0,
            to=100,
            increment=10,
            width=5,
            textvariable=self.colorTintAmount,
            state='readonly',
            wrap=True,
            repeatdelay=300,
            repeatinterval=repeatRateMs,
            command=OnSpin,
        )
        spnBorderAmount = tkinter.Spinbox(
            dlg,
            font=self.defaultFont,
            from_=0,
            to=100,
            increment=10,
            width=5,
            textvariable=self.borderAmount,
            state='readonly',
            wrap=True,
            repeatdelay=300,
            repeatinterval=repeatRateMs,
            command=OnSpin,
        )
        spnNashvilleAmount = tkinter.Spinbox(
            dlg,
            font=self.defaultFont,
            from_=0,
            to=100,
            increment=10,
            width=5,
            textvariable=self.nashvilleAmount,
            state='readonly',
            wrap=True,
            repeatdelay=300,
            repeatinterval=repeatRateMs,
            command=OnSpin,
        )
        spnBlurAmount = tkinter.Spinbox(
            dlg,
            font=self.defaultFont,
            from_=0,
            to=100,
            increment=10,
            width=5,
            textvariable=self.blurredAmount,
            state='readonly',
            wrap=True,
            repeatdelay=300,
            repeatinterval=repeatRateMs,
            command=OnSpin,
        )
        rowIdx = -1

        rowIdx += 1
        lblHeadingCol1.grid(row=rowIdx, column=0, sticky=tkinter.W, padx=4, pady=1)
        lblHeadingCol2.grid(row=rowIdx, column=1, sticky=tkinter.W, padx=4, pady=1)
        lblHeadingCol3.grid(row=rowIdx, column=2, sticky=tkinter.W, padx=4, pady=1)

        rowIdx += 1
        chkSharpen.grid(row=rowIdx, column=0, sticky=tkinter.W, padx=4, pady=1)
        spnSharpenAmount.grid(row=rowIdx, column=1, sticky=tkinter.W, padx=4, pady=1)

        rowIdx += 1
        chkDesaturate.grid(row=rowIdx, column=0, sticky=tkinter.W, padx=4, pady=1)
        spnDesaturateAmount.grid(row=rowIdx, column=1, sticky=tkinter.W, padx=4, pady=1)

        rowIdx += 1
        chkSepia.grid(row=rowIdx, column=0, sticky=tkinter.W, padx=4, pady=1)
        spnSepiaAmount.grid(row=rowIdx, column=1, sticky=tkinter.W, padx=4, pady=1)

        rowIdx += 1
        chkEdgeFade.grid(row=rowIdx, column=0, sticky=tkinter.W, padx=4, pady=1)
        spnFadedEdgeAmount.grid(row=rowIdx, column=1, sticky=tkinter.W, padx=4, pady=1)

        rowIdx += 1
        chkNashville.grid(row=rowIdx, column=0, sticky=tkinter.W, padx=4, pady=1)
        spnNashvilleAmount.grid(row=rowIdx, column=1, sticky=tkinter.W, padx=4, pady=1)

        rowIdx += 1
        chkColorTint.grid(row=rowIdx, column=0, sticky=tkinter.W, padx=4, pady=1)
        spnColorTintAmount.grid(row=rowIdx, column=1, sticky=tkinter.W, padx=4, pady=1)
        btnTintColor.grid(row=rowIdx, column=2, sticky=tkinter.W, padx=4, pady=1)

        rowIdx += 1
        chkBlurred.grid(row=rowIdx, column=0, sticky=tkinter.W, padx=4, pady=1)
        spnBlurAmount.grid(row=rowIdx, column=1, sticky=tkinter.W, padx=4, pady=1)

        rowIdx += 1
        chkBorder.grid(row=rowIdx, column=0, sticky=tkinter.W, padx=4, pady=1)
        spnBorderAmount.grid(row=rowIdx, column=1, sticky=tkinter.W, padx=4, pady=1)
        btnBorderColor.grid(row=rowIdx, column=2, sticky=tkinter.W, padx=4, pady=1)

        rowIdx += 1
        chkGrayScale.grid(row=rowIdx, column=0, sticky=tkinter.W, padx=4, pady=1)

        rowIdx += 1
        chkCinemagraph.grid(row=rowIdx, column=0, sticky=tkinter.W, padx=4, pady=1)
        btnEditMask.grid(row=rowIdx, column=2, sticky=tkinter.W, padx=4, pady=1)
        chkCinemaInvert.grid(row=rowIdx, column=3, sticky=tkinter.W, padx=4, pady=1)

        rowIdx += 1
        chkSound.grid(row=rowIdx, column=0, sticky=tkinter.W, padx=4, pady=1)
        btnEditSound.grid(row=rowIdx, column=2, sticky=tkinter.W, padx=4, pady=1)

        rowIdx += 1
        btnOK.grid(row=rowIdx, column=0, sticky=tkinter.EW, padx=4, pady=4, columnspan=4)

        tooltips = {
            chkGrayScale: 'More specifically grayscale. Converting your GIF to grayscale will reduce file size. This is the last filter applied in the chain.',
            chkSharpen: 'Sharpen edges. If left unchecked, GIFs will have a slightly washed out look, which is sometimes desirable.',
            chkDesaturate: 'Tumblr will sometimes reject GIFs that are too rich in color. Use this to make your GIF less colorful.',
            chkSepia: "Sepia tone. Make your GIF look like an early 1900's photo. It's the bee's knees!",
            chkColorTint: 'Add a color tint to your GIF',
            chkEdgeFade: 'Make the edges of your GIF look burnt.',
            chkBorder: 'Add a simple colored border.',
            chkNashville: 'Gives an iconic, nostalgic look to your GIF',
            chkBlurred: 'Blur effect',
            chkCinemagraph: 'Freeze the entire GIF except for regions which you define. Requires a mask setting',
            chkCinemaInvert: 'Animate the regions that are NOT painted instead',
            btnEditMask: 'Edit the areas you wish to stay animated.',
            chkSound: 'Enable sound. Requires you to choose a format that supports sound such as mp4, webm or mov.',
            btnEditSound: 'Pick your audio track and start time.',
        }

        for item, tipString in list(tooltips.items()):
            createToolTip(item, tipString)

        def OnOK():
            self.OnStopPreview(None)
            dlg.destroy()
            return True

        def OnEditAudioClicked():
            hadAudio = self.HaveAudioPath()
            ret = self.OnEditAudioSettings(dlg)

            self.ReModalDialog(dlg)

            if self.HaveAudioPath():
                if chkSound.cget('state') == 'disabled':
                    chkSound.configure(state='normal')
                    if hadAudio is False:
                        self.isAudioEnabled.set(1)
            else:
                chkSound.configure(state='disabled')

            self.OnEffectsChange(None)
            return ret

        def OnEditMaskClicked():
            hadMask = self.HaveMask()
            ret = self.OnEditMask(dlg)

            self.ReModalDialog(dlg)

            if self.HaveMask():
                if chkCinemagraph.cget('state') == 'disabled':
                    chkCinemagraph.configure(state='normal')
                    chkCinemaInvert.configure(state='normal')
                    if hadMask is False:
                        self.isCinemagraph.set(1)
            else:
                chkCinemagraph.configure(state='disabled')
                chkCinemaInvert.configure(state='disabled')

            self.OnEffectsChange(None)
            return ret

        def OnSelectTintColor():
            (colorRgb, colorHex) = askcolor(
                parent=self.parent,
                initialcolor=self.colorTintColor.get(),
                title='Choose Tint Color',
            )
            self.colorTintColor.set(colorHex)
            return True

        def OnSelectBorderColor():
            (colorRgb, colorHex) = askcolor(
                parent=self.parent,
                initialcolor=self.borderColor.get(),
                title='Choose Border Color',
            )
            self.borderColor.set(colorHex)
            return True

        dlg.protocol('WM_DELETE_WINDOW', OnOK)
        btnOK.configure(command=OnOK)
        btnTintColor.configure(command=OnSelectTintColor)
        btnBorderColor.configure(command=OnSelectBorderColor)
        btnEditMask.configure(command=OnEditMaskClicked)
        btnEditSound.configure(command=OnEditAudioClicked)

        if self.conf.GetParamBool('settings', 'autoPreview'):
            self.OnShowPreview(None)

        return self.WaitForChildDialog(dlg)

    #
    #
    # Caption configuration dialog
    #
    #
    def OnCaptionConfig(self):
        if self.gif is None:
            return False

        positions = (
            'Top Left',
            'Top',
            'Top Right',
            'Middle Left',
            'Center',
            'Middle Right',
            'Bottom Left',
            'Bottom',
            'Bottom Right',
        )
        fonts = self.gif.GetFonts()
        isEdit = False

        logging.info('Font count: %d' % (fonts.GetFontCount()))

        if fonts.GetFontCount() == 0:
            tkinter.messagebox.showinfo('Font Issue', "I wasn't able to find any fonts :(")
            return False

        # Default form values
        if len(self.OnCaptionConfigDefaults) == 0:
            recommendedFont = fonts.GetBestFontFamilyIdx(
                self.conf.GetParam('captiondefaults', 'captionFont')
            )

            try:
                positionIdx = positions.index(self.conf.GetParam('captiondefaults', 'position'))
            except Exception:
                positionIdx = 7

            try:
                styleList = fonts.GetFontAttributeList(
                    self.conf.GetParam('captiondefaults', 'captionFont')
                )
                styleIdx = styleList.index(self.conf.GetParam('captiondefaults', 'fontStyle'))
            except Exception:
                styleIdx = 0

            self.OnCaptionConfigDefaults['defaultFontSize'] = self.conf.GetParam(
                'captiondefaults', 'fontSize'
            )
            self.OnCaptionConfigDefaults['defaultFontColor'] = self.conf.GetParam(
                'captiondefaults', 'fontColor'
            )
            self.OnCaptionConfigDefaults['defaultFontOutlineColor'] = self.conf.GetParam(
                'captiondefaults', 'outlineColor'
            )
            self.OnCaptionConfigDefaults['defaultFontIdx'] = recommendedFont
            self.OnCaptionConfigDefaults['defaultFontStyleIdx'] = styleIdx
            self.OnCaptionConfigDefaults['defaultPosition'] = positionIdx
            self.OnCaptionConfigDefaults['defaultFontOutlineThickness'] = int(
                self.conf.GetParam('captiondefaults', 'outlineSize')
            )
            self.OnCaptionConfigDefaults['defaultOpacity'] = int(
                self.conf.GetParam('captiondefaults', 'opacity')
            )
            self.OnCaptionConfigDefaults['defaultDropShadow'] = int(
                self.conf.GetParamBool('captiondefaults', 'dropShadow')
            )
            self.OnCaptionConfigDefaults['defaultLineSpacing'] = int(
                self.conf.GetParam('captiondefaults', 'interlineSpacing')
            )
            self.OnCaptionConfigDefaults['defaultApplyFxToText'] = int(
                self.conf.GetParamBool('captiondefaults', 'applyFx')
            )

        if self.cbxCaptionList.current() == 0:  # Entry zero is "Add new caption"
            captionIdx = len(self.cbxCaptionList['values'])
        else:
            captionIdx = self.cbxCaptionList.current()
            isEdit = True

        # Create child dialog
        captionDlg = self.CreateChildDialog('Caption Configuration (%d)' % (captionIdx))
        if captionDlg is None:
            return False

        lblSample = tkinter.Label(captionDlg, text='Sample')
        lblFontPreview = tkinter.Label(captionDlg, text='AaBbYyZz')
        lblFontPreview['fg'] = self.OnCaptionConfigDefaults['defaultFontColor']
        lblFontPreview['bg'] = '#000000'

        lblCaption = tkinter.Label(captionDlg, text='Caption')
        txtCaption = tkinter.Text(captionDlg, font=self.defaultFont, width=45, height=3)
        txtCaption.focus()

        fontSize = tkinter.StringVar()
        font_size_values = tuple(f'{i}pt' for i in range(8, 73))
        spnCaptionFontSize = tkinter.Spinbox(
            captionDlg,
            font=self.defaultFont,
            from_=9,
            to=72,
            increment=1,
            values=font_size_values,
            width=5,
            textvariable=fontSize,
            repeatdelay=300,
            repeatinterval=60,
        )  # font=self.defaultFont
        fontSize.set(self.OnCaptionConfigDefaults['defaultFontSize'])

        lblFont = tkinter.Label(captionDlg, text='Font')
        fontFamily = tkinter.StringVar()
        cbxFontFamily = ttk.Combobox(
            captionDlg, textvariable=fontFamily, state='readonly', width=20
        )

        cbxFontFamily['values'] = fonts.GetFamilyList()
        cbxFontFamily.current(self.OnCaptionConfigDefaults['defaultFontIdx'])

        fontStyle = tkinter.StringVar()
        lblStyle = tkinter.Label(captionDlg, font=self.defaultFont, text='Style')
        cbxStyle = ttk.Combobox(
            captionDlg,
            textvariable=fontStyle,
            state='readonly',
            width=15,
            values=(fonts.GetFontAttributeList(fontFamily.get())),
        )
        cbxStyle.current(self.OnCaptionConfigDefaults['defaultFontStyleIdx'])

        positioning = tkinter.StringVar()
        lblPosition = tkinter.Label(captionDlg, font=self.defaultFont, text='Positioning')
        cbxPosition = ttk.Combobox(
            captionDlg,
            textvariable=positioning,
            state='readonly',
            width=15,
            values=positions,
        )
        cbxPosition.current(self.OnCaptionConfigDefaults['defaultPosition'])

        btnCaptionFontColor = tkinter.Button(captionDlg, font=self.defaultFont, text='Color Picker')

        numFrames = self.gif.GetNumFrames()
        lblStartFrame = tkinter.Label(captionDlg, font=self.defaultFont, text='Start Frame')
        sclStartFrame = tkinter.Scale(
            captionDlg,
            font=self.defaultFontTiny,
            from_=1,
            to=numFrames,
            resolution=1,
            tickinterval=0,
            orient=tkinter.HORIZONTAL,
            sliderlength=20,
            width=15,
            length=275,
            showvalue=True,
        )

        lblEndFrame = tkinter.Label(captionDlg, font=self.defaultFont, text='End Frame')
        sclEndFrame = tkinter.Scale(
            captionDlg,
            font=self.defaultFontTiny,
            from_=1,
            to=numFrames,
            resolution=1,
            tickinterval=0,
            orient=tkinter.HORIZONTAL,
            sliderlength=20,
            width=15,
            length=275,
            showvalue=True,
        )
        sclEndFrame.set(numFrames)

        animateSetting = tkinter.StringVar()
        animationType = tkinter.StringVar()
        lblAnimate = tkinter.Label(captionDlg, font=self.defaultFont, text='Animation')

        animValues = ['Off']

        for animType in (
            'FadeIn',
            'FadeOut',
            'FadeInOut',
            'Triangle',
            'Sawtooth',
            'Square',
        ):
            for animSpeed in ('Slow', 'Medium', 'Fast'):
                animValues.append(animType + ' ' + animSpeed)

        animValues.append('Random')

        cbxAnimate = ttk.Combobox(
            captionDlg,
            textvariable=animateSetting,
            state='readonly',
            width=15,
            values=tuple(animValues),
        )
        cbxAnimate.current(0)

        cbxAnimateType = ttk.Combobox(
            captionDlg,
            textvariable=animationType,
            state='readonly',
            width=15,
            values=('Blink', 'Left-Right', 'Up-Down'),
        )
        cbxAnimateType.current(0)

        lblOutline = tkinter.Label(captionDlg, font=self.defaultFont, text='Outline')
        lblOutlineSize = tkinter.Label(captionDlg, font=self.defaultFont, text='Size')

        outlineThickness = tkinter.IntVar()
        spnCaptionFontOutlineSize = tkinter.Spinbox(
            captionDlg,
            font=self.defaultFont,
            from_=0,
            to=15,
            increment=1,
            width=5,
            textvariable=outlineThickness,
            repeatdelay=300,
            repeatinterval=60,
            state='readonly',
        )
        outlineThickness.set(self.OnCaptionConfigDefaults['defaultFontOutlineThickness'])

        dropShadow = tkinter.IntVar()
        chkdropShadow = tkinter.Checkbutton(captionDlg, text='Shadow', variable=dropShadow)
        dropShadow.set(self.OnCaptionConfigDefaults['defaultDropShadow'])

        lblFilters = tkinter.Label(captionDlg, font=self.defaultFont, text='Effects')
        applyFxToText = tkinter.IntVar()
        chkApplyFxToText = tkinter.Checkbutton(
            captionDlg, text='Apply Filters', variable=applyFxToText
        )
        applyFxToText.set(self.OnCaptionConfigDefaults['defaultApplyFxToText'])

        lblOpacity = tkinter.Label(captionDlg, font=self.defaultFont, text='Opacity')
        opacity = tkinter.IntVar()
        spnOpacity = tkinter.Spinbox(
            captionDlg,
            font=self.defaultFont,
            from_=0,
            to=100,
            increment=1,
            width=5,
            textvariable=opacity,
            repeatdelay=300,
            repeatinterval=30,
            state='readonly',
            wrap=True,
        )
        opacity.set(self.OnCaptionConfigDefaults['defaultOpacity'])

        lblLineSpacing = tkinter.Label(captionDlg, font=self.defaultFont, text='Line Space Adj.')
        lineSpacing = tkinter.IntVar()
        spnSpacing = tkinter.Spinbox(
            captionDlg,
            font=self.defaultFont,
            from_=-200,
            to=200,
            increment=1,
            width=5,
            textvariable=lineSpacing,
            repeatdelay=300,
            repeatinterval=30,
            state='readonly',
        )
        lineSpacing.set(self.OnCaptionConfigDefaults['defaultLineSpacing'])

        btnOk = tkinter.Button(captionDlg, text='Done', padx=4, pady=4)

        # Place items on grid
        rowIdx = 0
        lblCaption.grid(row=rowIdx, column=0, sticky=tkinter.W, padx=4, pady=4)
        txtCaption.grid(row=rowIdx, column=1, sticky=tkinter.EW, padx=4, pady=4, columnspan=3)

        rowIdx += 1
        lblFont.grid(row=rowIdx, column=0, sticky=tkinter.W, padx=4, pady=4)
        cbxFontFamily.grid(row=rowIdx, column=1, sticky=tkinter.W, padx=4, pady=4)
        spnCaptionFontSize.grid(row=rowIdx, column=2, sticky=tkinter.W, padx=4, pady=4)
        btnCaptionFontColor.grid(row=rowIdx, column=3, sticky=tkinter.W, padx=4, pady=4)

        rowIdx += 1
        lblSample.grid(row=rowIdx, column=0, sticky=tkinter.W, padx=4, pady=4)
        lblFontPreview.grid(row=rowIdx, column=1, sticky=tkinter.W, padx=4, pady=4, columnspan=3)

        rowIdx += 1
        lblStyle.grid(row=rowIdx, column=0, sticky=tkinter.W, padx=4, pady=4)
        cbxStyle.grid(row=rowIdx, column=1, sticky=tkinter.EW, padx=4, pady=4)

        rowIdx += 1
        lblPosition.grid(row=rowIdx, column=0, sticky=tkinter.W, padx=4, pady=4)
        cbxPosition.grid(row=rowIdx, column=1, sticky=tkinter.EW, padx=4, pady=4)

        rowIdx += 1
        lblOutline.grid(row=rowIdx, column=0, sticky=tkinter.W, padx=4, pady=4)

        spnCaptionFontOutlineSize.grid(row=rowIdx, column=1, sticky=tkinter.W, padx=4, pady=4)
        chkdropShadow.grid(row=rowIdx, column=2, sticky=tkinter.W, padx=0, pady=4, columnspan=2)

        rowIdx += 1
        lblOpacity.grid(row=rowIdx, column=0, sticky=tkinter.W, padx=4, pady=4)
        spnOpacity.grid(row=rowIdx, column=1, sticky=tkinter.W, padx=4, pady=4)

        rowIdx += 1
        lblLineSpacing.grid(row=rowIdx, column=0, sticky=tkinter.W, padx=4, pady=4)
        spnSpacing.grid(row=rowIdx, column=1, sticky=tkinter.W, padx=4, pady=4)

        rowIdx += 1
        lblFilters.grid(row=rowIdx, column=0, sticky=tkinter.W, padx=4, pady=4)
        chkApplyFxToText.grid(row=rowIdx, column=1, sticky=tkinter.W, padx=0, pady=4, columnspan=1)

        rowIdx += 1
        lblAnimate.grid(row=rowIdx, column=0, sticky=tkinter.W, padx=4, pady=4)
        cbxAnimateType.grid(row=rowIdx, column=1, sticky=tkinter.EW, padx=4, pady=4)
        cbxAnimate.grid(row=rowIdx, column=2, sticky=tkinter.EW, padx=4, pady=4, columnspan=2)

        rowIdx += 1
        lblStartFrame.grid(row=rowIdx, column=0, sticky=tkinter.W, padx=4, pady=4)
        sclStartFrame.grid(row=rowIdx, column=1, sticky=tkinter.EW, padx=4, pady=4, columnspan=3)
        rowIdx += 1
        lblEndFrame.grid(row=rowIdx, column=0, sticky=tkinter.W, padx=4, pady=4)
        sclEndFrame.grid(row=rowIdx, column=1, sticky=tkinter.EW, padx=4, pady=4, columnspan=3)

        rowIdx += 1
        btnOk.grid(row=rowIdx, column=0, sticky=tkinter.EW, padx=4, pady=4, columnspan=4)

        tooltips = {
            txtCaption: 'Type your text here.',
            cbxFontFamily: 'Font family. Try selecting this field, then scroll-wheeling over it with your mouse :)',
            spnCaptionFontSize: 'Font point size.',
            btnCaptionFontColor: 'Open up a color chooser to pick a color for your font.',
            cbxStyle: 'Font parameters/styling',
            spnSpacing: 'Adjust the inter-line spacing. Only applies if your caption contains multiple lines. Can be negative or positive.',
            cbxAnimate: 'Pick your text animation style',
            cbxAnimateType: 'Pick your text animation effect',
            cbxPosition: 'Choose text placement on GIF.',
            spnCaptionFontOutlineSize: 'Thickness of the black font outline.',
            sclStartFrame: 'Choose where in the GIF you want the text to start',
            sclEndFrame: 'Choose where you want the text to disappear',
            spnOpacity: 'Set the amount of transparency. Smaller values = more see-through',
            chkdropShadow: 'Add a shadow under the caption',
            chkApplyFxToText: 'Apply filters to text. Otherwise, text will be pasted on top of the filtered image.',
            btnOk: 'Add this caption to the final GIF. Note: You can add up to 16 separate captions.',
        }

        for item, tipString in list(tooltips.items()):
            createToolTip(item, tipString)

        def OnFontUpdate(*args):
            # Did the font change?
            fontChanged = False
            if cbxFontFamily.current() != self.OnCaptionConfigDefaults['defaultFontIdx']:
                fontChanged = True

            if fontChanged:
                cbxStyle['values'] = fonts.GetFontAttributeList(fontFamily.get())
                cbxStyle.current(0)

            previewFont = tkinter.font.Font(family=fontFamily.get(), size=14)

            if fontStyle.get().find('Italic') != -1:
                previewFont.configure(slant=tkinter.font.ITALIC)
            if fontStyle.get().find('Bold') != -1:
                previewFont.configure(weight=tkinter.font.BOLD)

            lblFontPreview.configure(font=previewFont)

            self.OnCaptionConfigDefaults['defaultFontSize'] = fontSize.get()
            self.OnCaptionConfigDefaults['defaultFontColor'] = lblFontPreview['fg']
            self.OnCaptionConfigDefaults['defaultFontIdx'] = cbxFontFamily.current()
            self.OnCaptionConfigDefaults['defaultFontStyleIdx'] = cbxStyle.current()
            self.OnCaptionConfigDefaults['defaultPosition'] = cbxPosition.current()
            self.OnCaptionConfigDefaults['defaultFontOutlineThickness'] = outlineThickness.get()
            self.OnCaptionConfigDefaults['defaultOpacity'] = opacity.get()
            self.OnCaptionConfigDefaults['defaultDropShadow'] = dropShadow.get()
            self.OnCaptionConfigDefaults['defaultLineSpacing'] = lineSpacing.get()
            self.OnCaptionConfigDefaults['defaultApplyFxToText'] = applyFxToText.get()

            return True

        def OnSelectCaptionColor():
            (colorRgb, colorHex) = askcolor(
                parent=self.parent,
                initialcolor=lblFontPreview['fg'],
                title='Choose Caption Color',
            )
            lblFontPreview.configure(fg=colorHex)
            OnFontUpdate(None)

        def OnSetFramePosition(newIdx):
            self.SetThumbNailIndex(int(newIdx))
            self.UpdateThumbnailPreview()

            start = int(sclStartFrame.get())
            end = int(sclEndFrame.get())
            if start > end:
                sclStartFrame.set(end)
            return True

        def OnSaveCaption():
            caption = txtCaption.get(1.0, tkinter.END)
            if len(caption.strip()) <= 0 and not isEdit:
                captionDlg.destroy()
                return False

            # Strip last new line
            if caption.endswith('\n'):
                caption = caption[:-1]

            # Check for unsupported unicode
            try:
                caption.encode(locale.getpreferredencoding())
            except UnicodeError as e:
                tkinter.messagebox.showinfo(
                    'Invalid Characters Detected',
                    "Warning: Your caption contains invalid characters that don't exist in your locale's encoding ("
                    + locale.getpreferredencoding()
                    + '). Please remove unprintable characters before generating GIF.\n\n'
                    + str(e),
                )

            caption = caption.replace('\n', '[enter]')

            listValues = list(self.cbxCaptionList['values'])

            if len(caption) <= 0:
                self.captionChanges += self.conf.SetParam(confName, 'text', '')
                self.captionChanges += self.conf.SetParam(confName, 'font', '')
                self.captionChanges += self.conf.SetParam(confName, 'style', '')
                self.captionChanges += self.conf.SetParam(confName, 'size', '')
                self.captionChanges += self.conf.SetParam(confName, 'frameStart', '')
                self.captionChanges += self.conf.SetParam(confName, 'frameEnd', '')
                self.captionChanges += self.conf.SetParam(confName, 'color', '')
                self.captionChanges += self.conf.SetParam(confName, 'animationEnvelope', '')
                self.captionChanges += self.conf.SetParam(confName, 'animationType', '')
                self.captionChanges += self.conf.SetParam(confName, 'positioning', '')
                self.captionChanges += self.conf.SetParam(confName, 'outlineColor', '')
                self.captionChanges += self.conf.SetParam(confName, 'outlineThickness', '')
                self.captionChanges += self.conf.SetParam(confName, 'opacity', '')
                self.captionChanges += self.conf.SetParam(confName, 'dropShadow', '')
                self.captionChanges += self.conf.SetParam(confName, 'applyFx', '')
                self.captionChanges += self.conf.SetParam(confName, 'interlineSpacing', '')

                listValues[captionIdx] = '[deleted]'
            else:
                self.captionChanges += self.conf.SetParam(confName, 'text', caption)
                self.captionChanges += self.conf.SetParam(confName, 'font', fontFamily.get())
                self.captionChanges += self.conf.SetParam(confName, 'style', fontStyle.get())
                self.captionChanges += self.conf.SetParam(
                    confName, 'size', spnCaptionFontSize.get()
                )
                self.captionChanges += self.conf.SetParam(
                    confName, 'frameStart', sclStartFrame.get()
                )
                self.captionChanges += self.conf.SetParam(confName, 'frameEnd', sclEndFrame.get())
                self.captionChanges += self.conf.SetParam(confName, 'color', lblFontPreview['fg'])
                self.captionChanges += self.conf.SetParam(
                    confName, 'animationEnvelope', animateSetting.get()
                )
                self.captionChanges += self.conf.SetParam(
                    confName, 'animationType', animationType.get()
                )
                self.captionChanges += self.conf.SetParam(
                    confName, 'positioning', positioning.get()
                )
                self.captionChanges += self.conf.SetParam(
                    confName,
                    'outlineColor',
                    self.OnCaptionConfigDefaults['defaultFontOutlineColor'],
                )  # Fixed for now
                self.captionChanges += self.conf.SetParam(
                    confName, 'outlineThickness', outlineThickness.get()
                )
                self.captionChanges += self.conf.SetParam(confName, 'opacity', opacity.get())
                self.captionChanges += self.conf.SetParam(confName, 'dropShadow', dropShadow.get())
                self.captionChanges += self.conf.SetParam(confName, 'applyFx', applyFxToText.get())
                self.captionChanges += self.conf.SetParam(
                    confName, 'interlineSpacing', lineSpacing.get()
                )

                if isEdit:
                    listValues[captionIdx] = caption
                else:
                    listValues.append(caption)

            # Convert list back to tuple.  Make sure that the last caption entered has focus
            self.cbxCaptionList['values'] = tuple(listValues)
            self.cbxCaptionList.current(captionIdx)

            captionDlg.destroy()

        #
        # Attach handlers
        #

        sclStartFrame.configure(command=OnSetFramePosition)
        sclEndFrame.configure(command=OnSetFramePosition)
        btnOk.configure(command=OnSaveCaption)
        btnCaptionFontColor.configure(command=OnSelectCaptionColor)

        applyFxToText.trace_add('write', OnFontUpdate)
        dropShadow.trace_add('write', OnFontUpdate)
        outlineThickness.trace_add('write', OnFontUpdate)
        opacity.trace_add('write', OnFontUpdate)
        fontSize.trace_add('write', OnFontUpdate)
        fontFamily.trace_add('write', OnFontUpdate)
        fontStyle.trace_add('write', OnFontUpdate)
        positioning.trace_add('write', OnFontUpdate)
        animateSetting.trace_add('write', OnFontUpdate)
        animationType.trace_add('write', OnFontUpdate)
        lineSpacing.trace_add('write', OnFontUpdate)

        OnFontUpdate(None)

        # Initialize dialog with existing values
        confName = 'caption' + str(captionIdx)
        if self.conf.GetParam(confName, 'text') != '':
            txtCaption.insert(
                tkinter.END,
                self.conf.GetParam(confName, 'text').replace('[enter]', '\n'),
            )
            fontFamily.set(self.conf.GetParam(confName, 'font'))
            fontStyle.set(self.conf.GetParam(confName, 'style'))
            fontSize.set(self.conf.GetParam(confName, 'size'))
            sclStartFrame.set(self.conf.GetParam(confName, 'frameStart'))
            sclEndFrame.set(self.conf.GetParam(confName, 'frameEnd'))
            animateSetting.set(self.conf.GetParam(confName, 'animationEnvelope'))
            animationType.set(self.conf.GetParam(confName, 'animationType'))
            cbxPosition.set(self.conf.GetParam(confName, 'positioning'))
            captionColor = self.conf.GetParam(confName, 'color')
            outlineThickness.set(self.conf.GetParam(confName, 'outlineThickness'))
            opacity.set(self.conf.GetParam(confName, 'opacity'))
            dropShadow.set(self.conf.GetParam(confName, 'dropShadow'))
            applyFxToText.set(self.conf.GetParam(confName, 'applyFx'))
            lineSpacing.set(self.conf.GetParam(confName, 'interlineSpacing'))

            lblFontPreview.configure(fg=captionColor)

        self.parent.update_idletasks()

        return self.WaitForChildDialog(captionDlg)


class ToolTip(object):
    """Little yellow tool tips shown on mouse-over."""

    def __init__(self, widget):
        self.widget = widget
        self.tipwindow = None
        self.id = None
        self.x = self.y = 0

    def showtip(self, text):
        "Display text in tooltip window"

        # Line wrap
        text = '\n'.join(line.strip() for line in re.findall(r'.{1,40}(?:\s+|$)', text))

        self.text = text
        if self.tipwindow or not self.text:
            return

        bboxVals = self.widget.bbox('insert')

        if bboxVals is None:
            logging.error('Failed to display tooltip: ' + text)
            return False

        if len(bboxVals) == 4:
            x, y, cx, cy = bboxVals
        else:
            x, y, cx, cy = [int(n) for n in bboxVals.split()]

        # Set the X and Y offset
        x = x + self.widget.winfo_rootx() + 15
        y = y + cy + self.widget.winfo_rooty() + 50

        self.tipwindow = tw = tkinter.Toplevel(self.widget)
        # tw.wm_attributes('-alpha', 0.6)
        tw.wm_overrideredirect(True)
        tw.wm_geometry('+%d+%d' % (x, y))
        if IM_A_MAC:
            try:
                # For Mac OS
                tw.tk.call(
                    '::tk::unsupported::MacWindowStyle',
                    'style',
                    tw._w,
                    'help',
                    'noActivates',
                )
            except tkinter.TclError:
                pass

        # tw.withdraw()

        label = tkinter.Label(
            tw,
            text=self.text,
            justify=tkinter.LEFT,
            background='#ffffe0',
            relief=tkinter.SOLID,
            borderwidth=1,
            font=('tahoma', '8', 'normal'),
        )
        label.grid(ipadx=1)

    def makevisable(self):
        if self.tipwindow is not None:
            self.tipwindow.deiconify()

    def hidetip(self):
        tw = self.tipwindow
        self.tipwindow = None
        if tw:
            tw.destroy()


def createToolTip(widget, text):
    if len(text) <= 0:
        return

    toolTip = ToolTip(widget)

    def enter(event):
        toolTip.showtip(text)
        # toolTip.tipwindow.after(400, toolTip.makevisable())

    def leave(event):
        toolTip.hidetip()

    widget.bind('<Enter>', enter)
    widget.bind('<Leave>', leave)


def audio_play(wavPath):
    if IM_A_MAC:
        if wavPath is not None:
            subprocess.call(['afplay', wavPath])  # blocks

    elif IM_A_PC:
        if wavPath is None:
            winsound.PlaySound(None, 0)
        else:
            winsound.PlaySound(wavPath, winsound.SND_FILENAME | winsound.SND_ASYNC)

    return True


# Any uncaught exceptions will end up here
lastErrorTimestamp = 0


def tk_error_catcher(self, *args):
    global lastErrorTimestamp
    # Rate-limit the pop-up. If there's a constantly repeating bug, the never-ending popup becomes very annoying.
    if time.time() - lastErrorTimestamp > 10:
        showGuiMessage = True
        lastErrorTimestamp = time.time()
    else:
        showGuiMessage = False

    err = traceback.format_exception(*args)

    logging.error('Error trace:')

    for errLine in err:
        logging.error('%s' % (errLine))

        if 'invalid command name' in errLine:
            showGuiMessage = False

    if showGuiMessage:
        openBugReport = tkinter.messagebox.askyesno(
            'Oh crap, this is embarrassing!',
            "A problem occurred somewhere in the Instagiffer code. Please go to Help -> Generate Bug Report and send it to instagiffer@gmail.com and I'll fix it ASAP. Would you like to open the bug report now?",
            default='yes',
        )

        if openBugReport:
            igf_paths.open_file_with_default_app(igf_paths.get_log_path())
    # else:
    # raise SystemExit

    # self.quit()


def start(exe_dir, cmdline_video_path):
    # Start the app.  Gather some diagnostic information about this machine to help with bug reporting
    logging.info('Starting Instagiffer version ' + __version__ + '...')

    tkinter.Tk.report_callback_exception = tk_error_catcher

    import platform

    if IM_A_PC:
        logging.info('OS: ' + platform.platform())
    elif IM_A_MAC:
        logging.info('OSX Version: ' + str(platform.mac_ver()))
    else:
        logging.info('Unknown OS')

    logging.info(sys.version_info)
    logging.info('App: [' + exe_dir + ']. Home: [' + os.path.expanduser('~') + ']')
    logging.info(
        'System Locale: '
        + str(locale.getlocale())
        + '; Preferred encoding: '
        + locale.getpreferredencoding()
    )

    root = tkinter.Tk()
    app = GifApp(root, cmdline_video_path)

    root.mainloop()
