#!/bin/bash
# Usage: restrim_push.sh <service> <quality> <rtmp_url> <stream_key>
# service: mixcloud | extra  (метка в cmdline для pgrep)

SERVICE="$1"
QUALITY="$2"
RTMP_URL="$3"
STREAM_KEY="$4"

HLS_DIR="/home/beasty197/projects/vtrnk_radio/web/hls"
LOG="/home/beasty197/projects/vtrnk_radio/logs/restrim_${SERVICE}.log"

case "$QUALITY" in
  450) SRC="$HLS_DIR/0/index.m3u8" ;;
  750) SRC="$HLS_DIR/1/index.m3u8" ;;
  1080) SRC="$HLS_DIR/2/index.m3u8" ;;
  max)  SRC="$HLS_DIR/video_stream123.m3u8" ;;
  *)    SRC="$HLS_DIR/1/index.m3u8" ;;
esac

if [ -z "$RTMP_URL" ] || [ -z "$STREAM_KEY" ]; then
  echo "$(date) missing url or key" >> "$LOG"
  exit 1
fi

# собрать destination: url/key (без двойного /)
DEST="${RTMP_URL%/}/${STREAM_KEY}"

# ждём исходник до 30 сек
for i in $(seq 1 30); do
  [ -f "$SRC" ] && break
  sleep 1
done
if [ ! -f "$SRC" ]; then
  echo "$(date) source not found: $SRC" >> "$LOG"
  exit 1
fi

echo "$(date) starting restrim_$SERVICE q=$QUALITY dest=${RTMP_URL%/}/***" >> "$LOG"

# -re читать в realtime; copy video если HLS уже нужного битрейта
exec /usr/bin/ffmpeg -hide_banner -loglevel warning -re -i "$SRC" \
  -c:v copy -c:a aac -b:a 128k -ac 2 \
  -f flv \
  -metadata "restrim_${SERVICE}=1" \
  "$DEST" >> "$LOG" 2>&1
