"""Called by the fixed adapter after a node's code raised an exception.

event = {"interface": "on_exec_error/1", "exc_type": str, "exc_message": str,
         "traceback_tail": str (last lines of the execution output), "code": str (the node's code)}

Return None to leave the execution output unchanged, or a short string; the adapter appends it to the
node's execution output as "[harness note] <string>", which AIDE's reviewer and debugger read.
H0 returns None for every event, i.e. stock AIDE behaviour."""


def diagnose(event):
    return None
