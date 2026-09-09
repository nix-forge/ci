{
  description = "Shared nix-forge CI validation tools";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  outputs =
    { nixpkgs, ... }:
    let
      inherit (nixpkgs) lib;
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "aarch64-darwin"
      ];
      perSystem = lib.genAttrs systems (
        system:
        let
          pkgs = import nixpkgs { inherit system; };
          python = pkgs.python3.withPackages (p: [ p.pyyaml ]);
          contractsScript = pkgs.writers.writePython3Bin "check-workflow-contracts" {
            libraries = [ pkgs.python3Packages.pyyaml ];
            # Ruff owns formatting; retain the writer's other lint checks.
            flakeIgnore = [
              "E501"
              "W503"
            ];
          } ./scripts/check-workflow-contracts.py;
          modules = pkgs.linkFarm "workflow-inventory-module" {
            "repository_inventory.py" = ./scripts/repository_inventory.py;
          };
          contracts = pkgs.writeShellApplication {
            name = "check-workflow-contracts";
            runtimeEnv.PYTHONPATH = toString modules;
            text = ''exec ${lib.getExe contractsScript} "$@"'';
          };
          policy = pkgs.linkFarm "workflow-validation-policy" {
            ".yamllint.yml" = ./.yamllint.yml;
            "scripts/repository_inventory.py" = ./scripts/repository_inventory.py;
          };
          validator = pkgs.writeShellApplication {
            name = "validate-workflows";
            runtimeInputs = [
              pkgs.actionlint
              pkgs.shellcheck
              pkgs.zizmor
              pkgs.yamllint
              pkgs.coreutils
              python
            ];
            inheritPath = false;
            runtimeEnv = {
              WORKFLOW_LIBRARY = toString policy;
              WORKFLOW_CONTRACT_CHECKER = lib.getExe contracts;
            };
            text = builtins.readFile ./scripts/check-workflows.sh;
          };
          tooling = [
            pkgs.actionlint
            pkgs.zizmor
            pkgs.yamllint
            pkgs.ruff
            pkgs.shellcheck
            pkgs.gitleaks
            pkgs.git
            python
            validator
          ];
          source = lib.cleanSource ./.;
        in
        {
          packages = {
            validate-workflows = validator;
            default = validator;
          };
          checks.validation =
            pkgs.runCommand "ci-validation"
              {
                nativeBuildInputs = tooling;
                WORKFLOW_LIBRARY = toString policy;
                WORKFLOW_CONTRACT_CHECKER = lib.getExe contracts;
              }
              ''
                cp -R ${source}/. .
                chmod -R u+w .
                export PYTHONDONTWRITEBYTECODE=1
                bash scripts/check.sh
                touch "$out"
              '';
          devShells.default = pkgs.mkShellNoCC { packages = tooling; };
        }
      );
    in
    {
      packages = lib.mapAttrs (_: value: value.packages) perSystem;
      checks = lib.mapAttrs (_: value: value.checks) perSystem;
      devShells = lib.mapAttrs (_: value: value.devShells) perSystem;
    };
}
