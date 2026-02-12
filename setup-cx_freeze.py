import os
import sys

import cx_Freeze

__author__ = 'Justin Todd'
__email__ = 'instagiffer@gmail.com'
__copyright__ = 'Copyright 2019, Exhale Software Inc.'
NAME = 'instagiffer'
main_python_file = f'{NAME}.py'

if 'INSTAGIFFER_VERSION' in os.environ:
    INSTAGIFFER_VERSION = os.environ['INSTAGIFFER_VERSION']
else:
    INSTAGIFFER_VERSION = '0.0.1'
    # raise RuntimeError("No INSTAGIFFER_VERSION in environment!")

if 'INSTAGIFFER_PRERELEASE' in os.environ:
    INSTAGIFFER_PRERELEASE = os.environ['INSTAGIFFER_PRERELEASE']
    INSTAGIFFER_VERSION += INSTAGIFFER_PRERELEASE.replace('pre-', '.')

print(f'INSTAGIFFER_VERSION: {INSTAGIFFER_VERSION}')

# Empty log files
log_files = ['instagiffer.exe.log', 'instagiffer-event.log']
for log in log_files:
    open(log, 'w+').close()

BUILD_ROOT = 'build/'
PLATFORM_DATA = {
    'darwin': {
        'files': ['macdeps/', 'fonts/', 'instagiffer.conf', 'instagiffer.icns'],
        'base': None,
        'build_path': os.path.join(BUILD_ROOT, f'{NAME.title()}.app', 'Contents', 'MacOS'),
        'icon': 'doc/graphics/logo.png',
    },
    'win32': {
        'files': ['windeps/', 'uninstall.ico', 'instagiffer.ico', 'instagiffer.conf'],
        'base': 'gui',
        'build_path': os.path.join(BUILD_ROOT, 'Win32'),  # TODO: check!
        'icon': f'{NAME}.ico',
    },
    'linux': {
        'files': ['uninstall.ico', 'instagiffer.ico', 'instagiffer.conf'],
        'base': None,
        'build_path': os.path.join(
            BUILD_ROOT, f'exe.linux-x86_64-{sys.version_info.major}.{sys.version_info.minor}'
        ),
        'icon': 'doc/graphics/logo.png',
    },
}

if sys.platform not in PLATFORM_DATA:
    raise RuntimeError(
        f'Unsupported platform: "{sys.platform}"!\n Currently supported: {list(PLATFORM_DATA)}'
    )

data = PLATFORM_DATA[sys.platform]

includes = []
excludes = ['doctest', 'pdb', 'unittest', 'difflib']  # ssl (Needed for imgur uploading)
packages = ['PIL', 'PIL.ImageDraw', 'PIL.ImageGrab']
options = {
    'build_exe': {
        'excludes': excludes,
        'includes': includes,
        'packages': packages,
        'include_files': data['files'] + log_files,
        # "create_shared_zip": True,
        # "include_in_shared_zip": True,
        'optimize': True,
        'silent': True,
    }
}

print('Setting up cx_Freeze ...')
cx_Freeze.setup(
    name=NAME.title(),
    version=INSTAGIFFER_VERSION,
    description='Instagiffer - Animated GIF creator',
    url='http://www.instagiffer.com',
    author=__author__,
    options=options,
    executables=[
        cx_Freeze.Executable(
            main_python_file,
            init_script=None,
            base=data['base'],
            icon=data['icon'],
            # compress=True,
            # appendScriptToLibrary=False,
            # appendScriptToExe=True,
        )
    ],
)
print('... cx_Freeze Done!')


if sys.platform in ('darwin', 'linux'):
    # Make instagiffer executable
    os.chmod(
        f'{data["build_path"]}/{NAME}',
        0o755,
    )
