#!/bin/sh
set -eu

script_dir=$(CDPATH= cd "$(dirname "$0")" && pwd -P)
project_dir=$(CDPATH= cd "$script_dir/.." && pwd -P)
source_file="$script_dir/ww.fish"

if ! command -v fish >/dev/null 2>&1; then
    printf '%s\n' 'fish-install.sh: fish is required to install the PATH configuration.' >&2
    exit 1
fi

if [ -n "${XDG_CONFIG_HOME:-}" ]; then
    config_home=$XDG_CONFIG_HOME
else
    config_home=${HOME:?HOME is not set}/.config
fi

case $config_home in
    /*) ;;
    *) printf '%s\n' 'fish-install.sh: XDG_CONFIG_HOME must be an absolute path.' >&2; exit 1 ;;
esac

target_dir="$config_home/fish/completions"
target_file="$target_dir/ww.fish"
mkdir -p "$target_dir"

if [ -d "$target_file" ]; then
    printf 'fish-install.sh: %s is a directory.\n' "$target_file" >&2
    exit 1
fi

temporary_file=$(mktemp "$target_dir/.ww.fish.XXXXXX")
trap 'rm -f "$temporary_file"' 0
trap 'exit 1' 1 2 3 15
cp "$source_file" "$temporary_file"
chmod 644 "$temporary_file"
mv -f "$temporary_file" "$target_file"
trap - 0 1 2 3 15

printf 'Installed fish completion at %s\n' "$target_file"

path_dir="$config_home/fish/conf.d"
path_file="$path_dir/ww-path.fish"
mkdir -p "$path_dir"

if [ -d "$path_file" ]; then
    printf 'fish-install.sh: %s is a directory.\n' "$path_file" >&2
    exit 1
fi

escaped_project_dir=$(fish --no-config -c 'string escape --style=script -- $argv[1]' -- "$project_dir")
temporary_file=$(mktemp "$path_dir/.ww-path.fish.XXXXXX")
trap 'rm -f "$temporary_file"' 0
trap 'exit 1' 1 2 3 15
printf 'fish_add_path -- %s\n' "$escaped_project_dir" > "$temporary_file"
chmod 644 "$temporary_file"

mv -f "$temporary_file" "$path_file"
trap - 0 1 2 3 15

printf 'Added %s to fish PATH via %s\n' "$project_dir" "$path_file"
