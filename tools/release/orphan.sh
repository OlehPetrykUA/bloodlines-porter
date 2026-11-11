#!/bin/sh
set -eu

usage() {
    cat <<'EOF'
usage: tools/release/orphan.sh REF NEW-BRANCH

Creates NEW-BRANCH in this repository as a single commit with no parent, holding the tree of REF.
The working tree, the index and every other branch stay as they are, and nothing is pushed.

BP_BANNED is required: an extended regex, matched without case, of strings that must never be
published (private e-mail addresses, private names). Keep it in your shell, never in the repository.
It refuses when the tree (file contents or file names) or the author identity matches BP_BANNED,
when NEW-BRANCH exists, or when REF is not a commit.

The commit is authored as "Bloodlines Porter <noreply@invalid>". Set BP_AUTHOR_NAME and
BP_AUTHOR_EMAIL to publish under another identity.
EOF
}

[ $# -eq 2 ] || { usage >&2; exit 2; }
REF=$1
BRANCH=$2
REPO=$(cd "$(dirname "$0")/../.." && pwd)
g() { git -C "$REPO" "$@"; }
AUTHOR_NAME=${BP_AUTHOR_NAME:-Bloodlines Porter}
AUTHOR_EMAIL=${BP_AUTHOR_EMAIL:-noreply@invalid}
BANNED=${BP_BANNED:-}

fail() { echo "orphan: $*" >&2; exit 1; }

[ -n "$BANNED" ] || fail "set BP_BANNED to the regex of strings that must not be published"

g rev-parse --verify --quiet "$REF^{commit}" >/dev/null || fail "$REF is not a commit"
g check-ref-format "refs/heads/$BRANCH" || fail "$BRANCH is not a branch name"
if g show-ref --verify --quiet "refs/heads/$BRANCH"; then fail "branch $BRANCH exists"; fi

TREE=$(g rev-parse "$REF^{tree}")

hits=$(g grep -a -i -l -E "$BANNED" "$TREE" -- 2>/dev/null || true)
[ -z "$hits" ] || fail "the tree of $REF holds a banned string in: $(echo "$hits" | sed 's/^[0-9a-f]*://' | head -5 | tr '\n' ' ')"
names=$(g ls-tree -r --name-only "$TREE" | grep -i -E "$BANNED" || true)
[ -z "$names" ] || fail "the tree of $REF has a banned string in the file name $(echo "$names" | head -1)"
echo "$AUTHOR_NAME $AUTHOR_EMAIL" | grep -q -i -E "$BANNED" && fail "the author identity holds a banned string"

COMMIT=$(GIT_AUTHOR_NAME=$AUTHOR_NAME GIT_AUTHOR_EMAIL=$AUTHOR_EMAIL GIT_COMMITTER_NAME=$AUTHOR_NAME GIT_COMMITTER_EMAIL=$AUTHOR_EMAIL \
    git -C "$REPO" commit-tree -m "chore: initial import" "$TREE")

[ "$(g rev-list --count "$COMMIT")" = 1 ] || fail "the new commit has history"
g update-ref "refs/heads/$BRANCH" "$COMMIT"
echo "orphan: $BRANCH is $COMMIT, 1 commit, tree $TREE (from $REF)"
