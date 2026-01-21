from flask import Flask, render_template, request, send_file
import os
import json
import configparser
from datetime import datetime, timedelta

app = Flask(__name__)
config = None

def load_segments():
    cache_file = config.get('storage', 'cache_file')
    if os.path.exists(cache_file):
        with open(cache_file, 'r') as f:
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
    cache_file = config.get('storage', 'cache_file')
    cache_size = os.path.getsize(cache_file) if os.path.exists(cache_file) else 0
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
        
        # Get file size from segment offsets
        seg['size_bytes'] = seg['end_offset'] - seg['start_offset']
        if seg['size_bytes'] < 1024:
            seg['size'] = f'{seg["size_bytes"]} B'
        elif seg['size_bytes'] < 1024**2:
            seg['size'] = f'{seg["size_bytes"]/1024:.1f} KB'
        elif seg['size_bytes'] < 1024**3:
            seg['size'] = f'{seg["size_bytes"]/1024**2:.1f} MB'
        else:
            seg['size'] = f'{seg["size_bytes"]/1024**3:.1f} GB'
        
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
    
    data = load_segments()
    camera_map = {i: c for i, c in enumerate(data['cameras'])}
    
    cam = camera_map.get(camera_id)
    if not cam:
        return "Camera not found", 404
    
    video_file = os.path.join(cam['path'], f'hiv{file_num:05d}.mp4')
    
    if not os.path.exists(video_file):
        return "Video file not found", 404
    
    return send_file(video_file, mimetype='video/mp4', as_attachment=False)

if __name__ == '__main__':
    config = configparser.ConfigParser()
    config.read('/etc/footage-browser/app.conf')
    
    app.run(host=config.get('app', 'host'), 
            port=config.getint('app', 'port'))
