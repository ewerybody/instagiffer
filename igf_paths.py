import os
import re
import sys
import locale
import logging

from igf_common import IM_A_PC, IM_A_MAC
if IM_A_PC:
    import win32api  # ty:ignore[unresolved-import]

import tkinter.messagebox


RE_PATTERNS: dict[str, str] = {
    'url': r'^(www\.|https://|http://)'
}
_RE_PATTERNS: dict[str, re.Pattern] = {}
EXT_IMAGE = '.jpeg', '.jpg', '.png', '.bmp', '.tif.tga'
EXT_GIF = '.gif'
EXT_VIDEO = EXT_GIF, '.mp4', '.webm'
LOG_NAME = 'instagiffer-event.log'


def _get_pattern(name: str) -> re.Pattern:
    if name not in _RE_PATTERNS:
        _RE_PATTERNS[name] = re.compile(RE_PATTERNS[name], re.I)
    return _RE_PATTERNS[name]


def is_url(s):
    return _get_pattern('url').match(s)


def is_picture_file(file_name: str) -> bool:
    return get_file_extension(file_name) in EXT_IMAGE


def get_file_extension(file_name):
    try:
        ext = os.path.splitext(file_name)[1]
    except Exception:
        return ''

    if ext is None:
        return ''

    return ext.lower()


def is_gif(file_path):
    return get_file_extension(file_path) == EXT_GIF


def cleanup_path(path):
    """Convert path into short form to bypass unicode headaches.
    Mostly for Windows.

    Deal with Unicode video paths. On Windows, simply DON'T
    deal with it. Use short names and paths instead :S
    """

    if IM_A_PC:
        try:
            path.decode('ascii')
        except Exception:
            path = win32api.GetShortPathName(path)

    return path


def open_file_with_default_app(file_name):
    """Open a file in the application associated with this file extension."""
    if IM_A_MAC:
        os.system('open ' + file_name)
        return

    try:
        os.startfile(file_name)
    except Exception:
        tkinter.messagebox.showinfo(
            'Unable to open!',
            "I wasn't allowed to open '"
            + file_name
            + "'. You will need to perform this task manually.",
        )


def create_working_dir(conf):
    temp_dir = None

    # See if they specified a custom dir
    if conf.ParamExists('paths', 'workingDir'):
        temp_dir = conf.GetParam('paths', 'workingDir')

    appDataRoot = ''

    # No temp dir configured
    if not temp_dir:
        if IM_A_MAC:
            appDataRoot = os.path.expanduser('~') + '/Library/Application Support/'
            temp_dir = appDataRoot + 'Instagiffer/'
        else:
            appDataRoot = os.path.expanduser('~') + os.sep
            temp_dir = appDataRoot + '.instagiffer' + os.sep + 'working'

    # Pre-emptive detection and correction of language issues
    try:
        temp_dir.encode(locale.getpreferredencoding())
    except UnicodeError:
        logging.info(
            'Users home directory is problematic due to non-latin characters: '
            + temp_dir
        )
        temp_dir = get_fail_safe_dir(conf, temp_dir)

    if os.path.isdir(temp_dir):
        return temp_dir

    # Try to create temp directory
    os.makedirs(temp_dir)
    if not os.path.exists(temp_dir):
        logging.error('Failed to create working directory: ' + temp_dir)
        return ''

    logging.info('Working directory created: ' + temp_dir)
    return temp_dir


def get_fail_safe_dir(conf, badPath):
    """For language auto-fix."""
    path = badPath
    if not IM_A_PC:
        return path

    goodPath = conf.GetParam('paths', 'failSafeDir')
    if os.path.exists(goodPath):
        return goodPath

    if tkinter.messagebox.askyesno(
        'Automatically Fix Language Issue?',
        'It looks like you are using a non-latin locale. Can Instagiffer create directory '
        + goodPath
        + ' to solve this issue?',
    ):
        err = False
        try:
            os.makedirs(goodPath)
        except Exception:
            err = True

        if os.path.exists(goodPath):
            path = goodPath
        else:
            err = True

        if err:
            tkinter.messagebox.showinfo(
                'Error Fixing Language Issue',
                "Failed to create '"
                + goodPath
                + "'. Please make this directory manually in Windows Explorer, then restart Instagiffer.",
            )

    return path


def get_log_path():
    return os.path.dirname(os.path.realpath(sys.argv[0])) + os.sep + LOG_NAME
