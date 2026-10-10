{
  description = "Kerberos — a serif for reading on paper and on screens";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };
        lib = pkgs.lib;
        python = pkgs.python312;

        # Native libraries binary wheels load at runtime: libstdc++/zlib on
        # Linux, and fribidi/harfbuzz/raqm for Pillow's complex text layout.
        nativeLibs = with pkgs; [ stdenv.cc.cc.lib zlib fribidi harfbuzz libraqm freetype ];

        # fontspector, the Google Fonts QA tool, from its upstream release
        # binaries (it is not packaged in nixpkgs).
        fontspectorVersion = "1.9.0";
        fontspectorTargets = {
          aarch64-darwin = { target = "aarch64-apple-darwin"; hash = "sha256-DTTV+FrHou/lcVfDH0VOVqdZ7fiIh5tTr1C3a7HWthc="; };
          x86_64-darwin = { target = "x86_64-apple-darwin"; hash = "sha256-WBnm1hRmpO3bw81A1iQXkPUL2Oo+cXbnL1RvnwFPTzs="; };
          x86_64-linux = { target = "x86_64-unknown-linux-gnu"; hash = "sha256-DUp/vFd/f5mghN7Cp6zBP2T5I8vkTA48Xj12RpeGuOI="; };
        };
        fontspector = lib.optional (fontspectorTargets ? ${system}) (
          let t = fontspectorTargets.${system}; in
          pkgs.stdenv.mkDerivation {
            pname = "fontspector";
            version = fontspectorVersion;
            src = pkgs.fetchurl {
              url = "https://github.com/fonttools/fontspector/releases/download/fontspector-v${fontspectorVersion}/fontspector-${fontspectorVersion}-${t.target}.tar.gz";
              inherit (t) hash;
            };
            sourceRoot = ".";
            nativeBuildInputs = lib.optionals pkgs.stdenv.hostPlatform.isLinux [ pkgs.autoPatchelfHook ];
            buildInputs = lib.optionals pkgs.stdenv.hostPlatform.isLinux (with pkgs; [ openssl zlib stdenv.cc.cc.lib ]);
            installPhase = ''
              install -Dm755 fontspector-*/fontspector $out/bin/fontspector
            '';
          });
      in
      {
        devShells.default = pkgs.mkShell {
          packages = (with pkgs; [
            python
            uv
            git
            gnumake
            coreutils
            findutils
          ]) ++ fontspector;

          # Python dependencies are pinned in pyproject.toml / uv.lock and
          # installed by uv into .venv using the Nix-provided interpreter.
          shellHook = ''
            export UV_PYTHON="${python}/bin/python3.12"
            export UV_PYTHON_DOWNLOADS=never
            export UV_PROJECT_ENVIRONMENT="$PWD/.venv"
            export DYLD_FALLBACK_LIBRARY_PATH="${lib.makeLibraryPath nativeLibs}''${DYLD_FALLBACK_LIBRARY_PATH:+:$DYLD_FALLBACK_LIBRARY_PATH}"
            export LD_LIBRARY_PATH="${lib.makeLibraryPath nativeLibs}''${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
            export SOURCE_DATE_EPOCH="''${SOURCE_DATE_EPOCH:-$(git log -1 --format=%ct 2>/dev/null || date +%s)}"
          '';
        };
      });
}
