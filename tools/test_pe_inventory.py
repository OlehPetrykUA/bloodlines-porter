import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import devpaths
import pe_inventory as pi

GAME = devpaths.GAME


def get(rel):
    return pi.inspect(os.path.join(GAME, rel), rel)


exe = get("vampire.exe")
assert exe["machine"] == 0x14C
assert set(exe["imports"]) == {"kernel32.dll"}

dll = get("Vampire/dlls/vampire.dll")
assert len(dll["rtti"]) == 823
assert ".?AV?$CEntityDataInstantiator@UModelWidthScale@@@@" in get("Vampire/cl_dlls/client.dll")["rtti"]
assert dll["pdb"].lower().endswith("vampire.pdb"), dll["pdb"]
assert "VEngineServer014" in dll["ifaces"]
assert "VMaterialSystem074" in get("Bin/MaterialSystem.dll")["ifaces"]
assert "QAEXHAAH000" not in get("Bin/tier0.dll")["ifaces"]
assert "CreateInterface" in dll["exports"]

users = []
for root, _, files in os.walk(GAME):
    for f in files:
        if f.lower().endswith((".exe", ".dll", ".asi")):
            p = os.path.join(root, f)
            if "d3d9.dll" in pi.inspect(p, os.path.relpath(p, GAME))["imports"]:
                users.append(f.lower())
assert users == ["shaderapidx9.dll"], users
print("ok")
