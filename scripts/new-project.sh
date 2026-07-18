#!/bin/sh
# Stamp out a new project from template/: new-project.sh <name> <target-dir>
# <name> must be a valid identifier-ish slug (used in DB names, package names, env
# defaults): lowercase letters, digits, hyphens; it is underscored where needed.
set -e

name=$1
target=$2
if [ -z "$name" ] || [ -z "$target" ]; then
  echo "usage: $0 <project-name> <target-dir>" >&2
  exit 2
fi
case "$name" in
  *[!a-z0-9-]*) echo "project name must be lowercase letters, digits, hyphens" >&2; exit 2 ;;
esac
if [ -e "$target" ] && [ -n "$(ls -A "$target" 2>/dev/null)" ]; then
  echo "target '$target' exists and is not empty" >&2
  exit 2
fi

template_dir=$(cd "$(dirname "$0")/../template" && pwd)
mkdir -p "$target"
cp -R "$template_dir/." "$target/"

# Substitute the project name everywhere (python3 for portable in-place edits;
# underscores in DB/user names where hyphens are invalid).
python3 - "$name" "$target" <<'EOF'
import os, sys

name, target = sys.argv[1], sys.argv[2]
underscored = name.replace("-", "_")

for root, dirs, files in os.walk(target):
    dirs[:] = [d for d in dirs if d not in {".git", "node_modules", ".venv"}]
    for fname in files:
        path = os.path.join(root, fname)
        try:
            text = open(path, encoding="utf-8").read()
        except (UnicodeDecodeError, OSError):
            continue
        if "__PROJECT__" not in text:
            continue
        # DB names/users can't contain hyphens; display/package contexts can.
        replaced = []
        for line in text.split("\n"):
            token = underscored if (
                "POSTGRES" in line or "pg_isready" in line or "_test" in line
                or "postgres_data" in line
            ) else name
            replaced.append(line.replace("__PROJECT__", token))
        open(path, "w", encoding="utf-8").write("\n".join(replaced))
print("substituted __PROJECT__ ->", name)
EOF

chmod +x "$target/backend/entrypoint.sh" "$target/scripts/dev.sh" "$target/scripts/devctl.sh"
(cd "$target" && git init -q && git add -A && git commit -qm "Bootstrap from django-react-foundation template")

cat <<DONE

Project '$name' created at $target
Next:
  cd $target
  make install
  make dev        # backend :8000, frontend :5173
  make test
DONE
