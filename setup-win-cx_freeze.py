import os
import sys

import cx_Freeze

__author__ = "Justin Todd"
__email__ = "instagiffer@gmail.com"
__copyright__ = "Copyright 2019, Exhale Software Inc."
application_title = "Instagiffer"
main_python_file = "instagiffer.py"

if "INSTAGIFFER_VERSION" in os.environ:
    INSTAGIFFER_VERSION = os.environ["INSTAGIFFER_VERSION"]
else:
    INSTAGIFFER_VERSION = '0.0.1'
    # raise RuntimeError("No INSTAGIFFER_VERSION in environment!")

if "INSTAGIFFER_PRERELEASE" in os.environ:
    INSTAGIFFER_PRERELEASE = os.environ["INSTAGIFFER_PRERELEASE"]
    INSTAGIFFER_VERSION += INSTAGIFFER_PRERELEASE.replace("pre-", ".")

print(f'INSTAGIFFER_VERSION: {INSTAGIFFER_VERSION}')

base = None

# Empty log files
logFiles = ["instagiffer.exe.log", "instagiffer-event.log"]
for log in logFiles:
    open(log, "w+").close()

DATA_FILES = []

#
# Mac specifics
#

if sys.platform == "darwin":
    DATA_FILES = [
        "deps/",
        "fonts/",
        "instagiffer.conf",
        "instagiffer.icns",
    ]
#
# Windows Specifics
#
elif sys.platform == "win32":
    base = "gui"

    DATA_FILES = [
        # ("Microsoft.VC90.MFC", mfcfiles),
        "uninstall.ico",
        "instagiffer.ico",
        "instagiffer.conf",
        "bindeps/",
        #'bindeps/mogrify.exe',
        #'bindeps/convert.exe',
        #'bindeps/youtube-dl.exe',
        #'bindeps/vcomp100.dll',
        #'bindeps/tk85.dll',
        #'bindeps/tcl85.dll',
        #'bindeps/python27.dll',
    ]


includes = []
excludes = ["doctest", "pdb", "unittest", "difflib"]  # ssl (Needed for imgur uploading)
packages = ["PIL", "PIL.ImageDraw", "PIL.ImageGrab"]
options = {
    "build_exe": {
        "excludes": excludes,
        "includes": includes,
        "packages": packages,
        "include_files": DATA_FILES + logFiles,
        # "create_shared_zip": True,
        # "include_in_shared_zip": True,
        "optimize": True,
        "silent": True,
    }
}

cx_Freeze.setup(
    name=application_title,
    version=INSTAGIFFER_VERSION,
    description="Instagiffer - Animated GIF creator",
    url="http://www.instagiffer.com",
    author=__author__,
    options=options,
    executables=[
        cx_Freeze.Executable(
            main_python_file,
            init_script=None,
            base=base,
            icon="instagiffer.ico",

            # compress=True,
            # appendScriptToLibrary=False,
            # appendScriptToExe=True,
        )
    ],
)


# Mac post-compilation activities
if sys.platform == "darwin":
    # Make instagiffer executable
    os.chmod("build/Instagiffer.app/Contents/MacOS/instagiffer", 0o755)
