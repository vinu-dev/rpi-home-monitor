"""
Recording service — manages ffmpeg processes for video clip recording.

Responsibilities:
- One ffmpeg process per active camera
- RTSPS input → dual output:
  - HLS segments for live view (.m3u8 + .ts, 2s segments, rolling 5)
  - MP4 clips for recording (3-minute segments, faststart)
- Generate thumbnail JPEG for each completed clip
- Handle camera disconnect/reconnect gracefully
- Respect recording mode per camera (continuous/off)

File layout:
  /data/recordings/<cam-id>/YYYY-MM-DD/HH-MM-SS.mp4
  /data/recordings/<cam-id>/YYYY-MM-DD/HH-MM-SS.thumb.jpg
  /data/live/<cam-id>/stream.m3u8
  /data/live/<cam-id>/segment_NNN.ts
"""

# TODO: Implement RecorderService class
