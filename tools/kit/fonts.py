import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import vpk_list

FONT_DIR = "materials/fonts/"
ALIASES = (
    ("percolator_expert_35_1000_000", "vamp_mainfont_35_1000_000"),
    ("percolator_expert_26_1000_000", "vamp_mainfont_26_1000_000"),
    ("copperplate_gothic_bt_18_1000_000", "vamp_small_18_1000_000"),
    ("dominican_22_1000_000", "vamp_plain_22_1000_000"),
)


def copy_aliases(game, overlay, log):
    out = Path(overlay) / "files/Vampire" / FONT_DIR
    try:
        entries = {e.path.lower(): e for e in reversed(vpk_list.all_entries(str(game)))}
    except (OSError, ValueError) as e:
        log("kit: font alias skipped, cannot read game packs: %s" % e)
        return 0
    made = 0
    for alias, source in ALIASES:
        e = entries.get(FONT_DIR + source + ".fnt")
        if e is None:
            log("kit: font alias %s skipped: %s not in the game packs" % (alias, source))
            continue
        target = out / (alias + ".fnt")
        try:
            data = vpk_list.read_entry(str(game), e)
            if target.is_file() and target.read_bytes() == data:
                continue
            out.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, target)
        except OSError as err:
            log("kit: font alias %s skipped: %s" % (alias, err))
            continue
        log("kit: font alias %s -> %s" % (alias, source))
        made += 1
    return made
