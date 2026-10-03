#!/usr/bin/sh
set -eu
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
trap '/usr/bin/busybox poweroff -f' EXIT
/usr/bin/busybox mount -t proc proc /proc
/usr/bin/busybox mount -t sysfs sysfs /sys
/usr/bin/busybox mount -t devtmpfs devtmpfs /dev
/usr/bin/busybox mkdir -p /tmp /var/cache/apk /sys/fs/cgroup /mnt/modloop
/usr/sbin/apk --no-network --no-cache --initdb --no-scripts --force-overwrite --force-non-repository add /opt/packages/*.apk >/dev/null
/usr/bin/busybox mount -t cgroup2 none /sys/fs/cgroup
/usr/bin/busybox ln -s /usr/bin/busybox /usr/sbin/adduser
/usr/bin/busybox ln -s /usr/bin/busybox /usr/sbin/addgroup
/usr/bin/busybox modprobe loop
/usr/bin/busybox modprobe squashfs
/usr/bin/busybox mount -t squashfs -o loop,ro /opt/modloop-virt /mnt/modloop
/usr/bin/busybox cp -a /mnt/modloop/modules/* /lib/modules/
/usr/bin/busybox depmod -a
/usr/bin/busybox modprobe vmw_vsock_virtio_transport
/usr/bin/python3 /opt/native-bootstrap.py
