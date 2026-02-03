def GetFailSafeDir(conf, badPath):
    """For language auto-fix."""
    path = badPath
    if not IM_A_PC:
        return path

    goodPath = conf.GetParam('paths', 'failSafeDir')
    if os.path.exists(goodPath):
        return goodPath

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

    return path