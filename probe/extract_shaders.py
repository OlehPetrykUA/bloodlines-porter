import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import vpk_list


def main(game, out):
    n = 0
    for e in vpk_list.all_entries(game):
        if e.dir_only or not e.path.startswith("shaders/") or not e.path.endswith(".vcs"):
            continue
        dst = os.path.join(out, e.path[len("shaders/"):])
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as f:
            f.write(vpk_list.read_entry(game, e))
        n += 1
    print("extract: %d .vcs files into %s" % (n, out))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
