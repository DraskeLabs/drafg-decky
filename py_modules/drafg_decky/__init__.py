"""The parts of the DraFG Decky plugin that do not need Decky.

Kept free of the `decky` module so they can be tested anywhere:

* `steam` finds the copy of DraFG that Steam installed.
* `control` talks to a running DraFG over its control socket.
* `supervisor` starts, watches and stops DraFG's Game Mode process.
"""
