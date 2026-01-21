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
            # Handle old format (list) or new format (dict)
            if isinstance(data, list):
                return {'cameras': [], 'segments': data}
            return data
    return {'cameras': [], 'segments': []}

@app.route('/')
def index():
    days_param = request.args.get('days', config.getint('display', 'default_days'), type=int)
    camera = request.args.get('camera', type=int)
    end = int(datetime.now().timestamp())
    start = int((datetime.now() - timedelta(days=days_param)).timestamp())
    
    data = load_segments()
    cameras = data['cameras']
    segments = [s for s in data['segments'] if start < s['start_time'] < end]
    
    # Create camera lookup
    camera_map = {c['id']: c for c in cameras}
    
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
                         title=config.get('app', 'title'))

@app.route('/video')
def video():
    camera_id = request.args.get('camera_id', type=int)
    file_num = request.args.get('file', type=int)
    segment = request.args.get('segment', type=int)
    
    data = load_segments()
    camera_map = {c['id']: c for c in data['cameras']}
    
    seg = next((s for s in data['segments'] if s['camera_id'] == camera_id and s['file'] == file_num and s['segment'] == segment), None)
    
    if not seg:
        return "Segment not found", 404
    
    cam = camera_map.get(camera_id)
    if not cam:
        return "Camera not found", 404
    
    video_file = os.path.join(cam['path'], f'hiv{file_num:05d}.mp4')
    
    if not os.path.exists(video_file):
        return "Video file not found", 404
    
    # Serve only the segment portion
    with open(video_file, 'rb') as f:
        f.seek(seg['start_offset'])
        data = f.read(seg['end_offset'] - seg['start_offset'])
    
    from flask import Response
    return Response(data, mimetype='video/mp4')

if __name__ == '__main__':
    config = configparser.ConfigParser()
    config.read('/etc/footage-browser/app.conf')
    
    app.run(host=config.get('app', 'host'), 
            port=config.getint('app', 'port'))
