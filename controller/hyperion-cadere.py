#!/usr/bin/env python3
"""Optional weather / native screen controller; no GTK dependency."""
import argparse
import json
from pathlib import Path
import sys

# Installed modules live next to other app data, while development uses controller/.
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/"share/gl-overlay/controller"))
from cadere.service import request, serve
from cadere.screen import shader, DEFAULTS
from cadere.storage import directory

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument("--start-overlay",action="store_true")
parser.add_argument("command",nargs="?",default="status")
parser.add_argument("arguments",nargs=argparse.REMAINDER)
a=parser.parse_args()
try:
    if a.command=="serve": serve(a.start_overlay)
    elif a.command=="shader":
        values={}
        for arg in a.arguments:
            key,value=arg.split("=",1); values[key]=float(value)
        print(shader(values),end="")
    else:
        import shlex
        response=request(directory("RUNTIME")/"controller.sock",shlex.join([a.command]+a.arguments))
        print(response,end="")
        if "error" in json.loads(response): sys.exit(1)
except (OSError,ValueError,RuntimeError) as exc:
    print(str(exc),file=sys.stderr); sys.exit(1)
