#!/bin/bash

HLS_DIR="/home/beasty197/projects/vtrnk_radio/web/hls"
SOURCE_RTMP="rtmp://127.0.0.1/live/video_stream123"
SOURCE_HLS="$HLS_DIR/video_stream123.m3u8"
LOCK_FILE="/tmp/hls_transcoder.lock"
LOG_FILE="/home/beasty197/projects/vtrnk_radio/logs/hls_transcoder.log"
MASTER="$HLS_DIR/master.m3u8"

mkdir -p "$(dirname "$LOG_FILE")"
mkdir -p "$HLS_DIR"/{0,1,2}

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "$(date) already running, exit" >> "$LOG_FILE"
    exit 0
fi

# Ждём, пока nginx-rtmp реально примет поток (есть свежий HLS от nginx)
for i in $(seq 1 60); do
    if [ -f "$SOURCE_HLS" ]; then
        age=$(($(date +%s) - $(stat -c %Y "$SOURCE_HLS")))
        if [ "$age" -lt 45 ]; then
            break
        fi
    fi
    sleep 1
done

if [ ! -f "$SOURCE_HLS" ]; then
    echo "$(date) source HLS not found, exit" >> "$LOG_FILE"
    exit 1
fi

write_master_full() {
cat > "$MASTER" << 'MASTEREOF'
#EXTM3U
#EXT-X-VERSION:3
#EXT-X-STREAM-INF:BANDWIDTH=600000,RESOLUTION=640x360,CODECS="avc1.42e01e,mp4a.40.2"
0/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=900000,RESOLUTION=854x480,CODECS="avc1.42e01e,mp4a.40.2"
1/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080,CODECS="avc1.42e01f,mp4a.40.2"
2/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=8000000,CODECS="avc1.42e01f,mp4a.40.2"
video_stream123.m3u8
MASTEREOF
}

write_master_max_only() {
cat > "$MASTER" << 'MASTEREOF'
#EXTM3U
#EXT-X-VERSION:3
#EXT-X-STREAM-INF:BANDWIDTH=8000000,CODECS="avc1.42e01f,mp4a.40.2"
video_stream123.m3u8
MASTEREOF
}

write_master_max_only
echo "$(date) starting transcoder RTMP input (locked)" >> "$LOG_FILE"

# Вход с RTMP, без -re
ffmpeg -y -i "$SOURCE_RTMP" \
  -c:v libx264 -preset veryfast -tune zerolatency -g 60 -sc_threshold 0 \
  -map 0:v:0 -map 0:a:0? -b:v:0 450k -maxrate:v:0 450k -bufsize:v:0 900k -s:v:0 640x360 \
  -map 0:v:0 -map 0:a:0? -b:v:1 750k -maxrate:v:1 750k -bufsize:v:1 1500k -s:v:1 854x480 \
  -map 0:v:0 -map 0:a:0? -b:v:2 4500k -maxrate:v:2 4500k -bufsize:v:2 9000k -s:v:2 1920x1080 \
  -c:a aac -b:a 128k -ac 2 \
  -f hls -hls_time 4 -hls_list_size 8 -hls_flags delete_segments+independent_segments \
  -var_stream_map "v:0,a:0 v:1,a:1 v:2,a:2" \
  "$HLS_DIR/%v/index.m3u8" \
  >> "$LOG_FILE" 2>&1 &

FFMPEG_PID=$!

while kill -0 "$FFMPEG_PID" 2>/dev/null; do
    if [ -f "$HLS_DIR/0/index.m3u8" ] && [ -f "$HLS_DIR/1/index.m3u8" ] && [ -f "$HLS_DIR/2/index.m3u8" ]; then
        write_master_full
    else
        write_master_max_only
    fi
    sleep 20
done

wait $FFMPEG_PID
EXIT_CODE=$?
echo "$(date) transcoder stopped (code $EXIT_CODE)" >> "$LOG_FILE"
exit $EXIT_CODE