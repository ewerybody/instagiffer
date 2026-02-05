# /usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright (c) 2013-2019 Exhale Software Inc.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
# 1. Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
# 3. All advertising materials mentioning features or use of this software
#    must display the following acknowledgement:
#    This product includes software developed by Exhale Software Inc.
# 4. Neither Exhale Software Inc., nor the
#    names of its contributors may be used to endorse or promote products
#    derived from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY EXHALE SOFTWARE INC. ''AS IS'' AND ANY
# EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL EXHALE SOFTWARE INC.  BE LIABLE FOR ANY
# DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#
###############################################################################

"""instagiffer."""
import os
import sys
import os
import sys

# import argparse
import logging

import igf_paths
import igf_animgif
import igf_common
from igf_common import IM_A_PC, IM_A_MAC, __release__


__author__ = 'Justin Todd'
__copyright__ = 'Copyright 2013-2019, Exhale Software Inc.'
__maintainer__ = 'Justin Todd'
__email__ = 'instagiffer@gmail.com'
__status__ = 'Production'
# If this is False, bindep output, and info-level statements will be displayed stdout


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
            logging.info('File exists: %d' % (os.path.exists(self.videoFileName)))
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
        os.environ['PYTHONHOME'] = (
            '/System/Library/Frameworks/Python.framework/Versions/Current'
        )

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
