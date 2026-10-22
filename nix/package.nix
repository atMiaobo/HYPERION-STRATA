{ lib
, stdenv
, meson
, ninja
, pkg-config
, wayland-scanner
, wrapGAppsHook4
, gobject-introspection
, wayland
, wayland-protocols
, libglvnd
, mpv-unwrapped
, gtk4
, python3
, tzdata
}:

let
  python = python3.withPackages (ps: [ ps.pygobject3 ]);
in
stdenv.mkDerivation {
  pname = "hyperion-rain";
  version = "0.3.0";

  src = lib.cleanSource ../.;

  nativeBuildInputs = [
    meson
    ninja
    pkg-config
    wayland-scanner
    wrapGAppsHook4
    gobject-introspection
  ];

  buildInputs = [
    wayland
    wayland-protocols
    libglvnd
    mpv-unwrapped
    gtk4
    python
  ];

  mesonFlags = [ "-Dsound=enabled" ];

  # Pin the GUI interpreter and its PyGObject module to the same Python.
  postPatch = ''
    substituteInPlace gui/hyperion-rain.py controller/hyperion-cadere.py \
      --replace-fail '#!/usr/bin/env python3' '#!${python}/bin/python3'
  '';

  preFixup = ''
    gappsWrapperArgs+=(--prefix PATH : "$out/bin" --set PYTHONTZPATH "${tzdata}/share/zoneinfo")
  '';

  doCheck = true;
  checkPhase = ''
    runHook preCheck
    PYTHONDONTWRITEBYTECODE=1 PYTHONTZPATH=${tzdata}/share/zoneinfo ${python}/bin/python3 -m unittest discover -s ../tests -v
    runHook postCheck
  '';

  meta = {
    description = "Composable Wayland effects, weather controller and GTK4 control panel";
    homepage = "https://nyabi.org";
    license = lib.licenses.mit;
    platforms = lib.platforms.linux;
    mainProgram = "hyperion-rain";
  };
}
