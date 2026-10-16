function __ww_at_command
    test (count (commandline -opc)) -eq 1
end

function __ww_plugin_subcommand
    set -l words (commandline -opc)
    test (count $words) -eq 2; and test "$words[2]" = plugin
end

function __ww_plugin_install_argument
    set -l words (commandline -opc)
    test (count $words) -eq 3; and test "$words[2]" = plugin; and test "$words[3]" = install
end

function __ww_plugin_install_candidates
    test -z (commandline -ct); or return
    printf '%s\n' \
        'slug-or-search	e.g. akismet or cache' \
        'slug-or-search version	e.g. akismet 5.0' \
        '--help	Show this help'
end

function __ww_expand_plugin_install_template
    set -l previous_status $status
    if commandline --paging-mode
        return $previous_status
    end
    set -l words (commandline -op)
    if test (count $words) -ne 4; or not string match -qr '(^|/)ww$' -- "$words[1]"
        return $previous_status
    end
    if test "$words[2]" != plugin; or test "$words[3]" != install; or test "$words[4]" != 'slug-or-search version'
        return $previous_status
    end

    # Preserve the rest of the process, including quoted paths and whitespace.
    set -l process (commandline -p)
    set -l expanded "$process"
    for template in 'slug-or-search\ version' "'slug-or-search version'" '"slug-or-search version"'
        set expanded (string replace -- "$template" 'slug-or-search version' "$expanded")
    end
    if test "$expanded" != "$process"
        commandline -rp -- "$expanded"
        commandline -f repaint
    end
    return $previous_status
end

function __ww_install_template_bindings
    # Append to the effective binding so custom commands and vi mode changes survive.
    for mode in (bind --list-modes)
        for key in enter ctrl-m ctrl-j
            set -l binding (bind --user --silent --mode "$mode" "$key")
            if test -z "$binding"
                set binding (bind --preset --silent --mode "$mode" "$key")
            end
            test -n "$binding"; or continue
            set -l binding_args
            printf '%s\n' "$binding" | read --local --tokenize --array binding_args
            contains -- __ww_expand_plugin_install_template $binding_args; and continue
            if test "$binding_args[2]" = --preset
                set binding_args[2] --user
            end
            $binding_args __ww_expand_plugin_install_template
        end
    end
end

if status is-interactive
    __ww_install_template_bindings
end

function __ww_wp_argument
    set -l words (commandline -opc)
    test (count $words) -ge 2; and test "$words[2]" = wp
end

function __ww_wp_candidates
    set -l line (commandline -cp)
    # Match one shell token, including quotes/escaped spaces in an executable path.
    # Keep the cursor-truncated WP arguments exactly as typed.
    set -l executable_token "(?:[^\\s'\"\\\\]|\\\\.|'[^']*'|\"(?:[^\"\\\\]|\\\\.)*\")+"
    set -l wp_line (string replace -r "^\\s*$executable_token\\s+wp\\b" 'wp' -- "$line")
    ww __complete_wp "$wp_line"
end

function __ww_trick_slug
    set -l words (commandline -opc)
    test (count $words) -eq 2; and test "$words[2]" = tricks
end

function __ww_running_site
    set -l words (commandline -opc)
    test (count $words) -eq 2; and contains -- "$words[2]" stop destroy
end

complete -c ww -f -n __ww_at_command -a create -d 'Create a WordPress site'
complete -c ww -f -n __ww_at_command -a list -d 'List ww sites'
complete -c ww -f -n __ww_at_command -a start -d 'Start this site'
complete -c ww -f -n __ww_at_command -a stop -d 'Stop this site'
complete -c ww -f -n __ww_at_command -a stop-all -d 'Stop all running ww sites'
complete -c ww -f -n __ww_at_command -a status -d 'Show instance overview'
complete -c ww -f -n __ww_at_command -a info -d 'Show instance overview (alias of status)'
complete -c ww -f -n __ww_at_command -a reset-db -d 'Reinstall the database'
complete -c ww -f -n __ww_at_command -a destroy -d 'Destroy site data'
complete -c ww -f -n __ww_at_command -a wp -d 'Run WP-CLI'
complete -c ww -f -n __ww_at_command -a plugin -d 'Manage plugins'
complete -c ww -f -n __ww_at_command -a tricks -d 'Run a reusable WordPress task'
complete -c ww -f -n __ww_at_command -a help -d 'Show help'
complete -c ww -f -n __ww_plugin_subcommand -a install -d 'Install and activate a plugin'
complete -c ww -f -n __ww_plugin_subcommand -a list -d 'Manage installed plugins'
complete -c ww -f -k -n __ww_plugin_install_argument -a '(__ww_plugin_install_candidates)'
complete -c ww -f -n __ww_plugin_install_argument -l help -d 'Show this help'
complete -c ww -f -n __ww_trick_slug -a '(ww __complete_tricks)'
complete -c ww -f -n __ww_running_site -a '(ww __complete_running)'
complete -c ww -f -n __ww_wp_argument -a '(__ww_wp_candidates)'
