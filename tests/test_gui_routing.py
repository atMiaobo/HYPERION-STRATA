"""Exercise real panel controller methods with IPC doubles; no GTK display needed."""
import ast
from pathlib import Path
import unittest
from unittest.mock import Mock

ROOT=Path(__file__).resolve().parents[1]

class PanelRoutingTests(unittest.TestCase):
    def test_layer_controls_and_legacy_global_opacity(self):
        tree=ast.parse((ROOT/"gui/hyperion-rain.py").read_text())
        app=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=="RainPanel")
        methods=[n for n in app.body if isinstance(n,ast.FunctionDef) and n.name in ("set_param","set_layer_opacity")]
        cls=ast.ClassDef(name="Panel",bases=[],keywords=[],body=methods,decorator_list=[])
        ast.fix_missing_locations(cls)
        calls=[]
        scope={"ctl":calls.append}
        exec(compile(ast.Module(body=[cls],type_ignores=[]),"panel-routing","exec"),scope)
        panel=scope["Panel"]();panel.online=True;panel.composable=True;panel.effect="sakura"
        panel.layer_command=lambda eid,line:calls.append(line)
        panel.save_prefs=Mock();panel.refresh_hypr=Mock()
        store={}
        panel.set_param("uWind",.4,store)
        panel.set_param("opacity",.5,store)
        panel.set_layer_opacity("layerOpacity",.7,store)
        self.assertEqual(calls,["layer set sakura uWind 0.4","set opacity 0.5","layer opacity sakura 0.7"])
        self.assertEqual(store["uWind"],.4)
        self.assertEqual(panel.save_prefs.call_count,3)
        panel.composable=False;panel.set_param("uWind",.2,store)
        self.assertEqual(calls[-1],"set uWind 0.2")

    def test_controller_commands_do_not_go_to_renderer_socket(self):
        tree=ast.parse((ROOT/"gui/hyperion-rain.py").read_text())
        app=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=="RainPanel")
        method=next(n for n in app.body if isinstance(n,ast.FunctionDef) and n.name=="cmd")
        cls=ast.ClassDef(name="Panel",bases=[],keywords=[],body=[method],decorator_list=[])
        ast.fix_missing_locations(cls)
        calls=[];scope={"ctl":lambda line:calls.append(line) or "ok\n"}
        exec(compile(ast.Module(body=[cls],type_ignores=[]),"command-routing","exec"),scope)
        panel=scope["Panel"]();panel.control_call=Mock();panel.log=Mock();panel.poll=Mock()
        panel.cmd("screen set temperature 0.4")
        self.assertEqual(panel.control_call.call_args.args[0],"screen set temperature 0.4")
        self.assertFalse(calls)
        self.assertEqual(panel.cmd("layer list"),"ok\n")
        self.assertEqual(calls,["layer list"])
