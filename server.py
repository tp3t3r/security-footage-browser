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
            return json.load(f)
    return []

@app.route('/')
def index():
    days = request.args.get('days', config.getint('display', 'default_days'), type=int)
    camera = request.args.get('camera', type=int)
    end = int(datetime.now().timestamp())
    start = int((datetime.now() - timedelta(days=days)).timestamp())
    
    segments = [s for s in load_segments() if start < s['start_time'] < end]
    
    # Get unique cameras with names
    cameras_dict = {}
    for s in segments:
        if s['datadir'] not in cameras_dict:
            cameras_dict[s['datadir']] = {'id': s['datadir'], 'name': s.get('name', f"Camera {s['datadir']}")}
    cameras = sorted(cameras_dict.values(), key=lambda x: x['id'])
    
    # Filter by camera if specified
    if camera is not None:
        segments = [s for s in segments if s['datadir'] == camera]
    
    by_day = {}
    for seg in segments:
        # Use file modification time as fallback since segment timestamps are unreliable
        day = datetime.fromtimestamp(seg['start_time']).strftime('%Y-%m-%d') if seg['start_time'] > 1000000000 else 'Unknown'
        if day not in by_day:
            by_day[day] = []
        seg['start_time_str'] = datetime.fromtimestamp(seg['start_time']).strftime('%Y-%m-%d %H:%M:%S') if seg['start_time'] > 1000000000 else 'N/A'
        seg['end_time_str'] = ''
        
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
    datadir = request.args.get('datadir', type=int)
    file_num = request.args.get('file', type=int)
    segment = request.args.get('segment', type=int)
    
    segments = load_segments()
    seg = next((s for s in segments if s['datadir'] == datadir and s['file'] == file_num and s['segment'] == segment), None)
    
    if not seg:
        return "Segment not found", 404
    
    video_file = os.path.join(seg['path'], f'hiv{file_num:05d}.mp4')
    
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
