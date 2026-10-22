{
  description = "HYPERION-CADERE composable Wayland effects and weather engine";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
    in
    {
      packages = forAllSystems (system:
        let
          pkgs = import nixpkgs { inherit system; };
          hyperion-rain = pkgs.callPackage ./nix/package.nix { };
        in
        {
          inherit hyperion-rain;
          hyperion-cadere = hyperion-rain;
          default = hyperion-rain;
        });

      apps = forAllSystems (system: {
        default = self.apps.${system}.hyperion-rain;
        hyperion-rain = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/hyperion-rain";
          meta.description = "Open the HYPERION-CADERE control panel";
        };
        hyperion-cadere = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/hyperion-cadere";
        };
        gl-overlay = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/gl-overlay";
          meta.description = "Run or control the Wayland shader overlay";
        };
      });

      devShells = forAllSystems (system:
        let
          pkgs = import nixpkgs { inherit system; };
        in
        {
          default = pkgs.mkShell {
            inputsFrom = [ self.packages.${system}.default ];
            shellHook = ''export PYTHONTZPATH="${pkgs.tzdata}/share/zoneinfo"'';
          };
        });

      checks = forAllSystems (system: {
        package = self.packages.${system}.default;
      });
    };
}
