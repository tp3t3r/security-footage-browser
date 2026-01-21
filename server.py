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
    
    # Get unique cameras
    cameras = sorted(set(s['datadir'] for s in segments))
    
    # Filter by camera if specified
    if camera is not None:
        segments = [s for s in segments if s['datadir'] == camera]
    
    by_day = {}
    for seg in segments:
        day = datetime.fromtimestamp(seg['start_time']).strftime('%Y-%m-%d')
        if day not in by_day:
            by_day[day] = []
        seg['start_time_str'] = datetime.fromtimestamp(seg['start_time']).strftime('%H:%M:%S')
        seg['end_time_str'] = datetime.fromtimestamp(seg['end_time']).strftime('%H:%M:%S')
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
    start = request.args.get('start', type=int)
    end = request.args.get('end', type=int)
    
    segments = load_segments()
    seg = next((s for s in segments if s['datadir'] == datadir and s['file'] == file_num 
                and s['start_offset'] == start), None)
    
    if not seg:
        return "Segment not found", 404
    
    video_file = os.path.join(seg['path'], f'hiv{file_num:05d}.mp4')
    
    if not os.path.exists(video_file):
        return "Video file not found", 404
    
    # Create cache filename
    cache_dir = '/tmp/footage-cache'
    os.makedirs(cache_dir, exist_ok=True)
    cache_file = os.path.join(cache_dir, f'{datadir}_{file_num}_{start}_{end}.mp4')
    
    # Extract and convert segment if not cached
    if not os.path.exists(cache_file):
        import subprocess
        h264_file = cache_file + '.h264'
        
        # Extract raw H.264 segment
        with open(video_file, 'rb') as f:
            f.seek(start)
            with open(h264_file, 'wb') as out:
                out.write(f.read(end - start))
        
        # Convert to MP4 container
        subprocess.run(['ffmpeg', '-i', h264_file, '-c:v', 'copy', '-c:a', 'none', cache_file],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.remove(h264_file)
    
    return send_file(cache_file, mimetype='video/mp4')

if __name__ == '__main__':
    config = configparser.ConfigParser()
    config.read('/etc/footage-browser/app.conf')
    
    app.run(host=config.get('app', 'host'), 
            port=config.getint('app', 'port'))
