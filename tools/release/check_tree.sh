#!/bin/sh
set -eu

cd "$(dirname "$0")/../.."
fail=0

for f in $(git ls-files '*.sh'); do
    case "$(head -n 1 "$f")" in
        *bash*) bash -n "$f" ;;
        *zsh*) zsh -n "$f" ;;
        *) sh -n "$f" ;;
    esac || { echo "check_tree: shell syntax error in $f" >&2; fail=1; }
done

mail=$(git grep -n -I -E '[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}' -- . ':!third_party' ':!tools/kit/licences' \
    | grep -v -E '@(example\.(test|com|org)|invalid|users\.noreply\.github\.com)|@[0-9]x\.png' || true)
[ -z "$mail" ] || { echo "check_tree: e-mail addresses outside the allow list:" >&2; echo "$mail" >&2; fail=1; }

home=$(git grep -n -I -E '/Users/[A-Za-z0-9._@-]+/' -- . ':!third_party' \
    | grep -v -E '/Users/(x|someone|cutter|other|foo)/' || true)
[ -z "$home" ] || { echo "check_tree: home folder paths:" >&2; echo "$home" >&2; fail=1; }

[ "$fail" = 0 ] && echo "check_tree: ok"
exit "$fail"
