# !/usr/bin/python
# coding=utf-8
try:
    import pymel.core as pm
except ImportError as error:  # pragma: no cover - Maya runtime specific
    print(__file__, error)


def openPorts(**kwargs):
    """Open command ports for external script editor.

    Parameters:
        kwargs (str) = 'source type':'port name'
            source type (str) = The string argument is used to indicate which source type would be passed to the commandPort, ex. "mel" or "python".
            port name (str) = Specifies the name of the command port which this command creates.
                CommandPort names of the form name create a UNIX domain socket on the localhost corresponding to name.
                If name does not begin with "/", then /tmp/name is used.
                If name begins with "/", name denotes the full path to the socket.
                Names of the form :port number create an INET domain on the local host at the given port.
                Port numbers are in the range 1-65535. The port number is used to create a socket on the localhost.
                Port numbers are not used to create a socket on a remote host.
    Example:
        import commandPort; commandPort.openPorts(mel=':7001', python=':7002') #opens ports 7001/7002 for external script editor
    """
    for sourceType, port in kwargs.items():

        try:  # close existing open port.
            pm.commandPort(name=port, close=True)
        except RuntimeError as error:
            pass
            # pm.warning('Could not close port {}'.format(name))

        try:  # open new port.
            pm.commandPort(name=port, sourceType=sourceType)
        except RuntimeError as error:
            pm.warning("Could not open {} port {}".format(sourceType, port))


if __name__ == "__main__":
    openPorts()


# module name
print(__name__)
# -----------------------------------------------
# Notes
# -----------------------------------------------
