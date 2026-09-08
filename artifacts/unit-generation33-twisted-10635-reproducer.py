"""Python 3 mechanical port of the maintainer attachment for Twisted #10635.

Source: https://github.com/twisted/twisted/issues/10635
Original attachment SHA-256:
8b8d744cc80c7bbca9a72dd5a33bed3acc8380625994668b136e2e4f64929a1a

The process/descriptor topology is unchanged. Only Python 2 syntax and the
input fixture are made deterministic for the frozen Python 3 environment.
"""

import os
from pathlib import Path

from twisted.internet import protocol, reactor
from twisted.python import log


log.startLogging(open("trial-output.log", "w", encoding="utf-8"))


class PP(protocol.ProcessProtocol):
    def __init__(self, fds):
        self._fds = fds

    def outReceived(self, data):
        print(self._fds, "out received", repr(data), flush=True)

    def errReceived(self, data):
        print(self._fds, "err received", repr(data), flush=True)

    def processEnded(self, status):
        print("process ended", flush=True)
        if self._fds is not None:
            print("Closing", self._fds, flush=True)
            self.transport.closeChildFD(self._fds[0])
            self.transport.closeChildFD(self._fds[1])
        else:
            reactor.stop()


def start():
    fixture = Path("issue-10635-input.txt")
    fixture.write_bytes(b"twisted issue 10635 deterministic input\n")
    file_obj = fixture.open("rb")
    r1, w1 = os.pipe()
    r2, w2 = os.pipe()
    r3, w3 = os.pipe()

    p1 = PP((r1, w1))
    p2 = PP((r2, w2))
    p3 = PP((r3, w3))
    p4 = PP(None)
    reactor.spawnProcess(
        p1, "/bin/cat", ["cat1"], childFDs={0: file_obj.fileno(), 1: w1, 2: "r"}
    )
    reactor.spawnProcess(p2, "/bin/cat", ["cat2"], childFDs={0: r1, 1: w2, 2: "r"})
    reactor.spawnProcess(p3, "/bin/cat", ["cat3"], childFDs={0: r2, 1: w3, 2: "r"})
    reactor.spawnProcess(p4, "/bin/cat", ["cat4"], childFDs={0: r3, 1: "r", 2: "r"})


reactor.callWhenRunning(start)
reactor.run()
print("REPRODUCER_COMPLETED", flush=True)
