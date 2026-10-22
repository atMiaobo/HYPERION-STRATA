import copy
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"controller"))
from cadere import weather, screen
from cadere.storage import atomic, read

FIXTURE=json.loads((ROOT/"tests/fixtures/clear.json").read_text())

class WeatherTests(unittest.TestCase):
    def test_conditions_and_clock(self):
        w=weather.parse(FIXTURE)
        self.assertEqual(weather.scene(w,w.time)[0],"sunny-daytime")
        self.assertEqual(weather.scene(w,w.sunset-900)[0],"golden-hour")
        self.assertEqual(weather.scene(w,w.sunset+900)[0],"nighttime")
        self.assertEqual(weather.scene(w,w.sunrise+5000)[0],"clear-morning")
        for code,key,expected in [(95,"rain","stormy"),(73,"snowfall","snowy"),(63,"rain","rainy"),(3,"cloud_cover","overcast")]:
            d=copy.deepcopy(FIXTURE); d["current"]["weather_code"]=code; d["current"][key]=90 if key=="cloud_cover" else 1
            self.assertEqual(weather.scene(weather.parse(d),w.time)[0],expected)
        self.assertLess(weather.scene(w,w.time)[1]["auto-rain.uWind"],0)

    def test_nulls_bad_units_and_nan_rejected(self):
        for name,value in [("wind_speed_10m",float("nan")),("cloud_cover",101),("rain",None),("is_day",.5),("weather_code",42)]:
            d=copy.deepcopy(FIXTURE);d["current"][name]=value
            with self.assertRaises((ValueError,TypeError)): weather.parse(d)
        d=copy.deepcopy(FIXTURE);d["current_units"]["wind_speed_10m"]="km/h"
        with self.assertRaises(ValueError): weather.parse(d)

    def test_fetch_contract_and_json_errors(self):
        class Response:
            def __init__(self,body): self.body=body
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self,limit): return self.body[:limit]
        calls=[]
        def opener(request,timeout):
            calls.append((request.full_url,timeout))
            return Response(json.dumps(FIXTURE).encode())
        self.assertEqual(weather.fetch(25,121,"Asia/Taipei",opener=opener),FIXTURE)
        self.assertIn("wind_speed_unit=ms",calls[0][0])
        self.assertIn("timeformat=unixtime",calls[0][0])
        self.assertEqual(calls[0][1],8)
        with self.assertRaises(ValueError): weather.fetch(25,121,opener=lambda *a,**k:Response(b"bad json"))

    def test_transition_interrupt_and_overrides(self):
        t=weather.Transition({"a":0});t.retarget({"a":10},100,10)
        self.assertEqual(t.value(105)["a"],5)
        t.retarget({"a":0},105,10)
        self.assertEqual(t.value(105)["a"],5)
        self.assertEqual(t.value(115)["a"],0)
        self.assertEqual(weather.priorities({"x":1},{"x":2},{"x":3},"manual")["x"],1)
        self.assertEqual(weather.priorities({"x":1},{"x":2},{"x":3},"automatic")["x"],2)
        self.assertEqual(weather.priorities({"x":1},{"x":2},{"x":3},"hybrid")["x"],3)

    def test_atomic_cache_and_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"cache";atomic(p,"broken")
            self.assertIsNone(read(p))
            atomic(p,json.dumps(FIXTURE));self.assertEqual(read(p),FIXTURE)
            self.assertEqual(p.stat().st_mode & 0o777,0o600)

class ScreenTests(unittest.TestCase):
    def test_bounded_literals_and_damage_safe(self):
        source=screen.shader({"temperature":.4,"refraction":.01,"grain":.03})
        self.assertIn("texture(tex",source)
        self.assertIn("textureSize(tex",source)
        self.assertNotIn("uniform float time",source)
        self.assertNotIn("fullSize",source)
        for key,value in [("temperature",float("inf")),("grain",.5),("garbage",1),("gamma",0),("temperature","0; void bad(){}")]:
            with self.assertRaises(ValueError): screen.shader({key:value})

    def test_ownership_restore_and_external_changes(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{"XDG_RUNTIME_DIR":tmp}):
            state={"path":"user.frag"}
            def run(args):
                if args[1]=="version":return json.dumps({"tag":"v0.55.4","commit":"test"})
                if args[1]=="getoption":return json.dumps({"str":state["path"]})
                if args[1]=="keyword":state["path"]=args[-1];return "ok"
                raise RuntimeError("absent")
            b=screen.ScreenBackend(runner=run,validator=lambda path:None)
            with self.assertRaises(RuntimeError): b.apply({})
            b.apply({"temperature":.3},replace=True);self.assertTrue(b.owned)
            b.restore();self.assertEqual(state["path"],"user.frag")
            b.apply({},replace=True);state["path"]="someone-else.frag"
            with self.assertRaises(RuntimeError): b.apply({"temperature":.5})
            b.restore();self.assertEqual(state["path"],"someone-else.frag")

    def test_failed_validation_does_not_replace_shader(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{"XDG_RUNTIME_DIR":tmp}):
            state={"path":""}
            def run(args):
                if args[1]=="version":return '{"tag":"v0.55.4"}'
                if args[1]=="getoption":return json.dumps({"str":state["path"]})
                raise RuntimeError("unexpected mutation")
            def invalid(path):raise RuntimeError("compile failed")
            b=screen.ScreenBackend(runner=run,validator=invalid)
            with self.assertRaises(RuntimeError):b.apply({})
            self.assertFalse(b.owned);self.assertEqual(state["path"],"")

@unittest.skipUnless(os.environ.get("GL_OVERLAY_STILL"),"set GL_OVERLAY_STILL for GLES tests")
class NativeRenderingTests(unittest.TestCase):
    def render(self,values,source=None):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/"test.frag").write_text(source if source is not None else screen.shader(values))
            # Known test texture, deliberately NOT represented as live desktop capture.
            (p/"reference.ppm").write_bytes(b"P6\n128 96\n255\n"+bytes(v for y in range(96) for x in range(128) for v in ((x//8%2)*255,(y//8%2)*255,x*2)))
            subprocess.run([os.environ["GL_OVERLAY_STILL"],"-N",str(p/"test.frag"),"-B",str(p/"reference.ppm"),"-w","128","-h","96","-o",str(p/"out.pam")],check=True,capture_output=True)
            return (p/"out.pam").read_bytes().split(b"ENDHDR\n")[1]

    def test_pass_through_and_real_texture_displacement(self):
        plain=self.render({});bent=self.render({"refraction":.015})
        self.assertNotEqual(plain,bent)
        self.assertTrue(all(a==255 for a in bent[3::4]))
        expected=bytes(v for y in reversed(range(96)) for x in range(128) for v in ((x//8%2)*255,(y//8%2)*255,x*2,255))
        self.assertEqual(plain,expected)
        for values in ({"temperature":.5,"gamma":1.2},{"grain":.1,"grain_mono":0},
                       {"frost":1,"blur":3},{"haze":.005,"dispersion":1,"refraction":.01}):
            self.assertNotEqual(plain,self.render(values))

    def test_simple_static_refraction_example(self):
        self.assertNotEqual(self.render({}),self.render({},source=(ROOT/"data/screen/static-refraction.frag").read_text()))

    def test_layer_order_premultiplication_and_zero_opacity(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            (p/"red.frag").write_text("void mainImage(out vec4 c,in vec2 p){c=vec4(1,0,0,.5);}")
            (p/"blue.frag").write_text("void mainImage(out vec4 c,in vec2 p){c=vec4(0,0,1,.5);}")
            args=[os.environ["GL_OVERLAY_STILL"],"-s",str(p/"red.frag"),"-l","blue:"+str(p/"blue.frag"),"-w","4","-h","4","-o",str(p/"out.pam")]
            subprocess.run(args,check=True,capture_output=True)
            pixel=(p/"out.pam").read_bytes().split(b"ENDHDR\n")[1][:4]
            for actual,expected in zip(pixel,(85,0,170,191)):
                self.assertLessEqual(abs(actual-expected),2)
            subprocess.run(args+["-P","blue:opacity=0"],check=True,capture_output=True)
            pixel=(p/"out.pam").read_bytes().split(b"ENDHDR\n")[1][:4]
            self.assertEqual(tuple(pixel[:3]),(255,0,0))
            self.assertLessEqual(abs(pixel[3]-128),1)

    def test_all_overlay_effects_and_combinations(self):
        binary=os.environ["GL_OVERLAY_STILL"]
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("sunbeams","lens-flare","film-grain","grade"):
                subprocess.run([binary,"-s",name,"-w","320","-h","200","-o",str(Path(tmp)/"out.pam")],cwd=ROOT,check=True,capture_output=True)
            subprocess.run([binary,"-s","sakura","-l","light:sunbeams","-l","grain:film-grain","-l","wash:grade","-P","wash:uTemperature=.5","-w","320","-h","200","-o",str(Path(tmp)/"out.pam")],cwd=ROOT,check=True,capture_output=True)

class ServiceTests(unittest.TestCase):
    def make_controller(self,tmp):
        from cadere.service import Controller
        class Backend:
            error=None
            def status(self):return {"moving_refraction":False}
            def restore(self):pass
        return Controller(backend=Backend(),clock=lambda:1792728000,monotonic=lambda:100)

    def test_persistence_manual_return_and_hybrid_override(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{"XDG_CONFIG_HOME":tmp,"XDG_CACHE_HOME":tmp,"XDG_RUNTIME_DIR":tmp}):
            c=self.make_controller(tmp)
            c.send=lambda line:'ok\n'
            c.command("transition 0")
            c.command("mode hybrid")
            c.command("override screen.temperature 0.3")
            data=copy.deepcopy(FIXTURE);data["current"]["weather_code"]=63;data["current"]["rain"]=1
            c.weather=weather.parse(data);c.retarget();c.tick()
            self.assertEqual(c.last_screen_values["temperature"],.3)
            self.assertIn("auto-rain",c.known_layers)
            c.command("mode manual");c.retarget();c.tick()
            # Explicit opacity zero survives the switch to manual, rather than
            # leaving the last automated weather visible indefinitely.
            self.assertEqual(c.transition.target["auto-rain.opacity"],0)
            c.persist();c.close()
            restored=self.make_controller(tmp)
            self.assertEqual(restored.config["overrides"]["screen.temperature"],.3)
            self.assertEqual(restored.config["mode"],"manual")
            restored.close()

    def test_rapid_controls_profiles_and_clean_disable(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{"XDG_CONFIG_HOME":tmp,"XDG_CACHE_HOME":tmp,"XDG_RUNTIME_DIR":tmp}):
            c=self.make_controller(tmp);c.send=lambda line:'ok\n'
            c.command("screen set temperature 0.4");c.tick()
            c.command("screen set temperature 0.6");c.tick()
            self.assertEqual(c.last_screen_values["temperature"],.6)
            c.command("profile save warm manual")
            c.command("screen disable");c.tick()
            self.assertEqual(c.last_screen_values["temperature"],0)
            self.assertEqual(c.config["screen"]["temperature"],.6)
            c.command("profile load warm");c.tick()
            self.assertEqual(c.last_screen_values["temperature"],.6)
            c.close()

    def test_fetch_failure_backoff_preserves_cached_scene(self):
        from concurrent.futures import Future
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{"XDG_CONFIG_HOME":tmp,"XDG_CACHE_HOME":tmp,"XDG_RUNTIME_DIR":tmp}):
            c=self.make_controller(tmp);c.send=lambda line:'ok\n'
            c.weather=weather.parse(FIXTURE);c.last_update=1792720000
            future=Future();future.set_exception(ValueError("bad JSON"));c.future=future
            c.tick()
            self.assertIsNotNone(c.weather)
            self.assertTrue(c.status()["stale"])
            self.assertEqual(c.failures,1)
            self.assertGreater(c.next_fetch,100+1200)
            c.close()
