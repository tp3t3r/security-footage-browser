from flask import Flask, render_template, request, send_file
import os
import json
import configparser
import subprocess
import hashlib
from datetime import datetime, timedelta

app = Flask(__name__)
config = None

def load_segments():
    metacache_file = config.get('storage', 'metacache_file')
    if os.path.exists(metacache_file):
        with open(metacache_file, 'r') as f:
            data = json.load(f)
            # Handle old format (list) or new format (dict with grouped segments)
            if isinstance(data, list):
                return {'cameras': [], 'segments': {}}
            if 'segments' in data and isinstance(data['segments'], list):
                # Old format with flat list
                return {'cameras': data.get('cameras', []), 'segments': {}}
            return data
    return {'cameras': [], 'segments': {}}

@app.route('/')
def index():
    days_param = request.args.get('days', config.getint('display', 'default_days'), type=int)
    camera = request.args.get('camera', type=int)
    end = int(datetime.now().timestamp())
    start = int((datetime.now() - timedelta(days=days_param)).timestamp())
    
    data = load_segments()
    cameras = [{'id': i, 'name': c['name']} for i, c in enumerate(data['cameras'])]
    
    # Flatten segments from all cameras
    all_segments = []
    for cam_id, segs in data['segments'].items():
        for seg in segs:
            seg['camera_id'] = int(cam_id)
            all_segments.append(seg)
    
    segments = [s for s in all_segments if start < s['start_time'] < end]
    
    # Create camera lookup
    camera_map = {i: c for i, c in enumerate(data['cameras'])}
    
    # Calculate metadata
    metacache_file = config.get('storage', 'metacache_file')
    cache_size = os.path.getsize(metacache_file) if os.path.exists(metacache_file) else 0
    if cache_size < 1024:
        cache_size_str = f'{cache_size} B'
    elif cache_size < 1024**2:
        cache_size_str = f'{cache_size/1024:.1f} KB'
    else:
        cache_size_str = f'{cache_size/1024**2:.1f} MB'
    
    # Get last recording per camera
    last_recordings = {}
    for cam_id, segs in data['segments'].items():
        if segs:
            latest = max(segs, key=lambda s: s['start_time'])
            last_recordings[int(cam_id)] = datetime.fromtimestamp(latest['start_time']).strftime('%Y-%m-%d %H:%M:%S')
    
    # Filter by camera if specified
    if camera is not None:
        segments = [s for s in segments if s['camera_id'] == camera]
    
    by_day = {}
    for seg in segments:
        cam = camera_map.get(seg['camera_id'], {})
        day = datetime.fromtimestamp(seg['start_time']).strftime('%Y-%m-%d') if seg['start_time'] > 1000000000 else 'Unknown'
        if day not in by_day:
            by_day[day] = []
        
        seg['start_time_str'] = datetime.fromtimestamp(seg['start_time']).strftime('%Y-%m-%d %H:%M:%S') if seg['start_time'] > 1000000000 else 'N/A'
        seg['path'] = cam.get('path', '')
        seg['name'] = cam.get('name', f"Camera {seg['camera_id']}")
        
        # Calculate size from duration
        duration = seg['end_time'] - seg['start_time']
        if duration > 0:
            # Rough estimate: 1-2 Mbps for H.264
            estimated_bytes = duration * 150000  # ~1.2 Mbps
            seg['size_bytes'] = estimated_bytes
            if estimated_bytes < 1024**2:
                seg['size'] = f'~{estimated_bytes/1024:.0f} KB'
            else:
                seg['size'] = f'~{estimated_bytes/1024**2:.1f} MB'
        else:
            seg['size'] = 'Unknown'
            seg['size_bytes'] = 0
        
        by_day[day].append(seg)
    
    return render_template('index.html', 
                         days=sorted(by_day.items(), reverse=True),
                         cameras=cameras,
                         selected_camera=camera,
                         cache_size=cache_size_str,
                         last_recordings=last_recordings,
                         title=config.get('app', 'title'))

@app.route('/video')
def video():
    camera_id = request.args.get('camera_id', type=int)
    file_num = request.args.get('file', type=int)
    segment_num = request.args.get('segment', type=int)
    
    data = load_segments()
    camera_map = {i: c for i, c in enumerate(data['cameras'])}
    
    cam = camera_map.get(camera_id)
    if not cam:
        return "Camera not found", 404
    
    # Find segment info
    segments = data['segments'].get(str(camera_id), [])
    segment = next((s for s in segments if s['file'] == file_num and s['segment'] == segment_num), None)
    if not segment:
        return "Segment not found", 404
    
    video_file = os.path.join(cam['path'], f'hiv{file_num:05d}.mp4')
    if not os.path.exists(video_file):
        return "Video file not found", 404
    
    # Create cache directory
    cache_dir = '/tmp/footage-cache'
    os.makedirs(cache_dir, exist_ok=True)
    
    # Generate cache filename based on segment info
    cache_key = f"{camera_id}_{file_num}_{segment_num}_{segment['start_offset']}_{segment['end_offset']}"
    cache_hash = hashlib.md5(cache_key.encode()).hexdigest()
    cached_file = os.path.join(cache_dir, f'{cache_hash}.mp4')
    
    # Extract segment if not cached
    if not os.path.exists(cached_file):
        start_offset = segment['start_offset']
        end_offset = segment['end_offset']
        size = end_offset - start_offset
        
        # Extract raw H.264 segment and remux with ffmpeg
        temp_h264 = os.path.join(cache_dir, f'{cache_hash}.h264')
        
        try:
            # Extract raw bytes from offset range
            with open(video_file, 'rb') as f:
                f.seek(start_offset)
                with open(temp_h264, 'wb') as out:
                    remaining = size
                    while remaining > 0:
                        chunk_size = min(8192, remaining)
                        chunk = f.read(chunk_size)
                        if not chunk:
                            break
                        out.write(chunk)
                        remaining -= len(chunk)
            
            # Remux to MP4 container
            cmd = [
                'ffmpeg', '-y',
                '-i', temp_h264,
                '-c:v', 'copy',
                '-f', 'mp4',
                '-movflags', 'frag_keyframe+empty_moov',
                cached_file
            ]
            
            subprocess.run(cmd, check=True, capture_output=True, timeout=30)
            os.remove(temp_h264)
            
        except subprocess.CalledProcessError as e:
            if os.path.exists(temp_h264):
                os.remove(temp_h264)
            return f"Extraction failed: {e.stderr.decode()}", 500
        except subprocess.TimeoutExpired:
            if os.path.exists(temp_h264):
                os.remove(temp_h264)
            return "Extraction timeout", 500
        except Exception as e:
            if os.path.exists(temp_h264):
                os.remove(temp_h264)
            return f"Error: {str(e)}", 500
    
    return send_file(cached_file, mimetype='video/mp4', as_attachment=False)

if __name__ == '__main__':
    config = configparser.ConfigParser()
    config.read('/etc/footage-browser/app.conf')
    
    app.run(host=config.get('app', 'host'), 
            port=config.getint('app', 'port'))
