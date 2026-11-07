# Cast Hub terminal: status shows as a chip, ink text on a pastel highlight, the same
# as status chips everywhere else (brand/tokens.toml on_status on success/warning/error).
# The ANSI background slots carry the pastels (modes/console/CastHub.colorscheme):
#   41 error, 43 warning, 42 success, 44 accent; 30 is ink.
# Active only in the Cast Hub Konsole profiles, which set CASTHUB_TERMINAL=1.
# Installed to /etc/profile.d by tools/install-theme.sh.

if [ -n "${BASH_VERSION:-}" ] && [ -n "${PS1:-}" ] && [ "${CASTHUB_TERMINAL:-}" = 1 ] && [ -z "${__casthub_terminal:-}" ]; then
  __casthub_terminal=1

  # casthub_chip error|warn|ok|info TEXT : print a chip (for scripts and the shell)
  casthub_chip() {
    local bg
    case $1 in error) bg=41 ;; warn) bg=43 ;; ok) bg=42 ;; *) bg=44 ;; esac
    printf '\e[30;%sm %s \e[0m' "$bg" "$2"
  }

  # Prompt: a failed command leaves an error chip with its exit status before the prompt.
  __casthub_prompt_status() {
    local s=$?
    if [ "$s" -ne 0 ]; then
      PROMPT_START="\[\e[30;41m\] exit $s \[\e[0m\] "
    else
      PROMPT_START=""
    fi
    return "$s"
  }
  PROMPT_COMMAND=(__casthub_prompt_status "${PROMPT_COMMAND[@]}")
  # Fedora's colour prompt already expands PROMPT_START; any other prompt gets it in front.
  case $PS1 in *PROMPT_START*) ;; *) PS1='${PROMPT_START@P}'"$PS1" ;; esac

  # Compiler diagnostics (gcc, g++) as chips.
  export GCC_COLORS='error=30;41:warning=30;43:note=30;44:range1=32:range2=34:locus=01:quote=01:fixit-insert=32:fixit-delete=31'
fi
