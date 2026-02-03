#
# For language auto-fix
#
def GetFailSafeDir(conf, badPath):
    path = badPath

    if IM_A_PC:
        goodPath = conf.GetParam('paths', 'failSafeDir')
        if not os.path.exists(goodPath):
            if tkMessageBox.askyesno(
                'Automatically Fix Language Issue?',
                'It looks like you are using a non-latin locale. Can Instagiffer create directory '
                + goodPath
                + ' to solve this issue?',
            ):
                err = False
                try:
                    os.makedirs(goodPath)
                except:
                    err = True

                if os.path.exists(goodPath):
                    path = goodPath
                else:
                    err = True

                if err:
                    tkMessageBox.showinfo(
                        'Error Fixing Language Issue',
                        "Failed to create '"
                        + goodPath
                        + "'. Please make this directory manually in Windows Explorer, then restart Instagiffer.",
                    )
        else:
            path = goodPath

    return path