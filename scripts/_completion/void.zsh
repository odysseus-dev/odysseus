#compdef void void-backup void-calendar void-contacts void-cookbook void-docs void-gallery void-mail void-mcp void-memory void-notes void-personal void-preset void-research void-sessions void-signature void-skills void-tasks void-theme void-webhook
# Zsh tab-completion for the void umbrella + sub-CLIs.
#
# Drop in any directory on $fpath, e.g.:
#     fpath=(/path/to/void-ui/scripts/_completion $fpath)
#     autoload -U compinit; compinit
#
# Then `void <tab>` completes subcommands; `void mail <tab>`
# completes mail subcommands; `void-mail <tab>` works the same.

_void_scripts_dir() {
    local self="${(%):-%x}"
    while [[ -L "$self" ]]; do self="$(readlink "$self")"; done
    cd "${self:h}/.." && pwd
}

typeset -gA _void_subs

_void_refresh() {
    _void_subs=()
    local dir="$(_void_scripts_dir)"
    local py="$dir/../venv/bin/python"
    [[ -x "$py" ]] || py="$(command -v python3)"
    local f sub help_out commands
    for f in "$dir"/void-*; do
        [[ -x "$f" ]] || continue
        case "$f" in
            *.bak|*.pyc|*.pre-*) continue ;;
        esac
        sub="${${f:t}#void-}"
        help_out=$("$py" "$f" --help 2>/dev/null) || continue
        commands=$(echo "$help_out" | grep -oE '\{[a-z0-9_,-]+\}' | head -1 \
            | tr -d '{}' | tr ',' ' ')
        _void_subs[$sub]="$commands"
    done
}

_void() {
    [[ ${#_void_subs} -eq 0 ]] && _void_refresh

    local cmd="${words[1]}"

    if [[ "$cmd" == "void" ]]; then
        if (( CURRENT == 2 )); then
            local -a subs=(${(k)_void_subs} help)
            _describe 'subcommand' subs
            return
        fi
        local sub="${words[2]}"
        if [[ "$sub" == "help" ]] && (( CURRENT == 3 )); then
            local -a subs=(${(k)_void_subs})
            _describe 'subcommand' subs
            return
        fi
        if (( CURRENT == 3 )); then
            local -a sc=(${(s/ /)_void_subs[$sub]})
            _describe 'command' sc
            return
        fi
        return
    fi

    # void-foo <tab>
    local sub="${cmd#void-}"
    if (( CURRENT == 2 )); then
        local -a sc=(${(s/ /)_void_subs[$sub]})
        _describe 'command' sc
        return
    fi
}

_void "$@"
