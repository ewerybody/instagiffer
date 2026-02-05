import os
import glob


def CountFilesInDir(dirname, filenamePattern=None):
    if filenamePattern is None:
        return len(
            [
                name
                for name in os.listdir(dirname)
                if os.path.isfile(os.path.join(dirname, name))
            ]
        )
    else:
        file_glob = dirname + filenamePattern + '*'
        return len(glob.glob(file_glob))


def no_recurse(func):
    """decorator"""
    func.called = False

    def f(*args, **kwargs):
        if func.called:
            print('Recursion!')
            return False
        else:
            func.called = True
            result = func(*args, **kwargs)
            func.called = False
            return result

    return f
