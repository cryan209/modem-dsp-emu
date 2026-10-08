#!/bin/bash
# Capture one real card-to-card V.90A -> V.90D call on eicon420 (ISDN port 1,
# MSN 7911 -> 7910; ports 3/4 have no line).  Run as root on the box with
# eicon420_v90a_dial.py copied to /var/tmp; the bri-test-answerer service
# answers on ttyds3/4.  Split the audio with tools/mlog_audio_split.py.
set -u
D=/var/tmp/v90a-cap-$(date +%Y%m%d-%H%M%S); mkdir -p "$D"; cd "$D"
CTL=/usr/lib/divas/divactrl
for c in 1 2; do
  (sleep 150) | timeout 150 script -qfc "$CTL mlog -c $c -o -A3 -#3 -l2048" /dev/null > mlog$c.txt 2> mlog$c.err &
  timeout 150 $CTL load -c $c -ReadXlog > xlog$c.txt 2>&1 < /dev/null &
done
timeout 150 $CTL mantool -c 1 -b -m -a > mantool1.txt 2>&1 < /dev/null &
timeout 150 $CTL mantool -c 2 -b -m -a > mantool2.txt 2>&1 < /dev/null &
for t in 5 6 7 8; do fuser /dev/ttyds$t && { echo "ttyds$t busy"; }; done
fuser -s /dev/ttyds6 && { echo ABORT ttyds6 busy; exit 1; }
sleep 3
python3 /var/tmp/eicon420_v90a_dial.py /dev/ttyds6 1 7910 20 "$D/dial.log"
sleep 3
$CTL load -c 1 -FlushXlog >> xlog1.txt 2>&1
$CTL load -c 2 -FlushXlog >> xlog2.txt 2>&1
pkill -f "divactrl mlog"; pkill -f "sleep 150" ; pkill -f "divactrl mantool"; pkill -f "divactrl load -c . -ReadXlog"
sleep 1
journalctl -u bri-test-answerer --since "-3 min" --no-pager > answerer.journal
/usr/lib/divas/divas_status > status.txt 2>&1
chown -R scottcryan "$D"; ls -la "$D"; echo "$D"
