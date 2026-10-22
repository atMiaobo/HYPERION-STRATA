"""Optional controller service. Network and Hyprland calls never run in the C render loop."""
from concurrent.futures import ThreadPoolExecutor
import copy
import json
import os
from pathlib import Path
import selectors
import shlex
import shutil
import signal
import socket
import subprocess
import time
from . import weather
from .screen import DEFAULTS, SPECS, ScreenBackend, number
from .storage import directory, read, save

AUTO_RANGES = {"auto-rain.opacity":(0,1),"auto-rain.uRain":(0,2),"auto-rain.uWind":(-.5,.5),"auto-rain.uLightning":(0,1),"auto-snow.opacity":(0,1),"auto-snow.uSnow":(0,2),"auto-snow.uWind":(-.5,.5),"auto-sunbeams.opacity":(0,1),"auto-sunbeams.uOriginX":(0,1),"auto-flare.opacity":(0,1),"auto-flare.uOriginX":(0,1)}
AUTO_EFFECTS = {"auto-rain": "rain", "auto-snow": "snow", "auto-sunbeams": "sunbeams", "auto-flare": "lens-flare"}


def renderer_socket():
    return os.environ.get("GL_OVERLAY_SOCKET", str(Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))/"gl-overlay.sock"))


def request(path, line):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(.5)
        client.connect(str(path))
        client.sendall((line+"\n").encode())
        client.shutdown(socket.SHUT_WR)
        data = bytearray()
        while chunk := client.recv(8192):
            data.extend(chunk)
            if len(data) > 1_000_000: raise ValueError("IPC response too large")
    result = data.decode()
    if result.startswith("error:"): raise ValueError(result.strip())
    return result


class Controller:
    def __init__(self, backend=None, clock=time.time, monotonic=time.monotonic):
        self.clock, self.monotonic = clock, monotonic
        self.config_path = directory("CONFIG")/"engine.json"
        self.cache_path = directory("CACHE")/"weather.json"
        self.config = dict(mode="manual", location=None, timezone="auto", interval=1200,
                           transition=30, screen=dict(DEFAULTS), screen_enabled=True, native=False, layers={}, outputs={}, overrides={}, profiles={})
        saved = read(self.config_path, {})
        if isinstance(saved, dict): self.config.update({k:v for k,v in saved.items() if k in self.config})
        if self.config["mode"] not in ("manual", "automatic", "hybrid"): self.config["mode"] = "manual"
        self.config["screen"] = dict(DEFAULTS, **{k:number(k,v) for k,v in self.config["screen"].items() if k in SPECS})
        self.backend = backend or ScreenBackend()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="weather")
        self.future = None
        self.fetch_location = None
        self.next_fetch = 0
        self.force_fetch = False
        self.failures = 0
        self.weather = None
        self.last_update = None
        self.error = None
        self.active_scene = "manual"
        self.transition = weather.Transition()
        self.known_layers = set()
        self.sent = {}
        self.last_scene = None
        self.pending_screen = None
        self.screen_due = 0
        self.last_screen_apply = -100
        self.last_tick = -100
        self.dirty = True
        self.next_scene = 0
        self.restore_cache()

    def persist(self): save(self.config_path, self.config)

    def restore_cache(self):
        cached = read(self.cache_path)
        try:
            if cached and cached["location"] == self.config["location"]:
                self.weather = weather.parse(cached["data"])
                self.last_update = float(cached["fetched_at"])
        except (KeyError, ValueError, TypeError): pass

    def send(self, line):
        return request(renderer_socket(), line)

    def ensure(self, ident, effect):
        if ident in self.known_layers: return
        try: self.send(f"layer add {ident} {effect}")
        except ValueError:
            state = json.loads(self.send("engine"))
            if ident not in {s["id"] for s in state["layers"]}: raise
        self.known_layers.add(ident)

    def changed(self, key, value, command):
        if self.sent.get(key) != value:
            self.send(command)
            self.sent[key] = value

    def apply_layers(self, automated):
        try:
            for monitor,values in self.config["outputs"].items():
                for name,value in values.items(): self.changed(("output",monitor,name),value,f"output {monitor} {name} {value}")
            for ident, spec in self.config["layers"].items():
                self.ensure(ident, spec["effect"])
                for key in ("enable", "opacity", "order", "output", "animate"):
                    if key in spec: self.changed((ident,key), spec[key], f"layer {key} {ident} {spec[key]}")
                for name,value in spec.get("params", {}).items():
                    self.changed((ident,name), value, f"layer set {ident} {name} {value}")
            for ident,effect in AUTO_EFFECTS.items():
                values = {k.split(".",1)[1]:v for k,v in automated.items() if k.startswith(ident+".")}
                if not values or (ident not in self.known_layers and values.get("opacity",0) <= 0): continue
                self.ensure(ident, effect)
                for name,value in values.items():
                    value = round(value,3)
                    command = f"layer opacity {ident} {value}" if name == "opacity" else f"layer set {ident} {name} {value}"
                    self.changed((ident,name), value, command)
                self.changed((ident,"enable"), 1, f"layer enable {ident} 1")
                # Rain automation should not add the original dream background.
                if ident == "auto-rain": self.changed((ident,"uDream"), 0, f"layer set {ident} uDream 0")
        except (OSError, ValueError) as exc:
            self.error = "renderer: "+str(exc)
            self.known_layers.clear()
            self.sent.clear()

    def queue_screen(self, values):
        if self.pending_screen is None: self.screen_due = self.monotonic()+.18
        self.pending_screen = values

    def apply_screen(self):
        values = self.pending_screen
        if values is None: return
        if self.config["native"]:
            try:
                self.backend.apply(values)
                self.pending_screen = None
                self.last_screen_apply = self.monotonic()
            except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
                self.backend.error = str(exc)
                self.pending_screen = None
        else:
            # Portable approximation: warmth/tint/vignette and independent grain.
            # Saturation, contrast, exposure and gamma require actual sampled pixels.
            layers = {"portable-grade": ("grade", {"uTemperature": values["temperature"], "uTintHue": values["tint_hue"], "uTintOpacity": values["tint_opacity"], "uVignette": values["vignette"]}),
                      "portable-grain": ("film-grain", {"uIntensity": values["grain"], "uSize": values["grain_size"], "uMono": values["grain_mono"], "uHue": values["grain_hue"], "uColour": values["grain_colour"]})}
            try:
                for ident,(effect,params) in layers.items():
                    on = any(params[k] != 0 for k in ("uTemperature","uTintOpacity","uVignette")) if effect == "grade" else params["uIntensity"] > 0
                    if not on and ident not in self.known_layers: continue
                    self.ensure(ident,effect)
                    self.changed((ident,"enable"),int(on),f"layer enable {ident} {int(on)}")
                    self.changed((ident,"order"),900,f"layer order {ident} 900")
                    for name,value in params.items(): self.changed((ident,name),value,f"layer set {ident} {name} {value}")
                self.pending_screen = None
            except (OSError,ValueError) as exc:
                self.error = "portable backend: "+str(exc)
                self.pending_screen = None

    def retarget(self):
        now = self.monotonic()
        if self.config["mode"] == "manual" or not self.weather:
            self.active_scene = "manual" if self.config["mode"] == "manual" else "waiting-for-location-or-weather"
            target = {k:0 if k.endswith(".opacity") else v for k,v in self.transition.target.items() if not k.startswith("screen.")}
        else:
            self.active_scene,target = weather.scene(self.weather,self.clock())
        if target != self.last_scene:
            self.transition.retarget(target,now,self.config["transition"])
            self.last_scene = target

    def tick(self):
        now = self.monotonic()
        if self.future and self.future.done():
            try:
                data = self.future.result()
                if self.fetch_location == self.config["location"]:
                    self.weather = weather.parse(data)
                    self.last_update = self.clock()
                    save(self.cache_path,dict(location=self.fetch_location,data=data,fetched_at=self.last_update))
                    self.failures,self.error = 0,None
                    self.retarget()
            except Exception as exc:
                self.failures += 1
                self.error = "weather: "+str(exc)
            self.future = None
            self.next_fetch = now+min(7200,self.config["interval"]*2**min(self.failures,3))
        if (self.config["mode"] != "manual" or self.force_fetch) and self.config["location"] and not self.future and now >= self.next_fetch:
            self.force_fetch = False
            self.fetch_location = copy.deepcopy(self.config["location"])
            self.future = self.pool.submit(weather.fetch,*self.fetch_location,self.config["timezone"])
        if now >= self.next_scene:
            self.retarget()
            self.next_scene = now+60
        if self.dirty or now-self.last_tick >= .1:
            self.dirty = False
            values = self.transition.value(now)
            manual = {"screen."+k:v for k,v in self.config["screen"].items()}
            effective = weather.priorities(manual,values,self.config["overrides"],self.config["mode"])
            if self.config["mode"] == "manual": effective.update({k:v for k,v in values.items() if k.startswith("auto-")})
            self.apply_layers(effective)
            screen = {k:round(effective.get("screen."+k,v),3) for k,v in DEFAULTS.items()} if self.config["screen_enabled"] else dict(DEFAULTS)
            if screen != getattr(self,"last_screen_values",None):
                self.queue_screen(screen)
                self.last_screen_values = screen
            self.last_tick = now
        if self.pending_screen is not None and now >= self.screen_due and now-self.last_screen_apply >= 2:
            self.apply_screen()

    def status(self):
        try: renderer=json.loads(self.send("engine"))
        except (OSError,ValueError): renderer=None
        stale = self.last_update is None or self.clock()-self.last_update > self.config["interval"]*2
        return dict(mode=self.config["mode"],renderer=renderer,scene=self.active_scene,provider="Open-Meteo",location=self.config["location"],
                    timezone=self.weather.timezone if self.weather else self.config["timezone"],last_update=self.last_update,stale=stale,
                    condition=self.weather.code if self.weather else None,refreshing=self.future is not None,
                    next_refresh_seconds=max(0,self.next_fetch-self.monotonic()),error=self.error,
                    transition=self.config["transition"],screen=self.config["screen"],screen_enabled=self.config["screen_enabled"],layers=self.config["layers"],
                    overrides=self.config["overrides"],backend=self.backend.status(),
                    portable_unsupported=["saturation","contrast","exposure","gamma","refraction","frost","blur","haze"],
                    profiles=list(self.config["profiles"]))

    def command(self, line):
        args = shlex.split(line)
        if not args: raise ValueError("empty command")
        cmd,*a = args
        if cmd in ("status","capabilities","diagnostics"): return self.status()
        if cmd == "mode" and len(a)==1 and a[0] in ("manual","automatic","hybrid"):
            self.config["mode"] = a[0]; self.next_scene=0
        elif cmd == "location" and len(a) in (2,3):
            self.config["location"] = [weather.finite(a[0],-90,90),weather.finite(a[1],-180,180)]
            if len(a)==3:
                if a[2] != "auto": weather.ZoneInfo(a[2])
                self.config["timezone"] = a[2]
            self.weather,self.last_update,self.next_fetch = None,None,0
            self.last_scene=None; self.next_scene=0
        elif cmd == "weather" and a == ["refresh"]:
            if not self.config["location"]: raise ValueError("configure location before refreshing weather")
            self.next_fetch=0; self.force_fetch=True
        elif cmd == "weather" and len(a)==2 and a[0] == "interval": self.config["interval"] = weather.finite(a[1],900,1800)
        elif cmd == "transition" and len(a)==1: self.config["transition"] = weather.finite(a[0],0,300)
        elif cmd == "override" and len(a)==2:
            key,value=a
            if value == "clear": self.config["overrides"].pop(key,None)
            else:
                if key.startswith("screen."): value=number(key[7:],value)
                elif key in AUTO_RANGES: value=weather.finite(value,*AUTO_RANGES[key])
                else: raise ValueError("override must target a screen field or auto-layer field")
                self.config["overrides"][key]=float(value)
        elif cmd == "screen" and len(a)==3 and a[0] == "set":
            self.config["screen"][a[1]]=number(a[1],a[2])
            if self.config["mode"] == "hybrid": self.config["overrides"]["screen."+a[1]]=number(a[1],a[2])
        elif cmd == "screen" and a in (["enable"],["claim","replace"]):
            replace=a == ["claim","replace"]
            self.backend.apply(self.config["screen"],replace=replace)
            self.config["native"]=True
            self.config["screen_enabled"]=True
            for ident in ("portable-grade","portable-grain"):
                if ident in self.known_layers: self.changed((ident,"enable"),0,f"layer enable {ident} 0")
        elif cmd == "screen" and a in (["disable"],["portable"]):
            self.backend.restore(); self.config["native"]=False
            self.config["screen_enabled"] = a != ["disable"]
            self.last_screen_values=None
        elif cmd == "screen" and a == ["recover"]: self.backend.recover()
        elif cmd == "screen" and len(a)==2 and a[0] == "colour-manager-opt-in":
            if a[1] not in ("0","1"): raise ValueError("expected 0 or 1")
            self.backend.allow_colour_manager = a[1] == "1"
        elif cmd == "profile" and len(a) in (2,3):
            action,name=a[:2]
            scope=a[2] if len(a)==3 else "all"
            if not name or len(name)>64: raise ValueError("invalid profile name")
            if action == "save":
                scopes={"manual":("screen","screen_enabled","layers","outputs"),"environment":("mode","overrides","transition","location","timezone","interval")}
                if scope not in ("all","manual","environment"): raise ValueError("profile scope: all|manual|environment")
                keys=scopes["manual"]+scopes["environment"] if scope=="all" else scopes[scope]
                self.config["profiles"][name] = {k:copy.deepcopy(self.config[k]) for k in keys}
            elif action == "load":
                profile=copy.deepcopy(self.config["profiles"][name])
                if "layers" in profile:
                    for ident in set(self.config["layers"])-set(profile["layers"]):
                        self.send(f"layer remove {ident}"); self.known_layers.discard(ident)
                self.config.update(profile); self.sent.clear(); self.next_scene=0; self.next_fetch=0
            elif action == "merge":
                profile=self.config["profiles"][name]
                for key,value in profile.items():
                    if key in ("screen","layers","overrides","outputs"): self.config[key].update(copy.deepcopy(value))
                    else: self.config[key]=copy.deepcopy(value)
                self.next_scene=0; self.next_fetch=0
                self.sent.clear()
            elif action == "delete": del self.config["profiles"][name]
            else: raise ValueError("profile save|load|merge|delete NAME")
        elif cmd == "layer" and len(a)>=2:
            action,ident,*rest=a
            if ident.startswith(("auto-","portable-")): raise ValueError("reserved layer id; use hybrid override for automated fields")
            if action == "add" and len(rest)==1:
                if len(self.config["layers"])>=10: raise ValueError("manual layer limit is 10 (reserved automation slots)")
                self.send(line)
                self.known_layers.add(ident)
                self.config["layers"][ident]=dict(effect=rest[0],enable=1,opacity=1,params={})
            elif action == "remove" and not rest:
                self.send(line); self.config["layers"].pop(ident,None); self.known_layers.discard(ident)
            elif action in ("enable","opacity","order","output","animate") and len(rest)==1:
                self.send(line)
                value=rest[0] if action=="output" else float(rest[0])
                self.config["layers"][ident][action]=value
                self.sent[(ident,action)]=value
            elif action == "set" and len(rest)==2:
                self.send(line)
                self.config["layers"][ident]["params"][rest[0]]=float(rest[1])
                self.sent[(ident,rest[0])]=float(rest[1])
            else: raise ValueError("invalid layer command")
            if action == "remove": self.sent={key:value for key,value in self.sent.items() if key[0]!=ident}
        elif cmd == "renderer":
            result=self.send(" ".join(a)).strip()
            if len(a)==4 and a[0]=="output":
                self.config["outputs"].setdefault(a[1],{})[a[2]]=float(a[3]); self.persist()
            return result
        else: raise ValueError("commands: status; mode manual|automatic|hybrid; location LAT LON [TZ]; weather refresh|interval; transition SECONDS; screen set KEY VALUE|enable|disable|claim replace|recover; override KEY VALUE|clear; layer ...; profile save|load|merge|delete NAME; renderer COMMAND")
        self.dirty=True
        self.persist()
        return {"ok":True}

    def close(self):
        try: self.backend.restore()
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
            self.backend.error = str(exc)  # retain recovery journal on restoration failure
        finally: self.pool.shutdown(wait=False,cancel_futures=True)


def serve(start_overlay=False):
    root=directory("RUNTIME"); path=root/"controller.sock"
    # Exclusive socket ownership; refuse another live service.
    if path.exists():
        try: request(path,"status")
        except (OSError,ValueError): path.unlink()
        else: raise RuntimeError("controller already running")
    server=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
    server.bind(str(path)); path.chmod(0o600); server.listen(8); server.setblocking(False)
    selector=selectors.DefaultSelector(); selector.register(server,selectors.EVENT_READ)
    controller=Controller(); running=True; overlay=None
    if start_overlay:
        try: request(renderer_socket(),"status")
        except OSError:
            binary=shutil.which("gl-overlay")
            if not binary:
                for candidate in (Path(__file__).resolve().parents[2]/"build/gl-overlay",Path(__file__).resolve().parents[4]/"bin/gl-overlay"):
                    if candidate.exists(): binary=str(candidate); break
            if not binary: raise RuntimeError("gl-overlay not found; build or install it first")
            overlay=subprocess.Popen([binary,"-f","30"])
            for _ in range(30):
                try: controller.send("base 0"); break
                except OSError: time.sleep(.05)
    def stop(*_):
        nonlocal running
        running=False
    signal.signal(signal.SIGTERM,stop); signal.signal(signal.SIGINT,stop)
    try:
        while running:
            active=controller.transition.active(controller.monotonic())
            timeout=.1 if active else (1 if controller.future or controller.pending_screen else 30)
            for key,_ in selector.select(timeout):
                client,_=key.fileobj.accept()
                with client:
                    client.settimeout(.1)
                    try:
                        data=bytearray()
                        while b"\n" not in data and len(data)<8192:
                            chunk=client.recv(8192-len(data))
                            if not chunk: break
                            data.extend(chunk)
                        if len(data)>=8192: raise ValueError("command too long")
                        line=data.decode().strip()
                        if line=="quit": running=False; result={"ok":True}
                        else: result=controller.command(line)
                    except Exception as exc: result={"error":str(exc)}
                    try: client.sendall((json.dumps(result,allow_nan=False)+"\n").encode())
                    except OSError: pass
            controller.tick()
    finally:
        controller.close(); selector.close(); server.close(); path.unlink(missing_ok=True)
        if overlay: overlay.terminate(); overlay.wait(timeout=5)
