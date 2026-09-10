# Snapshot file
# Unset all aliases to avoid conflicts with functions
# Functions
gawklibpath_append () 
{ 
    [ -z "$AWKLIBPATH" ] && AWKLIBPATH=`gawk 'BEGIN {print ENVIRON["AWKLIBPATH"]}'`;
    export AWKLIBPATH="$AWKLIBPATH:$*"
}
gawklibpath_default () 
{ 
    unset AWKLIBPATH;
    export AWKLIBPATH=`gawk 'BEGIN {print ENVIRON["AWKLIBPATH"]}'`
}
gawklibpath_prepend () 
{ 
    [ -z "$AWKLIBPATH" ] && AWKLIBPATH=`gawk 'BEGIN {print ENVIRON["AWKLIBPATH"]}'`;
    export AWKLIBPATH="$*:$AWKLIBPATH"
}
gawkpath_append () 
{ 
    [ -z "$AWKPATH" ] && AWKPATH=`gawk 'BEGIN {print ENVIRON["AWKPATH"]}'`;
    export AWKPATH="$AWKPATH:$*"
}
gawkpath_default () 
{ 
    unset AWKPATH;
    export AWKPATH=`gawk 'BEGIN {print ENVIRON["AWKPATH"]}'`
}
gawkpath_prepend () 
{ 
    [ -z "$AWKPATH" ] && AWKPATH=`gawk 'BEGIN {print ENVIRON["AWKPATH"]}'`;
    export AWKPATH="$*:$AWKPATH"
}

# setopts 3
set -o braceexpand
set -o hashall
set -o interactive-comments

# aliases 0

# exports 50
declare -x AMENT_PREFIX_PATH="/opt/ros/galactic"
declare -x ANTHROPIC_AUTH_TOKEN="sk-50JVqIVDvLepHLK8bKLiAkFhZTysEu9NcOFF6ogxzFBYgmae"
declare -x ANTHROPIC_BASE_URL="https://a-ocnfniawgw.cn-shanghai.fcapp.run"
declare -x CLAUDE_CODE_SSE_PORT="13548"
declare -x CODEX_HOME="/home/heol/AI/own_implement/RL_Paper_Reproduction/.codex"
declare -x CODEX_MANAGED_BY_NPM="1"
declare -x CODEX_MANAGED_PACKAGE_ROOT="/usr/lib/node_modules/@openai/codex"
declare -x COLORTERM="truecolor"
declare -x CUDA_HOME="/usr/local/cuda/"
declare -x DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/1000/bus"
declare -x DISPLAY=":0"
declare -x GIT_ASKPASS="/home/heol/.vscode-server/bin/520fb30b2d3d324b4cb2342f6e88e2cd93751de1/extensions/git/dist/askpass.sh"
declare -x HF_ENDPOINT="https://hf-mirror.com"
declare -x HOME="/home/heol"
declare -x HOSTTYPE="x86_64"
declare -x LANG="C.UTF-8"
declare -x LD_LIBRARY_PATH="/usr/local/cuda/lib64:/usr/local/cuda/lib64:/opt/ros/galactic/opt/yaml_cpp_vendor/lib:/opt/ros/galactic/opt/rviz_ogre_vendor/lib:/opt/ros/galactic/lib/x86_64-linux-gnu:/opt/ros/galactic/lib"
declare -x LESSCLOSE="/usr/bin/lesspipe %s %s"
declare -x LESSOPEN="| /usr/bin/lesspipe %s"
declare -x LOGNAME="heol"
declare -x LS_COLORS="rs=0:di=01;34:ln=01;36:mh=00:pi=40;33:so=01;35:do=01;35:bd=40;33;01:cd=40;33;01:or=40;31;01:mi=00:su=37;41:sg=30;43:ca=30;41:tw=30;42:ow=34;42:st=37;44:ex=01;32:*.tar=01;31:*.tgz=01;31:*.arc=01;31:*.arj=01;31:*.taz=01;31:*.lha=01;31:*.lz4=01;31:*.lzh=01;31:*.lzma=01;31:*.tlz=01;31:*.txz=01;31:*.tzo=01;31:*.t7z=01;31:*.zip=01;31:*.z=01;31:*.dz=01;31:*.gz=01;31:*.lrz=01;31:*.lz=01;31:*.lzo=01;31:*.xz=01;31:*.zst=01;31:*.tzst=01;31:*.bz2=01;31:*.bz=01;31:*.tbz=01;31:*.tbz2=01;31:*.tz=01;31:*.deb=01;31:*.rpm=01;31:*.jar=01;31:*.war=01;31:*.ear=01;31:*.sar=01;31:*.rar=01;31:*.alz=01;31:*.ace=01;31:*.zoo=01;31:*.cpio=01;31:*.7z=01;31:*.rz=01;31:*.cab=01;31:*.wim=01;31:*.swm=01;31:*.dwm=01;31:*.esd=01;31:*.jpg=01;35:*.jpeg=01;35:*.mjpg=01;35:*.mjpeg=01;35:*.gif=01;35:*.bmp=01;35:*.pbm=01;35:*.pgm=01;35:*.ppm=01;35:*.tga=01;35:*.xbm=01;35:*.xpm=01;35:*.tif=01;35:*.tiff=01;35:*.png=01;35:*.svg=01;35:*.svgz=01;35:*.mng=01;35:*.pcx=01;35:*.mov=01;35:*.mpg=01;35:*.mpeg=01;35:*.m2v=01;35:*.mkv=01;35:*.webm=01;35:*.ogm=01;35:*.mp4=01;35:*.m4v=01;35:*.mp4v=01;35:*.vob=01;35:*.qt=01;35:*.nuv=01;35:*.wmv=01;35:*.asf=01;35:*.rm=01;35:*.rmvb=01;35:*.flc=01;35:*.avi=01;35:*.fli=01;35:*.flv=01;35:*.gl=01;35:*.dl=01;35:*.xcf=01;35:*.xwd=01;35:*.yuv=01;35:*.cgm=01;35:*.emf=01;35:*.ogv=01;35:*.ogx=01;35:*.aac=00;36:*.au=00;36:*.flac=00;36:*.m4a=00;36:*.mid=00;36:*.midi=00;36:*.mka=00;36:*.mp3=00;36:*.mpc=00;36:*.ogg=00;36:*.ra=00;36:*.wav=00;36:*.oga=00;36:*.opus=00;36:*.spx=00;36:*.xspf=00;36:"
declare -x NAME="SG-PC"
declare -x PATH="/home/heol/bin:/home/heol/.local/bin:/home/heol/AI/own_implement/RL_Paper_Reproduction/.codex/tmp/arg0/codex-arg0iQwZmU:/usr/lib/node_modules/@openai/codex/node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/codex-path:/usr/local/cuda/bin:/home/heol/bin:/home/heol/.local/bin:/home/heol/anaconda3/bin:/home/heol/.vscode-server/bin/520fb30b2d3d324b4cb2342f6e88e2cd93751de1/bin/remote-cli:/home/heol/bin:/home/heol/.local/bin:/usr/local/cuda/bin:/home/heol/bin:/home/heol/.local/bin:/opt/ros/galactic/bin:/home/heol/anaconda3/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/usr/lib/wsl/lib:/mnt/c/Program Files/NVIDIA GPU Computing Toolkit/CUDA/v12.6/bin:/mnt/c/Program Files/NVIDIA GPU Computing Toolkit/CUDA/v12.6/libnvvp:/mnt/c/Program Files/OpenLogic/jre-21.0.4.7-hotspot/bin:/mnt/c/Windows/system32:/mnt/c/Windows:/mnt/c/Windows/System32/Wbem:/mnt/c/Windows/System32/WindowsPowerShell/v1.0/:/mnt/c/Windows/System32/OpenSSH/:/mnt/c/Program Files/Microsoft SQL Server/150/Tools/Binn/:/mnt/c/Program Files/Microsoft SQL Server/Client SDK/ODBC/170/Tools/Binn/:/mnt/c/Program Files/dotnet/:/mnt/c/Program Files (x86)/NVIDIA Corporation/PhysX/Common:/mnt/c/Program Files/NVIDIA Corporation/NVIDIA NvDLISR:/mnt/c/Users/sg200/folder/software/anaconda:/mnt/c/Users/sg200/folder/software/anaconda/Scripts:/mnt/c/Users/sg200/folder/software/anaconda/Library/bin:/mnt/c/Users/sg200/folder/software/Git/cmd:/mnt/c/Users/sg200/folder/software/Git/bin:/mnt/c/Program Files/NVIDIA Corporation/Nsight Compute 2024.3.2/:/mnt/c/WINDOWS/system32:/mnt/c/WINDOWS:/mnt/c/WINDOWS/System32/Wbem:/mnt/c/WINDOWS/System32/WindowsPowerShell/v1.0/:/mnt/c/WINDOWS/System32/OpenSSH/:/mnt/c/Program Files/nodejs/:/mnt/c/Program Files/Docker/Docker/resources/bin:/mnt/c/Program Files (x86)/ZeroTier/One/:/mnt/c/Users/sg200/AppData/Local/Microsoft/WindowsApps:/mnt/c/Windows/system32:/mnt/c/Users/sg200/folder/software/Microsoft VS Code/bin:/mnt/c/Users/sg200/.dotnet/tools:/mnt/c/Users/sg200/folder/software/anaconda:/mnt/c/Users/sg200/folder/software/anaconda/Scripts:/mnt/c/Users/sg200/folder/software/anaconda/Library/bin:/mnt/c/Users/sg200/folder/software/anaconda/Libraray/mingw-w64/bin:/mnt/c/Users/sg200/folder/software/anaconda/Library/usr/bin:/mnt/c/Users/sg200/folder/software/Antigravity/bin:/mnt/c/Users/sg200/AppData/Roaming/npm:/snap/bin:/mnt/c/Windows/System32:/mnt/c/Windows/System32"
declare -x PULSE_SERVER="unix:/mnt/wslg/PulseServer"
declare -x PYTHONPATH="/opt/ros/galactic/lib/python3.8/site-packages"
declare -x PYTHONSTARTUP="/home/heol/.vscode-server/data/User/workspaceStorage/e8f721570b8f9d9d93d67f3be3089aac/ms-python.python/pythonrc.py"
declare -x PYTHON_BASIC_REPL="1"
declare -x ROS_DISTRO="galactic"
declare -x ROS_LOCALHOST_ONLY="0"
declare -x ROS_PYTHON_VERSION="3"
declare -x ROS_VERSION="2"
declare -x SHELL="/bin/bash"
declare -x SHLVL="2"
declare -x TERM="xterm-256color"
declare -x TERM_PROGRAM="vscode"
declare -x TERM_PROGRAM_VERSION="1.136.0"
declare -x USER="heol"
declare -x VSCODE_GIT_ASKPASS_EXTRA_ARGS=""
declare -x VSCODE_GIT_ASKPASS_MAIN="/home/heol/.vscode-server/bin/520fb30b2d3d324b4cb2342f6e88e2cd93751de1/extensions/git/dist/askpass-main.js"
declare -x VSCODE_GIT_ASKPASS_NODE="/home/heol/.vscode-server/bin/520fb30b2d3d324b4cb2342f6e88e2cd93751de1/node"
declare -x VSCODE_GIT_IPC_HANDLE="/run/user/1000/vscode-git-4a210efad9.sock"
declare -x VSCODE_IPC_HOOK_CLI="/run/user/1000/vscode-ipc-205637b1-8bf5-47d5-a1b8-b5058e27f018.sock"
declare -x VSCODE_PYTHON_AUTOACTIVATE_GUARD="1"
declare -x WAYLAND_DISPLAY="wayland-0"
declare -x WSL2_GUI_APPS_ENABLED="1"
declare -x WSLENV="VSCODE_WSL_EXT_LOCATION/up"
declare -x WSL_DISTRO_NAME="Ubuntu-20.04"
declare -x WSL_INTEROP="/run/WSL/1285_interop"
declare -x XDG_DATA_DIRS="/usr/local/share:/usr/share:/var/lib/snapd/desktop"
declare -x XDG_RUNTIME_DIR="/run/user/1000"
