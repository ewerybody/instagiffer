# /usr/bin/env python
#
# Copyright (c) 2013-2019 Exhale Software Inc.
# Copyright (c) 2026 ai6yr (conversion to Python 3)
# Copyright (c) 2026 ewerybody (refactor)
# All rights reserved.
#
# See LICENSE file in the project root for full license text.

import logging
import os
import sys

import igf_animgif
import igf_common
import igf_paths
from igf_common import IM_A_MAC, IM_A_PC, __release__

__author__ = 'Justin Todd'
__copyright__ = 'Copyright 2013-2019, Exhale Software Inc.'
__maintainer__ = 'Justin Todd'
__email__ = 'instagiffer@gmail.com'
__status__ = 'Production'


class InstaCommandLine:
    description = 'Instagiffer Command Line'
    author = 'Exhale Software Inc.'

    def __init__(self):
        if not self.ArgsArePresent():
            return

        self.videoFileName = None
        self.ParseArguments()

    def ParseArguments(self):
        # parser = argparse.ArgumentParser(
        #   prog="instagiffer",
        #   description="You've discovered the Instagiffer %s command line. You're hardcore!" % (__version__),
        #   epilog="Happy Giffing!"
        # )
        # parser.add_argument('video', help='Path to local video file or Youtube link')
        # self.args          = parser.parse_args()

        self.videoFileName = sys.argv[1]  # self.args.video

    def ArgsArePresent(self):
        return len(sys.argv) > 1

    def GetVideoPath(self):
        if self.videoFileName is not None:
            logging.info('File specified on command line: ' + self.videoFileName)
            logging.info(f'File exists: {os.path.exists(self.videoFileName)}')
            return self.videoFileName
        else:
            return None

    #
    # Coming soon stuff
    #

    def BatchRun(self):
        self.binPath = os.path.dirname(os.path.realpath(sys.argv[0]))
        self.conf = igf_common.InstaConfig(self.binPath + os.sep + 'instagiffer.conf')
        self.workDir = igf_paths.create_working_dir(self.conf)
        self.gif = igf_animgif.AnimatedGif(
            self.conf, self.videoFileName, self.workDir, self.OnShowProgress, None
        )
        self.MakeGif()
        return 0

    # Progress callback
    def OnShowProgress(self, doneFlag, ignore=None):
        if doneFlag:
            print(' [OK]')
        else:
            sys.stdout.write('.')

    # Makes a GIF according to current configuration
    def MakeGif(self):
        print('Extracting frames:')
        self.gif.ExtractFrames()
        print('Cropping and resizing:')
        self.gif.CropAndResize()
        print('Generating GIF:')
        self.gif.Generate()


def main():
    # cwd to the directory containing the executable
    exe_dir = os.path.dirname(os.path.realpath(sys.argv[0]))
    os.chdir(exe_dir)

    # Set environment
    if IM_A_MAC:
        os.environ['MAGICK_HOME'] = './macdeps/im'
        os.environ['MAGICK_CONFIGURE_PATH'] = './macdeps/im/etc/ImageMagick-6'
        os.environ['FONTCONFIG_PATH'] = './macdeps/im/etc/fonts'

        # Now that Insta is loaded with a bundled Python version, we want
        # to make sure that YouTube-dl to use the built-in Python
        os.environ['PYTHONHOME'] = '/System/Library/Frameworks/Python.framework/Versions/Current'

    # File logging options
    try:
        logging.basicConfig(
            level=logging.DEBUG,
            format='%(asctime)s %(levelname)s: %(message)s',
            # filename=open(os.path.expanduser("~") + igf_paths.LOG_NAME,
            filename=igf_paths.LOG_NAME,
            filemode='w',
        )

    except Exception:
        # Oh well. no logging!
        pass

    # Turn off annoying Pillow logs
    # print logging.Logger.manager.loggerDict # list all loggers
    logging.getLogger('PIL.Image').setLevel(logging.CRITICAL)
    logging.getLogger('PIL').setLevel(logging.CRITICAL)

    # Developers only:
    if not __release__:
        console = logging.StreamHandler(sys.stdout)
        logging.getLogger('').addHandler(console)

    cmdline = InstaCommandLine()
    cmdline_batch_mode = False
    cmdline_video_path = None
    if IM_A_PC and cmdline.ArgsArePresent():
        cmdline_video_path = cmdline.GetVideoPath()

    # Command line mode or GUI?
    if cmdline_batch_mode:
        pass
    else:
        import igf_ui

        igf_ui.start(exe_dir, cmdline_video_path)


if __name__ == '__main__':
    main()
