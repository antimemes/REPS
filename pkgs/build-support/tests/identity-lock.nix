{ identityLock, identityImport }:
let
  # Compare bytes, not parsed TOML: all non-dev text is identity.
  check = name: input: expected:
    let actual = builtins.readFile (identityLock name (builtins.toFile "input.lock" input));
    in if actual == expected then true
       else throw "identityLock ${name}: expected ${builtins.toJSON expected}, got ${builtins.toJSON actual}";
  first = "[[package]]\nname = \"first\"\nversion = \"1\"\n\n";
  second = "[[package]]\nname = \"second\"\nversion = \"2\"\n";
  dev = "[package.metadata.requires-dev]\ndev = [{ name = \"pytest\" }]\n\n";
  adjacentDev = "[package.metadata.requires-dev]\ndev = []\n";
  metadata = "[package.metadata]\nrequires-dist = [{ name = \"runtime\" }]\n";
  optional = "[package.optional-dependencies]\nextra = [{ name = \"runtime-extra\" }]\n";
  noDev = "# Preserve every byte\n\n" + first + metadata + "\n\n" + second + "\n";
  valueText = first + ''
    url = "https://example.invalid/[package.metadata.requires-dev]"
    note = "[package.metadata.requires-dev]"
    # [package.metadata.requires-dev]
  '';
  # fromJSON rejects store references; inspect the identity text, not its context.
  imported = builtins.fromJSON (builtins.unsafeDiscardStringContext
    (identityImport "identity-directory-test" ./fixtures));
  tree = builtins.readDir imported.tree;
  subtree = builtins.readDir (imported.tree + "/nested");
in {
  between-packages = check "between-packages" (first + dev + second) (first + second);
  followed-by-metadata = check "followed-by-metadata" (first + adjacentDev + metadata) (first + metadata);
  followed-by-optional = check "followed-by-optional" (first + adjacentDev + optional) (first + optional);
  multiline-array = check "multiline-array" (first + ''
    [package.metadata.requires-dev]
    dev = [
        { name = "pytest" },
        { name = "pytest-xdist" },
    ]

  '' + second) (first + second);
  last-table = check "last-table" (first + dev) "[[package]]\nname = \"first\"\nversion = \"1\"\n";
  last-table-without-newline = check "last-table-without-newline"
    (first + "[package.metadata.requires-dev]\ndev = []") "[[package]]\nname = \"first\"\nversion = \"1\"\n";
  no-dev-tables = check "no-dev-tables" noDev noDev;
  no-dev-without-newline = check "no-dev-without-newline" "version = 1" "version = 1";
  header-in-value-or-comment = check "header-in-value-or-comment" valueText valueText;
  nested-locks =
    assert builtins.attrNames imported.locks == [ "nested/uv.lock" "uv.lock" ];
    assert builtins.readFile imported.locks."uv.lock" == "[[package]]\nname = \"root\"\n";
    assert builtins.readFile imported.locks."nested/uv.lock" ==
      "[[package]]\nname = \"nested\"\n\n[package.metadata]\nrequires-dist = [{ name = \"retained\" }]\n";
    assert tree == { "keep.txt" = "regular"; nested = "directory"; };
    assert subtree == { };
    assert builtins.readFile (imported.tree + "/keep.txt") == "Non-lock content stays in the tree.\n";
    true;
}
