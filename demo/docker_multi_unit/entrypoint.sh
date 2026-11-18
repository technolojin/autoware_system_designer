#!/usr/bin/env bash
# Container entry of the multi-unit environment.
#   build     colcon build of ASD_PACKAGES and their dependencies from /ws/src into /ws/out
#   describe  the E/E architecture named by the argument, as the built workspace exports it
#   serve     studio daemon of the environment ASD_ENVIRONMENT of the E/E architecture
#             ASD_EE_ARCHITECTURE, with an sshd on ASD_SSHD_PORT when ASD_SSHD is true;
#             ASD_RMW_IMPLEMENTATION selects the RMW implementation; with ASD_INSTALL_ROOT it
#             serves that mounted install tree as it is
set -eo pipefail

OUT=/ws/out
SITE=/run/asd/site
PACKAGES="${ASD_PACKAGES:-autoware_system_designer_studio autoware_system_designer_demo_systems}"

# shellcheck disable=SC1090
source "/opt/ros/${ROS_DISTRO}/setup.bash"
# shellcheck disable=SC1091
source /opt/build_depends/setup.bash

build() {
  # shellcheck disable=SC2086
  colcon --log-base "${OUT}/log" build \
    --base-paths /ws/src \
    --build-base "${OUT}/build" \
    --install-base "${OUT}/install" \
    --packages-up-to ${PACKAGES} \
    --event-handlers console_cohesion+ \
    --cmake-args -DBUILD_TESTING=OFF
}

# sshd of the unprivileged user: key login as "asd" only, host key made once per container.
start_sshd() {
  local dir=/run/asd/sshd
  mkdir -p "${dir}"
  [[ -f "${dir}/host_key" ]] || ssh-keygen -q -t ed25519 -N "" -f "${dir}/host_key"
  cat >"${dir}/sshd_config" <<CONFIG
Port ${ASD_SSHD_PORT}
HostKey ${dir}/host_key
PidFile ${dir}/sshd.pid
AuthorizedKeysFile ${SITE}/authorized_keys
StrictModes no
UsePAM no
PasswordAuthentication no
KbdInteractiveAuthentication no
AllowTcpForwarding local
X11Forwarding no
CONFIG
  /usr/sbin/sshd -f "${dir}/sshd_config" -E "${dir}/sshd.log"
}

# SSH client configuration of the daemon's user: the user, port, key and host key handling its peers' sshd take.
write_ssh_config() {
  mkdir -p "${HOME}/.ssh"
  chmod 700 "${HOME}/.ssh"
  cat >"${HOME}/.ssh/config" <<CONFIG
Host *
  User asd
  Port ${ASD_SSHD_PORT}
  IdentityFile ${SITE}/peer_ed25519
  IdentitiesOnly yes
  StrictHostKeyChecking accept-new
  UserKnownHostsFile /run/asd/known_hosts
  ConnectTimeout 5
CONFIG
}

use_workspace() {
  if [[ ! -f "${OUT}/install/setup.bash" ]]; then
    echo "no workspace in ${OUT}; run the build service first" >&2
    exit 1
  fi
  # shellcheck disable=SC1091
  source "${OUT}/install/setup.bash"
}

describe() {
  use_workspace
  exec autoware-system-designer-studio environment \
    --install-root "${OUT}/install" \
    --ee-architecture "${1:?E/E architecture}"
}

serve() {
  local install="${OUT}/install" studio=autoware-system-designer-studio
  if [[ -n "${ASD_INSTALL_ROOT:-}" ]]; then
    install="${ASD_INSTALL_ROOT}"
    studio="${install}/autoware_system_designer_studio/bin/autoware-system-designer-studio"
  else
    use_workspace
  fi
  if [[ -n "${ASD_RMW_IMPLEMENTATION:-}" ]]; then
    export RMW_IMPLEMENTATION="${ASD_RMW_IMPLEMENTATION}"
  fi
  if [[ "${ASD_SSHD:-}" == true ]]; then
    start_sshd
  fi
  write_ssh_config
  exec "${studio}" serve \
    --install-root "${install}" \
    --ee-architecture "${ASD_EE_ARCHITECTURE:?E/E architecture of the environment}" \
    --environment "${ASD_ENVIRONMENT:?environment of this daemon}"
}

case "${1:-serve}" in
  build) build ;;
  describe) describe "${2:-}" ;;
  serve) serve ;;
  *) exec "$@" ;;
esac
