from flask import Flask, render_template, request, send_file, jsonify, Response
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

@app.route('/progress')
def progress():
    metacache_file = config.get('storage', 'metacache_file')
    progress_file = metacache_file.replace('.json', '.progress')
    
    if os.path.exists(progress_file):
        with open(progress_file, 'r') as f:
            return jsonify(json.load(f))
    else:
        return jsonify({'done': True})

@app.route('/')
def index():
    camera = request.args.get('camera', type=int)
    
    data = load_segments()
    cameras = [{'id': i, 'name': c['name']} for i, c in enumerate(data['cameras'])]
    
    # Flatten segments from all cameras
    all_segments = []
    for cam_id, segs in data['segments'].items():
        for seg in segs:
            seg['camera_id'] = int(cam_id)
            all_segments.append(seg)
    
    # Remove duplicates (same camera_id, file, segment)
    seen = set()
    unique_segments = []
    for seg in all_segments:
        key = (seg['camera_id'], seg['file'], seg['segment'])
        if key not in seen:
            seen.add(key)
            unique_segments.append(seg)
    
    # Filter by camera if specified
    if camera is not None:
        unique_segments = [s for s in unique_segments if s['camera_id'] == camera]
    
    # Sort by camera_id, then file number, then segment number
    unique_segments.sort(key=lambda s: (s['camera_id'], s['file'], s['segment']))
    
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
            latest = max(segs, key=lambda s: s.get('start_time', 0))
            last_recordings[int(cam_id)] = f"File {latest['file']}"
    
    # Group by file number instead of day
    by_file = {}
    for seg in unique_segments:
        cam = camera_map.get(seg['camera_id'], {})
        file_key = f"hiv{seg['file']:05d}.mp4"
        if file_key not in by_file:
            by_file[file_key] = []
        
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
        
        by_file[file_key].append(seg)
    
    return render_template('index.html', 
                         files=sorted(by_file.items()),
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
    cache_dir = '/opt/footage-browser/cache'
    os.makedirs(cache_dir, exist_ok=True)
    
    # Generate cache filename based on segment info
    cache_key = f"{camera_id}_{file_num}_{segment_num}_{segment['start_offset']}_{segment['end_offset']}"
    cache_hash = hashlib.md5(cache_key.encode()).hexdigest()
    cached_file = os.path.join(cache_dir, f'{cache_hash}.mp4')
    
    # Extract segment if not cached
    if not os.path.exists(cached_file):
        start_time = segment['start_offset']  # Time in seconds
        end_time = segment['end_offset']
        duration = end_time - start_time
        
        # Use ffmpeg to extract segment by time
        try:
            cmd = [
                'ffmpeg', '-y',
                '-ss', str(start_time),
                '-i', video_file,
                '-t', str(duration),
                '-map', '0:v:0',  # Only video stream
                '-c:v', 'copy',
                '-avoid_negative_ts', 'make_zero',
                '-f', 'mp4',
                cached_file
            ]
            
            subprocess.run(cmd, check=True, capture_output=True, timeout=120)
            
        except subprocess.CalledProcessError as e:
            return f"Extraction failed: {e.stderr.decode()}", 500
        except subprocess.TimeoutExpired:
            return "Extraction timeout", 500
        except Exception as e:
            return f"Error: {str(e)}", 500
    
    return send_file(cached_file, mimetype='video/mp4', as_attachment=False)

if __name__ == '__main__':
    config = configparser.ConfigParser()
    config.read('/etc/footage-browser/app.conf')
    
    app.run(host=config.get('app', 'host'), 
            port=config.getint('app', 'port'))
