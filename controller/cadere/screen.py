"""Single-pass static native processing. Never capture, use time, or change damage tracking."""
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
from .storage import atomic, directory, read, save

# min, max, default. Only finite numeric literals can enter generated GLSL.
SPECS = {
    "temperature": (-1, 1, 0), "saturation": (0, 2, 1), "contrast": (.25, 2, 1),
    "exposure": (-2, 2, 0), "gamma": (.5, 2, 1), "vignette": (0, .8, 0),
    "tint_hue": (0, 1, .08), "tint_opacity": (0, .3, 0),
    "grain": (0, .15, 0), "grain_size": (1, 8, 1), "grain_mono": (0, 1, 1),
    "grain_hue": (0, 1, .08), "grain_colour": (0, 1, 0),
    "refraction": (0, .025, 0), "dispersion": (0, 1, 0),
    "frost": (0, 1, 0), "blur": (0, 3, 0), "haze": (0, .01, 0),
}
DEFAULTS = {k: v[2] for k, v in SPECS.items()}


def number(name, value):
    if name not in SPECS: raise ValueError(f"unknown screen setting: {name}")
    value = float(value)
    lo, hi, _ = SPECS[name]
    if not math.isfinite(value) or not lo <= value <= hi:
        raise ValueError(f"{name}: expected {lo}..{hi}")
    return value


def shader(values):
    v = dict(DEFAULTS)
    v.update({k: number(k, x) for k, x in values.items()})
    constants = "\n".join(f"const float {k} = {x:.8f};" for k, x in v.items())
    return """#version 300 es
precision highp float;
in vec2 v_texcoord;
uniform sampler2D tex;
out vec4 fragColor;
""" + constants + r'''
float hash(vec2 p) { return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453); }
vec3 hue(float h) { return .5+.5*cos(6.2831853*(h+vec3(0,.333333,.666667))); }
vec2 bounded(vec2 p) { vec2 e=.5/vec2(textureSize(tex,0)); return clamp(p,e,1.-e); }
vec3 sampleAt(vec2 p) { return texture(tex,bounded(p)).rgb; }
void main() {
    vec2 size=vec2(textureSize(tex,0)), uv=v_texcoord;
    vec2 aspect=vec2(size.x/size.y,1.);
    vec2 cell=floor(uv*aspect*14.);
    vec2 local=fract(uv*aspect*14.)-.5;
    vec2 center=(vec2(hash(cell),hash(cell+3.7))-.5)*.25;
    vec2 d=(local-center)/vec2(.31,.39);
    float rr=dot(d,d);
    float mask=(1.-smoothstep(.7,1.,rr))*step(.40,hash(cell+11.));
    // Static droplet height field normal: real offsets into the composited tex.
    vec2 slope=-2.*d*mask;
    float edge=1.-smoothstep(.0,.16,min(min(uv.x,1.-uv.x),min(uv.y,1.-uv.y)));
    vec2 crystal=vec2(sin(uv.y*173.+uv.x*93.),cos(uv.x*197.-uv.y*71.));
    vec2 offset=refraction*slope/aspect + frost*edge*crystal*.003;
    offset+=haze*sin(uv.yx*vec2(39.,23.))*sin(uv.xy*vec2(17.,31.));
    vec2 p=bounded(uv+offset);
    vec3 c=sampleAt(p);
    if (dispersion>0.) {
        c.r=sampleAt(p+offset*dispersion*.12).r;
        c.b=sampleAt(p-offset*dispersion*.12).b;
    }
    if (blur*frost>0.) {
        vec2 b=vec2(blur)/size;
        vec3 soft=(c*2.+sampleAt(p+vec2(b.x,0))+sampleAt(p-vec2(b.x,0))
                     +sampleAt(p+vec2(0,b.y))+sampleAt(p-vec2(0,b.y)))/6.;
        c=mix(c,soft,edge*frost);
    }
    // Subtle Fresnel rim, tied to this STATIC mask, not to moving overlay rain.
    c+=refraction*mask*pow(clamp(rr,0.,1.),3.)*.7;
    c*=vec3(1.+temperature*.16,1.+temperature*.025,1.-temperature*.16)*exp2(exposure);
    float l=dot(c,vec3(.2126,.7152,.0722));
    c=mix(vec3(l),c,saturation);
    c=(c-.5)*contrast+.5;
    c=mix(c,hue(tint_hue),tint_opacity);
    c*=1.-vignette*smoothstep(.15,.72,length((uv-.5)*vec2(1.,.85)));
    vec2 gp=floor(uv*size/grain_size);
    float mono=hash(gp)-.5;
    vec3 rgb=vec3(mono,hash(gp+19.)-.5,hash(gp-31.)-.5);
    rgb=mix(rgb,vec3(mono),grain_mono);
    rgb*=mix(vec3(1.),hue(grain_hue)*2.,grain_colour);
    c=pow(clamp(c+rgb*grain,0.,1.),vec3(1./gamma));
    fragColor=vec4(c,texture(tex,bounded(uv)).a);
}
'''


def run(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=3)
    if result.returncode: raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "command failed")
    return result.stdout.strip()


class ScreenBackend:
    def __init__(self, runner=run, validator=None):
        self.run = runner
        self.validator = validator or self.validate
        self.owned = False
        self.original = None
        self.current = None
        self.current_values = {}
        self.error = None
        self.version = None
        self.available = False
        self.root = directory("RUNTIME")
        self.journal = self.root/"screen-owner.json"
        self.colour_manager = False
        self.allow_colour_manager = False
        self.detect()

    def option(self):
        return json.loads(self.run(["hyprctl", "getoption", "decoration:screen_shader", "-j"]))["str"]

    def detect(self):
        try:
            self.version = json.loads(self.run(["hyprctl", "version", "-j"]))
            tag = self.version.get("tag", "").lstrip("v")
            self.available = tag == "0.55.4"
            if not self.available: self.error = "native contract audited for Hyprland 0.55.4 only; unknown version uses portable overlay"
            self.option()
            try: self.colour_manager = bool(self.run(["pgrep", "-x", "hyprsunset"]))
            except (RuntimeError, OSError): pass
        except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as exc:
            self.available = False
            self.error = str(exc)

    @staticmethod
    def validate(path):
        binary = shutil.which("gl-overlay-still")
        if not binary:
            for candidate in (Path(__file__).resolve().parents[2]/"build/gl-overlay-still", Path(__file__).resolve().parents[4]/"bin/gl-overlay-still"):
                if candidate.exists(): binary = str(candidate); break
        if not binary: raise RuntimeError("gl-overlay-still is required for GLES compiler validation")
        result = subprocess.run([binary, "-N", str(path), "-w", "8", "-h", "8", "-o", "/dev/null"], capture_output=True, text=True, timeout=15)
        if result.returncode: raise RuntimeError("native GLSL validation failed: "+result.stderr[-2000:])

    def apply(self, values, replace=False):
        if not self.available: raise RuntimeError(self.error or "native backend unavailable")
        if self.colour_manager and not self.allow_colour_manager and (values.get("temperature", 0) or values.get("gamma", 1) != 1):
            raise RuntimeError("hyprsunset is running; temperature/gamma ownership needs explicit colour-manager opt-in")
        original = self.option()
        if self.owned and original != self.current:
            self.owned = False
            raise RuntimeError("screen shader ownership changed externally; leaving it untouched")
        if not self.owned and original not in ("", "[[EMPTY]]") and not replace:
            raise RuntimeError("another screen_shader is active; use screen claim replace explicitly")
        content = shader(values)
        target = self.root/("screen-"+hashlib.sha256(content.encode()).hexdigest()[:16]+".frag")
        if self.owned and str(target) == self.current: return
        atomic(target, content)
        self.validator(target)
        if self.option() != original:
            self.owned = False
            raise RuntimeError("screen shader changed during validation; leaving it untouched")
        if not self.owned:
            self.original = original
            save(self.journal, {"original": original, "owned": str(target)})
        previous = self.current if self.owned else original
        try:
            reply = self.run(["hyprctl", "keyword", "decoration:screen_shader", str(target)])
            if reply != "ok" or self.option() != str(target): raise RuntimeError("Hyprland did not accept generated shader")
        except Exception:
            self.run(["hyprctl", "keyword", "decoration:screen_shader", previous if previous != "[[EMPTY]]" else ""])
            raise
        self.current = str(target)
        self.current_values = dict(DEFAULTS, **values)
        self.owned = True
        self.error = None
        save(self.journal, {"original": self.original, "owned": self.current})
        atomic(self.root/"last-good.frag", content)
        # Bound transient artifacts, retaining the active and last-known-good shader.
        old = sorted(self.root.glob("screen-*.frag"), key=lambda p: p.stat().st_mtime, reverse=True)
        for path in old[4:]:
            if str(path) != self.current: path.unlink(missing_ok=True)

    def restore(self):
        if self.owned and self.option() == self.current:
            self.run(["hyprctl", "keyword", "decoration:screen_shader", "" if self.original == "[[EMPTY]]" else self.original])
        self.owned = False
        self.current = None
        self.journal.unlink(missing_ok=True)

    def recover(self):
        saved = read(self.journal)
        if not saved: raise ValueError("no recovery journal")
        if self.option() != saved["owned"]: raise ValueError("ownership changed; refusing recovery")
        self.original, self.current, self.owned = saved["original"], saved["owned"], True
        self.restore()

    def status(self):
        return dict(backend="hyprland-native" if self.owned else "portable-overlay", available=self.available,
                    owned=self.owned, version=self.version, error=self.error, hyprsunset=self.colour_manager,
                    hyprsunset_installed=bool(shutil.which("hyprsunset")),
                    real_sampling=self.owned, static_refraction_supported=self.available,
                    static_refraction=self.owned and any(self.current_values.get(k,0)>0 for k in ("refraction","frost","haze")), moving_refraction=False,
                    temporal_native_grain=False, time_uniform=False, fullSize_required=False,
                    displacement_damage_expansion_verified=False,
                    plugin={"enabled": False, "reason": "no verified version-matched render-stage backend; see docs/BACKENDS.md"})
