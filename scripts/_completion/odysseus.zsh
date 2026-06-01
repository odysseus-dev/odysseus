#compdef odysseus odysseus-backup odysseus-calendar odysseus-contacts odysseus-cookbook odysseus-docs odysseus-gallery odysseus-mail odysseus-mcp odysseus-memory odysseus-notes odysseus-personal odysseus-preset odysseus-research odysseus-sessions odysseus-signature odysseus-skills odysseus-tasks odysseus-theme odysseus-webhook
# Zsh tab-completion for the odysseus umbrella + sub-CLIs.
#
# Drop in any directory on $fpath, e.g.:
#     fpath=(/path/to/odysseus-ui/scripts/_completion $fpath)
#     autoload -U compinit; compinit
#
# Then `odysseus <tab>` completes subcommands; `odysseus mail <tab>`
# completes mail subcommands; `odysseus-mail <tab>` works the same.

_odysseus_scripts_dir() {
    local self="${(%):-%x}"
    while [[ -L "$self" ]]; do self="$(readlink "$self")"; done
    cd "${self:h}/.." && pwd
}

typeset -gA _odysseus_subs

_odysseus_refresh() {
    _odysseus_subs=()
    local dir="$(_odysseus_scripts_dir)"
    local py="$dir/../.venv/bin/python"
    [[ -x "$py" ]] || py="$(command -v python3)"
    local f sub help_out commands
    for f in "$dir"/odysseus-*; do
        [[ -x "$f" ]] || continue
        case "$f" in
            *.bak|*.pyc|*.pre-*) continue ;;
        esac
        sub="${${f:t}#odysseus-}"
        help_out=$("$py" "$f" --help 2>/dev/null) || continue
        commands=$(echo "$help_out" | grep -oE '\{[a-z0-9_,-]+\}' | head -1 \
            | tr -d '{}' | tr ',' ' ')
        _odysseus_subs[$sub]="$commands"
    done
}

_odysseus() {
    [[ ${#_odysseus_subs} -eq 0 ]] && _odysseus_refresh

    local cmd="${words[1]}"

    if [[ "$cmd" == "odysseus" ]]; then
        if (( CURRENT == 2 )); then
            local -a subs=(${(k)_odysseus_subs} help)
            _describe 'subcommand' subs
            return
        fi
        local sub="${words[2]}"
        if [[ "$sub" == "help" ]] && (( CURRENT == 3 )); then
            local -a subs=(${(k)_odysseus_subs})
            _describe 'subcommand' subs
            return
        fi
        if (( CURRENT == 3 )); then
            local -a sc=(${(s/ /)_odysseus_subs[$sub]})
            _describe 'command' sc
            return
        fi
        return
    fi

    # odysseus-foo <tab>
    local sub="${cmd#odysseus-}"
    if (( CURRENT == 2 )); then
        local -a sc=(${(s/ /)_odysseus_subs[$sub]})
        _describe 'command' sc
        return
    fi
}

_odysseus "$@"
