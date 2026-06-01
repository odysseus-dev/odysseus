#compdef juniperus juniperus-backup juniperus-calendar juniperus-contacts juniperus-cookbook juniperus-docs juniperus-gallery juniperus-mail juniperus-mcp juniperus-memory juniperus-notes juniperus-personal juniperus-preset juniperus-research juniperus-sessions juniperus-signature juniperus-skills juniperus-tasks juniperus-theme juniperus-webhook
# Zsh tab-completion for the juniperus umbrella + sub-CLIs.
#
# Drop in any directory on $fpath, e.g.:
#     fpath=(/path/to/juniperus-ui/scripts/_completion $fpath)
#     autoload -U compinit; compinit
#
# Then `juniperus <tab>` completes subcommands; `juniperus mail <tab>`
# completes mail subcommands; `juniperus-mail <tab>` works the same.

_juniperus_scripts_dir() {
    local self="${(%):-%x}"
    while [[ -L "$self" ]]; do self="$(readlink "$self")"; done
    cd "${self:h}/.." && pwd
}

typeset -gA _juniperus_subs

_juniperus_refresh() {
    _juniperus_subs=()
    local dir="$(_juniperus_scripts_dir)"
    local py="$dir/../venv/bin/python"
    [[ -x "$py" ]] || py="$(command -v python3)"
    local f sub help_out commands
    for f in "$dir"/juniperus-*; do
        [[ -x "$f" ]] || continue
        case "$f" in
            *.bak|*.pyc|*.pre-*) continue ;;
        esac
        sub="${${f:t}#juniperus-}"
        help_out=$("$py" "$f" --help 2>/dev/null) || continue
        commands=$(echo "$help_out" | grep -oE '\{[a-z0-9_,-]+\}' | head -1 \
            | tr -d '{}' | tr ',' ' ')
        _juniperus_subs[$sub]="$commands"
    done
}

_juniperus() {
    [[ ${#_juniperus_subs} -eq 0 ]] && _juniperus_refresh

    local cmd="${words[1]}"

    if [[ "$cmd" == "juniperus" ]]; then
        if (( CURRENT == 2 )); then
            local -a subs=(${(k)_juniperus_subs} help)
            _describe 'subcommand' subs
            return
        fi
        local sub="${words[2]}"
        if [[ "$sub" == "help" ]] && (( CURRENT == 3 )); then
            local -a subs=(${(k)_juniperus_subs})
            _describe 'subcommand' subs
            return
        fi
        if (( CURRENT == 3 )); then
            local -a sc=(${(s/ /)_juniperus_subs[$sub]})
            _describe 'command' sc
            return
        fi
        return
    fi

    # juniperus-foo <tab>
    local sub="${cmd#juniperus-}"
    if (( CURRENT == 2 )); then
        local -a sc=(${(s/ /)_juniperus_subs[$sub]})
        _describe 'command' sc
        return
    fi
}

_juniperus "$@"
