FROM debian:stable-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends openssh-server \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/bash keyup \
    && echo 'keyup:keyup-integration' | chpasswd \
    && mkdir -p /run/sshd /etc/ssh/sshd_config.d \
    && printf 'PasswordAuthentication yes\nKbdInteractiveAuthentication no\nUsePAM no\n' \
        > /etc/ssh/sshd_config.d/integration.conf

CMD ["/usr/sbin/sshd", "-D", "-e"]
